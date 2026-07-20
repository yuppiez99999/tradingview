# -*- coding: utf-8 -*-
"""
v9.0 期权策略执行模块 — 顶级对冲基金三大策略落地

策略:
    1. covered_call_overlay（备兑增强）
    2. risk_reversal_collar（双反向不对称组合 + 熊市价差）
    3. vega_event_driven（波动率套利与事件驱动）

注意:
    本模块仅生成可执行计划与 fills，不替代券商真实下单。
    实盘接入需对接期权柜台/模拟盘。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional


class OptionsRunner:
    def __init__(self,
                 trade_date: str,
                 hedge_capital: float = 1_000_000,
                 margin_usage_max: float = 600_000,
                 liquidity_buffer_min: float = 400_000) -> None:
        self.trade_date = trade_date
        self.hedge_capital = hedge_capital
        self.margin_usage_max = margin_usage_max
        self.liquidity_buffer_min = liquidity_buffer_min

    def run_modules(self,
                    modules: List[Dict[str, Any]],
                    market_data: Optional[Dict[str, Any]] = None,
                    trigger_date: Optional[str] = None,
                    event_calendar: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """按模块顺序执行期权策略"""
        market_data = market_data or {}
        trigger_date = trigger_date or self.trade_date
        event_calendar = event_calendar or []

        fills: List[Dict[str, Any]] = []
        for module in modules:
            name = module.get("name")
            runner = getattr(self, f"_run_{name}", None)
            if not runner:
                continue
            try:
                module_fills = runner(module, market_data, trigger_date, event_calendar)
                fills.extend(module_fills)
            except Exception as exc:
                fills.append({
                    "module": name,
                    "status": "FAIL",
                    "error": str(exc),
                    "trade_date": self.trade_date,
                })
        return fills

    def _run_covered_call_overlay(self,
                                  module: Dict[str, Any],
                                  market_data: Dict[str, Any],
                                  trigger_date: str,
                                  event_calendar: List[str]) -> List[Dict[str, Any]]:
        """备兑增强：按现货担保卖出虚值认购期权"""
        fills: List[Dict[str, Any]] = []
        for underlying in module.get("underlyings", []):
            fills.append({
                "module": module.get("name"),
                "alias": module.get("alias"),
                "strategy": "SELL_CALL",
                "underlying": underlying.get("code"),
                "underlying_name": underlying.get("name"),
                "direction": "SELL_CALL",
                "position_type": "covered_call",
                "allocation_amount": underlying.get("allocation_amount", 0),
                "strike_rule": underlying.get("strike_rule"),
                "expiry_rule": underlying.get("expiry_rule"),
                "status": "PENDING_ROLL",
                "trigger_date": trigger_date,
                "note": "每月初滚动卖出次月虚值Call",
            })
        return fills

    def _run_risk_reversal_collar(self,
                                  module: Dict[str, Any],
                                  market_data: Dict[str, Any],
                                  trigger_date: str,
                                  event_calendar: List[str]) -> List[Dict[str, Any]]:
        """双反向不对称组合：买入深虚值 Put + 熊市价差"""
        fills: List[Dict[str, Any]] = []
        for underlying in module.get("underlyings", []):
            fills.append({
                "module": module.get("name"),
                "alias": module.get("alias"),
                "strategy": "BUY_PUT",
                "underlying": underlying.get("code"),
                "underlying_name": underlying.get("name"),
                "direction": "BUY_PUT",
                "target_contracts": underlying.get("target_contracts", 0),
                "strike": underlying.get("strike"),
                "premium_budget": underlying.get("premium_budget", 0),
                "status": "PENDING_TRIGGER",
                "trigger_conditions": module.get("trigger_conditions", []),
                "trigger_date": trigger_date,
                "note": "触发条件满足后买入尾部保护",
            })

        spread = module.get("bear_put_spread") or {}
        if spread:
            fills.append({
                "module": module.get("name"),
                "alias": module.get("alias"),
                "strategy": "BEAR_PUT_SPREAD",
                "instrument": spread.get("instrument"),
                "structure": spread.get("structure"),
                "capital": spread.get("capital", 0),
                "status": "PENDING_TRIGGER",
                "trigger_conditions": module.get("trigger_conditions", []),
                "trigger_date": trigger_date,
                "note": "市场暴跌时指数级Gamma升值",
            })
        return fills

    def _run_vega_event_driven(self,
                               module: Dict[str, Any],
                               market_data: Dict[str, Any],
                               trigger_date: str,
                               event_calendar: List[str]) -> List[Dict[str, Any]]:
        """波动率套利与事件驱动：IV极低做多Straddle，IV极高做空Strangle"""
        fills: List[Dict[str, Any]] = []
        for underlying in module.get("underlyings", []):
            fills.append({
                "module": module.get("name"),
                "alias": module.get("alias"),
                "strategy": underlying.get("strategy"),
                "underlying": underlying.get("code"),
                "underlying_name": underlying.get("name"),
                "condition": underlying.get("condition"),
                "action": underlying.get("action"),
                "status": "PENDING_EVENT_WINDOW",
                "event_calendar": event_calendar,
                "pre_event_window": module.get("execution", {}).get("pre_event_window"),
                "post_event_exit": module.get("execution", {}).get("post_event_exit"),
                "max_loss_per_trade": module.get("execution", {}).get("max_loss_per_trade"),
                "trigger_date": trigger_date,
                "note": "政策会议前后捕捉Vol Crush或Gamma爆发",
            })
        return fills
