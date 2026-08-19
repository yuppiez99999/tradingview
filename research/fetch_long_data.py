"""拉取 30 股 × 1000 交易日历史数据（约 4 年，跨牛熊周期）

为 P4 跨周期验证准备更长数据. 保存到 mvsk_real_data_long_cache.npz.

Usage:
    .venv\\Scripts\\python.exe research\\fetch_long_data.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

os.environ.setdefault("WIND_API_KEY", "ak_Tk4Y_UE-MfUof8DLLbKpHZZY-kh1q5KD")

from tools.wind_mcp_fetcher import wind_get_kline  # noqa: E402

STOCKS = [
    ("600519.SH", "贵州茅台"), ("000858.SZ", "五粮液"),
    ("600036.SH", "招商银行"), ("601318.SH", "中国平安"), ("601398.SH", "工商银行"),
    ("000725.SZ", "京东方A"), ("002415.SZ", "海康威视"), ("300750.SZ", "宁德时代"),
    ("600276.SH", "药明康德"), ("000333.SZ", "美的集团"),
    ("601899.SH", "紫金矿业"), ("600028.SH", "中国石化"),
    ("002594.SZ", "比亚迪"), ("601012.SH", "隆基绿能"), ("002049.SZ", "紫光国微"),
    ("600436.SH", "片仔癀"), ("300015.SZ", "爱尔眼科"),
    ("001979.SZ", "招商蛇口"), ("601668.SH", "中国建筑"),
    ("600104.SH", "上汽集团"), ("600887.SH", "伊利股份"),
    ("600030.SH", "中信证券"), ("600900.SH", "长江电力"),
    ("600050.SH", "中国联通"), ("600309.SH", "万华化学"),
    ("600031.SH", "三一重工"), ("600019.SH", "宝钢股份"),
    ("601225.SH", "陕西煤业"), ("600029.SH", "南方航空"), ("601888.SH", "中国中免"),
]

N_DAYS = 1000  # 约 4 年交易日
CACHE_PATH = _PROJECT_ROOT / "research" / "mvsk_real_data_long_cache.npz"


def main() -> int:
    if CACHE_PATH.exists():
        cache = np.load(CACHE_PATH, allow_pickle=True)
        print(f"缓存已存在: {cache['returns'].shape[0]} 日 × {cache['returns'].shape[1]} 股")
        return 0

    print(f"获取 {len(STOCKS)} 只股票 × {N_DAYS} 交易日 K 线 (Wind MCP)...")
    all_klines: dict[str, dict[str, float]] = {}
    failed: list[str] = []

    for i, (code, name) in enumerate(STOCKS):
        try:
            records = wind_get_kline(code, days=N_DAYS)
            if not records or len(records) < 504:
                print(f"  [{i:2d}/{len(STOCKS)}] {code} {name}: 数据不足 ({len(records) if records else 0} 条), 跳过")
                failed.append(code)
                continue
            klines = {}
            for r in records:
                date = str(r.get("TIME", ""))[:10]
                close = r.get("MATCH")
                if close:
                    klines[date] = float(close)
            all_klines[code] = klines
            print(f"  [{i:2d}/{len(STOCKS)}] {code} {name}: {len(klines)} 条")
        except Exception as e:  # noqa: BLE001
            print(f"  [{i:2d}/{len(STOCKS)}] {code} {name}: 失败 {e}")
            failed.append(code)

    if failed:
        print(f"\n失败 {len(failed)} 只: {failed}")

    codes = list(all_klines.keys())
    date_sets = [set(k.keys()) for k in all_klines.values()]
    common_dates = sorted(set.intersection(*date_sets))
    print(f"\n共同交易日: {len(common_dates)}")

    if len(common_dates) < 504:
        print(f"错误: 共同交易日 {len(common_dates)} < 504")
        return 1

    T = len(common_dates) - 1  # noqa: N806
    N = len(codes)  # noqa: N806
    returns = np.zeros((T, N))
    for j, code in enumerate(codes):
        klines = all_klines[code]
        prices = np.array([klines[d] for d in common_dates])
        returns[:, j] = prices[1:] / prices[:-1] - 1.0

    np.savez(CACHE_PATH, returns=returns, codes=np.array(codes), dates=np.array(common_dates[1:]))
    print(f"缓存已保存: {CACHE_PATH} ({T} 日 × {N} 股)")
    print(f"日期范围: {common_dates[0]} ~ {common_dates[-1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
