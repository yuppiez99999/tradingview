"""Q5 契约: 禁止裸 except:.

契约: 禁止裸 `except:` (无异常类型), 会吞 KeyboardInterrupt/SystemExit 并掩盖真实错误.
      宽泛 `except Exception` 由 T7 门禁 (engineering_debt_gate) 独立管理, 此处只禁裸 except:.

对应门禁: scripts/engineering_debt_gate.py _check_bare_broad_except (T7)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_BARE_EXCEPT_RE = re.compile(r"^\s*except\s*:", re.MULTILINE)

_P0_DIRS = (
    _PROJECT_ROOT,
    _PROJECT_ROOT / "utils",
    _PROJECT_ROOT / "scripts",
    _PROJECT_ROOT / "quant_modules",
)

_SKIP_DIRS = {
    "__pycache__",
    ".venv",
    "qlib_env",
    "node_modules",
    ".git",
    "unsloth_compiled_cache",
    "external",
    "_archive",
}

_SKIP_FILES = {"__init__.py"}


def _scan_bare_except() -> list[tuple[Path, int]]:
    """扫描 P0 目录下 .py 文件的裸 except: 站点."""
    hits: list[tuple[Path, int]] = []
    for root in _P0_DIRS:
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            if path.name in _SKIP_FILES:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in _BARE_EXCEPT_RE.finditer(text):
                lineno = text.count("\n", 0, m.start()) + 1
                hits.append((path, lineno))
    return hits


def test_no_bare_except_in_p0_code() -> None:
    """P0 代码不得含裸 `except:` (无异常类型)."""
    hits = _scan_bare_except()
    if hits:
        sample = "\n".join(f"  {p.relative_to(_PROJECT_ROOT)}:{ln}" for p, ln in hits[:10])
        pytest.fail(
            f"Q5 违约: 发现 {len(hits)} 处裸 except: (会吞 KeyboardInterrupt/SystemExit):\n{sample}"
        )
