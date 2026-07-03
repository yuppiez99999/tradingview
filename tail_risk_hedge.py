# -*- coding: utf-8 -*-
"""
尾部风险对冲策略 - 第三层保护
世界级对冲基金的终极防线，用于应对极端市场情况和尾部风险事件

策略特点：
- 基于VaR和极值理论的风险识别
- 动态尾部风险指标监控
- 多重尾部保护机制（期权组合、波动率对冲、流动性保护）
- 压力测试和情景分析
- 极端情况下的自动保护机制
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import warnings
warnings.filterwarnings('ignore')

try:
    from utils.logger import get_logger
    from utils.risk_metrics import calculate_var, calculate_es, calculate_max_drawdown
    from utils.data_provider import get_market_data
    from utils.options_pricing import black_scholes_price, calculate_option_greeks
    logger = get_logger('tail_risk_hedge')
except ImportError:
    import logging
    logger = logging.getLogger('tail_risk_hedge')

class TailRiskHedge:
    """
    尾部风险对冲策略 - 世界级对冲基金的终极保护
    
    该策略采用多层次尾部保护机制：
    1. 指数看跌期权保护 - 应对市场崩盘风险
    2. 波动率锥度对冲 - 应对波动率极端上升风险  
    3. 流动性危机保护 - 应对市场流动性枯竭风险
    4. 极值理论预警 - 基于历史极值的预警系统
    """
    
    def __init__(self, capital: float = 1000000):
        """
        初始化尾部风险对冲策略
        
        Args:
            capital: 用于尾部风险对冲的资本（默认100万）
        """
        self.capital = capital
        self.positions = []
        self.risk_metrics = {}
        self.tail_protection_level = 0.0
        self.market_regime = 'normal'
        
        # 尾部风险阈值配置
        self.risk_thresholds = {
            'var_95': 0.025,      # 95% VaR阈值
            'var_99': 0.050,      # 99% VaR阈值
            'es_95': 0.040,       # 95% ES阈值
            'es_99': 0.080,       # 99% ES阈值
            'max_drawdown': 0.15, # 最大回撤阈值
            'volatility_spike': 0.30, # 波动率激增阈值
            'liquidity_dry': 0.50,    # 流动性枯竭阈值
            'tail_event': 0.03    # 尾部事件阈值
        }
        
        # 尾部保护配置
        self.protection_config = {
            'max_protection_ratio': 0.30,    # 最大保护比例
            'min_option_hedge': 0.10,       # 最小期权对冲比例
            'volatility_buffer': 0.10,       # 波动率缓冲
            'liquidity_buffer': 0.15,       # 流动性缓冲
            'recovery_threshold': 0.02,      # 恢复阈值（尾部风险缓解后）
            'stress_test_freq': 'daily',     # 压力测试频率
            'early_warning_system': True     # 启用早期预警系统
        }
        
        # 极值理论参数
        self.extreme_value_params = {
            'block_size': 20,      # 块大小（交易天数）
            'threshold_percentile': 95,   # 阈值百分位数
            'return_period': 100,  # 返回期（天）
            'confidence_level': 0.99  # 置信水平
        }
        
        # 期权策略配置
        self.option_strategies = {
            'protective_put': {
                'moneyness': 0.95,      # 价外程度
                'duration': 'monthly',   # 期限
                'min_otm': 0.05,        # 最小价外程度
                'max_cost': 0.05        # 最大成本限制
            },
            'collar': {
                'put_moneyness': 0.95,  # 看跌行权价
                'call_moneyness': 1.05, # 看涨行权价
                'ratio': '1:1'          # 比例
            },
            'straddle_hedge': {
                'moneyness': 1.0,       # 平价
                'volatility_multiplier': 1.5,  # 波动率倍数
                'max_cost': 0.08        # 最大成本限制
            }
        }
        
        # 监控指标历史
        self.metrics_history = []
        self.alert_history = []
        
        logger.info(f"尾部风险对冲策略初始化完成")
        logger.info(f"保护资本: {capital:,.0f}元")
        logger.info(f"风险阈值: {self.risk_thresholds}")
    
    def analyze_market_regime(self, market_data: Dict) -> str:
        """
        分析市场状态 - 检测是否进入尾部风险区域
        
        Args:
            market_data: 市场数据字典
            
        Returns:
            市场状态：'normal', 'warning', 'crisis', 'recovery'
        """
        try:
            # 提取关键指标
            returns = market_data.get('returns', [])
            volatility = market_data.get('volatility', 0.0)
            liquidity = market_data.get('liquidity', 1.0)
            var_95 = market_data.get('var_95', 0.0)
            es_95 = market_data.get('es_95', 0.0)
            
            # 计算尾部风险指标
            tail_risk_score = self._calculate_tail_risk_score(
                returns, volatility, liquidity, var_95, es_95
            )
            
            # 确定市场状态
            if tail_risk_score > 0.8:
                regime = 'crisis'
            elif tail_risk_score > 0.6:
                regime = 'warning'
            elif tail_risk_score > 0.3:
                regime = 'recovery'
            else:
                regime = 'normal'
            
            self.market_regime = regime
            logger.info(f"市场状态分析: {regime} (尾部风险评分: {tail_risk_score:.3f})")
            
            return regime
            
        except Exception as e:
            logger.error(f"市场状态分析失败: {e}")
            return 'normal'
    
    def _calculate_tail_risk_score(self, returns: List[float], volatility: float, 
                                 liquidity: float, var_95: float, es_95: float) -> float:
        """
        计算尾部风险评分（0-1之间）
        
        Args:
            returns: 收益率序列
            volatility: 波动率
            liquidity: 流动性指标
            var_95: 95% VaR
            es_95: 95% ES
            
        Returns:
            尾部风险评分
        """
        # 归一化各项指标
        volatility_score = min(volatility / self.risk_thresholds['volatility_spike'], 1.0)
        liquidity_score = max(0, (1.0 - liquidity) / self.risk_thresholds['liquidity_dry'])
        var_score = min(var_95 / self.risk_thresholds['var_95'], 1.0)
        es_score = min(es_95 / self.risk_thresholds['es_95'], 1.0)
        
        # 计算极值指标
        extreme_score = self._calculate_extreme_value_risk(returns)
        
        # 综合评分（加权平均）
        weights = {
            'volatility': 0.25,
            'liquidity': 0.20,
            'var': 0.20,
            'es': 0.25,
            'extreme': 0.10
        }
        
        tail_risk_score = (
            weights['volatility'] * volatility_score +
            weights['liquidity'] * liquidity_score +
            weights['var'] * var_score +
            weights['es'] * es_score +
            weights['extreme'] * extreme_score
        )
        
        return min(tail_risk_score, 1.0)
    
    def _calculate_extreme_value_risk(self, returns: List[float]) -> float:
        """
        使用极值理论计算风险
        
        Args:
            returns: 收益率序列
            
        Returns:
            极值风险评分
        """
        if len(returns) < self.extreme_value_params['block_size']:
            return 0.0
        
        try:
            returns_array = np.array(returns)
            
            # 计算块最大值
            n_blocks = len(returns_array) // self.extreme_value_params['block_size']
            block_maxima = []
            
            for i in range(n_blocks):
                start_idx = i * self.extreme_value_params['block_size']
                end_idx = start_idx + self.extreme_value_params['block_size']
                block_max = np.max(returns_array[start_idx:end_idx])
                block_maxima.append(block_max)
            
            if len(block_maxima) < 10:  # 需要足够的数据点
                return 0.0
            
            # 计算阈值
            threshold = np.percentile(block_maxima, 
                                    self.extreme_value_params['threshold_percentile'])
            
            # 计算超阈值数据点数量
            exceedances = np.sum(np.array(block_maxima) > threshold)
            
            # 极值风险评分
            risk_score = exceedances / len(block_maxima)
            
            return min(risk_score, 1.0)
            
        except Exception as e:
            logger.warning(f"极值理论计算失败: {e}")
            return 0.0
    
    def calculate_protection_needed(self, portfolio_value: float, 
                                 market_data: Dict) -> Dict:
        """
        计算需要的尾部保护
        
        Args:
            portfolio_value: 投资组合价值
            market_data: 市场数据
            
        Returns:
            保护需求字典
        """
        # 分析市场状态
        market_regime = self.analyze_market_regime(market_data)
        
        # 计算保护等级
        if market_regime == 'crisis':
            protection_ratio = self.protection_config['max_protection_ratio']
        elif market_regime == 'warning':
            protection_ratio = self.protection_config['max_protection_ratio'] * 0.7
        elif market_regime == 'recovery':
            protection_ratio = self.protection_config['max_protection_ratio'] * 0.3
        else:
            protection_ratio = 0.0
        
        # 动态调整基于VaR
        var_protection = min(market_data.get('var_95', 0) * 2, protection_ratio)
        
        # 计算期权对冲需求
        option_hedge = max(
            self.protection_config['min_option_hedge'],
            var_protection * 0.6
        )
        
        # 计算波动率对冲需求
        volatility_hedge = 0.0
        if market_data.get('volatility', 0) > self.risk_thresholds['volatility_spike'] * 0.8:
            volatility_hedge = min(
                protection_ratio * 0.4,
                (market_data['volatility'] - self.risk_thresholds['volatility_spike'] * 0.8) * 0.1
            )
        
        # 计算流动性保护需求
        liquidity_hedge = 0.0
        if market_data.get('liquidity', 1.0) < 0.5:
            liquidity_hedge = min(
                protection_ratio * 0.2,
                (1.0 - market_data['liquidity']) * 0.1
            )
        
        protection_needed = {
            'total_ratio': protection_ratio,
            'option_hedge': option_hedge,
            'volatility_hedge': volatility_hedge,
            'liquidity_hedge': liquidity_hedge,
            'market_regime': market_regime,
            'protection_capital': portfolio_value * protection_ratio,
            'confidence_level': self._calculate_protection_confidence(market_data)
        }
        
        self.tail_protection_level = protection_ratio
        logger.info(f"尾部保护需求计算完成: {protection_needed}")
        
        return protection_needed
    
    def _calculate_protection_confidence(self, market_data: Dict) -> float:
        """
        计算保护措施的置信度
        
        Args:
            market_data: 市场数据
            
        Returns:
            置信度评分（0-1）
        """
        # 基于多个因素计算置信度
        factors = []
        
        # 市场状态因子
        if market_data.get('volatility', 0) > self.risk_thresholds['volatility_spike']:
            factors.append(0.8)
        else:
            factors.append(1.0)
        
        # 流动性因子
        liquidity = market_data.get('liquidity', 1.0)
        factors.append(max(0.5, liquidity))
        
        # VaR因子
        var_ratio = market_data.get('var_95', 0) / self.risk_thresholds['var_95']
        factors.append(max(0.3, 1.0 - var_ratio))
        
        # 综合置信度
        confidence = np.mean(factors)
        
        return min(confidence, 1.0)
    
    def execute_protection_strategy(self, protection_needed: Dict, 
                                 market_data: Dict) -> List[Dict]:
        """
        执行尾部保护策略
        
        Args:
            protection_needed: 保护需求
            market_data: 市场数据
            
        Returns:
            交易指令列表
        """
        trades = []
        protection_capital = protection_needed['protection_capital']
        
        logger.info(f"开始执行尾部保护策略，保护资本: {protection_capital:,.0f}元")
        
        # 1. 执行期权保护策略
        option_trades = self._execute_option_protection(
            protection_needed['option_hedge'], protection_capital, market_data
        )
        trades.extend(option_trades)
        
        # 2. 执行波动率对冲策略
        volatility_trades = self._execute_volatility_hedge(
            protection_needed['volatility_hedge'], protection_capital, market_data
        )
        trades.extend(volatility_trades)
        
        # 3. 执行流动性保护策略
        liquidity_trades = self._execute_liquidity_protection(
            protection_needed['liquidity_hedge'], protection_capital, market_data
        )
        trades.extend(liquidity_trades)
        
        # 4. 执行压力测试
        if protection_needed['market_regime'] in ['crisis', 'warning']:
            stress_test_results = self._run_stress_test(market_data)
            logger.warning(f"压力测试结果: {stress_test_results}")
        
        # 记录执行结果
        self._record_protection_execution(protection_needed, trades)
        
        logger.info(f"尾部保护策略执行完成，生成 {len(trades)} 个交易指令")
        
        return trades
    
    def _execute_option_protection(self, option_ratio: float, protection_capital: float, 
                                market_data: Dict) -> List[Dict]:
        """
        执行期权保护策略
        
        Args:
            option_ratio: 期权对冲比例
            protection_capital: 保护资本
            market_data: 市场数据
            
        Returns:
            期权交易指令列表
        """
        trades = []
        
        if option_ratio <= 0:
            return trades
        
        # 计算期权保护资本
        option_capital = protection_capital * option_ratio
        
        # 选择期权策略
        if self.market_regime == 'crisis':
            # 危机时期：采用更激进的保护策略
            strategy = 'protective_put'
            moneyness = 0.90  # 更深的价外
        elif self.market_regime == 'warning':
            # 警告时期：平衡保护策略
            strategy = 'collar'
            moneyness = 0.95
        else:
            # 正常时期：最小保护策略
            strategy = 'protective_put'
            moneyness = 0.98
        
        # 计算期权数量
        underlying_price = market_data.get('index_price', 3000)
        option_premium = self._estimate_option_premium(strategy, moneyness, market_data)
        
        if option_premium <= 0:
            return trades
        
        # 计算需要保护的指数点位
        protection_units = option_capital / (underlying_price * 100)  # 假设每合约100股
        
        # 生成交易指令
        if strategy == 'protective_put':
            # 看跌期权保护
            strike_price = int(underlying_price * moneyness)
            quantity = int(protection_units)
            
            trade = {
                'symbol': f'沪深300认沽{strike_price}月',
                'quantity': quantity,
                'direction': 'buy',
                'type': 'option',
                'strategy': 'protective_put',
                'strike_price': strike_price,
                'premium': option_premium,
                'capital_required': quantity * option_premium * 100,
                'protection_ratio': option_ratio,
                'market_regime': self.market_regime
            }
            trades.append(trade)
            
        elif strategy == 'collar':
            # 领子策略
            put_strike = int(underlying_price * 0.95)
            call_strike = int(underlying_price * 1.05)
            quantity = int(protection_units)
            
            # 买入看跌期权
            put_trade = {
                'symbol': f'沪深300认沽{put_strike}月',
                'quantity': quantity,
                'direction': 'buy',
                'type': 'option',
                'strategy': 'collar_put',
                'strike_price': put_strike,
                'premium': option_premium,
                'capital_required': quantity * option_premium * 100,
                'protection_ratio': option_ratio * 0.6,
                'market_regime': self.market_regime
            }
            trades.append(put_trade)
            
            # 卖出看涨期权（融资）
            call_premium = self._estimate_option_premium('call', 1.05, market_data)
            call_trade = {
                'symbol': f'沪深300认购{call_strike}月',
                'quantity': -quantity,  # 卖出
                'direction': 'sell',
                'type': 'option',
                'strategy': 'collar_call',
                'strike_price': call_strike,
                'premium': call_premium,
                'capital_required': -quantity * call_premium * 100,
                'protection_ratio': option_ratio * 0.4,
                'market_regime': self.market_regime
            }
            trades.append(call_trade)
        
        logger.info(f"期权保护策略执行完成: {len(trades)} 个期权合约")
        
        return trades
    
    def _estimate_option_premium(self, strategy: str, moneyness: float, 
                               market_data: Dict) -> float:
        """
        估算期权权利金
        
        Args:
            strategy: 期权策略类型
            moneyness: 价外程度
            market_data: 市场数据
            
        Returns:
            估算的权利金
        """
        try:
            # 基础参数
            underlying_price = market_data.get('index_price', 3000)
            volatility = market_data.get('volatility', 0.2)
            risk_free_rate = 0.03
            time_to_expiry = 0.25  # 3个月
            
            # 计算行权价
            if strategy == 'put':
                strike_price = underlying_price * moneyness
            elif strategy == 'call':
                strike_price = underlying_price / moneyness
            else:
                strike_price = underlying_price
            
            # 简化的Black-Scholes定价
            d1 = (np.log(underlying_price / strike_price) + 
                  (risk_free_rate + 0.5 * volatility**2) * time_to_expiry) / \
                 (volatility * np.sqrt(time_to_expiry))
            
            d2 = d1 - volatility * np.sqrt(time_to_expiry)
            
            if strategy == 'call':
                premium = underlying_price * 0.5 * (1 + 0.5 * (d1 - 1)) - \
                         strike_price * np.exp(-risk_free_rate * time_to_expiry) * 0.5 * (1 + 0.5 * (d2 - 1))
            else:  # put
                premium = strike_price * np.exp(-risk_free_rate * time_to_expiry) * 0.5 * (1 + 0.5 * (1 - d2)) - \
                         underlying_price * 0.5 * (1 + 0.5 * (1 - d1))
            
            # 根据市场状态调整
            if self.market_regime == 'crisis':
                premium *= 1.5  # 危机时期期权溢价
            elif self.market_regime == 'warning':
                premium *= 1.2  # 警告时期期权溢价
            
            return max(premium, 0.001)  # 最小权利金
            
        except Exception as e:
            logger.warning(f"期权权利金估算失败: {e}")
            return 0.01  # 默认值
    
    def _execute_volatility_hedge(self, volatility_ratio: float, protection_capital: float, 
                                 market_data: Dict) -> List[Dict]:
        """
        执行波动率对冲策略
        
        Args:
            volatility_ratio: 波动率对冲比例
            protection_capital: 保护资本
            market_data: 市场数据
            
        Returns:
            波动率对冲交易指令列表
        """
        trades = []
        
        if volatility_ratio <= 0 or market_data.get('volatility', 0) < 0.2:
            return trades
        
        # 计算波动率对冲资本
        hedge_capital = protection_capital * volatility_ratio
        
        # VIX期货对冲
        vix_futures_price = market_data.get('vix_future_price', 20.0)
        if vix_futures_price > 0:
            # 计算需要卖出的VIX期货数量
            vix_contract_size = 1000  # VIX合约乘数
            quantity = int(hedge_capital / (vix_futures_price * vix_contract_size))
            
            trade = {
                'symbol': 'VIX期货',
                'quantity': -quantity,  # 卖出VIX期货
                'direction': 'sell',
                'type': 'future',
                'strategy': 'volatility_hedge',
                'strike_price': vix_futures_price,
                'capital_required': -quantity * vix_futures_price * vix_contract_size,
                'protection_ratio': volatility_ratio,
                'market_regime': self.market_regime
            }
            trades.append(trade)
        
        # 波动率互换对冲（简化）
        if market_data.get('volatility_swap_spread', 0) > 0.1:
            vol_swap_trade = {
                'symbol': '波动率互换',
                'quantity': -hedge_capital * 0.5,  # 卖出波动率
                'direction': 'sell',
                'type': 'derivative',
                'strategy': 'volatility_swap',
                'strike_price': market_data.get('volatility_swap_rate', 0.2),
                'capital_required': -hedge_capital * 0.5,
                'protection_ratio': volatility_ratio * 0.5,
                'market_regime': self.market_regime
            }
            trades.append(vol_swap_trade)
        
        logger.info(f"波动率对冲策略执行完成: {len(trades)} 个波动率产品")
        
        return trades
    
    def _execute_liquidity_protection(self, liquidity_ratio: float, protection_capital: float, 
                                    market_data: Dict) -> List[Dict]:
        """
        执行流动性保护策略
        
        Args:
            liquidity_ratio: 流动性对冲比例
            protection_capital: 保护资本
            market_data: 市场数据
            
        Returns:
            流动性保护交易指令列表
        """
        trades = []
        
        if liquidity_ratio <= 0 or market_data.get('liquidity', 1.0) > 0.7:
            return trades
        
        # 计算流动性保护资本
        liquidity_capital = protection_capital * liquidity_ratio
        
        # 货币市场工具保护
        if liquidity_capital > 100000:  # 最小规模要求
            money_market_trade = {
                'symbol': '货币基金',
                'quantity': liquidity_capital * 0.8,
                'direction': 'buy',
                'type': 'money_market',
                'strategy': 'liquidity_protection',
                'strike_price': 1.0,
                'capital_required': liquidity_capital * 0.8,
                'protection_ratio': liquidity_ratio * 0.8,
                'market_regime': self.market_regime
            }
            trades.append(money_market_trade)
        
        # 高流动性ETF保护
        if liquidity_capital > 200000:
            etf_trade = {
                'symbol': '货币ETF',
                'quantity': liquidity_capital * 0.2,
                'direction': 'buy',
                'type': 'etf',
                'strategy': 'liquidity_etf',
                'strike_price': 100.0,
                'capital_required': liquidity_capital * 0.2,
                'protection_ratio': liquidity_ratio * 0.2,
                'market_regime': self.market_regime
            }
            trades.append(etf_trade)
        
        logger.info(f"流动性保护策略执行完成: {len(trades)} 个流动性产品")
        
        return trades
    
    def _run_stress_test(self, market_data: Dict) -> Dict:
        """
        执行压力测试
        
        Args:
            market_data: 市场数据
            
        Returns:
            压力测试结果
        """
        try:
            # 历史情景分析
            stress_scenarios = {
                '2008_crash': {'market_return': -0.30, 'volatility': 0.50},
                '2015_china_crash': {'market_return': -0.25, 'volatility': 0.40},
                '2020_covid_crash': {'market_return': -0.20, 'volatility': 0.35},
                'volatility_spike': {'market_return': -0.15, 'volatility': 0.60}
            }
            
            # 模拟组合表现
            portfolio_value = 1000000  # 假设组合价值
            stress_results = {}
            
            for scenario_name, scenario_params in stress_scenarios.items():
                # 计算情景下的组合价值
                shock = scenario_params['market_return']
                new_value = portfolio_value * (1 + shock)
                
                # 评估对冲效果
                hedge_effectiveness = self._calculate_hedge_effectiveness(
                    shock, scenario_params['volatility'], market_data
                )
                
                stress_results[scenario_name] = {
                    'portfolio_value': new_value,
                    'loss': -shock * portfolio_value,
                    'hedge_effectiveness': hedge_effectiveness,
                    'protection_level': self.tail_protection_level
                }
            
            return stress_results
            
        except Exception as e:
            logger.error(f"压力测试失败: {e}")
            return {}
    
    def _calculate_hedge_effectiveness(self, market_shock: float, volatility: float, 
                                    market_data: Dict) -> float:
        """
        计算对冲有效性
        
        Args:
            market_shock: 市场冲击
            volatility: 波动率
            market_data: 市场数据
            
        Returns:
            对冲有效性评分（0-1）
        """
        # 基于对冲比例计算理论保护
        theoretical_protection = self.tail_protection_level * min(abs(market_shock), 0.3)
        
        # 考虑期权对冲效果
        if self.market_regime in ['crisis', 'warning']:
            option_effectiveness = 0.8  # 危机时期期权对冲效果好
        else:
            option_effectiveness = 0.6
        
        # 计算实际保护
        actual_protection = theoretical_protection * option_effectiveness
        
        # 对冲有效性评分
        if abs(market_shock) > 0.1:
            effectiveness = actual_protection / abs(market_shock)
        else:
            effectiveness = 1.0
        
        return min(effectiveness, 1.0)
    
    def _record_protection_execution(self, protection_needed: Dict, trades: List[Dict]):
        """
        记录保护策略执行结果
        
        Args:
            protection_needed: 保护需求
            trades: 交易指令列表
        """
        execution_record = {
            'timestamp': datetime.now().isoformat(),
            'market_regime': protection_needed['market_regime'],
            'protection_ratio': protection_needed['total_ratio'],
            'trades_count': len(trades),
            'trades': trades,
            'risk_metrics': self.risk_metrics
        }
        
        self.metrics_history.append(execution_record)
        
        # 更新风险指标
        self.risk_metrics.update({
            'last_execution': execution_record,
            'current_protection_level': self.tail_protection_level,
            'market_regime': self.market_regime,
            'total_protection_capital': sum(trade.get('capital_required', 0) for trade in trades)
        })
    
    def generate_protection_report(self) -> Dict:
        """
        生成尾部保护策略报告
        
        Returns:
            保护策略报告
        """
        # 计算历史统计数据
        if self.metrics_history:
            executions = self.metrics_history
            avg_protection_ratio = np.mean([e['protection_ratio'] for e in executions])
            max_protection_ratio = np.max([e['protection_ratio'] for e in executions])
            total_trades = np.sum([e['trades_count'] for e in executions])
        else:
            avg_protection_ratio = 0.0
            max_protection_ratio = 0.0
            total_trades = 0
        
        report = {
            'strategy_name': 'Tail Risk Hedge Strategy',
            'current_status': {
                'market_regime': self.market_regime,
                'protection_level': self.tail_protection_level,
                'protection_capital': self.capital * self.tail_protection_level,
                'positions_count': len(self.positions)
            },
            'historical_performance': {
                'executions_count': len(self.metrics_history),
                'average_protection_ratio': avg_protection_ratio,
                'maximum_protection_ratio': max_protection_ratio,
                'total_trades_generated': total_trades
            },
            'risk_metrics': self.risk_metrics,
            'current_positions': self.positions,
            'protection_thresholds': self.risk_thresholds,
            'configuration': self.protection_config,
            'last_update': datetime.now().isoformat()
        }
        
        logger.info(f"尾部保护策略报告生成完成")
        
        return report
    
    def monitor_early_warning(self, market_data: Dict) -> Dict:
        """
        早期预警系统监控
        
        Args:
            market_data: 市场数据
            
        Returns:
            预警信息
        """
        alerts = []
        
        # 检查VaR
        if market_data.get('var_95', 0) > self.risk_thresholds['var_95']:
            alerts.append({
                'type': 'VAR_WARNING',
                'severity': 'high' if market_data['var_95'] > self.risk_thresholds['var_99'] else 'medium',
                'message': f"VaR超限: {market_data['var_95']:.3f} > {self.risk_thresholds['var_95']:.3f}",
                'value': market_data['var_95']
            })
        
        # 检查ES
        if market_data.get('es_95', 0) > self.risk_thresholds['es_95']:
            alerts.append({
                'type': 'ES_WARNING',
                'severity': 'high',
                'message': f"ES超限: {market_data['es_95']:.3f} > {self.risk_thresholds['es_95']:.3f}",
                'value': market_data['es_95']
            })
        
        # 检查波动率
        if market_data.get('volatility', 0) > self.risk_thresholds['volatility_spike']:
            alerts.append({
                'type': 'VOLATILITY_SPIKE',
                'severity': 'high',
                'message': f"波动率激增: {market_data['volatility']:.3f} > {self.risk_thresholds['volatility_spike']:.3f}",
                'value': market_data['volatility']
            })
        
        # 检查流动性
        if market_data.get('liquidity', 1.0) < self.risk_thresholds['liquidity_dry']:
            alerts.append({
                'type': 'LIQUIDITY_DRY',
                'severity': 'critical',
                'message': f"流动性枯竭: {market_data['liquidity']:.3f} < {self.risk_thresholds['liquidity_dry']:.3f}",
                'value': market_data['liquidity']
            })
        
        # 检查最大回撤
        if market_data.get('max_drawdown', 0) > self.risk_thresholds['max_drawdown']:
            alerts.append({
                'type': 'MAX_DRAWDOWN',
                'severity': 'high',
                'message': f"最大回撤超限: {market_data['max_drawdown']:.3f} > {self.risk_thresholds['max_drawdown']:.3f}",
                'value': market_data['max_drawdown']
            })
        
        # 记录预警历史
        for alert in alerts:
            self.alert_history.append({
                **alert,
                'timestamp': datetime.now().isoformat(),
                'protection_level': self.tail_protection_level
            })
        
        if alerts:
            logger.warning(f"早期预警系统触发: {len(alerts)} 个预警")
        
        return {
            'alerts': alerts,
            'alert_count': len(alerts),
            'max_severity': max([a['severity'] for a in alerts], default='none'),
            'market_regime': self.market_regime
        }