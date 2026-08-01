"""v7.5 增强对冲子包 — 从v5.9迁移"""

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
