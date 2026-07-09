# -*- coding: utf-8 -*-
"""诊断：打印原始 HTTP 返回"""
import requests

session = requests.Session()
session.trust_env = False
session.proxies = {"http": None, "https": None}
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://stock.finance.sina.com.cn/",
}

urls = [
    "https://hq.sinajs.cn/list=nf_IF0,nf_IF2506,nf_IF",
    "https://qt.gtimg.cn/q=IF,IF2506,IF0",
    "https://hq.sinajs.cn/list=sz510300",
    "https://qt.gtimg.cn/q=sz510300",
]
for url in urls:
    try:
        resp = session.get(url, headers=headers, timeout=10, verify=False)
        print(f"URL: {url}")
        print(f"  status={resp.status_code} len={len(resp.text)}")
        print(f"  body={resp.text[:300]!r}")
        print()
    except Exception as e:
        print(f"URL: {url} ERROR: {e}\n")
