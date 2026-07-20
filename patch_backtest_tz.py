# -*- coding: utf-8 -*-
"""修复 backtest_runner 时区错误"""
import pathlib

p = pathlib.Path(r"E:\各种PY程序\28-终极量化交易系统7.1\backtest_runner.py")
text = p.read_text(encoding="utf-8")

old = """def _next_month_returns(symbol: str, date: pd.Timestamp, provider: MarketDataProvider) -> float:
    try:
        df = provider.get_historical_data(symbol, period="3y")
        if df is None or df.empty or len(df) < 22:
            return 0.0
        df = df.sort_index()
        try:
            if hasattr(df.index, "tz") and df.index.tz is not None:
                df.index = df.index.tz_localize(None)
        except Exception:
            pass
        compare_date = pd.Timestamp(date).normalize()
        data_start = df.index[0]
        if compare_date < data_start:
            return 0.0
        future = df[df.index > compare_date]
        if len(future) < 22:
            return 0.0
        start_price = float(future.iloc[0]["close"])
        end_price = float(future.iloc[21]["close"])
        if start_price <= 0 or end_price <= 0:
            return 0.0
        return float(end_price / start_price - 1)
    except Exception as e:
        logger.warning("获取%s月度收益失败 %s: %s", symbol, date, e)
        return 0.0"""

new = """def _next_month_returns(symbol: str, date: pd.Timestamp, provider: MarketDataProvider) -> float:
    try:
        df = provider.get_historical_data(symbol, period="3y")
        if df is None or df.empty or len(df) < 22:
            return 0.0
        df = df.sort_index()
        # 强制索引和比较日期都为 tz-naive，避免混合时区比较报错
        try:
            if hasattr(df.index, "tz") and df.index.tz is not None:
                df.index = df.index.tz_localize(None)
        except Exception:
            df.index = pd.DatetimeIndex([pd.Timestamp(idx).tz_localize(None) if pd.Timestamp(idx).tzinfo else pd.Timestamp(idx) for idx in df.index])
        compare_date = pd.Timestamp(date).normalize()
        try:
            if hasattr(compare_date, "tz") and compare_date.tz is not None:
                compare_date = compare_date.tz_localize(None)
        except Exception:
            compare_date = pd.Timestamp(compare_date).tz_localize(None) if pd.Timestamp(compare_date).tzinfo else pd.Timestamp(compare_date)
        data_start = df.index[0]
        if compare_date < data_start:
            return 0.0
        future = df[df.index > compare_date]
        if len(future) < 22:
            return 0.0
        start_price = float(future.iloc[0]["close"])
        end_price = float(future.iloc[21]["close"])
        if start_price <= 0 or end_price <= 0:
            return 0.0
        return float(end_price / start_price - 1)
    except Exception as e:
        logger.warning("获取%s月度收益失败 %s: %s", symbol, date, e)
        return 0.0"""

if old not in text:
    raise SystemExit("next_month_returns target not found")
text = text.replace(old, new)
p.write_text(text, encoding="utf-8")
print("fixed backtest tz issue")
