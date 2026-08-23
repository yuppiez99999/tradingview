"""自动对冲再平衡进化系统 — 自定义异常定义。

本模块定义了自动对冲再平衡系统在决策流程中可能抛出的全部异常类型，
供主协调器 AutoHedgeRebalanceEngine 进行异常映射与降级处理。

异常层级:
    PortfolioRiskAssessmentError      — 组合风险评估失败 (波动率/回撤计算异常)
    AllHedgeToolPriceUnavailable      — 全部对冲工具行情不可用
    BacktestPrecheckTimeout           — 策略可行性回测预检超时
    CircuitBreakerActive              — 熔断器处于活跃状态，拒绝新交易
    ToolPriceUnavailable              — 单一对冲工具行情不可用 (可降级)
    RegimeIsCalm                      — 市场状态为 CALM，无需对冲 (非错误，控制流信号)
    AllToolsUnavailable               — 全部工具选择失败 (工具池耗尽)
"""

from __future__ import annotations


class AutoHedgeRebalanceError(Exception):
    """自动对冲再平衡系统基础异常。

    所有本系统抛出的异常均继承自此基类，便于上层统一捕获与降级处理。
    """

    def __init__(self, message: str = "", *, degrade_to: str = "") -> None:
        """初始化异常实例。

        Args:
            message: 异常描述信息。
            degrade_to: 建议的降级目标 (如 "default_volatility"、"fallback_price")。
        """
        super().__init__(message)
        self.message = message
        self.degrade_to = degrade_to


class PortfolioRiskAssessmentError(AutoHedgeRebalanceError):
    """组合风险评估失败。

    触发场景: 波动率计算数据不足、回撤计算异常、持仓数据缺失。
    降级策略: 使用默认波动率 18% + 回撤 0% 兜底。
    """


class AllHedgeToolPriceUnavailable(AutoHedgeRebalanceError):
    """全部对冲工具行情不可用。

    触发场景: Wind/TDX/AKShare/Sina 全链失效，缓存亦不可用。
    降级策略: 使用兜底预定义价格。
    """


class BacktestPrecheckTimeout(AutoHedgeRebalanceError):
    """策略可行性回测预检超时。

    触发场景: HedgeRebalanceBacktest.run_backtest() 执行超过 120 秒。
    降级策略: 维持当前策略，标记"预检超时未验证"。
    """


class CircuitBreakerActive(AutoHedgeRebalanceError):
    """熔断器处于活跃状态。

    触发场景: 单日跌幅 >5% 或回撤 >25% 触发熔断后，熔断器锁定。
    降级策略: 返回熔断状态计划，拒绝执行任何新交易，等待管理员解除。
    """


class ToolPriceUnavailable(AutoHedgeRebalanceError):
    """单一对冲工具行情不可用。

    触发场景: 特定期货/期权/反向 ETF 行情获取失败。
    降级策略: 按降级链切换至下一工具 (ETF期权→股指期货→反向ETF→缓存→兜底)。
    """


class RegimeIsCalm(AutoHedgeRebalanceError):
    """市场状态为 CALM，无需对冲。

    触发场景: 组合自驱动状态判定为 CALM (低波动率 + 低回撤)。
    说明: 此异常为控制流信号而非错误，主协调器捕获后跳过对冲阶段。
    """


class AllToolsUnavailable(AutoHedgeRebalanceError):
    """全部工具选择失败。

    触发场景: 工具池耗尽，所有对冲工具均被成本效益过滤或行情不可用排除。
    降级策略: 返回空选择，标记"工具池耗尽"，仅执行再平衡。
    """


__all__ = [
    "AutoHedgeRebalanceError",
    "PortfolioRiskAssessmentError",
    "AllHedgeToolPriceUnavailable",
    "BacktestPrecheckTimeout",
    "CircuitBreakerActive",
    "ToolPriceUnavailable",
    "RegimeIsCalm",
    "AllToolsUnavailable",
]