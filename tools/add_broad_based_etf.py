# -*- coding: utf-8 -*-
"""
将宽基ETF (沪深300/中证500/上证50/中证1000) 加入 500万建仓计划,
并标记其「根据社保国家队ETF资金净流入加减仓」的可调节属性。

权重调整: 现有13标的按 0.84 缩放, 4只宽基ETF 合计占 16%
  (510300 5% / 510500 4% / 510050 4% / 512100 3%)
最后全局归一化, 使总权重=1.0、总金额=total_capital(500万)。
每只宽基ETF 写入 base_weight / base_shares / base_amount, 供每日幂等加减仓使用。

运行:
  python add_broad_based_etf.py
"""
import json
from pathlib import Path
from datetime import datetime

import utils.broad_based_etf_policy as policy

PLAN_FILE = Path(r"e:\各种PY程序\28-终极量化交易系统8.4\500万建仓计划_20260706.json")

SCALE_FACTOR = 0.84          # 现有标的缩放系数 (1 - 0.16)
BROAD_BASED = policy.BROAD_BASED_ETFS

PHASE_TEMPLATES = [
    {"phase": 1, "name": "第一阶段-底仓建立", "start": "2026-07-06", "capital_ratio": 0.35},
    {"phase": 2, "name": "第二阶段-配置完善", "start": "2026-07-20", "capital_ratio": 0.30},
    {"phase": 3, "name": "第三阶段-防御补充", "start": "2026-08-10", "capital_ratio": 0.20},
    {"phase": 4, "name": "第四阶段-最终调整", "start": "2026-09-01", "capital_ratio": 0.15},
]


def _shares(amount, est_price, lots):
    if not est_price or est_price <= 0:
        return 0
    raw = amount / est_price
    return int(raw // lots) * lots


def main():
    with open(PLAN_FILE, "r", encoding="utf-8") as f:
        plan = json.load(f)

    total_capital = float(plan["metadata"].get("total_capital", 5_000_000))
    tp = plan["target_portfolio"]
    pp = plan["position_plan"]

    # 1. 计算期望原始权重: 现有 *0.84, 宽基用 base_weight
    raw_w = {}
    for code, info in tp.items():
        raw_w[code] = float(info.get("weight", 0.0)) * SCALE_FACTOR
    for etf in BROAD_BASED:
        raw_w[etf["code"]] = etf["base_weight"]
    s = sum(raw_w.values())
    norm_w = {c: w / s for c, w in raw_w.items()}   # 归一化到 1.0

    # 2. 写回 target_portfolio
    print("=== 更新 target_portfolio (现有13标的缩放 + 新增宽基) ===")
    for code, info in tp.items():
        w = norm_w[code]
        amt = round(total_capital * w, 2)
        ep = info.get("est_price", 0.0)
        lots = info.get("lots", 100)
        shares = _shares(amt, ep, lots)
        info["weight"] = round(w, 6)
        info["target_amount"] = amt
        info["total_shares"] = shares
        info["actual_amount"] = round(shares * ep, 2)
        print(f"  {code} {info['name']}: {w:.4%} | CNY {amt:,.0f}")

    for etf in BROAD_BASED:
        code = etf["code"]
        w = norm_w[code]
        amt = round(total_capital * w, 2)
        ep = etf["est_price"]
        lots = etf["lots"]
        shares = _shares(amt, ep, lots)
        tp[code] = {
            "code": code, "name": etf["name"], "type": "ETF",
            "risk": "中", "style": "宽基",
            "weight": round(w, 6), "base_weight": round(w, 6),
            "adjustable": True, "broad_based": True,
            "est_price": ep, "lots": lots,
            "target_amount": amt, "total_shares": shares,
            "actual_amount": round(shares * ep, 2),
            "reason": etf["reason"],
        }
        print(f"  {code} {etf['name']} [宽基]: {w:.4%} | CNY {amt:,.0f} | {shares}股")

    # 3. 写回 position_plan (含 base_weight), 阶段金额按归一化权重重算
    print("\n=== 更新 position_plan ===")
    for code, pos in pp.items():
        w = norm_w[code]
        amt = round(total_capital * w, 2)
        ep = pos.get("est_price", 0.0)
        lots = pos.get("lots", 100)
        total_shares = _shares(amt, ep, lots)
        pos["target_weight"] = round(w, 6)
        pos["target_amount"] = amt
        pos["total_shares"] = total_shares
        pos["actual_amount"] = round(total_shares * ep, 2)
        for ph in pos.get("phases", []):
            ph_amt = round(amt * ph["capital_ratio"], 2)
            ph["target_amount"] = ph_amt
            ph["base_amount"] = ph_amt
            ph["actual_amount"] = ph_amt
            ph["shares"] = int(total_shares * ph["capital_ratio"] // lots) * lots
        print(f"  {code} {pos['name']}: {w:.4%}")

    for etf in BROAD_BASED:
        code = etf["code"]
        w = norm_w[code]
        amt = round(total_capital * w, 2)
        ep = etf["est_price"]
        lots = etf["lots"]
        total_shares = _shares(amt, ep, lots)
        phases = []
        for tmpl in PHASE_TEMPLATES:
            ph_amt = round(amt * tmpl["capital_ratio"], 2)
            ph_shares = int(total_shares * tmpl["capital_ratio"] // lots) * lots
            phases.append({
                "phase": tmpl["phase"], "name": tmpl["name"], "start": tmpl["start"],
                "capital_ratio": tmpl["capital_ratio"],
                "target_amount": ph_amt, "base_amount": ph_amt,
                "shares": ph_shares, "actual_amount": round(ph_shares * ep, 2),
                "cumulative_ratio": round(sum(p["capital_ratio"] for p in PHASE_TEMPLATES[:tmpl["phase"]]), 2),
                "code": code,
            })
        pp[code] = {
            "code": code, "name": etf["name"], "type": "ETF",
            "risk": "中", "style": "宽基",
            "target_weight": round(w, 6), "base_weight": round(w, 6),
            "adjustable": True, "broad_based": True,
            "target_amount": amt, "est_price": ep, "total_shares": total_shares,
            "actual_amount": round(total_shares * ep, 2),
            "reason": etf["reason"], "stop_loss": -0.12,
            "phases": phases,
        }
        print(f"  {code} {etf['name']} [宽基]: {w:.4%} | 4阶段已生成")

    # 4. 更新 phase_summary 资产股数/金额 (每日下单股数来源)
    print("\n=== 更新 phase_summary assets ===")
    for phase in plan.get("phase_summary", []):
        tmpl = PHASE_TEMPLATES[phase["phase"] - 1]
        # 现有资产按 0.84 缩放并取整到 lots
        for asset in phase.get("assets", []):
            code = asset.get("code")
            ep = tp.get(code, {}).get("est_price") or pp.get(code, {}).get("est_price") or 0.0
            lots = tp.get(code, {}).get("lots", 100)
            old_shares = int(asset.get("shares", 0))
            new_shares = int(round(old_shares * SCALE_FACTOR / lots) * lots)
            asset["shares"] = new_shares
            if ep and ep > 0:
                asset["amount"] = round(new_shares * ep, 2)
        # 新增宽基资产
        for etf in BROAD_BASED:
            code = etf["code"]
            ep = etf["est_price"]
            lots = etf["lots"]
            pos = pp[code]
            ph = pos["phases"][phase["phase"] - 1]
            shares = ph["shares"]
            amount = round(shares * ep, 2)
            phase["assets"].append({
                "code": code, "name": etf["name"],
                "shares": shares, "base_shares": shares,
                "amount": amount, "base_amount": amount,
            })
        phase["asset_count"] = len(phase["assets"])
        print(f"  阶段{phase['phase']} asset_count={phase['asset_count']}")

    # 5. 更新 style_summary: 新增 宽基 风格
    style_summary = plan.get("style_summary", {})
    bb_codes = [e["code"] for e in BROAD_BASED]
    bb_amount = sum(float(tp[c]["target_amount"]) for c in bb_codes)
    style_summary["宽基"] = {
        "amount": round(bb_amount, 2),
        "weight": round(bb_amount / total_capital, 6),
        "codes": bb_codes,
        "risk_distribution": {"中": round(bb_amount, 2)},
    }
    print(f"\n宽基风格: CNY {bb_amount:,.0f} | {bb_amount/total_capital:.2%}")

    # 6. metadata + 宽基ETF政策说明
    plan["metadata"]["target_count"] = len(tp)
    plan["metadata"]["note"] = (
        plan["metadata"].get("note", "")
        + " | 2026-07-19 新增4只宽基ETF(沪深300/中证500/上证50/中证1000)共约16%, "
        "现有13标的X0.84; 宽基ETF按社保国家队ETF资金净流入加减仓"
    )
    plan["metadata"]["rebalanced_at"] = datetime.now().isoformat()
    plan["broad_based_policy"] = {
        "description": "宽基ETF权重根据社保国家队ETF资金净流入(亿元)信号动态加减仓",
        "codes": bb_codes,
        "adjust_bands": [
            {"net_flow_yi_min": 50, "action": "强加仓", "factor": 0.30},
            {"net_flow_yi_min": 10, "action": "加仓", "factor": 0.15},
            {"net_flow_yi_min": 2, "action": "小幅加仓", "factor": 0.05},
            {"net_flow_yi_min": -2, "action": "持有", "factor": 0.0},
            {"net_flow_yi_min": -10, "action": "小幅减仓", "factor": -0.05},
            {"net_flow_yi_min": -50, "action": "减仓", "factor": -0.15},
            {"net_flow_yi_min": None, "action": "强减仓", "factor": -0.30},
        ],
        "scale_limit": [0.5, 1.5],
        "data_source": "utils.etf_flow_monitor.ETFRealTimeTracker (Wind MCP > iFinD > 新浪)",
    }

    # 7. 校验
    total_weight = sum(float(v.get("weight", 0)) for v in tp.values())
    total_amount = sum(float(v.get("target_amount", 0)) for v in tp.values())
    print("\n=== 校验 ===")
    print(f"  标的数量: {len(tp)}")
    print(f"  总权重: {total_weight:.6f} ({total_weight*100:.4f}%)")
    print(f"  总金额: CNY {total_amount:,.0f} (基准 CNY {total_capital:,.0f})")
    assert abs(total_weight - 1.0) < 0.001, f"权重异常: {total_weight}"
    assert abs(total_amount - total_capital) < 1.0, f"金额异常: {total_amount}"

    with open(PLAN_FILE, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    print(f"\nOK 已更新: {PLAN_FILE}")


if __name__ == "__main__":
    main()
