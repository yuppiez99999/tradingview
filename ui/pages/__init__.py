# -*- coding: utf-8 -*-
"""Streamlit UI 14 页面模块 — 终极量化交易系统 8.4 (T5.6).

任务: T5.6
责任层: L7 归因 + 前端

页面清单:
    01_dashboard.py   — 概览
    02_trade_plan.py  — 交易计划
    03_positions.py   — 持仓
    04_risk.py        — 风险
    05_attribution.py — 归因面板
    06_brinson.py     — Brinson 归因
    07_barra.py       — Barra 因子归因
    08_tca.py         — TCA 执行归因
    09_shadow.py      — Shadow 账户
    10_backtest.py    — 回测
    11_macro.py       — 宏观数据
    12_sector.py      — 行业轮动
    13_logs.py        — 系统日志
    14_config.py      — 配置
"""
from __future__ import annotations

# 页面注册表 (page_key -> page_title)
PAGE_REGISTRY = {
    "dashboard":   "概览",
    "trade_plan":  "交易计划",
    "positions":   "持仓",
    "risk":        "风险",
    "attribution": "归因面板",
    "brinson":     "Brinson 归因",
    "barra":       "Barra 因子归因",
    "tca":         "TCA 执行归因",
    "shadow":      "Shadow 账户",
    "backtest":    "回测",
    "macro":       "宏观数据",
    "sector":      "行业轮动",
    "logs":        "系统日志",
    "config":      "配置",
}

__all__ = ["PAGE_REGISTRY"]
