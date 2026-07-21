#!/usr/bin/env python3
"""
v7.5_institutional 系统可运行性全面检查 & 外部依赖清单生成
============================================================
检查内容：
1. 逐文件语法检查 (py_compile)
2. 内部模块导入可行性测试
3. 外部依赖分析 (区分 stdlib / third-party / internal)
4. 生成 requirements.txt
"""
import os, sys, re, ast, subprocess, importlib
from pathlib import Path
from collections import defaultdict, Counter
from typing import Dict, List, Set, Tuple
import py_compile
import traceback

# 配置
BASE_DIR = Path(r"E:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional")
SRC_DIR = BASE_DIR / "src"

# === STDLIB 模块全集 ===
STDLIB_MODULES = set(sys.stdlib_module_names) if hasattr(sys, 'stdlib_module_names') else {
    "abc", "aifc", "argparse", "array", "ast", "asynchat", "asyncio", "asyncore",
    "atexit", "audioop", "base64", "bdb", "binascii", "binhex", "bisect", "builtins",
    "bz2", "calendar", "cgi", "cgitb", "chunk", "cmath", "cmd", "code", "codecs",
    "codeop", "collections", "colorsys", "compileall", "concurrent", "configparser",
    "contextlib", "contextvars", "copy", "copyreg", "cProfile", "crypt", "csv",
    "ctypes", "curses", "dataclasses", "datetime", "dbm", "decimal", "difflib",
    "dis", "distutils", "doctest", "email", "encodings", "enum", "errno", "faulthandler",
    "fcntl", "filecmp", "fileinput", "fnmatch", "formatter", "fractions", "ftplib",
    "functools", "gc", "getopt", "getpass", "gettext", "glob", "grp", "gzip",
    "hashlib", "heapq", "hmac", "html", "http", "idlelib", "imaplib", "imghdr",
    "imp", "importlib", "inspect", "io", "ipaddress", "itertools", "json", "keyword",
    "lib2to3", "linecache", "locale", "logging", "lzma", "mailbox", "mailcap",
    "marshal", "math", "mimetypes", "mmap", "modulefinder", "multiprocessing",
    "netrc", "nis", "nntplib", "numbers", "operator", "optparse", "os", "ossaudiodev",
    "parser", "pathlib", "pdb", "pickle", "pickletools", "pipes", "pkgutil",
    "platform", "plistlib", "poplib", "posix", "posixpath", "pprint", "profile",
    "pstats", "pty", "pwd", "py_compile", "pyclbr", "pydoc", "queue", "quopri",
    "random", "re", "readline", "reprlib", "resource", "rlcompleter", "runpy",
    "sched", "secrets", "select", "selectors", "shelve", "shlex", "shutil", "signal",
    "site", "smtpd", "smtplib", "sndhdr", "socket", "socketserver", "sqlite3",
    "ssl", "stat", "statistics", "string", "stringprep", "struct", "subprocess",
    "sunau", "symtable", "sys", "sysconfig", "syslog", "tabnanny", "tarfile",
    "telnetlib", "tempfile", "termios", "test", "textwrap", "threading", "time",
    "timeit", "tkinter", "token", "tokenize", "trace", "traceback", "tracemalloc",
    "tty", "turtle", "turtledemo", "types", "typing", "unicodedata", "unittest",
    "urllib", "uu", "uuid", "venv", "warnings", "wave", "weakref", "webbrowser",
    "winreg", "winsound", "wsgiref", "xdrlib", "xml", "xmlrpc", "zipapp",
    "zipfile", "zipimport", "zlib", "__future__", "_thread", "_dummy_thread",
}

# 已知的内部项目引用（跨目录，非 pip 包）
KNOWN_INTERNAL = {
    "engine", "engine.data", "engine.rebalance",
    "quant_modules", "quant_modules.data_layer", "quant_modules.wind_terminal_connector",
    "utils", "utils.kondratiev_cycle", "utils.five_year_plan", "utils.social_security_etf",
    "utils.hedge_engine", "utils.hedge_rebalance_integrator", "utils.hedge_rebalance_backtest",
    "utils.signal_fusion", "utils.ai_coordinator",
    "utils.logging_manager", "utils.event_tracker", "utils.data_source_manager",
    "utils.ml_predictor", "utils.ml_enhanced_trainer",
    "config", "config.settings", "config.portfolio", "config.positions", "config.watchlist",
    "coal_news_daily_report", "daily_report", "daily_runner",
    "backtest_engine", "fast_backtest", "backtest_3year", "backtest_5year",
    "rebalancing_config_v6", "build_plan_100w",
    "ifind_client", "sina_api_helper", "lseg_mcp_connector",
    "ui", "ui.app", "ui.components", "ui.pages",
    "11_量化策略", "15_每日工作流", "10_第三方项目",
}


def find_all_py_files() -> List[Path]:
    """查找所有Python文件"""
    files = []
    for pattern in ["src/**/*.py", "*.py", "scripts/**/*.py", "utils/**/*.py", "data/**/*.py", "tests/**/*.py"]:
        for f in BASE_DIR.glob(pattern):
            if f.is_file():
                files.append(f)
    # 去重
    return sorted(set(files), key=lambda x: str(x))


def extract_imports_from_file(filepath: Path) -> Dict[str, Set[str]]:
    """使用AST提取文件中的import语句"""
    imports = {"stdlib": set(), "third_party": set(), "internal": set(), "relative": set()}
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            tree = ast.parse(f.read(), filename=str(filepath))
    except SyntaxError as e:
        return {"_syntax_error": {str(e)}}
    except Exception as e:
        return {"_parse_error": {str(e)}}

    for node in ast.walk(tree):
        # import X, import X.Y
        if isinstance(node, ast.Import):
            for alias in node.names:
                classify_import(alias.name, imports)
        # from X import Y, from X.Y import Z
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            classify_import(node.module, imports)

    return imports


def classify_import(module_name: str, imports: Dict[str, Set[str]]):
    """分类一个import"""
    top_level = module_name.split(".")[0]

    if top_level in STDLIB_MODULES:
        imports["stdlib"].add(top_level)
    elif top_level in KNOWN_INTERNAL or module_name.startswith("src."):
        imports["internal"].add(top_level)
    elif module_name.startswith("."):
        imports["relative"].add(module_name)
    else:
        imports["third_party"].add(top_level)


def check_syntax(filepath: Path) -> Tuple[bool, str]:
    """编译检查语法"""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            source = f.read() + "\n"
        py_compile.compile(filepath, doraise=True)
        return True, ""
    except py_compile.PyCompileError as e:
        return False, str(e)
    except Exception as e:
        return False, str(e)


def check_importable(package_name: str) -> bool:
    """检查第三方包是否可导入"""
    # 包名和导入名可能不同
    # 常见映射
    name_map = {
        "sklearn": "sklearn",
        "xgboost": "xgboost",
        "lightgbm": "lightgbm",
        "numpy": "numpy",
        "pandas": "pandas",
        "scipy": "scipy",
        "yaml": "yaml",
        "PyYAML": "yaml",
        "requests": "requests",
        "pyautogui": "pyautogui",
        "pywinauto": "pywinauto",
        "optuna": "optuna",
        "joblib": "joblib",
        "mlflow": "mlflow",
        "pyqlib": "qlib",
        "qlib": "qlib",
    }
    import_name = name_map.get(package_name, package_name)
    try:
        importlib.import_module(import_name)
        return True
    except ImportError:
        return False


def check_src_module_imports() -> Dict[str, Tuple[bool, str]]:
    """测试 src 下各子包的导入"""
    results = {}
    sys.path.insert(0, str(BASE_DIR))

    subpackages = [
        "src.backtest", "src.alpha", "src.hedging", "src.risk",
        "src.signals", "src.macro", "src.validation", "src.nlp",
        "src.ai", "src.ml", "src.factors", "src.derivatives",
        "src.execution", "src.bridges", "src.config",
        "src.portfolio", "src.pnl",
    ]

    for pkg in subpackages:
        try:
            # 先清理已加载的模块
            for mod_name in list(sys.modules.keys()):
                if mod_name.startswith(pkg) or mod_name.startswith("src."):
                    del sys.modules[mod_name]

            mod = importlib.import_module(pkg)
            results[pkg] = (True, f"OK (dir={dir(mod)})")
        except Exception as e:
            results[pkg] = (False, f"{type(e).__name__}: {str(e)[:200]}")

    return results


def main():
    print("=" * 70)
    print("  v7.5_institutional 系统可运行性检查 & 依赖清单生成")
    print("=" * 70)

    # 1. 查找所有Python文件
    all_files = find_all_py_files()
    print(f"\n[1/5] 文件扫描: 找到 {len(all_files)} 个Python文件")

    # 2. 语法检查
    print(f"\n[2/5] 逐文件语法检查 (py_compile)...")
    syntax_errors = []
    ok_count = 0
    for f in all_files:
        ok, err = check_syntax(f)
        if ok:
            ok_count += 1
        else:
            rel = str(f).replace(str(BASE_DIR) + "\\", "")
            syntax_errors.append((rel, err))
            print(f"    FAIL: {rel}")
            print(f"          {err[:150]}")

    print(f"  结果: {ok_count}/{len(all_files)} 通过, {len(syntax_errors)} 个语法错误")

    # 3. 导入分析
    print(f"\n[3/5] 导入分析 (AST 解析)...")
    all_third_party = Counter()
    all_stdlib = Counter()
    file_imports: Dict[str, Dict] = {}

    for f in all_files:
        imports = extract_imports_from_file(f)
        if "_syntax_error" in imports:
            continue  # 语法错误文件跳过
        rel = str(f).replace(str(BASE_DIR) + "\\", "")
        file_imports[rel] = imports
        for pkg in imports.get("third_party", set()):
            all_third_party[pkg] += 1
        for pkg in imports.get("stdlib", set()):
            all_stdlib[pkg] += 1

    print(f"  发现 {len(all_third_party)} 个唯一第三方依赖包")

    # 4. 检查当前环境各包安装情况
    print(f"\n[4/5] 第三方依赖安装状态检查...")
    dep_status = {}
    for pkg, count in all_third_party.most_common():
        installed = check_importable(pkg)
        dep_status[pkg] = (installed, count)
        status = "已安装" if installed else "未安装"
        print(f"  {'[OK]' if installed else '[MISS]'} {pkg:20s} - 被 {count:3d} 个文件引用 - {status}")

    # 5. 内部模块导入测试
    print(f"\n[5/5] 内部子包导入测试...")
    sys.path.insert(0, str(BASE_DIR))
    src_results = check_src_module_imports()
    for pkg, (ok, msg) in src_results.items():
        pkg_short = pkg.replace("src.", "")
        print(f"  {'[OK]' if ok else '[FAIL]'} {pkg_short:20s} - {msg[:120]}")

    # === 生成报告 ===
    print("\n" + "=" * 70)
    print("  📊 最终报告")
    print("=" * 70)

    # 语法检查汇总
    print(f"\n--- 语法检查 ---")
    print(f"  总文件数: {len(all_files)}")
    print(f"  通过: {ok_count}")
    print(f"  失败: {len(syntax_errors)}")
    if syntax_errors:
        print(f"\n  语法错误文件列表:")
        for fname, err in syntax_errors:
            print(f"    - {fname}")
            print(f"      {err.split(chr(10))[0][:150]}")

    # 内部模块导入状态
    print(f"\n--- 内部子包导入 (src/) ---")
    ok_pkgs = [p.replace("src.", "") for p, (ok, _) in src_results.items() if ok]
    fail_pkgs = [(p.replace("src.", ""), m) for p, (ok, m) in src_results.items() if not ok]
    print(f"  可导入: {len(ok_pkgs)}/{len(src_results)}")
    for pkg in ok_pkgs:
        print(f"    ✅ {pkg}")
    if fail_pkgs:
        for pkg, err in fail_pkgs:
            print(f"    ❌ {pkg}: {err[:120]}")

    # 外部依赖
    print(f"\n--- 外部依赖 (共 {len(all_third_party)} 个) ---")
    installed_pkgs = []
    missing_pkgs = []
    for pkg, count in all_third_party.most_common():
        installed, _ = dep_status[pkg]
        if installed:
            installed_pkgs.append((pkg, count))
        else:
            missing_pkgs.append((pkg, count))

    print(f"  已安装: {len(installed_pkgs)}")
    for pkg, cnt in installed_pkgs:
        print(f"    ✅ {pkg:20s} (被 {cnt:3d} 个文件引用)")
    print(f"  需安装: {len(missing_pkgs)}")
    for pkg, cnt in missing_pkgs:
        print(f"    ❌ {pkg:20s} (被 {cnt:3d} 个文件引用)")

    # 生成 requirements.txt
    req_path = BASE_DIR / "requirements_v75.txt"
    with open(req_path, "w", encoding="utf-8") as f:
        f.write("# v7.5_institutional 外部依赖清单\n")
        f.write(f"# 自动生成于: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# Python 版本: {sys.version.split()[0]}\n")
        f.write(f"# 平台: {sys.platform}\n\n")

        f.write("# === 核心数据处理 (已安装) ===\n")
        for pkg, cnt in installed_pkgs:
            if pkg in ("numpy", "pandas", "scipy"):
                try:
                    mod = importlib.import_module(pkg)
                    ver = getattr(mod, "__version__", "?")
                except Exception:
                    ver = "?"
                f.write(f"{pkg}  # v{ver}, 被 {cnt} 个文件引用\n")

        f.write("\n# === 机器学习 (已安装) ===\n")
        for pkg, cnt in installed_pkgs:
            if pkg in ("scikit-learn", "sklearn", "xgboost", "lightgbm", "optuna", "joblib"):
                note = " (通过 sklearn 导入)" if pkg == "scikit-learn" else ""
                f.write(f"{pkg}  # 被 {cnt} 个文件引用{note}\n")

        f.write("\n# === MLOps / 配置 / 网络 (已安装) ===\n")
        for pkg, cnt in installed_pkgs:
            if pkg in ("mlflow", "pyqlib", "yaml", "requests"):
                try:
                    import_name = "yaml" if pkg == "PyYAML" else pkg
                    mod = importlib.import_module(import_name)
                    ver = getattr(mod, "__version__", "?")
                except Exception:
                    ver = "?"
                f.write(f"{pkg}  # v{ver}, 被 {cnt} 个文件引用\n")

        f.write("\n# === GUI 自动化 (已安装) ===\n")
        for pkg, cnt in installed_pkgs:
            if pkg in ("pyautogui", "pywinauto"):
                f.write(f"{pkg}  # 被 {cnt} 个文件引用, 同花顺订单操作\n")

        if missing_pkgs:
            f.write("\n# === 需要额外安装 ===\n")
            for pkg, cnt in missing_pkgs:
                f.write(f"{pkg}  # 被 {cnt} 个文件引用, 当前未安装\n")

        f.write("\n# === 版本固定建议 ===\n")
        f.write("# 以下为推荐版本范围，可根据实际环境调整\n")
        f.write("# numpy>=1.21.0,<2.0.0\n")
        f.write("# pandas>=1.3.0,<2.0.0\n")
        f.write("# scipy>=1.7.0\n")
        f.write("# scikit-learn>=1.0.0\n")
        f.write("# xgboost>=1.5.0\n")
        f.write("# lightgbm>=3.3.0\n")
        f.write("# optuna>=3.0.0\n")
        f.write("# joblib>=1.1.0\n")
        f.write("# mlflow>=2.0.0\n")
        f.write("# pyqlib>=0.9.0\n")
        f.write("# PyYAML>=6.0\n")
        f.write("# requests>=2.28.0\n")
        f.write("# pyautogui>=0.9.53\n")
        f.write("# pywinauto>=0.6.8\n")

    print(f"\n✅ requirements_v75.txt 已生成: {req_path}")

    # 汇总评级
    print("\n" + "=" * 70)
    print("  📋 系统可运行性评级")
    print("=" * 70)

    syntax_score = ok_count / max(len(all_files), 1) * 100
    import_score = len(ok_pkgs) / max(len(src_results), 1) * 100
    dep_score = len(installed_pkgs) / max(len(all_third_party), 1) * 100

    print(f"  语法通过率: {syntax_score:.0f}% ({ok_count}/{len(all_files)})")
    print(f"  内部导入通过率: {import_score:.0f}% ({len(ok_pkgs)}/{len(src_results)})")
    print(f"  外部依赖就绪率: {dep_score:.0f}% ({len(installed_pkgs)}/{len(all_third_party)})")

    overall = (syntax_score * 0.3 + import_score * 0.3 + dep_score * 0.4)
    print(f"\n  综合评分: {overall:.0f}/100")
    if overall >= 90:
        print(f"  评级: 🟢 优秀 - 系统可正常运行")
    elif overall >= 70:
        print(f"  评级: 🟡 良好 - 需小幅修复")
    elif overall >= 50:
        print(f"  评级: 🟠 一般 - 需中等程度修复")
    else:
        print(f"  评级: 🔴 较差 - 需大规模修复")

    return syntax_errors, src_results, dep_status


if __name__ == "__main__":
    main()
