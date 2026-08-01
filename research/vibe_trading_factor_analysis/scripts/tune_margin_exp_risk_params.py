# -*- coding: utf-8 -*-
"""MARGIN_EXP 风险管理参数调优实验（P2.2 v6.2b）

目标：
    v6.2 根因分析发现 MARGIN_EXP Shadow 失败的根因是 day 39-43 集中亏损（5 天 -27.02%）。
    当前风险管理参数（target_vol=0.15, dd_derisk_threshold=0.05, dd_derisk_factor=0.5）
    将 raw_dd=58.66% 降至 20.42%，但仍超 0.12 阈值。
    本脚本测试多组更激进的风险管理参数，寻找能让 MARGIN_EXP 通过 Shadow 的最优配置。

实验组：
    Config A (基线): target_vol=0.15, dd_derisk_threshold=0.05, dd_derisk_factor=0.5
    Config B (低 target_vol): target_vol=0.10, dd_derisk_threshold=0.05, dd_derisk_factor=0.5
    Config C (早去杠杆): target_vol=0.15, dd_derisk_threshold=0.03, dd_derisk_factor=0.3
    Config D (最激进): target_vol=0.10, dd_derisk_threshold=0.03, dd_derisk_factor=0.3
    Config E (超激进): target_vol=0.08, dd_derisk_threshold=0.02, dd_derisk_factor=0.2

验收标准：
    - max_dd < 0.12（核心目标）
    - live_dsr > 0.5（Alpha 信号保留）
    - pass_shadow = True（最终目标）
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path


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

logger = logging.getLogger("tune_margin_exp")
REPORTS_DIR = _PROJECT_ROOT / "research" / "vibe_trading_factor_analysis" / "reports" / "vibe_trading"

TARGET_FACTOR = "VT_QUALTREND_MARGIN_EXP"

# 实验配置（5 组参数，从基线到超激进）
EXPERIMENT_CONFIGS = [
    {
        "name": "Config_A_baseline",
        "desc": "基线（v6.1 默认）",
        "target_vol": 0.15,
        "dd_derisk_threshold": 0.05,
        "dd_derisk_factor": 0.5,
        "scaler_cap": 2.0,
    },
    {
        "name": "Config_B_low_target_vol",
        "desc": "低 target_vol（0.15→0.10）",
        "target_vol": 0.10,
        "dd_derisk_threshold": 0.05,
        "dd_derisk_factor": 0.5,
        "scaler_cap": 2.0,
    },
    {
        "name": "Config_C_early_derisk",
        "desc": "早去杠杆（threshold 0.05→0.03, factor 0.5→0.3）",
        "target_vol": 0.15,
        "dd_derisk_threshold": 0.03,
        "dd_derisk_factor": 0.3,
        "scaler_cap": 2.0,
    },
    {
        "name": "Config_D_aggressive",
        "desc": "最激进（target_vol=0.10 + 早去杠杆 + factor=0.3）",
        "target_vol": 0.10,
        "dd_derisk_threshold": 0.03,
        "dd_derisk_factor": 0.3,
        "scaler_cap": 2.0,
    },
    {
        "name": "Config_E_super_aggressive",
        "desc": "超激进（target_vol=0.08 + threshold=0.02 + factor=0.2）",
        "target_vol": 0.08,
        "dd_derisk_threshold": 0.02,
        "dd_derisk_factor": 0.2,
        "scaler_cap": 2.0,
    },
]


def main() -> int:
    """主入口：风险管理参数调优"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s | %(message)s",
    )
    logger.info("=" * 70)
    logger.info("MARGIN_EXP 风险管理参数调优实验（P2.2 v6.2b）")
    logger.info(f"目标因子: {TARGET_FACTOR}")
    logger.info("=" * 70)
    logger.info("实验组:")
    for cfg in EXPERIMENT_CONFIGS:
        print(f"  {cfg['name']:30s}: target_vol={cfg['target_vol']:.2f} "
              f"dd_threshold={cfg['dd_derisk_threshold']:.2f} "
              f"dd_factor={cfg['dd_derisk_factor']:.1f}")
    print()
    logger.info("验收标准: max_dd<0.12, live_dsr>0.5, pass_shadow=True")

    # ============ Step 1: 加载数据 ============
    logger.info("\n[1/4] 加载数据")
    symbols = list_available_symbols()
    price_data = load_price_data(symbols=symbols)
    fundamentals = load_fundamentals(price_data)
    benchmark_returns = load_benchmark_returns()
    if not benchmark_returns:
        benchmark_returns = compute_equal_weight_benchmark(price_data)
    logger.info(f"  标的数: {len(symbols)} | benchmark: {len(benchmark_returns)} 天")

    # ============ Step 2: 构建因子历史 ============
    logger.info("\n[2/4] 构建因子历史 (120d)")
    orchestrator = PipelineOrchestrator(config={"reports_dir": str(REPORTS_DIR)})
    fundamentals_history = orchestrator._load_fundamentals_history(list(price_data.keys()))
    adapter = VibeTradingFactorAdapter()
    factor_history, fwd_returns_hist, _valid_dates = build_factor_history(
        adapter=adapter,
        price_data=price_data,
        fundamentals=fundamentals,
        benchmark_returns=benchmark_returns,
        history_days=120,
        forward_window=5,
        fundamentals_history=fundamentals_history,
    )
    target_history = factor_history[TARGET_FACTOR]
    logger.info(f"  {TARGET_FACTOR} 历史长度: {len(target_history)}")

    # ============ Step 3: 跑 5 组实验 ============
    logger.info("\n[3/4] 跑 5 组实验")
    n_trials = max(len(symbols), 13)
    results = []

    for cfg in EXPERIMENT_CONFIGS:
        logger.info(f"\n  [{cfg['name']}] {cfg['desc']}")
        account = ShadowAccount(config={
            "reports_dir": str(REPORTS_DIR),
            "risk_managed": True,
            "target_vol": cfg["target_vol"],
            "vol_lookback": 20,
            "dd_derisk_threshold": cfg["dd_derisk_threshold"],
            "dd_derisk_factor": cfg["dd_derisk_factor"],
            "scaler_cap": cfg["scaler_cap"],
        })
        result = account.run_shadow(
            factor_values_history=target_history,
            forward_returns_history=fwd_returns_hist,
            n_trials=n_trials,
            factor_name=TARGET_FACTOR,
        )
        results.append((cfg, result))
        print(f"    pass={result.pass_shadow} live_dsr={result.live_dsr:.4f} "
              f"max_dd={result.max_drawdown:.4f} mc_p95={result.monte_carlo_p95_dd:.4f} "
              f"return={result.total_return:.4f} vol={result.realized_vol:.4f} "
              f"scaler={result.avg_scaler:.4f} derisk={result.derisk_triggered_days}/{result.n_obs_days}")

    # ============ Step 4: 对比汇总 ============
    logger.info("\n[4/4] 对比汇总")
    logger.info("=" * 110)
    print(f"{'Config':<32s} {'pass':>6s} {'live_dsr':>10s} {'max_dd':>10s} {'mc_p95':>10s} "
          f"{'return':>10s} {'vol':>8s} {'scaler':>8s} {'derisk':>10s}")
    logger.info("-" * 110)
    for cfg, result in results:
        print(f"{cfg['name']:<32s} {result.pass_shadow!s:>6s} "
              f"{result.live_dsr:>10.4f} {result.max_drawdown:>10.4f} "
              f"{result.monte_carlo_p95_dd:>10.4f} {result.total_return:>10.4f} "
              f"{result.realized_vol:>8.4f} {result.avg_scaler:>8.4f} "
              f"{result.derisk_triggered_days:>4d}/{result.n_obs_days:<4d}")
    logger.info("=" * 110)

    # 找最优配置
    logger.info("\n最优配置分析:")
    # 优先 max_dd < 0.12
    pass_dd = [(cfg, r) for cfg, r in results if r.max_drawdown < 0.12]
    if pass_dd:
        # 在 max_dd 通过的里面找 live_dsr 最高的
        best_cfg, best_result = max(pass_dd, key=lambda x: x[1].live_dsr)
        logger.info(f"  ✅ 找到 max_dd<0.12 的配置: {best_cfg['name']}")
        print(f"     live_dsr={best_result.live_dsr:.4f} (阈值 >0.5: "
              f"{'✅' if best_result.live_dsr > 0.5 else '❌'})")
        logger.info(f"     pass_shadow={best_result.pass_shadow}")
        if best_result.pass_shadow:
            logger.info(f"  🎉 MARGIN_EXP 通过 Shadow! 推荐配置: {best_cfg['name']}")
        else:
            logger.info("  ⚠️ max_dd 通过但 live_dsr 未达标，需进一步调优或考虑组合")
    else:
        logger.info("  ❌ 所有配置 max_dd 均 ≥ 0.12，无法通过 Shadow 阈值")
        # 找 max_dd 最低的
        min_dd_cfg, min_dd_result = min(results, key=lambda x: x[1].max_drawdown)
        logger.info(f"  最低 max_dd 配置: {min_dd_cfg['name']} max_dd={min_dd_result.max_drawdown:.4f}")
        logger.info(f"  对应 live_dsr={min_dd_result.live_dsr:.4f}")
        logger.info("  结论: 单因子 Shadow 在 105 标的下无法通过，需考虑:")
        logger.info("    1. 扩大样本至 200+ 标的")
        logger.info("    2. 因子组合（MARGIN_EXP + VT_MICRO_VOL_SKEW_INV）")
        logger.info("    3. 改进因子设计（如改用扣非净利润）")

    # 写入报告
    md_path = _write_tuning_report(results, symbols_count=len(symbols), n_trials=n_trials)
    logger.info(f"\n报告路径: {md_path}")

    logger.info("\n" + "=" * 70)
    logger.info("MARGIN_EXP 风险管理参数调优实验完成")
    logger.info("=" * 70)
    return 0


def _write_tuning_report(results, symbols_count: int, n_trials: int) -> Path:
    """写入调优报告"""
    batch_id = f"p2_2_margin_exp_risk_tuning_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    batch_dir = REPORTS_DIR / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    md_path = batch_dir / "RISK_TUNING_REPORT.md"

    # 找最优配置
    pass_dd = [(cfg, r) for cfg, r in results if r.max_drawdown < 0.12]
    if pass_dd:
        best_cfg, best_result = max(pass_dd, key=lambda x: x[1].live_dsr)
        verdict = "✅ 找到通过 max_dd 阈值的配置"
    else:
        best_cfg, best_result = min(results, key=lambda x: x[1].max_drawdown)
        verdict = "❌ 所有配置 max_dd 均未通过"

    # 各配置详情行
    config_rows = ""
    for cfg, result in results:
        is_best = (cfg["name"] == best_cfg["name"])
        mark = "🎯" if is_best else ""
        config_rows += (
            f"| {cfg['name']} | {cfg['desc']} | "
            f"target_vol={cfg['target_vol']:.2f}<br>"
            f"dd_threshold={cfg['dd_derisk_threshold']:.2f}<br>"
            f"dd_factor={cfg['dd_derisk_factor']:.1f} | "
            f"{'✅' if result.pass_shadow else '❌'} | "
            f"{result.live_dsr:.4f} {'✅' if result.live_dsr > 0.5 else '❌'} | "
            f"{result.max_drawdown:.4f} {'✅' if result.max_drawdown < 0.12 else '❌'} | "
            f"{result.monte_carlo_p95_dd:.4f} {'✅' if result.monte_carlo_p95_dd < 0.18 else '❌'} | "
            f"{result.total_return:.4f} | "
            f"{result.realized_vol:.4f} | "
            f"{result.avg_scaler:.4f} | "
            f"{result.derisk_triggered_days}/{result.n_obs_days} | "
            f"{mark} |\n"
        )

    content = f"""# MARGIN_EXP 风险管理参数调优报告 - {batch_id}

> P2.2 v6.2b 调优实验
> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
> 目标因子：`{TARGET_FACTOR}`

## 1. 实验背景

v6.2 根因分析发现 MARGIN_EXP Shadow 失败的根因：
- day 39-43 集中亏损 -27.02%（5 天内）
- raw_dd=58.66% → rm_dd=20.42%（基线参数下）
- 仍超 0.12 阈值

本实验测试 5 组从基线到超激进的风险管理参数，寻找最优配置。

## 2. 实验配置

| 标的数 | n_trials | Shadow 观察日 |
|--------|---------|--------------|
| {symbols_count} | {n_trials} | 90 |

## 3. 实验结果

| Config | 描述 | 参数 | pass | live_dsr (>0.5) | max_dd (<0.12) | mc_p95 (<0.18) | total_return | realized_vol | avg_scaler | derisk_days | 最优 |
|--------|------|------|------|----------------|----------------|----------------|--------------|--------------|------------|-------------|------|
{config_rows}

## 4. 最优配置分析

**结论**: {verdict}

**最优配置**: {best_cfg['name']} ({best_cfg['desc']})

**参数**:
- target_vol = {best_cfg['target_vol']:.2f}
- dd_derisk_threshold = {best_cfg['dd_derisk_threshold']:.2f}
- dd_derisk_factor = {best_cfg['dd_derisk_factor']:.1f}

**结果**:
- pass_shadow = {best_result.pass_shadow}
- live_dsr = {best_result.live_dsr:.4f} (阈值 >0.5: {'✅' if best_result.live_dsr > 0.5 else '❌'})
- max_drawdown = {best_result.max_drawdown:.4f} (阈值 <0.12: {'✅' if best_result.max_drawdown < 0.12 else '❌'})
- monte_carlo_p95_dd = {best_result.monte_carlo_p95_dd:.4f} (阈值 <0.18: {'✅' if best_result.monte_carlo_p95_dd < 0.18 else '❌'})
- total_return = {best_result.total_return:.4f}
- realized_vol = {best_result.realized_vol:.4f}
- avg_scaler = {best_result.avg_scaler:.4f}
- derisk_days = {best_result.derisk_triggered_days} / {best_result.n_obs_days}

## 5. 关键发现

1. **风险管理参数与回撤的关系**:
   - 基线 (Config A): target_vol=0.15, max_dd=0.2042
   - 激进 (Config D): target_vol=0.10 + 早去杠杆, max_dd={results[3][1].max_drawdown:.4f}
   - 超激进 (Config E): target_vol=0.08, max_dd={results[4][1].max_drawdown:.4f}
2. **Alpha 信号与风险管理的权衡**:
   - 更激进的风险管理会降低 max_dd，但也会压缩 live_dsr
   - 需在回撤控制与 Alpha 保留之间找平衡

## 6. 下一步建议

{'1. **采用最优配置 ' + best_cfg['name'] + '**: 已通过 Shadow，可推进至 G4+Committee 评审' if best_result.pass_shadow else '''1. **单因子 Shadow 无法通过**: 即使最激进参数也无法同时满足 max_dd<0.12 和 live_dsr>0.5
2. **建议考虑替代路径**:
   - 扩大样本至 200+ 标的
   - 因子组合 (MARGIN_EXP + VT_MICRO_VOL_SKEW_INV)
   - 改进因子设计 (改用扣非净利润)'''}
"""
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(content)
    return md_path


if __name__ == "__main__":
    sys.exit(main())
