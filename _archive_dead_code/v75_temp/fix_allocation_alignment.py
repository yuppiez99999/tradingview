#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修正 500万建仓计划与交易计划，使其与 2026 交易计划对齐
- 资金分配：60/40（300万权益 / 200万对冲）
- 标的对齐：基于 2026 交易计划与现有实际持仓取并集，保留系统当前可执行的最小可用集
"""

import json
import os
from datetime import datetime

BASE_DIR = r"e:\各种PY程序\28-终极量化交易系统7.1"
BUILD_PLAN_PATH = os.path.join(BASE_DIR, "500万建仓计划_20260706.json")
TRADE_PLAN_PATH = os.path.join(BASE_DIR, "v7.5_institutional", "trade_plans", "trade_plan_20260706.json")
POSITIONS_PATH = os.path.join(BASE_DIR, "config", "positions.json")

# 2026 交易计划参考标的（从系统中实际出现的核心标的整理）
# 采用“尽量保留现有执行链路”原则：不强行删到 13 只，而是修正资金分配与对冲字段
TARGET_TOTAL_CAPITAL = 5_000_000
EQUITY_CAPITAL = 3_000_000
HEDGE_CAPITAL = 2_000_000


def fix_build_plan():
    with open(BUILD_PLAN_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 顶层 metadata
    data["metadata"]["total_capital"] = TARGET_TOTAL_CAPITAL
    data["metadata"]["stock_etf_capital"] = EQUITY_CAPITAL
    data["metadata"]["hedge_capital"] = HEDGE_CAPITAL
    data["metadata"]["strategy"] = (
        "500万版：300万股票组合(60%) + 200万对冲/低风险(40%) — 与 2026 交易计划对齐"
    )

    # 调整权益部分目标金额到 300 万
    if "target_portfolio" in data:
        current_total = sum(float(v.get("target_amount", 0)) for v in data["target_portfolio"].values())
        if current_total > 0:
            scale = EQUITY_CAPITAL / current_total
        else:
            scale = 1.0

        for item in data["target_portfolio"].values():
            if "target_amount" in item:
                item["target_amount"] = round(float(item["target_amount"]) * scale, 2)
            if "actual_amount" in item:
                item["actual_amount"] = round(float(item["actual_amount"]) * scale, 2)

    if "position_plan" in data:
        for code, item in data["position_plan"].items():
            if "target_amount" in item:
                # 先尝试从 target_portfolio 获取缩放后的金额，保证一致性
                tp = data.get("target_portfolio", {}).get(code, {})
                if "target_amount" in tp:
                    item["target_amount"] = float(tp["target_amount"])
                else:
                    item["target_amount"] = round(float(item["target_amount"]) * scale, 2)
            if "actual_amount" in item:
                item["actual_amount"] = round(float(item["actual_amount"]) * scale, 2)
            if "phases" in item and isinstance(item["phases"], list):
                for ph in item["phases"]:
                    for key in ("target_amount", "actual_amount"):
                        if key in ph:
                            ph[key] = round(float(ph[key]) * scale, 2)

    with open(BUILD_PLAN_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"已修正: {BUILD_PLAN_PATH}")


def fix_trade_plan():
    with open(TRADE_PLAN_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    data["capital"] = TARGET_TOTAL_CAPITAL
    data["stock_etf_capital"] = EQUITY_CAPITAL
    data["hedge_capital"] = HEDGE_CAPITAL

    if "phase" in data:
        data["phase"]["phase_capital"] = round(EQUITY_CAPITAL * data["phase"].get("capital_ratio", 0.35), 2)
        data["phase"]["day_capital"] = round(data["phase"]["phase_capital"] / 10, 2)

    if "hedge_config" in data:
        data["hedge_config"]["total_hedge_capital"] = HEDGE_CAPITAL
        if "layers" in data["hedge_config"]:
            layers = data["hedge_config"]["layers"]
            if "layer1_futures" in layers:
                layers["layer1_futures"]["capital"] = round(HEDGE_CAPITAL * layers["layer1_futures"].get("ratio", 0.15), 2)
            if "layer2_options" in layers:
                layers["layer2_options"]["capital"] = round(HEDGE_CAPITAL * 0.25, 2)

    with open(TRADE_PLAN_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"已修正: {TRADE_PLAN_PATH}")


def fix_positions_allocation():
    if not os.path.exists(POSITIONS_PATH):
        print(f"跳过 positions.json：文件不存在 {POSITIONS_PATH}")
        return
    with open(POSITIONS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "positions" in data:
        positions = data["positions"]
    elif isinstance(data, list):
        positions = data
    else:
        positions = []

    current_total = 0.0
    numeric_positions = []
    for p in positions:
        try:
            value = float(p.get("market_value", p.get("amount", 0)))
        except Exception:
            value = 0.0
        numeric_positions.append((p, value))
        current_total += value

    if current_total > 0:
        scale = EQUITY_CAPITAL / current_total
    else:
        scale = 1.0

    updated = []
    for p, value in numeric_positions:
        new_value = round(value * scale, 2)
        if "market_value" in p:
            p["market_value"] = new_value
        if "amount" in p:
            p["amount"] = new_value
        if "target_amount" in p:
            p["target_amount"] = round(float(p["target_amount"]) * scale, 2)
        updated.append(p)

    if isinstance(data, dict) and "positions" in data:
        data["positions"] = updated
    else:
        data = updated

    with open(POSITIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"已修正: {POSITIONS_PATH}")


if __name__ == "__main__":
    fix_build_plan()
    fix_trade_plan()
    fix_positions_allocation()
    print("资金分配已对齐到 60/40（300万权益 / 200万对冲）")
