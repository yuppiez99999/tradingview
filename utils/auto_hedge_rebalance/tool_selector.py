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
from enum import StrEnum
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


class MarketRegime(StrEnum):
    """组合自驱动市场状态 — 4 档。

    由组合自身波动率与回撤判定，非大盘市场状态。
    """

    CALM = "CALM"  # 平静: 低波动率 + 低回撤
    MILD = "MILD"  # 温和: 中波动率
    HIGH = "HIGH"  # 高波动: 高波动率
    TAIL_EVENT = "TAIL_EVENT"  # 尾部事件: 极端回撤


# ============================================================================
# Beta 分类阈值
# ============================================================================

_LARGE_CAP_BETA_THRESHOLD = 0.8  # Beta > 0.8 视为偏大盘

# v8.7: IV 感知阈值 — 期权成本随 IV 变化
_IV_CHEAP_THRESHOLD = 25.0  # IV < 25 时期权便宜, 优先用期权保护
_IV_EXPENSIVE_THRESHOLD = 35.0  # IV > 35 时期权太贵, 用期货替代


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
        iv_level: Optional[float] = None,
    ) -> ToolSelection:
        """按市场状态+组合 Beta 选择对冲工具。

        Args:
            regime: 组合自驱动市场状态。
            risk: 组合风险评估。
            hedge_ratio: 对冲比例 (0.0-1.0)。
            prices: 工具行情价格字典 (可选)。
            iv_level: v8.7 隐含波动率水平 (可选, 用于 IV 感知工具选择).

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

        # 按决策表选择工具类型 (v8.7: 传入 iv_level)
        tool_type, instruments, options_strategy = self._select_by_regime_beta(
            regime, beta, iv_level
        )

        # TAIL_EVENT 特殊处理 (v8.7: 传入 iv_level)
        if regime == MarketRegime.TAIL_EVENT:
            tool_type, instruments, options_strategy = self._select_by_tail_event(
                beta, iv_level
            )

        # 生成对冲方案
        futures_contracts: dict[str, int] = {}
        options_contracts: list[dict[str, Any]] = []
        cost_estimate = 0.0
        expected_benefit = 0.0

        if tool_type == HedgeToolType.INDEX_FUTURES and self.hedge_engine is not None:
            try:
                futures_contracts = self._generate_futures_hedge(
                    instruments, hedge_ratio, risk
                )
            except Exception as exc:
                logger.warning("期货对冲生成失败，降级至兜底: %s", exc)
                fallback_flags.append(f"期货对冲生成失败: {exc}")

        elif tool_type == HedgeToolType.ETF_OPTIONS and self.hedge_engine is not None:
            try:
                options_contracts = self._generate_options_hedge(
                    instruments, hedge_ratio, options_strategy
                )
            except Exception as exc:
                logger.warning("期权对冲生成失败，降级至期货: %s", exc)
                fallback_flags.append(f"期权对冲生成失败，降级至期货: {exc}")
                tool_type = HedgeToolType.INDEX_FUTURES
                instruments = ["IF", "IC"]
                options_strategy = None

        reasoning = f"市场状态={regime.value}, Beta={beta:.2f}, 选择工具={tool_type.value}, 对冲比例={hedge_ratio:.0%}"

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
        iv_level: Optional[float] = None,
    ) -> tuple[HedgeToolType, list[str], Optional[OptionsStrategy]]:
        """按市场状态+Beta 决策表选择工具类型。

        v8.7 改进: HIGH 状态区别于 MILD — IV 低时增配期权保护, IV 高时仅用期货.
        v8.7 改进: IV 感知 — IV < 25 期权便宜优先用, IV > 35 期权贵用期货.

        Returns:
            (工具类型, 标的列表, 期权策略)。
        """
        is_large_cap = beta > _LARGE_CAP_BETA_THRESHOLD
        iv_cheap = iv_level is not None and iv_level < _IV_CHEAP_THRESHOLD

        if regime == MarketRegime.CALM:
            return HedgeToolType.NONE, [], None

        if regime == MarketRegime.MILD:
            # MILD: 仅期货对冲, 不加期权 (成本意识)
            if is_large_cap:
                return HedgeToolType.INDEX_FUTURES, ["IF", "IH"], None
            return HedgeToolType.INDEX_FUTURES, ["IC", "IM"], None

        if regime == MarketRegime.HIGH:
            # v8.7: HIGH 区别于 MILD — IV 便宜时增配期权保护
            if is_large_cap:
                if iv_cheap:
                    return (
                        HedgeToolType.ETF_OPTIONS,
                        ["510300", "510050"],
                        OptionsStrategy.PROTECTIVE_PUT,
                    )
                return HedgeToolType.INDEX_FUTURES, ["IF", "IH"], None
            if iv_cheap:
                return (
                    HedgeToolType.MIXED,
                    ["510300", "IC", "IM"],
                    OptionsStrategy.PROTECTIVE_PUT,
                )
            return HedgeToolType.INDEX_FUTURES, ["IC", "IM"], None

        # TAIL_EVENT 在 _select_by_tail_event 中处理
        return HedgeToolType.ETF_OPTIONS, ["510300"], OptionsStrategy.PROTECTIVE_PUT

    def _select_by_tail_event(
        self,
        beta: float,
        iv_level: Optional[float] = None,
    ) -> tuple[HedgeToolType, list[str], Optional[OptionsStrategy]]:
        """TAIL_EVENT 状态工具选择。

        v8.7 改进: IV 感知策略选择 —
        - IV < 20: protective put (期权便宜, 直接买保护)
        - IV 20-35: collar (卖出 call 融资买 put, 降低成本)
        - IV > 35: 期货为主 (期权太贵, 用期货替代)
        """
        is_large_cap = beta > _LARGE_CAP_BETA_THRESHOLD

        # v8.7: 无 IV 时回退到原有行为 (向后兼容)
        if iv_level is None:
            if is_large_cap:
                return (
                    HedgeToolType.ETF_OPTIONS,
                    ["510300", "510050"],
                    OptionsStrategy.PROTECTIVE_PUT,
                )
            return HedgeToolType.MIXED, ["510300", "IC", "IM"], OptionsStrategy.COLLAR

        # v8.7: IV 感知策略选择
        if iv_level > _IV_EXPENSIVE_THRESHOLD:
            # IV 太贵: 期货为主, 放弃期权
            if is_large_cap:
                return HedgeToolType.INDEX_FUTURES, ["IF", "IH"], None
            return HedgeToolType.INDEX_FUTURES, ["IC", "IM"], None

        if iv_level is not None and iv_level < 20.0:
            # IV 便宜: protective put (直接买保护)
            if is_large_cap:
                return (
                    HedgeToolType.ETF_OPTIONS,
                    ["510300", "510050"],
                    OptionsStrategy.PROTECTIVE_PUT,
                )
            return (
                HedgeToolType.MIXED,
                ["510300", "IC", "IM"],
                OptionsStrategy.PROTECTIVE_PUT,
            )

        # IV 中等 (20-35): collar (融资买保护)
        if is_large_cap:
            return (
                HedgeToolType.ETF_OPTIONS,
                ["510300", "510050"],
                OptionsStrategy.COLLAR,
            )
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
            return dict.fromkeys(instruments, 1)

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
                strategy=(
                    options_strategy.value if options_strategy else "protective_put"
                ),
            )
            if isinstance(result, list):
                return result
            if isinstance(result, dict):
                return [result]
            return []
        except TypeError:
            return [
                {
                    "code": inst,
                    "strategy": options_strategy.value if options_strategy else "",
                }
                for inst in instruments
            ]


__all__ = ["HedgeToolSelector", "MarketRegime"]
