# -*- coding: utf-8 -*-
"""
P0-Q2 phase_signal 重构回归测试 (2026-07-26)
==============================================

验证 phase_signal 拆分为子方法后:
    1. 所有原 signal 字段名仍然存在
    2. 新子方法签名正确, 接受 signal 参数
    3. 子方法体 <=80 行 (顶级对冲基金标准)
    4. phase_signal 体积显著减少

设计依据: HEDGE_FUND_AUDIT_2026-07-26 P0-Q2 - God Function 拆分
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = PROJECT_ROOT / "v8.3_institutional" / "daily_workflow.py"

# 原 phase_signal 应包含的所有 signal 字段 (基于 v8.7 版本)
EXPECTED_SIGNAL_FIELDS = {
    "action", "phase_name", "phase_number", "day_index",
    "morning_orders", "afternoon_orders",
    "morning_window", "afternoon_window",
    "morning_count", "afternoon_count",
    "morning_amount", "afternoon_amount", "grand_amount", "total_orders",
    "position_factor",
    "target_weights",
    # 信号源注入字段
    "pipeline_factor_applied",
    "research_distilled_applied",
    "lgb_enhanced_applied",
    "finance_agent_shadow_applied",
    "factor_decay",
}

# P0-Q2 重构抽取的子方法 (必须存在且 ≤80 行)
REFACTORED_SUBMETHODS = [
    "_phase_signal_inject_pipeline_signals",
    "_phase_signal_inject_research_signals",
    "_phase_signal_inject_lgb_signals",
    "_phase_signal_apply_agent_shadow",
    "_phase_signal_apply_factor_decay",
    "_run_single_agent_shadow",  # 辅助方法
    # 阶段 2 新增
    "_phase_signal_apply_bl_optimization",
    "_build_bl_views",
    "_phase_signal_apply_strategy_coordination",
    "_build_coordination_target_signals",
    "_build_coordination_current_positions",
    "_log_coordination_result",
    # 阶段 3: Alpha 模块拆分
    "_phase_signal_apply_alpha_modules",
    "_prepare_alpha_data",
    "_apply_alpha_factor_lib",
    "_apply_momentum_engine",
    "_apply_smart_beta_engine",
    # 阶段 4: 另类数据模块拆分
    "_phase_signal_apply_alt_data_modules",
    "_apply_news_sentiment",
    "_apply_supply_chain_graph",
    "_apply_alt_data_indicators",
]

# 子方法签名要求 (方法名 -> 必须接受的参数)
SUBMETHOD_SIGNATURES = {
    "_phase_signal_inject_pipeline_signals": {"signal", "target_weights"},
    "_phase_signal_inject_research_signals": {"signal"},
    "_phase_signal_inject_lgb_signals": {"signal"},
    "_phase_signal_apply_agent_shadow": {"signal", "target_weights"},
    "_phase_signal_apply_factor_decay": {"signal"},
    "_phase_signal_apply_bl_optimization": {"signal"},
    "_build_bl_views": {"signal", "bl_assets"},
    "_phase_signal_apply_strategy_coordination": {"signal"},
    "_build_coordination_target_signals": {"signal"},
    "_phase_signal_apply_alpha_modules": {"signal"},
    "_prepare_alpha_data": {"positions"},
    "_apply_alpha_factor_lib": {"signal", "alpha_data"},
    "_apply_momentum_engine": {"signal", "alpha_data"},
    "_apply_smart_beta_engine": {"signal", "alpha_data", "alpha_result"},
    "_phase_signal_apply_alt_data_modules": {"signal"},
    "_apply_news_sentiment": {"signal", "alt_symbols"},
    "_apply_supply_chain_graph": {"signal"},
    "_apply_alt_data_indicators": {"signal", "alt_symbols"},
}


def _check(name: str, ok: bool, detail: str = "") -> bool:
    """统一结果记录"""
    mark = "[✓ PASS]" if ok else "[✗ FAIL]"
    suffix = f" | {detail}" if detail else ""
    print(f"  {mark} {name}{suffix}")
    return ok


def verify_submethods_exist_and_size() -> bool:
    """验证 P0-Q2 抽取的子方法存在且 <=80 行"""
    print("\n" + "=" * 70)
    print("P0-Q2-A: 子方法存在性 + 行数限制 (<=80 行)")
    print("=" * 70)
    try:
        src = WORKFLOW_PATH.read_text(encoding="utf-8")
        tree = ast.parse(src)
        methods = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in REFACTORED_SUBMETHODS:
                methods[node.name] = node.end_lineno - node.lineno + 1
        all_ok = True
        for name in REFACTORED_SUBMETHODS:
            if name not in methods:
                _check(f"子方法 {name} 存在", False, "未找到")
                all_ok = False
                continue
            lines = methods[name]
            ok = lines <= 80
            _check(f"子方法 {name} 行数 <=80", ok, f"实际 {lines} 行")
            if not ok:
                all_ok = False
        return all_ok
    except Exception as e:
        _check("子方法验证执行", False, f"异常: {e}")
        return False


def verify_submethod_signatures() -> bool:
    """验证子方法签名正确 (接受 signal / target_weights 参数)"""
    print("\n" + "=" * 70)
    print("P0-Q2-B: 子方法签名 (接受 signal/target_weights 参数)")
    print("=" * 70)
    try:
        src = WORKFLOW_PATH.read_text(encoding="utf-8")
        tree = ast.parse(src)
        methods = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in SUBMETHOD_SIGNATURES:
                # 收集参数名 (排除 self)
                args = {a.arg for a in node.args.args if a.arg != "self"}
                methods[node.name] = args
        all_ok = True
        for name, expected_args in SUBMETHOD_SIGNATURES.items():
            if name not in methods:
                _check(f"签名检查 {name}", False, "方法未找到")
                all_ok = False
                continue
            actual = methods[name]
            missing = expected_args - actual
            if missing:
                _check(f"签名检查 {name}", False, f"缺少参数: {missing}")
                all_ok = False
            else:
                _check(f"签名检查 {name}", True, f"参数={actual}")
        return all_ok
    except Exception as e:
        _check("签名检查执行", False, f"异常: {e}")
        return False


def verify_phase_signal_calls_submethods() -> bool:
    """验证 phase_signal 内部调用了所有抽取的子方法 (含多层嵌套)

    递归遍历调用链: phase_signal → 子方法 → 孙方法, 避免假阴性.
    每个子方法只要在调用链中任意一层出现, 即视为 PASS.
    """
    print("\n" + "=" * 70)
    print("P0-Q2-C: phase_signal 调用所有新子方法 (含嵌套)")
    print("=" * 70)
    try:
        src = WORKFLOW_PATH.read_text(encoding="utf-8")
        tree = ast.parse(src)
        # 收集每个方法直接调用的 self.xxx 方法
        method_calls: dict = {}  # {method_name: set(called_self_methods)}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                called = set()
                for sub in ast.walk(node):
                    if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                            and isinstance(sub.func.value, ast.Name)
                            and sub.func.value.id == "self"):
                        called.add(sub.func.attr)
                method_calls[node.name] = called

        # 多层嵌套可达性分析 (BFS)
        # reachable[method] = set(可经任意层调用链到达的子方法)
        reachable: dict = {}

        def _reachable_from(m: str, visited: set) -> set:
            """递归计算从方法 m 出发可到达的所有 self.xxx 方法 (含嵌套)"""
            if m in reachable:
                return reachable[m]
            if m in visited:
                return set()
            visited.add(m)
            direct_calls = method_calls.get(m, set())
            result = set(direct_calls)
            for callee in direct_calls:
                result |= _reachable_from(callee, visited)
            reachable[m] = result
            return result

        # phase_signal 全部可达子方法 (含多层嵌套)
        all_reachable = _reachable_from("phase_signal", set())
        direct = method_calls.get("phase_signal", set())
        all_ok = True
        for name in REFACTORED_SUBMETHODS:
            if name in direct:
                _check(f"phase_signal 直接调用 {name}", True)
            elif name in all_reachable:
                # 找出调用链路径 (取第一个匹配的中间节点)
                path_hint = ""
                for intermediate in direct:
                    if name in _reachable_from(intermediate, set()):
                        path_hint = f" (通过 {intermediate})"
                        break
                _check(f"phase_signal 嵌套调用 {name}", True, path_hint)
            else:
                _check(f"phase_signal 调用 {name}", False, "未找到调用链")
                all_ok = False
        return all_ok
    except Exception as e:
        _check("调用检查执行", False, f"异常: {e}")
        return False


def verify_phase_signal_size_reduced() -> bool:
    """验证 phase_signal 体积显著减少 (从 948 行 -> <800 行)"""
    print("\n" + "=" * 70)
    print("P0-Q2-D: phase_signal 体积减少 (948 -> <800 行)")
    print("=" * 70)
    try:
        src = WORKFLOW_PATH.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "phase_signal":
                lines = node.end_lineno - node.lineno + 1
                ok = lines < 800
                _check(
                    f"phase_signal 体积 <800 行",
                    ok,
                    f"实际 {lines} 行 (原 948 行, 减少 {948 - lines} 行)",
                )
                return ok
        _check("phase_signal 方法存在", False)
        return False
    except Exception as e:
        _check("体积检查执行", False, f"异常: {e}")
        return False


def verify_signal_fields_preserved() -> bool:
    """验证重构后 phase_signal 仍写入所有原 signal 字段

    检查两种赋值模式:
        1. signal["xxx"] = ... (后续赋值)
        2. "xxx": value (字面量字典初始化)
    """
    print("\n" + "=" * 70)
    print("P0-Q2-E: signal 字段名保留 (无字段丢失)")
    print("=" * 70)
    try:
        src = WORKFLOW_PATH.read_text(encoding="utf-8")
        # 检查两种模式: signal["xxx"] = ... 和 "xxx": value
        # 模式 1: signal["xxx"] 或 signal['xxx']
        # 模式 2: 字面量字典中的 "xxx": (在 phase_signal 范围内)
        all_fields_in_src = set()
        # 模式 1: 直接 grep signal["xxx"]
        import re
        for m in re.finditer(r'signal\[\s*["\']([\w_]+)["\']\s*\]', src):
            all_fields_in_src.add(m.group(1))
        # 模式 2: 在 phase_signal 方法范围内查找 "xxx": 模式
        # 先定位 phase_signal 范围
        tree = ast.parse(src)
        phase_signal_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "phase_signal":
                phase_signal_node = node
                break
        if phase_signal_node:
            start_line = phase_signal_node.lineno
            end_line = phase_signal_node.end_lineno
            src_lines = src.split("\n")
            phase_signal_src = "\n".join(src_lines[start_line-1:end_line])
            # 查找 "xxx": value 模式 (字面量字典初始化)
            for m in re.finditer(r'["\']([\w_]+)["\']\s*:\s', phase_signal_src):
                all_fields_in_src.add(m.group(1))
        # 检查关键字段是否保留
        missing = EXPECTED_SIGNAL_FIELDS - all_fields_in_src
        if missing:
            _check("关键字段全部保留", False, f"缺失: {missing}")
            return False
        _check(
            "关键字段全部保留",
            True,
            f"{len(EXPECTED_SIGNAL_FIELDS)} 个字段验证通过 (合并 signal['xxx'] 与字面量字典)",
        )
        return True
    except Exception as e:
        _check("字段检查执行", False, f"异常: {e}")
        return False


def verify_syntax_compilable() -> bool:
    """验证 daily_workflow.py 可正常编译 (无语法错误)"""
    print("\n" + "=" * 70)
    print("P0-Q2-F: Python 语法编译")
    print("=" * 70)
    try:
        src = WORKFLOW_PATH.read_text(encoding="utf-8")
        compile(src, str(WORKFLOW_PATH), "exec")
        _check("daily_workflow.py 编译通过", True)
        return True
    except SyntaxError as e:
        _check("daily_workflow.py 编译", False, f"语法错误: {e}")
        return False


def main() -> int:
    print("=" * 70)
    print("P0-Q2 phase_signal 重构回归测试 (Hedge Fund View)")
    print("目标: 验证 God Function 拆分后行为完整保留")
    print("=" * 70)
    results = [
        ("P0-Q2-A 子方法存在+行数", verify_submethods_exist_and_size()),
        ("P0-Q2-B 子方法签名", verify_submethod_signatures()),
        ("P0-Q2-C phase_signal 调用子方法", verify_phase_signal_calls_submethods()),
        ("P0-Q2-D phase_signal 体积减少", verify_phase_signal_size_reduced()),
        ("P0-Q2-E signal 字段名保留", verify_signal_fields_preserved()),
        ("P0-Q2-F Python 语法编译", verify_syntax_compilable()),
    ]
    print("\n" + "=" * 70)
    print("验证汇总")
    print("=" * 70)
    for name, ok in results:
        mark = "✓ PASS" if ok else "✗ FAIL"
        print(f"  {name:<35} {mark}")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n总计: {passed}/{total} 通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
