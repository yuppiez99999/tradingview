"""
WeatherAgent — 气象因子分析 Agent (v8.6.14)
============================================

基于 weather_factor_engine 的 7 因子体系, 评估气象条件对标的的影响.

决策逻辑:
    1. 获取标的地理坐标关联的天气数据 (实时/小时/天预报)
    2. 计算 7 个气象因子得分 (温度/降水/风速/辐照/气压/AQI/能见度)
    3. 按行业类别加权汇总 (电力敏感度 1.5x, 矿业 1.4x, 农业 1.5x)
    4. 输出 BUY/SELL/HOLD 决策 + 置信度

信号映射:
    composite_score >= 1.2 → BUY (气象强力利好)
    composite_score >= 0.4 → BUY (气象利好)
    composite_score <= -1.2 → SELL (气象强力利空)
    composite_score <= -0.4 → SELL (气象利空)
    其他 → HOLD (气象中性)

降级链:
    apizero.cn (彩云天气代理) → Open-Meteo (免费) → 本地缓存 → 中性

集成日期: 2026-08-01
"""

from __future__ import annotations

from typing import Any

from utils.finance_agents.base_agent import AgentDecision, BaseAgent


class WeatherAgent(BaseAgent):
    """气象因子分析 Agent — 第 6 位专家"""

    def __init__(self, name: str = "weather"):
        super().__init__(name=name)
        self._engine = None

    @property
    def engine(self):
        """懒加载 WeatherFactorEngine."""
        if self._engine is None:
            try:
                from utils.weather_factor_engine import get_engine
                self._engine = get_engine()
            except ImportError:
                self._engine = None
        return self._engine

    def is_available(self, context: dict[str, Any]) -> bool:
        """WeatherAgent 始终可用 (支持 Open-Meteo 降级链)."""
        weather_data = self._safe_get(context, "weather_data")
        if weather_data is not None:
            return True
        # 只要 engine 能实例化, 就认为可用 (降级链会处理数据源问题)
        try:
            engine = self.engine
            return engine is not None
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            return True  # 容错: 假设可用, analyze 内部会降级

    def analyze(self, symbol: str, context: dict[str, Any]) -> AgentDecision:
        """气象因子分析主入口.

        Args:
            symbol: 标的代码 (如 "600900.SH")
            context: 上下文, 可选包含:
                - weather_data: 预计算的 WeatherFactorResult 或 Dict
                - weather_force_refresh: 强制刷新

        Returns:
            AgentDecision 标准化决策
        """
        # 检查是否有预计算的气象结果
        pre_computed = self._safe_get(context, "weather_data")
        if pre_computed is not None:
            return self._decision_from_result(symbol, pre_computed)

        # 使用 engine 评估
        engine = self.engine
        if engine is None:
            return AgentDecision(
                agent_name=self.name,
                symbol=symbol,
                action="hold",
                strength=0.0,
                confidence=0.0,
                reasoning="气象数据源不可用, 降级为中性",
                key_metrics={
                    "data_source": "none",
                    "composite_score": 0.0,
                    "weather_signal": "NEUTRAL",
                    "confidence": 0.0,
                },
            )

        try:
            result = engine.evaluate_symbol(symbol)
            if result is None:
                return AgentDecision(
                    agent_name=self.name,
                    symbol=symbol,
                    action="hold",
                    strength=0.0,
                    confidence=0.1,
                    reasoning=f"标的 {symbol} 未在气象映射配置中找到",
                    key_metrics={"data_source": "none", "mapped": False},
                )

            return self._decision_from_result(symbol, result)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            return AgentDecision(
                agent_name=self.name,
                symbol=symbol,
                action="hold",
                strength=0.0,
                confidence=0.0,
                reasoning=f"气象因子计算异常: {e}",
                key_metrics={"error": str(e)[:100]},
            )

    def _decision_from_result(
        self, symbol: str, result: Any
    ) -> AgentDecision:
        """从 WeatherFactorResult 或 Dict 构建 AgentDecision."""
        # 支持两种输入: dict 或 WeatherFactorResult dataclass
        if isinstance(result, dict):
            composite = self._safe_float(result.get("composite_score"))
            confidence = self._safe_float(result.get("confidence"))
            signal = result.get("signal", "NEUTRAL")
            reasoning = result.get("reasoning", "")
            data_source = result.get("data_source", "unknown")
            category = result.get("category", "")
            weather_sensitivity = result.get("weather_sensitivity", 0.0)
            drivers = result.get("key_drivers", [])
            alerts = result.get("alerts", [])
        else:
            # WeatherFactorResult dataclass
            composite = getattr(result, "composite_score", 0.0)
            confidence = getattr(result, "confidence", 0.0)
            signal = getattr(result, "signal", "NEUTRAL")
            reasoning = getattr(result, "reasoning", "")
            data_source = getattr(result, "data_source", "unknown")
            category = getattr(result, "category", "")
            weather_sensitivity = getattr(result, "weather_sensitivity", 0.0)
            factors = getattr(result, "factors", [])
            drivers = [getattr(f, "description", "") or f.name
                       for f in factors[:3]] if factors else []
            alerts = getattr(result, "alerts", []) or []

        # 信号 → strength: composite [-2, 2] → strength [-1, 1]
        strength = max(-1.0, min(1.0, composite / 2.0))

        # 信号 → action
        action_map = {
            "STRONG_BULL": "buy",
            "BULLISH": "buy",
            "NEUTRAL": "hold",
            "BEARISH": "sell",
            "STRONG_BEAR": "sell",
        }
        action = action_map.get(signal, "hold")

        # 关键指标
        key_metrics: dict[str, Any] = {
            "composite_score": round(composite, 3),
            "weather_signal": signal,
            "confidence": round(confidence, 3),
            "data_source": data_source,
            "category": category,
            "weather_sensitivity": weather_sensitivity,
        }

        # 添加驱动因子
        if drivers:
            key_metrics["key_drivers"] = drivers[:3]

        # 添加预警
        if alerts:
            key_metrics["weather_alerts"] = alerts[:3]

        return AgentDecision(
            agent_name=self.name,
            symbol=symbol,
            action=action,
            strength=round(strength, 4),
            confidence=round(confidence, 4),
            reasoning=reasoning[:200] if reasoning else f"气象综合得分 {composite:.2f} ({signal})",
            key_metrics=key_metrics,
        )


def create_weather_agent() -> WeatherAgent:
    """工厂函数: 创建 WeatherAgent 实例."""
    return WeatherAgent()
