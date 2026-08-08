#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""量化专项静态检查 (CODE_REVIEW_PLAN Task 3.1).

对应 docs/CODE_REVIEW_STANDARD.md §6 量化专项清单中可机检的部分。
当前全部规则默认仅 warning (exit 0), 经 2 周试点无误报后可在 .pre-commit-config.yaml
与 ci.yml 中升为阻断。

可机检规则:
  Q1 信号执行分离: ms_strategy/src/execution/** 不得直接 import/调用 utils.notify
  Q2 回测未来函数: 回测文件禁止正向位移 shift(-N) 与 future_ 命名 (warn)
  Q3 金融计算精度: 价格取整禁止裸 float() / int() 截断 (建议 round/Decimal, warn)

用法:
  python scripts/quant_review_lint.py [files ...]   # 指定文件
  python scripts/quant_review_lint.py --all         # 全仓库扫描
  python scripts/quant_review_lint.py --strict      # 命中即 exit 1 (试点后启用)
兼容 Python 3.8。
"""
import argparse
import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

EXECUTION_GLOB = re.compile(r"ms_strategy[\\/]src[\\/](execution|backtest|data)[\\/]")
NOTIFY_CALL = re.compile(r"\butils\.notify\b|\bfrom\s+utils\.notify\b|\bimport\s+notify\b")
FUTURE_FN = re.compile(r"\.shift\(\s*-")          # pandas 正向位移 = 未来函数
# 仅匹配真正的未来函数命名, 排除 __future__ / lookahead(防前视的正确命名) / validate_no_lookahead
FUTURE_NAME = re.compile(r"(?<!_)future_[a-z]|tomorrow_close|next_day_(return|close|open)|lookahead_factor", re.IGNORECASE)
# 仅匹配"裸截断取整" (int(price)/int(close) 当取整用), 排除 float() 类型转换与 safe_float 等通用转换
BARE_CAST = re.compile(r"\bint\(\s*(price|close|open|high|low|bid|ask|px|value|spot)\b", re.IGNORECASE)

SKIP_DIRS = {"_archive", "_archive_dead_code", ".venv", "qlib_env", "node_modules", ".git", "__pycache__", ".mypy_cache"}


def iter_py(files):
    if files:
        for f in files:
            p = Path(f)
            if p.suffix == ".py":
                yield p
    else:
        for p in REPO.rglob("*.py"):
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            yield p


def _is_ignored(line: str) -> bool:
    """行内豁免: 含 quant-lint-ignore 注释则跳过该行所有规则."""
    return "quant-lint-ignore" in line


def check_q1(path: Path, src: str) -> list:
    """Q1 信号执行分离."""
    hits = []
    if EXECUTION_GLOB.search(str(path).replace("/", "\\")):
        for i, line in enumerate(src.splitlines(), 1):
            if NOTIFY_CALL.search(line):
                hits.append((i, "Q1 信号执行分离: execution 层禁止直接调用 utils.notify", line.strip()))
    return hits


def check_q2(path: Path, src: str) -> list:
    """Q2 回测未来函数 (仅 warn)."""
    hits = []
    is_backtest = "backtest" in str(path).replace("\\", "/")
    is_signal = "signal" in str(path).lower()
    for i, line in enumerate(src.splitlines(), 1):
        if _is_ignored(line):
            continue
        if FUTURE_FN.search(line) or FUTURE_NAME.search(line):
            # 标签构造 (target = ...shift(-1)) 是监督学习标签, 非特征泄漏, 豁免
            if "target" in line and "shift(-1)" in line:
                continue
            # 仅对回测/信号文件敏感
            if is_backtest or is_signal:
                hits.append((i, "Q2 疑似未来函数/前视偏差 (warn)", line.strip()))
    return hits


def check_q3(path: Path, src: str) -> list:
    """Q3 金融计算精度 (仅 warn)."""
    hits = []
    for i, line in enumerate(src.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#") or _is_ignored(line):
            continue  # 跳过注释行与豁免行, 避免文本误匹配
        if BARE_CAST.search(line) and "round(" not in line and "Decimal" not in line:
            hits.append((i, "Q3 价格计算疑似裸 int()/float() 截断 (warn, 建议 round/Decimal)", line.strip()))
    return hits


def main(argv=None):
    ap = argparse.ArgumentParser(description="量化专项静态检查")
    ap.add_argument("files", nargs="*", help="待检查文件 (默认全仓库)")
    ap.add_argument("--all", action="store_true", help="全仓库扫描")
    ap.add_argument("--strict", action="store_true", help="命中即 exit 1 (试点后启用)")
    args = ap.parse_args(argv)

    mode = "全仓库" if (args.all or not args.files) else f"{len(args.files)} 文件"
    print(f"[quant_review_lint] 扫描模式: {mode}")
    total = 0
    for p in iter_py(args.files if (args.files and not args.all) else ()):
        try:
            src = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            ast.parse(src)
        except SyntaxError:
            continue  # 语法错误交给 pyflakes/ruff
        hits = check_q1(p, src) + check_q2(p, src) + check_q3(p, src)
        for ln, rule, text in hits:
            total += 1
            print(f"  {p.relative_to(REPO)}:{ln} [{rule}] {text[:80]}")
    print(f"[quant_review_lint] 共 {total} 条量化专项提示 (当前 warn)")
    # 默认 warn 不阻断; --strict 才阻断
    return 1 if (args.strict and total > 0) else 0


if __name__ == "__main__":
    sys.exit(main())
