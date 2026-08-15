"""基本面因子模块 — 估值/成长/质量/杠杆/营运 5 大类

对标国泰君安经典三分类 (估值7 + 成长15 + 质量10) + 国泰海通风格时钟 (杠杆) + 营运效率。

命名规范 (国泰海通研报式):
- 估值: EP/BP/SP/CFP/DP/FCFP/EV_EBITDA/SP_EV/PEG/PB_INT/PE_EX (高=便宜=高分)
- 成长: REV_YOY/REV_Q/REV_TTM, NP_*, OCF_*, ROE_*, ROA_* (高=高增长=高分)
- 质量: ROE/ROA/ROIC/GROSS_MARGIN/NET_MARGIN/EBITDA_MARGIN/CASH_NP/ACCRUALS (高=优质=高分)
- 杠杆: EQ_MULT/INT_DEBT/QUICK/INT_COV/DE_RATIO/LT_DEBT (低杠杆=高分, 取负或倒数)
- 营运: ASSET_TURN/INV_TURN/AR_TURN/AP_TURN/CASH_CYCLE (高周转=高分)

参考:
- 国泰君安《多因子选股模型之因子分析与筛选》: 估值7+成长15+质量10
- 国泰海通《宏观周期下的风格时钟》: 成长/盈利/杠杆三大风格
"""

from __future__ import annotations

from utils.alpha_factor.base import FactorValue, neutralize_by_industry

# ============================================================
# 1. 估值因子 (Value) — 11 个
# ============================================================


def compute_value_factors(
    fundamentals: dict[str, dict[str, float]],
    industries: dict[str, str] | None = None,
) -> dict[str, FactorValue]:
    """估值类因子 11 个 (高 = 便宜 = 高分, 国泰君安研报正向惯例)

    EP/BP/SP/CFP: 倒数类 (1/PE 等)
    DP/FCFP: 直接收益率
    EV_EBITDA/SP_EV: 企业价值倍数倒数
    PEG: PE/增长率 (反向, 低PEG=高分)
    PB_INT: 行业调整 BP (BP - 同行业均值)
    PE_EX: 扣非PE倒数
    """
    factors: dict[str, FactorValue] = {}

    # 倒数类 (1/x, 正向) + 直接类
    value_spec = [
        ("EP", "pe", "inverse"),
        ("BP", "pb", "inverse"),
        ("SP", "ps", "inverse"),
        ("CFP", "pcf", "inverse"),
        ("DP", "dividend_yield", "direct"),
        ("FCFP", "fcf_yield", "direct"),
        ("EV_EBITDA", "ev_ebitda", "inverse"),
        ("SP_EV", "sales_ev", "inverse"),
        ("PE_EX", "pe_excluding", "inverse"),
    ]
    for name, fld, mode in value_spec:
        values = {}
        for sym, fund in fundamentals.items():
            raw = fund.get(fld, 0)
            if raw and raw > 0:
                values[sym] = float(1.0 / raw) if mode == "inverse" else float(raw)
        factors[name] = FactorValue(name=name, category="Value", values=values)

    # PEG = PE / 增长率 (反向: 低 PEG = 高分)
    values = {}
    for sym, fund in fundamentals.items():
        peg = fund.get("peg", 0)
        if peg and peg > 0:
            values[sym] = -float(peg)
        elif fund.get("pe", 0) > 0 and fund.get("net_profit_yoy", 0) > 0:
            values[sym] = -float(fund["pe"] / fund["net_profit_yoy"])
    factors["PEG"] = FactorValue(name="PEG", category="Value", values=values)

    # PB_INT (行业调整 BP = BP - 同行业均值)
    bp_values = {
        sym: float(1.0 / fund["pb"]) for sym, fund in fundamentals.items()
        if fund.get("pb", 0) and fund.get("pb", 0) > 0
    }
    if industries:
        bp_values = neutralize_by_industry(bp_values, industries)
    factors["PB_INT"] = FactorValue(name="PB_INT", category="Value", values=bp_values)

    return factors


# ============================================================
# 2. 成长因子 (Growth) — 15 个  [国泰君安核心分类]
# ============================================================


def _growth_from_fields(
    fundamentals: dict[str, dict[str, float]],
    field_yoy: str,
    field_qoq: str,
    factor_yoy: str,
    factor_qoq: str,
    factor_ttm: str,
) -> dict[str, FactorValue]:
    """从 fundamentals 读取预计算的增长率字段 (YOY/QoQ/TTM)

    国泰君安成长类标准: 单季环比 / 同比 / TTM同比, 各 5 个基础量 (营收/净利/现金流/ROE/ROA)
    """
    out = {}
    for fld, fname in [(field_yoy, factor_yoy), (field_qoq, factor_qoq), (field_yoy, factor_ttm)]:
        # TTM 简化为 YOY (若无独立 TTM 字段)
        values = {}
        for sym, fund in fundamentals.items():
            raw = fund.get(fld, 0)
            if raw is not None and raw != 0:
                values[sym] = float(raw)
        out[fname] = FactorValue(name=fname, category="Growth", values=values)
    return out


def compute_growth_factors(
    fundamentals: dict[str, dict[str, float]],
    fundamentals_prev: dict[str, dict[str, float]] | None = None,
) -> dict[str, FactorValue]:
    """成长类因子 15 个 (高 = 高增长 = 高分)

    优先从 fundamentals 读取预计算字段 (revenue_yoy/revenue_qoq 等);
    若提供 fundamentals_prev 且未预计算, 则用 (当期 - 上期) / |上期| 计算同比。

    国泰君安成长类 3×5 矩阵:
        单季环比 (Q) / 同比 (YOY) / TTM同比 (TTM)
        × 营收 (REV) / 净利润 (NP) / 经营现金流 (OCF) / ROE / ROA
    """
    factors: dict[str, FactorValue] = {}

    growth_bases = [
        ("revenue", "REV"),
        ("net_profit", "NP"),
        ("operating_cash_flow", "OCF"),
        ("roe", "ROE"),
        ("roa", "ROA"),
    ]

    for base_field, prefix in growth_bases:
        for suffix, kind in [("_qoq", "Q"), ("_yoy", "YOY"), ("_ttm", "TTM")]:
            factor_name = f"{prefix}_{kind}"
            fld = base_field + suffix
            values = {}
            # 优先读预计算字段
            for sym, fund in fundamentals.items():
                raw = fund.get(fld, None)
                if raw is not None and raw != 0:
                    values[sym] = float(raw)
            # 若无预计算且有上期数据, 计算 YOY/TTM 同比
            if not values and fundamentals_prev and kind in ("YOY", "TTM"):
                for sym, fund in fundamentals.items():
                    prev = fundamentals_prev.get(sym, {})
                    curr_v = fund.get(base_field, 0)
                    prev_v = prev.get(base_field, 0)
                    if curr_v and prev_v and abs(prev_v) > 1e-10:
                        values[sym] = float((curr_v - prev_v) / abs(prev_v))
            factors[factor_name] = FactorValue(name=factor_name, category="Growth", values=values)

    return factors


# ============================================================
# 3. 盈利质量因子 (Quality) — 12 个
# ============================================================


def compute_quality_factors(
    fundamentals: dict[str, dict[str, float]],
) -> dict[str, FactorValue]:
    """盈利质量类因子 12 个 (高 = 优质 = 高分)

    国泰君安质量类 10 + 补充 2:
    ROE/ROA/ROIC/GROSS_MARGIN/NET_MARGIN/EBITDA_MARGIN/CASH_NP/ACCRUALS(反向)/DEBT_TO_EQUITY(反向)/CURRENT_RATIO
    + GROSS_STAB (毛利率稳定性) + ACCRUAL_CHG (应计变动, 反向)
    """
    factors: dict[str, FactorValue] = {}

    quality_spec = [
        ("ROE", "roe", 1),
        ("ROA", "roa", 1),
        ("ROIC", "roic", 1),
        ("GROSS_MARGIN", "gross_margin", 1),
        ("NET_MARGIN", "net_margin", 1),
        ("EBITDA_MARGIN", "ebitda_margin", 1),
        ("CASH_NP", "cash_to_np", 1),  # 经营现金流/净利润, 高=盈利质量好
        ("ACCRUALS", "accruals", -1),  # 应计利润, 反向 (低=高质量)
        ("DEBT_TO_EQUITY", "debt_to_equity", -1),  # 反向
        ("CURRENT_RATIO", "current_ratio", 1),
        ("ACCRUAL_CHG", "accrual_chg", -1),  # 应计变动, 反向
    ]
    for name, fld, sign in quality_spec:
        values = {}
        for sym, fund in fundamentals.items():
            raw = fund.get(fld, 0)
            if raw:
                values[sym] = float(sign * raw)
        factors[name] = FactorValue(name=name, category="Quality", values=values)

    # QUICK_RATIO (Quality 版本) = 速动溢酬 = quick_ratio - current_ratio
    # 与 Leverage.QUICK 解耦: 度量 "超越一般流动性的快速变现能力"
    # 高值 = 速动显著高于流动 (存货占比低, 流动性结构优); 低值 = 速动接近流动 (存货占比高)
    values = {}
    for sym, fund in fundamentals.items():
        quick = fund.get("quick_ratio", 0)
        current = fund.get("current_ratio", 0)
        if quick and current:
            values[sym] = float(quick - current)
    factors["QUICK_RATIO"] = FactorValue(name="QUICK_RATIO", category="Quality", values=values)

    # GROSS_MARGIN_STAB (毛利率稳定性: 用 1/std, 需历史序列, 简化用 gross_margin_stab 字段)
    values = {}
    for sym, fund in fundamentals.items():
        stab = fund.get("gross_margin_stab", 0)
        if stab and stab > 0:
            values[sym] = float(stab)  # 高 = 稳定 = 高分
    factors["GROSS_MARGIN_STAB"] = FactorValue(name="GROSS_MARGIN_STAB", category="Quality", values=values)

    return factors


# ============================================================
# 4. 杠杆偿债因子 (Leverage) — 6 个  [国泰海通风格时钟核心]
# ============================================================


def compute_leverage_factors(
    fundamentals: dict[str, dict[str, float]],
) -> dict[str, FactorValue]:
    """杠杆偿债类因子 6 个 (低杠杆 = 高分, 国泰海通风格时钟核心)

    国泰海通《宏观周期下的风格时钟》: 杠杆风格在过热期强势。
    反向: 低杠杆 = 高分 (规避高杠杆风险)。
    """
    factors: dict[str, FactorValue] = {}

    leverage_spec = [
        ("EQ_MULT", "eq_multiplier", -1),  # 权益乘数 = 总资产/净资产, 反向
        ("INT_DEBT", "int_debt_ratio", -1),  # 有息负债率, 反向
        ("DE_RATIO", "debt_ratio", -1),  # 资产负债率, 反向
        ("LT_DEBT", "lt_debt_ratio", -1),  # 长期负债比, 反向
        ("QUICK", "quick_ratio", 1),  # 速动比率, 正向
        ("INT_COV", "int_coverage", 1),  # 利息保障倍数, 正向
    ]
    for name, fld, sign in leverage_spec:
        values = {}
        for sym, fund in fundamentals.items():
            raw = fund.get(fld, 0)
            if raw:
                values[sym] = float(sign * raw)
        factors[name] = FactorValue(name=name, category="Leverage", values=values)

    return factors


# ============================================================
# 5. 营运效率因子 (Operation) — 5 个
# ============================================================


def compute_operation_factors(
    fundamentals: dict[str, dict[str, float]],
) -> dict[str, FactorValue]:
    """营运效率类因子 5 个 (高周转 = 高分)

    资产/存货/应收/应付周转率 + 现金循环周期 (反向)
    """
    factors: dict[str, FactorValue] = {}

    operation_spec = [
        ("ASSET_TURN", "asset_turnover", 1),  # 资产周转率
        ("INV_TURN", "inventory_turnover", 1),  # 存货周转率
        ("AR_TURN", "ar_turnover", 1),  # 应收账款周转率
        ("AP_TURN", "ap_turnover", 1),  # 应付账款周转率
        ("CASH_CYCLE", "cash_cycle", -1),  # 现金循环周期, 反向 (短=高效=高分)
    ]
    for name, fld, sign in operation_spec:
        values = {}
        for sym, fund in fundamentals.items():
            raw = fund.get(fld, 0)
            if raw:
                values[sym] = float(sign * raw)
        factors[name] = FactorValue(name=name, category="Operation", values=values)

    return factors
