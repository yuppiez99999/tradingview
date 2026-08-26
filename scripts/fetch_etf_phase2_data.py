"""
ETF期权对冲再平衡 Phase 2 — P2.1 数据接入 (v8.6.14 复权口径修复)
==============================================================
拉取 14 只 ETF 2021-01-01 ~ 2026-08-20 日线数据。

数据源策略 (2026-08-25 修复: sina 未复权在份额折算 ETF 上严重失真,
  512100: +221.5% vs Wind +19.8%, ETF 回测必须用复权数据):
  默认 (--source wind): Wind MCP 前复权(qfq), 回测主数据源
    → data/etf_option_backtest/ (与 fetch_etf_data.py 同口径同目录, 复用其 Wind MCP 拉取路径)
  冗余 (--source sina):  新浪未复权, 仅作除权修正法交叉验证源
    → data_cache/etf_phase2/ (配合 scripts/run_p22_backtest.py adjust_sina_to_qfq 生成冗余复权序列)

用法:
  python scripts/fetch_etf_phase2_data.py                  # Wind MCP 主源 (默认)
  python scripts/fetch_etf_phase2_data.py --source sina    # sina 冗余源 (交叉验证)
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# NO_PROXY 绕过系统代理 (AGENTS.md 规范)
os.environ["NO_PROXY"] = "push2his.eastmoney.com,push2.eastmoney.com,eastmoney.com,sinajs.cn,sina.com.cn"
os.environ["no_proxy"] = os.environ["NO_PROXY"]

import akshare as ak
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# ============================================================
# 14 只 ETF 配置 (来自 config/etf_option_subportfolio.yaml)
# ============================================================
ETF_LIST = [
    # 宽基 60%
    {"code": "510300", "exchange": "SH", "name": "沪深300ETF华泰柏瑞", "category": "宽基", "weight": 0.15},
    {"code": "510500", "exchange": "SH", "name": "中证500ETF南方", "category": "宽基", "weight": 0.07},
    {"code": "510050", "exchange": "SH", "name": "上证50ETF华夏", "category": "宽基", "weight": 0.10},
    {"code": "512100", "exchange": "SH", "name": "中证1000ETF南方", "category": "宽基", "weight": 0.09},
    {"code": "588000", "exchange": "SH", "name": "科创50ETF华夏", "category": "宽基", "weight": 0.10},
    {"code": "159915", "exchange": "SZ", "name": "创业板ETF易方达", "category": "宽基", "weight": 0.08},
    # 行业/主题 25%
    {"code": "512480", "exchange": "SH", "name": "半导体ETF国联安", "category": "行业主题", "weight": 0.05},
    {"code": "512010", "exchange": "SH", "name": "医药ETF富国", "category": "行业主题", "weight": 0.06},
    {"code": "512660", "exchange": "SH", "name": "军工ETF鹏华", "category": "行业主题", "weight": 0.04},
    {"code": "515170", "exchange": "SH", "name": "新能源车ETF华夏", "category": "行业主题", "weight": 0.03},
    {"code": "159939", "exchange": "SZ", "name": "信息技术ETF广发", "category": "行业主题", "weight": 0.03},
    # 防御/抗通胀 15%
    {"code": "518880", "exchange": "SH", "name": "华安黄金ETF", "category": "防御抗通胀", "weight": 0.10},
    {"code": "511260", "exchange": "SH", "name": "国泰国债ETF", "category": "防御抗通胀", "weight": 0.05},
    {"code": "510310", "exchange": "SH", "name": "红利ETF易方达", "category": "防御抗通胀", "weight": 0.05},
]

START_DATE = "2021-01-01"
END_DATE = "2026-08-20"
OUTPUT_DIR = Path("data_cache/etf_phase2")


def sina_symbol(code: str, exchange: str) -> str:
    """转换为新浪接口格式: sh510300 / sz159915"""
    prefix = "sh" if exchange == "SH" else "sz"
    return f"{prefix}{code}"


def fetch_etf_sina(code: str, exchange: str) -> pd.DataFrame | None:
    """用新浪接口拉取 ETF 历史日线 (未复权, 仅作除权修正法交叉验证源)"""
    sym = sina_symbol(code, exchange)
    try:
        df = ak.fund_etf_hist_sina(symbol=sym)
        if df is None or len(df) == 0:
            return None
        # 标准化列名
        df = df.rename(columns={
            "date": "date", "open": "open", "high": "high",
            "low": "low", "close": "close", "volume": "volume",
            "amount": "amount",
        })
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        # 过滤日期范围
        df = df.loc[START_DATE:END_DATE]
        # 只保留需要的列
        cols = ["open", "high", "low", "close", "volume", "amount"]
        df = df[[c for c in cols if c in df.columns]].copy()
        df["code"] = code
        return df
    except Exception as e:
        print(f"  [FAIL] {sym}: {type(e).__name__}: {e}")
        return None


def _run_wind_mcp() -> int:
    """主数据源: Wind MCP 前复权(qfq)。

    复用 data/etf_option_backtest/fetch_etf_data.py 的 Wind MCP 拉取路径
    (wind_get_kline 默认前复权, 已与 Wind 终端实测一致), 保证回测主源
    口径统一, 避免重复实现。
    """
    ref = PROJECT_ROOT / "data" / "etf_option_backtest" / "fetch_etf_data.py"
    if not ref.exists():
        print(f"[ERROR] 未找到 Wind MCP 拉取脚本: {ref}")
        return 1
    spec = importlib.util.spec_from_file_location("fetch_etf_data", ref)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fetch_etf_data"] = mod
    spec.loader.exec_module(mod)
    return mod.main()


def _run_sina() -> int:
    """冗余数据源: sina 未复权 (仅用于除权修正法生成冗余复权序列 + 交叉验证)。"""
    print("=== ETF Phase 2 数据接入 (sina 未复权, 交叉验证冗余源) ===")
    print(f"标的数: {len(ETF_LIST)} | 日期范围: {START_DATE} ~ {END_DATE}")
    print(f"输出目录: {OUTPUT_DIR}")
    print()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_frames = []
    manifest = {
        "fetch_time": datetime.now().isoformat(),
        "start_date": START_DATE,
        "end_date": END_DATE,
        "source": "fund_etf_hist_sina",
        "adjust": "none",
        "note": "sina 未复权, 份额折算 ETF 上严重失真; 仅用于除权修正法交叉验证, 不作为回测主源",
        "etfs": [],
    }

    success_count = 0
    for i, etf in enumerate(ETF_LIST, 1):
        code = etf["code"]
        exchange = etf["exchange"]
        name = etf["name"]
        print(f"[{i}/{len(ETF_LIST)}] {code}.{exchange} {name} ...", end=" ", flush=True)

        df = fetch_etf_sina(code, exchange)
        if df is None or len(df) == 0:
            print("FAIL (无数据)")
            manifest["etfs"].append({**etf, "status": "fail", "rows": 0})
            continue

        # 保存单 ETF parquet
        out_file = OUTPUT_DIR / f"{code}.parquet"
        df.to_parquet(out_file, engine="pyarrow")

        all_frames.append(df)
        success_count += 1
        row_count = len(df)
        date_range = f"{df.index[0].date()}~{df.index[-1].date()}"
        print(f"OK ({row_count} 行, {date_range})")

        manifest["etfs"].append({
            **etf,
            "status": "ok",
            "rows": row_count,
            "date_range": date_range,
            "file": str(out_file),
        })

        # 礼貌延迟避免频率限制
        time.sleep(0.5)

    # 合并面板
    if all_frames:
        panel = pd.concat(all_frames, ignore_index=False)
        panel_file = OUTPUT_DIR / "all_etf_daily.parquet"
        panel.to_parquet(panel_file, engine="pyarrow")
        print(f"\n合并面板: {len(panel)} 行 → {panel_file}")

    # 保存清单
    manifest["success_count"] = success_count
    manifest["fail_count"] = len(ETF_LIST) - success_count
    manifest_file = OUTPUT_DIR / "_manifest.json"
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"\n=== 完成: {success_count}/{len(ETF_LIST)} 成功 ===")
    print(f"清单: {manifest_file}")
    return 0 if success_count == len(ETF_LIST) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="ETF Phase 2 数据接入 (v8.6.14 复权口径修复)")
    parser.add_argument(
        "--source",
        choices=["wind", "sina"],
        default="wind",
        help="数据源: wind=Wind MCP 前复权(主回测源,默认) / sina=新浪未复权(除权修正交叉验证冗余源)",
    )
    args = parser.parse_args()

    if args.source == "wind":
        return _run_wind_mcp()
    return _run_sina()


if __name__ == "__main__":
    sys.exit(main())
