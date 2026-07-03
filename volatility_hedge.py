# -*- coding: utf-8 -*-
"""
波动率对冲策略 - 第二层保护
基于市场波动率监控和期权策略构建的波动率保护

功能特点：
1. VIX相关工具监控
2. 动态波动率目标管理
3. 期权策略构建（跨式/宽跨式/蝶式）
4. 波动率交易机会识别

作者：量化策略系统 v5.10优化版
"""

import os
import sys
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import logging

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

try:
    from utils.logging_manager import get_logger
    logger = get_logger('volatility_hedge')
except ImportError:
    import logging
    logger = logging.getLogger('volatility_hedge')


class VolatilityHedge:
    """波动率对冲策略 - 第二层保护"""
    
    def __init__(self, 
                 initial_capital: float = 3000000,  # 300万对冲资金
                 target_volatility: float = 0.12,   # 目标波动率12%
                 volatility_threshold: float = 0.05,  # 波动率调整阈值5%
                 max_volatility: float = 0.30,      # 最大波动率30%
                 vix_threshold_high: float = 25.0,   # VIX高阈值
                 vix_threshold_low: float = 12.0,    # VIX低阈值
                 option_allocation: float = 0.70,    # 期权资金占比70%
                 future_allocation: float = 0.30     # 期货资金占比30%
                ):
        """
        初始化波动率对冲策略
        
        Args:
            initial_capital: 初始资金
            target_volatility: 目标波动率
            volatility_threshold: 波动率调整阈值
            max_volatility: 最大波动率
            vix_threshold_high: VIX高阈值
            vix_threshold_low: VIX低阈值
            option_allocation: 期权资金占比
            future_allocation: 期货资金占比
        """
        self.initial_capital = initial_capital
        self.total_capital = initial_capital
        
        # 波动率参数
        self.target_volatility = target_volatility
        self.volatility_threshold = volatility_threshold
        self.max_volatility = max_volatility
        self.vix_threshold_high = vix_threshold_high
        self.vix_threshold_low = vix_threshold_low
        
        # 资金配置
        self.option_allocation = option_allocation
        self.future_allocation = future_allocation
        
        # 风险限制
        self.risk_limits = {
            'max_position_size': 0.20,  # 单个期权头寸最大20%
            'max_spread_width': 0.15,   # 价差最大15%
            'max Vega': 5000,           # 最大Vega风险
            'max Theta': -1000,         # 最大Theta风险
            'max Delta': 0.30,          # 最大Delta风险
        }
        
        # 波动率历史
        self.volatility_history = []
        self.vix_history = []
        self.option_positions = []
        self.future_positions = []
        
        # 策略状态
        self.current_strategy = 'neutral'  # neutral, long_vol, short_vol
        self.performance_history = []
        
        # 期权合约规格
        self.option_specs = {
            'index_options': {
                'multiplier': 100,  # 合约乘数
                'margin_ratio': 0.12,  # 保证金比例
                'tick_size': 0.1,  # 最小变动单位
                'contract_month': ['当月', '下月', '下季', '隔季']
            },
            'etf_options': {
                'multiplier': 10000,  # 合约乘数
                'margin_ratio': 0.15,  # 保证金比例
                'tick_size': 0.001,  # 最小变动单位
            }
        }
        
        logger.info(f"波动率对冲策略初始化完成")
        logger.info(f"初始资金: {initial_capital:,.0f}元")
        logger.info(f"目标波动率: {target_volatility:.1%}")
        logger.info(f"期权资金占比: {option_allocation:.1%}")
        
    def calculate_volatility(self, 
                           returns: pd.Series, 
                           method: str = 'historical',
                           window: int = 30) -> float:
        """
        计算波动率
        
        Args:
            returns: 收益率序列
            method: 计算方法
            window: 时间窗口
            
        Returns:
            波动率值
        """
        if len(returns) < 20:
            return 0.15  # 默认值
            
        if method == 'historical':
            # 历史波动率
            vol = returns.std() * np.sqrt(252)  # 年化
        elif method == 'ewma':
            # 指数加权移动平均
            vol = returns.ewm(span=window).std().iloc[-1] * np.sqrt(252)
        elif method == 'garch':
            # GARCH模型（简化版）
            vol = returns.std() * np.sqrt(252)  # 简化处理
        else:
            vol = returns.std() * np.sqrt(252)
            
        return float(vol)
        
    def calculate_iv_index(self, option_prices: Dict) -> float:
        """
        计算隐含波动率指数
        
        Args:
            option_prices: 期权价格数据
            
        Returns:
            隐含波动率指数
        """
        if not option_prices:
            return 15.0  # 默认值
            
        # 简化的IV计算
        call_ivs = []
        put_ivs = []
        
        for strike, price in option_prices.items():
            if strike > 3000:  # 看涨期权
                call_ivs.append(price * 100)  # 简化计算
            else:  # 看跌期权
                put_ivs.append(price * 100)  # 简化计算
                
        if call_ivs and put_ivs:
            avg_call_iv = np.mean(call_ivs)
            avg_put_iv = np.mean(put_ivs)
            return float((avg_call_iv + avg_put_iv) / 2)
        else:
            return 15.0
            
    def determine_volatility_regime(self, 
                                  current_volatility: float,
                                  vix_value: float,
                                  iv_index: float) -> str:
        """
        确定波动率环境
        
        Args:
            current_volatility: 当前波动率
            vix_value: VIX指数
            iv_index: 隐含波动率指数
            
        Returns:
            波动率环境：low_vol, normal_vol, high_vol, extreme_vol
        """
        avg_vol = (current_volatility + vix_value/100 + iv_index/100) / 3
        
        if avg_vol < 0.10:
            return 'low_vol'
        elif avg_vol < 0.20:
            return 'normal_vol'
        elif avg_vol < 0.30:
            return 'high_vol'
        else:
            return 'extreme_vol'
            
    def build_options_strategy(self, 
                              market_regime: str,
                              volatility_level: float,
                              portfolio_value: float) -> Dict:
        """
        构建期权策略
        
        Args:
            market_regime: 市场环境
            volatility_level: 波动率水平
            portfolio_value: 组合价值
            
        Returns:
            期权策略配置
        """
        option_capital = portfolio_value * self.option_allocation
        
        if market_regime == 'low_vol':
            # 低波动率环境：买入跨式策略
            strategy = {
                'name': '买入跨式',
                'type': 'long_straddle',
                'direction': 'long_vol',
                'rationale': '预期波动率上升',
                'capital_allocation': option_capital * 0.8,
                'risk_level': 'medium'
            }
            
            # 选择执行价
            atm_strike = 3000  # 平价期权
            strategy['strikes'] = [atm_strike - 100, atm_strike, atm_strike + 100]
            strategy['positions'] = [
                {'option_type': 'call', 'strike': atm_strike + 100, 'quantity': int(option_capital * 0.4 / (100 * 50))},
                {'option_type': 'put', 'strike': atm_strike - 100, 'quantity': int(option_capital * 0.4 / (100 * 50))}
            ]
            
        elif market_regime == 'normal_vol':
            # 正常波动率环境：卖出宽跨式
            strategy = {
                'name': '卖出宽跨式',
                'type': 'short_strangle',
                'direction': 'short_vol',
                'rationale': '预期波动率下降',
                'capital_allocation': option_capital * 0.6,
                'risk_level': 'high'
            }
            
            # 选择执行价
            otm_strike = 3000  # 价外期权
            strategy['strikes'] = [otm_strike - 200, otm_strike + 200]
            strategy['positions'] = [
                {'option_type': 'call', 'strike': otm_strike + 200, 'quantity': -int(option_capital * 0.3 / (100 * 30))},
                {'option_type': 'put', 'strike': otm_strike - 200, 'quantity': -int(option_capital * 0.3 / (100 * 30))}
            ]
            
        elif market_regime == 'high_vol':
            # 高波动率环境：买入蝶式
            strategy = {
                'name': '买入蝶式',
                'type': 'long_butterfly',
                'direction': 'neutral',
                'rationale': '预期波动率回归均值',
                'capital_allocation': option_capital * 0.7,
                'risk_level': 'low'
            }
            
            # 选择执行价
            atm_strike = 3000
            strategy['strikes'] = [atm_strike - 100, atm_strike, atm_strike + 100]
            strategy['positions'] = [
                {'option_type': 'call', 'strike': atm_strike - 100, 'quantity': int(option_capital * 0.3 / (100 * 40))},
                {'option_type': 'call', 'strike': atm_strike, 'quantity': -int(option_capital * 0.6 / (100 * 45))},
                {'option_type': 'call', 'strike': atm_strike + 100, 'quantity': int(option_capital * 0.3 / (100 * 50))}
            ]
            
        else:  # extreme_vol
            # 极端波动率环境：买入保护性看跌
            strategy = {
                'name': '买入保护性看跌',
                'type': 'protective_put',
                'direction': 'protective',
                'rationale': '保护组合价值',
                'capital_allocation': option_capital * 0.9,
                'risk_level': 'low'
            }
            
            # 选择执行价
            atm_strike = 3000
            strategy['strike'] = atm_strike
            strategy['positions'] = [
                {'option_type': 'put', 'strike': atm_strike, 'quantity': int(portfolio_value / (100 * 45))}
            ]
            
        return strategy
        
    def build_future_hedge(self, 
                          market_regime: str,
                          portfolio_value: float) -> Dict:
        """
        构建期货对冲
        
        Args:
            market_regime: 市场环境
            portfolio_value: 组合价值
            
        Returns:
            期货对冲配置
        """
        future_capital = portfolio_value * self.future_allocation
        
        if market_regime in ['high_vol', 'extreme_vol']:
            # 高波动率环境：增加对冲
            hedge_ratio = min(0.4, future_capital / (3000 * 300))
        elif market_regime == 'normal_vol':
            # 正常波动率环境：适度对冲
            hedge_ratio = min(0.2, future_capital / (3000 * 300))
        else:
            # 低波动率环境：减少对冲
            hedge_ratio = min(0.1, future_capital / (3000 * 300))
            
        return {
            'name': '股指期货对冲',
            'type': 'future_hedge',
            'direction': 'short' if hedge_ratio > 0 else 'neutral',
            'quantity': int(hedge_ratio),
            'capital_allocation': future_capital,
            'risk_level': 'medium'
        }
        
    def execute_volatility_hedge(self, 
                               portfolio_value: float,
                               market_returns: pd.Series,
                               vix_value: float,
                               option_prices: Dict = None,
                               iv_index: float = 15.0) -> Dict:
        """
        执行波动率对冲
        
        Args:
            portfolio_value: 组合价值
            market_returns: 市场收益率
            vix_value: VIX指数
            option_prices: 期权价格数据
            iv_index: 隐含波动率指数
            
        Returns:
            波动率对冲结果
        """
        # 计算当前波动率
        current_volatility = self.calculate_volatility(market_returns)
        
        # 计算隐含波动率
        if option_prices:
            iv_index = self.calculate_iv_index(option_prices)
        
        # 确定波动率环境
        volatility_regime = self.determine_volatility_regime(
            current_volatility, vix_value, iv_index
        )
        
        # 构建期权策略
        options_strategy = self.build_options_strategy(
            volatility_regime, current_volatility, portfolio_value
        )
        
        # 构建期货对冲
        future_strategy = self.build_future_hedge(
            volatility_regime, portfolio_value
        )
        
        # 计算策略风险指标
        strategy_risk = self.calculate_strategy_risk(
            options_strategy, future_strategy
        )
        
        # 生成对冲指令
        hedge_instruction = {
            'timestamp': datetime.now().isoformat(),
            'volatility_regime': volatility_regime,
            'current_volatility': current_volatility,
            'target_volatility': self.target_volatility,
            'vix_value': vix_value,
            'iv_index': iv_index,
            'options_strategy': options_strategy,
            'future_strategy': future_strategy,
            'strategy_risk': strategy_risk,
            'current_strategy': self.current_strategy,
            'recommended_strategy': volatility_regime,
            'volatility_adjustment': self._calculate_volatility_adjustment(current_volatility)
        }
        
        # 更新策略状态
        self.current_strategy = volatility_regime
        
        # 记录历史
        self.volatility_history.append({
            'timestamp': hedge_instruction['timestamp'],
            'volatility': current_volatility,
            'vix': vix_value,
            'regime': volatility_regime
        })
        
        logger.info(f"波动率对冲执行完成 - 波动率环境: {volatility_regime}")
        logger.info(f"当前波动率: {current_volatility:.1%} | VIX: {vix_value:.1f}")
        logger.info(f"期权策略: {options_strategy['name']} | 期货策略: {future_strategy['name']}")
        
        return hedge_instruction
        
    def calculate_strategy_risk(self, options_strategy: Dict, future_strategy: Dict) -> Dict:
        """
        计算策略风险
        
        Args:
            options_strategy: 期权策略
            future_strategy: 期货策略
            
        Returns:
            风险指标
        """
        # 简化的风险计算
        delta_risk = 0.0
        vega_risk = 0.0
        theta_risk = 0.0
        
        # 期权风险计算
        for pos in options_strategy.get('positions', []):
            quantity = pos.get('quantity', 0)
            if pos['option_type'] == 'call':
                delta_risk += quantity * 0.5
                vega_risk += quantity * 10
                theta_risk += quantity * (-5)
            elif pos['option_type'] == 'put':
                delta_risk += quantity * (-0.5)
                vega_risk += quantity * 10
                theta_risk += quantity * (-5)
                
        # 期货风险计算
        future_qty = future_strategy.get('quantity', 0)
        delta_risk += future_qty * 1.0
        
        # 风险检查
        risk_check = {
            'delta_risk': abs(delta_risk),
            'vega_risk': abs(vega_risk),
            'theta_risk': abs(theta_risk),
            'delta_ok': abs(delta_risk) <= self.risk_limits['max Delta'],
            'vega_ok': abs(vega_risk) <= self.risk_limits['max Vega'],
            'theta_ok': abs(theta_risk) <= self.risk_limits['max Theta']
        }
        
        return risk_check
        
    def _calculate_volatility_adjustment(self, current_volatility: float) -> float:
        """
        计算波动率调整幅度
        
        Args:
            current_volatility: 当前波动率
            
        Returns:
            调整幅度
        """
        volatility_gap = current_volatility - self.target_volatility
        
        if abs(volatility_gap) < self.volatility_threshold:
            return 0.0
        else:
            return np.sign(volatility_gap) * min(abs(volatility_gap), 0.1)
            
    def monitor_volatility_risk(self, portfolio_value: float) -> Dict:
        """
        监控波动率风险
        
        Args:
            portfolio_value: 组合价值
            
        Returns:
            风险监控结果
        """
        if not self.volatility_history:
            return {'risk_level': 'unknown'}
            
        latest_vol = self.volatility_history[-1]['volatility']
        
        # 风险检查
        violations = []
        
        if latest_vol > self.max_volatility:
            violations.append(f"波动率超限: {latest_vol:.1%} > {self.max_volatility:.1%}")
            
        # 生成风险报告
        risk_report = {
            'timestamp': datetime.now().isoformat(),
            'portfolio_value': portfolio_value,
            'current_volatility': latest_vol,
            'target_volatility': self.target_volatility,
            'max_volatility': self.max_volatility,
            'risk_level': 'high' if violations else 'medium' if latest_vol > 0.20 else 'low',
            'violations': violations
        }
        
        if violations:
            logger.warning(f"波动率风险违规: {violations}")
        else:
            logger.info("波动率风险检查通过")
            
        return risk_report
        
    def generate_volatility_report(self) -> str:
        """
        生成波动率对冲策略报告
        
        Returns:
            波动率策略报告
        """
        if not self.volatility_history:
            return "暂无波动率历史数据"
            
        # 统计信息
        vol_data = [v['volatility'] for v in self.volatility_history]
        vix_data = [v['vix'] for v in self.volatility_history]
        
        avg_vol = np.mean(vol_data)
        max_vol = np.max(vol_data)
        min_vol = np.min(vol_data)
        
        avg_vix = np.mean(vix_data)
        max_vix = np.max(vix_data)
        min_vix = np.min(vix_data)
        
        # 策略统计
        strategy_counts = {}
        for v in self.volatility_history:
            regime = v['regime']
            strategy_counts[regime] = strategy_counts.get(regime, 0) + 1
            
        report = f"""
=== 波动率对冲策略报告 ===

基本信息:
- 初始资金: {self.initial_capital:,.0f}元
- 目标波动率: {self.target_volatility:.1%}
- 期权资金占比: {self.option_allocation:.1%}
- 期货资金占比: {self.future_allocation:.1%}

波动率统计:
- 平均波动率: {avg_vol:.1%}
- 最高波动率: {max_vol:.1%}
- 最低波动率: {min_vol:.1%}
- 波动率范围: {max_vol - min_vol:.1%}

VIX统计:
- 平均VIX: {avg_vix:.1f}
- 最高VIX: {max_vix:.1f}
- 最低VIX: {min_vix:.1f}

环境分布:
"""
        
        for regime, count in strategy_counts.items():
            percentage = count / len(self.volatility_history) * 100
            report += f"- {regime}: {count}天 ({percentage:.1f}%)\n"
            
        report += f"""
当前策略:
- 主导策略: {self.current_strategy}
- 期权策略: {self.option_positions[-1]['name'] if self.option_positions else '无'}
- 期货策略: {self.future_positions[-1]['name'] if self.future_positions else '无'}

风险控制:
- 最大波动率限制: {self.max_volatility:.1%}
- Delta风险限制: {self.risk_limits['max Delta']:.1%}
- Vega风险限制: {self.risk_limits['max Vega']:,}
- Theta风险限制: {self.risk_limits['max Theta']:,}

优化建议:
"""
        
        # 根据波动率环境提供建议
        if self.current_strategy == 'extreme_vol':
            report += "- 当前处于极端波动环境，建议保持高对冲比例\n"
            report += "- 关注尾部风险，准备应对市场剧烈波动\n"
        elif self.current_strategy == 'high_vol':
            report += "- 当前处于高波动环境，建议适度降低风险暴露\n"
            report += "- 考虑增加保护性期权策略\n"
        elif self.current_strategy == 'normal_vol':
            report += "- 当前波动率环境正常，可维持现有策略\n"
            report += "- 密切关注波动率变化趋势\n"
        else:
            report += "- 当前波动率较低，可适度增加风险敞口\n"
            report += "- 关注波动率上升机会\n"
            
        return report
        
    def run_simulation(self):
        """运行波动率对冲策略模拟"""
        print("开始波动率对冲策略模拟...")
        print("=" * 60)
        
        # 模拟市场数据
        simulation_days = 60
        base_volatility = 0.15
        base_vix = 15.0
        
        # 生成模拟市场收益率
        np.random.seed(42)
        market_returns = pd.Series(np.random.normal(0.001, 0.02, simulation_days))
        
        for day in range(simulation_days):
            date = datetime.now() - timedelta(days=simulation_days - day)
            
            # 生成模拟数据
            # 模拟波动率聚类效应
            if day < 20:
                # 低波动期
                volatility = np.random.normal(0.10, 0.02)
                vix = np.random.normal(12.0, 2.0)
            elif day < 40:
                # 高波动期
                volatility = np.random.normal(0.25, 0.05)
                vix = np.random.normal(30.0, 5.0)
            else:
                # 正常波动期
                volatility = np.random.normal(0.15, 0.03)
                vix = np.random.normal(18.0, 3.0)
                
            volatility = np.clip(volatility, 0.05, 0.40)
            vix = np.clip(vix, 8.0, 50.0)
            
            # 模拟期权价格
            option_prices = {
                2900: np.random.uniform(0.02, 0.05),  # 看跌期权
                3000: np.random.uniform(0.01, 0.03),  # 平价期权
                3100: np.random.uniform(0.02, 0.05),  # 看涨期权
            }
            
            # 组合价值（模拟）
            portfolio_value = self.total_capital * (1 + market_returns.iloc[day])
            
            # 执行波动率对冲
            hedge_result = self.execute_volatility_hedge(
                portfolio_value=portfolio_value,
                market_returns=market_returns.iloc[:day+1],
                vix_value=vix,
                option_prices=option_prices,
                iv_index=volatility * 100
            )
            
            # 监控风险
            risk_report = self.monitor_volatility_risk(portfolio_value)
            
            # 记录性能
            performance_record = {
                'date': date.isoformat(),
                'portfolio_value': portfolio_value,
                'volatility': volatility,
                'vix': vix,
                'volatility_regime': hedge_result['volatility_regime'],
                'strategy_risk': hedge_result['strategy_risk'],
                'risk_level': risk_report['risk_level']
            }
            self.performance_history.append(performance_record)
            
            # 每10天输出一次报告
            if day % 10 == 9:
                print(f"\n第{day+1}天状态:")
                print(f"  波动率环境: {hedge_result['volatility_regime']}")
                print(f"  波动率: {volatility:.1%} | VIX: {vix:.1f}")
                print(f"  期权策略: {hedge_result['options_strategy']['name']}")
                print(f"  风险等级: {risk_report['risk_level']}")
        
        # 生成最终报告
        print("\n" + "=" * 60)
        print("模拟完成，生成最终报告:")
        print(self.generate_volatility_report())


def main():
    """主函数"""
    print("波动率对冲策略启动")
    print("=" * 60)
    
    # 创建波动率对冲策略实例
    hedge_strategy = VolatilityHedge(
        initial_capital=3000000,
        target_volatility=0.12,
        volatility_threshold=0.05
    )
    
    # 运行模拟
    hedge_strategy.run_simulation()
    
    print("\n波动率对冲策略模拟完成")
    print("=" * 60)


if __name__ == "__main__":
    main()