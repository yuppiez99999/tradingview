# -*- coding: utf-8 -*-
"""
ML 模型预测模块 - 集成训练好的量化模型
提供特征工程、模型加载、涨跌预测、信号生成功能

最佳模型: GradientBoosting (F1: 0.628, 准确率: 56.01%)
备选模型: XGBoost_tuned / LightGBM_tuned
低延迟模型: LogisticRegression (PCA版, 8维)
"""

import os
import json
import glob
import numpy as np
import pandas as pd
import joblib
from typing import Dict, List, Optional, Any


class MLFeatureEngineer:
    """ML 特征工程 - 与训练脚本一致的特征构建逻辑"""

    def __init__(self):
        self.feature_cols = []

    def build_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        构建增强版特征（42个特征，与训练脚本一致）

        Args:
            df: 包含 OHLCV 的K线数据 DataFrame

        Returns:
            包含所有特征的 DataFrame
        """
        data = df.copy()

        if "close" not in data.columns:
            raise ValueError("DataFrame 必须包含 'close' 列")

        data["close"] = pd.to_numeric(data["close"], errors="coerce")
        data = data.dropna(subset=["close"])

        # 价格特征
        data["returns"] = data["close"].pct_change()
        data["log_returns"] = np.log(data["close"] / data["close"].shift(1))
        data["abs_returns"] = data["returns"].abs()

        # 移动平均线
        data["ma_5"] = data["close"].rolling(5).mean()
        data["ma_10"] = data["close"].rolling(10).mean()
        data["ma_20"] = data["close"].rolling(20).mean()
        data["ma_60"] = data["close"].rolling(60).mean()
        data["ma_120"] = data["close"].rolling(120).mean()

        # 均线比率
        data["ma5_ma20"] = data["ma_5"] / data["ma_20"]
        data["ma20_ma60"] = data["ma_20"] / data["ma_60"]
        data["ma60_ma120"] = data["ma_60"] / data["ma_120"]

        # 波动率
        data["volatility_5"] = data["returns"].rolling(5).std()
        data["volatility_20"] = data["returns"].rolling(20).std()
        data["volatility_60"] = data["returns"].rolling(60).std()
        data["vol_ratio_5_20"] = data["volatility_5"] / data["volatility_20"]

        # RSI
        data["rsi"] = self._calculate_rsi(data["close"], 14)
        data["rsi_7"] = self._calculate_rsi(data["close"], 7)
        data["rsi_21"] = self._calculate_rsi(data["close"], 21)

        # MACD
        ema_12 = data["close"].ewm(span=12).mean()
        ema_26 = data["close"].ewm(span=26).mean()
        data["macd"] = ema_12 - ema_26
        signal = data["macd"].ewm(span=9).mean()
        data["macd_signal"] = signal
        data["macd_hist"] = data["macd"] - signal

        # 布林带
        data["boll_mid"] = data["ma_20"]
        data["boll_upper"] = data["ma_20"] + 2 * data["close"].rolling(20).std()
        data["boll_lower"] = data["ma_20"] - 2 * data["close"].rolling(20).std()
        data["boll_width"] = (data["boll_upper"] - data["boll_lower"]) / data["boll_mid"]
        data["boll_pct"] = (data["close"] - data["boll_lower"]) / (data["boll_upper"] - data["boll_lower"])

        # 成交量特征
        if "volume" in data.columns:
            data["volume"] = pd.to_numeric(data["volume"], errors="coerce")
            data["volume_ma_5"] = data["volume"].rolling(5).mean()
            data["volume_ma_20"] = data["volume"].rolling(20).mean()
            data["volume_ratio"] = data["volume"] / data["volume_ma_5"]
            data["volume_ma_ratio"] = data["volume_ma_5"] / data["volume_ma_20"]
            data["volume_change"] = data["volume"].pct_change()

        if "VOLUME" in data.columns:
            data["VOLUME"] = pd.to_numeric(data["VOLUME"], errors="coerce")
            if "volume" not in data.columns:
                data["volume"] = data["VOLUME"]
            data["volume_ma_5"] = data["VOLUME"].rolling(5).mean()
            data["volume_ratio"] = data["VOLUME"] / data["volume_ma_5"]

        # 资金流向特征 (VWAP)
        vol = data.get("volume", pd.Series(1, index=data.index))
        data["vwap"] = (data["close"] * vol).rolling(5).sum() / vol.rolling(5).sum()
        data["price_vwap_diff"] = data["close"] - data["vwap"]
        data["price_vwap_ratio"] = data["close"] / data["vwap"]

        # 动量特征
        data["momentum_5"] = data["returns"].rolling(5).sum()
        data["momentum_10"] = data["returns"].rolling(10).sum()
        data["momentum_20"] = data["returns"].rolling(20).sum()
        data["momentum_60"] = data["returns"].rolling(60).sum()

        # 趋势强度
        data["trend_5"] = (data["close"] - data["close"].shift(5)) / data["close"].shift(5)
        data["trend_20"] = (data["close"] - data["close"].shift(20)) / data["close"].shift(20)
        data["trend_60"] = (data["close"] - data["close"].shift(60)) / data["close"].shift(60)

        # 日内波动特征
        if "high" in data.columns and "low" in data.columns:
            data["high"] = pd.to_numeric(data["high"], errors="coerce")
            data["low"] = pd.to_numeric(data["low"], errors="coerce")
            data["range"] = data["high"] - data["low"]
            data["range_ratio"] = data["range"] / data["close"]
            data["upper_shadow"] = data["high"] - data[["open", "close"]].max(axis=1)
            data["lower_shadow"] = data[["open", "close"]].min(axis=1) - data["low"]

        if "open" in data.columns:
            data["open"] = pd.to_numeric(data["open"], errors="coerce")
            data["open_close_diff"] = data["close"] - data["open"]
            data["gap"] = data["open"] - data["close"].shift(1)

        # 特征交叉
        data["rsi_volatility"] = data["rsi"] * data["volatility_20"]
        data["momentum_volatility"] = data["momentum_20"] * data["volatility_20"]
        data["macd_rsi"] = data["macd"] * data["rsi"]
        data["boll_momentum"] = data["boll_width"] * data["momentum_20"]
        data["trend_rsi"] = data["trend_20"] * data["rsi"]

        return data

    def _calculate_rsi(self, prices: pd.Series, period: int = 14) -> pd.Series:
        """计算RSI指标"""
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def get_feature_vector(self, df: pd.DataFrame, feature_list: List[str]) -> np.ndarray:
        """
        获取最新一行的特征向量（用于单样本预测）

        Args:
            df: 包含所有特征的 DataFrame
            feature_list: 需要的特征列名列表

        Returns:
            特征向量 (1, n_features)
        """
        # 确保所有需要的特征列都存在
        available_features = [f for f in feature_list if f in df.columns]
        if len(available_features) < len(feature_list):
            missing = set(feature_list) - set(available_features)
            print(f"[WARN] 缺失特征: {missing}")

        # 取最后一行有效数据
        last_row = df[available_features].dropna().iloc[-1:]
        return last_row.values


class MLModelPredictor:
    """ML 模型预测器 - 加载训练好的模型并生成预测信号"""

    def __init__(self, model_dir: str = "models"):
        self.model_dir = model_dir
        self.models = {}
        self.metadata = {}
        self.feature_engineer = MLFeatureEngineer()
        self.feature_selector = None
        self.scaler = None
        self.pca = None
        self.selected_features = []
        self._loaded = False

    # 模型优先级偏好：LightGBM > XGBoost > GradientBoosting > ExtraTrees > LGBMClassifier > LogisticRegression
    MODEL_PREFERENCE = [
        "LightGBM_tuned",
        "LightGBM",
        "LGBMClassifier",
        "XGBoost_tuned",
        "XGBoost",
        "XGBClassifier",
        "GradientBoosting",
        "ExtraTrees",
        "LogisticRegression",
    ]

    def auto_discover(self) -> bool:
        """
        自动发现并加载最佳模型
        策略：扫描所有元数据，优先选择 LightGBM_tuned，
        若多份元数据都有同类模型则选准确率最高的

        Returns:
            是否成功加载
        """
        # 查找所有优化版元数据
        meta_pattern = os.path.join(self.model_dir, "training_metadata_optimized_*.json")
        meta_files = sorted(glob.glob(meta_pattern))

        if meta_files:
            return self._load_optimized_best(meta_files)

        # 回退到增强版
        meta_pattern = os.path.join(self.model_dir, "training_metadata_enhanced_*.json")
        meta_files = sorted(glob.glob(meta_pattern))
        if meta_files:
            return self._load_enhanced(meta_files[-1])  # 最新

        # 回退到基础版
        meta_pattern = os.path.join(self.model_dir, "training_metadata_*.json")
        meta_files = sorted(glob.glob(meta_pattern))
        if meta_files:
            return self._load_basic(meta_files[-1])

        print("[ML] 未找到训练元数据")
        return False

    def _load_optimized_best(self, meta_files: List[str]) -> bool:
        """
        从多份优化版元数据中选择最佳模型
        优先选择 LightGBM_tuned，其次按 accuracy 排序
        """
        candidates = []  # [(meta_path, model_name, accuracy, f1, auc), ...]

        for mp in meta_files:
            try:
                with open(mp, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                results = meta.get("results", {})
                # 检查所有模型
                for model_name, metrics in results.items():
                    acc = metrics.get("accuracy", 0)
                    f1_val = metrics.get("f1", 0)
                    auc_val = metrics.get("auc", 0)
                    candidates.append((mp, model_name, acc, f1_val, auc_val))
            except Exception:
                continue

        if not candidates:
            return self._load_optimized(meta_files[-1])

        # 按偏好排序：LightGBM_tuned 优先，再按 accuracy 排序
        def sort_key(item):
            _mp, model_name, acc, _f1_val, _auc_val = item
            # 偏好分：LightGBM_tuned +10, LightGBM +8, XGBoost_tuned +5
            pref = 0
            if "LightGBM_tuned" in model_name:
                pref = 10
            elif "LightGBM" in model_name:
                pref = 8
            elif "XGBoost_tuned" in model_name:
                pref = 5
            elif "XGBoost" in model_name:
                pref = 3
            elif "GradientBoosting" in model_name:
                pref = 1
            return (-pref, -acc)

        candidates.sort(key=sort_key)
        best_meta_path, best_model_name, best_acc, best_f1, best_auc = candidates[0]

        print(
            f"[ML] 从 {len(meta_files)} 份元数据中择优 → {best_model_name} "
            f"(Acc={best_acc:.2%}, F1={best_f1:.4f}, AUC={best_auc:.4f})"
        )

        return self._load_optimized(best_meta_path)

    def _load_optimized(self, meta_path: str) -> bool:
        """加载优化版模型（含特征选择）"""
        print(f"[ML] 加载优化版模型: {os.path.basename(meta_path)}")

        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)

            self.metadata = meta
            self.selected_features = meta.get("features", [])
            best_model_name = meta.get("best_model", "GradientBoosting")

            # 加载特征选择器（容错）
            ts = meta.get("timestamp", "")
            selector_path = os.path.join(self.model_dir, f"feature_selector_{ts}.pkl")
            if os.path.exists(selector_path):
                try:
                    self.feature_selector = joblib.load(selector_path)
                    print(f"[ML] 特征选择器已加载: {os.path.basename(selector_path)}")
                except Exception as e:
                    print(f"[ML] 特征选择器加载失败: {e}")

            # 加载最佳模型（容错）
            model_pattern = os.path.join(self.model_dir, f"{best_model_name}_*.pkl")
            model_files = sorted(glob.glob(model_pattern), reverse=True)

            if model_files:
                try:
                    self.models[best_model_name] = joblib.load(model_files[0])
                    print(f"[ML] 最佳模型已加载: {best_model_name}")
                    if "results" in meta and best_model_name in meta["results"]:
                        print(f"[ML]   准确率: {meta['results'][best_model_name]['accuracy']:.2%}")
                        print(f"[ML]   F1分数: {meta['results'][best_model_name]['f1']:.4f}")
                    self._loaded = True
                    return True
                except Exception as e:
                    print(f"[ML] 模型加载失败 ({best_model_name}): {e}")
                    print("[ML] 尝试加载其他可用模型...")
                    return self._try_load_other_models()

            print(f"[ML] 未找到模型文件: {best_model_name}")
            return self._try_load_other_models()
        except Exception as e:
            print(f"[ML] 加载优化版模型失败: {e}")
            return self._try_load_other_models()

    def _try_load_other_models(self) -> bool:
        """尝试加载其他可用模型作为降级方案"""
        for model_name in self.MODEL_PREFERENCE:
            model_pattern = os.path.join(self.model_dir, f"{model_name}_*.pkl")
            model_files = sorted(glob.glob(model_pattern), reverse=True)
            if model_files:
                try:
                    self.models[model_name] = joblib.load(model_files[0])
                    print(f"[ML] 降级加载成功: {model_name}")
                    self._loaded = True
                    self.metadata = {"best_model": model_name, "features": self.selected_features}
                    return True
                except Exception:
                    continue
        print("[ML] 所有模型均无法加载，ML预测功能不可用")
        return False

    def _load_enhanced(self, meta_path: str) -> bool:
        """加载增强版模型"""
        print(f"[ML] 加载增强版模型: {os.path.basename(meta_path)}")

        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)

            self.metadata = meta
            best_model_name = meta.get("best_model", "GradientBoosting")

            model_pattern = os.path.join(self.model_dir, f"{best_model_name}_*.pkl")
            model_files = sorted(glob.glob(model_pattern), reverse=True)

            if model_files:
                try:
                    self.models[best_model_name] = joblib.load(model_files[0])
                    self.selected_features = meta.get("feature_cols", [])
                    print(f"[ML] 最佳模型已加载: {best_model_name}")
                    self._loaded = True
                    return True
                except Exception as e:
                    print(f"[ML] 模型加载失败 ({best_model_name}): {e}")
                    return self._try_load_other_models()

            return self._try_load_other_models()
        except Exception as e:
            print(f"[ML] 加载增强版模型失败: {e}")
            return self._try_load_other_models()

    def _load_basic(self, meta_path: str) -> bool:
        """加载基础版模型"""
        print(f"[ML] 加载基础版模型: {os.path.basename(meta_path)}")

        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)

            self.metadata = meta
            best_model_name = meta.get("best_model", "ExtraTrees")

            model_pattern = os.path.join(self.model_dir, f"{best_model_name}_*.pkl")
            model_files = sorted(glob.glob(model_pattern), reverse=True)

            if model_files:
                try:
                    self.models[best_model_name] = joblib.load(model_files[0])
                    self.selected_features = meta.get("feature_cols", [])
                    print(f"[ML] 最佳模型已加载: {best_model_name}")
                    self._loaded = True
                    return True
                except Exception as e:
                    print(f"[ML] 模型加载失败 ({best_model_name}): {e}")
                    return self._try_load_other_models()

            return self._try_load_other_models()
        except Exception as e:
            print(f"[ML] 加载基础版模型失败: {e}")
            return self._try_load_other_models()

    def predict(self, kline_df: pd.DataFrame, model_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        对单只标的进行涨跌预测

        Args:
            kline_df: K线数据 DataFrame（需包含 close, 可选 open/high/low/volume）
            model_name: 指定模型名，None则使用最佳模型

        Returns:
            预测结果字典，包含:
            - prediction: 预测方向 (1=上涨, 0=下跌)
            - probability: 上涨概率
            - signal: 信号强度 (-1~1)
            - confidence: 置信度
        """
        if not self._loaded:
            if not self.auto_discover():
                return None

        if model_name is None:
            model_name = self.metadata.get("best_model", "GradientBoosting")

        if model_name not in self.models:
            print(f"[ML] 模型未加载: {model_name}")
            return None

        model = self.models[model_name]

        # 构建特征
        feat_df = self.feature_engineer.build_features(kline_df)

        # 确保需要的特征列存在
        available_features = [f for f in self.selected_features if f in feat_df.columns]
        if len(available_features) < len(self.selected_features) * 0.5:
            print(f"[ML] 特征不足: 需要 {len(self.selected_features)}, 可用 {len(available_features)}")
            return None

        # 去除NaN行
        feat_clean = feat_df[available_features].dropna()
        if len(feat_clean) == 0:
            print("[ML] 无有效特征数据")
            return None

        # 取最后一行做预测
        X = feat_clean.iloc[-1:].values

        # 预测
        try:
            pred = model.predict(X)[0]
            prob = model.predict_proba(X)[0]
            up_prob = prob[1] if len(prob) > 1 else prob[0]

            # 信号强度: 0~1 映射到 -1~1
            signal = (up_prob - 0.5) * 2

            # 置信度: 概率离 0.5 越远越置信
            confidence = abs(up_prob - 0.5) * 2

            return {
                "prediction": int(pred),
                "direction": "上涨" if pred == 1 else "下跌",
                "probability": float(up_prob),
                "signal": float(signal),
                "confidence": float(confidence),
                "model": model_name,
            }
        except Exception as e:
            print(f"[ML] 预测失败: {e}")
            return None

    def batch_predict(
        self, kline_dict: Dict[str, pd.DataFrame], model_name: Optional[str] = None, top_n: int = 10
    ) -> List[Dict[str, Any]]:
        """
        批量预测多只标的

        Args:
            kline_dict: {code: kline_df} 字典
            model_name: 指定模型
            top_n: 返回前N只最强信号

        Returns:
            按信号强度排序的预测结果列表
        """
        results = []

        for code, df in kline_dict.items():
            pred = self.predict(df, model_name)
            if pred:
                pred["code"] = code
                results.append(pred)

        # 按上涨概率排序
        results.sort(key=lambda x: x["probability"], reverse=True)

        return results[:top_n]

    def generate_trading_signals(self, kline_dict: Dict[str, pd.DataFrame], threshold: float = 0.55) -> Dict[str, Any]:
        """
        生成交易信号

        Args:
            kline_dict: {code: kline_df} 字典
            threshold: 买入信号阈值（上涨概率 > threshold）

        Returns:
            信号字典，包含 buy/sell/hold 列表
        """
        predictions = self.batch_predict(kline_dict, top_n=len(kline_dict))

        buy_signals = []
        sell_signals = []
        hold_signals = []

        for pred in predictions:
            code = pred["code"]
            prob = pred["probability"]

            if prob > threshold:
                buy_signals.append(
                    {
                        "code": code,
                        "probability": prob,
                        "confidence": pred["confidence"],
                        "signal_strength": pred["signal"],
                    }
                )
            elif prob < (1 - threshold):
                sell_signals.append(
                    {
                        "code": code,
                        "probability": prob,
                        "confidence": pred["confidence"],
                        "signal_strength": pred["signal"],
                    }
                )
            else:
                hold_signals.append(
                    {
                        "code": code,
                        "probability": prob,
                        "confidence": pred["confidence"],
                    }
                )

        return {
            "buy": buy_signals,
            "sell": sell_signals,
            "hold": hold_signals,
            "threshold": threshold,
            "total": len(predictions),
            "model": self.metadata.get("best_model", "unknown"),
            "model_accuracy": self.metadata.get("results", {})
            .get(self.metadata.get("best_model", ""), {})
            .get("accuracy", 0),
        }

    def get_model_info(self) -> Dict[str, Any]:
        """获取当前加载的模型信息 (P0-2增强: 含统计显著性)"""
        if not self._loaded:
            return {"loaded": False}

        best_model = self.metadata.get("best_model", "unknown")
        result = self.metadata.get("results", {}).get(best_model, {})

        info = {
            "loaded": True,
            "best_model": best_model,
            "accuracy": result.get("accuracy", 0),
            "f1": result.get("f1", 0),
            "auc": result.get("auc", 0),
            "feature_count": len(self.selected_features),
            "features": self.selected_features,
            "timestamp": self.metadata.get("timestamp", ""),
            "n_test": result.get("n_test", 0),
            "n_correct": result.get("n_correct", 0),
        }

        # P0-2: 如果元数据中有统计验证结果，直接返回
        if "statistical_validation" in result:
            info["statistical_validation"] = result["statistical_validation"]
        # 否则从 n_test/n_correct 估算基础统计量
        elif info["n_test"] > 0:
            try:
                try:
                    from scipy.stats import binomtest

                    n_test = info["n_test"]
                    n_correct = info["n_correct"]
                    acc = n_correct / n_test
                    se = np.sqrt(acc * (1 - acc) / n_test)
                    t_stat = (acc - 0.5) / se if se > 0 else 0
                    binom_p = binomtest(n_correct, n_test, p=0.5, alternative="greater").pvalue
                except ImportError:
                    from scipy.stats import binom_test as _binom_test

                    n_test = info["n_test"]
                    n_correct = info["n_correct"]
                    acc = n_correct / n_test
                    se = np.sqrt(acc * (1 - acc) / n_test)
                    t_stat = (acc - 0.5) / se if se > 0 else 0
                    binom_p = _binom_test(n_correct, n_test, p=0.5, alternative="greater")
                info["statistical_validation"] = {
                    "accuracy": acc,
                    "n_test": n_test,
                    "n_correct": n_correct,
                    "t_statistic": float(t_stat),
                    "binomial_test": {"p_value": float(binom_p)},
                    "is_significant": binom_p < 0.05 or t_stat > 2.0,
                    "verdict": "PASS (显著)" if (binom_p < 0.05 or t_stat > 2.0) else "WEAK (边界)",
                    "note": "从 n_test/n_correct 估算，建议运行 --train-enhanced 获取完整BI/置换检验",
                }
            except ImportError:
                pass

        return info


class StackingPredictor:
    """Stacking 集成预测器 v1.0

    加载多个模型（XGBoost + LightGBM + GradientBoosting + ExtraTrees），
    使用概率平均 + 模型加权的方式融合多模型预测结果。

    相比单一模型：
    - 降低过拟合风险
    - 提高预测稳定性
    - 模型间互补（不同模型擅长不同模式）
    """

    def __init__(self, model_dir: str = "models", weight_method: str = "f1_weighted"):
        """
        Args:
            model_dir: 模型文件目录
            weight_method: 权重计算方式
                - 'f1_weighted': 按各模型历史 F1 分数加权
                - 'uniform': 等权平均
                - 'auc_weighted': 按 AUC 分数加权
        """
        self.model_dir = model_dir
        self.weight_method = weight_method
        self.models: Dict[str, Any] = {}
        self.model_weights: Dict[str, float] = {}
        self.feature_engineer = MLFeatureEngineer()
        self.selected_features: List[str] = []
        self.metadata: Dict[str, Any] = {}
        self._loaded = False

    def auto_discover_and_load(self) -> bool:
        """
        自动发现并加载所有可用的优化模型用于 Stacking。

        扫描 models/ 目录，加载所有 Optuna 优化版模型。
        如果 Optuna 版不存在，回退到 GridSearch 版。
        """
        # 优先加载 Optuna 优化版模型
        optuna_meta = sorted(glob.glob(os.path.join(self.model_dir, "training_metadata_optuna_*.json")))
        if not optuna_meta:
            optuna_meta = sorted(glob.glob(os.path.join(self.model_dir, "training_metadata_optimized_*.json")))

        if not optuna_meta:
            print("[Stacking] 未找到优化版元数据")
            return False

        # 加载最新的元数据
        meta_path = optuna_meta[-1]
        print(f"[Stacking] 加载元数据: {os.path.basename(meta_path)}")
        with open(meta_path, "r", encoding="utf-8") as f:
            self.metadata = json.load(f)

        results = self.metadata.get("results", {})
        self.selected_features = self.metadata.get("features", [])

        # 加载每个子模型
        loaded_count = 0
        f1_scores = {}
        auc_scores = {}

        for model_name, metrics in results.items():
            model_path = metrics.get("model_path", "")
            if not model_path or not os.path.exists(model_path):
                # 尝试按模式查找
                pattern = os.path.join(self.model_dir, f"{model_name}_*.pkl")
                model_files = sorted(glob.glob(pattern), reverse=True)
                if model_files:
                    model_path = model_files[0]
                else:
                    continue

            try:
                model = joblib.load(model_path)
                self.models[model_name] = model
                f1_scores[model_name] = metrics.get("f1", metrics.get("train_f1", 0))
                auc_scores[model_name] = metrics.get("auc", metrics.get("train_auc", 0))
                loaded_count += 1
                print(f"[Stacking] ✅ {model_name} (F1={f1_scores[model_name]:.4f})")
            except Exception as e:
                print(f"[Stacking] ⚠️ {model_name} 加载失败: {e}")

        if loaded_count < 2:
            print(f"[Stacking] 模型不足 ({loaded_count}个)，Stacking 至少需要 2 个模型")
            return False

        # 计算权重
        if self.weight_method == "f1_weighted":
            total_f1 = sum(f1_scores.values())
            if total_f1 > 0:
                self.model_weights = {k: v / total_f1 for k, v in f1_scores.items()}
            else:
                self.model_weights = {k: 1.0 / loaded_count for k in f1_scores}
        elif self.weight_method == "auc_weighted":
            total_auc = sum(auc_scores.values())
            if total_auc > 0:
                self.model_weights = {k: v / total_auc for k, v in auc_scores.items()}
            else:
                self.model_weights = {k: 1.0 / loaded_count for k in auc_scores}
        else:
            self.model_weights = {k: 1.0 / loaded_count for k in f1_scores}

        self._loaded = True
        print(f"[Stacking] 已加载 {loaded_count} 个模型，权重方法: {self.weight_method}")
        return True

    def predict(self, kline_df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """
        Stacking 融合预测。

        所有子模型分别预测，按权重平均概率，最后输出融合结果。
        """
        if not self._loaded:
            if not self.auto_discover_and_load():
                # 回退到单一最佳模型
                print("[Stacking] 回退到单一模型预测")
                single = MLModelPredictor(model_dir=self.model_dir)
                if single.auto_discover():
                    return single.predict(kline_df)
                return None

        # 构建特征
        feat_df = self.feature_engineer.build_features(kline_df)

        # 找可用特征
        available_features = [f for f in self.selected_features if f in feat_df.columns]
        if not available_features:
            print("[Stacking] 无可用特征")
            return None

        feat_clean = feat_df[available_features].dropna()
        if len(feat_clean) == 0:
            return None

        X = feat_clean.iloc[-1:].values

        # 各模型独立预测
        weighted_prob = 0.0
        individual_probs = {}
        individual_preds = {}
        total_weight = sum(self.model_weights.values())

        for name, model in self.models.items():
            try:
                proba = model.predict_proba(X)[0]
                up_prob = proba[1] if len(proba) > 1 else proba[0]
                pred = model.predict(X)[0]
                w = self.model_weights.get(name, 0)
                weighted_prob += up_prob * w
                individual_probs[name] = float(up_prob)
                individual_preds[name] = int(pred)
            except Exception as e:
                print(f"[Stacking] {name} 预测失败: {e}")

        if total_weight == 0:
            return None

        # 归一化加权概率
        final_prob = weighted_prob / total_weight
        final_pred = 1 if final_prob > 0.5 else 0

        # 计算信号强度与置信度
        signal = (final_prob - 0.5) * 2
        # 模型间一致性越高，置信度越高
        pred_values = list(individual_preds.values())
        agreement = sum(1 for p in pred_values if p == final_pred) / len(pred_values)

        return {
            "prediction": final_pred,
            "direction": "上涨" if final_pred == 1 else "下跌",
            "probability": float(final_prob),
            "signal": float(signal),
            "confidence": float(abs(final_prob - 0.5) * 2 * agreement),
            "model": "Stacking",
            "individual_probs": individual_probs,
            "model_count": len(self.models),
            "agreement": float(agreement),
        }

    def batch_predict(self, kline_dict: Dict[str, pd.DataFrame], top_n: int = 20) -> List[Dict[str, Any]]:
        """批量 Stacking 预测"""
        results = []
        for code, df in kline_dict.items():
            pred = self.predict(df)
            if pred:
                pred["code"] = code
                results.append(pred)
        results.sort(key=lambda x: x["probability"], reverse=True)
        return results[:top_n]


def run_ml_signal_scan(
    codes: Optional[List[str]] = None, data_dir: str = "data/cache", model_dir: str = "models", threshold: float = 0.55
) -> Dict[str, Any]:
    """
    运行 ML 信号扫描（便捷函数）

    Args:
        codes: 股票代码列表，None则扫描所有数据
        data_dir: K线数据目录
        model_dir: 模型目录
        threshold: 信号阈值

    Returns:
        扫描结果
    """
    predictor = MLModelPredictor(model_dir=model_dir)
    if not predictor.auto_discover():
        return {"error": "模型加载失败"}

    # 加载数据
    kline_dict = {}
    for f in os.listdir(data_dir):
        if not f.startswith("kline_") or not f.endswith(".parquet"):
            continue

        code = f.replace("kline_", "").replace("_daily.parquet", "")

        if codes and code not in codes:
            continue

        try:
            df = pd.read_parquet(os.path.join(data_dir, f))
            kline_dict[code] = df
        except Exception as e:
            print(f"[ML] 加载 {code} 失败: {e}")

    if not kline_dict:
        return {"error": "无有效K线数据"}

    # 生成信号
    signals = predictor.generate_trading_signals(kline_dict, threshold=threshold)

    return {
        "model_info": predictor.get_model_info(),
        "signals": signals,
        "scanned_count": len(kline_dict),
    }


# ═══════════════════════════════════════════════════════════════
# v2.0 增强预测器 — 四维优化
# ═══════════════════════════════════════════════════════════════


class EnhancedPredictor:
    """增强预测器 v2.0 — 支持多窗口 / 过滤震荡 / 增强特征 / 样本加权

    加载 ml_enhanced_trainer 训练的模型，能够：
    - 自动识别 T+1 / T+5 / T+10 预测窗口
    - 自动检测是否为过滤震荡日模型（默认忽略震荡预测）
    - 使用增强特征（行业RS/市场宽度/北向/PE/PB/ROE）
    - 多模型 Stacking 融合
    """

    def __init__(self, model_dir: str = "models", weight_method: str = "f1_weighted"):
        self.model_dir = model_dir
        self.weight_method = weight_method
        self.models: Dict[str, Any] = {}
        self.model_weights: Dict[str, float] = {}
        self.selected_features: List[str] = []
        self.metadata: Dict[str, Any] = {}
        self.prediction_horizon: int = 1
        self.filter_oscillation: bool = True
        self._loaded = False

        # 尝试加载增强特征工程器
        try:
            from .enhanced_trainer import EnhancedFeatureEngineer

            self.feature_engineer = EnhancedFeatureEngineer(data_dir=model_dir.replace("models", "data/cache"))
        except ImportError:
            self.feature_engineer = MLFeatureEngineer()

    def auto_discover_and_load(self, prefer_enhanced: bool = True) -> bool:
        """自动发现并加载最佳模型

        优先加载 enhanced_v2 模型，回退 enhanced → optimized → optuna
        """
        # 优先: 增强v2模型
        if prefer_enhanced:
            meta_patterns = [
                "training_metadata_enhanced_v2_*.json",
                "training_metadata_enhanced_*.json",
                "training_metadata_optuna_*.json",
                "training_metadata_optimized_*.json",
                "training_metadata_*.json",
            ]
        else:
            meta_patterns = [
                "training_metadata_optuna_*.json",
                "training_metadata_optimized_*.json",
                "training_metadata_enhanced_v2_*.json",
                "training_metadata_*.json",
            ]

        for pattern in meta_patterns:
            meta_files = sorted(glob.glob(os.path.join(self.model_dir, pattern)), reverse=True)
            if meta_files:
                return self._load_from_meta(meta_files[0])

        print("[EnhancedPredictor] 未找到训练元数据")
        return False

    def _load_from_meta(self, meta_path: str) -> bool:
        """从元数据加载所有模型"""
        print(f"[EnhancedPredictor] 加载: {os.path.basename(meta_path)}")
        with open(meta_path, "r", encoding="utf-8") as f:
            self.metadata = json.load(f)

        # 读取配置
        cfg = self.metadata.get("config", {})
        self.prediction_horizon = cfg.get("prediction_horizon", 1)
        self.filter_oscillation = cfg.get("filter_oscillation", True)

        self.selected_features = self.metadata.get("features", [])
        results = self.metadata.get("results", {})
        self.metadata.get("best_model", "")

        # 加载所有模型及其权重
        f1_scores = {}
        loaded_count = 0

        for model_name, metrics in results.items():
            model_path = metrics.get("model_path", "")
            if not model_path or not os.path.exists(model_path):
                pattern = os.path.join(self.model_dir, f"{model_name}_*.pkl")
                model_files = sorted(glob.glob(pattern), reverse=True)
                if model_files:
                    model_path = model_files[0]
                else:
                    continue

            try:
                model = joblib.load(model_path)
                self.models[model_name] = model
                f1_scores[model_name] = metrics.get("f1", 0)
                loaded_count += 1
                print(f"  ✅ {model_name} (F1={f1_scores[model_name]:.4f})")
            except Exception as e:
                print(f"  ⚠️ {model_name}: {e}")

        if loaded_count == 0:
            return False

        # 计算融合权重
        if self.weight_method == "f1_weighted" and sum(f1_scores.values()) > 0:
            self.model_weights = {k: v / sum(f1_scores.values()) for k, v in f1_scores.items()}
        elif self.weight_method == "auc_weighted":
            auc_scores = {k: results[k].get("auc", 0) for k in f1_scores}
            total = sum(auc_scores.values())
            self.model_weights = (
                {k: v / total for k, v in auc_scores.items()}
                if total > 0
                else {k: 1.0 / loaded_count for k in f1_scores}
            )
        else:
            self.model_weights = {k: 1.0 / loaded_count for k in f1_scores}

        self._loaded = True
        print(f"  📐 预测窗口: T+{self.prediction_horizon} | 过滤震荡: {self.filter_oscillation}")
        print(f"  📊 加载 {loaded_count} 个模型, 权重: {self.weight_method}")
        return True

    def predict(
        self,
        kline_df: pd.DataFrame,
        market_data: Optional[Dict[str, pd.DataFrame]] = None,
        northbound_data: pd.DataFrame = None,
    ) -> Optional[Dict[str, Any]]:
        """增强预测 — 支持多窗口 + 新特征

        Args:
            kline_df: 单标的K线数据
            market_data: 全市场K线(用于行业RS/市场宽度)
            northbound_data: 北向资金(可选)

        Returns:
            预测结果字典, 包含:
            - prediction: 0=跌, 1=涨
            - probability: 上涨概率
            - strength: 信号强度分类 (strong_buy/buy/hold/sell/strong_sell)
            - is_oscillation: 是否判定为震荡 (filter_oscillation模式)
            - horizon: 预测窗口 T+N
        """
        if not self._loaded:
            if not self.auto_discover_and_load():
                return None

        # 使用增强特征工程器或回退
        try:
            feat_df = self.feature_engineer.build_enhanced_features(
                kline_df,
                market_data=market_data,
                northbound_data=northbound_data,
                prediction_horizon=self.prediction_horizon,
            )
        except (AttributeError, TypeError):
            # 回退到基础特征工程
            feat_df = MLFeatureEngineer().build_features(kline_df)

        # 提取可用特征
        available = [f for f in self.selected_features if f in feat_df.columns]
        if not available:
            # 如果增强特征全不可用, 尝试基础特征
            available = [f for f in self.selected_features if f in feat_df.columns]
            if not available:
                print("[EnhancedPredictor] 无可用特征")
                return None

        feat_clean = feat_df[available].fillna(0).replace([np.inf, -np.inf], 0)
        if len(feat_clean) == 0:
            return None

        X = feat_clean.iloc[-1:].values

        # 多模型加权融合
        weighted_prob = 0.0
        individual_probs = {}
        individual_preds = {}
        total_weight = sum(self.model_weights.values())

        for name, model in self.models.items():
            try:
                proba = model.predict_proba(X)[0]
                up_prob = proba[1] if len(proba) > 1 else proba[0]
                pred = model.predict(X)[0]
                w = self.model_weights.get(name, 0)
                weighted_prob += up_prob * w
                individual_probs[name] = float(up_prob)
                individual_preds[name] = int(pred)
            except Exception:
                continue

        if total_weight == 0:
            return None

        final_prob = weighted_prob / total_weight
        final_pred = 1 if final_prob > 0.5 else 0

        # 模型间一致性
        pred_values = list(individual_preds.values())
        agreement = sum(1 for p in pred_values if p == final_pred) / max(len(pred_values), 1)

        # 信号强度分类 (三分类逻辑: 过滤震荡)
        if self.filter_oscillation:
            signal_strength = (final_prob - 0.5) * 2
            confidence = abs(signal_strength) * agreement

            if final_prob > 0.65:
                strength = "strong_buy"
            elif final_prob > 0.55:
                strength = "buy"
            elif final_prob < 0.35:
                strength = "strong_sell"
            elif final_prob < 0.45:
                strength = "sell"
            else:
                strength = "hold"  # 震荡区域 — 不参与交易
        else:
            signal_strength = (final_prob - 0.5) * 2
            confidence = abs(signal_strength) * agreement
            if final_prob > 0.55:
                strength = "buy"
            elif final_prob < 0.45:
                strength = "sell"
            else:
                strength = "hold"

        return {
            "prediction": final_pred,
            "direction": "上涨" if final_pred == 1 else "下跌",
            "probability": float(final_prob),
            "signal": float(signal_strength),
            "confidence": float(confidence),
            "strength": strength,
            "model": "EnhancedStacking",
            "individual_probs": individual_probs,
            "model_count": len(self.models),
            "agreement": float(agreement),
            "horizon": self.prediction_horizon,
            "filter_oscillation": self.filter_oscillation,
        }

    def batch_predict(
        self,
        kline_dict: Dict[str, pd.DataFrame],
        market_data: Optional[Dict[str, pd.DataFrame]] = None,
        top_n: int = 20,
    ) -> List[Dict[str, Any]]:
        """批量增强预测"""
        results = []
        for code, df in kline_dict.items():
            pred = self.predict(df, market_data=market_data)
            if pred:
                pred["code"] = code
                results.append(pred)
        results.sort(key=lambda x: x["probability"], reverse=True)
        return results[:top_n]

    def generate_trading_signals(
        self,
        kline_dict: Dict[str, pd.DataFrame],
        market_data: Optional[Dict[str, pd.DataFrame]] = None,
        threshold: float = 0.55,
    ) -> Dict[str, Any]:
        """生成交易信号 (增强版: 过滤震荡)"""
        predictions = self.batch_predict(kline_dict, market_data=market_data, top_n=len(kline_dict))

        buy_signals = []
        sell_signals = []
        hold_signals = []

        for pred in predictions:
            code = pred["code"]
            prob = pred["probability"]
            strength = pred.get("strength", "hold")

            entry = {
                "code": code,
                "probability": prob,
                "confidence": pred["confidence"],
                "signal_strength": pred["signal"],
                "strength": strength,
                "agreement": pred.get("agreement", 0),
                "horizon": self.prediction_horizon,
            }

            if strength in ("strong_buy", "buy"):
                buy_signals.append(entry)
            elif strength in ("strong_sell", "sell"):
                sell_signals.append(entry)
            else:
                hold_signals.append(entry)

        return {
            "buy": buy_signals,
            "sell": sell_signals,
            "hold": hold_signals,
            "threshold": threshold,
            "total": len(predictions),
            "model": "EnhancedStacking",
            "horizon": self.prediction_horizon,
            "filter_oscillation": self.filter_oscillation,
            "model_count": len(self.models),
            "model_accuracy": max(
                (self.metadata.get("results", {}).get(k, {}).get("accuracy", 0) for k in self.models), default=0
            ),
        }

    def get_model_info(self) -> Dict[str, Any]:
        """获取模型信息 (P0-2增强: 含统计显著性)"""
        if not self._loaded:
            return {"loaded": False}

        best_model = self.metadata.get("best_model", "unknown")
        result = self.metadata.get("results", {}).get(best_model, {})

        info = {
            "loaded": True,
            "best_model": best_model,
            "accuracy": result.get("accuracy", 0),
            "f1": result.get("f1", 0),
            "auc": result.get("auc", 0),
            "feature_count": len(self.selected_features),
            "features": self.selected_features,
            "timestamp": self.metadata.get("timestamp", ""),
            "horizon": self.prediction_horizon,
            "filter_oscillation": self.filter_oscillation,
            "model_count": len(self.models),
            "n_test": result.get("n_test", 0),
            "n_correct": result.get("n_correct", 0),
        }

        # P0-2: 统计显著性
        if "statistical_validation" in result:
            info["statistical_validation"] = result["statistical_validation"]
        elif info["n_test"] > 0:
            try:
                try:
                    from scipy.stats import binomtest

                    n_test = info["n_test"]
                    n_correct = info["n_correct"]
                    acc = n_correct / n_test
                    se = np.sqrt(acc * (1 - acc) / n_test)
                    t_stat = (acc - 0.5) / se if se > 0 else 0
                    binom_p = binomtest(n_correct, n_test, p=0.5, alternative="greater").pvalue
                except ImportError:
                    from scipy.stats import binom_test as _binom_test

                    n_test = info["n_test"]
                    n_correct = info["n_correct"]
                    acc = n_correct / n_test
                    se = np.sqrt(acc * (1 - acc) / n_test)
                    t_stat = (acc - 0.5) / se if se > 0 else 0
                    binom_p = _binom_test(n_correct, n_test, p=0.5, alternative="greater")
                info["statistical_validation"] = {
                    "accuracy": acc,
                    "n_test": n_test,
                    "n_correct": n_correct,
                    "t_statistic": float(t_stat),
                    "binomial_test": {"p_value": float(binom_p)},
                    "is_significant": binom_p < 0.05 or t_stat > 2.0,
                    "verdict": "PASS (显著)" if (binom_p < 0.05 or t_stat > 2.0) else "WEAK (边界)",
                    "note": "从 n_test/n_correct 估算，建议运行 --train-enhanced 获取完整BI/置换检验",
                }
            except ImportError:
                pass

        return info


if __name__ == "__main__":
    result = run_ml_signal_scan()
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
