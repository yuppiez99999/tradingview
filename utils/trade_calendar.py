# -*- coding: utf-8 -*-
"""
A股交易日历工具
================
基于 akshare 自动同步 A 股交易日历，判断交易日 / 下一交易日。

缓存策略:
    - 每年首次调用时拉取该年度交易日列表
    - 缓存到本地 JSON 文件 (按年), 避免重复联网
    - 失败时回退到 "周一至周五即交易日" 模式

用法:
    from utils.trade_calendar import is_trading_day, next_trading_day
    if is_trading_day('2026-07-10'):
        ...
    next = next_trading_day('2026-07-10')  # 返回 '2026-07-13'
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Set

# ============================================================
# 路径与缓存
# ============================================================
CACHE_DIR = Path(__file__).resolve().parent.parent / "config" / "trade_calendar_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(year: int) -> Path:
    """指定年度的缓存文件路径"""
    return CACHE_DIR / f"trade_dates_{year}.json"


def _fetch_trade_dates_via_akshare(year: int) -> Optional[Set[str]]:
    """通过 akshare 拉取交易日历

    使用 akshare.tool_trade_date_hist_sina() 获取所有 A 股交易日,
    然后筛选出指定年度的日期集合。
    """
    try:
        import akshare as ak  # type: ignore
        df = ak.tool_trade_date_hist_sina()
        if df is None or len(df) == 0:
            return None
        # 列名兼容: trade_date / date
        col = 'trade_date' if 'trade_date' in df.columns else df.columns[0]
        dates = df[col].astype(str).str[:10].tolist()
        year_dates = {d for d in dates if d.startswith(str(year))}
        return year_dates if year_dates else None
    except Exception as e:
        print(f"[trade_calendar] akshare 拉取失败 (year={year}): {e}")
        return None


def _load_year_dates(year: int, allow_fetch: bool = True) -> Set[str]:
    """加载指定年度的交易日集合 (优先缓存, 其次 akshare, 最后回退)"""
    cache_file = _cache_path(year)
    if cache_file.exists():
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                return set(json.load(f))
        except Exception:
            pass

    if not allow_fetch:
        return set()

    # 尝试 akshare
    dates = _fetch_trade_dates_via_akshare(year)
    if dates:
        try:
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(sorted(dates), f, ensure_ascii=False, indent=2)
            print(f"[trade_calendar] 缓存 {year} 年交易日: {len(dates)} 天")
        except Exception:
            pass
        return dates

    # 回退: 周一到周五即交易日 (无法联网时的兜底)
    print(f"[trade_calendar] 警告: 无法获取 {year} 年交易日历, 回退到周一至周五模式")
    return set()


def is_trading_day(date: Optional[str] = None) -> bool:
    """判断指定日期是否为 A 股交易日

    Args:
        date: 日期字符串 'YYYY-MM-DD' 或 'YYYYMMDD', None 表示今天

    Returns:
        True 表示是交易日
    """
    if date is None:
        date = datetime.now().strftime('%Y-%m-%d')
    date = date.replace('-', '').replace('/', '')
    iso_date = f"{date[:4]}-{date[4:6]}-{date[6:8]}"

    year = int(date[:4])
    year_dates = _load_year_dates(year)

    if year_dates:
        return iso_date in year_dates

    # 回退模式: 仅判断周末
    d = datetime.strptime(iso_date, '%Y-%m-%d')
    return d.weekday() < 5  # 0=周一 ... 4=周五


def next_trading_day(date: Optional[str] = None, max_lookahead: int = 30) -> str:
    """获取下一交易日 (跳过周末和节假日)

    Args:
        date: 起始日期, None 表示今天
        max_lookahead: 最多向后查看天数 (防止无限循环)

    Returns:
        下一交易日 'YYYY-MM-DD'
    """
    if date is None:
        d = datetime.now()
    else:
        clean = date.replace('-', '').replace('/', '')
        d = datetime.strptime(clean, '%Y%m%d')

    # 缓存当前年和下一年的交易日 (避免每次循环都加载)
    years_needed = {d.year, d.year + 1}
    year_dates_map = {y: _load_year_dates(y) for y in years_needed}

    for _ in range(max_lookahead):
        d = d + timedelta(days=1)
        iso = d.strftime('%Y-%m-%d')
        year_dates = year_dates_map.get(d.year) or _load_year_dates(d.year)
        year_dates_map[d.year] = year_dates
        if year_dates:
            if iso in year_dates:
                return iso
        else:
            # 回退模式: 跳过周末
            if d.weekday() < 5:
                return iso
    # 兜底: 返回下周一
    while d.weekday() >= 5:
        d = d + timedelta(days=1)
    return d.strftime('%Y-%m-%d')


def current_trading_day(date: Optional[str] = None) -> str:
    """获取当前交易日 (如果今天是交易日就返回今天, 否则返回上一交易日)

    Args:
        date: 起始日期, None 表示今天

    Returns:
        当前交易日 'YYYY-MM-DD'
    """
    if date is None:
        d = datetime.now()
    else:
        clean = date.replace('-', '').replace('/', '')
        d = datetime.strptime(clean, '%Y%m%d')

    years_needed = {d.year, d.year - 1}
    year_dates_map = {y: _load_year_dates(y) for y in years_needed}

    for _ in range(30):
        iso = d.strftime('%Y-%m-%d')
        year_dates = year_dates_map.get(d.year) or _load_year_dates(d.year)
        year_dates_map[d.year] = year_dates
        if year_dates:
            if iso in year_dates:
                return iso
        else:
            if d.weekday() < 5:
                return iso
        d = d - timedelta(days=1)
    return date or datetime.now().strftime('%Y-%m-%d')


if __name__ == '__main__':
    # 自测
    today = datetime.now().strftime('%Y-%m-%d')
    print(f"今天: {today}")
    print(f"  是交易日: {is_trading_day(today)}")
    print(f"  下一交易日: {next_trading_day(today)}")
    print(f"  当前交易日: {current_trading_day(today)}")
