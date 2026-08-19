#!/usr/bin/env python3
"""批量回填 Shadow 数据多源交叉校验。

读取 reports/shadow/daily_returns.jsonl 中现有记录, 通过
ShadowRealDataFeeder.cross_validate 对每日数据进行二次校验,
并将 cross_validated / source_consistency 字段回写到 jsonl.

用法:
    python scripts/run_shadow_cross_validation.py            # 处理 11 条全部记录
    python scripts/run_shadow_cross_validation.py --date 2026-08-10  # 单日
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("shadow_cross_validation")


def _load_records(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            logger.warning("跳过损坏记录: %s (%s)", line[:80], e)
    return rows


def _save_records(path: Path, rows: list[dict[str, Any]]) -> None:
    tmp = path.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Shadow 数据多源交叉校验")
    parser.add_argument(
        "--date",
        help="仅重校验指定日期 (YYYY-MM-DD); 默认处理全部已记录日期",
    )
    parser.add_argument(
        "--input",
        default=str(_PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"),
        help="daily_returns.jsonl 路径",
    )
    parser.add_argument(
        "--output",
        default=str(_PROJECT_ROOT / "reports" / "shadow" / "cross_validation_summary.json"),
        help="校验汇总输出路径",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        logger.error("daily_returns.jsonl 不存在: %s", input_path)
        return 1

    records = _load_records(input_path)
    logger.info("载入 %d 条 shadow 记录", len(records))

    try:
        from utils.alpha.shadow_real_data_feeder import ShadowRealDataFeeder
        from utils.data_provider import MarketDataProvider
    except (ImportError, AttributeError) as e:
        logger.error("导入模块失败: %s", e)
        return 1

    provider = MarketDataProvider(backtest_mode=False)
    feeder = ShadowRealDataFeeder(
        data_provider=provider,
        output_path=input_path,
        skip_weekend=False,
    )

    target_records = records
    if args.date:
        target_records = [r for r in records if r.get("date") == args.date]
        logger.info("按日期过滤: %s 命中 %d 条", args.date, len(target_records))

    summary: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_records": len(target_records),
        "results": [],
    }

    success = 0
    failed = 0
    for rec in target_records:
        date = rec.get("date")
        daily_return = rec.get("daily_return")
        if not date or daily_return is None:
            logger.warning("记录缺少 date/daily_return, 跳过: %s", rec)
            continue
        try:
            target_weights = feeder._load_target_weights(date)  # noqa: SLF001
            result = feeder.cross_validate(date, float(daily_return), target_weights)
            new_rec = dict(rec)
            new_rec["cross_validated"] = bool(getattr(result, "is_valid", False))
            new_rec["source_consistency"] = getattr(result, "source_consistency", "unknown")
            new_rec["cross_validated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            new_rec["cross_validated_notes"] = getattr(result, "notes", "") or ""
            new_rec["cross_validated_sources"] = list(getattr(result, "sources_used", []) or [])
            # 回写到总表
            for i, r in enumerate(records):
                if r.get("date") == date:
                    records[i] = new_rec
                    break
            summary["results"].append(
                {
                    "date": date,
                    "daily_return": daily_return,
                    "cross_validated": bool(getattr(result, "is_valid", False)),
                    "source_consistency": getattr(result, "source_consistency", "unknown"),
                    "notes": getattr(result, "notes", "") or "",
                }
            )
            success += 1
            logger.info(
                "[%s] valid=%s consistency=%s notes=%s",
                date,
                bool(getattr(result, "is_valid", False)),
                getattr(result, "source_consistency", "unknown"),
                (getattr(result, "notes", "") or "")[:80],
            )
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            failed += 1
            logger.error("[%s] 校验失败: %s", date, e)
            summary["results"].append(
                {
                    "date": date,
                    "daily_return": daily_return,
                    "error": str(e),
                }
            )

    # 写回 jsonl (带 cross_validated 字段)
    _save_records(input_path, records)

    summary["success"] = success
    summary["failed"] = failed
    summary["updated_file"] = str(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info("交叉校验完成: 成功 %d, 失败 %d", success, failed)
    logger.info("汇总: %s", output_path)
    return 0 if failed == 0 else 0  # 部分失败不阻断


if __name__ == "__main__":
    raise SystemExit(main())
