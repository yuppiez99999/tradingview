# -*- coding: utf-8 -*-
"""v7.6 信号子包 — 基础 + v5.9增强 + 信号半衰期管理"""

# v7.5原生
try:
    from .crowding_detector import SignalCrowdingDetector, CrowdingConfig
except ImportError:
    SignalCrowdingDetector = None
    CrowdingConfig = None
try:
    from .signal_fusion_v59 import SignalFusion
except ImportError:
    SignalFusion = None

# v5.9增强
try:
    from .signal_fusion_v59 import SignalFusionEngine
except ImportError:
    SignalFusionEngine = None
try:
    from .enhanced_fusion import EnhancedSignalFusion
except ImportError:
    EnhancedSignalFusion = None
try:
    from .independence import SignalIndependenceAnalyzer
except ImportError:
    SignalIndependenceAnalyzer = None
try:
    from .audit import SignalAuditor
except ImportError:
    SignalAuditor = None
try:
    from .rule_engine import RuleEngine
except ImportError:
    RuleEngine = None

# v7.6 信号半衰期管理
from .signal_half_life import SignalHalfLifeManager, SignalHalfLife, PRESET_HALF_LIVES

__all__ = [
    "PRESET_HALF_LIVES",
    "CrowdingConfig",
    "EnhancedSignalFusion",
    "RuleEngine",
    "SignalAuditor",
    "SignalCrowdingDetector",
    "SignalFusion",
    "SignalFusionEngine",
    "SignalHalfLife",
    "SignalHalfLifeManager",
    "SignalIndependenceAnalyzer",
]
