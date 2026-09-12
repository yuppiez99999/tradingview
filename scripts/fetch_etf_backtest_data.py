"""
ETF期权对冲再平衡子模型 - Phase 2 回测数据拉取脚本

拉取 14 只 ETF 的历史日线数据（前复权），用于 ETF 期权对冲再平衡回测验证。
数据源优先级：AKShare (P3) → efinance → baostock → tushare
遵循 AGENTS.md §6 数据源优先级与降级原则。

用法:
    C:\\Users\\Administrator\\py311\\python.exe scripts/fetch_etf_backtest_data.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# === 硬约束: NO_PROXY 配置 (必须在 import akshare/requests 之前) ===
os.environ["NO_PROXY"] = (
    "push2his.eastmoney.com,push2.eastmoney.com,eastmoney.com,"
    "sinajs.cn,sina.com.cn,api.akshare.akfamily.xyz"
)
os.environ["no_proxy"] = os.environ["NO_PROXY"]

import pandas as pd

# === 配置 ===
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "etf_option_backtest"
START_DATE = "20210101"
END_DATE = "20260820"
START_DATE_ISO = "2021-01-01"
END_DATE_ISO = "2026-08-20"
MIN_ROWS = 1000  # 5.5 年日线约 1300 行，下限 1000

# 14 只 ETF 清单 (code_full, code_short, name, category)
ETF_LIST = [
    ("510300.SH", "510300", "沪深300ETF华泰柏瑞", "宽基"),
    ("510500.SH", "510500", "中证500ETF南方", "宽基"),
    ("510050.SH", "510050", "上证50ETF华夏", "宽基"),
    ("512100.SH", "512100", "中证1000ETF南方", "宽基"),
    ("588000.SH", "588000", "科创50ETF华夏", "宽基"),
    ("159915.SZ", "159915", "创业板ETF易方达", "宽基"),
    ("512480.SH", "512480", "半导体ETF国联安", "行业"),
    ("512010.SH", "512010", "医药ETF富国", "行业"),
    ("512660.SH", "512660", "军工ETF鹏华", "行业"),
    ("515170.SH", "515170", "新能源车ETF华夏", "行业"),
    ("159939.SZ", "159939", "信息技术ETF广发", "行业"),
    ("518880.SH", "518880", "华安黄金ETF", "防御"),
    ("511260.SH", "511260", "国泰国债ETF", "防御"),
    ("510310.SH", "510310", "红利ETF易方达", "防御"),
]

# AKShare 中文列名 → 标准列名
COL_MAP = {
    "日期": "date",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
}


def fetch_via_akshare(code_short: str) -> pd.DataFrame | None:
    """通过 AKShare fund_etf_hist_em 拉取 ETF 前复权日线数据。"""
    import akshare as ak

    df = ak.fund_etf_hist_em(
        symbol=code_short,
        period="daily",
        start_date=START_DATE,
        end_date=END_DATE,
        adjust="qfq",
    )
    if df is None or len(df) == 0:
        return None
    # 列名映射
    df = df.rename(columns=COL_MAP)
    # 仅保留标准列
    keep = [
        c
        for c in ["date", "open", "high", "low", "close", "volume", "amount"]
        if c in df.columns
    ]
    df = df[keep].copy()
    # date 转为 datetime
    df["date"] = pd.to_datetime(df["date"])
    # 数值列转 float
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("date").reset_index(drop=True)
    return df


def fetch_via_efinance(code_short: str) -> pd.DataFrame | None:
    """通过 efinance 拉取 ETF 日线数据（降级方案）。"""
    try:
        import efinance as ef
    except ImportError:
        return None
    # efinance ETF 代码需要加前缀
    code = code_short if code_short.startswith(("5", "1")) else code_short
    df = ef.stock.get_quote_history(code, beg=START_DATE, end=END_DATE, kdt=1)
    if df is None or len(df) == 0:
        return None
    col_map_ef = {
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
    }
    df = df.rename(columns=col_map_ef)
    keep = [
        c
        for c in ["date", "open", "high", "low", "close", "volume", "amount"]
        if c in df.columns
    ]
    df = df[keep].copy()
    df["date"] = pd.to_datetime(df["date"])
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("date").reset_index(drop=True)
    return df


def fetch_one(
    code_full: str, code_short: str, name: str
) -> tuple[pd.DataFrame | None, str, str]:
    """
    拉取单只 ETF 数据，按优先级降级。
    返回 (df, data_source, error_msg)
    """
    # P3: AKShare
    try:
        df = fetch_via_akshare(code_short)
        if df is not None and len(df) > 0:
            return df, "akshare", ""
    except Exception as e:
        err_ak = f"akshare error: {e}"
    else:
        err_ak = "akshare returned empty"

    # 降级: efinance
    try:
        df = fetch_via_efinance(code_short)
        if df is not None and len(df) > 0:
            return df, "efinance", f"fallback from {err_ak}"
    except Exception as e:
        err_ef = f"efinance error: {e}"
    else:
        err_ef = "efinance returned empty"

    return None, "failed", f"{err_ak}; {err_ef}"


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[INFO] 输出目录: {OUTPUT_DIR}")
    print(f"[INFO] 时间范围: {START_DATE_ISO} ~ {END_DATE_ISO}")
    print(f"[INFO] ETF 数量: {len(ETF_LIST)}")
    print(f"[INFO] NO_PROXY: {os.environ.get('NO_PROXY')}")
    print("=" * 80)

    results: list[dict] = []
    all_frames: list[pd.DataFrame] = []
    success_count = 0
    fail_count = 0

    for i, (code_full, code_short, name, category) in enumerate(ETF_LIST, 1):
        print(
            f"[{i}/{len(ETF_LIST)}] {code_full} {name} ({category}) ...",
            end=" ",
            flush=True,
        )
        t0 = time.time()

        df, source, err = fetch_one(code_full, code_short, name)
        elapsed = time.time() - t0

        if df is None:
            fail_count += 1
            rec = {
                "code_full": code_full,
                "code_short": code_short,
                "name": name,
                "category": category,
                "status": "failed",
                "rows": 0,
                "date_min": None,
                "date_max": None,
                "data_source": source,
                "error": err,
                "elapsed_sec": round(elapsed, 2),
            }
            results.append(rec)
            print(f"FAILED ({err}) [{elapsed:.1f}s]")
            # 失败不中断，继续下一只
            time.sleep(0.5)
            continue

        # 添加元数据列
        df_out = df.copy()
        df_out["code"] = code_full
        # 列顺序: date, code, open, high, low, close, volume, amount
        col_order = ["date", "code", "open", "high", "low", "close", "volume", "amount"]
        col_order = [c for c in col_order if c in df_out.columns]
        df_out = df_out[col_order]

        # 保存单只 parquet
        out_path = OUTPUT_DIR / f"{code_short}.parquet"
        df_out.to_parquet(out_path, index=False, engine="pyarrow")

        success_count += 1
        date_min = str(df_out["date"].min().date())
        date_max = str(df_out["date"].max().date())
        rows = len(df_out)
        ok_rows = rows >= MIN_ROWS

        rec = {
            "code_full": code_full,
            "code_short": code_short,
            "name": name,
            "category": category,
            "status": "success",
            "rows": rows,
            "date_min": date_min,
            "date_max": date_max,
            "rows_pass_threshold": ok_rows,
            "data_source": source,
            "error": err if err else None,
            "elapsed_sec": round(elapsed, 2),
            "file": str(out_path),
        }
        results.append(rec)
        all_frames.append(df_out)

        flag = "OK" if ok_rows else "WARN(rows<1000)"
        print(
            f"{rows} rows [{date_min}~{date_max}] src={source} {flag} [{elapsed:.1f}s]"
        )

        # 礼貌延时，避免被限流
        time.sleep(0.3)

    print("=" * 80)
    print(f"[SUMMARY] 成功 {success_count}/{len(ETF_LIST)}, 失败 {fail_count}")

    # 合并为 all_etf_daily.parquet
    merged_path = OUTPUT_DIR / "all_etf_daily.parquet"
    if all_frames:
        merged = pd.concat(all_frames, ignore_index=True)
        merged = merged.sort_values(["code", "date"]).reset_index(drop=True)
        merged.to_parquet(merged_path, index=False, engine="pyarrow")
        print(
            f"[MERGED] {merged_path}  rows={len(merged)}  codes={merged['code'].nunique()}"
        )
    else:
        print("[MERGED] 无数据可合并")

    # 生成拉取报告
    report = {
        "generated_at": now_bj().isoformat(),
        "start_date": START_DATE_ISO,
        "end_date": END_DATE_ISO,
        "min_rows_threshold": MIN_ROWS,
        "output_dir": str(OUTPUT_DIR),
        "merged_file": str(merged_path) if all_frames else None,
        "total_etfs": len(ETF_LIST),
        "success_count": success_count,
        "fail_count": fail_count,
        "etfs": results,
    }
    report_path = OUTPUT_DIR / "_fetch_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"[REPORT] {report_path}")

    # 失败列表
    failed = [r for r in results if r["status"] == "failed"]
    if failed:
        print("\n[FAILED ETFs]")
        for r in failed:
            print(f"  - {r['code_full']} {r['name']}: {r['error']}")

    # 行数不足警告
    low_rows = [
        r
        for r in results
        if r["status"] == "success" and not r.get("rows_pass_threshold", True)
    ]
    if low_rows:
        print("\n[LOW ROWS WARNING]")
        for r in low_rows:
            print(f"  - {r['code_full']} {r['name']}: rows={r['rows']} < {MIN_ROWS}")

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
