"""预期与微观结构因子模块 — SUE/分析师预期/资金面微观结构

对标国泰海通《量化2025年度复盘》核心因子:
- SUE (盈利惊喜): 实际盈利与一致预期偏离
- SUE_REVISION: 分析师预期净利润调整
- CONSENSUS_2Y: 一致预期2年复合增速
- MS_TAIL_VOL: 尾盘成交占比 (微观结构)
- MS_OPEN_BIG: 开盘大单净买入占比
- RD_RATIO: 累计研发投入占比 (成长股核心)

数据依赖: 需 Wind/iFinD 预期数据 + 分时成交数据。
降级策略: 字段缺失时返回空 values, 不影响其他因子计算。
"""

from __future__ import annotations

from utils.alpha_factor.base import FactorValue


def compute_expectation_factors(
    fundamentals: dict[str, dict[str, float]],
    price_data: dict[str, dict[str, list[float]]] | None = None,
) -> dict[str, FactorValue]:
    """预期与微观结构类因子 6 个

    优先从 fundamentals 读取预计算字段:
    - sue / np_revision / consensus_2y_growth / rd_ratio
    - tail_volume_ratio / open_big_buy_ratio (可放 price_data[sym] 或 fundamentals[sym])
    """
    factors: dict[str, FactorValue] = {}

    # --- 预期类 (从 fundamentals) ---
    expectation_spec = [
        ("SUE", "sue", 1),  # 盈利惊喜, 正向 (高 = 超预期 = 高分)
        ("SUE_REVISION", "np_revision", 1),  # 预期净利调整, 正向
        ("CONSENSUS_2Y", "consensus_2y_growth", 1),  # 一致预期2年复合增速
        ("RD_RATIO", "rd_ratio", 1),  # 研发投入占比, 正向 (成长股)
    ]
    for name, fld, sign in expectation_spec:
        values = {}
        for sym, fund in fundamentals.items():
            raw = fund.get(fld, None)
            if raw is not None and raw != 0:
                values[sym] = float(sign * raw)
        factors[name] = FactorValue(name=name, category="Expectation", values=values)

    # --- 微观结构类 (从 price_data 或 fundamentals, 优先 price_data) ---
    micro_fields = [
        ("MS_TAIL_VOL", "tail_volume_ratio", -1),  # 尾盘成交占比, 反向 (高=拥挤=低分)
        ("MS_OPEN_BIG", "open_big_buy_ratio", 1),  # 开盘大单净买入占比, 正向
    ]
    for name, fld, sign in micro_fields:
        values = {}
        # 优先从 price_data[sym] 读取
        if price_data:
            for sym, data in price_data.items():
                raw = data.get(fld, None)
                if raw is not None and raw != 0:
                    values[sym] = float(sign * raw)
        # 其次从 fundamentals[sym] 读取
        for sym, fund in fundamentals.items():
            if sym not in values:
                raw = fund.get(fld, None)
                if raw is not None and raw != 0:
                    values[sym] = float(sign * raw)
        factors[name] = FactorValue(name=name, category="Expectation", values=values)

    return factors
