"""
CLI 命令调度器 — v5.10 P0-9 重构第二步
================================================
职责:
  - 管理 CLI 模式注册表
  - 统一分发入口
  - 统一执行时长追踪和错误处理
  - 与 core.context 解耦，不直接导入具体 handler

使用方式:
    from cli.dispatcher import Dispatcher
    dispatcher = Dispatcher()
    dispatcher.register('--daily', run_daily_workflow, '三阶段交易工作流')
    dispatcher.run()
"""

import time
from collections.abc import Callable
from typing import Any, Optional


class Dispatcher:
    """CLI 命令调度器"""

    def __init__(self, name: str = "量化策略系统"):
        self.name = name
        self._modes: list[tuple[str, str, str, Callable]] = []
        self._registry: dict[str, Callable] = {}

    def register(self, flag: str, dest: str, help_text: str, handler: Callable) -> None:
        """注册一个 CLI 模式"""
        self._modes.append((flag, dest, help_text, handler))
        self._registry[dest] = handler

    def get_modes(self) -> list[tuple[str, str, str, Callable]]:
        """获取所有注册的模式"""
        return list(self._modes)

    def get_handler(self, dest: str) -> Optional[Callable]:
        """根据 dest 获取 handler"""
        return self._registry.get(dest)

    def build_epilog(self) -> str:
        """生成 epilog 中的运行模式清单"""
        lines = '\n'.join(
            f"  {flag:<20s} {help_text}" for flag, _, help_text, _ in self._modes
        )
        return f"运行模式:\n{lines}"

    def run(self, args) -> Any:
        """根据解析后的 args 分发到对应 handler

        Args:
            args: argparse 解析后的参数对象

        Returns:
            handler 的返回值
        """
        for _flag, dest, _, handler in self._modes:
            if getattr(args, dest, False):
                start_time = time.time()
                try:
                    result = handler(args)
                    duration = time.time() - start_time
                    self._log_execution_summary(dest, duration, True, result)
                    return result
                except KeyboardInterrupt:
                    duration = time.time() - start_time
                    print(f"\n⏹️  用户中断 ({dest})")
                    self._log_execution_summary(dest, duration, False, {'interrupted': True})
                    return None
                except Exception as e:
                    duration = time.time() - start_time
                    print(f"\n❌ {dest} 执行异常: {e}")
                    self._log_execution_summary(dest, duration, False, {'error': str(e)})
                    return None
        return None

    def _log_execution_summary(self, mode_name: str, duration_sec: float,
                               success: bool, result: Any = None) -> None:
        """记录执行摘要"""
        status = "✅ 成功" if success else "❌ 失败"
        print(f"\n{'='*60}")
        print(f"  模式: {mode_name}")
        print(f"  状态: {status}")
        print(f"  耗时: {duration_sec:.2f}秒")
        if result is not None:
            print(f"  结果: {result}")
        print(f"{'='*60}")
