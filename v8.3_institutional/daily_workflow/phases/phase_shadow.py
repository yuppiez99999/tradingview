#!/usr/bin/env python3
"""
Phase implementation: phase_shadow_monitor

Extracted from original DailyWorkflow class for modularization.
This module contains the standalone phase function implementing the phase_shadow_monitor phase.

The function receives a DailyWorkflow instance as its first parameter ("workflow").
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def phase_shadow_monitor(workflow) -> bool:
    """Phase 10: 影子账户每日净值记录 + fail-fast 检查

    职责:
    - 加载影子账户状态 (output/shadow_account/shadow_state.json)
    - 计算当日组合净值 (基于实际持仓 + 当日收盘价)
    - 记录每日净值到 daily_nav
    - 检查 fail-fast 触发条件 (单日>3%, 3日>5%)
    - 触发时终止影子账户并记录原因
    - 保存状态

    2026-07-25 顶级对冲基金审计 P0-11: 影子账户 fail-fast 集成
    """
    logger.info(f"Phase 10: 影子账户监控 @ {workflow.trade_date}")

    try:
        import json as _json

        # === 加载影子账户状态 ===
        # v8.6.11 FIX: 使用 BASE_DIR.parent 定位 (与 launch_shadow_account.py 一致)
        # 原始 bug: 相对路径 "output/shadow_account/" 基于 cwd 解析,
        #   EOD 任务 cwd=v8.3_institutional 时找不到 state_file (在项目根目录下)
        _project_root_for_state = BASE_DIR.parent  # e:\各种PY程序\28-终极量化交易系统8.4
        state_file = _project_root_for_state / "output" / "shadow_account" / "shadow_state.json"
        if not state_file.exists():
            logger.info("Phase 10 跳过: 影子账户未初始化 (运行 python launch_shadow_account.py 启动)")
            workflow.state["phases"]["shadow_monitor"] = {
                "status": "SKIP",
                "reason": "shadow_account_not_initialized",
            }
            return True

        with open(state_file, encoding="utf-8") as f:
            shadow_state = _json.load(f)

        # 已终止的影子账户仅记录状态, 不再更新
        if shadow_state.get("status") == "TERMINATED":
            logger.warning(
                "Phase 10: 影子账户已被 fail-fast 终止 (原因: %s), 跳过净值记录",
                shadow_state.get("fail_fast_log", [{}])[-1].get("reason", "unknown")
                if shadow_state.get("fail_fast_log") else "unknown",
            )
            workflow.state["phases"]["shadow_monitor"] = {
                "status": "TERMINATED",
                "reason": shadow_state.get("fail_fast_log", [{}])[-1].get("reason", "")
                if shadow_state.get("fail_fast_log") else "",
            }
            return True

        # === 计算当日组合净值 ===
        # 从 Phase 6 获取今日执行的订单 (影子账户跟踪生产信号)
        execute_phase = workflow.state.get("phases", {}).get("execute", {})
        execute_phase.get("orders", []) if isinstance(execute_phase, dict) else []

        # 计算当日组合收益 (简化: 使用 signal 阶段的目标权重 + 实际收益)
        signal_phase = workflow.state.get("phases", {}).get("signal", {})
        target_weights = signal_phase.get("target_weights", {}) if isinstance(signal_phase, dict) else {}

        # v8.6.11 FIX: EOD 任务只运行 --phase shadow_monitor, signal phase 未执行
        # 兜底从 trade_plan 文件读取 execution_plan 推导 target_weights
        if not target_weights:
            try:
                _trade_plan_path = BASE_DIR / "trade_plans" / f"trade_plan_{workflow.trade_date.replace('-', '')}.json"
                if _trade_plan_path.exists():
                    with open(_trade_plan_path, encoding="utf-8") as _tp_f:
                        _tp = _json.load(_tp_f)
                    _exec_plan = _tp.get("execution_plan", {})
                    _day_capital = float(_tp.get("execution_plan", {}).get("day_capital", 100000))
                    _all_orders = []
                    _all_orders.extend(_exec_plan.get("morning_orders", []))
                    _all_orders.extend(_exec_plan.get("afternoon_orders", []))
                    for _ord in _all_orders:
                        _sym = _ord.get("code", "")
                        _amt = float(_ord.get("est_amount", 0))
                        _side = _ord.get("side", "BUY").upper()
                        _sign = 1.0 if _side == "BUY" else -1.0
                        if _sym and _day_capital > 0:
                            target_weights[_sym] = target_weights.get(_sym, 0.0) + _sign * _amt / _day_capital
                    if target_weights:
                        logger.info(
                            "Phase 10: 从 trade_plan 兜底读取 target_weights "
                            "(symbols=%d, day_capital=%.0f)",
                            len(target_weights), _day_capital,
                        )
            except Exception as _e:
                logger.warning(f"Phase 10: 读取 trade_plan 兜底失败: {_e}")

        # 从 data_provider 获取当日收盘价, 计算实际收益
        daily_return = 0.0
        if target_weights:
            try:
                from utils.data_provider import MarketDataProvider
                provider = MarketDataProvider(backtest_mode=False)
                total_weight = sum(abs(w) for w in target_weights.values())
                if total_weight > 0:
                    for symbol, weight in target_weights.items():
                        if abs(weight) < 0.001:
                            continue
                        try:
                            df = provider.get_historical_data(symbol, period="5d")
                            if df is not None and not df.empty and len(df) >= 2:
                                ret = float(df["close"].iloc[-1] / df["close"].iloc[-2] - 1)
                                daily_return += weight * ret
                        except Exception:
                            pass
            except Exception as e:
                logger.warning(f"Phase 10: 获取行情数据失败, 使用 0 收益: {e}")
                daily_return = 0.0

        # === 更新净值 ===
        daily_nav_list = shadow_state.get("daily_nav", [])
        today_str = str(workflow.trade_date)

        # v8.6.11 FIX: 幂等性检查 — 同一 trade_date 不重复追加 daily_nav
        _existing_idx = None
        for _i, _entry in enumerate(daily_nav_list):
            if _entry.get("date") == today_str:
                _existing_idx = _i
                break

        # v8.6.11 FIX: 基准 nav 计算 — 用前一天的 nav, 避免重复运行时 nav 累积
        # 原始 bug: 多次运行 Phase 10 时, current_nav 已是当日 nav, 再乘 (1+daily_return) 会重复计算
        if _existing_idx is not None and _existing_idx > 0:
            # 当天已有记录 → 用前一天的 nav 作为基准
            _base_nav = float(daily_nav_list[_existing_idx - 1].get("nav", 1.0))
        elif _existing_idx == 0:
            # 当天是第一条记录 → 用初始 nav (1.0)
            _base_nav = 1.0
        else:
            # 当天无记录 → 用 current_nav (前一天的最终 nav)
            _base_nav = float(shadow_state.get("current_nav", 1.0))

        new_nav = _base_nav * (1 + daily_return)

        _new_entry = {
            "date": today_str,
            "nav": round(new_nav, 6),
            "daily_return": round(daily_return, 6),
            "capital": round(new_nav * float(shadow_state.get("initial_capital", 500_000)), 2),
            "recorded_at": datetime.now().isoformat(),
        }
        if _existing_idx is not None:
            # 替换已有记录 (更新为最新计算结果)
            daily_nav_list[_existing_idx] = _new_entry
            logger.info("Phase 10: 更新已有 daily_nav 记录 (idx=%d, base_nav=%.6f)", _existing_idx, _base_nav)
        else:
            daily_nav_list.append(_new_entry)

        # 保留最近 365 天净值 (避免状态文件膨胀)
        if len(daily_nav_list) > 365:
            daily_nav_list = daily_nav_list[-365:]

        shadow_state["daily_nav"] = daily_nav_list
        shadow_state["current_nav"] = round(new_nav, 6)
        shadow_state["current_capital"] = round(new_nav * float(shadow_state.get("initial_capital", 500_000)), 2)
        shadow_state["last_updated"] = datetime.now().isoformat()

        # === Fail-fast 检查 (单日>3%, 3日>5%) ===
        ff_config = shadow_state.get("fail_fast_config", {})
        daily_dd_threshold = float(ff_config.get("daily_drawdown_threshold", 0.03))
        cum_3d_threshold = float(ff_config.get("cumulative_3d_drawdown_threshold", 0.05))

        fail_fast_triggered = False
        fail_fast_reason = ""

        # 检查 1: 单日回撤 > 3%
        if len(daily_nav_list) >= 2:
            prev_nav = float(daily_nav_list[-2].get("nav", new_nav))
            if prev_nav > 0:
                daily_dd = (prev_nav - new_nav) / prev_nav
                if daily_dd > daily_dd_threshold:
                    fail_fast_triggered = True
                    fail_fast_reason = f"daily_drawdown_exceeded: {daily_dd:.4f} > {daily_dd_threshold}"

        # 检查 2: 3日累计回撤 > 5%
        if not fail_fast_triggered and len(daily_nav_list) >= 4:
            nav_3d_ago = float(daily_nav_list[-4].get("nav", new_nav))
            if nav_3d_ago > 0:
                cum_3d_dd = (nav_3d_ago - new_nav) / nav_3d_ago
                if cum_3d_dd > cum_3d_threshold:
                    fail_fast_triggered = True
                    fail_fast_reason = f"cumulative_3d_drawdown_exceeded: {cum_3d_dd:.4f} > {cum_3d_threshold}"

        if fail_fast_triggered:
            # === Fail-fast 触发: 终止影子账户 ===
            shadow_state["status"] = "TERMINATED"
            termination_record = {
                "terminated_at": datetime.now().isoformat(),
                "trigger_date": today_str,
                "reason": fail_fast_reason,
                "final_nav": round(new_nav, 6),
                "final_capital": shadow_state["current_capital"],
                "days_tracked": len(daily_nav_list),
                "total_trades": len(shadow_state.get("trade_log", [])),
            }
            shadow_state.setdefault("fail_fast_log", []).append(termination_record)

            logger.critical(
                "Phase 10: ⚠️ FAIL-FAST 触发! 影子账户已终止: %s, "
                "final_nav=%.4f, days_tracked=%d",
                fail_fast_reason, new_nav, len(daily_nav_list),
            )
            logger.critical(
                "Phase 10: 影子账户终止 → 策略不得推进到下一灰度阶段, 需回滚!"
            )

            workflow.state["phases"]["shadow_monitor"] = {
                "status": "FAIL_FAST_TERMINATED",
                "reason": fail_fast_reason,
                "final_nav": round(new_nav, 6),
                "days_tracked": len(daily_nav_list),
            }
        else:
            # 正常记录
            logger.info(
                "Phase 10 完成: 影子账户净值=%.4f, 日收益=%.4f%%, 运行天数=%d, fail-fast=未触发",
                new_nav, daily_return * 100, len(daily_nav_list),
            )
            workflow.state["phases"]["shadow_monitor"] = {
                "status": "OK",
                "current_nav": round(new_nav, 6),
                "daily_return": round(daily_return, 6),
                "days_tracked": len(daily_nav_list),
                "stage": shadow_state.get("stage_name", "stage_1"),
            }

        # === 保存状态 ===
        with open(state_file, "w", encoding="utf-8") as f:
            _json.dump(shadow_state, f, ensure_ascii=False, indent=2, default=str)

        # === 追加写入 reports/shadow/daily_returns.jsonl (T2.4 准入流程数据源) ===
        # 数据流: V9 Phase 10 计算的 daily_return → JSONL → shadow_admission_launcher.py 读取
        # 格式: {"date": "...", "daily_return": ..., "source": "v9_phase10_real_backtest"}
        # 幂等性: 同一 trade_date 更新已有行 (v8.6.11 FIX: 原仅检查末行, 现全量扫描替换)
        # 失败隔离: JSONL 写入失败不影响 Phase 10 主流程
        try:
            _project_root = BASE_DIR.parent  # e:\各种PY程序\28-终极量化交易系统8.4
            _shadow_dir = _project_root / "reports" / "shadow"
            _shadow_dir.mkdir(parents=True, exist_ok=True)
            _jsonl_path = _shadow_dir / "daily_returns.jsonl"

            _record = {
                "date": today_str,
                "daily_return": round(float(daily_return), 6),
                "source": "v9_phase10_real_backtest",
            }

            # 幂等性: 全量读取, 若已有当日记录则替换, 否则追加
            _existing_lines: list = []
            _replaced = False
            if _jsonl_path.exists():
                try:
                    with open(_jsonl_path, encoding="utf-8") as _f:
                        _existing_lines = _f.readlines()
                except (_json.JSONDecodeError, OSError):
                    _existing_lines = []
            for _i, _line in enumerate(_existing_lines):
                try:
                    _parsed = _json.loads(_line)
                    if _parsed.get("date") == today_str:
                        _existing_lines[_i] = _json.dumps(_record, ensure_ascii=False) + "\n"
                        _replaced = True
                        break
                except _json.JSONDecodeError:
                    continue

            if _replaced:
                with open(_jsonl_path, "w", encoding="utf-8") as _f:
                    _f.writelines(_existing_lines)
                logger.info(
                    "Phase 10: daily_returns.jsonl 更新已有记录 (date=%s, return=%.6f)",
                    today_str, daily_return,
                )
            else:
                with open(_jsonl_path, "a", encoding="utf-8") as _f:
                    _f.write(_json.dumps(_record, ensure_ascii=False) + "\n")
                logger.info(
                    "Phase 10: daily_returns.jsonl 追加成功 (date=%s, return=%.6f)",
                    today_str, daily_return,
                )
        except Exception as _jsonl_err:
            logger.warning(
                "Phase 10: daily_returns.jsonl 写入失败 (不影响主流程): %s",
                _jsonl_err,
            )

        return True

    except Exception as e:
        logger.error(f"Phase 10 异常: {e}", exc_info=True)
        workflow.state["phases"]["shadow_monitor"] = {
            "status": "FAIL",
            "error": str(e),
        }
        # 异常隔离: Phase 10 失败不影响已完成的 Phase 1-9
        return True

    # --------------------------------------------------------
    # 主流程
    # --------------------------------------------------------

