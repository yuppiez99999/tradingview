# -*- coding: utf-8 -*-
"""
ai_decision.cli — 命令行入口
============================

用法:
  python -m ai_decision.cli --symbol 600519 --mode shadow
  python -m ai_decision.cli --batch symbols.txt --mode paper
  python -m ai_decision.cli --symbol 000001 --mock-force   # 强制 Mock 验证

说明:
  - 默认 shadow 模式 (仅记录, 不执行)
  - 无 API Key 时自动走 MockProvider, 全链路可跑
  - --mock-force: 清空所有 API Key 环境变量, 强制纯 Mock 验证全链路
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import List

from ai_decision.decision_gate import RiskContext
from ai_decision.execution_bridge import execute_decision
from ai_decision.orchestrator import run_batch, run_decision


def _force_mock() -> None:
    for k in ("DEEPSEEK_API_KEY", "GLM_API_KEY", "MOONSHOT_API_KEY",
              "CLAUDE_API_KEY", "OPENAI_API_KEY"):
        os.environ.pop(k, None)


def _build_risk_context(args) -> RiskContext:
    return RiskContext(
        symbol="",
        portfolio_value=float(args.portfolio_value),
        proposed_notional=float(args.proposed_notional),
        daily_used_pct=float(args.daily_used_pct),
        is_limit_up=args.limit_up,
        is_limit_down=args.limit_down,
        blacklist=tuple(args.blacklist or ()),
    )


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ai_decision", description="多 AI 辩论共识自动交易决策系统")
    parser.add_argument("--symbol", help="单标的代码, 如 600519")
    parser.add_argument("--batch", help="批量标的文件 (每行一个代码)")
    parser.add_argument("--mode", default=None,
                        choices=["shadow", "paper", "auto"],
                        help="运行模式 (缺省读配置, 默认 shadow)")
    parser.add_argument("--mock-force", action="store_true",
                        help="强制清空 API Key, 纯 Mock 验证全链路")
    parser.add_argument("--portfolio-value", type=float, default=1_000_000.0)
    parser.add_argument("--proposed-notional", type=float, default=0.0)
    parser.add_argument("--daily-used-pct", type=float, default=0.0)
    parser.add_argument("--limit-up", action="store_true")
    parser.add_argument("--limit-down", action="store_true")
    parser.add_argument("--blacklist", nargs="*", default=[])
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--execute", action="store_true",
                        help="启用执行桥接: 决策生成后自动调用 execute_bridge 进行灰度执行")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.mock_force:
        _force_mock()

    if not args.symbol and not args.batch:
        parser.error("必须指定 --symbol 或 --batch")

    rc_template = _build_risk_context(args)

    if args.symbol:
        rc = RiskContext(**{k: v for k, v in rc_template.__dict__.items()})
        rc.symbol = args.symbol
        dec = run_decision(args.symbol, mode=args.mode, risk_context=rc)

        # --- 执行桥接 (如果启用 --execute) ---
        # P0 修复: 仅 buy/sell 决策才执行, 排除 hold/veto/review
        # 原代码 dec.action != "hold" 会让 veto/review 也进入执行路径, 触发真实下单
        if args.execute and dec.action in ("buy", "sell"):
            # 仅当决策有明确方向 (buy/sell) 时才触发执行桥接
            # 传入 risk_context 让 L2 复用 L1 run_hard_risk (黑名单/涨跌停/日内累计)
            execution_result = execute_decision(
                dec,
                portfolio_value=args.portfolio_value,
                force_mode=args.mode,  # CLI 模式优先
                risk_context=rc,
            )
            dec.execution_result = execution_result
            # 步骤 1: 字段同步 (修复 veto 覆盖 bug + 新增 escalation 同步)
            # 设计原则: 用 dec.* 作默认值保留 L1 apply_mode() 已设的字段, 避免被空值覆盖
            # - dec.veto 之前用 False 作默认值会覆盖 L1 已设的 veto=True (已修复)
            # - dec.veto_reason / dec.escalation_reason 用 `or` 兜底, 避免空字符串覆盖
            dec.executed = execution_result.get("executed", dec.executed)
            dec.veto = execution_result.get("veto", dec.veto)
            dec.veto_reason = execution_result.get("veto_reason") or dec.veto_reason
            dec.escalation = execution_result.get("escalation", dec.escalation)
            dec.escalation_reason = (
                execution_result.get("escalation_reason") or dec.escalation_reason
            )

        print(json.dumps(dec.to_dict(), ensure_ascii=False, indent=2)
              if args.json else _fmt(dec))
        return 0

    # batch
    symbols: List[str] = []
    with open(args.batch, "r", encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if s and not s.startswith("#"):
                symbols.append(s)
    decisions = run_batch(symbols, mode=args.mode)
    for dec in decisions:
        print(_fmt(dec))
    return 0


def _fmt(dec) -> str:
    flag = "EXECUTED" if dec.executed else ("VETO" if dec.veto else "recorded")
    esc = f" [ESC:{dec.escalation_reason}]" if dec.escalation else ""
    exec_info = ""
    if getattr(dec, "execution_result", None):
        er = dec.execution_result
        mode_display = er.get("mode", dec.mode)
        plan = er.get("plan", {})
        exec_info = f" | mode={mode_display} qty={plan.get('qty', '?')} notional={plan.get('notional', 'N/A')}"
    return (f"[{dec.mode.upper()}|{flag}] {dec.symbol}: {dec.action} "
            f"strength={dec.strength:.3f} conf={dec.confidence:.3f} "
            f"verdict={dec.verdict_type}{esc}{exec_info}")


if __name__ == "__main__":
    sys.exit(main())
