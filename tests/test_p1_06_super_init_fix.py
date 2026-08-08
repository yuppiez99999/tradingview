"""
P1-6 回归测试: IntegratedExecutionSystem 父类回退到 object 时 super().__init__ 必崩

验证场景:
    1. 当 AutomatedExecutionSystem 父类不可用 (回退到 object) 时,
       IntegratedExecutionSystem.__init__ 不再抛出 TypeError
    2. _execute_daily_trading 不再抛出 AttributeError

复现路径:
    - 第 188 行: AutomatedExecutionSystem = object (父类不可用时回退)
    - 第 320 行 (修复前): super().__init__(total_capital=total_capital)
        → object.__init__(total_capital=...) → TypeError
    - 第 626 行 (修复前): super()._execute_daily_trading(execution_name)
        → object 无此方法 → AttributeError

修复方案:
    - 用 _AUTO_SYSTEM_AVAILABLE 标志条件性调用 super().__init__()
    - 用 _AUTO_SYSTEM_AVAILABLE 标志条件性调用 super()._execute_daily_trading()
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class P1_06_SuperInitFixTest(unittest.TestCase):
    """验证 P1-6 修复: 父类回退到 object 时不崩溃"""

    def setUp(self):
        """每个测试前重置模块级变量"""
        # 保存原始模块状态
        self._orig_sys_modules = dict(sys.modules)

    def tearDown(self):
        """恢复模块状态"""
        # 移除测试中可能加载的模块
        for key in list(sys.modules.keys()):
            if key not in self._orig_sys_modules:
                sys.modules.pop(key, None)

    def test_init_when_parent_is_object(self):
        """测试 1: 父类为 object 时 __init__ 不抛 TypeError

        场景: AutomatedExecutionSystem 模块导入失败,回退到 object.
        修复前: super().__init__(total_capital=...) → TypeError
        修复后: 跳过 super().__init__(), 仅初始化子类属性
        """
        # 用 mock 替换 system_integration 模块中的关键依赖
        with patch.dict(sys.modules, {"system_integration": None}):
            # 强制重新导入 system_integration 模块
            if "system_integration" in sys.modules:
                del sys.modules["system_integration"]

            # 模拟 AutomatedExecutionSystem 不可用的场景:
            # 通过 mock patch 让 _AUTO_SYSTEM_AVAILABLE = False
            # 但由于 system_integration 模块在导入时已确定该值,
            # 我们直接 patch 模块属性来验证修复逻辑
            import system_integration as si

            # 保存原始值
            orig_flag = si._AUTO_SYSTEM_AVAILABLE
            orig_parent = si.AutomatedExecutionSystem

            try:
                # 模拟父类不可用场景
                si._AUTO_SYSTEM_AVAILABLE = False
                si.AutomatedExecutionSystem = object  # type: ignore[assignment]
                # 验证: __init__ 不应抛 TypeError
                # 注意: 我们只验证 __init__ 的 super() 调用部分,
                # 不验证整个初始化流程 (会涉及大量外部依赖)
                with patch.object(
                    si.IntegratedExecutionSystem,
                    "_normalize_local_positions_file",
                    return_value=None,
                ), patch.object(
                    si.IntegratedExecutionSystem,
                    "_init_signal_fusion",
                    return_value=None,
                ), patch.object(
                    si.IntegratedExecutionSystem,
                    "_init_drift_detector",
                    return_value=None,
                ), patch.object(
                    si.IntegratedExecutionSystem,
                    "_init_stop_loss_monitor",
                    return_value=None,
                ):
                    try:
                        system = si.IntegratedExecutionSystem(total_capital=5_000_000)
                        # 验证子类属性正常初始化
                        self.assertIsNotNone(system.report_dir)
                        self.assertIsNone(system.signal_fusion)
                        self.assertIsNone(system.drift_detector)
                        self.assertIsNone(system.stop_loss_monitor)
                    except TypeError as e:
                        if "object.__init__()" in str(e):
                            self.fail(
                                f"P1-6 修复未生效: __init__ 仍抛 TypeError: {e}\n"
                                f"修复方案: 用 _AUTO_SYSTEM_AVAILABLE 标志条件性调用 super().__init__()"
                            )
                        raise  # 重新抛出其他 TypeError
            finally:
                # 恢复原始值
                si._AUTO_SYSTEM_AVAILABLE = orig_flag
                si.AutomatedExecutionSystem = orig_parent

    def test_execute_daily_trading_when_parent_is_object(self):
        """测试 2: 父类为 object 时 _execute_daily_trading 不抛 AttributeError

        场景: AutomatedExecutionSystem 模块不可用,回退到 object.
        修复前: super()._execute_daily_trading(...) → AttributeError
        修复后: 跳过 super() 调用, 仅执行 P0 风控钩子
        """
        import system_integration as si

        orig_flag = si._AUTO_SYSTEM_AVAILABLE
        orig_parent = si.AutomatedExecutionSystem

        try:
            si._AUTO_SYSTEM_AVAILABLE = False
            si.AutomatedExecutionSystem = object  # type: ignore[assignment]
            # 创建 mock 实例,避免触发完整 __init__
            system = MagicMock(spec=si.IntegratedExecutionSystem)
            # 用真实方法替换 mock 的 _execute_daily_trading
            system._execute_daily_trading = si.IntegratedExecutionSystem._execute_daily_trading.__get__(system)
            # 设置 mock 属性
            system._hook_update_signal_fusion = MagicMock()
            system._hook_drift_and_retrain = MagicMock()
            system._hook_cost_aware_backtest = MagicMock()
            system._hook_stop_loss_review = MagicMock()

            # 验证: _execute_daily_trading 不应抛 AttributeError
            try:
                system._execute_daily_trading("daily_execution")
                # 验证 P0 钩子被调用 (即使父类方法不可用,风控钩子仍执行)
                system._hook_update_signal_fusion.assert_called_once()
                system._hook_drift_and_retrain.assert_called_once()
                system._hook_cost_aware_backtest.assert_called_once()
                system._hook_stop_loss_review.assert_called_once()
            except AttributeError as e:
                if "_execute_daily_trading" in str(e):
                    self.fail(
                        f"P1-6 修复未生效: _execute_daily_trading 仍抛 AttributeError: {e}\n"
                        f"修复方案: 用 _AUTO_SYSTEM_AVAILABLE 标志条件性调用 super()._execute_daily_trading()"
                    )
                raise
        finally:
            si._AUTO_SYSTEM_AVAILABLE = orig_flag
            si.AutomatedExecutionSystem = orig_parent

    def test_init_when_parent_available(self):
        """测试 3: 父类可用时正常调用 super().__init__() (回归测试)

        确保修复没有破坏正常路径: 父类可用时, super().__init__() 应该被调用.
        验证方式: 检查父类 (AutomatedExecutionSystem) 的属性是否被初始化.
        """
        import system_integration as si

        orig_flag = si._AUTO_SYSTEM_AVAILABLE

        try:
            # 父类可用场景
            si._AUTO_SYSTEM_AVAILABLE = True

            with patch.object(
                si.IntegratedExecutionSystem,
                "_normalize_local_positions_file",
                return_value=None,
            ), patch.object(
                si.IntegratedExecutionSystem,
                "_init_signal_fusion",
                return_value=None,
            ), patch.object(
                si.IntegratedExecutionSystem,
                "_init_drift_detector",
                return_value=None,
            ), patch.object(
                si.IntegratedExecutionSystem,
                "_init_stop_loss_monitor",
                return_value=None,
            ):
                system = si.IntegratedExecutionSystem(total_capital=5_000_000)
                # 验证父类 AutomatedExecutionSystem 被初始化:
                # 父类 __init__ 会设置 total_capital 属性
                # (日志中会输出 "自动化执行系统初始化完成，总资本: 5,000,000元")
                self.assertTrue(
                    hasattr(system, "total_capital") or hasattr(system, "_total_capital"),
                    "父类 AutomatedExecutionSystem 未被初始化 "
                    "(未找到 total_capital 属性, 说明 super().__init__() 未被调用)"
                )
        finally:
            si._AUTO_SYSTEM_AVAILABLE = orig_flag


if __name__ == "__main__":
    unittest.main(verbosity=2)
