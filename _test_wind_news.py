# -*- coding: utf-8 -*-
"""测试 Wind MCP 新闻接口"""
import sys
sys.path.insert(0, r'e:\各种PY程序\28-终极量化交易系统7.1')
from wind_mcp_fetcher import wind_search_news
import json

# 测试1: 简单关键词
print('=== 测试1: 海光信息 ===')
items = wind_search_news('海光信息', top_k=3)
print(f'返回 {len(items)} 条新闻')
for i, item in enumerate(items[:2], 1):
    print(f'--- {i} ---')
    print(f'  title: {item.get("title", "")[:80]}')
    print(f'  publish_time: {item.get("publish_time", "")}')
    print(f'  source: {item.get("source", "")}')
    print(f'  snippet: {item.get("snippet", "")[:120]}')

# 测试2: 另一只股票
print()
print('=== 测试2: 中国神华 ===')
items2 = wind_search_news('中国神华', top_k=3)
print(f'返回 {len(items2)} 条新闻')
for i, item in enumerate(items2[:2], 1):
    print(f'--- {i} ---')
    print(f'  title: {item.get("title", "")[:80]}')
    print(f'  publish_time: {item.get("publish_time", "")}')

# 测试3: 美的集团
print()
print('=== 测试3: 美的集团 ===')
items3 = wind_search_news('美的集团', top_k=2)
print(f'返回 {len(items3)} 条新闻')
for i, item in enumerate(items3[:1], 1):
    print(f'--- {i} ---')
    print(f'  title: {item.get("title", "")[:80]}')
    print(f'  publish_time: {item.get("publish_time", "")}')

# 输出第一条的完整结构
if items:
    print()
    print('=== 完整结构 ===')
    print(json.dumps(items[0], ensure_ascii=False, indent=2))
