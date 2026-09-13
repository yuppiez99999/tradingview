"""执行链风控「喂数」辅助 (P0-4 闭环, Issue #13 → PR #17)

**为什么单独成模块**: `daily_trade_executor.py` 有硬性结构护栏
(`tests/unit/test_daily_executor_premarket_split_20260910.py::test_host_line_count_stays_bounded`,
宿主 ≤ 1500 行)。P0-4 闭环新增的喂数逻辑属于"独立可测、与宿主无状态耦合"的部分,
迁出可同时满足护栏与可测性; 宿主以 ``X as X`` 显式重导出, 保持
``monkeypatch.setattr(daily_trade_executor, NAME, ...)`` 语义不变。

**背景**: `init_wt_modules()` 组装出 `RiskControl` 后, 全仓库 (排除测试) 对
`update_equity()` / `check_circuit_breaker()` / `check_position_concentration()`
的调用者为 0 —— 阈值接了、风控器建了, 但没有任何地方喂数。后果是
``max_equity == 0`` 与 ``daily_loss == 0`` 使熔断/集中度两个分支永远短路。
本模块补上"喂数"这一环。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def refresh_mark_prices(positions: dict) -> dict:
    """批量解析持仓盯市价 (P0-MTM, 2026-09-12)。

    ``est_price`` 是上次成交价 (含滑点), 持仓长期无新成交时严重偏离市价 →
    止损/熔断/权益全部失真。本函数拉取实时行情 (东财+腾讯双源, 60s 缓存),
    缺失标的回退 est_price (fail-open), 并把覆盖率落日志留痕。

    Args:
        positions: config/positions.json 的 positions 段 (键为 full_code)

    Returns:
        ``{pure_code: {"price", "source"}}`` — 离线/行情故障时为空 dict。
    """
    from utils.execution.mark_to_market import (
        fetch_mark_price_map,
        summarize_mark_coverage,
    )

    codes = [c for c in (positions or {}).keys() if isinstance(c, str)]
    mark_prices = fetch_mark_price_map(codes)
    rt_count, total_count = summarize_mark_coverage(mark_prices, codes)
    if total_count and rt_count < total_count:
        logger.warning(
            "[P0-MTM] 盯市覆盖率 %d/%d — 未覆盖标的回退 est_price "
            "(陈旧成交价, 止损/权益可能失真)",
            rt_count,
            total_count,
        )
    elif total_count:
        logger.info("[P0-MTM] 盯市覆盖率 %d/%d", rt_count, total_count)
    return mark_prices


def _compute_positions_equity(
    positions: dict, mark_prices: dict | None = None
) -> float | None:
    """由持仓明细估算组合市值权益 (P0-4 闭环: 为 RiskControl 喂数)。

    口径与 `_run_stop_loss_check` 一致 (avg_cost / phase1_shares|total_shares|shares
    / est_price), 避免另立一套字段解读。任一标的的 qty 或价格缺失时不参与求和,
    全部缺失则返回 None (调用方据此跳过喂数并告警, 不得用 0 冒充权益)。

    P0-MTM (2026-09-12): 价格优先用实时盯市价, 回退 est_price。

    Args:
        positions: config/positions.json 的 positions 段
        mark_prices: ``refresh_mark_prices`` 产物; None 时全部回退 est_price

    Returns:
        组合权益估值 (float); 无法估算时 None
    """
    from utils.execution.mark_to_market import resolve_position_mark_price

    total = 0.0
    counted = 0
    for _code, item in (positions or {}).items():
        if not isinstance(item, dict):
            continue
        qty = item.get("phase1_shares") or item.get("total_shares") or item.get("shares", 0)
        price, _source = resolve_position_mark_price(str(_code), item, mark_prices)
        try:
            qty_f = abs(float(qty))
            price_f = float(price)
        except (TypeError, ValueError):
            continue
        if qty_f <= 0 or price_f <= 0:
            continue
        total += qty_f * price_f
        counted += 1
    return total if counted > 0 else None


def _feed_risk_control_equity(
    wt_modules: dict, positions: dict, mark_prices: dict | None = None
) -> dict:
    """把真实组合权益喂给 RiskControl (P0-4 闭环 · 缺口 A)。

    原缺陷: `init_wt_modules` 组装出 RiskControl 后**没有任何地方持有或使用它** ——
    `update_equity()` / `record_trade_result()` / `check_circuit_breaker()` /
    `check_position_concentration()` 在全仓库 (排除测试) 调用者为 0。
    后果是 `max_equity == 0`、`daily_loss == 0`, 熔断与集中度两个分支永远短路,
    阈值接了也无效。本函数补上"喂数"这一环。

    语义: fail-open —— 估算不出权益时只告警并跳过, 不阻断交易链路
    (与 config/risk_thresholds.yaml 的 fail-open 不变量一致)。真正的阻断判定
    由 premarket 产出的 risk_checks.circuit_breaker 负责 (见执行前置检查)。

    Args:
        wt_modules: WonderTrader 模块 dict
        positions: 当前持仓 dict

    Returns:
        {"fed": bool, "equity": float|None, "reason": str}
    """
    rc = wt_modules.get("risk_control")
    if not rc:
        return {"fed": False, "equity": None, "reason": "risk_control 不可用"}
    equity = _compute_positions_equity(positions, mark_prices)
    if equity is None or equity <= 0:
        logger.warning(
            "[P0-4] 无法由持仓估算组合权益, 熔断/集中度分支仍将短路 "
            "(喂数跳过, fail-open — 阻断判定以 risk_checks.circuit_breaker 为准)"
        )
        return {"fed": False, "equity": None, "reason": "持仓明细不足以估算权益"}
    rc.update_equity(equity)
    logger.info(
        "[P0-4] 已向 RiskControl 喂入组合权益 %.2f (熔断/集中度分支解除短路)",
        equity,
    )
    return {"fed": True, "equity": equity, "reason": ""}


def check_circuit_breaker_gate(risk_checks: dict) -> dict | None:
    """消费 `risk_checks.circuit_breaker`, fail-closed。

    背景 (P0-4 未闭环的缺口 B): `executor/premarket._build_risk_checks` 已把
    `circuit_breaker.passed` 从硬编码 `True` 改成"缺数据即 `False` (UNKNOWN)",
    但执行前置检查只看 `daily_limit` —— 报告上从"假 PASS"变成"UNKNOWN",
    执行上却从"假放行"**原样保留为放行**, 缺陷只是在链路上位移了一次。

    语义: `passed is False` 一律阻断 (不区分"真超限"与"数据不可用")。
    风控一票否决路径上, "无法证明安全"不得等同于"安全"。

    向后兼容: `risk_checks` 无 `circuit_breaker` 段时返回 `None` (不阻断) ——
    P0-4 落地前生成的老指令文件不含该字段, 不得因此被判阻断。

    Args:
        risk_checks: 指令文件的 risk_checks 段

    Returns:
        阻断结果 dict; None 表示通过
    """
    cb = risk_checks.get("circuit_breaker")
    if cb is None or cb.get("passed", False):
        return None

    unknown = cb.get("daily_loss_pct") is None or cb.get("portfolio_drawdown_pct") is None
    return {
        "status": "blocked",
        "reason": "熔断数据不可用 (UNKNOWN, fail-closed)" if unknown else "熔断/回撤检查未通过",
        "blocked_reason": "circuit_breaker not passed (P0-4 fail-closed)",
        "circuit_breaker": {
            "daily_loss_pct": cb.get("daily_loss_pct"),
            "portfolio_drawdown_pct": cb.get("portfolio_drawdown_pct"),
            "source": cb.get("source", ""),
        },
    }
