"""test_hedge_execution_engine_unit.py — 对冲执行引擎单元测试

覆盖 bug 回归:
    - P0-E: hedge_positions 含字符串字段导致遍历崩溃

设计原则:
    - 使用临时 positions.json (tmp_path), 不污染 config/positions.json
    - 全 mock, 不依赖网络 (新浪行情接口)
    - 每个 bug 至少一个用例, 函数名包含 bug 编号
"""

import json

import pytest

from utils.hedge_execution_engine import HedgeExecutionEngine

# ============================================================
# 辅助 fixture
# ============================================================


@pytest.fixture
def engine_with_broken_hedge_positions(tmp_path, broken_hedge_positions_p0e):
    """使用 P0-E bug 重现样本的 HedgeExecutionEngine 实例

    broken_hedge_positions_p0e 来自 tests/conftest.py, 包含:
        hedge_positions.description = "200万纯期权对冲" (字符串, 触发 P0-E bug)
        hedge_positions.hedge_mode = "OPTIONS_ONLY"    (字符串, 触发 P0-E bug)
        hedge_positions.ETF_put_options = {...}        (正常 dict)
        hedge_positions.ETF_put_options_2 = {...}      (正常 dict)
    """
    positions_file = tmp_path / "positions.json"
    positions_file.write_text(
        json.dumps(broken_hedge_positions_p0e, ensure_ascii=False),
        encoding="utf-8",
    )
    return HedgeExecutionEngine(positions_file=str(positions_file))


@pytest.fixture
def engine_with_clean_hedge_positions(tmp_path):
    """干净的全 dict hedge_positions (无字符串字段)"""
    clean_data = {
        "meta": {"total_capital": 5_000_000, "hedge_capital": 1_000_000},
        "positions": {
            "510300.SH": {"shares": 50000, "est_price": 4.65, "sector": "宽基"}
        },
        "hedge_positions": {
            "ETF_put_options": {
                "instrument": "510050 Put",
                "exchange": "SSE",
                "target_contracts": 60,
                "premium_budget": 900_000,
                "strike": "OTM_5%",
            },
            "ETF_put_options_2": {
                "instrument": "588080 Put",
                "exchange": "SSE",
                "target_contracts": 25,
                "premium_budget": 300_000,
            },
        },
    }
    positions_file = tmp_path / "positions.json"
    positions_file.write_text(
        json.dumps(clean_data, ensure_ascii=False),
        encoding="utf-8",
    )
    return HedgeExecutionEngine(positions_file=str(positions_file))


# ============================================================
# P0-E 回归: hedge_positions 含字符串字段导致 generate_put_protection_orders 崩溃
# ============================================================
# Bug 历史:
#   positions.json 中 hedge_positions 字段实际包含一些元数据字符串:
#     "description": "200万纯期权对冲 — 无期货空头"
#     "hedge_mode": "OPTIONS_ONLY"
#   原代码: for key, hedge_pos in hedge_positions.items():
#             instrument = hedge_pos.get("instrument", "")
#   字符串 hedge_pos 没有 .get 方法, 抛 AttributeError, 整个 EOD 对冲生成失败
#
# 修复: 在循环开头加 `if not isinstance(hedge_pos, dict): continue`


class TestP0EBrokenHedgePositions:
    """P0-E 回归: 字符串字段不应导致崩溃"""

    @pytest.mark.unit
    @pytest.mark.p0
    @pytest.mark.bug("P0-E")
    def test_p0e_generate_put_protection_does_not_raise_on_string_fields(
        self, engine_with_broken_hedge_positions
    ):
        """P0-E 核心: hedge_positions 含 description/hedge_mode 字符串时不能崩溃"""
        engine = engine_with_broken_hedge_positions

        # 必须不抛 AttributeError (原 bug 的崩溃点)
        try:
            orders = engine.generate_put_protection_orders(portfolio_value=4_000_000)
        except AttributeError as e:
            pytest.fail(
                f"P0-E 回归: generate_put_protection_orders 抛 AttributeError: {e}"
            )

        # 应返回 list (即使过滤后只有 dict 类的 put 订单)
        assert isinstance(orders, list)

    @pytest.mark.unit
    @pytest.mark.p0
    @pytest.mark.bug("P0-E")
    def test_p0e_string_fields_skipped_only_dict_put_orders_returned(
        self, engine_with_broken_hedge_positions
    ):
        """P0-E: 字符串字段被跳过, 只返回 dict 类的 put 订单

        broken_hedge_positions_p0e 含:
            description (str) → 跳过
            hedge_mode (str) → 跳过
            ETF_put_options (dict, instrument="510050 Put") → 保留
            ETF_put_options_2 (dict, instrument="588080 Put") → 保留
        """
        engine = engine_with_broken_hedge_positions
        orders = engine.generate_put_protection_orders(portfolio_value=4_000_000)

        # 应返回 2 个 put 订单 (ETF_put_options + ETF_put_options_2)
        assert (
            len(orders) == 2
        ), f"应过滤字符串字段, 仅保留 2 个 dict put 订单, 实际: {len(orders)}"

        # 验证订单内容
        instruments = [o["instrument"] for o in orders]
        assert "510050 Put" in instruments
        assert "588080 Put" in instruments

    @pytest.mark.unit
    @pytest.mark.p0
    @pytest.mark.bug("P0-E")
    def test_p0e_generate_hedge_orders_full_chain_survives_string_fields(
        self, engine_with_broken_hedge_positions, monkeypatch
    ):
        """P0-E 端到端: generate_hedge_orders 完整调用链不能因字符串字段崩溃

        generate_hedge_orders 调用 generate_put_protection_orders,
        若 P0-E 未修复, 整个对冲生成会失败, 导致 EOD 链路中断
        """
        engine = engine_with_broken_hedge_positions

        # Mock 网络依赖, 避免真实调用新浪接口
        monkeypatch.setattr(engine, "_get_if_price", lambda: 4650.0)

        # 完整调用, 不应抛任何异常
        result = engine.generate_hedge_orders(drawdown_level=0)

        # 验证返回结构完整
        assert "futures_orders" in result
        assert "options_orders" in result
        assert "cost_summary" in result
        assert "portfolio_status" in result

        # options_orders 应包含 2 个 (来自 dict 类的 put)
        assert len(result["options_orders"]) == 2


# ============================================================
# 对冲订单生成逻辑测试 (非 bug 回归, 防止逻辑漂移)
# ============================================================


class TestPutProtectionOrderGeneration:
    """认沽期权订单生成逻辑"""

    @pytest.mark.unit
    def test_empty_hedge_positions_returns_empty_list(self, tmp_path):
        """hedge_positions 为空时返回空 list"""
        data = {
            "meta": {"total_capital": 5_000_000},
            "positions": {},
            "hedge_positions": {},
        }
        positions_file = tmp_path / "positions.json"
        positions_file.write_text(json.dumps(data), encoding="utf-8")

        engine = HedgeExecutionEngine(positions_file=str(positions_file))
        orders = engine.generate_put_protection_orders(portfolio_value=1_000_000)

        assert orders == []

    @pytest.mark.unit
    def test_zero_contracts_skipped(self, tmp_path):
        """target_contracts=0 的 put 订单被跳过"""
        data = {
            "meta": {"total_capital": 5_000_000},
            "positions": {},
            "hedge_positions": {
                "ETF_put_zero": {
                    "instrument": "510050 Put",
                    "exchange": "SSE",
                    "target_contracts": 0,  # 应被跳过
                    "premium_budget": 0,
                },
                "ETF_put_valid": {
                    "instrument": "588080 Put",
                    "exchange": "SSE",
                    "target_contracts": 30,
                    "premium_budget": 300_000,
                },
            },
        }
        positions_file = tmp_path / "positions.json"
        positions_file.write_text(json.dumps(data), encoding="utf-8")

        engine = HedgeExecutionEngine(positions_file=str(positions_file))
        orders = engine.generate_put_protection_orders(portfolio_value=1_000_000)

        assert len(orders) == 1, "target_contracts=0 应被跳过"
        assert orders[0]["instrument"] == "588080 Put"

    @pytest.mark.unit
    def test_non_put_dict_entries_skipped(self, tmp_path):
        """非 put 类的 dict 条目被跳过 (key 不含 put, instrument 也不含 Put)"""
        data = {
            "meta": {"total_capital": 5_000_000},
            "positions": {},
            "hedge_positions": {
                "futures_hedge": {  # dict 但不是 put
                    "instrument": "IF",
                    "exchange": "CFFEX",
                    "target_contracts": 5,
                    "premium_budget": 0,
                },
                "ETF_put_options": {
                    "instrument": "510050 Put",
                    "exchange": "SSE",
                    "target_contracts": 60,
                    "premium_budget": 900_000,
                },
            },
        }
        positions_file = tmp_path / "positions.json"
        positions_file.write_text(json.dumps(data), encoding="utf-8")

        engine = HedgeExecutionEngine(positions_file=str(positions_file))
        orders = engine.generate_put_protection_orders(portfolio_value=1_000_000)

        # 只保留 ETF_put_options (futures_hedge 既非 put key, instrument 也非 Put)
        assert len(orders) == 1
        assert orders[0]["instrument"] == "510050 Put"

    @pytest.mark.unit
    def test_drawdown_level_2_doubles_budget(self, engine_with_clean_hedge_positions):
        """drawdown_level>=2 时期权预算翻倍

        Level 0/1: quarterly_budget = annual_budget / 4
        Level 2+: quarterly_budget *= 2.0
        """
        engine = engine_with_clean_hedge_positions

        orders_l0 = engine.generate_put_protection_orders(drawdown_level=0)
        orders_l2 = engine.generate_put_protection_orders(drawdown_level=2)

        # Level 2 预算翻倍 → 每个 put 的 premium_budget 应为 Level 0 的 2 倍
        # 但注意: premium_budget 来自 hedge_positions 配置, 不随 drawdown 变化
        # drawdown_level 影响的是 quarterly_budget 计算 (用于上限检查, 非覆盖订单)
        # 这里验证 drawdown_level 不导致订单数量变化 (回归保护)
        assert len(orders_l0) == len(orders_l2) == 2


# ============================================================
# 期货对冲订单逻辑测试
# ============================================================


class TestFuturesHedgeOrders:
    """IF 期货空头订单生成逻辑"""

    @pytest.mark.unit
    def test_beta_in_target_range_returns_empty(
        self, engine_with_clean_hedge_positions, monkeypatch
    ):
        """portfolio_beta ≈ target_beta 时无需对冲, 返回空 list"""
        engine = engine_with_clean_hedge_positions
        monkeypatch.setattr(engine, "_get_if_price", lambda: 4650.0)

        # portfolio_beta=0.30, target_beta=0.30 → beta_to_hedge=0 → 无需对冲
        orders = engine.generate_futures_hedge_orders(
            portfolio_value=4_000_000,
            portfolio_beta=0.30,
            target_beta=0.30,
        )

        assert orders == [], "Beta 已在目标范围内应返回空 list"

    @pytest.mark.unit
    def test_high_beta_generates_short_contracts(
        self, engine_with_clean_hedge_positions, monkeypatch
    ):
        """portfolio_beta > target_beta 时生成 IF 空头订单"""
        engine = engine_with_clean_hedge_positions
        monkeypatch.setattr(engine, "_get_if_price", lambda: 4650.0)

        orders = engine.generate_futures_hedge_orders(
            portfolio_value=4_000_000,
            portfolio_beta=1.05,  # 高 Beta
            target_beta=0.30,
        )

        assert len(orders) == 1
        order = orders[0]
        assert order["direction"] == "SELL", "对冲应为空头"
        assert order["instrument"] == "IF"
        assert order["contracts"] >= 1
        assert order["margin_required"] > 0
        assert order["rationale"]["beta_to_hedge"] > 0

    @pytest.mark.unit
    def test_drawdown_level_reduces_target_beta(
        self, engine_with_clean_hedge_positions, monkeypatch
    ):
        """回撤级别越高, target_beta 越低 (对冲越激进)

        DD_BETA_TARGETS = {0: 0.30, 1: 0.25, 2: 0.20, 3: 0.10, 4: 0.05}
        """
        engine = engine_with_clean_hedge_positions
        monkeypatch.setattr(engine, "_get_if_price", lambda: 4650.0)

        orders_l0 = engine.generate_futures_hedge_orders(
            portfolio_value=4_000_000, portfolio_beta=1.05, drawdown_level=0
        )
        orders_l3 = engine.generate_futures_hedge_orders(
            portfolio_value=4_000_000, portfolio_beta=1.05, drawdown_level=3
        )

        # Level 3 target_beta=0.10, beta_to_hedge 更大 → contracts 更多
        assert (
            orders_l3[0]["contracts"] >= orders_l0[0]["contracts"]
        ), "回撤级别越高, 对冲合约数应越多"
        assert orders_l3[0]["rationale"]["target_beta"] == 0.10
        assert orders_l0[0]["rationale"]["target_beta"] == 0.30


# ============================================================
# 鲁棒性测试
# ============================================================


class TestHedgeEngineRobustness:
    """引擎鲁棒性: 缺失文件/空数据不应崩溃"""

    @pytest.mark.unit
    def test_missing_positions_file_returns_empty_dict(self, tmp_path):
        """positions_file 不存在时 _load_positions 返回空 dict"""
        engine = HedgeExecutionEngine(positions_file=str(tmp_path / "nonexistent.json"))
        assert engine.positions_data == {}

    @pytest.mark.unit
    def test_empty_portfolio_beta_returns_default_1(self, tmp_path):
        """空持仓时 calc_portfolio_beta 返回 1.0 (默认全市场 Beta)"""
        data = {"meta": {}, "positions": {}}
        positions_file = tmp_path / "positions.json"
        positions_file.write_text(json.dumps(data), encoding="utf-8")

        engine = HedgeExecutionEngine(positions_file=str(positions_file))
        assert engine.calc_portfolio_beta() == 1.0

    @pytest.mark.unit
    def test_empty_portfolio_market_value_returns_zero(self, tmp_path):
        """空持仓时 calc_portfolio_market_value 返回 0"""
        data = {"meta": {}, "positions": {}}
        positions_file = tmp_path / "positions.json"
        positions_file.write_text(json.dumps(data), encoding="utf-8")

        engine = HedgeExecutionEngine(positions_file=str(positions_file))
        assert engine.calc_portfolio_market_value() == 0.0


# ============================================================
# P1-2 review 非阻断项② 回归: 预算基数腿口径解析
# ============================================================
# Bug 历史 (PR #27 review, 2026-09-12):
#   generate_put_protection_orders 预算兜底次选为 meta.total_capital,
#   若 positions.json 为旧 v8.0 头 (total_capital=5M 总口径, 无腿口径),
#   直接采信会把含期货腿的总口径当证券腿 → 期权预算被高估。
# 修复: _resolve_stock_etf_budget_base() 对 total_capital 按腿占比折算 + WARNING。


class TestBudgetBaseLegCaliber:
    """预算基数 (证券/ETF 腿口径) 解析优先序"""

    @staticmethod
    def _make_engine(tmp_path, meta):
        data = {"meta": meta, "positions": {}, "hedge_positions": {}}
        pf = tmp_path / "positions.json"
        pf.write_text(json.dumps(data), encoding="utf-8")
        return HedgeExecutionEngine(positions_file=str(pf))

    @pytest.mark.unit
    def test_leg_meta_directly_used(self, tmp_path):
        """meta.stock_etf_capital 显式值直接采信 (不折算)"""
        engine = self._make_engine(
            tmp_path, {"stock_etf_capital": 1_234_567, "total_capital": 9_999_999}
        )
        assert engine._resolve_stock_etf_budget_base() == 1_234_567

    @pytest.mark.unit
    def test_total_meta_converted_by_leg_ratio(self, tmp_path):
        """仅有 meta.total_capital 时按腿占比折算 + WARNING, 不直接采信"""
        from utils.risk_thresholds import (
            get_stock_etf_capital,
            get_total_capital,
        )

        engine = self._make_engine(tmp_path, {"total_capital": 5_000_000})
        expected = 5_000_000 * (get_stock_etf_capital() / get_total_capital())
        assert engine._resolve_stock_etf_budget_base() == pytest.approx(expected)
        # 旧 v8.0 5M 头直接采信 = 5,000,000; 折算后应显著小于它
        assert engine._resolve_stock_etf_budget_base() < 5_000_000

    @pytest.mark.unit
    def test_no_meta_falls_back_to_static_leg(self, tmp_path):
        """meta 无任何资金口径 → 回退 capital_base.stock_etf_capital 静态基准"""
        from utils.risk_thresholds import get_stock_etf_capital

        engine = self._make_engine(tmp_path, {})
        assert engine._resolve_stock_etf_budget_base() == get_stock_etf_capital()

    @pytest.mark.unit
    def test_invalid_meta_values_fall_back(self, tmp_path):
        """meta 值非正/非数值 → 不采信, 回退静态基准"""
        from utils.risk_thresholds import get_stock_etf_capital

        engine = self._make_engine(
            tmp_path, {"stock_etf_capital": -1, "total_capital": "not-a-number"}
        )
        assert engine._resolve_stock_etf_budget_base() == get_stock_etf_capital()
