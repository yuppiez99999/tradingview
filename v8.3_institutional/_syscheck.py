# -*- coding: utf-8 -*-
"""系统完整性检查脚本"""
import sys
import ast
import pathlib

base = pathlib.Path(r'E:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional')
src = base / 'src'
parent = base.parent

ok_files = []
syntax_errors = []

def check_file(fp):
    try:
        with open(fp, 'r', encoding='utf-8') as f:
            source = f.read()
        ast.parse(source)
        return True, None
    except SyntaxError as e:
        return False, str(e)
    except Exception as e:
        return False, str(e)

# Check all root .py files
print("=== 语法检查: 根目录 ===")
for f in sorted(base.glob('*.py')):
    if f.name.startswith('_') or f.name == '_syscheck.py':
        continue
    ok, err = check_file(f)
    rel = f.name
    if ok:
        ok_files.append(rel)
    else:
        syntax_errors.append((rel, err))
        print(f"  [FAIL] {rel}: {err}")

# Check all src/**/*.py files
print("\n=== 语法检查: src/ 子模块 ===")
for f in sorted(src.rglob('*.py')):
    if '__pycache__' in str(f):
        continue
    ok, err = check_file(f)
    rel = str(f.relative_to(base))
    if ok:
        ok_files.append(rel)
    else:
        syntax_errors.append((rel, err))
        print(f"  [FAIL] {rel}: {err}")

print(f"\n总计: {len(ok_files) + len(syntax_errors)} 个文件")
print(f"语法通过: {len(ok_files)}")
print(f"语法错误: {len(syntax_errors)}")

# Import tests
print("\n=== 关键模块导入测试 ===")
sys.path.insert(0, str(src))
sys.path.insert(0, str(base))

critical_modules = [
    'risk.risk_manager', 'risk.risk_budgeter', 'risk.circuit_breaker',
    'risk.stress_tester', 'hedging.hedge_coordinator',
    'execution.ntp_sync', 'execution.smart_order_router',
    'execution.algo_engine', 'execution.broker_api',
    'alpha.factor_library', 'alpha.signal_generator', 'alpha.signal_fusion',
]

import_ok = []
import_fail = []
for mod in critical_modules:
    try:
        __import__(mod)
        import_ok.append(mod)
        print(f"  [OK] {mod}")
    except Exception as e:
        import_fail.append((mod, str(e)))
        print(f"  [FAIL] {mod}: {e}")

# External deps
print("\n=== 外部依赖检查 ===")
deps = ['yaml', 'numpy', 'pandas', 'scipy', 'sklearn', 'matplotlib',
        'streamlit', 'akshare', 'requests', 'plotly', 'click', 'loguru', 'pydantic']
dep_ok = []
dep_miss = []
for d in deps:
    try:
        __import__(d)
        dep_ok.append(d)
    except Exception:
        dep_miss.append(d)
        print(f"  [MISSING] {d}")

if dep_ok:
    print(f"  已安装: {', '.join(dep_ok)}")

# Dependent script checks
print("\n=== 依赖脚本检查 (被 run_all_modules.py 引用) ===")
dep_scripts = [
    'update_position_prices.py', 'daily_trade_executor.py',
    'generate_daily_report.py', 'stop_loss_monitor.py',
]
for s in dep_scripts:
    fp = parent / s
    if fp.exists():
        print(f"  [OK] {s}")
    else:
        print(f"  [MISSING] {s}")

# Check config files
print("\n=== 配置文件检查 ===")
config_dir = base / 'config'
for cfg in ['settings.yaml', 'portfolio.yaml', 'execution.yaml', 'backtest.yaml', 'risk_budget.yaml']:
    fp = config_dir / cfg
    if fp.exists():
        print(f"  [OK] {cfg}")
    else:
        print(f"  [MISSING] {cfg}")

# Summary
print("\n" + "=" * 50)
print("总结:")
print(f"  语法错误: {len(syntax_errors)} 文件")
print(f"  导入失败: {len(import_fail)} 模块")
print(f"  缺失依赖: {len(dep_miss)} 包")
if not syntax_errors and not import_fail and not dep_miss:
    print("  结论: 所有模块语法通过, 核心导入正常")
else:
    print("  结论: 存在问题需要修复")
