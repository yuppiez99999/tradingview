"""L3 进化层 — 自我进化框架核心组件.

模块整合 8.4 — ARCHITECTURE_自我进化框架 §10.3 (v2.0 合并版)
灵感来源: AIDE² (Weco AI) — 双层循环架构 (外层策略进化 / 内层策略执行)

定位:
    与 utils/alpha/ 分层, 专司"自我进化"能力:
      - 进化记忆 (Memory): 全量审计留痕
      - 进化守卫 (Guard): 五道防线, 所有提案必过
      - 智能修复引擎 (AutoFixEngine): P0 自检升级为"检测+修复"
      - 实时反馈闭环 (FeedbackLoop): 交易结果驱动因子权重自适应
      - 自动因子工厂 (AutoFactorFactory): 因子自动发现/验证/部署/淘汰
      - 进化编排器 (Orchestrator): 三层 (L1/L2/L3) 路由中枢

当前阶段: T1.1 占位 (仅创建目录结构, 空 re-export)
后续任务:
  - T1.2: memory.py (进化记忆) — P0, 所有组件的审计基础
  - T1.3: guard.py (进化守卫五道防线) — P0, 依赖 Memory
  - T1.4: auto_fix_engine.py (智能修复引擎) — P1, 依赖 Memory
  - T1.5: P0 自检集成 auto_fix 参数 — P1, 依赖 AutoFixEngine
  - T2.1: feedback_loop.py (实时反馈闭环) — P1, 依赖 Memory + Guard
  - T3.1: orchestrator.py (进化编排器) — P0, 依赖 Memory + Guard + Evaluator
  - T3.3: auto_factor_factory.py (自动因子工厂) — P2, 依赖 Memory + Guard + Orchestrator

硬约束 (ARCHITECTURE §8.2):
  - HC-1: Feature Flag 默认 False, 双签启用
  - HC-2: 所有进化动作 100% 审计留痕 (写入 Memory)
  - HC-3: 所有进化动作可一键回滚
  - HC-4: L3 进化需人工审批, 不可自动执行
  - HC-5: 配置走 ConfigManager 4 级优先级

向后兼容:
  - 本包为新增, 不影响现有 utils/alpha/ 路径
  - 现有 evolution_orchestrator.py 保留在 utils/alpha/ (T3.1 完成后迁移)
"""

from __future__ import annotations

# Re-export API (按需显式导入, 避免循环依赖)
# T1.2: from utils.evolution.memory import EvolutionMemory, MemoryRecord
# T1.3: from utils.evolution.guard import EvolutionGuard, GuardDecision, GuardViolation
# T1.4: from utils.evolution.auto_fix_engine import AutoFixEngine, FixResult, FixAction
# T3.1: from utils.evolution.orchestrator import EvolutionOrchestrator as NewEvolutionOrchestrator
# T3.3: from utils.evolution.auto_factor_factory import AutoFactorFactory

# T1.2 已完成: 进化记忆
from utils.evolution.memory import EvolutionMemory, MemoryRecord

# T1.3 已完成: 进化守卫
from utils.evolution.guard import EvolutionGuard, GuardDecision, EvolutionProposal

# T1.4 已完成: 智能修复引擎
from utils.evolution.auto_fix_engine import AutoFixEngine, FixResult

# T2.1 已完成: 实时反馈闭环
from utils.evolution.feedback_loop import FeedbackLoop, WeightUpdate

# T2.2 已完成: P&L 归因适配器
from utils.evolution.pnl_attribution_adapter import (
    PnLAttributionAdapter,
    AttributionConversionResult,
    convert_to_feedback_loop_format,
    from_factor_attribution_result,
    from_attribution_result,
    from_report_file,
)

# T2.3 已完成: EOD FeedbackLoop 集成
from utils.evolution.eod_feedback_integration import (
    run_feedback_loop,
    run_feedback_loop_graceful,
    load_current_weights,
    FeedbackLoopResult,
)

# T3.1 已完成: V2 编排器
from utils.evolution.orchestrator import CycleResult, EvolutionOrchestratorV2

# T3.3 已完成: 自动因子工厂
from utils.evolution.auto_factor_factory import (
    AutoFactorFactory,
    CandidateFactor,
    ValidatedFactor,
    DeployedFactor,
    RetireSuggestion,
    FactoryPipelineReport,
)

# T3.4 已完成: 策略自动生成器
from utils.evolution.strategy_generator import (
    StrategyGenerator,
    StrategyTemplate,
    StrategyInstance,
    GenerationReport,
)

__version__ = "0.7.0"

__all__: list[str] = [
    # T1.2 Memory
    "EvolutionMemory",
    "MemoryRecord",
    # T1.3 Guard
    "EvolutionGuard",
    "GuardDecision",
    "EvolutionProposal",
    # T1.4 AutoFixEngine
    "AutoFixEngine",
    "FixResult",
    # T2.1 FeedbackLoop
    "FeedbackLoop",
    "WeightUpdate",
    # T2.2 PnL Attribution Adapter
    "PnLAttributionAdapter",
    "AttributionConversionResult",
    "convert_to_feedback_loop_format",
    "from_factor_attribution_result",
    "from_attribution_result",
    "from_report_file",
    # T2.3 EOD FeedbackLoop 集成
    "run_feedback_loop",
    "run_feedback_loop_graceful",
    "load_current_weights",
    "FeedbackLoopResult",
    # T3.1 Orchestrator V2
    "EvolutionOrchestratorV2",
    "CycleResult",
    # T3.3 AutoFactorFactory
    "AutoFactorFactory",
    "CandidateFactor",
    "ValidatedFactor",
    "DeployedFactor",
    "RetireSuggestion",
    "FactoryPipelineReport",
    # T3.4 StrategyGenerator
    "StrategyGenerator",
    "StrategyTemplate",
    "StrategyInstance",
    "GenerationReport",
]
