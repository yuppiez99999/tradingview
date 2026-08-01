"""LightGBM 增强训练器 — 模块化拆分 (B3.5)

本包将原 `lgb_enhanced_trainer.py` (2739 行) 按职责拆分为 7 个聚焦模块:

    data_loader.py          — 真实 OHLCV 数据加载 (Wind MCP > iFinD MCP > 新浪 HTTP)
    news_sentiment.py       — 新闻情绪因子 (Wind MCP 优先, iFinD 回退)
    feature_engineering.py  — 扩展特征工程 (均值回归/Regime/行业/资金/跨市场)
    metrics.py              — 评估指标 + 时间序列交叉验证
    persistence.py          — 模型持久化 (save/load/retrain 判定)
    trainer.py              — 训练器核心 (单标的 + V9 regime-specific)
    report_generator.py     — 三方对比报告生成

主入口 `lgb_enhanced_trainer.py` 保留为 thin coordinator,
仅负责 CLI 解析、配置和流程编排。
"""

# 关键导出 (供外部调用方保持向后兼容)
from .data_loader import fetch_all_real_ohlcv, load_real_ohlcv
from .feature_engineering import (
    add_capital_flow_features,
    add_cross_market_features,
    add_industry_relative_strength_features,
    add_mean_reversion_features,
    add_regime_aware_features,
)
from .metrics import (
    ic_score as _ic_score,
)
from .metrics import (
    r2_score as _r2_score,
)
from .metrics import (
    select_features_by_importance,
    time_series_cv_evaluate,
)
from .metrics import (
    signal_sharpe as _signal_sharpe,
)
from .news_sentiment import add_sentiment_features, compute_news_sentiment_factors
from .persistence import load_model_meta, save_model, should_retrain
from .report_generator import generate_comparison_report
from .trainer import (
    compute_regime_series,
    run_enhanced_training,
    train_symbol_enhanced,
    train_symbol_regime_specific,
)

__all__ = [
    # 数据层
    "load_real_ohlcv",
    "fetch_all_real_ohlcv",
    # 情绪因子
    "compute_news_sentiment_factors",
    "add_sentiment_features",
    # 特征工程
    "add_mean_reversion_features",
    "add_regime_aware_features",
    "add_industry_relative_strength_features",
    "add_capital_flow_features",
    "add_cross_market_features",
    # 评估
    "time_series_cv_evaluate",
    "select_features_by_importance",
    # 持久化
    "save_model",
    "load_model_meta",
    "should_retrain",
    # 训练
    "train_symbol_enhanced",
    "train_symbol_regime_specific",
    "compute_regime_series",
    "run_enhanced_training",
    # 报告
    "generate_comparison_report",
]
