# -*- coding: utf-8 -*-
"""src/risk __init__ — v7.6风控子包 (含v5.9+v7.6增强)"""

from .risk_budgeter import RiskBudgeter
from .risk_manager import RiskManager
from .circuit_breaker import CircuitBreaker
from .stress_tester import StressTester
from .pm_limits import PMLimitsMatrix, LimitStatus, LimitLevel, create_default_limits
from .deep_stress import DeepStressTester, ShockScenario, DEEP_SHOCK_SCENARIOS

# v5.9增强兼容
try:
    from .circuit_breaker import CircuitBreakerRegistry, CircuitState, CircuitStats
    SlippageCircuitBreaker = CircuitBreaker  # v5.9别名
except ImportError:
    CircuitBreakerRegistry = None
    CircuitState = None
    CircuitStats = None
    SlippageCircuitBreaker = None

# v5.9风险控制增强（仅存在文件的模块）
try: from .risk_controls_v59 import RiskControls, RiskControlLevel
except ImportError: RiskControls = None; RiskControlLevel = None
try: from .stress_test import StressTestEngine
except ImportError: StressTestEngine = None
try: from .concentration import ConcentrationRiskMonitor
except ImportError: ConcentrationRiskMonitor = None
try: from .correlation_monitor import CorrelationMonitor
except ImportError: CorrelationMonitor = None
try: from .psi_monitor import PSIMonitor
except ImportError: PSIMonitor = None
# dynamic_risk_budget 源文件不存在

__all__ = [
    'RiskBudgeter', 'RiskManager', 'CircuitBreaker', 'StressTester',
    'SlippageCircuitBreaker', 'CircuitBreakerRegistry', 'CircuitState', 'CircuitStats',
    'RiskControls', 'RiskControlLevel', 'StressTestEngine', 'ConcentrationRiskMonitor',
    'CorrelationMonitor', 'DynamicRiskBudget', 'PSIMonitor',
    'PMLimitsMatrix', 'LimitStatus', 'LimitLevel', 'create_default_limits',
    'DeepStressTester', 'ShockScenario', 'DEEP_SHOCK_SCENARIOS',
]
