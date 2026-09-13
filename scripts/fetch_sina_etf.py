"""从新浪财经获取 ETF 历史日线并写入本地兜底目录"""

import json
import os
import time
from threading import Lock

import pandas as pd
import requests

from utils.safe_url import validate_url

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",  # noqa: E501
    "Referer": "https://finance.sina.com.cn/",
}

ETF_LIST = [
    ("sh588080", "科创50ETF易方达"),
    ("sh512760", "半导体ETF国泰"),
    ("sh588000", "科创50ETF华夏"),
    ("sh512880", "证券ETF国泰"),
    ("sh512800", "银行ETF华宝"),
    ("sh510050", "上证50ETF华夏"),
    ("sh510300", "沪深300ETF华泰"),
    ("sh510500", "中证500ETF南方"),
    ("sh512100", "中证1000ETF"),
    ("sh515030", "新能源车ETF华夏"),
    ("sh512170", "医疗ETF华宝"),
    ("sh518880", "黄金ETF华安"),
    ("sz159915", "创业板ETF易方达"),
]

FALLBACK_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "etf_fallback")
os.makedirs(FALLBACK_DIR, exist_ok=True)


# P1-4: 新浪HTTP数据源速率限制器(防止IP被封)
class SinaRateLimiter:
    """令牌桶速率限制器,确保对新浪API的请求不超过频率限制"""

    def __init__(self, min_interval: float = 0.5):
        self.min_interval = min_interval
        self.last_request_time = 0.0
        self.lock = Lock()
        self.request_count = 0

    def acquire(self):
        """获取许可,自动等待直到满足速率限制"""
        with self.lock:
            now = time.time()
            elapsed = now - self.last_request_time
            if elapsed < self.min_interval:
                wait_time = self.min_interval - elapsed
                time.sleep(wait_time)

            self.last_request_time = time.time()
            self.request_count += 1

            # 每100次请求后暂停5秒,避免触发反爬机制
            if self.request_count % 100 == 0:
                time.sleep(5)
                print(f"[SinaRateLimiter] 已发送{self.request_count}个请求,暂停5秒")


# 全局速率限制器实例
_sina_rate_limiter = SinaRateLimiter(min_interval=0.5)


def fetch_sina_etf(symbol: str, name: str) -> pd.DataFrame:
    """请求新浪财经 ETF 日线接口(带速率限制)"""
    urls = [
        f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen=1023",  # noqa: E501
        f"https://stock.finance.sina.com.cn/fundinfo/api/jsonp.php/IO.XSRV2.CallbackList['{symbol}']/NetValueInfo.getKLineData?symbol={symbol}&scale=240&ma=no&datalen=1023",  # noqa: E501
    ]
    for url in urls:
        try:
            # P1-4: 请求前获取速率限制许可
            _sina_rate_limiter.acquire()
            url = validate_url(url)

            resp = requests.get(url, headers=HEADERS, timeout=20, verify=True)
            text = resp.text.strip()
            if not text or text.startswith("{") or "null" in text.lower():
                continue
            data = json.loads(text)
            if not isinstance(data, list) or len(data) == 0:
                continue
            records = []
            for item in data:
                day = item.get("day") or item.get("date") or item.get("DATE")
                close = item.get("close") or item.get("CLOSE") or item.get("nav")
                if not day or close is None:
                    continue
                records.append({"日期": str(day)[:10], "收盘": float(close)})
            if records:
                df = pd.DataFrame(records)
                df["日期"] = pd.to_datetime(df["日期"])
                df = df.sort_values("日期").drop_duplicates("日期")
                return df
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as exc:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            print(f"  {name} {symbol} 请求失败: {exc}")
        time.sleep(0.3)
    return pd.DataFrame()


def main():
    saved = 0
    skipped = []
    for symbol, name in ETF_LIST:
        df = fetch_sina_etf(symbol, name)
        if df.empty:
            skipped.append((symbol, name, "返回空"))
            continue
        out_path = os.path.join(FALLBACK_DIR, f"{symbol[2:]}.json")
        payload = {
            "code": symbol[2:],
            "name": name,
            "source": "sina_finance",
            "note": "由新浪财经接口直接获取",
            "prices": [
                {
                    "日期": row["日期"].strftime("%Y-%m-%d"),
                    "收盘": round(float(row["收盘"]), 6),
                }
                for _, row in df.iterrows()
            ],
        }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        saved += 1
        print(
            f"saved {symbol[2:]} {name}: {len(df)} days, {df['日期'].iloc[0].date()} ~ {df['日期'].iloc[-1].date()}, end={df['收盘'].iloc[-1]}"  # noqa: E501
        )

    print(f"\n已保存 {saved} 个标的")
    if skipped:
        print("跳过:")
        for symbol, name, reason in skipped:
            print(f"  {symbol} {name}: {reason}")


if __name__ == "__main__":
    main()
