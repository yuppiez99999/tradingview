"""
信号源包 — 多源信号融合引擎的信号源适配器集合

每个信号源适配器把外部数据/模型输出转为 SignalResult,
供 SignalFusionEngine.register_source() 注册后参与加权融合。

可用信号源:
- sentiment: 舆情情感信号源 (OpenBiliClaw / MediaCrawler 采集 + NewsSentimentEngine 打分)
- news_intel: 新闻智能信号源 (财经新闻 + LLM 深度解读, TradingAgents 启发)
- finnhunter: FinnewsHunter 事件驱动 alpha 信号源 (新闻事件类型 → alpha 强度, FinnewsHunter 启发)
"""
from __future__ import annotations

try:
    from .sentiment_signal_source import SentimentSignalSource
except ImportError:
    pass

try:
    from .news_intelligence_signal_source import NewsIntelligenceSignalSource
except ImportError:
    pass

try:
    from .finnhunter_signal_source import FinnewsHunterSignalSource
except ImportError:
    pass

__all__ = ["SentimentSignalSource", "NewsIntelligenceSignalSource", "FinnewsHunterSignalSource"]
