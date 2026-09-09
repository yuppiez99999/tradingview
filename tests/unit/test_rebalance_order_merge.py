"""再平衡订单合并去重回归测试 (2026-09-09).

背景: 511010.SH 当日同时命中 max_single_weight 减仓单 (卖 7000 股, 降至 15%)
与风格再平衡单 (卖 1400 股, 降至 22%), 直接拼接会卖出 8400 股 -> 实际权重约
8.3%, 两个目标都没达成 (超调), 还多付一次交易成本。

本测试锁定「同一 code + action 绝不叠加」这条不变量。
"""

from __future__ import annotations

import pytest

from utils.execution.rebalance_execution_orders import merge_duplicate_orders


def _order(code, action, shares, price, target_weight, reason=""):
    return {
        "style": "国债" if code.startswith("511") else "科技",
        "code": code,
        "action": action,
        "order_type": "LIMIT",
        "shares": shares,
        "est_price": price,
        "est_amount": shares * price,
        "target_weight": target_weight,
        "reason": reason,
    }


def test_sell_orders_same_code_are_not_summed():
    """同一标的的两笔 SELL 必须合并为 1 笔, 取更严格的 (股数更大) 那笔."""
    orders = [
        _order("511010.SH", "SELL", 7000, 141.112, 0.15, "max_single_weight_violation"),
        _order("511010.SH", "SELL", 1400, 141.112, 0.22, ""),
    ]

    merged = merge_duplicate_orders(orders)

    assert len(merged) == 1, "同一标的的重复 SELL 单必须合并, 不能叠加执行"
    assert merged[0]["shares"] == 7000, "SELL 应取股数更大者 (更严格的风控口径)"
    assert merged[0]["merged_count"] == 2
    # 叠加执行会卖出 8400 股 -> 权重超调, 这里显式锁定不变量
    assert merged[0]["shares"] != 8400
    assert merged[0]["needs_decision"] is True, "目标权重不一致必须标记待人工确认"
    assert merged[0]["conflicting_targets"] == [0.15, 0.22]


def test_merged_order_recomputes_est_amount():
    """合并后 est_amount 必须按胜出单的股数重算, 不能沿用被吞掉那笔."""
    orders = [
        _order("511010.SH", "SELL", 7000, 100.0, 0.15, "max_single_weight_violation"),
        _order("511010.SH", "SELL", 1400, 100.0, 0.22, ""),
    ]

    merged = merge_duplicate_orders(orders)

    assert merged[0]["est_amount"] == 700000.0


def test_buy_orders_same_code_take_smaller():
    """BUY 方向取股数更小者, 避免加仓超调."""
    orders = [
        _order("588080.SH", "BUY", 117400, 1.703, 0.15),
        _order("588080.SH", "BUY", 50000, 1.703, 0.15),
    ]

    merged = merge_duplicate_orders(orders)

    assert len(merged) == 1
    assert merged[0]["shares"] == 50000
    assert merged[0].get("needs_decision") is None, "目标一致不算口径冲突"


def test_distinct_codes_are_preserved():
    """不同标的不受影响, 保持原有顺序."""
    orders = [
        _order("511010.SH", "SELL", 7000, 141.112, 0.15),
        _order("588080.SH", "BUY", 117400, 1.703, 0.15),
        _order("512760.SH", "BUY", 25200, 7.923, 0.15),
    ]

    merged = merge_duplicate_orders(orders)

    assert [o["code"] for o in merged] == ["511010.SH", "588080.SH", "512760.SH"]


def test_empty_input():
    assert merge_duplicate_orders([]) == []


@pytest.mark.parametrize("action,expected", [("SELL", 7000), ("BUY", 1400)])
def test_merge_picks_expected_share_by_action(action, expected):
    """参数化锁定两个方向的选择规则."""
    orders = [
        _order("511010.SH", action, 7000, 10.0, 0.15),
        _order("511010.SH", action, 1400, 10.0, 0.22),
    ]

    merged = merge_duplicate_orders(orders)

    assert merged[0]["shares"] == expected
