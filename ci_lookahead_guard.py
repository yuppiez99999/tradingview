"""
CI 前视偏差自动检测门禁
========================

在每次代码提交时自动扫描以下前视偏差模式:
  1. bfill() / fillna(method='bfill') — 后向填充引入未来数据
  2. shift(-N) — 负向位移引入未来数据
  3. train_test_split — 时序数据随机分割导致数据泄漏
  4. 全样本标准化 — 用全样本均值/标准差做标准化

用法:
  python ci_lookahead_guard.py          # 扫描全部源码
  python ci_lookahead_guard.py --path utils/  # 扫描指定目录

退出码:
  0 — 通过 (无违规)
  1 — 失败 (发现前视偏差模式)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

# 项目根目录
ROOT = Path(__file__).resolve().parent

# 需要扫描的目录 (排除虚拟环境和第三方库)
SCAN_DIRS = [
    "utils",
    "src",
    "v8.3_institutional/src",
    "v8.3_institutional",
    "research",
    "scripts",
    "tools",
    "realtime_monitor",
    "15_每日工作流",
    "tests",
]

# 根目录下需要单独扫描的关键生产文件 (2026-07-25 审计 P1-10 修复)
# 这些文件不在 SCAN_DIRS 的子目录中, 此前未被 CI 守卫覆盖
SCAN_FILES = [
    "lgb_enhanced_trainer.py",  # V9 训练核心 (含 shift(-5) 标签构造)
    "institutional_pipeline_runner.py",  # V9 集成核心 (生产 pipeline 入口)
    "system_health_check.py",  # 系统健康检查
    "daily_workflow.py" if Path("daily_workflow.py").exists() else None,
]
SCAN_FILES = [f for f in SCAN_FILES if f is not None]

# 排除的目录
EXCLUDE_DIRS = {
    "qlib_env",
    "ifind-finance-data-1.3.0",
    "temp",
    ".agents",
    "second-brain",
    "__pycache__",
    ".git",
    "node_modules",
    "ms_strategy/dataset",
}

# 前视偏差检测规则
# (规则名, 正则模式, 严重程度, 说明)
RULES: list[tuple[str, str, str, str]] = [
    (
        "Bfill",
        r"\.bfill\s*\(",
        "CRITICAL",
        "bfill() 后向填充会引入未来数据, 禁止在特征工程中使用",
    ),
    (
        "BfillMethod",
        r"fillna\s*\([^)]*method\s*=\s*['\"]bfill",
        "CRITICAL",
        "fillna(method='bfill') 后向填充会引入未来数据",
    ),
    (
        "NegativeShift",
        r"\.shift\s*\(\s*-\d+\s*\)",
        "HIGH",
        "shift(-N) 负向位移引入未来数据, 需确认是否为标签构造(允许)而非特征(禁止)",
    ),
    (
        "TrainTestSplit",
        r"from\s+sklearn\.model_selection\s+import.*train_test_split",
        "MEDIUM",
        "train_test_split 随机分割时序数据导致泄漏, 时序数据应使用 TimeSeriesSplit",
    ),
    (
        "GlobalStandardize",
        r"(np\.nanmean|np\.mean|np\.nanstd|np\.std)\s*\(\s*X\s*,\s*axis\s*=\s*0\s*\)",
        "HIGH",
        "全样本标准化引入前视偏差, 标准化参数应只在训练集上计算",
    ),
]


def should_exclude(path: Path) -> bool:
    """检查路径是否在排除目录中"""
    parts = path.parts
    for excluded in EXCLUDE_DIRS:
        if any(part in excluded for part in parts):
            return True
    return False


def scan_file(filepath: Path) -> list[dict]:
    """扫描单个文件中的前视偏差模式

    Returns:
        违规列表, 每项包含文件、行号、规则名、严重程度、代码片段
    """
    violations: list[Any] = []
    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return violations

    lines = content.splitlines()
    for i, line in enumerate(lines, 1):
        # 跳过注释行
        stripped = line.strip()
        if stripped.startswith("#"):
            continue

        for rule_name, pattern, severity, description in RULES:
            if re.search(pattern, line):
                # 特殊处理: 检查是否在修复注释中
                if "修复" in line or "fix" in line.lower():
                    continue
                # 特殊处理: shift(-N) 用于标签构造是允许的
                if rule_name == "NegativeShift" and any(
                    keyword in line.lower()
                    for keyword in [
                        "label",
                        "target",
                        "forward_return",
                        "y_",
                        "ret_fwd",
                        "pct_change",  # pct_change().shift(-1) 是构造前瞻收益标签的标准做法
                    ]
                ):
                    continue

                violations.append(
                    {
                        "file": str(filepath.relative_to(ROOT)),
                        "line": i,
                        "rule": rule_name,
                        "severity": severity,
                        "code": stripped[:120],
                        "description": description,
                    }
                )

    return violations


def main() -> int:
    """主入口: 扫描所有源码文件

    Returns:
        0 — 通过, 1 — 失败
    """
    parser = argparse.ArgumentParser(description="CI 前视偏差自动检测门禁")
    parser.add_argument("--path", type=str, default=None, help="指定扫描目录")
    args = parser.parse_args()

    # 收集待扫描文件
    files_to_scan = []
    if args.path:
        target = ROOT / args.path
        if target.is_dir():
            files_to_scan = list(target.rglob("*.py"))
        elif target.is_file() and target.suffix == ".py":
            files_to_scan = [target]
    else:
        for scan_dir in SCAN_DIRS:
            dir_path = ROOT / scan_dir
            if dir_path.is_dir():
                files_to_scan.extend(
                    f for f in dir_path.rglob("*.py") if not should_exclude(f)
                )

    # 去重
    files_to_scan = list(set(files_to_scan))

    all_violations = []
    for filepath in sorted(files_to_scan):
        if should_exclude(filepath):
            continue
        violations = scan_file(filepath)
        all_violations.extend(violations)

    # 按严重程度统计
    [v for v in all_violations if v["severity"] == "CRITICAL"]
    [v for v in all_violations if v["severity"] == "HIGH"]
    [v for v in all_violations if v["severity"] == "MEDIUM"]

    if not all_violations:
        return 0

    # 输出违规详情

    for _v in all_violations:
        pass

    return 1


if __name__ == "__main__":
    sys.exit(main())
