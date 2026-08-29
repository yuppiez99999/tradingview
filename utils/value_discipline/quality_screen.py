"""去劣硬否决筛选 — 7 指标 + 3 豁免规则

源自 ai-berkshire quality-screen.md。
纯规则计算, 无 LLM 依赖。
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScreenResult:
    triggered: list[str] = field(default_factory=list)  # 触发的指标编号 ["1","3"]
    exemptions: list[str] = field(default_factory=list)  # 命中的豁免 ["A","C"]
    hard_fail: bool = False  # 触发且无对应豁免
    detail: dict[str, str] = field(default_factory=dict)  # 每指标判定说明


def _safe_get(data: dict[str, Any], key: str, default: float = float("nan")) -> float:
    v = data.get(key, default)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def screen_quality(
    fin: dict[str, Any],
    thresholds: dict[str, Any],
    is_bank_insurance: bool = False,
) -> ScreenResult:
    """执行 7 去劣指标 + 3 豁免规则。

    Args:
        fin: 财务数据 {
            "roe_10y_avg": float, "fcf_5y_cumulative": float,
            "interest_coverage": float, "gross_margin_avg": float,
            "ocf_to_netincome_5y": float, "net_margin_avg": float,
            "share_dilution_5y": float,
            "上市年数": int, "roe_recent_2y_positive_ocf": bool,
            "net_margin_recent_2y": float, "is_high_turnover_thin_margin": bool,
        }
        thresholds: config value_discipline.yaml quality_screen.indicators
        is_bank_insurance: 银行/保险不适用利息覆盖

    Returns:
        ScreenResult
    """
    res = ScreenResult()
    t = thresholds

    roe = _safe_get(fin, "roe_10y_avg")
    fcf = _safe_get(fin, "fcf_5y_cumulative")
    intcov = _safe_get(fin, "interest_coverage")
    gm = _safe_get(fin, "gross_margin_avg")
    ocf_ni = _safe_get(fin, "ocf_to_netincome_5y")
    nm = _safe_get(fin, "net_margin_avg")
    dilu = _safe_get(fin, "share_dilution_5y")

    # 1. 10年ROE < 8%
    if roe < t.get("roe_10y_min", 0.08):
        res.triggered.append("1")
        res.detail["1"] = f"10年ROE {roe:.2%} < {t.get('roe_10y_min', 0.08):.0%}"

    # 2. 5年累计FCF < 0
    if fcf < t.get("fcf_5y_min", 0.0):
        res.triggered.append("2")
        res.detail["2"] = f"5年累计FCF {fcf:.2e} < 0"

    # 3. 利息覆盖 < 2 (银行/保险豁免整条)
    if not is_bank_insurance and intcov < t.get("interest_coverage_min", 2.0):
        res.triggered.append("3")
        res.detail["3"] = (
            f"利息覆盖 {intcov:.1f} < {t.get('interest_coverage_min', 2.0)}"
        )

    # 4. 长期毛利率 < 15%
    if gm < t.get("gross_margin_min", 0.15):
        res.triggered.append("4")
        res.detail["4"] = f"长期毛利率 {gm:.2%} < {t.get('gross_margin_min', 0.15):.0%}"

    # 5. 经营CF/净利润 < 0.7
    if ocf_ni < t.get("ocf_to_netincome_min", 0.7):
        res.triggered.append("5")
        res.detail["5"] = (
            f"经营CF/净利润 {ocf_ni:.2f} < {t.get('ocf_to_netincome_min', 0.7)}"
        )

    # 6. 长期净利率 < 5%
    if nm < t.get("net_margin_min", 0.05):
        res.triggered.append("6")
        res.detail["6"] = f"长期净利率 {nm:.2%} < {t.get('net_margin_min', 0.05):.0%}"

    # 7. 5年股本膨胀 > 20%
    if dilu > t.get("share_dilution_5y_max", 0.20):
        res.triggered.append("7")
        res.detail["7"] = (
            f"5年股本膨胀 {dilu:.2%} > {t.get('share_dilution_5y_max', 0.20):.0%}"
        )

    if not res.triggered:
        res.detail["overall"] = "全部 7 指标达标"
        return res

    # 豁免规则
    years = fin.get("上市年数", 99)
    recent_ocf_pos = fin.get("roe_recent_2y_positive_ocf", False)
    nm_recent = _safe_get(fin, "net_margin_recent_2y", 0.0)
    high_turnover = fin.get("is_high_turnover_thin_margin", False)

    # 豁免A: 战略投入期 (适用于指标1)
    if "1" in res.triggered and years < 10 and gm > 0.30 and recent_ocf_pos:
        res.exemptions.append("A")
        res.detail["exemption_A"] = "战略投入期: 上市<10年 & 毛利>30% & 近2年经营CF为正"

    # 豁免B: 主动低利润率 (适用于指标6)
    if "6" in res.triggered and gm > 0.30 and nm_recent >= 0.05:
        res.exemptions.append("B")
        res.detail["exemption_B"] = "主动低利润率: 毛利>30% & 近2年净利回升≥5%"

    # 豁免C: 高周转薄利 (适用于指标4和6)
    if (
        ("4" in res.triggered or "6" in res.triggered)
        and roe > 0.20
        and ocf_ni > 1.0
        and high_turnover
    ):
        res.exemptions.append("C")
        res.detail["exemption_C"] = "高周转薄利: ROE>20% & CF/净利>1 & 高周转薄利模式"

    # hard_fail: 触发的指标没有被任何豁免覆盖
    covered_by_exemption = set()
    if "A" in res.exemptions:
        covered_by_exemption.add("1")
    if "B" in res.exemptions:
        covered_by_exemption.add("6")
    if "C" in res.exemptions:
        covered_by_exemption.update({"4", "6"})

    uncovered = set(res.triggered) - covered_by_exemption
    res.hard_fail = len(uncovered) > 0
    if res.hard_fail:
        res.detail["hard_fail"] = f"未豁免的触发指标: {sorted(uncovered)}"
    else:
        res.detail["hard_fail"] = "全部触发指标已被豁免覆盖"

    return res
