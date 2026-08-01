import os
import sys

base = os.path.abspath(r"..\..\15_每日工作流")
print('cwd=', os.getcwd())
print('insert=', base)
print('exists=', os.path.isdir(base))
sys.path.insert(0, base)
print('sys.path=', sys.path[:3])

from morning_market_fetcher import (
    _fetch_sina_coal_price,
    _fetch_ths_coal_price,
    _web_search_fallback_coal_price,
)

print("SINA:", _fetch_sina_coal_price())
print("THS:", _fetch_ths_coal_price())
print("WEB:", _web_search_fallback_coal_price())

from llm_client import test_connection

print("LLM:", test_connection())
