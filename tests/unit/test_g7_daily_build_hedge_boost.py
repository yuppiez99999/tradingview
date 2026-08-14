"""G7 覆盖率冲刺 — daily_build_and_hedge 补测试.

目标模块: utils/execution/daily_build_and_hedge.py (1170行, 6.29%→目标50%+)

覆盖核心路径:
    - DailyBuildHedgeSystem: __init__/_load_plan/get_active_phase/
      assess_market_state/_fetch_index_returns/calculate_risk_budget/
      generate_build_instructions/calculate_hedge_plan/
      _load_target_portfolio_plan/fetch_realtime_quotes/
      generate_report/_render_*/save_report/run

运行:
    python -m pytest tests/unit/test_g7_daily_build_hedge_boost.py -v
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, mock_open, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from utils.execution.daily_build_and_hedge import DailyBuildHedgeSystem


# ============================================================
# 测试数据
# ============================================================

SAMPLE_PLAN_DATA: dict[str, Any] = {
    "meta": {
        "total_capital": 5_000_000,
        "stock_etf_capital": 4_000_000,
        "hedge_capital": 1_000_000,
    },
    "execution_plan": {
        "phase1": {
            "name": "底仓建立与收租期",
            "phase": "建仓",
            "start_date": "2026-07-13",
            "end_date": "2026-07-24",
            "target_percentage": 0.60,
            "daily_limit": 250_000,
            "sectors": ["etf", "tech", "finance"],
        },
        "phase2": {
            "name": "加仓与对冲博弈期",
            "phase": "博弈",
            "start_date": "2026-07-27",
            "end_date": "2028-12-31",
            "target_percentage": 0.80,
            "daily_limit": 200_000,
            "sectors": ["new_energy", "pharma"],
        },
        "phase3": {
            "name": "动态再平衡期",
            "phase": "再平衡",
            "start_date": "2029-01-01",
            "end_date": "2030-06-30",
            "target_percentage": 0.50,
            "daily_limit": 150_000,
        },
        "phase4": {
            "name": "清仓退出期",
            "phase": "退出",
            "start_date": "2030-07-01",
            "end_date": "2030-12-31",
            "target_percentage": 0.0,
            "daily_limit": 300_000,
        },
    },
    "stock_etf_account": {
        "capital": 4_000_000,
        "positions": [
            {
                "code": "588080.SH",
                "name": "科创50ETF易方达",
                "target_weight": 0.05,
                "amount": 200_000,
                "style": "科技",
            },
            {
                "code": "512880.SH",
                "name": "证券ETF国泰",
                "target_weight": 0.05,
                "amount": 200_000,
                "style": "金融",
            },
        ],
    },
    "dynamic_rebalance": {
        "delta_control": {
            "target_range": {
                "normal": 0.6,
                "bull": 0.8,
                "cautious": 0.4,
                "bear": 0.2,
            },
        }
    },
    "hedge_account": {
        "modules": [
            {
                "name": "risk_reversal_collar",
                "underlyings": [
                    {
                        "code": "510050.SH",
                        "name": "上证50ETF华夏",
                        "direction": "BUY",
                        "strike": 2.80,
                        "premium_budget": 30_000,
                        "purpose": "尾部保护",
                    }
                ],
            },
            {
                "name": "vega_event_driven",
                "underlyings": [
                    {
                        "code": "588080.SH",
                        "name": "科创50ETF易方达",
                        "strategy": "long_straddle",
                        "condition": "vol_breakout",
                        "purpose": "波动率事件驱动",
                    }
                ],
            },
        ]
    },
}


# ============================================================
# 辅助函数
# ============================================================


def _make_system(
    target_date: date | None = None,
    dry_run: bool = False,
    plan_data: dict[str, Any] | None = None,
) -> DailyBuildHedgeSystem:
    """构造 DailyBuildHedgeSystem, 跳过真实 _load_plan 文件 IO."""
    with patch.object(DailyBuildHedgeSystem, "_load_plan", lambda self: None):
        system = DailyBuildHedgeSystem(target_date=target_date, dry_run=dry_run)
    if plan_data is not None:
        system.plan_data = plan_data
    return system


@pytest.fixture
def configured_system() -> DailyBuildHedgeSystem:
    """返回一个 state 已预填的 system (market_state/build_plan/hedge_plan/risk_status)."""
    system = _make_system(
        target_date=date(2026, 7, 13),
        dry_run=True,
        plan_data=SAMPLE_PLAN_DATA,
    )
    system.market_state = {
        "date": "2026-07-13",
        "vix_proxy": 18.5,
        "index_return_20d": 0.02,
        "index_return_5d": 0.005,
        "data_degraded": False,
        "vix_source": "live",
        "margin_balance_change": 0.005,
        "sector_health": {"high_end_manufacturing_20d": 0.03},
        "etf_flows": {},
        "macro_heat_score": 55,
        "macro_regime": "中性",
        "market_regime": "neutral",
        "etf_flow_decision": None,
    }
    system.build_plan = {
        "trade_date": "2026-07-13",
        "phase": "底仓建立与收租期",
        "phase_key": "phase1",
        "total_capital": 5_000_000,
        "day_capital": 250_000,
        "capital_multiplier": 1.0,
        "emergency_level": "NORMAL",
        "morning_orders": [
            {
                "priority": 1,
                "code": "588080.SH",
                "name": "科创50ETF易方达",
                "shares": 1000,
                "est_price": 1.05,
                "limit_price": 1.06,
                "est_amount": 1050,
                "style": "科技",
                "risk": "低",
                "note": "建仓",
            }
        ],
        "afternoon_orders": [
            {
                "priority": 2,
                "code": "512880.SH",
                "name": "证券ETF国泰",
                "shares": 500,
                "est_price": 1.20,
                "limit_price": 1.21,
                "est_amount": 600,
                "style": "金融",
                "risk": "低",
                "note": "建仓",
            }
        ],
        "paused_orders": [
            {"code": "510050.SH", "name": "上证50ETF", "reason": "价格异常"}
        ],
        "warnings": [],
        "emergency_actions": ["监控市场流动性"],
    }
    system.hedge_plan = {
        "portfolio_value": 1_000_000,
        "portfolio_beta": 1.10,
        "market_regime": "neutral",
        "target_delta": 0.6,
        "current_delta": 600_000,
        "current_gamma": 0.05,
        "current_vega": 5000,
        "current_theta": -500,
        "futures_hedge": {
            "instrument": "IF_futures",
            "direction": "SELL",
            "contracts": 5.0,
            "multiplier": 300,
            "estimated_notional": 6_750_000,
            "description": "对冲组合 Beta 1.10，目标 Delta 0.6",
        },
        "option_hedge": [
            {
                "type": "PUT_OPTION",
                "code": "510050.SH",
                "name": "上证50ETF华夏",
                "direction": "BUY",
                "strike": 2.80,
                "premium_budget": 30_000,
                "purpose": "尾部保护",
            }
        ],
        "rebalance_signal": {"delta_rebalance": True, "gamma_rebalance": False},
    }
    system.risk_status = {
        "total_capital": 5_000_000,
        "stock_capital": 4_000_000,
        "daily_limit": 250_000,
        "target_percentage": 0.6,
        "allocation": {"daily_budget": 250_000},
        "market_regime": "neutral",
    }
    return system


# ============================================================
# 1. __init__ / _load_plan 测试
# ============================================================


class TestInit:
    """DailyBuildHedgeSystem 初始化测试."""

    def test_init_default(self) -> None:
        system = _make_system()
        assert system.target_date == date.today()
        assert system.dry_run is False
        assert system.plan_data == {}
        assert system.market_state == {}
        assert system.build_plan == {}
        assert system.hedge_plan == {}
        assert system.risk_status == {}
        assert system.stock_positions == {}
        assert system.holdings == {}
        assert system.prices == {}

    def test_init_with_date_and_dry_run(self) -> None:
        system = _make_system(target_date=date(2026, 7, 13), dry_run=True)
        assert system.target_date == date(2026, 7, 13)
        assert system.dry_run is True

    def test_init_calls_load_plan(self) -> None:
        """__init__ 必须调用 _load_plan."""
        with patch.object(
            DailyBuildHedgeSystem, "_load_plan"
        ) as mock_load:
            DailyBuildHedgeSystem(target_date=date(2026, 1, 1))
        mock_load.assert_called_once()


class TestLoadPlan:
    """_load_plan 方法测试."""

    def test_load_plan_success(self, tmp_path: Path) -> None:
        """计划文件存在且可读."""
        plan_dir = tmp_path / "trade_plans"
        plan_dir.mkdir()
        plan_file = plan_dir / "auto_trade_plan_500w_2026-2030.json"
        plan_file.write_text(
            json.dumps({"meta": {"total_capital": 100}}), encoding="utf-8"
        )

        system = _make_system()
        with patch(
            "utils.execution.daily_build_and_hedge.BASE_DIR", tmp_path
        ):
            system._load_plan()
        assert system.plan_data == {"meta": {"total_capital": 100}}

    def test_load_plan_file_not_found(self, tmp_path: Path) -> None:
        """两个候选路径都不存在."""
        system = _make_system()
        system.plan_data = {"existing": True}
        with patch(
            "utils.execution.daily_build_and_hedge.BASE_DIR", tmp_path
        ):
            system._load_plan()
        # plan_data 不变 (未找到文件时不覆盖)
        assert system.plan_data == {"existing": True}

    def test_load_plan_invalid_json(self, tmp_path: Path) -> None:
        """计划文件存在但 JSON 无效."""
        plan_dir = tmp_path / "trade_plans"
        plan_dir.mkdir()
        plan_file = plan_dir / "auto_trade_plan_500w_2026-2030.json"
        plan_file.write_text("not valid json {{{", encoding="utf-8")

        system = _make_system()
        with patch(
            "utils.execution.daily_build_and_hedge.BASE_DIR", tmp_path
        ):
            system._load_plan()
        # JSON 解析失败, plan_data 保持空
        assert system.plan_data == {}

    def test_load_plan_falls_back_to_backup(self, tmp_path: Path) -> None:
        """主路径不存在时回退到 backup 路径."""
        # 不创建 trade_plans 目录, 只创建 backup 文件
        backup_file = tmp_path / "500万建仓计划_20260706.json"
        backup_file.write_text(
            json.dumps({"backup": True}), encoding="utf-8"
        )

        system = _make_system()
        with patch(
            "utils.execution.daily_build_and_hedge.BASE_DIR", tmp_path
        ):
            system._load_plan()
        assert system.plan_data == {"backup": True}


# ============================================================
# 2. get_active_phase 测试
# ============================================================


class TestGetActivePhase:
    """get_active_phase 方法测试."""

    def test_active_phase1(self) -> None:
        system = _make_system(
            target_date=date(2026, 7, 15), plan_data=SAMPLE_PLAN_DATA
        )
        phase, key = system.get_active_phase()
        assert key == "phase1"
        assert phase is not None
        assert phase["name"] == "底仓建立与收租期"

    def test_active_phase2(self) -> None:
        system = _make_system(
            target_date=date(2027, 6, 1), plan_data=SAMPLE_PLAN_DATA
        )
        phase, key = system.get_active_phase()
        assert key == "phase2"
        assert phase is not None
        assert phase["name"] == "加仓与对冲博弈期"

    def test_no_active_phase_completed(self) -> None:
        """日期在所有阶段之后 -> completed."""
        system = _make_system(
            target_date=date(2031, 1, 1), plan_data=SAMPLE_PLAN_DATA
        )
        phase, key = system.get_active_phase()
        assert phase is None
        assert key == "completed"

    def test_date_before_all_phases(self) -> None:
        system = _make_system(
            target_date=date(2026, 1, 1), plan_data=SAMPLE_PLAN_DATA
        )
        phase, key = system.get_active_phase()
        assert phase is None
        assert key == "completed"

    def test_empty_plan(self) -> None:
        system = _make_system(plan_data={})
        phase, key = system.get_active_phase()
        assert phase is None
        assert key == "completed"

    def test_invalid_date_string_in_phase(self) -> None:
        """阶段日期格式无效时跳过该阶段."""
        bad_plan = {
            "execution_plan": {
                "phase1": {
                    "name": "坏阶段",
                    "start_date": "not-a-date",
                    "end_date": "also-bad",
                }
            }
        }
        system = _make_system(
            target_date=date(2026, 7, 15), plan_data=bad_plan
        )
        phase, key = system.get_active_phase()
        assert phase is None
        assert key == "completed"

    def test_phase_missing_dates(self) -> None:
        """阶段缺少 start_date/end_date 时跳过."""
        no_dates_plan = {
            "execution_plan": {
                "phase1": {"name": "无日期阶段"},
            }
        }
        system = _make_system(
            target_date=date(2026, 7, 15), plan_data=no_dates_plan
        )
        phase, key = system.get_active_phase()
        assert phase is None
        assert key == "completed"


# ============================================================
# 3. assess_market_state 测试
# ============================================================


class TestAssessMarketState:
    """assess_market_state 方法测试."""

    @patch("utils.alpha.vix_data_source.fetch_vix", return_value=20.0)
    @patch("utils.etf_flow_decision.ETFFlowDecisionEngine")
    @patch("utils.etf_flow_monitor.ETFMonitor", create=True)
    def test_normal_market(
        self,
        mock_etf_monitor_cls: MagicMock,
        mock_decision_cls: MagicMock,
        mock_fetch_vix: MagicMock,
    ) -> None:
        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        mock_etf_monitor_cls.return_value.get_summary.return_value = {}
        mock_decision_cls.return_value.pre_market_decision.return_value = {
            "status": "success"
        }
        with patch.object(
            system,
            "_fetch_index_returns",
            return_value={5: 0.005, 20: 0.02},
        ):
            result = system.assess_market_state()

        assert result["market_regime"] == "neutral"
        assert result["vix_proxy"] == 20.0
        assert result["vix_source"] == "live"
        assert result["data_degraded"] is False
        assert result["index_return_20d"] == 0.02
        assert result["index_return_5d"] == 0.005
        assert result["etf_flow_decision"] is not None

    @patch("utils.alpha.vix_data_source.fetch_vix", return_value=45.0)
    @patch("utils.etf_flow_decision.ETFFlowDecisionEngine")
    @patch("utils.etf_flow_monitor.ETFMonitor", create=True)
    def test_bear_market_high_vix(
        self,
        mock_etf_monitor_cls: MagicMock,
        mock_decision_cls: MagicMock,
        mock_fetch_vix: MagicMock,
    ) -> None:
        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        mock_etf_monitor_cls.return_value.get_summary.return_value = {}
        mock_decision_cls.return_value.pre_market_decision.return_value = None
        with patch.object(
            system, "_fetch_index_returns", return_value={5: 0.01, 20: 0.03}
        ):
            result = system.assess_market_state()
        assert result["market_regime"] == "bear"

    @patch("utils.alpha.vix_data_source.fetch_vix", return_value=32.0)
    @patch("utils.etf_flow_decision.ETFFlowDecisionEngine")
    @patch("utils.etf_flow_monitor.ETFMonitor", create=True)
    def test_cautious_market(
        self,
        mock_etf_monitor_cls: MagicMock,
        mock_decision_cls: MagicMock,
        mock_fetch_vix: MagicMock,
    ) -> None:
        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        mock_etf_monitor_cls.return_value.get_summary.return_value = {}
        mock_decision_cls.return_value.pre_market_decision.return_value = None
        with patch.object(
            system, "_fetch_index_returns", return_value={5: 0.01, 20: 0.03}
        ):
            result = system.assess_market_state()
        assert result["market_regime"] == "cautious"

    @patch("utils.alpha.vix_data_source.fetch_vix", return_value=15.0)
    @patch("utils.etf_flow_decision.ETFFlowDecisionEngine")
    @patch("utils.etf_flow_monitor.ETFMonitor", create=True)
    def test_bull_market(
        self,
        mock_etf_monitor_cls: MagicMock,
        mock_decision_cls: MagicMock,
        mock_fetch_vix: MagicMock,
    ) -> None:
        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        mock_etf_monitor_cls.return_value.get_summary.return_value = {}
        mock_decision_cls.return_value.pre_market_decision.return_value = None
        with patch.object(
            system,
            "_fetch_index_returns",
            return_value={5: 0.02, 20: 0.12},
        ):
            result = system.assess_market_state()
        assert result["market_regime"] == "bull"

    @patch("utils.alpha.vix_data_source.fetch_vix", return_value=None)
    @patch("utils.etf_flow_decision.ETFFlowDecisionEngine")
    @patch("utils.etf_flow_monitor.ETFMonitor", create=True)
    def test_data_degraded_vix_none(
        self,
        mock_etf_monitor_cls: MagicMock,
        mock_decision_cls: MagicMock,
        mock_fetch_vix: MagicMock,
    ) -> None:
        """VIX 返回 None -> data_degraded, market_regime cautious."""
        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        mock_etf_monitor_cls.return_value.get_summary.return_value = {}
        mock_decision_cls.return_value.pre_market_decision.return_value = None
        with patch.object(
            system, "_fetch_index_returns", return_value={5: 0.01, 20: 0.02}
        ):
            result = system.assess_market_state()
        assert result["data_degraded"] is True
        assert result["market_regime"] == "cautious"
        assert result["vix_source"] == "degraded_default"

    @patch("utils.alpha.vix_data_source.fetch_vix", return_value=200.0)
    @patch("utils.etf_flow_decision.ETFFlowDecisionEngine")
    @patch("utils.etf_flow_monitor.ETFMonitor", create=True)
    def test_data_degraded_vix_out_of_range(
        self,
        mock_etf_monitor_cls: MagicMock,
        mock_decision_cls: MagicMock,
        mock_fetch_vix: MagicMock,
    ) -> None:
        """VIX 越界 (>150) -> data_degraded."""
        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        mock_etf_monitor_cls.return_value.get_summary.return_value = {}
        mock_decision_cls.return_value.pre_market_decision.return_value = None
        with patch.object(
            system, "_fetch_index_returns", return_value={5: 0.01, 20: 0.02}
        ):
            result = system.assess_market_state()
        assert result["data_degraded"] is True
        assert result["market_regime"] == "cautious"

    @patch("utils.alpha.vix_data_source.fetch_vix", side_effect=RuntimeError("net"))
    @patch("utils.etf_flow_decision.ETFFlowDecisionEngine")
    @patch("utils.etf_flow_monitor.ETFMonitor", create=True)
    def test_data_degraded_index_returns_none(
        self,
        mock_etf_monitor_cls: MagicMock,
        mock_decision_cls: MagicMock,
        mock_fetch_vix: MagicMock,
    ) -> None:
        """指数收益率 None -> data_degraded, cautious."""
        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        mock_etf_monitor_cls.return_value.get_summary.return_value = {}
        mock_decision_cls.return_value.pre_market_decision.return_value = None
        with patch.object(
            system, "_fetch_index_returns", return_value=None
        ):
            result = system.assess_market_state()
        assert result["data_degraded"] is True
        assert result["market_regime"] == "cautious"
        assert result["index_return_20d"] is None
        assert result["index_return_5d"] is None

    @patch("utils.alpha.vix_data_source.fetch_vix", return_value=20.0)
    @patch("utils.etf_flow_decision.ETFFlowDecisionEngine")
    @patch("utils.etf_flow_monitor.ETFMonitor", create=True)
    def test_bear_market_ret_20d_crash(
        self,
        mock_etf_monitor_cls: MagicMock,
        mock_decision_cls: MagicMock,
        mock_fetch_vix: MagicMock,
    ) -> None:
        """20日收益率 <= -0.15 -> bear."""
        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        mock_etf_monitor_cls.return_value.get_summary.return_value = {}
        mock_decision_cls.return_value.pre_market_decision.return_value = None
        with patch.object(
            system,
            "_fetch_index_returns",
            return_value={5: -0.02, 20: -0.18},
        ):
            result = system.assess_market_state()
        assert result["market_regime"] == "bear"


# ============================================================
# 4. _fetch_index_returns 测试
# ============================================================


class TestFetchIndexReturns:
    """_fetch_index_returns 方法测试."""

    def test_timeout_returns_none(self) -> None:
        """wind_get_index_data 抛 TimeoutError 时返回 None."""
        mock_module = MagicMock()
        mock_module.wind_get_index_data = MagicMock(
            side_effect=TimeoutError("timeout")
        )
        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        with patch.dict("sys.modules", {"wind_mcp_fetcher": mock_module}):
            result = system._fetch_index_returns("000300.SH")
        assert result is None

    def test_success_with_valid_data(self) -> None:
        """Wind MCP 返回有效数据."""
        import pandas as pd

        closes = [100.0 + i for i in range(25)]  # 25 个收盘价
        df = pd.DataFrame({"close": closes})

        mock_module = MagicMock()
        mock_module.wind_get_index_data = MagicMock(return_value=df)

        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        with patch.dict("sys.modules", {"wind_mcp_fetcher": mock_module}):
            result = system._fetch_index_returns("000300.SH")

        assert result is not None
        assert 5 in result
        assert 20 in result
        assert isinstance(result[5], float)
        assert isinstance(result[20], float)

    def test_insufficient_data_returns_none(self) -> None:
        """数据行数不足 21 行 -> None."""
        import pandas as pd

        df = pd.DataFrame({"close": [100.0, 101.0, 102.0]})

        mock_module = MagicMock()
        mock_module.wind_get_index_data = MagicMock(return_value=df)

        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        with patch.dict("sys.modules", {"wind_mcp_fetcher": mock_module}):
            result = system._fetch_index_returns("000300.SH")
        assert result is None

    def test_df_is_none(self) -> None:
        """wind_get_index_data 返回 None."""
        mock_module = MagicMock()
        mock_module.wind_get_index_data = MagicMock(return_value=None)

        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        with patch.dict("sys.modules", {"wind_mcp_fetcher": mock_module}):
            result = system._fetch_index_returns("000300.SH")
        assert result is None

    def test_close_column_missing(self) -> None:
        """df 不含 close 列 -> None."""
        import pandas as pd

        df = pd.DataFrame({"price": list(range(25))})
        mock_module = MagicMock()
        mock_module.wind_get_index_data = MagicMock(return_value=df)

        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        with patch.dict("sys.modules", {"wind_mcp_fetcher": mock_module}):
            result = system._fetch_index_returns("000300.SH")
        assert result is None

    def test_runtime_error_returns_none(self) -> None:
        """wind_get_index_data 抛异常 -> None."""
        mock_module = MagicMock()
        mock_module.wind_get_index_data = MagicMock(
            side_effect=RuntimeError("timeout")
        )

        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        with patch.dict("sys.modules", {"wind_mcp_fetcher": mock_module}):
            result = system._fetch_index_returns("000300.SH")
        assert result is None


# ============================================================
# 5. calculate_risk_budget 测试
# ============================================================


class TestCalculateRiskBudget:
    """calculate_risk_budget 方法测试."""

    @patch("utils.risk_budget_allocator.RiskBudgetAllocator")
    def test_basic(self, mock_allocator_cls: MagicMock) -> None:
        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        system.market_state = {"market_regime": "neutral"}

        mock_allocator = MagicMock()
        mock_allocator_cls.return_value = mock_allocator
        mock_allocator.allocate_daily_budget.return_value = {
            "daily_budget": 250_000,
            "allocations": [],
        }

        phase = SAMPLE_PLAN_DATA["execution_plan"]["phase1"]
        result = system.calculate_risk_budget(phase)

        assert result["total_capital"] == 5_000_000
        assert result["stock_capital"] == 4_000_000
        assert result["daily_limit"] == 250_000
        assert result["target_percentage"] == 0.60
        assert result["market_regime"] == "neutral"
        assert result["allocation"]["daily_budget"] == 250_000
        # 验证 allocator 构造参数
        mock_allocator_cls.assert_called_once()
        call_kwargs = mock_allocator_cls.call_args
        assert call_kwargs.kwargs["daily_budget_limit"] == 250_000

    @patch("utils.risk_budget_allocator.RiskBudgetAllocator")
    def test_empty_positions(self, mock_allocator_cls: MagicMock) -> None:
        """空 positions 列表."""
        empty_plan = {
            "meta": {"total_capital": 1_000_000, "stock_etf_capital": 800_000},
            "stock_etf_account": {"positions": []},
        }
        system = _make_system(plan_data=empty_plan)
        system.market_state = {"market_regime": "cautious"}

        mock_allocator = MagicMock()
        mock_allocator_cls.return_value = mock_allocator
        mock_allocator.allocate_daily_budget.return_value = {}

        result = system.calculate_risk_budget(
            {"daily_limit": 100_000, "target_percentage": 0.3}
        )
        assert result["total_capital"] == 1_000_000
        assert result["stock_capital"] == 800_000


# ============================================================
# 6. generate_build_instructions 测试
# ============================================================


class TestGenerateBuildInstructions:
    """generate_build_instructions 方法测试."""

    @patch("builtins.open", new_callable=mock_open)
    @patch("utils.data_types.normalize_stock_code")
    @patch("build_plan_executor.BuildPlanExecutor")
    def test_basic_build(
        self,
        mock_executor_cls: MagicMock,
        mock_normalize: MagicMock,
        mock_file: MagicMock,
    ) -> None:
        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        system.market_state = {"market_regime": "neutral"}

        # mock normalize_stock_code: 去掉 .SH/.SZ 后缀
        mock_normalize.side_effect = lambda c: c.replace(".SH", "").replace(".SZ", "")

        # mock plan_500w json
        plan_500w = {
            "target_portfolio": {
                "588080.SH": {"est_price": 1.05, "style": "科技"},
                "512880.SH": {"est_price": 1.20, "style": "金融"},
            },
            "position_plan": {
                "510050.SH": {"est_price": 2.80},
            },
        }
        mock_file.return_value.read.return_value = json.dumps(plan_500w)

        # mock executor
        mock_executor = MagicMock()
        mock_executor_cls.return_value = mock_executor
        mock_executor.get_emergency_protocol.return_value = {
            "day_capital_multiplier": 1.0,
            "level_name": "NORMAL",
            "actions": [],
        }
        mock_order = MagicMock(
            priority=1,
            code="588080",
            name="科创50ETF",
            shares=1000,
            est_price=1.05,
            limit_price=1.06,
            est_amount=1050,
            style="科技",
            risk="低",
            note="建仓",
        )
        mock_sheet = MagicMock(
            trade_date="2026-07-13",
            total_capital=5_000_000,
            day_capital=250_000,
            morning_orders=[mock_order],
            afternoon_orders=[],
            paused_orders=[],
            warnings=[],
        )
        mock_executor.generate_daily_orders.return_value = mock_sheet

        phase = SAMPLE_PLAN_DATA["execution_plan"]["phase1"]
        result = system.generate_build_instructions(phase)

        assert result["trade_date"] == "2026-07-13"
        assert result["phase"] == "底仓建立与收租期"
        assert result["total_capital"] == 5_000_000
        assert result["day_capital"] == 250_000
        assert result["capital_multiplier"] == 1.0
        assert result["emergency_level"] == "NORMAL"
        assert len(result["morning_orders"]) == 1
        assert result["morning_orders"][0]["code"] == "588080"
        assert result["morning_orders"][0]["shares"] == 1000
        assert result["paused_orders"] == []
        assert result["warnings"] == []

    @patch("builtins.open", new_callable=mock_open)
    @patch("utils.data_types.normalize_stock_code")
    @patch("build_plan_executor.BuildPlanExecutor")
    def test_with_broad_based_etf(
        self,
        mock_executor_cls: MagicMock,
        mock_normalize: MagicMock,
        mock_file: MagicMock,
    ) -> None:
        """含宽基ETF的 target_portfolio 触发 adjust_plan_with_national_team_flow."""
        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        system.market_state = {"market_regime": "neutral"}

        mock_normalize.side_effect = lambda c: c.replace(".SH", "").replace(".SZ", "")

        plan_500w = {
            "target_portfolio": {
                "510050.SH": {"est_price": 2.80, "style": "宽基", "name": "上证50ETF"},
            },
            "position_plan": {},
        }
        mock_file.return_value.read.return_value = json.dumps(plan_500w)

        mock_executor = MagicMock()
        mock_executor_cls.return_value = mock_executor
        mock_executor.get_emergency_protocol.return_value = {
            "day_capital_multiplier": 0.5,
            "level_name": "CAUTIOUS",
            "actions": ["减半建仓"],
        }
        mock_sheet = MagicMock(
            trade_date="2026-07-13",
            total_capital=5_000_000,
            day_capital=125_000,
            morning_orders=[],
            afternoon_orders=[],
            paused_orders=[],
            warnings=["注意"],
        )
        mock_executor.generate_daily_orders.return_value = mock_sheet

        # mock adjust_plan_with_national_team_flow
        mock_adjust = MagicMock(return_value={"applied": True, "summary": "已调整"})

        phase = SAMPLE_PLAN_DATA["execution_plan"]["phase1"]
        with patch(
            "utils.broad_based_etf_policy.adjust_plan_with_national_team_flow",
            mock_adjust,
        ):
            result = system.generate_build_instructions(phase)

        assert result["capital_multiplier"] == 0.5
        assert result["emergency_level"] == "CAUTIOUS"
        assert result["emergency_actions"] == ["减半建仓"]
        mock_adjust.assert_called_once()


# ============================================================
# 7. calculate_hedge_plan 测试
# ============================================================


class TestCalculateHedgePlan:
    """calculate_hedge_plan 方法测试."""

    @patch("utils.greek_hedge_manager.HedgeInstrument")
    @patch("utils.greek_hedge_manager.GreekHedgeManager")
    def test_basic(
        self,
        mock_ghm_cls: MagicMock,
        mock_hedge_inst_cls: MagicMock,
    ) -> None:
        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        system.build_plan = {
            "morning_orders": [
                {
                    "code": "588080.SH",
                    "shares": 1000,
                    "est_price": 1.05,
                    "style": "科技",
                },
                {
                    "code": "512880.SH",
                    "shares": 500,
                    "est_price": 1.20,
                    "style": "金融",
                },
            ],
            "afternoon_orders": [
                {
                    "code": "510050.SH",
                    "shares": 200,
                    "est_price": 2.80,
                    "style": "宽基",
                },
            ],
        }
        system.stock_positions = {
            "515030.SH": {"shares": 300, "est_price": 1.50}
        }
        system.market_state = {"market_regime": "neutral"}

        mock_ghm = MagicMock()
        mock_ghm_cls.return_value = mock_ghm
        mock_exposure = MagicMock(
            delta=100_000.0, gamma=0.05, vega=5000.0, theta=-500.0
        )
        mock_ghm.calc_portfolio_greeks.return_value = mock_exposure
        mock_ghm.target_futures_delta_hedge.return_value = {"IF_futures": 5.5}
        mock_ghm.rebalance_signal.return_value = {
            "delta_rebalance": True,
            "gamma_rebalance": False,
            "vega_rebalance": False,
        }

        result = system.calculate_hedge_plan()

        # portfolio_value = 1000*1.05 + 500*1.20 + 200*2.80 + 300*1.50 = 1050+600+560+450 = 2660
        assert result["portfolio_value"] == 2660
        assert result["market_regime"] == "neutral"
        assert result["target_delta"] == 0.6
        assert result["current_delta"] == 100_000.0
        assert result["futures_hedge"]["contracts"] == 5.5
        assert result["futures_hedge"]["instrument"] == "IF_futures"
        assert result["futures_hedge"]["direction"] == "SELL"
        assert result["futures_hedge"]["multiplier"] == 300
        # option_hedge: 1 risk_reversal + 1 vega_event = 2
        assert len(result["option_hedge"]) == 2
        assert result["option_hedge"][0]["type"] == "PUT_OPTION"
        assert result["option_hedge"][1]["type"] == "STRATEGY_OPTION"
        assert result["rebalance_signal"]["delta_rebalance"] is True

    @patch("utils.greek_hedge_manager.HedgeInstrument")
    @patch("utils.greek_hedge_manager.GreekHedgeManager")
    def test_empty_build_plan(
        self,
        mock_ghm_cls: MagicMock,
        mock_hedge_inst_cls: MagicMock,
    ) -> None:
        """空 build_plan, 只有 stock_positions."""
        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        system.build_plan = {
            "morning_orders": [],
            "afternoon_orders": [],
        }
        system.stock_positions = {}
        system.market_state = {"market_regime": "cautious"}

        mock_ghm = MagicMock()
        mock_ghm_cls.return_value = mock_ghm
        mock_exposure = MagicMock(
            delta=0.0, gamma=0.0, vega=0.0, theta=0.0
        )
        mock_ghm.calc_portfolio_greeks.return_value = mock_exposure
        mock_ghm.target_futures_delta_hedge.return_value = {"IF_futures": 0.0}
        mock_ghm.rebalance_signal.return_value = {"delta_rebalance": False}

        result = system.calculate_hedge_plan()
        assert result["portfolio_value"] == 0
        assert result["portfolio_beta"] == 0.0
        assert result["target_delta"] == 0.4  # cautious -> 0.4

    @patch("utils.greek_hedge_manager.HedgeInstrument")
    @patch("utils.greek_hedge_manager.GreekHedgeManager")
    def test_bear_market_target_delta(
        self,
        mock_ghm_cls: MagicMock,
        mock_hedge_inst_cls: MagicMock,
    ) -> None:
        """bear 市场 target_delta = 0.2."""
        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        system.build_plan = {"morning_orders": [], "afternoon_orders": []}
        system.stock_positions = {}
        system.market_state = {"market_regime": "bear"}

        mock_ghm = MagicMock()
        mock_ghm_cls.return_value = mock_ghm
        mock_ghm.calc_portfolio_greeks.return_value = MagicMock(
            delta=0, gamma=0, vega=0, theta=0
        )
        mock_ghm.target_futures_delta_hedge.return_value = {"IF_futures": 0}
        mock_ghm.rebalance_signal.return_value = {}

        result = system.calculate_hedge_plan()
        assert result["target_delta"] == 0.2


# ============================================================
# 8. _load_target_portfolio_plan 测试
# ============================================================


class TestLoadTargetPortfolioPlan:
    """_load_target_portfolio_plan 方法测试."""

    def test_success(self, tmp_path: Path) -> None:
        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        plan_file = tmp_path / "500万建仓计划_20260706.json"
        plan_file.write_text(
            json.dumps({"target_portfolio": {"510050.SH": {}}}),
            encoding="utf-8",
        )
        # 清除缓存
        system._target_plan_cache = None
        with patch(
            "utils.execution.daily_build_and_hedge.BASE_DIR", tmp_path
        ):
            result = system._load_target_portfolio_plan()
        assert "target_portfolio" in result
        assert "510050.SH" in result["target_portfolio"]

    def test_cached(self) -> None:
        """第二次调用使用缓存."""
        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        system._target_plan_cache = {"cached": True}
        result = system._load_target_portfolio_plan()
        assert result == {"cached": True}

    def test_file_not_found(self) -> None:
        """文件不存在时返回空 dict."""
        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        system._target_plan_cache = None
        with patch(
            "utils.execution.daily_build_and_hedge.BASE_DIR",
            Path("/nonexistent/path/xyz"),
        ):
            result = system._load_target_portfolio_plan()
        assert result == {}


# ============================================================
# 9. fetch_realtime_quotes 测试
# ============================================================


class TestFetchRealtimeQuotes:
    """fetch_realtime_quotes 方法测试."""

    @patch("utils.astock_realtime.get_realtime_quotes")
    def test_success(self, mock_get_quotes: MagicMock) -> None:
        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        system._target_plan_cache = {
            "target_portfolio": {"510050.SH": {}, "588080.SH": {}}
        }
        mock_get_quotes.return_value = {
            "510050": {"price": 2.80, "name": "上证50ETF"},
            "588080": {"price": 1.05, "name": "科创50ETF"},
        }

        result = system.fetch_realtime_quotes()
        assert len(result) == 2
        assert "510050" in result
        assert "588080" in result
        assert system.realtime_quotes == result

    @patch("utils.astock_realtime.get_realtime_quotes")
    def test_failure_returns_empty(self, mock_get_quotes: MagicMock) -> None:
        """异常时返回空 dict."""
        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        system._target_plan_cache = {}
        mock_get_quotes.side_effect = RuntimeError("network error")

        result = system.fetch_realtime_quotes()
        assert result == {}
        assert system.realtime_quotes == {}


# ============================================================
# 10. _render_* 测试
# ============================================================


class TestRenderHeader:
    def test_dry_run(self, configured_system: DailyBuildHedgeSystem) -> None:
        lines = configured_system._render_header()
        assert any("干跑模式" in l for l in lines)
        assert any("2026-07-13" in l for l in lines)

    def test_live_mode(self) -> None:
        system = _make_system(
            target_date=date(2026, 7, 13), dry_run=False, plan_data=SAMPLE_PLAN_DATA
        )
        lines = system._render_header()
        assert any("实盘模式" in l for l in lines)


class TestRenderMarketStateSection:
    def test_with_float_returns(
        self, configured_system: DailyBuildHedgeSystem
    ) -> None:
        lines = configured_system._render_market_state_section()
        assert any("市场状态" in l for l in lines)
        assert any("neutral" in l for l in lines)
        assert any("20日收益率" in l for l in lines)

    def test_with_none_returns(self) -> None:
        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        system.market_state = {
            "market_regime": "cautious",
            "vix_proxy": 18.5,
            "index_return_20d": None,
            "macro_heat_score": 40,
        }
        lines = system._render_market_state_section()
        assert any("cautious" in l for l in lines)
        # index_return_20d=None 时, .get(key, "N/A") 返回 None (key 存在)
        assert any("None" in l for l in lines)


class TestRenderBuildPlanSection:
    def test_full_plan(
        self, configured_system: DailyBuildHedgeSystem
    ) -> None:
        lines = configured_system._render_build_plan_section()
        assert any("建仓计划" in l for l in lines)
        assert any("底仓建立与收租期" in l for l in lines)
        assert any("上午批次" in l for l in lines)
        assert any("下午批次" in l for l in lines)
        assert any("暂停执行标的" in l for l in lines)
        assert any("紧急响应措施" in l for l in lines)
        assert any("上午合计" in l for l in lines)
        assert any("下午合计" in l for l in lines)

    def test_empty_plan(self) -> None:
        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        system.build_plan = {}
        lines = system._render_build_plan_section()
        assert any("建仓计划" in l for l in lines)


class TestRenderHedgePlanSection:
    def test_full_plan(
        self, configured_system: DailyBuildHedgeSystem
    ) -> None:
        lines = configured_system._render_hedge_plan_section()
        assert any("对冲计划" in l for l in lines)
        assert any("期货对冲" in l for l in lines)
        assert any("IF_futures" in l for l in lines)
        assert any("期权对冲" in l for l in lines)
        assert any("PUT_OPTION" in l for l in lines)
        assert any("再平衡信号" in l for l in lines)
        assert any("需要" in l for l in lines)
        assert any("无需" in l for l in lines)

    def test_empty_hedge(self) -> None:
        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        system.hedge_plan = {}
        lines = system._render_hedge_plan_section()
        assert any("对冲计划" in l for l in lines)


class TestRenderSummarySection:
    def test_summary(
        self, configured_system: DailyBuildHedgeSystem
    ) -> None:
        lines = configured_system._render_summary_section()
        assert any("执行摘要" in l for l in lines)
        assert any("股票订单数" in l for l in lines)
        assert any("期货对冲合约" in l for l in lines)
        assert any("期权对冲项目" in l for l in lines)


class TestRenderChecklistSection:
    def test_checklist(
        self, configured_system: DailyBuildHedgeSystem
    ) -> None:
        lines = configured_system._render_checklist_section()
        assert any("执行检查清单" in l for l in lines)
        assert any("确认账户可用资金" in l for l in lines)
        assert any("生成盘后报告" in l for l in lines)


class TestRenderFooter:
    def test_footer(
        self, configured_system: DailyBuildHedgeSystem
    ) -> None:
        lines = configured_system._render_footer()
        assert lines[0] == "---"
        assert any("报告生成" in l for l in lines)


class TestRenderComplianceSection:
    def test_success(
        self, configured_system: DailyBuildHedgeSystem
    ) -> None:
        mock_compliance = {
            "summary": {
                "passed": 8,
                "total": 10,
                "weak": 2,
                "avg_combined": 0.75,
                "weak_codes": ["515030.SH", "512760.SH"],
            },
            "holdings": [
                {
                    "code": "588080.SH",
                    "name": "科创50ETF",
                    "style": "科技",
                    "ff_score": 0.85,
                    "kc_score": 0.80,
                    "combined": 0.82,
                    "level": "strong",
                    "action": "维持",
                }
            ],
        }
        with patch.object(
            configured_system,
            "_load_target_portfolio_plan",
            return_value={"target_portfolio": {}},
        ):
            with patch(
                "utils.broad_based_etf_policy.validate_portfolio_compliance",
                return_value=mock_compliance,
            ):
                lines = configured_system._render_compliance_section()
        assert any("合规校验" in l for l in lines)
        assert any("8/10" in l for l in lines)
        assert any("588080.SH" in l for l in lines)
        assert any("需关注" in l for l in lines)

    def test_exception(
        self, configured_system: DailyBuildHedgeSystem
    ) -> None:
        with patch.object(
            configured_system,
            "_load_target_portfolio_plan",
            side_effect=RuntimeError("fail"),
        ):
            lines = configured_system._render_compliance_section()
        assert any("合规校验" in l for l in lines)
        assert any("暂不可用" in l for l in lines)


class TestRenderEtfFlowAdjustmentSection:
    def test_with_flow(
        self, configured_system: DailyBuildHedgeSystem
    ) -> None:
        target_plan = {
            "target_portfolio": {
                "510050.SH": {"name": "上证50ETF", "base_weight": 0.06},
            }
        }
        flow = {
            "510050.SH": {"net_flow_yi": 5.5},
        }
        with patch.object(
            configured_system,
            "_load_target_portfolio_plan",
            return_value=target_plan,
        ):
            with patch(
                "utils.broad_based_etf_policy.fetch_national_team_flow_signals",
                return_value=flow,
            ):
                with patch(
                    "utils.broad_based_etf_policy.get_broad_based_codes",
                    return_value=["510050.SH"],
                ):
                    with patch(
                        "utils.broad_based_etf_policy.flow_to_adjustment",
                        return_value={"signal": "加仓", "action": "增配", "factor": 0.1},
                    ):
                        lines = configured_system._render_etf_flow_adjustment_section()
        assert any("宽基ETF" in l for l in lines)
        assert any("510050.SH" in l for l in lines)
        assert any("加仓" in l for l in lines)

    def test_no_flow(
        self, configured_system: DailyBuildHedgeSystem
    ) -> None:
        with patch.object(
            configured_system,
            "_load_target_portfolio_plan",
            return_value={"target_portfolio": {}},
        ):
            with patch(
                "utils.broad_based_etf_policy.fetch_national_team_flow_signals",
                return_value={},
            ):
                with patch(
                    "utils.broad_based_etf_policy.get_broad_based_codes",
                    return_value=[],
                ):
                    lines = configured_system._render_etf_flow_adjustment_section()
        assert any("宽基ETF" in l for l in lines)
        assert any("暂不可用" in l for l in lines)

    def test_exception(
        self, configured_system: DailyBuildHedgeSystem
    ) -> None:
        with patch.object(
            configured_system,
            "_load_target_portfolio_plan",
            side_effect=RuntimeError("fail"),
        ):
            lines = configured_system._render_etf_flow_adjustment_section()
        assert any("宽基ETF" in l for l in lines)
        assert any("暂不可用" in l for l in lines)


class TestRenderRealtimeQuotesSection:
    def test_with_quotes(
        self, configured_system: DailyBuildHedgeSystem
    ) -> None:
        quotes = {
            "510050": {
                "name": "上证50ETF",
                "price": 2.80,
                "change_pct": 1.5,
                "pe": 12.3,
                "pb": 1.2,
                "mktcap_yi": 100.5,
                "source": "eastmoney",
            },
        }
        with patch.object(
            configured_system, "fetch_realtime_quotes", return_value=quotes
        ):
            lines = configured_system._render_realtime_quotes_section()
        assert any("实时行情快照" in l for l in lines)
        assert any("510050" in l for l in lines)
        assert any("eastmoney" in l for l in lines)

    def test_empty_quotes(
        self, configured_system: DailyBuildHedgeSystem
    ) -> None:
        with patch.object(
            configured_system, "fetch_realtime_quotes", return_value={}
        ):
            lines = configured_system._render_realtime_quotes_section()
        assert any("实时行情快照" in l for l in lines)
        assert any("暂不可用" in l for l in lines)

    def test_with_none_pe_pb(
        self, configured_system: DailyBuildHedgeSystem
    ) -> None:
        """pe/pb 为 None 时显示 '-'."""
        quotes = {
            "588080": {
                "name": "科创50ETF",
                "price": 1.05,
                "change_pct": -0.5,
                "pe": None,
                "pb": None,
                "mktcap_yi": 50,
                "source": "tencent",
            },
        }
        with patch.object(
            configured_system, "fetch_realtime_quotes", return_value=quotes
        ):
            lines = configured_system._render_realtime_quotes_section()
        assert any("-" in l for l in lines)


class TestRenderEtfFlowDecisionSection:
    def test_success_intraday(
        self, configured_system: DailyBuildHedgeSystem
    ) -> None:
        """盘中决策 (含突变信号 + 推荐 + LLM + 融合)."""
        configured_system.market_state["etf_flow_decision"] = {
            "status": "success",
            "phase": "intraday",
            "timestamp": "2026-07-13 10:30:00",
            "elapsed_seconds": 1.5,
            "summary": {
                "total_etfs": 10,
                "strong_signals": 3,
                "medium_signals": 5,
                "total_inflow": 15.5,
                "sudden_changes": 2,
            },
            "sudden_changes": [
                {"name": "科创50ETF", "description": "资金流突增5亿"},
                {"name": "证券ETF", "description": "资金流突降3亿"},
            ],
            "recommendations": [
                {
                    "action": "买入",
                    "strength": 0.8,
                    "confidence": 0.9,
                    "net_flow_yi": 5.5,
                    "price_change_pct": 2.3,
                    "reason": "资金流强信号",
                    "code": "588080.SH",
                    "name": "科创50ETF",
                }
            ],
            "llm_analysis": "市场情绪偏多，建议关注科技板块。\n资金流持续流入。",
            "fused_signals": [
                {
                    "symbol": "588080.SH",
                    "strength": 0.8,
                    "confidence": 0.9,
                    "meta": {"name": "科创50ETF"},
                    "sources": {
                        "etf_strength": 0.7,
                        "llm_strength": 0.6,
                        "alpha_strength": 0.5,
                    },
                }
            ],
        }
        lines = configured_system._render_etf_flow_decision_section()
        assert any("ETF资金流向" in l for l in lines)
        assert any("盘中决策" in l for l in lines)
        assert any("信号摘要" in l for l in lines)
        assert any("突变信号" in l for l in lines)
        assert any("突变信号详情" in l for l in lines)
        assert any("交易建议" in l for l in lines)
        assert any("LLM辅助分析" in l for l in lines)
        assert any("信号融合" in l for l in lines)

    def test_success_pre_market(self) -> None:
        """盘前决策 (无突变信号)."""
        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        system.market_state = {
            "etf_flow_decision": {
                "status": "success",
                "phase": "pre_market",
                "timestamp": "2026-07-13 09:20:00",
                "elapsed_seconds": 0.8,
                "summary": {
                    "total_etfs": 8,
                    "strong_signals": 1,
                    "medium_signals": 3,
                    "total_inflow": 5.2,
                    "sudden_changes": 0,
                },
                "recommendations": [],
                "llm_analysis": None,
                "fused_signals": [],
            }
        }
        lines = system._render_etf_flow_decision_section()
        assert any("盘前决策" in l for l in lines)
        assert any("信号摘要" in l for l in lines)
        # 无推荐 / 无融合 / 无LLM
        joined = "\n".join(lines)
        assert "交易建议" not in joined
        assert "信号融合" not in joined
        assert "LLM辅助分析" not in joined

    def test_unavailable(self) -> None:
        """决策引擎不可用."""
        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        system.market_state = {"etf_flow_decision": None}
        lines = system._render_etf_flow_decision_section()
        assert any("ETF资金流向" in l for l in lines)
        assert any("暂不可用" in l for l in lines)

    def test_status_not_success(self) -> None:
        """status != success -> 显示不可用."""
        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        system.market_state = {
            "etf_flow_decision": {"status": "error"}
        }
        lines = system._render_etf_flow_decision_section()
        assert any("暂不可用" in l for l in lines)

    def test_exception(self) -> None:
        """渲染过程异常 -> 显示不可用."""
        system = _make_system(plan_data=SAMPLE_PLAN_DATA)
        system.market_state = {"etf_flow_decision": "not_a_dict"}
        lines = system._render_etf_flow_decision_section()
        assert any("ETF资金流向" in l for l in lines)


# ============================================================
# 11. generate_report 测试
# ============================================================


class TestGenerateReport:
    """generate_report 方法测试."""

    def test_full_report(
        self, configured_system: DailyBuildHedgeSystem
    ) -> None:
        """完整报告生成 (etf_flow_decision 已存在, 不触发 assess_market_state)."""
        configured_system.market_state["etf_flow_decision"] = {
            "status": "success",
            "phase": "pre_market",
            "timestamp": "2026-07-13 09:20:00",
            "elapsed_seconds": 0.5,
            "summary": {
                "total_etfs": 5,
                "strong_signals": 1,
                "medium_signals": 2,
                "total_inflow": 3.2,
                "sudden_changes": 0,
            },
            "recommendations": [],
            "fused_signals": [],
        }
        with patch.object(
            configured_system,
            "_load_target_portfolio_plan",
            return_value={"target_portfolio": {}},
        ):
            with patch.object(
                configured_system, "fetch_realtime_quotes", return_value={}
            ):
                with patch(
                    "utils.broad_based_etf_policy.validate_portfolio_compliance",
                    return_value={"summary": {}, "holdings": []},
                ):
                    with patch(
                        "utils.broad_based_etf_policy.fetch_national_team_flow_signals",
                        return_value={},
                    ):
                        with patch(
                            "utils.broad_based_etf_policy.get_broad_based_codes",
                            return_value=[],
                        ):
                            report = configured_system.generate_report()

        assert isinstance(report, str)
        assert "每日建仓计划" in report
        assert "一、市场状态评估" in report
        assert "二、建仓计划" in report
        assert "三、对冲计划" in report
        assert "四、执行摘要" in report
        assert "五、执行检查清单" in report
        assert "六、" in report
        assert "七、" in report
        assert "八、" in report
        assert "九、" in report

    def test_calls_assess_if_no_decision(
        self, configured_system: DailyBuildHedgeSystem
    ) -> None:
        """etf_flow_decision 缺失时调用 assess_market_state."""
        configured_system.market_state["etf_flow_decision"] = None
        with patch.object(
            configured_system, "assess_market_state"
        ) as mock_assess:
            mock_assess.return_value = {
                "market_regime": "neutral",
                "etf_flow_decision": {"status": "success"},
                "vix_proxy": 18.5,
                "index_return_20d": 0.02,
                "macro_heat_score": 55,
            }
            with patch.object(
                configured_system,
                "_load_target_portfolio_plan",
                return_value={"target_portfolio": {}},
            ):
                with patch.object(
                    configured_system,
                    "fetch_realtime_quotes",
                    return_value={},
                ):
                    with patch(
                        "utils.broad_based_etf_policy.validate_portfolio_compliance",
                        return_value={"summary": {}, "holdings": []},
                    ):
                        with patch(
                            "utils.broad_based_etf_policy.fetch_national_team_flow_signals",
                            return_value={},
                        ):
                            with patch(
                                "utils.broad_based_etf_policy.get_broad_based_codes",
                                return_value=[],
                            ):
                                configured_system.generate_report()
        mock_assess.assert_called_once()


# ============================================================
# 12. save_report 测试
# ============================================================


class TestSaveReport:
    """save_report 方法测试."""

    def test_save_to_tmp(
        self,
        configured_system: DailyBuildHedgeSystem,
        tmp_path: Path,
    ) -> None:
        with patch.object(
            configured_system, "generate_report", return_value="# Test Report"
        ):
            with patch.object(
                configured_system,
                "_load_target_portfolio_plan",
                return_value={"target_portfolio": {}},
            ):
                with patch(
                    "utils.broad_based_etf_policy.validate_portfolio_compliance",
                    return_value={"summary": {}, "holdings": []},
                ):
                    with patch(
                        "utils.broad_based_etf_policy.fetch_national_team_flow_signals",
                        return_value={},
                    ):
                        with patch(
                            "utils.broad_based_etf_policy.get_broad_based_codes",
                            return_value=[],
                        ):
                            md_path, json_path = configured_system.save_report(
                                str(tmp_path)
                            )

        assert Path(md_path).exists()
        assert Path(json_path).exists()
        assert Path(md_path).read_text(encoding="utf-8") == "# Test Report"

        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        assert "build_plan" in data
        assert "hedge_plan" in data
        assert "market_state" in data
        assert "risk_status" in data
        assert "policy_compliance" in data
        assert "broad_based_etf" in data

    def test_save_default_dir(
        self,
        configured_system: DailyBuildHedgeSystem,
        tmp_path: Path,
    ) -> None:
        """无 output_dir 时使用默认路径 (BASE_DIR/每日报告归档/...)."""
        with patch.object(
            configured_system, "generate_report", return_value="# Test"
        ):
            with patch.object(
                configured_system,
                "_load_target_portfolio_plan",
                return_value={"target_portfolio": {}},
            ):
                with patch(
                    "utils.broad_based_etf_policy.validate_portfolio_compliance",
                    return_value={"summary": {}, "holdings": []},
                ):
                    with patch(
                        "utils.broad_based_etf_policy.fetch_national_team_flow_signals",
                        return_value={},
                    ):
                        with patch(
                            "utils.broad_based_etf_policy.get_broad_based_codes",
                            return_value=[],
                        ):
                            with patch(
                                "utils.execution.daily_build_and_hedge.BASE_DIR",
                                tmp_path,
                            ):
                                md_path, json_path = configured_system.save_report()

        assert Path(md_path).exists()
        assert Path(json_path).exists()
        assert "每日报告归档" in md_path


# ============================================================
# 13. run 测试
# ============================================================


class TestRun:
    """run 方法测试."""

    def test_success(self) -> None:
        system = _make_system(
            target_date=date(2026, 7, 15), plan_data=SAMPLE_PLAN_DATA
        )

        def fake_assess():
            system.market_state = {"market_regime": "neutral"}
            return system.market_state

        with patch.object(
            system,
            "get_active_phase",
            return_value=(SAMPLE_PLAN_DATA["execution_plan"]["phase1"], "phase1"),
        ):
            with patch.object(
                system, "assess_market_state", side_effect=fake_assess
            ):
                with patch.object(
                    system, "calculate_risk_budget", return_value={}
                ):
                    with patch.object(
                        system,
                        "generate_build_instructions",
                        return_value={"morning_orders": [], "afternoon_orders": []},
                    ):
                        with patch.object(
                            system,
                            "calculate_hedge_plan",
                            return_value={
                                "futures_hedge": {"contracts": 0},
                                "option_hedge": [],
                            },
                        ):
                            result = system.run()

        assert result["status"] == "success"
        assert result["phase_key"] == "phase1"
        assert result["market_regime"] == "neutral"
        assert "build_plan" in result
        assert "hedge_plan" in result

    def test_no_active_phase(self) -> None:
        system = _make_system(
            target_date=date(2031, 1, 1), plan_data=SAMPLE_PLAN_DATA
        )
        with patch.object(
            system, "get_active_phase", return_value=(None, "completed")
        ):
            result = system.run()
        assert result["status"] == "no_active_phase"
