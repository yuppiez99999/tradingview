"""v7.5 NLP/情感子包 — 金融情感/事件因子/情感聚合"""

try:
    from .sentiment import FinSentimentAnalyzer
except ImportError:
    FinSentimentAnalyzer = None
try:
    from .event_factor import EventDrivenFactor
except ImportError:
    EventDrivenFactor = None
try:
    from .sentiment_hub import SentimentHub
except ImportError:
    SentimentHub = None
