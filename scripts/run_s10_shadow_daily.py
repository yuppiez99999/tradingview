#!/usr/bin/env python
"""S10 影子账户每日净值计入 wrapper: 先计算真实净值再计入.

调度: v84_ShadowS10Daily 17:46 周一至周五
流程: compute_s10_nav.py (真实净值) → launch_etf_shadow.py --daily --nav <真实净值>
降级: compute_s10_nav.py 失败时回退到估算值 (与原行为一致)
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable
CONFIG = "shadow_etf_s10_candidate.json"


def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")  # noqa: DTZ005
    print(f"=== S10 影子账户每日净值计入 {now} ===")

    print("[1/2] 计算 S10 真实净值...")
    result = subprocess.run(
        [
            PYTHON,
            str(PROJECT_ROOT / "scripts" / "compute_s10_nav.py"),
            "--output",
            "json",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(PROJECT_ROOT),
        check=False,
    )

    if result.returncode != 0:
        print(f"compute_s10_nav.py 失败 (exit={result.returncode}), 回退到估算值")
        if result.stderr:
            print(result.stderr.strip())
        subprocess.run(
            [
                PYTHON,
                str(PROJECT_ROOT / "scripts" / "launch_etf_shadow.py"),
                "--daily",
                "--config",
                CONFIG,
            ],
            cwd=str(PROJECT_ROOT),
            check=False,
        )
        return

    data = json.loads(result.stdout)
    nav = data["nav"]
    actual_date = data.get("actual_date", "")
    print(f"真实净值: {nav} (数据日期: {actual_date})")

    print("[2/2] 计入影子账户...")
    subprocess.run(
        [
            PYTHON,
            str(PROJECT_ROOT / "scripts" / "launch_etf_shadow.py"),
            "--daily",
            "--config",
            CONFIG,
            "--nav",
            str(nav),
        ],
        cwd=str(PROJECT_ROOT),
        check=False,
    )

    print("=== 完成 ===")


if __name__ == "__main__":
    main()
