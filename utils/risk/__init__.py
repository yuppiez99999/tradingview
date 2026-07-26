"""L5 风控层 — 中央风控总线 + 多模块适配器.

模块整合 8.4 — ARCHITECTURE §2.1 / §3
当前阶段：T1.2 占位
后续任务：
  - T3.1: risk_event.py + risk_bus.py（事件驱动总线）
  - T3.2: kill_switch 适配（保留同步路径，HC-2）
  - T3.3: circuit_breaker / risk_guard / var_monitor / overnight_gap 适配
  - 后续迁移: risk_attribution / barra_decomposer / risk_budget / stress_test

硬约束（HC-2）：KillSwitch 必须保留同步直调路径，延迟 <1ms，不走总线
"""
