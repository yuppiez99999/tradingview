#!/usr/bin/env python
"""裸宽捕获审计器 (AST, R10/T6 渐进清理配套).

扫描目录下所有 `except Exception` / `except BaseException` 且**不带**
`# fail-safe` / `# noqa: BLE001` 标记的站点 (即工程债 T7 / R10 口径),
并为每个站点打印 try 块体上下文 + 推断出的安全精确化异常族。

判定原则:
  - try 体仅含纯标准库文件读 / json 解析 / float 转换 / dict 导航 / dataclass
    构造的, 归为 "可安全收窄" 并给出推荐异常族;
  - 含外部调用 (requests/urllib/client/http/runner/engine/provider/_redis 等)
    的站点无法盲收窄, 归为 "需人工审查 (外部调用)", 仅提示。

用法:
    python scripts/audit_bare_except_sites.py [dir ...] [--json out.json]
    --json: 同时输出机器可读 JSON (支持后续批量精确化脚本消费)

这是渐进清理辅助工具, 不修改任何源码。
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DIRS = [
    "utils",
    "scripts",
    "quant_modules",
    "ai_decision",
    "v8.3_institutional",
    "15_每日工作流",
]

_BARE_RE = re.compile(r"^\s*except\s+(Exception|BaseException)\b")
_MARKED_RE = re.compile(r"#.*(?:fail-safe|noqa:\s*BLE001)", re.IGNORECASE)

# 推断: try 块体若命中这些外部调用信号, 则不可盲收窄
_EXTERNAL_SIG = re.compile(
    r"(requests\.|urlopen|httpx|urllib\.|_redis\.|\.run_backtest\(|"
    r"\.get_history\(|\.get_realtime\(|provider\.|\.client\.|\.execute\(|"
    r"\.fetch\(|client\b|run_engine|\.send\(|http)",
    re.IGNORECASE,
)

# 纯标准库信号 -> 建议异常族
_PURE_EXC = {
    "float": "(ValueError, TypeError)",
    "json": "(OSError, ValueError, TypeError, KeyError, AttributeError)",
    "fs": "(OSError, TypeError)",
}
_PURE_SIG = [
    (re.compile(r"float\("), "float"),
    (re.compile(r"json\.(load|loads|dump|dumps)"), "json"),
    (re.compile(r"(open\(|\.read_text\(|\.read_bytes\(|\.write_text\(|mkdir\()"), "fs"),
]


def _try_body_src(node: ast.Try, lines: list[str]) -> str:
    return "\n".join(lines[node.lineno - 1 : _first_handler_lineno(node, lines) - 1])


def _first_handler_lineno(node: ast.Try, lines: list[str]) -> int:
    return node.handlers[0].lineno if node.handlers else node.end_lineno


def _infer(body: str) -> str:
    """推断安全收窄的异常族, 或 None (需人工/外部调用)."""
    if _EXTERNAL_SIG.search(body):
        return None  # 外部调用, 不可盲收窄
    hits = []
    for rx, kind in _PURE_SIG:
        if rx.search(body):
            hits.append(_PURE_EXC[kind])
    if not hits:
        return None
    # 并集: 若命中多种纯操作, 取最全的 json 集 (涵盖 fs/float 情形)
    if any(h == _PURE_EXC["json"] for h in hits):
        return _PURE_EXC["json"]
    if any(h == _PURE_EXC["fs"] for h in hits) and any(
        h == _PURE_EXC["float"] for h in hits
    ):
        return "(OSError, TypeError, ValueError)"
    if hits:
        return hits[0]
    return None


def scan_files(files: list[Path]) -> list[dict]:
    records: list[dict] = []
    for f in files:
        if f.name in ("ci_integrity_check.py", "audit_bare_except_sites.py"):
            continue
        try:
            src = f.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(src)
        except SyntaxError:
            continue
        lines = src.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            for h in node.handlers:
                if h.type is None:
                    continue
                if getattr(h.type, "id", "") not in ("Exception", "BaseException"):
                    continue
                line = lines[h.lineno - 1]
                if _MARKED_RE.search(line):
                    continue  # fail-safe / noqa: BLE001 (T6 口径, 非本次范围)
                body = _try_body_src(node, lines).strip()
                rec = {
                    "file": str(f.relative_to(_PROJECT_ROOT)),
                    "line": h.lineno,
                    "except": line.strip(),
                    "has_as": bool(h.name),
                    "suggest": _infer(body),
                    "body_first": "\n".join(body.splitlines()[:4]),
                }
                records.append(rec)
    records.sort(key=lambda r: (r["file"], r["line"]))
    return records


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="*", default=_DEFAULT_DIRS)
    ap.add_argument("--json", dest="json_path")
    args = ap.parse_args()

    files: list[Path] = []
    for d in args.dirs:
        p = Path(d) if Path(d).is_absolute() else _PROJECT_ROOT / d
        if p.is_dir():
            files.extend(sorted(p.rglob("*.py")))
        elif p.exists():
            files.append(p)
    files = sorted(set(files))

    records = scan_files(files)
    total = len(records)
    safe = [r for r in records if r["suggest"]]
    manual = [r for r in records if not r["suggest"]]

    print(f"裸宽捕获总数: {total}  (候选启发式建议(需人工复核): {len(safe)} / 需人工: {len(manual)})")
    for r in safe:
        print(
            f"[候选] {r['file']}:{r['line']} -> {r['suggest']}\n"
            f"         {r['except']}"
        )
    for r in manual:
        print(f"[人工  ] {r['file']}:{r['line']}\n         {r['except']}")

    if args.json_path:
        (Path(args.json_path)).write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nJSON 已写出: {args.json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
