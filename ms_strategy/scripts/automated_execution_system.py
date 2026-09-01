"""
自动化执行系统 - 世界级对冲基金的自动化交易执行架构

系统特点：
- 7:00AM自动执行：精确的时间控制，确保按时执行
- 智能订单路由：基于市场状况和交易成本的最优订单路由
- 滑点控制：多层级滑点控制，确保执行质量
- 分层执行策略：基于市场状态的分层执行策略
- 实时执行监控：执行过程的实时监控和异常处理
- 自动恢复机制：执行失败后的自动重试和恢复

核心功能：
1. 定时执行控制：精确的定时执行和日历管理
2. 市场状态评估：基于市场状况的执行策略选择
3. 订单生成和路由：智能订单生成和路由
4. 执行质量控制：多层级执行质量控制
5. 异常处理：全面的异常处理和恢复机制
6. 性能监控：执行性能监控和分析
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from datetime import time as datetime_time

import numpy as np
import pandas as pd

# schedule 模块为可选依赖 (本文件实际未使用其 API, 仅保留 import 以兼容旧代码)
try:
    import schedule  # noqa: F401
except ImportError:
    schedule = None
import os
import sys

# 确保 v7.5_institutional 对冲模块可导入
_V7_5_SRC = os.path.join(os.path.dirname(__file__), "v7.5_institutional", "src")
if _V7_5_SRC not in sys.path:
    sys.path.insert(0, _V7_5_SRC)

try:
    from hedging.hedge_coordinator import HedgeCoordinator
    _HEDGE_AVAILABLE = True
except ImportError:
    # 导入降级: hedging 模块缺失时关闭对冲功能
    HedgeCoordinator = None  # type: ignore[assignment]
    _HEDGE_AVAILABLE = False

try:
    from utils.data_provider import MarketDataProvider, get_market_data  # noqa: F401
    from utils.data_types import safe_float
    from utils.logger import get_logger
    from utils.order_execution import cancel_order, execute_order  # noqa: F401
    from utils.risk_metrics import calculate_es, calculate_var  # noqa: F401
    logger = get_logger('automated_execution_system')
except ImportError:
    import logging
    logger = logging.getLogger('automated_execution_system')
    def safe_float(x, default=None):
        return x if x is not None else default

try:
    from wind_mcp_fetcher import wind_get_quote
    _WIND_MCP_AVAILABLE = True
except ImportError:
    # 导入降级: wind_mcp_fetcher 缺失时关闭 Wind MCP 行情功能
    wind_get_quote = None  # type: ignore[assignment]
    _WIND_MCP_AVAILABLE = False


def _eye(n: int):
    """生成 n×n 单位矩阵 (numpy 降级: 纯 Python 实现)."""
    try:
        import numpy as np
        return np.eye(n)
    except ImportError:
        return [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]


def _to_wind_code(symbol: str):
    s = str(symbol).strip()
    for prefix in ("sh", "sz", "bj", "SH", "SZ", "BJ"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    for suffix in (".SH", ".SZ", ".BJ", ".sh", ".sz", ".bj"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    if s.startswith(("51", "58")):
        return f"{s}.SH", True
    if s.startswith(("15", "16")):
        return f"{s}.SZ", True
    if s.startswith(("00", "30")):
        return f"{s}.SZ", False
    if s.startswith("6"):
        return f"{s}.SH", False
    if s.startswith(("4", "8")):
        return f"{s}.BJ", False
    return f"{s}.SH", False

class TradingCalendar:
    """
    交易日历管理器
    """

    def __init__(self):
        self.trading_schedule = {
            'morning_open': datetime_time(7, 0),      # 7:00 AM
            'morning_close': datetime_time(11, 30),   # 11:30 AM
            'afternoon_open': datetime_time(13, 0),   # 1:00 PM
            'afternoon_close': datetime_time(15, 0),  # 3:00 PM
            'executions': [
                {'time': datetime_time(7, 0), 'name': 'daily_execution'},  # 7:00 AM
                {'time': datetime_time(10, 0), 'name': 'morning_review'},  # 10:00 AM
                {'time': datetime_time(14, 0), 'name': 'afternoon_adjustment'}  # 2:00 PM
            ]
        }

        # 特殊交易日处理 (2025-2027 中国A股休市日)
        self.special_days = {
            # === 2025 ===
            "2025-01-01": {"is_trading": False, "name": "元旦"},
            "2025-01-28": {"is_trading": False, "name": "春节除夕"},
            "2025-01-29": {"is_trading": False, "name": "春节"},
            "2025-01-30": {"is_trading": False, "name": "春节"},
            "2025-01-31": {"is_trading": False, "name": "春节"},
            "2025-02-03": {"is_trading": False, "name": "春节调休"},
            "2025-04-04": {"is_trading": False, "name": "清明节"},
            "2025-04-05": {"is_trading": False, "name": "清明节"},
            "2025-04-06": {"is_trading": False, "name": "清明节"},
            "2025-05-01": {"is_trading": False, "name": "劳动节"},
            "2025-05-02": {"is_trading": False, "name": "劳动节"},
            "2025-05-05": {"is_trading": False, "name": "劳动节"},
            "2025-05-31": {"is_trading": False, "name": "端午节"},
            "2025-06-02": {"is_trading": False, "name": "端午节调休"},
            "2025-10-01": {"is_trading": False, "name": "国庆节"},
            "2025-10-02": {"is_trading": False, "name": "国庆节"},
            "2025-10-03": {"is_trading": False, "name": "国庆节"},
            "2025-10-06": {"is_trading": False, "name": "国庆节调休"},
            "2025-10-07": {"is_trading": False, "name": "国庆节调休"},
            "2025-10-08": {"is_trading": False, "name": "中秋节"},
            # === 2026 ===
            "2026-01-01": {"is_trading": False, "name": "元旦"},
            "2026-02-16": {"is_trading": False, "name": "春节除夕"},
            "2026-02-17": {"is_trading": False, "name": "春节"},
            "2026-02-18": {"is_trading": False, "name": "春节"},
            "2026-02-19": {"is_trading": False, "name": "春节"},
            "2026-02-20": {"is_trading": False, "name": "春节"},
            "2026-02-23": {"is_trading": False, "name": "春节调休"},
            "2026-02-24": {"is_trading": False, "name": "春节调休"},
            "2026-04-06": {"is_trading": False, "name": "清明节"},
            "2026-05-01": {"is_trading": False, "name": "劳动节"},
            "2026-05-04": {"is_trading": False, "name": "劳动节"},
            "2026-05-05": {"is_trading": False, "name": "劳动节"},
            "2026-06-19": {"is_trading": False, "name": "端午节"},
            "2026-09-25": {"is_trading": False, "name": "中秋节"},
            "2026-10-01": {"is_trading": False, "name": "国庆节"},
            "2026-10-02": {"is_trading": False, "name": "国庆节"},
            "2026-10-05": {"is_trading": False, "name": "国庆节"},
            "2026-10-06": {"is_trading": False, "name": "国庆节"},
            "2026-10-07": {"is_trading": False, "name": "国庆节"},
            "2026-10-08": {"is_trading": False, "name": "国庆节调休"},
            # === 2027 ===
            "2027-01-01": {"is_trading": False, "name": "元旦"},
        }

        # 执行窗口（允许的执行时间范围）
        self.execution_windows = {
            'daily_execution': {
                'start': datetime_time(6, 30),
                'end': datetime_time(8, 0),     # 放宽为 6:30-8:00
                'allow_early': True,
                'allow_late': True,
                'early_minutes': 30,
                'late_minutes': 60
            },
            'morning_review': {
                'start': datetime_time(9, 30),
                'end': datetime_time(10, 30),   # 放宽为 9:30-10:30
                'allow_early': True,
                'allow_late': True,
                'early_minutes': 30,
                'late_minutes': 30
            },
            'afternoon_adjustment': {
                'start': datetime_time(13, 30),
                'end': datetime_time(14, 30),   # 放宽为 13:30-14:30
                'allow_early': True,
                'allow_late': True,
                'early_minutes': 30,
                'late_minutes': 30
            }
        }

        # 历史执行记录
        self.execution_history = deque(maxlen=100)

        logger.info("交易日历初始化完成")

    def is_trading_day(self, date: datetime = None) -> bool:
        """判断是否为交易日"""
        if date is None:
            date = datetime.now()

        # 检查是否为周末
        if date.weekday() >= 5:
            return False

        # 检查是否为特殊交易日
        date_str = date.strftime('%Y-%m-%d')
        if date_str in self.special_days:
            return self.special_days[date_str]['is_trading']

        # 检查是否为节假日（这里简化处理，实际应该从节假日API获取）
        # 简单判断一些常见节假日
        month, day = date.month, date.day
        if (month == 1 and day in [1, 2, 3]) or (month == 10 and day == 1):
            return False

        return True

    def is_within_execution_window(self, execution_name: str) -> tuple[bool, str]:
        """判断当前是否在执行窗口内"""
        now = datetime.now().time()
        window = self.execution_windows.get(execution_name)

        if not window:
            return False, f"未知的执行类型: {execution_name}"

        # 检查是否在窗口内
        if window['start'] <= now <= window['end']:
            return True, "在执行窗口内"

        # 检查是否允许提前执行
        if window.get('allow_early') and now < window['start']:
            early_minutes = window.get('early_minutes', 15)
            time_diff = (datetime.combine(datetime.min, window['start']) -
                        datetime.combine(datetime.min, now)).total_seconds()
            if time_diff <= early_minutes * 60:
                return True, "允许提前执行"

        # 检查是否允许延后执行
        if window.get('allow_late') and now > window['end']:
            late_minutes = window.get('late_minutes', 15)
            time_diff = (datetime.combine(datetime.min, now) -
                        datetime.combine(datetime.min, window['end'])).total_seconds()
            if time_diff <= late_minutes * 60:
                return True, "允许延后执行"

        return False, "不在执行窗口内"

    def get_next_execution_time(self) -> datetime | None:
        """获取下次执行时间"""
        now = datetime.now()

        # 如果不是交易日，返回下一个交易日
        if not self.is_trading_day(now):
            next_day = now + timedelta(days=1)
            while not self.is_trading_day(next_day):
                next_day += timedelta(days=1)
            return next_day.replace(
                hour=self.trading_schedule['morning_open'].hour,
                minute=self.trading_schedule['morning_open'].minute
            )

        today = now.date()

        # 优先检查今天剩余执行
        for execution in self.trading_schedule['executions']:
            execution_time = datetime.combine(today, execution['time'])
            if execution_time > now:
                return execution_time

        # 否则返回明天最早的执行
        tomorrow = now + timedelta(days=1)
        while not self.is_trading_day(tomorrow):
            tomorrow += timedelta(days=1)
        return datetime.combine(
            tomorrow,
            self.trading_schedule['executions'][0]['time']
        )

    def get_execution_schedule(self, days_ahead: int = 7) -> list[dict]:
        """获取未来几天的执行计划"""
        schedule = []
        now = datetime.now()

        for i in range(days_ahead):
            date = now + timedelta(days=i)
            if self.is_trading_day(date):
                day_schedule = {
                    'date': date.strftime('%Y-%m-%d'),
                    'is_trading': True,
                    'executions': []
                }

                for execution in self.trading_schedule['executions']:
                    execution_time = datetime.combine(date, execution['time'])
                    day_schedule['executions'].append({
                        'name': execution['name'],
                        'time': execution_time.isoformat(),
                        'timestamp': execution_time.timestamp()
                    })

                schedule.append(day_schedule)

        return schedule

    def record_execution(self, execution_name: str, start_time: datetime,
                       end_time: datetime, success: bool, details: dict):
        """记录执行历史"""
        record = {
            'timestamp': datetime.now().isoformat(),
            'execution_name': execution_name,
            'start_time': start_time.isoformat(),
            'end_time': end_time.isoformat(),
            'duration_seconds': (end_time - start_time).total_seconds(),
            'success': success,
            'details': details
        }

        self.execution_history.append(record)
        logger.info(f"执行记录: {execution_name} - {'成功' if success else '失败'}")

    def get_execution_summary(self) -> dict:
        """获取执行总结"""
        if not self.execution_history:
            return {'message': '暂无执行历史'}

        # 最近执行
        latest_execution = self.execution_history[-1]

        # 统计成功率
        total_executions = len(self.execution_history)
        successful_executions = sum(1 for r in self.execution_history if r['success'])
        success_rate = successful_executions / total_executions if total_executions > 0 else 0.0

        # 平均执行时间
        avg_duration = np.mean([
            r['duration_seconds'] for r in self.execution_history
        ])

        # 按执行类型统计
        execution_stats = {}
        for record in self.execution_history:
            exec_name = record['execution_name']
            if exec_name not in execution_stats:
                execution_stats[exec_name] = {
                    'count': 0,
                    'success': 0,
                    'avg_duration': 0.0
                }

            execution_stats[exec_name]['count'] += 1
            if record['success']:
                execution_stats[exec_name]['success'] += 1

            # 更新平均执行时间
            stats = execution_stats[exec_name]
            if stats['count'] > 0:
                stats['avg_duration'] = (
                    (stats['avg_duration'] * (stats['count'] - 1) + record['duration_seconds']) / stats['count']
                )

        return {
            'total_executions': total_executions,
            'latest_execution': latest_execution['execution_name'],
            'latest_time': latest_execution['timestamp'],
            'success_rate': success_rate,
            'average_duration_seconds': avg_duration,
            'execution_stats': execution_stats,
            'next_execution_time': self.get_next_execution_time().isoformat() if self.get_next_execution_time() else None  # noqa: E501
        }

class MarketStateEvaluator:
    """
    市场状态评估器
    """

    def __init__(self):
        # 市场状态定义
        self.market_states = {
            'normal': {
                'description': '正常市场',
                'execution_strategy': 'aggressive',
                'priority': 'normal',
                'risk_tolerance': 'medium'
            },
            'volatile': {
                'description': '高波动市场',
                'execution_strategy': 'conservative',
                'priority': 'high',
                'risk_tolerance': 'low'
            },
            'illiquid': {
                'description': '低流动性市场',
                'execution_strategy': 'patient',
                'priority': 'high',
                'risk_tolerance': 'low'
            },
            'stress': {
                'description': '压力市场',
                'execution_strategy': 'defensive',
                'priority': 'critical',
                'risk_tolerance': 'very_low'
            },
            'crisis': {
                'description': '危机市场',
                'execution_strategy': 'emergency',
                'priority': 'critical',
                'risk_tolerance': 'minimal'
            }
        }

        # 状态评估历史
        self.state_history = deque(maxlen=100)

        # 状态切换阈值
        self.state_thresholds = {
            'volatility_threshold': 0.25,
            'liquidity_threshold': 0.5,
            'var_threshold': 0.05,
            'sentiment_threshold': -0.6,
            'correlation_breakdown': 0.8
        }

        logger.info("市场状态评估器初始化完成")

    def evaluate_market_state(self, market_data: dict) -> dict:
        """
        评估当前市场状态

        Args:
            market_data: 市场数据

        Returns:
            市场状态评估结果
        """
        try:
            logger.info("开始市场状态评估")

            # 提取关键指标
            volatility = market_data.get('volatility', 0.15)
            liquidity = market_data.get('liquidity', 1.0)
            var_95 = market_data.get('var_95', 0.02)
            sentiment = market_data.get('sentiment_score', 0.0)

            # 计算相关性和波动性指标
            # 兼容 list / ndarray 两种输入 (line 1800 返回 list, 但 tolist() 需要 ndarray)
            _corr_raw = market_data.get('correlation_matrix', np.eye(3))
            correlation_matrix = np.array(_corr_raw) if not isinstance(_corr_raw, np.ndarray) else _corr_raw
            correlation_breakdown = self._calculate_correlation_breakdown(correlation_matrix)

            # 评估各个维度
            volatility_score = self._evaluate_volatility(volatility)
            liquidity_score = self._evaluate_liquidity(liquidity)
            var_score = self._evaluate_var(var_95)
            sentiment_score = self._evaluate_sentiment(sentiment)

            # 综合评估
            market_state = self._determine_market_state(
                volatility_score, liquidity_score, var_score,
                sentiment_score, correlation_breakdown
            )

            # 计算状态置信度
            confidence = self._calculate_state_confidence(
                volatility_score, liquidity_score, var_score,
                sentiment_score, correlation_breakdown
            )

            # 生成评估报告
            evaluation_report = {
                'timestamp': datetime.now().isoformat(),
                'market_state': market_state,
                'confidence': confidence,
                'individual_scores': {
                    'volatility': volatility_score,
                    'liquidity': liquidity_score,
                    'var': var_score,
                    'sentiment': sentiment_score,
                    'correlation_breakdown': correlation_breakdown
                },
                'detailed_metrics': {
                    'volatility': volatility,
                    'liquidity': liquidity,
                    'var_95': var_95,
                    'sentiment': sentiment,
                    'correlation_matrix': correlation_matrix.tolist()
                },
                'state_characteristics': self.market_states[market_state]
            }

            # 记录历史
            self.state_history.append(evaluation_report)

            logger.info(f"市场状态评估完成: {market_state} (置信度: {confidence:.2f})")

            return evaluation_report

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:  # noqa: E501

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error(f"市场状态评估失败: {e}")
            return {
                'market_state': 'normal',
                'confidence': 0.0,
                'error': str(e)
            }

    def _evaluate_volatility(self, volatility: float) -> float:
        """评估波动性"""
        if volatility < 0.10:
            return 0.0  # 非常低
        if volatility < 0.20:
            return 0.3  # 低
        if volatility < 0.30:
            return 0.6  # 中等
        if volatility < 0.40:
            return 0.8  # 高
        return 1.0  # 非常高

    def _evaluate_liquidity(self, liquidity: float) -> float:
        """评估流动性"""
        if liquidity > 0.8:
            return 0.0  # 非常高
        if liquidity > 0.6:
            return 0.3  # 高
        if liquidity > 0.4:
            return 0.6  # 中等
        if liquidity > 0.2:
            return 0.8  # 低
        return 1.0  # 非常低

    def _evaluate_var(self, var: float) -> float:
        """评估VaR"""
        if var < 0.02:
            return 0.0  # 非常低
        if var < 0.03:
            return 0.3  # 低
        if var < 0.05:
            return 0.6  # 中等
        if var < 0.08:
            return 0.8  # 高
        return 1.0  # 非常高

    def _evaluate_sentiment(self, sentiment: float) -> float:
        """评估市场情绪"""
        abs_sentiment = abs(sentiment)
        if abs_sentiment < 0.2:
            return 0.0  # 中性
        if abs_sentiment < 0.4:
            return 0.3  # 轻微
        if abs_sentiment < 0.6:
            return 0.6  # 中度
        if abs_sentiment < 0.8:
            return 0.8  # 强烈
        return 1.0  # 极端

    def _calculate_correlation_breakdown(self, correlation_matrix: np.ndarray) -> float:
        """计算相关性崩溃程度"""
        # 计算相关性的标准差
        upper_tri = np.triu(correlation_matrix, k=1)
        valid_correlations = upper_tri[upper_tri != 0]

        if len(valid_correlations) > 0:
            correlation_std = np.std(valid_correlations)
            return min(correlation_std / 0.5, 1.0)  # 归一化到0-1
        return 0.0

    def _determine_market_state(self, volatility_score: float, liquidity_score: float,
                              var_score: float, sentiment_score: float,
                              correlation_breakdown: float) -> str:
        """确定市场状态"""
        # 计算风险分数
        risk_score = (
            volatility_score * 0.3 +
            liquidity_score * 0.3 +
            var_score * 0.2 +
            sentiment_score * 0.1 +
            correlation_breakdown * 0.1
        )

        # 根据风险分数确定状态
        if risk_score >= 0.8:
            return 'crisis'
        if risk_score >= 0.6:
            return 'stress'
        if risk_score >= 0.4:
            return 'illiquid'
        if risk_score >= 0.2:
            return 'volatile'
        return 'normal'

    def _calculate_state_confidence(self, volatility_score: float, liquidity_score: float,
                                  var_score: float, sentiment_score: float,
                                  correlation_breakdown: float) -> float:
        """计算状态置信度"""
        # 基于各指标的极端程度计算置信度
        extreme_indicators = 0
        total_indicators = 5

        if volatility_score >= 0.8:
            extreme_indicators += 1
        if liquidity_score >= 0.8:
            extreme_indicators += 1
        if var_score >= 0.8:
            extreme_indicators += 1
        if sentiment_score >= 0.8:
            extreme_indicators += 1
        if correlation_breakdown >= 0.8:
            extreme_indicators += 1

        # 计算置信度
        confidence = extreme_indicators / total_indicators

        # 考虑历史趋势
        if len(self.state_history) > 2:
            recent_states = [s['market_state'] for s in list(self.state_history)[-3:]]
            if len(set(recent_states)) == 1:  # 最近3个状态相同
                confidence += 0.2

        return min(confidence, 1.0)

    def get_market_state_summary(self) -> dict:
        """获取市场状态总结"""
        if not self.state_history:
            return {'message': '暂无市场状态数据'}

        latest_state = self.state_history[-1]

        # 状态分布统计
        state_distribution = {}
        for state_record in self.state_history:
            state = state_record['market_state']
            state_distribution[state] = state_distribution.get(state, 0) + 1

        # 平均置信度
        avg_confidence = np.mean([s['confidence'] for s in self.state_history])

        # 状态稳定性
        if len(self.state_history) > 5:
            recent_states = [s['market_state'] for s in list(self.state_history)[-10:]]
            state_changes = sum(1 for i in range(1, len(recent_states))
                              if recent_states[i] != recent_states[i-1])
            stability = 1.0 - (state_changes / len(recent_states))
        else:
            stability = 1.0

        return {
            'current_state': latest_state['market_state'],
            'current_confidence': latest_state['confidence'],
            'timestamp': latest_state['timestamp'],
            'state_distribution': state_distribution,
            'average_confidence': avg_confidence,
            'state_stability': stability,
            'total_evaluations': len(self.state_history)
        }

class ExecutionStrategy:
    """
    执行策略控制器
    """

    def __init__(self):
        # 执行策略定义
        self.execution_strategies = {
            'aggressive': {
                'strategy_name': 'aggressive',
                'description': '激进执行',
                'order_type': 'market',
                'execution_style': 'immediate',
                'slice_size': 1.0,
                'timeout_seconds': 30,
                'retry_attempts': 2,
                'slippage_tolerance': 0.01
            },
            'conservative': {
                'strategy_name': 'conservative',
                'description': '保守执行',
                'order_type': 'limit',
                'execution_style': 'sliced',
                'slice_size': 0.3,
                'timeout_seconds': 120,
                'retry_attempts': 3,
                'slippage_tolerance': 0.005
            },
            'patient': {
                'strategy_name': 'patient',
                'description': '耐心执行',
                'order_type': 'limit',
                'execution_style': 'gradual',
                'slice_size': 0.2,
                'timeout_seconds': 300,
                'retry_attempts': 5,
                'slippage_tolerance': 0.003
            },
            'defensive': {
                'strategy_name': 'defensive',
                'description': '防御性执行',
                'order_type': 'limit',
                'execution_style': 'weighted',
                'slice_size': 0.1,
                'timeout_seconds': 600,
                'retry_attempts': 8,
                'slippage_tolerance': 0.002
            },
            'emergency': {
                'strategy_name': 'emergency',
                'description': '紧急执行',
                'order_type': 'market',
                'execution_style': 'immediate',
                'slice_size': 1.0,
                'timeout_seconds': 15,
                'retry_attempts': 1,
                'slippage_tolerance': 0.02
            }
        }

        # 市场状态到执行策略的映射
        self.state_to_strategy = {
            'normal': 'aggressive',
            'volatile': 'conservative',
            'illiquid': 'patient',
            'stress': 'defensive',
            'crisis': 'emergency'
        }

        # 执行历史
        self.execution_history = deque(maxlen=100)

        logger.info("执行策略控制器初始化完成")

    def select_execution_strategy(self, market_state: str, trade_info: dict) -> dict:
        """
        选择执行策略

        Args:
            market_state: 市场状态
            trade_info: 交易信息

        Returns:
            执行策略配置
        """
        try:
            # 基于市场状态选择策略
            strategy_name = self.state_to_strategy.get(market_state, 'conservative')
            strategy_config = self.execution_strategies[strategy_name].copy()

            # 根据交易规模调整策略参数
            trade_size = trade_info.get('trade_size', 0)
            if trade_size > 1000000:  # 大额交易
                strategy_config['slice_size'] = max(0.1, strategy_config['slice_size'] * 0.5)
                strategy_config['timeout_seconds'] = min(600, strategy_config['timeout_seconds'] * 1.5)

            # 根据紧急程度调整
            urgency = trade_info.get('urgency', 'normal')
            if urgency == 'high':
                strategy_config['slice_size'] = min(1.0, strategy_config['slice_size'] * 2)
                strategy_config['timeout_seconds'] = max(10, strategy_config['timeout_seconds'] * 0.5)

            # 根据资产特性调整
            asset_type = trade_info.get('asset_type', 'equity')
            if asset_type == 'bond':
                strategy_config['slice_size'] = min(0.5, strategy_config['slice_size'] * 1.5)
                strategy_config['slippage_tolerance'] = min(0.01, strategy_config['slippage_tolerance'] * 2)

            logger.info(f"选择执行策略: {strategy_name}")

            return {
                'strategy_name': strategy_name,
                'strategy_config': strategy_config,
                'reasoning': f"基于市场状态{market_state}和交易特性选择"
            }

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:  # noqa: E501

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error(f"执行策略选择失败: {e}")
            # 返回默认策略
            return {
                'strategy_name': 'conservative',
                'strategy_config': self.execution_strategies['conservative'].copy(),
                'error': str(e)
            }

    def generate_execution_plan(self, trade_info: dict,
                              strategy_config: dict) -> dict:
        """
        生成执行计划

        Args:
            trade_info: 交易信息
            strategy_config: 策略配置

        Returns:
            执行计划
        """
        try:
            # 计算切片数量
            slice_size = strategy_config['slice_size']
            if slice_size >= 1.0:
                num_slices = 1
            else:
                num_slices = max(1, int(1.0 / slice_size))

            # 生成切片
            trade_size = trade_info.get('trade_size', 0)
            instrument = trade_info.get('instrument', '')
            direction = trade_info.get('direction', 'buy')

            slices = []
            for i in range(num_slices):
                if i == num_slices - 1:  # 最后一片
                    slice_size = trade_size - sum(s['size'] for s in slices)
                else:
                    slice_size = trade_size * strategy_config['slice_size']

                slices.append({
                    'slice_id': i + 1,
                    'size': slice_size,
                    'direction': direction,
                    'instrument': instrument,
                    'price_type': 'limit' if strategy_config['order_type'] == 'limit' else 'market',
                    'priority': 'high' if i == 0 else 'normal',
                    'created_at': datetime.now().isoformat()
                })

            # 计算总超时时间
            timeout_per_slice = strategy_config['timeout_seconds']
            total_timeout = timeout_per_slice * num_slices

            execution_plan = {
                'trade_id': trade_info.get('trade_id', ''),
                'instrument': instrument,
                'total_size': trade_size,
                'total_direction': direction,
                'strategy': strategy_config['strategy_name'],
                'num_slices': num_slices,
                'slices': slices,
                'timeout_seconds': total_timeout,
                'max_retry_attempts': strategy_config['retry_attempts'],
                'slippage_tolerance': strategy_config['slippage_tolerance'],
                'execution_style': strategy_config['execution_style'],
                'created_at': datetime.now().isoformat()
            }

            logger.info(f"执行计划生成完成: {num_slices}个切片")

            return execution_plan

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:  # noqa: E501

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error(f"执行计划生成失败: {e}")
            return {'error': str(e)}

    def record_execution_result(self, execution_plan: dict,
                             execution_result: dict):
        """记录执行结果"""
        record = {
            'timestamp': datetime.now().isoformat(),
            'plan': execution_plan,
            'result': execution_result,
            'success': execution_result.get('success', False),
            'execution_time': execution_result.get('execution_time', 0),
            'actual_slippage': execution_result.get('slippage', 0),
            'retry_attempts': execution_result.get('retry_attempts', 0)
        }

        self.execution_history.append(record)
        logger.info(f"执行结果记录: {'成功' if record['success'] else '失败'}")

    def get_execution_summary(self) -> dict:
        """获取执行总结"""
        if not self.execution_history:
            return {'message': '暂无执行历史'}

        # 最近执行
        latest_execution = self.execution_history[-1]

        # 统计成功率
        total_executions = len(self.execution_history)
        successful_executions = sum(1 for r in self.execution_history if r['success'])
        success_rate = successful_executions / total_executions if total_executions > 0 else 0.0

        # 平均执行时间
        avg_execution_time = np.mean([
            r['execution_time'] for r in self.execution_history if r['execution_time'] > 0
        ])

        # 平均滑点
        avg_slippage = np.mean([
            r['actual_slippage'] for r in self.execution_history
            if r['actual_slippage'] is not None
        ])

        # 按策略统计
        strategy_stats = {}
        for record in self.execution_history:
            strategy = record['plan']['strategy']
            if strategy not in strategy_stats:
                strategy_stats[strategy] = {
                    'count': 0,
                    'success': 0,
                    'avg_time': 0.0,
                    'avg_slippage': 0.0
                }

            stats = strategy_stats[strategy]
            stats['count'] += 1
            if record['success']:
                stats['success'] += 1
            stats['avg_time'] = (
                (stats['avg_time'] * (stats['count'] - 1) + record['execution_time']) / stats['count']
            )
            if record['actual_slippage'] is not None:
                stats['avg_slippage'] = (
                    (stats['avg_slippage'] * (stats['count'] - 1) + record['actual_slippage']) / stats['count']
                )

        return {
            'total_executions': total_executions,
            'success_rate': success_rate,
            'latest_execution': latest_execution['plan']['strategy'],
            'average_execution_time_seconds': avg_execution_time,
            'average_slippage': avg_slippage,
            'strategy_stats': strategy_stats,
            'latest_time': latest_execution['timestamp']
        }

class OrderRouter:
    """
    订单路由器
    """

    def __init__(self):
        # 执行池配置
        self.execution_pools = {
            'normal': {
                'broker': 'broker_a',
                'priority': 'normal',
                'max_concurrent': 10,
                'min_balance': 100000
            },
            'priority': {
                'broker': 'broker_b',
                'priority': 'high',
                'max_concurrent': 5,
                'min_balance': 500000
            },
            'emergency': {
                'broker': 'broker_c',
                'priority': 'critical',
                'max_concurrent': 3,
                'min_balance': 1000000
            }
        }

        # 当前活跃订单
        self.active_orders = {}

        # 执行队列
        self.execution_queue = deque(maxlen=50)

        # 执行统计
        self.execution_stats = {
            'total_orders': 0,
            'successful_orders': 0,
            'failed_orders': 0,
            'average_time': 0.0,
            'average_slippage': 0.0
        }

        logger.info("订单路由器初始化完成")

    def route_order(self, execution_plan: dict, market_state: str) -> dict:
        """
        路由订单到执行池

        Args:
            execution_plan: 执行计划
            market_state: 市场状态

        Returns:
            路由结果
        """
        try:
            # 根据市场状态选择执行池
            if market_state in ['crisis', 'stress']:
                pool_name = 'emergency'
            elif market_state == 'illiquid':
                pool_name = 'priority'
            else:
                pool_name = 'normal'

            pool = self.execution_pools[pool_name]

            # 检查执行池可用性
            if not self._check_pool_availability(pool):
                # 如果当前池不可用，尝试其他池
                available_pool = self._find_available_pool()
                if available_pool:
                    pool = available_pool
                    pool_name = list(self.execution_pools.keys())[
                        list(self.execution_pools.values()).index(available_pool)
                    ]
                else:
                    return {
                        'success': False,
                        'error': '无可用执行池',
                        'suggested_action': '等待'
                    }

            # 为每个切片生成订单
            routed_orders = []
            for slice_info in execution_plan['slices']:
                order_id = self._generate_order_id()

                order = {
                    'order_id': order_id,
                    'slice_info': slice_info,
                    'execution_plan': execution_plan,
                    'target_pool': pool_name,
                    'priority': pool['priority'],
                    'created_at': datetime.now().isoformat(),
                    'status': 'pending',
                    'retry_count': 0
                }

                routed_orders.append(order)

            # 更新活跃订单
            for order in routed_orders:
                self.active_orders[order['order_id']] = order

            # 加入执行队列
            for order in routed_orders:
                self.execution_queue.append(order)

            logger.info(f"订单路由完成: {len(routed_orders)}个订单到{pool_name}池")

            return {
                'success': True,
                'routed_orders': routed_orders,
                'target_pool': pool_name,
                'estimated_wait_time': self._estimate_wait_time(pool_name)
            }

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:  # noqa: E501

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error(f"订单路由失败: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    def _check_pool_availability(self, pool: dict) -> bool:
        """检查执行池可用性"""
        # 检查并发限制
        active_count = sum(1 for order in self.active_orders.values()
                          if order.get('target_pool') == list(self.execution_pools.values()).index(pool))

        if active_count >= pool['max_concurrent']:
            return False

        # 检查余额限制（简化处理）
        # 实际应该查询真实的账户余额
        return True

    def _find_available_pool(self) -> dict | None:
        """查找可用的执行池"""
        for pool in self.execution_pools.values():
            if self._check_pool_availability(pool):
                return pool
        return None

    def _generate_order_id(self) -> str:
        """生成订单ID"""
        return f"ORD_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{np.random.randint(1000, 9999)}"

    def _estimate_wait_time(self, pool_name: str) -> float:
        """估算等待时间"""
        pool = self.execution_pools[pool_name]

        # 基础等待时间
        base_wait = 10.0

        # 加上当前活跃订单的影响
        active_count = sum(1 for order in self.active_orders.values()
                          if order.get('target_pool') == pool_name)
        queue_wait = active_count * pool['max_concurrent'] * 5.0

        return base_wait + queue_wait

    def process_execution_queue(self):
        """处理执行队列"""
        try:
            while self.execution_queue:
                # 获取下一个订单
                order = self.execution_queue[0]

                # 检查是否可以执行
                if self._can_execute_order(order):
                    # 执行订单
                    execution_result = self._execute_order(order)

                    # 更新订单状态
                    if execution_result['success']:
                        order['status'] = 'completed'
                        order['completed_at'] = datetime.now().isoformat()
                        order['execution_result'] = execution_result
                    else:
                        order['status'] = 'failed'
                        order['error'] = execution_result.get('error', '未知错误')
                        order['retry_count'] += 1

                        # 重试逻辑
                        if order['retry_count'] < 3:
                            order['status'] = 'pending'
                        else:
                            order['status'] = 'abandoned'

                    # 从队列中移除
                    self.execution_queue.popleft()

                    # 更新统计
                    self._update_execution_stats(execution_result)

                    logger.info(f"订单处理完成: {order['order_id']} - {order['status']}")

                else:
                    # 暂停处理
                    break

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:  # noqa: E501

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error(f"执行队列处理失败: {e}")

    def _can_execute_order(self, order: dict) -> bool:
        """检查是否可以执行订单"""
        # 检查执行池可用性
        pool_name = order.get('target_pool', 'normal')
        pool = self.execution_pools.get(pool_name)

        if not pool:
            return False

        # 检查并发限制
        active_count = sum(1 for o in self.active_orders.values()
                          if o.get('target_pool') == pool_name and o.get('status') == 'pending')

        if active_count >= pool['max_concurrent']:
            return False

        return True

    def _execute_order(self, order: dict) -> dict:
        """执行单个订单"""
        try:
            # 模拟订单执行
            slice_info = order['slice_info']

            # 模拟执行延迟
            time.sleep(np.random.uniform(0.1, 0.5))

            # 模拟执行结果
            execution_time = np.random.uniform(5, 20)
            slippage = np.random.uniform(0, 0.01)

            return {
                'success': True,
                'execution_time': execution_time,
                'slippage': slippage,
                'filled_size': slice_info['size'],
                'average_price': slice_info.get('price', 0) * (1 + slippage),
                'broker': self.execution_pools[order['target_pool']]['broker']
            }

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:  # noqa: E501

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            return {
                'success': False,
                'error': str(e)
            }

    def _update_execution_stats(self, execution_result: dict):
        """更新执行统计"""
        self.execution_stats['total_orders'] += 1

        if execution_result['success']:
            self.execution_stats['successful_orders'] += 1
            self.execution_stats['average_time'] = (
                (self.execution_stats['average_time'] *
                 (self.execution_stats['total_orders'] - 1) +
                 execution_result['execution_time']) /
                self.execution_stats['total_orders']
            )
            self.execution_stats['average_slippage'] = (
                (self.execution_stats['average_slippage'] *
                 (self.execution_stats['total_orders'] - 1) +
                 execution_result['slippage']) /
                self.execution_stats['total_orders']
            )
        else:
            self.execution_stats['failed_orders'] += 1

    def get_router_summary(self) -> dict:
        """获取路由器总结"""
        # 活跃订单统计
        active_orders = list(self.active_orders.values())

        # 按状态统计
        status_stats = {}
        for order in active_orders:
            status = order.get('status', 'unknown')
            status_stats[status] = status_stats.get(status, 0) + 1

        # 按池统计
        pool_stats = {}
        for order in active_orders:
            pool = order.get('target_pool', 'unknown')
            pool_stats[pool] = pool_stats.get(pool, 0) + 1

        return {
            'total_active_orders': len(active_orders),
            'queue_length': len(self.execution_queue),
            'status_distribution': status_stats,
            'pool_distribution': pool_stats,
            'execution_stats': self.execution_stats,
            'current_time': datetime.now().isoformat()
        }


class AutomatedExecutionSystem:
    """
    自动化执行系统 - 主控制器
    """

    def __init__(self, total_capital: float = 1000000):
        self.total_capital = total_capital

        # 初始化组件
        self.trading_calendar = TradingCalendar()
        self.market_evaluator = MarketStateEvaluator()
        self.execution_strategy = ExecutionStrategy()
        self.order_router = OrderRouter()

        # 系统状态
        self.system_enabled = False
        self.is_running = False
        self.execution_thread = None

        # 对冲模块
        self.hedge_enabled = False
        self.hedge_coordinator = None
        self.last_hedge_plan = None
        if _HEDGE_AVAILABLE:
            try:
                self.hedge_coordinator = HedgeCoordinator()
                self.hedge_enabled = True
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as exc:  # noqa: E501
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                logger.warning("对冲模块初始化失败: %s", exc)

        # 执行状态
        self.current_market_state = 'normal'
        self.current_execution_plan = None
        self.current_routed_orders = []

        # 系统历史
        self.system_history = deque(maxlen=100)

        # 配置参数
        self.config = {
            'auto_start': True,
            'execution_window_check': True,
            'risk_pre_check': True,
            'max_retry_attempts': 3,
            'emergency_stop': True,
            'performance_monitoring': True
        }

        logger.info(f"自动化执行系统初始化完成，总资本: {total_capital:,.0f}元")

    def start_system(self):
        """启动系统"""
        if not self.system_enabled:
            self.system_enabled = True
            self.is_running = True

            # 启动执行线程
            self.execution_thread = threading.Thread(target=self._execution_loop)
            self.execution_thread.daemon = True
            self.execution_thread.start()

            # 启动性能监控
            if self.config['performance_monitoring']:
                self._start_performance_monitoring()

            logger.info("自动化执行系统启动")

    def enable_hedge(self, enabled: bool = True):
        """开启或关闭对冲模块"""
        if not _HEDGE_AVAILABLE or self.hedge_coordinator is None:
            logger.warning("对冲模块不可用，无法开启")
            return False
        self.hedge_enabled = bool(enabled)
        logger.info("对冲模块已%s", "开启" if self.hedge_enabled else "关闭")
        return self.hedge_enabled

    def disable_hedge(self):
        """关闭对冲模块"""
        return self.enable_hedge(False)

    def stop_system(self):
        """停止系统"""
        self.is_running = False
        self.system_enabled = False

        if self.execution_thread:
            self.execution_thread.join()

        logger.info("自动化执行系统停止")

    def _execution_loop(self):
        """执行循环"""
        while self.is_running:
            try:
                next_execution = self.trading_calendar.get_next_execution_time()
                if not next_execution:
                    time.sleep(60)
                    continue

                current_time = datetime.now()
                if current_time < next_execution:
                    sleep_time = (next_execution - current_time).total_seconds()
                    time.sleep(min(sleep_time, 60))
                    continue

                matched_execution = self._match_current_execution(current_time)
                if not matched_execution:
                    time.sleep(60)
                    continue

                logger.info(f"进入执行窗口: {matched_execution}")
                self._execute_daily_trading(matched_execution)
                time.sleep(60)

            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:  # noqa: E501

                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                logger.error(f"执行循环错误: {e}")
                time.sleep(60)

    def _match_current_execution(self, current_time: datetime) -> str | None:
        """根据当前时间匹配应触发的执行项"""
        execution_map = {
            'daily_execution': (datetime_time(6, 30), datetime_time(8, 0)),
            'morning_review': (datetime_time(9, 30), datetime_time(10, 30)),
            'afternoon_adjustment': (datetime_time(13, 30), datetime_time(14, 30)),
        }

        now_time = current_time.time()
        for name, (start, end) in execution_map.items():
            is_allowed, _message = self.trading_calendar.is_within_execution_window(name)
            if is_allowed:
                return name
            if start <= now_time <= end:
                return name
        return None

    def _execute_daily_trading(self, execution_name: str = 'daily_execution'):
        """执行每日交易"""
        try:
            logger.info(f"开始每日交易执行: {execution_name}")
            logger.debug(f"执行参数: total_capital={self.total_capital}, market_state={self.current_market_state}")

            # 0. 每日自动更新历史收益率数据（供对冲引擎使用真实Beta/相关性）
            try:
                self._update_historical_returns()
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as update_exc:  # noqa: E501
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                logger.warning("历史收益率自动更新失败: %s", update_exc)

            # 0.5 更新持仓实时价格
            try:
                self._update_position_prices()
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as update_exc:  # noqa: E501
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                logger.warning("持仓价格更新失败: %s", update_exc)

            # 1. 市场状态评估
            market_data = self._get_market_data()
            logger.debug(f"市场数据: { {k: v for k, v in market_data.items() if k != 'correlation_matrix'} }")
            market_state = self.market_evaluator.evaluate_market_state(market_data)

            if isinstance(market_state, dict):
                self.current_market_state = market_state['market_state']
                market_state_data = market_state
            else:
                self.current_market_state = market_state
                market_state_data = {'market_state': market_state}

            logger.info(f"市场状态评估完成: {self.current_market_state}")

            # 2. 检查风险
            if self.config['risk_pre_check']:
                if not self._risk_pre_check(market_state_data):
                    logger.warning("风险预检查失败，取消今日交易")
                    return

            # 3. 对冲决策（可选）
            hedge_plan = None
            if self.hedge_enabled and self.hedge_coordinator is not None:
                hedge_plan = self._run_hedge_decision(market_data, market_state_data)
                hedge_plan = self._apply_hedge_triggers(market_data, hedge_plan)
                self.last_hedge_plan = hedge_plan

            # 4. 生成交易计划（这里简化处理）
            trade_info = {
                'trade_id': f"TRADE_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                'instrument': 'SPY',
                'direction': 'buy',
                'trade_size': 100000,
                'urgency': 'normal',
                'asset_type': 'equity'
            }

            # 5. 选择执行策略
            strategy_result = self.execution_strategy.select_execution_strategy(
                self.current_market_state, trade_info
            )
            strategy_config = strategy_result['strategy_config']
            logger.debug(f"执行策略: {strategy_result.get('strategy_name')}, 切片大小: {strategy_config.get('slice_size')}")  # noqa: E501

            # 6. 生成执行计划
            execution_plan = self.execution_strategy.generate_execution_plan(
                trade_info, strategy_config
            )

            if isinstance(execution_plan, dict) and 'error' in execution_plan:
                logger.error(f"执行计划生成失败: {execution_plan['error']}")
                return

            self.current_execution_plan = execution_plan
            logger.info(f"执行计划生成完成: {execution_plan.get('num_slices')}个切片")

            # 7. 订单路由
            routing_result = self.order_router.route_order(
                execution_plan, self.current_market_state
            )

            if not routing_result['success']:
                logger.error(f"订单路由失败: {routing_result['error']}")
                return

            self.current_routed_orders = routing_result['routed_orders']
            logger.info(f"订单路由完成: {len(routing_result.get('routed_orders', []))}个订单")

            # 8. 处理执行队列
            self.order_router.process_execution_queue()

            # 9. 记录执行结果
            execution_result = {
                'market_state': self.current_market_state,
                'execution_plan': execution_plan,
                'routed_orders': self.current_routed_orders,
                'routing_result': routing_result,
                'execution_time': datetime.now().isoformat(),
                'execution_name': execution_name,
                'hedge_plan': hedge_plan,
                'rebalance_plan': None,
            }

            system_record = {
                'timestamp': datetime.now().isoformat(),
                'event': execution_name,
                'execution_result': execution_result,
                'market_state_data': market_state_data
            }

            self.system_history.append(system_record)
            logger.info(f"每日交易执行完成: {execution_name}")

            # 10. 生成对冲执行单
            try:
                self._generate_hedge_execution_orders(hedge_plan)
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as exc:  # noqa: E501
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                logger.warning("对冲执行单生成失败: %s", exc)

            # 11. 生成再平衡执行单
            try:
                rebalance_report = self._generate_rebalance_orders()
                execution_result['rebalance_plan'] = rebalance_report
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as exc:  # noqa: E501
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                logger.warning("再平衡订单生成失败: %s", exc)

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:  # noqa: E501

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error(f"每日交易执行失败: {e}", exc_info=True)
            failure_record = {
                'timestamp': datetime.now().isoformat(),
                'event': 'execution_failure',
                'error': str(e),
                'market_state': self.current_market_state,
                'execution_name': execution_name
            }
            self.system_history.append(failure_record)

    def _run_hedge_decision(self, market_data: dict, market_state_data: dict) -> dict | None:
        """运行对冲决策"""
        try:
            # 1. 读取真实持仓与价格
            positions_path = os.path.join(os.path.dirname(__file__), "config", "positions.json")
            positions = {}
            prices = {}
            style_map = {}
            if os.path.exists(positions_path):
                with open(positions_path, encoding="utf-8") as f:
                    pos_data = json.load(f).get("positions", {})
                for _key, item in pos_data.items():
                    code = item.get("code")
                    qty = item.get("phase1_shares") or item.get("total_shares") or item.get("shares", 0)
                    price = item.get("est_price", 0.0)
                    if code and qty:
                        positions[code] = float(qty)
                        prices[code] = float(price)
                        style_map[code] = item.get("style", "其他")

            # 2. 尝试加载真实历史收益率；缺失时使用风格 Beta 估算
            returns = pd.DataFrame()
            market_returns = pd.Series(dtype=float)
            returns_path = os.path.join(os.path.dirname(__file__), "config", "returns_history.json")
            market_path = os.path.join(os.path.dirname(__file__), "config", "market_returns.json")
            if os.path.exists(returns_path) and os.path.exists(market_path):
                try:
                    returns = pd.read_json(returns_path, orient='split')
                    market_returns = pd.read_json(market_path, orient='split', typ='series')
                    returns.columns = returns.columns.astype(str)
                    logger.info("已加载历史收益率数据: %s 条, %s 个标的", len(market_returns), returns.shape[1])
                except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:  # noqa: E501
                    # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                    logger.warning("加载历史收益率失败: %s", e)

            if returns.empty or market_returns is None or len(market_returns) == 0:
                style_beta_proxy = {
                    "宽基": 0.95,
                    "高端制造": 1.15,
                    "科技": 1.20,
                    "制造": 1.05,
                    "新能源": 1.10,
                    "医药": 0.85,
                    "化工": 1.00,
                    "银行": 0.75,
                    "防御": 0.60,
                    "顺周期": 1.10,
                    "避险": -0.10,
                    "红利": 0.70,
                    "成长": 1.25,
                }
                day_capital = float(sum(positions.get(s, 0) * prices.get(s, 0) for s in positions) or self.total_capital)  # noqa: E501
                portfolio_beta_est = 0.0
                for code, qty in positions.items():
                    amt = qty * prices.get(code, 0.0)
                    style = style_map.get(code, "其他")
                    beta = style_beta_proxy.get(style, 1.0)
                    portfolio_beta_est += (amt / day_capital) * beta if day_capital > 0 else 0.0
            else:
                portfolio_beta_est = 0.0

            # 3. 构造市场输入
            vix = float(market_data.get('vix_future_price', 20.0) or 20.0)
            portfolio_value = float(self.total_capital)
            hwm_drawdown = 0.03
            bs_loss = 0.0

            # 4. 调用真实对冲引擎
            plan = self.hedge_coordinator.coordinate(
                positions=positions,
                prices=prices,
                returns=returns,
                market_returns=market_returns,
                vix=vix,
                portfolio_value=portfolio_value,
                hwm_drawdown=hwm_drawdown,
                bs_loss=bs_loss,
            )

            # 5. 若仍缺少真实数据且 Beta 过高，给出结构化 fallback
            if plan.get("action") == "NO_HEDGE" and portfolio_beta_est > 0.7:
                plan = dict(plan)
                plan["action"] = "PREPARE_HEDGE"
                plan["portfolio_beta"] = float(portfolio_beta_est)
                plan["prepared_reason"] = (
                    f"估算组合Beta {portfolio_beta_est:.2f} > 0.7，"
                    "建议准备 Beta 对冲/避险配置"
                )

            logger.info(
                "对冲决策完成: action=%s, total_hedge_pct=%.2f%%, cost_pct=%.2f%%",
                plan.get("action"),
                float(plan.get("total_hedge_pct", 0.0) or 0.0) * 100,
                float(plan.get("total_cost_pct", 0.0) or 0.0) * 100,
            )
            return plan
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:  # noqa: E501
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error(f"对冲决策失败: {e}")
            return None

    def _update_position_prices(self):
        """更新持仓实时价格 - 优先 Wind MCP"""
        try:
            positions_path = os.path.join(os.path.dirname(__file__), "config", "positions.json")
            if not os.path.exists(positions_path):
                return

            with open(positions_path, encoding="utf-8") as f:
                data = json.load(f)
            positions = data.get('positions', {})

            update_count = 0
            fail_count = 0

            for item in positions.values():
                code = item.get('code')
                item.get('est_price', 0.0)
                shares = item.get('phase1_shares') or item.get('total_shares') or item.get('shares', 0)

                if not code or not shares:
                    continue

                real_time_price = None
                price_source = None

                if _WIND_MCP_AVAILABLE and wind_get_quote is not None:
                    try:
                        wind_code, is_fund = _to_wind_code(code)
                        quote = wind_get_quote(wind_code, is_fund=is_fund)
                        if quote and quote.get('price') is not None:
                            real_time_price = float(quote['price'])
                            if real_time_price > 0:
                                price_source = 'wind_mcp'
                    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:  # noqa: E501
                        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                        logger.debug("Wind MCP 获取价格失败 %s: %s", code, e)

                if real_time_price is None or real_time_price <= 0:
                    fail_count += 1
                    continue

                item['est_price'] = real_time_price
                item['last_update'] = datetime.now().isoformat()
                item['price_source'] = price_source or 'unknown'
                update_count += 1

            if update_count > 0:
                with open(positions_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

            logger.info("持仓价格更新完成: 成功 %s, 失败 %s", update_count, fail_count)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:  # noqa: E501
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("持仓价格更新失败: %s", e)

    def _update_historical_returns(self):
        """更新历史收益率数据并写入 config/"""
        try:
            positions_path = os.path.join(os.path.dirname(__file__), "config", "positions.json")
            if not os.path.exists(positions_path):
                return

            with open(positions_path, encoding="utf-8") as f:
                positions_data = json.load(f).get("positions", {})

            symbols = [item.get("code") for item in positions_data.values() if item.get("code")]
            if not symbols:
                return

            provider = MarketDataProvider()
            period = "1y"
            returns_data = {}
            for symbol in symbols:
                try:
                    df = provider.get_historical_data(symbol, period)
                    if df is not None and not df.empty and "close" in df.columns:
                        df["return"] = df["close"].pct_change()
                        returns_data[symbol] = df["return"].dropna()
                except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):  # noqa: E501
                    # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                    continue

            if not returns_data:
                return

            returns_df = pd.DataFrame(returns_data)
            returns_path = os.path.join(os.path.dirname(__file__), "config", "returns_history.json")
            returns_df.to_json(returns_path, orient="split", date_format="iso")

            market_symbol = "510300"
            market_df = provider.get_historical_data(market_symbol, period)
            if market_df is not None and not market_df.empty and "close" in market_df.columns:
                market_returns = market_df["close"].pct_change().dropna()
                market_path = os.path.join(os.path.dirname(__file__), "config", "market_returns.json")
                market_returns.to_json(market_path, orient="split", date_format="iso")

            logger.info("历史收益率自动更新完成: %s 个标的", len(returns_df.columns))
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:  # noqa: E501
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("历史收益率自动更新异常: %s", e)

    def _apply_hedge_triggers(self, market_data: dict, hedge_plan: dict | None) -> dict | None:
        """基于 VIX / 回撤 / 市场状态做强制触发覆盖"""
        if not hedge_plan:
            return hedge_plan

        plan = dict(hedge_plan)
        vix = float(market_data.get('vix_future_price', 20.0) or 20.0)
        market_state = str(self.current_market_state)
        drawdown = float(getattr(self, 'current_drawdown', 0.0) or 0.0)

        forced_reason = None
        if vix >= 30.0:
            forced_reason = f'VIX={vix:.1f} >= 30.0'
        elif market_state in {'elevated', 'crisis', 'stress'}:
            forced_reason = f'market_state={market_state}'
        elif drawdown >= 0.03:
            forced_reason = f'drawdown={drawdown*100:.1f}% >= 3%'

        if forced_reason and plan.get('action') == 'NO_HEDGE':
            plan['action'] = 'FORCED_HEDGE'
            plan['forced_reason'] = forced_reason
            logger.warning('对冲被自动触发: %s', forced_reason)

        return plan

    def _generate_hedge_execution_orders(self, hedge_plan: dict | None):
        """根据对冲决策生成可执行订单文件"""
        try:
            positions_path = os.path.join(os.path.dirname(__file__), "config", "positions.json")
            positions = {}
            prices = {}
            if os.path.exists(positions_path):
                with open(positions_path, encoding="utf-8") as f:
                    pos_data = json.load(f).get("positions", {})
                for item in pos_data.values():
                    code = item.get("code")
                    qty = item.get("phase1_shares") or item.get("total_shares") or item.get("shares", 0)
                    price = item.get("est_price", 0.0)
                    if code and qty:
                        positions[code] = float(qty)
                        prices[code] = float(price)

            plan = hedge_plan or {}
            plan.setdefault('portfolio_beta', 0.0)
            plan.setdefault('total_hedge_pct', 0.0)
            plan.setdefault('action', 'NO_HEDGE')

            sys.path.insert(0, os.path.dirname(__file__))
            try:
                from hedge_execution_orders import build_orders
                orders = build_orders(plan, positions, prices)
            except (ImportError, ValueError, TypeError, KeyError, AttributeError, RuntimeError) as e:
                # ImportError: hedge_execution_orders 模块缺失
                # ValueError/TypeError/KeyError: build_orders 参数/返回格式问题
                # AttributeError: build_orders 接口不匹配
                # RuntimeError: build_orders 内部异常
                logger.warning("build_orders 降级为 NO_HEDGE: %s", e)
                orders = {
                    'date': datetime.now().strftime('%Y-%m-%d'),
                    'action': plan.get('action', 'NO_HEDGE'),
                    'orders': []
                }

            report_dir = os.path.join(os.path.dirname(__file__), 'reports')
            os.makedirs(report_dir, exist_ok=True)
            out_path = os.path.join(report_dir, f'hedge_execution_orders_{datetime.now().strftime("%Y%m%d")}.json')
            with open(out_path, 'w', encoding='utf-8') as f:
                json.dump(orders, f, ensure_ascii=False, indent=2)

            logger.info("对冲执行单已生成: %s", out_path)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:  # noqa: E501
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("生成对冲执行单失败: %s", e)

    def _fetch_index_price_from_wind(self) -> float | None:
        """从 Wind MCP 获取沪深300实时价格"""
        if not (_WIND_MCP_AVAILABLE and wind_get_quote is not None):
            return None
        try:
            quote = wind_get_quote('510300.SH', is_fund=True)
            if quote and quote.get('price') is not None:
                return float(quote['price'])
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:  # noqa: E501
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.debug("Wind MCP 获取市场指数失败: %s", e)
        return None

    def _fetch_index_price_from_history(self) -> float | None:
        """从历史数据文件获取指数价格（回退方案）"""
        try:
            base_dir = os.path.dirname(__file__)
            market_path = os.path.join(base_dir, "config", "market_returns.json")
            if os.path.exists(market_path):
                market_returns = pd.read_json(market_path, orient='split', typ='series')
                if not market_returns.empty:
                    return safe_float(float(market_returns.iloc[-1]) * 1000 + 3000)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass
        return None

    def _compute_portfolio_beta(self, returns: pd.DataFrame, market_returns: pd.Series) -> float:
        """计算组合相对市场的 beta（取各列有效 beta 的平均值）"""
        betas = []
        for col in returns.columns:
            try:
                series = returns[col].dropna()
                if len(series) < 5:
                    continue
                cov = series.cov(market_returns)
                var_m = market_returns.var()
                if var_m > 0 and not np.isnan(cov):
                    betas.append(float(cov / var_m))
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):  # noqa: E501
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                continue

        # 组合 beta 取有效值的平均
        if betas:
            return safe_float(sum(betas) / len(betas))
        return safe_float(1.0)

    def _compute_market_metrics_from_returns(self, index_price: float) -> dict | None:
        """从历史收益率计算真实市场指标，失败返回 None"""
        try:
            base_dir = os.path.dirname(__file__)
            returns_path = os.path.join(base_dir, "config", "returns_history.json")
            market_path = os.path.join(base_dir, "config", "market_returns.json")

            if not (os.path.exists(returns_path) and os.path.exists(market_path)):
                return None

            returns = pd.read_json(returns_path, orient='split')
            market_returns = pd.read_json(market_path, orient='split', typ='series')
            returns.columns = returns.columns.astype(str)

            if market_returns.empty or returns.empty:
                return None

            # 计算市场波动率
            vol = safe_float(market_returns.std() * (252 ** 0.5))

            # 计算 VaR
            var_95 = safe_float(market_returns.quantile(0.05))
            var_99 = safe_float(market_returns.quantile(0.01))

            # 计算相关性矩阵
            corr_matrix = returns.corr().fillna(0.0).values.tolist()
            if len(corr_matrix) == 0:
                corr_matrix = _eye(3)

            # 计算 beta（组合相对市场）
            beta = self._compute_portfolio_beta(returns, market_returns)

            # 跟踪误差
            tracking_error = safe_float(market_returns.std() * (252 ** 0.5) * 0.5)

            return {
                'index_price': safe_float(index_price),
                'volatility': safe_float(vol),
                'var_95': safe_float(var_95),
                'var_99': safe_float(var_99),
                'liquidity': safe_float(1.0),
                'sentiment_score': safe_float(0.0),
                'correlation_matrix': corr_matrix,
                'beta': safe_float(beta),
                'tracking_error': safe_float(tracking_error),
                'market_correlation': safe_float(0.7),
                'volatility_skew': safe_float(0.0),
                'volatility_term': safe_float(0.0),
                'vix_future_price': safe_float(20.0),
                'kurtosis': safe_float(3.0),
                'skewness': safe_float(0.0),
                'extreme_events': safe_float(0, default=0)
            }
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:  # noqa: E501
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.debug("历史收益率市场数据计算失败: %s", e)
            return None

    def _build_default_market_data(self, index_price: float) -> dict:
        """构建默认市场数据字典（兜底值）"""
        return {
            'index_price': safe_float(index_price),
            'volatility': safe_float(0.15),
            'var_95': safe_float(0.02),
            'var_99': safe_float(0.035),
            'liquidity': safe_float(1.0),
            'sentiment_score': safe_float(0.2),
            'correlation_matrix': _eye(3),
            'beta': safe_float(1.0),
            'tracking_error': safe_float(0.03),
            'market_correlation': safe_float(0.7),
            'volatility_skew': safe_float(0.0),
            'volatility_term': safe_float(0.0),
            'vix_future_price': safe_float(20.0),
            'kurtosis': safe_float(3.0),
            'skewness': safe_float(0.0),
            'extreme_events': safe_float(0, default=0)
        }

    def _get_market_data(self) -> dict:
        """获取市场数据 - 优先 Wind MCP"""
        try:
            # 优先从 Wind MCP 获取沪深300实时价格
            index_price = self._fetch_index_price_from_wind()

            # 回退到历史数据
            if index_price is None or index_price <= 0:
                index_price = self._fetch_index_price_from_history()

            # 最终兜底
            if index_price is None or index_price <= 0:
                index_price = safe_float(3000)

            # 尝试从历史收益率计算真实指标
            metrics = self._compute_market_metrics_from_returns(index_price)
            if metrics is not None:
                return metrics

            return self._build_default_market_data(index_price)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:  # noqa: E501
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("获取市场数据失败: %s", e)
            return self._build_default_market_data(safe_float(3000))

    def _risk_pre_check(self, market_state_data: dict) -> bool:
        """执行风险预检查"""
        try:
            market_state = market_state_data['market_state']
            if market_state in ['crisis', 'stress']:
                logger.warning(f"市场状态异常: {market_state}")
                return False

            var_95 = market_state_data.get('individual_scores', {}).get('var', 0)
            if var_95 > 0.8:
                logger.warning(f"VaR风险过高: {var_95}")
                return False

            liquidity = market_state_data.get('individual_scores', {}).get('liquidity', 0)
            if liquidity > 0.8:
                logger.warning(f"流动性风险过高: {liquidity}")
                return False

            logger.debug("风险预检查通过")
            return True

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:  # noqa: E501

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error(f"风险预检查失败: {e}")
            return False

    def _start_performance_monitoring(self):
        """启动性能监控"""
        monitoring_thread = threading.Thread(target=self._performance_monitoring_loop)
        monitoring_thread.daemon = True
        monitoring_thread.start()

    def _performance_monitoring_loop(self):
        """性能监控循环"""
        while self.is_running:
            try:
                summary = self.get_system_summary()
                perf = summary['performance_metrics']
                total_orders = self.order_router.execution_stats['total_orders']

                if total_orders > 0:
                    if perf['execution_success_rate'] < 0.8:
                        logger.warning("执行成功率过低，系统性能下降")

                    if perf['average_execution_time'] > 30:
                        logger.warning("执行时间过长，系统性能下降")

                    if perf['average_slippage'] > 0.01:
                        logger.warning("滑点过大，系统性能下降")
                else:
                    logger.debug("性能监控：暂无订单执行记录，跳过阈值告警")

                time.sleep(300)
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:  # noqa: E501
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                logger.error(f"性能监控错误: {e}")
                time.sleep(300)

    def get_system_summary(self) -> dict:
        """获取系统总结"""
        market_evaluator_summary = self.market_evaluator.get_market_state_summary()

        hedge_summary = {
            'available': _HEDGE_AVAILABLE,
            'enabled': self.hedge_enabled,
            'coordinator_loaded': self.hedge_coordinator is not None,
            'last_action': self.last_hedge_plan.get('action') if isinstance(self.last_hedge_plan, dict) else None,
            'last_total_hedge_pct': self.last_hedge_plan.get('total_hedge_pct') if isinstance(self.last_hedge_plan, dict) else None,  # noqa: E501
            'last_total_cost_pct': self.last_hedge_plan.get('total_cost_pct') if isinstance(self.last_hedge_plan, dict) else None,  # noqa: E501
        }

        return {
            'system_status': 'running' if self.is_running else 'stopped',
            'current_market_state': self.current_market_state,
            'current_plan': self.current_execution_plan,
            'routed_orders_count': len(self.current_routed_orders),
            'hedge': hedge_summary,

            # 各组件状态
            'trading_calendar': self.trading_calendar.get_execution_summary(),
            'market_state_evaluator': market_evaluator_summary,
            'execution_strategy': self.execution_strategy.get_execution_summary(),
            'order_router': self.order_router.get_router_summary(),

            # 系统统计
            'system_history_count': len(self.system_history),
            'last_execution': self.system_history[-1]['timestamp'] if self.system_history else None,

            # 性能指标
            'performance_metrics': {
                'execution_success_rate':
                    self.order_router.execution_stats['successful_orders'] /
                    max(self.order_router.execution_stats['total_orders'], 1),
                'average_execution_time':
                    self.order_router.execution_stats['average_time'],
                'average_slippage':
                    self.order_router.execution_stats['average_slippage']
            }
        }

    def get_execution_schedule(self, days_ahead: int = 7) -> list[dict]:
        """获取执行计划"""
        return self.trading_calendar.get_execution_schedule(days_ahead)

    def _generate_rebalance_orders(self):
        """生成再平衡执行订单"""
        try:
            sys.path.insert(0, os.path.dirname(__file__))
            from rebalance_execution_orders import (
                TARGET_ALLOCATION,
                build_report,
                calc_current_allocation,
                generate_rebalance_orders,
                load_positions,
            )

            positions, prices, styles = load_positions()
            style_allocation = calc_current_allocation(positions, prices, styles)
            orders = generate_rebalance_orders(style_allocation, TARGET_ALLOCATION, positions, prices)
            report = build_report(style_allocation, TARGET_ALLOCATION, orders)

            report_dir = os.path.join(os.path.dirname(__file__), 'reports')
            os.makedirs(report_dir, exist_ok=True)
            out_path = os.path.join(report_dir, f'rebalance_execution_orders_{datetime.now().strftime("%Y%m%d")}.json')
            with open(out_path, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)

            valid_count = report['summary']['valid_orders']
            total_count = report['summary']['total_orders']
            logger.info(f"再平衡订单生成完成: {valid_count}/{total_count} 有效订单")

            return report
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:  # noqa: E501
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error(f"生成再平衡订单失败: {e}")
            return None


# 主程序
if __name__ == "__main__":
    logger.info("自动化执行系统启动")
    logger.info("=" * 50)

    # 创建自动化执行系统
    execution_system = AutomatedExecutionSystem(total_capital=1000000)

    # 尝试开启对冲模块
    if execution_system.enable_hedge(True):
        logger.info("对冲模块: 已开启")
    else:
        logger.info("对冲模块: 不可用或开启失败")

    # 启动系统
    execution_system.start_system()

    # 等待一段时间观察执行
    time.sleep(10)

    # 获取系统总结
    summary = execution_system.get_system_summary()

    logger.info("\n系统状态")
    logger.info("=" * 50)
    logger.info(f"系统状态: {summary['system_status']}")
    logger.info(f"当前市场状态: {summary['current_market_state']}")
    logger.info(f"当前执行计划: {'有' if summary['current_plan'] else '无'}")
    logger.info(f"路由订单数: {summary['routed_orders_count']}")

    logger.info("\n组件状态")
    logger.info("=" * 50)
    logger.info(f"交易日历: 总执行数={summary['trading_calendar'].get('total_executions', 0)}")
    logger.info(f"市场评估: 当前状态={summary['market_state_evaluator'].get('current_state', 'unknown')}")
    logger.info(f"执行策略: 成功率={summary['execution_strategy'].get('success_rate', 0):.2%}")
    logger.info(f"订单路由: 活跃订单={summary['order_router'].get('total_active_orders', 0)}")

    logger.info("\n性能指标")
    logger.info("=" * 50)
    perf = summary['performance_metrics']
    logger.info(f"执行成功率: {perf['execution_success_rate']:.2%}")
    logger.info(f"平均执行时间: {perf['average_execution_time']:.2f}秒")
    logger.info(f"平均滑点: {perf['average_slippage']:.4%}")

    # 获取未来执行计划
    future_schedule = execution_system.get_execution_schedule(3)
    logger.info("\n未来3天执行计划")
    logger.info("=" * 50)
    for day in future_schedule:
        logger.info(f"{day['date']}: {len(day['executions'])}个执行")

    logger.info("\n系统运行中...按Ctrl+C停止")

    try:
        # 保持运行
        while True:
            time.sleep(30)
            # 更新状态
            current_summary = execution_system.get_system_summary()
            print(f"\r当前时间: {datetime.now().strftime('%H:%M:%S')} | "
                  f"系统状态: {current_summary['system_status']} | "
                  f"市场状态: {current_summary['current_market_state']}", end='')
    except KeyboardInterrupt:
        logger.info("\n正在停止系统...")
        execution_system.stop_system()
        logger.info("系统已停止")
