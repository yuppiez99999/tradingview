"""
涨跌停检测与流动性检查模块 (v8.5升级)
=====================================
功能:
1. A股涨跌停板检测(10%/20%涨跌幅限制)
2. 流动性枯竭检测(成交量骤降、盘口深度不足)
3. 停牌股票过滤
4. 市场冲击成本估算
5. 调仓时机建议(开盘/收盘放量时段)

核心规则:
- 主板涨跌停: ±10%
- 创业板/科创板涨跌停: ±20%
- ST股票涨跌停: ±5%
- 流动性阈值: 日均成交额<1000万视为低流动性
- 冲击成本模型: Almgren-Chriss平方根模型
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class StockStatus:
    """股票状态数据"""
    symbol: str
    name: str
    is_suspended: bool  # 是否停牌
    is_limit_up: bool  # 是否涨停
    is_limit_down: bool  # 是否跌停
    daily_return: float  # 当日收益率
    limit_up_price: float  # 涨停价
    limit_down_price: float  # 跌停价
    change_pct_limit: float  # 是否触及涨跌停阈值(%)


@dataclass
class LiquidityMetrics:
    """流动性指标"""
    avg_daily_volume: float  # 日均成交量(股)
    avg_daily_turnover: float  # 日均成交额(元)
    current_volume: float  # 当前成交量
    current_turnover: float  # 当前成交额
    volume_ratio: float  # 量比(当前/平均)
    turnover_rate: float  # 换手率(%)
    bid_ask_spread: float  # 买卖价差
    market_depth: float  # 盘口深度(买一+卖一量)
    liquidity_score: float  # 流动性评分(0-100)


@dataclass
class ImpactCost:
    """市场冲击成本"""
    estimated_slippage_bps: float  # 预估滑点(bps)
    slippage_cny: float  # 滑点金额(元)
    execution_cost: float  # 执行总成本
    recommended_order_size: float  # 建议订单大小
    execution_days: int  # 建议执行天数


@dataclass
class TradeFeasibility:
    """交易可行性评估"""
    symbol: str
    can_trade: bool  # 是否可以交易
    trade_direction: str  # 'BUY', 'SELL', 'NONE'
    reason: str  # 原因说明
    max_order_size: float  # 最大订单大小(元)
    estimated_impact_cost: ImpactCost
    risk_level: str  # 'LOW', 'MEDIUM', 'HIGH', 'BLOCKED'


@dataclass
class LiquidityReport:
    """流动性报告"""
    timestamp: datetime
    total_stocks: int
    suspended_count: int  # 停牌数
    limit_up_count: int  # 涨停数
    limit_down_count: int  # 跌停数
    low_liquidity_count: int  # 低流动性数
    avg_liquidity_score: float  # 平均流动性评分
    blocked_trades: List[str]  # 被阻止的交易列表
    warnings: List[str] = field(default_factory=list)


class LimitUpDownDetector:
    """涨跌停检测器"""

    def __init__(self):
        # 主板涨跌停阈值±10%
        self.main_board_threshold = 0.10
        # 创业板/科创板涨跌停阈值±20%
        self.gem_board_threshold = 0.20
        # ST股票涨跌停阈值±5%
        self.st_threshold = 0.05

        logger.info("[LimitUpDownDetector] 初始化完成")

    def detect(self,
               symbol: str,
               current_price: float,
               prev_close: float,
               today_high: float,
               today_low: float,
               board_type: str = 'MAIN',  # 'MAIN', 'GEM', 'STAR', 'ST'
               is_suspended: bool = False) -> StockStatus:
        """
        检测股票涨跌停状态

        Args:
            symbol: 股票代码
            current_price: 当前价格
            prev_close: 前收盘价
            today_high: 今日最高价
            today_low: 今日最低价
            board_type: 板块类型
            is_suspended: 是否停牌

        Returns:
            StockStatus对象
        """
        if prev_close <= 0:
            return StockStatus(
                symbol=symbol,
                name="",
                is_suspended=False,
                is_limit_up=False,
                is_limit_down=False,
                daily_return=0.0,
                limit_up_price=0,
                limit_down_price=0,
                change_pct_limit=0.0
            )

        # 计算涨跌幅
        daily_return = (current_price - prev_close) / prev_close

        # 确定涨跌停阈值
        if board_type == 'GEM' or board_type == 'STAR':
            threshold = self.gem_board_threshold
        elif board_type == 'ST':
            threshold = self.st_threshold
        else:
            threshold = self.main_board_threshold

        # 计算涨跌停价格
        limit_up_price = prev_close * (1 + threshold)
        limit_down_price = prev_close * (1 - threshold)

        # 判断是否触及涨跌停(允许±0.01元误差)
        tolerance = 0.01
        is_limit_up = (today_high >= limit_up_price - tolerance and
                       today_low >= limit_up_price - tolerance)
        is_limit_down = (today_low <= limit_down_price + tolerance and
                         today_high <= limit_down_price + tolerance)

        return StockStatus(
            symbol=symbol,
            name="",
            is_suspended=is_suspended,
            is_limit_up=is_limit_up,
            is_limit_down=is_limit_down,
            daily_return=daily_return,
            limit_up_price=limit_up_price,
            limit_down_price=limit_down_price,
            change_pct_limit=abs(daily_return) / threshold
        )


class LiquidityChecker:
    """流动性检查器"""

    def __init__(self,
                 min_turnover: float = 10_000_000,  # 最低成交额1000万
                 min_volume: float = 1_000_000,  # 最低成交量100万股
                 min_liquidity_score: float = 30.0):  # 最低流动性评分30分
        self.min_turnover = min_turnover
        self.min_volume = min_volume
        self.min_liquidity_score = min_liquidity_score

        logger.info(f"[LiquidityChecker] 初始化完成 | "
                    f"Min Turnover={min_turnover:,.0f} | "
                    f"Min Volume={min_volume:,.0f}")

    def calculate_metrics(self,
                          current_volume: float,
                          avg_daily_volume: float,
                          current_turnover: float,
                          avg_daily_turnover: float,
                          bid_price: float,
                          ask_price: float,
                          bid_volume: float,
                          ask_volume: float,
                          total_shares: float) -> LiquidityMetrics:
        """
        计算流动性指标

        Args:
            current_volume: 当前成交量
            avg_daily_volume: 日均成交量(20日)
            current_turnover: 当前成交额
            avg_daily_turnover: 日均成交额(20日)
            bid_price: 买一价
            ask_price: 卖一价
            bid_volume: 买一量(股)
            ask_volume: 卖一量(股)
            total_shares: 总股本

        Returns:
            LiquidityMetrics对象
        """
        # 量比
        volume_ratio = current_volume / avg_daily_volume if avg_daily_volume > 0 else 0

        # 换手率
        turnover_rate = (current_volume / total_shares * 100) if total_shares > 0 else 0

        # 买卖价差(bps)
        mid_price = (bid_price + ask_price) / 2
        bid_ask_spread = ((ask_price - bid_price) / mid_price * 10000) if mid_price > 0 else 0

        # 盘口深度
        market_depth = bid_volume + ask_volume

        # 流动性评分(0-100)
        liquidity_score = self._calculate_liquidity_score(
            current_turnover, volume_ratio, turnover_rate, bid_ask_spread, market_depth
        )

        return LiquidityMetrics(
            avg_daily_volume=avg_daily_volume,
            avg_daily_turnover=avg_daily_turnover,
            current_volume=current_volume,
            current_turnover=current_turnover,
            volume_ratio=volume_ratio,
            turnover_rate=turnover_rate,
            bid_ask_spread=bid_ask_spread,
            market_depth=market_depth,
            liquidity_score=liquidity_score
        )

    def _calculate_liquidity_score(self,
                                   turnover: float,
                                   volume_ratio: float,
                                   turnover_rate: float,
                                   bid_ask_spread: float,
                                   market_depth: float) -> float:
        """
        计算流动性评分(0-100)

        评分维度:
        1. 成交额(40分): 日成交额越高得分越高
        2. 量比(20分): 量比越大得分越高
        3. 换手率(20分): 适度换手率得分高
        4. 买卖价差(10分): 价差越小得分越高
        5. 盘口深度(10分): 深度越大得分越高
        """
        score = 0.0

        # 1. 成交额评分(0-40分)
        if turnover >= 50_000_000:
            score += 40
        elif turnover >= 20_000_000:
            score += 30
        elif turnover >= 10_000_000:
            score += 20
        elif turnover >= 5_000_000:
            score += 10
        else:
            score += 5

        # 2. 量比评分(0-20分)
        if volume_ratio >= 2.0:
            score += 20
        elif volume_ratio >= 1.5:
            score += 15
        elif volume_ratio >= 1.0:
            score += 10
        else:
            score += 5

        # 3. 换手率评分(0-20分)
        if 1.0 <= turnover_rate <= 10.0:
            score += 20
        elif 0.5 <= turnover_rate <= 15.0:
            score += 15
        else:
            score += 10

        # 4. 买卖价差评分(0-10分,价差越小越好)
        if bid_ask_spread <= 5:
            score += 10
        elif bid_ask_spread <= 10:
            score += 7
        elif bid_ask_spread <= 20:
            score += 4
        else:
            score += 1

        # 5. 盘口深度评分(0-10分)
        if market_depth >= 1_000_000:
            score += 10
        elif market_depth >= 500_000:
            score += 7
        elif market_depth >= 100_000:
            score += 4
        else:
            score += 1

        return min(score, 100)

    def check_feasibility(self,
                          symbol: str,
                          order_value: float,
                          liquidity: LiquidityMetrics,
                          stock_status: StockStatus,
                          max_impact_pct: float = 0.005) -> TradeFeasibility:
        """
        检查交易可行性

        Args:
            symbol: 股票代码
            order_value: 订单金额(元)
            liquidity: 流动性指标
            stock_status: 股票状态
            max_impact_pct: 最大可接受冲击成本比例(0.5%)

        Returns:
            TradeFeasibility对象
        """
        # 1. 停牌检查
        if stock_status.is_suspended:
            return TradeFeasibility(
                symbol=symbol,
                can_trade=False,
                trade_direction='NONE',
                reason="股票停牌,无法交易",
                max_order_size=0,
                estimated_impact_cost=self._estimate_impact(0),
                risk_level='BLOCKED'
            )

        # 2. 涨跌停检查
        if stock_status.is_limit_up and stock_status.symbol.startswith(('6', '9')):
            # 涨停无法买入
            return TradeFeasibility(
                symbol=symbol,
                can_trade=False,
                trade_direction='SELL',
                reason="股票涨停,无法买入",
                max_order_size=0,
                estimated_impact_cost=self._estimate_impact(0),
                risk_level='BLOCKED'
            )

        if stock_status.is_limit_down:
            # 跌停无法卖出
            return TradeFeasibility(
                symbol=symbol,
                can_trade=False,
                trade_direction='BUY',
                reason="股票跌停,无法卖出",
                max_order_size=0,
                estimated_impact_cost=self._estimate_impact(0),
                risk_level='BLOCKED'
            )

        # 3. 流动性检查
        if liquidity.liquidity_score < self.min_liquidity_score:
            return TradeFeasibility(
                symbol=symbol,
                can_trade=False,
                trade_direction='NONE',
                reason=f"流动性不足(评分{liquidity.liquidity_score:.1f}<{self.min_liquidity_score})",
                max_order_size=0,
                estimated_impact_cost=self._estimate_impact(0),
                risk_level='HIGH'
            )

        # 4. 计算最大订单大小(不超过日均成交额的5%)
        max_order = liquidity.avg_daily_turnover * 0.05
        max_order_size = min(order_value, max_order)

        # 5. 估算冲击成本
        impact_cost = self._estimate_impact(max_order_size, order_value)

        # 6. 风险评估
        if impact_cost.estimated_slippage_bps > 30:
            risk_level = 'HIGH'
        elif impact_cost.estimated_slippage_bps > 10:
            risk_level = 'MEDIUM'
        else:
            risk_level = 'LOW'

        return TradeFeasibility(
            symbol=symbol,
            can_trade=True,
            trade_direction='BUY' if order_value > 0 else 'SELL',
            reason="流动性充足,可以交易",
            max_order_size=max_order_size,
            estimated_impact_cost=impact_cost,
            risk_level=risk_level
        )

    def _estimate_impact(self,
                         order_size: float,
                         market_size: float = 50_000_000) -> ImpactCost:
        """
        使用Almgren-Chriss平方根模型估算市场冲击成本

        公式: Slippage = k * sqrt(OrderSize / MarketSize)

        Args:
            order_size: 订单大小(元)
            market_size: 市场流动性(元)

        Returns:
            ImpactCost对象
        """
        if order_size <= 0 or market_size <= 0:
            return ImpactCost(
                estimated_slippage_bps=0,
                slippage_cny=0,
                execution_cost=0,
                recommended_order_size=market_size * 0.05,
                execution_days=1
            )

        # 平方根模型
        k_factor = 100  # 常数因子(bps)
        slippage_bps = k_factor * np.sqrt(order_size / market_size)
        slippage_cny = order_size * slippage_bps / 10000

        # 建议订单大小(不超过日均成交额的5%)
        recommended_size = market_size * 0.05

        # 建议执行天数
        if order_size > market_size * 0.1:
            execution_days = 3
        elif order_size > market_size * 0.05:
            execution_days = 2
        else:
            execution_days = 1

        return ImpactCost(
            estimated_slippage_bps=slippage_bps,
            slippage_cny=slippage_cny,
            execution_cost=order_size + slippage_cny,
            recommended_order_size=recommended_size,
            execution_days=execution_days
        )


class LiquidityMonitor:
    """流动性监控主控制器"""

    def __init__(self,
                 min_turnover: float = 10_000_000,
                 min_liquidity_score: float = 30.0):
        self.limit_detector = LimitUpDownDetector()
        self.liquidity_checker = LiquidityChecker(
            min_turnover=min_turnover,
            min_liquidity_score=min_liquidity_score
        )

        logger.info(f"[LiquidityMonitor] 初始化完成 | "
                    f"Min Turnover={min_turnover:,.0f} | "
                    f"Min Score={min_liquidity_score}")

    def scan_market(self,
                    stock_data: List[Dict]) -> LiquidityReport:
        """
        扫描全市场股票流动性

        Args:
            stock_data: 股票数据列表,每项包含:
                - symbol: 股票代码
                - current_price: 当前价格
                - prev_close: 前收盘价
                - today_high: 今日最高
                - today_low: 今日最低
                - volume: 成交量
                - turnover: 成交额
                - avg_daily_volume: 日均成交量
                - avg_daily_turnover: 日均成交额
                - bid_price: 买一价
                - ask_price: 卖一价
                - bid_volume: 买一量
                - ask_volume: 卖一量
                - total_shares: 总股本
                - board_type: 板块类型
                - is_suspended: 是否停牌

        Returns:
            LiquidityReport对象
        """
        blocked_trades = []
        warnings = []
        suspended_count = 0
        limit_up_count = 0
        limit_down_count = 0
        low_liquidity_count = 0
        liquidity_scores = []

        for stock in stock_data:
            symbol = stock.get('symbol', '')

            # 1. 涨跌停检测
            status = self.limit_detector.detect(
                symbol=symbol,
                current_price=stock.get('current_price', 0),
                prev_close=stock.get('prev_close', 0),
                today_high=stock.get('today_high', 0),
                today_low=stock.get('today_low', 0),
                board_type=stock.get('board_type', 'MAIN'),
                is_suspended=stock.get('is_suspended', False)
            )

            if status.is_suspended:
                suspended_count += 1
            if status.is_limit_up:
                limit_up_count += 1
            if status.is_limit_down:
                limit_down_count += 1

            # 2. 流动性指标计算
            liquidity = self.liquidity_checker.calculate_metrics(
                current_volume=stock.get('volume', 0),
                avg_daily_volume=stock.get('avg_daily_volume', 0),
                current_turnover=stock.get('turnover', 0),
                avg_daily_turnover=stock.get('avg_daily_turnover', 0),
                bid_price=stock.get('bid_price', 0),
                ask_price=stock.get('ask_price', 0),
                bid_volume=stock.get('bid_volume', 0),
                ask_volume=stock.get('ask_volume', 0),
                total_shares=stock.get('total_shares', 1)
            )

            liquidity_scores.append(liquidity.liquidity_score)

            if liquidity.liquidity_score < self.liquidity_checker.min_liquidity_score:
                low_liquidity_count += 1
                blocked_trades.append(symbol)

        # 生成警告
        if suspended_count > 0:
            warnings.append(f"[WARNING] {suspended_count}只股票停牌")
        if limit_up_count > 100:
            warnings.append(f"[WARNING] {limit_up_count}只股票涨停,市场情绪过热")
        if limit_down_count > 50:
            warnings.append(f"[WARNING] {limit_down_count}只股票跌停,市场恐慌")
        if low_liquidity_count > 500:
            warnings.append(f"[WARNING] {low_liquidity_count}只股票流动性不足")

        return LiquidityReport(
            timestamp=datetime.now(),
            total_stocks=len(stock_data),
            suspended_count=suspended_count,
            limit_up_count=limit_up_count,
            limit_down_count=limit_down_count,
            low_liquidity_count=low_liquidity_count,
            avg_liquidity_score=np.mean(liquidity_scores) if liquidity_scores else 0,
            blocked_trades=blocked_trades,
            warnings=warnings
        )


if __name__ == "__main__":
    # 测试示例
    print("涨跌停检测与流动性检查模块测试\n")
    print("="*60)

    monitor = LiquidityMonitor()

    # 模拟股票数据
    test_stocks = [
        {
            'symbol': '600519.SH',
            'current_price': 1800.00,
            'prev_close': 1730.00,
            'today_high': 1805.00,
            'today_low': 1780.00,
            'volume': 5000,
            'turnover': 9_000_000,
            'avg_daily_volume': 50000,
            'avg_daily_turnover': 100_000_000,
            'bid_price': 1799.00,
            'ask_price': 1801.00,
            'bid_volume': 100,
            'ask_volume': 150,
            'total_shares': 25_000_000_000,
            'board_type': 'MAIN',
            'is_suspended': False
        },
        {
            'symbol': '300750.SZ',
            'current_price': 50.00,
            'prev_close': 40.00,
            'today_high': 50.50,
            'today_low': 49.00,
            'volume': 1_000_000,
            'turnover': 50_000_000,
            'avg_daily_volume': 500_000,
            'avg_daily_turnover': 25_000_000,
            'bid_price': 49.90,
            'ask_price': 50.10,
            'bid_volume': 5000,
            'ask_volume': 6000,
            'total_shares': 5_000_000_000,
            'board_type': 'GEM',
            'is_suspended': False
        },
        {
            'symbol': '000001.SZ',
            'current_price': 15.00,
            'prev_close': 15.00,
            'today_high': 15.00,
            'today_low': 15.00,
            'volume': 0,
            'turnover': 0,
            'avg_daily_volume': 10_000_000,
            'avg_daily_turnover': 150_000_000,
            'bid_price': 15.00,
            'ask_price': 15.00,
            'bid_volume': 0,
            'ask_volume': 0,
            'total_shares': 18_000_000_000,
            'board_type': 'MAIN',
            'is_suspended': True
        }
    ]

    # 扫描市场
    report = monitor.scan_market(test_stocks)

    print(f"\n市场流动性概览 ({report.timestamp.strftime('%Y-%m-%d %H:%M')})")
    print(f"{'='*60}")
    print(f"总股票数: {report.total_stocks}")
    print(f"停牌数: {report.suspended_count}")
    print(f"涨停数: {report.limit_up_count}")
    print(f"跌停数: {report.limit_down_count}")
    print(f"低流动性数: {report.low_liquidity_count}")
    print(f"平均流动性评分: {report.avg_liquidity_score:.1f}/100")

    if report.blocked_trades:
        print(f"\n被阻止的交易:")
        for symbol in report.blocked_trades:
            print(f"  - {symbol}")

    if report.warnings:
        print(f"\n[WARNING] 警告:")
        for w in report.warnings:
            print(f"  {w}")

    print(f"{'='*60}\n")
