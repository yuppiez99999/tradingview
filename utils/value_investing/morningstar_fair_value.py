#!/usr/bin/env python3
"""
从 Morningstar 筛选器 API 抓取所有有公允价值估计的股票，
计算潜在涨幅，输出 Top 100。
"""

import csv
import json
import os
import subprocess
import time
from datetime import datetime

API_BASE = (
    "https://lt.morningstar.com/api/rest.svc/klr5zyak8x/security/screener"
    "?page={page}&pageSize={page_size}"
    "&sortOrder=FairValueEstimate%20desc"
    "&outputType=json&version=1"
    "&languageId=en-US&currencyId=USD"
    "&universeIds=E0EXG%24XNAS%7CE0EXG%24XNYS"
    "&securityDataPoints=SecId%7CName%7CPriceCurrency%7CTenforeId%7CClosePrice"
    "%7CStarRatingM255%7CQuantitativeFairValue%7CFairValueEstimate"
    "%7CAssessmentOfFairValueUncertainty%7CEconomicMoat%7CIndustryName%7CSectorName"
    "&filters=FairValueEstimate:notnull"
)

PAGE_SIZE = 100
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")


def fetch_page(page: int) -> dict:
    url = API_BASE.format(page=page, page_size=PAGE_SIZE)
    result = subprocess.run(
        ["curl", "-s", "-H", "User-Agent: Mozilla/5.0", url],
        capture_output=True, text=True, timeout=30,
    )
    return json.loads(result.stdout)


def extract_ticker(tenforeid: str) -> str:
    if not tenforeid:
        return ""
    parts = tenforeid.split(".")
    return parts[-1] if len(parts) >= 3 else tenforeid


def main():

    # 第一页获取总数
    data = fetch_page(1)
    total = data.get("total", 0)
    all_rows = data.get("rows", [])
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE

    # 抓取剩余页
    for page in range(2, total_pages + 1):
        if page % 10 == 0 or page == total_pages:
            pass
        try:
            data = fetch_page(page)
            rows = data.get("rows", [])
            if not rows:
                break
            all_rows.extend(rows)
            time.sleep(0.3)
        except Exception:
            time.sleep(1)


    # 计算潜在涨幅
    stocks = []
    for row in all_rows:
        fair_value = row.get("FairValueEstimate")
        close_price = row.get("ClosePrice")
        if not fair_value or not close_price or close_price <= 0:
            continue

        ticker = extract_ticker(row.get("TenforeId", ""))
        upside = (fair_value - close_price) / close_price * 100

        stocks.append({
            "ticker": ticker,
            "name": row.get("Name", ""),
            "close_price": round(close_price, 2),
            "fair_value": round(fair_value, 2),
            "upside_pct": round(upside, 1),
            "star_rating": row.get("StarRatingM255", ""),
            "moat": row.get("EconomicMoat", ""),
            "uncertainty": row.get("AssessmentOfFairValueUncertainty", ""),
            "sector": row.get("SectorName", ""),
            "industry": row.get("IndustryName", ""),
        })

    # 按潜在涨幅排序
    stocks.sort(key=lambda x: x["upside_pct"], reverse=True)

    # 输出 Top 100

    for i, s in enumerate(stocks[:100], 1):
        pass

    # 保存完整数据到 CSV
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    csv_path = os.path.join(OUTPUT_DIR, f"morningstar_fair_value_{today}.csv")

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "rank", "ticker", "name", "close_price", "fair_value",
            "upside_pct", "star_rating", "moat", "uncertainty", "sector", "industry"
        ])
        writer.writeheader()
        for i, s in enumerate(stocks, 1):
            writer.writerow({"rank": i, **s})


    # 统计摘要
    undervalued = [s for s in stocks if s["upside_pct"] > 0]
    [s for s in stocks if s["upside_pct"] < 0]
    if undervalued:
        sum(s["upside_pct"] for s in undervalued) / len(undervalued)
    if stocks:
        [s for s in stocks if s["moat"] == "Wide" and s["upside_pct"] > 0]


if __name__ == "__main__":
    main()
