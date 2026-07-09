# -*- coding: utf-8 -*-
"""
v7.5 熔断引擎 —— 继承 v7.2 四级熔断 + 执行层滑点熔断

两类熔断:
    1. 市场熔断 (CircuitBreaker.check): 基于组合跌幅 + VIX 的 4 级响应
    2. 数据/服务熔断 (is_available/record_failure): 基于失败次数的连接熔断
    3. 滑点熔断 (SlippageCircuitBreaker): 执行层专用
"""

import time
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from threading import RLock
from typing import Dict, Optional
from enum import IntEnum

logger = logging.getLogger('v7.5.circuit_breaker')


class CircuitLevel(IntEnum):
    """市场熔断 4 级响应"""
    NORMAL = 0       # 正常
    LEVEL_1 = 1      # 跌 3-5%: 仅限减仓, 禁开新仓
    LEVEL_2 = 2      # 跌 5-7%: 强制减仓 30%
    LEVEL_3 = 3      # 跌 7%+ 或 VIX>=60+跌5%+: 强制减仓 50%, 加对冲
    LEVEL_4 = 4      # VIX>80: 全市场熔断, 仅允许平仓


class CircuitBreakerState:
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """熔断器 (兼容数据服务熔断 + 市场跌幅熔断)"""

    # 市场熔断阈值
    DROP_L1 = 0.03       # 3%
    DROP_L2 = 0.05       # 5%
    DROP_L3 = 0.07       # 7%
    VIX_L4 = 80.0        # VIX > 80 → LEVEL_4
    VIX_ESCALATE = 60.0  # VIX >= 60 且 跌 > 5% → LEVEL_3

    def __init__(self,
                 failure_threshold: int = 3,
                 cooldown_seconds: float = 300.0,
                 half_open_max_calls: int = 1):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.half_open_max_calls = half_open_max_calls
        self._states: Dict[str, Dict] = {}
        self._lock = RLock()

        # 市场熔断状态
        self.current_level: CircuitLevel = CircuitLevel.NORMAL
        self.last_check_ts: Optional[datetime] = None

    # ---------- 市场熔断 API ----------
    def check(self,
              portfolio_drop: float = 0.0,
              vix: float = 0.0) -> CircuitLevel:
        """
        基于组合跌幅和 VIX 判定熔断级别 (简化版, 向后兼容)

        Args:
            portfolio_drop: 组合当日跌幅 (正数, 如 0.04 表示跌 4%)
            vix: VIX 指数 (0 表示未提供)

        Returns:
            CircuitLevel 枚举
        """
        drop = float(abs(portfolio_drop))
        vix = float(vix) if vix else 0.0

        # LEVEL_4: VIX > 80 全市场熔断
        if vix > self.VIX_L4:
            level = CircuitLevel.LEVEL_4
        # LEVEL_3: 跌 >= 7% 或 (VIX >= 60 且 跌 > 5%)
        elif drop >= self.DROP_L3:
            level = CircuitLevel.LEVEL_3
        elif vix >= self.VIX_ESCALATE and drop > self.DROP_L2:
            level = CircuitLevel.LEVEL_3
        # LEVEL_2: 跌 >= 5%
        elif drop >= self.DROP_L2:
            level = CircuitLevel.LEVEL_2
        # LEVEL_1: 跌 >= 3%
        elif drop >= self.DROP_L1:
            level = CircuitLevel.LEVEL_1
        else:
            level = CircuitLevel.NORMAL

        self.current_level = level
        self.last_check_ts = datetime.now()

        if level > CircuitLevel.NORMAL:
            logger.warning(f"[市场熔断] drop={drop:.2%} vix={vix:.1f} → {level.name}")

        return level

    # ---------- 7.4 移植: 四维熔断 (HWM 回撤 + weekly_drop + VIX + daily_drop) ----------
    # 来源: auto_hedge_executor.py:556-590 (7.4 _fallback_evaluate)
    DD_L2 = 0.10           # HWM 回撤 10% → LEVEL_2 (7.4 black_swan_optimizer.py:109)
    DD_L3 = 0.15           # HWM 回撤 15% → LEVEL_3 (7.4 black_swan_optimizer.py:110)
    DD_L4 = 0.20           # HWM 回撤 20% → LEVEL_4 (7.4 black_swan_optimizer.py:111)
    WEEKLY_DROP_L2 = 0.10  # 周累计跌幅 10% → LEVEL_2 (7.4 auto_hedge_executor.py:574)
    DAILY_DROP_L4 = 0.09   # 单日跌幅 9% → LEVEL_4 (7.4 auto_hedge_executor.py:556)
    DAILY_DROP_L3 = 0.07   # 单日跌幅 7% → LEVEL_3 (7.4 auto_hedge_executor.py:565)
    DAILY_DROP_L2 = 0.05   # 单日跌幅 5% → LEVEL_2 (7.4 auto_hedge_executor.py:574)

    def check_advanced(self,
                       daily_drop: float = 0.0,
                       hwm_drawdown: float = 0.0,
                       weekly_drop: float = 0.0,
                       vix: float = 0.0) -> CircuitLevel:
        """
        7.4 移植: 四维熔断判定 (HWM 累计回撤 + 周累计跌幅 + VIX + 当日跌幅)

        相比 check() 的改进:
            - HWM 累计回撤触发 (10%/15%/20%) 抓住"温水煮青蛙"式累积下跌
            - weekly_drop 触发 周内连续跌幅
            - 多维度 OR 触发, 任一维度达到阈值即升级

        Args:
            daily_drop: 当日跌幅 (正数)
            hwm_drawdown: 距历史高点的累计回撤 (正数)
            weekly_drop: 近 5 日累计跌幅 (正数)
            vix: VIX 指数

        Returns:
            CircuitLevel 枚举
        """
        dd = float(abs(daily_drop))
        hwm_dd = float(abs(hwm_drawdown))
        w_drop = float(abs(weekly_drop))
        vix = float(vix) if vix else 0.0

        # LEVEL_4: HWM>=20% 或 VIX>=80 或 单日>=9%
        if hwm_dd >= self.DD_L4 or vix >= self.VIX_L4 or dd >= self.DAILY_DROP_L4:
            level = CircuitLevel.LEVEL_4
        # LEVEL_3: HWM>=15% 或 VIX>=60 或 单日>=7%
        elif hwm_dd >= self.DD_L3 or vix >= self.VIX_ESCALATE or dd >= self.DAILY_DROP_L3:
            level = CircuitLevel.LEVEL_3
        # LEVEL_2: HWM>=10% 或 单日>=5% 或 周累计>=10%
        elif hwm_dd >= self.DD_L2 or dd >= self.DAILY_DROP_L2 or w_drop >= self.WEEKLY_DROP_L2:
            level = CircuitLevel.LEVEL_2
        # LEVEL_1: 单日 >= 3%
        elif dd >= self.DROP_L1:
            level = CircuitLevel.LEVEL_1
        else:
            level = CircuitLevel.NORMAL

        self.current_level = level
        self.last_check_ts = datetime.now()

        if level > CircuitLevel.NORMAL:
            logger.warning(
                f"[市场熔断-7.4] daily={dd:.2%} hwm={hwm_dd:.2%} "
                f"weekly={w_drop:.2%} vix={vix:.1f} → {level.name}"
            )

        return level

    # ---------- 7.4 移植: 分级对冲/减仓目标比例 ----------
    # 来源: auto_hedge_executor.py:604-611 (target_equity_ratio) + 752-759 (target_hedge_ratio)
    HEDGE_RATIO_BY_LEVEL = {
        CircuitLevel.NORMAL: 0.30,    # 基础对冲 30%
        CircuitLevel.LEVEL_1: 0.30,   # 维持 30%
        CircuitLevel.LEVEL_2: 0.50,   # 提升至 50%
        CircuitLevel.LEVEL_3: 0.70,   # 提升至 70%
        CircuitLevel.LEVEL_4: 0.90,   # 极端 90%
    }
    EQUITY_RATIO_BY_LEVEL = {
        CircuitLevel.NORMAL: 1.00,    # 满仓
        CircuitLevel.LEVEL_1: 0.50,   # 7.4 black_swan_optimizer.py:109 降到 50%
        CircuitLevel.LEVEL_2: 0.45,   # 7.4 auto_hedge_executor.py:607
        CircuitLevel.LEVEL_3: 0.30,   # 7.4 auto_hedge_executor.py:606
        CircuitLevel.LEVEL_4: 0.20,   # 7.4 auto_hedge_executor.py:605
    }

    def target_hedge_ratio(self) -> float:
        """7.4 移植: 当前熔断级别对应的目标对冲比例"""
        return self.HEDGE_RATIO_BY_LEVEL.get(self.current_level, 0.30)

    def target_equity_ratio(self) -> float:
        """7.4 移植: 当前熔断级别对应的目标权益占比"""
        return self.EQUITY_RATIO_BY_LEVEL.get(self.current_level, 1.00)

    def allowed_actions(self) -> Dict[str, object]:
        """根据当前熔断级别返回允许的操作"""
        lvl = self.current_level

        if lvl == CircuitLevel.NORMAL:
            return {
                "open_new": True,
                "reduce": True,
                "hedge": True,
                "force_reduce_pct": 0.0,
                "level": lvl.name,
            }
        if lvl == CircuitLevel.LEVEL_1:
            return {
                "open_new": False,     # 禁开新仓
                "reduce": True,
                "hedge": True,
                "force_reduce_pct": 0.0,
                "level": lvl.name,
            }
        if lvl == CircuitLevel.LEVEL_2:
            return {
                "open_new": False,
                "reduce": True,
                "hedge": True,
                "force_reduce_pct": 0.30,   # 强制减仓 30%
                "level": lvl.name,
            }
        if lvl == CircuitLevel.LEVEL_3:
            return {
                "open_new": False,
                "reduce": True,
                "hedge": True,
                "force_reduce_pct": 0.50,   # 强制减仓 50%
                "level": lvl.name,
            }
        # LEVEL_4
        return {
            "open_new": False,
            "reduce": True,                # 仅允许平仓
            "hedge": True,
            "force_reduce_pct": 1.0,       # 全部平仓
            "level": lvl.name,
        }

    # ---------- 数据服务熔断 API ----------
    def _get_state(self, source: str) -> Dict:
        if source not in self._states:
            self._states[source] = {
                "state": CircuitBreakerState.CLOSED,
                "failures": 0,
                "last_failure_time": 0.0,
                "half_open_calls": 0,
            }
        return self._states[source]

    def is_available(self, source: str) -> bool:
        with self._lock:
            s = self._get_state(source)
            now = time.time()

            if s["state"] == CircuitBreakerState.CLOSED:
                return True

            if s["state"] == CircuitBreakerState.OPEN:
                if now - s["last_failure_time"] >= self.cooldown_seconds:
                    s["state"] = CircuitBreakerState.HALF_OPEN
                    s["half_open_calls"] = 0
                    s["last_failure_time"] = now
                    logger.info(f"[熔断] {source} 冷却完成, 进入半开")
                else:
                    return False

            if s["state"] == CircuitBreakerState.HALF_OPEN:
                if s["half_open_calls"] < self.half_open_max_calls:
                    s["half_open_calls"] += 1
                    return True
                return False
            return True

    def record_success(self, source: str):
        with self._lock:
            s = self._get_state(source)
            s["state"] = CircuitBreakerState.CLOSED
            s["failures"] = 0
            s["half_open_calls"] = 0

    def record_failure(self, source: str, error: Optional[str] = None):
        with self._lock:
            s = self._get_state(source)
            s["failures"] += 1
            s["last_failure_time"] = time.time()
            if s["state"] == CircuitBreakerState.HALF_OPEN:
                s["state"] = CircuitBreakerState.OPEN
                s["half_open_calls"] = 0
            elif s["failures"] >= self.failure_threshold:
                s["state"] = CircuitBreakerState.OPEN
                logger.warning(f"[熔断] {source} 连续失败 {s['failures']} 次, 进入熔断")

    def reset(self, source: Optional[str] = None):
        with self._lock:
            if source:
                self._states.pop(source, None)
            else:
                self._states.clear()
        self.current_level = CircuitLevel.NORMAL
        self.last_check_ts = None


class SlippageCircuitBreaker:
    """滑点熔断 —— 执行层专用"""

    def __init__(self,
                 per_trade_break: float = 0.005,
                 daily_cumulative_break: float = 0.010,
                 global_slow_threshold: float = 0.003,
                 pause_minutes: int = 30):
        self.per_trade_break = per_trade_break
        self.daily_cumulative_break = daily_cumulative_break
        self.global_slow_threshold = global_slow_threshold
        self.pause_minutes = pause_minutes

        self.slip_per_symbol = defaultdict(float)
        self.slip_pause_until: Dict[str, datetime] = {}
        self.global_slips = []
        self.global_slow = False
        self.consecutive_pauses = defaultdict(int)

    def check_single(self, symbol: str, actual_price: float,
                     decision_price: float) -> Dict:
        """检查单笔滑点"""
        slip = abs(actual_price - decision_price) / decision_price
        result = {'symbol': symbol, 'slip': slip, 'action': 'PASS'}

        if slip > self.per_trade_break:
            self.slip_pause_until[symbol] = datetime.now() + timedelta(minutes=self.pause_minutes)
            self.consecutive_pauses[symbol] += 1
            result['action'] = 'BREAK'
            result['reason'] = f'滑点 {slip:.4%} > {self.per_trade_break:.4%}'
            logger.warning(f"[滑点熔断] {symbol} 单笔滑点 {slip:.4%}")

        self.slip_per_symbol[symbol] += slip
        if self.slip_per_symbol[symbol] > self.daily_cumulative_break:
            self.slip_pause_until[symbol] = datetime.now() + timedelta(minutes=self.pause_minutes)
            result['action'] = 'DAILY_BREAK'
            result['reason'] = f'当日累计滑点 {self.slip_per_symbol[symbol]:.4%} > {self.daily_cumulative_break:.4%}'

        self.global_slips.append(slip)
        return result

    def check_global(self) -> bool:
        """全市场滑点检查"""
        if len(self.global_slips) < 5:
            return False
        median_slip = sorted(self.global_slips[-20:])[len(self.global_slips[-20:]) // 2]
        if median_slip > self.global_slow_threshold:
            self.global_slow = True
            logger.warning(f"[全局降速] 全市场滑点中位数 {median_slip:.4%} > {self.global_slow_threshold:.4%}")
            return True
        self.global_slow = False
        return False

    def is_paused(self, symbol: str) -> bool:
        until = self.slip_pause_until.get(symbol)
        return until is not None and datetime.now() < until

    def reset_daily(self):
        self.slip_per_symbol.clear()
        self.slip_pause_until.clear()
        self.global_slips.clear()
        self.global_slow = False
