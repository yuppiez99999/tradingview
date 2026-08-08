"""FactorKillSwitch 每日运行器（daily_workflow Phase 9 集成适配器）

本模块封装 FactorKillSwitch 的「每日增量更新」流程，专为 daily_workflow.py
Phase 9 调用而设计。与 run_kill_switch_monitor.py 的 main() 区别：
    - main() 是一次性 30 日历史模拟（用于 S6 启动演示）
    - 本 runner 是每日增量更新（用于 daily_workflow 集成）

设计原则：
    1. 幂等：以 trade_date 作为批次 ID，同一天重跑不产生重复批次目录
    2. 异常隔离：失败不抛异常到 daily_workflow，返回 SKIP 状态
    3. 自包含：单次调用即完成 load_state → update → persist → dashboard 全流程
    4. 复用既有接口：调用 load_kill_switch_state / run_daily_update（不重复造轮子）

输入：
    trade_date: 交易日期（YYYY-MM-DD）

输出：
    {
        "status": "PASS" | "SKIP" | "FAIL",
        "trade_date": str,
        "batch_dir": str,
        "total_monitored": int,
        "state_distribution": {status: count},
        "triggered_today": [{factor, prev_status, new_status, trigger}],
        "dashboard_path": str,
        "state_json_path": str,
    }

集成方式（daily_workflow.py Phase 9）：
    from research.vibe_trading_factor_analysis.scripts.kill_switch_daily_runner import (
        run_daily_kill_switch,
        FACTOR_KS_READY,
    )
    if FACTOR_KS_READY:
        result = run_daily_kill_switch(trade_date=self.trade_date)
        self.state["phases"]["factor_kill_switch"] = result
"""
from __future__ import annotations

import json
import logging
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

# 项目根路径
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 模块就绪标志（供 daily_workflow 顶层导入检查）
FACTOR_KS_READY = True
try:
    from research.vibe_trading_factor_analysis.safety.factor_kill_switch import (
        FactorKillSwitch,  # noqa: F401
        FactorStatus,
        KillSwitchStatus,
    )
    from research.vibe_trading_factor_analysis.scripts.real_data_loader import (
        list_available_symbols,
        load_all_for_pipeline,
    )
except ImportError as _e:
    FACTOR_KS_READY = False
    _IMPORT_ERROR = str(_e)

logger = logging.getLogger("kill_switch_daily_runner")

# 监控窗口：每日更新前先用最近 N 日历史 IC + PnL 回放，再写入当日状态
# 选 5 日而非 30 日，避免与首批次 main() 重复占用计算资源；增量场景下大部分因子
# 的状态由「持久化状态」直接继承，仅最近 5 日用于触发延迟型状态转移（如连续 5d IC<0.02）
DEFAULT_HISTORY_DAYS = 5
REPORTS_DIR = _PROJECT_ROOT / "research" / "vibe_trading_factor_analysis" / "reports" / "kill_switch"


def run_daily_kill_switch(
    trade_date: str,
    history_days: int = DEFAULT_HISTORY_DAYS,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """每日运行 FactorKillSwitch 监控（daily_workflow Phase 9 入口）

    流程：
        1. 加载上一日持久化状态（load_kill_switch_state）
        2. 加载现有 51 生产因子 + 16 候选因子（当日值）
        3. 计算最近 history_days 日 IC + PnL 序列
        4. 逐日推进每个因子的状态机
        5. 持久化新状态到 reports/kill_switch/{trade_date}/
        6. 生成 Dashboard

    Args:
        trade_date: 交易日期（YYYY-MM-DD）
        history_days: 每日回放的历史天数（默认 5）
        force_refresh: True 时强制重新计算（忽略已有批次目录）

    Returns:
        状态字典（结构见模块 docstring）
    """
    if not FACTOR_KS_READY:
        return {
            "status": "SKIP",
            "reason": f"FactorKillSwitch 模块未就绪: {_IMPORT_ERROR}",
            "trade_date": trade_date,
        }

    # 幂等检查：同一天已跑过则直接返回（除非 force_refresh）
    batch_dir = REPORTS_DIR / trade_date
    state_json_path = batch_dir / "kill_switch_state.json"
    dashboard_path = batch_dir / "KILL_SWITCH_DASHBOARD.md"
    if batch_dir.exists() and state_json_path.exists() and not force_refresh:
        logger.info("[Phase9] %s 批次已存在，跳过（force_refresh=False）", trade_date)
        try:
            with open(state_json_path, encoding="utf-8") as f:
                cached = json.load(f)
            return {
                "status": "PASS",
                "trade_date": trade_date,
                "batch_dir": str(batch_dir),
                "total_monitored": cached.get("total_monitored", 0),
                "state_distribution": cached.get("state_distribution", {}),
                "triggered_today": cached.get("triggered_today", []),
                "dashboard_path": str(dashboard_path),
                "state_json_path": str(state_json_path),
                "cached": True,
            }
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("[Phase9] 读取缓存批次失败，重新计算: %s", e)

    try:
        # ============ Step 1: 加载上一日持久化状态 ============
        logger.info("[Phase9] Step1: 加载上一日 KillSwitch 状态")
        from research.vibe_trading_factor_analysis.scripts.run_kill_switch_monitor import (
            load_kill_switch_state,
        )
        ks = load_kill_switch_state()
        prev_status_map = {
            name: s.status for name, s in ks.list_all().items()
        }
        logger.info("[Phase9] 加载 %d 个因子历史状态", len(prev_status_map))

        # ============ Step 2: 加载因子库（现有 51 + 候选 16） ============
        logger.info("[Phase9] Step2: 加载因子库（现有 51 + 候选 16）")
        all_monitored = _load_all_monitored_factors()
        total_monitored = len(all_monitored)
        if total_monitored == 0:
            return {
                "status": "SKIP",
                "reason": "无可监控因子（因子库全部加载失败）",
                "trade_date": trade_date,
            }
        logger.info("[Phase9] 监控因子总数=%d", total_monitored)

        # ============ Step 3: 计算最近 history_days 日 IC + PnL 序列 ============
        logger.info("[Phase9] Step3: 计算最近 %d 日 IC + PnL 序列", history_days)
        symbols = list_available_symbols()
        price_data, _fundamentals, _benchmark_returns = load_all_for_pipeline(symbols=symbols)
        ic_pnl_series = _compute_historical_ic_pnl(all_monitored, price_data, history_days)

        # ============ Step 4: 逐日推进状态机 + 记录当日触发 ============
        logger.info("[Phase9] Step4: 逐日推进状态机")
        triggered_today: list[dict[str, Any]] = []
        for day_idx in range(history_days):
            day_label = f"{trade_date}-D-{history_days - day_idx}"
            for factor_name in all_monitored:
                ic, pnl = ic_pnl_series[factor_name][day_idx]
                prev_status = ks.get_status(factor_name)
                prev_status_str = prev_status.status if prev_status else "INIT"
                new_status = ks.update(
                    factor_name, ic=ic, daily_pnl=pnl, timestamp=day_label,
                )
                # 仅记录最后一日的新触发（避免历史回放触发淹没日志）
                if day_idx == history_days - 1 and new_status.status != prev_status_str:
                    last_trigger = new_status.triggers[-1] if new_status.triggers else ""
                    triggered_today.append({
                        "factor": factor_name,
                        "prev_status": prev_status_str,
                        "new_status": new_status.status,
                        "trigger": last_trigger,
                        "position_ratio": new_status.current_position_ratio,
                    })

        # ============ Step 5: 持久化状态 + 生成 Dashboard ============
        logger.info("[Phase9] Step5: 持久化 + Dashboard 生成")
        all_states = ks.list_all()
        state_dist = Counter(s.status for s in all_states.values())
        origin_dist = defaultdict(lambda: Counter())
        for name, s in all_states.items():
            origin = all_monitored.get(name, {}).get("origin", "historical")
            origin_dist[origin][s.status] += 1

        batch_dir.mkdir(parents=True, exist_ok=True)
        _persist_state_json(
            state_json_path=state_json_path,
            trade_date=trade_date,
            history_days=history_days,
            total_monitored=total_monitored,
            state_dist=state_dist,
            origin_dist=origin_dist,
            all_states=all_states,
            triggered_today=triggered_today,
        )
        _persist_dashboard_md(
            dashboard_path=dashboard_path,
            trade_date=trade_date,
            history_days=history_days,
            total_monitored=total_monitored,
            all_monitored=all_monitored,
            state_dist=state_dist,
            origin_dist=origin_dist,
            all_states=all_states,
            triggered_today=triggered_today,
        )

        active_cnt = state_dist.get(FactorStatus.ACTIVE.value, 0)
        logger.info(
            "[Phase9] 完成 | trade_date=%s total=%d active=%d (%.1f%%) triggered_today=%d",
            trade_date, total_monitored, active_cnt,
            active_cnt / max(1, total_monitored) * 100,
            len(triggered_today),
        )

        return {
            "status": "PASS",
            "trade_date": trade_date,
            "batch_dir": str(batch_dir),
            "total_monitored": total_monitored,
            "state_distribution": dict(state_dist),
            "triggered_today": triggered_today,
            "dashboard_path": str(dashboard_path),
            "state_json_path": str(state_json_path),
            "cached": False,
        }

    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:

        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        logger.exception("[Phase9] FactorKillSwitch 每日运行失败: %s", e)
        return {
            "status": "FAIL",
            "trade_date": trade_date,
            "error": str(e),
        }


# ============================================================
# 内部辅助函数
# ============================================================

def _load_all_monitored_factors() -> dict[str, dict[str, Any]]:
    """加载全部需要监控的因子（现有 51 + 候选 16）

    Returns:
        {factor_name: {"values": {symbol: float}, "origin": str}}
    """
    all_monitored: dict[str, dict[str, Any]] = {}

    # 现有 51 个生产因子
    try:
        symbols = list_available_symbols()
        price_data, fundamentals, benchmark_returns = load_all_for_pipeline(symbols=symbols)
        from utils.alpha_factor_library import AlphaFactorLibrary
        lib = AlphaFactorLibrary()
        result = lib.compute_all(
            price_data=price_data,
            fundamentals=fundamentals,
            benchmark_returns=benchmark_returns,
        )
        for name, fv in result.factors.items():
            all_monitored[name] = {"values": dict(fv.values), "origin": "existing"}
        logger.info("[Phase9] 现有生产因子加载: %d 个", len(all_monitored))
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        logger.warning("[Phase9] 现有因子库加载失败: %s", e)

    # 候选 16 个因子
    try:
        from research.vibe_trading_factor_analysis.adapters.vibe_trading_factor_adapter import (
            VibeTradingFactorAdapter,
        )
        adapter = VibeTradingFactorAdapter()
        pool = adapter.compute_candidate_factors(
            price_data=price_data,
            fundamentals=fundamentals,
            benchmark_returns=benchmark_returns,
        )
        cand_cnt = 0
        for name, cf in pool.factors.items():
            # 不覆盖现有同名因子
            if name not in all_monitored:
                all_monitored[name] = {"values": dict(cf.values), "origin": "vibe_trading_candidate"}
                cand_cnt += 1
        logger.info("[Phase9] 候选因子加载: %d 个", cand_cnt)
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        logger.warning("[Phase9] 候选因子加载失败: %s", e)

    return all_monitored


def _compute_historical_ic_pnl(
    all_monitored: dict[str, dict[str, Any]],
    price_data: dict[str, dict[str, list[float]]],
    history_days: int,
) -> dict[str, list[tuple[float, float]]]:
    """计算每个因子的最近 history_days 日 IC + PnL 序列

    复用 run_kill_switch_monitor._compute_historical_ic_pnl 的算法，但封装为
    本模块内部函数，避免循环依赖。
    """
    from research.vibe_trading_factor_analysis.scripts.run_kill_switch_monitor import (
        _compute_historical_ic_pnl as _compute,
    )
    return _compute(all_monitored, price_data, history_days)


def _persist_state_json(
    state_json_path: Path,
    trade_date: str,
    history_days: int,
    total_monitored: int,
    state_dist: Counter,
    origin_dist: dict[str, Counter],
    all_states: dict[str, KillSwitchStatus],
    triggered_today: list[dict[str, Any]],
) -> None:
    """持久化状态 JSON（供次日 load_kill_switch_state 加载）"""
    state = {
        "batch_id": trade_date,
        "trade_date": trade_date,
        "generated_at": datetime.now().isoformat(),
        "history_days": history_days,
        "total_monitored": total_monitored,
        "state_distribution": dict(state_dist),
        "origin_distribution": {origin: dict(dist) for origin, dist in origin_dist.items()},
        "triggered_today": triggered_today,
        "factors": {name: s.to_dict() for name, s in all_states.items()},
    }
    with open(state_json_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, default=str)
    logger.info("[Phase9] 状态 JSON 写入: %s", state_json_path)


def _persist_dashboard_md(
    dashboard_path: Path,
    trade_date: str,
    history_days: int,
    total_monitored: int,
    all_monitored: dict[str, dict[str, Any]],
    state_dist: Counter,
    origin_dist: dict[str, Counter],
    all_states: dict[str, KillSwitchStatus],
    triggered_today: list[dict[str, Any]],
) -> None:
    """生成 Markdown Dashboard（人类可读）"""
    active_cnt = state_dist.get(FactorStatus.ACTIVE.value, 0)
    active_rate = active_cnt / total_monitored * 100 if total_monitored > 0 else 0

    state_md = ""
    for state, cnt in state_dist.most_common():
        rate = cnt / total_monitored * 100 if total_monitored > 0 else 0
        state_md += f"| {state} | {cnt} | {rate:.1f}% |\n"

    origin_md = ""
    for origin, dist in origin_dist.items():
        for state, cnt in dist.most_common():
            origin_md += f"| {origin} | {state} | {cnt} |\n"

    # 非活跃因子详情（按状态 + 累计回撤排序）
    triggered = [
        (n, s) for n, s in all_states.items()
        if s.status != FactorStatus.ACTIVE.value
    ]
    triggered_sorted = sorted(triggered, key=lambda kv: (kv[1].status, -kv[1].cumulative_drawdown))
    triggered_detail_md = ""
    for i, (name, s) in enumerate(triggered_sorted, 1):
        origin = all_monitored.get(name, {}).get("origin", "unknown")
        last_trigger = s.triggers[-1] if s.triggers else "-"
        triggered_detail_md += (
            f"| {i} | `{name}` | {origin} | {s.status} | "
            f"{s.current_position_ratio:.2f} | {s.last_ic:+.4f} | "
            f"{s.cumulative_drawdown:.3f} | {s.consecutive_low_ic_days}d | "
            f"{s.consecutive_neg_ic_days}d | {last_trigger} |\n"
        )

    # 当日新触发表
    triggered_today_md = ""
    for t in triggered_today:
        triggered_today_md += (
            f"| `{t['factor']}` | {t['prev_status']} → {t['new_status']} | "
            f"{t['position_ratio']:.2f} | {t['trigger']} |\n"
        )
    if not triggered_today_md:
        triggered_today_md = "| - | 无新触发 | - | - |\n"

    existing_cnt = sum(1 for v in all_monitored.values() if v['origin'] == 'existing')
    candidate_cnt = sum(1 for v in all_monitored.values() if v['origin'] == 'vibe_trading_candidate')

    content = f"""# FactorKillSwitch 每日监控仪表盘

> 交易日期：{trade_date}
> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
> 监控窗口：最近 {history_days} 日增量更新

## 1. 监控概览

| 项目 | 值 |
|------|-----|
| 交易日期 | {trade_date} |
| 监控因子总数 | {total_monitored} |
| 现有生产因子 | {existing_cnt} |
| 候选因子（vibe_trading） | {candidate_cnt} |
| ACTIVE 比例 | {active_cnt}/{total_monitored} ({active_rate:.1f}%) |
| 当日新触发 | {len(triggered_today)} 个 |

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

## 5. 当日新触发（仅记录最后一日变化）

| 因子 | 状态转移 | 新仓位 | 触发原因 |
|------|---------|--------|---------|
{triggered_today_md}

## 6. 非活跃因子详情（全量）

| # | 因子 | 来源 | 状态 | 仓位 | 最后 IC | 累计回撤 | 连续低IC日 | 连续负IC日 | 最后触发 |
|---|------|------|------|------|---------|---------|----------|----------|---------|
{triggered_detail_md}

## 7. 审计轨迹

完整状态持久化于：
```
reports/kill_switch/{trade_date}/kill_switch_state.json
```

包含每个因子的：
- 当前状态 + 仓位比例
- IC 历史（最近 {history_days} 日）
- PnL 历史
- 触发记录列表
- 累计回撤 / 连续低 IC 日数 / 连续负 IC 日数

## 8. 集成入口

本批次由 `daily_workflow.py` Phase 9 自动调用 `run_daily_kill_switch(trade_date)` 生成。

---
*本仪表盘由 FactorKillSwitch v1.0 + kill_switch_daily_runner 自动生成。*
"""
    dashboard_path.write_text(content, encoding="utf-8")
    logger.info("[Phase9] Dashboard 写入: %s", dashboard_path)


# ============================================================
# 自检入口（独立运行，不依赖 daily_workflow）
# ============================================================

def _self_test(trade_date: str | None = None) -> int:
    """自检入口：单独跑一次每日监控（不依赖 daily_workflow）

    用法：
        python -m research.vibe_trading_factor_analysis.scripts.kill_switch_daily_runner
        python kill_switch_daily_runner.py --date 2026-07-25
    """
    import argparse
    parser = argparse.ArgumentParser(description="FactorKillSwitch 每日运行器（自检）")
    parser.add_argument("--date", default=trade_date or datetime.now().strftime("%Y-%m-%d"),
                        help="交易日期 YYYY-MM-DD")
    parser.add_argument("--history-days", type=int, default=DEFAULT_HISTORY_DAYS,
                        help=f"历史监控窗口天数（默认 {DEFAULT_HISTORY_DAYS}）")
    parser.add_argument("--force-refresh", action="store_true",
                        help="强制重新计算（忽略缓存）")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s | %(message)s",
    )
    logger.info("=" * 80)
    logger.info(f"FactorKillSwitch 每日运行器（自检） | trade_date={args.date}")
    logger.info("=" * 80)
    result = run_daily_kill_switch(
        trade_date=args.date,
        history_days=args.history_days,
        force_refresh=args.force_refresh,
    )
    logger.info(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("status") in ("PASS", "SKIP") else 1


if __name__ == "__main__":
    sys.exit(_self_test())
