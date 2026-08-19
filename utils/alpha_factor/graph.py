"""图神经网络产业链因子 (Lead-Lag) — 第 12 大类

基于供应链关系图, 将邻居(上下游/概念关联)的基本面/收益作为因子特征注入,
捕获传统单股横截面因子缺失的「产业链传导」维度。

核心思想 (Lead-Lag 效应):
  上游/关联公司的基本面变动, 会通过供应链关系链式传导到中下游。
  本模块用邻居聚合特征作为个股因子值: 邻居动量高 → 该股后续大概率跟随。
  即 "邻居先动, 个股滞后", 实现提前抢跑。

数据依赖:
  - price_data: 同 price_volume.py 的 {sym: {closes/volumes/highs/lows}}
  - graph: SupplyChainGraph 实例 (由 utils.supply_chain_builder 构建)

因子列表 (5 个, category="LeadLag"):
  CHAIN_MOM_20D       邻居 20 日动量 strength 加权均值
  CHAIN_MOM_60D       邻居 60 日动量 strength 加权均值 (与 20D 部分共线, 提供正交化)
  CHAIN_REVERSAL_5D  邻居短期反转 (负动量, 邻居超跌 → 该股机会)
  CHAIN_NEIGHBOR_DIFF 个股动量 - 邻居动量 (脱钩度: 高 = 个股领先于产业链)
  CHAIN_CONCENTRATION 邻居强度集中度 (赫芬达尔, 高 = 依赖单一强邻居)

参考:
  - 图神经网络产业链因子研报 (Lead-Lag, GCN/GAT)
  - 本项目 cairn/gnn-supply-chain-factor.md 设计文档
"""

from __future__ import annotations

import numpy as np

from utils.alpha_factor.base import FactorValue, residualize


def _momentum(closes: list[float], window: int) -> float | None:
    """计算收盘价序列的 window 日动量."""
    if len(closes) > window and closes[-window] > 0:
        return float(closes[-1] / closes[-window] - 1)
    return None


def _neighbor_map(graph, sym: str) -> dict[str, float]:
    """获取 sym 在图中所有邻居及其边强度 (source->target 出边).

    Returns:
        {neighbor_symbol: strength}
    """
    neighbors: dict[str, float] = {}
    if graph is None:
        return neighbors
    for edge in graph.adjacency.get(sym, []):
        # 只取有强度且目标节点有效的边
        s = float(getattr(edge, "strength", 0) or 0)
        if s > 0:
            neighbors[edge.target] = s
    return neighbors


def _weighted_avg(values: dict[str, float], weights: dict[str, float]) -> float | None:
    """strength 加权均值, 仅用有权重且有效的值."""
    num = 0.0
    den = 0.0
    for sym, v in values.items():
        w = weights.get(sym, 0)
        if w > 0 and v is not None:
            num += w * v
            den += w
    return num / den if den > 0 else None


def compute_lead_lag_factors(
    price_data: dict[str, dict[str, list[float]]],
    graph=None,
    industries: dict[str, str] | None = None,
    min_neighbors: int = 1,
) -> dict[str, FactorValue]:
    """Lead-Lag 产业链因子 5 个 (category="LeadLag")

    Args:
        price_data: {sym: {closes/volumes/highs/lows}}
        graph: SupplyChainGraph 实例 (含 adjacency), 提供邻居关系
        industries: {sym: industry} 行业映射 (用于行业中性化, 可选)
        min_neighbors: 因子有效所需最少邻居数

    Returns:
        dict[str, FactorValue]
    """
    factors: dict[str, FactorValue] = {}

    # 预计算每只股票的动量 (避免重复)
    mom_20d: dict[str, float] = {}
    mom_60d: dict[str, float] = {}
    rev_5d: dict[str, float] = {}
    for sym, data in price_data.items():
        closes = data.get("closes", [])
        m20 = _momentum(closes, 20)
        m60 = _momentum(closes, 60)
        m5 = _momentum(closes, 5)
        if m20 is not None:
            mom_20d[sym] = m20
        if m60 is not None:
            mom_60d[sym] = m60
        if m5 is not None:
            rev_5d[sym] = -m5  # 反转: 邻居超跌 → 机会

    # 邻居映射 (仅一次)
    neighbors_map: dict[str, dict[str, float]] = {
        sym: _neighbor_map(graph, sym) for sym in price_data
    }

    # 1. CHAIN_MOM_20D: 邻居 20 日动量加权
    values = {}
    for sym, _data in price_data.items():
        neigh = neighbors_map.get(sym, {})
        # 只保留在 price_data 中的邻居
        valid = {n: w for n, w in neigh.items() if n in mom_20d}
        if len(valid) < min_neighbors:
            continue
        agg = _weighted_avg({n: mom_20d[n] for n in valid}, valid)
        if agg is not None:
            values[sym] = agg
    factors["CHAIN_MOM_20D"] = FactorValue(name="CHAIN_MOM_20D", category="LeadLag", values=values)

    # 2. CHAIN_MOM_60D: 邻居 60 日动量加权
    values = {}
    for sym in price_data:
        neigh = neighbors_map.get(sym, {})
        valid = {n: w for n, w in neigh.items() if n in mom_60d}
        if len(valid) < min_neighbors:
            continue
        agg = _weighted_avg({n: mom_60d[n] for n in valid}, valid)
        if agg is not None:
            values[sym] = agg
    factors["CHAIN_MOM_60D"] = FactorValue(name="CHAIN_MOM_60D", category="LeadLag", values=values)

    # 3. CHAIN_REVERSAL_5D: 邻居短期反转
    values = {}
    for sym in price_data:
        neigh = neighbors_map.get(sym, {})
        valid = {n: w for n, w in neigh.items() if n in rev_5d}
        if len(valid) < min_neighbors:
            continue
        agg = _weighted_avg({n: rev_5d[n] for n in valid}, valid)
        if agg is not None:
            values[sym] = agg
    factors["CHAIN_REVERSAL_5D"] = FactorValue(name="CHAIN_REVERSAL_5D", category="LeadLag", values=values)

    # 4. CHAIN_NEIGHBOR_DIFF: 个股 20 日动量 - 邻居 20 日动量 (脱钩度)
    values = {}
    for sym, _data in price_data.items():
        if sym not in mom_20d:
            continue
        neigh = neighbors_map.get(sym, {})
        valid = {n: w for n, w in neigh.items() if n in mom_20d}
        if len(valid) < min_neighbors:
            continue
        agg = _weighted_avg({n: mom_20d[n] for n in valid}, valid)
        if agg is not None:
            values[sym] = mom_20d[sym] - agg  # 高 = 个股领先于产业链
    factors["CHAIN_NEIGHBOR_DIFF"] = FactorValue(
        name="CHAIN_NEIGHBOR_DIFF", category="LeadLag", values=values
    )

    # 5. CHAIN_CONCENTRATION: 邻居强度赫芬达尔指数 (依赖单一强邻居 = 高)
    values = {}
    for sym in price_data:
        neigh = neighbors_map.get(sym, {})
        if len(neigh) < 1:
            continue
        ws = np.array(list(neigh.values()), dtype=float)
        total = ws.sum()
        if total > 0:
            values[sym] = float(((ws / total) ** 2).sum())
    factors["CHAIN_CONCENTRATION"] = FactorValue(
        name="CHAIN_CONCENTRATION", category="LeadLag", values=values
    )

    # 行业中性化 (可选): 对 CHAIN_MOM_20D 做行业中性化
    if industries and factors.get("CHAIN_MOM_20D"):
        from utils.alpha_factor.base import neutralize_by_industry
        factors["CHAIN_MOM_20D"].values = neutralize_by_industry(
            factors["CHAIN_MOM_20D"].values, industries
        )

    return factors


def orthogonalize_chain_factors(
    chain_factors: dict[str, FactorValue],
    mom_factors: dict[str, FactorValue],
) -> dict[str, FactorValue]:
    """对 Lead-Lag 因子正交化, 消除与已有动量因子的共线.

    关键: 验证「邻居信息」的增量价值, 而非重复动量暴露.
    CHAIN_MOM_20D -> 对 MOM_20D 残差化 (剔除自身动量, 保留邻居净贡献)
    CHAIN_MOM_60D -> 对 MOM_60D 残差化
    CHAIN_REVERSAL_5D -> 对 MOM_REVERSAL_5D 残差化
    CHAIN_NEIGHBOR_DIFF -> 对 MOM_20D 残差化 (脱钩度剔除自身动量后)
    """
    result: dict[str, FactorValue] = {}
    mapping = {
        "CHAIN_MOM_20D": "MOM_20D",
        "CHAIN_MOM_60D": "MOM_60D",
        "CHAIN_REVERSAL_5D": "MOM_REVERSAL_5D",
        "CHAIN_NEIGHBOR_DIFF": "MOM_20D",
    }
    for name, factor in chain_factors.items():
        if not factor.values:
            continue
        anchor_name = mapping.get(name)
        if anchor_name and anchor_name in mom_factors and mom_factors[anchor_name].values:
            # 正交化: 保留因子中独立于对应动量因子的部分
            factor.values = residualize(factor.values, mom_factors[anchor_name].values)
        result[name] = factor
    return result
