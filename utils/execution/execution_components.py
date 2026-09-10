"""
自动化执行系统组件
=================

从 automated_execution_system.py 抽取的独立组件:
- TradingCalendar: 交易日历管理器
- MarketStateEvaluator: 市场状态评估器
- ExecutionStrategy: 执行策略控制器
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta
from datetime import time as datetime_time
from typing import Any, TypedDict

import numpy as np

from utils.datetime_utils import now_bj

logger = logging.getLogger("automated_execution_system")


class SpecialDayEntry(TypedDict):
    """交易日历特殊日条目 (休市/调休)。"""

    is_trading: bool
    name: str


class TradingCalendar:
    """
    交易日历管理器
    """

    def __init__(self) -> None:
        self.trading_schedule: dict[str, Any] = {
            "morning_open": datetime_time(7, 0),
            "morning_close": datetime_time(11, 30),
            "afternoon_open": datetime_time(13, 0),
            "afternoon_close": datetime_time(15, 0),
            "executions": [
                {"time": datetime_time(7, 0), "name": "daily_execution"},
                {"time": datetime_time(10, 0), "name": "morning_review"},
                {"time": datetime_time(14, 0), "name": "afternoon_adjustment"},
            ],
        }

        self.special_days: dict[str, SpecialDayEntry] = {
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

        self.execution_windows: dict[str, dict[str, Any]] = {
            "daily_execution": {
                "start": datetime_time(6, 30),
                "end": datetime_time(8, 0),
                "allow_early": True,
                "allow_late": True,
                "early_minutes": 30,
                "late_minutes": 60,
            },
            "morning_review": {
                "start": datetime_time(9, 30),
                "end": datetime_time(10, 30),
                "allow_early": True,
                "allow_late": True,
                "early_minutes": 30,
                "late_minutes": 30,
            },
            "afternoon_adjustment": {
                "start": datetime_time(13, 30),
                "end": datetime_time(14, 30),
                "allow_early": True,
                "allow_late": True,
                "early_minutes": 30,
                "late_minutes": 30,
            },
        }

        self.execution_history: deque = deque(maxlen=100)

        logger.info("交易日历初始化完成")

    def is_trading_day(self, date: datetime | None = None) -> bool:
        """判断是否为交易日"""
        if date is None:
            date = now_bj()

        if date.weekday() >= 5:
            return False

        date_str = date.strftime("%Y-%m-%d")
        entry = self.special_days.get(date_str)
        if entry is not None:
            return entry["is_trading"]

        month, day = date.month, date.day
        if (month == 1 and day in [1, 2, 3]) or (month == 10 and day == 1):
            return False

        return True

    def is_within_execution_window(self, execution_name: str) -> tuple[bool, str]:
        """判断当前是否在执行窗口内"""
        now = now_bj().time()
        window = self.execution_windows.get(execution_name)

        if not window:
            return False, f"未知的执行类型: {execution_name}"

        if window["start"] <= now <= window["end"]:
            return True, "在执行窗口内"

        if window.get("allow_early") and now < window["start"]:
            early_minutes = window.get("early_minutes", 15)
            time_diff = (
                datetime.combine(datetime.min, window["start"])
                - datetime.combine(datetime.min, now)
            ).total_seconds()
            if time_diff <= early_minutes * 60:
                return True, "允许提前执行"

        if window.get("allow_late") and now > window["end"]:
            late_minutes = window.get("late_minutes", 15)
            time_diff = (
                datetime.combine(datetime.min, now)
                - datetime.combine(datetime.min, window["end"])
            ).total_seconds()
            if time_diff <= late_minutes * 60:
                return True, "允许延后执行"

        return False, "不在执行窗口内"

    def get_next_execution_time(self) -> datetime | None:
        """获取下次执行时间"""
        now = now_bj()

        if not self.is_trading_day(now):
            next_day = now + timedelta(days=1)
            while not self.is_trading_day(next_day):
                next_day += timedelta(days=1)
            return next_day.replace(
                hour=self.trading_schedule["morning_open"].hour,
                minute=self.trading_schedule["morning_open"].minute,
            )

        today = now.date()

        for execution in self.trading_schedule["executions"]:
            execution_time = datetime.combine(today, execution["time"])
            if execution_time > now:
                return execution_time

        tomorrow = now + timedelta(days=1)
        while not self.is_trading_day(tomorrow):
            tomorrow += timedelta(days=1)
        return datetime.combine(
            tomorrow, self.trading_schedule["executions"][0]["time"]
        )

    def get_execution_schedule(self, days_ahead: int = 7) -> list[dict]:
        """获取未来几天的执行计划"""
        schedule = []
        now = now_bj()

        for i in range(days_ahead):
            date = now + timedelta(days=i)
            if self.is_trading_day(date):
                day_schedule: dict[str, Any] = {
                    "date": date.strftime("%Y-%m-%d"),
                    "is_trading": True,
                    "executions": [],
                }

                for execution in self.trading_schedule["executions"]:
                    execution_time = datetime.combine(date, execution["time"])
                    day_schedule["executions"].append(
                        {
                            "name": execution["name"],
                            "time": execution_time.isoformat(),
                            "timestamp": execution_time.timestamp(),
                        }
                    )

                schedule.append(day_schedule)

        return schedule

    def record_execution(
        self,
        execution_name: str,
        start_time: datetime,
        end_time: datetime,
        success: bool,
        details: dict,
    ) -> None:
        """记录执行历史"""
        record = {
            "timestamp": now_bj().isoformat(),
            "execution_name": execution_name,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "duration_seconds": (end_time - start_time).total_seconds(),
            "success": success,
            "details": details,
        }

        self.execution_history.append(record)
        logger.info(f"执行记录: {execution_name} - {'成功' if success else '失败'}")

    def get_execution_summary(self) -> dict:
        """获取执行总结"""
        if not self.execution_history:
            return {"message": "暂无执行历史"}

        latest_execution = self.execution_history[-1]

        total_executions = len(self.execution_history)
        successful_executions = sum(1 for r in self.execution_history if r["success"])
        success_rate = (
            successful_executions / total_executions if total_executions > 0 else 0.0
        )

        avg_duration = np.mean([r["duration_seconds"] for r in self.execution_history])

        execution_stats = {}
        for record in self.execution_history:
            exec_name = record["execution_name"]
            if exec_name not in execution_stats:
                execution_stats[exec_name] = {
                    "count": 0,
                    "success": 0,
                    "avg_duration": 0.0,
                }

            execution_stats[exec_name]["count"] += 1
            if record["success"]:
                execution_stats[exec_name]["success"] += 1

            stats = execution_stats[exec_name]
            if stats["count"] > 0:
                stats["avg_duration"] = (
                    stats["avg_duration"] * (stats["count"] - 1)
                    + record["duration_seconds"]
                ) / stats["count"]

        next_exec = self.get_next_execution_time()
        return {
            "total_executions": total_executions,
            "latest_execution": latest_execution["execution_name"],
            "latest_time": latest_execution["timestamp"],
            "success_rate": success_rate,
            "average_duration_seconds": avg_duration,
            "execution_stats": execution_stats,
            "next_execution_time": (
                next_exec.isoformat() if next_exec is not None else None
            ),
        }


class MarketStateEvaluator:
    """
    市场状态评估器
    """

    def __init__(self) -> None:
        self.market_states = {
            "normal": {
                "description": "正常市场",
                "execution_strategy": "aggressive",
                "priority": "normal",
                "risk_tolerance": "medium",
            },
            "volatile": {
                "description": "高波动市场",
                "execution_strategy": "conservative",
                "priority": "high",
                "risk_tolerance": "low",
            },
            "illiquid": {
                "description": "低流动性市场",
                "execution_strategy": "patient",
                "priority": "high",
                "risk_tolerance": "low",
            },
            "stress": {
                "description": "压力市场",
                "execution_strategy": "defensive",
                "priority": "critical",
                "risk_tolerance": "very_low",
            },
            "crisis": {
                "description": "危机市场",
                "execution_strategy": "emergency",
                "priority": "critical",
                "risk_tolerance": "minimal",
            },
        }

        self.state_history: deque = deque(maxlen=100)

        self.state_thresholds = {
            "volatility_threshold": 0.25,
            "liquidity_threshold": 0.5,
            "var_threshold": 0.05,
            "sentiment_threshold": -0.6,
            "correlation_breakdown": 0.8,
        }

        logger.info("市场状态评估器初始化完成")

    def evaluate_market_state(self, market_data: dict) -> dict:
        """评估当前市场状态"""
        try:
            logger.info("开始市场状态评估")

            volatility = market_data.get("volatility", 0.15)
            liquidity = market_data.get("liquidity", 1.0)
            var_95 = market_data.get("var_95", 0.02)
            sentiment = market_data.get("sentiment_score", 0.0)

            _corr_raw = market_data.get("correlation_matrix", np.eye(3))
            correlation_matrix = (
                np.array(_corr_raw)
                if not isinstance(_corr_raw, np.ndarray)
                else _corr_raw
            )
            correlation_breakdown = self._calculate_correlation_breakdown(
                correlation_matrix
            )

            volatility_score = self._evaluate_volatility(volatility)
            liquidity_score = self._evaluate_liquidity(liquidity)
            var_score = self._evaluate_var(var_95)
            sentiment_score = self._evaluate_sentiment(sentiment)

            market_state = self._determine_market_state(
                volatility_score,
                liquidity_score,
                var_score,
                sentiment_score,
                correlation_breakdown,
            )

            confidence = self._calculate_state_confidence(
                volatility_score,
                liquidity_score,
                var_score,
                sentiment_score,
                correlation_breakdown,
            )

            evaluation_report = {
                "timestamp": now_bj().isoformat(),
                "market_state": market_state,
                "confidence": confidence,
                "individual_scores": {
                    "volatility": volatility_score,
                    "liquidity": liquidity_score,
                    "var": var_score,
                    "sentiment": sentiment_score,
                    "correlation_breakdown": correlation_breakdown,
                },
                "detailed_metrics": {
                    "volatility": volatility,
                    "liquidity": liquidity,
                    "var_95": var_95,
                    "sentiment": sentiment,
                    "correlation_matrix": correlation_matrix.tolist(),
                },
                "state_characteristics": self.market_states[market_state],
            }

            self.state_history.append(evaluation_report)

            logger.info(f"市场状态评估完成: {market_state} (置信度: {confidence:.2f})")

            return evaluation_report

        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            logger.error(f"市场状态评估失败: {e}")
            return {"market_state": "normal", "confidence": 0.0, "error": str(e)}

    def _evaluate_volatility(self, volatility: float) -> float:
        if volatility < 0.10:
            return 0.0
        if volatility < 0.20:
            return 0.3
        if volatility < 0.30:
            return 0.6
        if volatility < 0.40:
            return 0.8
        return 1.0

    def _evaluate_liquidity(self, liquidity: float) -> float:
        if liquidity > 0.8:
            return 0.0
        if liquidity > 0.6:
            return 0.3
        if liquidity > 0.4:
            return 0.6
        if liquidity > 0.2:
            return 0.8
        return 1.0

    def _evaluate_var(self, var: float) -> float:
        if var < 0.02:
            return 0.0
        if var < 0.03:
            return 0.3
        if var < 0.05:
            return 0.6
        if var < 0.08:
            return 0.8
        return 1.0

    def _evaluate_sentiment(self, sentiment: float) -> float:
        abs_sentiment = abs(sentiment)
        if abs_sentiment < 0.2:
            return 0.0
        if abs_sentiment < 0.4:
            return 0.3
        if abs_sentiment < 0.6:
            return 0.6
        if abs_sentiment < 0.8:
            return 0.8
        return 1.0

    def _calculate_correlation_breakdown(self, correlation_matrix: np.ndarray) -> float:
        upper_tri = np.triu(correlation_matrix, k=1)
        valid_correlations = upper_tri[upper_tri != 0]

        if len(valid_correlations) > 0:
            correlation_std = np.std(valid_correlations)
            return float(min(correlation_std / 0.5, 1.0))
        return 0.0

    def _determine_market_state(
        self,
        volatility_score: float,
        liquidity_score: float,
        var_score: float,
        sentiment_score: float,
        correlation_breakdown: float,
    ) -> str:
        risk_score = (
            volatility_score * 0.3
            + liquidity_score * 0.3
            + var_score * 0.2
            + sentiment_score * 0.1
            + correlation_breakdown * 0.1
        )

        if risk_score >= 0.8:
            return "crisis"
        if risk_score >= 0.6:
            return "stress"
        if risk_score >= 0.4:
            return "illiquid"
        if risk_score >= 0.2:
            return "volatile"
        return "normal"

    def _calculate_state_confidence(
        self,
        volatility_score: float,
        liquidity_score: float,
        var_score: float,
        sentiment_score: float,
        correlation_breakdown: float,
    ) -> float:
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

        confidence = extreme_indicators / total_indicators

        if len(self.state_history) > 2:
            recent_states = [s["market_state"] for s in list(self.state_history)[-3:]]
            if len(set(recent_states)) == 1:
                confidence += 0.2

        return min(confidence, 1.0)

    def get_market_state_summary(self) -> dict:
        if not self.state_history:
            return {"message": "暂无市场状态数据"}

        latest_state = self.state_history[-1]

        state_distribution: dict[str, int] = {}
        for state_record in self.state_history:
            state = state_record["market_state"]
            state_distribution[state] = state_distribution.get(state, 0) + 1

        avg_confidence = np.mean([s["confidence"] for s in self.state_history])

        if len(self.state_history) > 5:
            recent_states = [s["market_state"] for s in list(self.state_history)[-10:]]
            state_changes = sum(
                1
                for i in range(1, len(recent_states))
                if recent_states[i] != recent_states[i - 1]
            )
            stability = 1.0 - (state_changes / len(recent_states))
        else:
            stability = 1.0

        return {
            "current_state": latest_state["market_state"],
            "current_confidence": latest_state["confidence"],
            "timestamp": latest_state["timestamp"],
            "state_distribution": state_distribution,
            "average_confidence": avg_confidence,
            "state_stability": stability,
            "total_evaluations": len(self.state_history),
        }


class ExecutionStrategy:
    """
    执行策略控制器
    """

    def __init__(self) -> None:
        self.execution_strategies: dict[str, dict[str, Any]] = {
            "aggressive": {
                "strategy_name": "aggressive",
                "description": "激进执行",
                "order_type": "market",
                "execution_style": "immediate",
                "slice_size": 1.0,
                "timeout_seconds": 30,
                "retry_attempts": 2,
                "slippage_tolerance": 0.01,
            },
            "conservative": {
                "strategy_name": "conservative",
                "description": "保守执行",
                "order_type": "limit",
                "execution_style": "sliced",
                "slice_size": 0.3,
                "timeout_seconds": 120,
                "retry_attempts": 3,
                "slippage_tolerance": 0.005,
            },
            "patient": {
                "strategy_name": "patient",
                "description": "耐心执行",
                "order_type": "limit",
                "execution_style": "gradual",
                "slice_size": 0.2,
                "timeout_seconds": 300,
                "retry_attempts": 5,
                "slippage_tolerance": 0.003,
            },
            "defensive": {
                "strategy_name": "defensive",
                "description": "防御性执行",
                "order_type": "limit",
                "execution_style": "weighted",
                "slice_size": 0.1,
                "timeout_seconds": 600,
                "retry_attempts": 8,
                "slippage_tolerance": 0.002,
            },
            "emergency": {
                "strategy_name": "emergency",
                "description": "紧急执行",
                "order_type": "market",
                "execution_style": "immediate",
                "slice_size": 1.0,
                "timeout_seconds": 15,
                "retry_attempts": 1,
                "slippage_tolerance": 0.02,
            },
        }

        self.state_to_strategy = {
            "normal": "aggressive",
            "volatile": "conservative",
            "illiquid": "patient",
            "stress": "defensive",
            "crisis": "emergency",
        }

        self.execution_history: deque = deque(maxlen=100)

        logger.info("执行策略控制器初始化完成")

    def select_execution_strategy(self, market_state: str, trade_info: dict) -> dict:
        """选择执行策略"""
        try:
            strategy_name = self.state_to_strategy.get(market_state, "conservative")
            strategy_config = self.execution_strategies[strategy_name].copy()

            trade_size = trade_info.get("trade_size", 0)
            if trade_size > 1000000:
                strategy_config["slice_size"] = max(
                    0.1, strategy_config["slice_size"] * 0.5
                )
                strategy_config["timeout_seconds"] = min(
                    600, strategy_config["timeout_seconds"] * 1.5
                )

            urgency = trade_info.get("urgency", "normal")
            if urgency == "high":
                strategy_config["slice_size"] = min(
                    1.0, strategy_config["slice_size"] * 2
                )
                strategy_config["timeout_seconds"] = max(
                    10, strategy_config["timeout_seconds"] * 0.5
                )

            asset_type = trade_info.get("asset_type", "equity")
            if asset_type == "bond":
                strategy_config["slice_size"] = min(
                    0.5, strategy_config["slice_size"] * 1.5
                )
                strategy_config["slippage_tolerance"] = min(
                    0.01, strategy_config["slippage_tolerance"] * 2
                )

            logger.info(f"选择执行策略: {strategy_name}")

            return {
                "strategy_name": strategy_name,
                "strategy_config": strategy_config,
                "reasoning": f"基于市场状态{market_state}和交易特性选择",
            }

        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            logger.error(f"执行策略选择失败: {e}")
            return {
                "strategy_name": "conservative",
                "strategy_config": self.execution_strategies["conservative"].copy(),
                "error": str(e),
            }

    def generate_execution_plan(self, trade_info: dict, strategy_config: dict) -> dict:
        """生成执行计划"""
        try:
            slice_size = strategy_config["slice_size"]
            if slice_size >= 1.0:
                num_slices = 1
            else:
                num_slices = max(1, int(1.0 / slice_size))

            trade_size = trade_info.get("trade_size", 0)
            instrument = trade_info.get("instrument", "")
            direction = trade_info.get("direction", "buy")

            slices: list[Any] = []
            for i in range(num_slices):
                if i == num_slices - 1:
                    slice_size = trade_size - sum(s["size"] for s in slices)
                else:
                    slice_size = trade_size * strategy_config["slice_size"]

                slices.append(
                    {
                        "slice_id": i + 1,
                        "size": slice_size,
                        "direction": direction,
                        "instrument": instrument,
                        "price_type": (
                            "limit"
                            if strategy_config["order_type"] == "limit"
                            else "market"
                        ),
                        "priority": "high" if i == 0 else "normal",
                        "created_at": now_bj().isoformat(),
                    }
                )

            timeout_per_slice = strategy_config["timeout_seconds"]
            total_timeout = timeout_per_slice * num_slices

            execution_plan = {
                "trade_id": trade_info.get("trade_id", ""),
                "instrument": instrument,
                "total_size": trade_size,
                "total_direction": direction,
                "strategy": strategy_config["strategy_name"],
                "num_slices": num_slices,
                "slices": slices,
                "timeout_seconds": total_timeout,
                "max_retry_attempts": strategy_config["retry_attempts"],
                "slippage_tolerance": strategy_config["slippage_tolerance"],
                "execution_style": strategy_config["execution_style"],
                "created_at": now_bj().isoformat(),
            }

            logger.info(f"执行计划生成完成: {num_slices}个切片")

            return execution_plan

        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            logger.error(f"执行计划生成失败: {e}")
            return {"error": str(e)}

    def record_execution_result(
        self, execution_plan: dict, execution_result: dict
    ) -> None:
        """记录执行结果"""
        record = {
            "timestamp": now_bj().isoformat(),
            "plan": execution_plan,
            "result": execution_result,
            "success": execution_result.get("success", False),
            "execution_time": execution_result.get("execution_time", 0),
            "actual_slippage": execution_result.get("slippage", 0),
            "retry_attempts": execution_result.get("retry_attempts", 0),
        }

        self.execution_history.append(record)
        logger.info(f"执行结果记录: {'成功' if record['success'] else '失败'}")

    def get_execution_summary(self) -> dict:
        """获取执行总结"""
        if not self.execution_history:
            return {"message": "暂无执行历史"}

        latest_execution = self.execution_history[-1]

        total_executions = len(self.execution_history)
        successful_executions = sum(1 for r in self.execution_history if r["success"])
        success_rate = (
            successful_executions / total_executions if total_executions > 0 else 0.0
        )

        avg_execution_time = np.mean(
            [
                r["execution_time"]
                for r in self.execution_history
                if r["execution_time"] > 0
            ]
        )

        avg_slippage = np.mean(
            [
                r["actual_slippage"]
                for r in self.execution_history
                if r["actual_slippage"] is not None
            ]
        )

        strategy_stats = {}
        for record in self.execution_history:
            strategy = record["plan"]["strategy"]
            if strategy not in strategy_stats:
                strategy_stats[strategy] = {
                    "count": 0,
                    "success": 0,
                    "avg_time": 0.0,
                    "avg_slippage": 0.0,
                }

            stats = strategy_stats[strategy]
            stats["count"] += 1
            if record["success"]:
                stats["success"] += 1
            stats["avg_time"] = (
                stats["avg_time"] * (stats["count"] - 1) + record["execution_time"]
            ) / stats["count"]
            if record["actual_slippage"] is not None:
                stats["avg_slippage"] = (
                    stats["avg_slippage"] * (stats["count"] - 1)
                    + record["actual_slippage"]
                ) / stats["count"]

        return {
            "total_executions": total_executions,
            "success_rate": success_rate,
            "latest_execution": latest_execution["plan"]["strategy"],
            "average_execution_time_seconds": avg_execution_time,
            "average_slippage": avg_slippage,
            "strategy_stats": strategy_stats,
            "latest_time": latest_execution["timestamp"],
        }
