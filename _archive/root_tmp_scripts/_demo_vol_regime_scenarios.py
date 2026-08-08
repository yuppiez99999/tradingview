# -*- coding: utf-8 -*-
"""VolRegimeWeighter 模拟场景演示

用真实 portfolio.yaml 持仓快照, 构造 7 个 VIX + 回撤组合场景,
运行动态调整权重逻辑, 验证输出是否符合预期.

场景设计:
    1. 牛市·无回撤          VIX=15, dd=0.0%   → 预期 bull
    2. 中性·小回撤          VIX=25, dd=3.0%   → 预期 neutral
    3. 熊市·中回撤          VIX=35, dd=6.0%   → 预期 bear
    4. 危机·超大回撤        VIX=50, dd=14.0%  → 预期 crisis
    5. ⚠强制升级: VIX牛市+大回撤  VIX=15, dd=14% → 预期 bear (回撤>12%至少bear)
    6. ⚠强制升级: VIX中性+大回撤  VIX=22, dd=13% → 预期 bear
    7. ⚠VIX/RV不一致: VIX牛市+高波动  VIX=15, RV≈32% → 预期 bear (取更保守档)
"""
import json
import os
import random
import sys
import unicodedata
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.getcwd())

import yaml

from utils.alpha.vol_regime_weighter import (
    VolRegimeWeighter,
    DEFAULT_WEIGHT_MATRIX,
    STYLE_CATEGORIES,
)

# ============================================================
# 辅助: 中英文混排对齐
# ============================================================

def dw(s: str) -> int:
    """计算字符串显示宽度 (中文占 2, 英文占 1)."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(s))


def pad(s: str, width: int) -> str:
    """按显示宽度左对齐填充."""
    s = str(s)
    return s + " " * max(0, width - dw(s))


def fmt(v: float, places: int = 4) -> str:
    """格式化浮点, 带正负号."""
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.{places}f}"


# ============================================================
# 加载真实 portfolio.yaml
# ============================================================

PORTFOLIO_PATH = Path("configs/portfolio.yaml")
with PORTFOLIO_PATH.open("r", encoding="utf-8") as f:
    PORTFOLIO_DATA = yaml.safe_load(f) or {}

# ============================================================
# 构造模拟 daily_returns (场景 7 用)
# ============================================================

random.seed(42)
# 日波动率 3.0%, 经 EWMA 后年化 ≈ 31% (bear 档: 0.25-0.40)
# 目的: 让 RV 分类为 bear, 与 VIX=15(bull) 不一致 → 测试"取更保守档"
SIM_DAILY_RETURNS_HIGH_VOL = [random.gauss(0, 0.03) for _ in range(20)]

# ============================================================
# 场景定义
# ============================================================

SCENARIOS = [
    {
        "name": "牛市·无回撤",
        "vix": 15.0,
        "drawdown": 0.0,
        "returns": None,
        "expected": "bull",
        "note": "进攻类加仓, 防御减仓",
    },
    {
        "name": "中性·小回撤",
        "vix": 25.0,
        "drawdown": 0.03,
        "returns": None,
        "expected": "neutral",
        "note": "全 1.0 中性",
    },
    {
        "name": "熊市·中回撤",
        "vix": 35.0,
        "drawdown": 0.06,
        "returns": None,
        "expected": "bear",
        "note": "进攻减仓, 防御加仓",
    },
    {
        "name": "危机·超大回撤",
        "vix": 50.0,
        "drawdown": 0.14,
        "returns": None,
        "expected": "crisis",
        "note": "科技×0.30, 现金×3.00",
    },
    {
        "name": "⚠强制升级: VIX牛市 + 大回撤14%",
        "vix": 15.0,
        "drawdown": 0.14,
        "returns": None,
        "expected": "bear",
        "note": "VIX=bull 但回撤>12% → 至少 bear",
    },
    {
        "name": "⚠强制升级: VIX中性 + 大回撤13%",
        "vix": 22.0,
        "drawdown": 0.13,
        "returns": None,
        "expected": "bear",
        "note": "VIX=neutral 但回撤>12% → 至少 bear",
    },
    {
        "name": "⚠VIX/RV不一致: VIX牛市 + 高波动",
        "vix": 15.0,
        "drawdown": 0.0,
        "returns": SIM_DAILY_RETURNS_HIGH_VOL,
        "expected": "bear",
        "note": "VIX=bull, RV≈31%(bear) → 取更保守 bear, conf↓到0.50",
    },
]


# ============================================================
# 主流程
# ============================================================

def main() -> int:
    print("=" * 78)
    print("VolRegimeWeighter 模拟场景演示")
    print("  数据源: 真实 configs/portfolio.yaml 持仓快照")
    print("  模式:   临时启用 Flag (USE_VOL_REGIME_WEIGHTER=True), Phase 0 只读建议")
    print("=" * 78)

    # 临时启用 Flag
    with patch("utils.alpha.vol_regime_weighter.VolRegimeWeighter._check_flag", return_value=True):
        weighter = VolRegimeWeighter(reports_dir=Path("reports/evolution"))

        # ---- 展示真实持仓当前权重 ----
        current_weights = weighter._parse_portfolio_snapshot(PORTFOLIO_DATA)
        total_w = sum(current_weights.values())

        print("\n【真实持仓 · 按风格聚合】")
        print(f"  {'风格':<8} {'权重':>8}   {'占比':>8}")
        print(f"  {'-'*8} {'-'*8}   {'-'*8}")
        for style in sorted(current_weights.keys(), key=lambda s: -current_weights[s]):
            w = current_weights[style]
            pct = f"{w/total_w*100:.1f}%" if total_w > 0 else "N/A"
            print(f"  {pad(style, 8)} {w:>8.4f}   {pct:>8}")
        print(f"  {'-'*8} {'-'*8}   {'-'*8}")
        print(f"  {'合计':<8} {total_w:>8.4f}   {'100.0%':>8}")
        if abs(total_w - 1.0) > 1e-6:
            print(f"  ⚠ 总和 {total_w:.4f} ≠ 1.0, 差额 {1.0-total_w:+.4f} 将在约束阶段归现金")

        # ---- 逐场景运行 ----
        for idx, sc in enumerate(SCENARIOS, 1):
            print("\n" + "=" * 78)
            print(f"场景 {idx}: {sc['name']}")
            vix_str = f"VIX={sc['vix']}"
            dd_str = f"回撤={sc['drawdown']*100:.1f}%"
            rv_str = "RV≈31.7%(bear档)" if sc["returns"] else "无RV"
            print(f"  输入: {vix_str}  {dd_str}  {rv_str}")
            print(f"  预期: regime={sc['expected']}  ({sc['note']})")
            print("-" * 78)

            # 运行 compute_weights (拿完整对象)
            suggestion = weighter.compute_weights(
                current_weights=current_weights,
                vix_value=sc["vix"],
                daily_returns=sc["returns"],
                current_drawdown=sc["drawdown"],
            )

            regime = suggestion.regime

            # ---- Regime 识别结果 ----
            print("【Regime 识别】")
            print(f"  标签     = {regime.label}")
            print(f"  置信度   = {regime.confidence:.2f}")
            print(f"  来源     = {regime.source}")
            print(f"  对冲率   = {regime.aligned_hedge_ratio:.2f} ({regime.hedge_policy_key})")
            ind = regime.indicators
            ind_str = ", ".join(f"{k}={v:.4f}" for k, v in ind.items()) if ind else "无"
            print(f"  指标     = {ind_str}")
            cons = regime.consistency_check
            cons_str = cons.get("consistent", "N/A") if isinstance(cons, dict) else str(cons)
            print(f"  一致性   = {cons_str}")
            if isinstance(cons, dict) and not cons.get("consistent", True):
                print(f"           VIX分类={cons.get('vix_classification')}, "
                      f"RV分类={cons.get('realized_vol_classification')}, "
                      f"选择={cons.get('chosen')}")

            # 验证是否符合预期
            match = "✅" if regime.label == sc["expected"] else "❌ 预期不符!"
            print(f"  预期校验 = {match} (预期={sc['expected']}, 实际={regime.label})")

            # ---- 权重调整对比表 ----
            print("\n【权重调整】")
            print(f"  {'风格':<8} {'当前':>8} {'倍数':>6} {'原始建议':>10} {'最终建议':>10} {'变动':>10}")
            print(f"  {'-'*8} {'-'*8} {'-'*6} {'-'*10} {'-'*10} {'-'*10}")

            # 原始建议 = current × multiplier (未经约束)
            for style in current_weights:
                cur = current_weights.get(style, 0.0)
                mult = suggestion.multipliers.get(style, 1.0)
                raw = cur * mult
                final = suggestion.suggested_weights.get(style, 0.0)
                delta = suggestion.deltas.get(style, 0.0)
                print(f"  {pad(style, 8)} {cur:>8.4f} {mult:>6.2f} {raw:>10.4f} {final:>10.4f} {fmt(delta):>10}")

            # 校验总和
            sug_total = sum(suggestion.suggested_weights.values())
            print(f"  {'-'*8} {'-'*8} {'-'*6} {'-'*10} {'-'*10} {'-'*10}")
            print(f"  {pad('合计', 8)} {total_w:>8.4f} {'':>6} {'':>10} {sug_total:>10.4f} {'':>10}")

            # ---- 触发的约束 ----
            print("\n【约束执行】")
            for c in suggestion.constraints_applied:
                print(f"  - {c}")

            # ---- 触发理由 ----
            print(f"\n【触发理由】 {suggestion.trigger_reason}")

            # ---- 写报告 ----
            report_path = weighter.emit_suggestion(suggestion)
            print(f"【报告路径】 {report_path}")

        # ---- 汇总 ----
        print("\n" + "=" * 78)
        print("【汇总】")
        print(f"  场景总数: {len(SCENARIOS)}")
        print(f"  报告目录: reports/evolution/")
        print(f"  矩阵规模: 4 regime × 8 风格 = 32 调整倍数")
        print(f"  默认矩阵:")
        for regime_label in ["bull", "neutral", "bear", "crisis"]:
            matrix = DEFAULT_WEIGHT_MATRIX[regime_label]
            brief = "  ".join(f"{s}×{matrix[s]:.2f}" for s in ["科技", "新能源", "防御", "现金"])
            print(f"    {regime_label:<8}: {brief}")
        print("=" * 78)

    return 0


if __name__ == "__main__":
    sys.exit(main())
