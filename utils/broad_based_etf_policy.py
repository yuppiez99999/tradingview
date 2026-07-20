# -*- coding: utf-8 -*-
"""
宽基ETF 政策合规与社保国家队流向加减仓模块

职责:
1. 标的合规校验: 校验组合内每个标的是否符合「十五五规划 + 康波周期」对齐要求。
2. 宽基ETF纳入: 定义宽基ETF候选 (沪深300 / 中证500 / 上证50 / 中证1000) 及其十五五/康波标签。
3. 宽基ETF加减仓: 根据社保国家队ETF资金净流入(亿元) 信号, 对宽基ETF执行加仓/减仓调整。

设计原则:
- 与现有 macro_policy_scoring (十五五 + 康波) 与 etf_flow_monitor (国家队资金流) 解耦复用,
  通过防御式导入避免强依赖。
- 所有加减仓均基于「基准权重 base_weight」做相对调整, 可幂等重复执行。
"""

from __future__ import annotations

import copy
from typing import Dict, List, Optional

from utils.logger import get_logger

logger = get_logger("broad_based_etf_policy")


# =====================
# 宽基ETF 候选定义
# =====================
# 宽基ETF 同时命中国家十五五「防御型公用事业/核心资产」映射与康波「宽基 1.00」中性权重,
# 是承接社保国家队流入最纯粹的载体。
BROAD_BASED_ETFS: List[Dict] = [
    {
        "code": "510300", "name": "沪深300ETF华泰柏瑞", "style": "宽基",
        "base_weight": 0.05, "est_price": 4.10, "lots": 100,
        "ff_dir": "defensive_utility", "kc_style": "宽基",
        "reason": "A股核心资产宽基, 十五五防御型公用事业映射, 康波宽基中性; 社保国家队护盘首选载体",
    },
    {
        "code": "510500", "name": "中证500ETF南方", "style": "宽基",
        "base_weight": 0.04, "est_price": 6.20, "lots": 100,
        "ff_dir": "defensive_utility", "kc_style": "宽基",
        "reason": "中盘成长宽基, 十五五中小市值战略新兴承载, 康波宽基中性; 承接结构性流入",
    },
    {
        "code": "510050", "name": "上证50ETF华夏", "style": "宽基",
        "base_weight": 0.04, "est_price": 2.95, "lots": 100,
        "ff_dir": "defensive_utility", "kc_style": "宽基",
        "reason": "大盘蓝筹宽基, 十五五核心资产映射, 康波宽基中性; 社保国家队底仓压舱石",
    },
    {
        "code": "512100", "name": "中证1000ETF南方", "style": "宽基",
        "base_weight": 0.03, "est_price": 2.65, "lots": 100,
        "ff_dir": "defensive_utility", "kc_style": "宽基",
        "reason": "小盘风格宽基, 十五五专精特新映射, 康波宽基中性; 弹性承接国家队边际流入",
    },
]

# 加减仓幅度系数 (相对基准权重的比例), 与 etf_flow_monitor.SIGNAL_THRESHOLDS 一致
ADJUST_BANDS = [
    # (方向, 净流下限亿元, 净流上限亿元, 信号, 动作, 系数)
    ("in", 50, float("inf"), "国家队强加仓信号", "强加仓", 0.30),
    ("in", 10, 50, "国家队加仓信号", "加仓", 0.15),
    ("in", 2, 10, "国家队关注信号", "小幅加仓", 0.05),
    ("hold", -2, 2, "中性", "持有", 0.0),
    ("out", -10, -2, "国家队减持关注", "小幅减仓", -0.05),
    ("out", -50, -10, "国家队减仓信号", "减仓", -0.15),
    ("out", float("-inf"), -50, "国家队强减仓信号", "强减仓", -0.30),
]

# 宽基ETF 权重上下限 (相对基准的缩放范围), 防止极端漂移
MIN_SCALE = 0.5
MAX_SCALE = 1.5


# =====================
# 宏观评分 (十五五 + 康波) 防御式导入
# =====================
def _import_macro():
    """优先导入 ms_strategy 版本, 失败回退 v7.5_institutional 版本。"""
    candidates = [
        "ms_strategy.src.macro.macro_policy_scoring",
        "v7.5_institutional.src.macro.macro_policy_scoring",
    ]
    for modname in candidates:
        try:
            import importlib
            mod = importlib.import_module(modname)
            return mod
        except Exception as e:  # noqa: BLE001
            logger.debug(f"导入 {modname} 失败: {e}")
    return None


_MACRO = _import_macro()


def normalize_code(code: str) -> List[str]:
    """生成用于查表的候选键: 裸代码 + sz/sh 前缀。"""
    s = str(code).strip().lstrip("0").zfill(6)
    return [s, f"sz{s}", f"sh{s}", str(code).strip()]


# =====================
# 1. 标的合规校验 (十五五 + 康波)
# =====================
def validate_portfolio_compliance(target_portfolio: Dict) -> Dict:
    """
    校验组合内每个标的是否符合「十五五规划 + 康波周期」对齐要求。

    Returns:
        {
            'holdings': [ {code, name, style, ff_score, kc_score, combined,
                           passed, level, note, action} ],
            'summary': {total, passed, weak, weak_codes, avg_combined}
        }
    """
    if _MACRO is None:
        logger.warning("macro_policy_scoring 不可用, 合规校验退化为仅风格标签检查")
    codes = list(target_portfolio.keys())
    norm_map = {c: normalize_code(c) for c in codes}

    # 收集所有候选键, 一次性评分
    all_keys = []
    for keys in norm_map.values():
        all_keys.extend(keys)
    if _MACRO is not None:
        ff = _MACRO.score_fifteen_five(all_keys)
        kp = _MACRO.score_kondratiev(all_keys)
    else:
        ff, kp = {}, {}

    # 构建 键->分数 映射
    ff_score_of = {}
    for item in ff.values():
        for sym in item.symbols:
            ff_score_of[sym] = item.score
    kc_of = {sym: res for sym, res in kp.items()}

    holdings = []
    weak_codes = []
    combined_sum = 0.0
    passed_count = 0

    for code, info in target_portfolio.items():
        name = info.get("name", code)
        style = info.get("style", "")
        keys = norm_map[code]

        ff_score = max([ff_score_of.get(k, 1.0) for k in keys], default=1.0)
        # 康波: 优先用风格映射, 否则用标的 style 映射
        kc_res = None
        for k in keys:
            if k in kc_of:
                kc_res = kc_of[k]
                break
        if kc_res is not None:
            kc_score = kc_res.cycle_score
            kc_note = kc_res.note
        else:
            kc_weight = _MACRO.KONDRATIEV_STYLE_WEIGHTS.get(style, 1.0) if _MACRO else 1.0
            kc_score = float(kc_weight)
            kc_note = "康波中性/防御" if kc_score >= 0.95 else "康波偏弱"

        combined = round((ff_score + kc_score) / 2, 4)
        combined_sum += combined

        # 判定: combined >= 1.0 强对齐; 0.9~1.0 中性; < 0.9 偏弱建议减配
        if combined >= 1.0:
            level, passed, action = "强对齐", True, "维持/可加配"
        elif combined >= 0.9:
            level, passed, action = "中性", True, "维持"
        else:
            level, passed, action = "偏弱", False, "建议减配"
            weak_codes.append(code)

        if passed:
            passed_count += 1

        holdings.append({
            "code": code, "name": name, "style": style,
            "ff_score": round(ff_score, 4), "kc_score": round(kc_score, 4),
            "combined": combined, "passed": passed, "level": level,
            "note": f"十五五命中分 {ff_score:.2f}; 康波 {kc_note}", "action": action,
        })

    summary = {
        "total": len(holdings),
        "passed": passed_count,
        "weak": len(weak_codes),
        "weak_codes": weak_codes,
        "avg_combined": round(combined_sum / max(len(holdings), 1), 4),
    }
    return {"holdings": holdings, "summary": summary}


# =====================
# 2/3. 宽基ETF 加减仓 (社保国家队流入驱动)
# =====================
def flow_to_adjustment(net_flow_yi: float) -> Dict:
    """将单只ETF净流(亿元) 映射为加减仓信号/动作/系数。"""
    for direction, lo, hi, signal, action, factor in ADJUST_BANDS:
        if lo <= net_flow_yi < hi:
            return {"signal": signal, "action": action, "factor": factor,
                    "direction": direction}
    # 兜底
    return {"signal": "中性", "action": "持有", "factor": 0.0, "direction": "hold"}


def get_broad_based_codes(plan: Dict) -> List[str]:
    """从计划中提取被标记为宽基/可调节的标的代码。"""
    codes = []
    tp = plan.get("target_portfolio", {})
    for code, info in tp.items():
        if info.get("style") == "宽基" or info.get("adjustable") is True:
            codes.append(code)
    return codes


def compute_broad_based_adjustments(flow_signals: Dict,
                                    broad_based: Optional[List[Dict]] = None) -> List[Dict]:
    """
    根据社保国家队ETF资金流信号, 计算宽基ETF加减仓方案。

    Args:
        flow_signals: {code: {net_flow_yi: float, ...}} (来自 etf_flow_monitor.get_all_etf_fund_flows)
        broad_based: 宽基ETF定义列表, 默认 BROAD_BASED_ETFS

    Returns:
        [ {code, name, base_weight, net_flow_yi, signal, action, factor,
           target_weight, scale} ]
    """
    broad_based = broad_based or BROAD_BASED_ETFS
    out = []
    for etf in broad_based:
        code = etf["code"]
        base = etf["base_weight"]
        sig = flow_signals.get(code, {})
        net_flow = float(sig.get("net_flow_yi", 0.0) or 0.0)
        adj = flow_to_adjustment(net_flow)
        scale = max(MIN_SCALE, min(MAX_SCALE, 1.0 + adj["factor"]))
        target_weight = round(base * scale, 6)
        out.append({
            "code": code, "name": etf["name"], "base_weight": base,
            "net_flow_yi": net_flow, "signal": adj["signal"],
            "action": adj["action"], "factor": adj["factor"],
            "target_weight": target_weight, "scale": round(scale, 4),
        })
    return out


def apply_broad_based_adjustments_to_plan(plan: Dict,
                                          flow_signals: Dict,
                                          broad_based: Optional[List[Dict]] = None) -> Dict:
    """
    将宽基ETF加减仓方案就地应用到计划 dict (target_portfolio / position_plan / phase_summary)。
    基于 base_weight / base_shares / base_amount 做幂等相对调整。

    Returns:
        {applied: bool, adjustments: [...], summary: str}
    """
    broad_based = broad_based or BROAD_BASED_ETFS
    adjustments = compute_broad_based_adjustments(flow_signals, broad_based)
    tp = plan.get("target_portfolio", {})
    pp = plan.get("position_plan", {})
    phase_summary = plan.get("phase_summary", [])

    applied = False
    for adj in adjustments:
        code = adj["code"]
        if code not in tp:
            continue
        applied = True
        base = adj["base_weight"]
        scale = adj["scale"]
        target_weight = adj["target_weight"]

        # target_portfolio
        info = tp[code]
        info["weight"] = target_weight
        info["target_amount"] = round(3_000_000 * target_weight, 2) if "target_amount" in info else info.get("target_amount")
        if info.get("est_price", 0) > 0:
            raw = info["target_amount"] / info["est_price"]
            lots = info.get("lots", 100)
            info["total_shares"] = int(raw // lots) * lots
            info["actual_amount"] = round(info["total_shares"] * info["est_price"], 2)
        info["last_flow_signal"] = adj["signal"]
        info["last_flow_net_yi"] = adj["net_flow_yi"]
        info["last_adjust_action"] = adj["action"]

        # position_plan
        if code in pp:
            pos = pp[code]
            pos_base = pos.get("base_weight", base)
            pos["target_weight"] = target_weight
            pos["target_amount"] = round(3_000_000 * target_weight, 2)
            for ph in pos.get("phases", []):
                ph_base_amount = ph.get("base_amount")
                if ph_base_amount is None:
                    ph_base_amount = ph["target_amount"] / (pos_base if pos_base else 1.0)
                    ph["base_amount"] = ph_base_amount
                ph["target_amount"] = round(ph_base_amount * scale, 2)

        # phase_summary assets (每日真实下单股数来源)
        for phase in phase_summary:
            for asset in phase.get("assets", []):
                if asset.get("code") == code:
                    b_shares = asset.get("base_shares")
                    if b_shares is None:
                        b_shares = int(asset.get("shares", 0))
                        asset["base_shares"] = b_shares
                    b_amount = asset.get("base_amount")
                    if b_amount is None:
                        b_amount = float(asset.get("amount", 0.0))
                        asset["base_amount"] = b_amount
                    asset["shares"] = int(round(b_shares * scale))
                    asset["amount"] = round(b_amount * scale, 2)

    return {
        "applied": applied,
        "adjustments": adjustments,
        "summary": "; ".join(
            f"{a['name']} {a['action']}(净流{a['net_flow_yi']:+.1f}亿→权重{a['target_weight']:.2%})"
            for a in adjustments
        ),
    }


def fetch_national_team_flow_signals() -> Dict:
    """
    获取社保国家队ETF资金流信号 (优先 Wind MCP, 回退 iFinD, 再回退新浪)。
    失败返回空 dict, 调用方据此跳过加减仓。
    """
    try:
        from utils.etf_flow_monitor import ETFRealTimeTracker
        tracker = ETFRealTimeTracker()
        return tracker.get_all_etf_fund_flows()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"获取社保国家队资金流失败, 宽基ETF维持基准权重: {e}")
        return {}


def adjust_plan_with_national_team_flow(plan: Dict) -> Dict:
    """
    便捷入口: 拉取国家队资金流并对宽基ETF就地加减仓。
    用于每日建仓指令生成前调用 (幂等, 异常安全)。
    """
    try:
        flow = fetch_national_team_flow_signals()
        if not flow:
            return {"applied": False, "reason": "无资金流数据", "adjustments": []}
        return apply_broad_based_adjustments_to_plan(plan, flow)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"宽基ETF国家队加减仓执行异常, 维持基准: {e}")
        return {"applied": False, "reason": str(e), "adjustments": []}
