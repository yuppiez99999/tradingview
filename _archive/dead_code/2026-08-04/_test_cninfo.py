# -*- coding: utf-8 -*-
"""测试巨潮公告接口 — 搜索供应商/客户披露公告 (真实供应商-客户边来源)."""
from __future__ import annotations

import io
import sys
import time

import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Referer": "http://www.cninfo.com.cn/",
    "Origin": "http://www.cninfo.com.cn",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
}

# 巨潮公告查询接口
url = "http://www.cninfo.com.cn/new/hisAnnouncement/query"


def query_announcements(stock_code: str, key: str, page: int = 1) -> list[dict]:
    """搜索指定股票的公告 (按关键词)."""
    payload = {
        "pageNum": str(page),
        "pageSize": "10",
        "column": "szse",   # 深交所
        "tabName": "fulltext",
        "plate": "",
        "stock": stock_code,
        "searchkey": key,
        "secid": "",
        "category": "",
        "trade": "",
        "seDate": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    r = requests.post(url, data=payload, headers=HEADERS, timeout=15)
    r.encoding = "utf-8"
    return r.json().get("announcements") or []


# 用贵州茅台 (600519, 上交所) 和宁德时代测试
print("=== 巨潮公告查询测试 ===")
# 先查股东/客户相关公告
for code, name, key in [("000725", "京东方A", "前五名客户"), ("300750", "宁德时代", "前五大供应商")]:
    try:
        anns = query_announcements(code, key)
        print(f"\n[{name}] 关键词 '{key}' 公告数: {len(anns)}")
        for a in anns[:3]:
            print(f"  {a.get('announcementTitle','')} ({a.get('adjunctUrl','')})")
    except Exception as e:
        print(f"[FAIL] {name}: {type(e).__name__}: {e}")
    time.sleep(1)
