"""R10 拖债清偿 — ai_hedge_fund/ 下 30 处裸 except Exception 精确化.

按行号精确替换 except Exception → except (具体类型), 不改变行数。
执行后 ruff BLE001 在 ai_hedge_fund/ + alpha_factor/ + notify.py 应归零。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "quant_modules" / "ai_hedge_fund"

MAPPING: dict[tuple[str, int], str] = {
    ("agents/charlie_munger.py", 724): "(TypeError, ValueError)",
    ("agents/hedge_analyst.py", 172): "(RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, TimeoutError, ImportError)",
    ("agents/rakesh_jhunjhunwala.py", 577): "(TypeError, ValueError, AttributeError, ZeroDivisionError)",
    ("agents/risk_manager.py", 84): "(ValueError, TypeError, KeyError)",
    ("agents/valuation.py", 395): "(TypeError, ValueError, ZeroDivisionError)",
    ("data_adapter.py", 200): "(ValueError, TypeError, KeyError, OSError, TimeoutError, ImportError)",
    ("data_adapter.py", 240): "(ValueError, TypeError, KeyError, AttributeError, OSError, TimeoutError)",
    ("data_adapter.py", 336): "(TypeError, ValueError, KeyError)",
    ("data_adapter.py", 343): "(ValueError, TypeError, KeyError, AttributeError, OSError, TimeoutError)",
    ("data_adapter.py", 383): "(ValueError, TypeError, KeyError, AttributeError, OSError, TimeoutError)",
    ("data_adapter.py", 499): "(TypeError, ValueError, KeyError, AttributeError)",
    ("data_adapter.py", 507): "(ValueError, TypeError, KeyError, AttributeError, OSError, TimeoutError)",
    ("data_adapter.py", 569): "(ValueError, TypeError, KeyError, AttributeError, OSError, TimeoutError, ImportError)",
    ("data_adapter.py", 621): "(TypeError, ValueError, KeyError, AttributeError)",
    ("data_adapter.py", 635): "(ValueError, TypeError, KeyError, AttributeError, OSError, TimeoutError, ImportError)",
    ("data_adapter.py", 663): "(OSError, TimeoutError, ImportError, ValueError, TypeError)",
    ("debate_layer.py", 135): "(TypeError, ValueError, AttributeError)",
    ("debate_layer.py", 215): "(RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, TimeoutError, ImportError)",
    ("debate_layer.py", 304): "(RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, TimeoutError, ImportError)",
    ("debate_layer.py", 411): "(RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, TimeoutError)",
    ("debate_layer.py", 612): "(OSError, TypeError, ValueError)",
    ("debate_layer.py", 727): "(ImportError, TypeError, ValueError, KeyError, AttributeError)",
    ("llm_rate_limiter.py", 390): "(RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, TimeoutError)",
    ("memory_reflection.py", 250): "(ValueError, TypeError, KeyError, AttributeError)",
    ("memory_reflection.py", 386): "(TypeError, ValueError, KeyError, AttributeError)",
    ("memory_reflection.py", 453): "(ImportError, OSError, TypeError, ValueError, AttributeError)",
    ("memory_reflection.py", 513): "(ValueError, TypeError, KeyError, AttributeError, OSError, TimeoutError)",
    ("utils/llm.py", 72): "(RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError, TimeoutError)",
    ("utils/llm.py", 157): "(TypeError, AttributeError, ValueError)",
    ("utils/ollama.py", 87): "(OSError, TypeError, ValueError)",
}

PAT_AS = re.compile(r"^(\s*)except\s+Exception\s+as\s+(\w+)\s*:\s*(.*)$")
PAT_BARE = re.compile(r"^(\s*)except\s+Exception\s*:\s*(.*)$")


def main() -> int:
    changed = 0
    skipped: list[str] = []
    for (rel, lineno), types_str in MAPPING.items():
        fpath = BASE / rel
        if not fpath.exists():
            skipped.append(f"{rel}:{lineno} (file missing)")
            continue
        lines = fpath.read_text(encoding="utf-8").splitlines(keepends=True)
        idx = lineno - 1
        if idx >= len(lines):
            skipped.append(f"{rel}:{lineno} (line out of range)")
            continue
        line = lines[idx]
        m = PAT_AS.match(line)
        if m:
            indent, var, tail = m.groups()
            new_line = f"{indent}except {types_str} as {var}:{tail}\n" if line.endswith("\n") else f"{indent}except {types_str} as {var}:{tail}"
            lines[idx] = new_line
            fpath.write_text("".join(lines), encoding="utf-8")
            changed += 1
            continue
        m = PAT_BARE.match(line)
        if m:
            indent, tail = m.groups()
            new_line = f"{indent}except {types_str}:{tail}\n" if line.endswith("\n") else f"{indent}except {types_str}:{tail}"
            lines[idx] = new_line
            fpath.write_text("".join(lines), encoding="utf-8")
            changed += 1
            continue
        skipped.append(f"{rel}:{lineno} (not except Exception: {line.rstrip()})")

    print(f"R10 拖债清偿: {changed} 处精确化, {len(skipped)} 处跳过")
    for s in skipped:
        print(f"  SKIP {s}")
    return 0 if not skipped else 1


if __name__ == "__main__":
    sys.exit(main())
