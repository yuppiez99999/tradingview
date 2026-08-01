#!/usr/bin/env python3
"""用新浪财经免费 API 拉取 511010 国债 ETF 收盘价.

这是 P4 层级免费回退方案 (项目硬约束: Wind/MCP/iFinD 不可用时回退).
"""
import json
import sys
import time

BASE = r'e:\各种PY程序\28-终极量化交易系统8.4'
sys.path.insert(0, BASE)

def fetch_sina_price(wind_code):
    """
    新浪财经 API 获取单只基金/股票最新价:
    http://push2his.eastmoney.com/api/qt/kishot?symb={code}&udn=d31&callback=jQuery...
    其中 code 对于上证5年期国债ETF是 "511010" (不带 .SH)
    """
    url = f"http://push2his.eastmoney.com/api/qt/kishot?symb={wind_code}&udn=d1"
    time.time()
    try:
        import requests
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        if resp.status_code == 200:
            # 返回 JSON, 如 {"data":{"name":"上证5年期国债ETF","price":"140.824","change":"0.94","updown":"+0.94",...}}
            data = resp.json()
            if 'data' in data and isinstance(data['data'], dict):
                d = data['data']
                price_str = str(d.get('price', ''))
                name = d.get('name', '')
                d.get('change', '').strip('%')
                if price_str.replace('.', '', 1).isdigit():
                    return {
                        'price': float(price_str),
                        'name': name,
                        'source': 'sina_stock',
                        'update_time': time.time(),
                    }
    except Exception:
        pass
    return None

# 国债ETF 511010 在 Wind 里编码为 "511010.SH" 或 "511010"
result = fetch_sina_price("511010")
if result:
    print(json.dumps(result, indent=2, ensure_ascii=False))
else:
    print("FAIL: 获取失败")
