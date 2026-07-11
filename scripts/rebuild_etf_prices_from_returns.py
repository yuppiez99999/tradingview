# -*- coding: utf-8 -*-
"""从 returns_history.json 重建 ETF/股票价格序列并写入本地兜底目录"""
import json
import os
import pandas as pd
from datetime import datetime

BASE_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
FALLBACK_DIR = os.path.join(PROJECT_ROOT, "data", "etf_fallback")
RETURNS_PATH = os.path.join(PROJECT_ROOT, "config", "returns_history.json")
POSITIONS_PATH = os.path.join(PROJECT_ROOT, "config", "positions.json")


def load_returns():
    with open(RETURNS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    cols = data["columns"]
    index = pd.to_datetime(data["index"])
    df = pd.DataFrame(data["data"], index=index, columns=cols)
    return df


def load_positions():
    with open(POSITIONS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("positions", {})


def code_alias(code: str):
    code = str(code).strip()
    for prefix in ("sh", "sz", "bj", "SH", "SZ", "BJ"):
        if code.startswith(prefix):
            return code[len(prefix):]
    return code


def main():
    os.makedirs(FALLBACK_DIR, exist_ok=True)
    returns = load_returns()
    positions = load_positions()

    portfolio_codes = [
        "588080", "512760", "588000", "512880", "512800",
        "510050", "510300", "510500", "512100", "515030",
        "512170", "518880", "159915",
        "688041", "300308", "002371", "603019", "688017",
        "300274", "601088", "600276", "600900",
    ]

    saved = 0
    skipped = []
    for code in portfolio_codes:
        col = code_alias(code)
        if col not in returns.columns:
            skipped.append((code, "不在returns_history中"))
            continue

        # 找参考价格
        ref_price = None
        for key, item in positions.items():
            if code in key:
                ref_price = item.get("est_price") or item.get("avg_cost")
                if ref_price and float(ref_price) > 0:
                    ref_price = float(ref_price)
                else:
                    ref_price = None
                break

        if ref_price is None:
            skipped.append((code, "无参考价格"))
            continue

        rets = returns[col].dropna()
        if len(rets) < 60:
            skipped.append((code, f"收益数据不足: {len(rets)}"))
            continue

        prices = pd.Series(index=rets.index, dtype=float)
        prices.iloc[-1] = ref_price
        for i in range(len(rets) - 2, -1, -1):
            r = rets.iloc[i + 1]
            if pd.isna(r):
                prices.iloc[i] = prices.iloc[i + 1]
            else:
                prices.iloc[i] = prices.iloc[i + 1] / (1.0 + float(r))

        prices = prices[prices > 0].dropna()
        if len(prices) < 60:
            skipped.append((code, f"重建价格不足: {len(prices)}"))
            continue

        records = []
        for dt, price in prices.items():
            records.append({
                "日期": pd.Timestamp(dt).strftime("%Y-%m-%d"),
                "收盘": round(float(price), 6),
            })

        out_path = os.path.join(FALLBACK_DIR, f"{code}.json")
        payload = {
            "code": code,
            "source": "returns_history_reconstruction",
            "note": "由 returns_history.json 的收益序列反推价格，末端对齐 positions.json 当前价格。",
            "prices": records,
        }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        saved += 1
        print(f"saved {code}: {len(records)} days, {records[0]['日期']} ~ {records[-1]['日期']}, end={records[-1]['收盘']}")

    print(f"\n已保存 {saved} 个标的到 {FALLBACK_DIR}")
    if skipped:
        print("跳过:")
        for code, reason in skipped:
            print(f"  {code}: {reason}")


if __name__ == "__main__":
    main()
