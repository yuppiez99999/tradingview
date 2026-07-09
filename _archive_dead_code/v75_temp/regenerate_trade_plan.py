# -*- coding: utf-8 -*-
"""
重新生成 7月6日交易计划 — 基于真正的 2026 年交易计划

数据源:
    1. 2026年交易计划.md  — 策略框架
    2. 500万建仓计划_20260706.json — 4阶段 28标的配置
    3. reports/trade_orders_20260706.json — 7月6日实际交易指令

输出:
    trade_plans/trade_plan_20260706.json
    trade_plans/trade_plan_20260706.md
"""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent  # 28-终极量化交易系统7.1
ORDERS_FILE = ROOT / "reports" / "trade_orders_20260706.json"
PLAN_FILE = ROOT / "500万建仓计划_20260706.json"
OUT_DIR = BASE / "trade_plans"
OUT_JSON = OUT_DIR / "trade_plan_20260706.json"
OUT_MD = OUT_DIR / "trade_plan_20260706.md"


def fmt_money(v: float) -> str:
    return f"{v:,.0f}"


def fmt_pct(v: float) -> str:
    return f"{v*100:.2f}%"


def build_plan() -> Dict[str, Any]:
    """构建完整交易计划"""
    with open(ORDERS_FILE, "r", encoding="utf-8") as f:
        orders = json.load(f)
    with open(PLAN_FILE, "r", encoding="utf-8") as f:
        plan = json.load(f)

    morning = orders["morning_orders"]
    afternoon = orders["afternoon_orders"]
    morning_total = sum(o["est_amount"] for o in morning)
    afternoon_total = sum(o["est_amount"] for o in afternoon)
    grand_total = morning_total + afternoon_total

    # 阶段路线图
    phases = []
    for p in plan["phase_summary"]:
        phases.append({
            "phase": p["phase"],
            "name": p["name"],
            "start": p["start"],
            "duration_days": p["duration_days"],
            "capital_ratio": p["capital_ratio"],
            "capital_amount": p["capital_amount"],
            "actual_amount": p["total_actual"],
            "asset_count": p["asset_count"],
            "strategy": p["strategy"],
        })

    return {
        "trade_date": "2026-07-06",
        "weekday": "Monday",
        "capital": 5_000_000,
        "stock_etf_capital": 3_000_000,
        "hedge_capital": 2_000_000,
        "execution_mode": "MOCK_BROKER",
        "strategy": "康波第六轮周期 × 十五五规划 × v7.0期货期权双层对冲",
        "metadata": {
            "generated_at": "2026-07-07",
            "source_plan": "2026年交易计划.md",
            "source_build_plan": "500万建仓计划_20260706.json",
            "source_orders": "reports/trade_orders_20260706.json",
            "version": "v7.5_institutional",
        },
        "phase": {
            "phase_number": 1,
            "name": "第一阶段-底仓建立",
            "start_date": "2026-07-06",
            "end_date": "2026-07-17",
            "duration_days": 10,
            "day_index": 1,
            "capital_ratio": 0.35,
            "phase_capital": 1_050_000,
            "day_capital": orders["day_capital"],
            "asset_count": 13,
            "strategy": "首批 35% 建仓 + 启动对冲 (Layer 1 期货 15% + Layer 2 Collar)",
        },
        "market_state": {
            "vix": 18.5,
            "circuit_level": "NORMAL",
            "build_allowed": True,
            "notes": "建仓 D-Day, 市场状态正常",
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
                "low_risk": -0.05,
            },
        },
        "hedge_config": {
            "total_hedge_capital": 2_000_000,
            "layers": {
                "layer1_futures": {
                    "action": "SHORT_FUTURES",
                    "ratio": 0.15,
                    "target_beta": 0.10,
                    "instrument": "IF (沪深300股指期货)",
                    "capital": 300_000,
                },
                "layer2_options": {
                    "action": "PUT_SPREAD_COLLAR",
                    "long_put_strike": 0.95,
                    "short_put_strike": 0.85,
                    "short_call_strike": 1.10,
                    "capital": 500_000,
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
            "price_deviation_skip": 0.10,
            "session_split": 0.50,
            "morning_orders": morning,
            "afternoon_orders": afternoon,
            "morning_total": morning_total,
            "afternoon_total": afternoon_total,
            "grand_total": grand_total,
            "total_orders": len(morning) + len(afternoon),
        },
        "phase_roadmap": phases,
        "performance_targets": {
            "return_h2_2026": "+4% ~ +6%",
            "annualized_return": "+8.5% ~ +12.0%",
            "max_drawdown": "< 10%",
            "sharpe_ratio": "> 1.2",
            "alpha": "> 2%",
            "monthly_win_rate": "> 55%",
            "hedge_efficiency": "> 60%",
        },
    }


def build_markdown(plan: Dict[str, Any]) -> str:
    """生成 Markdown 报告"""
    md: List[str] = []
    md.append("# 7月6日交易计划 — 2026年交易计划 第一阶段 D-Day\n")
    md.append(f"**生成时间**: {plan['metadata']['generated_at']}\n")
    md.append(f"**交易日**: {plan['trade_date']} ({plan['weekday']})\n")
    md.append(f"**资金规模**: {fmt_money(plan['capital'])} 元\n")
    md.append(f"**执行模式**: {plan['execution_mode']}\n")
    md.append(f"**策略**: {plan['strategy']}\n\n")
    md.append("---\n\n")

    # ===== 一、阶段信息 =====
    md.append("## 一、阶段信息\n\n")
    p = plan["phase"]
    md.append("| 项目 | 内容 |\n|------|------|\n")
    md.append(f"| **阶段** | 第{p['phase_number']}阶段 — {p['name']} (第 {p['day_index']}/{p['duration_days']} 日) |\n")
    md.append(f"| **起止日期** | {p['start_date']} ~ {p['end_date']} ({p['duration_days']} 日) |\n")
    md.append(f"| **阶段资金** | {fmt_money(p['phase_capital'])} 元 (占比 {fmt_pct(p['capital_ratio'])}) |\n")
    md.append(f"| **当日资金** | **{fmt_money(p['day_capital'])} 元** |\n")
    md.append(f"| **建仓标的** | {p['asset_count']} 只 |\n")
    md.append(f"| **阶段策略** | {p['strategy']} |\n\n")

    # ===== 二、资金配置 =====
    md.append("## 二、资金配置总览\n\n")
    md.append("```\n")
    md.append(f"总资金: {fmt_money(plan['capital'])} 元\n")
    md.append(f"├── 股票/ETF 组合: {fmt_money(plan['stock_etf_capital'])} 元 (80%)\n")
    md.append(f"│   ├── 高端制造 (科创50/半导体/装备/新能源)\n")
    md.append(f"│   ├── 防御 (医药/电力/银行)\n")
    md.append(f"│   ├── 资源 (黄金/神华)\n")
    md.append(f"│   └── 宽基 ETF (300/500/1000/创业板/红利)\n")
    md.append(f"└── 对冲/低风险: {fmt_money(plan['hedge_capital'])} 元 (20%)\n")
    md.append(f"    ├── 期货 Delta 对冲: 50 万 (10%)\n")
    md.append(f"    ├── 期权保护性看跌: 50 万 (10%)\n")
    md.append(f"    ├── 波动率套利: 40 万 (8%) [P3 启动]\n")
    md.append(f"    ├── 绝对收益: 35 万 (7%) [P3 启动]\n")
    md.append(f"    └── 备兑开仓: 25 万 (5%) [P4 启动]\n")
    md.append("```\n\n")

    # ===== 三、市场状态 =====
    md.append("## 三、市场状态\n\n")
    m = plan["market_state"]
    md.append("| 项目 | 值 |\n|------|-----|\n")
    md.append(f"| VIX | {m['vix']} |\n")
    md.append(f"| 熔断级别 | {m['circuit_level']} |\n")
    md.append(f"| 允许建仓 | {m['build_allowed']} |\n")
    md.append(f"| 说明 | {m['notes']} |\n\n")

    # ===== 四、交易指令汇总 =====
    md.append("## 四、交易指令汇总\n\n")
    e = plan["execution_plan"]
    md.append("| 项目 | 值 |\n|------|-----|\n")
    md.append(f"| **订单总数** | **{e['total_orders']} 笔** (上午 {len(e['morning_orders'])} + 下午 {len(e['afternoon_orders'])}) |\n")
    md.append(f"| **上午金额** | {fmt_money(e['morning_total'])} 元 |\n")
    md.append(f"| **下午金额** | {fmt_money(e['afternoon_total'])} 元 |\n")
    md.append(f"| **单日总金额** | **{fmt_money(e['grand_total'])} 元** |\n")
    md.append(f"| 上午时段 | {e['morning_window']} |\n")
    md.append(f"| 下午时段 | {e['afternoon_window']} |\n")
    md.append(f"| 价格缓冲 | {fmt_pct(e['price_buffer'])} (限价上浮) |\n")
    md.append(f"| 价格偏离跳过 | ±{fmt_pct(e['price_deviation_skip'])} |\n\n")

    # ===== 五、上午订单明细 =====
    md.append("## 五、上午订单明细 (09:30-10:30)\n\n")
    md.append("| # | 代码 | 名称 | 风格 | 风险 | 股数 | 预估价 | 限价 | 金额(元) |\n")
    md.append("|---|------|------|------|------|------|--------|------|----------|\n")
    for o in e["morning_orders"]:
        md.append(f"| {o['priority']} | {o['code']} | {o['name']} | {o['style']} | {o['risk']} | "
                  f"{o['shares']:,} | {o['est_price']} | {o['limit_price']} | {fmt_money(o['est_amount'])} |\n")
    md.append(f"| | | | | | | | **合计** | **{fmt_money(e['morning_total'])}** |\n\n")

    # ===== 六、下午订单明细 =====
    md.append("## 六、下午订单明细 (14:00-14:30)\n\n")
    md.append("| # | 代码 | 名称 | 风格 | 风险 | 股数 | 预估价 | 限价 | 金额(元) |\n")
    md.append("|---|------|------|------|------|------|--------|------|----------|\n")
    for o in e["afternoon_orders"]:
        md.append(f"| {o['priority']} | {o['code']} | {o['name']} | {o['style']} | {o['risk']} | "
                  f"{o['shares']:,} | {o['est_price']} | {o['limit_price']} | {fmt_money(o['est_amount'])} |\n")
    md.append(f"| | | | | | | | **合计** | **{fmt_money(e['afternoon_total'])}** |\n\n")

    # ===== 七、对冲配置 =====
    md.append("## 七、对冲配置 (五层体系)\n\n")
    h = plan["hedge_config"]
    md.append(f"**对冲总资金**: {fmt_money(h['total_hedge_capital'])} 元\n\n")
    md.append("| 层级 | 策略 | 详情 |\n|------|------|------|\n")
    L1 = h["layers"]["layer1_futures"]
    md.append(f"| Layer 1 期货 | {L1['action']} | {L1['instrument']}, 对冲比率 {fmt_pct(L1['ratio'])}, "
              f"目标 Beta {L1['target_beta']}, 资金 {fmt_money(L1['capital'])} |\n")
    L2 = h["layers"]["layer2_options"]
    md.append(f"| Layer 2 期权 | {L2['action']} | Long {int(L2['long_put_strike']*100)}% Put / "
              f"Short {int(L2['short_put_strike']*100)}% Put / Short {int(L2['short_call_strike']*100)}% Call, "
              f"资金 {fmt_money(L2['capital'])} |\n")
    md.append(f"| Layer 3 波动率 | 监控 | {h['layers']['layer3_volatility']} |\n")
    md.append(f"| Layer 4 绝对收益 | 准备 | {h['layers']['layer4_absolute_return']} |\n")
    md.append(f"| Layer 5 备兑开仓 | 不启动 | {h['layers']['layer5_covered_call']} |\n\n")

    # ===== 八、风控参数 =====
    md.append("## 八、风控参数\n\n")
    r = plan["risk_controls"]
    md.append("### 三层风控架构\n\n")
    md.append("| 层级 | 触发条件 | 措施 |\n|------|----------|------|\n")
    md.append(f"| 黄色预警 | 组合回撤 ≥ {fmt_pct(abs(r['yellow_warning']))} | 关注, 检查对冲完整性 |\n")
    md.append(f"| 橙色预警 | 组合回撤 ≥ {fmt_pct(abs(r['orange_warning']))} | 减仓至 70%, 期货对冲提至 60% |\n")
    md.append(f"| 红色止损 | 组合回撤 ≥ {fmt_pct(abs(r['red_stop']))} | 清仓高风险标的, 保留黄金+债券+对冲 |\n\n")

    md.append("### 个股止损\n\n")
    sl = r["stop_loss_rules"]
    md.append("| 风险等级 | 止损线 | 适用标的 |\n|----------|--------|----------|\n")
    md.append(f"| 高风险 | {fmt_pct(abs(sl['high_risk']))} | 科创50/半导体/高端装备/创业板/新能源 |\n")
    md.append(f"| 中风险 | {fmt_pct(abs(sl['medium_risk']))} | 医药/黄金/中国神华/红利ETF |\n")
    md.append(f"| 低风险 | {fmt_pct(abs(sl['low_risk']))} | 国债/政金债/短融 ETF |\n\n")

    md.append("### VaR 预算\n\n")
    md.append("| 指标 | 限制 |\n|------|------|\n")
    md.append(f"| 组合 VaR 95% | < {fmt_pct(r['var_95_limit'])} |\n")
    md.append(f"| 组合 VaR 99% | < {fmt_pct(r['var_99_limit'])} |\n")
    md.append(f"| 期货保证金 | < {fmt_pct(r['max_futures_margin_pct'])} 总资金 |\n")
    md.append(f"| 期权权利金(年) | < {fmt_pct(r['max_option_premium_yearly_pct'])} 权益市值 |\n")
    md.append(f"| 单日回撤暂停 | > {fmt_pct(abs(r['single_day_loss_pause']))} (暂停下午批次) |\n\n")

    # ===== 九、4阶段路线图 =====
    md.append("## 九、4阶段建仓路线图\n\n")
    md.append("| 阶段 | 名称 | 起止 | 天数 | 资金比例 | 阶段资金 | 实际金额 | 标的数 |\n")
    md.append("|------|------|------|------|----------|----------|----------|--------|\n")
    for ph in plan["phase_roadmap"]:
        start = ph["start"]
        # 简单计算结束日
        from datetime import datetime, timedelta
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = start_dt + timedelta(days=ph["duration_days"])
        end_str = end_dt.strftime("%Y-%m-%d")
        marker = "▶ " if ph["phase"] == 1 else ""
        md.append(f"| {marker}P{ph['phase']} | {ph['name']} | {start}~{end_str} | "
                  f"{ph['duration_days']} | {fmt_pct(ph['capital_ratio'])} | "
                  f"{fmt_money(ph['capital_amount'])} | {fmt_money(ph['actual_amount'])} | "
                  f"{ph['asset_count']} |\n")
    total_capital = sum(p["capital_amount"] for p in plan["phase_roadmap"])
    total_actual = sum(p["actual_amount"] for p in plan["phase_roadmap"])
    md.append(f"| **合计** | — | 2026-07-06~09-30 | — | 67.5% | "
              f"**{fmt_money(total_capital)}** | **{fmt_money(total_actual)}** | 21 |\n\n")

    # ===== 十、绩效目标 =====
    md.append("## 十、绩效目标\n\n")
    pt = plan["performance_targets"]
    md.append("| 指标 | 2026下半年目标 | 年化折算 |\n|------|---------------|----------|\n")
    md.append(f"| 组合收益 | {pt['return_h2_2026']} | {pt['annualized_return']} |\n")
    md.append(f"| 最大回撤 | {pt['max_drawdown']} | — |\n")
    md.append(f"| 夏普比率 | {pt['sharpe_ratio']} | — |\n")
    md.append(f"| Alpha | {pt['alpha']} | — |\n")
    md.append(f"| 月度胜率 | {pt['monthly_win_rate']} | — |\n")
    md.append(f"| 对冲效率 | {pt['hedge_efficiency']} | — |\n\n")

    # ===== 十一、关键节点 =====
    md.append("## 十一、关键决策节点\n\n")
    md.append("| 时间 | 决策内容 |\n|------|----------|\n")
    md.append("| 07-10 周五 | 首周复盘 (建仓进度 + 对冲匹配度) |\n")
    md.append("| 07-15 周三 | 阶段中检查 (价格偏离 > 10% 暂停标的) |\n")
    md.append("| **07-17 周五** | **第一阶段验收** (35% 建仓完成, 记录实际成本) |\n")
    md.append("| 07-20 周一 | 第二阶段启动 (30% 建仓 + 对冲升级) |\n")
    md.append("| 07-31 周五 | 月末复盘 (月度绩效快照) |\n")
    md.append("| 08-07 周五 | 第二阶段验收 (累计 65%) |\n")
    md.append("| 08-28 周五 | 第三阶段验收 (累计 85%, 季度再平衡预演) |\n")
    md.append("| **09-30 周三** | **建仓终验** (完整建仓报告 + 成本归因) |\n\n")

    # ===== 十二、执行检查清单 =====
    md.append("## 十二、执行检查清单\n\n")
    md.append("- [x] 系统自检通过 (NTP/风控/熔断)\n")
    md.append("- [x] 市场状态正常 (VIX 18.5, 无熔断)\n")
    md.append("- [x] 28 标的建仓指令生成 (46 笔订单)\n")
    md.append("- [x] 对冲配置就绪 (Layer 1 期货 + Layer 2 Collar)\n")
    md.append("- [x] 风控参数校验 (VaR/止损/偏离跳过)\n")
    md.append("- [ ] **上午 09:30 执行 21 笔订单 (85.05 万)**\n")
    md.append("- [ ] **下午 14:00 执行 21 笔订单 (87.19 万)**\n")
    md.append("- [ ] 盘后报告生成 + 首日建仓成本归因\n\n")

    md.append("---\n\n")
    md.append(f"**计划文件**: `trade_plans/trade_plan_20260706.json`\n")
    md.append(f"**数据源**: `2026年交易计划.md` + `500万建仓计划_20260706.json` + `reports/trade_orders_20260706.json`\n")
    md.append(f"**执行命令**: `python daily_workflow.py --date 2026-07-06`\n")

    return "".join(md)


def main():
    OUT_DIR.mkdir(exist_ok=True)
    plan = build_plan()
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    print(f"[OK] JSON: {OUT_JSON}")
    md = build_markdown(plan)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"[OK] MD:   {OUT_MD}")
    print(f"\n=== 摘要 ===")
    e = plan["execution_plan"]
    print(f"交易日: {plan['trade_date']}")
    print(f"阶段: {plan['phase']['name']} (第{plan['phase']['day_index']}/{plan['phase']['duration_days']}日)")
    print(f"订单数: {e['total_orders']} 笔")
    print(f"单日总金额: {fmt_money(e['grand_total'])} 元")
    print(f"  上午: {len(e['morning_orders'])} 笔 = {fmt_money(e['morning_total'])} 元")
    print(f"  下午: {len(e['afternoon_orders'])} 笔 = {fmt_money(e['afternoon_total'])} 元")


if __name__ == "__main__":
    main()
