"""L4 执行层 — 订单路由/对冲/再平衡/TCA.

模块整合 8.4 — ARCHITECTURE §2.1

已完成的迁移任务:
  - T3.4: tca_pre_trade_estimator.py 接入执行前预估
  - T3.5: tca_post_trade_attribution.py 接入执行后归因
  - T3.6: 根目录执行模块迁移 (2026-07-27 完成)
    * automated_execution_system.py (2203 行)
    * daily_build_and_hedge.py (1017 行)
    * rebalance_execution_orders.py (211 行)

后续待迁移 (Phase 4+):
  - execution_router / smart_order_router / hedge_execution_engine / qmt_broker
"""
