# -*- coding: utf-8 -*-
"""v7.5 衍生品子包 — 基础 + v5.9增强"""

# v7.5原生
try:
    from .futures_scan import FuturesOptionsScanner
except ImportError:
    FuturesOptionsScanner = None

# v5.9增强
try:
    from .greeks import GreeksCalculator
except ImportError:
    GreeksCalculator = None
try:
    from .futures_scan import ArbitrageSignal, FuturesQuote, FuturesTermStructure, OptionsSnapshot
except ImportError:
    FuturesQuote = None
    FuturesTermStructure = None
    ArbitrageSignal = None
    OptionsSnapshot = None
