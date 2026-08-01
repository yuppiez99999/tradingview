"""模型持久化 (B3.5: 从 lgb_enhanced_trainer.py 抽取)

本模块集中以下职责:
  - save_model: 保存 LGB 模型 + 元数据 (JSON)
  - load_model_meta: 加载模型元数据
  - should_retrain: 根据 retrain_interval_days 判定是否需要重训

路径由主模块通过 configure_paths 注入。
"""

from __future__ import annotations

import json
import logging
import pickle
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("lgb_enhanced")


# ============================================================
# 路径 (由主模块注入)
# ============================================================
BASE_DIR: Path = Path(__file__).resolve().parent.parent
MODELS_DIR: Path = BASE_DIR / "models" / "lgb_enhanced"


def configure_paths(base_dir: Path, models_dir: Path) -> None:
    """由主模块注入路径。"""
    global BASE_DIR, MODELS_DIR
    BASE_DIR = base_dir
    MODELS_DIR = models_dir


# ============================================================
# 模型持久化
# ============================================================
def save_model(symbol: str, result: dict[str, Any], config: dict[str, Any]) -> dict[str, str]:
    """保存 LGB 模型 + 元数据。

    Args:
        symbol: 标的代码
        result: 训练结果 (含 model, selected_features, metrics 等)
        config: 训练配置 (用于记录 lgb_params 等)

    Returns:
        {"model_path": str, "meta_path": str}
    """
    symbol_dir = MODELS_DIR / symbol
    # parents=True: 当 MODELS_DIR 本身不存在时也能创建 (如临时目录/测试场景)
    symbol_dir.mkdir(parents=True, exist_ok=True)

    model_path = symbol_dir / f"{symbol}_lgb_enhanced_model.pkl"
    meta_path = symbol_dir / f"{symbol}_meta.json"

    with open(model_path, "wb") as f:
        pickle.dump(result["model"], f)

    meta: dict[str, Any] = {
        "symbol": symbol,
        "saved_at": datetime.now().isoformat(),
        "model_type": "LightGBM_Enhanced_RealOHLCV_Sentiment",
        "data_source": "real_ohlcv_via_wind_ifind_sina",
        "n_samples": result["n_samples"],
        "n_features_before": result["n_features_before"],
        "n_features_after": result["n_features_after"],
        "selected_features": result["selected_features"],
        "train_period": result["train_period"],
        "test_period": result["test_period"],
        "best_iteration": result["best_iteration"],
        "adaptive_retrained": result.get("adaptive_retrained", False),
        "cv_before_selection": result["cv_before_selection"],
        "cv_after_selection": result["cv_after_selection"],
        "final_metrics": result["final_metrics"],
        "signal": result["signal"],
        "raw_prediction": result["raw_prediction"],
        "top_features": result["top_features"],
        "config": {
            "lgb_params": config["lgb_params"],
            "early_stopping_rounds": config["early_stopping_rounds"],
            "n_splits": config["n_splits"],
            "test_ratio": config["test_ratio"],
            "top_n_features": config["top_n_features"],
            "feature_selection_threshold": config["feature_selection_threshold"],
            "news_lookback_days": config["news_lookback_days"],
        },
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2, default=str)

    return {"model_path": str(model_path), "meta_path": str(meta_path)}


def load_model_meta(symbol: str) -> dict[str, Any] | None:
    """加载模型元数据。

    Args:
        symbol: 标的代码

    Returns:
        元数据字典, 未找到时返回 None
    """
    meta_path = MODELS_DIR / symbol / f"{symbol}_meta.json"
    if not meta_path.exists():
        return None
    with open(meta_path, encoding="utf-8") as f:
        return json.load(f)


def should_retrain(symbol: str, config: dict[str, Any]) -> bool:
    """根据 retrain_interval_days 判定是否需要重训。

    Args:
        symbol: 标的代码
        config: 训练配置 (含 retrain_interval_days)

    Returns:
        True 表示需要重训 (无元数据或已过期)
    """
    meta = load_model_meta(symbol)
    if meta is None:
        return True
    saved_at = datetime.fromisoformat(meta["saved_at"])
    age_days = (datetime.now() - saved_at).days
    return age_days >= config["retrain_interval_days"]
