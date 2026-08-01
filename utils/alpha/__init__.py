"""L3 Alpha 层 — 因子/信号/ML/LLM/决策理论.

模块整合 8.4 — ARCHITECTURE §2.1
当前阶段：T4.2 已完成, 准备进入 T4.3 (整合 managers.py 组合优化/大宗/ETF)
已完成：
  - T2.1: llm_router.py（多模型路由器, 4 provider fallback 链, 54 单测 PASS）
  - T2.2: decision_theories.py（四大决策理论引擎 + 融合, 84.72% 覆盖率, 54 单测 PASS）
  - T2.3: multi_factor_signal.py（IC 加权融合 + 反向信号处理, 92.68% 覆盖率, 52 单测 PASS）
  - T4.1: fast_backtest.py（ML 回测验证引擎, 65 单测 PASS, 2.23s）
  - T4.2: ml_enhanced_selector.py（numpy 逻辑回归 + joblib 持久化, 45 单测 PASS, 94.89% 覆盖率）
后续任务：
  - T2.4: 启动 Shadow 准入流程（14 天观察期, P0 阻塞 Phase 3）
  - T4.3: 整合 managers.py（组合优化/大宗/ETF）
  - T4.4: 整合宏观与行业轮动模块
  - 后续迁移: factor_library / factor_evaluator / gtja191_factors / signal_fusion
"""

from __future__ import annotations

# Re-export API (按需显式导入, 避免循环依赖)
# T2.1: from utils.alpha.llm_router import LLMRouter, chat, chat_deep, test_connection, list_providers, reload
# T2.2: from utils.alpha.decision_theories import TheoryFusionEngine, SorosReflexivityEngine, DalioEconomicMachine, FirstPrinciplesAnalyzer, BuffettMungerFramework
# T2.3: from utils.alpha.multi_factor_signal import MultiFactorSignal, combine_factors, detect_inverted_factors, FactorICMetrics, CombinationResult
