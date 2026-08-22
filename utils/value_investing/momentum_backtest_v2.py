#!/usr/bin/env python3
"""
动量发现 + 价值验证 回测工具 v2
回测标的：NVDA / AMD / MU（AI芯片三巨头）
核心问题：这个框架能否在AI浪潮早期捕捉到这些股票？

NVDA：手工录入关键节点（Yahoo API被限制）
AMD/MU：从JSON文件加载真实日线数据
"""

import json
import os
from collections import OrderedDict
from datetime import datetime

# ============================================================
# 基本面数据（手工录入，比API更准确）
# ============================================================

FUNDAMENTALS = {
    "NVDA": {
        "name": "英伟达",
        "quarters": OrderedDict([
            ("2022-08-24", {"rev": 67.0, "rev_yoy": -4.0, "gm": 43.5, "eps_beat": -24.0, "label": "FY23Q2(Jul22) 游戏崩盘"}),
            ("2022-11-16", {"rev": 59.3, "rev_yoy": -17.0, "gm": 53.6, "eps_beat": 7.4, "label": "FY23Q3(Oct22) 数据中心撑住"}),
            ("2023-02-22", {"rev": 60.5, "rev_yoy": -21.0, "gm": 63.3, "eps_beat": 10.0, "label": "FY23Q4(Jan23) 毛利率拐点!"}),
            ("2023-05-24", {"rev": 71.9, "rev_yoy": -13.0, "gm": 64.6, "eps_beat": 18.5, "label": "FY24Q1(Apr23) ★营收拐点+EPS大超预期"}),
            ("2023-08-23", {"rev": 135.1, "rev_yoy": 101.0, "gm": 70.1, "eps_beat": 29.0, "label": "FY24Q2(Jul23) ★★爆发!营收翻倍"}),
            ("2023-11-21", {"rev": 181.2, "rev_yoy": 206.0, "gm": 74.0, "eps_beat": 19.0, "label": "FY24Q3(Oct23) ★★★3倍增长"}),
            ("2024-02-21", {"rev": 221.0, "rev_yoy": 265.0, "gm": 76.0, "eps_beat": 12.0, "label": "FY24Q4(Jan24) 巅峰增速"}),
            ("2024-05-22", {"rev": 260.4, "rev_yoy": 262.0, "gm": 78.4, "eps_beat": 9.0, "label": "FY25Q1(Apr24)"}),
        ]),
    },
    "AMD": {
        "name": "AMD",
        "quarters": OrderedDict([
            ("2022-08-02", {"rev": 65.5, "rev_yoy": 70.0, "gm": 46.0, "eps_beat": 5.0, "label": "Q2 2022 高峰"}),
            ("2022-11-01", {"rev": 55.7, "rev_yoy": 29.0, "gm": 42.0, "eps_beat": 2.3, "label": "Q3 2022 回落"}),
            ("2023-01-31", {"rev": 55.0, "rev_yoy": 16.0, "gm": 43.0, "eps_beat": 6.2, "label": "Q4 2022"}),
            ("2023-05-02", {"rev": 53.5, "rev_yoy": -9.0, "gm": 44.0, "eps_beat": 7.1, "label": "Q1 2023 底部"}),
            ("2023-08-01", {"rev": 54.0, "rev_yoy": -18.0, "gm": 46.0, "eps_beat": 1.8, "label": "Q2 2023"}),
            ("2023-10-31", {"rev": 58.0, "rev_yoy": 4.0, "gm": 47.0, "eps_beat": 6.1, "label": "Q3 2023 开始反弹"}),
            ("2024-01-30", {"rev": 61.7, "rev_yoy": 10.0, "gm": 47.0, "eps_beat": 3.7, "label": "Q4 2023 ★MI300发布"}),
            ("2024-04-30", {"rev": 54.7, "rev_yoy": 2.0, "gm": 47.0, "eps_beat": 3.3, "label": "Q1 2024"}),
            ("2024-07-30", {"rev": 58.3, "rev_yoy": 9.0, "gm": 49.0, "eps_beat": 1.5, "label": "Q2 2024"}),
            ("2024-10-29", {"rev": 68.2, "rev_yoy": 18.0, "gm": 50.0, "eps_beat": 4.5, "label": "Q3 2024 ★数据中心加速"}),
        ]),
    },
    "MU": {
        "name": "美光科技",
        "quarters": OrderedDict([
            ("2022-09-29", {"rev": 66.4, "rev_yoy": -20.0, "gm": 40.0, "eps_beat": -5.0, "label": "FY22Q4 开始下滑"}),
            ("2022-12-21", {"rev": 40.9, "rev_yoy": -47.0, "gm": 22.0, "eps_beat": 22.0, "label": "FY23Q1 暴跌但超预期"}),
            ("2023-03-28", {"rev": 36.9, "rev_yoy": -53.0, "gm": 11.0, "eps_beat": 5.0, "label": "FY23Q2 谷底"}),
            ("2023-06-28", {"rev": 37.5, "rev_yoy": -57.0, "gm": -8.0, "eps_beat": 15.0, "label": "FY23Q3 毛利率转负"}),
            ("2023-09-27", {"rev": 40.1, "rev_yoy": -40.0, "gm": -1.0, "eps_beat": 18.0, "label": "FY23Q4 ★HBM拐点信号"}),
            ("2023-12-20", {"rev": 47.3, "rev_yoy": 16.0, "gm": 20.0, "eps_beat": 68.0, "label": "FY24Q1 ★★营收反转!EPS超68%"}),
            ("2024-03-20", {"rev": 58.2, "rev_yoy": 58.0, "gm": 28.0, "eps_beat": 82.0, "label": "FY24Q2 ★★★爆发"}),
            ("2024-06-26", {"rev": 68.1, "rev_yoy": 82.0, "gm": 35.4, "eps_beat": 6.9, "label": "FY24Q3"}),
            ("2024-09-25", {"rev": 77.5, "rev_yoy": 93.0, "gm": 36.5, "eps_beat": 5.4, "label": "FY24Q4"}),
        ]),
    },
}


# ============================================================
# 从JSON文件加载价格数据
# ============================================================

def load_prices_from_json(filepath):
    with open(filepath) as f:
        data = json.load(f)
    result = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    rows = []
    for i, ts in enumerate(timestamps):
        dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        c = quote["close"][i]
        v = quote["volume"][i]
        h = quote["high"][i]
        if c and v and h:
            rows.append({"date": dt, "close": c, "high": h, "volume": v})
    return rows


# ============================================================
# 动量发现引擎
# ============================================================

def scan_momentum(prices):
    signals = []
    for i in range(60, len(prices)):
        row = prices[i]
        close = row["close"]
        past_60_highs = [prices[j]["high"] for j in range(i - 60, i)]
        is_60d_high = close > max(past_60_highs)
        vol_5 = sum(prices[j]["volume"] for j in range(i - 4, i + 1)) / 5
        vol_20 = sum(prices[j]["volume"] for j in range(i - 19, i + 1)) / 20
        is_volume_surge = vol_5 > vol_20 * 1.5
        close_30d_ago = prices[i - 30]["close"]
        pct_30d = (close - close_30d_ago) / close_30d_ago * 100

        if is_60d_high and is_volume_surge:
            signals.append({
                "date": row["date"],
                "close": round(close, 2),
                "pct_30d": round(pct_30d, 1),
                "vol_ratio": round(vol_5 / vol_20, 2),
            })
    return signals


# ============================================================
# 价值验证引擎
# ============================================================

def find_fund(ticker, date):
    quarters = list(FUNDAMENTALS[ticker]["quarters"].items())
    latest = None
    prev = None
    for idx, (qd, qf) in enumerate(quarters):
        if qd <= date:
            prev = latest
            latest = (qd, qf)
    return latest, prev


def verify(fund, prev_fund):
    if not fund:
        return 0, {}
    d = fund[1]
    pd = prev_fund[1] if prev_fund else None

    checks = {}
    # 1.营收加速（同比增速改善）
    if pd:
        checks["营收加速"] = d["rev_yoy"] > pd["rev_yoy"]
    else:
        checks["营收加速"] = d["rev_yoy"] > 20

    # 2.毛利率方向
    if pd:
        checks["毛利率↑"] = d["gm"] > pd["gm"] or d["gm"] > 50
    else:
        checks["毛利率↑"] = d["gm"] > 40

    # 3.EPS超预期>10%
    checks["盈利惊喜"] = d["eps_beat"] > 10

    # 4.营收高增>15%
    checks["营收高增"] = d["rev_yoy"] > 15

    # 5.毛利率>40%
    checks["毛利健康"] = d["gm"] > 40

    score = sum(1 for v in checks.values() if v)
    return score, checks


# ============================================================
# 回测主逻辑
# ============================================================

def backtest(ticker, prices):
    FUNDAMENTALS[ticker]["name"]

    signals = scan_momentum(prices)

    seen_months = set()
    buy_signals = []
    reject_signals = []

    for sig in signals:
        mk = sig["date"][:7]
        if mk in seen_months:
            continue
        seen_months.add(mk)

        fund, prev = find_fund(ticker, sig["date"])
        score, checks = verify(fund, prev)

        entry = {
            "date": sig["date"],
            "close": sig["close"],
            "pct_30d": sig["pct_30d"],
            "vol_ratio": sig["vol_ratio"],
            "score": score,
            "checks": checks,
            "fund_label": fund[1]["label"] if fund else "N/A",
            "rev_yoy": fund[1]["rev_yoy"] if fund else "N/A",
            "gm": fund[1]["gm"] if fund else "N/A",
            "eps_beat": fund[1]["eps_beat"] if fund else "N/A",
        }

        if score >= 3:
            buy_signals.append(entry)
        else:
            reject_signals.append(entry)

    # 输出关键信号
    first_buy = None
    for bs in buy_signals:
        if bs["date"] < "2022-06-01":
            continue
        if not first_buy:
            first_buy = bs
        " ".join(
            f"{'✅' if v else '❌'}{k}" for k, v in bs["checks"].items()
        )

    # 展示部分被拒绝的信号（帮助理解筛选效果）
    early_rejects = [r for r in reject_signals if "2022-06" <= r["date"] <= "2023-06"]
    if early_rejects:
        for r in early_rejects[:3]:
            " ".join(
                f"{'✅' if v else '❌'}{k}" for k, v in r["checks"].items()
            )

    # 计算收益
    if first_buy:
        final = prices[-1]
        (final["close"] - first_buy["close"]) / first_buy["close"] * 100

    return first_buy


# ============================================================
# NVDA手工分析（无法获取日线数据）
# ============================================================

def nvda_manual_analysis():

    # NVDA关键价格节点（拆股调整后）
    key_prices = [
        ("2022-10-14", 11.2, "年内低点"),
        ("2023-01-06", 14.3, "ChatGPT催化后第一波"),
        ("2023-01-27", 19.9, "★ 创60日新高+放量突破 → 动量触发"),
        ("2023-02-22", 23.4, "FY23Q4财报：毛利率63.3%拐点+EPS超10%"),
        ("2023-05-24", 30.5, "FY24Q1财报前"),
        ("2023-05-25", 37.9, "★★ FY24Q1财报后gap up 24%：营收超预期18.5%"),
        ("2023-08-24", 49.3, "FY24Q2：营收翻倍101%"),
        ("2024-01-08", 52.2, "CES 2024"),
        ("2024-03-08", 87.5, "接近历史高点"),
        ("2024-06-20", 140.8, "拆股后ATH"),
        ("2025-01-06", 149.4, "2025年初"),
    ]

    for date, price, note in key_prices:
        pass

    # 分析动量信号


    fund1, prev1 = find_fund("NVDA", "2023-01-27")
    s1, c1 = verify(fund1, prev1)
    " ".join(f"{'✅' if v else '❌'}{k}" for k, v in c1.items())
    if s1 >= 3:
        pass
    else:
        pass

    fund2, prev2 = find_fund("NVDA", "2023-02-23")
    s2, c2 = verify(fund2, prev2)
    " ".join(f"{'✅' if v else '❌'}{k}" for k, v in c2.items())
    if s2 >= 3:
        pass
    else:
        pass

    fund3, prev3 = find_fund("NVDA", "2023-05-25")
    s3, c3 = verify(fund3, prev3)
    " ".join(f"{'✅' if v else '❌'}{k}" for k, v in c3.items())
    if s3 >= 3:
        pass

    fund4, prev4 = find_fund("NVDA", "2023-08-24")
    s4, c4 = verify(fund4, prev4)
    " ".join(f"{'✅' if v else '❌'}{k}" for k, v in c4.items())

    # 收益计算
    scenarios = [
        ("2023-01-27（边缘信号）", 19.9, 149.4, "2025-01"),
        ("2023-02-22（财报确认）", 23.4, 149.4, "2025-01"),
        ("2023-05-25（AI炸弹）", 37.9, 149.4, "2025-01"),
    ]
    for label, buy_p, sell_p, sell_d in scenarios:
        (sell_p - buy_p) / buy_p * 100


# ============================================================
# 主程序
# ============================================================

if __name__ == "__main__":

    # NVDA：手工分析
    nvda_manual_analysis()

    # AMD：真实日线回测
    amd_file = "/tmp/AMD_prices.json"
    if os.path.exists(amd_file):
        amd_prices = load_prices_from_json(amd_file)
        amd_first = backtest("AMD", amd_prices)
    else:
        pass

    # MU：真实日线回测
    mu_file = "/tmp/MU_prices.json"
    if os.path.exists(mu_file):
        mu_prices = load_prices_from_json(mu_file)
        mu_first = backtest("MU", mu_prices)
    else:
        pass

    # 总结
