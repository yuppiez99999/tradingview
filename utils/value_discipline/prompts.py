"""四大师 Prompt 模板 — 源自 ai-berkshire investment-team.md / dyp-ask.md

A股语境本地化: 巴菲特/芒格补充政策护城河/国企牌照/十五五规划对齐。
"""

from typing import Any

from utils.value_discipline.info_grade import grade_strategy_adjustment

_MASTER_BASE = """你是{master_name}, 从{perspective}视角评估 {stock_name}。
{belief}
{a_share_extra}

财务数据: {financial_json}
当前信号: action={action}, confidence={confidence:.2f}, reason={reason}
信息评级: {info_grade} — {grade_hint}

仅输出 JSON: {{"score": 1到5的整数, "reason": "不超过80字, 必须点明核心判断"}}
"""


def build_buffett_prompt(stock_name: str, fin: dict[str, Any], signal: Any, grade: str) -> str:
    return _MASTER_BASE.format(
        master_name="巴菲特",
        perspective="财务与估值",
        belief=(
            "关注: ROE(>15%优秀/>20%卓越) / 毛利率(>40%暗示定价权) / "
            "自由现金流(持续为正且≈净利润) / 安全边际(内在价值 vs 股价)。"
        ),
        a_share_extra=(
            "A股补充: 政策护城河(牌照/准入) / 国企牌照价值 / 十五五规划对齐度。"
        ),
        stock_name=stock_name,
        financial_json=_fin_json(fin),
        action=getattr(signal, "action", ""),
        confidence=getattr(signal, "confidence", 0.0),
        reason=getattr(signal, "reason", ""),
        info_grade=grade,
        grade_hint=grade_strategy_adjustment(grade),
    )


def build_munger_prompt(stock_name: str, fin: dict[str, Any], signal: Any, grade: str) -> str:
    return _MASTER_BASE.format(
        master_name="芒格",
        perspective="行业与竞争",
        belief=(
            "关注: 市场规模/增速 / 竞争格局 / 护城河可持续性 / "
            "产业链价值分配 / 10年确定性。多元思维: 不只看财务, 看行业演进/技术变革/政策/人性。"
        ),
        a_share_extra="A股补充: 国产替代/政策驱动行业/集中度提升趋势。",
        stock_name=stock_name,
        financial_json=_fin_json(fin),
        action=getattr(signal, "action", ""),
        confidence=getattr(signal, "confidence", 0.0),
        reason=getattr(signal, "reason", ""),
        info_grade=grade,
        grade_hint=grade_strategy_adjustment(grade),
    )


def build_dyp_prompt(stock_name: str, fin: dict[str, Any], signal: Any, grade: str) -> str:
    return _MASTER_BASE.format(
        master_name="段永平",
        perspective="商业模式",
        belief=(
            "信仰: 买股票=买公司未来现金流折现。"
            "好生意四特征: 差异化 / 护城河(品牌/转换成本/网络效应/规模) / 定价权 / 轻资产。"
            "Stop doing: 不懂不投/不做空/不借钱/不频繁交易/不看宏观/不预测股价。"
        ),
        a_share_extra="A股补充: 消费品牌/渠道壁垒/用户心智份额。",
        stock_name=stock_name,
        financial_json=_fin_json(fin),
        action=getattr(signal, "action", ""),
        confidence=getattr(signal, "confidence", 0.0),
        reason=getattr(signal, "reason", ""),
        info_grade=grade,
        grade_hint=grade_strategy_adjustment(grade),
    )


def build_lixu_prompt(stock_name: str, fin: dict[str, Any], signal: Any, grade: str) -> str:
    return _MASTER_BASE.format(
        master_name="李录",
        perspective="风险与管理层",
        belief=(
            "关注: 管理层诚信/资本配置能力 / 监管风险 / 治理结构 / "
            "关联交易 / 10年后会被颠覆吗。"
        ),
        a_share_extra="A股补充: 国企治理 / 政策风险 / 股东回报文化 / 大股东减持史。",
        stock_name=stock_name,
        financial_json=_fin_json(fin),
        action=getattr(signal, "action", ""),
        confidence=getattr(signal, "confidence", 0.0),
        reason=getattr(signal, "reason", ""),
        info_grade=grade,
        grade_hint=grade_strategy_adjustment(grade),
    )


MASTER_BUILDERS = {
    "buffett": build_buffett_prompt,
    "munger": build_munger_prompt,
    "dyp": build_dyp_prompt,
    "lixu": build_lixu_prompt,
}


def _fin_json(fin: dict[str, Any]) -> str:
    import json
    safe = {k: v for k, v in fin.items() if isinstance(v, (int, float, str, bool, type(None)))}
    return json.dumps(safe, ensure_ascii=False, default=str)[:500]
