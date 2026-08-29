"""生成 ruff 增量门禁基线 (G-1, 2026-08-09)

冻结当前全仓库 ruff 违规计数, 作为增量门禁的对照基线。
- 受控规则集 (enforced): E,F,W,B,C90,I,N,UP,T,BLE  (排除 ANN, 因其 12500+ 条存量, 项目尚未全面注解)
- 每文件违规计数写入 reports/ruff_baseline.json
- 同时记录阻断子集 (blocking): F,B,N,BLE,T 的每文件计数

运行:
    python scripts/ruff_baseline_gen.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = ROOT / "reports" / "ruff_baseline.json"

# 受控规则集 (不含 ANN)
ENFORCED = "E,F,W,B,C90,I,N,UP,T,BLE"
# 阻断子集: 任何新增命中即阻断 (不依赖基线豁免)
BLOCKING = "F,B,N,BLE,T"


def _run_ruff(rules: str) -> str:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--select",
            rules,
            "--output-format",
            "concise",
            "--no-cache",
            ".",
        ],
        cwd=str(ROOT),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.stdout or ""


def _count_per_file(output: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    # concise 格式: path:line:col: CODE message
    pat = re.compile(r"^([^\s:]+(?:\\[^\s:]+)*):\d+:\d+:")
    for line in output.splitlines():
        m = pat.match(line)
        if m:
            f = m.group(1)
            counts[f] = counts.get(f, 0) + 1
    return counts


def main() -> int:
    enforced_out = _run_ruff(ENFORCED)
    blocking_out = _run_ruff(BLOCKING)
    enforced_counts = _count_per_file(enforced_out)
    blocking_counts = _count_per_file(blocking_out)

    baseline = {
        "enforced_rules": ENFORCED,
        "blocking_rules": BLOCKING,
        "total_enforced": sum(enforced_counts.values()),
        "total_blocking": sum(blocking_counts.values()),
        "per_file_enforced": enforced_counts,
        "per_file_blocking": blocking_counts,
    }
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(
        json.dumps(baseline, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"基线已生成: {BASELINE_PATH}")
    print(f"  enforced 规则 [{ENFORCED}] 总违规: {baseline['total_enforced']}")
    print(f"  blocking 规则 [{BLOCKING}] 总违规: {baseline['total_blocking']}")
    print(f"  受监控文件数: {len(enforced_counts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
