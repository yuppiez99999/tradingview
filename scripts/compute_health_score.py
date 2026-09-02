"""System Health Score 计算入口 (Production Edition T2, 2026-09-02).

每交易日 17:05 (先于 17:10 状态报告, 使其可读取同日评分) 由计划任务
System_HealthScore 调用, 幂等落盘 (同日重跑覆盖):
  reports/health_score/health_score_{date}.json

用法:
  python scripts/compute_health_score.py                      # 当日
  python scripts/compute_health_score.py --date 2026-09-01    # 指定日期
  python scripts/compute_health_score.py --print              # 打印明细
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.health.score_engine import compute_health_score  # noqa: E402

_BACKUP_ROOT = Path(r"D:\QuantBackup\28-quant")


def main() -> int:
    parser = argparse.ArgumentParser(description="System Health Score 聚合")
    parser.add_argument("--date", default=None, help="评估日期 YYYY-MM-DD (默认今日)")
    parser.add_argument("--print", action="store_true", help="打印评分明细")
    args = parser.parse_args()

    date = args.date or datetime.now().strftime("%Y-%m-%d")
    result = compute_health_score(_PROJECT_ROOT, date, backup_root=_BACKUP_ROOT)

    out_dir = _PROJECT_ROOT / "reports" / "health_score"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"health_score_{date}.json"
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[OK] {result['status']} {result['total_score']}/100 -> {out_path}")
    if args.print:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
