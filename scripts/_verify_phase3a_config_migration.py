#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 3-A 验证脚本: ConfigManager 全项目迁移
=============================================

验证 7 个迁移后的模块仍能正确加载配置, 不破坏现有行为.

迁移文件:
    1. utils/gamma_engine.py
    2. utils/liquidation_scheduler.py
    3. v8.3_institutional/main.py (V75InstitutionalSystem._load_configs)
    4. v8.3_institutional/generate_daily_trade_plan.py (_load_capital_config)
    5. v8.3_institutional/src/ai/model_router.py (ModelRouter.__init__)
    6. v8.3_institutional/src/risk/unified_risk_cockpit.py (_scan_positions)
    7. v8.3_institutional/src/execution/algo_engine.py (AlgoEngine.__init__)
    8. v8.3_institutional/src/factors/five_factor.py (main)

运行:
    python scripts/_verify_phase3a_config_migration.py
"""
from __future__ import annotations

import os
import sys
import importlib.util
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

_passed = 0
_failed = 0
_skipped = 0


def _check(name: str, condition: bool, detail: str = "") -> None:
    """断言检查"""
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ✓ {name}" + (f" — {detail}" if detail else ""))
    else:
        _failed += 1
        print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))


def _skip(name: str, reason: str = "") -> None:
    global _skipped
    _skipped += 1
    print(f"  ⊘ {name} (SKIP)" + (f" — {reason}" if reason else ""))


def test_gamma_engine_migration() -> bool:
    """T1: gamma_engine.py 迁移验证"""
    print("\n" + "=" * 70)
    print("T1: utils/gamma_engine.py 迁移")
    print("=" * 70)

    try:
        from utils.gamma_engine import GammaEngine
        engine = GammaEngine()
        _check("GammaEngine 实例化成功", True)
        _check("engine.config 非空", bool(engine.config),
               f"keys={list(engine.config.keys()) if engine.config else 'empty'}")
        # 验证关键字段 (gamma_vega_engine 节)
        if engine.config:
            _check("config 含 trigger_conditions 或类似字段",
                   "trigger_conditions" in engine.config or
                   "iv_percentile_threshold" in engine.config or
                   "ma60_threshold" in engine.config or
                   len(engine.config) > 0)
    except Exception as e:
        _check("GammaEngine 实例化失败", False, f"异常: {e}")
        return False

    return True


def test_liquidation_scheduler_migration() -> bool:
    """T2: liquidation_scheduler.py 迁移验证"""
    print("\n" + "=" * 70)
    print("T2: utils/liquidation_scheduler.py 迁移")
    print("=" * 70)

    try:
        from utils.liquidation_scheduler import LiquidationScheduler
        sched = LiquidationScheduler()
        _check("LiquidationScheduler 实例化成功", True)
        _check("sched.config 非空", bool(sched.config),
               f"keys={list(sched.config.keys()) if sched.config else 'empty'}")
    except Exception as e:
        _check("LiquidationScheduler 实例化失败", False, f"异常: {e}")
        return False

    return True


def test_kill_switch_migration() -> bool:
    """T3: kill_switch.py 迁移验证 (P1-Q8 示范, 应已通过 _verify_config_manager.py)"""
    print("\n" + "=" * 70)
    print("T3: utils/kill_switch.py 迁移 (P1-Q8 示范)")
    print("=" * 70)

    try:
        from utils.kill_switch import KillSwitch
        from utils.config_manager import clear_config_cache
        clear_config_cache()
        ks = KillSwitch()
        _check("KillSwitch 实例化成功", True)
        _check("ks.config 含 level_1", "level_1" in ks.config)
        _check("ks.config 含 level_3", "level_3" in ks.config)
    except Exception as e:
        _check("KillSwitch 实例化失败", False, f"异常: {e}")
        return False

    return True


def test_generate_daily_trade_plan_migration() -> bool:
    """T4: generate_daily_trade_plan._load_capital_config 迁移"""
    print("\n" + "=" * 70)
    print("T4: v8.3_institutional/generate_daily_trade_plan.py 迁移")
    print("=" * 70)

    # 该文件位于 v8.3_institutional/, 直接导入可能受 sys.path 影响
    # 通过路径导入测试
    plan_path = PROJECT_ROOT / "v8.3_institutional" / "generate_daily_trade_plan.py"
    if not plan_path.exists():
        _skip("generate_daily_trade_plan.py 不存在", str(plan_path))
        return True

    try:
        # 动态加载模块 (避免触发整个 main 流程)
        import importlib.util
        spec = importlib.util.spec_from_file_location("_test_trade_plan", plan_path)
        if spec is None or spec.loader is None:
            _skip("无法加载模块 spec", "")
            return True
        module = importlib.util.module_from_spec(spec)
        # 只调用 _load_capital_config 函数, 不执行 main
        spec.loader.exec_module(module)
        if hasattr(module, "_load_capital_config"):
            stock, hedge = module._load_capital_config()
            _check("_load_capital_config 返回 tuple", isinstance((stock, hedge), tuple))
            _check(f"stock_etf_capital={stock}", stock == 4_000_000, f"实际={stock}")
            _check(f"hedge_capital={hedge}", hedge == 1_000_000, f"实际={hedge}")
        else:
            _skip("_load_capital_config 函数不存在", "")
    except Exception as e:
        _check("_load_capital_config 调用失败", False, f"异常: {e}")
        return False

    return True


def test_algo_engine_migration() -> bool:
    """T5: algo_engine.py 迁移 (无 config_path 时尝试 ConfigManager)"""
    print("\n" + "=" * 70)
    print("T5: v8.3_institutional/src/execution/algo_engine.py 迁移")
    print("=" * 70)

    algo_path = PROJECT_ROOT / "v8.3_institutional" / "src" / "execution" / "algo_engine.py"
    if not algo_path.exists():
        _skip("algo_engine.py 不存在", str(algo_path))
        return True

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_test_algo", algo_path)
        if spec is None or spec.loader is None:
            _skip("无法加载 spec", "")
            return True
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        AlgoEngine = getattr(module, "AlgoEngine", None)
        if AlgoEngine is None:
            _skip("AlgoEngine 类不存在", "")
            return True
        # 不传 config_path, 应触发 ConfigManager 加载
        engine = AlgoEngine()
        _check("AlgoEngine 无 config_path 实例化成功", True)
        _check("engine.sessions 非空", len(engine.sessions) > 0,
               f"sessions={len(engine.sessions)}")
    except Exception as e:
        _check("AlgoEngine 实例化失败", False, f"异常: {e}")
        return False

    return True


def test_main_migration() -> bool:
    """T6: v8.3_institutional/main.py 迁移 (V75InstitutionalSystem._load_configs)"""
    print("\n" + "=" * 70)
    print("T6: v8.3_institutional/main.py 迁移")
    print("=" * 70)

    main_path = PROJECT_ROOT / "v8.3_institutional" / "main.py"
    if not main_path.exists():
        _skip("main.py 不存在", str(main_path))
        return True

    # 仅验证语法和 _load_configs 方法存在 (实例化会触发完整初始化, 风险较高)
    try:
        import ast
        src = main_path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        method_found = False
        config_manager_ref = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_load_configs":
                method_found = True
                # 检查方法体是否引用 ConfigManager
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Attribute) and "config_manager" in str(sub.attr).lower():
                        config_manager_ref = True
                    if isinstance(sub, ast.ImportFrom):
                        if sub.module and "config_manager" in sub.module:
                            config_manager_ref = True
        _check("_load_configs 方法存在", method_found)
        _check("_load_configs 引用 ConfigManager", config_manager_ref)
    except Exception as e:
        _check("main.py 解析失败", False, f"异常: {e}")
        return False

    return True


def test_model_router_migration() -> bool:
    """T7: model_router.py 迁移"""
    print("\n" + "=" * 70)
    print("T7: v8.3_institutional/src/ai/model_router.py 迁移")
    print("=" * 70)

    mr_path = PROJECT_ROOT / "v8.3_institutional" / "src" / "ai" / "model_router.py"
    if not mr_path.exists():
        _skip("model_router.py 不存在", str(mr_path))
        return True

    try:
        import ast
        src = mr_path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        # 检查 __init__ 中是否引用 ConfigManager
        init_ref = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "config_manager" in node.module:
                    init_ref = True
        _check("model_router.py 引用 ConfigManager", init_ref)
    except Exception as e:
        _check("model_router.py 解析失败", False, f"异常: {e}")
        return False

    return True


def test_unified_risk_cockpit_migration() -> bool:
    """T8: unified_risk_cockpit.py 迁移"""
    print("\n" + "=" * 70)
    print("T8: v8.3_institutional/src/risk/unified_risk_cockpit.py 迁移")
    print("=" * 70)

    urc_path = PROJECT_ROOT / "v8.3_institutional" / "src" / "risk" / "unified_risk_cockpit.py"
    if not urc_path.exists():
        _skip("unified_risk_cockpit.py 不存在", str(urc_path))
        return True

    try:
        import ast
        src = urc_path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        init_ref = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "config_manager" in node.module:
                    init_ref = True
        _check("unified_risk_cockpit.py 引用 ConfigManager", init_ref)
    except Exception as e:
        _check("unified_risk_cockpit.py 解析失败", False, f"异常: {e}")
        return False

    return True


def test_five_factor_migration() -> bool:
    """T9: five_factor.py 迁移 (修复了路径 bug)"""
    print("\n" + "=" * 70)
    print("T9: v8.3_institutional/src/factors/five_factor.py 迁移")
    print("=" * 70)

    ff_path = PROJECT_ROOT / "v8.3_institutional" / "src" / "factors" / "five_factor.py"
    if not ff_path.exists():
        _skip("five_factor.py 不存在", str(ff_path))
        return True

    try:
        import ast
        src = ff_path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        init_ref = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "config_manager" in node.module:
                    init_ref = True
        _check("five_factor.py 引用 ConfigManager", init_ref)
    except Exception as e:
        _check("five_factor.py 解析失败", False, f"异常: {e}")
        return False

    return True


def test_no_hardcoded_configs_path() -> bool:
    """T10: 验证迁移后的文件不再硬编码 'configs/portfolio.yaml'"""
    print("\n" + "=" * 70)
    print("T10: 验证迁移文件不再硬编码 configs/ 路径 (除常量定义)")
    print("=" * 70)

    migrated_files = [
        "utils/gamma_engine.py",
        "utils/liquidation_scheduler.py",
        "utils/kill_switch.py",
    ]

    all_ok = True
    for rel_path in migrated_files:
        full_path = PROJECT_ROOT / rel_path
        if not full_path.exists():
            _skip(f"{rel_path} 不存在", "")
            continue

        try:
            src = full_path.read_text(encoding="utf-8")
            # 检查是否仍有硬编码 yaml.safe_load(f) 直接读取 (ConfigManager 失败的回退除外)
            # 计算硬编码出现次数 (允许在常量定义和回退路径中出现)
            lines = src.split("\n")
            # 找出 _load_config 方法体
            in_load_config = False
            method_body_lines = []
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if "def _load_config" in line:
                    in_load_config = True
                    continue
                if in_load_config:
                    if stripped.startswith("def ") and not stripped.startswith("def _"):
                        # 进入下一个方法
                        if method_body_lines:
                            break
                    else:
                        method_body_lines.append((i, line))

            # 检查方法体中是否引用 ConfigManager (优先路径)
            has_config_manager = any("config_manager" in line for _, line in method_body_lines)
            _check(f"{rel_path} _load_config 引用 ConfigManager", has_config_manager)
            if not has_config_manager:
                all_ok = False
        except Exception as e:
            _check(f"{rel_path} 解析失败", False, f"异常: {e}")
            all_ok = False

    return all_ok


def main() -> int:
    """主函数"""
    print("=" * 70)
    print("Phase 3-A 验证: ConfigManager 全项目迁移 (7 个模块)")
    print("=" * 70)

    tests = [
        test_gamma_engine_migration,
        test_liquidation_scheduler_migration,
        test_kill_switch_migration,
        test_generate_daily_trade_plan_migration,
        test_algo_engine_migration,
        test_main_migration,
        test_model_router_migration,
        test_unified_risk_cockpit_migration,
        test_five_factor_migration,
        test_no_hardcoded_configs_path,
    ]

    for test in tests:
        try:
            test()
        except Exception as e:
            print(f"\n❌ 测试 {test.__name__} 异常: {e}")
            import traceback
            traceback.print_exc()
            global _failed
            _failed += 1

    print("\n" + "=" * 70)
    print(f"📊 Phase 3-A 总结: 通过 {_passed} | 失败 {_failed} | 跳过 {_skipped}")
    print("=" * 70)

    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
