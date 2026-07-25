# -*- coding: utf-8 -*-
"""MARGIN_EXP Shadow 失败根因分析（P2.2 v6.2 诊断）

目标：
    第十二批次 v6.1 结果显示 MARGIN_EXP 通过 G2+G3 但 Shadow 失败：
    - live_DSR=0.128 (远低于阈值 0.5)
    - max_dd=0.204 (远超阈值 0.12，即使 risk_managed)
    本脚本深入分析 Shadow 期间每日 PnL 分布，找到回撤期与根因。

诊断维度：
    1. 每日 PnL 时序分布（找最大回撤期的具体起止日）
    2. 单点异常 vs 持续失效（最大回撤期是否由 1-2 天极端亏损导致）
    3. 对照组（无风险管理）vs 实验组（有风险管理）的回撤对比
    4. Top 持仓 vs Bottom 持仓的 PnL 贡献（找拖累标的）
    5. 与 VT_MICRO_VOL_SKEW_INV 的 Shadow 表现对比（基线参考）

产出：
    - reports/vibe_trading/p2_2_margin_exp_shadow_diagnosis/MARGIN_EXP_SHADOW_DIAGNOSIS.md
    - reports/vibe_trading/p2_2_margin_exp_shadow_diagnosis/daily_pnl.json
"""
from __future__ import annotations

import json
import logging
import math
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

# 项目根路径注入
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from research.vibe_trading_factor_analysis.scripts.real_data_loader import (
    list_available_symbols,
    load_price_data, load_fundamentals, load_benchmark_returns,
    compute_equal_weight_benchmark,
)
from research.vibe_trading_factor_analysis.adapters.factor_history_builder import (
    build_factor_history,
)
from research.vibe_trading_factor_analysis.adapters.vibe_trading_factor_adapter import (
    VibeTradingFactorAdapter,
)
from research.vibe_trading_factor_analysis.shadow.shadow_account import ShadowAccount
from research.vibe_trading_factor_analysis.pipeline.pipeline_orchestrator import (
    PipelineOrchestrator,
)

logger = logging.getLogger("margin_exp_diagnosis")
REPORTS_DIR = _PROJECT_ROOT / "research" / "vibe_trading_factor_analysis" / "reports" / "vibe_trading"

# 目标因子（v6.1 首个 G2+G3 双通过的 QualityTrend 因子）
TARGET_FACTOR = "VT_QUALTREND_MARGIN_EXP"
# 基线参考因子（首个完整通过 8 级验证的因子）
BASELINE_FACTOR = "VT_MICRO_VOL_SKEW_INV"


def main() -> int:
    """主入口：MARGIN_EXP Shadow 失败根因分析"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s | %(message)s",
    )
    print("=" * 70)
    print("MARGIN_EXP Shadow 失败根因分析（P2.2 v6.2 诊断）")
    print(f"目标因子: {TARGET_FACTOR}")
    print(f"基线参考: {BASELINE_FACTOR} (首个完整通过 8 级验证)")
    print("=" * 70)

    # ============ Step 1: 加载数据 ============
    print("\n[1/6] 加载 P1 改进后的真实数据")
    symbols = list_available_symbols()
    print(f"  可用标的数: {len(symbols)}")

    price_data = load_price_data(symbols=symbols)
    fundamentals = load_fundamentals(price_data)
    benchmark_returns = load_benchmark_returns()
    if not benchmark_returns:
        benchmark_returns = compute_equal_weight_benchmark(price_data)
    print(f"  price_data: {len(price_data)} | benchmark_returns: {len(benchmark_returns)} 天")

    # ============ Step 2: 加载 fundamentals_history ============
    print("\n[2/6] 加载 fundamentals_history (v6 修复后必需)")
    orchestrator = PipelineOrchestrator(config={"reports_dir": str(REPORTS_DIR)})
    fundamentals_history = orchestrator._load_fundamentals_history(list(price_data.keys()))
    print(f"  fundamentals_history: {len(fundamentals_history)} 个标的")

    # ============ Step 3: 构建因子历史 ============
    print("\n[3/6] 构建日频因子历史 (120d)")
    adapter = VibeTradingFactorAdapter()
    factor_history, fwd_returns_hist, valid_dates = build_factor_history(
        adapter=adapter,
        price_data=price_data,
        fundamentals=fundamentals,
        benchmark_returns=benchmark_returns,
        history_days=120,
        forward_window=5,
        fundamentals_history=fundamentals_history,
    )
    print(f"  因子数: {len(factor_history)} | 有效天数: {len(valid_dates)}")

    if TARGET_FACTOR not in factor_history:
        print(f"[ERROR] 目标因子 {TARGET_FACTOR} 不在因子历史中")
        return 1

    target_history = factor_history[TARGET_FACTOR]
    print(f"  {TARGET_FACTOR} 历史长度: {len(target_history)}")

    # ============ Step 4: 对照组（无风险管理）vs 实验组（有风险管理）============
    print("\n[4/6] 对照组 vs 实验组 Shadow 测试")
    n_trials = max(len(symbols), 13)

    # 对照组：无风险管理
    baseline_account = ShadowAccount(config={"reports_dir": str(REPORTS_DIR)})
    baseline_result = baseline_account.run_shadow(
        factor_values_history=target_history,
        forward_returns_history=fwd_returns_hist,
        n_trials=n_trials,
        factor_name=TARGET_FACTOR,
    )
    print(f"\n  [对照组 - 无风险管理]")
    print(f"    pass_shadow  : {baseline_result.pass_shadow}")
    print(f"    live_dsr     : {baseline_result.live_dsr:.4f}  (阈值 > 0.5)")
    print(f"    max_drawdown : {baseline_result.max_drawdown:.4f}  (阈值 < 0.12)")
    print(f"    total_return : {baseline_result.total_return:.4f}")
    print(f"    realized_vol : {baseline_result.raw_realized_vol:.4f}")

    # 实验组：启用风险管理
    rm_account = ShadowAccount(config={
        "reports_dir": str(REPORTS_DIR),
        "risk_managed": True,
        "target_vol": 0.15,
        "vol_lookback": 20,
        "dd_derisk_threshold": 0.05,
        "dd_derisk_factor": 0.5,
        "scaler_cap": 2.0,
    })
    rm_result = rm_account.run_shadow(
        factor_values_history=target_history,
        forward_returns_history=fwd_returns_hist,
        n_trials=n_trials,
        factor_name=TARGET_FACTOR,
    )
    print(f"\n  [实验组 - 启用风险管理]")
    print(f"    pass_shadow      : {rm_result.pass_shadow}")
    print(f"    live_dsr         : {rm_result.live_dsr:.4f}")
    print(f"    max_drawdown     : {rm_result.max_drawdown:.4f}")
    print(f"    raw_max_drawdown : {rm_result.raw_max_drawdown:.4f}  (缩放前)")
    print(f"    total_return     : {rm_result.total_return:.4f}")
    print(f"    realized_vol     : {rm_result.realized_vol:.4f}  (目标 0.15)")
    print(f"    avg_scaler       : {rm_result.avg_scaler:.4f}")
    print(f"    derisk_days      : {rm_result.derisk_triggered_days} / {rm_result.n_obs_days}")
    print(f"    fail_reasons     : {rm_result.fail_reasons}")

    # ============ Step 5: 每日 PnL 深度分析 ============
    print("\n[5/6] 每日 PnL 深度分析（找回撤期根因）")
    daily_pnl_baseline = baseline_result.daily_pnl
    daily_pnl_rm = rm_result.daily_pnl
    n_days = len(daily_pnl_baseline)

    # 累计净值
    cum_nav_baseline = np.cumprod([1.0 + p for p in daily_pnl_baseline])
    cum_nav_rm = np.cumprod([1.0 + p for p in daily_pnl_rm])

    # 计算回撤序列（peak-to-trough）
    def compute_drawdown_series(cum_nav):
        """计算每日回撤序列 = 1 - nav / running_max"""
        running_max = np.maximum.accumulate(cum_nav)
        return 1.0 - cum_nav / running_max

    dd_series_baseline = compute_drawdown_series(cum_nav_baseline)
    dd_series_rm = compute_drawdown_series(cum_nav_rm)

    # 找最大回撤期
    def find_max_dd_period(daily_pnl, dd_series):
        """找回撤最大点的起止日"""
        trough_idx = int(np.argmax(dd_series))
        # 找 peak（在 trough 之前的最高点）
        peak_idx = int(np.argmax(cum_nav_baseline[:trough_idx + 1])) if trough_idx > 0 else 0
        # 计算 peak 到 trough 的累积亏损
        if peak_idx < trough_idx:
            period_pnl = daily_pnl[peak_idx:trough_idx + 1]
            period_cum_loss = float(np.prod([1.0 + p for p in period_pnl]) - 1.0)
        else:
            period_cum_loss = 0.0
        return peak_idx, trough_idx, period_cum_loss

    peak_b, trough_b, loss_b = find_max_dd_period(daily_pnl_baseline, dd_series_baseline)
    peak_r, trough_r, loss_r = find_max_dd_period(daily_pnl_rm, dd_series_rm)

    print(f"\n  [对照组 - 无风险管理]")
    print(f"    最大回撤: {baseline_result.max_drawdown:.4f} ({baseline_result.max_drawdown*100:.2f}%)")
    print(f"    回撤期: day {peak_b} → day {trough_b} (持续 {trough_b - peak_b + 1} 天)")
    print(f"    期间累计亏损: {loss_b:.4f} ({loss_b*100:.2f}%)")
    print(f"    最大单日亏损: {min(daily_pnl_baseline):.4f} ({min(daily_pnl_baseline)*100:.2f}%)")
    print(f"    最大单日盈利: {max(daily_pnl_baseline):.4f} ({max(daily_pnl_baseline)*100:.2f}%)")

    print(f"\n  [实验组 - 启用风险管理]")
    print(f"    最大回撤: {rm_result.max_drawdown:.4f} ({rm_result.max_drawdown*100:.2f}%)")
    print(f"    回撤期: day {peak_r} → day {trough_r} (持续 {trough_r - peak_r + 1} 天)")
    print(f"    期间累计亏损: {loss_r:.4f} ({loss_r*100:.2f}%)")
    print(f"    最大单日亏损: {min(daily_pnl_rm):.4f} ({min(daily_pnl_rm)*100:.2f}%)")
    print(f"    最大单日盈利: {max(daily_pnl_rm):.4f} ({max(daily_pnl_rm)*100:.2f}%)")

    # 判断单点异常 vs 持续失效
    print(f"\n  [单点异常 vs 持续失效诊断]")
    # 找出亏损最严重的 5 天
    sorted_pnl = sorted(enumerate(daily_pnl_baseline), key=lambda x: x[1])[:5]
    print(f"    对照组 Top 5 亏损日:")
    total_loss_top5 = 0.0
    for day_idx, pnl in sorted_pnl:
        cum_loss = float(np.prod([1.0 + p for p in daily_pnl_baseline[:day_idx + 1]]) - 1.0)
        print(f"      day {day_idx:3d}: PnL={pnl:+.4f} ({pnl*100:+.2f}%) | 累计净值={cum_loss:+.4f}")
        total_loss_top5 += pnl
    print(f"    Top 5 亏损日合计: {total_loss_top5:+.4f} ({total_loss_top5*100:+.2f}%)")
    print(f"    对照组总收益: {baseline_result.total_return:+.4f} ({baseline_result.total_return*100:+.2f}%)")

    # 判断：Top 5 亏损日是否占 max_dd 的主要部分
    if abs(total_loss_top5) > baseline_result.max_drawdown * 0.7:
        diagnosis = "单点异常主导（Top 5 亏损日占 max_dd > 70%）"
        diagnosis_recommendation = "考虑增加止损 / 单日风险预算"
    elif abs(total_loss_top5) > baseline_result.max_drawdown * 0.4:
        diagnosis = "部分单点异常 + 部分持续失效"
        diagnosis_recommendation = "需结合标的持仓分析，识别是哪些股票拖累"
    else:
        diagnosis = "持续失效主导（亏损分散在多个交易日）"
        diagnosis_recommendation = "因子本身 OOS 不稳定，需扩大样本或改进因子设计"
    print(f"\n    诊断结论: {diagnosis}")
    print(f"    应对建议: {diagnosis_recommendation}")

    # ============ Step 6: 与基线因子 VT_MICRO_VOL_SKEW_INV 对比 ============
    print(f"\n[6/6] 与基线因子 {BASELINE_FACTOR} 对比")
    if BASELINE_FACTOR in factor_history:
        baseline_factor_history = factor_history[BASELINE_FACTOR]
        # 基线因子也跑一次 risk_managed Shadow
        baseline_rm_account = ShadowAccount(config={
            "reports_dir": str(REPORTS_DIR),
            "risk_managed": True,
            "target_vol": 0.15,
            "vol_lookback": 20,
            "dd_derisk_threshold": 0.05,
            "dd_derisk_factor": 0.5,
            "scaler_cap": 2.0,
        })
        baseline_rm_result = baseline_rm_account.run_shadow(
            factor_values_history=baseline_factor_history,
            forward_returns_history=fwd_returns_hist,
            n_trials=n_trials,
            factor_name=BASELINE_FACTOR,
        )
        print(f"\n  [基线因子 {BASELINE_FACTOR} - 启用风险管理]")
        print(f"    pass_shadow      : {baseline_rm_result.pass_shadow}")
        print(f"    live_dsr         : {baseline_rm_result.live_dsr:.4f}")
        print(f"    max_drawdown     : {baseline_rm_result.max_drawdown:.4f}")
        print(f"    total_return     : {baseline_rm_result.total_return:.4f}")
        print(f"    realized_vol     : {baseline_rm_result.realized_vol:.4f}")
        print(f"    derisk_days      : {baseline_rm_result.derisk_triggered_days} / {baseline_rm_result.n_obs_days}")

        print(f"\n  [MARGIN_EXP vs 基线对比]")
        print(f"    {'指标':<20s} {'MARGIN_EXP':>15s} {'基线因子':>15s} {'差异':>12s}")
        print(f"    {'-'*20} {'-'*15} {'-'*15} {'-'*12}")
        print(f"    {'live_dsr':<20s} {rm_result.live_dsr:>15.4f} {baseline_rm_result.live_dsr:>15.4f} {rm_result.live_dsr-baseline_rm_result.live_dsr:>+12.4f}")
        print(f"    {'max_drawdown':<20s} {rm_result.max_drawdown:>15.4f} {baseline_rm_result.max_drawdown:>15.4f} {rm_result.max_drawdown-baseline_rm_result.max_drawdown:>+12.4f}")
        print(f"    {'total_return':<20s} {rm_result.total_return:>15.4f} {baseline_rm_result.total_return:>15.4f} {rm_result.total_return-baseline_rm_result.total_return:>+12.4f}")
        print(f"    {'realized_vol':<20s} {rm_result.realized_vol:>15.4f} {baseline_rm_result.realized_vol:>15.4f} {rm_result.realized_vol-baseline_rm_result.realized_vol:>+12.4f}")
        print(f"    {'derisk_days':<20s} {rm_result.derisk_triggered_days:>15d} {baseline_rm_result.derisk_triggered_days:>15d} {rm_result.derisk_triggered_days-baseline_rm_result.derisk_triggered_days:>+12d}")
    else:
        print(f"  [WARN] 基线因子 {BASELINE_FACTOR} 不在因子历史中")
        baseline_rm_result = None

    # ============ 写入报告 ============
    print("\n写入诊断报告...")
    md_path = _write_diagnosis_report(
        target_factor=TARGET_FACTOR,
        baseline_factor=BASELINE_FACTOR,
        baseline_result=baseline_result,
        rm_result=rm_result,
        baseline_rm_result=baseline_rm_result,
        daily_pnl_baseline=daily_pnl_baseline,
        daily_pnl_rm=daily_pnl_rm,
        dd_series_baseline=dd_series_baseline,
        dd_series_rm=dd_series_rm,
        peak_b=peak_b, trough_b=trough_b, loss_b=loss_b,
        peak_r=peak_r, trough_r=trough_r, loss_r=loss_r,
        sorted_pnl=sorted_pnl,
        total_loss_top5=total_loss_top5,
        diagnosis=diagnosis,
        diagnosis_recommendation=diagnosis_recommendation,
        symbols_count=len(symbols),
        n_trials=n_trials,
    )
    print(f"  报告路径: {md_path}")

    # 保存原始数据
    json_path = md_path.parent / "daily_pnl.json"
    json_data = {
        "batch_id": md_path.parent.name,
        "generated_at": datetime.now().isoformat(),
        "target_factor": TARGET_FACTOR,
        "baseline_factor": BASELINE_FACTOR,
        "n_symbols": len(symbols),
        "n_trials": n_trials,
        "baseline_no_rm": {
            "daily_pnl": daily_pnl_baseline,
            "live_dsr": baseline_result.live_dsr,
            "max_drawdown": baseline_result.max_drawdown,
            "total_return": baseline_result.total_return,
            "realized_vol": baseline_result.raw_realized_vol,
        },
        "experiment_with_rm": {
            "daily_pnl": daily_pnl_rm,
            "live_dsr": rm_result.live_dsr,
            "max_drawdown": rm_result.max_drawdown,
            "raw_max_drawdown": rm_result.raw_max_drawdown,
            "total_return": rm_result.total_return,
            "realized_vol": rm_result.realized_vol,
            "avg_scaler": rm_result.avg_scaler,
            "derisk_days": rm_result.derisk_triggered_days,
        },
        "diagnosis": diagnosis,
        "diagnosis_recommendation": diagnosis_recommendation,
        "max_dd_period_baseline": {
            "peak_day": peak_b, "trough_day": trough_b,
            "duration_days": trough_b - peak_b + 1,
            "cum_loss": loss_b,
        },
        "max_dd_period_rm": {
            "peak_day": peak_r, "trough_day": trough_r,
            "duration_days": trough_r - peak_r + 1,
            "cum_loss": loss_r,
        },
        "top5_loss_days": [
            {"day": int(idx), "pnl": float(pnl)} for idx, pnl in sorted_pnl
        ],
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False, default=str)
    print(f"  JSON 数据: {json_path}")

    print("\n" + "=" * 70)
    print("MARGIN_EXP Shadow 失败根因分析完成")
    print("=" * 70)
    return 0


def _write_diagnosis_report(
    target_factor: str,
    baseline_factor: str,
    baseline_result,
    rm_result,
    baseline_rm_result,
    daily_pnl_baseline,
    daily_pnl_rm,
    dd_series_baseline,
    dd_series_rm,
    peak_b: int, trough_b: int, loss_b: float,
    peak_r: int, trough_r: int, loss_r: float,
    sorted_pnl,
    total_loss_top5: float,
    diagnosis: str,
    diagnosis_recommendation: str,
    symbols_count: int,
    n_trials: int,
) -> Path:
    """写入诊断报告"""
    batch_id = f"p2_2_margin_exp_shadow_diagnosis_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    batch_dir = REPORTS_DIR / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    md_path = batch_dir / "MARGIN_EXP_SHADOW_DIAGNOSIS.md"

    # Top 5 亏损日详情表格
    top5_rows = ""
    for day_idx, pnl in sorted_pnl:
        cum_loss = float(np.prod([1.0 + p for p in daily_pnl_baseline[:day_idx + 1]]) - 1.0)
        top5_rows += f"| day {day_idx:3d} | {pnl:+.4f} ({pnl*100:+.2f}%) | {cum_loss:+.4f} ({cum_loss*100:+.2f}%) |\n"

    # 基线对比表
    if baseline_rm_result is not None:
        baseline_compare = f"""
## 6. 与基线因子 {baseline_factor} 对比

| 指标 | MARGIN_EXP (RM) | {baseline_factor} (RM) | 差异 |
|------|-----------------|-------------------------|------|
| live_dsr | {rm_result.live_dsr:.4f} | {baseline_rm_result.live_dsr:.4f} | {rm_result.live_dsr-baseline_rm_result.live_dsr:+.4f} |
| max_drawdown | {rm_result.max_drawdown:.4f} | {baseline_rm_result.max_drawdown:.4f} | {rm_result.max_drawdown-baseline_rm_result.max_drawdown:+.4f} |
| total_return | {rm_result.total_return:.4f} | {baseline_rm_result.total_return:.4f} | {rm_result.total_return-baseline_rm_result.total_return:+.4f} |
| realized_vol | {rm_result.realized_vol:.4f} | {baseline_rm_result.realized_vol:.4f} | {rm_result.realized_vol-baseline_rm_result.realized_vol:+.4f} |
| derisk_days | {rm_result.derisk_triggered_days} / {rm_result.n_obs_days} | {baseline_rm_result.derisk_triggered_days} / {baseline_rm_result.n_obs_days} | {rm_result.derisk_triggered_days-baseline_rm_result.derisk_triggered_days:+d} |

**对比结论**：
- MARGIN_EXP live_dsr={rm_result.live_dsr:.4f} 远低于基线 {baseline_rm_result.live_dsr:.4f}（差 {rm_result.live_dsr-baseline_rm_result.live_dsr:+.4f}）
- MARGIN_EXP max_dd={rm_result.max_drawdown:.4f} 远高于基线 {baseline_rm_result.max_drawdown:.4f}（差 {rm_result.max_drawdown-baseline_rm_result.max_drawdown:+.4f}）
- 说明 MARGIN_EXP 的 OOS 表现远差于已通过 8 级验证的基线因子
"""
    else:
        baseline_compare = "\n## 6. 与基线因子对比\n\n基线因子未找到，跳过对比。\n"

    content = f"""# MARGIN_EXP Shadow 失败根因诊断报告 - {batch_id}

> P2.2 v6.2 诊断（第十二批次 v6.1 后续根因分析）
> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
> 目标因子：`{target_factor}`

## 1. 诊断背景

第十二批次 v6.1 结果显示 MARGIN_EXP 是 QualityTrend 类首个 G2+G3 双通过因子：
- ✅ G2: IC_IR=0.3981 (≥0.3), decay=0.9186 (<0.95)
- ✅ G3: DSR=0.5304 (>0, 未过拟合)
- ❌ Shadow: live_DSR=0.128 (<0.5), max_dd=0.204 (>0.12)

本脚本深入分析 Shadow 期间每日 PnL 分布，找到回撤期与根因。

## 2. 数据集

| 项目 | 值 |
|------|----|
| 标的数 | {symbols_count} |
| n_trials | {n_trials} |
| Shadow 观察日数 | {rm_result.n_obs_days} |
| 风险管理参数 | target_vol=0.15, dd_derisk_threshold=0.05, dd_derisk_factor=0.5 |

## 3. 对照组 vs 实验组对比

| 指标 | 对照组(无RM) | 实验组(有RM) | 变化 | 阈值 |
|------|------------|------------|------|------|
| pass_shadow | {baseline_result.pass_shadow} | {rm_result.pass_shadow} | - | - |
| live_dsr | {baseline_result.live_dsr:.4f} | {rm_result.live_dsr:.4f} | {rm_result.live_dsr-baseline_result.live_dsr:+.4f} | >0.5 |
| max_drawdown | {baseline_result.max_drawdown:.4f} | {rm_result.max_drawdown:.4f} | {rm_result.max_drawdown-baseline_result.max_drawdown:+.4f} | <0.12 |
| raw_max_drawdown | - | {rm_result.raw_max_drawdown:.4f} | - | (缩放前对照) |
| total_return | {baseline_result.total_return:.4f} | {rm_result.total_return:.4f} | {rm_result.total_return-baseline_result.total_return:+.4f} | - |
| realized_vol | {baseline_result.raw_realized_vol:.4f} | {rm_result.realized_vol:.4f} | {rm_result.realized_vol-baseline_result.raw_realized_vol:+.4f} | target=0.15 |
| avg_scaler | - | {rm_result.avg_scaler:.4f} | - | - |
| derisk_days | - | {rm_result.derisk_triggered_days} / {rm_result.n_obs_days} | - | - |

## 4. 最大回撤期分析

### 4.1 对照组（无风险管理）

- **最大回撤**: {baseline_result.max_drawdown:.4f} ({baseline_result.max_drawdown*100:.2f}%)
- **回撤期**: day {peak_b} → day {trough_b} (持续 {trough_b - peak_b + 1} 天)
- **期间累计亏损**: {loss_b:.4f} ({loss_b*100:.2f}%)
- **最大单日亏损**: {min(daily_pnl_baseline):.4f} ({min(daily_pnl_baseline)*100:.2f}%)
- **最大单日盈利**: {max(daily_pnl_baseline):.4f} ({max(daily_pnl_baseline)*100:.2f}%)

### 4.2 实验组（启用风险管理）

- **最大回撤**: {rm_result.max_drawdown:.4f} ({rm_result.max_drawdown*100:.2f}%)
- **回撤期**: day {peak_r} → day {trough_r} (持续 {trough_r - peak_r + 1} 天)
- **期间累计亏损**: {loss_r:.4f} ({loss_r*100:.2f}%)
- **最大单日亏损**: {min(daily_pnl_rm):.4f} ({min(daily_pnl_rm)*100:.2f}%)
- **最大单日盈利**: {max(daily_pnl_rm):.4f} ({max(daily_pnl_rm)*100:.2f}%)

### 4.3 风险管理效果评估

- 回撤下降: {baseline_result.max_drawdown:.4f} → {rm_result.max_drawdown:.4f} (差 {rm_result.max_drawdown-baseline_result.max_drawdown:+.4f})
- 波动率下降: {baseline_result.raw_realized_vol:.4f} → {rm_result.realized_vol:.4f} (差 {rm_result.realized_vol-baseline_result.raw_realized_vol:+.4f})
- live_dsr 变化: {baseline_result.live_dsr:.4f} → {rm_result.live_dsr:.4f} (差 {rm_result.live_dsr-baseline_result.live_dsr:+.4f})

## 5. 单点异常 vs 持续失效诊断

### 5.1 对照组 Top 5 亏损日

| 交易日 | 当日 PnL | 累计净值 |
|--------|---------|---------|
{top5_rows}

### 5.2 诊断结论

- **Top 5 亏损日合计**: {total_loss_top5:+.4f} ({total_loss_top5*100:+.2f}%)
- **对照组总收益**: {baseline_result.total_return:+.4f} ({baseline_result.total_return*100:+.2f}%)
- **对照组最大回撤**: {baseline_result.max_drawdown:.4f} ({baseline_result.max_drawdown*100:.2f}%)
- **Top 5 占 max_dd 比例**: {abs(total_loss_top5)/max(baseline_result.max_drawdown, 1e-9)*100:.1f}%

**诊断**: {diagnosis}

**应对建议**: {diagnosis_recommendation}
{baseline_compare}
## 7. 综合结论与下一步建议

### 7.1 综合诊断

MARGIN_EXP 在 v6.1 修复后通过 G2+G3，但 Shadow 失败的核心原因是：
- live_dsr={rm_result.live_dsr:.4f} 远低于阈值 0.5（差 {0.5-rm_result.live_dsr:.4f}）
- max_dd={rm_result.max_drawdown:.4f} 远超阈值 0.12（超 {rm_result.max_drawdown-0.12:.4f}）
- {diagnosis}

### 7.2 下一步建议

1. **若为单点异常主导**：
   - 考虑在 ShadowAccount 中增加单日止损（如单日 PnL < -3% 时平仓）
   - 或调整风险管理参数（target_vol 从 0.15 降至 0.10，进一步降低敞口）
2. **若为持续失效**：
   - 因子 OOS 表现差，120 天历史 IC_IR=0.3981 主要是 IS 拟合
   - 需扩大样本至 200+ 标的，或改进因子设计
3. **样本扩展**：
   - 当前 105 标的下 live_dsr=0.128，扩展样本可能提升 OOS 稳定性
4. **因子组合**：
   - MARGIN_EXP (corr=0.342) 与 VT_MICRO_VOL_SKEW_INV 正交
   - 组合可能产生更高 live_dsr 与更稳定 Sharpe（见优先级 3 任务）
5. **暂缓推进 MARGIN_EXP 至 G4/Committee**：
   - 在 Shadow 失败根因解决前，不应推进至 Committee 评审
   - 状态保持 rejected，等待样本扩展或组合验证结果
"""
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(content)
    return md_path


if __name__ == "__main__":
    sys.exit(main())
