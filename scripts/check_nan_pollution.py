#!/usr/bin/env python3
"""
NaN 污染守卫检查 (F-4/F-5/F-6 门禁).

扫描代码中可能导致 NaN 传播的高风险模式:
  - np.corrcoef 调用后未接 nan_to_num 防护 (F-5)
  - IC / 相关性计算前未检查常数序列 (std < 阈值) (F-4/F-6)

设计原则:
  - 低误报: 只检查最明确的高风险模式, 避免泛化扫描导致门禁空转
  - 可追溯: 每条报告关联具体修复参考 (F-4/F-5/F-6)
  - 渐进式: 先拦截最明确的违规, 再逐步扩展规则

用法:
    python scripts/check_nan_pollution.py [--path DIR]

退出码:
    0  未发现违规
    1  发现违规
    2  工具自身错误
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
from pathlib import Path

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 需要扫描的目录 (聚焦核心风险模块)
DEFAULT_PATHS = [
    _PROJECT_ROOT / "utils",
    _PROJECT_ROOT / "ms_strategy" / "src",
]

# 文件扩展名
SCAN_EXTS = {".py"}

# 忽略目录
IGNORE_DIRS = {
    ".venv", "venv", "__pycache__", "_archive", "external",
    "node_modules", ".git",
}

# 高风险 API 模式
CORRCOEF_CALLS = {"np.corrcoef", "numpy.corrcoef"}

# F-5 防护: corrcoef 调用后等效的 NaN 清洗模式 (任一匹配即视为已防护).
# 覆盖: nan_to_num / isnan / isfinite / math.isfinite / nanmean / nanmedian.
NAN_PROTECTION_PATTERNS = (
    "nan_to_num",
    "np.isnan",
    "numpy.isnan",
    "np.isfinite",
    "numpy.isfinite",
    "math.isfinite",
    "np.nanmean",
    "numpy.nanmean",
    "np.nanmedian",
    "numpy.nanmedian",
    "pd.isna",  # pandas 等效 NaN 检查
)


class _Violation:
    def __init__(self, filepath: Path, lineno: int, message: str) -> None:
        self.filepath = filepath
        self.lineno = lineno
        self.message = message

    def __str__(self) -> str:
        # 相对路径回退: 传入相对路径 (如 --files utils/foo.py) 时 relative_to 会抛
        # ValueError, 回退到显示原始路径, 避免脚本自身崩溃 (Bug 修复).
        try:
            rel = self.filepath.relative_to(_PROJECT_ROOT)
        except ValueError:
            rel = self.filepath
        return f"{rel}:{self.lineno}: {self.message}"


def _resolve_file(fpath: Path) -> Path:
    """解析文件路径 — 兼容 pre-commit 传入的相对路径.

    pre-commit 通常传入相对于 Git 根的路径 (如 utils/foo.py), 但 cwd 可能
    不是项目根。依次尝试: 原路径 → 相对于 _PROJECT_ROOT → 相对于 cwd.
    """
    if fpath.is_absolute() and fpath.exists():
        return fpath
    # 相对于项目根
    candidate = _PROJECT_ROOT / fpath
    if candidate.exists():
        return candidate.resolve()
    # 原路径 (可能是相对于 cwd 的)
    if fpath.exists():
        return fpath.resolve()
    # 都找不到, 返回原路径 (让 scan_file 报 WARN)
    return fpath


def _iter_python_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        for fname in filenames:
            fpath = Path(dirpath) / fname
            if fpath.suffix in SCAN_EXTS:
                files.append(fpath)
    return files


def _get_line_text(lines: list[str], lineno: int) -> str:
    """获取指定行文本 (1-based)."""
    if 1 <= lineno <= len(lines):
        return lines[lineno - 1]
    return ""


# F-4/F-6 常数序列前置防护: corrcoef 调用前等效的检查模式.
# 覆盖: std 比较检查 / len 检查 / shape 检查 / 样本量 vs 维度比较 (T > N 等).
# 样本量变量名: T/N/n/k/n_obs/n_samples/n_rows/n_cols 等常见量化命名.
CONSTANT_SERIES_GUARD_PATTERNS = (
    # std 检查: 任意 std(...) 比较即视为防护 (>, <, ==)
    ("np.std", (">", "<", "==")),
    ("numpy.std", (">", "<", "==")),
    (".std()", (">", "<", "==")),
    ("std(", (">", "<", "==")),
    # 最小样本量检查: len(...) 比较间接防常数序列
    ("len(", (">=", "<=", ">", "<", "==")),
    # shape 检查
    (".shape", (">=", "<=", ">", "<", "==")),
)

# 样本量 vs 维度变量比较 (如 T > N, n_samples > 1, n_obs >= 2)
# 这些比较间接防止常数序列传入 corrcoef (样本量不足时 corrcoef 无意义).
_SAMPLE_VAR_NAMES = (
    "T", "N", "n", "k", "n_obs", "n_samples", "n_rows", "n_cols",
    "n_observed", "n_assets", "n_features", "n_pairs", "num",
)

# F-5 后置 NaN 防护: 如果 corrcoef 前已有常数序列检查 (std > 0 等),
# 则 corrcoef 不会产生 NaN → 跳过 F-5 后置检查, 避免误报.


def _has_constant_series_guard_before(
    lines: list[str], corrcoef_lineno: int,
) -> bool:
    """检查 corrcoef 调用前 20 行内是否有常数序列防护 (F-4/F-6).

    识别模式 (任一匹配即视为已防护):
      - std 检查: np.std(x) > 阈值 / < 阈值 / == 0 等
      - 最小样本量: len(x) >= N / < N
      - shape 检查: x.shape[0] >= N
      - 样本量 vs 维度比较: T > N / n_samples > 1 等
    """
    start_line = max(1, corrcoef_lineno - 20)
    for i in range(start_line, corrcoef_lineno + 1):
        line_text = _get_line_text(lines, i)
        # 模式 1-3: std/len/shape 比较检查
        for substr, ops in CONSTANT_SERIES_GUARD_PATTERNS:
            if substr in line_text and any(op in line_text for op in ops):
                return True
        # 模式 4: 样本量变量比较 (T > N, n_samples > 1 等)
        for var in _SAMPLE_VAR_NAMES:
            # 匹配 `var > N` / `var >= N` / `var == N` 等 (N 可以是数字或另一个变量)
            for op in (">=", "<=", ">", "<", "=="):
                token = f"{var} {op}"
                if token in line_text:
                    return True
    return False


def _check_corrcoef_nan_protection(
    filepath: Path, tree: ast.AST, lines: list[str],
) -> list[_Violation]:
    """检查 np.corrcoef 调用后是否有 NaN 防护 (F-5).

    识别的等效防护模式 (任一匹配即视为已防护):
      - nan_to_num / np.isnan / np.isfinite / math.isfinite
      - np.nanmean / np.nanmedian / pd.isna
      - 前置常数序列检查 (std > 0 等) 已防住 NaN 产生 → 跳过后置检查
    """
    violations: list[_Violation] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        func_name = ""
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name):
                func_name = f"{func.value.id}.{func.attr}"
        elif isinstance(func, ast.Name):
            func_name = func.id

        if func_name not in CORRCOEF_CALLS:
            continue

        lineno = getattr(node, "lineno", 0)
        if lineno == 0:
            continue

        # 如果 corrcoef 前已有常数序列防护 (std > 0 等), corrcoef 不会产生 NaN
        # → 跳过 F-5 后置检查, 避免误报
        if _has_constant_series_guard_before(lines, lineno):
            continue

        # 检查后续 10 行内是否有任一 NaN 防护模式
        found_protection = False
        for i in range(lineno, min(lineno + 11, len(lines) + 1)):
            line_text = _get_line_text(lines, i)
            for pattern in NAN_PROTECTION_PATTERNS:
                if pattern in line_text:
                    found_protection = True
                    break
            if found_protection:
                break

        if not found_protection:
            violations.append(
                _Violation(
                    filepath,
                    lineno,
                    "np.corrcoef 调用后未检测到 NaN 防护 (F-5)",
                )
            )

    return violations


def _check_ic_constant_series_guard(
    filepath: Path, tree: ast.AST, lines: list[str],
) -> list[_Violation]:
    """检查 IC / 相关性计算前是否有常数序列防护 (F-4/F-6).

    识别的等效防护模式 (任一匹配即视为已防护):
      - std 检查: np.std(x) > 阈值 / < 阈值 / == 0 等
      - 最小样本量: len(x) >= N / < N
      - shape 检查: x.shape[0] >= N
      - 样本量 vs 维度比较: T > N / n_samples > 1 等
    """
    violations: list[_Violation] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        func_name = ""
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name):
                func_name = f"{func.value.id}.{func.attr}"
        elif isinstance(func, ast.Name):
            func_name = func.id

        if func_name not in CORRCOEF_CALLS:
            continue

        lineno = getattr(node, "lineno", 0)
        if lineno == 0:
            continue

        if _has_constant_series_guard_before(lines, lineno):
            continue

        violations.append(
            _Violation(
                filepath,
                lineno,
                f"{func_name} 调用前未检测到常数序列防护 (std 检查) (F-4/F-6)",
            )
        )

    return violations


def scan_file(fpath: Path) -> list[_Violation]:
    """扫描单个 Python 文件."""
    if fpath.suffix not in SCAN_EXTS:
        return []
    try:
        source = fpath.read_text(encoding="utf-8")
        tree = ast.parse(source)
        lines = source.splitlines()
    except Exception as exc:
        print(f"[WARN] 无法解析 {fpath}: {exc}", file=sys.stderr)
        return []

    return (
        _check_corrcoef_nan_protection(fpath, tree, lines)
        + _check_ic_constant_series_guard(fpath, tree, lines)
    )


def scan(path: Path) -> tuple[list[_Violation], int]:
    """扫描指定路径下的 Python 文件."""
    all_violations: list[_Violation] = []
    scanned = 0

    if path.is_file():
        files = [path]
    else:
        files = _iter_python_files(path)

    for fpath in files:
        violations = scan_file(fpath)
        if violations:
            all_violations.extend(violations)
            scanned += 1

    return all_violations, scanned


def main() -> int:
    parser = argparse.ArgumentParser(
        description="NaN 污染守卫检查 (F-4/F-5/F-6)"
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=None,
        help="要扫描的目录或文件 (默认: utils/ ms_strategy/src/)",
    )
    parser.add_argument(
        "--files",
        nargs="+",
        type=Path,
        default=None,
        help="要检查的具体文件列表 (用于 pre-commit 暂存文件检查)",
    )
    args = parser.parse_args()

    all_violations: list[_Violation] = []
    total_scanned = 0

    if args.files:
        # 只检查指定文件 (pre-commit 暂存文件模式)
        for fpath in args.files:
            resolved = _resolve_file(fpath)
            violations = scan_file(resolved)
            all_violations.extend(violations)
            if violations:
                total_scanned += 1
    else:
        # 扫描整个目录
        targets = (
            [args.path] if args.path else [p for p in DEFAULT_PATHS if p.exists()]
        )
        if not targets:
            print("[INFO] 未找到需要扫描的目录", file=sys.stderr)
            return 0

        for target in targets:
            violations, scanned = scan(target)
            all_violations.extend(violations)
            total_scanned += scanned

    if all_violations:
        print(f"[FAIL] 发现 {len(all_violations)} 处 NaN 污染风险 (扫描 {total_scanned} 文件):")
        for v in all_violations:
            print(f"  {v}")
        return 1

    print(f"[PASS] NaN 守卫检查通过 (扫描 {total_scanned} 文件)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
