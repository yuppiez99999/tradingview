"""
信号后处理 — 行业中性化 + 排名归一化
通过组内排名消除行业偏差，提升信号质量
"""

import json
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent
POSITIONS_FILE = PROJECT_ROOT / "config" / "positions.json"
REPORTS_DIR = PROJECT_ROOT / "reports"

INDUSTRY_GROUPS = {
    "科技": ["SZ300308", "SZ002371", "SH603019", "SH688041", "SH688981", "SZ300274"],
    "金融": ["SH600036"],
    "能源": ["SH601088", "SH600900"],
    "医药": ["SH600276", "SH688017"],
    "材料": ["SH600019", "SH600219"],
    "机械": ["SZ000425"],
}


def get_latest_qlib_report():
    preferred = [
        "qlib_v7_train_*.json",
        "qlib_v9_train_*.json",
        "qlib_v8_train_*.json",
        "qlib_v6_train_*.json",
        "qlib_v5_train_*.json",
        "qlib_v4_train_*.json",
        "qlib_v3_5_fix_train_*.json",
        "qlib_v3_5_train_*.json",
        "qlib_v3_train_*.json",
        "qlib_improved_train_*.json",
    ]
    for pattern in preferred:
        reports = sorted(REPORTS_DIR.glob(pattern), key=lambda x: x.stat().st_mtime, reverse=True)
        if reports:
            return reports[0]
    return None


def apply_industry_neutralization():
    report_path = get_latest_qlib_report()
    if not report_path:
        print("[错误] 未找到 QLib 训练报告")
        return

    print(f"[读取] {report_path}")
    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)

    with open(POSITIONS_FILE, encoding="utf-8") as f:
        positions_data = json.load(f)

    stock_signals = {}
    if "stock_signals" in report:
        for sig in report["stock_signals"]:
            stock_signals[sig["code"]] = {
                "raw_signal": sig["latest_signal"],
                "direction": sig["direction"],
                "name": sig["name"],
            }
    elif "signals" in report:
        for symbol, signal in report["signals"].items():
            direction = "看多" if signal > 0 else "看空"
            stock_signals[symbol] = {
                "raw_signal": float(signal),
                "direction": direction,
                "name": "",
            }

    print("\n--- 行业中性化处理 ---")
    for industry, stocks in INDUSTRY_GROUPS.items():
        valid_stocks = [s for s in stocks if s in stock_signals]
        if len(valid_stocks) < 2:
            print(f"  {industry}: 股票数量不足，跳过")
            continue

        signals = [stock_signals[s]["raw_signal"] for s in valid_stocks]
        ranks = np.argsort(np.argsort(signals)) / (len(signals) - 1)
        ranks = ranks * 2 - 1

        print(f"\n  {industry} ({len(valid_stocks)}只):")
        for i, stock_code in enumerate(valid_stocks):
            neutral_signal = float(ranks[i])
            stock_signals[stock_code]["neutral_signal"] = neutral_signal
            direction = "看多" if neutral_signal > 0.3 else ("看空" if neutral_signal < -0.3 else "中性")
            stock_signals[stock_code]["neutral_direction"] = direction
            print(
                f"    {stock_code} {stock_signals[stock_code]['name']:6s} 原始:{stock_signals[stock_code]['raw_signal']:+.4f} → 中性化:{neutral_signal:+.4f} ({direction})"
            )

    def convert_code(code):
        if code.startswith("SH"):
            return code[2:] + ".SH"
        elif code.startswith("SZ"):
            return code[2:] + ".SZ"
        return code

    print("\n--- 更新持仓数据 ---")
    for stock_code, sig_info in stock_signals.items():
        pos_code = convert_code(stock_code)
        if pos_code in positions_data["positions"]:
            pos = positions_data["positions"][pos_code]
            pos["qlib_neutral_signal"] = sig_info.get("neutral_signal")
            pos["qlib_neutral_direction"] = sig_info.get("neutral_direction", pos.get("qlib_direction"))
            print(f"    {stock_code} → {pos_code} {pos.get('name', '')} 中性化:{sig_info.get('neutral_direction', '')}")

    positions_data["meta"]["last_qlib_neutral_update"] = report.get("timestamp", "")

    with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(positions_data, f, indent=2, ensure_ascii=False)

    print("\n行业中性化完成!")


if __name__ == "__main__":
    apply_industry_neutralization()
