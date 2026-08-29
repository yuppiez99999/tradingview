"""G7 boost: ms_strategy/scripts/hedge_execution_orders.py 单元测试.

覆盖:
  - build_orders: beta 分支 (IF/IC/IM) + n=0 + 避险 gap>0/gap=0 + OPTIONS + NO_HEDGE
  - load_positions: 文件解析 + 字段回退 (phase1_shares/total_shares/shares)
  - main: 主流程 (mock 文件 IO)
mock 文件读取与 json 加载, 不依赖真实 positions.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import mock_open, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "ms_strategy" / "scripts"))

import hedge_execution_orders as heo  # noqa: E402

# ============================================================
# 1. build_orders — beta 分支
# ============================================================


class TestBuildOrdersBetaBranch:
    def test_beta_high_uses_if(self):
        plan = {"action": "HEDGE", "portfolio_beta": 1.2, "total_hedge_pct": 0.5}
        result = heo.build_orders(plan, {}, {})
        assert result["portfolio_beta"] == 1.2
        # hedge_value = 0.5 * 5_000_000 = 2_500_000
        # IF: notional = 300 * 3800 = 1_140_000, beta_adj=1.0
        # n = int(2_500_000 / 1_140_000) = 2
        futures = [o for o in result["orders"] if o["type"] == "FUTURES"]
        assert len(futures) == 1
        assert futures[0]["instrument"] == "IF"
        assert futures[0]["contracts"] == 2

    def test_beta_mid_uses_ic(self):
        plan = {"action": "HEDGE", "portfolio_beta": 1.0, "total_hedge_pct": 0.5}
        result = heo.build_orders(plan, {}, {})
        # IC: notional = 200 * 5500 = 1_100_000, beta_adj=1.2
        # n = int(2_500_000 / (1_100_000 * 1.2)) = int(1.89) = 1
        futures = [o for o in result["orders"] if o["type"] == "FUTURES"]
        assert len(futures) == 1
        assert futures[0]["instrument"] == "IC"
        assert futures[0]["contracts"] == 1

    def test_beta_low_uses_im(self):
        plan = {"action": "HEDGE", "portfolio_beta": 0.5, "total_hedge_pct": 0.5}
        result = heo.build_orders(plan, {}, {})
        # IM: notional = 200 * 5800 = 1_160_000, beta_adj=1.1
        # n = int(2_500_000 / (1_160_000 * 1.1)) = int(1.96) = 1
        futures = [o for o in result["orders"] if o["type"] == "FUTURES"]
        assert len(futures) == 1
        assert futures[0]["instrument"] == "IM"
        assert futures[0]["contracts"] == 1

    def test_beta_boundary_above_1_1(self):
        plan = {"action": "HEDGE", "portfolio_beta": 1.11, "total_hedge_pct": 0.3}
        result = heo.build_orders(plan, {}, {})
        futures = [o for o in result["orders"] if o["type"] == "FUTURES"]
        assert futures[0]["instrument"] == "IF"

    def test_beta_boundary_above_0_9(self):
        plan = {"action": "HEDGE", "portfolio_beta": 0.91, "total_hedge_pct": 0.3}
        result = heo.build_orders(plan, {}, {})
        futures = [o for o in result["orders"] if o["type"] == "FUTURES"]
        assert futures[0]["instrument"] == "IC"


# ============================================================
# 2. build_orders — n=0 (无期货对冲)
# ============================================================


class TestBuildOrdersNoFutures:
    def test_zero_hedge_pct_no_futures(self):
        plan = {"action": "NO_HEDGE", "portfolio_beta": 1.2, "total_hedge_pct": 0.0}
        result = heo.build_orders(plan, {}, {})
        futures = [o for o in result["orders"] if o["type"] == "FUTURES"]
        assert len(futures) == 0

    def test_small_hedge_pct_rounds_to_zero(self):
        plan = {"action": "HEDGE", "portfolio_beta": 1.2, "total_hedge_pct": 0.001}
        result = heo.build_orders(plan, {}, {})
        # hedge_value = 5000, IF notional*adj = 1_140_000 → n=0
        futures = [o for o in result["orders"] if o["type"] == "FUTURES"]
        assert len(futures) == 0


# ============================================================
# 3. build_orders — 避险追加
# ============================================================


class TestBuildOrdersDefense:
    def test_no_defense_when_no_positions(self):
        # deployed=0 → target_defense=0 → gap=max(0, 0-196750)=0
        plan = {"action": "HEDGE", "portfolio_beta": 0.5, "total_hedge_pct": 0.0}
        result = heo.build_orders(plan, {}, {})
        safe = [o for o in result["orders"] if o["type"] == "SAFE_HAVEN"]
        assert len(safe) == 0

    def test_defense_gap_when_deployed_large(self):
        # deployed * 0.15 > 196750 → deployed > 1311666.67
        positions = {"X": 100000}
        prices = {"X": 20.0}  # deployed = 2_000_000, target_defense = 300_000
        plan = {"action": "HEDGE", "portfolio_beta": 0.5, "total_hedge_pct": 0.0}
        result = heo.build_orders(plan, positions, prices)
        safe = [o for o in result["orders"] if o["type"] == "SAFE_HAVEN"]
        assert len(safe) == 1
        # gap = 300_000 - 196_750 = 103_250
        assert safe[0]["amount"] == pytest.approx(103250, abs=1)
        assert safe[0]["instrument"] == "518880"

    def test_no_defense_when_deployed_small(self):
        positions = {"X": 100}
        prices = {"X": 10.0}  # deployed = 1000, target_defense = 150
        plan = {"action": "HEDGE", "portfolio_beta": 0.5, "total_hedge_pct": 0.0}
        result = heo.build_orders(plan, positions, prices)
        safe = [o for o in result["orders"] if o["type"] == "SAFE_HAVEN"]
        assert len(safe) == 0


# ============================================================
# 4. build_orders — OPTIONS 尾部保护
# ============================================================


class TestBuildOrdersOptions:
    def test_options_always_present(self):
        plan = {"action": "HEDGE", "portfolio_beta": 0.5, "total_hedge_pct": 0.0}
        result = heo.build_orders(plan, {}, {})
        options = [o for o in result["orders"] if o["type"] == "OPTIONS"]
        assert len(options) == 1
        assert options[0]["action"] == "BUY_PROTECTION"
        assert options[0]["contracts"] == 1


# ============================================================
# 5. build_orders — 返回结构
# ============================================================


class TestBuildOrdersStructure:
    def test_return_keys(self):
        plan = {"action": "HEDGE", "portfolio_beta": 1.2, "total_hedge_pct": 0.5}
        result = heo.build_orders(plan, {}, {})
        assert set(result.keys()) == {
            "date",
            "action",
            "portfolio_beta",
            "hedge_pct",
            "orders",
        }
        assert result["action"] == "HEDGE"
        assert result["hedge_pct"] == 0.5

    def test_date_format(self):
        plan = {"action": "HEDGE", "portfolio_beta": 0.5, "total_hedge_pct": 0.0}
        result = heo.build_orders(plan, {}, {})
        # YYYY-MM-DD
        assert len(result["date"]) == 10
        assert result["date"][4] == "-"

    def test_default_action_no_hedge(self):
        plan = {}
        result = heo.build_orders(plan, {}, {})
        assert result["action"] == "NO_HEDGE"

    def test_default_beta_zero(self):
        plan = {}
        result = heo.build_orders(plan, {}, {})
        assert result["portfolio_beta"] == 0.0

    def test_futures_order_fields(self):
        plan = {"action": "HEDGE", "portfolio_beta": 1.2, "total_hedge_pct": 0.5}
        result = heo.build_orders(plan, {}, {})
        futures = [o for o in result["orders"] if o["type"] == "FUTURES"][0]
        assert "notional" in futures
        assert "estimated_cost" in futures
        assert "budget_pct" in futures
        assert futures["estimated_cost"] == pytest.approx(futures["notional"] * 0.00013)

    def test_beta_none_treated_as_zero(self):
        plan = {"action": "HEDGE", "portfolio_beta": None, "total_hedge_pct": 0.0}
        result = heo.build_orders(plan, {}, {})
        assert result["portfolio_beta"] == 0.0

    def test_hedge_pct_none_treated_as_zero(self):
        plan = {"action": "HEDGE", "portfolio_beta": 0.5, "total_hedge_pct": None}
        result = heo.build_orders(plan, {}, {})
        assert result["hedge_pct"] == 0.0


# ============================================================
# 6. load_positions
# ============================================================


class TestLoadPositions:
    def test_load_positions_normal(self):
        fake_data = {
            "positions": {
                "510300.SH": {
                    "code": "510300.SH",
                    "phase1_shares": 1000,
                    "est_price": 4.5,
                },
                "510050.SH": {
                    "code": "510050.SH",
                    "total_shares": 500,
                    "est_price": 3.0,
                },
            }
        }
        with patch("builtins.open", mock_open(read_data=json.dumps(fake_data))):
            positions, prices = heo.load_positions()
        assert positions == {"510300.SH": 1000.0, "510050.SH": 500.0}
        assert prices == {"510300.SH": 4.5, "510050.SH": 3.0}

    def test_load_positions_shares_fallback(self):
        fake_data = {
            "positions": {
                "X": {"code": "X", "shares": 200, "est_price": 10.0},
            }
        }
        with patch("builtins.open", mock_open(read_data=json.dumps(fake_data))):
            positions, prices = heo.load_positions()
        assert positions == {"X": 200.0}
        assert prices == {"X": 10.0}

    def test_load_positions_skip_no_code(self):
        fake_data = {
            "positions": {
                "X": {"phase1_shares": 100, "est_price": 4.5},  # 无 code
            }
        }
        with patch("builtins.open", mock_open(read_data=json.dumps(fake_data))):
            positions, prices = heo.load_positions()
        assert positions == {}

    def test_load_positions_skip_zero_qty(self):
        fake_data = {
            "positions": {
                "X": {"code": "X", "phase1_shares": 0, "est_price": 4.5},
            }
        }
        with patch("builtins.open", mock_open(read_data=json.dumps(fake_data))):
            positions, prices = heo.load_positions()
        assert positions == {}

    def test_load_positions_default_price_zero(self):
        fake_data = {
            "positions": {
                "X": {"code": "X", "phase1_shares": 100},  # 无 est_price
            }
        }
        with patch("builtins.open", mock_open(read_data=json.dumps(fake_data))):
            positions, prices = heo.load_positions()
        assert positions == {"X": 100.0}
        assert prices == {"X": 0.0}


# ============================================================
# 7. main
# ============================================================


class TestMain:
    def test_main_writes_output_file(self, tmp_path):
        fake_positions = {"X": 100.0}
        fake_prices = {"X": 10.0}

        with (
            patch.object(
                heo, "load_positions", return_value=(fake_positions, fake_prices)
            ),
            patch("os.path.exists", return_value=False),
            patch("builtins.open", mock_open()) as m_open,
            patch("builtins.print"),
        ):
            heo.main()

        # 验证 open 被调用写文件 (load_positions 被 mock, 但 main 仍 open out_path 写)
        # open 调用: 读 plan_path (exists=False 跳过) + 写 out_path
        write_calls = [c for c in m_open.call_args_list if "w" in str(c)]
        assert len(write_calls) >= 1

    def test_main_reads_plan_when_exists(self, tmp_path):
        fake_plan = {"action": "HEDGE", "portfolio_beta": 1.2, "total_hedge_pct": 0.3}
        with (
            patch.object(heo, "load_positions", return_value=({}, {})),
            patch("os.path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data=json.dumps(fake_plan))),
            patch("builtins.print"),
        ):
            # 不应抛出
            heo.main()

    def test_main_default_plan_when_not_exists(self):
        with (
            patch.object(heo, "load_positions", return_value=({}, {})),
            patch("os.path.exists", return_value=False),
            patch("builtins.open", mock_open()),
            patch("builtins.print"),
        ):
            # 不应抛出
            heo.main()
