# -*- coding: utf-8 -*-
"""v7.5 增强对冲子包 — 从v5.9迁移"""
from .hedge_engine_v59 import HedgeEngine, HedgeSignalStrength, HedgeType, HedgeRecommendation, PortfolioRisk
from .hedge_rebalance_v59 import HedgeRebalanceIntegrator
try: from .multi_layer_hedge import MultiLayerHedgeManager
except ImportError: MultiLayerHedgeManager = None
try: from .smart_trigger import SmartHedgeTrigger
except ImportError: SmartHedgeTrigger = None
try: from .tail_risk import TailRiskHedge
except ImportError: TailRiskHedge = None
try: from .vol_hedge import VolatilityHedge
except ImportError: VolatilityHedge = None
try: from .enhanced_delta import EnhancedDeltaHedge
except ImportError: EnhancedDeltaHedge = None
