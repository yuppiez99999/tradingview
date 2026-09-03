"""R10/T6 裸宽捕获精确化 批次3 (AUTO-1): 再精确化 10 处可具体化的 except Exception.

复用 scripts/_r10_refine_bare_excepts.py 风格，按行号精确替换 except Exception -> except (具体类型)。
选中标准（与批次1/2 一致「保守显式元组」策略，不缩小捕获范围）：
  - 仅选非核心执行路径（配置加载 / 实时监控 / 报告 / 数据获取），且 try 块操作可 100% 判定异常类型。
  - 保留带 # fail-safe 的合法降级（本批次未触碰）。
  - 边缘异常（ZeroDivisionError / 等）一并纳入，避免漏捕获改变行为。
验收: ruff --select BLE001 不新增 + python -m py_compile 通过。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

MAPPING: dict[tuple[str, int], str] = {
    ("utils/prompt_registry.py", 137): "(OSError, yaml.YAMLError)",
    ("utils/auto_hedge_rebalance/engine.py", 159): "(OSError, yaml.YAMLError)",
    ("realtime_monitor/watch_my_positions.py", 170): "requests.RequestException",
    ("realtime_monitor/watch_my_positions.py", 197): "(ValueError, TypeError, ZeroDivisionError)",
    ("realtime_monitor/watch_my_universe.py", 212): "requests.RequestException",
    ("realtime_monitor/watch_my_universe.py", 88): "(ValueError, TypeError, ZeroDivisionError)",
    ("realtime_monitor/watch_my_universe.py", 239): "(ValueError, TypeError, ZeroDivisionError)",
    ("utils/value_investing/ashare_data.py", 165): "(InvalidOperation, TypeError, ZeroDivisionError)",
    ("reporting/pnl_calculator.py", 333): "(OSError, json.JSONDecodeError, ValueError, TypeError, KeyError)",
    ("reporting/pnl_calculator.py", 364): "(OSError, json.JSONDecodeError, ValueError, TypeError, KeyError)",
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
            new_line = (
                f"{indent}except {types_str} as {var}:{tail}\n"
                if line.endswith("\n")
                else f"{indent}except {types_str} as {var}:{tail}"
            )
            lines[idx] = new_line
            fpath.write_text("".join(lines), encoding="utf-8")
            changed += 1
            continue
        m = PAT_BARE.match(line)
        if m:
            indent, tail = m.groups()
            new_line = (
                f"{indent}except {types_str}:{tail}\n"
                if line.endswith("\n")
                else f"{indent}except {types_str}:{tail}"
            )
            lines[idx] = new_line
            fpath.write_text("".join(lines), encoding="utf-8")
            changed += 1
            continue
        skipped.append(f"{rel}:{lineno} (not except Exception: {line.rstrip()})")

    print(f"R10/T6 批次3: {changed} 处精确化, {len(skipped)} 处跳过")
    for s in skipped:
        print(f"  SKIP {s}")
    return 0 if not skipped else 1


if __name__ == "__main__":
    sys.exit(main())
