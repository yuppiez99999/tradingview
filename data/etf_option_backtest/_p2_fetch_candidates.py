"""P2/P3 数据工具: 拉取高 Sharpe 候选资产 (纳指/标普/红利低波) 日线.

复用 fetch_etf_data 的数据源逻辑 (Wind MCP -> AKShare qfq), 输出到独立目录,
不覆盖现有 14 ETF 数据. 支持任意日期范围 (P2 用 2021-2026, P3 长样本用 2015-2026).

用法:
  python data/etf_option_backtest/_p2_fetch_candidates.py \
      --start-date 2015-01-01 --end-date 2026-08-20 \
      --output-dir data/etf_option_backtest/p2_candidates_long
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from utils.datetime_utils import now_bj

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

import fetch_etf_data as fed  # noqa: E402
from fetch_etf_data import _save_parquet, fetch_via_akshare, fetch_via_wind_mcp  # noqa: E402

DEFAULT_START = "2015-01-01"
DEFAULT_END = "2026-08-20"

CANDIDATES: dict[str, str] = {
    "513100.SH": "纳指100ETF国泰",
    "513500.SH": "标普500ETF博时",
    "512890.SH": "红利低波ETF华泰柏瑞",
}


def _filter_range(records: list[dict], start: str, end: str) -> list[dict]:
    """本地日期过滤 (不依赖 fetch_etf_data 模块全局常量)"""
    return [r for r in records if start <= r.get("date", "") <= end]


def main() -> int:
    parser = argparse.ArgumentParser(description="P2/P3 候选资产日线拉取 (Wind MCP -> AKShare)")
    parser.add_argument("--start-date", default=DEFAULT_START, help="起始日期 (默认 2015-01-01)")
    parser.add_argument("--end-date", default=DEFAULT_END, help="结束日期 (默认 2026-08-20)")
    parser.add_argument("--output-dir", default=None, help="输出目录 (默认 data/etf_option_backtest/p2_candidates/)")
    args = parser.parse_args()

    date_start, date_end = args.start_date, args.end_date
    # 对齐 fetch_etf_data 的全局: 供其内部 DAYS_FETCH / akShare 回退路径使用
    fed.DATE_START = date_start
    fed.DATE_END = date_end
    start_year = int(date_start[:4])
    end_year = int(date_end[:4])
    fed.DAYS_FETCH = (end_year - start_year + 1) * 365 + 100

    out_dir = (
        Path(args.output_dir) if args.output_dir
        else PROJECT_ROOT / "data" / "etf_option_backtest" / "p2_candidates"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    all_records = []
    ok_count = fail_count = 0
    for windcode, name in CANDIDATES.items():
        records, msg = fetch_via_wind_mcp(windcode)
        source = "wind_mcp"
        if not records:
            records, msg = fetch_via_akshare(windcode)
            source = "akshare"
        if not records:
            results.append({"code": windcode, "name": name, "success": False, "error": msg})
            fail_count += 1
            continue
        filtered = _filter_range(records, date_start, date_end)
        if not filtered:
            results.append({"code": windcode, "name": name, "success": False, "error": f"过滤后为空 ({msg})"})
            fail_count += 1
            continue
        code_short = windcode.split(".")[0]
        if not _save_parquet(filtered, out_dir / f"{code_short}.parquet"):
            results.append({"code": windcode, "name": name, "success": False, "error": "保存失败"})
            fail_count += 1
            continue
        results.append({
            "code": windcode, "name": name, "success": True, "source": source,
            "rows": len(filtered), "date_range": [filtered[0]["date"], filtered[-1]["date"]],
        })
        all_records.extend(filtered)
        ok_count += 1

    merged = out_dir / "p2_universe_extra.parquet"
    if all_records:
        _save_parquet(all_records, merged)

    report = {
        "generated_at": now_bj().strftime("%Y-%m-%d %H:%M:%S"),
        "date_range": [date_start, date_end],
        "adjust": "qfq",
        "ok_count": ok_count, "fail_count": fail_count,
        "candidates": results,
        "merged_file": str(merged),
    }
    (out_dir / "_fetch_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    for r in results:
        status = "OK " if r["success"] else "FAIL"
        extra = f"{r['source']} {r['rows']}条 {r['date_range']}" if r["success"] else r.get("error", "")
        print(f"[{status}] {r['code']} {r['name']}: {extra}")  # noqa: T201
    print(f"\n成功 {ok_count}/{len(CANDIDATES)}, 合并文件: {merged}")  # noqa: T201
    return 0 if ok_count == len(CANDIDATES) else 1


if __name__ == "__main__":
    sys.exit(main())
