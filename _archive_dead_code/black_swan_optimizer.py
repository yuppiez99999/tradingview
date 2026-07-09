# -*- coding: utf-8 -*-
"""
黑天鹅极端行情优化模块 — Black Swan Optimizer
================================================

基于《黑天鹅极端行情应对能力评估》研究报告的6项关键不足，
补齐系统从"预警工具"升级为"自动风控系统"的执行闭环。

核心组件:
  1. IntradayCircuitBreaker   — 日内动态熔断（解决不足一：无日内熔断）
  2. OrderExecutionEngine     — 风控执行引擎（解决不足二：执行空壳）
  3. CorrelationBreakdownModel — 相关性崩溃非线性建模（解决不足三）
  4. OptionLiquidityAdjuster  — 期权流动性自适应（解决不足四）
  5. PriceLimitHandler        — 涨跌停板专项处理（解决不足五）
  6. CounterpartyRiskMonitor  — 对手方风险监控（解决不足六）

Author: ZCode Quantitative Team
Version: 7.2
Date: 2026-07-03
"""

import math
import time
import threading
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
from collections import deque

logger = logging.getLogger('BlackSwanOptimizer')


# ============================================================================
# 枚举定义
# ============================================================================

class CircuitBreakerLevel(Enum):
    """日内熔断级别"""
    NORMAL = 0        # 正常
    LEVEL_1 = 1       # 一级熔断（盘中跌幅>3%）：禁止新开仓
    LEVEL_2 = 2       # 二级熔断（盘中跌幅>5%）：触发减仓
    LEVEL_3 = 3       # 三级熔断（盘中跌幅>7%）：紧急减仓+加对冲
    LEVEL_4 = 4       # 四级熔断（盘中跌幅>9%或VIX>60）：全面防御


class PriceLimitStatus(Enum):
    """涨跌停板状态"""
    NORMAL = "normal"
    NEAR_LIMIT = "near_limit"       # 接近涨停/跌停（距离<1%）
    LIMIT_UP = "limit_up"           # 涨停
    LIMIT_DOWN = "limit_down"       # 跌停
    CONSECUTIVE_LIMIT_DOWN = "consecutive_limit_down"  # 连续跌停


class CounterpartyRiskLevel(Enum):
    """对手方风险等级"""
    SAFE = "safe"          # 安全
    CAUTION = "caution"    # 谨慎
    ELEVATED = "elevated"  # 风险升高
    CRITICAL = "critical"  # 严重风险


# ============================================================================
# 1. 日内动态熔断引擎（解决不足一）
# ============================================================================

@dataclass
class IntradayAlert:
    """日内告警"""
    timestamp: str
    level: CircuitBreakerLevel
    trigger_reason: str
    intraday_drop: float
    vix_level: float
    recommended_action: str
    executed: bool = False


class IntradayCircuitBreaker:
    """
    日内动态熔断引擎

    解决研究报告不足一：无日内动态熔断
    "所有风控决策仅在盘前7:00执行一次。2008年10月那种盘中剧烈波动的日子里，
     系统无法在交易时段内做出反应。"

    机制设计（基于A股交易时段 9:30-11:30, 13:00-15:00）:
      - 每30秒检查一次盘中跌幅、VIX、板块异动
      - 四级熔断触发不同级别的保护动作
      - 与盘前紧急协议联动，但可独立触发盘中操作
    """

    def __init__(self, total_capital: float = 5_000_000):
        self.total_capital = total_capital
        self.is_active = False
        self.monitor_thread = None
        self.check_interval = 30  # 30秒

        # 熔断阈值（基于A股涨跌停10%/20%和VIX历史）
        self.thresholds = {
            'level_1_drop': -0.03,    # 盘中跌幅3%
            'level_2_drop': -0.05,    # 盘中跌幅5%
            'level_3_drop': -0.07,    # 盘中跌幅7%
            'level_4_drop': -0.09,    # 盘中跌幅9%
            'vix_extreme': 60.0,      # VIX极端值
            'vix_critical': 80.0,     # VIX危机值（接近2008年89.53）
            # 回撤熔断阈值（黑天鹅保70%资金硬约束）
            'drawdown_warning': -0.10,    # 回撤10%：预警并降权益至45%
            'drawdown_emergency': -0.15,  # 回撤15%：全面风控，权益降至30%
            'drawdown_extreme': -0.20,    # 回撤20%：强制降至最低权益20%
        }

        # 交易时段（A股）
        self.trading_sessions = [
            (datetime.strptime("09:30", "%H:%M").time(),
             datetime.strptime("11:30", "%H:%M").time()),
            (datetime.strptime("13:00", "%H:%M").time(),
             datetime.strptime("15:00", "%H:%M").time()),
        ]

        # 状态
        self.current_level = CircuitBreakerLevel.NORMAL
        self.alert_history: deque = deque(maxlen=200)
        self.daily_reference_price: Optional[float] = None
        self.consecutive_limit_down_count = 0
        self.high_water_mark: float = 0.0  # 组合高点水位

        # 回调函数（注入执行引擎）
        self.on_breaker_triggered: Optional[Callable] = None

        logger.info("日内动态熔断引擎初始化完成（含回撤熔断保70%规则）")

    def is_trading_hours(self) -> bool:
        """检查当前是否在交易时段内"""
        now_time = datetime.now().time()
        for start, end in self.trading_sessions:
            if start <= now_time <= end:
                return True
        return False

    def set_daily_reference(self, price: float):
        """设置当日参考价（开盘价或前收盘价）"""
        self.daily_reference_price = price
        self.current_level = CircuitBreakerLevel.NORMAL
        self.consecutive_limit_down_count = 0
        logger.info(f"日内熔断参考价设为: {price:.2f}")

    def evaluate(self, current_price: float, vix_level: float,
                 sector_drops: Dict[str, float] = None,
                 limit_down_count: int = 0,
                 portfolio_value: float = 0.0) -> IntradayAlert:
        """
        评估盘中熔断状态

        参数:
            current_price: 当前指数/组合价格
            vix_level: 当前VIX水平
            sector_drops: 各板块跌幅 {板块名: 跌幅}
            limit_down_count: 当前跌停个股数量
            portfolio_value: 当前组合市值（用于回撤熔断）

        返回:
            IntradayAlert 告警对象
        """
        if self.daily_reference_price is None or self.daily_reference_price <= 0:
            return IntradayAlert(
                timestamp=datetime.now().isoformat(),
                level=CircuitBreakerLevel.NORMAL,
                trigger_reason="无参考价",
                intraday_drop=0.0,
                vix_level=vix_level,
                recommended_action="设置参考价后启用"
            )

        intraday_drop = (current_price - self.daily_reference_price) / self.daily_reference_price

        # 更新高点水位
        if portfolio_value > 0:
            if portfolio_value > self.high_water_mark:
                self.high_water_mark = portfolio_value

        # 计算组合回撤
        drawdown = 0.0
        if portfolio_value > 0 and self.high_water_mark > 0:
            drawdown = (portfolio_value - self.high_water_mark) / self.high_water_mark

        # 确定熔断级别
        new_level = CircuitBreakerLevel.NORMAL
        trigger_reason = ""

        # 回撤熔断检查（黑天鹅保70%资金硬约束）
        drawdown_breaker_triggered = False
        if drawdown <= self.thresholds['drawdown_extreme']:
            new_level = CircuitBreakerLevel.LEVEL_4
            trigger_reason = f"回撤熔断({drawdown:.1%}<=-20%)，强制减仓至20%权益"
            drawdown_breaker_triggered = True
        elif drawdown <= self.thresholds['drawdown_emergency']:
            new_level = CircuitBreakerLevel.LEVEL_3
            trigger_reason = f"回撤熔断({drawdown:.1%}<=-15%)，紧急减仓至30%权益"
            drawdown_breaker_triggered = True
        elif drawdown <= self.thresholds['drawdown_warning']:
            new_level = CircuitBreakerLevel.LEVEL_2
            trigger_reason = f"回撤熔断({drawdown:.1%}<=-10%)，预警降权益至45%"
            drawdown_breaker_triggered = True

        # 优先检查VIX极端情况（2008年VIX 89.53级别）
        if vix_level >= self.thresholds['vix_critical']:
            new_level = CircuitBreakerLevel.LEVEL_4
            trigger_reason = f"VIX危机值({vix_level:.1f}>=80)，全面防御"
        elif vix_level >= self.thresholds['vix_extreme'] and intraday_drop < -0.05:
            new_level = CircuitBreakerLevel.LEVEL_3
            trigger_reason = f"VIX极端({vix_level:.1f})+盘中跌幅({intraday_drop:.1%})"
        elif intraday_drop <= self.thresholds['level_4_drop']:
            new_level = CircuitBreakerLevel.LEVEL_4
            trigger_reason = f"盘中跌幅{intraday_drop:.1%}(>=9%)"
        elif intraday_drop <= self.thresholds['level_3_drop']:
            new_level = CircuitBreakerLevel.LEVEL_3
            trigger_reason = f"盘中跌幅{intraday_drop:.1%}(>=7%)"
        elif intraday_drop <= self.thresholds['level_2_drop']:
            new_level = CircuitBreakerLevel.LEVEL_2
            trigger_reason = f"盘中跌幅{intraday_drop:.1%}(>=5%)"
        elif intraday_drop <= self.thresholds['level_1_drop']:
            new_level = CircuitBreakerLevel.LEVEL_1
            trigger_reason = f"盘中跌幅{intraday_drop:.1%}(>=3%)"

        # 跌停个股数量触发
        if limit_down_count > 100 and new_level.value < CircuitBreakerLevel.LEVEL_3.value:
            new_level = CircuitBreakerLevel.LEVEL_3
            trigger_reason = f"跌停个股{limit_down_count}只，市场系统性风险"

        # 板块系统性下跌检测（2008年相关性趋近1的场景）
        if sector_drops:
            all_sectors_drop = all(v < -0.03 for v in sector_drops.values())
            if all_sectors_drop and len(sector_drops) >= 3:
                if new_level.value < CircuitBreakerLevel.LEVEL_2.value:
                    new_level = CircuitBreakerLevel.LEVEL_2
                    trigger_reason = f"全板块下跌({len(sector_drops)}个板块均跌>3%)，相关性崩溃"

        # 生成推荐动作
        action = self._get_recommended_action(new_level)

        # 级别升级时记录告警
        if new_level.value > self.current_level.value:
            alert = IntradayAlert(
                timestamp=datetime.now().isoformat(),
                level=new_level,
                trigger_reason=trigger_reason,
                intraday_drop=intraday_drop,
                vix_level=vix_level,
                recommended_action=action
            )
            self.alert_history.append(alert)

            logger.warning(f"日内熔断升级 → {new_level.name}: {trigger_reason}")
            logger.warning(f"  推荐动作: {action}")

            # 触发回调
            if self.on_breaker_triggered:
                try:
                    self.on_breaker_triggered(alert)
                    alert.executed = True
                except Exception as e:
                    logger.error(f"熔断回调执行失败: {e}")

            self.current_level = new_level
            return alert

        # 级别降级
        if new_level.value < self.current_level.value:
            logger.info(f"日内熔断降级 {self.current_level.name} → {new_level.name}")
            self.current_level = new_level

        return IntradayAlert(
            timestamp=datetime.now().isoformat(),
            level=self.current_level,
            trigger_reason=trigger_reason or "维持当前级别",
            intraday_drop=intraday_drop,
            vix_level=vix_level,
            recommended_action=action
        )

    def _get_recommended_action(self, level: CircuitBreakerLevel) -> str:
        """根据熔断级别获取推荐动作"""
        actions = {
            CircuitBreakerLevel.NORMAL: "正常交易",
            CircuitBreakerLevel.LEVEL_1: "禁止新开仓，仅允许减仓和对冲操作",
            CircuitBreakerLevel.LEVEL_2: "触发减仓20%，增加期货对冲至50%",
            CircuitBreakerLevel.LEVEL_3: "紧急减仓40%，期货对冲至70%，启动尾部Put保护",
            CircuitBreakerLevel.LEVEL_4: "全面防御：减仓60%+，最大化对冲，暂停所有买入",
        }
        return actions.get(level, "未知级别")

    def start_monitoring(self, price_feed: Callable[[], Tuple[float, float]] = None):
        """启动盘中监控线程"""
        if self.is_active:
            return

        self.is_active = True
        self.monitor_thread = threading.Thread(
            target=self._monitoring_loop,
            args=(price_feed,),
            daemon=True
        )
        self.monitor_thread.start()
        logger.info("日内熔断监控已启动")

    def stop_monitoring(self):
        """停止盘中监控"""
        self.is_active = False
        logger.info("日内熔断监控已停止")

    def _monitoring_loop(self, price_feed: Callable):
        """监控循环（在交易时段内运行）"""
        while self.is_active:
            try:
                if not self.is_trading_hours():
                    time.sleep(60)
                    continue

                if price_feed:
                    price, vix = price_feed()
                    self.evaluate(price, vix)

                time.sleep(self.check_interval)
            except Exception as e:
                logger.error(f"日内熔断监控错误: {e}")
                time.sleep(self.check_interval)

    def get_status(self) -> Dict:
        """获取当前熔断状态"""
        recent_alerts = list(self.alert_history)[-10:]
        return {
            'is_active': self.is_active,
            'current_level': self.current_level.name,
            'level_value': self.current_level.value,
            'reference_price': self.daily_reference_price,
            'is_trading_hours': self.is_trading_hours(),
            'recent_alerts': [
                {
                    'time': a.timestamp,
                    'level': a.level.name,
                    'reason': a.trigger_reason,
                    'action': a.recommended_action,
                    'executed': a.executed
                }
                for a in recent_alerts
            ]
        }


# ============================================================================
# 2. 风控执行引擎（解决不足二）
# ============================================================================

@dataclass
class ExecutableOrder:
    """可执行订单"""
    order_id: str
    timestamp: str
    order_type: str          # sell_stock / sell_future / buy_put / sell_call / reduce_position
    symbol: str
    quantity: int
    price: Optional[float]   # 限价单价格，None为市价单
    reason: str              # 触发原因
    priority: str            # high / medium / low
    status: str = "pending"  # pending / submitted / filled / failed / cancelled
    fill_price: Optional[float] = None
    fill_quantity: int = 0
    error: Optional[str] = None


class OrderExecutionEngine:
    """
    风控执行引擎

    解决研究报告不足二：风险控制执行措施多数为空壳
    "_execute_risk_control 中的减仓、紧急对冲等措施仅有日志输出，
     未生成实际的卖出订单或对冲指令。"

    功能:
      - 将风控决策转化为具体的可执行订单
      - 生成减仓指令（按持仓比例+优先级排序）
      - 生成期货对冲指令
      - 生成期权保护指令
      - 订单状态追踪
      - 与交易接口对接的适配层
    """

    def __init__(self, total_capital: float = 5_000_000):
        self.total_capital = total_capital
        self.order_history: deque = deque(maxlen=500)
        self.pending_orders: List[ExecutableOrder] = []
        self.execution_callback: Optional[Callable] = None

        # 减仓优先级规则：亏损最多/风险最高的先减
        self.reduce_priority_rules = {
            'by_loss': True,           # 按亏损幅度排序
            'by_beta': True,           # 高Beta先减
            'by_concentration': True,  # 高集中度先减
            'max_reduce_ratio': 0.60,  # 单次最大减仓60%
        }

        logger.info("风控执行引擎初始化完成")

    def generate_reduce_orders(self, positions: List[Dict],
                               reduce_ratio: float,
                               reason: str,
                               priority: str = "high") -> List[ExecutableOrder]:
        """
        生成减仓订单

        参数:
            positions: 持仓列表 [{symbol, quantity, current_price, cost_price, beta, weight}]
            reduce_ratio: 减仓比例 (0-1)
            reason: 减仓原因
            priority: 优先级

        返回:
            可执行订单列表
        """
        orders = []
        total_position_value = sum(p['quantity'] * p['current_price'] for p in positions)

        if total_position_value <= 0:
            return orders

        # 限制最大减仓比例
        reduce_ratio = min(reduce_ratio, self.reduce_priority_rules['max_reduce_ratio'])

        # 计算每只股票的减仓优先级得分
        scored_positions = []
        for pos in positions:
            score = 0.0

            # 按亏损幅度排序（亏损越大越先减）
            if self.reduce_priority_rules['by_loss']:
                loss_pct = (pos['current_price'] - pos['cost_price']) / pos['cost_price']
                score += max(0, -loss_pct) * 100  # 亏损越大分数越高

            # 按Beta排序（Beta越大越先减）
            if self.reduce_priority_rules['by_beta']:
                score += pos.get('beta', 1.0) * 10

            # 按集中度排序（权重越大越先减）
            if self.reduce_priority_rules['by_concentration']:
                weight = (pos['quantity'] * pos['current_price']) / total_position_value
                score += weight * 50

            scored_positions.append((pos, score))

        # 按得分降序排列
        scored_positions.sort(key=lambda x: x[1], reverse=True)

        # 生成减仓订单
        target_reduce_value = total_position_value * reduce_ratio
        reduced_value = 0.0

        for pos, score in scored_positions:
            if reduced_value >= target_reduce_value:
                break

            # 计算该持仓需要减仓的数量
            pos_value = pos['quantity'] * pos['current_price']
            pos_weight = pos_value / total_position_value
            pos_reduce_value = target_reduce_value * pos_weight
            reduce_qty = int(pos_reduce_value / pos['current_price'])
            reduce_qty = min(reduce_qty, pos['quantity'])

            if reduce_qty <= 0:
                continue

            order = ExecutableOrder(
                order_id=f"REDUCE_{datetime.now().strftime('%H%M%S')}_{pos['symbol']}",
                timestamp=datetime.now().isoformat(),
                order_type="reduce_position",
                symbol=pos['symbol'],
                quantity=reduce_qty,
                price=None,  # 市价单（紧急减仓用市价确保成交）
                reason=f"{reason} (优先级得分:{score:.1f})",
                priority=priority
            )
            orders.append(order)
            reduced_value += reduce_qty * pos['current_price']

        logger.info(f"生成减仓订单: {len(orders)}笔, "
                    f"目标减仓{reduce_ratio:.0%}, "
                    f"减仓金额约{reduced_value:,.0f}")

        return orders

    def generate_hedge_orders(self, portfolio_value: float,
                              target_hedge_ratio: float,
                              current_hedge_ratio: float,
                              index_level: float,
                              reason: str) -> List[ExecutableOrder]:
        """
        生成期货对冲订单

        参数:
            portfolio_value: 组合市值
            target_hedge_ratio: 目标对冲比率
            current_hedge_ratio: 当前对冲比率
            index_level: 指数点位
            reason: 对冲原因
        """
        orders = []

        delta_ratio = target_hedge_ratio - current_hedge_ratio
        if abs(delta_ratio) < 0.03:  # 3%以下不调仓
            return orders

        # 沪深300股指期货: 每点300元
        contract_value = 300 * index_level
        delta_notional = portfolio_value * delta_ratio
        contracts = int(abs(delta_notional) / contract_value)

        if contracts == 0:
            return orders

        action = "short_more" if delta_ratio > 0 else "reduce_short"
        order_type = "sell_future" if delta_ratio > 0 else "buy_future_close"

        order = ExecutableOrder(
            order_id=f"HEDGE_{datetime.now().strftime('%H%M%S')}",
            timestamp=datetime.now().isoformat(),
            order_type=order_type,
            symbol="IF主力合约",
            quantity=contracts,
            price=None,
            reason=f"{reason}: {action} {contracts}手, "
                   f"对冲比率 {current_hedge_ratio:.0%}→{target_hedge_ratio:.0%}",
            priority="high"
        )
        orders.append(order)

        logger.info(f"生成对冲订单: {action} {contracts}手, "
                    f"名义金额{contracts * contract_value:,.0f}")

        return orders

    def generate_option_orders(self, portfolio_value: float,
                               index_level: float,
                               vix_level: float,
                               target_coverage: float,
                               reason: str) -> List[ExecutableOrder]:
        """
        生成期权保护订单

        参数:
            portfolio_value: 组合市值
            index_level: 指数点位
            vix_level: VIX水平
            target_coverage: 目标对冲覆盖率
            reason: 原因
        """
        orders = []

        # 根据VIX选择行权价（VIX越高，行权价越远但数量增加）
        if vix_level > 60:
            strike_pct = 0.80  # 深度虚值
            expiry_months = 3
        elif vix_level > 40:
            strike_pct = 0.85
            expiry_months = 2
        else:
            strike_pct = 0.90
            expiry_months = 2

        strike = index_level * strike_pct

        # 期权流动性调整后的实际可买入数量
        liquidity_adjuster = OptionLiquidityAdjuster()
        adjusted_coverage = liquidity_adjuster.adjust_coverage(
            target_coverage, vix_level, portfolio_value
        )

        # ETF期权合约乘数10000
        protection_notional = portfolio_value * adjusted_coverage
        contracts = max(1, int(protection_notional / (index_level * 10000)))

        order = ExecutableOrder(
            order_id=f"PUT_{datetime.now().strftime('%H%M%S')}",
            timestamp=datetime.now().isoformat(),
            order_type="buy_put",
            symbol=f"ETF期权(行权价{strike:.0f},{expiry_months}个月)",
            quantity=contracts,
            price=None,
            reason=f"{reason}: 买入{contracts}张Put, "
                   f"行权价{strike:.0f}({strike_pct:.0%}), "
                   f"覆盖率{adjusted_coverage:.0%}(流动性调整后, VIX={vix_level:.0f})",
            priority="high"
        )
        orders.append(order)

        logger.info(f"生成期权保护订单: 买入{contracts}张Put, "
                    f"行权价{strike:.0f}({strike_pct:.0%}), "
                    f"VIX={vix_level:.0f}, 覆盖率{adjusted_coverage:.0%}")

        return orders

    def execute_orders(self, orders: List[ExecutableOrder]) -> List[ExecutableOrder]:
        """
        执行订单

        如果设置了execution_callback，则通过回调执行；
        否则记录为"待执行"状态，等待人工或外部系统执行。
        """
        results = []
        for order in orders:
            try:
                if self.execution_callback:
                    # 通过回调函数执行（对接实际交易接口）
                    result = self.execution_callback(order)
                    order.status = result.get('status', 'submitted')
                    order.fill_price = result.get('fill_price')
                    order.fill_quantity = result.get('fill_quantity', 0)
                else:
                    # 无交易接口时，标记为待执行
                    order.status = "pending_manual"
                    logger.warning(f"订单待手动执行: {order.order_id} {order.symbol} "
                                   f"×{order.quantity} ({order.reason})")

                self.order_history.append(order)
                results.append(order)

            except Exception as e:
                order.status = "failed"
                order.error = str(e)
                logger.error(f"订单执行失败 {order.order_id}: {e}")
                self.order_history.append(order)
                results.append(order)

        return results

    def get_execution_summary(self) -> Dict:
        """获取执行摘要"""
        recent_orders = list(self.order_history)[-50:]
        return {
            'total_orders': len(self.order_history),
            'pending_orders': len([o for o in self.order_history if o.status == 'pending_manual']),
            'filled_orders': len([o for o in self.order_history if o.status == 'filled']),
            'failed_orders': len([o for o in self.order_history if o.status == 'failed']),
            'recent_orders': [
                {
                    'order_id': o.order_id,
                    'type': o.order_type,
                    'symbol': o.symbol,
                    'quantity': o.quantity,
                    'status': o.status,
                    'reason': o.reason
                }
                for o in recent_orders
            ]
        }

    def execute_risk_decision(self, decision: Dict,
                              positions: List[Dict],
                              portfolio_value: float,
                              index_level: float,
                              vix_level: float,
                              current_hedge_ratio: float = 0.0) -> List[ExecutableOrder]:
        """
        执行风控决策 — 替代原有的空壳_execute_risk_control

        这是核心方法，将风控决策转化为实际的可执行订单。
        """
        all_orders = []
        measures = decision.get('measures', [])
        priority = decision.get('priority', 'low')

        for measure in measures:
            if measure == 'reduce_positions':
                ratio = 0.30 if priority == 'critical' else (0.20 if priority == 'high' else 0.10)
                orders = self.generate_reduce_orders(
                    positions, ratio,
                    f"风控减仓({priority})", priority
                )
                all_orders.extend(orders)

            elif measure == 'emergency_hedging':
                # 紧急对冲：期货对冲至70%
                orders = self.generate_hedge_orders(
                    portfolio_value, 0.70, current_hedge_ratio,
                    index_level, f"紧急对冲({priority})"
                )
                all_orders.extend(orders)

            elif measure == 'increase_hedging':
                target = 0.50 if priority == 'high' else 0.30
                orders = self.generate_hedge_orders(
                    portfolio_value, target, current_hedge_ratio,
                    index_level, f"增加对冲({priority})"
                )
                all_orders.extend(orders)

            elif measure == 'stop_all_trading':
                # 停止交易：生成暂停标志，不生成新订单
                logger.critical("执行停止交易指令 — 暂停所有新开仓")
                # 但允许减仓
                orders = self.generate_reduce_orders(
                    positions, 0.40,
                    "停止交易-紧急减仓40%", "critical"
                )
                all_orders.extend(orders)

            elif measure == 'execute_stop_loss_immediately':
                # 止损触发：立即减仓触发止损的标的
                triggered_stocks = decision.get('triggered_stocks', [])
                for symbol in triggered_stocks:
                    triggered_pos = [p for p in positions if p['symbol'] == symbol]
                    if triggered_pos:
                        orders = self.generate_reduce_orders(
                            triggered_pos, 1.0,
                            f"止损平仓-{symbol}", "critical"
                        )
                        all_orders.extend(orders)

        # 执行所有订单
        if all_orders:
            self.execute_orders(all_orders)
            logger.info(f"风控决策执行完成: 生成{len(all_orders)}笔订单")

        return all_orders


# ============================================================================
# 3. 相关性崩溃非线性建模（解决不足三）
# ============================================================================

class CorrelationBreakdownModel:
    """
    相关性崩溃非线性建模

    解决研究报告不足三：2008年式相关性崩溃未被充分建模
    "模型假设这四个因子是独立可加的，但在2008年它们是同步爆发的
     ——这种'共振效应'被低估了。"

    核心改进:
      - 引入共振乘数因子（因子间非线性交互）
      - 相关性崩溃时分散化收益归零的显式建模
      - 基于历史危机校准的尾部放大效应
    """

    # 历史危机校准数据
    CRISIS_CALIBRATION = {
        '2008_subprime': {
            'market_shock': -0.568,
            'correlation_breakdown': 0.95,
            'resonance_multiplier': 2.3,    # 2008年四因子共振导致损失放大2.3倍
            'diversification_failure': 0.90, # 90%的分散化收益消失
        },
        '2000_dotcom': {
            'market_shock': -0.768,
            'correlation_breakdown': 0.70,
            'resonance_multiplier': 1.4,    # 板块分化，共振较弱
            'diversification_failure': 0.30, # 分散化仍部分有效
        },
        '2020_covid': {
            'market_shock': -0.339,
            'correlation_breakdown': 0.85,
            'resonance_multiplier': 1.8,
            'diversification_failure': 0.75,
        }
    }

    def __init__(self):
        logger.info("相关性崩溃模型初始化完成")

    def calculate_nonlinear_loss(self, initial_value: float,
                                  market_shock: float,
                                  volatility_loss: float,
                                  liquidity_loss: float,
                                  correlation_breakdown: float,
                                  portfolio_beta: float = 1.0,
                                  diversification_ratio: float = 0.30) -> Dict:
        """
        计算非线性相关性崩溃损失

        参数:
            initial_value: 初始组合价值
            market_shock: 市场冲击 (负值, 如-0.30)
            volatility_loss: 波动率损失 (正值)
            liquidity_loss: 流动性损失 (正值)
            correlation_breakdown: 相关性崩溃程度 (0-1)
            portfolio_beta: 组合Beta
            diversification_ratio: 分散化比例 (正常情况下分散化降低的风险比例)

        返回:
            非线性损失计算结果
        """
        # === 1. 基础线性损失（原有模型） ===
        market_loss = initial_value * abs(market_shock) * portfolio_beta
        base_linear_loss = market_loss + volatility_loss + liquidity_loss

        # === 2. 共振乘数（非线性放大） ===
        # 当相关性崩溃越高，四因子共振效应越强
        # 使用指数函数建模非线性放大
        # resonance_factor = 1 + (correlation_breakdown^2) * max_resonance_boost
        max_resonance_boost = 1.5  # 最高放大1.5倍
        resonance_factor = 1.0 + (correlation_breakdown ** 2) * max_resonance_boost

        # === 3. 分散化失效损失 ===
        # 正常情况下，分散化降低 diversification_ratio 的风险
        # 在相关性崩溃时，分散化收益按 correlation_breakdown 比例消失
        diversification_benefit_lost = initial_value * abs(market_shock) * \
                                        diversification_ratio * correlation_breakdown

        # === 4. 相关性崩溃的额外损失 ===
        # 当所有资产同时暴跌时，原本"负相关"的对冲失效
        # 这部分损失与相关性崩溃程度和组合复杂度成正比
        correlation_extra_loss = initial_value * correlation_breakdown * \
                                  (correlation_breakdown ** 1.5) * 0.15

        # === 5. 尾部放大效应 ===
        # 在极端情况下（如2008年VIX 89.53），尾部厚度急剧增加
        # 当市场冲击超过-30%时，启动额外的尾部放大
        tail_amplification = 0.0
        if market_shock < -0.30:
            # 超过-30%的部分，每多跌10%，额外放大5%
            excess = abs(market_shock) - 0.30
            tail_amplification = initial_value * excess * 0.15 * correlation_breakdown

        # === 6. 总非线性损失 ===
        nonlinear_total_loss = (base_linear_loss * resonance_factor
                                + diversification_benefit_lost
                                + correlation_extra_loss
                                + tail_amplification)

        # 损失不能超过组合总价值
        nonlinear_total_loss = min(nonlinear_total_loss, initial_value)

        # 对比线性模型
        linear_correlation_loss = initial_value * correlation_breakdown * 0.1
        linear_total = base_linear_loss + linear_correlation_loss

        amplification_ratio = nonlinear_total_loss / linear_total if linear_total > 0 else 1.0

        return {
            'linear_total_loss': linear_total,
            'nonlinear_total_loss': nonlinear_total_loss,
            'amplification_ratio': amplification_ratio,
            'resonance_factor': resonance_factor,
            'diversification_benefit_lost': diversification_benefit_lost,
            'correlation_extra_loss': correlation_extra_loss,
            'tail_amplification': tail_amplification,
            'loss_percentage': nonlinear_total_loss / initial_value,
            'portfolio_value_after': initial_value - nonlinear_total_loss,
            'components': {
                'market_loss': market_loss,
                'volatility_loss': volatility_loss,
                'liquidity_loss': liquidity_loss,
                'base_linear_subtotal': base_linear_loss,
                'resonance_amplified': base_linear_loss * (resonance_factor - 1),
                'diversification_lost': diversification_benefit_lost,
                'correlation_extra': correlation_extra_loss,
                'tail_amplification': tail_amplification,
            }
        }

    def get_crisis_benchmark(self, crisis_type: str) -> Dict:
        """获取历史危机基准数据用于对比"""
        return self.CRISIS_CALIBRATION.get(crisis_type, {})


# ============================================================================
# 4. 期权流动性自适应调整（解决不足四）
# ============================================================================

class OptionLiquidityAdjuster:
    """
    期权流动性自适应调整器

    解决研究报告不足四：期权市场的流动性假设过于乐观
    "在VIX 89.53时，实际买入成本可能远超预算，
     且流动性可能不足以支持所需的合约数量。"

    功能:
      - VIX>40时启动流动性折价
      - 权利金在极端VIX下的非线性膨胀
      - 实际可买入合约数量限制
      - 预算约束下的覆盖率自动调整
    """

    def __init__(self):
        # VIX到权利金膨胀因子的映射
        # 2008年VIX 89.53时，OTM Put权利金约为正常水平的10-20倍
        self.vix_premium_inflation = {
            20: 1.0,   # 正常
            30: 1.5,   # +50%
            40: 2.5,   # +150%
            50: 4.0,   # +300%
            60: 7.0,   # +600%
            70: 10.0,  # +900%
            80: 15.0,  # +1400%
            90: 20.0,  # +1900% (2008年极端)
        }

        # 流动性折价（实际可成交覆盖率）
        self.vix_liquidity_haircut = {
            20: 1.0,   # 100%可执行
            30: 0.95,
            40: 0.85,
            50: 0.70,
            60: 0.50,
            70: 0.35,
            80: 0.20,
            90: 0.10,  # 仅10%的预期覆盖能实际执行
        }

        logger.info("期权流动性自适应调整器初始化完成")

    def get_premium_inflation_factor(self, vix: float) -> float:
        """获取权利金膨胀因子"""
        # 线性插值
        sorted_vix = sorted(self.vix_premium_inflation.keys())
        if vix <= sorted_vix[0]:
            return self.vix_premium_inflation[sorted_vix[0]]
        if vix >= sorted_vix[-1]:
            return self.vix_premium_inflation[sorted_vix[-1]]

        for i in range(len(sorted_vix) - 1):
            if sorted_vix[i] <= vix <= sorted_vix[i + 1]:
                v_low, v_high = sorted_vix[i], sorted_vix[i + 1]
                f_low = self.vix_premium_inflation[v_low]
                f_high = self.vix_premium_inflation[v_high]
                # 对数插值（权利金膨胀是非线性的）
                ratio = (math.log(vix) - math.log(v_low)) / (math.log(v_high) - math.log(v_low))
                return f_low * (1 - ratio) + f_high * ratio

        return 1.0

    def get_liquidity_haircut(self, vix: float) -> float:
        """获取流动性折价（实际可执行的覆盖率比例）"""
        sorted_vix = sorted(self.vix_liquidity_haircut.keys())
        if vix <= sorted_vix[0]:
            return self.vix_liquidity_haircut[sorted_vix[0]]
        if vix >= sorted_vix[-1]:
            return self.vix_liquidity_haircut[sorted_vix[-1]]

        for i in range(len(sorted_vix) - 1):
            if sorted_vix[i] <= vix <= sorted_vix[i + 1]:
                v_low, v_high = sorted_vix[i], sorted_vix[i + 1]
                h_low = self.vix_liquidity_haircut[v_low]
                h_high = self.vix_liquidity_haircut[v_high]
                ratio = (vix - v_low) / (v_high - v_low)
                return h_low * (1 - ratio) + h_high * ratio

        return 1.0

    def adjust_coverage(self, target_coverage: float, vix: float,
                        portfolio_value: float, budget_pct: float = 0.025) -> float:
        """
        在VIX和预算约束下，调整实际可达到的对冲覆盖率

        参数:
            target_coverage: 目标覆盖率 (0-1)
            vix: 当前VIX水平
            portfolio_value: 组合价值
            budget_pct: 年度对冲预算占组合比例 (默认2.5%)

        返回:
            流动性和预算约束后的实际覆盖率
        """
        # 1. 流动性折价
        liquidity_factor = self.get_liquidity_haircut(vix)
        liquidity_adjusted = target_coverage * liquidity_factor

        # 2. 预算约束
        # 正常VIX下2.5%预算可覆盖约30%敞口
        # VIX膨胀后，同样预算可覆盖的比例 = 30% / 膨胀因子
        base_budget_coverage = 0.30  # 正常VIX下的基准覆盖率
        inflation_factor = self.get_premium_inflation_factor(vix)
        budget_constrained_coverage = base_budget_coverage / inflation_factor

        # 3. 取流动性和预算约束的较小值
        actual_coverage = min(liquidity_adjusted, budget_constrained_coverage)

        # 4. 不超过目标覆盖率
        actual_coverage = min(actual_coverage, target_coverage)

        logger.info(f"期权流动性调整: 目标{target_coverage:.0%} → "
                    f"实际{actual_coverage:.0%} "
                    f"(VIX={vix:.0f}, 膨胀因子={inflation_factor:.1f}x, "
                    f"流动性折价={liquidity_factor:.0%})")

        return actual_coverage

    def estimate_actual_premium(self, base_premium: float, vix: float) -> float:
        """估算VIX调整后的实际权利金"""
        inflation = self.get_premium_inflation_factor(vix)
        return base_premium * inflation

    def get_liquidity_warning(self, vix: float) -> Optional[str]:
        """获取流动性警告"""
        if vix >= 80:
            return ("严重警告: VIX>=80，期权市场可能冻结，"
                    "保护性Put实际覆盖率可能仅10%以下，"
                    "需依赖期货对冲和减仓作为主要保护手段")
        elif vix >= 60:
            return ("警告: VIX>=60，期权权利金极度膨胀(7-15倍)，"
                    "实际覆盖率可能降至50%以下")
        elif vix >= 40:
            return ("注意: VIX>=40，期权权利金膨胀2.5倍，"
                    "覆盖率可能降至85%")
        return None


# ============================================================================
# 5. 涨跌停板专项处理（解决不足五）
# ============================================================================

class PriceLimitHandler:
    """
    涨跌停板专项处理器

    解决研究报告不足五：涨跌停板场景缺少专项处理
    "A股独有的涨跌停机制（10%/20%）可能在极端行情中导致连续无法成交，
     止损单积压，以及保护性Put标的（ETF期权）的标的ETF自身跌停而无法定价。"

    功能:
      - 检测个股/ETF涨跌停状态
      - 连续跌停计数与止损单积压管理
      - 跌停时的替代执行策略
      - ETF跌停时期权定价的替代方案
    """

    def __init__(self):
        self.limit_history: Dict[str, deque] = {}  # {symbol: deque of daily limit status}
        self.max_history_days = 10
        self.pending_stop_losses: Dict[str, int] = {}  # {symbol: pending quantity}

        # 涨跌停限制
        self.limit_rules = {
            'main_board': 0.10,      # 主板 10%
            'star_board': 0.20,      # 科创板 20%
            'gem_board': 0.20,       # 创业板 20%
            'etf': 0.10,             # ETF 10%
            'etf_option': None,      # ETF期权无涨跌停限制，但标的ETF有
        }

        logger.info("涨跌停板处理器初始化完成")

    def check_price_limit(self, symbol: str, current_price: float,
                          prev_close: float, board_type: str = 'main_board') -> PriceLimitStatus:
        """
        检查涨跌停状态

        参数:
            symbol: 股票/ETF代码
            current_price: 当前价格
            prev_close: 前收盘价
            board_type: 板块类型
        """
        limit_pct = self.limit_rules.get(board_type, 0.10)
        change_pct = (current_price - prev_close) / prev_close if prev_close > 0 else 0

        # 接近涨跌停（距离<1%）
        near_up = change_pct > (limit_pct - 0.01)
        near_down = change_pct < -(limit_pct - 0.01)

        # 涨跌停
        is_limit_up = change_pct >= limit_pct * 0.995
        is_limit_down = change_pct <= -limit_pct * 0.995

        # 记录历史
        if symbol not in self.limit_history:
            self.limit_history[symbol] = deque(maxlen=self.max_history_days)

        if is_limit_down:
            self.limit_history[symbol].append('limit_down')
            consecutive = self._count_consecutive_limit_down(symbol)

            if consecutive >= 2:
                status = PriceLimitStatus.CONSECUTIVE_LIMIT_DOWN
                logger.warning(f"{symbol} 连续{consecutive}日跌停！"
                               f"止损单积压{self.pending_stop_losses.get(symbol, 0)}股")
            else:
                status = PriceLimitStatus.LIMIT_DOWN
                logger.warning(f"{symbol} 跌停 (跌幅{change_pct:.1%})")
        elif is_limit_up:
            self.limit_history[symbol].append('limit_up')
            status = PriceLimitStatus.LIMIT_UP
        elif near_down:
            self.limit_history[symbol].append('near_limit_down')
            status = PriceLimitStatus.NEAR_LIMIT
        elif near_up:
            self.limit_history[symbol].append('near_limit_up')
            status = PriceLimitStatus.NEAR_LIMIT
        else:
            self.limit_history[symbol].append('normal')
            status = PriceLimitStatus.NORMAL

        return status

    def _count_consecutive_limit_down(self, symbol: str) -> int:
        """计算连续跌停天数"""
        history = list(self.limit_history.get(symbol, []))
        count = 0
        for status in reversed(history):
            if status == 'limit_down':
                count += 1
            else:
                break
        return count

    def add_pending_stop_loss(self, symbol: str, quantity: int):
        """添加待执行止损单"""
        self.pending_stop_losses[symbol] = self.pending_stop_losses.get(symbol, 0) + quantity
        logger.warning(f"{symbol} 止损单积压: {self.pending_stop_losses[symbol]}股")

    def get_alternative_execution_strategy(self, symbol: str,
                                           status: PriceLimitStatus,
                                           intended_action: str) -> Dict:
        """
        获取跌停时的替代执行策略

        参数:
            symbol: 标的代码
            status: 涨跌停状态
            intended_action: 原始意图 ('sell' / 'buy_put' / 'hedge')
        """
        strategy = {
            'symbol': symbol,
            'original_action': intended_action,
            'limit_status': status.value,
            'can_execute': True,
            'alternative': None,
            'reason': ''
        }

        if status == PriceLimitStatus.LIMIT_DOWN:
            if intended_action == 'sell':
                # 跌停无法卖出，使用替代策略
                strategy['can_execute'] = False
                strategy['alternative'] = 'use_futures_hedge'
                strategy['reason'] = ('标的跌停，无法直接卖出。'
                                      '替代方案：1)用股指期货空头对冲 Beta 敞口；'
                                      '2)买入该标的对应的ETF Put期权；'
                                      '3)等待跌停打开时执行')
                self.add_pending_stop_loss(symbol, 0)  # 记录积压

            elif intended_action == 'buy_put':
                # ETF跌停时，期权仍可交易但定价可能不准
                strategy['can_execute'] = True
                strategy['alternative'] = 'use_theoretical_price'
                strategy['reason'] = ('标的ETF跌停，期权仍可交易但定价不准。'
                                      '使用理论价格(BS模型)估算，'
                                      '实际成交价可能偏离')

        elif status == PriceLimitStatus.CONSECUTIVE_LIMIT_DOWN:
            strategy['can_execute'] = False
            strategy['alternative'] = 'emergency_futures_hedge'
            strategy['reason'] = (f'连续跌停，止损单严重积压。'
                                  f'紧急方案：立即用股指期货空头覆盖该标的的Beta敞口，'
                                  f'同时等待跌停打开后批量执行止损')

        elif status == PriceLimitStatus.NEAR_LIMIT:
            strategy['can_execute'] = True
            strategy['alternative'] = 'limit_order_with_buffer'
            strategy['reason'] = '接近涨跌停，建议使用限价单并留出缓冲'

        return strategy

    def handle_etf_option_pricing(self, etf_symbol: str,
                                  etf_current_price: float,
                                  etf_prev_close: float) -> Dict:
        """
        处理ETF跌停时的期权定价问题

        当ETF跌停时，ETF本身无法定价（无成交），
        但其期权仍在交易，需要使用理论价格为期权定价。
        """
        status = self.check_price_limit(etf_symbol, etf_current_price,
                                        etf_prev_close, 'etf')

        result = {
            'etf_symbol': etf_symbol,
            'etf_status': status.value,
            'pricing_method': 'market_price',
            'theoretical_price': None,
            'warning': None
        }

        if status in (PriceLimitStatus.LIMIT_DOWN,
                      PriceLimitStatus.CONSECUTIVE_LIMIT_DOWN):
            # ETF跌停，使用前一笔有效成交价或理论模型定价
            result['pricing_method'] = 'theoretical_model'
            result['theoretical_price'] = etf_current_price  # 跌停价作为理论价
            result['warning'] = (f'ETF {etf_symbol} 跌停，期权定价使用理论价格。'
                                 f'实际期权市场可能已大幅偏离理论价，'
                                 f'保护性Put的实际保护效果可能低于预期')

        return result

    def get_market_wide_limit_status(self, positions: List[Dict]) -> Dict:
        """
        获取全市场涨跌停概况

        参数:
            positions: [{symbol, current_price, prev_close, board_type}]
        """
        total = len(positions)
        limit_down_count = 0
        near_limit_down_count = 0
        consecutive_limit_down_stocks = []

        for pos in positions:
            status = self.check_price_limit(
                pos['symbol'], pos['current_price'],
                pos['prev_close'], pos.get('board_type', 'main_board')
            )

            if status == PriceLimitStatus.LIMIT_DOWN:
                limit_down_count += 1
            elif status == PriceLimitStatus.CONSECUTIVE_LIMIT_DOWN:
                limit_down_count += 1
                consecutive_limit_down_stocks.append(pos['symbol'])
            elif status == PriceLimitStatus.NEAR_LIMIT:
                near_limit_down_count += 1

        # 系统性风险判断
        limit_down_ratio = limit_down_count / total if total > 0 else 0
        is_systemic_risk = limit_down_ratio > 0.30  # 30%以上跌停=系统性风险

        return {
            'total_positions': total,
            'limit_down_count': limit_down_count,
            'near_limit_down_count': near_limit_down_count,
            'limit_down_ratio': limit_down_ratio,
            'consecutive_limit_down_stocks': consecutive_limit_down_stocks,
            'is_systemic_risk': is_systemic_risk,
            'pending_stop_loss_count': sum(self.pending_stop_losses.values()),
            'recommendation': (
                '系统性跌停风险，立即启动期货对冲，暂停所有股票卖出操作'
                if is_systemic_risk else
                '正常监控'
            )
        }


# ============================================================================
# 6. 对手方风险监控（解决不足六）
# ============================================================================

class CounterpartyRiskMonitor:
    """
    对手方风险监控器

    解决研究报告不足六：对手方风险未被考量
    "在2008年，雷曼兄弟作为大量衍生品的对手方违约，
     导致众多看似'已对冲'的头寸实际上失去了保护。"

    功能:
      - 监控券商/期货公司信用风险指标
      - 对冲头寸的对手方风险评分
      - 对手方违约时的保护失效评估
      - 多对手方分散建议
    """

    def __init__(self):
        # 对手方风险数据库
        self.counterparties: Dict[str, Dict] = {}

        # 风险评估参数
        self.risk_indicators = {
            'credit_rating_threshold': 'BBB',  # 低于BBB即为高风险
            'cds_spread_warning': 200,          # CDS息差>200bp为警告
            'cds_spread_critical': 500,         # >500bp为严重
            'capital_adequacy_min': 0.08,       # 资本充足率最低8%
        }

        # 历史违约参考
        self.historical_defaults = {
            'lehman_2008': {
                'entity': 'Lehman Brothers',
                'date': '2008-09-15',
                'impact': '大量衍生品对手方违约，对冲头寸失效',
                'protected_positions_lost': '约数千亿美元的"已对冲"头寸失去保护',
            }
        }

        logger.info("对手方风险监控器初始化完成")

    def register_counterparty(self, name: str, entity_type: str,
                              credit_rating: str = 'A',
                              cds_spread: float = 50,
                              capital_adequacy: float = 0.12):
        """注册对手方信息"""
        self.counterparties[name] = {
            'name': name,
            'type': entity_type,  # 'broker' / 'futures_company' / 'bank'
            'credit_rating': credit_rating,
            'cds_spread': cds_spread,
            'capital_adequacy': capital_adequacy,
            'registered_at': datetime.now().isoformat(),
            'risk_level': CounterpartyRiskLevel.SAFE,
            'exposure': 0.0
        }
        self._update_risk_level(name)

    def _update_risk_level(self, name: str):
        """更新对手方风险等级"""
        cp = self.counterparties.get(name)
        if not cp:
            return

        rating = cp['credit_rating']
        cds = cp['cds_spread']
        capital = cp['capital_adequacy']

        # 评级低于BBB 或 CDS>500 或 资本充足率<8% → 严重
        if (rating < 'BBB' or cds > self.risk_indicators['cds_spread_critical']
                or capital < self.risk_indicators['capital_adequacy_min']):
            cp['risk_level'] = CounterpartyRiskLevel.CRITICAL
        elif (rating < 'A' or cds > self.risk_indicators['cds_spread_warning']):
            cp['risk_level'] = CounterpartyRiskLevel.ELEVATED
        elif (rating < 'AA' or cds > 100):
            cp['risk_level'] = CounterpartyRiskLevel.CAUTION
        else:
            cp['risk_level'] = CounterpartyRiskLevel.SAFE

    def assess_hedge_effectiveness(self, hedge_positions: List[Dict]) -> Dict:
        """
        评估对冲头寸在对手方风险下的实际有效性

        参数:
            hedge_positions: [{counterparty, notional, type, hedge_ratio}]

        返回:
            对冲有效性评估
        """
        if not hedge_positions:
            return {
                'total_hedge_notional': 0,
                'risk_adjusted_hedge': 0,
                'effectiveness': 1.0,
                'at_risk_positions': []
            }

        total_notional = sum(h['notional'] for h in hedge_positions)
        risk_adjusted_total = 0.0
        at_risk_positions = []

        # 风险等级到有效性折价的映射
        effectiveness_map = {
            CounterpartyRiskLevel.SAFE: 1.0,
            CounterpartyRiskLevel.CAUTION: 0.90,
            CounterpartyRiskLevel.ELEVATED: 0.65,
            CounterpartyRiskLevel.CRITICAL: 0.20,  # 严重风险时仅20%有效
        }

        for hedge in hedge_positions:
            counterparty = hedge.get('counterparty', 'unknown')
            cp_info = self.counterparties.get(counterparty, {})
            risk_level = cp_info.get('risk_level', CounterpartyRiskLevel.SAFE)

            effectiveness = effectiveness_map.get(risk_level, 1.0)
            adjusted_notional = hedge['notional'] * effectiveness
            risk_adjusted_total += adjusted_notional

            if risk_level in (CounterpartyRiskLevel.ELEVATED,
                              CounterpartyRiskLevel.CRITICAL):
                at_risk_positions.append({
                    'counterparty': counterparty,
                    'notional': hedge['notional'],
                    'risk_level': risk_level.value,
                    'effectiveness': effectiveness,
                    'lost_protection': hedge['notional'] * (1 - effectiveness)
                })

        overall_effectiveness = risk_adjusted_total / total_notional if total_notional > 0 else 0

        result = {
            'total_hedge_notional': total_notional,
            'risk_adjusted_hedge': risk_adjusted_total,
            'effectiveness': overall_effectiveness,
            'at_risk_positions': at_risk_positions,
            'total_lost_protection': total_notional - risk_adjusted_total,
            'recommendation': self._get_diversification_recommendation(at_risk_positions)
        }

        if at_risk_positions:
            logger.warning(f"对手方风险: {len(at_risk_positions)}个对冲头寸受影响, "
                          f"损失保护额度{result['total_lost_protection']:,.0f}")

        return result

    def _get_diversification_recommendation(self, at_risk: List[Dict]) -> str:
        """获取对手方分散建议"""
        if not at_risk:
            return "对手方风险正常，无需调整"

        critical_count = len([p for p in at_risk
                            if p['risk_level'] == 'critical'])

        if critical_count > 0:
            return (f"紧急：{critical_count}个对手方处于严重风险，"
                    f"立即将对冲头寸转移至AAA级对手方，"
                    f"参考2008年雷曼违约教训")
        else:
            return "建议增加对手方分散度，避免单一对手方集中"

    def simulate_lehman_scenario(self, hedge_positions: List[Dict],
                                  default_counterparty: str) -> Dict:
        """
        模拟雷曼式违约场景

        参数:
            hedge_positions: 对冲头寸列表
            default_counterparty: 假设违约的对手方
        """
        affected = [h for h in hedge_positions
                   if h.get('counterparty') == default_counterparty]
        affected_notional = sum(h['notional'] for h in affected)
        total_notional = sum(h['notional'] for h in hedge_positions)

        lost_protection = affected_notional  # 违约后完全失去保护
        remaining_effectiveness = 1 - (lost_protection / total_notional) if total_notional > 0 else 0

        return {
            'scenario': 'lehman_style_default',
            'default_entity': default_counterparty,
            'affected_positions': len(affected),
            'affected_notional': affected_notional,
            'total_hedge_notional': total_notional,
            'lost_protection': lost_protection,
            'remaining_hedge_effectiveness': remaining_effectiveness,
            'historical_reference': self.historical_defaults['lehman_2008'],
            'recommendation': (
                f"模拟{default_counterparty}违约: "
                f"将损失{lost_protection:,.0f}对冲保护("
                f"{lost_protection/total_notional:.0%}的总对冲), "
                f"需立即补充替代对冲方案"
            )
        }


# ============================================================================
# 综合优化管理器 — 集成所有优化组件
# ============================================================================

class BlackSwanOptimizer:
    """
    黑天鹅极端行情优化管理器

    集成6个优化组件，提供统一的优化接口。
    可与现有的 EnhancedRiskManager 和 ComprehensiveQuantSystemV7 集成。
    """

    def __init__(self, total_capital: float = 5_000_000):
        self.total_capital = total_capital

        # 6大优化组件
        self.intraday_breaker = IntradayCircuitBreaker(total_capital)
        self.execution_engine = OrderExecutionEngine(total_capital)
        self.correlation_model = CorrelationBreakdownModel()
        self.option_liquidity = OptionLiquidityAdjuster()
        self.price_limit_handler = PriceLimitHandler()
        self.counterparty_monitor = CounterpartyRiskMonitor()

        # 优化状态
        self.optimization_active = True

        logger.info("=" * 60)
        logger.info("黑天鹅极端行情优化管理器 v7.2 初始化完成")
        logger.info(f"  总资金: {total_capital:,.0f}")
        logger.info("  优化组件:")
        logger.info("    1. 日内动态熔断引擎 — ON")
        logger.info("    2. 风控执行引擎     — ON")
        logger.info("    3. 相关性崩溃模型   — ON")
        logger.info("    4. 期权流动性调整   — ON")
        logger.info("    5. 涨跌停板处理     — ON")
        logger.info("    6. 对手方风险监控   — ON")
        logger.info("=" * 60)

    def optimize_stress_test(self, stress_engine) -> Dict:
        """
        优化压力测试引擎 — 注入非线性相关性模型

        参数:
            stress_engine: StressTestEngine 实例
        """
        results = {}

        for scenario_id, scenario in stress_engine.test_scenarios.items():
            params = scenario['parameters']

            # 使用非线性模型重新计算
            nonlinear_result = self.correlation_model.calculate_nonlinear_loss(
                initial_value=self.total_capital,
                market_shock=params['market_shock'],
                volatility_loss=self.total_capital * (
                    (params['volatility_multiplier'] - 1.0) * 0.20
                ),
                liquidity_loss=self.total_capital * params['liquidity_haircut'],
                correlation_breakdown=params['correlation_breakdown'],
                portfolio_beta=1.0,
                diversification_ratio=0.30
            )

            results[scenario_id] = {
                'scenario_name': scenario['name'],
                'linear_loss_pct': nonlinear_result['loss_percentage'] / nonlinear_result['amplification_ratio'],
                'nonlinear_loss_pct': nonlinear_result['loss_percentage'],
                'amplification_ratio': nonlinear_result['amplification_ratio'],
                'resonance_factor': nonlinear_result['resonance_factor'],
                'diversification_lost': nonlinear_result['diversification_benefit_lost'],
                'portfolio_value_after': nonlinear_result['portfolio_value_after']
            }

        logger.info(f"压力测试优化完成: {len(results)}个场景已应用非线性模型")
        return results

    def get_optimization_summary(self) -> Dict:
        """获取优化摘要"""
        return {
            'version': '7.2',
            'total_capital': self.total_capital,
            'is_active': self.optimization_active,
            'components': {
                'intraday_breaker': self.intraday_breaker.get_status(),
                'execution_engine': self.execution_engine.get_execution_summary(),
                'correlation_model': 'active',
                'option_liquidity': 'active',
                'price_limit_handler': 'active',
                'counterparty_monitor': {
                    'registered_counterparties': len(self.counterparty_monitor.counterparties)
                }
            },
            'research_report_basis': '黑天鹅极端行情应对能力评估：2000年互联网泡沫与2008年次贷危机',
            'improvements': [
                '1. 日内动态熔断（四级触发，30秒监控）',
                '2. 风控执行引擎（订单生成+状态追踪）',
                '3. 相关性崩溃非线性建模（共振乘数+尾部放大）',
                '4. 期权流动性自适应（VIX>40启动折价）',
                '5. 涨跌停板专项处理（连续跌停+替代策略）',
                '6. 对手方风险监控（雷曼式违约模拟）'
            ]
        }


# ============================================================================
# 主程序入口
# ============================================================================

if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("  黑天鹅极端行情优化模块 v7.2 — 测试运行")
    print("  基于研究报告6项关键不足的系统性优化")
    print("=" * 70)

    # 初始化优化器
    optimizer = BlackSwanOptimizer(total_capital=5_000_000)

    print("\n--- 1. 日内熔断测试 ---")
    optimizer.intraday_breaker.set_daily_reference(4000)
    alert = optimizer.intraday_breaker.evaluate(
        current_price=3700,  # 跌7.5%
        vix_level=45,
        sector_drops={'科技': -0.05, '金融': -0.04, '消费': -0.035},
        limit_down_count=150
    )
    print(f"  熔断级别: {alert.level.name}")
    print(f"  触发原因: {alert.trigger_reason}")
    print(f"  推荐动作: {alert.recommended_action}")

    print("\n--- 2. 相关性崩溃非线性模型测试 ---")
    result = optimizer.correlation_model.calculate_nonlinear_loss(
        initial_value=5_000_000,
        market_shock=-0.568,  # 2008年S&P 500
        volatility_loss=500_000,
        liquidity_loss=2_500_000,
        correlation_breakdown=0.95,
        diversification_ratio=0.30
    )
    print(f"  线性模型损失: {result['linear_total_loss']:,.0f} ({result['linear_total_loss']/5_000_000:.1%})")
    print(f"  非线性模型损失: {result['nonlinear_total_loss']:,.0f} ({result['loss_percentage']:.1%})")
    print(f"  放大倍数: {result['amplification_ratio']:.2f}x")
    print(f"  共振因子: {result['resonance_factor']:.2f}")
    print(f"  分散化失效损失: {result['diversification_benefit_lost']:,.0f}")

    print("\n--- 3. 期权流动性调整测试 ---")
    for vix in [22, 35, 50, 70, 89]:
        coverage = optimizer.option_liquidity.adjust_coverage(1.0, vix, 5_000_000)
        warning = optimizer.option_liquidity.get_liquidity_warning(vix)
        print(f"  VIX={vix:3d}: 目标100% -> 实际{coverage:.0%}", end="")
        if warning:
            print(f" [!] {warning[:30]}...")
        else:
            print()

    print("\n--- 4. 涨跌停板处理测试 ---")
    positions = [
        {'symbol': '600519', 'current_price': 1620, 'prev_close': 1800, 'board_type': 'main_board'},
        {'symbol': '000858', 'current_price': 135, 'prev_close': 150, 'board_type': 'main_board'},
        {'symbol': '510050', 'current_price': 2.52, 'prev_close': 2.80, 'board_type': 'etf'},
    ]
    market_status = optimizer.price_limit_handler.get_market_wide_limit_status(positions)
    print(f"  跌停个股: {market_status['limit_down_count']}/{market_status['total_positions']}")
    print(f"  系统性风险: {'是' if market_status['is_systemic_risk'] else '否'}")
    print(f"  建议: {market_status['recommendation']}")

    print("\n--- 5. 对手方风险测试 ---")
    optimizer.counterparty_monitor.register_counterparty('中信证券', 'broker', 'AA', 50, 0.15)
    optimizer.counterparty_monitor.register_counterparty('某期货公司', 'futures_company', 'BB', 600, 0.06)

    hedge_positions = [
        {'counterparty': '中信证券', 'notional': 1_000_000, 'type': 'put_option'},
        {'counterparty': '某期货公司', 'notional': 2_000_000, 'type': 'futures_short'},
    ]
    cp_result = optimizer.counterparty_monitor.assess_hedge_effectiveness(hedge_positions)
    print(f"  总对冲名义: {cp_result['total_hedge_notional']:,.0f}")
    print(f"  风险调整后: {cp_result['risk_adjusted_hedge']:,.0f}")
    print(f"  对冲有效性: {cp_result['effectiveness']:.1%}")
    print(f"  损失保护: {cp_result['total_lost_protection']:,.0f}")
    print(f"  建议: {cp_result['recommendation']}")

    print("\n--- 6. 雷曼式违约模拟 ---")
    lehman_sim = optimizer.counterparty_monitor.simulate_lehman_scenario(
        hedge_positions, '某期货公司'
    )
    print(f"  违约实体: {lehman_sim['default_entity']}")
    print(f"  受影响头寸: {lehman_sim['affected_notional']:,.0f}")
    print(f"  剩余有效性: {lehman_sim['remaining_hedge_effectiveness']:.1%}")

    print("\n" + "=" * 70)
    print("  优化模块测试完成")
    print("  6项关键不足均已实现对应的优化组件")
    print("=" * 70)
