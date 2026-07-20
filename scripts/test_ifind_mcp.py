# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, r"E:\各种PY程序\15_每日工作流")

from morning_market_fetcher import _exec_ifind, _is_ifind_success

print("=== 测试 iFinD MCP ===")

tests = [
    ("美元指数", "global_stock", "global_stock_quotes", {"query": "美元指数最新价格"}),
    ("WTI原油", "global_stock", "global_stock_quotes", {"query": "WTI原油期货最新价格"}),
    ("LME铜", "global_stock", "global_stock_quotes", {"query": "LME铜期货最新价格"}),
    ("动力煤期货", "edb", "get_edb_data", {"query": "动力煤期货活跃合约收盘价"}),
    ("秦皇岛港库存", "edb", "get_edb_data", {"query": "秦皇岛港动力煤库存"}),
]

for name, server, tool, params in tests:
    result = _exec_ifind(server, tool, params)
    success = _is_ifind_success(result)
    print(f"\n{name}: {'✅ 成功' if success else '❌ 失败'}")
    if not success:
        print(f"  错误: {result.get('error', '未知错误')}")
    else:
        source = result.get('source', 'N/A')
        data = result.get('data', {})
        print(f"  来源: {source}")
        if isinstance(data, dict):
            answer = data.get('answer', '')
            if answer:
                print(f"  答案: {str(answer)[:200]}")
