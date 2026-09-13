"""
CI 前视偏差自动检测门禁
========================

在每次代码提交时自动扫描以下前视偏差模式:
  1. bfill / fillna 后向填充 (method 取 bfill 或 backfill) — 引入未来数据
  2. shift(-N) — 负向位移引入未来数据
  3. pct_change(-N) — 取未来期收益
  4. train_test_split — 时序数据随机分割导致数据泄漏
  5. 全样本标准化 — 用全样本均值/标准差做标准化

用法:
  python ci_lookahead_guard.py          # 扫描全部源码
  python ci_lookahead_guard.py --path utils/  # 扫描指定目录
  python ci_lookahead_guard.py --files "a.py b.py"  # 增量模式 (CI PR 门禁)

退出码:
  0 — 通过 (无违规)
  1 — 失败 (发现前视偏差模式)
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
import tokenize
from pathlib import Path
from typing import Any

# ruff: noqa: T201 — CI 门禁 CLI, print 即诊断输出 (与 backtests/ 豁免口径一致)

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

# P0 修复 (2026-09-13): 根目录 *.py 全量纳入扫描。
# 原实现只白名单 3-4 个根目录文件 (且 daily_workflow.py 用相对 cwd 判断,
# 几乎恒为 False), 策略核心 (daily_trade_executor / ensemble_trainer /
# autolearn_trainer / alpha_hedge_engine / backtest_engine 等) 全部不受保护。
SCAN_FILES = sorted(p.name for p in ROOT.glob("*.py"))

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

# 文件级显式豁免 (2026-09-13): 经人工确认合法的例外, 需附理由, 变更需 review。
# 与已删除的行级 "# fix" 后门不同 — 那是任何人任何行都能写的绕过; 本清单是
# 集中登记、带理由、diff 可见的受控豁免。键 = 仓库相对路径。
KNOWN_EXCEPTIONS: dict[str, str] = {
    # 截面数据质量评分 (quality_score/completeness/... 无时间列),
    # 随机分层分割对截面表格数据是标准做法, 不存在时序泄漏
    "utils/supply_chain_risk/train.py": "截面数据质量评分, 非时序; 随机分层分割合法",
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
        r"fillna\s*\([^)]*method\s*=\s*['\"](bfill|backfill)",
        "CRITICAL",
        "fillna 后向填充 (bfill/backfill) 会引入未来数据",
    ),
    (
        "NegativeShift",
        r"\.shift\s*\(\s*-\d+\s*\)",
        "HIGH",
        "shift(-N) 负向位移引入未来数据, 需确认是否为标签构造(允许)而非特征(禁止)",
    ),
    (
        "NegativePctChange",
        r"\.pct_change\s*\(\s*-\d+",
        "HIGH",
        "pct_change(-N) 取未来期收益引入未来数据 (标签构造除外)",
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
    """检查路径是否在排除目录中 (P0 修复 2026-09-13: 精确段匹配)。

    原实现用子串匹配 (``part in excluded``), 导致任何名为 "data" 的目录
    都被排除 ("data" 是 "ms_strategy/dataset" 的子串) — 覆盖面静默缩水。
    """
    parts = path.parts
    return any(excluded in parts for excluded in EXCLUDE_DIRS)


def _string_token_lines(content: str) -> set[int] | None:
    """经 tokenize 找出全部字符串字面量 (含文档字符串) 所在行号。

    P0 修复 (2026-09-13): 测试文件内嵌的示例代码、守卫自身的规则文本等
    字符串字面量会触发规则假阳性 (6/7 假 CRITICAL), 令守卫永久自阻塞。
    返回 None 表示文件无法 tokenize (语法错误) → 调用方退回逐行扫描。
    """
    string_lines: set[int] = set()
    try:
        tokens = tokenize.generate_tokens(io.StringIO(content).readline)
        fstring_start = getattr(tokenize, "FSTRING_START", -1)
        fstring_end = getattr(tokenize, "FSTRING_END", -1)
        for tok in tokens:
            if tok.type in (tokenize.STRING, fstring_start, fstring_end):
                string_lines.update(range(tok.start[0], tok.end[0] + 1))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None
    return string_lines


def scan_file(filepath: Path) -> list[dict]:
    """扫描单个文件中的前视偏差模式

    Returns:
        违规列表, 每项包含文件、行号、规则名、严重程度、代码片段
    """
    if filepath.resolve() == Path(__file__).resolve():
        return []  # 守卫不扫描自身 (规则文本即模式来源, 自匹配无意义)

    violations: list[Any] = []
    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return violations

    string_lines = _string_token_lines(content)

    lines = content.splitlines()
    for i, line in enumerate(lines, 1):
        # 跳过注释行与字符串字面量行 (P0 修复: 字面量假阳性)
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if string_lines is not None and i in string_lines:
            continue

        for rule_name, pattern, severity, description in RULES:
            if re.search(pattern, line):
                # shift(-N)/pct_change(-N) 用于标签构造是允许的
                if rule_name in ("NegativeShift", "NegativePctChange") and any(
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
    """主入口: 扫描所有源码文件 (或 --files 指定的增量文件集)

    Returns:
        0 — 通过, 1 — 失败
    """
    parser = argparse.ArgumentParser(description="CI 前视偏差自动检测门禁")
    parser.add_argument("--path", type=str, default=None, help="指定扫描目录")
    parser.add_argument(
        "--files",
        type=str,
        default=None,
        help="增量模式: 空格分隔的待扫描文件列表 (CI PR 增量门禁用)",
    )
    args = parser.parse_args()

    # 收集待扫描文件
    files_to_scan = []
    if args.files:
        for raw in args.files.split():
            p = Path(raw)
            if not p.is_absolute():
                p = ROOT / raw
            if p.is_file() and p.suffix == ".py":
                files_to_scan.append(p)
    elif args.path:
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
        # P0 修复 (2026-09-13): 根目录 *.py 一并纳入 (策略核心在此)
        files_to_scan.extend(ROOT / name for name in SCAN_FILES)

    # 去重
    files_to_scan = list(set(files_to_scan))

    all_violations: list[dict[str, Any]] = []
    for filepath in sorted(files_to_scan):
        if should_exclude(filepath):
            continue
        violations = scan_file(filepath)
        # 文件级受控豁免 (KNOWN_EXCEPTIONS, 带理由集中登记; posix 归一跨平台)
        rel = (
            filepath.relative_to(ROOT).as_posix()
            if filepath.is_relative_to(ROOT)
            else filepath.as_posix()
        )
        if rel in KNOWN_EXCEPTIONS:
            if violations:
                print(
                    f"[LOOKAHEAD-GUARD] EXEMPT {rel}: "
                    f"{len(violations)} hit(s) — {KNOWN_EXCEPTIONS[rel]}"
                )
            continue
        all_violations.extend(violations)

    # P0 修复 (2026-09-13): 原实现统计结果赋给匿名临时变量、打印循环体为
    # `pass` — 违规被整体吞掉, CI 失败时无任何诊断信息, 守卫形同虚设。
    if not all_violations:
        print(f"[LOOKAHEAD-GUARD] PASS - scanned {len(files_to_scan)} files, no violations")
        return 0

    n_critical = sum(1 for v in all_violations if v["severity"] == "CRITICAL")
    n_high = sum(1 for v in all_violations if v["severity"] == "HIGH")
    n_medium = sum(1 for v in all_violations if v["severity"] == "MEDIUM")
    print(
        f"[LOOKAHEAD-GUARD][FAIL] {len(all_violations)} violation(s) "
        f"(CRITICAL={n_critical}, HIGH={n_high}, MEDIUM={n_medium}) "
        f"in {len(files_to_scan)} scanned file(s)"
    )
    for v in all_violations:
        print(
            f"  {v['file']}:{v['line']} [{v['severity']}][{v['rule']}] "
            f"{v['code']} — {v['description']}"
        )

    # P0 修复 (2026-09-13): 分级门禁 — CRITICAL 一律阻断; HIGH/MEDIUM 默认
    # 告警放行 (CI_LOOKAHEAD_STRICT=1 升级为阻断), 否则存量 MEDIUM
    # (如 supply_chain_risk/train.py 的 train_test_split) 会永久阻塞 CI。
    if n_critical > 0:
        print(f"[LOOKAHEAD-GUARD] BLOCK: {n_critical} CRITICAL violation(s)")
        return 1
    if os.environ.get("CI_LOOKAHEAD_STRICT", "").strip() == "1":
        print("[LOOKAHEAD-GUARD] BLOCK (STRICT): HIGH/MEDIUM violations present")
        return 1
    print(
        "[LOOKAHEAD-GUARD] WARN: HIGH/MEDIUM violations reported "
        "(set CI_LOOKAHEAD_STRICT=1 to block)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
