"""统一入口 main() 拆解（审计批次三 item 11 续做，2026-09-10）的结构回归测试。

背景：`量化策略系统_统一入口_v8.6.py` 的 `main()` 原为 593 行，其中 ~480 行是纯声明
（模式注册表 / argparse epilog / 选项声明）。已迁出为：

    cli/handlers/cli_parser.py       MODE_SPECS + build_parser()
    cli/handlers/etf_combo_runner.py run_etf_combo()
    cli/handlers/mode_dispatch.py    dispatch()

**为什么这些测试必须存**（拆解引入的新风险，静态可判）：
    1. handler 绑定被从 `MODES` 里拆出（避免 `cli_parser ↔ 入口` 循环导入）⇒ 出现
       "模式表声明了 dest 但入口没绑定 handler" 的新漂移面。运行时由 `dispatch()` fail-closed 兜底，
       但**测试侧要提前拦**，不能让漂移活到运行期。
    2. 声明层一旦被重新塞回 `main()`，等于回退本次拆解 ⇒ 用行数上限守住。

设计：**纯 AST 静态解析，不 import 入口模块**（沿用 `test_unified_entry_audit_regression_20260824.py`
的约定，避免大文件导入副作用与重型依赖）。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_ENTRY_CANDIDATES = sorted(p for p in ROOT.glob("*v8.6.py") if p.is_file())
ENTRY: Path | None = _ENTRY_CANDIDATES[0] if _ENTRY_CANDIDATES else None
PARSER_MOD = ROOT / "cli" / "handlers" / "cli_parser.py"
ETF_MOD = ROOT / "cli" / "handlers" / "etf_combo_runner.py"
DISPATCH_MOD = ROOT / "cli" / "handlers" / "mode_dispatch.py"

# 拆分前后一致的运行模式数（新增/删除模式时须同步更新，属有意为之的摩擦）
EXPECTED_MODE_COUNT = 37


def _require_entry() -> Path:
    assert ENTRY is not None, f"未在 {ROOT} 找到 量化策略系统_统一入口_v8.6.py"
    return ENTRY


def _tree(p: Path) -> ast.Module:
    assert p.is_file(), f"缺失文件 {p}"
    return ast.parse(p.read_text(encoding="utf-8"))


def _module_level_assign(tree: ast.Module, name: str) -> ast.Assign:
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == name:
            return node
    raise AssertionError(f"模块级赋值 {name} 未找到")


def _spec_dests() -> list[str]:
    node = _module_level_assign(_tree(PARSER_MOD), "MODE_SPECS")
    dests = []
    for el in node.value.elts:
        assert len(el.elts) == 3, "MODE_SPECS 元素必须是 (flag, dest, help) 三元组"
        dests.append(ast.literal_eval(el.elts[1]))
    return dests


def _handler_dests() -> list[str]:
    node = _module_level_assign(_tree(_require_entry()), "MODE_HANDLERS")
    return [ast.literal_eval(k) for k in node.value.keys]


class TestModeTableConsistency:
    """模式表（cli_parser）与 handler 绑定表（入口）必须严格同构。"""

    def test_dest_sets_identical(self):
        specs, handlers = _spec_dests(), _handler_dests()
        assert sorted(specs) == sorted(handlers), (
            "模式表与 handler 绑定表漂移: "
            f"仅声明未绑定={sorted(set(specs) - set(handlers))} "
            f"仅绑定未声明={sorted(set(handlers) - set(specs))}"
        )

    def test_no_duplicate_dest(self):
        specs = _spec_dests()
        dupes = sorted({d for d in specs if specs.count(d) > 1})
        assert not dupes, f"MODE_SPECS 存在重复 dest: {dupes}"

    def test_mode_count_unchanged(self):
        n = len(_spec_dests())
        assert n == EXPECTED_MODE_COUNT, (
            f"运行模式数由 {EXPECTED_MODE_COUNT} 变为 {n} —— 若属有意增删请同步更新本断言"
        )

    def test_help_texts_non_empty(self):
        node = _module_level_assign(_tree(PARSER_MOD), "MODE_SPECS")
        for el in node.value.elts:
            flag, dest, help_text = (ast.literal_eval(x) for x in el.elts)
            assert flag.startswith("--"), f"{dest} 的 flag 必须以 -- 开头: {flag}"
            assert help_text.strip(), f"{dest} 的 help 文本为空"


class TestMainStaysThin:
    """main() 只能做编排，声明层必须留在 cli_parser.py。"""

    MAX_MAIN_LINES = 40

    @staticmethod
    def _main() -> ast.FunctionDef:
        tree = _tree(_require_entry())
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "main":
                return node
        raise AssertionError("入口文件未定义模块级 main()")

    def test_main_within_line_budget(self):
        main = self._main()
        size = main.end_lineno - main.lineno + 1
        assert size <= self.MAX_MAIN_LINES, (
            f"main() 已膨胀到 {size} 行（上限 {self.MAX_MAIN_LINES}）—— "
            "声明层应留在 cli/handlers/cli_parser.py，勿塞回入口"
        )

    def test_main_delegates_to_extracted_modules(self):
        main = self._main()
        called = {
            n.func.id
            for n in ast.walk(main)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert {"build_parser", "dispatch"} <= called, (
            f"main() 未通过迁出模块编排，实际调用: {sorted(called)}"
        )

    def test_main_no_longer_declares_options(self):
        """main() 不应再出现选项声明（add_argument / 互斥组），只允许 parser.parse_args()。"""
        main = self._main()
        attrs = {n.attr for n in ast.walk(main) if isinstance(n, ast.Attribute)}
        forbidden = attrs & {"add_argument", "add_mutually_exclusive_group"}
        assert not forbidden, f"main() 内出现内联选项声明 {sorted(forbidden)}，应迁至 build_parser()"
        assert "parse_args" in attrs, "main() 应自行解析 args（保持唯一事实源）"


class TestExtractedModules:
    """迁出模块的存在性与对外入口。"""

    def test_public_entrypoints_present(self):
        expected = {
            PARSER_MOD: "build_parser",
            ETF_MOD: "run_etf_combo",
            DISPATCH_MOD: "dispatch",
        }
        for path, fn in expected.items():
            names = {
                n.name for n in _tree(path).body if isinstance(n, ast.FunctionDef)
            }
            assert fn in names, f"{path.name} 未定义 {fn}()"

    def test_dispatch_fails_closed_on_unbound_dest(self):
        """运行期实证：dest 无 handler 绑定 ⇒ SystemExit(1)，且正常路径调用一次 handler。"""
        import pytest

        from cli.handlers import mode_dispatch

        specs = [("--fake", "fake", "伪模式")]
        seen: list[object] = []
        args = type("A", (), {"fake": True})()
        mode_dispatch.dispatch(args, specs, {"fake": lambda a: seen.append(a)})
        assert len(seen) == 1, "正常路径未调用 handler"

        with pytest.raises(SystemExit) as excinfo:
            mode_dispatch.dispatch(args, specs, {})
        assert excinfo.value.code == 1, "模式表/绑定表漂移必须显式失败（非 0），不得静默跳过"
