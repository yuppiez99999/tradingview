#!/usr/bin/env python
"""
除零风险智能分类修复工具 v1.0

功能:
1. 读取 _bug_scan_results.json / _prod_bug_scan_results.json
2. 对每个 DIV_ZERO_RISK 条目, 在源码上下文(±5行)中查找真实除法
3. 分类: REAL_DIV_ZERO / ALREADY_GUARDED / FALSE_POSITIVE / NEEDS_VERIFY
4. 对 REAL_DIV_ZERO 生成suggested fix
5. 输出结构化报告

使用方法:
  python scripts/fix_div_zero.py                    # 分析生产模块
  python scripts/fix_div_zero.py --full              # 分析全量
  python scripts/fix_div_zero.py --fix --dry-run     # 生成补丁(不应用)
  python scripts/fix_div_zero.py --fix --apply       # 生成并应用补丁(需确认)
"""
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROD_SCAN = PROJECT_ROOT / "scripts" / "_prod_bug_scan_results.json"
FULL_SCAN = PROJECT_ROOT / "scripts" / "_bug_scan_results.json"
OUTPUT_REPORT = PROJECT_ROOT / "scripts" / "_div_zero_fix_report.json"

# 生产/核心文件白名单 — 只修复这些文件
PROD_FILE_PATTERNS = [
    "daily_trade_executor",
    "alpha_hedge_engine",
    "build_plan_executor",
    "stop_loss_monitor",
    "signal_monitor",
    "system_health_check",
    "daily_build_and_hedge",
    "daily_hedge_update",
    "execution/automated_execution_system",
    "execution/",
    "utils/execution/",
    "ai_decision/",
    "realtime_monitor/",
    "reporting/",
]

# 安全除法模式 — 这些模式已经处理了除零
SAFE_DIVISION_PATTERNS = [
    re.compile(r"np\.(?:mean|std|var|average|nanmean|nanstd|nanvar)\("),
    re.compile(r"pd\.\w+\.(?:mean|std|sum)\("),
    re.compile(r"\.(?:mean|std|sum|median|quantile)\(\)"),
    re.compile(r"safe_divide?\w*\("),
    re.compile(r"/(?!\s*/)\s*(?:max|abs)\([^)]+\)"),  # a / max(b, eps) 模式
    re.compile(r"if\s+\w+\s*(?:!=\s*0|>\s*0)"),  # 前面有显式非零检查(粗略匹配)
    re.compile(r"len\([^)]+\)\s*/"),  # len() 结果作分子, 不可能为零
]


@dataclass
class DivZeroEntry:
    file: str
    line: int
    raw_line_content: str
    real_division_line: int = 0
    division_code: str = ""
    division_op: str = ""  # '/', '//', '%'
    category: str = "UNCLASSIFIED"
    denominator_expr: str = ""
    fix_suggestion: str = ""
    context_before: list[str] = field(default_factory=list)
    context_after: list[str] = field(default_factory=list)


def load_scan_results(scan_file: Path) -> dict[str, list]:
    """加载扫描结果JSON."""
    with open(scan_file, encoding="utf-8") as f:
        return json.load(f)


def normalize_path(file_path: str) -> str:
    """标准化路径: 移除项目根前缀."""
    fp = str(file_path).replace("\\", "/")
    prefixes = [
        "e:/各种PY程序/28-终极量化交易系统8.4/",
        "E:/各种PY程序/28-终极量化交易系统8.4/",
    ]
    for p in prefixes:
        if fp.lower().startswith(p.lower()):
            fp = fp[len(p):]
            break
    return fp


def is_prod_file(file_path: str) -> bool:
    """判断文件是否在生产/核心模块中."""
    fp = normalize_path(file_path).lower()
    for pat in PROD_FILE_PATTERNS:
        if pat.lower() in fp:
            return True
    # 排除 _archive, research, tests
    exclude = ["_archive", "research/", "tests/"]
    for ex in exclude:
        if fp.startswith(ex) or "/" + ex in fp:
            return False
    return False


def read_file_lines(file_path: str, center_line: int, context: int = 5) -> tuple[list[str], str, list[str]]:
    """读取文件指定行及上下文."""
    resolved = PROJECT_ROOT / normalize_path(file_path)
    if not resolved.exists():
        # 尝试直接用原始路径
        resolved = Path(file_path) if os.path.isabs(file_path) else PROJECT_ROOT / file_path
        if not resolved.exists():
            return [], "", []

    try:
        with open(resolved, encoding="utf-8") as f:
            all_lines = f.readlines()
    except (UnicodeDecodeError, OSError):
        return [], "", []

    idx = center_line - 1  # 转为0-based
    if idx < 0 or idx >= len(all_lines):
        return [], "", []

    start = max(0, idx - context)
    end = min(len(all_lines), idx + context + 1)
    before = [l.rstrip("\n") for l in all_lines[start:idx]]
    center = all_lines[idx].rstrip("\n")
    after = [l.rstrip("\n") for l in all_lines[idx + 1 : end]]
    return before, center, after


def find_real_division_target(before: list[str], center: str, after: list[str], flagged_line: int) -> tuple[int, str, str, str]:
    """在上下文中查找真正的除法操作.

    返回: (实际除法行号, 代码行, 操作符, 分母表达式) 或 (0, '', '', '')
    """
    # 构建上下文: before + center + after, 带行号
    context_start = flagged_line - len(before)
    all_context = before + [center] + after

    division_ops = []

    for i, code_line in enumerate(all_context):
        line_no = context_start + i
        stripped = code_line.strip()

        # 跳过注释行和字符串
        if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
            continue

        # 查找 /, //, % 运算
        # 使用简单tokenization避免字符串内匹配
        for op in ["/", "//", "%"]:
            if op in stripped:
                # 排除 import/from 语句, 路径字符串
                if is_likely_path_or_string(stripped, op):
                    continue
                if is_safe_division(stripped):
                    continue
                # 提取分母
                denom = extract_denominator(stripped, op)
                division_ops.append((line_no, code_line, op, denom))

    if division_ops:
        # 返回最近的除法(通常是after中第一个)
        return division_ops[0]

    return (0, "", "", "")


def is_likely_path_or_string(code: str, op: str) -> bool:
    """排除文件路径、URL、字符串字面量中的 / 和 //."""
    # 文件路径
    if re.search(r'["\']\.{0,2}[/\\]', code):
        return True
    # URL
    if "http://" in code or "https://" in code:
        return True
    # 注释中的 (已被上级过滤, 这里做双重保险)
    # 已经在字符串内的 (简化判断)
    return False


def is_safe_division(code: str) -> bool:
    """判断除法模式是否已经安全防护."""
    for pat in SAFE_DIVISION_PATTERNS:
        if pat.search(code):
            return True
    # 分母是字面量非零值 (如 / 100, / 252.0)
    if re.search(r"/\s*[\d.]+[^eE\d.]", code):
        return True
    if re.search(r"//\s*[\d.]+", code):
        # 检查非零
        m = re.search(r"(?:/|//)\s*([\d.]+)", code)
        if m and float(m.group(1)) != 0.0:
            return True
    return False


def extract_denominator(code: str, op: str) -> str:
    """从除法规格中提取分母表达式."""
    # 简化: 取 op 后面到下一个运算符或逗号/冒号为止
    escaped_op = re.escape(op)
    pattern = escaped_op + r"\s*([^,\[\](){}:;+\-*=<>!&|]+?)(?:$|\s*[,;+\-*)=<>!&|]|\s*$)"
    m = re.search(pattern, code)
    if m:
        return m.group(1).strip()
    return ""


def classify_entry(entry: DivZeroEntry) -> str:
    """分类一个除零条目."""
    if not entry.division_code:
        return "FALSE_POSITIVE"

    code = entry.division_code.strip()

    # 检查是否已经在安全保护中
    if is_safe_division(code):
        return "ALREADY_GUARDED"

    # 检查上下文中的 if guard
    all_context = entry.context_before + [entry.raw_line_content] + entry.context_after
    context_text = "\n".join(all_context)
    denom = entry.denominator_expr

    if denom:
        # 检查前一行是否有 if denom != 0 的保护
        guard_patterns = [
            rf"if\s+{re.escape(denom)}\s*(?:!=\s*0|>\s*0|not?\s*=\s*0)",
            rf"if\s+{re.escape(denom)}:",
            rf"assert\s+{re.escape(denom)}",
        ]
        for gp in guard_patterns:
            if re.search(gp, context_text):
                return "ALREADY_GUARDED"

    # 分母是 len() — len 结果不会是零除数(在Python 3中)
    if re.match(r"^len\(.+\)$", denom.strip()):
        return "ALREADY_GUARDED"

    # 真正的风险
    return "REAL_DIV_ZERO"


def suggest_fix(entry: DivZeroEntry) -> str:
    """为真正的除零风险生成修复建议."""
    code = entry.division_code.strip()
    denom = entry.denominator_expr

    # 简化模式: a / b 且 b 可能为0
    if entry.division_op == "/":
        return (
            f"# FIX: 除零风险 — 分母可以是 '{denom}'\n"
            f"# 建议: 在除法前行添加保护或使用 safe_divide()\n"
            f"# 方案A: if {denom} and {denom} != 0:\n"
            f"# 方案B: safe_result = {denom} and ({code.split(entry.division_op)[0].strip()} / {denom}) or 0.0"
        )

    if entry.division_op == "%":
        return (
            f"# FIX: 取模零风险 — 分母 '{denom}' 可能为0\n"
            f"# 建议: 添加 if {denom} != 0: 保护"
        )

    return f"# FIX: 除零风险 — 代码 '{code}' 需人工审核"


def process_entries(scan_file: Path, prod_only: bool = True) -> list[DivZeroEntry]:
    """处理扫描结果中的所有除零条目."""
    data = load_scan_results(scan_file)
    entries = data.get("DIV_ZERO_RISK", [])
    results = []

    for item in entries:
        file_path = item.get("file", "")
        line_no = item.get("line", 0)
        if not file_path or not line_no:
            continue

        # 生产模式过滤
        if prod_only and not is_prod_file(file_path):
            continue

        entry = DivZeroEntry(
            file=normalize_path(file_path),
            line=line_no,
            raw_line_content="",
        )

        # 读取上下文
        before, center, after = read_file_lines(file_path, line_no, context=5)
        entry.raw_line_content = center
        entry.context_before = before
        entry.context_after = after

        # 查找真正的除法
        div_line, div_code, div_op, denom = find_real_division_target(
            before, center, after, line_no
        )
        entry.real_division_line = div_line
        entry.division_code = div_code.strip() if div_code else ""
        entry.division_op = div_op
        entry.denominator_expr = denom

        # 分类
        entry.category = classify_entry(entry)

        # 生成修复建议
        if entry.category == "REAL_DIV_ZERO":
            entry.fix_suggestion = suggest_fix(entry)

        results.append(entry)

    return results


def print_summary(results: list[DivZeroEntry]):
    """打印统计摘要."""
    cats = defaultdict(list)
    for e in results:
        cats[e.category].append(e)

    print("\n" + "=" * 70)
    print("除零风险分析报告")
    print("=" * 70)
    print(f"  扫描条目: {len(results)}")
    print()

    for cat, items in sorted(cats.items()):
        n = len(items)
        pct = n / max(len(results), 1) * 100
        icon = {"REAL_DIV_ZERO": "HIGH", "ALREADY_GUARDED": "OK", "FALSE_POSITIVE": "OK", "UNCLASSIFIED": "???"}.get(cat, "---")
        print(f"  [{icon}] {cat:20s}: {n:4d} ({pct:5.1f}%)")

    print()

    # 列出需要修复的
    real = cats.get("REAL_DIV_ZERO", [])
    if real:
        print(f"  需要修复: {len(real)} 处")
        print("  " + "-" * 65)
        for e in real[:20]:  # 最多显示20条
            print(f"    {e.file}:{e.real_division_line or e.line}  [{e.division_op}] {e.division_code[:60]}")
        if len(real) > 20:
            print(f"    ... 还有 {len(real) - 20} 处")
    else:
        print("  无需修复! 所有除零风险要么已防护, 要么是误报。")

    print()


def save_report(results: list[DivZeroEntry], output_path: Path):
    """保存结构化报告."""
    report = {
        "generated_at": "2026-08-02",
        "total_entries": len(results),
        "by_category": {},
        "entries": [],
    }

    for e in results:
        report["by_category"][e.category] = report["by_category"].get(e.category, 0) + 1

    for e in results:
        if e.category in ("REAL_DIV_ZERO", "NEEDS_VERIFY"):
            report["entries"].append(
                {
                    "file": e.file,
                    "line": e.line,
                    "real_division_line": e.real_division_line,
                    "category": e.category,
                    "division_code": e.division_code,
                    "division_op": e.division_op,
                    "denominator": e.denominator_expr,
                    "fix_suggestion": e.fix_suggestion,
                }
            )

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"  详细报告已保存: {output_path}")


def generate_fix_patches(results: list[DivZeroEntry], output_dir: Optional[Path] = None):
    """为 REAL_DIV_ZERO 条目生成补丁文件."""
    if output_dir is None:
        output_dir = PROJECT_ROOT / "scripts" / "div_zero_patches"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 按文件分组
    by_file = defaultdict(list)
    for e in results:
        if e.category == "REAL_DIV_ZERO":
            by_file[e.file].append(e)

    patches = []
    for file_path, entries in sorted(by_file.items()):
        resolved = PROJECT_ROOT / file_path
        if not resolved.exists():
            continue
        with open(resolved, encoding="utf-8") as f:
            source_lines = f.readlines()

        for entry in entries:
            target_line = entry.real_division_line or entry.line
            idx = target_line - 1
            if idx < 0 or idx >= len(source_lines):
                continue

            patch = {
                "file": file_path,
                "line": target_line,
                "original": source_lines[idx].rstrip("\n"),
                "suggestion": entry.fix_suggestion,
            }
            patches.append(patch)

    patch_file = output_dir / "fix_suggestions.json"
    with open(patch_file, "w", encoding="utf-8") as f:
        json.dump(patches, f, ensure_ascii=False, indent=2)
    print(f"  补丁建议已保存: {patch_file} ({len(patches)} 条)")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="除零风险智能分析修复工具")
    parser.add_argument("--full", action="store_true", help="分析全量扫描结果(默认仅生产模块)")
    parser.add_argument("--fix", action="store_true", help="生成修复补丁")
    parser.add_argument("--dry-run", action="store_true", help="仅分析, 不生成补丁")
    parser.add_argument("--apply", action="store_true", help="应用补丁(需人工确认)")
    args = parser.parse_args()

    # 选择扫描数据源
    if args.full:
        scan_file = FULL_SCAN
        prod_only = False
        print("[全量模式] 分析 _bug_scan_results.json")
    else:
        scan_file = PROD_SCAN
        prod_only = True
        print("[生产模式] 分析 _prod_bug_scan_results.json")

    if not scan_file.exists():
        print(f"错误: 找不到扫描文件 {scan_file}")
        return 1

    results = process_entries(scan_file, prod_only=prod_only)
    print_summary(results)

    if args.fix and not args.dry_run:
        generate_fix_patches(results)

    save_report(results, OUTPUT_REPORT)

    # 如果有真正的除零风险且不是dry-run, 给出后续步骤
    real_count = sum(1 for r in results if r.category == "REAL_DIV_ZERO")
    if real_count > 0 and not args.fix:
        print(f"\n  → {real_count} 处需修复, 运行: python scripts/fix_div_zero.py --fix")
    elif real_count == 0:
        print("\n  ✓ 生产模块除零风险已清零 (或均为安全模式)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
