# -*- coding: utf-8 -*-
"""重新生成 7月6日交易计划 — 基于 4 份权威文档

数据源:
1. 300万年度交易组合优化方案（2026年6月）.pdf  — 300万股票组合
2. 长江电力分红再投资5年回报测算与对比分析.pdf  — 长江电力建仓策略
3. 中国神华股价分析与建仓策略.pdf              — 中国神华建仓策略
4. 棉花的加仓方案与期权保护策略_20260704.md     — 200万期权对冲

资金配置:
- 总资金 500 万
- 股票组合 300 万 (60%)
- 期权对冲 200 万 (40%)
"""
import json
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).parent
PLAN_DIR = BASE / "trade_plans"
PLAN_DIR.mkdir(exist_ok=True)


# ============================================================
# 300 万股票组合 (基于 PDF1)
# ============================================================
STOCK_PORTFOLIO = {
    "total_capital": 3_000_000,
    "categories": {
        "核心宽基ETF": {"weight": 0.28, "amount": 840_000, "items": [
            {"code": "sh510300", "name": "沪深300ETF华泰柏瑞", "weight": 0.08, "amount": 240_000, "est_price": 4.0, "shares": 60_000},
            {"code": "sh510500", "name": "中证500ETF南方", "weight": 0.06, "amount": 180_000, "est_price": 6.5, "shares": 27_700},
            {"code": "sh512100", "name": "中证1000ETF南方", "weight": 0.05, "amount": 150_000, "est_price": 2.3, "shares": 65_200},
            {"code": "sh588000", "name": "科创50ETF华夏", "weight": 0.05, "amount": 150_000, "est_price": 1.05, "shares": 142_900},
            {"code": "sz159915", "name": "创业板ETF易方达", "weight": 0.04, "amount": 120_000, "est_price": 2.15, "shares": 55_800},
        ]},
        "科技成长个股": {"weight": 0.20, "amount": 600_000, "items": [
            {"code": "sh688041", "name": "海光信息", "weight": 0.03, "amount": 90_000, "est_price": 85.0, "shares": 1_100, "stop_loss": -0.10},
            {"code": "sz300308", "name": "中际旭创", "weight": 0.03, "amount": 90_000, "est_price": 120.0, "shares": 800, "stop_loss": -0.12},
            {"code": "sz300274", "name": "阳光电源", "weight": 0.04, "amount": 120_000, "est_price": 45.0, "shares": 2_700, "stop_loss": -0.12},
            {"code": "sz002371", "name": "北方华创", "weight": 0.03, "amount": 90_000, "est_price": 350.0, "shares": 300, "stop_loss": -0.12},
            {"code": "sh688017", "name": "绿的谐波", "weight": 0.03, "amount": 90_000, "est_price": 180.0, "shares": 500, "stop_loss": -0.15},
            {"code": "sh600276", "name": "恒瑞医药", "weight": 0.04, "amount": 120_000, "est_price": 50.0, "shares": 2_400, "stop_loss": -0.10},
        ]},
        "高端制造/基建": {"weight": 0.20, "amount": 600_000, "items": [
            {"code": "sh600089", "name": "特变电工", "weight": 0.05, "amount": 150_000, "est_price": 25.0, "shares": 6_000},
            {"code": "sh600875", "name": "东方电气", "weight": 0.04, "amount": 120_000, "est_price": 22.0, "shares": 5_500},
            {"code": "sz000425", "name": "徐工机械", "weight": 0.04, "amount": 120_000, "est_price": 8.5, "shares": 14_100},
            {"code": "sh600406", "name": "国电南瑞", "weight": 0.04, "amount": 120_000, "est_price": 35.0, "shares": 3_400},
            {"code": "sh600989", "name": "宝丰能源", "weight": 0.03, "amount": 90_000, "est_price": 18.0, "shares": 5_000},
        ]},
        "防御/红利": {"weight": 0.15, "amount": 450_000, "items": [
            {"code": "sz515180", "name": "易方达中证红利ETF", "weight": 0.06, "amount": 180_000, "est_price": 5.0, "shares": 36_000},
            {"code": "sh600036", "name": "招商银行", "weight": 0.04, "amount": 120_000, "est_price": 38.0, "shares": 3_200},
            {"code": "sh600900", "name": "长江电力", "weight": 0.03, "amount": 90_000, "est_price": 27.05, "shares": 3_300},
            {"code": "sh601088", "name": "中国神华", "weight": 0.02, "amount": 60_000, "est_price": 40.70, "shares": 1_500},
        ]},
        "商品/避险": {"weight": 0.05, "amount": 150_000, "items": [
            {"code": "sz518880", "name": "黄金ETF华安", "weight": 0.05, "amount": 150_000, "est_price": 5.85, "shares": 25_600},
        ]},
        "现金缓冲": {"weight": 0.08, "amount": 240_000, "items": []},
    }
}


# ============================================================
# 200 万期权对冲 (基于棉花 MD + 股票期权保护)
# ============================================================
HEDGE_PORTFOLIO = {
    "total_capital": 2_000_000,
    "categories": {
        "棉花期货保证金": {
            "weight": 0.50, "amount": 1_000_000,
            "underlying": "CF2609 郑棉2609合约",
            "current_price": 16_290,
            "contract_unit": 5,
            "margin_per_lot": 5_700,
            "notional_per_lot": 81_450,
            "max_lots_50pct_leverage": 175,
            "phases": [
                {"phase": 1, "name": "底仓建立", "window": "7月第1-2周",
                 "price_range": "16,100-16,290", "position_pct": 0.30, "lots": 52},
                {"phase": 2, "name": "回调加仓", "window": "7月中旬",
                 "price_range": "15,900-16,100", "position_pct": 0.30, "lots": 52},
                {"phase": 3, "name": "突破确认加仓", "window": "7月下旬-8月初",
                 "price_range": "16,300-16,400", "position_pct": 0.20, "lots": 35},
                {"phase": 4, "name": "趋势末端加仓", "window": "8月上旬",
                 "price_range": "16,800-17,000", "position_pct": 0.20, "lots": 35},
            ],
            "stop_loss": 15_500,
            "target_range": "17,000-18,000",
        },
        "棉花期权保护": {
            "weight": 0.0875, "amount": 175_000,
            "strategy": "方案A: 保护性看跌 (Protective Put)",
            "structure": "Long 1手 CF2609期货 + Long 1手 CF609P15600看跌期权",
            "put_strike": 15_600,
            "premium_per_ton": 200,
            "premium_per_lot": 1_000,
            "total_lots": 175,
            "total_cost": 175_000,
            "max_loss_per_lot": 4_000,
            "breakeven": 16_400,
            "roll_up_trigger": "价格涨至17,000以上时, 平仓P15600→买入P16200",
        },
        "股票期权保护": {
            "weight": 0.25, "amount": 500_000,
            "strategy": "50ETF/300ETF Put Spread Collar",
            "structure": "Long 95% Put / Short 85% Put / Short 110% Call",
            "coverage": "保护300万股票组合的β暴露",
            "target_beta": 0.10,
            "hedge_ratio": 0.15,
        },
        "现金缓冲": {
            "weight": 0.1625, "amount": 325_000,
            "purpose": "追加保证金/行权资金/应急",
        },
    }
}


# ============================================================
# 个股建仓策略 (基于 PDF2 长江电力 + PDF3 中国神华)
# ============================================================
STOCK_BUILD_STRATEGY = {
    "sh600900": {  # 长江电力
        "current_price": 27.05,
        "target_weight": 0.03,
        "target_amount": 90_000,
        "build_tiers": [
            {"tier": 1, "price_range": "26.5-27.5", "position_pct": 0.40, "shares": 1_320},
            {"tier": 2, "price_range": "25.5-26.5", "position_pct": 0.30, "shares": 990},
            {"tier": 3, "price_range": "24.5-25.5", "position_pct": 0.30, "shares": 990},
        ],
        "5yr_return": 0.382,
        "5yr_cagr": 0.067,
        "stop_loss": -0.08,
        "reason": "5年分红再投资年化6.7%, 跑赢债市450BP",
    },
    "sh601088": {  # 中国神华
        "current_price": 40.70,
        "target_weight": 0.02,
        "target_amount": 60_000,
        "build_tiers": [
            {"tier": 1, "price_range": "40-42", "position_pct": 0.40, "shares": 600},
            {"tier": 2, "price_range": "36-39", "position_pct": 0.35, "shares": 525},
            {"tier": 3, "price_range": "32-35", "position_pct": 0.25, "shares": 375},
        ],
        "dividend_yield": 0.0464,
        "target_price": "53-55",
        "upside": "30-35%",
        "stop_loss": -0.12,
        "reason": "股息率4.64%, 分析师目标价53-55元, 上涨空间30-35%",
    },
}


# ============================================================
# 风控体系 (基于 PDF1 三级风控 + MD 棉花止损)
# ============================================================
RISK_CONTROLS = {
    "stock_portfolio": {
        "individual_stop_loss": {
            "宽基ETF": -0.08,
            "科技股": -0.12,
            "防御股": -0.08,
            "黄金ETF": {"half_position": -0.08, "clear": -0.12},
        },
        "portfolio_drawdown": [
            {"level": "预警", "threshold": -0.08, "action": "检查持仓"},
            {"level": "减仓1", "threshold": -0.10, "action": "权益仓位降至70%"},
            {"level": "减仓2", "threshold": -0.12, "action": "权益仓位降至50%"},
            {"level": "全部止损", "threshold": -0.15, "action": "全部止损"},
        ],
        "intraday_circuit_breaker": [
            {"level": "停止买入", "threshold": -0.03, "action": "停止买入"},
            {"level": "强制减仓", "threshold": -0.05, "action": "强制减仓30%"},
        ],
    },
    "hedge_portfolio": {
        "cotton_stop_loss": 15_500,
        "cotton_pause_add": 15_800,
        "cotton_target": "17,000-18,000",
        "option_theta_management": "8月中旬前决策滚动至CF2701",
    },
    "rebalance_rules": {
        "periodic": "每月末恢复目标权重",
        "threshold": "单只标的权重偏离>5%即时调仓",
        "event_driven": "重大政策/黑天鹅事件紧急调整",
    },
}


# ============================================================
# 7月6日交易指令生成
# ============================================================
def build_trade_orders():
    """生成7月6日交易指令"""
    morning_orders = []
    afternoon_orders = []
    priority = 1

    # === 股票组合 — 第一阶段建仓 (50% 资金) ===
    stock_first_phase = 0.50  # 7月6日建仓50%

    for cat_name, cat_data in STOCK_PORTFOLIO["categories"].items():
        if cat_name == "现金缓冲":
            continue
        for item in cat_data["items"]:
            code = item["code"]
            name = item["name"]
            est_price = item["est_price"]
            target_amount = item["amount"]
            # 7月6日建仓50%
            day_amount = target_amount * stock_first_phase
            shares = max(100, int(day_amount / est_price / 100) * 100)
            limit_price = round(est_price * 1.008, 4)
            est_amount = shares * est_price

            # 检查是否有特殊建仓策略
            strategy = STOCK_BUILD_STRATEGY.get(code, {})
            tier1 = strategy.get("build_tiers", [{}])[0]
            price_range = tier1.get("price_range", f"{est_price*0.98:.2f}-{est_price*1.02:.2f}")

            order = {
                "priority": priority,
                "code": code,
                "name": name,
                "category": cat_name,
                "session": "morning",
                "shares": shares,
                "est_price": est_price,
                "limit_price": limit_price,
                "est_amount": est_amount,
                "side": "BUY",
                "order_type": "LIMIT",
                "price_range": price_range,
                "strategy": strategy.get("reason", f"{cat_name}板块配置"),
            }
            morning_orders.append(order)
            priority += 1

    # === 棉花期货 — 第一阶段建仓 (52手) ===
    cotton_phase1_lots = 52
    morning_orders.append({
        "priority": priority,
        "code": "CF2609",
        "name": "郑棉2609合约",
        "category": "棉花期货",
        "session": "morning",
        "shares": cotton_phase1_lots,
        "est_price": 16_290,
        "limit_price": 16_290,
        "est_amount": cotton_phase1_lots * 5_700,  # 保证金
        "side": "BUY",
        "order_type": "FUTURES",
        "price_range": "16,100-16,290",
        "strategy": "棉花4阶段加仓 第1阶段 (底仓30%)",
    })
    priority += 1

    # === 棉花期权保护 — 方案A 保护性看跌 ===
    put_lots = cotton_phase1_lots
    morning_orders.append({
        "priority": priority,
        "code": "CF609P15600",
        "name": "棉花看跌期权15600",
        "category": "棉花期权",
        "session": "morning",
        "shares": put_lots,
        "est_price": 200,  # 每吨权利金
        "limit_price": 200,
        "est_amount": put_lots * 1_000,  # 每手1000元
        "side": "BUY",
        "order_type": "OPTION",
        "price_range": "180-250",
        "strategy": "方案A 保护性看跌 (保护棉花期货52手)",
    })
    priority += 1

    # === 下午批次 — 股票组合补单 (相同标的, 不同时段) ===
    afternoon_priority = 1
    for cat_name, cat_data in STOCK_PORTFOLIO["categories"].items():
        if cat_name == "现金缓冲":
            continue
        for item in cat_data["items"]:
            code = item["code"]
            name = item["name"]
            est_price = item["est_price"]
            target_amount = item["amount"]
            # 下午再建仓30%
            day_amount = target_amount * 0.30
            shares = max(100, int(day_amount / est_price / 100) * 100)
            limit_price = round(est_price * 1.008, 4)
            est_amount = shares * est_price

            afternoon_orders.append({
                "priority": afternoon_priority,
                "code": code,
                "name": name,
                "category": cat_name,
                "session": "afternoon",
                "shares": shares,
                "est_price": est_price,
                "limit_price": limit_price,
                "est_amount": est_amount,
                "side": "BUY",
                "order_type": "LIMIT",
                "strategy": f"{cat_name}板块下午批次",
            })
            afternoon_priority += 1

    morning_total = sum(o["est_amount"] for o in morning_orders)
    afternoon_total = sum(o["est_amount"] for o in afternoon_orders)

    return {
        "morning_orders": morning_orders,
        "afternoon_orders": afternoon_orders,
        "morning_total": morning_total,
        "afternoon_total": afternoon_total,
        "grand_total": morning_total + afternoon_total,
        "total_orders": len(morning_orders) + len(afternoon_orders),
    }


# ============================================================
# 主函数
# ============================================================
def build_plan():
    """构建交易计划"""
    execution = build_trade_orders()

    plan = {
        "trade_date": "2026-07-06",
        "generated_at": datetime.now().isoformat(),
        "capital": 5_000_000,
        "stock_capital": 3_000_000,
        "hedge_capital": 2_000_000,
        "strategy": "300万股票组合 + 200万期权对冲 (棉花CF2609 + 股票Put)",
        "data_sources": [
            "300万年度交易组合优化方案（2026年6月）.pdf",
            "长江电力分红再投资5年回报测算与对比分析.pdf",
            "中国神华股价分析与建仓策略.pdf",
            "棉花的加仓方案与期权保护策略_20260704.md",
        ],

        "stock_portfolio": STOCK_PORTFOLIO,
        "hedge_portfolio": HEDGE_PORTFOLIO,
        "stock_build_strategy": STOCK_BUILD_STRATEGY,
        "risk_controls": RISK_CONTROLS,

        "execution_plan": execution,

        "performance_targets": {
            "stock_portfolio": {
                "annual_return": ">=8%",
                "max_drawdown": "<=15%",
                "sharpe": ">0.62",
                "prob_return_gt_8pct": "68%",
                "prob_drawdown_lt_15pct": "82%",
            },
            "hedge_portfolio": {
                "cotton_target": "17,000-18,000元/吨",
                "cotton_upside": "+4.4%~+10.5%",
                "option_protection": "最大亏损锁定 4,000元/手",
                "stock_option_hedge_ratio": "15%",
                "target_beta": "0.10",
            },
            "long_term": {
                "长江电力_5yr_cagr": "6.7%",
                "中国神华_target_upside": "30-35%",
                "中国神华_dividend_yield": "4.64%",
            },
        },
    }
    return plan


def build_markdown(plan: dict) -> str:
    """生成 Markdown 报告"""
    lines = []
    lines.append(f"# 7月6日交易计划 — 300万股票 + 200万期权对冲")
    lines.append("")
    lines.append(f"**生成时间**: {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"**交易日**: 2026-07-06 (Monday)")
    lines.append(f"**资金规模**: 5,000,000 元 = 300万股票 + 200万期权对冲")
    lines.append(f"**策略**: 基于4份权威文档的整合交易计划")
    lines.append("")
    lines.append("---")
    lines.append("")

    # === 一、数据源 ===
    lines.append("## 一、数据源")
    lines.append("")
    lines.append("| # | 文档 | 用途 |")
    lines.append("|---|------|------|")
    lines.append("| 1 | 300万年度交易组合优化方案（2026年6月）.pdf | 300万股票组合配置 |")
    lines.append("| 2 | 长江电力分红再投资5年回报测算与对比分析.pdf | 长江电力建仓策略 |")
    lines.append("| 3 | 中国神华股价分析与建仓策略.pdf | 中国神华建仓策略 |")
    lines.append("| 4 | 棉花的加仓方案与期权保护策略_20260704.md | 200万期权对冲 |")
    lines.append("")

    # === 二、资金配置总览 ===
    lines.append("## 二、资金配置总览")
    lines.append("")
    lines.append("```")
    lines.append("总资金 500万")
    lines.append("├── 股票组合 300万 (60%)")
    lines.append("│   ├── 核心宽基ETF 84万 (28%)")
    lines.append("│   ├── 科技成长个股 60万 (20%)")
    lines.append("│   ├── 高端制造/基建 60万 (20%)")
    lines.append("│   ├── 防御/红利 45万 (15%)")
    lines.append("│   ├── 商品/避险 15万 (5%)")
    lines.append("│   └── 现金缓冲 24万 (8%)")
    lines.append("│")
    lines.append("└── 期权对冲 200万 (40%)")
    lines.append("    ├── 棉花期货保证金 100万 (50%) — CF2609, 175手")
    lines.append("    ├── 棉花期权保护 17.5万 (8.75%) — 方案A保护性看跌")
    lines.append("    ├── 股票期权保护 50万 (25%) — Put Spread Collar")
    lines.append("    └── 现金缓冲 32.5万 (16.25%) — 追加保证金/行权")
    lines.append("```")
    lines.append("")

    # === 三、300万股票组合明细 ===
    lines.append("## 三、300万股票组合明细")
    lines.append("")
    lines.append("| 类别 | 权重 | 金额(万) | 标的数 |")
    lines.append("|------|------|----------|--------|")
    for cat_name, cat_data in STOCK_PORTFOLIO["categories"].items():
        lines.append(f"| {cat_name} | {cat_data['weight']:.0%} | {cat_data['amount']/10000:.0f} | {len(cat_data['items'])} |")
    lines.append("| **合计** | **100%** | **300** | — |")
    lines.append("")

    # 标的明细
    lines.append("### 标的明细")
    lines.append("")
    lines.append("| 代码 | 名称 | 类别 | 权重 | 金额(元) | 预估价 | 股数 |")
    lines.append("|------|------|------|------|----------|--------|------|")
    for cat_name, cat_data in STOCK_PORTFOLIO["categories"].items():
        if cat_name == "现金缓冲":
            continue
        for item in cat_data["items"]:
            lines.append(f"| {item['code']} | {item['name']} | {cat_name} | "
                         f"{item['weight']:.0%} | {item['amount']:,} | "
                         f"{item['est_price']} | {item['shares']:,} |")
    lines.append("")

    # === 四、长江电力建仓策略 ===
    lines.append("## 四、长江电力建仓策略 (基于PDF2)")
    lines.append("")
    lines.append("| 项目 | 内容 |")
    lines.append("|------|------|")
    lines.append("| 当前股价 | 27.05 元 |")
    lines.append("| 5年累计回报 | 38.2% |")
    lines.append("| 5年年化回报 | 6.7% |")
    lines.append("| 目标仓位 | 3% (9万) |")
    lines.append("| 止损线 | -8% |")
    lines.append("")
    lines.append("**分档建仓**:")
    lines.append("")
    lines.append("| 档位 | 价格区间 | 仓位比例 | 股数 |")
    lines.append("|------|----------|----------|------|")
    for tier in STOCK_BUILD_STRATEGY["sh600900"]["build_tiers"]:
        lines.append(f"| {tier['tier']} | {tier['price_range']} | {tier['position_pct']:.0%} | {tier['shares']:,} |")
    lines.append("")

    # === 五、中国神华建仓策略 ===
    lines.append("## 五、中国神华建仓策略 (基于PDF3)")
    lines.append("")
    lines.append("| 项目 | 内容 |")
    lines.append("|------|------|")
    lines.append("| 当前股价 | 40.70 元 |")
    lines.append("| 股息率 | 4.64% |")
    lines.append("| 分析师目标价 | 53-55 元 |")
    lines.append("| 上涨空间 | 30-35% |")
    lines.append("| 目标仓位 | 2% (6万) |")
    lines.append("| 止损线 | -12% |")
    lines.append("")
    lines.append("**分档建仓**:")
    lines.append("")
    lines.append("| 档位 | 价格区间 | 仓位比例 | 股数 |")
    lines.append("|------|----------|----------|------|")
    for tier in STOCK_BUILD_STRATEGY["sh601088"]["build_tiers"]:
        lines.append(f"| {tier['tier']} | {tier['price_range']} | {tier['position_pct']:.0%} | {tier['shares']:,} |")
    lines.append("")

    # === 六、200万期权对冲 ===
    lines.append("## 六、200万期权对冲 (基于棉花MD)")
    lines.append("")
    lines.append("### 棉花期货 (CF2609)")
    lines.append("")
    lines.append("| 项目 | 数值 |")
    lines.append("|------|------|")
    lines.append("| 当前价格 | 16,290 元/吨 |")
    lines.append("| 合约单位 | 5 吨/手 |")
    lines.append("| 每手保证金 | 5,700 元 |")
    lines.append("| 每手名义价值 | 81,450 元 |")
    lines.append("| 最大手数 (50%杠杆) | 175 手 |")
    lines.append("| 止损线 | 15,500 元/吨 |")
    lines.append("| 目标区间 | 17,000-18,000 元/吨 |")
    lines.append("")
    lines.append("**4阶段加仓路径**:")
    lines.append("")
    lines.append("| 阶段 | 时间窗口 | 价格区间 | 仓位比例 | 手数 |")
    lines.append("|------|----------|----------|----------|------|")
    for phase in HEDGE_PORTFOLIO["categories"]["棉花期货保证金"]["phases"]:
        lines.append(f"| {phase['phase']} | {phase['window']} | {phase['price_range']} | "
                     f"{phase['position_pct']:.0%} | {phase['lots']} |")
    lines.append("")

    lines.append("### 棉花期权保护 (方案A: 保护性看跌)")
    lines.append("")
    lines.append("| 项目 | 数值 |")
    lines.append("|------|------|")
    lines.append("| 策略结构 | Long 1手 CF2609 + Long 1手 CF609P15600 |")
    lines.append("| 看跌行权价 | 15,600 元/吨 |")
    lines.append("| 每吨权利金 | 200 元 |")
    lines.append("| 每手权利金 | 1,000 元 |")
    lines.append("| 保护手数 | 175 手 |")
    lines.append("| 总保护成本 | 175,000 元 |")
    lines.append("| 最大亏损/手 | 4,000 元 |")
    lines.append("| 盈亏平衡点 | 16,400 元/吨 |")
    lines.append("| Roll Up触发 | 价格>17,000时, P15600→P16200 |")
    lines.append("")

    lines.append("### 股票期权保护 (Put Spread Collar)")
    lines.append("")
    lines.append("| 项目 | 数值 |")
    lines.append("|------|------|")
    lines.append("| 策略结构 | Long 95% Put / Short 85% Put / Short 110% Call |")
    lines.append("| 覆盖范围 | 300万股票组合的β暴露 |")
    lines.append("| 目标Beta | 0.10 |")
    lines.append("| 对冲比率 | 15% |")
    lines.append("| 资金 | 500,000 元 |")
    lines.append("")

    # === 七、7月6日交易指令 ===
    lines.append("## 七、7月6日交易指令汇总")
    lines.append("")
    exec_plan = plan["execution_plan"]
    lines.append(f"| 项目 | 值 |")
    lines.append(f"|------|-----|")
    lines.append(f"| **订单总数** | **{exec_plan['total_orders']} 笔** (上午 {len(exec_plan['morning_orders'])} + 下午 {len(exec_plan['afternoon_orders'])}) |")
    lines.append(f"| **上午金额** | {exec_plan['morning_total']:,.0f} 元 |")
    lines.append(f"| **下午金额** | {exec_plan['afternoon_total']:,.0f} 元 |")
    lines.append(f"| **单日总金额** | **{exec_plan['grand_total']:,.0f} 元** |")
    lines.append("")

    # 上午订单
    lines.append("### 上午订单 (09:30-10:30)")
    lines.append("")
    lines.append("| # | 代码 | 名称 | 类别 | 股数/手数 | 预估价 | 金额(元) | 类型 |")
    lines.append("|---|------|------|------|-----------|--------|----------|------|")
    for o in exec_plan["morning_orders"]:
        lines.append(f"| {o['priority']} | {o['code']} | {o['name']} | {o['category']} | "
                     f"{o['shares']:,} | {o['est_price']} | {o['est_amount']:,.0f} | {o['order_type']} |")
    lines.append(f"| | | | | | | **{exec_plan['morning_total']:,.0f}** | |")
    lines.append("")

    # 下午订单
    lines.append("### 下午订单 (14:00-14:30)")
    lines.append("")
    lines.append("| # | 代码 | 名称 | 类别 | 股数 | 预估价 | 金额(元) |")
    lines.append("|---|------|------|------|------|--------|----------|")
    for o in exec_plan["afternoon_orders"]:
        lines.append(f"| {o['priority']} | {o['code']} | {o['name']} | {o['category']} | "
                     f"{o['shares']:,} | {o['est_price']} | {o['est_amount']:,.0f} |")
    lines.append(f"| | | | | | | **{exec_plan['afternoon_total']:,.0f}** |")
    lines.append("")

    # === 八、风控体系 ===
    lines.append("## 八、风控体系")
    lines.append("")
    lines.append("### 三级风控机制 (股票组合)")
    lines.append("")
    lines.append("| 层级 | 触发条件 | 措施 |")
    lines.append("|------|----------|------|")
    for dd in RISK_CONTROLS["stock_portfolio"]["portfolio_drawdown"]:
        lines.append(f"| {dd['level']} | 组合回撤 {dd['threshold']:.0%} | {dd['action']} |")
    lines.append("")
    lines.append("### 个股止损")
    lines.append("")
    lines.append("| 类别 | 止损线 |")
    lines.append("|------|--------|")
    lines.append("| 宽基ETF | -8% 减半仓 |")
    lines.append("| 科技股 | -10% ~ -12% 清仓 |")
    lines.append("| 防御股 | -8% 减半仓 |")
    lines.append("| 黄金ETF | -8% 减半仓, -12% 清仓 |")
    lines.append("")
    lines.append("### 棉花期货风控")
    lines.append("")
    lines.append("| 项目 | 数值 |")
    lines.append("|------|------|")
    lines.append("| 暂停加仓 | 价格 < 15,800 |")
    lines.append("| 全部止损 | 价格 < 15,500 |")
    lines.append("| 期权到期管理 | 8月中旬前决策滚动至CF2701 |")
    lines.append("")

    # === 九、绩效目标 ===
    lines.append("## 九、绩效目标")
    lines.append("")
    lines.append("### 股票组合 (300万)")
    lines.append("")
    lines.append("| 指标 | 目标 |")
    lines.append("|------|------|")
    lines.append("| 年化收益 | >= 8% |")
    lines.append("| 最大回撤 | <= 15% |")
    lines.append("| 夏普比率 | > 0.62 |")
    lines.append("| 收益>8%概率 | 68% |")
    lines.append("| 回撤<15%概率 | 82% |")
    lines.append("")
    lines.append("### 期权对冲 (200万)")
    lines.append("")
    lines.append("| 指标 | 目标 |")
    lines.append("|------|------|")
    lines.append("| 棉花目标价 | 17,000-18,000 元/吨 |")
    lines.append("| 棉花上涨空间 | +4.4% ~ +10.5% |")
    lines.append("| 期权最大亏损/手 | 4,000 元 (锁定) |")
    lines.append("| 股票期权对冲比率 | 15% |")
    lines.append("| 组合目标Beta | 0.10 |")
    lines.append("")
    lines.append("### 长期投资目标")
    lines.append("")
    lines.append("| 标的 | 指标 | 目标 |")
    lines.append("|------|------|------|")
    lines.append("| 长江电力 | 5年年化 | 6.7% |")
    lines.append("| 中国神华 | 目标价上涨空间 | 30-35% |")
    lines.append("| 中国神华 | 股息率 | 4.64% |")
    lines.append("")

    # === 十、执行检查清单 ===
    lines.append("## 十、执行检查清单")
    lines.append("")
    lines.append("- [ ] 系统自检通过 (NTP/风控/熔断)")
    lines.append("- [ ] 市场状态正常 (VIX < 30, 无熔断)")
    lines.append(f"- [ ] 上午执行 {len(exec_plan['morning_orders'])} 笔订单 ({exec_plan['morning_total']:,.0f} 元)")
    lines.append(f"- [ ] 下午执行 {len(exec_plan['afternoon_orders'])} 笔订单 ({exec_plan['afternoon_total']:,.0f} 元)")
    lines.append("- [ ] 棉花期货第1阶段建仓 52手 (保证金 296,400 元)")
    lines.append("- [ ] 棉花期权保护 方案A 买入 52手 CF609P15600 (52,000 元)")
    lines.append("- [ ] 股票期权保护 Put Spread Collar 配置")
    lines.append("- [ ] 风控参数校验 (止损/VaR/偏离)")
    lines.append("- [ ] 盘后报告生成 + 建仓成本归因")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"**计划文件**: `trade_plans/trade_plan_20260706.json`")
    lines.append(f"**数据源**: 4份权威文档 (300万股票组合PDF + 长江电力PDF + 中国神华PDF + 棉花期权MD)")
    lines.append(f"**执行命令**: `python daily_workflow.py --date 2026-07-06`")

    return "\n".join(lines)


def main():
    """主函数"""
    print("=" * 60)
    print("重新生成 7月6日交易计划")
    print("基于 4 份权威文档:")
    print("  1. 300万年度交易组合优化方案（2026年6月）.pdf")
    print("  2. 长江电力分红再投资5年回报测算与对比分析.pdf")
    print("  3. 中国神华股价分析与建仓策略.pdf")
    print("  4. 棉花的加仓方案与期权保护策略_20260704.md")
    print("=" * 60)

    plan = build_plan()

    # 保存 JSON
    json_path = PLAN_DIR / "trade_plan_20260706.json"
    json_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[OK] JSON 已保存: {json_path}")

    # 保存 Markdown
    md_path = PLAN_DIR / "trade_plan_20260706.md"
    md_path.write_text(build_markdown(plan), encoding="utf-8")
    print(f"[OK] Markdown 已保存: {md_path}")

    # 打印摘要
    exec_plan = plan["execution_plan"]
    print("\n" + "=" * 60)
    print("交易计划摘要")
    print("=" * 60)
    print(f"交易日: {plan['trade_date']}")
    print(f"总资金: {plan['capital']:,} 元")
    print(f"  股票组合: {plan['stock_capital']:,} 元 (60%)")
    print(f"  期权对冲: {plan['hedge_capital']:,} 元 (40%)")
    print(f"订单总数: {exec_plan['total_orders']} 笔")
    print(f"  上午批次: {len(exec_plan['morning_orders'])} 笔, {exec_plan['morning_total']:,.0f} 元")
    print(f"  下午批次: {len(exec_plan['afternoon_orders'])} 笔, {exec_plan['afternoon_total']:,.0f} 元")
    print(f"  单日合计: {exec_plan['grand_total']:,.0f} 元")
    print("=" * 60)


if __name__ == "__main__":
    main()
