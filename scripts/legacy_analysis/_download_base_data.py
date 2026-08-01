# -*- coding: utf-8 -*-
"""
预下载 33 个标的的 5 年日K数据（Sina HTTP 免费源）
=================================================

用途：
- 为 LGB Walk-forward 训练提供足够长的历史数据
- 截断到 2024-01-01 后仍有 ~640 天（远超 min_samples=150）
- 保存为 _base.parquet，绕过 data_provider 的 24h TTL 机制

数据源：新浪财经 KLine 接口（免费，无需 token，绕过系统代理）
"""

import os
import json
import time
import pathlib
import requests
import pandas as pd
from typing import List, Tuple

# 禁用系统代理（与 data_provider 的 Sina session 一致）
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['NO_PROXY'] = '*'

_SESSION = requests.Session()
_SESSION.trust_env = False
_SESSION.proxies = {"http": None, "https": None}

BASE_DIR = pathlib.Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "data_cache"
CACHE_DIR.mkdir(exist_ok=True)

# 23 个 LGB 训练标的
LGB_SYMBOLS: List[Tuple[str, str]] = [
    ("588000", "科创50ETF华夏"),
    ("688041", "海光信息"),
    ("002371", "北方华创"),
    ("688981", "中芯国际"),
    ("300308", "中际旭创"),
    ("000425", "徐工机械"),
    ("601088", "中国神华"),
    ("600276", "恒瑞医药"),
    ("600900", "长江电力"),
    ("515180", "易方达红利ETF"),
    ("600036", "招商银行"),
    ("518880", "黄金ETF华安"),
    ("300274", "阳光电源"),
    ("603019", "中科曙光"),
    ("600089", "特变电工"),
    ("688017", "绿的谐波"),
    ("600219", "南山铝业"),
    ("600019", "宝钢股份"),
    ("000680", "山推股份"),
    ("000333", "美的集团"),
    ("000408", "藏格矿业"),
    ("000975", "山金国际"),
    ("002422", "科伦药业"),
]

# 10 个 ETF 候选（特征工程用）
ETF_SYMBOLS: List[Tuple[str, str]] = [
    ("510300", "沪深300ETF"),
    ("510500", "中证500ETF"),
    ("510050", "上证50ETF"),
    ("159915", "创业板ETF"),
    ("512100", "中证1000ETF"),
    ("512010", "医药ETF"),
    ("512480", "半导体ETF"),
    ("512760", "半导体50ETF"),
    ("515030", "新能源车ETF"),
    ("515790", "光伏ETF"),
]

ALL_SYMBOLS = LGB_SYMBOLS + ETF_SYMBOLS


def to_sina_code(symbol: str) -> str:
    """A 股代码转新浪代码（带 sh/sz/bj 前缀）"""
    s = str(symbol).strip()
    if s.startswith(("51", "58")):
        return f"sh{s}"
    if s.startswith(("15", "16")):
        return f"sz{s}"
    if s.startswith(("00", "30")):
        return f"sz{s}"
    if s.startswith(("6",)):
        return f"sh{s}"
    if s.startswith(("4", "8")):
        return f"bj{s}"
    return f"sh{s}"


def fetch_sina_kline(symbol: str, datalen: int = 1260) -> pd.DataFrame:
    """通过新浪 HTTP 接口拉取日K数据

    Args:
        symbol: 6 位 A 股代码
        datalen: 拉取的 K 线根数（1260 ≈ 5 年）

    Returns:
        DataFrame[date, open, high, low, close, volume]
    """
    sina_code = to_sina_code(symbol)
    url = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "CN_MarketData.getKLineData"
        f"?symbol={sina_code}&scale=240&ma=no&datalen={datalen}"
    )
    headers = {"Referer": "https://finance.sina.com.cn"}
    resp = _SESSION.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    text = (resp.text or "").strip()
    if not text or text in ("null", "None"):
        return pd.DataFrame()
    payload = json.loads(text)
    if not payload:
        return pd.DataFrame()
    records = []
    for item in payload:
        day = item.get("day") or item.get("date")
        if not day:
            continue
        close = float(item.get("close", 0) or 0)
        if close <= 0:
            continue
        records.append({
            "date": pd.to_datetime(day),
            "open": float(item.get("open", 0) or 0),
            "high": float(item.get("high", 0) or 0),
            "low": float(item.get("low", 0) or 0),
            "close": close,
            "volume": float(item.get("volume", 0) or 0),
        })
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    df.set_index("date", inplace=True)
    df = df.sort_index()
    # 去重（防止 Sina 偶发返回重复日期）
    df = df[~df.index.duplicated(keep="last")]
    return df


def main():
    print("=" * 70)
    print("预下载 5 年日K数据（Sina HTTP 免费源）")
    print(f"标的数量: {len(ALL_SYMBOLS)}（23 LGB + 10 ETF）")
    print(f"缓存目录: {CACHE_DIR}")
    print("=" * 70)

    success = 0
    failed = 0
    results = []

    for i, (symbol, name) in enumerate(ALL_SYMBOLS, 1):
        cache_file = CACHE_DIR / f"historical_{symbol}_5y_base.parquet"

        # 已存在且数据量足够则跳过
        if cache_file.exists():
            try:
                existing = pd.read_parquet(cache_file)
                if len(existing) >= 150:
                    print(f"[{i:>2}/{len(ALL_SYMBOLS)}] {symbol} {name}: "
                          f"已存在 {len(existing)} 行 ({existing.index[0].date()} ~ "
                          f"{existing.index[-1].date()})，跳过")
                    success += 1
                    results.append((symbol, name, len(existing), "cached"))
                    continue
            except Exception:
                pass  # 文件损坏，重新下载

        # 拉取数据
        retry = 0
        df = pd.DataFrame()
        while retry < 3 and df.empty:
            try:
                df = fetch_sina_kline(symbol, datalen=1260)
            except Exception as e:
                retry += 1
                print(f"[{i:>2}/{len(ALL_SYMBOLS)}] {symbol} {name}: "
                      f"第 {retry} 次拉取失败: {e}")
                if retry < 3:
                    time.sleep(2)

        if df is None or df.empty:
            print(f"[{i:>2}/{len(ALL_SYMBOLS)}] {symbol} {name}: ❌ 拉取失败")
            failed += 1
            results.append((symbol, name, 0, "failed"))
            continue

        # 保存为 parquet
        df.to_parquet(cache_file, index=True)
        print(f"[{i:>2}/{len(ALL_SYMBOLS)}] {symbol} {name}: ✅ {len(df)} 行 "
              f"({df.index[0].date()} ~ {df.index[-1].date()})")
        success += 1
        results.append((symbol, name, len(df), "downloaded"))

        # 礼貌延时，避免被 Sina 限流
        time.sleep(0.5)

    print("=" * 70)
    print(f"完成: 成功 {success}/{len(ALL_SYMBOLS)}, 失败 {failed}")
    if failed > 0:
        print("失败标的:")
        for symbol, name, _rows, status in results:
            if status == "failed":
                print(f"  - {symbol} {name}")
    print("=" * 70)

    # 输出数据覆盖摘要
    print("\n数据覆盖摘要（用于验证 2024-01-01 截断后是否足够）:")
    for symbol, name, _rows, status in results:
        if status == "failed":
            continue
        df = pd.read_parquet(_CACHE_FILE := CACHE_DIR / f"historical_{symbol}_5y_base.parquet")
        cutoff = pd.Timestamp("2024-01-01")
        before_cutoff = df[df.index <= cutoff]
        print(f"  {symbol} {name}: 总 {len(df)} 行, "
              f"截断到 2024-01-01 后 {len(before_cutoff)} 行 "
              f"{'✅' if len(before_cutoff) >= 150 else '❌'}")


if __name__ == "__main__":
    main()
