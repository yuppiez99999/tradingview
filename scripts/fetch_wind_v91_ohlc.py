#!/usr/bin/env python
"""取 v9.1 组合全部标的 + 基准指数的 Wind 日 K (OHLC), 落盘为证据基座。

为什么需要它
------------
`data/etf_option_backtest/` 内的本地 parquet **原始出处未在仓库留痕**; 虽然已实测与
Wind 逐日零偏差 (`scripts/verify_etf_price_source.py`), 但证据基座应当**自证来源**:
本脚本把「哪个源、什么窗口、什么复权口径、多少行」写进 manifest, 使回测数字可追溯。

产物 (默认 `data/etf_option_backtest/wind_2021_2026/`)
------------------------------------------------------
  * `<code>.parquet`            逐标的 OHLC (date/open/high/low/close/volume/amount)
  * `wind_ohlc_all.parquet`     全部标的长表 (含 code/windcode/kind)
  * `manifest.json`             来源/窗口/复权口径/行数/失败清单/复现命令
  * `<benchmark>.parquet`       基准指数 (默认 000300.SH 沪深300)

判定: 任一标的取数为空 → 非零退出并列出失败清单 (缺数据不得静默通过)。

用法
----
  .venv/Scripts/python.exe scripts/fetch_wind_v91_ohlc.py
  .venv/Scripts/python.exe scripts/fetch_wind_v91_ohlc.py --begin 2021-01-04 --end 2026-08-20
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import pandas as pd
import yaml

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from tools.wind_mcp_fetcher import (  # noqa: E402
    wind_get_index_kline,
    wind_get_kline_range,
)
from utils.datetime_utils import now_bj  # noqa: E402

_HOLDING_LAYERS = ("core_holdings", "satellite_holdings", "ballast_holdings", "cash_holdings")
_DEFAULT_CONFIG = os.path.join(_PROJECT_ROOT, "config", "portfolio_200w_etf_v91.yaml")
_DEFAULT_OUT = os.path.join(_PROJECT_ROOT, "data", "etf_option_backtest", "wind_2021_2026")

#: Wind K 线中文字段 -> 内部字段 (MATCH 是收盘价, 无 CLOSE 列)
_FIELD_MAP = {
    "OPEN": "open",
    "HIGH": "high",
    "LOW": "low",
    "MATCH": "close",
    "VOLUME": "volume",
    "TURNOVER": "amount",
}


def _windcode_of(code: str) -> str:
    """6 位代码 -> Wind 代码 (5/6 开头沪市, 其余深市)。"""
    if "." in code:
        return code
    six = code.split(".")[0]
    return f"{six}.SH" if six[0] in ("5", "6") else f"{six}.SZ"


def _config_codes(config_path: str) -> list[str]:
    with open(config_path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    codes: list[str] = []
    for layer in _HOLDING_LAYERS:
        for item in cfg.get(layer) or []:
            if isinstance(item, dict) and item.get("code"):
                code = str(item["code"]).split(".")[0]
                if code not in codes:
                    codes.append(code)
    return codes


def _to_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Wind K 线记录 -> 规范化 DataFrame (date 为 tz-naive 北京时间日期)。"""
    df = pd.DataFrame(records)
    df["date"] = (
        pd.to_datetime(df["TIME"], utc=True)
        .dt.tz_convert("Asia/Shanghai")
        .dt.normalize()
        .dt.tz_localize(None)
    )
    for src, dst in _FIELD_MAP.items():
        df[dst] = pd.to_numeric(df.get(src), errors="coerce")
    keep = ["date", "open", "high", "low", "close", "volume", "amount"]
    df = df[keep].dropna(subset=["close"]).sort_values("date")
    return df.drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="取 v9.1 全部标的 + 基准的 Wind 日 K (OHLC)")
    parser.add_argument("--config", default=_DEFAULT_CONFIG, help="组合配置 YAML")
    parser.add_argument("--begin", default="2021-01-04", help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", default="2026-08-20", help="结束日期 YYYY-MM-DD")
    parser.add_argument("--benchmark", default="000300.SH", help="基准指数 Wind 代码")
    parser.add_argument("--out-dir", default=_DEFAULT_OUT, help="产物目录")
    args = parser.parse_args(argv)

    codes = _config_codes(args.config)
    print("=" * 64)
    print(f"Wind OHLC 证据基座: {len(codes)} 标的 + 基准 {args.benchmark}")
    print(f"窗口: {args.begin} ~ {args.end}")
    print("=" * 64)

    os.makedirs(args.out_dir, exist_ok=True)
    frames: list[pd.DataFrame] = []
    manifest_rows: list[dict[str, Any]] = []
    failures: list[str] = []

    for code in codes:
        windcode = _windcode_of(code)
        records = wind_get_kline_range(windcode, args.begin, args.end, kind="fund")
        if not records:
            failures.append(f"{windcode} 取数为空")
            print(f"  {windcode}: FAIL (取数为空)")
            continue
        frame = _to_frame(records)
        frame.to_parquet(os.path.join(args.out_dir, f"{code}.parquet"), index=False)
        long = frame.copy()
        long.insert(0, "code", code)
        long.insert(1, "windcode", windcode)
        long["kind"] = "fund"
        frames.append(long)
        manifest_rows.append(
            {
                "code": code,
                "windcode": windcode,
                "kind": "fund",
                "rows": int(frame.shape[0]),
                "first": str(frame["date"].iloc[0].date()),
                "last": str(frame["date"].iloc[-1].date()),
            }
        )
        print(
            f"  {windcode}: {frame.shape[0]} 行 "
            f"({frame['date'].iloc[0].date()} ~ {frame['date'].iloc[-1].date()})"
        )

    bench_records = wind_get_index_kline(args.benchmark, args.begin, args.end)
    if not bench_records:
        failures.append(f"基准 {args.benchmark} 取数为空")
        print(f"  基准 {args.benchmark}: FAIL (取数为空)")
    else:
        bench = _to_frame(bench_records)
        bench.to_parquet(os.path.join(args.out_dir, f"{args.benchmark}.parquet"), index=False)
        long_b = bench.copy()
        long_b.insert(0, "code", args.benchmark.split(".")[0])
        long_b.insert(1, "windcode", args.benchmark)
        long_b["kind"] = "index"
        frames.append(long_b)
        manifest_rows.append(
            {
                "code": args.benchmark.split(".")[0],
                "windcode": args.benchmark,
                "kind": "index",
                "rows": int(bench.shape[0]),
                "first": str(bench["date"].iloc[0].date()),
                "last": str(bench["date"].iloc[-1].date()),
            }
        )
        print(
            f"  {args.benchmark} (基准): {bench.shape[0]} 行 "
            f"({bench['date'].iloc[0].date()} ~ {bench['date'].iloc[-1].date()})"
        )

    if frames:
        pd.concat(frames, ignore_index=True).to_parquet(
            os.path.join(args.out_dir, "wind_ohlc_all.parquet"), index=False
        )

    manifest = {
        "source": "wind_mcp",
        "tool": "fund_data.get_fund_kline / index_data.get_index_kline",
        "adjust": "基金 price_type=1 (前复权) / 指数 aftype=0 (前复权)",
        "config": os.path.relpath(args.config, _PROJECT_ROOT).replace("\\", "/"),
        "window": [args.begin, args.end],
        "benchmark": args.benchmark,
        "n_instruments": len(manifest_rows),
        "instruments": manifest_rows,
        "failures": failures,
        "generated_at": now_bj().isoformat(),
        "reproduce": (
            ".venv/Scripts/python.exe scripts/fetch_wind_v91_ohlc.py "
            f"--begin {args.begin} --end {args.end} --benchmark {args.benchmark}"
        ),
    }
    with open(os.path.join(args.out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    print("-" * 64)
    print(f"产物: {args.out_dir}")
    if failures:
        print(f"RESULT: FAIL — {len(failures)} 项失败: {failures}")
        return 1
    print(f"RESULT: PASS — {len(manifest_rows)} 个标的全部落盘 (含基准)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
