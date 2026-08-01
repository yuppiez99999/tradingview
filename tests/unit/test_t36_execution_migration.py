"""T3.6 根目录执行模块迁移单元测试.

验证:
    1. utils/execution/ 下 3 个迁移文件可正常 import
    2. 根目录 re-export 兼容层可正常 import (HC-1 透传)
    3. 新旧路径导入的符号等价 (功能等价性)
    4. rebalance_execution_orders 路径修正 (硬编码 v7.1 → 动态解析)
    5. automated_execution_system 路径修正 (__file__ 回退两级)
    6. daily_build_and_hedge 路径修正 (BASE_DIR 回退两级)
    7. 关键接口 (类/函数/常量) 可访问且行为正确
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================
# 1. 新路径 utils/execution/ 导入测试
# ============================================================
class TestNewPathImport:
    """验证 utils/execution/ 下 3 个迁移文件可正常 import."""

    def test_import_rebalance_execution_orders_new_path(self):
        """从新路径导入 rebalance_execution_orders."""
        from utils.execution import rebalance_execution_orders as mod
        assert mod is not None
        assert hasattr(mod, "TARGET_ALLOCATION")
        assert hasattr(mod, "load_positions")
        assert hasattr(mod, "generate_rebalance_orders")

    def test_import_daily_build_and_hedge_new_path(self):
        """从新路径导入 daily_build_and_hedge."""
        from utils.execution import daily_build_and_hedge as mod
        assert mod is not None
        assert hasattr(mod, "DailyBuildHedgeSystem")
        assert hasattr(mod, "BASE_DIR")
        assert hasattr(mod, "logger")

    def test_import_automated_execution_system_new_path(self):
        """从新路径导入 automated_execution_system."""
        from utils.execution import automated_execution_system as mod
        assert mod is not None
        assert hasattr(mod, "AutomatedExecutionSystem")
        assert hasattr(mod, "TradingCalendar")
        assert hasattr(mod, "OrderRouter")

    def test_import_all_three_modules_together(self):
        """同时导入 3 个模块不冲突."""
        from utils.execution import (
            automated_execution_system,
            daily_build_and_hedge,
            rebalance_execution_orders,
        )
        assert automated_execution_system is not None
        assert daily_build_and_hedge is not None
        assert rebalance_execution_orders is not None


# ============================================================
# 2. 根目录 re-export 兼容层导入测试 (HC-1 透传)
# ============================================================
class TestReExportCompat:
    """验证根目录 re-export 兼容层可正常 import."""

    def test_import_rebalance_from_root(self):
        """根目录 import rebalance_execution_orders 仍可工作."""
        # 使用 importlib 避免缓存影响
        mod = importlib.import_module("rebalance_execution_orders")
        assert hasattr(mod, "TARGET_ALLOCATION")
        assert hasattr(mod, "load_positions")

    def test_import_daily_build_from_root(self):
        """根目录 import daily_build_and_hedge 仍可工作."""
        mod = importlib.import_module("daily_build_and_hedge")
        assert hasattr(mod, "DailyBuildHedgeSystem")

    def test_import_automated_execution_from_root(self):
        """根目录 import automated_execution_system 仍可工作."""
        mod = importlib.import_module("automated_execution_system")
        assert hasattr(mod, "AutomatedExecutionSystem")


# ============================================================
# 3. 新旧路径符号等价性测试
# ============================================================
class TestPathEquivalence:
    """验证新旧路径导入的符号等价."""

    def test_rebalance_target_allocation_equivalent(self):
        """新旧路径的 TARGET_ALLOCATION 等价."""
        import rebalance_execution_orders as old_mod
        from utils.execution.rebalance_execution_orders import TARGET_ALLOCATION as new_tgt
        assert new_tgt == old_mod.TARGET_ALLOCATION
        # 验证关键内容
        assert new_tgt["宽基"] == 0.25
        assert new_tgt["科技"] == 0.20

    def test_rebalance_constants_equivalent(self):
        """新旧路径的常量等价."""
        import rebalance_execution_orders as old_mod
        from utils.execution.rebalance_execution_orders import (
            MAX_SINGLE_ORDER_AMOUNT,
            MIN_LOT_SIZE,
            MIN_TRADE_AMOUNT,
            TARGET_TOTAL,
        )
        assert MIN_TRADE_AMOUNT == old_mod.MIN_TRADE_AMOUNT == 10000
        assert MAX_SINGLE_ORDER_AMOUNT == old_mod.MAX_SINGLE_ORDER_AMOUNT == 200000
        assert MIN_LOT_SIZE == old_mod.MIN_LOT_SIZE == 100
        assert TARGET_TOTAL == old_mod.TARGET_TOTAL == 5_000_000.0

    def test_rebalance_functions_are_same_object(self):
        """新旧路径导入的函数是同一对象 (内存等价)."""
        import rebalance_execution_orders as old_mod
        from utils.execution.rebalance_execution_orders import (
            load_positions as new_func,
        )
        # re-export 应该是同一对象引用
        assert new_func is old_mod.load_positions

    def test_automated_execution_class_equivalent(self):
        """新旧路径的 AutomatedExecutionSystem 类等价."""
        import automated_execution_system as old_mod
        from utils.execution.automated_execution_system import (
            AutomatedExecutionSystem as new_cls,
        )
        assert new_cls is old_mod.AutomatedExecutionSystem

    def test_daily_build_class_equivalent(self):
        """新旧路径的 DailyBuildHedgeSystem 类等价."""
        import daily_build_and_hedge as old_mod
        from utils.execution.daily_build_and_hedge import (
            DailyBuildHedgeSystem as new_cls,
        )
        assert new_cls is old_mod.DailyBuildHedgeSystem


# ============================================================
# 4. 路径修正测试 (T3.6 关键修正)
# ============================================================
class TestPathFix:
    """验证迁移后的路径修正."""

    def test_rebalance_project_root_resolved(self):
        """rebalance_execution_orders._PROJECT_ROOT 指向 8.4 项目根目录."""
        from utils.execution.rebalance_execution_orders import _PROJECT_ROOT
        root = Path(_PROJECT_ROOT)
        # 验证路径包含项目根目录的标志 (config/positions.json 存在)
        assert (root / "config" / "positions.json").exists()
        # 验证路径不再指向 v7.1 旧路径
        assert "28-终极量化交易系统7.1" not in str(root)
        assert "28-终极量化交易系统8.4" in str(root)

    def test_rebalance_no_hardcoded_v71_path(self):
        """rebalance_execution_orders 源码中不再包含硬编码 v7.1 路径."""
        src_path = _PROJECT_ROOT / "utils" / "execution" / "rebalance_execution_orders.py"
        content = src_path.read_text(encoding="utf-8")
        # 旧 bug 路径不应出现 (除了注释说明)
        assert "e:\\\\各种PY程序\\\\28-终极量化交易系统7.1" not in content
        assert "'e:\\\\各种PY程序\\\\28-终极量化交易系统7.1'" not in content

    def test_automated_execution_project_root_resolved(self):
        """automated_execution_system._PROJECT_ROOT 指向 8.4 项目根目录."""
        from utils.execution.automated_execution_system import _PROJECT_ROOT
        root = Path(_PROJECT_ROOT)
        assert (root / "v8.3_institutional" / "src").exists()
        assert "28-终极量化交易系统8.4" in str(root)

    def test_daily_build_base_dir_resolved(self):
        """daily_build_and_hedge.BASE_DIR 指向 8.4 项目根目录."""
        from utils.execution.daily_build_and_hedge import BASE_DIR
        root = Path(BASE_DIR)
        assert (root / "v8.3_institutional").exists()
        assert "28-终极量化交易系统8.4" in str(root)
        # 验证 BASE_DIR 不是 utils/execution/ 目录
        assert root.name == "28-终极量化交易系统8.4"


# ============================================================
# 5. 关键接口功能测试
# ============================================================
class TestKeyInterfaces:
    """验证迁移后的关键接口可访问且行为正确."""

    def test_rebalance_classify_style(self):
        """classify_style 函数行为正确."""
        from utils.execution.rebalance_execution_orders import classify_style
        style_map = {"600276": "医药", "000001": "银行", "510050": "宽基"}
        result = classify_style(style_map)
        assert "医药" in result
        assert "银行" in result
        assert "宽基" in result
        assert result["医药"]["codes"] == ["600276"]

    def test_rebalance_validate_order_buy(self):
        """validate_order BUY 订单验证 (金额需 >= MIN_TRADE_AMOUNT=10000)."""
        from utils.execution.rebalance_execution_orders import validate_order
        positions = {"600276": 0}
        # 200股 * 50元 = 10000元 = MIN_TRADE_AMOUNT, 满足要求
        result = validate_order("600276", "BUY", 200, 50.0, positions)
        assert result["valid"] is True
        assert result["est_amount"] == 10000
        assert result["errors"] == []

    def test_rebalance_validate_order_sell_exceeds_position(self):
        """validate_order SELL 超持仓验证."""
        from utils.execution.rebalance_execution_orders import validate_order
        positions = {"600276": 50}
        result = validate_order("600276", "SELL", 100, 50.0, positions)
        assert result["valid"] is False
        assert any("超过持仓" in err for err in result["errors"])

    def test_rebalance_calc_current_allocation(self):
        """calc_current_allocation 计算风格配置."""
        from utils.execution.rebalance_execution_orders import calc_current_allocation
        positions = {"600276": 1000, "000001": 500}
        prices = {"600276": 50.0, "000001": 15.0}
        styles = {"600276": "医药", "000001": "银行"}
        result = calc_current_allocation(positions, prices, styles)
        # 医药: 1000*50 = 50000, 银行: 500*15 = 7500, 总: 57500
        assert result["医药"]["amount"] == 50000
        assert result["银行"]["amount"] == 7500
        assert abs(result["医药"]["weight"] - 50000 / 57500) < 0.01

    def test_automated_execution_to_wind_code(self):
        """_to_wind_code 函数行为正确 (返回 (code, is_etf) 元组)."""
        from utils.execution.automated_execution_system import _to_wind_code
        # 测试前缀剥离 + 自动添加后缀 (返回元组: code, is_etf)
        # sh600276 → 600276 (剥前缀) → 6开头 → "600276.SH", is_etf=False
        result = _to_wind_code("sh600276")
        assert result[0] == "600276.SH"
        assert result[1] is False
        # SZ000001 → 000001 (剥前缀) → 0开头 → "000001.SZ", is_etf=False
        result = _to_wind_code("SZ000001")
        assert result[0] == "000001.SZ"
        assert result[1] is False
        # 测试后缀剥离 + 重新添加
        result = _to_wind_code("600276.SH")
        assert result[0] == "600276.SH"
        # 510050 → 51开头 → ETF → "510050.SH", is_etf=True
        result = _to_wind_code("510050")
        assert result[0] == "510050.SH"
        assert result[1] is True

    def test_automated_execution_trading_calendar_class(self):
        """TradingCalendar 类可实例化."""
        from utils.execution.automated_execution_system import TradingCalendar
        cal = TradingCalendar()
        assert cal is not None
        # 验证关键方法存在
        assert hasattr(cal, "get_execution_schedule")


# ============================================================
# 6. system_integration.py 引用修正测试
# ============================================================
class TestSystemIntegrationRef:
    """验证 system_integration.py 引用修正."""

    def test_system_integration_can_import_automated_execution(self):
        """system_integration.py 能正常导入 AutomatedExecutionSystem."""
        # 由于 system_integration 导入链较重, 这里仅验证 import 语句本身可工作
        # 不实际导入整个 system_integration 模块 (避免触发其他依赖)
        src_path = _PROJECT_ROOT / "system_integration.py"
        content = src_path.read_text(encoding="utf-8")
        # 验证新路径优先
        assert "from utils.execution.automated_execution_system import" in content
        # 验证旧路径作为回退
        assert "from automated_execution_system import AutomatedExecutionSystem" in content


# ============================================================
# 7. 迁移完整性测试
# ============================================================
class TestMigrationIntegrity:
    """验证迁移完整性."""

    def test_migrated_files_exist(self):
        """utils/execution/ 下 3 个迁移文件存在."""
        files = [
            "automated_execution_system.py",
            "daily_build_and_hedge.py",
            "rebalance_execution_orders.py",
        ]
        for f in files:
            path = _PROJECT_ROOT / "utils" / "execution" / f
            assert path.exists(), f"迁移文件不存在: {path}"

    def test_reexport_files_exist(self):
        """根目录 3 个 re-export 兼容层文件存在."""
        files = [
            "automated_execution_system.py",
            "daily_build_and_hedge.py",
            "rebalance_execution_orders.py",
        ]
        for f in files:
            path = _PROJECT_ROOT / f
            assert path.exists(), f"re-export 文件不存在: {path}"

    def test_reexport_files_are_small(self):
        """re-export 兼容层文件应该是小文件 (不含完整代码)."""
        # re-export 文件应 < 5KB (约 80 行), 原文件 8KB-93KB
        files = [
            ("automated_execution_system.py", 5000),
            ("daily_build_and_hedge.py", 5000),
            ("rebalance_execution_orders.py", 5000),
        ]
        for f, max_size in files:
            path = _PROJECT_ROOT / f
            size = path.stat().st_size
            assert size < max_size, f"re-export 文件 {f} 过大: {size} bytes (应 < {max_size})"

    def test_migrated_files_are_large(self):
        """迁移后的文件应保留完整代码."""
        # 迁移后的文件应 > 原文件大小 * 0.9 (允许路径修正带来的微小变化)
        files = [
            ("automated_execution_system.py", 90000),
            ("daily_build_and_hedge.py", 45000),
            ("rebalance_execution_orders.py", 8000),
        ]
        for f, min_size in files:
            path = _PROJECT_ROOT / "utils" / "execution" / f
            size = path.stat().st_size
            assert size > min_size, f"迁移文件 {f} 过小: {size} bytes (应 > {min_size})"

    def test_reexport_contains_try_except_fallback(self):
        """re-export 文件包含 try-except 兜底机制 (HC-1 透传)."""
        files = [
            "automated_execution_system.py",
            "daily_build_and_hedge.py",
            "rebalance_execution_orders.py",
        ]
        for f in files:
            path = _PROJECT_ROOT / f
            content = path.read_text(encoding="utf-8")
            # 验证 try-except 兜底机制
            assert "try:" in content
            assert "except ImportError" in content
            # 验证 re-export 注释
            assert "T3.6 re-export" in content
            assert "HC-1" in content


# ============================================================
# 8. 模块属性测试
# ============================================================
class TestModuleAttributes:
    """验证模块的 __file__ 属性指向正确位置."""

    def test_rebalance_module_file_attribute(self):
        """rebalance_execution_orders.__file__ 指向 utils/execution/."""
        from utils.execution import rebalance_execution_orders as mod
        file_path = Path(mod.__file__).resolve()
        assert "utils" in file_path.parts
        assert "execution" in file_path.parts
        assert file_path.name == "rebalance_execution_orders.py"

    def test_daily_build_module_file_attribute(self):
        """daily_build_and_hedge.__file__ 指向 utils/execution/."""
        from utils.execution import daily_build_and_hedge as mod
        file_path = Path(mod.__file__).resolve()
        assert "utils" in file_path.parts
        assert "execution" in file_path.parts
        assert file_path.name == "daily_build_and_hedge.py"

    def test_automated_execution_module_file_attribute(self):
        """automated_execution_system.__file__ 指向 utils/execution/."""
        from utils.execution import automated_execution_system as mod
        file_path = Path(mod.__file__).resolve()
        assert "utils" in file_path.parts
        assert "execution" in file_path.parts
        assert file_path.name == "automated_execution_system.py"


# ============================================================
# 9. __all__ 导出测试
# ============================================================
class TestAllExport:
    """验证 __all__ 导出列表完整."""

    def test_reexport_rebalance_all(self):
        """rebalance_execution_orders re-export __all__ 完整."""
        import rebalance_execution_orders as mod
        expected = {
            "TARGET_ALLOCATION", "MIN_TRADE_AMOUNT", "MAX_SINGLE_ORDER_AMOUNT",
            "MIN_LOT_SIZE", "TARGET_TOTAL", "load_positions", "classify_style",
            "calc_current_allocation", "validate_order", "generate_rebalance_orders",
            "build_report", "main",
        }
        assert expected.issubset(set(mod.__all__))

    def test_reexport_daily_build_all(self):
        """daily_build_and_hedge re-export __all__ 完整."""
        import daily_build_and_hedge as mod
        expected = {"DailyBuildHedgeSystem", "BASE_DIR", "LOG_DIR", "logger"}
        assert expected.issubset(set(mod.__all__))

    def test_reexport_automated_execution_all(self):
        """automated_execution_system re-export __all__ 完整."""
        import automated_execution_system as mod
        expected = {
            "AutomatedExecutionSystem", "ExecutionStrategy",
            "MarketStateEvaluator", "OrderRouter", "TradingCalendar",
        }
        assert expected.issubset(set(mod.__all__))


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
