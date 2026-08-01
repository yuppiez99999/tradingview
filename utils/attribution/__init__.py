"""L7 归因层 — Brinson / Barra / Factor 三合一日级面板.

模块整合 8.4 — ARCHITECTURE §2.1 / §5
当前阶段：T5.3 已完成 (daily_panel.py), 后续可推进 T5.4 (拆分 quantitative_system.py)
已完成：
  - T1.2: 占位 (目录与 __init__.py)
  - T4.3: managers.py (Facade 整合 BlackLittermanOptimizer + CommodityManager + ETFRealTimeTracker, 883 行)
  - T5.1: brinson_attribution.py (Brinson-Fachler 三效应归因, 620 行, 覆盖率 95.96%)
  - T5.2: factor_attribution.py (Barra 10 风格因子 + 8 行业因子 PnL 拆分, 1257 行, 覆盖率 96.86%, 132 个单测全部通过)
  - T5.3: daily_panel.py (Facade 模式整合 Brinson + Factor + TCA 三合一, 92 个单测全部通过, 覆盖率 88.23%)
后续任务：
  - T5.4: 拆分 quantitative_system.py 为多个模块
  - 后续迁移: pnl_attribution_engine.py

对冲基金视角：每一行 PnL 必须可归因（Alpha / Execution / Risk）
"""
