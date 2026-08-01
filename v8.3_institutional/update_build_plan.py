# -*- coding: utf-8 -*-
"""批量更新500万建仓计划，补全28标的"""
import json
from pathlib import Path

BASE = Path(r"e:\各种PY程序\28-终极量化交易系统7.1")
PLAN_FILE = BASE / "500万建仓计划_20260706.json"

with open(PLAN_FILE, "r", encoding="utf-8") as f:
    plan = json.load(f)

new_symbols = [
    {
        "key": "sh688981",
        "code": "688981",
        "name": "中芯国际",
        "type": "个股",
        "risk": "高",
        "style": "科技",
        "weight": 0.02,
        "est_price": 142.93,
        "lots": 100,
        "reason": "半导体制造龙头",
        "target_amount": 142934.0,
        "total_shares": 1000,
        "actual_amount": 142934.0,
        "stop_loss": -0.10,
        "phase1_shares": 1000,
        "phase1_amount": 142934.0,
    },
    {
        "key": "sh603019",
        "code": "603019",
        "name": "中科曙光",
        "type": "个股",
        "risk": "高",
        "style": "科技",
        "weight": 0.02,
        "est_price": 94.42,
        "lots": 100,
        "reason": "AI算力服务器龙头",
        "target_amount": 94419.0,
        "total_shares": 1000,
        "actual_amount": 94419.0,
        "stop_loss": -0.12,
        "phase1_shares": 1000,
        "phase1_amount": 94419.0,
    },
    {
        "key": "sh600219",
        "code": "600219",
        "name": "南山铝业",
        "type": "个股",
        "risk": "中",
        "style": "制造",
        "weight": 0.02,
        "est_price": 4.19,
        "lots": 100,
        "reason": "铝材+航空+汽车轻量化",
        "target_amount": 149690.0,
        "total_shares": 35700,
        "actual_amount": 149690.0,
        "stop_loss": -0.10,
        "phase1_shares": 35700,
        "phase1_amount": 149690.0,
    },
    {
        "key": "sh600019",
        "code": "600019",
        "name": "宝钢股份",
        "type": "个股",
        "risk": "中",
        "style": "制造",
        "weight": 0.02,
        "est_price": 5.61,
        "lots": 100,
        "reason": "钢铁龙头+汽车板高端化",
        "target_amount": 149760.0,
        "total_shares": 26700,
        "actual_amount": 149760.0,
        "stop_loss": -0.08,
        "phase1_shares": 26700,
        "phase1_amount": 149760.0,
    },
    {
        "key": "sz000792",
        "code": "000792",
        "name": "盐湖股份",
        "type": "个股",
        "risk": "高",
        "style": "资源",
        "weight": 0.02,
        "est_price": 29.41,
        "lots": 100,
        "reason": "钾肥+锂资源双驱动",
        "target_amount": 149991.0,
        "total_shares": 5100,
        "actual_amount": 149991.0,
        "stop_loss": -0.12,
        "phase1_shares": 5100,
        "phase1_amount": 149991.0,
    },
    {
        "key": "sh601318",
        "code": "601318",
        "name": "中国平安",
        "type": "个股",
        "risk": "中",
        "style": "金融",
        "weight": 0.02,
        "est_price": 48.96,
        "lots": 100,
        "reason": "保险+金融科技综合集团",
        "target_amount": 97922.0,
        "total_shares": 2000,
        "actual_amount": 97922.0,
        "stop_loss": -0.08,
        "phase1_shares": 2000,
        "phase1_amount": 97922.0,
    },
    {
        "key": "sz000858",
        "code": "000858",
        "name": "五粮液",
        "type": "个股",
        "risk": "中",
        "style": "消费",
        "weight": 0.02,
        "est_price": 73.21,
        "lots": 100,
        "reason": "白酒龙头+品牌护城河",
        "target_amount": 146420.0,
        "total_shares": 2000,
        "actual_amount": 146420.0,
        "stop_loss": -0.10,
        "phase1_shares": 2000,
        "phase1_amount": 146420.0,
    },
]

for sym in new_symbols:
    key = sym["key"]
    plan["target_portfolio"][key] = {
        "code": sym["code"],
        "name": sym["name"],
        "type": sym["type"],
        "risk": sym["risk"],
        "style": sym["style"],
        "weight": sym["weight"],
        "est_price": sym["est_price"],
        "lots": sym["lots"],
        "reason": sym["reason"],
        "target_amount": sym["target_amount"],
        "total_shares": sym["total_shares"],
        "actual_amount": sym["actual_amount"],
    }
    plan["position_plan"][key] = {
        "code": sym["code"],
        "name": sym["name"],
        "type": sym["type"],
        "risk": sym["risk"],
        "style": sym["style"],
        "target_weight": sym["weight"],
        "target_amount": sym["target_amount"],
        "est_price": sym["est_price"],
        "total_shares": sym["total_shares"],
        "actual_amount": sym["actual_amount"],
        "reason": sym["reason"],
        "stop_loss": sym["stop_loss"],
        "phases": [
            {
                "phase": 1,
                "name": "第一阶段-底仓建立",
                "start": "2026-07-06",
                "capital_ratio": 0.225,
                "target_amount": sym["phase1_amount"],
                "shares": sym["phase1_shares"],
                "actual_amount": sym["phase1_amount"],
                "cumulative_ratio": 0.225,
                "code": key,
            },
            {
                "phase": 2,
                "name": "第二阶段-配置完善",
                "start": "2026-07-20",
                "capital_ratio": 0.2,
                "target_amount": 0.0,
                "shares": 0,
                "actual_amount": 0.0,
                "cumulative_ratio": 0.2,
                "code": key,
            },
            {
                "phase": 3,
                "name": "第三阶段-防御补充",
                "start": "2026-08-10",
                "capital_ratio": 0.15,
                "target_amount": 0.0,
                "shares": 0,
                "actual_amount": 0.0,
                "cumulative_ratio": 0.15,
                "code": key,
            },
            {
                "phase": 4,
                "name": "第四阶段-最终调整",
                "start": "2026-09-01",
                "capital_ratio": 0.1,
                "target_amount": 0.0,
                "shares": 0,
                "actual_amount": 0.0,
                "cumulative_ratio": 0.1,
                "code": key,
            },
        ],
    }

# 更新 phase_summary
for phase in plan["phase_summary"]:
    phase["asset_count"] = 28
    phase["assets"].extend(
        [
            {"code": sym["key"], "name": sym["name"], "shares": sym["phase1_shares"], "amount": sym["phase1_amount"]}
            for sym in new_symbols
        ]
    )

plan["metadata"]["target_count"] = 28

with open(PLAN_FILE, "w", encoding="utf-8") as f:
    json.dump(plan, f, ensure_ascii=False, indent=4)

print("[OK] 500万建仓计划已更新为28标的")
