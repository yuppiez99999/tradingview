"""
分层组合构建 + 风险预算

对冲基金工业级组合构建：
- 三层结构: 短线(20只) + 中线(30只) + 长线(50只) = 100 只
- 三层权重: 短线 20% / 中线 30% / 长线 50%
- 风险约束:
  - 单股权重 ≤ 5%
  - 单行业暴露 ≤ 25%
  - 组合年化波动率目标 15-20%
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class PortfolioConfig:
    """组合配置"""

    # 三层选股数
    short_count: int = 20  # 短线持仓数
    mid_count: int = 30  # 中线持仓数
    long_count: int = 50  # 长线持仓数

    # 三层权重
    short_weight: float = 0.20  # 短线层总权重
    mid_weight: float = 0.30  # 中线层总权重
    long_weight: float = 0.50  # 长线层总权重

    # 风险约束
    max_single_position: float = 0.05  # 单股上限 5%
    max_industry_exposure: float = 0.25  # 单行业上限 25%
    target_volatility: float = 0.18  # 目标年化波动率 18%

    # 选股逻辑偏好
    short_emphasis: str = "momentum"  # 短线偏好动量
    mid_emphasis: str = "balanced"  # 中线均衡
    long_emphasis: str = "low_vol"  # 长线偏好低波动


@dataclass
class Holding:
    """单只持仓"""

    symbol: str
    name: str = ""
    industry: str = ""
    layer: str = ""  # short / mid / long
    weight: float = 0.0  # 最终权重
    score_composite: float = 0.0
    score_momentum: float = 0.0
    score_reversal: float = 0.0
    score_volume: float = 0.0
    score_volatility: float = 0.0
    score_liquidity: float = 0.0
    rank: int = 0
    reason: str = ""


@dataclass
class LayeredPortfolio:
    """分层组合"""

    trade_date: str = ""
    holdings: list[Holding] = field(default_factory=list)
    universe_size: int = 0  # 股票池总数
    filtered_size: int = 0  # 过滤后总数
    # 行业暴露
    industry_exposure: dict[str, float] = field(default_factory=dict)
    # 各层统计
    layer_stats: dict[str, dict] = field(default_factory=dict)
    # 风险指标
    portfolio_volatility: float = 0.0
    concentration_hhi: float = 0.0  # Herfindahl 集中度

    def to_df(self) -> pd.DataFrame:
        """转换为 DataFrame"""
        rows = []
        for h in self.holdings:
            rows.append(
                {
                    "symbol": h.symbol,
                    "name": h.name,
                    "industry": h.industry,
                    "layer": h.layer,
                    "weight": h.weight,
                    "rank": h.rank,
                    "composite_score": h.score_composite,
                    "momentum_score": h.score_momentum,
                    "reversal_score": h.score_reversal,
                    "volume_score": h.score_volume,
                    "volatility_score": h.score_volatility,
                    "liquidity_score": h.score_liquidity,
                    "reason": h.reason,
                }
            )
        return pd.DataFrame(rows)

    def summary(self) -> str:
        """生成摘要"""
        lines = [
            "=" * 60,
            f"分层组合摘要 (trade_date={self.trade_date})",
            "=" * 60,
            f"股票池: {self.universe_size} 只 → 过滤后 {self.filtered_size} 只",
            f"持仓数: {len(self.holdings)} 只 (短{sum(1 for h in self.holdings if h.layer == 'short')}/"
            f"中{sum(1 for h in self.holdings if h.layer == 'mid')}/"
            f"长{sum(1 for h in self.holdings if h.layer == 'long')})",
            f"组合波动率(预估): {self.portfolio_volatility * 100:.2f}%",
            f"集中度 HHI: {self.concentration_hhi:.4f}",
            "",
            "行业暴露 Top 5:",
        ]
        for ind, exp in sorted(self.industry_exposure.items(), key=lambda x: -x[1])[:5]:
            lines.append(f"  {ind}: {exp * 100:.2f}%")
        lines.append("=" * 60)
        return "\n".join(lines)


def _get_score_column(scores_df: pd.DataFrame, theme: str) -> str:
    """获取主题得分列名"""
    col = f"{theme}_score"
    if col in scores_df.columns:
        return col
    return "composite_score"


def _select_layer(
    scores_df: pd.DataFrame,
    n: int,
    emphasis: str,
    industry_map: dict[str, str],
    name_map: dict[str, str],
    exclude_symbols: set,
    layer_name: str,
) -> list[Holding]:
    """选取单层组合

    Args:
        scores_df: 打分 DataFrame (index=symbol, columns=[*_score, composite_score, rank])
        n: 选取数量
        emphasis: 偏好主题 (momentum/reversal/balanced/low_vol)
        industry_map: {symbol: industry}
        name_map: {symbol: name}
        exclude_symbols: 已选股票（避免跨层重复）
        layer_name: 层名

    Returns:
        List[Holding]
    """
    available = scores_df[~scores_df.index.isin(exclude_symbols)].copy()
    if available.empty:
        return []

    # 根据偏好重新排序
    if emphasis == "momentum":
        sort_col = _get_score_column(available, "momentum")
        available = available.sort_values(sort_col, ascending=False)
        reason = "动量得分最高"
    elif emphasis == "reversal":
        sort_col = _get_score_column(available, "reversal")
        available = available.sort_values(sort_col, ascending=False)
        reason = "反转得分最高"
    elif emphasis == "low_vol":
        # 低波动 = volatility_score 低（但我们的打分是 z-score，越高越好意味着相对低波动）
        sort_col = _get_score_column(available, "volatility")
        available = available.sort_values(sort_col, ascending=False)
        reason = "波动率得分最高(低波动)"
    else:  # balanced
        available = available.sort_values("composite_score", ascending=False)
        reason = "综合得分最高"

    # 行业分散：每行业最多 N 只（动态：若可用行业过少则放宽）
    from collections import Counter

    industry_counter = Counter(industry_map.get(s, "未知") for s in available.index)
    unique_industries = len([c for c in industry_counter.values() if c > 0])
    # 单行业上限 = max(3, ceil(n / max(unique_industries, 1)))
    max_per_industry = max(3, -(-n // max(unique_industries, 1)))  # ceil division
    industry_count: dict[str, int] = {}
    selected: list[Holding] = []
    for sym, row in available.iterrows():
        if len(selected) >= n:
            break
        industry = industry_map.get(sym, "未知")
        if industry_count.get(industry, 0) >= max_per_industry:
            continue
        industry_count[industry] = industry_count.get(industry, 0) + 1
        selected.append(
            Holding(
                symbol=sym,
                name=name_map.get(sym, ""),
                industry=industry,
                layer=layer_name,
                score_composite=float(row.get("composite_score", 0)),
                score_momentum=float(row.get("momentum_score", 0)),
                score_reversal=float(row.get("reversal_score", 0)),
                score_volume=float(row.get("volume_score", 0)),
                score_volatility=float(row.get("volatility_score", 0)),
                score_liquidity=float(row.get("liquidity_score", 0)),
                rank=int(row.get("rank", 0)),
                reason=reason,
            )
        )
    return selected


def apply_risk_constraints(
    portfolio: LayeredPortfolio,
    config: PortfolioConfig,
) -> LayeredPortfolio:
    """应用风险预算约束

    - 单股权重 ≤ 5%
    - 单行业暴露 ≤ 25%
    """
    # 各层总权重
    layer_total = {
        "short": config.short_weight,
        "mid": config.mid_weight,
        "long": config.long_weight,
    }

    # 计算每只股票的初始权重
    layer_holdings: dict[str, list[Holding]] = {"short": [], "mid": [], "long": []}
    for h in portfolio.holdings:
        layer_holdings[h.layer].append(h)

    for layer, holdings in layer_holdings.items():
        if not holdings:
            continue
        total_weight = layer_total[layer]
        # 等权分配
        per_stock = total_weight / len(holdings)
        for h in holdings:
            h.weight = min(per_stock, config.max_single_position)

    # 行业暴露约束
    industry_total: dict[str, float] = {}
    for h in portfolio.holdings:
        industry_total[h.industry] = industry_total.get(h.industry, 0) + h.weight

    over_exposed = {ind: w for ind, w in industry_total.items() if w > config.max_industry_exposure}
    if over_exposed:
        logger.warning(f"行业暴露超限: {over_exposed}")
        # 简单处理：超限行业按比例缩减
        for ind, w in over_exposed.items():
            scale = config.max_industry_exposure / w
            for h in portfolio.holdings:
                if h.industry == ind:
                    h.weight *= scale

    # 重新归一化总权重到 100%
    total_w = sum(h.weight for h in portfolio.holdings)
    if total_w > 0:
        for h in portfolio.holdings:
            h.weight /= total_w

    # 更新行业暴露
    portfolio.industry_exposure = {}
    for h in portfolio.holdings:
        portfolio.industry_exposure[h.industry] = portfolio.industry_exposure.get(h.industry, 0) + h.weight

    # 计算 HHI 集中度
    weights = np.array([h.weight for h in portfolio.holdings])
    portfolio.concentration_hhi = float(np.sum(weights**2))

    # 各层统计
    portfolio.layer_stats = {}
    for layer in ["short", "mid", "long"]:
        layer_hs = [h for h in portfolio.holdings if h.layer == layer]
        if layer_hs:
            portfolio.layer_stats[layer] = {
                "count": len(layer_hs),
                "total_weight": sum(h.weight for h in layer_hs),
                "avg_score": np.mean([h.score_composite for h in layer_hs]),
                "industries": len(set(h.industry for h in layer_hs)),
            }

    return portfolio


def build_layered_portfolio(
    scores_df: pd.DataFrame,
    industry_map: dict[str, str],
    name_map: dict[str, str] | None = None,
    config: PortfolioConfig | None = None,
    trade_date: str = "",
    universe_size: int = 0,
    filtered_size: int = 0,
) -> LayeredPortfolio:
    """构建分层组合

    Args:
        scores_df: 横截面打分 (index=symbol, columns=[*_score, composite_score, rank])
        industry_map: {symbol: industry}
        name_map: {symbol: name}
        config: 组合配置
        trade_date: 交易日期
        universe_size: 初始股票池大小
        filtered_size: 过滤后股票池大小

    Returns:
        LayeredPortfolio
    """
    if config is None:
        config = PortfolioConfig()
    if name_map is None:
        name_map = {}

    portfolio = LayeredPortfolio(
        trade_date=trade_date,
        universe_size=universe_size,
        filtered_size=filtered_size,
    )

    # 三层选股
    exclude: set = set()
    layers = [
        ("short", config.short_count, config.short_emphasis, "短线"),
        ("mid", config.mid_count, config.mid_emphasis, "中线"),
        ("long", config.long_count, config.long_emphasis, "长线"),
    ]
    for layer_name, n, emphasis, label in layers:
        holdings = _select_layer(scores_df, n, emphasis, industry_map, name_map, exclude, layer_name)
        exclude.update(h.symbol for h in holdings)
        portfolio.holdings.extend(holdings)
        logger.info(f"  {label}层 [{emphasis}]: 选取 {len(holdings)} 只 (累计 {len(portfolio.holdings)})")

    # 应用风险约束
    portfolio = apply_risk_constraints(portfolio, config)

    logger.info(f"\n{portfolio.summary()}")
    return portfolio


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # 自测
    cfg = PortfolioConfig()
    logger.info(cfg)
