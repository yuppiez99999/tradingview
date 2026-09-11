"""daily_workflow.DailyWorkflow mixin 拆解的回归护栏 (2026-09-11)。

背景
----
``v8.3_institutional/daily_workflow.py`` (2180 行) 已做过一轮 phase 拆分, 多数
``phase_*`` 方法是**委托 shim** (真逻辑在 ``workflow/phases/*``)。本轮把仅存的
两大**真实现**簇迁出为 mixin:

* ``workflow_mixins/context_loader.py`` → ``ContextLoaderMixin`` (装载/环境感知, 9 方法)
* ``workflow_mixins/sim_execution.py`` → ``SimExecutionMixin`` (模拟执行, 6 方法)

编排层 (``__init__`` / ``phase_*`` shim / ``_build_context`` / ``run`` / ``main``)
留在宿主。迁出方法只引用标准库与 typing 名字 ⇒ **纯代码搬移, 无需宿主属性式间接层**
(与 daily_trade_executor 拆解不同, 那次因 21 个被 patch 的宿主名字必须走 ``_h()``)。

关键约束 (本护栏锁死):

1. **落点不能是 ``v8.3_institutional/daily_workflow/`` 目录** —— 带 ``__init__.py``
   的同名包会**抢先**于同名模块被 import, 直接弄坏 4 个测试文件 + v87 release gate
   依赖的 ``from daily_workflow import DailyWorkflow``。
2. ``SmartOrderRouter`` / ``MockBroker`` / ``AlgoType`` 属 V75 **可选导入**: 注解走
   ``TYPE_CHECKING``、运行时在 ``_execute_order_batch`` 内局部导入 —— 若改成模块级
   导入, 可选模块缺失时会把宿主整体导入炸掉, 破坏 graceful degradation。
3. logger 名字必须保持 ``v75.daily_workflow``, 否则日志分区会漂移。
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOST = REPO_ROOT / "v8.3_institutional" / "daily_workflow.py"
MIXIN_DIR = REPO_ROOT / "v8.3_institutional" / "workflow_mixins"
LOADER = MIXIN_DIR / "context_loader.py"
SIMEX = MIXIN_DIR / "sim_execution.py"

MOVED_LOADER = [
    "_load_fusion_config",
    "_load_external_reports",
    "_detect_market_regime",
    "_get_regime_weights",
    "_calculate_external_factor",
    "_get_ifind_insights",
    "_get_edb_futures_data",
    "_get_futures_scanner_summary",
    "_load_trade_plan",
]
MOVED_SIMEX = [
    "_execute_sim_mode",
    "_execute_sim_batch",
    "_normalize_sim_order",
    "_extract_futures_night_orders",
    "_execute_order_batch",
    "_aggregate_order_summary",
]
MOVED = MOVED_LOADER + MOVED_SIMEX

# 编排层必须留在宿主的方法
KEPT_ORCHESTRATION = [
    "__init__",
    "_build_context",
    "phase_check",
    "phase_execute",
    "phase_report",
    "run",
]

MAX_HOST_LINES = 1600


def _class_methods(path: Path, class_name: str) -> set[str]:
    """返回指定模块中指定类的顶层方法名集合。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                m.name for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    raise AssertionError(f"{path} 中找不到类 {class_name}")


class TestStructuralSplit:
    """AST 静态护栏 (不 import 宿主)。"""

    def test_host_no_longer_defines_moved_methods(self):
        defined = _class_methods(HOST, "DailyWorkflow")
        leftover = sorted(set(MOVED) & defined)
        assert not leftover, f"宿主仍定义了已迁出的方法: {leftover}"

    def test_loader_mixin_defines_expected_methods(self):
        got = _class_methods(LOADER, "ContextLoaderMixin")
        missing = sorted(set(MOVED_LOADER) - got)
        assert not missing, f"ContextLoaderMixin 缺少应迁出的方法: {missing}"

    def test_simex_mixin_defines_expected_methods(self):
        got = _class_methods(SIMEX, "SimExecutionMixin")
        missing = sorted(set(MOVED_SIMEX) - got)
        assert not missing, f"SimExecutionMixin 缺少应迁出的方法: {missing}"

    def test_host_keeps_orchestration(self):
        defined = _class_methods(HOST, "DailyWorkflow")
        missing = sorted(set(KEPT_ORCHESTRATION) - defined)
        assert not missing, f"编排层方法被误迁出: {missing}"

    def test_host_inherits_both_mixins(self):
        tree = ast.parse(HOST.read_text(encoding="utf-8"))
        bases: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "DailyWorkflow":
                bases = [ast.unparse(b) for b in node.bases]
        assert "ContextLoaderMixin" in bases, f"DailyWorkflow 基类缺少 ContextLoaderMixin: {bases}"
        assert "SimExecutionMixin" in bases, f"DailyWorkflow 基类缺少 SimExecutionMixin: {bases}"

    def test_no_same_name_package_created(self):
        """关键约束: 不得创建与宿主模块同名的包 (会抢在模块前被 import)。"""
        assert not (MIXIN_DIR.parent / "daily_workflow").exists(), (
            "不得创建 v8.3_institutional/daily_workflow/ 目录: 带 __init__.py 的同名包"
            "会抢先于 daily_workflow.py 被 import, 弄坏 from daily_workflow import DailyWorkflow"
        )

    def test_optional_imports_not_module_level(self):
        """SmartOrderRouter/MockBroker/AlgoType 不得在 mixin 模块级导入 (可选降级语义)。"""
        for path in (LOADER, SIMEX):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    mod = getattr(node, "module", "") or ""
                    names = [a.name for a in node.names]
                    assert not any(
                        n in {"SmartOrderRouter", "MockBroker", "AlgoType"} for n in names
                    ), f"{path.name} 模块级导入了 V75 可选符号 {names} — 会破坏 graceful degradation"
                    assert "smart_order_router" not in mod and "algo_engine" not in mod

    def test_runtime_algotype_import_stays_inside_method(self):
        """AlgoType 的运行时使用必须在方法内局部导入 (可选模块缺失时不炸模块导入)。"""
        src = SIMEX.read_text(encoding="utf-8")
        assert "from execution.algo_engine import AlgoType" in src
        tree = ast.parse(src)
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = getattr(node, "module", "") or ""
                assert "algo_engine" not in mod, "AlgoType 被提到模块级导入"

    def test_host_line_count_stays_bounded(self):
        n = len(HOST.read_text(encoding="utf-8").splitlines())
        assert n <= MAX_HOST_LINES, f"宿主回涨到 {n} 行 (> {MAX_HOST_LINES})"

    def test_logger_name_preserved_in_mixins(self):
        """logger 名字必须与宿主一致, 否则日志分区会漂移。"""
        assert 'getLogger("v75.daily_workflow")' in LOADER.read_text(encoding="utf-8")
        assert 'getLogger("v75.daily_workflow")' in SIMEX.read_text(encoding="utf-8")


class TestRuntimeMro:
    """运行时契约: mixin 经 MRO 挂到 DailyWorkflow, 方法一个都不能少。"""

    def test_all_moved_methods_resolvable(self):
        import sys

        sys.path.insert(0, str(REPO_ROOT / "v8.3_institutional"))
        try:
            import daily_workflow as m  # noqa: PLC0415
            from workflow_mixins.context_loader import ContextLoaderMixin  # noqa: PLC0415
            from workflow_mixins.sim_execution import SimExecutionMixin  # noqa: PLC0415
        finally:
            sys.path.remove(str(REPO_ROOT / "v8.3_institutional"))

        assert issubclass(m.DailyWorkflow, ContextLoaderMixin)
        assert issubclass(m.DailyWorkflow, SimExecutionMixin)
        missing = [n for n in MOVED if not hasattr(m.DailyWorkflow, n)]
        assert not missing, f"MRO 解析失败, 以下方法不可达: {missing}"

        # 迁出方法确实由对应 mixin 提供 (而不是宿主残留)
        for n in MOVED_LOADER:
            assert getattr(m.DailyWorkflow, n) is getattr(ContextLoaderMixin, n)
        for n in MOVED_SIMEX:
            assert getattr(m.DailyWorkflow, n) is getattr(SimExecutionMixin, n)

    def test_degradation_flags_still_exported(self):
        """宿主模块级可选导入旗标必须仍然可导入 (tests/test_eod_dry_run.py 依赖)。"""
        import sys

        sys.path.insert(0, str(REPO_ROOT / "v8.3_institutional"))
        try:
            import daily_workflow as m  # noqa: PLC0415
        finally:
            sys.path.remove(str(REPO_ROOT / "v8.3_institutional"))

        assert hasattr(m, "V75_READY")
        assert hasattr(m, "DailyWorkflow")
