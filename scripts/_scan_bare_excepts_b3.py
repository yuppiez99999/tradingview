"""批次3 探索: 扫描生产代码中可具体化的 except Exception (信号词启发).

输出 CANDIDATE（无 # fail-safe / # noqa 注释）及信号建议异常类型，供人工挑选精确化目标。
复用 scripts/_r10_refine_bare_excepts.py 风格的精确化脚本落盘。
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCAN = [
    "utils", "ai", "cli", "scripts", "quant_modules", "core", "data", "reporting",
    "nlp", "cache", "realtime_monitor", "config", "cloud",
]
SKIP_DIRS = {
    "external", "docs", "archive", "_archive", "v8.3_institutional", "research",
    "ifind-finance-data", "mobius_addon", "mlruns", "backtests", "tests",
    "node_modules", ".git", ".venv", "__pycache__",
}

SIGNALS = [
    ("json.loads", "json.JSONDecodeError"),
    ("json.load(", "json.JSONDecodeError"),
    ("json.dump", "TypeError"),
    ("int(", "ValueError"),
    ("float(", "ValueError"),
    ("Decimal(", "decimal.InvalidOperation"),
    ("open(", "OSError"),
    ("read_text", "OSError"),
    ("read_bytes", "OSError"),
    ("write_text", "OSError"),
    ("yaml.safe_load", "yaml.YAMLError"),
    ("yaml.load", "yaml.YAMLError"),
    ("importlib.import_module", "(ImportError, ModuleNotFoundError)"),
    ("__import__", "(ImportError, ModuleNotFoundError)"),
    ("requests.get", "requests.RequestException"),
    ("requests.post", "requests.RequestException"),
    ("httpx.", "httpx.HTTPError"),
    ("os.environ", "KeyError"),
    ("getenv", "KeyError"),
    ("pd.read", "pd.errors.ParserError"),
    ("np.load", "OSError"),
    ("pickle.load", "(pickle.UnpicklingError, OSError)"),
    ("subprocess", "(subprocess.SubprocessError, OSError)"),
]


def scan_file(fpath: Path) -> list[str]:
    try:
        src = fpath.read_text(encoding="utf-8")
        tree = ast.parse(src)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []
    lines = src.splitlines()
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        try_lines = "\n".join(lines[node.lineno - 1: node.end_lineno])
        for h in node.handlers:
            if not (isinstance(h.type, ast.Name) and h.type.id == "Exception"):
                continue
            h_line = lines[h.lineno - 1]
            if "# fail-safe" in h_line or "# noqa" in h_line or "failsafe" in h_line.lower():
                continue
            found = []
            for sig, exc in SIGNALS:
                if sig in try_lines and exc not in found:
                    found.append(exc)
            if found:
                out.append(f"{fpath.relative_to(ROOT)}:{h.lineno} -> {found}")
                out.append(f"    {h_line.strip()}")
    return out


def main() -> int:
    results: list[str] = []
    for d in SCAN:
        base = ROOT / d
        if not base.exists():
            continue
        for fpath in base.rglob("*.py"):
            if any(part in SKIP_DIRS for part in fpath.parts):
                continue
            results.extend(scan_file(fpath))
    for fpath in ROOT.glob("*.py"):
        if fpath.name in {"_r10_refine_bare_excepts.py", "_scan_bare_excepts_b3.py"}:
            continue
        results.extend(scan_file(fpath))
    print(f"批次3 候选: {len(results) // 2} 处")
    for r in results:
        print(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
