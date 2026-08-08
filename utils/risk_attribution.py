"""
风险归因面板 (Risk Attribution Panel)
====================================

实现 README v8.1 中的「风险归因面板」:
  - 行业 / 风格 / 资产类型风险分解
  - 集中度指标 (HHI, Top-N 占比)
  - 个股特异风险 (最大单标的占比)
  - 对冲工具剩余风险 (期货/期权对冲后的净敞口)

数据来源:
  - config/positions.json (持仓 + hedge_positions)
  - 可选: v7.5_institutional/reports/daily_pnl_report_*.json (Beta/相关性)
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import logging

logger = logging.getLogger(__name__)

# ============================================================
# 默认路径
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POSITIONS_PATH = PROJECT_ROOT / "config" / "positions.json"


# ============================================================
# 数据结构
# ============================================================
@dataclass
class RiskAttribution:
    """风险归因结果"""

    total_value: float = 0.0
    by_sector: dict[str, float] = field(default_factory=dict)
    by_style: dict[str, float] = field(default_factory=dict)
    by_type: dict[str, float] = field(default_factory=dict)
    concentration: dict[str, float] = field(default_factory=dict)  # HHI / Top1 / Top5
    hedge_residual: dict[str, Any] = field(default_factory=dict)  # 对冲工具剩余风险
    warnings: list[str] = field(default_factory=list)


# ============================================================
# 数据加载
# ============================================================
def load_positions(path: str | Path = DEFAULT_POSITIONS_PATH) -> list[dict[str, Any]]:
    """加载持仓列表 (兼容旧版接口)"""
    path = Path(path)
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:  # P2 模块 fail-safe, 待后续精确化  # noqa: BLE001
        return []

    positions = []
    for item in data.get("positions", {}).values():
        positions.append(
            {
                "code": item.get("code", ""),
                "name": item.get("name", ""),
                "amount": float(item.get("amount", 0.0) or 0.0),
                "shares": float(item.get("shares", 0) or 0.0),
                "est_price": float(item.get("est_price", 0.0) or 0.0),
                "avg_cost": float(item.get("avg_cost", 0.0) or 0.0),
                "style": item.get("style", "其他"),
                "sector": item.get("sector", "其他"),
                "type": item.get("type", "STOCK"),
                "beta": float(item.get("beta", 1.0) or 1.0),
            }
        )
    return positions


def load_hedge_positions(path: str | Path = DEFAULT_POSITIONS_PATH) -> dict[str, Any]:
    """加载对冲工具配置"""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("hedge_positions", {})  # type: ignore[index]
    except Exception:  # P2 模块 fail-safe, 待后续精确化  # noqa: BLE001
        return {}


# ============================================================
# 集中度指标
# ============================================================
def _calc_concentration(weights: list[float]) -> dict[str, float]:
    """计算集中度指标

    Returns:
        {
            "hhi": Herfindahl-Hirschman Index (0~1, 越高越集中),
            "top1": 最大单标的占比,
            "top3": 前三大占比,
            "top5": 前五大占比,
            "effective_n": 1/HHI (有效标的数)
        }
    """
    if not weights:
        return {"hhi": 0.0, "top1": 0.0, "top3": 0.0, "top5": 0.0, "effective_n": 0.0}

    sorted_w = sorted(weights, reverse=True)
    total = sum(weights)
    if total <= 0:
        return {"hhi": 0.0, "top1": 0.0, "top3": 0.0, "top5": 0.0, "effective_n": 0.0}

    norm = [w / total for w in sorted_w]
    hhi = sum(w * w for w in norm)
    top1 = norm[0] if len(norm) >= 1 else 0.0
    top3 = sum(norm[:3]) if len(norm) >= 3 else sum(norm)
    top5 = sum(norm[:5]) if len(norm) >= 5 else sum(norm)
    effective_n = 1.0 / hhi if hhi > 1e-6 else float(len(norm))
    return {
        "hhi": round(hhi, 4),
        "top1": round(top1, 4),
        "top3": round(top3, 4),
        "top5": round(top5, 4),
        "effective_n": round(effective_n, 2),
    }


def _aggregate(values: list[tuple[str, float]]) -> dict[str, float]:
    """按 key 聚合并返回 {key: sum_value}"""
    agg: dict[str, float] = defaultdict(float)
    for k, v in values:
        agg[k or "其他"] += float(v or 0.0)
    return dict(agg)


def _to_pct_map(value_map: dict[str, float], total: float) -> dict[str, float]:
    """将金额 map 转为百分比 map"""
    if total <= 0:
        return {k: 0.0 for k in value_map}
    return {k: round(v / total, 4) for k, v in value_map.items()}


# ============================================================
# 对冲工具剩余风险
# ============================================================
def _calc_hedge_residual(positions: list[dict[str, Any]], hedge_positions: dict[str, Any]) -> dict[str, Any]:
    """计算对冲后的剩余风险

    Returns:
        {
            "portfolio_beta": 组合加权 Beta,
            "futures_contracts": 期货手数,
            "futures_beta_reduction": 期货 Beta 对冲量,
            "options_contracts": 期权合约数,
            "options_premium_budget": 期权权利金预算,
            "residual_beta": 期货对冲后剩余 Beta,
            "tail_risk_coverage_pct": 尾部风险覆盖率 (期权名义价值/组合市值)
        }
    """
    total_value = sum(p.get("amount", 0.0) for p in positions)
    portfolio_beta = 0.0
    if total_value > 0:
        for p in positions:
            weight = p.get("amount", 0.0) / total_value
            portfolio_beta += weight * float(p.get("beta", 1.0))

    futures_contracts = 0
    futures_beta_reduction = 0.0
    options_contracts = 0
    options_premium_budget = 0.0
    options_notional = 0.0

    for _, hp in hedge_positions.items():
        if not isinstance(hp, dict):
            continue
        instrument = (hp.get("instrument") or "").upper()
        is_option = bool(hp.get("is_option")) or "PUT" in instrument or "CALL" in instrument
        contracts = int(hp.get("target_contracts", 0) or 0)

        if is_option:
            options_contracts += contracts
            options_premium_budget += float(hp.get("premium_budget", 0.0) or 0.0)
            options_notional += float(hp.get("estimated_notional", 0.0) or 0.0)
        else:
            futures_contracts += contracts
            futures_beta_reduction += float(hp.get("target_beta_reduction", 0.0) or 0.0)

    residual_beta = max(portfolio_beta - futures_beta_reduction, 0.0)
    tail_coverage = options_notional / total_value if total_value > 0 else 0.0

    return {
        "portfolio_beta": round(portfolio_beta, 4),
        "futures_contracts": futures_contracts,
        "futures_beta_reduction": round(futures_beta_reduction, 4),
        "options_contracts": options_contracts,
        "options_premium_budget": round(options_premium_budget, 2),
        "options_notional": round(options_notional, 2),
        "residual_beta": round(residual_beta, 4),
        "tail_risk_coverage_pct": round(tail_coverage, 4),
    }


# ============================================================
# 主归因函数
# ============================================================
def compute_attribution(positions_path: str | Path = DEFAULT_POSITIONS_PATH) -> RiskAttribution:
    """计算风险归因面板

    Args:
        positions_path: positions.json 路径

    Returns:
        RiskAttribution 数据对象
    """
    positions = load_positions(positions_path)
    hedge_positions = load_hedge_positions(positions_path)

    result = RiskAttribution()
    if not positions:
        result.warnings.append("无持仓数据")
        return result

    total = sum(p.get("amount", 0.0) for p in positions)
    result.total_value = round(total, 2)
    if total <= 0:
        result.warnings.append("总持仓金额为 0")
        return result

    # 按行业 / 风格 / 类型聚合
    sector_pairs = [(p.get("sector", "其他"), p.get("amount", 0.0)) for p in positions]
    style_pairs = [(p.get("style", "其他"), p.get("amount", 0.0)) for p in positions]
    type_pairs = [(p.get("type", "STOCK"), p.get("amount", 0.0)) for p in positions]

    result.by_sector = _aggregate(sector_pairs)
    result.by_style = _aggregate(style_pairs)
    result.by_type = _aggregate(type_pairs)

    # 集中度
    weights = [p.get("amount", 0.0) for p in positions]
    result.concentration = _calc_concentration(weights)

    # 对冲工具剩余风险
    result.hedge_residual = _calc_hedge_residual(positions, hedge_positions)

    # 警告
    if result.concentration.get("top1", 0) > 0.20:
        result.warnings.append(f"单标的集中度过高: Top1 = {result.concentration['top1']:.2%} > 20%")
    if result.concentration.get("hhi", 0) > 0.15:
        result.warnings.append(f"组合 HHI = {result.concentration['hhi']:.3f} > 0.15, 分散度不足")
    if result.hedge_residual.get("residual_beta", 0) > 0.5:
        result.warnings.append(f"对冲后剩余 Beta = {result.hedge_residual['residual_beta']:.2f} > 0.5, 对冲不足")
    if result.hedge_residual.get("tail_risk_coverage_pct", 0) < 0.10:
        result.warnings.append(
            f"尾部风险覆盖率 = {result.hedge_residual['tail_risk_coverage_pct']:.2%} < 10%, 期权保护不足"
        )

    return result


def attribution_to_dict(attribution: RiskAttribution) -> dict[str, Any]:
    """将归因结果转为可序列化字典"""
    return {
        "total_value": attribution.total_value,
        "by_sector": attribution.by_sector,
        "by_style": attribution.by_style,
        "by_type": attribution.by_type,
        "by_sector_pct": _to_pct_map(attribution.by_sector, attribution.total_value),
        "by_style_pct": _to_pct_map(attribution.by_style, attribution.total_value),
        "by_type_pct": _to_pct_map(attribution.by_type, attribution.total_value),
        "concentration": attribution.concentration,
        "hedge_residual": attribution.hedge_residual,
        "warnings": attribution.warnings,
    }


# ============================================================
# 终端输出 (Markdown 风格)
# ============================================================
def print_attribution(positions_path: str | Path = DEFAULT_POSITIONS_PATH) -> None:
    """打印风险归因面板 (兼容旧版接口)"""
    attribution = compute_attribution(positions_path)
    if not attribution.total_value:
        logger.info("无持仓数据")
        return

    logger.info("=" * 70)
    logger.info("组合风险归因 (Risk Attribution Panel)")
    logger.info(f"总持仓金额: ¥{attribution.total_value:,.0f}")
    logger.info("=" * 70)

    logger.info("\n[行业分布]")
    for k, v in sorted(attribution.by_sector.items(), key=lambda x: x[1], reverse=True):
        pct = v / attribution.total_value
        logger.info(f"  {k:<10} ¥{v:>12,.0f}  {pct:>6.2%}")

    logger.info("\n[风格分布]")
    for k, v in sorted(attribution.by_style.items(), key=lambda x: x[1], reverse=True):
        pct = v / attribution.total_value
        logger.info(f"  {k:<10} ¥{v:>12,.0f}  {pct:>6.2%}")

    logger.info("\n[资产类型分布]")
    for k, v in sorted(attribution.by_type.items(), key=lambda x: x[1], reverse=True):
        pct = v / attribution.total_value
        logger.info(f"  {k:<10} ¥{v:>12,.0f}  {pct:>6.2%}")

    logger.info("\n[集中度指标]")
    c = attribution.concentration
    logger.info(f"  HHI:        {c.get('hhi', 0):.4f}  (越低越分散)")
    logger.info(f"  Top1 占比:  {c.get('top1', 0):.2%}")
    logger.info(f"  Top3 占比:  {c.get('top3', 0):.2%}")
    logger.info(f"  Top5 占比:  {c.get('top5', 0):.2%}")
    logger.info(f"  有效标的数: {c.get('effective_n', 0):.2f}")

    logger.info("\n[对冲工具剩余风险]")
    h = attribution.hedge_residual
    logger.info(f"  组合加权 Beta:        {h.get('portfolio_beta', 0):.4f}")
    logger.info(f"  期货对冲 Beta 减少:   {h.get('futures_beta_reduction', 0):.4f}")
    logger.info(f"  期货手数:             {h.get('futures_contracts', 0)}")
    logger.info(f"  期权合约数:           {h.get('options_contracts', 0)}")
    logger.info(f"  期权权利金预算:       ¥{h.get('options_premium_budget', 0):,.0f}")
    logger.info(f"  期权名义价值:         ¥{h.get('options_notional', 0):,.0f}")
    logger.info(f"  剩余 Beta:            {h.get('residual_beta', 0):.4f}")
    logger.info(f"  尾部风险覆盖率:       {h.get('tail_risk_coverage_pct', 0):.2%}")

    if attribution.warnings:
        logger.warning("\n[警告]")
        for w in attribution.warnings:
            logger.info(f"  ⚠️  {w}")
    logger.info("=" * 70)


if __name__ == "__main__":
    print_attribution()
