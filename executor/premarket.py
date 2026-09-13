"""盘前指令生成簇 —— 2026-09-10 自 ``daily_trade_executor.py`` 拆出。

为什么用 ``_h().NAME`` 而不是直接 import
----------------------------------------
测试通过 ``monkeypatch.setattr(daily_trade_executor, "INSTRUCTIONS_DIR", tmp_path)``
这类方式替换宿主模块属性, 并要求被调用的实现看到替换后的值。若本模块写成
``from daily_trade_executor import INSTRUCTIONS_DIR``, 取到的便是**导入时刻的值**,
替换会静默失效 (经典 monkeypatch 盲区)。因此凡宿主模块持有的名字 (常量 / 函数 /
路径 / 别名) 一律以 ``_h().NAME`` 属性式读取 (调用时刻才查属性表); 真正定义在
本模块内的函数则直接调用。

宿主在文件末尾以 ``from executor.premarket import ...`` 回填命名空间, 故
``daily_trade_executor.NAME`` 与 ``from daily_trade_executor import NAME``
对外行为完全不变。
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("daily_trade_executor")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.append(str(_PROJECT_ROOT))

from utils.path_config import setup_sys_path  # noqa: E402

setup_sys_path()

from utils.datetime_utils import now_bj  # noqa: E402


def _h() -> Any:
    """Return the host module object (lazy, avoids an import cycle)."""
    mod = sys.modules.get("daily_trade_executor")
    if mod is not None:
        return mod
    main_mod = sys.modules.get("__main__")
    if main_mod is not None and str(getattr(main_mod, "__file__", "")).endswith(
        "daily_trade_executor.py"
    ):
        return main_mod
    import daily_trade_executor as _fallback  # noqa: PLC0415

    return _fallback


class TradePlanUnavailableError(RuntimeError):
    """交易计划文件缺失/损坏/无标的 —— 显式错误态.

    SC-1 修复 (2026-09-11): 原 ``load_trade_plan()`` 在计划文件缺失时静默返回
    空计划 ``{"stock_etf_account": {"positions": []}}``, 使 ``generate_instructions``
    误判为「所有标的已建仓完成」并返回**假完成状态** (每日 09:00 rc=0 但零指令产出),
    违背本项目「缺数据 ≠ 通过」铁律。现改为抛出本异常, 由调用方显式转 error 状态。
    """


def is_accumulation_period(d: date) -> bool:
    """检查是否处于建仓期"""
    return _h().ACCUMULATION_START <= d <= _h().ACCUMULATION_END


def load_trade_plan() -> dict:
    """加载交易计划.

    Raises:
        TradePlanUnavailableError: 计划文件不存在 / 不可读 / 非 JSON 对象 /
            或 `stock_etf_account.positions` 为空 (无有效标的)。

    SC-1 修复 (2026-09-11): 上述任一情形均**不再**降级为空计划, 因为空计划会让
    上游误判「已建仓完成」。失败必须可见 (error 状态 + 告警), 不允许静默通过。
    """
    path = _h().TRADE_PLAN_FILE
    if not path.exists():
        raise TradePlanUnavailableError(f"交易计划文件不存在: {path}")
    try:
        with open(path, encoding="utf-8") as f:
            plan = json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        raise TradePlanUnavailableError(f"交易计划文件不可读/格式错误: {path} ({e})") from e
    if not isinstance(plan, dict):
        raise TradePlanUnavailableError(f"交易计划根节点非对象: {path}")
    positions = (plan.get("stock_etf_account") or {}).get("positions")
    if not positions:
        raise TradePlanUnavailableError(
            f"交易计划无有效标的 (stock_etf_account.positions 为空): {path}"
        )
    return plan


def _report_trade_plan_unavailable(exc: Exception, target_date_str: str) -> None:
    """SC-1: 交易计划不可用的观测链 (降级审计 + 告警), 全程 fail-open 不掩盖错误状态."""
    reason = str(exc)
    logger.error("[SC-1] 交易计划不可用, 拒绝生成盘前指令: %s", reason)
    try:
        from utils.degradation_audit import record_degradation

        record_degradation(
            scope="executor.premarket",
            key="trade_plan_unavailable",
            default="返回 status=error, 不生成任何指令 (禁止假完成)",
            reason=reason,
        )
    except Exception as e:  # noqa: BLE001  # 观测路径 fail-open
        logger.warning("record_degradation 失败: %s", e)
    try:
        from utils.notify import send_alert

        send_alert(
            title="盘前指令生成失败: 交易计划不可用",
            content=f"{target_date_str} {reason}",
            level="critical",
        )
    except Exception as e:  # noqa: BLE001  # 观测路径 fail-open
        logger.warning("send_alert 失败: %s", e)


def assess_etf_signal(code: str, positions_data: dict) -> str:
    """评估ETF资金流信号强度

    基于持仓配置中的 etf_flow_signal 字段判断
    返回: "strong" / "medium" / "none"
    """
    pos = positions_data.get("positions", {}).get(code, {})
    if not isinstance(pos, dict):
        return "none"
    signal = pos.get("etf_flow_signal", "")
    if "强" in signal:
        return "strong"
    if "加仓" in signal or "中" in signal:
        return "medium"
    return "none"


def calculate_daily_budget(
    target_date: date, progress: dict, positions_data: dict
) -> dict:
    """计算当日建仓预算

    策略:
      - 2026-07-13起: 固定每日20万
      - 2026-07-10~07-12: 智能分批 (ETF信号强度)
      - 上限: 20万/日
    """
    remaining_total = _h().STOCK_ETF_TARGET - progress.get("total_built", 0)
    if remaining_total <= 0:
        return {
            "daily_budget": 0,
            "signal_strength": "completed",
            "remaining_total": 0,
            "remaining_days": 0,
            "reason": "已完成300万建仓目标",
        }

    remaining_days = _h().get_remaining_days(target_date)

    # 统计ETF信号强度
    strong_count = 0
    medium_count = 0
    none_count = 0
    for _code, pos in positions_data.get("positions", {}).items():
        if not isinstance(pos, dict):
            continue
        signal = pos.get("etf_flow_signal", "")
        if "强" in signal:
            strong_count += 1
        elif "加仓" in signal or "中" in signal:
            medium_count += 1
        else:
            none_count += 1

    # 2026-07-13起: 固定每日20万
    if target_date >= _h().FIXED_BUDGET_START:
        daily_budget = min(_h().DAILY_FIXED_BUDGET, remaining_total)
        return {
            "daily_budget": round(daily_budget, 2),
            "signal_strength": "fixed_200k",
            "strong_signal_count": strong_count,
            "medium_signal_count": medium_count,
            "remaining_total": remaining_total,
            "remaining_days": remaining_days,
            "base_daily": _h().DAILY_FIXED_BUDGET,
        }

    # 2026-07-10~07-12: 智能分批 (原逻辑)
    base_daily = remaining_total / max(remaining_days, 1)  # 防除零

    if strong_count >= 3:
        signal_strength = "strong"
        daily_budget = min(base_daily * 1.5, _h().SIGNAL_AMOUNTS["strong"])
    elif medium_count >= 3 or strong_count >= 1:
        signal_strength = "medium"
        daily_budget = min(base_daily * 1.0, _h().SIGNAL_AMOUNTS["medium"])
    else:
        signal_strength = "none"
        daily_budget = min(base_daily * 0.5, _h().SIGNAL_AMOUNTS["none"])

    # 应用单日上限 + 可用资金校验 (防止超资金下单)
    daily_budget = min(daily_budget, _h().DAILY_AMOUNT_LIMIT, remaining_total)
    if daily_budget <= 0:
        daily_budget = 0
        signal_strength = "insufficient_budget"

    return {
        "daily_budget": round(daily_budget, 2),
        "signal_strength": signal_strength,
        "strong_signal_count": strong_count,
        "medium_signal_count": medium_count,
        "remaining_total": remaining_total,
        "remaining_days": remaining_days,
        "base_daily": round(base_daily, 2),
    }


def load_latest_prices() -> dict[str, float]:
    """从最近的收盘报告读取最新价格

    优先级:
      1. v8.3_institutional/reports/daily_pnl_report_YYYY-MM-DD.json
      2. v8.3_institutional/reports/daily_pnl_report_YYYY-MM-DD.md
    """
    reports_dir = _h().PROJECT_ROOT / "v8.3_institutional" / "reports"
    if not reports_dir.exists():
        return {}

    # 找最新的 JSON 报告
    json_files = sorted(reports_dir.glob("daily_pnl_report_*.json"), reverse=True)
    if not json_files:
        return {}

    latest_file = json_files[0]
    try:
        with open(latest_file, encoding="utf-8") as f:
            report = json.load(f)
        prices = {}
        for detail in report.get("portfolio_pnl", {}).get("details", []):
            code = detail.get("code", "")
            # 标准化代码 (去 .SH/.SZ 后缀)
            code_clean = code.split(".")[0]
            close_price = detail.get("close_price", 0)
            if close_price and close_price > 0:
                prices[code_clean] = close_price
        return prices
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.error(f"读取最新价格失败: {e}")
        return {}


# 默认参考价 (二级兜底: 仅当 load_latest_prices() 收盘报告也无该代码时使用)
# Q-5 标注: 个股价格为历史快照会 stale, 正常路径走 load_latest_prices() 动态读取;
# ETF 价格相对稳定。未来可改为仅保留 ETF 兜底 + 个股无价时跳过 (需同步更新测试)。
DEFAULT_PRICES = {
    "588080": 2.26,
    "512880": 1.13,
    "510050": 3.09,
    "512800": 1.50,
    "515030": 1.71,
    "512760": 1.55,
    "512170": 0.31,
    "518880": 6.50,
    "688041": 363.46,
    "300308": 1194.90,
    "002371": 878.43,
    "603019": 103.99,
    "300033": 230.30,
    "300782": 92.83,
    "688017": 408.28,
    "300274": 100.00,
    "000408": 35.00,
    "601088": 40.00,
    "600276": 55.61,
    "600900": 27.77,
}


def adjust_allocation_by_signal(
    base_allocated: float, signal: dict, daily_budget: float
) -> tuple:
    """根据预测信号调整分配金额

    Args:
        base_allocated: 基础分配金额
        signal: 预测信号 (fetch_prediction_signals 返回的单项)
        daily_budget: 当日总预算

    Returns:
        (adjusted_allocated, signal_tag)
        signal_tag: "strong_buy"/"buy"/"neutral"/"caution"/"skip"/"signal_degraded"
    """
    if not signal:
        return base_allocated, "neutral"

    # P0-H6 (2026-09-13): 信号降级 (数据缺失 no_data / 预测异常 error) ≠ 真实中性 —
    # 原实现把降级当 NEUTRAL 照常走分配, "模型信号在大多数日子里没在工作但流程
    # 显示一切正常"。现: 降级信号保持基准分配 (不参与置信度加/减仓), tag 显式
    # 标注 signal_degraded, 由调用方聚合落 WARNING + 指令文件字段。
    method = str(signal.get("method", "")).lower()
    if method in ("no_data", "error"):
        return base_allocated, "signal_degraded"

    direction = signal.get("direction", "NEUTRAL")
    confidence = signal.get("confidence", 0)
    strength = signal.get("signal_strength", 0)

    # 强看空 + 高置信度 → 跳过
    if direction == "DOWN" and confidence >= 0.7 and strength <= -0.5:
        return 0, "skip"

    # 弱看空 + 中置信度 → 缩减 50%
    if direction == "DOWN" and confidence >= 0.5:
        return base_allocated * 0.5, "caution"

    # 强看多 + 高置信度 → 加码 30% (不超过单标的上限)
    if direction == "UP" and confidence >= 0.7 and strength >= 0.5:
        return min(base_allocated * 1.3, daily_budget * 0.30), "strong_buy"

    # 弱看多 → 加码 10%
    if direction == "UP" and confidence >= 0.5:
        return base_allocated * 1.1, "buy"

    return base_allocated, "neutral"


def _precheck_instructions_preconditions(
    target_date_str: str, target_date: date
) -> dict | None:
    """前置检查: 交易日和建仓期。

    Args:
        target_date_str: 目标日期字符串 (YYYY-MM-DD)
        target_date: 目标日期 date 对象

    Returns:
        未通过返回跳过结果 dict; 通过返回 None
    """
    if not _h().is_trading_day(target_date):
        return {"status": "skipped", "reason": f"{target_date_str} 非交易日(周末)"}
    if not is_accumulation_period(target_date):
        return {
            "status": "skipped",
            "reason": f"{target_date_str} 不在建仓期({_h().ACCUMULATION_START} ~ {_h().ACCUMULATION_END})",
        }
    return None


def _refresh_etf_flow(positions_file: Path) -> dict:
    """刷新 ETF 资金流信号并重新加载持仓配置。

    Args:
        positions_file: positions.json 路径

    Returns:
        重新加载后的 positions_data (失败时也返回当前持仓)
    """
    try:
        from utils.etf_flow_monitor import refresh_etf_flow_signals

        etf_result = refresh_etf_flow_signals(str(positions_file))
        if etf_result.get("status") == "success":
            logger.info(
                f"[INFO] ETF资金流信号刷新成功: 更新 {etf_result['updated_count']} 个标的, 检测到 {etf_result.get('signal_count', 0)} 条信号"  # noqa: E501
            )
            return _h().load_positions()
        logger.error(
            f"[WARN] ETF资金流信号刷新失败: {etf_result.get('message', 'unknown')}"
        )
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.error(f"[WARN] ETF资金流信号刷新模块加载失败: {e}")
    return _h().load_positions()


def _run_wt_risk_precheck(
    wt_modules: dict, positions_data: dict, progress: dict
) -> dict | None:
    """WT 风控预检查 (盘前阻断级)。

    P1-5 修复: 此前仅打印 risk_score 不阻断, 高风险组合仍生成指令。
    现在当 risk_score 超阈值或集中度超限时返回阻断标记,
    generate_instructions 据此过滤或标注 HIGH_RISK。

    Args:
        wt_modules: WonderTrader 模块 dict
        positions_data: 持仓配置
        progress: 建仓进度

    Returns:
        None 表示通过; dict 表示阻断 (含 reason/risk_score)
    """
    analyzer = wt_modules.get("portfolio_risk_analyzer")
    if not analyzer:
        # P1-3 修复 (2026-09-09): fail-close — 风控分析器缺失时阻断, 而非 return None 放行.
        # 原代码与 DTE-2 "决策路径 fail-close" 铁律矛盾: 风控防线恰在初始化失败场景失效.
        logger.error(
            "[BLOCK] WT风控分析器不可用 (wt_modules 未初始化), 盘前风控 fail-close 阻断"
        )
        try:
            from utils.notify import send_alert

            send_alert(
                "[BLOCK] WT风控分析器不可用 — 盘前阻断",
                "portfolio_risk_analyzer 未初始化, fail-close 阻断指令生成。请检查 wt_modules 初始化。",
                severity="ERROR",
            )
        except Exception as e:  # noqa: BLE001  # notify fail-open, 不阻断交易
            logger.exception(f"发送 WT 风控不可用告警失败, 已 fail-open: {e}")
        return {"reason": "WT风控分析器不可用", "risk_score": -1, "concentration_risk": -1}
    try:
        risk_summary = analyzer.analyze_portfolio(
            positions_data,
            progress.get("total_built", 0),
            _h().STOCK_ETF_TARGET,
        )
        risk_score = risk_summary.get("risk_score", 0)
        concentration = risk_summary.get("concentration_risk", 0)
        logger.info(f"[INFO] WT风控分析: 组合风险评分 {risk_score}")
        logger.info(f"[INFO]   - 集中度风险: {concentration}")
        logger.info(
            f"[INFO]   - 行业分布: {risk_summary.get('sector_distribution', 'N/A')}"
        )

        # P1-5: 盘前阻断级校验 (阈值与 _run_wt_risk_block_check 的集中度逻辑对齐)
        # risk_score >= 80 或集中度 >= 0.3 (30%) 时阻断
        try:
            score_val = float(risk_score) if risk_score is not None else 0.0
        except (TypeError, ValueError):
            score_val = 0.0
        try:
            conc_val = float(concentration) if concentration is not None else 0.0
        except (TypeError, ValueError):
            conc_val = 0.0

        if score_val >= 80 or conc_val >= 0.3:
            logger.warning(
                "[BLOCK] WT盘前风控阻断: risk_score=%.1f, concentration=%.3f (超阈值)",
                score_val,
                conc_val,
            )
            return {
                "status": "blocked",
                "reason": f"盘前风控超限: risk_score={score_val:.1f}, concentration={conc_val:.3f}",
                "risk_score": score_val,
                "concentration": conc_val,
            }
    except Exception as e:  # noqa: BLE001
        # DTE-2: 风控是决策路径, 崩溃时必须 fail-close 保守阻断, 而非静默放行
        # (否则"风控崩溃=无风控", 违反"决策路径 fail-close"铁律)。
        logger.error(f"[BLOCK] WT风控分析异常, 保守阻断: {e}")
        return {
            "status": "blocked",
            "reason": f"WT风控分析异常, 保守阻断: {e}",
            "risk_score": 1.0,
            "concentration": 1.0,
        }


def _compute_progress_ratio(target_date: date) -> float:
    """计算建仓进度比例 (已过交易日 / 总交易日)。

    Args:
        target_date: 目标日期

    Returns:
        进度比例 [0, 1]
    """
    from datetime import timedelta

    elapsed_days = 0
    current = _h().ACCUMULATION_START
    while current <= target_date:
        if _h().is_trading_day(current):
            elapsed_days += 1
        current += timedelta(days=1)
    total_accumulation_days = _h().get_remaining_days(_h().ACCUMULATION_START)
    return min(elapsed_days / max(total_accumulation_days, 1), 1.0)


def _collect_pending_positions(
    plan_positions: list, progress: dict, progress_ratio: float
) -> list:
    """收集所有未完成建仓的标的, 并计算缺口 (缺口大者优先)。

    Args:
        plan_positions: 交易计划中的标的列表
        progress: 建仓进度
        progress_ratio: 建仓进度比例

    Returns:
        未完成建仓的标的列表 (含 gap 字段, 已按缺口降序)
    """
    pending = []
    for pos in plan_positions:
        code = pos.get("code", "")
        target_amount = pos.get("amount", 0)
        built = progress.get("built_amounts", {}).get(code, 0)
        remaining = target_amount - built
        if remaining > 0:
            # 理论应建仓金额
            theoretical_built = target_amount * progress_ratio
            # 缺口 = 理论应建仓 - 实际已建仓 (正值表示落后于进度)
            gap = theoretical_built - built
            pending.append(
                {
                    "code": code,
                    "code_clean": code.split(".")[0],
                    "name": pos.get("name", ""),
                    "weight": pos.get("weight", 0),
                    "target_amount": target_amount,
                    "built": built,
                    "remaining": remaining,
                    "gap": gap,
                }
            )
    # 按缺口降序排序 (缺口大的优先买入)
    pending.sort(key=lambda x: x["gap"], reverse=True)
    return pending


def _compute_price_band(ref_price: float) -> tuple:
    """计算价格保护带 (最大/最小买入价)。

    Args:
        ref_price: 参考价

    Returns:
        (max_buy_price, min_buy_price) 元组
    """
    max_buy_price = round(ref_price * (1 + _h().PRICE_PROTECTION_PCT), 4)
    min_buy_price = round(ref_price * (1 - _h().PRICE_PROTECTION_PCT), 4)
    return max_buy_price, min_buy_price


def check_price_band_violation(
    inst: dict,
    is_sell: bool,
    exec_price: float,
    progress: dict | None = None,
) -> dict | None:
    """P0-H5 (2026-09-13): 执行端消费价格保护带 — 越带返回 SKIPPED 结果 dict。

    原实现 max_buy_price/min_buy_price 只用于盘前预算守卫, 执行端完全不校验。
    校验与保护带计算同源本模块, 避免"生成一处、消费缺失"的断链重演。

    Args:
        inst: 指令 (读 max_buy_price/min_buy_price; 缺省 None = 未带保护带, 放行)
        is_sell: 卖出方向 (卖单对照下限, 买单对照上限)
        exec_price: 含滑点执行价
        progress: 建仓进度 (SKIPPED 结果的 built_before/after 口径)

    Returns:
        SKIPPED 结果 dict (越带) 或 None (放行 / 指令未带保护带 / 字段异常 fail-open)
    """
    max_buy_price = inst.get("max_buy_price")
    min_buy_price = inst.get("min_buy_price")
    try:
        violated = None
        if max_buy_price is not None and not is_sell and exec_price > float(max_buy_price):
            violated = "exec_price_above_buy_band"
        elif min_buy_price is not None and is_sell and exec_price < float(min_buy_price):
            violated = "exec_price_below_sell_band"
    except (TypeError, ValueError):
        return None  # 字段异常 fail-open (告警由调用方日志覆盖)
    if violated is None:
        return None
    code = inst.get("full_code", "")
    built = progress.get("built_amounts", {}).get(code, 0) if progress else 0
    return {
        "code": code,
        "name": inst.get("name", code),
        "action": inst.get("action", "BUY"),
        "qty": 0,
        "fill_price": exec_price,
        "fill_amount": 0.0,
        "commission": 0.0,
        "transfer_fee": 0.0,
        "stamp_duty": 0.0,
        "total_cost": 0.0,
        "status": "SKIPPED",
        "reason": violated,
        "built_before": built,
        "built_after": built,
    }


def _allocate_position(
    pos: dict,
    target_date_str: str,
    daily_budget: float,
    remaining_budget: float,
    latest_prices: dict,
    prediction_signals: dict,
    positions_data: dict,
) -> tuple | None:
    """为单个标的分配预算并构建买入指令。

    分配策略: 按权重比例分配, 受单标的上限(当日预算30%)和价格保护带约束,
    高价股特殊处理 (100股最小手数)。

    Args:
        pos: 标的持仓信息 (含 code_clean/weight/remaining 等)
        target_date_str: 目标日期字符串
        daily_budget: 当日总预算
        remaining_budget: 剩余预算
        latest_prices: 最新价格字典
        prediction_signals: 预测信号字典
        positions_data: 持仓配置

    Returns:
        (instruction_dict, actual_amount) 元组; None 表示跳过该标的
    """
    code_clean = pos["code_clean"]
    ref_price = latest_prices.get(code_clean, 0)
    if not ref_price:
        # DTE-4: 行情缺失时不再静默用假价 10.0 分配 (会导致按 10 元/股虚增股数)。
        # 优先回退 DEFAULT_PRICES 历史价 (显式告警, 标记 stale), 连兜底也没有则跳过该标的。
        ref_price = DEFAULT_PRICES.get(code_clean, 0.0)
        if ref_price > 0:
            logger.warning(
                "[STALE] %s 无实时行情, 使用 DEFAULT_PRICES 历史价 %.2f (资金分配基于陈旧价)",
                code_clean,
                ref_price,
            )
        else:
            logger.warning(
                "[STALE] %s 无实时行情且无兜底价, 跳过该标的 (不按假价分配)", code_clean
            )
            return None

    max_buy_price, min_buy_price = _compute_price_band(ref_price)
    min_lot_cost = 100 * ref_price

    # 按权重分配预算 (权重10% → 分配剩余预算的10%)
    # 注: 旧公式 `* weight / 0.05 * 0.15` 等价于 weight*3, 会过度分配, 已修正为纯权重比例
    allocated = min(
        remaining_budget * pos["weight"], remaining_budget, pos["remaining"]
    )
    # 单标的上限: 当日预算的30% (20万预算下单标最多6万)
    allocated = min(allocated, daily_budget * 0.30)

    # v7.5+: 根据预测信号调整分配
    signal = prediction_signals.get(code_clean, {})
    allocated, signal_tag = adjust_allocation_by_signal(allocated, signal, daily_budget)
    # 强看空 → 跳过该标的
    if signal_tag == "skip" and allocated == 0:
        logger.warning(
            f"[WARN] 预测信号触发跳过: {code_clean} ({pos['name']}) - 强看空 (置信度 {signal.get('confidence', 0):.0%})"
        )
        return None
    # P0-H6 (2026-09-13): 信号降级 (no_data/error) 显式告警 — 不再静默当 NEUTRAL
    if signal_tag == "signal_degraded":
        logger.warning(
            "[H6] 预测信号降级: %s (%s) method=%s — 保持基准分配 (不参与置信度调仓)",
            code_clean,
            pos["name"],
            signal.get("method", "unknown"),
        )

    # 高价股处理: 如果 100 股成本 > 分配预算
    if min_lot_cost > allocated:
        # 如果 100 股成本超过当日预算的 50%, 跳过 (避免单标的占用过多预算)
        if min_lot_cost > daily_budget * 0.50:
            return None
        # 否则检查剩余预算是否足够买 100 股
        if remaining_budget < min_lot_cost:
            return None
        allocated = min_lot_cost  # 只买 100 股

    allocated = min(allocated, remaining_budget, pos["remaining"])

    # 估算购买数量 (100股整数倍)
    est_qty = int(allocated / max_buy_price / 100) * 100
    if est_qty <= 0:
        if allocated >= min_lot_cost:
            # 高价股路径: 上方已确保 remaining_budget >= min_lot_cost, 按一手执行
            est_qty = 100
        else:
            # P2-2 修复 (2026-09-01): 预算不足一手时跳过 (原强制 100 股属超预算下单)
            logger.warning(
                "[SKIP] %s 预算不足一手: allocated=%.2f < 一手成本≈%.2f",
                code_clean, allocated, min_lot_cost,
            )
            return None

    actual_amount = round(est_qty * ref_price, 2)

    # P2-2 修复: 预算守卫按价格带上限 (最坏成交成本) 校验,
    # 原用 ref_price 低估 — 若实际按带内高价成交会超预算
    worst_case_amount = round(est_qty * max_buy_price, 2)
    if worst_case_amount > remaining_budget:
        logger.warning(
            "[SKIP] %s 最坏成本超预算: %d股×%.2f(带顶)=%.2f > 剩余%.2f",
            code_clean, est_qty, max_buy_price, worst_case_amount, remaining_budget,
        )
        return None

    # 评估ETF信号 + 预测信号摘要 (供人工审核参考)
    etf_signal = assess_etf_signal(pos["code"], positions_data)
    pred_signal = prediction_signals.get(code_clean, {})

    instruction = {
        "instruction_id": f"{target_date_str.replace('-', '')}-{code_clean}",
        "code": code_clean,
        "full_code": pos["code"],
        "name": pos["name"],
        "action": "BUY",
        "qty": est_qty,
        "ref_price": ref_price,
        "max_buy_price": max_buy_price,
        "min_buy_price": min_buy_price,
        "estimated_amount": actual_amount,
        "weight": pos["weight"],
        "target_amount": pos["target_amount"],
        "built_before": pos["built"],
        "remaining_after": round(pos["remaining"] - actual_amount, 2),
        "etf_signal": etf_signal,
        "prediction_signal": {
            "direction": pred_signal.get("direction", "NEUTRAL"),
            "confidence": round(pred_signal.get("confidence", 0), 3),
            "target_price": round(pred_signal.get("target_price", 0), 2),
            "method": pred_signal.get("method", "no_data"),
            "tag": signal_tag,
        },
        "gap": round(pos["gap"], 2),
        "confirm": False,  # 默认未确认, 需人工改为 true
    }
    return instruction, actual_amount


def _load_latest_circuit_breaker_metrics() -> dict:
    """从最近一份收盘盈亏报告读取熔断判定所需的真实指标 (P0-4 修复).

    巡检发现 circuit_breaker 的 daily_loss_pct / portfolio_drawdown_pct /
    passed 三个字段全部硬编码 (0/0/True), 报告恒打印 PASS —— 熔断检查
    名存实亡且具有误导性。现改为读取 v8.3_institutional/reports 下最新
    daily_pnl_report_*.json:
      - daily_loss_pct ← net_performance.net_pnl_pct (最新一日净值盈亏, %)
      - portfolio_drawdown_pct ← risk_metrics.max_drawdown_pct (%, 负值)

    数据不可用时返回 {"available": False}; _build_risk_checks 据此标注
    UNKNOWN 而非伪造 PASS (fail-closed 语义: 数据缺失 ≠ 检查通过).
    """
    reports_dir = _h().PROJECT_ROOT / "v8.3_institutional" / "reports"
    if not reports_dir.exists():
        return {"available": False, "reason": f"报告目录不存在: {reports_dir}"}
    json_files = sorted(reports_dir.glob("daily_pnl_report_*.json"), reverse=True)
    if not json_files:
        return {"available": False, "reason": "无收盘盈亏报告"}
    try:
        with open(json_files[0], encoding="utf-8") as f:
            report = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return {"available": False, "reason": f"读取报告失败: {e}"}
    net = report.get("net_performance", {})
    risk = report.get("risk_metrics", {})
    if "net_pnl_pct" not in net or "max_drawdown_pct" not in risk:
        return {"available": False, "reason": "报告缺少 net_pnl_pct / max_drawdown_pct"}
    return {
        "available": True,
        "report_date": report.get("meta", {}).get("report_date", ""),
        "daily_loss_pct": float(net.get("net_pnl_pct", 0.0)),
        "portfolio_drawdown_pct": float(risk.get("max_drawdown_pct", 0.0)),
    }


def _fmt_cb_metrics(cb: dict) -> str:
    """格式化熔断指标供报告表格 (P0-4). 数据缺失返回 UNKNOWN 说明."""
    daily = cb.get("daily_loss_pct")
    drawdown = cb.get("portfolio_drawdown_pct")
    if daily is None or drawdown is None:
        return f"UNKNOWN ({cb.get('source', '数据不可用')})"
    return f"单日{daily:+.2f}% / 回撤{abs(drawdown):.2f}% ({cb.get('source', '')})"


def _build_risk_checks(total_allocated: float) -> dict:
    """构建风控检查字典。

    Args:
        total_allocated: 当日已分配总额

    Returns:
        风控检查字典
    """
    # P0-4 修复: 熔断检查读真实指标, 数据缺失时标 UNKNOWN (不再伪造 PASS)
    cb_data = _load_latest_circuit_breaker_metrics()
    if cb_data.get("available"):
        daily_loss_pct = cb_data["daily_loss_pct"]
        drawdown_pct = abs(cb_data["portfolio_drawdown_pct"])
        cb_passed = (
            daily_loss_pct >= -_h().DAILY_LOSS_STOP_PCT * 100
            and drawdown_pct <= _h().PORTFOLIO_DRAWDOWN_STOP_PCT * 100
        )
        cb_source = f"基于 {cb_data.get('report_date', '?')} 收盘报告"
    else:
        daily_loss_pct = None
        drawdown_pct = None
        cb_passed = False
        cb_source = f"数据不可用: {cb_data.get('reason', '?')} (UNKNOWN, 需人工核查)"
    return {
        "daily_limit": {
            "rule": f"单日金额上限 {_h().DAILY_AMOUNT_LIMIT:,}",
            "value": total_allocated,
            "limit": _h().DAILY_AMOUNT_LIMIT,
            "passed": total_allocated <= _h().DAILY_AMOUNT_LIMIT,
        },
        "price_protection": {
            "rule": f"价格保护带 {_h().PRICE_PROTECTION_PCT:.0%}",
            "passed": True,  # 已在每条指令中应用
        },
        "circuit_breaker": {
            "rule": f"单日亏损-{_h().DAILY_LOSS_STOP_PCT:.0%}/组合回撤-{_h().PORTFOLIO_DRAWDOWN_STOP_PCT:.0%}熔断",
            "daily_loss_pct": daily_loss_pct,
            "portfolio_drawdown_pct": drawdown_pct,
            "passed": cb_passed,
            "source": cb_source,
        },
        "manual_confirm": {
            "rule": "盘前人工确认 (confirm字段需为true)",
            "passed": False,  # 默认未确认
        },
    }


def _build_instruction_file(
    target_date_str: str,
    progress: dict,
    budget_info: dict,
    risk_checks: dict,
    instructions: list,
    total_allocated: float,
) -> dict:
    """构建指令文件字典 (含 meta/budget/risk/instructions)。

    Args:
        target_date_str: 目标日期字符串
        progress: 建仓进度
        budget_info: 预算信息
        risk_checks: 风控检查
        instructions: 指令列表
        total_allocated: 已分配总额

    Returns:
        指令文件字典
    """
    # P0-H6 (2026-09-13): 信号降级聚合 — 指令文件显式携带降级计数,
    # "模型信号没在工作"不再只藏在每条 instruction 的 method 字段里。
    degraded_count = sum(
        1
        for i in instructions
        if isinstance(i, dict) and i.get("prediction_signal", {}).get("tag") == "signal_degraded"
    )
    if degraded_count:
        logger.warning(
            "[H6] %d/%d 条指令的预测信号降级 (no_data/error) — 按基准分配执行",
            degraded_count,
            len(instructions),
        )
    return {
        "meta": {
            "instruction_date": target_date_str,
            "generated_at": now_bj().isoformat(),
            "phase": "phase_1_accumulation",
            "total_capital": _h().STOCK_ETF_TARGET,
            "total_built_before": progress.get("total_built", 0),
            "remaining_total": _h().STOCK_ETF_TARGET - progress.get("total_built", 0),
            "signal_degraded_count": degraded_count,
        },
        "budget_info": budget_info,
        "risk_checks": risk_checks,
        "instructions": instructions,
        "total_allocated": round(total_allocated, 2),
        "confirm_required": True,
        "confirm_instruction": "将每个 instruction 中的 confirm 字段改为 true, 然后运行 post-market 执行",
    }


def _save_instruction_file(target_date_str: str, instruction_file: dict) -> tuple:
    """保存指令文件 (JSON + Markdown 两个版本)。

    Args:
        target_date_str: 目标日期字符串
        instruction_file: 指令文件字典

    Returns:
        (output_file, md_file) 路径元组
    """
    _h().INSTRUCTIONS_DIR.mkdir(parents=True, exist_ok=True)
    output_file = _h().INSTRUCTIONS_DIR / f"{target_date_str}_instructions.json"
    # 原子写入指令文件, 防止进程中断导致文件损坏
    _h().atomic_write_json(output_file, instruction_file)

    # 生成 markdown 版本
    md_file = _h().INSTRUCTIONS_DIR / f"{target_date_str}_instructions.md"
    md_content = render_instructions_md(instruction_file)
    with open(md_file, "w", encoding="utf-8") as f:
        f.write(md_content)
    return output_file, md_file


def generate_instructions(target_date_str: str) -> dict:
    """盘前生成交易指令

    生成包含所有待买入标的的指令清单,
    默认 confirm=false, 等待人工确认后改为 true.

    分配策略: 按剩余目标金额比例分配当日预算
    (确保每个未完成建仓的标的都能获得合理份额)
    """
    target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()

    # 前置检查: 交易日 / 建仓期
    skip = _precheck_instructions_preconditions(target_date_str, target_date)
    if skip:
        return skip

    # 加载数据 (v7.5+: 盘前自动刷新ETF资金流信号)
    positions_data = _refresh_etf_flow(_h().POSITIONS_FILE)
    wt_modules = _h().init_wt_modules()
    # SC-1 修复 (2026-09-11): 计划缺失必须显式失败, 禁止降级为空计划 → 假完成状态
    try:
        trade_plan = _h().load_trade_plan()
    except TradePlanUnavailableError as e:
        _report_trade_plan_unavailable(e, target_date_str)
        return {
            "status": "error",
            "error_type": "trade_plan_unavailable",
            "reason": str(e),
            "target_date": target_date_str,
        }
    progress = _h().load_build_progress()
    latest_prices = _h().load_latest_prices()

    # v7.8+: WT风控预检查 (使用 WT PortfolioRiskAnalyzer)
    # P1-5: 盘前风控超阈值时阻断, 不再仅打印
    precheck_result = _run_wt_risk_precheck(wt_modules, positions_data, progress)
    if precheck_result and precheck_result.get("status") == "blocked":
        logger.warning(
            "[BLOCK] 盘前风控阻断, 停止生成指令: %s", precheck_result.get("reason")
        )
        return {
            "status": "blocked",
            "reason": precheck_result.get("reason", "WT盘前风控阻断"),
            "risk_score": precheck_result.get("risk_score"),
            "budget_info": {"daily_budget": 0},
        }

    # 获取预测信号 (v7.5+ 集成 tf_price_predictor, 失败时静默降级)
    pending_codes = [
        p.get("code", "").split(".")[0]
        for p in trade_plan.get("stock_etf_account", {}).get("positions", [])
    ]
    prediction_signals = _h().fetch_prediction_signals(pending_codes, horizon=5)
    if prediction_signals:
        up_count = sum(
            1 for s in prediction_signals.values() if s.get("direction") == "UP"
        )
        down_count = sum(
            1 for s in prediction_signals.values() if s.get("direction") == "DOWN"
        )
        logger.info(
            f"[INFO] 预测信号: {len(prediction_signals)} 个标的, 看多 {up_count}, 看空 {down_count}"
        )

    # 计算当日预算
    budget_info = calculate_daily_budget(target_date, progress, positions_data)
    if budget_info["daily_budget"] <= 0:
        return {
            "status": "completed",
            "reason": "已完成建仓目标",
            "budget_info": budget_info,
        }

    # 收集所有未完成建仓的标的 (按缺口降序, 缺口大的优先买入)
    progress_ratio = _compute_progress_ratio(target_date)
    plan_positions = trade_plan.get("stock_etf_account", {}).get("positions", [])
    pending_positions = _collect_pending_positions(
        plan_positions, progress, progress_ratio
    )

    if not pending_positions:
        return {"status": "completed", "reason": "所有标的已建仓完成"}

    # 轮换分配: 优先满足缺口大的标的
    daily_budget = budget_info["daily_budget"]
    instructions = []
    total_allocated = 0
    remaining_budget = daily_budget

    for pos in pending_positions:
        if remaining_budget < 100:
            break  # 预算耗尽
        result = _allocate_position(
            pos,
            target_date_str,
            daily_budget,
            remaining_budget,
            latest_prices,
            prediction_signals,
            positions_data,
        )
        if result is None:
            continue
        instruction, actual_amount = result
        instructions.append(instruction)
        total_allocated += actual_amount
        remaining_budget -= actual_amount

    # 风控检查 + 构建指令文件 (manual_confirm 不阻塞生成, 只标记需要确认)
    risk_checks = _build_risk_checks(total_allocated)
    instruction_file = _build_instruction_file(
        target_date_str,
        progress,
        budget_info,
        risk_checks,
        instructions,
        total_allocated,
    )
    output_file, md_file = _save_instruction_file(target_date_str, instruction_file)

    return {
        "status": "generated",
        "output_files": [str(output_file), str(md_file)],
        "instruction_count": len(instructions),
        "total_allocated": total_allocated,
        "budget_info": budget_info,
    }


def confirm_all_instructions(target_date_str: str) -> int:
    """自动确认指定日期的所有未确认指令"""
    instruction_file = _h().INSTRUCTIONS_DIR / f"{target_date_str}_instructions.json"
    if not instruction_file.exists():
        return 0

    with open(instruction_file, encoding="utf-8") as f:
        data = json.load(f)

    confirmed_count = 0
    for inst in data.get("instructions", []):
        if not inst.get("confirm", False):
            inst["confirm"] = True
            confirmed_count += 1

    if confirmed_count > 0:
        _h().atomic_write_json(instruction_file, data)

    return confirmed_count


def render_instructions_md(data: dict) -> str:
    """渲染交易指令 markdown 版本"""
    meta = data["meta"]
    budget = data["budget_info"]
    risk = data["risk_checks"]
    instructions = data["instructions"]

    lines = [
        f"# 交易指令清单 {meta['instruction_date']}",
        "",
        f"**生成时间**: {meta['generated_at']}",
        f"**阶段**: {meta['phase']} (建仓期 {_h().ACCUMULATION_START} ~ {_h().ACCUMULATION_END})",
        f"**目标总额**: {meta['total_capital']:,}",
        f"**已建仓**: {meta['total_built_before']:,.0f}",
        f"**剩余**: {meta['remaining_total']:,.0f}",
        "",
        "---",
        "",
        "## 当日预算",
        "",
        f"- **信号强度**: {budget.get('signal_strength', 'unknown')}",
        f"- **强信号数**: {budget.get('strong_signal_count', 0)}",
        f"- **弱信号数**: {budget.get('medium_signal_count', 0)}",
        f"- **基础日预算**: {budget.get('base_daily', 0):,.0f}",
        f"- **当日预算**: {budget.get('daily_budget', 0):,.0f}",
        f"- **剩余交易日**: {budget.get('remaining_days', 0)} 天",
        "",
        "## 风控检查",
        "",
        "| 检查项 | 规则 | 数值 | 上限 | 状态 |",
        "|--------|------|------|------|------|",
        f"| 单日金额上限 | {_h().DAILY_AMOUNT_LIMIT:,} | {data['total_allocated']:,.0f} | {_h().DAILY_AMOUNT_LIMIT:,} | {'PASS' if risk['daily_limit']['passed'] else 'FAIL'} |",  # noqa: E501
        f"| 价格保护带 | {_h().PRICE_PROTECTION_PCT:.0%} | - | - | {'PASS' if risk['price_protection']['passed'] else 'FAIL'} |",  # noqa: E501
        # P0-4 修复: 打印真实熔断指标 (原硬编码 0%/PASS); 数据缺失打印 UNKNOWN
        f"| 熔断停止 | 单日-{_h().DAILY_LOSS_STOP_PCT:.0%}/组合-{_h().PORTFOLIO_DRAWDOWN_STOP_PCT:.0%} | {_fmt_cb_metrics(risk['circuit_breaker'])} | - | {'PASS' if risk['circuit_breaker']['passed'] else ('FAIL' if risk['circuit_breaker'].get('daily_loss_pct') is not None else 'UNKNOWN')} |",  # noqa: E501
        "| 人工确认 | confirm=true | - | - | PENDING |",
        "",
        "## 交易指令",
        "",
        f"**总指令数**: {len(instructions)}",
        f"**总分配金额**: {data['total_allocated']:,.0f}",
        "",
        "| # | 代码 | 名称 | 动作 | 数量 | 参考价 | 最高买入价 | 最低买入价 | 估算金额 | ETF信号 | 已建仓 | 剩余 | 确认 |",  # noqa: E501
        "|---|------|------|------|------|--------|-----------|-----------|---------|---------|--------|------|------|",
    ]

    for idx, inst in enumerate(instructions, 1):
        confirm = "OK" if inst["confirm"] else "PENDING"
        lines.append(
            f"| {idx} | {inst['code']} | {inst['name']} | {inst['action']} | "
            f"{inst['qty']} | {inst['ref_price']:.4f} | {inst['max_buy_price']:.4f} | "
            f"{inst['min_buy_price']:.4f} | {inst['estimated_amount']:,.0f} | "
            f"{inst['etf_signal']} | {inst['built_before']:,.0f} | "
            f"{inst['remaining_after']:,.0f} | {confirm} |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 确认步骤",
            "",
            "1. 打开 JSON 文件: `"
            + data["meta"]["instruction_date"].replace("-", "")
            + "_instructions.json`",
            "2. 检查每条指令的 `qty`, `max_buy_price` 等参数",
            "3. 将需要执行的指令的 `confirm` 字段改为 `true`",
            "4. 运行: `python daily_trade_executor.py post-market --date "
            + data["meta"]["instruction_date"]
            + "`",
            "",
            "## 风控规则",
            "",
            f"- **单日金额上限**: {_h().DAILY_AMOUNT_LIMIT:,}",
            f"- **价格保护带**: 买入价不超过昨收 +{_h().PRICE_PROTECTION_PCT:.0%}",
            f"- **单日熔断**: 亏损 >{_h().DAILY_LOSS_STOP_PCT:.0%} 停止建仓",
            f"- **组合熔断**: 回撤 >{_h().PORTFOLIO_DRAWDOWN_STOP_PCT:.0%} 停止建仓",
            "",
            "*由 daily_trade_executor.py 自动生成*",
        ]
    )

    return "\n".join(lines)


def generate_next_trading_day_plan(today_str: str) -> dict:
    """收盘后自动生成下一个交易日的执行计划

    参数:
        today_str: 今日日期字符串 (YYYY-MM-DD)

    返回:
        下一个交易日的交易计划结果
    """
    from datetime import timedelta

    # 计算下一个交易日
    today = datetime.strptime(today_str, "%Y-%m-%d").date()
    next_day = today + timedelta(days=1)

    # 跳过周末和节假日
    max_attempts = 10
    attempts = 0
    while not _h().is_trading_day(next_day) and attempts < max_attempts:
        next_day += timedelta(days=1)
        attempts += 1

    if attempts >= max_attempts:
        return {
            "status": "error",
            "reason": f"无法在{today_str}后的10天内找到下一个交易日",
        }

    next_day_str = next_day.isoformat()
    logger.info(f"[INFO] 今日: {today_str}, 下一交易日: {next_day_str}")

    # 生成下一个交易日的计划
    result = _h().generate_instructions(next_day_str)

    # 添加元信息
    if isinstance(result, dict):
        meta = result.get("meta")
        if meta is None:
            meta = {}
            result["meta"] = meta
        meta["generated_after"] = today_str
        meta["auto_generated"] = True
        meta["next_trading_day"] = next_day_str

    return result


def run_premarket_mode(args: Any, target_date_str: str) -> dict:
    """``--mode pre-market`` 的完整编排 (2026-09-11 自宿主 ``main()`` 迁入)。

    迁移原因有二:
      1. 宿主 ``daily_trade_executor.py`` 有 **1500 行结构护栏**
         (``tests/unit/test_daily_executor_premarket_split_20260910.py``), 盘前职责
         的新增代码必须落在本模块, 否则护栏必红 (宿主当时已顶到 1500 行);
      2. SC-1「显式错误态必须以非零退出码暴露给计划任务」本身就是盘前语义。

    与宿主原内联实现**逐句等价**: 生成 → (可选)自动确认 → 打印结果 →
    错误态 ``SystemExit(1)`` 收场 (禁止 rc=0 的「假完成」)。

    注: ``generate_instructions`` 属受 monkeypatch 的名字, 必须经 ``_h()`` 取;
    写成裸名字或 ``from 宿主 import`` 会让
    ``monkeypatch.setattr(daily_trade_executor, "generate_instructions", ...)``
    静默失效 (见本模块顶部说明与上述护栏的结构层用例)。
    """
    result = _h().generate_instructions(target_date_str)

    # 自动确认所有指令
    if getattr(args, "auto_confirm", False) and result.get("status") == "generated":
        # 经 _h() 取: 宿主重导出的名字一律属性式读取, 保住 monkeypatch 语义
        confirm_count = _h().confirm_all_instructions(target_date_str)
        result["auto_confirmed"] = True
        result["auto_confirm_count"] = confirm_count
        logger.info(f"[INFO] Auto-confirmed {confirm_count} instructions")

    logger.info(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    # SC-1 修复 (2026-09-11): 显式错误态必须以非零退出码暴露给计划任务,
    # 避免「假完成」以 rc=0 静默通过 (原 status=completed 空计划即此路径)。
    if result.get("status") == "error":
        logger.error("[SC-1] 盘前指令生成失败, 以 rc=1 退出: %s", result.get("reason"))
        raise SystemExit(1)
    return result
