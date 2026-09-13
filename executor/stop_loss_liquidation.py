"""止损自动平仓执行器 (S-1 三个授权口径 · Issue #13, 2026-09-12)

背景
----
S-1 修复后止损链路为「阻断性告警 + 每日重复告警 + 待人工确认」, 不自动平仓 ——
因为自动平仓涉及**自动下单权限**, 需与灰度阶段 (auto_10) 的风险预算一起定。
本模块把「授权口径」落成可执行代码, 使启用只需翻 ``stop_loss.auto_liquidate.enabled``。

三个授权口径 (唯一事实源: ``config/risk_thresholds.yaml`` ``stop_loss.auto_liquidate``)
--------------------------------------------------------------------------------------
口径 1 · 授权范围 (scope)
    - 仅 ``stop_loss`` (止盈属收益实现, 不是风险处置, 不自动平仓);
    - 仅已持仓标的 (``positions`` 中真实存在且数量 > 0);
    - 仅减仓不反手 (``reduce_only``: 只允许 SELL, 且数量 <= 持仓数量);
    - 单标的单日最多 ``max_liquidations_per_symbol_per_day`` 次。

口径 2 · 与 auto_10 风险预算的关系 (budget)
    自动平仓属**降风险**动作, 默认豁免日度下单额度与单笔上限。
    ⚠ 若不豁免: 极端行情下可能因额度耗尽而平不掉仓。
    豁免必须可审计 —— 每次豁免写入平仓记录的 ``exempted_from`` 字段。

口径 3 · 失败处置 (failure)
    平仓单被拒/部分成交 → 按 ``max_exec_retries`` (总尝试次数上限) 重试; 达上限且
    ``escalate_on_failure`` → 升级人工介入**并保持阻断**, 绝不静默放弃。

安全不变量
----------
1. **默认关闭**: ``enabled=False`` 时本模块**不产生任何指令** (纯函数式判定);
2. **fail-closed**: 授权配置非法 (如 scope 漂移) → 拒绝生成平仓单 + 显式告警;
3. **可审计**: 每笔平仓单携带 ``authorization`` 块 (口径快照 + 豁免项 + 触发价);
4. **不静默部分成交**: 数量取整后为 0 (如持仓不足 100 股) → 记 ``skipped`` 并告警,
   不生成 0 股指令。
"""

from __future__ import annotations

import logging
from typing import Any

from utils.datetime_utils import now_bj

logger = logging.getLogger(__name__)

# 口径 1 唯一合法 scope 值 —— 防止配置漂移引入"止盈也自动平仓"等未授权行为
_ALLOWED_SCOPE = ("stop_loss_only",)
# A 股最小交易单位 (平仓数量向下取整到整手; 不足一手 → 记 skipped)
_LOT_SIZE = 100


def is_auto_liquidate_enabled(cfg: dict[str, Any] | None) -> bool:
    """是否已授权自动平仓 (默认未授权)。"""
    return bool((cfg or {}).get("enabled", False))


def validate_authorization(cfg: dict[str, Any]) -> tuple[bool, str]:
    """校验授权口径合法性 (fail-closed)。

    Returns:
        ``(ok, reason)`` — ``ok=False`` 时调用方必须**拒绝**生成平仓单。
    """
    scope = str(cfg.get("scope", ""))
    if scope not in _ALLOWED_SCOPE:
        return False, (
            f"授权范围 scope={scope!r} 非法 (仅允许 {list(_ALLOWED_SCOPE)}); "
            f"fail-closed 拒绝自动平仓"
        )
    max_per_day = cfg.get("max_liquidations_per_symbol_per_day", 1)
    try:
        if int(max_per_day) < 1:
            return False, f"max_liquidations_per_symbol_per_day={max_per_day!r} 必须 >=1"
    except (TypeError, ValueError):
        return False, f"max_liquidations_per_symbol_per_day={max_per_day!r} 非整数"
    try:
        if int(cfg.get("max_exec_retries", 0)) < 0:
            return False, f"max_exec_retries={cfg.get('max_exec_retries')!r} 不得为负"
    except (TypeError, ValueError):
        return False, f"max_exec_retries={cfg.get('max_exec_retries')!r} 非整数"
    if not bool(cfg.get("reduce_only", True)):
        return False, "reduce_only=false 会使平仓单可能反手建仓, 属未授权行为; fail-closed"
    return True, ""


def _holding_qty(item: dict[str, Any]) -> int:
    """取持仓数量 (字段口径与 ``_run_stop_loss_check`` 一致)。"""
    qty = item.get("phase1_shares") or item.get("total_shares") or item.get("shares", 0)
    try:
        return int(abs(float(qty)))
    except (TypeError, ValueError):
        return 0


def _scope_rejection(action: str, cfg: dict[str, Any]) -> str | None:
    """口径 1-a: 授权范围外的 action 返回拒绝原因 (None 表示在范围内)。"""
    if action == "stop_loss":
        return None
    if action == "take_profit" and bool(cfg.get("allow_take_profit", False)):
        return None
    return f"授权范围外 (action={action})"


def _build_one_instruction(
    item: dict[str, Any],
    positions: dict[str, Any],
    cfg: dict[str, Any],
    prior_counts: dict[str, int],
    daily_limit: int,
    *,
    date_str: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """按三个口径判定单个触发项 → ``(指令 or None, 跳过原因 or None)``。"""
    code = str(item.get("code", ""))
    pure_code = code.split(".")[0]

    reason = _scope_rejection(str(item.get("action", "")), cfg)
    if reason:
        return None, reason

    pos = positions.get(code) or positions.get(pure_code)
    if bool(cfg.get("held_positions_only", True)) and not isinstance(pos, dict):
        return None, "无持仓记录 (held_positions_only)"

    qty = _holding_qty(pos or {})
    if qty <= 0:
        return None, "持仓数量为 0, 无可平仓位"

    if prior_counts.get(code, 0) >= daily_limit:
        return None, f"单标的单日平仓次数已达上限 {daily_limit}"

    # 口径 1-c: 仅减仓不反手 —— 数量取持仓数量 (永不超出持仓), 整手向下取整
    # P0-H1 (2026-09-13): T+1 — 当日买入部分不可卖, 平仓数量截断到可卖范围
    from utils.execution.t1_constraint import clamp_sell_quantity

    sell_qty, available, frozen = clamp_sell_quantity(
        code, qty, qty, date=date_str, lot_aligned=False
    )
    lots = sell_qty // _LOT_SIZE
    if lots < 1:
        if frozen > 0:
            return None, (
                f"当日买入冻结 {frozen} 股 (T+1), 可卖 {available} 股不足一手 {_LOT_SIZE}"
            )
        return None, f"持仓 {qty} 股不足一手 ({_LOT_SIZE}), 无法平仓"
    liquidate_qty = lots * _LOT_SIZE

    price = float(item.get("current_price") or 0.0)
    if price <= 0:
        return None, "触发价不可用 (<=0), 拒绝生成平仓单"

    exempted = [
        name
        for flag, name in (
            ("exempt_from_daily_quota", "daily_quota"),
            ("exempt_from_single_trade_limit", "single_trade_limit"),
        )
        if cfg.get(flag)
    ]

    return (
        {
            "full_code": code,
            # 宿主 _execute_single_instruction 直接取 inst["code"]/inst["name"]
            # (WT 拆分路径与 SKIPPED 分支), 缺失会 KeyError — 平仓指令必须带全。
            "code": code,
            "name": str(item.get("name", "") or code),
            "action": "SELL",
            "qty": liquidate_qty,
            "ref_price": price,
            "estimated_amount": round(liquidate_qty * price, 2),
            "confirm": True,
            "reason": "stop_loss_auto_liquidate (S-1 口径 1~3)",
            # 审计块: 授权口径快照 + 豁免项 + 触发上下文, 不静默豁免
            "authorization": {
                "scope": cfg.get("scope"),
                "reduce_only": bool(cfg.get("reduce_only", True)),
                "trigger_action": item.get("action"),
                "trigger_price": price,
                "triggered_at": now_bj().isoformat(),
                "exempted_from": exempted,
                "per_symbol_daily_limit": daily_limit,
                "max_exec_retries": int(cfg.get("max_exec_retries", 0)),
                "escalate_on_failure": bool(cfg.get("escalate_on_failure", True)),
                "authorized_by": "config/risk_thresholds.yaml stop_loss.auto_liquidate",
            },
        },
        None,
    )


def build_liquidation_instructions(
    triggered: list[dict[str, Any]],
    positions: dict[str, Any],
    cfg: dict[str, Any],
    *,
    date_str: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按三个授权口径, 把止损触发项转为**减仓平仓指令**。

    Args:
        triggered: ``_run_stop_loss_check`` 的返回项 (含 code/action/current_price)
        positions: 当前持仓 dict
        cfg: ``get_stop_loss_auto_liquidate_config()`` 结果
        date_str: 当日日期 (单标的单日次数统计口径), 缺省取北京时间今天

    Returns:
        ``(instructions, skipped)``:
          - ``instructions`` 可直接并入指令文件 (带 ``authorization`` 审计块)
          - ``skipped`` 含 ``reason``, 供日志/报告显式呈现"为何没平"
    """
    date_str = date_str or now_bj().strftime("%Y-%m-%d")  # noqa: ARG001 — 口径留痕
    instructions: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    ok, reason = validate_authorization(cfg)
    if not ok:
        logger.error("[AutoLiq] %s", reason)
        return [], [{"code": t.get("code"), "reason": reason} for t in triggered]

    if not is_auto_liquidate_enabled(cfg):
        # 未授权: 纯直通, 不产生任何指令 (默认路径零行为变化)
        return [], [
            {"code": t.get("code"), "reason": "auto_liquidate 未授权 (enabled=false)"}
            for t in triggered
        ]

    # 单标的单日次数: 由调用方经 liquidations_today 传入历史次数 (幂等/多轮场景)
    prior_counts: dict[str, int] = {}
    for t in triggered:
        counts = t.get("liquidations_today")
        if isinstance(counts, dict):
            prior_counts.update({str(k): int(v) for k, v in counts.items()})

    daily_limit = int(cfg.get("max_liquidations_per_symbol_per_day", 1))
    for item in triggered:
        instruction, skip_reason = _build_one_instruction(
            item, positions, cfg, prior_counts, daily_limit, date_str=date_str
        )
        if instruction is not None:
            instructions.append(instruction)
        else:
            skipped.append({"code": item.get("code"), "reason": skip_reason})

    if instructions:
        logger.warning(
            "[AutoLiq] 授权自动平仓 %d 笔 (scope=%s, 豁免=%s)",
            len(instructions),
            cfg.get("scope"),
            "daily_quota+single_trade_limit" if cfg.get("exempt_from_daily_quota") else "无",
        )
    for s in skipped:
        logger.warning("[AutoLiq] 跳过平仓 %s: %s", s.get("code"), s.get("reason"))
    return instructions, skipped


def record_exec_attempt(
    instruction: dict[str, Any],
    result: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    """记录一次平仓执行尝试, 按口径 3 决定「重试 / 升级人工」。

    Args:
        instruction: 平仓指令 (含 authorization 块)
        result: 单条指令执行结果 (``_execute_single_instruction`` 产物; 含 status/qty,
            兼容读取历史调用方/测试注入的 fill_qty)
        state: 可变的执行状态 (跨日/跨轮次累计); 预期键 ``exec_retries: {code: int}``

    Returns:
        ``{"code", "attempt", "retries_left", "escalated", "reason"}``
    """
    code = str(instruction.get("full_code", ""))
    auth = instruction.get("authorization") or {}
    max_retries = int(auth.get("max_exec_retries", 0))
    escalate = bool(auth.get("escalate_on_failure", True))

    # 口径 3 语义: max_exec_retries = **总尝试次数上限** (首次尝试计入)。
    # 例: max_exec_retries=2 → 允许 2 次尝试 (1 次首发 + 1 次重试), 第 2 次失败即升级。
    retries = state.setdefault("exec_retries", {})
    attempt = int(retries.get(code, 0)) + 1
    retries[code] = attempt
    total_allowed = max(1, max_retries)

    status = str((result or {}).get("status", "")).upper()
    # 2026-09-12 字段口径修复: _execute_single_instruction 的成交数量字段是 "qty",
    # 原代码只读 "fill_qty" → 恒为 0, 成功永远被判失败, 触发无限重试/误升级。
    filled = int((result or {}).get("qty", (result or {}).get("fill_qty", 0)) or 0)
    target = int(instruction.get("qty", 0) or 0)

    # 成功判据: 状态非失败 且 有成交; 部分成交视作未完成 (需人工确认剩余)
    success = status not in ("FAILED", "REJECTED", "ERROR") and filled >= target > 0
    if success:
        logger.info("[AutoLiq] %s 平仓成功 (%d 股)", code, filled)
        return {
            "code": code,
            "attempt": attempt,
            "retries_left": max(0, total_allowed - attempt),
            "escalated": False,
            "reason": "ok",
        }

    partial = 0 < filled < target
    retries_left = max(0, total_allowed - attempt)
    escalated = escalate and retries_left == 0
    reason = "部分成交, 剩余需人工确认" if partial else "平仓单未成交"
    if escalated:
        logger.error(
            "[AutoLiq] %s %s, 已达尝试上限 %d 次 → 升级人工介入 (保持阻断)",
            code,
            reason,
            total_allowed,
        )
    else:
        logger.warning(
            "[AutoLiq] %s %s (第 %d 次尝试, 剩余重试 %d)",
            code,
            reason,
            attempt,
            retries_left,
        )
    return {
        "code": code,
        "attempt": attempt,
        "retries_left": retries_left,
        "escalated": escalated,
        "reason": reason,
    }


# ============================================================
# 主链挂载点 (S-1 阻断性告警 + 授权自动平仓)
# ============================================================


def execute_liquidation_instructions(
    instructions: list[dict[str, Any]],
    *,
    executor_fn: Any,
    executed_keys: set[str],
    sl_manager: Any = None,
    exec_state: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """执行授权自动平仓单 (S-1 口径 2/3 · 2026-09-12 链路闭环)。

    原缺陷: ``handle_stop_loss_events`` 授权后返回的平仓指令
    (status=auto_liquidate) 既不并入宿主 confirmed 也未单独执行 —— 静默消失,
    止损保护在授权模式下失效。本函数由宿主在幂等键初始化后调用, 语义:

      - 复用宿主 ``_execute_single_instruction`` (经 ``executor_fn`` 注入,
        与 :func:`handle_stop_loss_events` 的注入契约一致);
      - C1 幂等键: 仅 FILLED 指令落键 (SKIPPED/ERROR 不落键, 允许下轮重试);
      - ``record_exec_attempt`` 按口径 3 记录重试/升级;
      - 成功后 ``acknowledge_stop_loss`` 将状态机转终态 (不再重复告警)。

    Args:
        instructions: 平仓指令列表 (``build_liquidation_instructions`` 产物)
        executor_fn: 单指令执行函数 ``fn(inst) -> result`` (结果含 status/qty)
        executed_keys: 已执行指令幂等键集合 (原地新增; 键格式与宿主 C1 一致)
        sl_manager: ``StopLossManager`` (平仓成功后调 ``acknowledge_stop_loss``)
        exec_state: 可选的跨轮次执行状态 (缺省内部新建)

    Returns:
        ``(attempts, exec_results)`` — 口径 3 处置记录与执行结果
        (exec_results 剔除 SKIPPED, 与宿主常规执行报告口径一致)。
    """
    if not instructions:
        return [], []
    state = exec_state if exec_state is not None else {"exec_retries": {}}
    attempts: list[dict[str, Any]] = []
    exec_results: list[dict[str, Any]] = []
    for inst in instructions:
        ide_key = (
            f"{inst['full_code']}:{inst.get('action', 'SELL')}:{inst.get('qty', 0)}:{inst.get('ref_price', 0)}"
        )
        if ide_key in executed_keys:
            logger.warning("[AutoLiq] 平仓指令 %s 已执行过, 跳过 (C1 幂等)", ide_key)
            continue
        try:
            exec_result = executor_fn(inst)
        except Exception as exc:  # noqa: BLE001 — 执行异常计入口径 3 失败处置, 不上抛
            logger.error("[AutoLiq] %s 平仓执行异常: %s", inst.get("full_code"), exc)
            exec_result = {"status": "ERROR", "qty": 0, "fill_amount": 0.0}
        if str(exec_result.get("status", "")).upper() == "FILLED":
            executed_keys.add(ide_key)
        else:
            logger.warning(
                "[AutoLiq] 平仓指令 %s 未成交 (status=%s), 不落幂等键以便下轮重试",
                ide_key,
                exec_result.get("status"),
            )
        if str(exec_result.get("status", "")).upper() != "SKIPPED":
            exec_results.append(exec_result)
        record = record_exec_attempt(inst, exec_result, state)
        attempts.append(record)
        if record["reason"] == "ok" and sl_manager is not None:
            # 平仓成功 → 状态机转终态, 不再重复告警
            try:
                sl_manager.acknowledge_stop_loss(
                    str(inst["full_code"]).split(".")[0],
                    note="auto_liquidate 成功",
                )
            except Exception as exc:  # noqa: BLE001 — 状态机异常不吞执行结果
                logger.warning("[AutoLiq] acknowledge_stop_loss 失败: %s", exc)
    return attempts, exec_results


def run_authorized_liquidation(
    sl_event: dict[str, Any] | None,
    *,
    executor_fn: Any,
    executed_keys: set[str],
    sl_manager: Any = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    """授权路径总编排 (宿主单点调用): 执行平仓单 + 口径 3 收口。

    非 auto_liquidate 事件 (None/blocked/auto_liquidate_failed) 直接透传空结果,
    宿主无需再判事件状态 → 降低宿主 ``execute_instructions`` 圈复杂度 (C901)。

    Returns:
        ``(attempts, exec_results, blocked)`` — blocked 非 None 表示口径 3 未完成,
        宿主必须先落盘 progress/positions 再返回该阻断结果。
    """
    if (sl_event or {}).get("status") != "auto_liquidate":
        return [], [], None
    attempts, exec_results = execute_liquidation_instructions(
        sl_event.get("instructions") or [],
        executor_fn=executor_fn,
        executed_keys=executed_keys,
        sl_manager=sl_manager,
    )
    blocked = build_unresolved_block_result(attempts)
    return attempts, exec_results, blocked


def build_unresolved_block_result(
    attempts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """口径 3 收口: 平仓未完成 → 构造阻断结果 (全部成功返回 None)。

    宿主职责: 收到非 None 后**先落盘 progress/positions 再返回** (已成交部分
    已计入内存态, 不落盘会丢成交), 并不再执行常规 confirmed 指令。
    """
    unresolved = [a for a in attempts if a.get("reason") != "ok"]
    if not unresolved:
        return None
    escalated = [a["code"] for a in unresolved if a.get("escalated")]
    logger.error(
        "[AutoLiq] 口径 3: %d 笔平仓未完成 → 阻断执行; 升级人工=%s",
        len(unresolved),
        escalated,
    )
    return {
        "status": "blocked",
        "blocked_reason": "auto_liquidate_failed (S-1 口径 3)",
        "reason": (
            f"{len(unresolved)} 笔平仓未完成"
            + f" ({', '.join(str(a['code']) for a in unresolved)})"
            + (f", 其中 {len(escalated)} 笔已升级人工" if escalated else "")
            + ", 需人工确认剩余仓位"
        ),
        "auto_liquidate_attempts": attempts,
    }


def handle_stop_loss_events(
    triggered: list[dict[str, Any]],
    positions: dict[str, Any],
    cfg: dict[str, Any],
    *,
    block_on_trigger: bool = True,
    sl_manager: Any = None,
    executor_fn: Any = None,
    date_str: str | None = None,
) -> dict[str, Any] | None:
    """止损事件的统一处置入口 (原 ``daily_trade_executor`` 内联块迁出)。

    行为分三档 (逐步升级, 任一档都不静默):

    1. **未授权自动平仓** (``enabled=false``, 默认):
       维持 S-1 语义 —— 触发即**阻断执行**, 待人工确认。返回值与迁出前逐字段一致
       (``status`` / ``reason`` / ``blocked_reason`` / ``stop_loss_alerts``)。
    2. **已授权且成功生成平仓单**:
       返回 ``{"auto_liquidate": ..., "instructions": [...]}`` 供调用方并入本次执行;
       **不阻断** (由调用方在平仓失败时按口径 3 升级)。
    3. **已授权但全部被跳过** (口径不满足 / 不足一手 / 已达单日限次):
       仍按档 1 **阻断** + 在 ``skipped`` 里给出每笔"为何没平"的显式原因。

    Args:
        triggered: ``_run_stop_loss_check`` 的返回项 (已剔除 ``__manager_unavailable``)
        positions: 当前持仓 (自动平仓数量与授权范围依据)
        cfg: ``get_stop_loss_auto_liquidate_config()`` 结果
        block_on_trigger: ``stop_loss.block_on_trigger`` (未授权时的阻断开关)
        sl_manager: ``StopLossManager`` (平仓成功后调 ``acknowledge_stop_loss``)
        executor_fn: 可选的单指令执行函数 (测试注入; 缺省不在此处执行)
        date_str: 单标的单日次数统计口径日

    Returns:
        None 表示无需处置; 否则返回处置结果 dict。
    """
    if not triggered:
        return None

    summary = ", ".join(
        f"{t.get('name', t.get('code'))}({t.get('action')}, P&L {t.get('pnl_pct', 0):+.1%})"
        for t in triggered
    )
    logger.warning(
        "[StopLoss] %d 个标的触发止损/止盈, 需人工确认平仓操作: %s",
        len(triggered),
        summary,
    )

    def _blocked(extra: dict[str, Any]) -> dict[str, Any]:
        """S-1 阻断语义 (字段与迁出前保持一致, 只新增 extra)。"""
        return {
            "status": "blocked",
            "reason": (
                f"{len(triggered)} 个标的触发止损/止盈, 阻断执行 "
                f"(S-1 阻断性告警, 需人工确认; 确认后调用 "
                f"StopLossManager.acknowledge_stop_loss 解除)"
            ),
            "blocked_reason": "stop_loss_triggered (S-1)",
            "stop_loss_alerts": triggered,
            **extra,
        }

    if not is_auto_liquidate_enabled(cfg):
        if not block_on_trigger:
            return None
        logger.error("[StopLoss] 阻断本次执行 (block_on_trigger=true): %s", summary)
        return _blocked({})

    instructions, skipped = build_liquidation_instructions(
        triggered, positions, cfg, date_str=date_str
    )

    if not instructions:
        # 全部被跳过: 不得静默变成"已处置" —— 仍然阻断, 并给出每笔原因
        logger.error(
            "[AutoLiq] 已授权但无可执行平仓单 (全部被口径跳过), 仍按 S-1 阻断: %s",
            [s.get("reason") for s in skipped],
        )
        if not block_on_trigger:
            return None
        return _blocked({"auto_liquidate_skipped": skipped})

    result: dict[str, Any] = {
        "status": "auto_liquidate",
        "reason": f"{len(instructions)} 个标的触发止损, 已按授权口径生成平仓单",
        "auto_liquidate": {
            "scope": cfg.get("scope"),
            "exempted_from": instructions[0]["authorization"]["exempted_from"],
            "max_exec_retries": instructions[0]["authorization"]["max_exec_retries"],
        },
        "instructions": instructions,
        "skipped": skipped,
    }

    if executor_fn is not None:
        exec_state: dict[str, Any] = {"exec_retries": {}}
        attempts = []
        for inst in instructions:
            try:
                exec_result = executor_fn(inst)
            except Exception as exc:  # noqa: BLE001 — 执行异常必须计入失败处置, 不上抛
                logger.error("[AutoLiq] %s 平仓执行异常: %s", inst.get("full_code"), exc)
                exec_result = {"status": "ERROR", "fill_qty": 0}
            record = record_exec_attempt(inst, exec_result, exec_state)
            attempts.append(record)
            if record["reason"] == "ok" and sl_manager is not None:
                # 平仓成功 → 状态机转终态, 不再重复告警
                try:
                    sl_manager.acknowledge_stop_loss(
                        str(inst["full_code"]).split(".")[0],
                        note="auto_liquidate 成功",
                    )
                except Exception as exc:  # noqa: BLE001 — 状态机异常不应吞掉执行结果
                    logger.warning("[AutoLiq] acknowledge_stop_loss 失败: %s", exc)
        result["attempts"] = attempts
        unresolved = [a for a in attempts if a["reason"] != "ok"]
        if unresolved:
            escalated = [a["code"] for a in unresolved if a["escalated"]]
            result["status"] = "blocked"
            result["blocked_reason"] = "auto_liquidate_failed (S-1 口径 3)"
            result["reason"] = (
                f"{len(unresolved)} 笔平仓未完成"
                + (f", 其中 {len(escalated)} 笔已升级人工" if escalated else "")
            )
            logger.error("[AutoLiq] 口径 3: 平仓未完成 → 阻断执行; 升级人工=%s", escalated)
    return result
