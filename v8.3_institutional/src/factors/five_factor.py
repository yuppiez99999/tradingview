#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
五维因子选股模型
基于世界顶级对冲基金标准的综合因子评估系统

五大因子维度：
1. 价值因子 (Value) - 估值水平、股息收益率
2. 质量因子 (Quality) - 盈利能力、财务健康状况
3. 动量因子 (Momentum) - 价格趋势、相对强度
4. 增长因子 (Growth) - 收入增长、利润增长
5. 安全因子 (Safety) - 风险控制、稳定性

使用方法：
python five_factor_model.py
"""

import os
import sys
import yaml
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import logging
from pathlib import Path

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class FiveFactorModel:
    """五维因子选股模型"""
    
    def __init__(self, portfolio_config: Dict, settings: Dict):
        self.portfolio = portfolio_config
        self.settings = settings
        self.factors = {
            'value': ValueFactor(),
            'quality': QualityFactor(),
            'momentum': MomentumFactor(),
            'growth': GrowthFactor(),
            'safety': SafetyFactor()
        }
        self.lookback_period = 252  # 1年数据
        self.rebalance_frequency = 20  # 20天再平衡
        
    def evaluate_stocks(self, klines: Dict[str, pd.DataFrame], 
                       fundamental_data: Optional[Dict] = None) -> Dict:
        """
        评估所有股票的因子得分
        
        Args:
            klines: K线数据字典
            fundamental_data: 基本面数据
            
        Returns:
            因子评估结果字典
        """
        logger.info("开始五维因子评估...")
        
        evaluation_results = {}
        
        for code, df in klines.items():
            if len(df) < self.lookback_period:
                continue
            
            # 提取特征
            features = self._extract_features(df, code, fundamental_data)
            
            # 计算各因子得分
            factor_scores = {}
            for factor_name, factor in self.factors.items():
                score = factor.calculate_score(features)
                factor_scores[factor_name] = score
            
            # 计算综合得分
            composite_score = self._calculate_composite_score(factor_scores)
            
            evaluation_results[code] = {
                'factor_scores': factor_scores,
                'composite_score': composite_score,
                'rank': None  # 待排序
            }
        
        # 排序和排名
        evaluation_results = self._rank_stocks(evaluation_results)
        
        logger.info(f"完成 {len(evaluation_results)} 只股票的因子评估")
        self._log_evaluation_results(evaluation_results)
        
        return evaluation_results
    
    def _extract_features(self, df: pd.DataFrame, code: str, 
                         fundamental_data: Optional[Dict]) -> Dict:
        """提取因子特征"""
        features = {}
        
        # 技术指标特征
        features['returns_1m'] = self._calculate_return(df, 21)  # 1个月收益率
        features['returns_3m'] = self._calculate_return(df, 63)  # 3个月收益率
        features['returns_6m'] = self._calculate_return(df, 126)  # 6个月收益率
        features['returns_12m'] = self._calculate_return(df, 252)  # 1年收益率
        features['volatility'] = self._calculate_volatility(df)
        features['momentum_1m'] = self._calculate_momentum(df, 21)
        features['momentum_3m'] = self._calculate_momentum(df, 63)
        features['ma_ratio'] = self._calculate_ma_ratio(df, 20, 60)
        features['rsi'] = self._calculate_rsi(df)
        features['bollinger_position'] = self._calculate_bollinger_position(df)
        
        # 基本面特征（如果有）
        if fundamental_data and code in fundamental_data:
            fundamentals = fundamental_data[code]
            features.update(fundamentals)
        
        return features
    
    def _calculate_return(self, df: pd.DataFrame, days: int) -> float:
        """计算收益率"""
        if len(df) < days + 1:
            return 0.0
        recent_price = df['close'].tail(days).iloc[0]
        current_price = df['close'].iloc[-1]
        return (current_price - recent_price) / recent_price
    
    def _calculate_volatility(self, df: pd.DataFrame) -> float:
        """计算波动率"""
        returns = df['close'].pct_change().dropna()
        return np.std(returns) if len(returns) > 0 else 0.0
    
    def _calculate_momentum(self, df: pd.DataFrame, days: int) -> float:
        """计算动量指标"""
        if len(df) < days + 1:
            return 0.0
        recent_price = df['close'].tail(days).iloc[0]
        current_price = df['close'].iloc[-1]
        return (current_price - recent_price) / recent_price
    
    def _calculate_ma_ratio(self, df: pd.DataFrame, short_ma: int, long_ma: int) -> float:
        """计算均线比率"""
        if len(df) < long_ma:
            return 1.0
        
        short_ma_price = df['close'].tail(short_ma).mean()
        long_ma_price = df['close'].tail(long_ma).mean()
        
        return short_ma_price / long_ma_price if long_ma_price > 0 else 1.0
    
    def _calculate_rsi(self, df: pd.DataFrame, period: int = 14) -> float:
        """计算RSI指标"""
        if len(df) < period + 1:
            return 50.0
        
        returns = df['close'].pct_change().dropna()
        gains = returns[returns > 0].tail(period)
        losses = -returns[returns < 0].tail(period)
        
        avg_gain = gains.mean() if len(gains) > 0 else 0
        avg_loss = losses.mean() if len(losses) > 0 else 0
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
    
    def _calculate_bollinger_position(self, df: pd.DataFrame, period: int = 20) -> float:
        """计算布林带位置"""
        if len(df) < period:
            return 0.5
        
        prices = df['close'].tail(period)
        ma = prices.mean()
        std = prices.std()
        
        if std == 0:
            return 0.5
        
        current_price = df['close'].iloc[-1]
        upper = ma + 2 * std
        lower = ma - 2 * std
        
        return (current_price - lower) / (upper - lower)
    
    def _calculate_composite_score(self, factor_scores: Dict) -> float:
        """计算综合因子得分"""
        # 权重分配
        weights = {
            'value': 0.20,
            'quality': 0.25,
            'momentum': 0.20,
            'growth': 0.20,
            'safety': 0.15
        }
        
        composite_score = 0.0
        for factor_name, score in factor_scores.items():
            if factor_name in weights:
                composite_score += score * weights[factor_name]
        
        return composite_score
    
    def _rank_stocks(self, evaluation_results: Dict) -> Dict:
        """对股票进行排名"""
        # 按综合得分排序
        sorted_stocks = sorted(evaluation_results.items(), 
                              key=lambda x: x[1]['composite_score'], 
                              reverse=True)
        
        # 分配排名
        for rank, (code, result) in enumerate(sorted_stocks):
            result['rank'] = rank + 1
        
        return evaluation_results
    
    def _log_evaluation_results(self, evaluation_results: Dict):
        """记录评估结果"""
        logger.info("因子评估结果TOP10:")
        sorted_results = sorted(evaluation_results.items(), 
                              key=lambda x: x[1]['composite_score'], 
                              reverse=True)
        
        for i, (code, result) in enumerate(sorted_results[:10]):
            logger.info(f"  {i+1}. {code}: 综合得分={result['composite_score']:.4f}")
            for factor_name, score in result['factor_scores'].items():
                logger.info(f"     {factor_name}: {score:.4f}")
    
    def select_portfolio(self, evaluation_results: Dict, top_n: int = 12) -> Dict:
        """
        选择投资组合
        
        Args:
            evaluation_results: 因子评估结果
            top_n: 选择前N只股票
            
        Returns:
            投资组合配置
        """
        logger.info(f"选择前{top_n}只股票构建投资组合...")
        
        # 按排名排序
        sorted_results = sorted(evaluation_results.items(), 
                              key=lambda x: x[1]['rank'])
        
        # 选择前N只股票
        selected_stocks = sorted_results[:top_n]
        
        # 计算等权重
        weight_per_stock = 1.0 / top_n
        portfolio_weights = {}
        
        for code, result in selected_stocks:
            portfolio_weights[code] = weight_per_stock
        
        # 加入现金
        portfolio_weights['CASH'] = 0.35
        
        logger.info("投资组合配置:")
        for code, weight in portfolio_weights.items():
            logger.info(f"  {code}: {weight:.4f} ({weight*100:.2f}%)")
        
        return {
            'selected_stocks': selected_stocks,
            'portfolio_weights': portfolio_weights,
            'composite_scores': {code: result['composite_score'] 
                              for code, result in evaluation_results.items()}
        }


class ValueFactor:
    """价值因子"""
    
    def calculate_score(self, features: Dict) -> float:
        """计算价值因子得分"""
        score = 0.0
        
        # P/E比率（倒数）
        pe_ratio = features.get('pe_ratio', 15)
        if pe_ratio > 0:
            score += 1.0 / pe_ratio
        
        # P/B比率（倒数）
        pb_ratio = features.get('pb_ratio', 1.5)
        if pb_ratio > 0:
            score += 1.0 / pb_ratio
        
        # 股息收益率
        dividend_yield = features.get('dividend_yield', 0.02)
        score += dividend_yield
        
        # 市盈率相对历史水平
        pe_relative = features.get('pe_relative', 1.0)
        score += (2.0 - pe_relative) * 0.5
        
        # 标准化到0-1
        return self._normalize_score(score, 0, 2)
    
    def _normalize_score(self, score: float, min_val: float, max_val: float) -> float:
        """标准化得分"""
        return max(0.0, min(1.0, (score - min_val) / (max_val - min_val)))


class QualityFactor:
    """质量因子"""
    
    def calculate_score(self, features: Dict) -> float:
        """计算质量因子得分"""
        score = 0.0
        
        # ROE
        roe = features.get('roe', 0.1)
        score += roe
        
        # 毛利率
        gross_margin = features.get('gross_margin', 0.3)
        score += gross_margin
        
        # 净利率
        net_margin = features.get('net_margin', 0.1)
        score += net_margin
        
        # 负债率（倒数）
        debt_ratio = features.get('debt_ratio', 0.5)
        if debt_ratio > 0:
            score += (1.0 - debt_ratio) * 0.5
        
        # 经营现金流/净利润
        cash_flow_ratio = features.get('cash_flow_ratio', 1.0)
        score += cash_flow_ratio * 0.3
        
        return self._normalize_score(score, 0, 2)
    
    def _normalize_score(self, score: float, min_val: float, max_val: float) -> float:
        """标准化得分"""
        return max(0.0, min(1.0, (score - min_val) / (max_val - min_val)))


class MomentumFactor:
    """动量因子"""
    
    def calculate_score(self, features: Dict) -> float:
        """计算动量因子得分"""
        score = 0.0
        
        # 1个月收益率
        returns_1m = features.get('returns_1m', 0.0)
        score += max(0, returns_1m) * 5
        
        # 3个月收益率
        returns_3m = features.get('returns_3m', 0.0)
        score += max(0, returns_3m) * 2
        
        # 6个月收益率
        returns_6m = features.get('returns_6m', 0.0)
        score += max(0, returns_6m) * 1
        
        # RSI
        rsi = features.get('rsi', 50.0)
        if rsi > 50:
            score += (rsi - 50) / 50 * 0.5
        
        # 均线比率
        ma_ratio = features.get('ma_ratio', 1.0)
        if ma_ratio > 1:
            score += (ma_ratio - 1) * 2
        
        return self._normalize_score(score, 0, 5)
    
    def _normalize_score(self, score: float, min_val: float, max_val: float) -> float:
        """标准化得分"""
        return max(0.0, min(1.0, (score - min_val) / (max_val - min_val)))


class GrowthFactor:
    """增长因子"""
    
    def calculate_score(self, features: Dict) -> float:
        """计算增长因子得分"""
        score = 0.0
        
        # 收入增长率
        revenue_growth = features.get('revenue_growth', 0.1)
        score += revenue_growth * 2
        
        # 利润增长率
        profit_growth = features.get('profit_growth', 0.1)
        score += profit_growth * 3
        
        # EPS增长率
        eps_growth = features.get('eps_growth', 0.1)
        score += eps_growth * 2
        
        # 营收连续增长季度数
        growth_quarters = features.get('growth_quarters', 4)
        score += growth_quarters * 0.1
        
        return self._normalize_score(score, 0, 2)
    
    def _normalize_score(self, score: float, min_val: float, max_val: float) -> float:
        """标准化得分"""
        return max(0.0, min(1.0, (score - min_val) / (max_val - min_val)))


class SafetyFactor:
    """安全因子"""
    
    def calculate_score(self, features: Dict) -> float:
        """计算安全因子得分"""
        score = 0.0
        
        # 波动率（倒数）
        volatility = features.get('volatility', 0.2)
        if volatility > 0:
            score += (1.0 / volatility) * 0.1
        
        # 最大回撤（倒数）
        max_drawdown = features.get('max_drawdown', 0.2)
        if max_drawdown > 0:
            score += (1.0 - max_drawdown) * 2
        
        # 布林带位置
        bollinger_position = features.get('bollinger_position', 0.5)
        if 0.3 <= bollinger_position <= 0.7:
            score += 0.5
        
        # 股息连续增长年数
        dividend_growth_years = features.get('dividend_growth_years', 5)
        score += dividend_growth_years * 0.1
        
        # Z-score财务健康度
        z_score = features.get('z_score', 3.0)
        score += min(z_score / 5.0, 1.0)
        
        return self._normalize_score(score, 0, 2)
    
    def _normalize_score(self, score: float, min_val: float, max_val: float) -> float:
        """标准化得分"""
        return max(0.0, min(1.0, (score - min_val) / (max_val - min_val)))


def main():
    """主函数"""
    try:
        # 加载配置
        base_dir = os.path.dirname(os.path.abspath(__file__))
        settings_path = os.path.join(base_dir, 'config', 'settings.yaml')
        portfolio_path = os.path.join(base_dir, 'config', 'portfolio.yaml')
        
        with open(settings_path, 'r', encoding='utf-8') as f:
            settings = yaml.safe_load(f)
        with open(portfolio_path, 'r', encoding='utf-8') as f:
            portfolio = yaml.safe_load(f)
        
        # 创建五因子模型
        model = FiveFactorModel(portfolio, settings)
        
        logger.info("五维因子模型初始化完成")
        logger.info("由于Python环境限制，实际数据计算需要在正常环境中运行")
        
        return 0
        
    except Exception as e:
        logger.error(f"错误: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())