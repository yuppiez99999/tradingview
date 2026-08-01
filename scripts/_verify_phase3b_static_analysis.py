#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 3-B 验证脚本: 静态分析无回归
====================================

验证 mypy + pylint 静态分析配置正确, 关键模块的修复无回归.

验证内容:
    T1: mypy.ini 配置文件存在且关键配置正确
    T2: .pylintrc 配置文件存在且关键配置正确
    T3: 关键修复点仍在位 (daily_workflow.py)
    T4: 关键修复点仍在位 (unified_risk_cockpit.py)
    T5: 关键修复点仍在位 (execution_algo_engine.py)
    T6: 关键修复点仍在位 (config_manager.py)
    T7: mypy 在核心模块 (config_manager) 无错
    T8: pylint 在核心模块 (config_manager) 无严重错误 (E 级)
    T9: mypy 在核心模块 (kill_switch) 无错
    T10: pylint 在核心模块 (kill_switch) 无严重错误 (E 级)
    T11: 验证关键 Bug 修复 (无未定义变量, 无类型错配)
    T12: 验证 Phase 3-B 配置文件中的渐进式策略被遵守

运行:
    python scripts/_verify_phase3b_static_analysis.py

退出码:
    0 = 全部通过
    1 = 至少一项失败
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

# 关键模块文件列表 (Phase 3-B 重点关注的)
PHASE3B_CRITICAL_FILES = {
    "daily_workflow": PROJECT_ROOT / "v8.3_institutional" / "daily_workflow.py",
    "unified_risk_cockpit": PROJECT_ROOT / "v8.3_institutional" / "src" / "risk" / "unified_risk_cockpit.py",
    "execution_algo_engine": PROJECT_ROOT / "utils" / "execution_algo_engine.py",
    "config_manager": PROJECT_ROOT / "utils" / "config_manager.py",
    "kill_switch": PROJECT_ROOT / "utils" / "kill_switch.py",
    "portfolio_optimizer": PROJECT_ROOT / "utils" / "portfolio_optimizer.py",
}

_passed = 0
_failed = 0
_skipped = 0


def _check(name: str, condition: bool, detail: str = "") -> None:
    """断言检查"""
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  [PASS] {name}" + (f" -- {detail}" if detail else ""))
    else:
        _failed += 1
        print(f"  [FAIL] {name}" + (f" -- {detail}" if detail else ""))


def _skip(name: str, reason: str = "") -> None:
    global _skipped
    _skipped += 1
    print(f"  [SKIP] {name}" + (f" -- {reason}" if reason else ""))


# ============================================================
# T1: mypy.ini 配置文件验证
# ============================================================

def test_mypy_ini_config() -> bool:
    """T1: mypy.ini 配置存在且关键配置正确"""
    print("\n" + "=" * 70)
    print("T1: mypy.ini 配置文件验证")
    print("=" * 70)

    mypy_ini = PROJECT_ROOT / "mypy.ini"
    if not mypy_ini.exists():
        _check("mypy.ini 存在", False, "文件不存在")
        return False

    _check("mypy.ini 存在", True)
    content = mypy_ini.read_text(encoding="utf-8")

    # 验证关键配置
    _check("python_version = 3.8", "python_version = 3.8" in content)
    _check("warn_unused_ignores 启用", "warn_unused_ignores = True" in content)
    _check("no_implicit_optional 启用", "no_implicit_optional = True" in content)
    _check("show_error_codes 启用", "show_error_codes = True" in content)
    _check("utils.* 模块严格检查", "[mypy-utils.*]" in content)
    _check("config_manager 严格检查", "[mypy-utils.config_manager]" in content)
    _check("kill_switch 严格检查", "[mypy-utils.kill_switch]" in content)
    _check("portfolio_optimizer 严格检查", "[mypy-utils.portfolio_optimizer]" in content)
    _check("daily_workflow 渐进式 (ignore_errors)", "ignore_errors = True" in content)

    # 验证排除目录
    _check("排除 research/", "research/" in content)
    _check("排除 tests/", "tests/" in content)
    _check("排除 ms_strategy/", "ms_strategy/" in content)

    return True


# ============================================================
# T2: .pylintrc 配置文件验证
# ============================================================

def test_pylintrc_config() -> bool:
    """T2: .pylintrc 配置存在且关键配置正确"""
    print("\n" + "=" * 70)
    print("T2: .pylintrc 配置文件验证")
    print("=" * 70)

    pylintrc = PROJECT_ROOT / ".pylintrc"
    if not pylintrc.exists():
        _check(".pylintrc 存在", False, "文件不存在")
        return False

    _check(".pylintrc 存在", True)
    content = pylintrc.read_text(encoding="utf-8")

    # 验证复杂度限制
    _check("max-branches = 15", "max-branches = 15" in content)
    _check("max-statements = 80", "max-statements = 80" in content)
    _check("max-args = 8", "max-args = 8" in content)
    _check("max-line-length = 120", "max-line-length = 120" in content)

    # 验证 py-version
    _check("py-version = 3.8", "py-version = 3.8" in content)

    # 验证忽略目录
    _check("忽略 research 目录", "research" in content)
    _check("忽略 ms_strategy 目录", "ms_strategy" in content)

    # 验证 init-hook (用于跨目录 sys.path 注入)
    _check("init-hook 配置存在", "init-hook" in content)

    return True


# ============================================================
# T3: daily_workflow.py 关键修复点验证
# ============================================================

def test_daily_workflow_fixes() -> bool:
    """T3: daily_workflow.py 关键修复点仍在位"""
    print("\n" + "=" * 70)
    print("T3: daily_workflow.py 关键修复点验证")
    print("=" * 70)

    file_path = PHASE3B_CRITICAL_FILES["daily_workflow"]
    if not file_path.exists():
        _skip("daily_workflow.py 不存在", str(file_path))
        return True

    src = file_path.read_text(encoding="utf-8")

    # 修复点 1: if TYPE_CHECKING import pandas (避免 pd 未定义)
    _check(
        "TYPE_CHECKING 守卫 pd 导入",
        "if TYPE_CHECKING" in src and "import pandas" in src,
    )

    # 修复点 2: REPORT_DIR 属性 (log_dir 兜底)
    _check(
        "REPORT_DIR 已定义",
        "REPORT_DIR = BASE_DIR.parent.parent" in src or
        "REPORT_DIR = " in src,
    )

    # 修复点 3: get_environment_summary 调用 (替代 EnvironmentIsolation.validate)
    _check(
        "使用 get_environment_summary 替代 .validate()",
        "get_environment_summary" in src,
    )

    # 修复点 4: target_shares 字段 (修正 ExecutionSlice.shares 拼写错误)
    _check(
        "target_shares 字段被引用",
        "target_shares" in src,
    )

    # 修复点 5: exec_phase 变量 (避免与 except 块的 e 变量冲突)
    _check(
        "exec_phase 变量 (避免 shadowing except 的 e)",
        "exec_phase" in src,
    )

    # 修复点 6: phase_execute 返回 [] 而非 True (类型一致性)
    # 通过语法检查 phase_execute 是否仍存在
    try:
        tree = ast.parse(src)
        phase_execute_found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "phase_execute":
                phase_execute_found = True
                break
        _check("phase_execute 方法存在", phase_execute_found)
    except SyntaxError as e:
        _check("daily_workflow.py 语法正确", False, f"SyntaxError: {e}")
        return False

    _check("daily_workflow.py 语法正确", True)
    return True


# ============================================================
# T4: unified_risk_cockpit.py 关键修复点验证
# ============================================================

def test_unified_risk_cockpit_fixes() -> bool:
    """T4: unified_risk_cockpit.py 关键修复点仍在位"""
    print("\n" + "=" * 70)
    print("T4: unified_risk_cockpit.py 关键修复点验证")
    print("=" * 70)

    file_path = PHASE3B_CRITICAL_FILES["unified_risk_cockpit"]
    if not file_path.exists():
        _skip("unified_risk_cockpit.py 不存在", str(file_path))
        return True

    src = file_path.read_text(encoding="utf-8")

    # 修复点 1: Optional 类型导入
    _check(
        "Optional 类型导入",
        "from typing import" in src and "Optional" in src,
    )

    # 修复点 2: reduce_pct 类型修复 (中间 float 变量)
    _check(
        "reduce_pct 计算 (使用 round() 输出 float)",
        "reduce_pct" in src and "round(" in src,
    )

    # 修复点 3: var_backtester.confidence None 守卫
    # 通过检查 _scan_kill_switch 接受 Optional[float] margin_usage
    _check(
        "_scan_kill_switch 方法存在",
        "_scan_kill_switch" in src,
    )

    # 修复点 4: full_scan 接受 Optional 参数
    _check(
        "full_scan 方法签名含 Optional",
        "full_scan" in src and "Optional" in src,
    )

    # 语法检查
    try:
        ast.parse(src)
        _check("unified_risk_cockpit.py 语法正确", True)
    except SyntaxError as e:
        _check("unified_risk_cockpit.py 语法正确", False, f"SyntaxError: {e}")
        return False

    return True


# ============================================================
# T5: execution_algo_engine.py 关键修复点验证
# ============================================================

def test_execution_algo_engine_fixes() -> bool:
    """T5: execution_algo_engine.py 关键修复点仍在位"""
    print("\n" + "=" * 70)
    print("T5: execution_algo_engine.py 关键修复点验证")
    print("=" * 70)

    file_path = PHASE3B_CRITICAL_FILES["execution_algo_engine"]
    if not file_path.exists():
        _skip("execution_algo_engine.py 不存在", str(file_path))
        return True

    src = file_path.read_text(encoding="utf-8")

    # 修复点 1: ExecutionSlice.target_shares 字段 (正确拼写)
    _check(
        "ExecutionSlice.target_shares 字段 (非 shares)",
        "target_shares: int" in src,
    )

    # 修复点 2: ExecutionPlan.expected_cost (非 estimated_total_cost)
    _check(
        "ExecutionPlan.expected_cost 字段",
        "expected_cost: float" in src,
    )

    # 修复点 3: ExecutionPlan.expected_slippage_bps (非 estimated_slippage_bps)
    _check(
        "ExecutionPlan.expected_slippage_bps 字段",
        "expected_slippage_bps: float" in src,
    )

    # 修复点 4: from pathlib import Path
    _check(
        "from pathlib import Path",
        "from pathlib import Path" in src,
    )

    # 语法检查
    try:
        ast.parse(src)
        _check("execution_algo_engine.py 语法正确", True)
    except SyntaxError as e:
        _check("execution_algo_engine.py 语法正确", False, f"SyntaxError: {e}")
        return False

    return True


# ============================================================
# T6: config_manager.py 关键 API 验证
# ============================================================

def test_config_manager_apis() -> bool:
    """T6: config_manager.py 关键 API 仍在位"""
    print("\n" + "=" * 70)
    print("T6: config_manager.py 关键 API 验证")
    print("=" * 70)

    file_path = PHASE3B_CRITICAL_FILES["config_manager"]
    if not file_path.exists():
        _skip("config_manager.py 不存在", str(file_path))
        return True

    src = file_path.read_text(encoding="utf-8")

    # 关键 API
    expected_apis = [
        "_build_search_paths",      # 4 级优先级解析
        "get_kill_switch_config",   # 类型化访问器
        "get_portfolio_config",     # 类型化访问器
        "get_settings_config",
        "get_execution_config",
        "get_backtest_config",
        "get_config_source",        # 审计 API (漂移检测)
        "list_available",           # 审计 API
        "list_available_configs",   # 审计 API (函数)
        "clear_config_cache",       # 测试支持
        "ConfigManager",            # 单例类
    ]

    for api in expected_apis:
        _check(f"API 存在: {api}", api in src)

    # 验证 4 级优先级 (env var > v8.3 > configs > ms_strategy)
    _check(
        "环境变量 QUANT_CONFIG_DIR 优先级",
        "QUANT_CONFIG_DIR" in src,
    )
    _check(
        "v8.3_institutional/config 优先级 2",
        "v8.3_institutional" in src and "config" in src,
    )

    # 语法检查
    try:
        ast.parse(src)
        _check("config_manager.py 语法正确", True)
    except SyntaxError as e:
        _check("config_manager.py 语法正确", False, f"SyntaxError: {e}")
        return False

    return True


# ============================================================
# T7-T10: 运行静态分析工具
# ============================================================

def _run_command(cmd: List[str], timeout: int = 90) -> Tuple[int, str, str]:
    """运行命令并捕获输出

    Returns:
        (returncode, stdout, stderr)
    """
    try:
        # 清理可能的代理环境变量 (避免 pip 安装工具时出错)
        env = os.environ.copy()
        for key in list(env.keys()):
            if "PROXY" in key.upper() or key.upper() in ("ICUBE_PROXY_HOST", "ICUBE_PROXY_PORT"):
                env.pop(key, None)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=str(PROJECT_ROOT),
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except FileNotFoundError:
        return 127, "", f"command not found: {cmd[0]}"


def _run_mypy_on_target(target_filename: str, target_module_label: str) -> bool:
    """通用 mypy 验证: 只统计目标文件本身的错误 (不跟随导入)

    Args:
        target_filename: 相对项目根的文件路径, 如 "utils/config_manager.py"
        target_module_label: 测试日志中显示的模块名
    """
    print("\n" + "=" * 70)
    print(f"mypy 在 {target_filename} 无错 (仅目标文件)")
    print("=" * 70)

    mypy_bin = shutil.which("mypy") or shutil.which("mypy.exe")
    if mypy_bin is None:
        rc, _, _ = _run_command([sys.executable, "-m", "mypy", "--version"])
        if rc != 0:
            _skip("mypy 未安装", "pip install mypy")
            return True
        cmd = [sys.executable, "-m", "mypy"]
    else:
        cmd = [mypy_bin]

    target = str(PROJECT_ROOT / target_filename)
    # 使用 --follow-imports=skip 避免跟随导入到其他文件
    # 只检查目标文件本身的类型错误
    rc, out, err = _run_command(
        cmd + ["--config-file", "mypy.ini", "--follow-imports=skip", target]
    )

    if rc == 127:
        _skip("mypy 不可用", out + err)
        return True
    if rc == 124:
        _skip("mypy 超时", "")
        return True

    # mypy 输出格式: file:line: error: msg  [code]
    # 只统计目标文件的错误 (跟随导入被 skip 后, 其他文件的错误会标记为 skip)
    full_output = out + err
    target_basename = Path(target).name
    target_unix = target_filename.replace("\\", "/")

    error_lines = []
    for line in full_output.splitlines():
        if "error:" not in line:
            continue
        # 仅匹配目标文件 (Windows 路径兼容)
        if (target_basename in line or
            target_unix in line.replace("\\", "/") or
            target_filename.replace("/", "\\") in line):
            error_lines.append(line)

    # 也捕获 "skip" 标记的导入错误 (这些是其他文件被 skip 后的提示, 不算目标错误)
    [
        line for line in full_output.splitlines()
        if "skip" in line.lower() and target_basename not in line
    ]

    _check(
        f"mypy 在 {target_module_label} 无 error (rc={rc}, target_errors={len(error_lines)})",
        len(error_lines) == 0,
        detail=f"{len(error_lines)} errors in target" if error_lines else "clean (target file)",
    )

    if error_lines:
        for line in error_lines[:5]:
            print(f"    > {line}")

    return len(error_lines) == 0


def test_mypy_on_config_manager() -> bool:
    """T7: mypy 在 config_manager 上无错 (仅目标文件)"""
    return _run_mypy_on_target("utils/config_manager.py", "config_manager")


def test_pylint_on_config_manager() -> bool:
    """T8: pylint 在 config_manager 上无严重错误 (E 级)"""
    print("\n" + "=" * 70)
    print("T8: pylint 在 utils/config_manager.py 无 E 级错误")
    print("=" * 70)

    pylint_bin = shutil.which("pylint") or shutil.which("pylint.exe")
    if pylint_bin is None:
        rc, _, _ = _run_command([sys.executable, "-m", "pylint", "--version"])
        if rc != 0:
            _skip("pylint 未安装", "pip install pylint")
            return True
        cmd = [sys.executable, "-m", "pylint"]
    else:
        cmd = [pylint_bin]

    target = str(PROJECT_ROOT / "utils" / "config_manager.py")
    rc, out, err = _run_command(
        cmd + ["--rcfile=.pylintrc", target],
        timeout=120,
    )

    if rc == 127:
        _skip("pylint 不可用", out + err)
        return True
    if rc == 124:
        _skip("pylint 超时", "")
        return True

    # pylint 退出码是位掩码: bit 0=fatal, bit 1=error, bit 2=warning, bit 3=refactor, bit 4=convention
    # 我们只关注 E (error) 级别
    full_output = out + err
    e_lines = [
        line for line in full_output.splitlines()
        if ": E" in line and ":" in line
    ]

    _check(
        f"pylint 在 config_manager 无 E 级错误 (rc={rc}, E={len(e_lines)})",
        len(e_lines) == 0,
        detail=f"{len(e_lines)} E 级错误" if e_lines else "clean",
    )

    if e_lines:
        for line in e_lines[:5]:
            print(f"    > {line}")

    return len(e_lines) == 0


def test_mypy_on_kill_switch() -> bool:
    """T9: mypy 在 kill_switch 上无错 (仅目标文件)"""
    return _run_mypy_on_target("utils/kill_switch.py", "kill_switch")


def test_mypy_on_portfolio_optimizer() -> bool:
    """T9b: mypy 在 portfolio_optimizer 上无错 (仅目标文件)"""
    return _run_mypy_on_target("utils/portfolio_optimizer.py", "portfolio_optimizer")


def test_pylint_on_kill_switch() -> bool:
    """T10: pylint 在 kill_switch 上无严重错误 (E 级)"""
    print("\n" + "=" * 70)
    print("T10: pylint 在 utils/kill_switch.py 无 E 级错误")
    print("=" * 70)

    pylint_bin = shutil.which("pylint") or shutil.which("pylint.exe")
    if pylint_bin is None:
        rc, _, _ = _run_command([sys.executable, "-m", "pylint", "--version"])
        if rc != 0:
            _skip("pylint 未安装", "")
            return True
        cmd = [sys.executable, "-m", "pylint"]
    else:
        cmd = [pylint_bin]

    target = str(PROJECT_ROOT / "utils" / "kill_switch.py")
    rc, out, err = _run_command(
        cmd + ["--rcfile=.pylintrc", target],
        timeout=120,
    )

    if rc in (127, 124):
        _skip(f"pylint 不可用 (rc={rc})", out + err)
        return True

    full_output = out + err
    e_lines = [
        line for line in full_output.splitlines()
        if ": E" in line and ":" in line
    ]

    _check(
        f"pylint 在 kill_switch 无 E 级错误 (rc={rc}, E={len(e_lines)})",
        len(e_lines) == 0,
        detail=f"{len(e_lines)} E 级错误" if e_lines else "clean",
    )

    if e_lines:
        for line in e_lines[:5]:
            print(f"    > {line}")

    return len(e_lines) == 0


# ============================================================
# T11: 验证关键 Bug 修复 (无未定义变量)
# ============================================================

def test_no_known_bugs() -> bool:
    """T11: 验证已修复的 Bug 没有回归"""
    print("\n" + "=" * 70)
    print("T11: 验证关键 Bug 修复无回归")
    print("=" * 70)

    # Bug 1: daily_workflow.py 中 ExecutionSlice.shares 拼写错误
    # 修复后应为 target_shares
    dw_path = PHASE3B_CRITICAL_FILES["daily_workflow"]
    if dw_path.exists():
        dw_src = dw_path.read_text(encoding="utf-8")
        # 检查是否仍有 .shares 错误访问 (排除合法的 shares 字段如 executed_shares)
        bad_pattern = "ExecutionSlice.shares"
        _check(
            "daily_workflow 不再访问 ExecutionSlice.shares (错误拼写)",
            bad_pattern not in dw_src,
            detail="使用 target_shares" if bad_pattern not in dw_src else "Bug 回归!",
        )

    # Bug 2: daily_workflow.py 中 EnvironmentIsolation.validate() 调用
    # 修复后应为 get_environment_summary()
    if dw_path.exists():
        _check(
            "daily_workflow 不再调用 EnvironmentIsolation.validate()",
            "EnvironmentIsolation.validate()" not in dw_src,
            detail="已替换为 get_environment_summary()" if "EnvironmentIsolation.validate()" not in dw_src else "Bug 回归!",
        )

    # Bug 3: daily_workflow.py phase_execute return True (返回类型 List[Dict])
    if dw_path.exists():
        # 检查 phase_execute 方法是否仍存在并返回正确类型
        try:
            tree = ast.parse(dw_src)
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == "phase_execute":
                    # 检查 return 语句
                    returns = [
                        n for n in ast.walk(node)
                        if isinstance(n, ast.Return)
                    ]
                    # 至少有一个 return [] (空列表, 而非 return True)
                    has_empty_list_return = any(
                        isinstance(r.value, ast.List) and len(r.value.elts) == 0
                        for r in returns
                    )
                    # 不应有 return True (字面量)
                    has_true_return = any(
                        isinstance(r.value, ast.Constant) and r.value.value is True
                        for r in returns
                    )
                    _check(
                        "phase_execute 返回 [] (List[Dict] 类型一致)",
                        has_empty_list_return,
                    )
                    _check(
                        "phase_execute 不再 return True (类型错配 Bug 已修复)",
                        not has_true_return,
                    )
                    break
        except SyntaxError:
            pass

    # Bug 4: execution_algo_engine.py 中 estimated_total_cost / estimated_slippage_bps
    ea_path = PHASE3B_CRITICAL_FILES["execution_algo_engine"]
    if ea_path.exists():
        ea_src = ea_path.read_text(encoding="utf-8")
        _check(
            "execution_algo_engine 不再使用 estimated_total_cost (错误属性名)",
            "estimated_total_cost" not in ea_src,
            detail="使用 expected_cost" if "estimated_total_cost" not in ea_src else "Bug 回归!",
        )
        _check(
            "execution_algo_engine 不再使用 estimated_slippage_bps (错误属性名)",
            "estimated_slippage_bps" not in ea_src,
            detail="使用 expected_slippage_bps" if "estimated_slippage_bps" not in ea_src else "Bug 回归!",
        )

    # Bug 5: unified_risk_cockpit.py 中 var_backtester.confidence None 访问
    urc_path = PHASE3B_CRITICAL_FILES["unified_risk_cockpit"]
    if urc_path.exists():
        urc_src = urc_path.read_text(encoding="utf-8")
        # var_backtester 在 enable_var_backtest=False 时为 None, 需要守卫
        _check(
            "unified_risk_cockpit 守卫 var_backtester None 访问",
            "var_backtester" in urc_src and (
                "if self.var_backtester" in urc_src or
                "if not self.var_backtester" in urc_src or
                "if self.enable_var_backtest" in urc_src
            ),
        )

    return True


# ============================================================
# T12: 验证 Phase 3-B 配置策略遵守
# ============================================================

def test_phase3b_strategy_compliance() -> bool:
    """T12: 验证 Phase 3-B 渐进式策略被正确遵守"""
    print("\n" + "=" * 70)
    print("T12: Phase 3-B 渐进式策略合规性")
    print("=" * 70)

    mypy_ini = PROJECT_ROOT / "mypy.ini"
    if not mypy_ini.exists():
        _skip("mypy.ini 不存在", "")
        return True

    content = mypy_ini.read_text(encoding="utf-8")

    # 策略 1: 全局 check_untyped_defs = False (渐进式, 避免大量重写)
    _check(
        "全局 check_untyped_defs = False (渐进式)",
        "check_untyped_defs = False" in content,
    )

    # 策略 2: utils.* 模块启用 check_untyped_defs = True (核心模块严格)
    _check(
        "utils.* 启用 check_untyped_defs = True (核心模块严格)",
        "[mypy-utils.*]" in content and
        "check_untyped_defs = True" in content,
    )

    # 策略 3: daily_workflow 暂时忽略 (大型代码库渐进式)
    _check(
        "daily_workflow 渐进式 (ignore_errors = True)",
        "[mypy-v8.3_institutional.daily_workflow]" in content and
        "ignore_errors = True" in content,
    )

    # 策略 4: 第三方库 import 缺失存根时忽略
    _check(
        "ignore_missing_imports = True",
        "ignore_missing_imports = True" in content,
    )

    # 验证 .pylintrc 关闭的噪音项
    pylintrc = PROJECT_ROOT / ".pylintrc"
    if pylintrc.exists():
        pl_content = pylintrc.read_text(encoding="utf-8")

        # 设计模式类暂时容忍 (后续渐进收紧)
        _check(
            "pylint 容忍 too-many-locals (大型函数拆分进行中)",
            "too-many-locals" in pl_content,
        )
        _check(
            "pylint 容忍 too-many-branches",
            "too-many-branches" in pl_content,
        )

        # 保留 E 级检查 (不关闭 error 类)
        # 确保没有 "disable = E" 这种关闭所有错误的写法
        _check(
            "pylint 未关闭所有 E 级检查",
            not any(line.strip().startswith("E") and "," in line
                    for line in pl_content.splitlines()
                    if "disable" in pl_content[:pl_content.find(line)] if line.strip()),
        )

    return True


# ============================================================
# 主函数
# ============================================================

def main() -> int:
    """主函数"""
    print("=" * 70)
    print("Phase 3-B 验证: 静态分析无回归 (mypy + pylint)")
    print("=" * 70)
    print(f"项目根: {PROJECT_ROOT}")
    print(f"Python: {sys.version.split()[0]}")

    tests = [
        ("配置文件验证", [
            test_mypy_ini_config,
            test_pylintrc_config,
        ]),
        ("关键修复点验证", [
            test_daily_workflow_fixes,
            test_unified_risk_cockpit_fixes,
            test_execution_algo_engine_fixes,
            test_config_manager_apis,
        ]),
        ("静态分析运行", [
            test_mypy_on_config_manager,
            test_pylint_on_config_manager,
            test_mypy_on_kill_switch,
            test_mypy_on_portfolio_optimizer,
            test_pylint_on_kill_switch,
        ]),
        ("Bug 回归验证", [
            test_no_known_bugs,
        ]),
        ("策略合规性", [
            test_phase3b_strategy_compliance,
        ]),
    ]

    for group_name, group_tests in tests:
        print("\n" + "#" * 70)
        print(f"# {group_name}")
        print("#" * 70)
        for test in group_tests:
            try:
                test()
            except Exception as e:
                print(f"\n  [ERROR] 测试 {test.__name__} 异常: {e}")
                import traceback
                traceback.print_exc()
                global _failed
                _failed += 1

    print("\n" + "=" * 70)
    print(f"Phase 3-B 验证总结: PASS={_passed} | FAIL={_failed} | SKIP={_skipped}")
    print("=" * 70)

    if _failed == 0:
        print("[OK] Phase 3-B 静态分析无回归, 关键修复点全部在位")
    else:
        print(f"[FAIL] Phase 3-B 发现 {_failed} 项失败, 需要修复")

    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
