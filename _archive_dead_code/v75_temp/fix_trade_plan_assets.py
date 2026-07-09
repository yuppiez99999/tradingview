#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按 2026 交易计划修正 trade_plan_20260706.json：
- 资金：500万权益/对冲已修正为 300万/200万
- 阶段：修正为 35/30/20/15
- 资产：按 2026 交易计划 13 只重新生成当日订单
"""

import json
import os
from datetime import datetime

BASE_DIR = r"e:\各种PY程序\28-终极量化交易系统7.1"
PLAN_PATH = os.path.join(BASE_DIR, "v7.5_institutional", "trade_plans", "trade_plan_20260706.json")

TOTAL_CAPITAL = 5_000_000
EQUITY_CAPITAL = 3_000_000
HEDGE_CAPITAL = 2_000_000

PLAN_DATE = "2026-07-06"
PHASE_CAPITAL_RATIO = 0.35
PHASE_CAPITAL = round(TOTAL_CAPITAL * PHASE_CAPITAL_RATIO, 2)
DAY_CAPITAL = round(PHASE_CAPITAL / 10, 2)

# 2026 交易计划 13 只标的（风格映射参考 AGENTS.md 与持仓）
PLANNED_ASSETS = [
    {"code": "sz510300", "name": "沪深300ETF华泰柏瑞", "style": "宽基", "risk": "中", "est_price": 4.0, "weight_ratio": 0.10},
    {"code": "sz515180", "name": "易方达中证红利ETF", "style": "红利", "risk": "中", "est_price": 5.0, "weight_ratio": 0.10},
    {"code": "sh600089", "name": "特变电工", "style": "制造", "risk": "中", "est_price": 25.0, "weight_ratio": 0.07},
    {"code": "sz588000", "name": "科创50ETF华夏", "style": "高端制造", "risk": "高", "est_price": 1.05, "weight_ratio": 0.08},
    {"code": "sh688041", "name": "海光信息", "style": "科技", "risk": "高", "est_price": 85.0, "weight_ratio": 0.07},
    {"code": "sz300308", "name": "中际旭创", "style": "科技", "risk": "高", "est_price": 120.0, "weight_ratio": 0.07},
    {"code": "sz002371", "name": "北方华创", "style": "科技", "risk": "高", "est_price": 350.0, "weight_ratio": 0.07},
    {"code": "sh688981", "name": "中芯国际", "style": "科技", "risk": "高", "est_price": 142.93, "weight_ratio": 0.07},
    {"code": "sh603019", "name": "中科曙光", "style": "科技", "risk": "高", "est_price": 94.42, "weight_ratio": 0.07},
    {"code": "sz000425", "name": "徐工机械", "style": "制造", "risk": "中", "est_price": 8.5, "weight_ratio": 0.07},
    {"code": "sh600276", "name": "恒瑞医药", "style": "医药", "risk": "中高", "est_price": 50.0, "weight_ratio": 0.07},
    {"code": "sh600900", "name": "长江电力", "style": "防御", "risk": "低", "est_price": 27.05, "weight_ratio": 0.08},
    {"code": "sh601088", "name": "中国神华", "style": "顺周期", "risk": "中", "est_price": 40.70, "weight_ratio": 0.07},
]

# 保持现有低风险/避险映射，避免完全破坏执行链
DEFENSE_ASSETS = [
    {"code": "sz518880", "name": "黄金ETF华安", "style": "避险", "risk": "中", "est_price": 5.85, "weight_ratio": 0.05},
]


def make_order(code, name, session, priority, est_price, weight_ratio, style, risk):
    budget = round(EQUITY_CAPITAL * weight_ratio, 2)
    if code.startswith("sz") or code.startswith("sh"):
        if est_price >= 100:
            shares = int(budget * 0.5 / est_price / 100) * 100
        elif est_price >= 10:
            shares = int(budget * 0.7 / est_price / 100) * 100
        else:
            shares = int(budget * 0.9 / est_price / 100) * 100
        shares = max(shares, 100)
        est_amount = round(shares * est_price, 2)
        limit_price = round(est_price * 1.008, 3)
        return {
            "priority": priority,
            "code": code,
            "name": name,
            "session": session,
            "shares": shares,
            "est_price": est_price,
            "limit_price": limit_price,
            "est_amount": est_amount,
            "side": "BUY",
            "order_type": "LIMIT",
            "style": style,
            "risk": risk,
            "note": f"{'上午批次 09:30-10:30' if session == 'morning' else '下午批次 14:00-14:30'}",
            "technical_alpha": 1.0,
        }
    return None


def build_orders():
    morning = []
    afternoon = []
    priority = 1
    for asset in PLANNED_ASSETS + DEFENSE_ASSETS:
        mo = make_order(asset["code"], asset["name"], "morning", priority, asset["est_price"], asset["weight_ratio"], asset["style"], asset["risk"])
        ao = make_order(asset["code"], asset["name"], "afternoon", priority, asset["est_price"], asset["weight_ratio"], asset["style"], asset["risk"])
        if mo:
            morning.append(mo)
        if ao:
            afternoon.append(ao)
        priority += 1
    return morning, afternoon


morning_orders, afternoon_orders = build_orders()
morning_total = round(sum(o["est_amount"] for o in morning_orders), 2)
afternoon_total = round(sum(o["est_amount"] for o in afternoon_orders), 2)
grand_total = round(morning_total + afternoon_total, 2)

plan = {
    "trade_date": PLAN_DATE,
    "weekday": "Monday",
    "capital": TOTAL_CAPITAL,
    "stock_etf_capital": EQUITY_CAPITAL,
    "hedge_capital": HEDGE_CAPITAL,
    "execution_mode": "MOCK_BROKER",
    "strategy": "康波第六轮周期 × 十五五规划 × v7.0期货期权双层对冲",
    "metadata": {
        "generated_at": datetime.now().strftime("%Y-%m-%d"),
        "source_plan": "2026年交易计划.md",
        "source_build_plan": "500万建仓计划_20260706.json",
        "source_orders": "reports/trade_orders_20260706.json",
        "version": "v7.5_institutional",
        "aligned_assets_count": len(PLANNED_ASSETS) + len(DEFENSE_ASSETS),
    },
    "phase": {
        "phase_number": 1,
        "name": "第一阶段-底仓建立",
        "start_date": "2026-07-06",
        "end_date": "2026-07-17",
        "duration_days": 10,
        "day_index": 1,
        "capital_ratio": PHASE_CAPITAL_RATIO,
        "phase_capital": PHASE_CAPITAL,
        "day_capital": DAY_CAPITAL,
        "asset_count": len(PLANNED_ASSETS) + len(DEFENSE_ASSETS),
        "strategy": "首批 35% 建仓 + 启动对冲 (Layer 1 期货 15% + Layer 2 Collar)"
    },
    "market_state": {
        "vix": 18.5,
        "circuit_level": "NORMAL",
        "build_allowed": True,
        "notes": "建仓 D-Day, 市场状态正常"
    },
    "risk_controls": {
        "yellow_warning": -0.06,
        "orange_warning": -0.08,
        "red_stop": -0.12,
        "single_day_loss_pause": -0.03,
        "var_95_limit": 0.05,
        "var_99_limit": 0.08,
        "max_futures_margin_pct": 0.08,
        "max_option_premium_yearly_pct": 0.02,
        "stop_loss_rules": {
            "high_risk": -0.15,
            "medium_risk": -0.12,
            "low_risk": -0.05
        }
    },
    "hedge_config": {
        "total_hedge_capital": HEDGE_CAPITAL,
        "layers": {
            "layer1_futures": {
                "action": "SHORT_FUTURES",
                "ratio": 0.15,
                "target_beta": 0.1,
                "instrument": "IF (沪深300股指期货)",
                "capital": round(HEDGE_CAPITAL * 0.15, 2)
            },
            "layer2_options": {
                "action": "PUT_SPREAD_COLLAR",
                "long_put_strike": 0.95,
                "short_put_strike": 0.85,
                "short_call_strike": 1.1,
                "capital": round(HEDGE_CAPITAL * 0.25, 2)
            },
            "layer3_volatility": "监控模式 (IV/RV 偏离 > 5% 时小仓位试单)",
            "layer4_absolute_return": "准备配对池, 暂不交易",
            "layer5_covered_call": "不启动 (建仓初期)"
        }
    },
    "execution_plan": {
        "broker": "MockBroker",
        "morning_window": "09:30-10:30",
        "afternoon_window": "14:00-14:30",
        "price_buffer": 0.008,
        "price_deviation_skip": 0.1,
        "session_split": 0.5,
        "morning_orders": morning_orders,
        "afternoon_orders": afternoon_orders,
        "morning_total": morning_total,
        "afternoon_total": afternoon_total,
        "grand_total": grand_total,
        "total_orders": len(morning_orders) + len(afternoon_orders),
    },
    "phase_roadmap": [
        {"phase": 1, "name": "第一阶段-底仓建立", "start": "2026-07-06", "duration_days": 10, "capital_ratio": 0.35, "capital_amount": 1750000.0, "asset_count": len(PLANNED_ASSETS) + len(DEFENSE_ASSETS), "strategy": "首批 35% 建仓 + 启动对冲"},
        {"phase": 2, "name": "第二阶段-配置完善", "start": "2026-07-20", "duration_days": 15, "capital_ratio": 0.30, "capital_amount": 1500000.0, "asset_count": len(PLANNED_ASSETS) + len(DEFENSE_ASSETS), "strategy": "第2阶段建仓 + 增配期权保护"},
        {"phase": 3, "name": "第三阶段-防御补充", "start": "2026-08-10", "duration_days": 15, "capital_ratio": 0.20, "capital_amount": 1000000.0, "asset_count": len(PLANNED_ASSETS) + len(DEFENSE_ASSETS), "strategy": "防御板块补仓 + 尾部对冲"},
        {"phase": 4, "name": "第四阶段-最终调整", "start": "2026-09-01", "duration_days": 20, "capital_ratio": 0.15, "capital_amount": 750000.0, "asset_count": len(PLANNED_ASSETS) + len(DEFENSE_ASSETS), "strategy": "最终仓位调整 + 对冲结构优化"}
    ],
    "performance_targets": {
        "return_h2_2026": "+4% ~ +6%",
        "annualized_return": "+8.5% ~ +12.0%",
        "max_drawdown": "< 10%",
        "sharpe_ratio": "> 1.2",
        "alpha": "> 2%",
        "monthly_win_rate": "> 55%",
        "hedge_efficiency": "> 60%"
    }
}

with open(PLAN_PATH, "w", encoding="utf-8") as f:
    json.dump(plan, f, ensure_ascii=False, indent=2)

print(f"已重写交易计划: {PLAN_PATH}")
print(f"资产数: {len(PLAN_PATH)}")
