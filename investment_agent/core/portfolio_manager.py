"""
投资组合管理器
管理300万资金的组合配置、持仓追踪、再平衡建议
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import numpy as np
import pandas as pd

from ..config.config import PORTFOLIO_CONFIG, CAPITAL_CONFIG, RISK_CONTROL_CONFIG, AssetCategory

@dataclass
class Position:
    """持仓信息"""
    symbol: str
    name: str
    category: AssetCategory
    current_shares: float          # 当前持仓数量
    current_price: float           # 当前价格
    current_value: float           # 当前市值
    cost_basis: float              # 成本价格
    cost_value: float              # 成本市值
    unrealized_pnl: float          # 浮动盈亏
    unrealized_pnl_percent: float  # 浮动盈亏百分比
    weight_in_portfolio: float     # 在组合中的权重
    last_updated: datetime         # 最后更新时间

@dataclass
class Portfolio:
    """投资组合整体信息"""
    total_value: float             # 总市值
    cash_balance: float            # 现金余额
    stock_value: float             # 股票市值
    futures_value: float           # 期货期权市值
    total_pnl: float               # 总盈亏
    total_pnl_percent: float       # 总盈亏百分比
    positions: Dict[str, Position] # 持仓字典
    last_updated: datetime         # 最后更新时间
    total_capital: float           # 总资本

class PortfolioManager:
    """投资组合管理器"""
    
    def __init__(self, total_capital: float = CAPITAL_CONFIG['total_capital']):
        """
        初始化组合管理器
        
        Args:
            total_capital: 总资金，默认300万
        """
        self.total_capital = total_capital
        self.stock_capital = CAPITAL_CONFIG['stock_capital']  # 200万股票
        self.futures_capital = CAPITAL_CONFIG['futures_capital']  # 100万期货期权
        self.cash_balance = total_capital
        
        # 初始化空组合
        self.portfolio = Portfolio(
            total_value=total_capital,
            cash_balance=total_capital,
            stock_value=0.0,
            futures_value=0.0,
            total_pnl=0.0,
            total_pnl_percent=0.0,
            positions={},
            last_updated=datetime.now(),
            total_capital=total_capital
        )
        
        # 初始建仓计划
        self.initial_positions = self._calculate_initial_positions()
    
    def _calculate_initial_positions(self) -> Dict[str, float]:
        """
        计算初始建仓计划
        
        Returns:
            建仓金额字典 {symbol: amount}
        """
        positions = {}
        for symbol, config in PORTFOLIO_CONFIG.items():
            if symbol != 'CASH':  # 现金不计入建仓
                positions[symbol] = config.amount * 10000  # 转换为元
        return positions
    
    def get_initial_positions_summary(self) -> pd.DataFrame:
        """
        获取初始建仓摘要
        
        Returns:
            建仓摘要DataFrame
        """
        data = []
        for symbol, config in PORTFOLIO_CONFIG.items():
            if symbol == 'CASH':
                continue
            
            data.append({
                '代码': symbol,
                '名称': config.name,
                '类别': config.category.value,
                '权重': f"{config.weight:.1%}",
                '建仓金额(万)': config.amount,
                '止损线': f"{config.stop_loss:.1%}",
                '投资逻辑': config.investment_logic
            })
        
        df = pd.DataFrame(data)
        df = df.sort_values(['类别', '代码'])
        return df
    
    def create_position(self, symbol: str, shares: float, price: float) -> Position:
        """
        创建新持仓
        
        Args:
            symbol: 股票代码
            shares: 持仓数量
            price: 成交价格
            
        Returns:
            持仓对象
        """
        if symbol not in PORTFOLIO_CONFIG:
            raise ValueError(f"未知标的: {symbol}")
        
        config = PORTFOLIO_CONFIG[symbol]
        current_value = shares * price
        cost_value = shares * price
        
        position = Position(
            symbol=symbol,
            name=config.name,
            category=config.category,
            current_shares=shares,
            current_price=price,
            current_value=current_value,
            cost_basis=price,
            cost_value=cost_value,
            unrealized_pnl=0.0,
            unrealized_pnl_percent=0.0,
            weight_in_portfolio=current_value / self.portfolio.total_value if self.portfolio.total_value > 0 else 0,
            last_updated=datetime.now()
        )
        
        return position
    
    def add_position(self, symbol: str, shares: float, price: float):
        """
        添加持仓
        
        Args:
            symbol: 股票代码
            shares: 买入数量
            price: 买入价格
        """
        # 计算交易金额
        trade_value = shares * price
        
        # 检查资金是否足够
        if trade_value > self.cash_balance:
            raise ValueError(f"资金不足: 需要{trade_value:,.2f}，现金余额{self.cash_balance:,.2f}")
        
        # 更新现金
        self.cash_balance -= trade_value
        
        if symbol in self.portfolio.positions:
            # 已有持仓，更新
            existing_pos = self.portfolio.positions[symbol]
            total_shares = existing_pos.current_shares + shares
            total_cost = existing_pos.cost_value + trade_value
            
            # 更新持仓
            existing_pos.current_shares = total_shares
            existing_pos.cost_basis = total_cost / total_shares
            existing_pos.cost_value = total_cost
            existing_pos.current_price = price
            existing_pos.current_value = total_shares * price
            existing_pos.last_updated = datetime.now()
        else:
            # 新建持仓
            self.portfolio.positions[symbol] = self.create_position(symbol, shares, price)
        
        # 更新组合信息
        self._update_portfolio_stats()
    
    def remove_position(self, symbol: str, shares: float, price: float):
        """
        减少持仓
        
        Args:
            symbol: 股票代码
            shares: 卖出数量
            price: 卖出价格
        """
        if symbol not in self.portfolio.positions:
            raise ValueError(f"不存在持仓: {symbol}")
        
        position = self.portfolio.positions[symbol]
        
        if shares > position.current_shares:
            raise ValueError(f"持仓不足: 持有{position.current_shares}股，尝试卖出{shares}股")
        
        # 计算交易金额
        trade_value = shares * price
        
        # 更新现金
        self.cash_balance += trade_value
        
        # 计算盈亏
        sell_cost = shares * position.cost_basis
        realized_pnl = trade_value - sell_cost
        
        if shares == position.current_shares:
            # 全部卖出，删除持仓
            del self.portfolio.positions[symbol]
        else:
            # 部分卖出，更新持仓
            position.current_shares -= shares
            position.cost_value -= sell_cost
            position.current_price = price
            position.current_value = position.current_shares * price
            position.last_updated = datetime.now()
        
        # 更新组合信息
        self._update_portfolio_stats()
    
    def update_position_price(self, symbol: str, current_price: float):
        """
        更新持仓价格
        
        Args:
            symbol: 股票代码
            current_price: 当前价格
        """
        if symbol not in self.portfolio.positions:
            return
        
        position = self.portfolio.positions[symbol]
        position.current_price = current_price
        position.current_value = position.current_shares * current_price
        
        # 计算浮动盈亏
        position.unrealized_pnl = position.current_value - position.cost_value
        position.unrealized_pnl_percent = position.unrealized_pnl / position.cost_value if position.cost_value > 0 else 0
        position.last_updated = datetime.now()
        
        # 更新组合信息
        self._update_portfolio_stats()
    
    def _update_portfolio_stats(self):
        """更新组合统计信息"""
        total_stock_value = sum(pos.current_value for pos in self.portfolio.positions.values())
        total_value = total_stock_value + self.cash_balance
        
        # 计算总盈亏
        total_cost = sum(pos.cost_value for pos in self.portfolio.positions.values())
        total_pnl = (total_value - self.total_capital)
        total_pnl_percent = total_pnl / self.total_capital if self.total_capital > 0 else 0
        
        # 更新持仓权重
        for symbol, position in self.portfolio.positions.items():
            position.weight_in_portfolio = position.current_value / total_value if total_value > 0 else 0
        
        # 更新组合
        self.portfolio.total_value = total_value
        self.portfolio.cash_balance = self.cash_balance
        self.portfolio.stock_value = total_stock_value
        self.portfolio.futures_value = 0.0  # 暂未实现期货期权
        self.portfolio.total_pnl = total_pnl
        self.portfolio.total_pnl_percent = total_pnl_percent
        self.portfolio.last_updated = datetime.now()
    
    def get_portfolio_summary(self) -> pd.DataFrame:
        """
        获取组合摘要
        
        Returns:
            组合摘要DataFrame
        """
        data = []
        for symbol, position in self.portfolio.positions.items():
            data.append({
                '代码': symbol,
                '名称': position.name,
                '类别': position.category.value,
                '持仓数量': position.current_shares,
                '当前价格': position.current_price,
                '当前市值': position.current_value,
                '成本价格': position.cost_basis,
                '成本市值': position.cost_value,
                '浮动盈亏': position.unrealized_pnl,
                '浮动盈亏%': f"{position.unrealized_pnl_percent:.2%}",
                '组合权重': f"{position.weight_in_portfolio:.2%}",
                '最后更新': position.last_updated.strftime('%Y-%m-%d %H:%M:%S')
            })
        
        df = pd.DataFrame(data)
        
        # 添加总计行
        total_row = {
            '代码': '总计',
            '名称': '-',
            '类别': '-',
            '持仓数量': '-',
            '当前价格': '-',
            '当前市值': self.portfolio.stock_value,
            '成本价格': '-',
            '成本市值': sum(pos.cost_value for pos in self.portfolio.positions.values()),
            '浮动盈亏': self.portfolio.total_pnl,
            '浮动盈亏%': f"{self.portfolio.total_pnl_percent:.2%}",
            '组合权重': f"{self.portfolio.stock_value / self.portfolio.total_value:.2%}",
            '最后更新': self.portfolio.last_updated.strftime('%Y-%m-%d %H:%M:%S')
        }
        
        df = pd.concat([df, pd.DataFrame([total_row])], ignore_index=True)
        return df
    
    def get_current_weights(self) -> Dict[str, float]:
        """
        获取当前权重
        
        Returns:
            权重字典 {symbol: weight}
        """
        weights = {}
        total_value = self.portfolio.total_value
        
        if total_value > 0:
            for symbol, position in self.portfolio.positions.items():
                weights[symbol] = position.current_value / total_value
            
            # 添加现金权重
            weights['CASH'] = self.cash_balance / total_value
        
        return weights
    
    def get_target_weights(self) -> Dict[str, float]:
        """
        获取目标权重（来自配置）
        
        Returns:
            目标权重字典 {symbol: weight}
        """
        weights = {}
        for symbol, config in PORTFOLIO_CONFIG.items():
            weights[symbol] = config.weight
        return weights
    
    def calculate_rebalance_needed(self) -> Dict[str, Tuple[float, float, float]]:
        """
        计算再平衡需求
        
        Returns:
            再平衡需求字典 {symbol: (current_weight, target_weight, difference)}
        """
        current_weights = self.get_current_weights()
        target_weights = self.get_target_weights()
        
        rebalance_info = {}
        for symbol in set(list(current_weights.keys()) + list(target_weights.keys())):
            current = current_weights.get(symbol, 0.0)
            target = target_weights.get(symbol, 0.0)
            difference = target - current
            
            if abs(difference) > 0.01:  # 超过1%的偏差才调整
                rebalance_info[symbol] = (current, target, difference)
        
        return rebalance_info
    
    def get_risk_status(self) -> Dict[str, any]:
        """
        获取风险状态
        
        Returns:
            风险状态字典
        """
        risk_status = {
            'portfolio_drawdown': self.portfolio.total_pnl_percent,
            'individual_stops': [],
            'risk_level': 'LOW',
            'actions': []
        }
        
        # 检查个股止损
        for symbol, position in self.portfolio.positions.items():
            if symbol in PORTFOLIO_CONFIG:
                stop_loss = PORTFOLIO_CONFIG[symbol].stop_loss
                current_pnl = position.unrealized_pnl_percent
                
                if current_pnl < stop_loss:
                    risk_status['individual_stops'].append({
                        'symbol': symbol,
                        'name': position.name,
                        'current_pnl': f"{current_pnl:.2%}",
                        'stop_loss': f"{stop_loss:.2%}",
                        'action': 'SELL_ALL' if abs(current_pnl) > 0.10 else 'REDUCE_HALF'
                    })
        
        # 确定风险等级
        if risk_status['portfolio_drawdown'] < -0.15:
            risk_status['risk_level'] = 'CRITICAL'
        elif risk_status['portfolio_drawdown'] < -0.10:
            risk_status['risk_level'] = 'HIGH'
        elif risk_status['portfolio_drawdown'] < -0.05:
            risk_status['risk_level'] = 'MEDIUM'
        
        return risk_status