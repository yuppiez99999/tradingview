#!/usr/bin/env python
"""
静默异常处理正态化工具 v1.0

功能:
1. 扫描 BROAD_EXCEPT_SILENT 条目
2. 分类: TRUE_SILENT / VARIABLE_FALLBACK / ALREADY_LOGGED / FALSE_POSITIVE
3. TRUE_SILENT: except块体为 pass/空 — 最小添加 logger.warning
4. VARIABLE_FALLBACK: except块体仅赋值 None/默认值 — 添加 logger.info
5. 生成修复补丁供人工审核

使用:
  python scripts/fix_silent_except.py                    # 分析
  python scripts/fix_silent_except.py --fix              # 生成补丁
  python scripts/fix_silent_except.py --fix --apply      # 应用补丁
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
OUTPUT_REPORT = PROJECT_ROOT / "scripts" / "_silent_except_fix_report.json"
PATCH_DIR = PROJECT_ROOT / "scripts" / "silent_except_patches"


PROD_FILE_PATTERNS = [
    "execution/",
    "utils/execution/",
    "ai_decision/",
    "realtime_monitor/",
    "reporting/",
]

# except块体模式分类
PASS_PATTERN = re.compile(r"^\s*pass\s*(?:#.*)?$")
EMPTY_PATTERN = re.compile(r"^\s*$")
ASSIGN_NONE_PATTERN = re.compile(r"^\s*(\w[\w.]*)\s*=\s*(?:None|\[\]|\{\}|set\(\)|False|0|0\.0|{}\"\")\s*(?:#.*)?$")
LOGGER_PATTERN = re.compile(r"^\s*(?:logger|self\.logger|logging)\.\s*(?:warning|error|info|debug|exception)\(")


@dataclass
class SilentExceptEntry:
    file: str
    line: int
    except_stmt: str
    body_lines: list[str] = field(default_factory=list)
    category: str = "UNCLASSIFIED"
    fix_suggestion: str = ""


def load_scan_results(scan_file: Path) -> dict[str, list]:
    with open(scan_file, encoding="utf-8") as f:
        return json.load(f)


def normalize_path(file_path: str) -> str:
    fp = str(file_path).replace("\\", "/")
    for prefix in ["e:/各种PY程序/28-终极量化交易系统8.4/", "E:/各种PY程序/28-终极量化交易系统8.4/"]:
        if fp.lower().startswith(prefix.lower()):
            fp = fp[len(prefix):]
            break
    return fp


def is_prod_file(file_path: str) -> bool:
    fp = normalize_path(file_path).lower()
    for pat in PROD_FILE_PATTERNS:
        if pat.lower() in fp:
            return True
    exclude = ["_archive", "research/", "tests/"]
    for ex in exclude:
        if fp.startswith(ex) or "/" + ex in fp:
            return False
    return False


def read_except_block(file_path: str, except_line: int, max_body: int = 10) -> tuple[str, list[str]]:
    """读取 except 语句及其块体."""
    resolved = PROJECT_ROOT / normalize_path(file_path)
    if not resolved.exists():
        if os.path.isabs(file_path):
            resolved = Path(file_path)
        if not resolved.exists():
            return "", []

    try:
        with open(resolved, encoding="utf-8") as f:
            all_lines = f.readlines()
    except (UnicodeDecodeError, OSError):
        return "", []

    idx = except_line - 1
    if idx < 0 or idx >= len(all_lines):
        return "", []

    # 获取 except 语句
    except_stmt = all_lines[idx].rstrip("\n")

    # 获取块体 (通过缩进判断)
    indent_match = re.match(r"^(\s*)", except_stmt)
    if not indent_match:
        return except_stmt, []
    except_indent = len(indent_match.group(1))
    body_indent = except_indent + 4  # 假设4空格缩进

    body = []
    for j in range(idx + 1, min(idx + 1 + max_body, len(all_lines))):
        line = all_lines[j].rstrip("\n")
        if line.strip() == "":
            body.append(line)
            continue
        # 判断是否是 except/else/finally/return/decorator — 块体结束
        if re.match(r"^\s{0,%d}(?:except|else|finally|return|@|def |class )" % (except_indent), line):
            break
        # 判断缩进是否更深或相同(同级代码结束块体)
        line_indent = len(re.match(r"^(\s*)", line).group(1))
        if line_indent <= except_indent and line.strip() != "":
            break
        body.append(line)

    return except_stmt, body


def classify_except(except_stmt: str, body_lines: list[str]) -> str:
    """分类 except 块."""
    # 提取块体中有意义的行 (非空非纯注释)
    meaningful = [l for l in body_lines if l.strip() and not l.strip().startswith("#")]

    if not meaningful:
        # 空的 except 块
        if re.search(r"except\s*(?:Exception|BaseException|)\s*:", except_stmt):
            return "TRUE_SILENT_EMPTY"
        if re.search(r"except\s+\w+Error\s*:", except_stmt):
            return "SPECIFIC_EMPTY"  # 特定异常但空处理 — 需人工判断
        return "TRUE_SILENT_EMPTY"

    # 检查是否已包含日志
    for line in meaningful:
        if LOGGER_PATTERN.search(line.strip()):
            return "ALREADY_LOGGED"  # 已有日志
        if "traceback" in line.lower() or "print_exc" in line:
            return "ALREADY_LOGGED"

    # 检查是否仅做变量回退
    if len(meaningful) == 1 and ASSIGN_NONE_PATTERN.match(meaningful[0].strip()):
        return "VARIABLE_FALLBACK"

    # 检查是否是 raise / re-raise
    if any("raise" in l.strip() for l in meaningful):
        return "RE_RAISE"  # 抛出异常 — 不是静默

    # 其他情况
    return "NEEDS_REVIEW"


def suggest_fix(entry: SilentExceptEntry) -> str:
    """生成修复建议."""
    body_lines = entry.body_lines
    except_stmt = entry.except_stmt

    # 确定 logger 变量名
    logger_var = "logger"
    # 检查文件是否使用 self.logger
    if "self." in except_stmt:
        logger_var = "self.logger"

    if entry.category == "TRUE_SILENT_EMPTY":
        return f'    {logger_var}.warning("静默异常已捕获", exc_info=True)'

    if entry.category == "VARIABLE_FALLBACK":
        return f'    {logger_var}.info("回退到默认值", exc_info=True)'

    if entry.category == "SPECIFIC_EMPTY":
        return f'    {logger_var}.warning("特定异常已捕获", exc_info=True)'

    return '    # TODO: 人工审核此异常处理'


def process_entries(scan_file: Path, prod_only: bool = True) -> list[SilentExceptEntry]:
    data = load_scan_results(scan_file)
    entries = data.get("BROAD_EXCEPT_SILENT", [])
    results = []

    for item in entries:
        file_path = item.get("file", "")
        line_no = item.get("line", 0)
        if not file_path or not line_no:
            continue

        if prod_only and not is_prod_file(file_path):
            continue

        except_stmt, body = read_except_block(file_path, line_no, max_body=10)
        entry = SilentExceptEntry(
            file=normalize_path(file_path),
            line=line_no,
            except_stmt=except_stmt,
            body_lines=body,
        )
        entry.category = classify_except(except_stmt, body)

        if entry.category in ("TRUE_SILENT_EMPTY", "VARIABLE_FALLBACK", "SPECIFIC_EMPTY"):
            entry.fix_suggestion = suggest_fix(entry)

        results.append(entry)

    return results


def print_summary(results: list[SilentExceptEntry]):
    cats = defaultdict(list)
    for e in results:
        cats[e.category].append(e)

    print("\n" + "=" * 70)
    print("静默异常分析报告")
    print("=" * 70)
    print(f"  扫描条目: {len(results)}")
    print()

    descriptions = {
        "TRUE_SILENT_EMPTY": "HIGH — except块为空,吞异常无任何处理",
        "SPECIFIC_EMPTY": "MED — 特定异常但空处理,需人工判断意图",
        "VARIABLE_FALLBACK": "MED — 异常时仅回退默认值,应记录",
        "ALREADY_LOGGED": "OK — 已有日志处理",
        "RE_RAISE": "OK — 重新抛出异常",
        "NEEDS_REVIEW": "?? — 需人工审核",
        "UNCLASSIFIED": "?? — 未能读取",
    }

    for cat in sorted(cats.keys()):
        n = len(cats[cat])
        pct = n / max(len(results), 1) * 100
        desc = descriptions.get(cat, "")
        print(f"  {cat:25s}: {n:4d} ({pct:5.1f}%)  {desc}")

    print()

    actionable = cats.get("TRUE_SILENT_EMPTY", []) + cats.get("VARIABLE_FALLBACK", [])
    if actionable:
        print(f"  可自动修复: {len(actionable)} 处")
        print("  " + "-" * 65)
        for e in actionable[:10]:
            body_preview = e.body_lines[0].strip() if e.body_lines else "(空)"
            print(f"    {e.file}:{e.line}  [{e.category}] {body_preview[:60]}")


def save_report(results: list[SilentExceptEntry], output_path: Path):
    report = {
        "generated_at": "2026-08-02",
        "total_entries": len(results),
        "by_category": {},
        "fixable_entries": [],
    }
    for e in results:
        report["by_category"][e.category] = report["by_category"].get(e.category, 0) + 1
    for e in results:
        if e.category in ("TRUE_SILENT_EMPTY", "VARIABLE_FALLBACK", "SPECIFIC_EMPTY"):
            report["fixable_entries"].append({
                "file": e.file,
                "line": e.line,
                "category": e.category,
                "fix_suggestion": e.fix_suggestion,
            })
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n  详细报告已保存: {output_path}")


def generate_patches(results: list[SilentExceptEntry], output_dir: Optional[Path] = None):
    if output_dir is None:
        output_dir = PATCH_DIR
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    by_file = defaultdict(list)
    for e in results:
        if e.category in ("TRUE_SILENT_EMPTY", "VARIABLE_FALLBACK"):
            by_file[e.file].append(e)

    patches = defaultdict(list)
    for file_path, entries in sorted(by_file.items()):
        resolved = PROJECT_ROOT / file_path
        if not resolved.exists():
            continue
        with open(resolved, encoding="utf-8") as f:
            source_lines = f.readlines()

        for entry in sorted(entries, key=lambda x: x.line):
            idx = entry.line - 1
            if idx < 0 or idx >= len(source_lines):
                continue

            except_line = source_lines[idx]
            except_indent = len(re.match(r"^(\s*)", except_line).group(1))
            body_indent = " " * (except_indent + 4)

            # 找到插入位置: except块体第一行之前
            insert_idx = idx + 1
            # 跳过空白/注释行
            while insert_idx < len(source_lines):
                line = source_lines[insert_idx]
                s = line.strip()
                if s == "" or s.startswith("#"):
                    insert_idx += 1
                    continue
                if s.startswith("pass"):
                    # 替换 pass 为日志语句
                    patches[file_path].append({
                        "line": insert_idx + 1,
                        "action": "replace",
                        "old": source_lines[insert_idx].rstrip("\n"),
                        "new": body_indent + entry.fix_suggestion,
                    })
                else:
                    # 在块体前插入日志语句
                    patches[file_path].append({
                        "line": insert_idx,
                        "action": "insert_before",
                        "new": body_indent + entry.fix_suggestion,
                    })
                break

    total = sum(len(v) for v in patches.values())
    patch_file = output_dir / "fix_suggestions.json"
    with open(patch_file, "w", encoding="utf-8") as f:
        json.dump(dict(patches), f, ensure_ascii=False, indent=2)
    print(f"  补丁已生成: {patch_file} ({total} 条建议)")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="静默异常正态化工具")
    parser.add_argument("--full", action="store_true", help="分析全量")
    parser.add_argument("--fix", action="store_true", help="生成补丁")
    parser.add_argument("--apply", action="store_true", help="应用补丁")
    args = parser.parse_args()

    if args.full:
        scan_file = FULL_SCAN
        prod_only = False
        print("[全量模式]")
    else:
        scan_file = PROD_SCAN
        prod_only = True
        print("[生产模式]")

    if not scan_file.exists():
        print(f"错误: 找不到 {scan_file}")
        return 1

    results = process_entries(scan_file, prod_only=prod_only)
    print_summary(results)

    if args.fix:
        generate_patches(results)

    save_report(results, OUTPUT_REPORT)

    actionable = sum(1 for r in results if r.category in ("TRUE_SILENT_EMPTY", "VARIABLE_FALLBACK"))
    if actionable > 0 and not args.fix:
        print(f"\n  → {actionable} 处可自动修复, 运行: python scripts/fix_silent_except.py --fix")

    return 0


if __name__ == "__main__":
    sys.exit(main())
