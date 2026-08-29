#!/usr/bin/env python
"""R10 技术债逐处精确化工具 (AST 驱动).

将 `except Exception` (配 # fail-safe / # noqa: BLE001) 按 try 块体上下文
收窄为具体异常族, 并移除 noqa 注释; 对真正需宽捕获的顶层 (日志写/清理)
保留 `except Exception` 并登记为隔离豁免.

用法:
    python scripts/_refine_failsafe_excepts.py <file> [--check]
    --check: 只报告将做的改动, 不落盘
"""

from __future__ import annotations

import argparse
import re
import sys
from ast import (
    ExceptHandler,
    Try,
    parse,
    walk,
)
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 原 fail-safe 标记
_FS_RE = re.compile(r"#.*(?:fail-safe|noqa:\s*BLE001)", re.IGNORECASE)
_NOQA_BLE = re.compile(r"\s*#\s*noqa:\s*BLE001\b.*", re.IGNORECASE)
_FS_TAIL = re.compile(
    r"\s*#.*(?:fail-safe|模块 fail-safe|待后续精确化).*$", re.IGNORECASE
)

# try 体信号 -> 异常族
_IMPORT_HINTS = ("import ", "from ")
_NET_HINTS = (
    "request",
    "http",
    "urlopen",
    "url",
    "fetch",
    "get(",
    "post(",
    "source_health",
    "read_text",
    "read_csv",
    "json.load",
    "json.dumps",
    "loads(",
    "pd.read",
    "open(",
    "mkdir",
    "connect",
    "client",
    "get_history",
    "get_realtime",
    "to_datetime",
    "get_macro",
    "get_risk",
    "get_news",
)
_PROBE_HINTS = (
    "is_ready",
    "test_connection",
    "return None",
    "return False",
    "return {}",
)

# 顶层清理/日志写: 必须保留宽捕获的豁免标记 (改写后)
# 仅明确清理语义 (atexit.register / sys.exit 包裹) 才豁免, 避免误吞数据层异常
_SAFE_KEEP_RE = re.compile(
    r"(atexit\.register|sys\.exit|os\._exit|atexit\b)", re.IGNORECASE
)


def _infer_exception_family(try_src: str) -> tuple[str, bool]:
    """根据 try 块体源码推断异常族. 返回 (异常表达式, 是否需保留宽捕获)."""
    src = try_src
    # 顶层清理/日志写失败 -> 保留宽捕获
    if _SAFE_KEEP_RE.search(src):
        return "Exception", True
    # 导入探测
    stripped = src.strip()
    if stripped.startswith("import ") or stripped.startswith("from "):
        return "(ImportError, AttributeError)", False
    first_line = src.splitlines()[0].strip() if src.splitlines() else ""
    has_import = (
        first_line.startswith("import ")
        or first_line.startswith("from ")
        or ("importlib.import_module" in src or "import " in src)
    )
    net = any(h in src for h in _NET_HINTS)
    probe = any(h in src for h in _PROBE_HINTS)
    if has_import and not net and not probe:
        return "(ImportError, AttributeError)", False
    if net and not probe:
        return (
            "(ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError)",
            False,
        )
    if probe and not net:
        return "(AttributeError, TypeError, ValueError, OSError)", False
    if net and probe:
        return (
            "(ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError)",
            False,
        )
    # 默认: 收窄到最常见的几类 (仍比 Exception 窄)
    return "(ValueError, TypeError, KeyError, AttributeError, OSError)", False


def refine_file(path: Path, check: bool = False) -> list[str]:
    """精确化单个文件. 返回改动说明列表."""
    text = path.read_text(encoding="utf-8", errors="replace")
    tree = parse(text)
    changes: list[str] = []

    # 收集所有 try 语句的 except Exception handler 位置 (按行)
    targets: list[tuple[int, ExceptHandler, Try]] = []
    for node in walk(tree):
        if isinstance(node, Try):
            for h in node.handlers:
                if h.type is None:
                    continue
                # 仅处理 `except Exception`
                tname = getattr(h.type, "id", None)
                if tname != "Exception":
                    continue
                # 该 handler 行需带 fail-safe / noqa 标记 (在源码中)
                targets.append((h.lineno, h, node))

    if not targets:
        return changes

    lines = text.splitlines()
    # 从后往前改, 避免行号漂移
    new_lines = list(lines)
    for lineno, handler, try_node in sorted(targets, key=lambda x: x[0], reverse=True):
        src_line = lines[lineno - 1]
        if not _FS_RE.search(src_line):
            continue  # 非 fail-safe 宽捕获, 跳过 (不碰)
        # try 块体源码
        try_src_lines = lines[try_node.lineno - 1 : handler.lineno - 1]
        try_src = "\n".join(try_src_lines)
        family, keep = _infer_exception_family(try_src)

        # 解析原行: except Exception as e:  # noqa: BLE001  # fail-safe ...
        m = re.match(r"(\s*)except\s+Exception(\s+as\s+\w+)?\s*(:\s*)(.*)$", src_line)
        if not m:
            continue
        indent, as_clause, colon, tail = m.groups()
        # 清理 tail 中的 noqa / fail-safe
        tail_clean = _NOQA_BLE.sub("", tail)
        tail_clean = _FS_TAIL.sub("", tail_clean)
        tail_clean = tail_clean.rstrip()
        if keep:
            # 保留宽捕获但登记豁免
            new_tail = "  # noqa: BLE001  # 顶层清理/日志, 必须吞掉所有异常"
            new_line = f"{indent}except Exception{as_clause or ''}:{new_tail}"
        else:
            new_line = f"{indent}except {family}{as_clause or ''}:{('  ' + tail_clean) if tail_clean else ''}"
        new_lines[lineno - 1] = new_line
        changes.append(f"L{lineno}: {src_line.strip()}  ->  {new_line.strip()}")

    if not check and changes:
        path.write_text("\n".join(new_lines), encoding="utf-8")
    return changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="+")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    total = 0
    for p in args.path:
        fp = Path(p)
        if not fp.exists():
            fp = _PROJECT_ROOT / p
        if not fp.exists():
            print(f"[SKIP] 不存在: {p}")
            continue
        ch = refine_file(fp, check=args.check)
        if ch:
            print(f"\n=== {fp.name} ({len(ch)} 处) ===")
            for c in ch:
                print(f"  {c}")
            total += len(ch)
    print(f"\n总计 {total} 处 {'[CHECK]' if args.check else '[APPLIED]'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
