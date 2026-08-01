"""
硬性风险约束执行器 (Hard Risk Constraints)
=========================================

顶级对冲基金标准：风险预算是硬约束，优化器输出一旦突破上限，必须被强制
clamp 回合规区间，并明确记录违例（不允许“拦截了却仍报告 ok”）。

此前问题：
- institutional_pipeline_runner 以 max_weight=0.25 实例化优化器与风险引擎，
  与既定 15% 单票上限矛盾；优化器曾输出 33.3% 单票权重。
- 缺少板块集中度硬上限（科技板块在组合中约 31%，远超审慎水平）。

本模块提供：
- enforce_hard_constraints: 将权重 clamp 到单票/板块硬上限，返回违例清单
- validate_risk_budget: 组合级 VaR / 单票 VaR / 集中度 / 板块多维校验
"""

from __future__ import annotations

# 顶级对冲基金审慎默认值
# V3优化 (2026-07-24): 单标的硬上限 15%→10%
# 动机: 2025-08 300308 占 15% 权重产生 +84% 月收益, 导致极端月份依赖
#       (去极端月份后年化 10.67%, 低于目标 12%+); Walk-Forward Sharpe CV=0.80 不稳定
# 10% 上限限制单股集中度, 降低路径依赖风险, 提升稳定性
DEFAULT_MAX_WEIGHT = 0.10  # 单标的硬上限 10% (V3: 15%→10%)
DEFAULT_MAX_SECTOR = 0.25  # 单一板块硬上限 25%
DEFAULT_MAX_DAILY_VAR = 0.015  # 组合日度 VaR95 硬上限 1.5%
DEFAULT_MAX_SINGLE_VAR = 0.008  # 单票日度 VaR95 硬上限 0.8%


def enforce_hard_constraints(  # noqa: C901
    weights: dict[str, float],
    max_weight: float = DEFAULT_MAX_WEIGHT,
    sector_map: dict[str, str] | None = None,
    max_sector: float = DEFAULT_MAX_SECTOR,
) -> tuple[dict[str, float], list[str]]:
    """将权重强制约束到硬上限，返回 (合规权重, 违例清单)。

    注意：本函数始终返回合规后的权重，但会如实记录被 clamp 的违例，
    调用方必须将违例写进报告，不可静默吞掉。

    BUG-04 修复 (2026-07-31): 原归一化步骤会等比例缩放所有权重, 破坏前面
        已经压缩好的板块权重比例。修复: 归一化后重新检查板块上限, 必要时
        再次压缩, 并循环直到收敛 (最多 3 次以防边界震荡)。
    """
    violations: list[str] = []
    clamped: dict[str, float] = {}

    # 1) 单标的硬上限 + 非负
    for sym, w in weights.items():
        w = float(w)
        if w > max_weight + 1e-9:
            violations.append(f"单标的 {sym} 权重 {w:.2%} 超过硬上限 {max_weight:.2%}，已截断")
            w = max_weight
        if w < 0:
            w = 0.0
        clamped[sym] = w

    # 2) 板块集中度硬上限 (循环收敛: 归一化可能破坏板块压缩, 需要多次迭代)
    if sector_map:
        for iteration in range(3):  # 最多 3 次以防边界震荡
            sector_exp: dict[str, float] = {}
            for sym, w in clamped.items():
                sec = sector_map.get(sym, "unknown")
                sector_exp[sec] = sector_exp.get(sec, 0.0) + w
            need_recompress = False
            for sec, exp in sector_exp.items():
                if sec != "unknown" and exp > max_sector + 1e-9:
                    if iteration == 0:
                        violations.append(
                            f"板块 {sec} 暴露 {exp:.2%} 超过硬上限 {max_sector:.2%}，已按比例压缩"
                        )
                    members = [s for s in clamped if sector_map.get(s) == sec]
                    if members and exp > 0:
                        scale = max_sector / exp
                        for s in members:
                            clamped[s] *= scale
                        need_recompress = True
            if not need_recompress:
                break

    # 3) 归一化（允许保留现金，不强制 100% 满仓）
    total = sum(clamped.values())
    if total > 1.0 + 1e-9:
        violations.append(f"权重和 {total:.2%} 超过 100%，已归一化（剩余作为现金）")
        clamped = {s: w / total for s, w in clamped.items()}

        # BUG-04 修复: 归一化后重新检查板块上限 (归一化是等比例缩放, 不会破坏相对比例,
        # 但如果板块压缩后总权重 < 1.0 而单标的截断后总权重 > 1.0, 归一化会让板块权重
        # 等比例缩小, 虽然不会超限但会破坏配置意图; 反之如果板块压缩后总权重 = 1.0,
        # 归一化不会触发, 这里只需复查板块上限未被破坏)
        if sector_map:
            sector_exp2: dict[str, float] = {}
            for sym, w in clamped.items():
                sec = sector_map.get(sym, "unknown")
                sector_exp2[sec] = sector_exp2.get(sec, 0.0) + w
            for sec, exp in sector_exp2.items():
                if sec != "unknown" and exp > max_sector + 1e-9:
                    # 归一化后仍超限 (理论上不应发生, 但防御性处理)
                    violations.append(
                        f"归一化后板块 {sec} 暴露 {exp:.2%} 仍超过 {max_sector:.2%}，二次压缩"
                    )
                    members = [s for s in clamped if sector_map.get(s) == sec]
                    if members and exp > 0:
                        scale = max_sector / exp
                        for s in members:
                            clamped[s] *= scale

    return clamped, violations


def validate_risk_budget(
    target_weights: dict[str, float],
    price_data: dict[str, object] | None = None,
    total_capital: float = 5_000_000.0,
    max_weight: float = DEFAULT_MAX_WEIGHT,
    max_daily_var: float = DEFAULT_MAX_DAILY_VAR,
    max_single_var: float = DEFAULT_MAX_SINGLE_VAR,
    sector_map: dict[str, str] | None = None,
    max_sector: float = DEFAULT_MAX_SECTOR,
) -> tuple[bool, list[str]]:
    """多维风险预算校验，返回 (是否允许, 违例清单)。"""
    violations: list[str] = []
    price_data = price_data or {}

    # 集中度
    for sym, w in target_weights.items():
        if w > max_weight + 1e-9:
            violations.append(f"单标的 {sym} 权重 {w:.2%} 超过 {max_weight:.2%}")

    # 板块
    if sector_map:
        sector_exp: dict[str, float] = {}
        for sym, w in target_weights.items():
            sec = sector_map.get(sym, "unknown")
            sector_exp[sec] = sector_exp.get(sec, 0.0) + w
        for sec, exp in sector_exp.items():
            if sec != "unknown" and exp > max_sector + 1e-9:
                violations.append(f"板块 {sec} 暴露 {exp:.2%} 超过 {max_sector:.2%}")

    # VaR（仅在提供历史价格时计算；否则用默认波动率近似）
    if price_data:
        port_var, single_vars = _approx_var(target_weights, price_data, total_capital)
        if port_var > max_daily_var:
            violations.append(f"组合日度VaR95={port_var:.2%} 超过上限 {max_daily_var:.2%}")
        for sym, v in single_vars.items():
            if v > max_single_var:
                violations.append(f"单标的 {sym} VaR95={v:.2%} 超过上限 {max_single_var:.2%}")

    return (len(violations) == 0), violations


def _approx_var(
    target_weights: dict[str, float],
    price_data: dict[str, object],
    total_capital: float,
) -> tuple[float, dict[str, float]]:
    """基于历史收益率的近似 VaR（日度，95%）。"""
    import numpy as np  # 局部导入，避免无 numpy 环境报错

    rets = []
    weights = []
    single = {}
    default_vol_daily = 0.25 / (252**0.5)
    for sym, w in target_weights.items():
        series = price_data.get(sym)
        r = None
        if series is not None and hasattr(series, "pct_change"):
            try:
                r = series.pct_change().dropna().values
            except Exception:  # P2 模块 fail-safe, 待后续精确化
                r = None
        if r is None or len(r) < 5:
            var = default_vol_daily * 1.65 * abs(w)
            single[sym] = float(var)
            continue
        sorted_r = np.sort(r)
        idx = max(0, int(0.05 * len(sorted_r)) - 1)
        var_sym = -sorted_r[idx] if idx >= 0 else default_vol_daily
        single[sym] = float(var_sym * abs(w))
        rets.append(r[-min(len(r), 252) :])
        weights.append(w)

    if not rets:
        return 0.0, single
    min_len = min(len(x) for x in rets)
    aligned = np.column_stack([x[-min_len:] for x in rets])
    w = np.array(weights, dtype=float)  # type: ignore
    w = w / (w.sum() if w.sum() > 0 else 1.0)  # type: ignore
    port_ret = aligned @ w
    sorted_pr = np.sort(port_ret)
    idx = max(0, int(0.05 * len(sorted_pr)) - 1)
    port_var = float(-sorted_pr[idx]) if idx >= 0 else default_vol_daily
    return port_var, single
