"""src/risk __init__ — v7.6风控子包 (含v5.9+v7.6增强)"""

from .circuit_breaker import CircuitBreaker
from .deep_stress import DEEP_SHOCK_SCENARIOS, DeepStressTester, ShockScenario
from .pm_limits import LimitLevel, LimitStatus, PMLimitsMatrix, create_default_limits
from .risk_budgeter import RiskBudgeter
from .risk_manager import RiskManager
from .stress_tester import StressTester

# v8.4 P0-7: 统一风险驾驶舱 (整合KillSwitch+CircuitBreaker+Drawdown+VaR回测)
try:
    from .unified_risk_cockpit import (
        DrawdownController,
        RiskLevel,
        RiskSnapshot,
        UnifiedRiskCockpit,
        VaRBacktester,
        VaRModel,
    )
except ImportError:
    UnifiedRiskCockpit = None
    VaRBacktester = None
    VaRModel = None
    DrawdownController = None
    RiskSnapshot = None
    RiskLevel = None

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
try:
    from .risk_controls_v59 import RiskControlLevel, RiskControls
except ImportError:
    RiskControls = None
    RiskControlLevel = None
try:
    from .stress_test import StressTestEngine
except ImportError:
    StressTestEngine = None
try:
    from .concentration import ConcentrationRiskMonitor
except ImportError:
    ConcentrationRiskMonitor = None
try:
    from .correlation_monitor import CorrelationMonitor
except ImportError:
    CorrelationMonitor = None
try:
    from .psi_monitor import PSIMonitor
except ImportError:
    PSIMonitor = None

__all__ = [
    "DEEP_SHOCK_SCENARIOS",
    "CircuitBreaker",
    "CircuitBreakerRegistry",
    "CircuitState",
    "CircuitStats",
    "ConcentrationRiskMonitor",
    "CorrelationMonitor",
    "DeepStressTester",
    "DrawdownController",
    "LimitLevel",
    "LimitStatus",
    "PMLimitsMatrix",
    "PSIMonitor",
    "RiskBudgeter",
    "RiskControlLevel",
    "RiskControls",
    "RiskLevel",
    "RiskManager",
    "RiskSnapshot",
    "ShockScenario",
    "SlippageCircuitBreaker",
    "StressTestEngine",
    "StressTester",
    # v8.4 P0-7: 统一风险驾驶舱
    "UnifiedRiskCockpit",
    "VaRBacktester",
    "VaRModel",
    "create_default_limits",
]
