"""L6 调度层 — 日级报告生成器 (T5.4).

模块整合 8.4 — ARCHITECTURE §2.1 / §5
当前阶段：T5.4 已完成 (daily_report_generator.py)

设计原则 (对冲基金视角):
  1. daily_workflow.py 保持只读 (HC-1: V9 基线不可破坏)
  2. 新建独立模块, 提供 Feature Flag 透传 (USE_DAILY_REPORT_GENERATOR 默认 False)
  3. Facade 模式: 不修改 PnLAttributionEngine / BarraRiskDecomposer
  4. 纯函数 + 主类双层 API: 渲染函数可独立测试, 主类集成 Feature Flag

已完成:
  - T5.4: daily_report_generator.py (Facade 主类 + 6 个独立渲染函数)
    从 daily_workflow.phase_report (615 行) 抽取为独立模块
    不修改原文件, 保持 V9 基线完整

后续任务:
  - 双签启用 Feature Flag 后, daily_workflow.py 可选择性调用新模块
"""
