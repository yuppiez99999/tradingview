# -*- coding: utf-8 -*-
"""S6 FactorKillSwitch 实时监控启动器

将 FactorKillSwitch 接入生产环境，监控现有 51 个生产因子 + 16 个候选因子。
每日输入：因子 IC + 因子多空 PnL
每日输出：状态机转移 + 仓位调整建议 + 监控仪表盘

本脚本可独立运行（不依赖 daily_workflow.py），也可被 daily_workflow 集成调用。

执行流程：
1. 加载现有 51 个生产因子（通过 AlphaFactorLibrary.compute_all）
2. 加载 16 个候选因子（从首批次 pipeline_state.json 读取）
3. 用最近 30 日真实数据模拟历史 IC + PnL 序列
4. 调用 FactorKillSwitch.update() 逐日推进状态机
5. 输出实时监控仪表盘（Markdown + JSON）
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from research.vibe_trading_factor_analysis.scripts.real_data_loader import (
    load_all_for_pipeline, list_available_symbols,
)
from research.vibe_trading_factor_analysis.safety.factor_kill_switch import (
    FactorKillSwitch, FactorStatus, KillSwitchStatus,
)

logger = logging.getLogger("kill_switch_monitor")
REPORTS_DIR = _PROJECT_ROOT / "research" / "vibe_trading_factor_analysis" / "reports" / "kill_switch"
HISTORY_DAYS = 30  # 历史监控窗口


def main() -> int:
    """主入口：S6 启用 FactorKillSwitch 实时监控

    Returns:
        0=成功, 1=失败
    """
    parser = argparse.ArgumentParser(description="Run the FactorKillSwitch monitor")
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="Use synthetic demo data when real market data is unavailable",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s | %(message)s",
    )
    logger.info("=" * 80)
    logger.info("S6 FactorKillSwitch 实时监控启动")
    logger.info("=" * 80)

    if args.synthetic:
        logger.info("\n[1/5] 使用合成 Demo 数据（跳过依赖市场数据）")
        price_data, fundamentals, benchmark_returns, existing_factors, candidate_factors = _load_synthetic_demo_inputs()
        logger.info("  ✓ 合成因子/价格数据已就绪")
    else:
        # ============ Step 1: 加载真实数据 ============
        logger.info("\n[1/5] 加载真实 A 股历史数据")
        symbols = list_available_symbols()
        logger.info(f"  可用标的数: {len(symbols)}")
        price_data, fundamentals, benchmark_returns = load_all_for_pipeline(symbols=symbols)

        # ============ Step 2: 加载现有 51 个生产因子 ============
        logger.info("\n[2/5] 加载现有生产因子库（51 个）")
        try:
            from utils.alpha_factor_library import AlphaFactorLibrary
            lib = AlphaFactorLibrary()
            result = lib.compute_all(
                price_data=price_data,
                fundamentals=fundamentals,
                benchmark_returns=benchmark_returns,
            )
            existing_factors = dict(result.factors)
            logger.info(f"  ✓ 现有生产因子加载: {len(existing_factors)} 个")
        except Exception as e:
            logger.info(f"  ✗ 现有因子库加载失败: {e}")
            existing_factors = {}

        # ============ Step 3: 加载首批次候选因子 ============
        logger.info("\n[3/5] 加载首批次候选因子（16 个）")
        candidate_factors = _load_first_batch_candidates()
        logger.info(f"  ✓ 候选因子加载: {len(candidate_factors)} 个")

    # ============ Step 4: 初始化 KillSwitch 并模拟历史监控 ============
    logger.info(f"\n[4/5] 初始化 KillSwitch + 模拟 {HISTORY_DAYS} 日历史监控")
    ks = FactorKillSwitch()

    # 合并监控列表：现有因子 + 候选因子（候选因子标记为"vibe_trading 候选"）
    all_monitored = {}
    for name, fv in existing_factors.items():
        all_monitored[name] = {"values": fv.values, "origin": "existing"}
    for name, values in candidate_factors.items():
        all_monitored[name] = {"values": values, "origin": "vibe_trading_candidate"}

    logger.info(f"  监控因子总数: {len(all_monitored)}")

    # ============ Step 5: 跑 30 日历史监控 ============
    logger.info(f"\n[5/5] 跑 {HISTORY_DAYS} 日历史监控（演示状态转移）")
    logger.info("-" * 80)
    ic_pnl_series = _compute_historical_ic_pnl(all_monitored, price_data, HISTORY_DAYS)

    # 逐日推进状态机
    for day_idx in range(HISTORY_DAYS):
        day_label = f"Day -{HISTORY_DAYS - day_idx}"
        for factor_name in all_monitored:
            ic, pnl = ic_pnl_series[factor_name][day_idx]
            ks.update(factor_name, ic=ic, daily_pnl=pnl, timestamp=day_label)

    # ============ 汇总监控状态 ============
    all_states = ks.list_all()
    state_dist = Counter(s.status for s in all_states.values())
    origin_dist = defaultdict(lambda: Counter())
    for name, s in all_states.items():
        origin = all_monitored.get(name, {}).get("origin", "historical")
        origin_dist[origin][s.status] += 1

    logger.info(f"\n========== 监控仪表盘（{HISTORY_DAYS} 日后）==========")
    logger.info(f"  监控总数: {len(all_states)}")
    logger.info("\n  状态分布（全部）:")
    for state, cnt in state_dist.most_common():
        logger.info(f"    {state:20s}: {cnt}")
    logger.info("\n  按来源分布:")
    for origin, dist in origin_dist.items():
        logger.info(f"    {origin}:")
        for state, cnt in dist.most_common():
            logger.info(f"      {state:18s}: {cnt}")

    # ============ 触发记录 ============
    triggered = [(n, s) for n, s in all_states.items() if s.status != FactorStatus.ACTIVE.value]
    logger.info(f"\n  非活跃因子 ({len(triggered)}):")
    for name, s in sorted(triggered, key=lambda kv: kv[1].status):
        last_trigger = s.triggers[-1] if s.triggers else "-"
        print(f"    {name:35s} | {s.status:18s} | pos={s.current_position_ratio:.2f} | "
              f"IC={s.last_ic:+.4f} | DD={s.cumulative_drawdown:.3f} | {last_trigger}")

    # ============ 持久化仪表盘 ============
    md_path, json_path = _persist_dashboard(
        ks=ks,
        all_monitored=all_monitored,
        history_days=HISTORY_DAYS,
        state_dist=state_dist,
        origin_dist=origin_dist,
    )
    logger.info(f"\n  监控仪表盘写入: {md_path}")
    logger.info(f"  JSON 状态写入: {json_path}")

    # ============ 提供 daily 集成入口 ============
    logger.info("\n" + "=" * 80)
    logger.info("S6 完成：FactorKillSwitch 实时监控已启动")
    logger.info("=" * 80)
    logger.info("\n  集成方式（供 daily_workflow 调用）:")
    logger.info("    from research.vibe_trading_factor_analysis.scripts.run_kill_switch_monitor import (")
    logger.info("        load_kill_switch_state, run_daily_update")
    logger.info("    )")
    logger.info("    ks = load_kill_switch_state()  # 加载持久化状态")
    logger.info("    new_status = run_daily_update(ks, factor_name, ic=0.05, daily_pnl=0.001)")
    print()
    return 0


def _load_synthetic_demo_inputs() -> Tuple[Dict[str, Dict[str, List[float]]], Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Dict[str, float]]]:
    """构造可独立运行的合成 demo 数据，便于验证状态机行为。"""

    class _SyntheticFactor:
        def __init__(self, values: Dict[str, float]):
            self.values = values

    symbols = [f"000{idx:03d}_SZ" for idx in range(1, 9)]
    price_data: Dict[str, Dict[str, List[float]]] = {}
    for idx, sym in enumerate(symbols):
        closes: List[float] = []
        prev = 10.0 + idx * 0.25
        for day in range(HISTORY_DAYS + 8):
            drift = 0.002 * ((day % 6) - 3)
            prev *= 1 + drift + 0.0003 * (idx % 4)
            closes.append(prev)
        price_data[sym] = {"closes": closes}

    existing_factors = {
        "MOM_5D": _SyntheticFactor({sym: 0.02 * (idx % 4 - 1.5) for idx, sym in enumerate(symbols)}),
        "VOL_20D": _SyntheticFactor({sym: 0.01 * (idx % 3) for idx, sym in enumerate(symbols)}),
    }
    candidate_factors = {
        "SENTIMENT": {sym: 0.015 * ((idx + 1) % 2 - 0.5) for idx, sym in enumerate(symbols)},
        "LIQUIDITY": {sym: 0.012 * ((idx % 3) - 1) for idx, sym in enumerate(symbols)},
    }
    return price_data, {}, {}, existing_factors, candidate_factors


def _load_first_batch_candidates() -> Dict[str, Dict[str, float]]:
    """加载首批次候选因子值"""
    candidates_path = (
        _PROJECT_ROOT
        / "research/vibe_trading_factor_analysis/reports/vibe_trading"
        / "first_batch_20260725_103736/pipeline_state.json"
    )
    if not candidates_path.exists():
        logger.warning("首批次 pipeline_state.json 不存在: %s", candidates_path)
        return {}
    with open(candidates_path, "r", encoding="utf-8") as f:
        json.load(f)
    # pipeline_state.json 中的 factors 列表只保留了 g1/g2 结果，没有原始 values
    # 改为重新计算候选因子（调用 adapter）
    try:
        from research.vibe_trading_factor_analysis.adapters.vibe_trading_factor_adapter import (
            VibeTradingFactorAdapter,
        )
        from research.vibe_trading_factor_analysis.scripts.real_data_loader import (
            load_all_for_pipeline, list_available_symbols,
        )
        symbols = list_available_symbols()
        price_data, fundamentals, bench = load_all_for_pipeline(symbols=symbols)
        adapter = VibeTradingFactorAdapter()
        pool = adapter.compute_candidate_factors(
            price_data=price_data, fundamentals=fundamentals, benchmark_returns=bench,
        )
        return {name: cf.values for name, cf in pool.factors.items()}
    except Exception as e:
        logger.error("重算候选因子失败: %s", e)
        return {}


def _compute_historical_ic_pnl(
    all_monitored: Dict[str, Dict[str, Any]],
    price_data: Dict[str, Dict[str, List[float]]],
    history_days: int,
) -> Dict[str, List[Tuple[float, float]]]:
    """计算每个因子的历史 IC + PnL 序列

    简化实现：
    - 用最近 history_days 日每日 cross-sectional IC（因子值 vs 5d forward return）
    - 用因子多空组合的当日 PnL 作为 daily_pnl
    """
    series: Dict[str, List[Tuple[float, float]]] = {}

    # 准备每个标的的收盘价序列
    sym_closes = {sym: data["closes"] for sym, data in price_data.items() if "closes" in data}
    if not sym_closes:
        return series

    # 找最长长度
    max_len = max(len(c) for c in sym_closes.values())
    start_idx = max_len - history_days - 5  # 留 5d forward return 余量
    if start_idx < 0:
        start_idx = 0

    for factor_name, info in all_monitored.items():
        factor_values = info["values"]
        if not factor_values:
            series[factor_name] = [(0.0, 0.0)] * history_days
            continue

        ic_pnl_list: List[Tuple[float, float]] = []
        for day_offset in range(history_days):
            day_idx = start_idx + day_offset
            # 计算当日 IC（因子值 vs 5d forward return）
            x_vals, y_vals = [], []
            for sym, fv in factor_values.items():
                closes = sym_closes.get(sym, [])
                if not isinstance(fv, (int, float)) or not math_isfinite(fv):
                    continue
                if day_idx + 5 < len(closes) and closes[day_idx] > 0:
                    fwd_ret = closes[day_idx + 5] / closes[day_idx] - 1
                    x_vals.append(float(fv))
                    y_vals.append(float(fwd_ret))

            if len(x_vals) >= 5 and np.std(x_vals) > 1e-12 and np.std(y_vals) > 1e-12:
                ic = float(np.corrcoef(x_vals, y_vals)[0, 1])
                if not math_isfinite(ic):
                    ic = 0.0
            else:
                ic = 0.0

            # 简化：当日 PnL = 因子值排序 TopN 多空组合当日收益
            pnl = _compute_daily_factor_pnl(factor_values, sym_closes, day_idx)
            ic_pnl_list.append((ic, pnl))

        series[factor_name] = ic_pnl_list
    return series


def math_isfinite(x: float) -> bool:
    """兼容性 isfinite 检查"""
    try:
        import math
        return math.isfinite(x)
    except (TypeError, ValueError):
        return False


def _compute_daily_factor_pnl(
    factor_values: Dict[str, float],
    sym_closes: Dict[str, List[float]],
    day_idx: int,
) -> float:
    """计算当日多空组合 PnL（简化版）"""
    valid = [(s, v) for s, v in factor_values.items()
             if isinstance(v, (int, float)) and math_isfinite(v)]
    if len(valid) < 4:
        return 0.0

    n_each = max(1, len(valid) // 4)
    sorted_syms = sorted(valid, key=lambda x: x[1])
    short_syms = [s for s, _ in sorted_syms[:n_each]]
    long_syms = [s for s, _ in sorted_syms[-n_each:]]

    long_rets, short_rets = [], []
    for sym in long_syms:
        closes = sym_closes.get(sym, [])
        if day_idx + 1 < len(closes) and closes[day_idx] > 0:
            long_rets.append(closes[day_idx + 1] / closes[day_idx] - 1)
    for sym in short_syms:
        closes = sym_closes.get(sym, [])
        if day_idx + 1 < len(closes) and closes[day_idx] > 0:
            short_rets.append(closes[day_idx + 1] / closes[day_idx] - 1)

    if not long_rets or not short_rets:
        return 0.0
    return float(np.mean(long_rets) - np.mean(short_rets))


def _persist_dashboard(
    ks: FactorKillSwitch,
    all_monitored: Dict[str, Dict[str, Any]],
    history_days: int,
    state_dist: Counter,
    origin_dist: Dict[str, Counter],
) -> Tuple[Path, Path]:
    """持久化监控仪表盘到 Markdown + JSON"""
    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_dir = REPORTS_DIR / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    md_path = batch_dir / "KILL_SWITCH_DASHBOARD.md"
    json_path = batch_dir / "kill_switch_state.json"

    # 持久化 JSON 状态
    all_states = ks.list_all()
    json_state = {
        "batch_id": batch_id,
        "history_days": history_days,
        "total_monitored": len(all_states),
        "state_distribution": dict(state_dist),
        "origin_distribution": {origin: dict(dist) for origin, dist in origin_dist.items()},
        "factors": {name: s.to_dict() for name, s in all_states.items()},
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_state, f, ensure_ascii=False, indent=2, default=str)

    # 持久化 Markdown 仪表盘
    total = len(all_states)
    active_cnt = state_dist.get(FactorStatus.ACTIVE.value, 0)
    active_rate = active_cnt / total * 100 if total > 0 else 0

    # 非活跃因子详情表
    triggered = [
        (n, s) for n, s in all_states.items()
        if s.status != FactorStatus.ACTIVE.value
    ]
    triggered_sorted = sorted(triggered, key=lambda kv: (kv[1].status, -kv[1].cumulative_drawdown))
    triggered_md = ""
    for i, (name, s) in enumerate(triggered_sorted, 1):
        origin = all_monitored.get(name, {}).get("origin", "historical")
        last_trigger = s.triggers[-1] if s.triggers else "-"
        triggered_md += (
            f"| {i} | `{name}` | {origin} | {s.status} | "
            f"{s.current_position_ratio:.2f} | {s.last_ic:+.4f} | "
            f"{s.cumulative_drawdown:.3f} | {s.consecutive_low_ic_days}d | "
            f"{s.consecutive_neg_ic_days}d | {last_trigger} |\n"
        )

    # 状态分布 Markdown
    state_md = ""
    for state, cnt in state_dist.most_common():
        rate = cnt / total * 100 if total > 0 else 0
        state_md += f"| {state} | {cnt} | {rate:.1f}% |\n"

    # 来源分布
    origin_md = ""
    for origin, dist in origin_dist.items():
        for state, cnt in dist.most_common():
            origin_md += f"| {origin} | {state} | {cnt} |\n"

    existing_cnt = sum(1 for v in all_monitored.values() if v['origin'] == 'existing')
    candidate_cnt = sum(1 for v in all_monitored.values() if v['origin'] == 'vibe_trading_candidate')

    # 注意：代码示例块用普通字符串拼接，避免 f-string 把 {factor_name} 当变量
    integration_code = (
        "```python\n"
        "from research.vibe_trading_factor_analysis.scripts.run_kill_switch_monitor import (\n"
        "    load_kill_switch_state, run_daily_update,\n"
        ")\n\n"
        "# Phase 9: FactorKillSwitch 实时监控（新增）\n"
        "ks = load_kill_switch_state()  # 加载持久化状态\n"
        "for factor_name, factor_ic, factor_pnl in daily_factor_metrics:\n"
        "    new_status = run_daily_update(ks, factor_name, ic=factor_ic, daily_pnl=factor_pnl)\n"
        "    if not new_status.is_tradable:\n"
        "        logger.warning(f\"因子 {factor_name} 触发 KillSwitch: {new_status.status}\")\n"
        "        # 调整生产仓位 / 禁用信号源\n"
        "```\n"
    )

    content = f"""# FactorKillSwitch 实时监控仪表盘

> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
> 批次：{batch_id}
> 历史监控窗口：{history_days} 日

## 1. 监控概览

| 项目 | 值 |
|------|-----|
| 监控因子总数 | {total} |
| 现有生产因子 | {existing_cnt} |
| 候选因子（vibe_trading） | {candidate_cnt} |
| ACTIVE 比例 | {active_cnt}/{total} ({active_rate:.1f}%) |

## 2. 锁定参数（DECISION v1.0）

| 触发条件 | 阈值 | 动作 |
|----------|------|------|
| 连续 5 日 IC < 0.02 | DEGRADED | 仓位减半 |
| 连续 10 日 IC < 0 | DISABLED | 自动禁用 |
| 连续 20 日 IC < 0 | RETIRED | 强制退役（不可恢复） |
| 单日回撤 > 3% | DEGRADED | 仓位减半 |
| 累计回撤 > 8% | DEGRADED | 仓位减至 25% |
| 累计回撤 > 12% | EMERGENCY_EXIT | 全部退出 |

## 3. 状态分布

| 状态 | 因子数 | 占比 |
|------|--------|------|
{state_md}

## 4. 按来源 × 状态分布

| 来源 | 状态 | 因子数 |
|------|------|--------|
{origin_md}

## 5. 非活跃因子详情

| # | 因子 | 来源 | 状态 | 仓位 | 最后 IC | 累计回撤 | 连续低IC日 | 连续负IC日 | 最后触发 |
|---|------|------|------|------|---------|---------|----------|----------|---------|
{triggered_md}

## 6. 集成方式

本监控可被 daily_workflow.py 集成调用：

{integration_code}

## 7. 审计轨迹

完整状态持久化于：
```
reports/kill_switch/{batch_id}/kill_switch_state.json
```

包含每个因子的：
- 当前状态 + 仓位比例
- IC 历史（最近 {history_days} 日）
- PnL 历史
- 触发记录列表
- 累计回撤 / 连续低 IC 日数 / 连续负 IC 日数

---
*本仪表盘由 FactorKillSwitch v1.0 自动生成，遵循 DECISION_v1.0 锁定参数。*
"""
    md_path.write_text(content, encoding="utf-8")
    return md_path, json_path


def load_kill_switch_state(state_file: Path = None) -> FactorKillSwitch:
    """从持久化 JSON 加载 KillSwitch 状态

    Args:
        state_file: 状态文件路径；None 表示使用最新批次

    Returns:
        已加载历史状态的 FactorKillSwitch 实例
    """
    if state_file is None:
        # 找最新批次
        if not REPORTS_DIR.exists():
            return FactorKillSwitch()
        batches = sorted([d for d in REPORTS_DIR.iterdir() if d.is_dir()])
        if not batches:
            return FactorKillSwitch()
        state_file = batches[-1] / "kill_switch_state.json"

    if not state_file.exists():
        return FactorKillSwitch()

    with open(state_file, "r", encoding="utf-8") as f:
        state = json.load(f)

    ks = FactorKillSwitch()
    for name, factor_state in state.get("factors", {}).items():
        ks.init(name)
        s = ks.get_status(name)
        if s:
            s.status = factor_state.get("status", FactorStatus.ACTIVE.value)
            s.current_position_ratio = factor_state.get("current_position_ratio", 1.0)
            s.consecutive_low_ic_days = factor_state.get("consecutive_low_ic_days", 0)
            s.consecutive_neg_ic_days = factor_state.get("consecutive_neg_ic_days", 0)
            s.peak_pnl = factor_state.get("peak_pnl", 0.0)
            s.current_pnl = factor_state.get("current_pnl", 0.0)
            s.cumulative_drawdown = factor_state.get("cumulative_drawdown", 0.0)
            s.last_ic = factor_state.get("last_ic", 0.0)
            s.ic_history = factor_state.get("ic_history", [])
            s.pnl_history = factor_state.get("pnl_history", [])
            s.triggers = factor_state.get("triggers", [])
            s.last_update = factor_state.get("last_update", "")
    logger.info("[LoadState] 加载 %d 个因子状态", len(ks.list_all()))
    return ks


def run_daily_update(
    ks: FactorKillSwitch,
    factor_name: str,
    ic: float,
    daily_pnl: float,
    timestamp: str = "",
) -> KillSwitchStatus:
    """每日更新单个因子状态（供 daily_workflow 集成调用）

    Args:
        ks: FactorKillSwitch 实例
        factor_name: 因子名称
        ic: 当日 IC
        daily_pnl: 当日 PnL
        timestamp: 时间戳

    Returns:
        更新后的 KillSwitchStatus
    """
    return ks.update(factor_name, ic=ic, daily_pnl=daily_pnl, timestamp=timestamp)


if __name__ == "__main__":
    sys.exit(main())
