"""
将 ETF 资金净流入 TOP10 中缺失的标的加入年度/本周交易计划，
并生成本周 5 天交易计划文件。

缺失标的：512100、510500、588200、159516
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(r"e:\各种PY程序\28-终极量化交易系统7.1")
PLAN_DIR = BASE / "v7.5_institutional" / "trade_plans"
BUILD_PLAN_FILE = BASE / "500万建仓计划_20260706.json"

# 本周：2026-07-20 ~ 2026-07-24
WEEK_START = datetime(2026, 7, 20)
WEEK_DAYS = ["周一", "周二", "周三", "周四", "周五"]
WEEK_DATES = [(WEEK_START + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(5)]

# 缺失标的配置
MISSING_ETFS = {
    "512100": {
        "name": "南方中证1000ETF",
        "code": "512100",
        "type": "ETF",
        "risk": "中",
        "style": "宽基",
        "weight": 0.03,
        "base_weight": 0.03,
        "adjustable": True,
        "broad_based": True,
        "est_price": 2.65,
        "lots": 100,
        "target_amount": 150000.0,
        "total_shares": 56700,
        "actual_amount": 0.0,
        "reason": "小盘风格宽基, 十五五专精特新映射, 资金净流入20亿信号",
        "stop_loss": -0.10,
        "etf_flow_signal": "strong",
        "net_flow_yi": 20.12,
    },
    "510500": {
        "name": "南方中证500ETF",
        "code": "510500",
        "type": "ETF",
        "risk": "中",
        "style": "宽基",
        "weight": 0.04,
        "base_weight": 0.04,
        "adjustable": True,
        "broad_based": True,
        "est_price": 6.2,
        "lots": 100,
        "target_amount": 200000.0,
        "total_shares": 32200,
        "actual_amount": 0.0,
        "reason": "中盘成长宽基, 承接结构性流入, 资金净流入18.5亿",
        "stop_loss": -0.10,
        "etf_flow_signal": "strong",
        "net_flow_yi": 18.53,
    },
    "588200": {
        "name": "嘉实上证科创板芯片ETF",
        "code": "588200",
        "type": "ETF",
        "risk": "高",
        "style": "高端制造",
        "weight": 0.025,
        "base_weight": 0.025,
        "adjustable": True,
        "broad_based": False,
        "est_price": 1.05,
        "lots": 100,
        "target_amount": 125000.0,
        "total_shares": 119000,
        "actual_amount": 0.0,
        "reason": "芯片自主可控, 十五五半导体重点, 资金净流入16.8亿",
        "stop_loss": -0.12,
        "etf_flow_signal": "strong",
        "net_flow_yi": 16.81,
    },
    "159516": {
        "name": "国泰中证半导体材料设备主题ETF",
        "code": "159516",
        "type": "ETF",
        "risk": "高",
        "style": "高端制造",
        "weight": 0.02,
        "base_weight": 0.02,
        "adjustable": True,
        "broad_based": False,
        "est_price": 0.85,
        "lots": 100,
        "target_amount": 100000.0,
        "total_shares": 117600,
        "actual_amount": 0.0,
        "reason": "半导体材料/设备, 国产替代主线, 资金净流入13.5亿",
        "stop_loss": -0.12,
        "etf_flow_signal": "strong",
        "net_flow_yi": 13.49,
    },
}


def update_build_plan():
    """更新 500万建仓计划：加入缺失的 ETF"""
    with open(BUILD_PLAN_FILE, encoding="utf-8") as f:
        plan = json.load(f)

    target_portfolio = plan.get("target_portfolio", {})
    position_plan = plan.get("position_plan", {})

    added = []
    for code, info in MISSING_ETFS.items():
        if code not in target_portfolio:
            target_portfolio[code] = {
                "name": info["name"],
                "type": info["type"],
                "risk": info["risk"],
                "style": info["style"],
                "weight": info["weight"],
                "est_price": info["est_price"],
                "lots": info["lots"],
                "target_amount": info["target_amount"],
                "total_shares": info["total_shares"],
                "actual_amount": info["actual_amount"],
                "reason": info["reason"],
            }
            added.append(code)

        if code not in position_plan:
            position_plan[code] = {
                "code": code,
                "name": info["name"],
                "type": info["type"],
                "risk": info["risk"],
                "style": info["style"],
                "target_weight": info["weight"],
                "target_amount": info["target_amount"],
                "est_price": info["est_price"],
                "total_shares": info["total_shares"],
                "actual_amount": info["actual_amount"],
                "reason": info["reason"],
                "stop_loss": info["stop_loss"],
                "phases": [
                    {
                        "phase": 1,
                        "name": "第一阶段-底仓建立",
                        "start": "2026-07-20",
                        "capital_ratio": 0.4,
                        "target_amount": info["target_amount"] * 0.4,
                        "shares": info["total_shares"] * 40 // 100 * 100,
                        "actual_amount": 0.0,
                        "cumulative_ratio": 0.4,
                        "base_amount": info["target_amount"] * 0.4,
                    },
                    {
                        "phase": 2,
                        "name": "第二阶段-配置完善",
                        "start": "2026-07-22",
                        "capital_ratio": 0.6,
                        "target_amount": info["target_amount"] * 0.6,
                        "shares": info["total_shares"] * 60 // 100 * 100,
                        "actual_amount": 0.0,
                        "cumulative_ratio": 1.0,
                        "base_amount": info["target_amount"] * 0.6,
                    },
                ],
            }

    # 重新归一化权重
    total_weight = sum(float(v.get("weight", 0)) for v in target_portfolio.values())
    if total_weight > 0:
        for info in target_portfolio.values():
            info["weight"] = round(float(info.get("weight", 0)) / total_weight, 6)

    # 更新 target_count
    plan["metadata"]["target_count"] = len(target_portfolio)
    plan["metadata"]["note"] = plan["metadata"].get("note", "") + " | 2026-07-21 新增4只资金净流入TOP10缺失标的(512100/510500/588200/159516)"

    # 更新风格分布
    style_amounts = {}
    style_risks = {}
    for _code, info in target_portfolio.items():
        style = info.get("style", "其他")
        amount = info.get("target_amount", 0)
        risk = info.get("risk", "中")
        style_amounts[style] = style_amounts.get(style, 0) + amount
        if style not in style_risks:
            style_risks[style] = {}
        style_risks[style][risk] = style_risks[style].get(risk, 0) + amount

    plan["style_distribution"] = {}
    for style, amount in style_amounts.items():
        plan["style_distribution"][style] = {
            "amount": amount,
            "weight": round(amount / sum(style_amounts.values()), 4),
            "codes": [code for code, info in target_portfolio.items() if info.get("style") == style],
            "risk_distribution": style_risks.get(style, {}),
        }

    # 更新 broad_based_policy
    plan["broad_based_policy"]["codes"] = list(set([*plan["broad_based_policy"].get("codes", []), "512100", "510500"]))

    with open(BUILD_PLAN_FILE, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    print(f"已更新建仓计划: {BUILD_PLAN_FILE.name}")
    print(f"新增标的: {added}")
    print(f"当前标的数: {len(target_portfolio)}")
    return added


def generate_weekly_plan():
    """生成本周 5 天交易计划"""
    week_plan = {
        "week_start": WEEK_START.strftime("%Y-%m-%d"),
        "week_end": (WEEK_START + timedelta(days=4)).strftime("%Y-%m-%d"),
        "week_number": 3,
        "phase": "Phase 2 快速建仓期(加速)",
        "phase_period": "2026-07-20 to 2026-08-07",
        "days_elapsed": 11,
        "days_remaining": 17,
        "total_days": 28,
        "progress_pct": 39.3,
        "daily_capital": 200000,
        "weekly_capital_target": 1000000,
        "generated_at": datetime.now().isoformat(),
        "daily_plans": [],
        "key_notes": [
            "本周为建仓加速周, 每日20万现货建仓 + 150万对冲资金",
            "新增4只ETF资金净流入TOP10标的: 512100/510500/588200/159516",
            "累计建仓中: 已执行约140万/300万 (约47%完成)",
            "周四-周五为建仓后半段, 优先补齐缺口标的",
            "Theta Covered Call月度计划有效至8/17",
            "IF期货5手对冲 (LLM建议增持), KillSwitch=L0正常",
            "周五下午14:00-14:30为本周最后一笔建仓窗口",
        ],
        "targets_this_week": {
            "daily_spot_build": "20万/日",
            "weekly_spot_target": "100万",
            "cumulative_by_friday": "预计累计建仓约240万 (80%完成)",
            "hedge_maintenance": "5手IF + 6标的Covered Call + 4只Put保护",
            "new_etfs_this_week": "512100/510500/588200/159516",
            "risk_checks": "每日熔断检查 + ETF资金流监测 + 宽基加减仓信号",
        },
        "new_etfs_added": [
            {"code": "512100", "name": "南方中证1000ETF", "net_flow_yi": 20.12, "style": "宽基"},
            {"code": "510500", "name": "南方中证500ETF", "net_flow_yi": 18.53, "style": "宽基"},
            {"code": "588200", "name": "嘉实上证科创板芯片ETF", "net_flow_yi": 16.81, "style": "高端制造"},
            {"code": "159516", "name": "国泰中证半导体材料设备主题ETF", "net_flow_yi": 13.49, "style": "高端制造"},
        ],
    }

    for i, date in enumerate(WEEK_DATES):
        week_plan["daily_plans"].append({
            "date": date,
            "weekday": WEEK_DAYS[i],
            "day_index": 11 + i,
            "file": f"trade_plan_{date.replace('-', '')}.json",
            "sessions": ["09:30 早盘", "14:00 午盘"] + (["21:00 夜盘"] if i < 4 else []),
            "focus": "新ETF建仓" if i < 2 else "补齐缺口 + 对冲维护",
        })

    week_plan_path = PLAN_DIR / f"weekly_plan_{WEEK_START.strftime('%Y%m%d')}_{(WEEK_START + timedelta(days=4)).strftime('%Y%m%d')}.json"
    with open(week_plan_path, "w", encoding="utf-8") as f:
        json.dump(week_plan, f, ensure_ascii=False, indent=2)

    print(f"\n已生成本周计划: {week_plan_path.name}")
    print(f"本周日期: {WEEK_START.strftime('%Y-%m-%d')} ~ {(WEEK_START + timedelta(days=4)).strftime('%Y-%m-%d')}")
    print("每日计划文件:")
    for day in week_plan["daily_plans"]:
        print(f"  {day['date']} ({day['weekday']}): {day['file']}")
    return week_plan_path


def update_symbol_info():
    """更新 generate_daily_trade_plan.py 中的 SYMBOL_INFO"""
    sym_file = BASE / "v7.5_institutional" / "generate_daily_trade_plan.py"
    if not sym_file.exists():
        print("[WARN] 未找到 generate_daily_trade_plan.py")
        return

    text = sym_file.read_text(encoding="utf-8")

    additions = '''
    "512100": {"name": "中证1000ETF南方", "est_price": 2.65, "style": "宽基", "risk": "中", "lots": 100},
    "510500": {"name": "中证500ETF南方", "est_price": 6.2, "style": "宽基", "risk": "中", "lots": 100},
    "588200": {"name": "科创板芯片ETF嘉实", "est_price": 1.05, "style": "高端制造", "risk": "高", "lots": 100},
    "159516": {"name": "半导体材料设备ETF国泰", "est_price": 0.85, "style": "高端制造", "risk": "高", "lots": 100},
'''

    if '"512100"' not in text:
        text = text.replace(
            '    "601088": {"name": "中国神华", "est_price": 42.04, "style": "顺周期", "risk": "中", "lots": 100},',
            '    "601088": {"name": "中国神华", "est_price": 42.04, "style": "顺周期", "risk": "中", "lots": 100},' + additions
        )
        sym_file.write_text(text, encoding="utf-8")
        print("已更新 generate_daily_trade_plan.py SYMBOL_INFO")
    else:
        print("generate_daily_trade_plan.py SYMBOL_INFO 已包含新标的，跳过")


if __name__ == "__main__":
    print("=" * 80)
    print("加入年度/本周交易计划 + 生成本周自动交易计划")
    print("=" * 80)

    print("\n[1/3] 更新 500万建仓计划...")
    update_build_plan()

    print("\n[2/3] 更新 generate_daily_trade_plan.py SYMBOL_INFO...")
    update_symbol_info()

    print("\n[3/3] 生成本周交易计划...")
    week_path = generate_weekly_plan()

    print("\n" + "=" * 80)
    print("完成!")
    print("=" * 80)
    print(f"\n本周计划文件: {week_path}")
    print(f"建仓计划文件: {BUILD_PLAN_FILE}")
    print("\n新增4只ETF:")
    for code, info in MISSING_ETFS.items():
        print(f"  {code} {info['name']} 资金净流入={info['net_flow_yi']}亿 风格={info['style']}")
