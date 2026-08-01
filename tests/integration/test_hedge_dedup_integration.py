# -*- coding: utf-8 -*-
"""test_hedge_dedup_integration.py — 对冲订单去重 + apply_to_plan 集成测试

5 条关键链路 #5: HedgeExecutionEngine + ProtectivePutEngine → 同一底层重复 → 去重

设计原则:
    - 真实加载 HedgeExecutionEngine (非 mock)
    - Mock ProtectivePutEngine (避免真实期权链数据依赖)
    - 验证 _deduplicate_put_orders 在 plan 字段层级正确去重
"""
import json
import pytest

from utils.risk_guard_integrator import RiskGuardIntegrator
from utils.hedge_execution_engine import HedgeExecutionEngine


@pytest.fixture
def integrator_isolated(tmp_path, monkeypatch):
    """隔离的 RiskGuardIntegrator"""
    monkeypatch.setattr(
        "utils.risk_guard_integrator.LOGS_DIR", tmp_path / "logs"
    )
    monkeypatch.setattr(
        "utils.risk_guard_integrator.REPORTS_DIR", tmp_path / "reports"
    )
    monkeypatch.setattr(
        "utils.risk_guard_integrator.TRADE_PLANS_DIR", tmp_path / "trade_plans"
    )
    return RiskGuardIntegrator(report_date="2026-07-21")


@pytest.fixture
def plan_with_overlapping_puts():
    """含重复 PUT 订单的 plan (HedgeExecutionEngine 与 ProtectivePutEngine 重叠)

    HedgeExecutionEngine.options_orders 含 "510050 Put"
    ProtectivePutEngine.put_protection_orders 含 underlying="510050"
    → 510050 应被去重 (从 hedge_execution 剔除)
    """
    return {
        "put_protection_orders": [
            {
                "underlying": "510050",
                "instrument": "510050 Put",
                "contracts": 60,
                "premium_budget": 900_000,
                "source": "ProtectivePutEngine",
            },
            {
                "underlying": "588080",
                "instrument": "588080 Put",
                "contracts": 25,
                "premium_budget": 300_000,
                "source": "ProtectivePutEngine",
            },
        ],
        "hedge_execution": {
            "futures_orders": [
                {"instrument": "IF", "direction": "SELL", "contracts": 3}
            ],
            "options_orders": [
                # 重复: 510050 已被 ProtectivePutEngine 覆盖
                {
                    "order_id": "HEDGE_PUT_1",
                    "instrument": "510050 Put",
                    "direction": "BUY_PUT",
                    "contracts": 30,
                    "source": "HedgeExecutionEngine",
                },
                # 不重复: 159915 不在 put_protection_orders 中
                {
                    "order_id": "HEDGE_PUT_2",
                    "instrument": "159915 Put",
                    "direction": "BUY_PUT",
                    "contracts": 20,
                    "source": "HedgeExecutionEngine",
                },
            ],
        },
        "risk_guard": {},
    }


# ============================================================
# 集成测试: PUT 订单去重逻辑
# ============================================================


class TestPutOrderDeduplication:
    """对冲订单去重集成测试"""

    @pytest.mark.integration
    def test_duplicate_510050_removed_from_hedge_options(
        self, integrator_isolated, plan_with_overlapping_puts
    ):
        """510050 重复时, 从 hedge_execution.options_orders 剔除"""
        plan = plan_with_overlapping_puts
        integrator_isolated._deduplicate_put_orders(plan)

        options = plan["hedge_execution"]["options_orders"]

        # 510050 Put 应被剔除
        assert all("510050" not in o.get("instrument", "") for o in options), \
            "510050 Put 应被去重剔除"

        # 159915 Put 应保留 (不重复)
        assert any("159915" in o.get("instrument", "") for o in options), \
            "159915 Put 应保留 (不重复)"

        # 应只剩 1 个 options 订单
        assert len(options) == 1

    @pytest.mark.integration
    def test_dedup_records_removed_in_risk_guard(
        self, integrator_isolated, plan_with_overlapping_puts
    ):
        """去重结果应记录在 risk_guard.put_hedge_dedup"""
        plan = plan_with_overlapping_puts
        integrator_isolated._deduplicate_put_orders(plan)

        dedup_info = plan["risk_guard"].get("put_hedge_dedup")
        assert dedup_info is not None, "去重信息应记录在 risk_guard.put_hedge_dedup"
        assert dedup_info["removed_count"] == 1
        assert "510050" in dedup_info["removed"], \
            "removed 列表应包含被剔除的底层代码"
        # reason 是固定文案, 不含具体代码
        assert "ProtectivePutEngine" in dedup_info["reason"]
        assert "避免超额对冲" in dedup_info["reason"]

    @pytest.mark.integration
    def test_futures_orders_not_affected_by_dedup(
        self, integrator_isolated, plan_with_overlapping_puts
    ):
        """去重只影响 options_orders, 不影响 futures_orders"""
        plan = plan_with_overlapping_puts
        original_futures = list(plan["hedge_execution"]["futures_orders"])

        integrator_isolated._deduplicate_put_orders(plan)

        # futures_orders 应保持不变
        assert plan["hedge_execution"]["futures_orders"] == original_futures

    @pytest.mark.integration
    def test_no_overlap_no_modification(
        self, integrator_isolated, plan_with_overlapping_puts
    ):
        """无重叠时 (修改 put_protection_orders 为不重复的底层) 不去重"""
        plan = plan_with_overlapping_puts
        # 修改 put_protection_orders, 使其与 options_orders 不重叠
        plan["put_protection_orders"] = [
            {"underlying": "510500", "instrument": "510500 Put"}
        ]

        original_options = list(plan["hedge_execution"]["options_orders"])
        integrator_isolated._deduplicate_put_orders(plan)

        # options_orders 应保持不变
        assert plan["hedge_execution"]["options_orders"] == original_options


# ============================================================
# 集成测试: 真实 HedgeExecutionEngine + 去重
# ============================================================


class TestHedgeEngineWithDedup:
    """真实 HedgeExecutionEngine 生成订单 + 去重"""

    @pytest.mark.integration
    def test_hedge_engine_orders_can_be_deduplicated(
        self, tmp_path, integrator_isolated
    ):
        """HedgeExecutionEngine 生成的真实订单应能被去重逻辑处理

        构造场景:
            1. HedgeExecutionEngine 生成 options_orders (含 "510050 Put")
            2. ProtectivePutEngine 也覆盖 510050
            3. 去重后 hedge_execution.options_orders 中 510050 被剔除
        """
        # 构造干净的 positions.json
        positions_data = {
            "meta": {"total_capital": 5_000_000, "hedge_capital": 1_000_000},
            "positions": {
                "510050.SH": {"shares": 50000, "est_price": 2.85, "sector": "宽基"}
            },
            "hedge_positions": {
                "ETF_put_options": {
                    "instrument": "510050 Put",
                    "exchange": "SSE",
                    "target_contracts": 60,
                    "premium_budget": 900_000,
                    "strike": "OTM_5%",
                }
            },
        }
        positions_file = tmp_path / "positions.json"
        positions_file.write_text(
            json.dumps(positions_data, ensure_ascii=False), encoding="utf-8"
        )

        engine = HedgeExecutionEngine(positions_file=str(positions_file))

        # Mock IF 期货价格 (避免网络调用)
        import unittest.mock as um
        with um.patch.object(engine, "_get_if_price", return_value=4650.0):
            hedge_result = engine.generate_hedge_orders(drawdown_level=0)

        # hedge_result 应含 options_orders (来自 hedge_positions)
        assert len(hedge_result["options_orders"]) == 1
        assert hedge_result["options_orders"][0]["instrument"] == "510050 Put"

        # 构造 plan, 让 put_protection_orders 也覆盖 510050
        plan = {
            "put_protection_orders": [
                {"underlying": "510050", "instrument": "510050 Put"}
            ],
            "hedge_execution": hedge_result,
            "risk_guard": {},
        }

        # 执行去重
        integrator_isolated._deduplicate_put_orders(plan)

        # 510050 Put 应被剔除 (因 ProtectivePutEngine 已覆盖)
        remaining_options = plan["hedge_execution"]["options_orders"]
        assert all(
            "510050" not in o.get("instrument", "") for o in remaining_options
        ), "510050 Put 应被去重剔除"

        # 去重信息应被记录
        assert plan["risk_guard"]["put_hedge_dedup"]["removed_count"] == 1
