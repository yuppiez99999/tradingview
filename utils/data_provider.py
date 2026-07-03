# -*- coding: utf-8 -*-
"""
数据提供器

功能：
- 市场数据获取
- 数据预处理
- 数据缓存
- 数据验证
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Union
import json
import os
from collections import deque
import threading
import time

from logger import get_logger

logger = get_logger('data_provider')


class MarketDataProvider:
    """市场数据提供器"""
    
    def __init__(self, cache_size: int = 1000):
        """
        初始化市场数据提供器
        
        Args:
            cache_size: 缓存大小
        """
        self.cache_size = cache_size
        self.data_cache = {}
        self.cache_lock = threading.Lock()
        
        # 数据源配置
        self.data_sources = {
            'real_time': {
                'enabled': True,
                'refresh_interval': 60,  # 60秒
                'last_update': None
            },
            'historical': {
                'enabled': True,
                'cache_days': 365,
                'update_frequency': 'daily'
            },
            'sentiment': {
                'enabled': True,
                'refresh_interval': 300,  # 5分钟
                'last_update': None
            }
        }
        
        logger.info("市场数据提供器初始化完成")
    
    def get_market_data(self, symbol: str = None) -> Dict:
        """
        获取市场数据
        
        Args:
            symbol: 交易品种代码
            
        Returns:
            市场数据字典
        """
        try:
            # 检查缓存
            cache_key = f"market_{symbol or 'SPY'}"
            
            with self.cache_lock:
                if cache_key in self.data_cache:
                    cached_data = self.data_cache[cache_key]
                    cache_time = cached_data.get('timestamp')
                    
                    # 检查缓存是否过期
                    if cache_time and (datetime.now() - cache_time).seconds < 60:
                        logger.debug(f"使用缓存的市场数据: {cache_key}")
                        return cached_data['data']
            
            # 获取实时数据
            market_data = self._fetch_real_time_data(symbol)
            
            # 更新缓存
            with self.cache_lock:
                self.data_cache[cache_key] = {
                    'data': market_data,
                    'timestamp': datetime.now()
                }
                
                # 限制缓存大小
                if len(self.data_cache) > self.cache_size:
                    oldest_key = next(iter(self.data_cache))
                    del self.data_cache[oldest_key]
            
            logger.info(f"获取市场数据: {cache_key}")
            return market_data
            
        except Exception as e:
            logger.error(f"获取市场数据失败: {e}")
            return self._get_default_market_data()
    
    def get_historical_data(self, symbol: str, period: str = '1y') -> pd.DataFrame:
        """
        获取历史数据
        
        Args:
            symbol: 交易品种代码
            period: 时间周期 ('1d', '1w', '1m', '3m', '6m', '1y')
            
        Returns:
            历史数据DataFrame
        """
        try:
            # 检查缓存
            cache_key = f"historical_{symbol}_{period}"
            
            with self.cache_lock:
                if cache_key in self.data_cache:
                    cached_data = self.data_cache[cache_key]
                    cache_time = cached_data.get('timestamp')
                    
                    # 检查缓存是否过期
                    if cache_time and (datetime.now() - cache_time).days < 1:
                        logger.debug(f"使用缓存的历史数据: {cache_key}")
                        return cached_data['data']
            
            # 获取历史数据
            historical_data = self._fetch_historical_data(symbol, period)
            
            # 更新缓存
            with self.cache_lock:
                self.data_cache[cache_key] = {
                    'data': historical_data,
                    'timestamp': datetime.now()
                }
            
            logger.info(f"获取历史数据: {cache_key}")
            return historical_data
            
        except Exception as e:
            logger.error(f"获取历史数据失败: {e}")
            return self._get_default_historical_data()
    
    def get_sentiment_data(self, symbol: str = None) -> Dict:
        """
        获取情绪数据
        
        Args:
            symbol: 交易品种代码
            
        Returns:
            情绪数据字典
        """
        try:
            # 检查缓存
            cache_key = f"sentiment_{symbol or 'SPY'}"
            
            with self.cache_lock:
                if cache_key in self.data_cache:
                    cached_data = self.data_cache[cache_key]
                    cache_time = cached_data.get('timestamp')
                    
                    # 检查缓存是否过期
                    if cache_time and (datetime.now() - cache_time).seconds < 300:
                        logger.debug(f"使用缓存的情绪数据: {cache_key}")
                        return cached_data['data']
            
            # 获取情绪数据
            sentiment_data = self._fetch_sentiment_data(symbol)
            
            # 更新缓存
            with self.cache_lock:
                self.data_cache[cache_key] = {
                    'data': sentiment_data,
                    'timestamp': datetime.now()
                }
            
            logger.info(f"获取情绪数据: {cache_key}")
            return sentiment_data
            
        except Exception as e:
            logger.error(f"获取情绪数据失败: {e}")
            return self._get_default_sentiment_data()
    
    def get_technical_indicators(self, symbol: str) -> Dict:
        """
        获取技术指标
        
        Args:
            symbol: 交易品种代码
            
        Returns:
            技术指标字典
        """
        try:
            # 获取历史数据
            historical_data = self.get_historical_data(symbol)
            
            # 计算技术指标
            technical_indicators = self._calculate_technical_indicators(historical_data)
            
            logger.info(f"计算技术指标: {symbol}")
            return technical_indicators
            
        except Exception as e:
            logger.error(f"获取技术指标失败: {e}")
            return {}
    
    def _fetch_real_time_data(self, symbol: str) -> Dict:
        """获取实时数据"""
        try:
            # 模拟实时数据获取
            # 实际应用中应该从数据源API获取
            base_price = 3000
            
            return {
                'timestamp': datetime.now().isoformat(),
                'symbol': symbol or 'SPY',
                'index_price': base_price,
                'prev_close': base_price * 0.999,
                'open': base_price * 0.998,
                'high': base_price * 1.002,
                'low': base_price * 0.996,
                'volume': 10000000,
                'volatility': 0.15,
                'var_95': 0.02,
                'var_99': 0.035,
                'es_95': 0.03,
                'beta': 1.0,
                'liquidity': 1.0,
                'sentiment_score': 0.2,
                'correlation_matrix': np.eye(3).tolist(),
                'tracking_error': 0.03,
                'market_correlation': 0.7,
                'returns': np.random.normal(0.0003, 0.01, 252),
                'vix_future_price': 20.0,
                'kurtosis': 3.0,
                'skewness': 0.0,
                'extreme_events': 0,
                'put_call_ratio': 1.2,
                'options_skew': 0.0,
                'news_count': 50,
                'positive_news': 25,
                'negative_news': 20,
                'social_mentions': {'positive': 120, 'negative': 80},
                'analyst_ratings': {'buy': 15, 'sell': 8, 'hold': 12}
            }
            
        except Exception as e:
            logger.error(f"获取实时数据失败: {e}")
            return self._get_default_market_data()
    
    def _fetch_historical_data(self, symbol: str, period: str) -> pd.DataFrame:
        """获取历史数据"""
        try:
            # 根据周期确定数据点数
            period_mapping = {
                '1d': 1,
                '1w': 5,
                '1m': 20,
                '3m': 60,
                '6m': 120,
                '1y': 252
            }
            
            data_points = period_mapping.get(period, 252)
            
            # 生成模拟历史数据
            dates = pd.date_range(
                end=datetime.now(),
                periods=data_points,
                freq='D'
            )
            
            base_price = 3000
            returns = np.random.normal(0.0003, 0.01, data_points)
            prices = [base_price]
            
            for ret in returns[1:]:
                prices.append(prices[-1] * (1 + ret))
            
            historical_data = pd.DataFrame({
                'date': dates,
                'open': prices,
                'high': [p * 1.01 for p in prices],
                'low': [p * 0.99 for p in prices],
                'close': prices,
                'volume': [10000000 + i * 1000 for i in range(data_points)],
                'returns': returns
            })
            
            historical_data.set_index('date', inplace=True)
            
            return historical_data
            
        except Exception as e:
            logger.error(f"获取历史数据失败: {e}")
            return self._get_default_historical_data()
    
    def _fetch_sentiment_data(self, symbol: str) -> Dict:
        """获取情绪数据"""
        try:
            # 模拟情绪数据
            return {
                'timestamp': datetime.now().isoformat(),
                'symbol': symbol or 'SPY',
                'news_sentiment': 0.1,
                'social_sentiment': 0.2,
                'analyst_sentiment': 0.15,
                'options_sentiment': 0.05,
                'composite_sentiment': 0.15,
                'news_count': 50,
                'positive_news': 25,
                'negative_news': 20,
                'social_mentions': {'positive': 120, 'negative': 80},
                'analyst_ratings': {'buy': 15, 'sell': 8, 'hold': 12},
                'put_call_ratio': 1.2,
                'options_skew': 0.0,
                'sentiment_trend': 'neutral'
            }
            
        except Exception as e:
            logger.error(f"获取情绪数据失败: {e}")
            return self._get_default_sentiment_data()
    
    def _calculate_technical_indicators(self, data: pd.DataFrame) -> Dict:
        """计算技术指标"""
        try:
            if len(data) < 20:
                return {}
            
            prices = data['close'].values
            volumes = data['volume'].values
            
            # 移动平均
            ma20 = np.mean(prices[-20:])
            ma50 = np.mean(prices[-50:])
            ma200 = np.mean(prices[-200:]) if len(prices) >= 200 else ma50
            
            # RSI
            delta = np.diff(prices)
            gain = np.where(delta > 0, delta, 0)
            loss = np.where(delta < 0, -delta, 0)
            
            avg_gain = np.mean(gain[-14:])
            avg_loss = np.mean(loss[-14:])
            
            if avg_loss == 0:
                rsi = 50
            else:
                rs = avg_gain / avg_loss
                rsi = 100 - (100 / (1 + rs))
            
            # MACD
            ema12 = self._calculate_ema(prices, 12)
            ema26 = self._calculate_ema(prices, 26)
            macd = ema12 - ema26
            
            # 布林带
            sma20 = np.mean(prices[-20:])
            std20 = np.std(prices[-20:])
            bb_upper = sma20 + 2 * std20
            bb_lower = sma20 - 2 * std20
            
            # 计算返回结果
            technical_indicators = {
                'timestamp': datetime.now().isoformat(),
                'ma20': ma20,
                'ma50': ma50,
                'ma200': ma200,
                'rsi': rsi,
                'macd': macd,
                'bb_upper': bb_upper,
                'bb_lower': bb_lower,
                'bb_width': (bb_upper - bb_lower) / sma20,
                'price_position': (prices[-1] - bb_lower) / (bb_upper - bb_lower),
                'volume_sma': np.mean(volumes[-20:]),
                'trend': 'upward' if prices[-1] > prices[-5] else 'downward'
            }
            
            return technical_indicators
            
        except Exception as e:
            logger.error(f"计算技术指标失败: {e}")
            return {}
    
    def _calculate_ema(self, data: np.ndarray, period: int) -> float:
        """计算指数移动平均"""
        if len(data) < period:
            return np.mean(data)
        
        alpha = 2 / (period + 1)
        ema = data[0]
        
        for value in data[1:]:
            ema = alpha * value + (1 - alpha) * ema
        
        return ema
    
    def _get_default_market_data(self) -> Dict:
        """获取默认市场数据"""
        return {
            'timestamp': datetime.now().isoformat(),
            'symbol': 'SPY',
            'index_price': 3000,
            'prev_close': 3000,
            'open': 3000,
            'high': 3000,
            'low': 3000,
            'volume': 10000000,
            'volatility': 0.15,
            'var_95': 0.02,
            'var_99': 0.035,
            'es_95': 0.03,
            'beta': 1.0,
            'liquidity': 1.0,
            'sentiment_score': 0.0,
            'correlation_matrix': np.eye(3).tolist(),
            'tracking_error': 0.03,
            'market_correlation': 0.7,
            'returns': np.zeros(252),
            'vix_future_price': 20.0,
            'kurtosis': 3.0,
            'skewness': 0.0,
            'extreme_events': 0,
            'put_call_ratio': 1.0,
            'options_skew': 0.0,
            'news_count': 0,
            'positive_news': 0,
            'negative_news': 0,
            'social_mentions': {'positive': 0, 'negative': 0},
            'analyst_ratings': {'buy': 0, 'sell': 0, 'hold': 0}
        }
    
    def _get_default_historical_data(self) -> pd.DataFrame:
        """获取默认历史数据"""
        dates = pd.date_range(end=datetime.now(), periods=252, freq='D')
        base_price = 3000
        
        return pd.DataFrame({
            'date': dates,
            'open': [base_price] * 252,
            'high': [base_price * 1.01] * 252,
            'low': [base_price * 0.99] * 252,
            'close': [base_price] * 252,
            'volume': [10000000] * 252,
            'returns': [0] * 252
        }).set_index('date')
    
    def _get_default_sentiment_data(self) -> Dict:
        """获取默认情绪数据"""
        return {
            'timestamp': datetime.now().isoformat(),
            'symbol': 'SPY',
            'news_sentiment': 0.0,
            'social_sentiment': 0.0,
            'analyst_sentiment': 0.0,
            'options_sentiment': 0.0,
            'composite_sentiment': 0.0,
            'news_count': 0,
            'positive_news': 0,
            'negative_news': 0,
            'social_mentions': {'positive': 0, 'negative': 0},
            'analyst_ratings': {'buy': 0, 'sell': 0, 'hold': 0},
            'put_call_ratio': 1.0,
            'options_skew': 0.0,
            'sentiment_trend': 'neutral'
        }
    
    def clear_cache(self):
        """清除缓存"""
        with self.cache_lock:
            self.data_cache.clear()
            logger.info("数据缓存已清除")
    
    def get_cache_info(self) -> Dict:
        """获取缓存信息"""
        with self.cache_lock:
            return {
                'cache_size': len(self.data_cache),
                'max_cache_size': self.cache_size,
                'cached_items': list(self.data_cache.keys())
            }


# 全局数据提供器实例
_data_provider = None

def get_market_data(symbol: str = None) -> Dict:
    """获取市场数据（全局函数）"""
    global _data_provider
    if _data_provider is None:
        _data_provider = MarketDataProvider()
    return _data_provider.get_market_data(symbol)

def get_historical_data(symbol: str, period: str = '1y') -> pd.DataFrame:
    """获取历史数据（全局函数）"""
    global _data_provider
    if _data_provider is None:
        _data_provider = MarketDataProvider()
    return _data_provider.get_historical_data(symbol, period)

def get_sentiment_data(symbol: str = None) -> Dict:
    """获取情绪数据（全局函数）"""
    global _data_provider
    if _data_provider is None:
        _data_provider = MarketDataProvider()
    return _data_provider.get_sentiment_data(symbol)

def get_technical_indicators(symbol: str) -> Dict:
    """获取技术指标（全局函数）"""
    global _data_provider
    if _data_provider is None:
        _data_provider = MarketDataProvider()
    return _data_provider.get_technical_indicators(symbol)


if __name__ == "__main__":
    # 测试数据提供器
    print("测试市场数据提供器")
    
    # 获取市场数据
    market_data = get_market_data()
    print("市场数据:", market_data['index_price'])
    
    # 获取历史数据
    historical_data = get_historical_data('SPY', '1m')
    print("历史数据形状:", historical_data.shape)
    
    # 获取情绪数据
    sentiment_data = get_sentiment_data()
    print("情绪数据:", sentiment_data['composite_sentiment'])
    
    # 获取技术指标
    tech_indicators = get_technical_indicators('SPY')
    print("技术指标:", list(tech_indicators.keys()))
    
    # 获取缓存信息
    cache_info = _data_provider.get_cache_info()
    print("缓存信息:", cache_info)