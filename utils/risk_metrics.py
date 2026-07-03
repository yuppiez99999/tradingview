# -*- coding: utf-8 -*-
"""
风险指标计算工具

功能：
- VaR计算
- ES计算
- 最大回撤计算
- 夏普比率计算
- 波动率计算
- 相关性分析
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Union
import logging

from logger import get_logger

logger = get_logger('risk_metrics')


def calculate_var(returns: np.ndarray, confidence_level: float = 0.95, 
                 method: str = 'historical') -> float:
    """
    计算风险价值(VaR)
    
    Args:
        returns: 收益率数组
        confidence_level: 置信水平
        method: 计算方法 ('historical', 'parametric', 'monte_carlo')
    
    Returns:
        VaR值
    """
    try:
        if len(returns) == 0:
            return 0.0
        
        returns = np.array(returns)
        returns = returns[~np.isnan(returns)]  # 移除NaN值
        
        if len(returns) == 0:
            return 0.0
        
        if method == 'historical':
            # 历史模拟法
            var = np.percentile(returns, (1 - confidence_level) * 100)
        
        elif method == 'parametric':
            # 参数法（假设正态分布）
            mean = np.mean(returns)
            std = np.std(returns)
            from scipy.stats import norm
            var = mean + std * norm.ppf(1 - confidence_level)
        
        elif method == 'monte_carlo':
            # 蒙特卡洛模拟法
            mean = np.mean(returns)
            std = np.std(returns)
            simulations = 10000
            simulated_returns = np.random.normal(mean, std, simulations)
            var = np.percentile(simulated_returns, (1 - confidence_level) * 100)
        
        else:
            raise ValueError(f"不支持的VaR计算方法: {method}")
        
        logger.debug(f"VaR计算完成: 方法={method}, 置信水平={confidence_level}, VaR={var:.4f}")
        return var
    
    except Exception as e:
        logger.error(f"VaR计算失败: {e}")
        return 0.0


def calculate_es(returns: np.ndarray, confidence_level: float = 0.95) -> float:
    """
    计算预期短缺(ES)
    
    Args:
        returns: 收益率数组
        confidence_level: 置信水平
    
    Returns:
        ES值
    """
    try:
        if len(returns) == 0:
            return 0.0
        
        returns = np.array(returns)
        returns = returns[~np.isnan(returns)]  # 移除NaN值
        
        if len(returns) == 0:
            return 0.0
        
        # 计算VaR
        var = calculate_var(returns, confidence_level)
        
        # 计算ES（超过VaR的平均损失）
        tail_returns = returns[returns <= var]
        if len(tail_returns) > 0:
            es = np.mean(tail_returns)
        else:
            es = var
        
        logger.debug(f"ES计算完成: 置信水平={confidence_level}, ES={es:.4f}")
        return es
    
    except Exception as e:
        logger.error(f"ES计算失败: {e}")
        return 0.0


def calculate_max_drawdown(prices: np.ndarray) -> Tuple[float, int, int]:
    """
    计算最大回撤
    
    Args:
        prices: 价格数组
    
    Returns:
        (最大回撤, 回撤开始索引, 回撤结束索引)
    """
    try:
        if len(prices) == 0:
            return 0.0, 0, 0
        
        prices = np.array(prices)
        prices = prices[~np.isnan(prices)]  # 移除NaN值
        
        if len(prices) == 0:
            return 0.0, 0, 0
        
        # 计算累计最高价
        peak = np.maximum.accumulate(prices)
        
        # 计算回撤
        drawdown = (peak - prices) / peak
        
        # 找到最大回撤
        max_dd = np.max(drawdown)
        max_dd_idx = np.argmax(drawdown)
        
        # 找到回撤开始和结束
        peak_idx = np.argmax(prices[:max_dd_idx+1])
        trough_idx = max_dd_idx
        
        logger.debug(f"最大回撤计算完成: 最大回撤={max_dd:.4f}, 开始={peak_idx}, 结束={trough_idx}")
        return max_dd, peak_idx, trough_idx
    
    except Exception as e:
        logger.error(f"最大回撤计算失败: {e}")
        return 0.0, 0, 0


def calculate_sharpe_ratio(returns: np.ndarray, risk_free_rate: float = 0.02) -> float:
    """
    计算夏普比率
    
    Args:
        returns: 收益率数组
        risk_free_rate: 无风险利率
    
    Returns:
        夏普比率
    """
    try:
        if len(returns) == 0:
            return 0.0
        
        returns = np.array(returns)
        returns = returns[~np.isnan(returns)]  # 移除NaN值
        
        if len(returns) == 0:
            return 0.0
        
        # 计算超额收益
        excess_returns = returns - risk_free_rate / 252  # 日化无风险利率
        
        # 计算年化超额收益和波动率
        annual_excess_return = np.mean(excess_returns) * 252
        annual_volatility = np.std(excess_returns) * np.sqrt(252)
        
        # 计算夏普比率
        if annual_volatility > 0:
            sharpe_ratio = annual_excess_return / annual_volatility
        else:
            sharpe_ratio = 0.0
        
        logger.debug(f"夏普比率计算完成: {sharpe_ratio:.4f}")
        return sharpe_ratio
    
    except Exception as e:
        logger.error(f"夏普比率计算失败: {e}")
        return 0.0


def calculate_sortino_ratio(returns: np.ndarray, risk_free_rate: float = 0.02) -> float:
    """
    计算索提诺比率
    
    Args:
        returns: 收益率数组
        risk_free_rate: 无风险利率
    
    Returns:
        索提诺比率
    """
    try:
        if len(returns) == 0:
            return 0.0
        
        returns = np.array(returns)
        returns = returns[~np.isnan(returns)]  # 移除NaN值
        
        if len(returns) == 0:
            return 0.0
        
        # 计算超额收益
        excess_returns = returns - risk_free_rate / 252
        
        # 计算下行标准差
        downside_returns = np.minimum(excess_returns, 0)
        downside_volatility = np.std(downside_returns) * np.sqrt(252)
        
        # 计算索提诺比率
        annual_excess_return = np.mean(excess_returns) * 252
        
        if downside_volatility > 0:
            sortino_ratio = annual_excess_return / downside_volatility
        else:
            sortino_ratio = 0.0
        
        logger.debug(f"索提诺比率计算完成: {sortino_ratio:.4f}")
        return sortino_ratio
    
    except Exception as e:
        logger.error(f"索提诺比率计算失败: {e}")
        return 0.0


def calculate_calmar_ratio(returns: np.ndarray, prices: np.ndarray) -> float:
    """
    计算卡玛比率
    
    Args:
        returns: 收益率数组
        prices: 价格数组
    
    Returns:
        卡玛比率
    """
    try:
        if len(returns) == 0 or len(prices) == 0:
            return 0.0
        
        # 计算年化收益
        annual_return = np.mean(returns) * 252
        
        # 计算最大回撤
        max_dd, _, _ = calculate_max_drawdown(prices)
        
        # 计算卡玛比率
        if max_dd > 0:
            calmar_ratio = annual_return / max_dd
        else:
            calmar_ratio = float('inf') if annual_return > 0 else 0.0
        
        logger.debug(f"卡玛比率计算完成: {calmar_ratio:.4f}")
        return calmar_ratio
    
    except Exception as e:
        logger.error(f"卡玛比率计算失败: {e}")
        return 0.0


def calculate_volatility(returns: np.ndarray, period: int = 252) -> float:
    """
    计算波动率
    
    Args:
        returns: 收益率数组
        period: 年化周期
    
    Returns:
        年化波动率
    """
    try:
        if len(returns) == 0:
            return 0.0
        
        returns = np.array(returns)
        returns = returns[~np.isnan(returns)]  # 移除NaN值
        
        if len(returns) == 0:
            return 0.0
        
        # 计算标准差并年化
        volatility = np.std(returns) * np.sqrt(period)
        
        logger.debug(f"波动率计算完成: {volatility:.4f}")
        return volatility
    
    except Exception as e:
        logger.error(f"波动率计算失败: {e}")
        return 0.0


def calculate_beta(returns: np.ndarray, market_returns: np.ndarray) -> float:
    """
    计算Beta系数
    
    Args:
        returns: 资产收益率数组
        market_returns: 市场收益率数组
    
    Returns:
        Beta系数
    """
    try:
        if len(returns) == 0 or len(market_returns) == 0:
            return 1.0
        
        # 确保长度一致
        min_length = min(len(returns), len(market_returns))
        returns = returns[-min_length:]
        market_returns = market_returns[-min_length:]
        
        # 移除NaN值
        mask = ~(np.isnan(returns) | np.isnan(market_returns))
        returns = returns[mask]
        market_returns = market_returns[mask]
        
        if len(returns) < 2:
            return 1.0
        
        # 计算协方差和方差
        covariance = np.cov(returns, market_returns)[0, 1]
        market_variance = np.var(market_returns)
        
        if market_variance > 0:
            beta = covariance / market_variance
        else:
            beta = 1.0
        
        logger.debug(f"Beta计算完成: {beta:.4f}")
        return beta
    
    except Exception as e:
        logger.error(f"Beta计算失败: {e}")
        return 1.0


def calculate_alpha(returns: np.ndarray, market_returns: np.ndarray, 
                  risk_free_rate: float = 0.02) -> float:
    """
    计算Alpha系数
    
    Args:
        returns: 资产收益率数组
        market_returns: 市场收益率数组
        risk_free_rate: 无风险利率
    
    Returns:
        Alpha系数
    """
    try:
        if len(returns) == 0 or len(market_returns) == 0:
            return 0.0
        
        # 计算Beta
        beta = calculate_beta(returns, market_returns)
        
        # 计算超额收益
        excess_return = np.mean(returns) * 252 - risk_free_rate
        market_excess_return = np.mean(market_returns) * 252 - risk_free_rate
        
        # 计算Alpha
        alpha = excess_return - beta * market_excess_return
        
        logger.debug(f"Alpha计算完成: {alpha:.4f}")
        return alpha
    
    except Exception as e:
        logger.error(f"Alpha计算失败: {e}")
        return 0.0


def calculate_correlation(matrix: np.ndarray) -> np.ndarray:
    """
    计算相关系数矩阵
    
    Args:
        matrix: 收益率矩阵 (时间 x 资产)
    
    Returns:
        相关系数矩阵
    """
    try:
        if matrix.shape[0] < 2 or matrix.shape[1] < 2:
            return np.eye(matrix.shape[1])
        
        # 计算相关系数矩阵
        correlation_matrix = np.corrcoef(matrix, rowvar=False)
        
        logger.debug("相关系数矩阵计算完成")
        return correlation_matrix
    
    except Exception as e:
        logger.error(f"相关系数矩阵计算失败: {e}")
        return np.eye(matrix.shape[1])


def calculate_tracking_error(returns: np.ndarray, benchmark_returns: np.ndarray) -> float:
    """
    计算跟踪误差
    
    Args:
        returns: 组合收益率数组
        benchmark_returns: 基准收益率数组
    
    Returns:
        跟踪误差
    """
    try:
        if len(returns) == 0 or len(benchmark_returns) == 0:
            return 0.0
        
        # 确保长度一致
        min_length = min(len(returns), len(benchmark_returns))
        returns = returns[-min_length:]
        benchmark_returns = benchmark_returns[-min_length:]
        
        # 计算超额收益
        excess_returns = returns - benchmark_returns
        
        # 计算跟踪误差（年化）
        tracking_error = np.std(excess_returns) * np.sqrt(252)
        
        logger.debug(f"跟踪误差计算完成: {tracking_error:.4f}")
        return tracking_error
    
    except Exception as e:
        logger.error(f"跟踪误差计算失败: {e}")
        return 0.0


def calculate_information_ratio(returns: np.ndarray, benchmark_returns: np.ndarray) -> float:
    """
    计算信息比率
    
    Args:
        returns: 组合收益率数组
        benchmark_returns: 基准收益率数组
    
    Returns:
        信息比率
    """
    try:
        if len(returns) == 0 or len(benchmark_returns) == 0:
            return 0.0
        
        # 计算跟踪误差
        tracking_error = calculate_tracking_error(returns, benchmark_returns)
        
        # 计算超额收益
        excess_returns = returns - benchmark_returns
        excess_return = np.mean(excess_returns) * 252
        
        # 计算信息比率
        if tracking_error > 0:
            information_ratio = excess_return / tracking_error
        else:
            information_ratio = 0.0
        
        logger.debug(f"信息比率计算完成: {information_ratio:.4f}")
        return information_ratio
    
    except Exception as e:
        logger.error(f"信息比率计算失败: {e}")
        return 0.0


def calculate_win_rate(returns: np.ndarray) -> float:
    """
    计算胜率
    
    Args:
        returns: 收益率数组
    
    Returns:
        胜率
    """
    try:
        if len(returns) == 0:
            return 0.0
        
        returns = np.array(returns)
        returns = returns[~np.isnan(returns)]  # 移除NaN值
        
        if len(returns) == 0:
            return 0.0
        
        # 计算正收益比例
        win_rate = np.mean(returns > 0)
        
        logger.debug(f"胜率计算完成: {win_rate:.4f}")
        return win_rate
    
    except Exception as e:
        logger.error(f"胜率计算失败: {e}")
        return 0.0


def calculate_profit_factor(returns: np.ndarray) -> float:
    """
    计算利润因子
    
    Args:
        returns: 收益率数组
    
    Returns:
        利润因子
    """
    try:
        if len(returns) == 0:
            return 0.0
        
        returns = np.array(returns)
        returns = returns[~np.isnan(returns)]  # 移除NaN值
        
        if len(returns) == 0:
            return 0.0
        
        # 分离盈利和亏损
        profit_returns = returns[returns > 0]
        loss_returns = returns[returns <= 0]
        
        # 计算利润因子
        if len(loss_returns) == 0:
            profit_factor = float('inf')
        else:
            total_profit = np.sum(profit_returns)
            total_loss = np.sum(np.abs(loss_returns))
            profit_factor = total_profit / total_loss
        
        logger.debug(f"利润因子计算完成: {profit_factor:.4f}")
        return profit_factor
    
    except Exception as e:
        logger.error(f"利润因子计算失败: {e}")
        return 0.0


def calculate_performance_metrics(returns: np.ndarray, prices: np.ndarray = None,
                                benchmark_returns: np.ndarray = None,
                                risk_free_rate: float = 0.02) -> Dict:
    """
    计算完整的绩效指标
    
    Args:
        returns: 收益率数组
        prices: 价格数组
        benchmark_returns: 基准收益率数组
        risk_free_rate: 无风险利率
    
    Returns:
        绩效指标字典
    """
    try:
        metrics = {}
        
        # 基本指标
        metrics['total_return'] = np.sum(returns) if len(returns) > 0 else 0.0
        metrics['annual_return'] = np.mean(returns) * 252 if len(returns) > 0 else 0.0
        metrics['volatility'] = calculate_volatility(returns)
        metrics['sharpe_ratio'] = calculate_sharpe_ratio(returns, risk_free_rate)
        metrics['sortino_ratio'] = calculate_sortino_ratio(returns, risk_free_rate)
        metrics['win_rate'] = calculate_win_rate(returns)
        metrics['profit_factor'] = calculate_profit_factor(returns)
        
        # 风险指标
        metrics['var_95'] = calculate_var(returns, 0.95)
        metrics['var_99'] = calculate_var(returns, 0.99)
        metrics['es_95'] = calculate_es(returns, 0.95)
        metrics['es_99'] = calculate_es(returns, 0.99)
        
        # 回撤指标
        if prices is not None:
            metrics['max_drawdown'], metrics['dd_start'], metrics['dd_end'] = calculate_max_drawdown(prices)
            metrics['calmar_ratio'] = calculate_calmar_ratio(returns, prices)
        else:
            metrics['max_drawdown'] = 0.0
            metrics['dd_start'] = 0
            metrics['dd_end'] = 0
            metrics['calmar_ratio'] = 0.0
        
        # 相对指标
        if benchmark_returns is not None:
            metrics['beta'] = calculate_beta(returns, benchmark_returns)
            metrics['alpha'] = calculate_alpha(returns, benchmark_returns, risk_free_rate)
            metrics['tracking_error'] = calculate_tracking_error(returns, benchmark_returns)
            metrics['information_ratio'] = calculate_information_ratio(returns, benchmark_returns)
        else:
            metrics['beta'] = 1.0
            metrics['alpha'] = 0.0
            metrics['tracking_error'] = 0.0
            metrics['information_ratio'] = 0.0
        
        logger.info("绩效指标计算完成")
        return metrics
    
    except Exception as e:
        logger.error(f"绩效指标计算失败: {e}")
        return {}


if __name__ == "__main__":
    # 测试风险指标计算
    print("测试风险指标计算")
    
    # 生成测试数据
    np.random.seed(42)
    returns = np.random.normal(0.001, 0.02, 252)
    prices = 3000 * np.exp(np.cumsum(returns))
    benchmark_returns = np.random.normal(0.0005, 0.018, 252)
    
    # 计算各种指标
    print("VaR (95%):", calculate_var(returns, 0.95))
    print("ES (95%):", calculate_es(returns, 0.95))
    print("最大回撤:", calculate_max_drawdown(prices))
    print("夏普比率:", calculate_sharpe_ratio(returns))
    print("索提诺比率:", calculate_sortino_ratio(returns))
    print("Beta:", calculate_beta(returns, benchmark_returns))
    print("Alpha:", calculate_alpha(returns, benchmark_returns))
    print("跟踪误差:", calculate_tracking_error(returns, benchmark_returns))
    print("信息比率:", calculate_information_ratio(returns, benchmark_returns))
    print("胜率:", calculate_win_rate(returns))
    print("利润因子:", calculate_profit_factor(returns))
    
    # 计算完整绩效指标
    metrics = calculate_performance_metrics(returns, prices, benchmark_returns)
    print("\n完整绩效指标:")
    for key, value in metrics.items():
        print(f"  {key}: {value:.4f}")