# -*- coding: utf-8 -*-
"""
动态生成 trade_plan_{YYYYMMDD}.json — 基于 500万建仓计划 + 23 标的新权重

用法:
    py -3.11 generate_daily_trade_plan.py [YYYY-MM-DD] [--capital 5000000]

默认:
    - 日期 = 下一个交易日 (跳过周末)
    - 资金 = 5,000,000

输出:
    trade_plans/trade_plan_{YYYYMMDD}.json

阶段逻辑 (4阶段建仓):
    P1 (7/6-7/16):  底仓 35% = 105 万 / 10 交易日 / 日均 10.5 万
    P2 (7/17-7/30): 加仓 30% = 90 万 / 10 交易日 / 日均 9 万
    P3 (8/1-8/15):  调仓 20% = 60 万 / 11 交易日 / 日均 5.5 万
    P4 (8/16-9/26): 最终 15% = 45 万 / 30 交易日 / 日均 1.5 万
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

BASE = Path(__file__).parent
PLAN_DIR = BASE / "trade_plans"
PLAN_DIR.mkdir(exist_ok=True)

BUILD_PLAN_FILE = BASE.parent / "500万建仓计划_20260706.json"

# ============================================================
# 4 阶段建仓计划 (基于 2026年交易计划.md)
# ============================================================
PHASES = [
    {"phase": 1, "name": "第一阶段-底仓建立",
     "start": "2026-07-06", "end": "2026-07-16",
     "capital_ratio": 0.35, "phase_capital": 1_050_000,
     "duration_days": 10, "strategy": "首批 35% 建仓 + 启动对冲"},
    {"phase": 2, "name": "第二阶段-加仓",
     "start": "2026-07-17", "end": "2026-07-30",
     "capital_ratio": 0.30, "phase_capital": 900_000,
     "duration_days": 10, "strategy": "加仓 30% + 优化结构"},
    {"phase": 3, "name": "第三阶段-调仓",
     "start": "2026-07-31", "end": "2026-08-15",
     "capital_ratio": 0.20, "phase_capital": 600_000,
     "duration_days": 11, "strategy": "调仓 20% + 风险再平衡"},
    {"phase": 4, "name": "第四阶段-最终调整",
     "start": "2026-08-16", "end": "2026-09-26",
     "capital_ratio": 0.15, "phase_capital": 450_000,
     "duration_days": 30, "strategy": "最终 15% + 完成建仓"},
]


def next_trading_day(date: datetime) -> datetime:
    """获取下一个交易日 (跳过周末)"""
    d = date + timedelta(days=1)
    while d.weekday() >= 5:  # 5=周六, 6=周日
        d += timedelta(days=1)
    return d


def get_phase(date: datetime) -> Dict:
    """根据日期判断当前阶段"""
    date_str = date.strftime("%Y-%m-%d")
    for p in PHASES:
        if p["start"] <= date_str <= p["end"]:
            # 计算 day_index
            start = datetime.strptime(p["start"], "%Y-%m-%d")
            # 仅计算工作日
            day_index = 0
            cur = start
            while cur <= date:
                if cur.weekday() < 5:
                    day_index += 1
                cur += timedelta(days=1)
            return {**p, "day_index": day_index}
    # 默认返回第一阶段
    return {**PHASES[0], "day_index": 1}


def load_build_plan() -> Dict:
    """加载 500万建仓计划 (含 23 标的新权重)"""
    with open(BUILD_PLAN_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================
# 标的基础信息 (est_price / 风格 / 风险)
# ============================================================
SYMBOL_INFO = {
    "sz588000": {"name": "科创50ETF华夏", "est_price": 1.05, "style": "高端制造", "risk": "高", "lots": 100},
    "sh688041": {"name": "海光信息", "est_price": 85.0, "style": "科技", "risk": "高", "lots": 100},
    "sz002371": {"name": "北方华创", "est_price": 350.0, "style": "科技", "risk": "高", "lots": 100},
    "sh688981": {"name": "中芯国际", "est_price": 95.0, "style": "科技", "risk": "高", "lots": 100},
    "sz300308": {"name": "中际旭创", "est_price": 120.0, "style": "科技", "risk": "高", "lots": 100},
    "sz000425": {"name": "徐工机械", "est_price": 8.5, "style": "制造", "risk": "中", "lots": 100},
    "sh601088": {"name": "中国神华", "est_price": 40.70, "style": "顺周期", "risk": "中", "lots": 100},
    "sh600276": {"name": "恒瑞医药", "est_price": 50.0, "style": "医药", "risk": "中高", "lots": 100},
    "sh600900": {"name": "长江电力", "est_price": 27.05, "style": "防御", "risk": "低", "lots": 100},
    "sz515180": {"name": "易方达中证红利ETF", "est_price": 5.0, "style": "红利", "risk": "中", "lots": 100},
    "sh600036": {"name": "招商银行", "est_price": 38.0, "style": "银行", "risk": "中", "lots": 100},
    "sz518880": {"name": "黄金ETF华安", "est_price": 5.85, "style": "避险", "risk": "中", "lots": 100},
    # 2026-07-09 新增 6 标的
    "sz300274": {"name": "阳光电源", "est_price": 45.0, "style": "新能源", "risk": "中高", "lots": 100},
    "sh603019": {"name": "中科曙光", "est_price": 94.42, "style": "科技", "risk": "高", "lots": 100},
    "sh600089": {"name": "特变电工", "est_price": 25.0, "style": "制造", "risk": "中", "lots": 100},
    "sh688017": {"name": "绿的谐波", "est_price": 180.0, "style": "制造", "risk": "中高", "lots": 100},
    "sh600219": {"name": "南山铝业", "est_price": 4.19, "style": "资源", "risk": "中", "lots": 100},
    "sh600019": {"name": "宝钢股份", "est_price": 5.61, "style": "资源", "risk": "中", "lots": 100},
    # 2026-07-09 再增 5 标的 (来自盘前综合报告十五五对标)
    "sz000680": {"name": "山推股份", "est_price": 7.50, "style": "制造", "risk": "中", "lots": 100},
    "sz000333": {"name": "美的集团", "est_price": 75.00, "style": "制造", "risk": "中", "lots": 100},
    "sz000408": {"name": "藏格矿业", "est_price": 35.00, "style": "资源", "risk": "中高", "lots": 100},
    "sz000975": {"name": "山金国际", "est_price": 15.00, "style": "资源", "risk": "中", "lots": 100},
    "sz002422": {"name": "科伦药业", "est_price": 28.00, "style": "医药", "risk": "中", "lots": 100},
}


def generate_orders(trade_date: str, phase: Dict, build_plan: Dict,
                    stock_capital: float = 3_000_000) -> Dict:
    """生成当日买卖订单 (上午 + 下午批次)

    策略:
        - 每日建仓资金 = phase_capital / duration_days
        - 按 23 标的权重分配
        - 每个标的按 100 股整数倍取整
        - 上午 50% / 下午 50%
    """
    day_capital = phase["phase_capital"] / phase["duration_days"]
    # 股票部分占 60% (剩余 40% 为对冲资金)
    stock_day_capital = day_capital * (stock_capital / (stock_capital + 2_000_000))

    target_portfolio = build_plan.get("target_portfolio", {})

    morning_orders: List[Dict] = []
    afternoon_orders: List[Dict] = []
    priority = 1
    total_amount = 0.0

    for symbol, info in target_portfolio.items():
        weight = info["weight"]
        target_amount = stock_day_capital * weight

        est_price = SYMBOL_INFO.get(symbol, {}).get("est_price", 10.0)
        lots_size = SYMBOL_INFO.get(symbol, {}).get("lots", 100)
        name = SYMBOL_INFO.get(symbol, {}).get("name", info.get("name", symbol))
        style = SYMBOL_INFO.get(symbol, {}).get("style", "其他")
        risk = SYMBOL_INFO.get(symbol, {}).get("risk", "中")

        # 计算股数 (按手数取整)
        raw_shares = int(target_amount / est_price)
        shares = (raw_shares // lots_size) * lots_size
        if shares <= 0:
            shares = lots_size  # 最少 1 手

        est_amount = shares * est_price
        limit_price = round(est_price * 1.008, 3)  # +0.8% 限价缓冲

        # 上午/下午拆分 (50% / 50%)
        morning_shares = shares // 2
        afternoon_shares = shares - morning_shares

        if morning_shares > 0:
            morning_orders.append({
                "priority": priority,
                "code": symbol,
                "name": name,
                "session": "morning",
                "shares": morning_shares,
                "est_price": est_price,
                "limit_price": round(est_price * 1.008, 3),
                "est_amount": round(morning_shares * est_price, 2),
                "side": "BUY",
                "order_type": "LIMIT",
                "style": style,
                "risk": risk,
                "note": f"上午批次 09:30-10:30 (阶段{phase['phase']} 第{phase['day_index']}天)",
                "technical_alpha": 1.0,
            })
            priority += 1
            total_amount += morning_shares * est_price

        if afternoon_shares > 0:
            afternoon_orders.append({
                "priority": priority,
                "code": symbol,
                "name": name,
                "session": "afternoon",
                "shares": afternoon_shares,
                "est_price": est_price,
                "limit_price": round(est_price * 1.008, 3),
                "est_amount": round(afternoon_shares * est_price, 2),
                "side": "BUY",
                "order_type": "LIMIT",
                "style": style,
                "risk": risk,
                "note": f"下午批次 14:00-14:30 (阶段{phase['phase']} 第{phase['day_index']}天)",
                "technical_alpha": 1.0,
            })
            priority += 1
            total_amount += afternoon_shares * est_price

    return {
        "morning_orders": morning_orders,
        "afternoon_orders": afternoon_orders,
        "total_orders": len(morning_orders) + len(afternoon_orders),
        "total_amount": round(total_amount, 2),
        "day_capital": round(stock_day_capital, 2),
    }


def generate_trade_plan(trade_date: str, capital: float = 5_000_000) -> Dict:
    """生成完整 trade_plan 字典"""
    dt = datetime.strptime(trade_date, "%Y-%m-%d")
    weekday_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][dt.weekday()]
    phase = get_phase(dt)
    build_plan = load_build_plan()

    stock_capital = int(capital * 0.6)
    hedge_capital = int(capital * 0.4)

    orders = generate_orders(trade_date, phase, build_plan, stock_capital)

    return {
        "trade_date": trade_date,
        "weekday": weekday_cn,
        "capital": capital,
        "stock_etf_capital": stock_capital,
        "hedge_capital": hedge_capital,
        "execution_mode": "MOCK_BROKER",
        "strategy": "康波第六轮周期 × 十五五规划 × v7.0期货期权双层对冲",
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "source_plan": "500万建仓计划_20260706.json",
            "source_build_plan": str(BUILD_PLAN_FILE),
            "version": "v7.5_institutional",
            "note": f"动态生成 — 阶段{phase['phase']} 第{phase['day_index']}天, 23 标的新权重",
        },
        "phase": {
            "phase_number": phase["phase"],
            "name": phase["name"],
            "start_date": phase["start"],
            "end_date": phase["end"],
            "duration_days": phase["duration_days"],
            "day_index": phase["day_index"],
            "capital_ratio": phase["capital_ratio"],
            "phase_capital": phase["phase_capital"],
            "day_capital": round(phase["phase_capital"] / phase["duration_days"], 2),
            "asset_count": len(build_plan.get("target_portfolio", {})),
            "strategy": phase["strategy"],
        },
        "market_state": {
            "vix": 18.5,
            "circuit_level": "NORMAL",
            "build_allowed": True,
            "notes": "动态生成, 假设市场状态正常",
        },
        "risk_controls": {
            "yellow_warning": -0.08,
            "orange_warning": -0.10,
            "red_stop": -0.12,
            "single_day_loss_pause": -0.03,
            "var_95_limit": 0.05,
            "var_99_limit": 0.08,
            "max_futures_margin_pct": 0.08,
            "max_option_premium_yearly_pct": 0.02,
            "stop_loss_rules": {
                "high_risk": -0.15,
                "medium_risk": -0.12,
                "low_risk": -0.05,
            },
        },
        "hedge_config": {
            "total_hedge_capital": hedge_capital,
            "layers": {
                "layer1_futures": {
                    "action": "SHORT_FUTURES",
                    "ratio": 0.15,
                    "target_beta": 0.25,
                    "instrument": "IF (沪深300股指期货)",
                    "capital": int(hedge_capital * 0.5),
                },
                "layer2_options": {
                    "action": "PUT_SPREAD_COLLAR",
                    "long_put_strike": 0.95,
                    "short_put_strike": 0.85,
                    "short_call_strike": 1.10,
                    "capital": int(hedge_capital * 0.5),
                },
                "layer3_volatility": "监控模式 (IV/RV 偏离 > 5% 时小仓位试单)",
                "layer4_absolute_return": "准备配对池, 暂不交易",
                "layer5_covered_call": "不启动 (建仓初期)",
            },
        },
        "execution_plan": {
            "broker": "MockBroker",
            "morning_window": "09:30-10:30",
            "afternoon_window": "14:00-14:30",
            "price_buffer": 0.008,
            "price_deviation_skip": 0.1,
            "session_split": 0.5,
            "morning_orders": orders["morning_orders"],
            "afternoon_orders": orders["afternoon_orders"],
            "total_orders": orders["total_orders"],
            "total_amount": orders["total_amount"],
            "day_capital": orders["day_capital"],
        },
    }


def main():
    parser = argparse.ArgumentParser(description="动态生成 trade_plan_{YYYYMMDD}.json")
    parser.add_argument("date", nargs="?", default=None,
                        help="交易日期 YYYY-MM-DD (默认: 下一交易日)")
    parser.add_argument("--capital", type=float, default=5_000_000,
                        help="总资金 (默认: 5000000)")
    args = parser.parse_args()

    if args.date:
        trade_date = args.date
    else:
        trade_date = next_trading_day(datetime.now()).strftime("%Y-%m-%d")

    print(f"生成交易计划: {trade_date}")
    print(f"总资金: ¥{args.capital:,.0f}")

    plan = generate_trade_plan(trade_date, args.capital)

    # 保存 JSON
    date_compact = trade_date.replace("-", "")
    json_path = PLAN_DIR / f"trade_plan_{date_compact}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    print(f"\n✓ 已生成: {json_path}")
    print(f"\n--- 计划摘要 ---")
    print(f"交易日: {plan['trade_date']} ({plan['weekday']})")
    print(f"阶段: {plan['phase']['name']} (第 {plan['phase']['day_index']}/{plan['phase']['duration_days']} 天)")
    print(f"当日资金: ¥{plan['execution_plan']['day_capital']:,.0f}")
    print(f"订单数: {plan['execution_plan']['total_orders']} (上午 {len(plan['execution_plan']['morning_orders'])} + 下午 {len(plan['execution_plan']['afternoon_orders'])})")
    print(f"订单总额: ¥{plan['execution_plan']['total_amount']:,.0f}")
    print(f"\n--- 前 5 订单 (上午) ---")
    for o in plan["execution_plan"]["morning_orders"][:5]:
        print(f"  {o['priority']}. {o['code']} {o['name']:<12} {o['shares']:>5} 股 @ {o['est_price']:<7.2f} = ¥{o['est_amount']:>10,.0f}")


if __name__ == "__main__":
    main()
