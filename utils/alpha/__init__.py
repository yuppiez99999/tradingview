"""L3 Alpha 层 — 因子/信号/ML/LLM/决策理论.

模块整合 8.4 — ARCHITECTURE §2.1
当前阶段：T2.1 进行中
已完成：
  - T2.1: llm_router.py（多模型路由器, 4 provider fallback 链）
后续任务：
  - T2.2: decision_theories.py（从 v8.3_institutional 迁移）
  - T2.3: multi_factor_signal.py（多因子融合）
  - T4.1: fast_backtest.py（ML 回测验证）
  - T4.2: ml_enhanced_selector.py（真实 Transformer）
  - 后续迁移: factor_library / factor_evaluator / gtja191_factors / signal_fusion
"""
from __future__ import annotations

# Re-export LLMRouter API (T2.1)
# 注意: 仅在显式导入时才加载, 避免循环依赖
# from utils.alpha.llm_router import LLMRouter, chat, chat_deep, test_connection, list_providers, reload
