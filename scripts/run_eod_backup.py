"""EOD 备份 CLI (Production Edition T4, 2026-09-02).

每交易日 17:30 (EOD 链尾) 由计划任务 EOD_Backup 调用.
目的地: 工程内 backups/28-quant/ (2026-09-05 起自包含; 原 D:\\QuantBackup 历史已并入, QUANT_BACKUP_ROOT 可覆盖异盘).

用法:
  python scripts/run_eod_backup.py backup [--date YYYY-MM-DD]   # 备份 (默认今日)
  python scripts/run_eod_backup.py verify [--date YYYY-MM-DD]   # 校验最新/指定日
  python scripts/run_eod_backup.py restore <target_dir> [--date YYYY-MM-DD] [--items a,b]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from utils.datetime_utils import now_bj

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
# 2026-09-05: 默认备份到工程内 backups/28-quant (自包含/换机便携); 可用 QUANT_BACKUP_ROOT 覆盖 (保留异盘部署能力)
_BACKUP_ROOT = Path(
    os.environ.get("QUANT_BACKUP_ROOT") or (_PROJECT_ROOT / "backups" / "28-quant")
)
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.backup.eod_backup import EodBackup, restore_backup, verify_backup  # noqa: E402


def _resolve_date(date_arg: str | None) -> str:
    return date_arg or now_bj().strftime("%Y-%m-%d")


def _find_day_dir(date: str) -> Path:
    day_dir = _BACKUP_ROOT / date
    if not day_dir.is_dir():
        # fallback: 最新备份目录
        if _BACKUP_ROOT.is_dir():
            dated = [d for d in _BACKUP_ROOT.iterdir() if d.is_dir() and len(d.name) == 10 and d.name[4] == "-"]
            if dated:
                day_dir = max(dated, key=lambda d: d.name)
    return day_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="EOD 备份 (工程内 backups/ + 90 天滚动)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_bak = sub.add_parser("backup", help="执行备份 (幂等, 同日重跑覆盖)")
    p_bak.add_argument("--date", default=None)
    p_ver = sub.add_parser("verify", help="校验备份完整性")
    p_ver.add_argument("--date", default=None)
    p_res = sub.add_parser("restore", help="回拉备份到目标目录 (主机故障恢复)")
    p_res.add_argument("target", help="回拉目标目录")
    p_res.add_argument("--date", default=None)
    p_res.add_argument("--items", default=None, help="仅回拉指定相对路径 (逗号分隔)")
    args = parser.parse_args()

    date = _resolve_date(getattr(args, "date", None))

    if args.cmd == "backup":
        bak = EodBackup(_PROJECT_ROOT, _BACKUP_ROOT)
        manifest = bak.run(date)
        removed = bak.cleanup_old(today=date)
        r = verify_backup(_BACKUP_ROOT / date)
        status = "OK" if r["ok"] else "VERIFY_FAIL"
        print(f"[OK] backup {date}: {manifest['total_files']} files "
              f"({manifest['total_bytes']} bytes) -> {_BACKUP_ROOT / date} [{status}]")
        if manifest["missing_sources"]:
            print(f"[WARN] 源缺失 (容忍): {manifest['missing_sources']}")
        if removed:
            print(f"[OK] cleanup {len(removed)} old backup(s): {removed}")
        if not r["ok"]:
            print(f"[FAIL] verify: mismatched={r['mismatched']} missing={r['missing']}")
            return 1
        return 0

    if args.cmd == "verify":
        day_dir = _find_day_dir(date)
        r = verify_backup(day_dir)
        print(f"[{'OK' if r['ok'] else 'FAIL'}] verify {day_dir.name}: "
              f"checked={r['checked']} mismatched={r['mismatched']} missing={r['missing']}")
        return 0 if r["ok"] else 1

    if args.cmd == "restore":
        day_dir = _find_day_dir(date)
        items = args.items.split(",") if args.items else None
        r = restore_backup(day_dir, Path(args.target), items=items)
        print(f"[OK] restore {day_dir.name} -> {args.target}: {r['restored']} files")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
