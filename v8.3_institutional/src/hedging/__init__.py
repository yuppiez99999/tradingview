# -*- coding: utf-8 -*-
"""v7.5 对冲子包 — 基础 + v5.9增强"""

from .beta_hedger import BetaHedger
from .vol_hedger import VolHedger
from .correlation_hedger import CorrelationHedger
from .hedge_coordinator import HedgeCoordinator

# v5.9增强对冲
try:
    from .hedge_engine_v59 import HedgeEngine, PortfolioRisk, HedgeRecommendation
except ImportError:
    HedgeEngine = None
    PortfolioRisk = None
    HedgeRecommendation = None
try:
    from .hedge_rebalance_v59 import HedgeRebalanceIntegrator
except ImportError:
    HedgeRebalanceIntegrator = None
try:
    from .multi_layer_hedge import MultiLayerHedgeManager
except ImportError:
    MultiLayerHedgeManager = None
try:
    from .smart_trigger import SmartHedgeTrigger
except ImportError:
    SmartHedgeTrigger = None
try:
    from .tail_risk import TailRiskHedge
except ImportError:
    TailRiskHedge = None
try:
    from .vol_hedge import VolatilityHedge
except ImportError:
    VolatilityHedge = None
try:
    from .enhanced_delta import EnhancedDeltaHedge
except ImportError:
    EnhancedDeltaHedge = None

__all__ = [
    "BetaHedger",
    "CorrelationHedger",
    "EnhancedDeltaHedge",
    "HedgeCoordinator",
    "HedgeEngine",
    "HedgeRebalanceIntegrator",
    "HedgeRecommendation",
    "MultiLayerHedgeManager",
    "PortfolioRisk",
    "SmartHedgeTrigger",
    "TailRiskHedge",
    "VolHedger",
    "VolatilityHedge",
]
