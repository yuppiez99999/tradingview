# -*- coding: utf-8 -*-
import sys
import os
sys.path.insert(0, r"e:\各种PY程序\15_每日工作流")

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
