# -*- coding: utf-8 -*-
"""v7.5 宏观周期子包 — 康波/十五五/社保ETF"""

try:
    from .kondratiev import KondratievCycleAnalyzer, KondratievPhase
except ImportError:
    KondratievCycleAnalyzer = None
    KondratievPhase = None
try:
    from .five_year_plan import FifteenFivePlanAnalyzer
except ImportError:
    FifteenFivePlanAnalyzer = None
try:
    from .social_security_etf import NationalTeamSignalDetector, SocialSecurityETFTracker
except ImportError:
    SocialSecurityETFTracker = None
    NationalTeamSignalDetector = None
