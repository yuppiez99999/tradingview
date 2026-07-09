# -*- coding: utf-8 -*-
"""
智能对冲触发机制 - 世界级对冲基金的智能决策系统

系统特点：
- 市场情绪监控：基于新闻、社交媒体、分析师情绪的综合分析
- 技术指标监控：多时间框架技术指标组合分析
- ML预测模型：机器学习预测市场趋势和波动率
- 动态阈值调整：基于市场状态的自适应阈值
- 多重验证机制：避免误触发，提高触发准确性
- 前瞻性预警：提前预警潜在风险，而非滞后反应

策略逻辑：
1. 数据收集层：多源数据收集和预处理
2. 信号生成层：独立生成各类信号
3. 信号融合层：权重融合各类信号
4. 决策执行层：基于置信度的触发决策
5. 反馈优化层：基于历史表现的持续优化
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Union
import json
import os
from collections import deque

try:
    from utils.logger import get_logger
    from utils.data_provider import get_market_data, get_sentiment_data, get_technical_indicators
    from utils.ml_models import MarketPredictor
    logger = get_logger('smart_hedge_trigger')
except ImportError:
    import logging
    logger = logging.getLogger('smart_hedge_trigger')

class MarketSentimentMonitor:
    """
    市场情绪监控器
    """
    
    def __init__(self, window_size: int = 20):
        self.window_size = window_size
        self.sentiment_history = deque(maxlen=window_size)
        self.sentiment_weights = {
            'news_sentiment': 0.4,      # 新闻情绪权重
            'social_sentiment': 0.3,    # 社交媒体情绪权重
            'analyst_sentiment': 0.2,   # 分析师情绪权重
            'options_sentiment': 0.1    # 期权情绪权重
        }
        
    def calculate_sentiment_score(self, market_data: Dict) -> float:
        """
        计算市场情绪综合评分
        
        Args:
            market_data: 市场数据
            
        Returns:
            情绪评分 (-1 到 1)
        """
        try:
            # 收集各项情绪指标
            news_sentiment = self._analyze_news_sentiment(market_data)
            social_sentiment = self._analyze_social_sentiment(market_data)
            analyst_sentiment = self._analyze_analyst_sentiment(market_data)
            options_sentiment = self._analyze_options_sentiment(market_data)
            
            # 加权计算综合情绪
            sentiment_components = {
                'news_sentiment': news_sentiment,
                'social_sentiment': social_sentiment,
                'analyst_sentiment': analyst_sentiment,
                'options_sentiment': options_sentiment
            }
            
            composite_sentiment = sum(
                sentiment_components[key] * weight 
                for key, weight in self.sentiment_weights.items()
            )
            
            # 归一化到 -1 到 1
            normalized_sentiment = np.tanh(composite_sentiment)
            
            # 记录历史
            self.sentiment_history.append({
                'timestamp': datetime.now().isoformat(),
                'composite_sentiment': normalized_sentiment,
                'components': sentiment_components
            })
            
            logger.info(f"市场情绪评分: {normalized_sentiment:.3f}")
            return normalized_sentiment
            
        except Exception as e:
            logger.error(f"市场情绪计算失败: {e}")
            return 0.0
    
    def _analyze_news_sentiment(self, market_data: Dict) -> float:
        """分析新闻情绪"""
        # 模拟新闻情绪分析
        try:
            # 这里应该是实际的新闻情绪分析逻辑
            # 比如分析财经新闻标题、内容、来源等
            news_count = market_data.get('news_count', 0)
            positive_news = market_data.get('positive_news', 0)
            negative_news = market_data.get('negative_news', 0)
            
            if news_count > 0:
                positive_ratio = positive_news / news_count
                negative_ratio = negative_news / news_count
                news_sentiment = (positive_ratio - negative_ratio) * 0.8
            else:
                news_sentiment = 0.0
                
            return news_sentiment
            
        except Exception as e:
            logger.error(f"新闻情绪分析失败: {e}")
            return 0.0
    
    def _analyze_social_sentiment(self, market_data: Dict) -> float:
        """分析社交媒体情绪"""
        # 模拟社交媒体情绪分析
        try:
            social_mentions = market_data.get('social_mentions', {})
            positive_mentions = social_mentions.get('positive', 0)
            negative_mentions = social_mentions.get('negative', 0)
            
            if positive_mentions + negative_mentions > 0:
                social_sentiment = (positive_mentions - negative_mentions) / (positive_mentions + negative_mentions)
            else:
                social_sentiment = 0.0
                
            return social_sentiment
            
        except Exception as e:
            logger.error(f"社交媒体情绪分析失败: {e}")
            return 0.0
    
    def _analyze_analyst_sentiment(self, market_data: Dict) -> float:
        """分析分析师情绪"""
        # 模拟分析师情绪分析
        try:
            analyst_ratings = market_data.get('analyst_ratings', {})
            buy_ratings = analyst_ratings.get('buy', 0)
            sell_ratings = analyst_ratings.get('sell', 0)
            hold_ratings = analyst_ratings.get('hold', 0)
            
            total_ratings = buy_ratings + sell_ratings + hold_ratings
            if total_ratings > 0:
                buy_ratio = buy_ratings / total_ratings
                sell_ratio = sell_ratings / total_ratings
                analyst_sentiment = (buy_ratio - sell_ratio) * 1.2
            else:
                analyst_sentiment = 0.0
                
            return analyst_sentiment
            
        except Exception as e:
            logger.error(f"分析师情绪分析失败: {e}")
            return 0.0
    
    def _analyze_options_sentiment(self, market_data: Dict) -> float:
        """分析期权情绪"""
        # 模拟期权情绪分析
        try:
            put_call_ratio = market_data.get('put_call_ratio', 1.0)
            skewness = market_data.get('options_skew', 0.0)
            
            # PCR > 1.0 通常表示看跌情绪
            options_sentiment = -(put_call_ratio - 1.0) * 0.5 - skewness * 0.3
            
            return options_sentiment
            
        except Exception as e:
            logger.error(f"期权情绪分析失败: {e}")
            return 0.0

class TechnicalIndicatorMonitor:
    """
    技术指标监控器
    """
    
    def __init__(self):
        self.indicators_config = {
            'trend_indicators': {
                'ma20_50': {'weight': 0.3, 'threshold': 0.02},
                'ma50_200': {'weight': 0.4, 'threshold': 0.03},
                'macd': {'weight': 0.3, 'threshold': 0.01}
            },
            'momentum_indicators': {
                'rsi': {'weight': 0.4, 'threshold': 0.3},
                'stoch': {'weight': 0.3, 'threshold': 0.3},
                'cci': {'weight': 0.3, 'threshold': 0.2}
            },
            'volatility_indicators': {
                'bb_width': {'weight': 0.4, 'threshold': 0.15},
                'atr': {'weight': 0.3, 'threshold': 0.02},
                'keltner': {'weight': 0.3, 'threshold': 0.1}
            },
            'volume_indicators': {
                'volume_sma': {'weight': 0.5, 'threshold': 0.2},
                'volume_profile': {'weight': 0.3, 'threshold': 0.15},
                'money_flow': {'weight': 0.2, 'threshold': 0.1}
            }
        }
        
        self.indicators_history = deque(maxlen=100)
        
    def calculate_technical_score(self, market_data: Dict) -> float:
        """
        计算技术指标综合评分
        
        Args:
            market_data: 市场数据
            
        Returns:
            技术评分 (-1 到 1)
        """
        try:
            # 计算各类指标
            trend_score = self._calculate_trend_score(market_data)
            momentum_score = self._calculate_momentum_score(market_data)
            volatility_score = self._calculate_volatility_score(market_data)
            volume_score = self._calculate_volume_score(market_data)
            
            # 权重计算综合技术评分
            technical_scores = {
                'trend_score': trend_score,
                'momentum_score': momentum_score,
                'volatility_score': volatility_score,
                'volume_score': volume_score
            }
            
            weights = {
                'trend_score': 0.4,
                'momentum_score': 0.3,
                'volatility_score': 0.2,
                'volume_score': 0.1
            }
            
            composite_score = sum(
                technical_scores[key] * weight 
                for key, weight in weights.items()
            )
            
            # 归一化
            normalized_score = np.tanh(composite_score)
            
            # 记录历史
            self.indicators_history.append({
                'timestamp': datetime.now().isoformat(),
                'composite_score': normalized_score,
                'component_scores': technical_scores
            })
            
            logger.info(f"技术指标评分: {normalized_score:.3f}")
            return normalized_score
            
        except Exception as e:
            logger.error(f"技术指标计算失败: {e}")
            return 0.0
    
    def _calculate_trend_score(self, market_data: Dict) -> float:
        """计算趋势指标评分"""
        try:
            price_data = market_data.get('price_data', [])
            if len(price_data) < 200:
                return 0.0
                
            # 计算20日、50日、200日移动平均
            prices = np.array(price_data)
            
            # 20日均线 vs 50日均线
            ma20 = np.mean(prices[-20:])
            ma50 = np.mean(prices[-50:])
            ma20_50_ratio = (ma20 - ma50) / ma50
            
            # 50日均线 vs 200日均线
            ma200 = np.mean(prices[-200:])
            ma50_200_ratio = (ma50 - ma200) / ma200
            
            # MACD
            macd_line = self._calculate_macd(prices)
            
            # 综合趋势评分
            trend_score = (
                ma20_50_ratio * self.indicators_config['trend_indicators']['ma20_50']['weight'] +
                ma50_200_ratio * self.indicators_config['trend_indicators']['ma50_200']['weight'] +
                macd_line * self.indicators_config['trend_indicators']['macd']['weight']
            )
            
            return trend_score
            
        except Exception as e:
            logger.error(f"趋势指标计算失败: {e}")
            return 0.0
    
    def _calculate_momentum_score(self, market_data: Dict) -> float:
        """计算动量指标评分"""
        try:
            price_data = market_data.get('price_data', [])
            if len(price_data) < 20:
                return 0.0
                
            prices = np.array(price_data)
            
            # RSI
            rsi = self._calculate_rsi(prices)
            
            # Stochastic
            stoch = self._calculate_stochastic(prices)
            
            # CCI
            cci = self._calculate_cci(prices)
            
            # 综合动量评分
            momentum_score = (
                rsi * self.indicators_config['momentum_indicators']['rsi']['weight'] +
                stoch * self.indicators_config['momentum_indicators']['stoch']['weight'] +
                cci * self.indicators_config['momentum_indicators']['cci']['weight']
            )
            
            return momentum_score
            
        except Exception as e:
            logger.error(f"动量指标计算失败: {e}")
            return 0.0
    
    def _calculate_volatility_score(self, market_data: Dict) -> float:
        """计算波动率指标评分"""
        try:
            price_data = market_data.get('price_data', [])
            if len(price_data) < 20:
                return 0.0
                
            prices = np.array(price_data)
            
            # 布林带宽度
            bb_width = self._calculate_bollinger_bands_width(prices)
            
            # ATR
            atr = self._calculate_atr(prices)
            
            # Keltner通道
            keltner = self._calculate_keltner(prices)
            
            # 综合波动率评分
            volatility_score = (
                bb_width * self.indicators_config['volatility_indicators']['bb_width']['weight'] +
                atr * self.indicators_config['volatility_indicators']['atr']['weight'] +
                keltner * self.indicators_config['volatility_indicators']['keltner']['weight']
            )
            
            return volatility_score
            
        except Exception as e:
            logger.error(f"波动率指标计算失败: {e}")
            return 0.0
    
    def _calculate_volume_score(self, market_data: Dict) -> float:
        """计算成交量指标评分"""
        try:
            volume_data = market_data.get('volume_data', [])
            price_data = market_data.get('price_data', [])
            
            if len(volume_data) < 20 or len(price_data) < 20:
                return 0.0
                
            volumes = np.array(volume_data)
            prices = np.array(price_data)
            
            # 成交量SMA
            volume_sma = self._calculate_volume_sma(volumes)
            
            # 成交量分布
            volume_profile = self._calculate_volume_profile(volumes)
            
            # 资金流向
            money_flow = self._calculate_money_flow(prices, volumes)
            
            # 综合成交量评分
            volume_score = (
                volume_sma * self.indicators_config['volume_indicators']['volume_sma']['weight'] +
                volume_profile * self.indicators_config['volume_indicators']['volume_profile']['weight'] +
                money_flow * self.indicators_config['volume_indicators']['money_flow']['weight']
            )
            
            return volume_score
            
        except Exception as e:
            logger.error(f"成交量指标计算失败: {e}")
            return 0.0
    
    # 技术指标计算辅助方法
    def _calculate_macd(self, prices: np.ndarray) -> float:
        """计算MACD"""
        ema12 = self._calculate_ema(prices, 12)
        ema26 = self._calculate_ema(prices, 26)
        return ema12 - ema26
    
    def _calculate_rsi(self, prices: np.ndarray) -> float:
        """计算RSI"""
        delta = np.diff(prices)
        gain = np.where(delta > 0, delta, 0)
        loss = np.where(delta < 0, -delta, 0)
        
        avg_gain = np.mean(gain[-14:])
        avg_loss = np.mean(loss[-14:])
        
        if avg_loss == 0:
            return 1.0
            
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return (rsi - 50) / 50  # 归一化到 -1 到 1
    
    def _calculate_stochastic(self, prices: np.ndarray) -> float:
        """计算Stochastic"""
        if len(prices) < 14:
            return 0.0
            
        recent_high = np.max(prices[-14:])
        recent_low = np.min(prices[-14:])
        current_price = prices[-1]
        
        if recent_high == recent_low:
            return 0.0
            
        stoch = (current_price - recent_low) / (recent_high - recent_low)
        return (stoch - 0.5) * 2  # 归一化到 -1 到 1
    
    def _calculate_cci(self, prices: np.ndarray) -> float:
        """计算CCI"""
        if len(prices) < 20:
            return 0.0
            
        tp = prices  # 简化为价格，实际应该是典型价格
        sma20 = np.mean(tp[-20:])
        mad = np.mean(np.abs(tp[-20:] - sma20))
        
        if mad == 0:
            return 0.0
            
        cci = (tp[-1] - sma20) / (0.015 * mad)
        return np.tanh(cci / 100)  # 归一化到 -1 到 1
    
    def _calculate_bollinger_bands_width(self, prices: np.ndarray) -> float:
        """计算布林带宽度"""
        if len(prices) < 20:
            return 0.0
            
        sma20 = np.mean(prices[-20:])
        std20 = np.std(prices[-20:])
        
        if std20 == 0:
            return 0.0
            
        bb_width = (2 * std20) / sma20
        return np.tanh(bb_width * 5)  # 归一化
    
    def _calculate_atr(self, prices: np.ndarray) -> float:
        """计算ATR"""
        if len(prices) < 20:
            return 0.0
            
        # 简化为价格波动率
        atr = np.std(prices[-20:]) / np.mean(prices[-20:])
        return np.tanh(atr * 10)  # 归一化
    
    def _calculate_keltner(self, prices: np.ndarray) -> float:
        """计算Keltner通道"""
        if len(prices) < 20:
            return 0.0
            
        ema20 = self._calculate_ema(prices, 20)
        atr = self._calculate_atr(prices)
        
        keltner_width = atr * 2  # 简化计算
        return np.tanh(keltner_width * 5)  # 归一化
    
    def _calculate_volume_sma(self, volumes: np.ndarray) -> float:
        """计算成交量SMA"""
        if len(volumes) < 20:
            return 0.0
            
        recent_volume = np.mean(volumes[-10:])
        historical_volume = np.mean(volumes[-20:-10])
        
        if historical_volume == 0:
            return 0.0
            
        volume_ratio = (recent_volume - historical_volume) / historical_volume
        return np.tanh(volume_ratio * 3)  # 归一化
    
    def _calculate_volume_profile(self, volumes: np.ndarray) -> float:
        """计算成交量分布"""
        # 简化的成交量分布分析
        volume_std = np.std(volumes)
        volume_mean = np.mean(volumes)
        
        if volume_mean == 0:
            return 0.0
            
        cv = volume_std / volume_mean  # 变异系数
        return np.tanh(cv * 2)  # 归一化
    
    def _calculate_money_flow(self, prices: np.ndarray, volumes: np.ndarray) -> float:
        """计算资金流向"""
        if len(prices) < 20 or len(volumes) < 20:
            return 0.0
            
        # 计算价格变化
        price_change = prices[-1] - prices[-20]
        
        # 计算成交量变化
        volume_change = volumes[-1] - volumes[-20]
        
        # 资金流向评分
        money_flow = (price_change * volume_change) / (abs(price_change) * abs(volume_change) + 1e-6)
        return np.tanh(money_flow * 5)  # 归一化
    
    def _calculate_ema(self, data: np.ndarray, period: int) -> float:
        """计算指数移动平均"""
        if len(data) < period:
            return np.mean(data)
            
        alpha = 2 / (period + 1)
        ema = data[0]
        
        for value in data[1:]:
            ema = alpha * value + (1 - alpha) * ema
            
        return ema

class MLPredictor:
    """
    机器学习预测器
    """
    
    def __init__(self, model_type: str = 'ensemble'):
        self.model_type = model_type
        self.models = {}
        self.feature_importance = {}
        self.prediction_history = deque(maxlen=100)
        
        # 初始化模型
        self._initialize_models()
    
    def _initialize_models(self):
        """初始化机器学习模型"""
        try:
            # 这里应该是实际的模型初始化
            # 比如加载预训练的模型
            self.models = {
                'lstm': {'model': None, 'weight': 0.4},
                'random_forest': {'model': None, 'weight': 0.3},
                'gradient_boosting': {'model': None, 'weight': 0.3}
            }
            
            logger.info("ML预测器初始化完成")
            
        except Exception as e:
            logger.error(f"ML预测器初始化失败: {e}")
    
    def predict_market_direction(self, market_data: Dict) -> Tuple[float, float]:
        """
        预测市场方向
        
        Args:
            market_data: 市场数据
            
        Returns:
            (方向概率, 置信度)
        """
        try:
            # 准备特征
            features = self._prepare_features(market_data)
            
            # 单个模型预测
            lstm_pred = self._lstm_predict(features)
            rf_pred = self._random_forest_predict(features)
            gb_pred = self._gradient_boosting_predict(features)
            
            # 加权集成
            ensemble_pred = (
                lstm_pred[0] * self.models['lstm']['weight'] +
                rf_pred[0] * self.models['random_forest']['weight'] +
                gb_pred[0] * self.models['gradient_boosting']['weight']
            )
            
            # 计算置信度
            confidence = self._calculate_confidence([lstm_pred, rf_pred, gb_pred])
            
            # 记录预测历史
            self.prediction_history.append({
                'timestamp': datetime.now().isoformat(),
                'prediction': ensemble_pred,
                'confidence': confidence,
                'individual_predictions': {
                    'lstm': lstm_pred[0],
                    'random_forest': rf_pred[0],
                    'gradient_boosting': gb_pred[0]
                }
            })
            
            logger.info(f"ML预测结果: 方向={ensemble_pred:.3f}, 置信度={confidence:.3f}")
            return ensemble_pred, confidence
            
        except Exception as e:
            logger.error(f"ML预测失败: {e}")
            return 0.0, 0.0
    
    def predict_volatility(self, market_data: Dict) -> Tuple[float, float]:
        """
        预测波动率
        
        Args:
            market_data: 市场数据
            
        Returns:
            (波动率预测, 置信度)
        """
        try:
            # 准备特征
            features = self._prepare_features(market_data)
            
            # 单个模型预测
            lstm_pred = self._lstm_predict_volatility(features)
            rf_pred = self._random_forest_predict_volatility(features)
            gb_pred = self._gradient_boosting_predict_volatility(features)
            
            # 加权集成
            ensemble_pred = (
                lstm_pred[0] * self.models['lstm']['weight'] +
                rf_pred[0] * self.models['random_forest']['weight'] +
                gb_pred[0] * self.models['gradient_boosting']['weight']
            )
            
            # 计算置信度
            confidence = self._calculate_confidence([lstm_pred, rf_pred, gb_pred])
            
            logger.info(f"波动率预测结果: {ensemble_pred:.3f}, 置信度={confidence:.3f}")
            return ensemble_pred, confidence
            
        except Exception as e:
            logger.error(f"波动率预测失败: {e}")
            return 0.15, 0.0
    
    def _prepare_features(self, market_data: Dict) -> np.ndarray:
        """准备特征向量"""
        try:
            # 提取基本特征
            features = [
                market_data.get('index_price', 3000) / 3000,  # 归一化价格
                market_data.get('volatility', 0.15) / 0.3,   # 归一化波动率
                market_data.get('var_95', 0.02) / 0.05,      # 归一化VaR
                market_data.get('beta', 1.0),                # Beta
                market_data.get('liquidity', 1.0),            # 流动性
                market_data.get('vix_future_price', 20.0) / 50,  # 归一化VIX
            ]
            
            # 添加技术指标特征
            if 'price_data' in market_data:
                prices = np.array(market_data['price_data'])
                if len(prices) >= 20:
                    # 计算技术指标特征
                    returns = np.diff(prices) / prices[:-1]
                    features.extend([
                        np.mean(returns[-10:]),   # 近期平均收益
                        np.std(returns[-20:]),   # 近期波动率
                        np.max(returns[-10:]),    # 近期最大收益
                        np.min(returns[-10:]),    # 近期最小收益
                    ])
            
            # 添加情绪指标特征
            features.extend([
                market_data.get('sentiment_score', 0.0),  # 情绪评分
                market_data.get('put_call_ratio', 1.0) - 1.0,  # PCR
            ])
            
            return np.array(features)
            
        except Exception as e:
            logger.error(f"特征准备失败: {e}")
            return np.zeros(15)
    
    def _lstm_predict(self, features: np.ndarray) -> Tuple[float, float]:
        """LSTM预测"""
        # 模拟LSTM预测
        # 实际应该使用预训练的LSTM模型
        direction = np.tanh(np.sum(features[:5]) * 0.2)
        confidence = min(abs(direction) * 1.5, 1.0)
        return direction, confidence
    
    def _random_forest_predict(self, features: np.ndarray) -> Tuple[float, float]:
        """随机森林预测"""
        # 模拟随机森林预测
        direction = np.tanh(np.sum(features[5:10]) * 0.15)
        confidence = min(abs(direction) * 1.2, 1.0)
        return direction, confidence
    
    def _gradient_boosting_predict(self, features: np.ndarray) -> Tuple[float, float]:
        """梯度提升预测"""
        # 模拟梯度提升预测
        direction = np.tanh(np.sum(features[10:]) * 0.1)
        confidence = min(abs(direction) * 1.0, 1.0)
        return direction, confidence
    
    def _lstm_predict_volatility(self, features: np.ndarray) -> Tuple[float, float]:
        """LSTM波动率预测"""
        # 模拟LSTM波动率预测
        volatility = min(abs(features[1]) * 1.5 + 0.05, 1.0)
        confidence = 0.7
        return volatility, confidence
    
    def _random_forest_predict_volatility(self, features: np.ndarray) -> Tuple[float, float]:
        """随机森林波动率预测"""
        volatility = min(abs(features[1]) * 1.2 + 0.08, 1.0)
        confidence = 0.6
        return volatility, confidence
    
    def _gradient_boosting_predict_volatility(self, features: np.ndarray) -> Tuple[float, float]:
        """梯度提升波动率预测"""
        volatility = min(abs(features[1]) * 1.0 + 0.10, 1.0)
        confidence = 0.5
        return volatility, confidence
    
    def _calculate_confidence(self, predictions: List[Tuple[float, float]]) -> float:
        """计算置信度"""
        # 基于多个预测的一致性计算置信度
        prediction_values = [p[0] for p in predictions]
        confidences = [p[1] for p in predictions]
        
        # 计算预测的一致性
        consistency = 1.0 - np.std(prediction_values) / (np.mean(np.abs(prediction_values)) + 1e-6)
        consistency = max(0, consistency)
        
        # 平均置信度
        avg_confidence = np.mean(confidences)
        
        # 综合置信度
        final_confidence = consistency * 0.6 + avg_confidence * 0.4
        
        return min(final_confidence, 1.0)

class SmartHedgeTrigger:
    """
    智能对冲触发器 - 整合所有触发机制
    """
    
    def __init__(self, confidence_threshold: float = 0.7):
        self.confidence_threshold = confidence_threshold
        
        # 初始化各个监控器
        self.sentiment_monitor = MarketSentimentMonitor()
        self.technical_monitor = TechnicalIndicatorMonitor()
        self.ml_predictor = MLPredictor()
        
        # 触发配置
        self.trigger_config = {
            'sentiment_threshold': 0.5,      # 情绪触发阈值
            'technical_threshold': 0.4,      # 技术指标触发阈值
            'prediction_threshold': 0.6,      # 预测触发阈值
            'combined_threshold': 0.7,        # 综合触发阈值
            'volatility_threshold': 0.25,     # 波动率触发阈值
            'min_confirmation': 2,             # 最少确认数
            'cooldown_period': 4,              # 冷却期（小时）
        }
        
        # 触发历史
        self.trigger_history = deque(maxlen=100)
        self.last_trigger_time = None
        self.cooldown_ends = None
        
        logger.info("智能对冲触发器初始化完成")
    
    def should_trigger_hedge(self, market_data: Dict) -> Dict:
        """
        判断是否应该触发对冲
        
        Args:
            market_data: 市场数据
            
        Returns:
            触发决策结果
        """
        try:
            logger.info("开始智能对冲触发判断")
            
            # 检查冷却期
            if self._is_in_cooldown():
                return {
                    'should_trigger': False,
                    'reason': '在冷却期中',
                    'confidence': 0.0,
                    'trigger_type': 'cooldown'
                }
            
            # 1. 市场情绪分析
            sentiment_score = self.sentiment_monitor.calculate_sentiment_score(market_data)
            sentiment_trigger = self._evaluate_sentiment_trigger(sentiment_score)
            
            # 2. 技术指标分析
            technical_score = self.technical_monitor.calculate_technical_score(market_data)
            technical_trigger = self._evaluate_technical_trigger(technical_score)
            
            # 3. ML预测分析
            direction_pred, direction_conf = self.ml_predictor.predict_market_direction(market_data)
            volatility_pred, volatility_conf = self.ml_predictor.predict_volatility(market_data)
            ml_trigger = self._evaluate_ml_trigger(direction_pred, volatility_pred, direction_conf, volatility_conf)
            
            # 4. 综合分析
            trigger_decision = self._make_trigger_decision(
                sentiment_trigger, technical_trigger, ml_trigger, market_data
            )
            
            # 5. 更新冷却期
            if trigger_decision['should_trigger']:
                self._update_cooldown()
            
            # 记录触发历史
            self.trigger_history.append({
                'timestamp': datetime.now().isoformat(),
                'trigger_decision': trigger_decision,
                'sentiment_score': sentiment_score,
                'technical_score': technical_score,
                'ml_prediction': direction_pred,
                'volatility_prediction': volatility_pred
            })
            
            logger.info(f"智能对冲触发决策: {trigger_decision}")
            return trigger_decision
            
        except Exception as e:
            logger.error(f"智能对冲触发判断失败: {e}")
            return {
                'should_trigger': False,
                'reason': f'系统错误: {e}',
                'confidence': 0.0,
                'trigger_type': 'error'
            }
    
    def _is_in_cooldown(self) -> bool:
        """检查是否在冷却期"""
        if self.cooldown_ends is None:
            return False
            
        return datetime.now() < self.cooldown_ends
    
    def _evaluate_sentiment_trigger(self, sentiment_score: float) -> Dict:
        """评估情绪触发"""
        if abs(sentiment_score) > self.trigger_config['sentiment_threshold']:
            return {
                'should_trigger': True,
                'confidence': abs(sentiment_score),
                'trigger_type': 'sentiment',
                'direction': 'negative' if sentiment_score < 0 else 'positive',
                'strength': 'strong' if abs(sentiment_score) > 0.8 else 'moderate'
            }
        else:
            return {
                'should_trigger': False,
                'confidence': 0.0,
                'trigger_type': 'sentiment',
                'reason': '情绪指标未达阈值'
            }
    
    def _evaluate_technical_trigger(self, technical_score: float) -> Dict:
        """评估技术指标触发"""
        if abs(technical_score) > self.trigger_config['technical_threshold']:
            return {
                'should_trigger': True,
                'confidence': abs(technical_score),
                'trigger_type': 'technical',
                'direction': 'negative' if technical_score < 0 else 'positive',
                'strength': 'strong' if abs(technical_score) > 0.7 else 'moderate'
            }
        else:
            return {
                'should_trigger': False,
                'confidence': 0.0,
                'trigger_type': 'technical',
                'reason': '技术指标未达阈值'
            }
    
    def _evaluate_ml_trigger(self, direction_pred: float, volatility_pred: float, 
                           direction_conf: float, volatility_conf: float) -> Dict:
        """评估ML预测触发"""
        # 检查方向预测
        direction_trigger = abs(direction_pred) > self.trigger_config['prediction_threshold']
        
        # 检查波动率预测
        volatility_trigger = volatility_pred > self.trigger_config['volatility_threshold']
        
        # 综合判断
        if direction_trigger or volatility_trigger:
            confidence = max(direction_conf, volatility_conf)
            return {
                'should_trigger': True,
                'confidence': confidence,
                'trigger_type': 'ml_prediction',
                'direction_trigger': direction_trigger,
                'volatility_trigger': volatility_trigger,
                'direction': 'negative' if direction_pred < 0 else 'positive',
                'strength': 'strong' if confidence > 0.8 else 'moderate'
            }
        else:
            return {
                'should_trigger': False,
                'confidence': 0.0,
                'trigger_type': 'ml_prediction',
                'reason': 'ML预测未达阈值'
            }
    
    def _make_trigger_decision(self, sentiment_trigger: Dict, technical_trigger: Dict, 
                             ml_trigger: Dict, market_data: Dict) -> Dict:
        """做出最终触发决策"""
        try:
            # 统计触发信号数量
            trigger_signals = []
            for trigger in [sentiment_trigger, technical_trigger, ml_trigger]:
                if trigger['should_trigger']:
                    trigger_signals.append(trigger)
            
            # 基本条件：至少需要2个信号确认
            if len(trigger_signals) < self.trigger_config['min_confirmation']:
                return {
                    'should_trigger': False,
                    'reason': f'确认信号不足 ({len(trigger_signals)}/{self.trigger_config["min_confirmation"]})',
                    'confidence': 0.0,
                    'trigger_type': 'insufficient_signals'
                }
            
            # 计算综合置信度
            confidences = [s['confidence'] for s in trigger_signals]
            max_confidence = max(confidences)
            avg_confidence = np.mean(confidences)
            
            # 检查波动率条件
            current_volatility = market_data.get('volatility', 0.15)
            if current_volatility > self.trigger_config['volatility_threshold']:
                # 高波动率时降低触发阈值
                adjusted_threshold = self.trigger_config['combined_threshold'] * 0.8
            else:
                adjusted_threshold = self.trigger_config['combined_threshold']
            
            # 综合置信度需要达到阈值
            if avg_confidence < adjusted_threshold:
                return {
                    'should_trigger': False,
                    'reason': f'综合置信度不足 ({avg_confidence:.3f} < {adjusted_threshold:.3f})',
                    'confidence': avg_confidence,
                    'trigger_type': 'low_confidence'
                }
            
            # 综合方向判断
            negative_signals = sum(1 for s in trigger_signals if s.get('direction') == 'negative')
            positive_signals = len(trigger_signals) - negative_signals
            
            # 确定主要触发类型
            trigger_types = [s['trigger_type'] for s in trigger_signals]
            primary_trigger_type = max(set(trigger_types), key=trigger_types.count)
            
            return {
                'should_trigger': True,
                'confidence': avg_confidence,
                'max_confidence': max_confidence,
                'trigger_type': primary_trigger_type,
                'trigger_signals': trigger_signals,
                'signal_count': len(trigger_signals),
                'direction': 'negative' if negative_signals >= positive_signals else 'positive',
                'volatility_context': 'high' if current_volatility > self.trigger_config['volatility_threshold'] else 'normal',
                'trigger_strength': 'strong' if max_confidence > 0.8 else 'moderate'
            }
            
        except Exception as e:
            logger.error(f"触发决策失败: {e}")
            return {
                'should_trigger': False,
                'reason': f'决策错误: {e}',
                'confidence': 0.0,
                'trigger_type': 'error'
            }
    
    def _update_cooldown(self):
        """更新冷却期"""
        self.cooldown_ends = datetime.now() + timedelta(hours=self.trigger_config['cooldown_period'])
        logger.info(f"冷却期更新至: {self.cooldown_ends}")
    
    def get_trigger_summary(self) -> Dict:
        """获取触发器总结"""
        try:
            if not self.trigger_history:
                return {'message': '暂无触发历史'}
            
            # 最近10次触发分析
            recent_triggers = list(self.trigger_history)[-10:]
            
            # 统计触发类型分布
            trigger_types = {}
            for trigger in recent_triggers:
                trigger_type = trigger['trigger_decision']['trigger_type']
                trigger_types[trigger_type] = trigger_types.get(trigger_type, 0) + 1
            
            # 平均置信度
            avg_confidence = np.mean([
                t['trigger_decision']['confidence'] for t in recent_triggers
            ])
            
            # 触发频率
            total_triggers = len(recent_triggers)
            successful_triggers = sum(1 for t in recent_triggers if t['trigger_decision']['should_trigger'])
            
            return {
                'total_triggers': len(self.trigger_history),
                'recent_triggers': len(recent_triggers),
                'successful_triggers': successful_triggers,
                'success_rate': successful_triggers / total_triggers if total_triggers > 0 else 0,
                'average_confidence': avg_confidence,
                'trigger_type_distribution': trigger_types,
                'is_in_cooldown': self._is_in_cooldown(),
                'next_trigger_time': self.cooldown_ends.isoformat() if self.cooldown_ends else None
            }
            
        except Exception as e:
            logger.error(f"触发器总结生成失败: {e}")
            return {'error': str(e)}
    
    def run_simulation(self) -> Dict:
        """运行触发器模拟"""
        try:
            logger.info("开始智能对冲触发器模拟")
            
            # 模拟市场数据
            simulation_data = {
                'index_price': 3000,
                'volatility': 0.15,
                'var_95': 0.02,
                'es_95': 0.03,
                'beta': 1.0,
                'liquidity': 1.0,
                'vix_future_price': 20.0,
                'sentiment_score': 0.2,
                'put_call_ratio': 1.2,
                'price_data': [3000, 3020, 2980, 3050, 3100, 3080, 3120, 3150, 3130, 3180],
                'volume_data': [10000, 12000, 8000, 15000, 18000, 16000, 20000, 22000, 19000, 25000],
                'news_count': 50,
                'positive_news': 20,
                'negative_news': 25,
                'social_mentions': {'positive': 100, 'negative': 150},
                'analyst_ratings': {'buy': 10, 'sell': 8, 'hold': 12}
            }
            
            # 测试不同市场情况
            test_scenarios = [
                {'name': '正常市场', 'data': simulation_data.copy()},
                {'name': '恐慌市场', 'data': self._create_panic_scenario(simulation_data)},
                {'name': '乐观市场', 'data': self._create_bullish_scenario(simulation_data)},
                {'name': '高波动市场', 'data': self._create_high_volatility_scenario(simulation_data)}
            ]
            
            results = []
            
            for scenario in test_scenarios:
                logger.info(f"测试场景: {scenario['name']}")
                
                # 执行触发判断
                trigger_result = self.should_trigger_hedge(scenario['data'])
                
                result = {
                    'scenario': scenario['name'],
                    'trigger_decision': trigger_result,
                    'market_data': scenario['data']
                }
                results.append(result)
            
            # 生成总结报告
            summary = self.get_trigger_summary()
            
            logger.info("智能对冲触发器模拟完成")
            
            return {
                'success': True,
                'simulation_results': results,
                'trigger_summary': summary,
                'conclusions': self._generate_simulation_conclusions(results)
            }
            
        except Exception as e:
            logger.error(f"触发器模拟失败: {e}")
            return {'success': False, 'error': str(e)}
    
    def _create_panic_scenario(self, base_data: Dict) -> Dict:
        """创建恐慌市场场景"""
        panic_data = base_data.copy()
        panic_data.update({
            'index_price': 2800,
            'volatility': 0.35,
            'var_95': 0.08,
            'sentiment_score': -0.8,
            'put_call_ratio': 2.5,
            'price_data': [3000, 2950, 2900, 2850, 2800, 2750, 2800, 2780, 2820, 2790],
            'news_count': 100,
            'positive_news': 10,
            'negative_news': 80,
            'social_mentions': {'positive': 50, 'negative': 300},
            'analyst_ratings': {'buy': 2, 'sell': 20, 'hold': 8}
        })
        return panic_data
    
    def _create_bullish_scenario(self, base_data: Dict) -> Dict:
        """创建乐观市场场景"""
        bullish_data = base_data.copy()
        bullish_data.update({
            'index_price': 3200,
            'volatility': 0.12,
            'var_95': 0.015,
            'sentiment_score': 0.7,
            'put_call_ratio': 0.8,
            'price_data': [3000, 3050, 3080, 3120, 3150, 3180, 3220, 3200, 3250, 3280],
            'news_count': 80,
            'positive_news': 60,
            'negative_news': 15,
            'social_mentions': {'positive': 200, 'negative': 50},
            'analyst_ratings': {'buy': 25, 'sell': 3, 'hold': 12}
        })
        return bullish_data
    
    def _create_high_volatility_scenario(self, base_data: Dict) -> Dict:
        """创建高波动率场景"""
        high_vol_data = base_data.copy()
        high_vol_data.update({
            'index_price': 3000,
            'volatility': 0.40,
            'var_95': 0.12,
            'sentiment_score': 0.0,
            'put_call_ratio': 1.8,
            'price_data': [3000, 3100, 2900, 3200, 2800, 3300, 2700, 3400, 2600, 3500],
            'volume_data': [15000, 25000, 20000, 30000, 35000, 28000, 40000, 32000, 45000, 38000],
            'news_count': 120,
            'positive_news': 40,
            'negative_news': 70,
            'social_mentions': {'positive': 100, 'negative': 200}
        })
        return high_vol_data
    
    def _generate_simulation_conclusions(self, results: List[Dict]) -> List[str]:
        """生成模拟结论"""
        conclusions = []
        
        # 分析各场景的触发情况
        for result in results:
            scenario = result['scenario']
            trigger_decision = result['trigger_decision']
            
            if trigger_decision['should_trigger']:
                conclusions.append(
                    f"{scenario}: 触发对冲 (置信度: {trigger_decision['confidence']:.3f}, "
                    f"类型: {trigger_decision['trigger_type']})"
                )
            else:
                conclusions.append(
                    f"{scenario}: 未触发对冲 (原因: {trigger_decision['reason']})"
                )
        
        # 分析触发器的整体表现
        total_scenarios = len(results)
        triggered_scenarios = sum(1 for r in results if r['trigger_decision']['should_trigger'])
        trigger_rate = triggered_scenarios / total_scenarios
        
        conclusions.append(f"总体触发率: {trigger_rate:.2%}")
        
        # 分析触发类型的有效性
        trigger_types = {}
        for result in results:
            if result['trigger_decision']['should_trigger']:
                trigger_type = result['trigger_decision']['trigger_type']
                trigger_types[trigger_type] = trigger_types.get(trigger_type, 0) + 1
        
        if trigger_types:
            most_effective_type = max(trigger_types.items(), key=lambda x: x[1])
            conclusions.append(f"最有效的触发类型: {most_effective_type[0]} ({most_effective_type[1]}次)")
        
        return conclusions


# 主程序
if __name__ == "__main__":
    print("智能对冲触发器启动")
    print("=" * 50)
    
    # 创建智能对冲触发器
    trigger = SmartHedgeTrigger(confidence_threshold=0.7)
    
    # 运行模拟
    simulation_result = trigger.run_simulation()
    
    print("\n模拟完成")
    print("=" * 50)
    
    # 输出结果
    if simulation_result['success']:
        print("模拟结果:")
        for conclusion in simulation_result['conclusions']:
            print(f"  - {conclusion}")
        
        print("\n触发器总结:")
        summary = simulation_result['trigger_summary']
        print(f"  总触发次数: {summary['total_triggers']}")
        print(f"  成功触发率: {summary['success_rate']:.2%}")
        print(f"  平均置信度: {summary['average_confidence']:.3f}")
        print(f"  触发类型分布: {summary['trigger_type_distribution']}")
        
        if summary['is_in_cooldown']:
            print(f"  当前在冷却期，下次触发时间: {summary['next_trigger_time']}")
    else:
        print(f"模拟失败: {simulation_result['error']}")