"""
再平衡执行单生成器
基于当前持仓与目标配置，生成可执行的再平衡订单

T3.6 迁移: 2026-07-27 从项目根目录迁移到 utils/execution/
- 修正硬编码的 v7.1 旧路径为 v8.4 项目根目录 (基于 __file__ 动态解析)
- 修正路径: sys.path / positions.json / 输出文件路径
"""

import json
import logging
import os
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# T3.6 修正: 动态解析项目根目录 (utils/execution/ → 项目根)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

# T2: 波动率调制 no-trade band (2026-09-07)
from utils.risk.no_trade_band import should_rebalance  # noqa: E402

TARGET_ALLOCATION = {
    "宽基": 0.15,
    "科技": 0.15,
    "制造": 0.08,
    "新能源": 0.08,
    "医药": 0.08,
    "金融": 0.08,
    "资源": 0.05,
    "防御": 0.04,
    "成长": 0.04,
    "顺周期": 0.03,
    # 2026-09-09 (用户拍板 R-9): 国债由 0.22 上调至 0.25, 与 tools/add_treasury_etf.py
    # 的目标权重(0.25)对齐, 消除"风格目标 22% / 建仓脚本 25%"第三套口径。
    # 硬上限见 config/risk.yaml thresholds.max_weight_by_style.国债 = 0.30。
    "国债": 0.25,
}

MIN_TRADE_AMOUNT = 10000
MAX_SINGLE_ORDER_AMOUNT = 200000
MIN_LOT_SIZE = 100
TARGET_TOTAL = 5_000_000.0


def load_positions() -> tuple[dict[str, float], dict[str, float], dict[str, str]]:
    # T3.6 修正: 使用动态解析的项目根目录 (不再硬编码 v7.1 路径)
    path = _PROJECT_ROOT / "config" / "positions.json"
    try:
        with open(path, encoding="utf-8") as f:
            # P2-2 修复: positions 键可能缺失, 用 .get() 保护避免 KeyError 被外层吞掉静默返回 None
            data = json.load(f).get("positions", {}) or {}
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        # C7-1 修复: 文件缺失/损坏时降级为空持仓, 避免整个再平衡流程崩溃
        logger.exception("读取持仓文件失败, 降级为空持仓: %s", e)
        data = {}
    positions = {}
    prices = {}
    styles = {}
    for item in data.values():
        code = item.get("code")
        qty = item.get("phase1_shares") or item.get("total_shares") or item.get("shares", 0)
        price = item.get("est_price", 0.0)
        style = item.get("style", "其他")
        if code and qty:
            positions[code] = float(qty)
            prices[code] = float(price)
            styles[code] = style
    return positions, prices, styles


def classify_style(style_map: dict) -> dict[str, dict[str, Any]]:
    style_allocation: dict[str, dict[str, Any]] = {}
    for code, style in style_map.items():
        if style not in style_allocation:
            style_allocation[style] = {"amount": 0.0, "codes": []}
        style_allocation[style]["codes"].append(code)
    return style_allocation


def calc_current_allocation(positions: dict, prices: dict, style_map: dict) -> dict:
    total = sum(positions.get(s, 0) * prices.get(s, 0.0) for s in positions)
    style_allocation = classify_style(style_map)
    for _style, info in style_allocation.items():
        amount = sum(positions.get(s, 0) * prices.get(s, 0.0) for s in info["codes"])
        info["amount"] = amount
        info["weight"] = amount / total if total > 0 else 0.0
    return style_allocation


def validate_order(code: str, action: str, shares: int, price: float, positions: dict) -> dict:
    est_amount = shares * price
    errors = []
    warnings = []

    if est_amount < MIN_TRADE_AMOUNT:
        errors.append(f"金额不足 {MIN_TRADE_AMOUNT} 元")

    if est_amount > MAX_SINGLE_ORDER_AMOUNT:
        warnings.append(f"单笔金额超过 {MAX_SINGLE_ORDER_AMOUNT} 元")

    if shares % MIN_LOT_SIZE != 0:
        errors.append(f"数量不是 {MIN_LOT_SIZE} 的倍数")

    if action == "SELL":
        current_qty = positions.get(code, 0)
        if shares > current_qty:
            errors.append(f"卖出数量超过持仓: 持仓={current_qty}, 卖出={shares}")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "est_amount": est_amount,
    }


def generate_rebalance_orders(
    style_allocation: dict,
    target_allocation: dict,
    positions: dict,
    prices: dict,
    *,
    volatility: dict[str, float] | None = None,
) -> list:
    """生成再平衡订单.

    T2 新增: volatility — 风格名 → 年化波动率映射, 启用波动率调制
    no-trade band (偏离 < max(2%, 0.5*目标权重*sigma) 的风格跳过).
    None (缺省) 时退化为固定 2% 绝对带宽, 行为向后兼容.
    """
    orders = []
    band_skipped: list[str] = []

    for style, target_weight in target_allocation.items():
        info = style_allocation.get(style, {"amount": 0.0, "weight": 0.0, "codes": []})
        current_weight = info["weight"]
        target_amount = TARGET_TOTAL * target_weight
        current_amount = info["amount"]
        gap = current_amount - target_amount

        # T2: no-trade band — 偏离在容忍带内的风格不调仓, 避免高波动期过度交易
        band_decision = should_rebalance(current_weight, target_weight, sigma=(volatility or {}).get(style))
        if not band_decision.triggered:
            band_skipped.append(
                f"{style}(dev={band_decision.deviation:+.2%} band={band_decision.band:.2%} "
                f"reason={band_decision.reason})"
            )
            continue

        if abs(gap) < MIN_TRADE_AMOUNT:
            continue

        action = "SELL" if gap > 0 else "BUY"
        remaining_gap = abs(gap)

        for code in info["codes"]:
            if code not in prices or code not in positions:
                continue

            price = prices[code]
            if price <= 0:
                continue

            # H20 修复: SELL qty 需受持仓上限约束，避免批量无效单
            # GLM 4.5 复核: positions[code] 来自 load_positions() 第56行 float(qty), 不是 dict
            if action == "SELL":
                current_qty = positions.get(code, 0)
                if current_qty <= 0:
                    continue  # 无持仓，跳过后去下一标的

            max_shares_for_code = int(MAX_SINGLE_ORDER_AMOUNT / price / MIN_LOT_SIZE) * MIN_LOT_SIZE
            needed_shares = int(remaining_gap / price / MIN_LOT_SIZE) * MIN_LOT_SIZE
            qty = min(max_shares_for_code, needed_shares)

            if action == "SELL":
                # 持仓可能含零股(非整百), 卖出数量需向下取整到整百手, 避免 validate_order 判"非100倍数"无效
                qty = min(qty, current_qty)
                qty = int(qty // MIN_LOT_SIZE) * MIN_LOT_SIZE

            if qty == 0:
                continue

            validation = validate_order(code, action, qty, price, positions)

            orders.append(
                {
                    "style": style,
                    "code": code,
                    "action": action,
                    "order_type": "LIMIT",
                    "shares": qty,
                    "est_price": price,
                    "est_amount": validation["est_amount"],
                    "target_weight": target_weight,
                    "current_weight": current_weight,
                    "gap": gap,
                    "validation": validation,
                }
            )

            # H19 修复: 无效订单不占用 remaining_gap，避免阻断同风格其他有效单
            if validation.get("valid"):
                remaining_gap -= validation["est_amount"]
            if remaining_gap < MIN_TRADE_AMOUNT:
                break

    # T2: band 过滤明细记日志 (审计可追溯)
    if band_skipped:
        logger.info(
            "[NoTradeBand] %d 个风格偏离在容忍带内, 跳过调仓: %s",
            len(band_skipped),
            "; ".join(band_skipped),
        )

    orders.sort(key=lambda o: abs(o["gap"]), reverse=True)
    return orders


def generate_max_weight_reduction_orders(
    positions: dict,
    prices: dict,
    styles: dict,
    max_weight: float = 0.15,
    max_weight_by_style: dict | None = None,
) -> list:
    """生成 max_single_weight 违规减仓订单

    对每个权重超上限的标的, 生成 SELL 单将其降至上限。
    返回的订单列表会合并到再平衡订单中优先执行。

    Args:
        max_weight_by_style: 风格 -> 权重硬上限 (2026-09-09 R-9)。国债/货基类
            ETF 作为防御与现金替代, 单列更宽的上限 (如 {"国债": 0.30}),
            避免与风格目标 (TARGET_ALLOCATION["国债"]=0.25) 冲突产生两笔相互
            矛盾的减仓单。缺省(空)时全部沿用 max_weight。
    """
    total = sum(positions.get(s, 0) * prices.get(s, 0.0) for s in positions)
    if total <= 0:
        return []

    style_limits = max_weight_by_style or {}
    orders = []
    for code, qty in positions.items():
        price = prices.get(code, 0.0)
        if price <= 0 or qty <= 0:
            continue
        current_value = qty * price
        current_weight = current_value / total
        limit = float(style_limits.get(styles.get(code) or "", max_weight))
        if current_weight <= limit:
            continue
        target_value = total * limit
        excess_value = current_value - target_value
        excess_shares = int(excess_value / price / MIN_LOT_SIZE) * MIN_LOT_SIZE
        if excess_shares < MIN_LOT_SIZE:
            continue
        style = styles.get(code, "其他")
        validation = validate_order(code, "SELL", excess_shares, price, positions)
        orders.append(
            {
                "style": style,
                "code": code,
                "action": "SELL",
                "order_type": "LIMIT",
                "shares": excess_shares,
                "est_price": price,
                "est_amount": excess_shares * price,
                "target_weight": limit,
                "current_weight": current_weight,
                "gap": excess_value,
                "validation": validation,
                "reason": "max_single_weight_violation",
            }
        )
        logger.warning(
            "max_single_weight 违规: %s (%s) 当前 %.1f%% > %.1f%%, 强制减仓 %d 股",
            code,
            style,
            current_weight * 100,
            max_weight * 100,
            excess_shares,
        )
    return orders


def merge_duplicate_orders(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """合并同一标的(相同 code + action)的重复订单, 防止数量叠加导致权重超调.

    背景 (2026-09-09 实测): ``max_single_weight`` 强制减仓单与风格再平衡单可能
    指向同一标的 —— 511010.SH 当日同时出现「mw 单卖 7000 股(降至 15%)」与
    「风格单卖 1400 股(降至 22%)」, 直接拼接执行会卖出 8400 股, 实际权重跌到
    约 8.3%, 既不等于 15% 也不等于 22%, 且多付一次冲击成本与佣金。

    合并规则 (保守, 绝不叠加):
      * SELL -> 取 shares 最大者 (减得更彻底, 对应更严格的风控口径)
      * BUY  -> 取 shares 最小者 (加得更保守)
      * 同一组内 ``target_weight`` 不一致时标记 ``needs_decision=True`` 并告警,
        说明两套口径(个券上限 vs 风格目标)冲突, 需人工拍板; 标记不阻断执行,
        但必须在再平衡报告中可见。

    订单顺序保持首次出现次序, 便于报告 diff 稳定。
    """
    if not orders:
        return []

    buckets: "OrderedDict[tuple, list[dict[str, Any]]]" = OrderedDict()
    for order in orders:
        key = (order.get("code"), order.get("action"))
        buckets.setdefault(key, []).append(order)

    merged: list[dict[str, Any]] = []
    for (code, action), group in buckets.items():
        if len(group) == 1:
            merged.append(group[0])
            continue

        if action == "SELL":
            winner = max(group, key=lambda o: int(o.get("shares", 0) or 0))
        else:
            winner = min(group, key=lambda o: int(o.get("shares", 0) or 0))

        targets = {round(float(o.get("target_weight", 0) or 0), 6) for o in group}
        conflicting = len(targets) > 1

        coalesced = dict(winner)
        coalesced["est_amount"] = round(
            float(coalesced.get("shares", 0) or 0) * float(coalesced.get("est_price", 0.0) or 0.0),
            2,
        )
        coalesced["merged_count"] = len(group)
        coalesced["merged_reasons"] = sorted({str(o.get("reason", "")) for o in group if o.get("reason")})
        if conflicting:
            coalesced["needs_decision"] = True
            coalesced["conflicting_targets"] = sorted(targets)

        logger.warning(
            "再平衡订单合并: %s %s 出现 %d 单 (取 shares=%d), 目标权重冲突=%s -> %s",
            code,
            action,
            len(group),
            coalesced.get("shares", 0),
            conflicting,
            sorted(targets),
        )
        merged.append(coalesced)

    return merged


def build_report(style_allocation: dict, target_allocation: dict, orders: list) -> dict:
    total = sum(style_allocation[s]["amount"] for s in style_allocation)
    valid_orders = [o for o in orders if o["validation"]["valid"]]
    report = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "total_value": total,
        "style_allocation": {
            s: {
                "amount": style_allocation[s]["amount"],
                "weight": style_allocation[s]["weight"],
            }
            for s in style_allocation
        },
        "target_allocation": target_allocation,
        "orders": orders,
        "summary": {
            "total_orders": len(orders),
            "valid_orders": len(valid_orders),
            "buy_orders": sum(1 for o in orders if o["action"] == "BUY"),
            "sell_orders": sum(1 for o in orders if o["action"] == "SELL"),
            "total_trade_value": sum(abs(o["est_amount"]) for o in valid_orders),
            "min_trade_amount": MIN_TRADE_AMOUNT,
            "max_single_order": MAX_SINGLE_ORDER_AMOUNT,
        },
    }
    return report


def _load_style_volatility() -> dict[str, float] | None:
    """T2: 读取风格波动率映射 (可选).

    来源: reports/style_volatility.json, 格式 {"宽基": 0.25, "国债": 0.05, ...}.
    文件缺失/损坏时返回 None (fail-open), generate_rebalance_orders 退化为
    固定 2% 带宽 — 行为与本变更前一致.
    """
    path = _PROJECT_ROOT / "reports" / "style_volatility.json"
    try:
        if not path.exists():
            logger.info("[NoTradeBand] %s 不存在, 波动率调制未启用 (固定 2%% 带宽)", path)
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        vol = {str(k): float(v) for k, v in raw.items() if isinstance(v, (int, float))}
        if not vol:
            logger.warning("[NoTradeBand] %s 内容为空, 波动率调制未启用", path)
            return None
        logger.info("[NoTradeBand] 已加载 %d 个风格的波动率, 启用调制容忍带", len(vol))
        return vol
    except (json.JSONDecodeError, OSError, TypeError, ValueError) as e:
        logger.warning("[NoTradeBand] 读取波动率文件失败, 未启用调制: %s", e)
        return None


def main() -> None:
    positions, prices, styles = load_positions()
    style_allocation = calc_current_allocation(positions, prices, styles)
    volatility = _load_style_volatility()
    orders = generate_rebalance_orders(style_allocation, TARGET_ALLOCATION, positions, prices, volatility=volatility)
    report = build_report(style_allocation, TARGET_ALLOCATION, orders)

    logger.info("=" * 70)
    logger.info("再平衡执行单")
    logger.info("=" * 70)
    logger.info(f"日期: {report['date']}")
    logger.info(f"组合总市值: {report['total_value']:,.0f}")
    logger.info(f"{'风格':10s} {'当前权重':>10s} {'目标权重':>10s} {'偏差':>10s}")
    logger.info("-" * 70)
    for style in sorted(set(list(style_allocation.keys()) + list(TARGET_ALLOCATION.keys()))):
        current = style_allocation.get(style, {}).get("weight", 0.0)
        target = TARGET_ALLOCATION.get(style, 0.0)
        deviation = current - target
        status = "✅" if abs(deviation) < 0.02 else "⚠️"
        logger.info(f"{style:10s} {current:>10.2%} {target:>10.2%} {deviation:>+10.2%} {status}")
    logger.info("-" * 70)
    logger.info(f"订单数: {report['summary']['total_orders']} (有效 {report['summary']['valid_orders']})")
    logger.info(f"买入: {report['summary']['buy_orders']} | 卖出: {report['summary']['sell_orders']}")
    logger.info(f"总交易金额: {report['summary']['total_trade_value']:,.0f}")
    logger.info(f"单笔限额: {MIN_TRADE_AMOUNT:,} ~ {MAX_SINGLE_ORDER_AMOUNT:,} 元")

    for i, o in enumerate(orders, 1):
        status = "✅" if o["validation"]["valid"] else "❌"
        logger.info(f"[{i}] {status} {o['action']} | {o['code']} | {o['style']}")
        logger.info(f"    数量: {o['shares']} | 预估金额: {o['est_amount']:,.0f}")
        logger.info(f"    当前权重: {o['current_weight']:.2%} | 目标权重: {o['target_weight']:.2%}")
        if o["validation"]["warnings"]:
            logger.warning(f"    警告: {'; '.join(o['validation']['warnings'])}")
        if not o["validation"]["valid"]:
            logger.error(f"    错误: {'; '.join(o['validation']['errors'])}")
    logger.info("=" * 70)

    # T3.6 修正: 输出路径使用项目根目录的 reports/
    out_path = _PROJECT_ROOT / "reports" / f"rebalance_execution_orders_{datetime.now():%Y%m%d}.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info(f"已保存: {out_path}")


if __name__ == "__main__":
    main()
