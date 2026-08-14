#!/usr/bin/env python3
"""Phase B Observation Period Bypass (TEST ONLY).

WARNING: This script bypasses the 21-day observation safety constraint.
Use ONLY for local testing of --advance behavior. Do NOT use in production.

Usage:
    python scripts/phase_b_force_observation_complete.py
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATUS_PATH = PROJECT_ROOT / "reports" / "evolution" / "phase_b_status.json"


def main() -> int:
    if not STATUS_PATH.exists():
        print(f"ERROR: {STATUS_PATH} not found")
        return 1

    with open(STATUS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    old_value = data.get("observation_days_completed", 0)
    data["observation_days_completed"] = 21
    data.setdefault("notes", [])
    data["notes"].append(
        f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] "
        f"TEST ONLY: observation_days_completed forced from {old_value} to 21 "
        f"to validate --advance behavior"
    )

    with open(STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"OK: observation_days_completed = {data['observation_days_completed']}")
    print(f"    notes appended with TEST ONLY marker")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
