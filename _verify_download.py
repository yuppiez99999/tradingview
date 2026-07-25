# -*- coding: utf-8 -*-
"""验证 baostock 下载结果的数据质量"""
import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(r"e:\各种PY程序\28-终极量化交易系统8.4")
OHLCV_DIR = PROJECT_ROOT / "cache" / "ohlcv"
FUND_DIR = PROJECT_ROOT / "cache" / "fundamentals"


def main():
    # === 1. OHLCV ===
    ohlcv_files = sorted(OHLCV_DIR.glob("*_2y.parquet"))
    print(f"[OHLCV] {len(ohlcv_files)} 个文件")

    # 检查天数分布
    days_list = []
    for f in ohlcv_files:
        try:
            df = pd.read_parquet(f)
            days_list.append((f.stem.replace("_2y", ""), len(df)))
        except Exception as e:
            print(f"  ERROR {f.name}: {e}")

    if days_list:
        days_arr = [d for _, d in days_list]
        print(f"  天数范围: {min(days_arr)} ~ {max(days_arr)} | 中位数 {sorted(days_arr)[len(days_arr)//2]}")
        print(f"  数据时间: {pd.read_parquet(ohlcv_files[0]).index[0].date()} ~ "
              f"{pd.read_parquet(ohlcv_files[0]).index[-1].date()}")

    # === 2. 基准 ===
    bench_path = OHLCV_DIR / "sh_000300_index.parquet"
    if bench_path.exists():
        df = pd.read_parquet(bench_path)
        print(f"\n[Benchmark 沪深300] {len(df)} 天 | {df.index[0].date()} ~ {df.index[-1].date()}")
        closes = df["close"].astype(float).tolist()
        cum_ret = (pd.Series(closes).iloc[-1] / pd.Series(closes).iloc[0] - 1) * 100
        print(f"  累计收益: {cum_ret:.2f}%")

    # === 3. Fundamentals ===
    fund_files = sorted(FUND_DIR.glob("*.json"))
    print(f"\n[Fundamentals] {len(fund_files)} 个文件")

    real_count = 0
    empty_count = 0
    sample_real = None
    field_stats = {"pe": [], "pb": [], "ps": [], "roe": [], "gross_margin": [], "debt_to_equity": []}

    for f in fund_files:
        try:
            with open(f, "r", encoding="utf-8") as fp:
                d = json.load(fp)
            if d.get("pe", 0) > 0 or d.get("roe", 0) != 0:
                real_count += 1
                if sample_real is None:
                    sample_real = d
                for k in field_stats:
                    v = d.get(k, 0)
                    if isinstance(v, (int, float)) and v == v:  # not NaN
                        field_stats[k].append(v)
            else:
                empty_count += 1
        except Exception as e:
            print(f"  ERROR {f.name}: {e}")
            empty_count += 1

    print(f"  真实数据: {real_count} 个")
    print(f"  空/代理:  {empty_count} 个")

    if sample_real:
        print(f"\n  样例 {sample_real.get('symbol')} ({sample_real.get('bs_code')}):")
        print(f"    报告期: {sample_real.get('report_year')} Q{sample_real.get('report_quarter')}")
        print(f"    ROE={sample_real.get('roe', 0):.4f} | 净利率={sample_real.get('net_margin', 0):.4f} | 毛利率={sample_real.get('gross_margin', 0):.4f}")
        print(f"    PE={sample_real.get('pe', 0):.2f} | PB={sample_real.get('pb', 0):.2f} | PS={sample_real.get('ps', 0):.2f}")
        print(f"    负债率={sample_real.get('liability_to_assets', 0):.4f} | debt_to_equity={sample_real.get('debt_to_equity', 0):.4f}")
        print(f"    市值={sample_real.get('market_cap', 0):.0f} | 营收={sample_real.get('revenue', 0):.0f}")

    if real_count > 0:
        print(f"\n  === 字段分布（{real_count} 个真实样本）===")
        for k, vals in field_stats.items():
            if vals:
                import numpy as np
                arr = np.array(vals)
                print(f"    {k:20s}: min={arr.min():.3f} | max={arr.max():.3f} | median={np.median(arr):.3f} | mean={arr.mean():.3f}")


if __name__ == "__main__":
    main()
