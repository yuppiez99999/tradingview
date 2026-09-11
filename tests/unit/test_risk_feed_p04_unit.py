"""executor/risk_feed.py 单元测试 — P0-4 闭环 (2026-09-11, Issue #13 → PR #17)

背景:
    P0-4 把 premarket 的 circuit_breaker.passed 从硬编码 True 改成"缺数据即
    False (UNKNOWN)", 但下游 (daily_trade_executor._check_execution_preconditions)
    只看 daily_limit —— 报告从"假 PASS"变 UNKNOWN, 执行却原样放行。
    本模块承载闭环所需的三件事:
      1. check_circuit_breaker_gate —— 主链消费熔断 (fail-closed)
      2. _compute_positions_equity —— 由持仓估算权益 (口径与止损检查一致)
      3. _feed_risk_control_equity —— 真喂数, 解除熔断/集中度分支短路

迁出理由: 宿主 daily_trade_executor.py 有 ≤1500 行结构护栏。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from executor import risk_feed  # noqa: E402


class TestCheckCircuitBreakerGate:
    """check_circuit_breaker_gate: 熔断字段的主链消费口径"""

    def test_passed_true_allows(self):
        assert risk_feed.check_circuit_breaker_gate(
            {"circuit_breaker": {"passed": True, "daily_loss_pct": 0.87, "portfolio_drawdown_pct": 0.93}}
        ) is None

    def test_passed_false_with_data_blocks(self):
        block = risk_feed.check_circuit_breaker_gate(
            {
                "circuit_breaker": {
                    "passed": False,
                    "daily_loss_pct": -4.2,
                    "portfolio_drawdown_pct": 6.1,
                    "source": "基于 2026-09-10 收盘报告",
                }
            }
        )
        assert block is not None
        assert block["status"] == "blocked"
        assert block["reason"] == "熔断/回撤检查未通过"
        assert block["circuit_breaker"]["daily_loss_pct"] == -4.2

    def test_passed_false_unknown_reason_is_explicit(self):
        """数据缺失 (daily_loss_pct=None) 必须给出可区分的 UNKNOWN 文案。"""
        block = risk_feed.check_circuit_breaker_gate(
            {
                "circuit_breaker": {
                    "passed": False,
                    "daily_loss_pct": None,
                    "portfolio_drawdown_pct": None,
                    "source": "数据不可用: 无收盘盈亏报告",
                }
            }
        )
        assert block is not None
        assert "UNKNOWN" in block["reason"]
        assert block["circuit_breaker"]["daily_loss_pct"] is None

    def test_missing_section_is_backward_compatible(self):
        """老指令文件无 circuit_breaker 段 -> 不阻断 (不得误伤存量产物)。"""
        assert risk_feed.check_circuit_breaker_gate({}) is None
        assert risk_feed.check_circuit_breaker_gate({"daily_limit": {"passed": True}}) is None

    def test_passed_missing_defaults_to_block(self):
        """段存在但缺 passed -> 保守阻断 (fail-closed, 不默认放行)。"""
        block = risk_feed.check_circuit_breaker_gate({"circuit_breaker": {}})
        assert block is not None
        assert block["status"] == "blocked"

    def test_block_result_shape_is_stable(self):
        block = risk_feed.check_circuit_breaker_gate({"circuit_breaker": {"passed": False}})
        assert set(block) == {"status", "reason", "blocked_reason", "circuit_breaker"}
        assert block["blocked_reason"] == "circuit_breaker not passed (P0-4 fail-closed)"


class TestComputePositionsEquity:
    @pytest.mark.parametrize(
        ("positions", "expected"),
        [
            ({}, None),
            ({"A.SH": {"phase1_shares": 0, "est_price": 10}}, None),
            ({"A.SH": {"phase1_shares": 100, "est_price": 0}}, None),
            ({"A.SH": {"phase1_shares": 100, "est_price": 10}}, 1000.0),
            ({"A.SH": {"total_shares": 200, "est_price": 10}}, 2000.0),
            ({"A.SH": {"shares": 50, "est_price": 20}}, 1000.0),
            ({"A.SH": {"phase1_shares": -100, "est_price": 10}}, 1000.0),  # 取绝对值
            ({"A.SH": {"phase1_shares": "100", "est_price": "10"}}, 1000.0),
            ({"A.SH": "not-a-dict"}, None),
        ],
    )
    def test_matrix(self, positions, expected):
        assert risk_feed._compute_positions_equity(positions) == expected

    def test_partial_rows_still_summed(self):
        """单行不完整不应导致整份持仓判为不可估算 (那会让喂数永远跳过)。"""
        positions = {
            "OK.SH": {"phase1_shares": 200, "est_price": 1700.0},
            "ZERO.SZ": {"phase1_shares": 0, "est_price": 12.5},
        }
        assert risk_feed._compute_positions_equity(positions) == pytest.approx(340000.0)

    def test_priority_phase1_over_total_over_shares(self):
        positions = {"A.SH": {"phase1_shares": 1, "total_shares": 2, "shares": 3, "est_price": 100}}
        assert risk_feed._compute_positions_equity(positions) == 100.0


class TestFeedRiskControlEquity:
    def test_feeds_real_equity(self):
        rc = MagicMock()
        result = risk_feed._feed_risk_control_equity(
            {"risk_control": rc}, {"A.SH": {"phase1_shares": 100, "est_price": 20}}
        )
        assert result["fed"] is True
        assert result["equity"] == pytest.approx(2000.0)
        rc.update_equity.assert_called_once_with(pytest.approx(2000.0))

    def test_never_feeds_zero(self):
        """估算失败时不得用 0 喂数 —— max_equity=0 会让回撤阈值恒不触发。"""
        rc = MagicMock()
        result = risk_feed._feed_risk_control_equity({"risk_control": rc}, {})
        assert result["fed"] is False
        rc.update_equity.assert_not_called()

    def test_missing_risk_control_is_noop(self):
        result = risk_feed._feed_risk_control_equity({}, {"A.SH": {"phase1_shares": 1, "est_price": 1}})
        assert result["fed"] is False
        assert result["equity"] is None
