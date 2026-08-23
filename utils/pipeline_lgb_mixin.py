"""
机构管道 LGB Walk-forward 训练 Mixin
====================================

从 institutional_pipeline_runner.py 抽取的 LGB 训练方法:
- 特征构建 (_build_lgb_feature_dict)
- Regime 序列计算 (_compute_regime_series_for_cutoff, _build_regime_series_safe)
- Walk-forward 训练 (_lgb_walkforward_train, _train_symbol_with_retry)
- 模型缓存 (_build_model_cache, _log_trained_symbol, _lgb_get_signal)
"""

from __future__ import annotations

import gc
import logging
import time
from typing import Any

import pandas as pd

logger = logging.getLogger("institutional_pipeline")

# === LGB 特性标志与导入 (与主模块一致, 同一 import 路径保证一致性) ===
try:
    from lgb_enhanced_trainer import LGB_ENHANCED_CONFIG, POSITION_SYMBOLS
    from lgb_trainer import (
        add_capital_flow_features,
        add_cross_market_features,
        add_cross_sectional_features,
        add_industry_relative_strength_features,
        add_mean_reversion_features,
        add_sentiment_features,
        add_technical_features,
        compute_regime_series,
        train_symbol_enhanced,
        train_symbol_regime_specific,
    )

    _HAS_LGB = True
except Exception as _lgb_import_err:  # noqa: BLE001
    _HAS_LGB = False
    _LGB_IMPORT_ERR = str(_lgb_import_err)
    POSITION_SYMBOLS = []
    logger.warning(f"LightGBM 导入失败, LGB Mixin 降级: {_lgb_import_err}")

# Walk-forward 训练配置（加速版：月度重训无需 2000 轮）
WALKFORWARD_LGB_CONFIG = (
    {
        **LGB_ENHANCED_CONFIG,
        "lgb_params": {
            **LGB_ENHANCED_CONFIG["lgb_params"],
            "n_estimators": 1000,
            "n_jobs": 1,
        },
        "early_stopping_rounds": 100,
        "news_lookback_days": 0,
        "adaptive_retrain_threshold": 5,
        "adaptive_retrain_lr": 0.001,
        "adaptive_retrain_n_estimators": 2000,
    }
    if _HAS_LGB
    else {}
)

# LGB 训练重试配置
_LGB_TRAIN_MAX_RETRIES = 2
_LGB_TRAIN_RETRY_DELAY = 1.0

# V9: Regime-Specific 训练开关
_V9_REGIME_SPECIFIC_ENABLED = True
_V9_MIN_SAMPLES_PER_REGIME = 100
_V9_REGIME_PROXY_SYMBOL = "510300"
_V9_REGIME_MA_PERIOD = 60
_V9_REGIME_SLOPE_WINDOW = 5


class LGBMixin:
    """LGB Walk-forward 训练 Mixin — 特征构建 + 训练 + 模型缓存。"""

    def _build_lgb_feature_dict(self) -> dict[str, pd.DataFrame]:
        """从 _historical_cache 构建完整特征字典（与训练管线一致）。

        流程:
            1. 从缓存提取 OHLCV（已截断到 cutoff）
            2. 逐标的添加技术因子
            3. 添加截面/行业/资金流向/跨市场/情绪因子
        Returns:
            {symbol: DataFrame[含全部特征列]}
        """
        if not _HAS_LGB:
            return {}

        # Step 1: 提取 OHLCV
        ohlcv_dict: dict[str, pd.DataFrame] = {}
        for symbol, df in self._historical_cache.items():
            if df is not None and not df.empty:
                required_cols = {"open", "high", "low", "close", "volume"}
                if required_cols.issubset(set(df.columns)):
                    ohlcv_dict[symbol] = df.copy()

        if not ohlcv_dict:
            logger.warning("[LGB-WF] 无可用 OHLCV 数据构建特征")
            return {}

        # Step 2: 技术因子（逐标的）
        featured_dict: dict[str, pd.DataFrame] = {}
        for symbol, df in ohlcv_dict.items():
            try:
                df_feat = add_technical_features(df)
                featured_dict[symbol] = df_feat
            except Exception as e:
                logger.debug("[LGB-WF] 技术因子失败 %s: %s", symbol, e)
                featured_dict[symbol] = df.copy()

        # Step 2.5: V6 均值回归特征 (提升震荡市Alpha信号质量)
        try:
            featured_dict = add_mean_reversion_features(featured_dict)
            logger.debug("[LGB-WF] 均值回归特征已添加 (9个特征/标的)")
        except Exception as e:
            logger.warning("[LGB-WF] 均值回归特征失败: %s", e)

        # Step 3: 截面 + 行业 + 资金流向 + 跨市场 + 情绪
        try:
            featured_dict = add_cross_sectional_features(featured_dict)
        except Exception as e:
            logger.debug("[LGB-WF] 截面因子失败: %s", e)
        try:
            featured_dict = add_industry_relative_strength_features(featured_dict)
        except Exception as e:
            logger.debug("[LGB-WF] 行业相对强度失败: %s", e)
        try:
            featured_dict = add_capital_flow_features(featured_dict)
        except Exception as e:
            logger.debug("[LGB-WF] 资金流向失败: %s", e)
        try:
            featured_dict = add_cross_market_features(featured_dict)
        except Exception as e:
            logger.debug("[LGB-WF] 跨市场失败: %s", e)
        try:
            featured_dict = add_sentiment_features(featured_dict, {})
        except Exception as e:
            logger.debug("[LGB-WF] 情绪因子失败: %s", e)

        return featured_dict

    def _compute_regime_series_for_cutoff(self) -> pd.Series | None:
        """V9: 计算截至 cutoff 的大盘 regime 序列 (bull/bear/choppy/rebound)

        用 _V9_REGIME_PROXY_SYMBOL (510300) 作为大盘代理,
        数据从 _historical_cache 获取 (已截断到 cutoff, 无前视偏差)。

        Returns:
            pd.Series[index=date, values="bull"/"bear"/"choppy"/"rebound"/"unknown"]
            若代理数据缺失则返回 None
        """
        if not _HAS_LGB:
            return None

        proxy_code = _V9_REGIME_PROXY_SYMBOL
        proxy_df = self._historical_cache.get(proxy_code)
        if proxy_df is None or proxy_df.empty:
            logger.warning("[V9-Regime] 大盘代理 %s 数据缺失, 无法计算 regime", proxy_code)
            return None

        try:
            regime_series = compute_regime_series(
                proxy_df,
                ma_period=_V9_REGIME_MA_PERIOD,
                slope_window=_V9_REGIME_SLOPE_WINDOW,
            )
            if regime_series is None or regime_series.empty:
                logger.warning("[V9-Regime] regime 序列计算返回空")
                return None

            n_valid = int((regime_series != "unknown").sum())
            logger.info(
                "[V9-Regime] regime 序列构建成功: %d 行 (有效 %d, 代理=%s)", len(regime_series), n_valid, proxy_code
            )
            return regime_series
        except Exception as e:
            logger.warning("[V9-Regime] regime 序列计算异常: %s", e)
            return None

    def _lgb_walkforward_train(self) -> dict[str, Any]:
        """Walk-forward 训练 LGB 模型（用截至 cutoff 的数据，无前视偏差）。

        V9: 当 _V9_REGIME_SPECIFIC_ENABLED=True 时, 每个标的训练 bull/non-bull 双模型,
            预测时按当前 regime 选择对应模型。否则回退 V6.2 单模型。

        Returns:
            {trained: N, failed: N, skipped: N, results: {...}}
        """
        empty_result = {"trained": 0, "failed": 0, "skipped": 0, "results": {}}
        if not _HAS_LGB:
            logger.warning("[LGB-WF] LGB 依赖不可用: %s", _LGB_IMPORT_ERR)
            return empty_result
        if self.ctx.mode != "backtest":
            return empty_result

        logger.info(
            "[LGB-WF] 开始 Walk-forward 训练 (as_of=%s, V9=%s)", self.ctx.report_date, _V9_REGIME_SPECIFIC_ENABLED
        )

        featured_dict = self._build_lgb_feature_dict()
        if not featured_dict:
            logger.warning("[LGB-WF] 特征构建失败，降级为动量信号")
            return empty_result

        regime_series = self._build_regime_series_safe()

        config = WALKFORWARD_LGB_CONFIG
        trained = 0
        failed = 0
        skipped = 0

        # 逐标的训练
        for code, _suffix, _stype, name, _style in POSITION_SYMBOLS:
            df = featured_dict.get(code)
            if df is None or len(df) < config.get("min_samples", 150):
                logger.debug(
                    "[LGB-WF] %s: 样本不足 (%d < %d)，跳过",
                    code,
                    len(df) if df is not None else 0,
                    config.get("min_samples", 150),
                )
                skipped += 1
                continue

            try:
                result = self._train_symbol_with_retry(code, df, config, regime_series)
                if result is None:
                    skipped += 1
                    continue

                self._lgb_models[code] = self._build_model_cache(result, regime_series)
                trained += 1
                self._log_trained_symbol(code, name, result, regime_series)
                # 释放当前标的的特征 DataFrame, 减少内存压力
                del df
                gc.collect()
            except Exception as e:
                failed += 1
                logger.warning("[LGB-WF] %s 训练失败 (重试 %d 次后): %s", code, _LGB_TRAIN_MAX_RETRIES, e)

        logger.info("[LGB-WF] 训练完成: trained=%d failed=%d skipped=%d", trained, failed, skipped)
        return {"trained": trained, "failed": failed, "skipped": skipped, "results": self._lgb_models}

    def _build_regime_series_safe(self) -> Any | None:
        """V9: 计算大盘 regime 序列 (用截至 cutoff 的 proxy 数据), 失败返回 None。"""
        if not _V9_REGIME_SPECIFIC_ENABLED:
            return None
        regime_series = self._compute_regime_series_for_cutoff()
        if regime_series is None:
            logger.warning("[V9-Regime] regime 序列构建失败, 降级为单模型训练")
            return None
        regime_counts = regime_series.value_counts().to_dict()
        logger.info("[V9-Regime] regime 分布: %s", regime_counts)
        return regime_series

    def _train_symbol_with_retry(
        self,
        code: str,
        df: pd.DataFrame,
        config: dict,
        regime_series: Any | None,
    ) -> dict[str, Any] | None:
        """训练单个标的 (带重试机制), 失败抛异常, SKIP 返回 None。"""
        result = None
        last_err = None
        for attempt in range(_LGB_TRAIN_MAX_RETRIES + 1):
            try:
                if _V9_REGIME_SPECIFIC_ENABLED and regime_series is not None:
                    result = train_symbol_regime_specific(
                        code, df, config, regime_series, min_samples_per_regime=_V9_MIN_SAMPLES_PER_REGIME,
                    )
                else:
                    result = train_symbol_enhanced(code, df, config)
                break
            except Exception as e:
                last_err = e
                if attempt < _LGB_TRAIN_MAX_RETRIES:
                    logger.warning(
                        "[LGB-WF] %s 第 %d 次训练失败: %s, 准备重试 (%d/%d)",
                        code, attempt + 1, e, attempt + 1, _LGB_TRAIN_MAX_RETRIES,
                    )
                    time.sleep(_LGB_TRAIN_RETRY_DELAY)
                    gc.collect()
                else:
                    raise

        if result is None:
            raise RuntimeError(f"训练返回 None: {last_err}")
        if result.get("status") != "OK":
            logger.debug("[LGB-WF] %s: 训练返回非 OK: %s", code, result.get("reason"))
            return None
        return result

    def _build_model_cache(self, result: dict[str, Any], regime_series: Any | None) -> dict[str, Any]:
        """构建模型缓存 (内存中, 不落盘)。

        V9: 额外缓存 models_by_regime / features_by_regime / selected_regime
        """
        model_cache = {
            "model": result["model"],
            "selected_features": result["selected_features"],
            "cv_after_selection": result["cv_after_selection"],
            "final_metrics": result["final_metrics"],
            "signal": result["signal"],
            "raw_prediction": result["raw_prediction"],
        }
        if _V9_REGIME_SPECIFIC_ENABLED and regime_series is not None:
            model_cache["v9_regime_specific"] = True
            model_cache["current_regime"] = result.get("current_regime", "unknown")
            model_cache["selected_regime"] = result.get("selected_regime", "full")
            model_cache["models_by_regime"] = result.get("models_by_regime", {})
            model_cache["features_by_regime"] = result.get("features_by_regime", {})
            model_cache["n_bull_samples"] = result.get("n_bull_samples", 0)
            model_cache["n_non_bull_samples"] = result.get("n_non_bull_samples", 0)
        return model_cache

    def _log_trained_symbol(
        self,
        code: str,
        name: str,
        result: dict[str, Any],
        regime_series: Any | None,
    ) -> None:
        """输出训练成功日志。"""
        cv_m = result["cv_after_selection"]
        v9_tag = ""
        if _V9_REGIME_SPECIFIC_ENABLED and regime_series is not None:
            v9_tag = f" [V9 regime={result.get('selected_regime', '?')}]"
        logger.info(
            "[LGB-WF] %s (%s): CV IC=%.4f±%.4f, final_ic=%.4f, signal=%.4f, features=%d%s",
            code,
            name,
            cv_m["mean_ic"],
            cv_m["std_ic"],
            result["final_metrics"]["ic"],
            result["signal"],
            result["n_features_after"],
            v9_tag,
        )

    def _lgb_get_signal(self, symbol: str) -> tuple:
        """获取指定标的的 LGB 信号。

        Returns:
            (signal_strength, confidence)
            - signal_strength: tanh(raw_pred * 100), 范围 [-1, 1]
            - confidence: min(1.0, abs(cv_ic) + 0.2), 基于 CV IC
        """
        model_info = self._lgb_models.get(symbol)
        if not model_info:
            return (0.0, 0.0)

        signal = float(model_info.get("signal", 0.0))
        cv_metrics = model_info.get("cv_after_selection", {})
        cv_ic = float(cv_metrics.get("mean_ic", 0.0))
        confidence = min(1.0, abs(cv_ic) + 0.2)
        return (signal, confidence)
