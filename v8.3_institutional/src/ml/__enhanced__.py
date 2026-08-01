"""v7.5 增强ML子包 — 从v5.9迁移"""

try:
    from .ml_predictor_v59 import MLPredictor, MLSignal
except ImportError:
    MLPredictor = None
    MLSignal = None
try:
    from .enhanced_trainer import EnhancedTrainer
except ImportError:
    EnhancedTrainer = None
try:
    from .optuna_trainer import OptunaTrainer
except ImportError:
    OptunaTrainer = None
try:
    from .mlflow_tracker import MLflowTracker
except ImportError:
    MLflowTracker = None
try:
    from .significance import MLSignificance
except ImportError:
    MLSignificance = None
try:
    from .labeling import LabelEngine
except ImportError:
    LabelEngine = None
