"""daily_trade_executor.py 盘前指令簇拆解的回归护栏 (2026-09-10)。

背景
----
2275 行的 P0 文件 ``daily_trade_executor.py`` 拆出盘前指令生成簇到
``executor/premarket.py``。难点不是"搬代码", 而是**不能改变 monkeypatch 语义**:
5 个测试文件通过 ``monkeypatch.setattr(daily_trade_executor, NAME, ...)`` 替换
21 个宿主名字 (路径常量 / 函数), 若迁出模块改成 ``from 宿主 import NAME`` 取**值**,
这些补丁会静默失效 (经典 monkeypatch 盲区)。

因此本文件的护栏分两层:

1. **行为层** (失败即证明语义被破坏): 在宿主上打补丁, 断言迁出的实现真的看到了补丁。
   这两条用例在"直接 import 取值的写法"下必然会失败 —— 是本护栏的核心实证。
2. **结构层** (AST 静态断言): 宿主不再定义被迁出的名字 / 新模块定义了全部 /
   受补丁名字在新模块中**绝不以裸名字 Load** (必须是 ``_h().NAME`` 属性式)。

不 import 宿主做 AST 断言, 避免副作用; 行为层用例才真正 import。
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOST = REPO_ROOT / "daily_trade_executor.py"
PREMARKET = REPO_ROOT / "executor" / "premarket.py"

# 2026-09-10 迁出并需要在宿主重导出的名字 (顺序与宿主重导出块一致)
MOVED_NAMES = [
    "DEFAULT_PRICES",
    "_allocate_position",
    "_build_instruction_file",
    "_build_risk_checks",
    "_collect_pending_positions",
    "_compute_price_band",
    "_compute_progress_ratio",
    "_precheck_instructions_preconditions",
    "_refresh_etf_flow",
    "_run_wt_risk_precheck",
    "_save_instruction_file",
    "adjust_allocation_by_signal",
    "assess_etf_signal",
    "calculate_daily_budget",
    "confirm_all_instructions",
    "generate_instructions",
    "generate_next_trading_day_plan",
    "is_accumulation_period",
    "load_latest_prices",
    "load_trade_plan",
    "render_instructions_md",
]

# 被既有测试 monkeypatch.setattr(daily_trade_executor, NAME, ...) 替换的名字
PATCHED_NAMES = {
    "INSTRUCTIONS_DIR",
    "POSITIONS_FILE",
    "PROGRESS_FILE",
    "PROJECT_ROOT",
    "TRADE_PLAN_FILE",
    "_build_and_save_execution_report",
    "_check_execution_preconditions",
    "_cost_model",
    "_execute_single_instruction",
    "_run_wt_risk_block_check",
    "atomic_write_json",
    "fetch_prediction_signals",
    "generate_instructions",
    "generate_next_trading_day_plan",
    "init_wt_modules",
    "is_trading_day",
    "load_build_progress",
    "load_latest_prices",
    "load_positions",
    "load_trade_plan",
    "save_build_progress",
}

# 拆解后宿主行数上限 (拆解前 2275 行)
MAX_HOST_LINES = 1500


def _top_level_defs(src: str) -> set[str]:
    """返回模块顶层定义的函数 / 常量名集合 (不含 import 进来的重导出名)。"""
    tree = ast.parse(src)
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    names.add(tgt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


class TestStructuralSplit:
    """AST 静态护栏 (不 import 宿主)。"""

    def test_host_no_longer_defines_moved_names(self):
        defined = _top_level_defs(HOST.read_text(encoding="utf-8"))
        leftover = sorted(set(MOVED_NAMES) & defined)
        assert not leftover, f"宿主仍定义了已迁出的名字: {leftover}"

    def test_premarket_defines_all_moved_names(self):
        defined = _top_level_defs(PREMARKET.read_text(encoding="utf-8"))
        missing = sorted(set(MOVED_NAMES) - defined)
        assert not missing, f"executor/premarket.py 缺少应迁出的名字: {missing}"

    def test_patched_names_are_never_loaded_directly(self):
        """受补丁的名字只能以 ``_h().NAME`` 形式出现, 不允许裸名字 Load。

        这条是 monkeypatch 语义不失效的根本保证: 一旦有人把 ``_h().INSTRUCTIONS_DIR``
        改回 ``from daily_trade_executor import INSTRUCTIONS_DIR``, 本用例立刻失败。
        """
        tree = ast.parse(PREMARKET.read_text(encoding="utf-8"))
        offenders = [
            (node.lineno, node.id)
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in PATCHED_NAMES
        ]
        assert not offenders, f"受补丁名字被直接引用 (必须 _h().NAME): {offenders}"

    def test_host_line_count_stays_bounded(self):
        n = len(HOST.read_text(encoding="utf-8").splitlines())
        assert n <= MAX_HOST_LINES, f"宿主回涨到 {n} 行 (> {MAX_HOST_LINES})"

    def test_premarket_has_no_naive_datetime_now(self):
        """DTZ 等价护栏: 迁出代码不得引入裸 datetime.now() / date.today()。"""
        src = PREMARKET.read_text(encoding="utf-8")
        assert "datetime.now(" not in src
        assert "date.today(" not in src


class TestRuntimeReexport:
    """运行时契约: dte.NAME 与 executor.premarket.NAME 必须是同一对象。"""

    def test_reexport_identity(self):
        import daily_trade_executor as dte
        import executor.premarket as pm

        mismatched = [n for n in MOVED_NAMES if getattr(dte, n, None) is not getattr(pm, n, None)]
        assert not mismatched, f"重导出后对象不同一: {mismatched}"

    def test_h_resolves_to_host_module(self):
        import daily_trade_executor as dte
        import executor.premarket as pm

        assert pm._h() is dte


class TestMonkeypatchStillReachesMovedCode:
    """行为层护栏: 在宿主上打补丁, 迁出的实现必须看到补丁。

    这两条用例在"迁出模块直接 import 宿主名字取值"的写法下必然失败,
    是本拆解"零行为变更"的核心实证。
    """

    def test_patched_constant_is_seen(self, monkeypatch, tmp_path):
        """patch TRADE_PLAN_FILE -> 迁出的 load_trade_plan() 必须读补丁后的路径。"""
        import daily_trade_executor as dte

        plan = tmp_path / "plan.json"
        plan.write_text(
            '{"marker": "from-tmp", "stock_etf_account": {"positions": [{"code": "600519"}]}}',
            encoding="utf-8",
        )
        monkeypatch.setattr(dte, "TRADE_PLAN_FILE", plan)

        assert dte.load_trade_plan()["marker"] == "from-tmp"

    def test_patched_function_is_seen(self, monkeypatch):
        """patch is_trading_day -> 迁出的前置检查必须按补丁值判定为跳过。

        2026-09-10 是周四 (真实交易日), 故若补丁失效该用例会走到"建仓期"分支而失败。
        """
        from datetime import date

        import daily_trade_executor as dte

        monkeypatch.setattr(dte, "is_trading_day", lambda d: False)

        out = dte._precheck_instructions_preconditions("2026-09-10", date(2026, 9, 10))

        assert out is not None
        assert out["status"] == "skipped"
        assert "非交易日" in out["reason"]
