#!/usr/bin/env python3
"""
硬编码绝对路径扫描器 (v8.5+ 质量门禁)
======================================

在 pre-commit / CI 阶段扫描 Python 文件中的硬编码绝对路径,
阻止新引入的 "e:\\各种PY程序..." 类路径进入仓库。

用法:
    python scripts/check_hardcoded_paths.py            # 扫描暂存区文件
    python scripts/check_hardcoded_paths.py --all      # 扫描全部 .py 文件
    python scripts/check_hardcoded_paths.py --path utils/  # 扫描指定目录
    python scripts/check_hardcoded_paths.py --staged   # 仅扫描 git diff --cached (默认)

退出码:
    0 = 通过 (无硬编码路径)
    1 = 失败 (检测到硬编码路径, 详细报告输出到 stderr)
    2 = 扫描器自身异常
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 硬编码绝对路径模式 (匹配 Windows 绝对路径 + 常见项目路径前缀)
_HARDCODED_PATH_PATTERNS: list[tuple[str, str]] = [
    # (正则模式, 描述)
    (r'[re]?\s*["\']\s*[a-zA-Z]:\\(?:各种PY程序|Users|Program)', "Windows 绝对路径"),
    (r'(?:sys\.path\.insert|BASE_DIR|PROJECT_ROOT|DATA_DIR|REPORT_DIR)\s*=\s*[re]?\s*["\'][a-zA-Z]:\\', "硬编码项目路径"),
    (r'os\.path\.join\s*\(\s*[re]?\s*["\'][a-zA-Z]:\\', "os.path.join 使用绝对路径"),
]

# 允许白名单 (这些文件依法保留硬编码路径)
_WHITELIST: set[str] = {
    "scripts/check_hardcoded_paths.py",  # 自引
    "scripts/pre_commit_check.py",
    "githooks/",
    "docs/",
    "config/",
    "research/references/Vibe-Trading/",  # 已 gitignore
    "_archive/",                          # 已归档遗留代码
    "ms_strategy/cloud_train/",           # 云训练脚本, 需本地 qlib_data/reports 路径 (历史债务)
    "ms_strategy/scripts/vol_adjusted_stop_loss.py",  # 环境变量覆盖设计, 默认开发机路径 (QUANT11_ENV_FILE/WIND_MCP_SKILL_DIR/QUANT11_STOPLOSS_CONFIG)
    ".trae/",                             # IDE 内部文件
    "qlib_env/",
    ".env.example",
    "tools/wind_mcp_fetcher.py",          # 保留外部工具路径 (node.exe)
    # 合法外部工具路径 (venv Python/Python安装/浏览器):
    "15_每日工作流/morning_info_runner.py",
    "15_每日工作流/run_daily_morning.py",
    "research/generate_weekly_report.py",
}


def _is_whitelisted(file_path: str, rel_root: Path) -> bool:
    """检查文件是否在白名单中."""
    try:
        rel = str(Path(file_path).resolve().relative_to(rel_root)).replace("\\", "/")
    except ValueError:
        return False
    for w in _WHITELIST:
        if rel.startswith(w) or rel == w:
            return True
    return False


def _get_staged_py_files(project_root: Path) -> list[str]:
    """获取 git 暂存区中的 .py 文件列表."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True, text=True, cwd=str(project_root), timeout=10,
        )
        if result.returncode != 0:
            return []
        return [
            f for f in result.stdout.strip().split("\n")
            if f and f.endswith(".py")
        ]
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        return []


def _collect_py_files(project_root: Path, staged_only: bool, target_path: str | None) -> list[Path]:
    """收集需要扫描的 .py 文件."""
    if target_path:
        target = Path(target_path)
        if not target.is_absolute():
            target = project_root / target
        if target.is_file():
            return [target]
        return sorted(target.rglob("*.py"))

    if staged_only:
        staged = _get_staged_py_files(project_root)
        return [project_root / f for f in staged if (project_root / f).exists()]

    # --all 模式
    return sorted(
        p for p in project_root.rglob("*.py")
        if "__pycache__" not in str(p)
        and "qlib_env" not in str(p)
        and "node_modules" not in str(p)
    )


def _scan_file(file_path: Path, project_root: Path) -> list[dict]:
    """扫描单个文件, 返回违规列表."""
    violations: list[dict] = []

    if _is_whitelisted(str(file_path), project_root):
        return violations

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        return violations

    lines = content.split("\n")
    for lineno, line in enumerate(lines, 1):
        for pattern, desc in _HARDCODED_PATH_PATTERNS:
            if re.search(pattern, line):
                # 排除注释行 (文档中引用路径的例子)
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                    continue
                violations.append({
                    "file": str(file_path.relative_to(project_root)).replace("\\", "/"),
                    "line": lineno,
                    "desc": desc,
                    "content": line.strip()[:120],
                })
                break  # 每行只报告一次

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description="硬编码绝对路径扫描器")
    parser.add_argument("--all", action="store_true", help="扫描全部 .py 文件")
    parser.add_argument("--staged", action="store_true", default=True, help="仅扫描 git 暂存区 (默认)")
    parser.add_argument("--path", type=str, help="扫描指定文件/目录")
    parser.add_argument("--quiet", action="store_true", help="静默模式, 仅输出违规")
    args = parser.parse_args()

    if args.path:
        args.staged = False

    try:
        files = _collect_py_files(PROJECT_ROOT, args.staged and not args.path and not args.all, args.path)
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        print(f"[path-check] 文件收集异常: {e}", file=sys.stderr)
        return 0  # 容错通过

    if not files:
        if not args.quiet:
            print("[path-check] 无待扫描 .py 文件, 跳过")
        return 0

    all_violations: list[dict] = []
    for fp in files:
        all_violations.extend(_scan_file(fp, PROJECT_ROOT))

    if not all_violations:
        if not args.quiet:
            print(f"[path-check] HARDCODED_PATH_CHECK: PASSED (scanned {len(files)} files)")
        return 0

    # 分类输出
    by_file: dict[str, list[dict]] = {}
    for v in all_violations:
        by_file.setdefault(v["file"], []).append(v)

    print(f"\n[path-check] HARDCODED_PATH_CHECK: FAILED - found {len(all_violations)} violations in {len(by_file)} files:\n", file=sys.stderr)
    for fpath, vlist in sorted(by_file.items()):
        print(f"  {fpath}", file=sys.stderr)
        for v in vlist:
            print(f"    L{v['line']:>4}: [{v['desc']}] {v['content']}", file=sys.stderr)
        print(file=sys.stderr)

    print(
        "[path-check] 修复方式: 用 'from utils.path_config import setup_sys_path; setup_sys_path()' "
        "替代 sys.path.insert, 用 get_config_dir()/get_report_dir() 等替代硬编码路径.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
