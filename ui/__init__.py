# -*- coding: utf-8 -*-
"""Streamlit UI 14 页面应用 — 模块整合 8.4 (T5.6).

任务: T5.6
责任层: L7 归因 + 前端
依赖: T5.3 (归因面板)

设计目标:
    1. 完整 14 页面多页面应用 (基于 st.navigation + st.Page)
    2. 接入 T5.3 归因面板 JSON
    3. 盘中实时刷新 (1 分钟级)
    4. 生产环境鉴权 (Feature Flag 控制)

目录结构:
    ui/
    ├── __init__.py           — 本文件, 包级 API
    ├── app.py                — 主入口, st.navigation 多页面路由
    ├── auth.py               — 鉴权模块 (HC-1 Feature Flag 透传)
    ├── data_loader.py        — 统一数据加载器 (JSON/YAML/JSONL 读取 + 缓存)
    ├── layout.py             — 共享布局组件 (页头/侧边栏/状态徽标)
    └── pages/                — 14 个页面模块
        ├── 01_dashboard.py   — 概览
        ├── 02_trade_plan.py  — 交易计划
        ├── 03_positions.py   — 持仓
        ├── 04_risk.py        — 风险
        ├── 05_attribution.py — 归因
        ├── 06_brinson.py     — Brinson 归因
        ├── 07_barra.py       — Barra 因子归因
        ├── 08_tca.py         — TCA 执行归因
        ├── 09_shadow.py      — Shadow 账户
        ├── 10_backtest.py    — 回测
        ├── 11_macro.py       — 宏观数据
        ├── 12_sector.py      — 行业轮动
        ├── 13_logs.py        — 系统日志
        └── 14_config.py      — 配置

硬约束:
    - HC-1: USE_STREAMLIT_UI Feature Flag 默认 False, 关闭时 app.py 显示提示
    - HC-5: 配置走 ConfigManager 4 级优先级
    - HC-7: 不修改 v8.3_institutional/monitor.py (保留作为旧版骨架)
"""
from __future__ import annotations

# 版本号
__version__ = "1.0.0"

# Feature Flag 名称
FLAG_NAME = "USE_STREAMLIT_UI"

__all__ = ["FLAG_NAME", "__version__"]
