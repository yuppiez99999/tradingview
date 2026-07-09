# -*- coding: utf-8 -*-
"""快速验证 data_provider 在无 numpy 环境下可正常导入并返回数据"""
import sys
print("Python:", sys.version)

try:
    from utils.data_provider import get_market_data, get_historical_data
    print("data_provider import OK")
except Exception as e:
    print("data_provider import FAIL:", e)
    sys.exit(1)

try:
    md = get_market_data("000001")
    print("market_data keys:", list(md.keys())[:10])
except Exception as e:
    print("get_market_data FAIL:", e)

try:
    hist = get_historical_data("000001", "1m")
    print("historical shape:", hist.shape)
except Exception as e:
    print("get_historical_data FAIL:", e)
