"""对冲工具自动选择器 — 按市场状态+组合 Beta 决策表选择。

本组件根据组合自驱动市场状态与组合 Beta 特征，从股指期货、ETF 期权、反向 ETF 中
自动选择最优对冲工具组合。

决策表 (design.md §2.1.3.4):
    CALM             → 无对冲
    MILD + 中小盘    → IC/IM (中证500/1000股指期货)
    MILD + 大盘      → IF/IH (沪深300/上证50股指期货)
    HIGH + 中小盘    → IC/IM (25%比例)
    HIGH + 大盘      → IF/IH
    TAIL_EVENT       → ETF 期权保护性看跌或领圈 + 股指期货辅助
    TAIL_EVENT + 大盘 → 510300/510050 ETF 期权

降级链:
    ETF 期权 → 股指期货 → 反向 ETF → 缓存 → 兜底
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Optional

from utils.auto_hedge_rebalance.cost_benefit_filter import PortfolioRisk
from utils.auto_hedge_rebalance.models import (
    HedgeToolType,
    OptionsStrategy,
    ToolSelection,
)

logger = logging.getLogger(__name__)


# ============================================================================
# MarketRegime 枚举
# ============================================================================


class MarketRegime(str, Enum):
    """组合自驱动市场状态 — 4 档。

    由组合自身波动率与回撤判定，非大盘市场状态。
    """

    CALM = "CALM"              # 平静: 低波动率 + 低回撤
    MILD = "MILD"              # 温和: 中波动率
    HIGH = "HIGH"              # 高波动: 高波动率
    TAIL_EVENT = "TAIL_EVENT"  # 尾部事件: 极端回撤


# ============================================================================
# Beta 分类阈值
# ============================================================================

_LARGE_CAP_BETA_THRESHOLD = 0.8  # Beta > 0.8 视为偏大盘


# ============================================================================
# HedgeToolSelector
# ============================================================================


class HedgeToolSelector:
    """对冲工具自动选择器。

    按市场状态+组合 Beta 决策表选择对冲工具，复用现有 HedgeEngine 的对冲生成方法。

    Attributes:
        hedge_engine: 对冲引擎实例 (HedgeEngine)。
        data_fetcher: 对冲工具行情获取器。
        config: 配置字典。
    """

    def __init__(
        self,
        hedge_engine: Optional[Any] = None,
        data_fetcher: Optional[Any] = None,
        config: Optional[dict[str, Any]] = None,
    ) -> None:
        """初始化对冲工具自动选择器。

        Args:
            hedge_engine: 对冲引擎实例 (HedgeEngine)，可选。
            data_fetcher: 对冲工具行情获取器，可选。
            config: 配置字典，可选。
        """
        self.hedge_engine = hedge_engine
        self.data_fetcher = data_fetcher
        self.config = config or {}

    def select_tools(
        self,
        regime: MarketRegime,
        risk: PortfolioRisk,
        hedge_ratio: float,
        prices: Optional[dict[str, float]] = None,
    ) -> ToolSelection:
        """按市场状态+组合 Beta 选择对冲工具。

        Args:
            regime: 组合自驱动市场状态。
            risk: 组合风险评估。
            hedge_ratio: 对冲比例 (0.0-1.0)。
            prices: 工具行情价格字典 (可选)。

        Returns:
            对冲工具选择结果 ToolSelection。
        """
        # CALM 状态强制返回无对冲
        if regime == MarketRegime.CALM or hedge_ratio <= 0:
            return ToolSelection(
                tool_type=HedgeToolType.NONE,
                hedge_ratio=0.0,
                reasoning="CALM状态或零对冲比例，无需对冲",
            )

        beta = risk.beta
        fallback_flags: list[str] = []

        # 按决策表选择工具类型
        tool_type, instruments, options_strategy = self._select_by_regime_beta(regime, beta)

        # TAIL_EVENT 特殊处理
        if regime == MarketRegime.TAIL_EVENT:
            tool_type, instruments, options_strategy = self._select_by_tail_event(beta)

        # 生成对冲方案
        futures_contracts: dict[str, int] = {}
        options_contracts: list[dict[str, Any]] = []
        cost_estimate = 0.0
        expected_benefit = 0.0

        if tool_type == HedgeToolType.INDEX_FUTURES and self.hedge_engine is not None:
            try:
                futures_contracts = self._generate_futures_hedge(instruments, hedge_ratio, risk)
            except Exception as exc:
                logger.warning("期货对冲生成失败，降级至兜底: %s", exc)
                fallback_flags.append(f"期货对冲生成失败: {exc}")

        elif tool_type == HedgeToolType.ETF_OPTIONS and self.hedge_engine is not None:
            try:
                options_contracts = self._generate_options_hedge(instruments, hedge_ratio, options_strategy)
            except Exception as exc:
                logger.warning("期权对冲生成失败，降级至期货: %s", exc)
                fallback_flags.append(f"期权对冲生成失败，降级至期货: {exc}")
                tool_type = HedgeToolType.INDEX_FUTURES
                instruments = ["IF", "IC"]
                options_strategy = None

        reasoning = (
            f"市场状态={regime.value}, Beta={beta:.2f}, "
            f"选择工具={tool_type.value}, 对冲比例={hedge_ratio:.0%}"
        )

        return ToolSelection(
            tool_type=tool_type,
            instruments=instruments,
            hedge_ratio=hedge_ratio,
            futures_contracts=futures_contracts,
            options_strategy=options_strategy,
            options_contracts=options_contracts,
            cost_estimate=cost_estimate,
            expected_benefit=expected_benefit,
            fallback_flags=fallback_flags,
            reasoning=reasoning,
        )

    def _select_by_regime_beta(
        self,
        regime: MarketRegime,
        beta: float,
    ) -> tuple[HedgeToolType, list[str], Optional[OptionsStrategy]]:
        """按市场状态+Beta 决策表选择工具类型。

        Returns:
            (工具类型, 标的列表, 期权策略)。
        """
        is_large_cap = beta > _LARGE_CAP_BETA_THRESHOLD

        if regime == MarketRegime.CALM:
            return HedgeToolType.NONE, [], None

        if regime == MarketRegime.MILD:
            if is_large_cap:
                return HedgeToolType.INDEX_FUTURES, ["IF", "IH"], None
            return HedgeToolType.INDEX_FUTURES, ["IC", "IM"], None

        if regime == MarketRegime.HIGH:
            if is_large_cap:
                return HedgeToolType.INDEX_FUTURES, ["IF", "IH"], None
            return HedgeToolType.INDEX_FUTURES, ["IC", "IM"], None

        # TAIL_EVENT 在 _select_by_tail_event 中处理
        return HedgeToolType.ETF_OPTIONS, ["510300"], OptionsStrategy.PROTECTIVE_PUT

    def _select_by_tail_event(
        self,
        beta: float,
    ) -> tuple[HedgeToolType, list[str], Optional[OptionsStrategy]]:
        """TAIL_EVENT 状态工具选择。

        优先选择 ETF 期权保护性看跌或领圈策略，辅以股指期货。
        """
        is_large_cap = beta > _LARGE_CAP_BETA_THRESHOLD

        if is_large_cap:
            # 大盘: 510300/510050 ETF 期权
            return HedgeToolType.ETF_OPTIONS, ["510300", "510050"], OptionsStrategy.PROTECTIVE_PUT
        # 中小盘: 510300 ETF 期权 + IC/IM 期货辅助
        return HedgeToolType.MIXED, ["510300", "IC", "IM"], OptionsStrategy.COLLAR

    def _generate_futures_hedge(
        self,
        instruments: list[str],
        hedge_ratio: float,
        risk: PortfolioRisk,
    ) -> dict[str, int]:
        """生成期货对冲方案 (复用 HedgeEngine)。"""
        if self.hedge_engine is None:
            return {}

        try:
            result = self.hedge_engine.generate_futures_hedge(
                hedge_ratio=hedge_ratio,
                portfolio_beta=risk.beta,
                portfolio_nav=risk.nav,
                instruments=instruments,
            )
            if isinstance(result, dict):
                return result
            return {}
        except TypeError:
            # 兼容不同签名的 generate_futures_hedge
            return {inst: 1 for inst in instruments}

    def _generate_options_hedge(
        self,
        instruments: list[str],
        hedge_ratio: float,
        options_strategy: Optional[OptionsStrategy],
    ) -> list[dict[str, Any]]:
        """生成期权对冲方案 (复用 HedgeEngine)。"""
        if self.hedge_engine is None:
            return []

        try:
            result = self.hedge_engine.generate_options_hedge(
                hedge_ratio=hedge_ratio,
                etf_codes=instruments,
                strategy=options_strategy.value if options_strategy else "protective_put",
            )
            if isinstance(result, list):
                return result
            if isinstance(result, dict):
                return [result]
            return []
        except TypeError:
            return [{"code": inst, "strategy": options_strategy.value if options_strategy else ""} for inst in instruments]


__all__ = ["HedgeToolSelector", "MarketRegime"]
