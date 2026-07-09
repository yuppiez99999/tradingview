# -*- coding: utf-8 -*-
import os, sys, json
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PLAN_FILE = os.path.join(_BASE_DIR, "500万建仓计划_20260706.json")
with open(_PLAN_FILE, "r", encoding="utf-8") as f:
    data = json.load(f)
codes = list(data.get("position_plan", {}).keys())
print("CODES:", codes[:5])
sys.path.insert(0, _BASE_DIR)
from wind_mcp_fetcher import wind_get_quote, wind_get_kline
for c in codes[:5]:
    raw = c
    clean = raw[2:] if raw.startswith(("sh","sz","bj","SH","SZ","BJ")) else raw
    is_fund = clean.startswith(("51","58","15","16"))
    print("CALL:", clean, "is_fund=", is_fund)
    q = wind_get_quote(clean, is_fund=is_fund)
    print("QUOTE:", q)
    k = wind_get_kline(clean, days=2, is_fund=is_fund)
    print("KLINE:", k)
