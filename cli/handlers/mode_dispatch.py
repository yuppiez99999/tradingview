"""模式分发 + 执行时长追踪 — 由 `量化策略系统_统一入口_v8.6.py` 的 main() 迁出（item 11 续做）。

迁出原则：字节级等价（仅去掉一级 4 空格缩进 + 补导入），三种退出码语义原样保留：
废弃 stub 假成功 → 1；用户中断 → 130；执行异常 → 1。
**新增一处 fail-closed**：`MODE_SPECS` 里的 dest 若在 `handlers` 中无绑定，直接报错并退出 1
（模式表与绑定表漂移必须显式失败，不得静默跳过、更不得假成功）。
"""
from __future__ import annotations

import argparse
import sys
import time

from cli.handlers.helpers import _log_execution_summary
from cli.handlers.support import logger


def dispatch(
    args: argparse.Namespace,
    mode_specs: list[tuple[str, str, str]],
    handlers: dict[str, object],
) -> None:
    # ── 数据驱动分发（v5.7 Phase 1 增强：统一执行时长追踪）──
    # P2-4: 已废弃模式不再记为假成功——stub 返回 {'deprecated': True} 时 success=False 且退出码非 0
    for _flag, dest, _help in mode_specs:
        if getattr(args, dest):
            start_time = time.time()
            try:
                handler = handlers.get(dest)
                if handler is None:
                    logger.error(
                        f"\n❌ {dest} 模式未绑定 handler (MODE_SPECS 与 MODE_HANDLERS 不一致)"
                    )
                    sys.exit(1)
                result = handler(args)
                duration = time.time() - start_time
                # P2-4: 识别 deprecated stub 的假成功, 改为失败并给出生产入口提示
                if isinstance(result, dict) and result.get("deprecated"):
                    logger.error(
                        f"\n❌ {dest} 模式已废弃, 未实际执行。生产入口: "
                        f"{result.get('alt_entry') or 'py -3.8 v8.3_institutional/daily_workflow.py --phase all'}"
                    )
                    _log_execution_summary(dest, duration, False, result)
                    sys.exit(1)  # 非 0 退出码, 避免自动化脚本误判成功
                _log_execution_summary(dest, duration, True, result)
            except KeyboardInterrupt:
                duration = time.time() - start_time
                logger.info(f"\n⏹️  用户中断 ({dest})")
                _log_execution_summary(dest, duration, False, {"interrupted": True})
                sys.exit(130)
            except SystemExit:
                raise
            except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
                duration = time.time() - start_time
                logger.error(f"\n❌ {dest} 执行异常: {e}")
                _log_execution_summary(dest, duration, False, {"error": str(e)})
                sys.exit(1)
            break
