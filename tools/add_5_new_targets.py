# -*- coding: utf-8 -*-
"""
添加5个新标的到500万建仓计划 — 同时重平衡23标的权重
新增: 山推股份/美的集团/藏格矿业/山金国际/科伦药业
权重调整: 现有18标的按0.82系数缩放, 5新标的共占18%
"""
import json
from pathlib import Path
from datetime import datetime

PLAN_FILE = Path(r"e:\各种PY程序\28-终极量化交易系统8.4\500万建仓计划_20260706.json")
STOCK_CAPITAL = 3_000_000  # 股票部分300万

# 5 新标的定义 (权重合计 0.18)
NEW_TARGETS = {
    "sz000680": {
        "code": "000680", "name": "山推股份", "type": "个股",
        "risk": "中", "style": "制造", "weight": 0.04,
        "est_price": 7.50, "lots": 100,
        "reason": "补十五五科技自立自强+高端装备制造; 工程机械龙头; 康波繁荣期基建受益",
        "stop_loss": -0.10,
    },
    "sz000333": {
        "code": "000333", "name": "美的集团", "type": "个股",
        "risk": "中", "style": "制造", "weight": 0.04,
        "est_price": 75.00, "lots": 100,
        "reason": "补十五五科技自立自强(智能家电+工业自动化); 康波繁荣期消费升级; 全球化龙头",
        "stop_loss": -0.10,
    },
    "sz000408": {
        "code": "000408", "name": "藏格矿业", "type": "个股",
        "risk": "中高", "style": "资源", "weight": 0.05,
        "est_price": 35.00, "lots": 100,
        "reason": "补十五五新能源与双碳+资源安全; 康波繁荣期大宗商品主升浪; 锂+钾双资源",
        "stop_loss": -0.12,
    },
    "sz000975": {
        "code": "000975", "name": "山金国际", "type": "个股",
        "risk": "中", "style": "资源", "weight": 0.03,
        "est_price": 15.00, "lots": 100,
        "reason": "补十五五资源安全(黄金); 康波繁荣期黄金抗通胀; 货币信用对冲",
        "stop_loss": -0.10,
    },
    "sz002422": {
        "code": "002422", "name": "科伦药业", "type": "个股",
        "risk": "中", "style": "医药", "weight": 0.02,
        "est_price": 28.00, "lots": 100,
        "reason": "补十五五医药健康方向; 大输液+原料药+创新药三轨; 防御性配置",
        "stop_loss": -0.10,
    },
}

# 现有18标的权重缩放系数 (1.0 - 0.18 = 0.82)
SCALE_FACTOR = 0.82


def main():
    """主函数 - 读取计划, 缩放现有权重, 添加新标的, 写回"""
    with open(PLAN_FILE, "r", encoding="utf-8") as f:
        plan = json.load(f)

    # 1. 更新 metadata
    plan["metadata"]["target_count"] = 23
    plan["metadata"]["note"] = (
        plan["metadata"]["note"] +
        " | 2026-07-09 新增5标的(山推/美的/藏格/山金/科伦)来自盘前综合报告十五五对标; "
        "23标的重平衡: 现有18标的×0.82 + 5新标的=18%"
    )
    plan["metadata"]["rebalanced_at"] = datetime.now().isoformat()

    # 2. 缩放现有 target_portfolio 权重
    print("=== 现有18标的权重缩放 ===")
    for code, info in plan["target_portfolio"].items():
        old_w = info["weight"]
        new_w = round(old_w * SCALE_FACTOR, 6)
        info["weight"] = new_w
        # 重算 target_amount 和 total_shares
        info["target_amount"] = round(STOCK_CAPITAL * new_w, 2)
        # total_shares 按 lots 取整
        if info["est_price"] > 0:
            raw_shares = info["target_amount"] / info["est_price"]
            info["total_shares"] = int(raw_shares // info["lots"]) * info["lots"]
            info["actual_amount"] = round(info["total_shares"] * info["est_price"], 2)
        print(f"  {code} {info['name']}: {old_w:.4%} -> {new_w:.4%}")

    # 3. 添加5个新标的到 target_portfolio
    print("\n=== 新增5个标的 ===")
    for code, info in NEW_TARGETS.items():
        target_amount = round(STOCK_CAPITAL * info["weight"], 2)
        raw_shares = target_amount / info["est_price"]
        total_shares = int(raw_shares // info["lots"]) * info["lots"]
        actual_amount = round(total_shares * info["est_price"], 2)
        plan["target_portfolio"][code] = {
            "code": info["code"],
            "name": info["name"],
            "type": info["type"],
            "risk": info["risk"],
            "style": info["style"],
            "weight": info["weight"],
            "est_price": info["est_price"],
            "lots": info["lots"],
            "reason": info["reason"],
            "target_amount": target_amount,
            "total_shares": total_shares,
            "actual_amount": actual_amount,
        }
        print(f"  {code} {info['name']}: {info['weight']:.4%} | ¥{target_amount} | {total_shares}股")

    # 4. 缩放现有 position_plan 权重
    print("\n=== 缩放 position_plan 现有标的 ===")
    for code, pos in plan["position_plan"].items():
        old_w = pos["target_weight"]
        new_w = round(old_w * SCALE_FACTOR, 6)
        pos["target_weight"] = new_w
        pos["target_amount"] = round(STOCK_CAPITAL * new_w, 2)
        # 更新各阶段资金
        for phase in pos.get("phases", []):
            phase["target_amount"]
            new_phase_amount = round(new_w * STOCK_CAPITAL * phase["capital_ratio"], 2)
            phase["target_amount"] = new_phase_amount
            # shares 保持不变 (避免重新计算, 后续执行时按当日资金动态分配)
        print(f"  {code} {pos['name']}: {old_w:.4%} -> {new_w:.4%}")

    # 5. 添加5个新标的到 position_plan (4阶段)
    print("\n=== 新增 position_plan 5个标的 ===")
    PHASE_TEMPLATES = [
        {"phase": 1, "name": "第一阶段-底仓建立", "start": "2026-07-10", "capital_ratio": 0.35},
        {"phase": 2, "name": "第二阶段-配置完善", "start": "2026-07-20", "capital_ratio": 0.30},
        {"phase": 3, "name": "第三阶段-防御补充", "start": "2026-08-10", "capital_ratio": 0.20},
        {"phase": 4, "name": "第四阶段-最终调整", "start": "2026-09-01", "capital_ratio": 0.15},
    ]
    for code, info in NEW_TARGETS.items():
        target_amount = round(STOCK_CAPITAL * info["weight"], 2)
        raw_shares = target_amount / info["est_price"]
        total_shares = int(raw_shares // info["lots"]) * info["lots"]

        phases = []
        for tmpl in PHASE_TEMPLATES:
            phase_amount = round(target_amount * tmpl["capital_ratio"], 2)
            phase_shares = int(total_shares * tmpl["capital_ratio"] // info["lots"]) * info["lots"]
            phases.append({
                "phase": tmpl["phase"],
                "name": tmpl["name"],
                "start": tmpl["start"],
                "capital_ratio": tmpl["capital_ratio"],
                "target_amount": phase_amount,
                "shares": phase_shares,
                "actual_amount": phase_shares * info["est_price"],
                "cumulative_ratio": round(sum(p["capital_ratio"] for p in PHASE_TEMPLATES[:tmpl["phase"]]), 2),
                "code": code,
            })

        plan["position_plan"][code] = {
            "code": info["code"],
            "name": info["name"],
            "target_weight": info["weight"],
            "target_amount": target_amount,
            "est_price": info["est_price"],
            "total_shares": total_shares,
            "actual_amount": total_shares * info["est_price"],
            "reason": info["reason"],
            "stop_loss": info["stop_loss"],
            "phases": phases,
        }
        print(f"  {code} {info['name']}: {info['weight']:.4%} | 4阶段已生成")

    # 6. 更新 phase_summary 的 asset_count
    for phase in plan.get("phase_summary", []):
        phase["asset_count"] = 23

    # 7. 验证总权重
    total_weight = sum(v["weight"] for v in plan["target_portfolio"].values())
    print("\n=== 权重验证 ===")
    print(f"  23标的总权重: {total_weight:.6f} ({total_weight*100:.4f}%)")
    assert abs(total_weight - 1.0) < 0.001, f"权重不等于1.0: {total_weight}"

    # 8. 写回文件
    with open(PLAN_FILE, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    print(f"\n✓ 已更新: {PLAN_FILE}")
    print("  23标的清单:")
    for code, info in plan["target_portfolio"].items():
        print(f"    {code} {info['name']:>10}  权重 {info['weight']:.4%}  ¥{info['target_amount']:>10}")


if __name__ == "__main__":
    main()
