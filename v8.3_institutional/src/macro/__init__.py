"""v7.5 宏观子包 — 康波/十五五/社保ETF + v5.9增强"""

# v7.5原生
try:
    from .macro_analyzer import MacroAnalyzer
except ImportError:
    MacroAnalyzer = None
try:
    from .regime_detector import RegimeDetector
except ImportError:
    RegimeDetector = None
try:
    from .black_litterman import BlackLittermanModel
except ImportError:
    BlackLittermanModel = None

# v5.9增强
from .__enhanced__ import *  # noqa: F403
