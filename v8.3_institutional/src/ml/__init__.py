# -*- coding: utf-8 -*-
"""v7.5 ML子包 — 原生 + v5.9增强"""

# v7.5原生
try:
    from .autolearn_trainer import AutoLearnTrainer
except ImportError:
    AutoLearnTrainer = None
try:
    from .ensemble_trainer import EnsembleTrainer
except ImportError:
    EnsembleTrainer = None
try:
    from .ml_optimizer import MLOptimizer
except ImportError:
    MLOptimizer = None

# v5.9增强
from .__enhanced__ import *  # noqa: F403
