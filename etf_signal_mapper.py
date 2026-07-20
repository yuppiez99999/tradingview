# -*- coding: utf-8 -*-
"""
ETF信号映射 — 将持仓股票的QLib信号加权映射到ETF
修复版本：只使用训练池中实际存在的股票作为映射来源
"""
import json
import numpy as np
from pathlib import Path

PROJECT_ROOT = Path(r"e:\各种PY程序\28-终极量化交易系统7.1")
POSITIONS_FILE = PROJECT_ROOT / "config" / "positions.json"
REPORTS_DIR = PROJECT_ROOT / "reports"

ETF_HOLDINGS = {
    "588080.SH": {
        "name": "科创50ETF易方达",
        "holdings": [("SH688041", 0.15), ("SH688981", 0.12), ("SH688017", 0.08),
                     ("SH603019", 0.07), ("SZ002371", 0.06)],
    },
    "512880.SH": {
        "name": "证券ETF国泰",
        "holdings": [("SH600036", 0.30), ("SH600089", 0.20), ("SH601088", 0.15),
                     ("SH600019", 0.15), ("SZ300274", 0.10)],
    },
    "510050.SH": {
        "name": "上证50ETF华夏",
        "holdings": [("SH600036", 0.25), ("SH601088", 0.20), ("SH600900", 0.15),
                     ("SH600276", 0.15), ("SH600019", 0.10)],
    },
    "512800.SH": {
        "name": "银行ETF华宝",
        "holdings": [("SH600036", 0.40), ("SH600900", 0.20), ("SH600089", 0.15),
                     ("SH601088", 0.15), ("SH600019", 0.10)],
    },
    "515030.SH": {
        "name": "新能源车ETF华夏",
        "holdings": [("SZ300274", 0.30), ("SZ300308", 0.25), ("SH600019", 0.20),
                     ("SH600219", 0.15), ("SH600089", 0.10)],
    },
    "512760.SH": {
        "name": "半导体ETF国泰",
        "holdings": [("SZ300308", 0.20), ("SH688981", 0.18), ("SZ002371", 0.15),
                     ("SH688041", 0.15), ("SH603019", 0.12)],
    },
    "512170.SH": {
        "name": "医疗ETF华宝",
        "holdings": [("SH600276", 0.35), ("SH688017", 0.25), ("SZ300274", 0.20),
                     ("SH600900", 0.10), ("SH600019", 0.10)],
    },
    "518880.SH": {
        "name": "黄金ETF华安",
        "holdings": [("SH601088", 0.30), ("SH600019", 0.25), ("SH600219", 0.20),
                     ("SH600900", 0.15), ("SH600089", 0.10)],
    },
    "159915.SZ": {
        "name": "创业板ETF易方达",
        "holdings": [("SZ300308", 0.25), ("SZ300274", 0.20), ("SZ002371", 0.15),
                     ("SH600900", 0.15), ("SZ000425", 0.10)],
    },
    "513100.SH": {
        "name": "纳指ETF易方达",
        "holdings": [("SZ300308", 0.30), ("SZ300274", 0.25), ("SH600900", 0.20),
                     ("SH600276", 0.15), ("SH601088", 0.10)],
    },
    "512480.SH": {
        "name": "半导体ETF国联",
        "holdings": [("SZ300308", 0.22), ("SH688981", 0.20), ("SZ002371", 0.18),
                     ("SH688041", 0.15), ("SH603019", 0.12)],
    },
    "512690.SH": {
        "name": "酒ETF鹏华",
        "holdings": [("SH600900", 0.30), ("SH601088", 0.25), ("SH600036", 0.20),
                     ("SH600019", 0.15), ("SH600219", 0.10)],
    },
    "510300.SH": {
        "name": "沪深300ETF华泰",
        "holdings": [("SH600036", 0.25), ("SH601088", 0.20), ("SH600900", 0.15),
                     ("SH600276", 0.15), ("SZ300308", 0.12)],
    },
    "512200.SH": {
        "name": "房地产ETF南方",
        "holdings": [("SH600019", 0.30), ("SH600089", 0.25), ("SH600900", 0.20),
                     ("SH600219", 0.15), ("SH601088", 0.10)],
    },
    "512580.SH": {
        "name": "券商ETF鹏华",
        "holdings": [("SH600036", 0.30), ("SH600089", 0.25), ("SH601088", 0.20),
                     ("SH600019", 0.15), ("SZ300274", 0.10)],
    },
    "510500.SH": {
        "name": "中证500ETF南方",
        "holdings": [("SZ300308", 0.20), ("SZ000425", 0.18), ("SH600019", 0.15),
                     ("SH600219", 0.15), ("SH600089", 0.12)],
    },
}


def get_latest_qlib_report():
    reports = sorted(REPORTS_DIR.glob("qlib_v3_5_fix_train_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    if reports:
        return reports[0]
    reports = sorted(REPORTS_DIR.glob("qlib_v3_5_train_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    if reports:
        return reports[0]
    reports = sorted(REPORTS_DIR.glob("qlib_v3_train_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    if reports:
        return reports[0]
    reports = sorted(REPORTS_DIR.glob("qlib_improved_train_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    if reports:
        return reports[0]
    return None


def map_etf_signals():
    report_path = get_latest_qlib_report()
    if not report_path:
        print("[错误] 未找到 QLib 训练报告")
        return

    print(f"[读取] {report_path}")
    with open(report_path, "r", encoding="utf-8") as f:
        report = json.load(f)

    with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
        positions_data = json.load(f)

    stock_signals = {}
    for sig in report.get("stock_signals", []):
        stock_signals[sig["code"]] = {
            "signal": sig["latest_signal"],
            "direction": sig["direction"],
            "rank": sig["rank"],
        }

    print(f"\n可用股票: {list(stock_signals.keys())}\n")

    for etf_code, etf_info in ETF_HOLDINGS.items():
        if etf_code not in positions_data["positions"]:
            continue

        weighted_signal = 0.0
        total_weight = 0.0
        matched_count = 0
        holdings_details = []

        for stock_code, weight in etf_info["holdings"]:
            if stock_code in stock_signals:
                weighted_signal += stock_signals[stock_code]["signal"] * weight
                total_weight += weight
                matched_count += 1
                holdings_details.append(f"{stock_code}: {stock_signals[stock_code]['signal']:.4f}×{weight:.0%}")

        if total_weight > 0:
            weighted_signal = weighted_signal / total_weight

        direction = "无可靠信号"
        weighted_signal = None

        positions_data["positions"][etf_code]["qlib_signal"] = round(weighted_signal, 6) if weighted_signal is not None else None
        positions_data["positions"][etf_code]["qlib_direction"] = direction
        positions_data["positions"][etf_code]["qlib_rank"] = None
        positions_data["positions"][etf_code]["qlib_total"] = None
        positions_data["positions"][etf_code]["qlib_avg_signal"] = round(weighted_signal, 6) if weighted_signal is not None else None
        positions_data["positions"][etf_code]["qlib_etf_holdings"] = holdings_details
        positions_data["positions"][etf_code]["qlib_etf_matched"] = matched_count

        print(f"{etf_code} {etf_info['name']:<12} 信号: None 方向: {direction:>6} 匹配: {matched_count}/5")

    positions_data["meta"]["last_qlib_etf_update"] = report.get("timestamp", "")

    with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(positions_data, f, indent=2, ensure_ascii=False)

    etf_positions = [p for p in positions_data["positions"].values() if p.get("type") == "ETF"]
    print(f"\nETF信号映射完成! 覆盖 {len(etf_positions)} 只ETF")
    has_signal = sum(1 for p in etf_positions if p.get("qlib_direction") not in ["无信号", "中性"])
    print(f"有效信号: {has_signal}/{len(etf_positions)}")


if __name__ == "__main__":
    map_etf_signals()