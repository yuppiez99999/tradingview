#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中国神华(601088) 建仓计划生成器
================================

基于 PDF《中国神华股价分析与建仓策略》(2026-07-03) 生成分批建仓计划 JSON。

核心策略（来自 PDF）：
  第一档(底仓): 40-42元, 30-40% 仓位
  第二档(加仓): 36-39元, 30-40% 仓位
  第三档(重仓): 32-35元, 20-30% 仓位

输入：
  总资金（默认 100 万元，可通过 --capital 参数调整）

输出：
  中国神华建仓计划_YYYYMMDD.json
  中国神华建仓计划_YYYYMMDD.md
"""

import os
import json
import argparse
from datetime import datetime, timedelta
from typing import Dict, List, Any


# =============================================================
# 核心配置（来自 PDF 建仓策略）
# =============================================================
SHENHUA_CODE = "601088"
SHENHUA_NAME = "中国神华"

# 三档建仓价位与仓位（来自 PDF）
BUILD_TIERS = [
    {
        "tier": 1,
        "name": "第一档-底仓",
        "price_low": 40.0,
        "price_high": 42.0,
        "price_mid": 41.0,                    # 价位中枢
        "weight_ratio": 0.35,                 # 35% 仓位
        "pe_implied": 17.0,                   # 对应 PE
        "dividend_yield_implied": 0.0464,     # 股息率 4.64%
        "trigger": "当前估值合理，股息率有吸引力，PB 接近 1.5 倍支撑",
    },
    {
        "tier": 2,
        "name": "第二档-加仓",
        "price_low": 36.0,
        "price_high": 39.0,
        "price_mid": 37.5,
        "weight_ratio": 0.35,                 # 35% 仓位
        "pe_implied": 15.0,
        "dividend_yield_implied": 0.0520,     # 股息率升至 5%+
        "trigger": "接近 3 年估值中枢下沿，股息率升至 5% 以上",
    },
    {
        "tier": 3,
        "name": "第三档-重仓",
        "price_low": 32.0,
        "price_high": 35.0,
        "price_mid": 33.5,
        "weight_ratio": 0.30,                 # 30% 仓位
        "pe_implied": 13.0,
        "dividend_yield_implied": 0.0580,     # 股息率接近 6%
        "trigger": "接近 3 年估值底部，PB 接近 1.5 倍历史低位，安全边际高",
    },
]

# 阶段时间安排（与 500 万建仓计划保持一致的阶段逻辑）
BUILD_PHASES = [
    {
        "phase": 1,
        "name": "第一阶段-底仓建立",
        "duration_days": 10,
        "capital_ratio": 0.35,                # 与第一档对应
        "tier_ref": 1,
    },
    {
        "phase": 2,
        "name": "第二阶段-回调加仓",
        "duration_days": 15,
        "capital_ratio": 0.35,
        "tier_ref": 2,
    },
    {
        "phase": 3,
        "name": "第三阶段-深度配置",
        "duration_days": 20,
        "capital_ratio": 0.30,
        "tier_ref": 3,
    },
]

# 风险参数
RISK_PARAMS = {
    "stop_loss": -0.12,                      # 止损 -12%（中风险个股）
    "take_profit": 0.30,                     # 目标价 53-55 元对应 +30~35%
    "max_single_weight": 0.15,               # 单一标的最大权重 15%
    "max_position_per_trade": 0.05,          # 单笔最大加仓 5%
    "trailing_stop": 0.08,                   # 移动止损 8%
}

# 执行规则
EXECUTION_RULES = {
    "daily_timing": {
        "morning_window": ["09:35", "10:15"],     # 避开开盘集合竞价
        "afternoon_window": ["14:00", "14:30"],   # 尾盘前加仓
    },
    "price_rules": {
        "discount_buy": 0.02,                 # 价格低于档位中枢 2% 时积极买入
        "normal_buy": 0.00,                   # 价格在中枢附近正常买入
        "premium_skip": 0.03,                 # 价格高于档位中枢 3% 时跳过
    },
    "min_lots": 100,                          # 最小交易单位 100 股
    "max_daily_lots": 1000,                   # 单日最大 1000 股
}

# 分析师目标价（来自 PDF）
ANALYST_TARGETS = {
    "avg_target_price": 54.0,                 # 平均目标价 53-55 元
    "upside_potential": 0.326,                # 较 40.7 元上涨空间约 32.6%
    "sources": [
        {"institution": "某券商", "rating": "增持", "target": 55.44, "date": "2026-04-27"},
        {"institution": "花旗",   "rating": "买入", "target": 54.70, "date": "2026-04-27"},
        {"institution": "东方证券", "rating": "买进", "target": 54.37, "date": "2026-04-05"},
        {"institution": "国盛证券", "rating": "买入", "target": None,  "date": "2026-05-09"},
    ],
}

# 当前估值快照（来自 PDF）
VALUATION_SNAPSHOT = {
    "current_price": 40.70,
    "pe_ttm": 17.12,
    "pb": 1.76,
    "dividend_yield_ttm": 0.0464,
    "total_market_cap": 862_500_000_000,      # 8,625 亿元
    "pe_3y_low": 7.75,
    "pe_3y_high": 21.35,
    "pb_3y_low": 1.47,
    "pb_3y_high": 2.47,
}


# =============================================================
# 计算函数
# =============================================================
def calc_lots(target_amount: float, price: float, min_lots: int = 100) -> int:
    """根据目标金额和价格计算股数（按 100 股取整）"""
    raw_shares = target_amount / price
    lots = int(raw_shares // min_lots) * min_lots
    return max(lots, min_lots)


def build_position_plan(total_capital: float, start_date: datetime) -> Dict[str, Any]:
    """
    生成中国神华分档建仓计划

    参数:
        total_capital: 总投入资金（元）
        start_date: 建仓起始日

    返回:
        完整的建仓计划字典
    """
    plan = {
        "metadata": {
            "code": SHENHUA_CODE,
            "name": SHENHUA_NAME,
            "total_capital": total_capital,
            "start_date": start_date.strftime("%Y-%m-%d"),
            "end_date": (start_date + timedelta(days=45)).strftime("%Y-%m-%d"),
            "build_tiers": len(BUILD_TIERS),
            "generated_at": datetime.now().isoformat(),
            "strategy": "PDF 建仓策略: 三档分批 + 估值驱动 + 股息率锚定",
            "source_pdf": "中国神华股价分析与建仓策略.pdf",
            "source_date": "2026-07-03",
        },
        "valuation_snapshot": VALUATION_SNAPSHOT,
        "analyst_targets": ANALYST_TARGETS,
        "build_tiers": [],
        "position_plan": {},
        "phase_summary": [],
        "risk_params": RISK_PARAMS,
        "execution_rules": EXECUTION_RULES,
    }

    # 构建三档建仓明细
    cumulative_shares = 0
    cumulative_amount = 0.0

    for tier in BUILD_TIERS:
        tier_amount = total_capital * tier["weight_ratio"]
        tier_shares = calc_lots(tier_amount, tier["price_mid"], EXECUTION_RULES["min_lots"])
        tier_actual_amount = tier_shares * tier["price_mid"]

        tier_data = {
            "tier": tier["tier"],
            "name": tier["name"],
            "price_low": tier["price_low"],
            "price_high": tier["price_high"],
            "price_mid": tier["price_mid"],
            "target_amount": tier_amount,
            "actual_amount": tier_actual_amount,
            "shares": tier_shares,
            "weight_ratio": tier["weight_ratio"],
            "pe_implied": tier["pe_implied"],
            "dividend_yield_implied": tier["dividend_yield_implied"],
            "trigger_condition": tier["trigger"],
            "cumulative_shares": cumulative_shares + tier_shares,
            "cumulative_amount": cumulative_amount + tier_actual_amount,
            "cumulative_weight": (cumulative_amount + tier_actual_amount) / total_capital,
        }
        plan["build_tiers"].append(tier_data)
        cumulative_shares += tier_shares
        cumulative_amount += tier_actual_amount

    # 构建 position_plan（兼容 BuildPlanExecutor 格式）
    plan["position_plan"] = {
        SHENHUA_CODE: {
            "code": SHENHUA_CODE,
            "name": SHENHUA_NAME,
            "target_weight": 1.0,                 # 单标的全部仓位
            "target_amount": total_capital,
            "est_price": VALUATION_SNAPSHOT["current_price"],
            "total_shares": cumulative_shares,
            "stop_loss": RISK_PARAMS["stop_loss"],
            "take_profit": RISK_PARAMS["take_profit"],
            "phases": [
                {
                    "phase": p["phase"],
                    "name": p["name"],
                    "duration_days": p["duration_days"],
                    "capital_ratio": p["capital_ratio"],
                    "tier_ref": p["tier_ref"],
                    "shares": plan["build_tiers"][i]["shares"],
                    "actual_amount": plan["build_tiers"][i]["actual_amount"],
                    "price_ref": plan["build_tiers"][i]["price_mid"],
                }
                for i, p in enumerate(BUILD_PHASES)
            ],
        }
    }

    # 阶段汇总
    current_date = start_date
    for i, phase in enumerate(BUILD_PHASES):
        phase_end = current_date + timedelta(days=phase["duration_days"])
        tier = plan["build_tiers"][i]
        plan["phase_summary"].append({
            "phase": phase["phase"],
            "name": phase["name"],
            "start_date": current_date.strftime("%Y-%m-%d"),
            "end_date": phase_end.strftime("%Y-%m-%d"),
            "duration_days": phase["duration_days"],
            "capital_ratio": phase["capital_ratio"],
            "tier_ref": phase["tier_ref"],
            "target_amount": tier["actual_amount"],
            "shares": tier["shares"],
            "price_range": f"{tier['price_low']}-{tier['price_high']}",
        })
        current_date = phase_end + timedelta(days=1)

    plan["metadata"]["end_date"] = current_date.strftime("%Y-%m-%d")
    plan["metadata"]["total_shares"] = cumulative_shares
    plan["metadata"]["total_actual_amount"] = cumulative_amount
    plan["metadata"]["avg_cost"] = cumulative_amount / cumulative_shares if cumulative_shares > 0 else 0

    return plan


def format_markdown_report(plan: Dict[str, Any]) -> str:
    """将建仓计划格式化为 Markdown 报告"""
    md = []
    md.append(f"# 中国神华({plan['metadata']['code']}) 建仓计划\n")
    md.append(f"**生成时间**: {plan['metadata']['generated_at']}\n")
    md.append(f"**策略来源**: {plan['metadata']['source_pdf']} ({plan['metadata']['source_date']})\n")
    md.append(f"**总投入资金**: {plan['metadata']['total_capital']:,.0f} 元\n")
    md.append(f"**建仓周期**: {plan['metadata']['start_date']} ~ {plan['metadata']['end_date']}\n\n")

    md.append("## 一、当前估值快照\n\n")
    v = plan["valuation_snapshot"]
    md.append(f"- 当前股价: **{v['current_price']} 元**\n")
    md.append(f"- PE(TTM): **{v['pe_ttm']} 倍** (3年区间 {v['pe_3y_low']}-{v['pe_3y_high']})\n")
    md.append(f"- PB: **{v['pb']} 倍** (3年区间 {v['pb_3y_low']}-{v['pb_3y_high']})\n")
    md.append(f"- 股息率 TTM: **{v['dividend_yield_ttm']*100:.2f}%**\n")
    md.append(f"- 总市值: {v['total_market_cap']/1e8:,.0f} 亿元\n\n")

    md.append("## 二、分析师目标价\n\n")
    md.append(f"- **平均目标价**: {plan['analyst_targets']['avg_target_price']} 元\n")
    md.append(f"- **上涨空间**: {plan['analyst_targets']['upside_potential']*100:.1f}%\n\n")
    md.append("| 机构 | 评级 | 目标价 | 发布日期 |\n")
    md.append("|------|------|--------|----------|\n")
    for s in plan["analyst_targets"]["sources"]:
        target_str = f"{s['target']} 元" if s['target'] else "-"
        md.append(f"| {s['institution']} | {s['rating']} | {target_str} | {s['date']} |\n")
    md.append("\n")

    md.append("## 三、三档建仓策略\n\n")
    md.append("| 档位 | 价位区间 | 价位中枢 | 仓位比例 | 股数 | 金额(元) | PE | 股息率 |\n")
    md.append("|------|---------|---------|---------|------|---------|----|--------|\n")
    for t in plan["build_tiers"]:
        md.append(f"| {t['name']} | {t['price_low']}-{t['price_high']} | {t['price_mid']} | "
                  f"{t['weight_ratio']*100:.0f}% | {t['shares']} | {t['actual_amount']:,.0f} | "
                  f"{t['pe_implied']}x | {t['dividend_yield_implied']*100:.2f}% |\n")
    md.append("\n")

    md.append("## 四、阶段执行计划\n\n")
    md.append("| 阶段 | 名称 | 起止日期 | 金额(元) | 股数 | 价位区间 |\n")
    md.append("|------|------|---------|---------|------|---------|\n")
    for p in plan["phase_summary"]:
        md.append(f"| {p['phase']} | {p['name']} | {p['start_date']}~{p['end_date']} | "
                  f"{p['target_amount']:,.0f} | {p['shares']} | {p['price_range']} 元 |\n")
    md.append("\n")

    md.append("## 五、风险控制\n\n")
    rp = plan["risk_params"]
    md.append(f"- 止损线: **{rp['stop_loss']*100:.0f}%**\n")
    md.append(f"- 止盈线: **+{rp['take_profit']*100:.0f}%** (对应目标价区间)\n")
    md.append(f"- 单笔最大加仓: {rp['max_position_per_trade']*100:.0f}%\n")
    md.append(f"- 移动止损: {rp['trailing_stop']*100:.0f}%\n\n")

    md.append("## 六、执行规则\n\n")
    er = plan["execution_rules"]
    md.append(f"- 上午窗口: {er['daily_timing']['morning_window'][0]}-{er['daily_timing']['morning_window'][1]}\n")
    md.append(f"- 下午窗口: {er['daily_timing']['afternoon_window'][0]}-{er['daily_timing']['afternoon_window'][1]}\n")
    md.append(f"- 折价买入阈值: 中枢下方 {er['price_rules']['discount_buy']*100:.0f}%\n")
    md.append(f"- 溢价跳过阈值: 中枢上方 {er['price_rules']['premium_skip']*100:.0f}%\n")
    md.append(f"- 最小交易单位: {er['min_lots']} 股\n\n")

    md.append("## 七、汇总\n\n")
    md.append(f"- **总股数**: {plan['metadata']['total_shares']} 股\n")
    md.append(f"- **总金额**: {plan['metadata']['total_actual_amount']:,.0f} 元\n")
    md.append(f"- **平均成本**: {plan['metadata']['avg_cost']:.2f} 元\n")

    return "".join(md)


# =============================================================
# 主入口
# =============================================================
def main():
    parser = argparse.ArgumentParser(
        description="中国神华(601088) 建仓计划生成器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--capital", type=float, default=1_000_000,
                        help="总投入资金(元)，默认 100 万")
    parser.add_argument("--start-date", type=str, default=None,
                        help="建仓起始日(YYYY-MM-DD)，默认今日")
    parser.add_argument("--output-dir", type=str, default=".",
                        help="输出目录")
    args = parser.parse_args()

    # 解析起始日
    if args.start_date:
        start_date = datetime.strptime(args.start_date, "%Y-%m-%d")
    else:
        start_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # 生成计划
    plan = build_position_plan(args.capital, start_date)

    # 输出文件名
    date_str = datetime.now().strftime("%Y%m%d")
    json_path = os.path.join(args.output_dir, f"中国神华建仓计划_{date_str}.json")
    md_path = os.path.join(args.output_dir, f"中国神华建仓计划_{date_str}.md")

    # 保存 JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    print(f"[OK] JSON 已保存: {json_path}")

    # 保存 Markdown
    md_report = format_markdown_report(plan)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_report)
    print(f"[OK] Markdown 已保存: {md_path}")

    # 控制台打印汇总
    print("\n" + "=" * 60)
    print(f"中国神华({SHENHUA_CODE}) 建仓计划汇总")
    print("=" * 60)
    print(f"总资金: {args.capital:,.0f} 元")
    print(f"总股数: {plan['metadata']['total_shares']} 股")
    print(f"平均成本: {plan['metadata']['avg_cost']:.2f} 元")
    print(f"止损线: {RISK_PARAMS['stop_loss']*100:.0f}%  止盈线: +{RISK_PARAMS['take_profit']*100:.0f}%")
    print("=" * 60)
    for t in plan["build_tiers"]:
        print(f"{t['name']}: {t['price_low']}-{t['price_high']}元 × "
              f"{t['shares']}股 = {t['actual_amount']:,.0f}元 (仓位{t['weight_ratio']*100:.0f}%)")


if __name__ == "__main__":
    main()
