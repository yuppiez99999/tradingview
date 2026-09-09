"""风格级权重硬上限 (max_weight_by_style) 回归测试 — R-9 (2026-09-09).

用户拍板: 国债/货基类 ETF 作为防御与现金替代, 单列 25%~30% 上限, 不再受个券
15% 上限约束。否则会出现「个券上限 15%」与「风格目标 25%」两笔矛盾的减仓单。
"""

from __future__ import annotations

from utils.execution.rebalance_execution_orders import (
    TARGET_ALLOCATION,
    generate_max_weight_reduction_orders,
)


def test_treasury_uses_wider_style_limit():
    """国债 51% 在国债上限 30% 下: 减至 30%, 而非个券默认 15%."""
    positions = {"511010.SH": 10000.0, "588080.SH": 10000.0}
    prices = {"511010.SH": 100.0, "588080.SH": 100.0}  # 各 100 万, 总 200 万
    styles = {"511010.SH": "国债", "588080.SH": "科技"}

    orders = generate_max_weight_reduction_orders(
        positions, prices, styles, max_weight=0.15, max_weight_by_style={"国债": 0.30}
    )

    by_code = {o["code"]: o for o in orders}
    assert set(by_code) == {"511010.SH", "588080.SH"}, "两者均超限 (50% > 各自上限)"
    assert by_code["511010.SH"]["target_weight"] == 0.30
    # 减至 30% -> 目标 60 万, 卖出 40 万 = 4000 股
    assert by_code["511010.SH"]["shares"] == 4000
    assert by_code["588080.SH"]["target_weight"] == 0.15


def test_without_style_limits_falls_back_to_single_weight():
    """未配置风格上限时行为与变更前一致 (向后兼容)."""
    positions = {"511010.SH": 10000.0, "588080.SH": 10000.0}
    prices = {"511010.SH": 100.0, "588080.SH": 100.0}
    styles = {"511010.SH": "国债", "588080.SH": "科技"}

    orders = generate_max_weight_reduction_orders(positions, prices, styles, max_weight=0.15)

    by_code = {o["code"]: o for o in orders}
    assert by_code["511010.SH"]["target_weight"] == 0.15
    assert by_code["511010.SH"]["shares"] == 7000  # 100万 -> 30万, 卖 70 万


def test_style_limit_below_current_weight_only():
    """权重已在风格上限内的标的不生成减仓单."""
    positions = {"511010.SH": 10000.0, "588080.SH": 70000.0}
    prices = {"511010.SH": 100.0, "588080.SH": 100.0}  # 100万 / 700万, 总 800万
    styles = {"511010.SH": "国债", "588080.SH": "科技"}

    orders = generate_max_weight_reduction_orders(
        positions, prices, styles, max_weight=0.15, max_weight_by_style={"国债": 0.30}
    )

    # 国债 12.5% < 30% -> 不减; 科技 87.5% > 15% -> 减
    assert [o["code"] for o in orders] == ["588080.SH"]


def test_treasury_target_allocation_aligned_with_build_script():
    """国债风格目标 0.25 — 与 tools/add_treasury_etf.py 的 25% 对齐 (消除第三套口径)."""
    assert TARGET_ALLOCATION["国债"] == 0.25
