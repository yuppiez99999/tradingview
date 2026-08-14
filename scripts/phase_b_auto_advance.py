#!/usr/bin/env python3
"""Phase B Auto Advance Scheduler.

Daily check: if observation period is complete, run phase_b_progressive_enabler.py --advance.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "phase_b_progressive_enabler.py"
LOG_DIR = PROJECT_ROOT / "reports" / "evolution" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "phase_b_auto_advance.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def run_cmd(args: list[str]) -> str:
    result = subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT_PATH)] + args,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = result.stdout.strip()
    if result.stderr.strip():
        out += "\nSTDERR:\n" + result.stderr.strip()
    return out


def parse_remaining_days(text: str) -> int | None:
    import re

    m = re.search(r"observation remaining:\s*(\d+)\s*day", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)/(\d+)\s*天", text)
    if m:
        completed = int(m.group(1))
        required = int(m.group(2))
        return max(0, required - completed)
    return None


def main() -> int:
    logger.info("=== Phase B auto check start ===")

    if not SCRIPT_PATH.exists():
        logger.error("script not found: %s", SCRIPT_PATH)
        return 1

    check_output = run_cmd(["--check"])
    logger.info("CHECK OUTPUT:\n%s", check_output)

    remaining = parse_remaining_days(check_output)
    if remaining is None:
        logger.warning("cannot parse remaining days, skip auto advance")
        remaining = 1

    logger.info("remaining observation days: %s", remaining)

    if remaining > 0:
        logger.info("observation not complete, skip advance")
        logger.info("=== Phase B auto check finished (no action) ===")
        return 0

    logger.info("observation complete, running --advance ...")
    advance_output = run_cmd(["--advance"])
    logger.info("ADVANCE OUTPUT:\n%s", advance_output)

    if "[OK]" in advance_output:
        logger.info("SUCCESS: advanced to next stage")
    elif "[WAIT]" in advance_output:
        logger.info("WAIT: conditions not met")
    elif "[DONE]" in advance_output:
        logger.info("DONE: already at final stage")
    else:
        logger.warning("unexpected advance output")

    logger.info("=== Phase B auto check finished ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
