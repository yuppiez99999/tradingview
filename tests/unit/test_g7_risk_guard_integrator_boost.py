"""test_g7_risk_guard_integrator_boost.py — 风控守卫集成器覆盖率提升测试

目标: 将 utils/risk_guard_integrator.py 覆盖率提升到 80%+

覆盖方法:
    - parse_kill_switch_level (所有分支: int/str/enum/OK隐式升级/异常类型)
    - RiskGuardIntegrator.__init__ / _log (含 UnicodeEncodeError 路径)
    - _load_pnl_report (多路径查找)
    - _get_pnl_summary / _extract_positions / _extract_summary (多格式兼容)
    - _load_next_trade_plan / _save_trade_plan (含备份)
    - guard_drawdown (Level 0/1/2/3/4 + ImportError + 无成本数据)
    - _apply_budget_cut / _apply_hedge_boost
    - guard_vol_target (normal/scale_down/import_failed/无收益率)
    - _extract_daily_returns
    - guard_hedge_execution (within_budget/over_budget/options_only/none/error)
    - guard_protective_put (below_threshold/generate/existing/error/import)
    - guard_kill_switch 降级模式 (L3/L2/L1/OK + P0-D无ks + 集中度升级)
    - guard_market_circuit_breaker (normal/L2+/crash/import)
    - guard_liquidity_crisis (data_unavailable/triggered/normal/crash)
    - _fetch_limit_counts (akshare/astock_sample/fail_closed)
    - guard_overnight_gap (normal/L1/L2+/crash/import)
    - guard_sentiment_breaking_news (flag_off/no_holdings/import/unavailable/ok/triggered)
    - _extract_holding_symbols (3种来源)
    - guard_correlation_hedge (import/no_data/safe_haven/no_action/crash)
    - _build_position_returns (无持仓/少量报告/有效数据)
    - _build_safe_haven_orders (gold/repo/both/neither)
    - _deduplicate_put_orders 边界 (无法识别底层/无options)
    - _extract_underlying_code 模糊匹配
    - run_all_guards (集成 + 一致性校验)
    - _write_guard_log
    - main() CLI 入口
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# ============================================================
# PROJECT_ROOT sys.path 注入
# ============================================================
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
for _p in [
    PROJECT_ROOT,
    os.path.join(PROJECT_ROOT, "v8.3_institutional"),
    os.path.join(PROJECT_ROOT, "v8.3_institutional", "src"),
]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 审计 item 11 拆解 (2026-09-10): KillSwitchLevel / parse_kill_switch_level 已迁至
# utils.risk.guards.kill_switch_level; RiskGuardIntegrator / main 仍在编排模块。
from utils.datetime_utils import now_bj  # noqa: E402
from utils.risk.guards.kill_switch_level import (  # noqa: E402
    KillSwitchLevel,
    parse_kill_switch_level,
)
from utils.risk_guard_integrator import (  # noqa: E402
    RiskGuardIntegrator,
    main,
)

# ============================================================
# 辅助函数与 Fixtures
# ============================================================


@pytest.fixture
def integrator(tmp_path, monkeypatch):
    """隔离 LOGS_DIR / REPORTS_DIR / TRADE_PLANS_DIR / DAILY_REPORT_DIR 的实例"""
    logs_dir = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    trade_plans_dir = tmp_path / "trade_plans"
    daily_report_dir = tmp_path / "daily_reports"
    for d in (logs_dir, reports_dir, trade_plans_dir, daily_report_dir):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("utils.risk.guards.plan_context.LOGS_DIR", logs_dir)
    monkeypatch.setattr("utils.risk.guards.plan_context.REPORTS_DIR", reports_dir)
    monkeypatch.setattr("utils.risk.guards.plan_context.TRADE_PLANS_DIR", trade_plans_dir)
    monkeypatch.setattr(
        "utils.risk.guards.plan_context.DAILY_REPORT_DIR", daily_report_dir
    )
    return RiskGuardIntegrator(report_date="2026-07-21", total_capital=5_000_000)


def _mock_module(monkeypatch, module_name, class_name, instance=None):
    """在 sys.modules 中注入 mock 模块, 含指定类

    Returns: (mock_module, mock_class, mock_instance)
    """
    mock_inst = instance if instance is not None else MagicMock()
    mock_cls = MagicMock(return_value=mock_inst)
    mock_mod = MagicMock()
    setattr(mock_mod, class_name, mock_cls)
    monkeypatch.setitem(sys.modules, module_name, mock_mod)
    return mock_mod, mock_cls, mock_inst


def _block_module(monkeypatch, module_name):
    """将模块设为 None, 触发 ImportError"""
    monkeypatch.setitem(sys.modules, module_name, None)


def _block_all_optional_modules(monkeypatch):
    """阻止所有可选风控模块导入, 使 guards 走 ImportError 降级路径"""
    for mod in [
        "utils.drawdown_controller",
        "utils.vol_target_controller",
        "utils.hedge_execution_engine",
        "utils.protective_put_engine",
        "utils.kill_switch",
        "utils.market_circuit_breaker",
        "utils.overnight_gap_monitor",
        "utils.ifind_news_analyzer",
        "hedging.correlation_hedger",
    ]:
        _block_module(monkeypatch, mod)
    monkeypatch.setitem(sys.modules, "hedging", MagicMock())
    monkeypatch.setitem(sys.modules, "akshare", None)


def _make_pnl_report(
    total_cost=1_000_000,
    total_market_value=1_050_000,
    total_pnl=50_000,
    margin_used=600_000,
    total_equity=1_500_000,
    positions=None,
):
    """构造完整格式 pnl_report"""
    details = positions or [
        {
            "code": "588080.SH",
            "name": "科创50ETF",
            "market_value": 350_000,
            "pnl": 20_000,
            "daily_pnl_pct": 6.0,
            "cost_amount": 330_000,
        },
        {
            "code": "510050.SH",
            "name": "上证50ETF",
            "market_value": 300_000,
            "pnl": 15_000,
            "daily_pnl_pct": 5.3,
            "cost_amount": 285_000,
        },
    ]
    return {
        "meta": {"report_date": "2026-07-21"},
        "portfolio_pnl": {
            "summary": {
                "total_cost": total_cost,
                "total_market_value": total_market_value,
                "total_pnl": total_pnl,
                "total_pnl_pct": 5.0,
                "margin_used": margin_used,
                "total_equity": total_equity,
            },
            "details": details,
        },
    }


def _make_plan(circuit_level=None, spot_build_allowed=None, build_allowed=None):
    """构造标准交易计划"""
    plan = {
        "trade_date": "2026-07-22",
        "phase": {"daily_capital": 150_000, "day_capital": 150_000},
        "execution_plan": {
            "morning_orders": [
                {
                    "symbol": "588080.SH",
                    "direction": "BUY",
                    "shares": 1000,
                    "est_amount": 100_000,
                },
                {
                    "symbol": "512880.SH",
                    "direction": "SELL",
                    "shares": 500,
                    "est_amount": 50_000,
                },
            ],
            "afternoon_orders": [
                {
                    "symbol": "510050.SH",
                    "direction": "BUY",
                    "shares": 2000,
                    "est_amount": 200_000,
                },
            ],
        },
        "market_state": {},
        "risk_guard": {},
        "hedge_config": {"layers": {"layer1_futures": {"ratio": 0.15}}},
    }
    if circuit_level is not None:
        plan["market_state"]["circuit_level"] = circuit_level
    if spot_build_allowed is not None:
        plan["market_state"]["spot_build_allowed"] = spot_build_allowed
    if build_allowed is not None:
        plan["market_state"]["build_allowed"] = build_allowed
    return plan


# ============================================================
# parse_kill_switch_level 测试
# ============================================================


class TestParseKillSwitchLevel:
    """parse_kill_switch_level: 所有分支覆盖"""

    @pytest.mark.unit
    def test_int_values(self):
        assert parse_kill_switch_level(0) == KillSwitchLevel.OK
        assert parse_kill_switch_level(1) == KillSwitchLevel.L1
        assert parse_kill_switch_level(2) == KillSwitchLevel.L2
        assert parse_kill_switch_level(3) == KillSwitchLevel.L3

    @pytest.mark.unit
    def test_invalid_int_returns_ok(self):
        assert parse_kill_switch_level(5) == KillSwitchLevel.OK
        assert parse_kill_switch_level(-1) == KillSwitchLevel.OK

    @pytest.mark.unit
    def test_string_l_prefix(self):
        assert parse_kill_switch_level("L0") == KillSwitchLevel.OK
        assert parse_kill_switch_level("L1") == KillSwitchLevel.L1
        assert parse_kill_switch_level("L2") == KillSwitchLevel.L2
        assert parse_kill_switch_level("L3") == KillSwitchLevel.L3

    @pytest.mark.unit
    def test_string_plain_number(self):
        assert parse_kill_switch_level("0") == KillSwitchLevel.OK
        assert parse_kill_switch_level("2") == KillSwitchLevel.L2

    @pytest.mark.unit
    def test_string_ok_no_margin_upgrade(self):
        assert parse_kill_switch_level("OK") == KillSwitchLevel.OK
        assert parse_kill_switch_level("OK", margin_usage=0.50) == KillSwitchLevel.OK
        assert parse_kill_switch_level("OK", margin_usage=0.74) == KillSwitchLevel.OK

    @pytest.mark.unit
    def test_string_ok_with_high_margin_upgrades_to_l3(self):
        assert parse_kill_switch_level("OK", margin_usage=0.75) == KillSwitchLevel.L3
        assert parse_kill_switch_level("OK", margin_usage=0.95) == KillSwitchLevel.L3

    @pytest.mark.unit
    def test_string_case_insensitive(self):
        assert parse_kill_switch_level("l1") == KillSwitchLevel.L1
        assert parse_kill_switch_level("ok") == KillSwitchLevel.OK
        assert parse_kill_switch_level("Ok") == KillSwitchLevel.OK

    @pytest.mark.unit
    def test_string_with_spaces(self):
        assert parse_kill_switch_level("  L2  ") == KillSwitchLevel.L2
        assert parse_kill_switch_level(" L3 ") == KillSwitchLevel.L3

    @pytest.mark.unit
    def test_invalid_string_returns_ok(self):
        assert parse_kill_switch_level("invalid") == KillSwitchLevel.OK
        assert parse_kill_switch_level("abc") == KillSwitchLevel.OK

    @pytest.mark.unit
    def test_enum_passthrough(self):
        for level in KillSwitchLevel:
            assert parse_kill_switch_level(level) == level

    @pytest.mark.unit
    def test_none_returns_ok(self):
        assert parse_kill_switch_level(None) == KillSwitchLevel.OK

    @pytest.mark.unit
    def test_float_returns_ok(self):
        assert parse_kill_switch_level(1.5) == KillSwitchLevel.OK

    @pytest.mark.unit
    def test_list_returns_ok(self):
        assert parse_kill_switch_level([1]) == KillSwitchLevel.OK


# ============================================================
# __init__ 和 _log 测试
# ============================================================


class TestInitAndLog:
    """初始化与日志"""

    @pytest.mark.unit
    def test_init_defaults(self, tmp_path, monkeypatch):
        monkeypatch.setattr("utils.risk.guards.plan_context.LOGS_DIR", tmp_path / "logs")
        rgi = RiskGuardIntegrator()
        assert rgi.report_date == now_bj().strftime("%Y-%m-%d")
        assert rgi.total_capital == 5_000_000
        assert rgi.log_entries == []

    @pytest.mark.unit
    def test_init_custom_params(self, integrator):
        assert integrator.report_date == "2026-07-21"
        assert integrator.total_capital == 5_000_000

    @pytest.mark.unit
    def test_log_appends_entry(self, integrator):
        integrator._log("测试消息")
        assert len(integrator.log_entries) == 1
        assert "[RiskGuard]" in integrator.log_entries[0]
        assert "测试消息" in integrator.log_entries[0]

    @pytest.mark.unit
    def test_log_unicode_encode_error_handled(self, integrator, monkeypatch):
        """_log 的 UnicodeEncodeError 降级路径"""
        mock_logger = MagicMock()
        mock_logger.info.side_effect = [
            UnicodeEncodeError("gbk", "test", 0, 1, "error"),
            None,
        ]
        monkeypatch.setattr("utils.risk.guards.plan_context.logger", mock_logger)
        integrator._log("测试")
        assert mock_logger.info.call_count == 2
        assert len(integrator.log_entries) == 1


# ============================================================
# _get_pnl_summary / _extract_positions / _extract_summary 测试
# ============================================================


class TestExtractPositionsAndSummary:
    """_extract_positions / _extract_summary / _get_pnl_summary 多格式兼容"""

    @pytest.mark.unit
    def test_get_pnl_summary_nested(self, integrator):
        report = {"portfolio_pnl": {"summary": {"total_cost": 100}}}
        assert integrator._get_pnl_summary(report) == {"total_cost": 100}

    @pytest.mark.unit
    def test_get_pnl_summary_empty(self, integrator):
        assert integrator._get_pnl_summary({}) == {}

    @pytest.mark.unit
    def test_extract_positions_from_details_list(self, integrator):
        report = {"portfolio_pnl": {"details": [{"code": "A"}, {"code": "B"}]}}
        result = integrator._extract_positions(report)
        assert len(result) == 2

    @pytest.mark.unit
    def test_extract_positions_from_details_dict(self, integrator):
        report = {
            "portfolio_pnl": {"details": {"A": {"code": "A"}, "B": {"code": "B"}}}
        }
        result = integrator._extract_positions(report)
        assert len(result) == 2

    @pytest.mark.unit
    def test_extract_positions_from_positions_list(self, integrator):
        report = {"portfolio_pnl": {"positions": [{"code": "A"}, {"code": "B"}]}}
        result = integrator._extract_positions(report)
        assert len(result) == 2

    @pytest.mark.unit
    def test_extract_positions_from_positions_dict(self, integrator):
        report = {"portfolio_pnl": {"positions": {"A": {"code": "A"}}}}
        result = integrator._extract_positions(report)
        assert len(result) == 1

    @pytest.mark.unit
    def test_extract_positions_top_level_list(self, integrator):
        report = {"positions": [{"code": "A"}]}
        result = integrator._extract_positions(report)
        assert len(result) == 1

    @pytest.mark.unit
    def test_extract_positions_top_level_dict(self, integrator):
        report = {"positions": {"A": {"code": "A"}}}
        result = integrator._extract_positions(report)
        assert len(result) == 1

    @pytest.mark.unit
    def test_extract_positions_empty(self, integrator):
        assert integrator._extract_positions({}) == []

    @pytest.mark.unit
    def test_extract_positions_portfolio_pnl_not_dict(self, integrator):
        report = {"portfolio_pnl": "not_a_dict", "positions": [{"code": "A"}]}
        result = integrator._extract_positions(report)
        assert len(result) == 1

    @pytest.mark.unit
    def test_extract_summary_nested(self, integrator):
        report = {"portfolio_pnl": {"summary": {"total_cost": 100}}}
        assert integrator._extract_summary(report) == {"total_cost": 100}

    @pytest.mark.unit
    def test_extract_summary_top_level(self, integrator):
        report = {"summary": {"total_cost": 200}}
        assert integrator._extract_summary(report) == {"total_cost": 200}

    @pytest.mark.unit
    def test_extract_summary_empty(self, integrator):
        assert integrator._extract_summary({}) == {}


# ============================================================
# _load_pnl_report 测试
# ============================================================


class TestLoadPnlReport:
    """_load_pnl_report: 多路径查找"""

    @pytest.mark.unit
    def test_load_from_daily_report_dir_with_dash(self, integrator, tmp_path):
        daily_dir = tmp_path / "daily_reports" / "2026-07-21"
        daily_dir.mkdir(parents=True)
        report_data = {"portfolio_pnl": {"summary": {"total_cost": 100}}}
        (daily_dir / "daily_pnl_report_2026-07-21.json").write_text(
            json.dumps(report_data), encoding="utf-8"
        )
        result = integrator._load_pnl_report()
        assert result == report_data

    @pytest.mark.unit
    def test_load_from_daily_report_dir_no_dash(self, integrator, tmp_path):
        daily_dir = tmp_path / "daily_reports" / "2026-07-21"
        daily_dir.mkdir(parents=True)
        report_data = {"portfolio_pnl": {"summary": {"total_cost": 200}}}
        (daily_dir / "daily_pnl_report_20260721.json").write_text(
            json.dumps(report_data), encoding="utf-8"
        )
        result = integrator._load_pnl_report()
        assert result == report_data

    @pytest.mark.unit
    def test_load_from_reports_dir_fallback(self, integrator, tmp_path):
        reports_dir = tmp_path / "reports"
        report_data = {"portfolio_pnl": {"summary": {"total_cost": 300}}}
        (reports_dir / "daily_pnl_report_2026-07-21.json").write_text(
            json.dumps(report_data), encoding="utf-8"
        )
        result = integrator._load_pnl_report()
        assert result == report_data

    @pytest.mark.unit
    def test_load_not_found_returns_none(self, integrator):
        result = integrator._load_pnl_report()
        assert result is None

    @pytest.mark.unit
    def test_load_corrupt_json_returns_none(self, integrator, tmp_path):
        daily_dir = tmp_path / "daily_reports" / "2026-07-21"
        daily_dir.mkdir(parents=True)
        (daily_dir / "daily_pnl_report_2026-07-21.json").write_text(
            "not valid json {{{", encoding="utf-8"
        )
        result = integrator._load_pnl_report()
        assert result is None


# ============================================================
# _load_next_trade_plan / _save_trade_plan 测试
# ============================================================


class TestLoadAndSaveTradePlan:
    """_load_next_trade_plan / _save_trade_plan"""

    @pytest.mark.unit
    def test_load_next_trade_plan_exists(self, integrator, tmp_path):
        plan_data = {"trade_date": "2026-07-22"}
        plan_path = tmp_path / "trade_plans" / "trade_plan_20260722.json"
        plan_path.write_text(json.dumps(plan_data), encoding="utf-8")
        result = integrator._load_next_trade_plan("2026-07-22")
        assert result == plan_data

    @pytest.mark.unit
    def test_load_next_trade_plan_not_found(self, integrator):
        result = integrator._load_next_trade_plan("2026-07-22")
        assert result is None

    @pytest.mark.unit
    def test_load_next_trade_plan_corrupt(self, integrator, tmp_path):
        plan_path = tmp_path / "trade_plans" / "trade_plan_20260722.json"
        plan_path.write_text("not json {{{", encoding="utf-8")
        result = integrator._load_next_trade_plan("2026-07-22")
        assert result is None

    @pytest.mark.unit
    def test_save_trade_plan_creates_file(self, integrator, tmp_path):
        plan = {"trade_date": "2026-07-22", "phase": {}}
        integrator._save_trade_plan(plan, "2026-07-22")
        plan_path = tmp_path / "trade_plans" / "trade_plan_20260722.json"
        assert plan_path.exists()
        loaded = json.loads(plan_path.read_text(encoding="utf-8"))
        assert loaded["trade_date"] == "2026-07-22"

    @pytest.mark.unit
    def test_save_trade_plan_backs_up_existing(self, integrator, tmp_path):
        plan_path = tmp_path / "trade_plans" / "trade_plan_20260722.json"
        plan_path.write_text(json.dumps({"old": True}), encoding="utf-8")
        integrator._save_trade_plan({"new": True}, "2026-07-22")
        # 新文件写入
        loaded = json.loads(plan_path.read_text(encoding="utf-8"))
        assert loaded == {"new": True}
        # 备份文件存在
        bak_files = list(plan_path.parent.glob("trade_plan_20260722.json.bak_*"))
        assert len(bak_files) == 1


# ============================================================
# guard_drawdown 测试
# ============================================================


class TestGuardDrawdown:
    """guard_drawdown: Level 0/1/2/3/4 + ImportError + 无成本"""

    @pytest.mark.unit
    def test_import_error_returns_plan(self, integrator, monkeypatch):
        _block_module(monkeypatch, "utils.drawdown_controller")
        plan = _make_plan()
        result = integrator.guard_drawdown(_make_pnl_report(), plan)
        assert result is plan

    @pytest.mark.unit
    def test_no_cost_data_returns_plan(self, integrator, monkeypatch):
        _, mock_cls, mock_inst = _mock_module(
            monkeypatch, "utils.drawdown_controller", "DrawdownController"
        )
        report = _make_pnl_report(total_cost=0)
        plan = _make_plan()
        integrator.guard_drawdown(report, plan)
        # 无成本 → 直接返回, 不调用 check_drawdown
        mock_inst.check_drawdown.assert_not_called()

    @pytest.mark.unit
    def test_level0_normal(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.drawdown_controller", "DrawdownController"
        )
        mock_inst.check_drawdown.return_value = {"level": 0, "drawdown_pct": 0.02}
        plan = _make_plan()
        result = integrator.guard_drawdown(_make_pnl_report(), plan)
        assert result["risk_guard"]["drawdown_level"] == 0
        assert result["risk_guard"]["drawdown_action"] == "NORMAL"

    @pytest.mark.unit
    def test_level1_warning(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.drawdown_controller", "DrawdownController"
        )
        mock_inst.check_drawdown.return_value = {"level": 1, "drawdown_pct": 0.06}
        plan = _make_plan()
        result = integrator.guard_drawdown(_make_pnl_report(), plan)
        assert result["risk_guard"]["drawdown_level"] == 1
        assert result["risk_guard"]["drawdown_action"] == "WARNING"

    @pytest.mark.unit
    def test_level2_reduce_20pct(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.drawdown_controller", "DrawdownController"
        )
        mock_inst.check_drawdown.return_value = {"level": 2, "drawdown_pct": 0.09}
        plan = _make_plan()
        original_shares = plan["execution_plan"]["morning_orders"][0]["shares"]
        result = integrator.guard_drawdown(_make_pnl_report(), plan)
        assert result["risk_guard"]["drawdown_level"] == 2
        assert result["risk_guard"]["drawdown_action"] == "REDUCE_20PCT"
        # 预算缩减 20%
        assert result["phase"]["daily_capital"] == 150_000 * 0.8
        # 订单 shares 也缩减
        assert result["execution_plan"]["morning_orders"][0]["shares"] == int(
            original_shares * 0.8
        )

    @pytest.mark.unit
    def test_level3_reduce_60pct_clears_orders(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.drawdown_controller", "DrawdownController"
        )
        mock_inst.check_drawdown.return_value = {"level": 3, "drawdown_pct": 0.13}
        plan = _make_plan()
        result = integrator.guard_drawdown(_make_pnl_report(), plan)
        assert result["risk_guard"]["drawdown_level"] == 3
        assert result["risk_guard"]["drawdown_action"] == "REDUCE_60PCT_PAUSE_BUILD"
        assert result["execution_plan"]["morning_orders"] == []
        assert result["execution_plan"]["afternoon_orders"] == []
        assert result["market_state"]["spot_build_allowed"] is False

    @pytest.mark.unit
    def test_level4_full_stop(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.drawdown_controller", "DrawdownController"
        )
        mock_inst.check_drawdown.return_value = {"level": 4, "drawdown_pct": 0.16}
        plan = _make_plan()
        result = integrator.guard_drawdown(_make_pnl_report(), plan)
        assert result["risk_guard"]["drawdown_level"] == 4
        assert result["risk_guard"]["drawdown_action"] == "FULL_STOP_LIQUIDATE"
        assert result["execution_plan"]["morning_orders"] == []
        assert result["market_state"]["build_allowed"] is False
        assert result["market_state"]["circuit_level"] == "CRITICAL"


# ============================================================
# _apply_budget_cut / _apply_hedge_boost 测试
# ============================================================


class TestApplyBudgetCutAndHedgeBoost:
    """_apply_budget_cut / _apply_hedge_boost"""

    @pytest.mark.unit
    def test_budget_cut_reduces_capital_and_shares(self, integrator):
        plan = _make_plan()
        result = integrator._apply_budget_cut(plan, cut_ratio=0.30)
        assert result["phase"]["daily_capital"] == 150_000 * 0.7
        assert result["phase"]["day_capital"] == 150_000 * 0.7
        assert result["phase"]["budget_cut_reason"] == "drawdown_cut_30%"
        # 订单 shares 缩减
        assert result["execution_plan"]["morning_orders"][0]["shares"] == int(
            1000 * 0.7
        )

    @pytest.mark.unit
    def test_budget_cut_default_budget(self, integrator):
        plan = {"phase": {}, "execution_plan": {"morning_orders": []}}
        result = integrator._apply_budget_cut(plan, cut_ratio=0.20)
        # 默认 150000
        assert result["phase"]["daily_capital"] == 150_000 * 0.8

    @pytest.mark.unit
    def test_hedge_boost_increases_ratio(self, integrator):
        plan = _make_plan()
        result = integrator._apply_hedge_boost(plan, boost_pct=0.50)
        layer1 = result["hedge_config"]["layers"]["layer1_futures"]
        assert layer1["ratio"] == min(0.15 * 1.5, 0.60)
        assert "boost_reason" in layer1

    @pytest.mark.unit
    def test_hedge_boost_capped_at_060(self, integrator):
        plan = {"hedge_config": {"layers": {"layer1_futures": {"ratio": 0.50}}}}
        result = integrator._apply_hedge_boost(plan, boost_pct=0.50)
        layer1 = result["hedge_config"]["layers"]["layer1_futures"]
        assert layer1["ratio"] == 0.60  # min(0.75, 0.60)


# ============================================================
# guard_vol_target 测试
# ============================================================


class TestGuardVolTarget:
    """guard_vol_target: normal/scale_down/import/无收益率"""

    @pytest.mark.unit
    def test_import_error_returns_plan(self, integrator, monkeypatch):
        _block_module(monkeypatch, "utils.vol_target_controller")
        plan = _make_plan()
        result = integrator.guard_vol_target(_make_pnl_report(), plan)
        assert result is plan

    @pytest.mark.unit
    def test_no_daily_returns_normal(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.vol_target_controller", "VolTargetController"
        )
        monkeypatch.setattr(integrator, "_extract_daily_returns", lambda r: [])
        plan = _make_plan()
        result = integrator.guard_vol_target(_make_pnl_report(), plan)
        assert result["risk_guard"]["vol_action"] == "NORMAL"
        assert result["risk_guard"]["vol_scale"] is None

    @pytest.mark.unit
    def test_vol_scale_above_threshold(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.vol_target_controller", "VolTargetController"
        )
        mock_inst.calc_realized_vol.return_value = 0.10
        mock_inst.calc_vol_scale.return_value = 0.90
        monkeypatch.setattr(
            integrator, "_extract_daily_returns", lambda r: [0.01, 0.02]
        )
        plan = _make_plan()
        result = integrator.guard_vol_target(_make_pnl_report(), plan)
        assert result["risk_guard"]["vol_action"] == "NORMAL"
        assert result["risk_guard"]["vol_scale"] == 0.90

    @pytest.mark.unit
    def test_vol_scale_below_threshold_scales_down(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.vol_target_controller", "VolTargetController"
        )
        mock_inst.calc_realized_vol.return_value = 0.25
        mock_inst.calc_vol_scale.return_value = 0.50
        monkeypatch.setattr(
            integrator, "_extract_daily_returns", lambda r: [0.01, 0.02]
        )
        plan = _make_plan()
        # 添加 BUY 订单 with side 字段
        plan["execution_plan"]["morning_orders"][0]["side"] = "BUY"
        plan["execution_plan"]["morning_orders"][1]["side"] = "SELL"
        plan["execution_plan"]["afternoon_orders"][0]["side"] = "BUY"
        result = integrator.guard_vol_target(_make_pnl_report(), plan)
        assert result["risk_guard"]["vol_action"] == "SCALE_DOWN_0.50"
        assert result["risk_guard"]["vol_scale"] == 0.50
        assert result["phase"]["daily_capital"] < 150_000
        assert "original_daily_capital" in result["phase"]
        assert "vol_scale_executed_summary" in result["risk_guard"]

    @pytest.mark.unit
    def test_realized_vol_none_normal(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.vol_target_controller", "VolTargetController"
        )
        mock_inst.calc_realized_vol.return_value = None
        monkeypatch.setattr(integrator, "_extract_daily_returns", lambda r: [0.01])
        plan = _make_plan()
        result = integrator.guard_vol_target(_make_pnl_report(), plan)
        assert result["risk_guard"]["vol_action"] == "NORMAL"


# ============================================================
# _extract_daily_returns 测试
# ============================================================


class TestExtractDailyReturns:
    """_extract_daily_returns"""

    @pytest.mark.unit
    def test_insufficient_files_returns_empty(self, integrator, tmp_path):
        # 只创建 3 个文件 (< 5)
        for i in range(3):
            report = {"portfolio_pnl": {"summary": {"total_pnl_pct": 1.0}}}
            (
                tmp_path / "reports" / f"daily_pnl_report_2026-07-{i+1:02d}.json"
            ).write_text(json.dumps(report), encoding="utf-8")
        result = integrator._extract_daily_returns({})
        assert result == []

    @pytest.mark.unit
    def test_sufficient_files_returns_returns(self, integrator, tmp_path):
        for i in range(6):
            report = {"portfolio_pnl": {"summary": {"total_pnl_pct": 1.0 + i}}}
            (
                tmp_path / "reports" / f"daily_pnl_report_2026-07-{i+1:02d}.json"
            ).write_text(json.dumps(report), encoding="utf-8")
        result = integrator._extract_daily_returns({})
        assert len(result) == 6
        assert result[0] == 0.01  # 1.0 / 100

    @pytest.mark.unit
    def test_fallback_to_pnl_summary_key(self, integrator, tmp_path):
        for i in range(6):
            report = {"pnl_summary": {"total_pnl_pct": 2.0}}
            (
                tmp_path / "reports" / f"daily_pnl_report_2026-07-{i+1:02d}.json"
            ).write_text(json.dumps(report), encoding="utf-8")
        result = integrator._extract_daily_returns({})
        assert len(result) == 6
        assert result[0] == 0.02


# ============================================================
# guard_hedge_execution 测试
# ============================================================


class TestGuardHedgeExecution:
    """guard_hedge_execution: within/over/options_only/none/error"""

    @pytest.mark.unit
    def test_import_error_returns_plan(self, integrator, monkeypatch):
        _block_module(monkeypatch, "utils.hedge_execution_engine")
        plan = _make_plan()
        result = integrator.guard_hedge_execution(
            _make_pnl_report(), plan, "2026-07-22"
        )
        assert result is plan

    @pytest.mark.unit
    def test_none_result_no_change(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.hedge_execution_engine", "HedgeExecutionEngine"
        )
        mock_inst.generate_hedge_orders.return_value = None
        plan = _make_plan()
        result = integrator.guard_hedge_execution(
            _make_pnl_report(), plan, "2026-07-22"
        )
        assert result["risk_guard"]["hedge_action"] == "NO_CHANGE_NEEDED"

    @pytest.mark.unit
    def test_within_budget_options_only(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.hedge_execution_engine", "HedgeExecutionEngine"
        )
        mock_inst.generate_hedge_orders.return_value = {
            "futures_orders": [],
            "options_orders": [
                {"instrument": "510050 Put", "contracts": 30, "est_price": 0.15}
            ],
            "cost_summary": {
                "within_budget": True,
                "total_cost": 50000,
                "budget_threshold": 100000,
                "hedge_mode": "OPTIONS_ONLY",
                "total_premium_budget": 50000,
                "total_margin_required": 0,
                "buffer_for_roll": 10000,
                "hedge_capital_usage_pct": 0.05,
            },
            "portfolio_status": {
                "portfolio_beta_before": 1.0,
                "target_beta_after": 0.5,
                "hedge_capital": 1000000,
            },
        }
        plan = _make_plan()
        result = integrator.guard_hedge_execution(
            _make_pnl_report(), plan, "2026-07-22"
        )
        assert result["risk_guard"]["hedge_action"] == "GENERATED_1_ORDERS"
        assert result["hedge_execution"]["execution_status"] == "PENDING"
        assert "OPTIONS_ONLY" in result["hedge_execution"]["execution_notes"][0]
        assert "futures_options_hedge" in result
        mock_inst.write_to_trade_plan.assert_called_once()

    @pytest.mark.unit
    def test_over_budget_cancelled(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.hedge_execution_engine", "HedgeExecutionEngine"
        )
        mock_inst.generate_hedge_orders.return_value = {
            "futures_orders": [
                {
                    "instrument": "IF",
                    "contracts": 2,
                    "est_price": 3800.0,
                    "notional": 760000,
                    "rationale": {"beta_to_hedge": 0.3},
                },
            ],
            "options_orders": [
                {"instrument": "510050 Put", "contracts": 30, "est_price": 0.15}
            ],
            "cost_summary": {
                "within_budget": False,
                "total_cost": 200000,
                "budget_threshold": 100000,
                "hedge_mode": "FUTURES_AND_OPTIONS",
                "total_premium_budget": 50000,
                "total_margin_required": 150000,
                "buffer_for_roll": 10000,
                "hedge_capital_usage_pct": 0.20,
            },
            "portfolio_status": {
                "portfolio_beta_before": 1.0,
                "target_beta_after": 0.5,
                "hedge_capital": 1000000,
            },
        }
        plan = _make_plan()
        result = integrator.guard_hedge_execution(
            _make_pnl_report(), plan, "2026-07-22"
        )
        assert "CANCELLED" in result["risk_guard"]["hedge_action"]
        assert result["hedge_execution"]["execution_status"] == "CANCELLED"
        # 订单状态被改写
        assert (
            result["hedge_execution"]["futures_orders"][0]["status"]
            == "CANCELLED_OVER_BUDGET"
        )
        assert (
            result["hedge_execution"]["options_orders"][0]["status"]
            == "CANCELLED_OVER_BUDGET"
        )

    @pytest.mark.unit
    def test_engine_exception_error(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.hedge_execution_engine", "HedgeExecutionEngine"
        )
        mock_inst.generate_hedge_orders.side_effect = ValueError("engine crashed")
        plan = _make_plan()
        result = integrator.guard_hedge_execution(
            _make_pnl_report(), plan, "2026-07-22"
        )
        assert "ERROR" in result["risk_guard"]["hedge_action"]


# ============================================================
# guard_protective_put 测试
# ============================================================


class TestGuardProtectivePut:
    """guard_protective_put: below/generate/existing/error/import"""

    @pytest.mark.unit
    def test_import_error_returns_plan(self, integrator, monkeypatch):
        _block_module(monkeypatch, "utils.protective_put_engine")
        plan = _make_plan()
        result = integrator.guard_protective_put(_make_pnl_report(), plan, "2026-07-22")
        assert result is plan

    @pytest.mark.unit
    def test_below_threshold(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.protective_put_engine", "ProtectivePutEngine"
        )
        report = _make_pnl_report(total_market_value=500_000)
        plan = _make_plan()
        result = integrator.guard_protective_put(report, plan, "2026-07-22")
        assert result["risk_guard"]["put_action"] == "BELOW_THRESHOLD"

    @pytest.mark.unit
    def test_generate_put_orders(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.protective_put_engine", "ProtectivePutEngine"
        )
        mock_inst.generate_put_orders.return_value = {
            "should_execute": True,
            "orders": [{"underlying": "510050", "contracts": 60}],
            "total_premium_est": 90000,
        }
        plan = _make_plan()
        result = integrator.guard_protective_put(_make_pnl_report(), plan, "2026-07-22")
        assert result["risk_guard"]["put_action"] == "GENERATED_1_PUTS"
        assert len(result["put_protection_orders"]) == 1

    @pytest.mark.unit
    def test_existing_protection_ok(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.protective_put_engine", "ProtectivePutEngine"
        )
        mock_inst.generate_put_orders.return_value = {
            "should_execute": False,
            "orders": [],
            "reason": "已有保护",
        }
        plan = _make_plan()
        result = integrator.guard_protective_put(_make_pnl_report(), plan, "2026-07-22")
        assert result["risk_guard"]["put_action"] == "EXISTING_PROTECTION_OK"

    @pytest.mark.unit
    def test_engine_exception_error(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.protective_put_engine", "ProtectivePutEngine"
        )
        mock_inst.generate_put_orders.side_effect = ValueError("crash")
        plan = _make_plan()
        result = integrator.guard_protective_put(_make_pnl_report(), plan, "2026-07-22")
        assert "ERROR" in result["risk_guard"]["put_action"]


# ============================================================
# guard_kill_switch 降级模式测试
# ============================================================


class TestGuardKillSwitchDegraded:
    """guard_kill_switch: 降级模式 (无 KillSwitch 模块)"""

    @pytest.mark.unit
    def test_degraded_l3_high_margin(self, integrator, monkeypatch):
        _block_module(monkeypatch, "utils.kill_switch")
        report = _make_pnl_report(margin_used=960_000, total_equity=1_000_000)
        plan = _make_plan()
        result = integrator.guard_kill_switch(report, plan)
        assert result["market_state"]["circuit_level"] == "CRITICAL"
        assert result["market_state"]["build_allowed"] is False
        assert result["execution_plan"]["morning_orders"] == []

    @pytest.mark.unit
    def test_degraded_l2_filters_buy(self, integrator, monkeypatch):
        _block_module(monkeypatch, "utils.kill_switch")
        report = _make_pnl_report(margin_used=800_000, total_equity=1_000_000)
        plan = _make_plan()
        result = integrator.guard_kill_switch(report, plan)
        assert result["market_state"]["circuit_level"] == "WARNING"
        assert result["market_state"]["build_allowed"] is False
        # BUY 被过滤, SELL 保留
        morning = result["execution_plan"]["morning_orders"]
        assert len(morning) == 1
        assert morning[0]["direction"] == "SELL"

    @pytest.mark.unit
    def test_degraded_l1_watch(self, integrator, monkeypatch):
        _block_module(monkeypatch, "utils.kill_switch")
        report = _make_pnl_report(margin_used=550_000, total_equity=1_000_000)
        plan = _make_plan()
        result = integrator.guard_kill_switch(report, plan)
        assert result["market_state"]["circuit_level"] == "WATCH"

    @pytest.mark.unit
    def test_degraded_ok_no_change(self, integrator, monkeypatch):
        _block_module(monkeypatch, "utils.kill_switch")
        report = _make_pnl_report(margin_used=300_000, total_equity=1_000_000)
        plan = _make_plan()
        original_morning = list(plan["execution_plan"]["morning_orders"])
        result = integrator.guard_kill_switch(report, plan)
        # OK 不改订单
        assert result["execution_plan"]["morning_orders"] == original_morning

    @pytest.mark.unit
    def test_degraded_p0d_none_margin_conservative(self, integrator, monkeypatch):
        """P0-D: margin_used=None, total_equity=None, 无 ks → 保守值 0.50 → L1"""
        _block_module(monkeypatch, "utils.kill_switch")
        report = _make_pnl_report(margin_used=None, total_equity=None)
        plan = _make_plan()
        result = integrator.guard_kill_switch(report, plan)
        # 0.50 → L1 WATCH
        assert result["market_state"]["circuit_level"] == "WATCH"


# ============================================================
# guard_kill_switch 集中度检查测试
# ============================================================


class TestGuardKillSwitchConcentration:
    """guard_kill_switch: 集中度检查与升级"""

    @pytest.mark.unit
    def test_concentration_upgrade_to_l2(self, integrator, monkeypatch):
        """保证金 OK 但集中度 L2 → 升级到 L2"""
        mock_inst = MagicMock()
        mock_inst._estimate_margin_from_positions.return_value = 0.30
        mock_inst.check_margin_status.return_value = {
            "level": 0,
            "margin_usage_ratio": 0.30,
            "can_trade": True,
            "can_open": True,
            "action": "正常",
        }
        mock_inst.check_concentration.return_value = {
            "level": "L2",
            "max_concentration": 0.40,
            "max_concentration_code": "588080",
        }
        mock_cls = MagicMock(return_value=mock_inst)
        mock_mod = MagicMock()
        mock_mod.KillSwitch = mock_cls
        monkeypatch.setitem(sys.modules, "utils.kill_switch", mock_mod)

        report = _make_pnl_report()
        plan = _make_plan()
        result = integrator.guard_kill_switch(report, plan)
        # 集中度 L2 升级 → 过滤 BUY
        assert result["market_state"]["circuit_level"] == "WARNING"

    @pytest.mark.unit
    def test_concentration_exception_handled(self, integrator, monkeypatch):
        """check_concentration 抛异常 → concentration_status=None, 不崩溃"""
        mock_inst = MagicMock()
        mock_inst._estimate_margin_from_positions.return_value = 0.30
        mock_inst.check_margin_status.return_value = {
            "level": 0,
            "margin_usage_ratio": 0.30,
            "can_trade": True,
            "can_open": True,
            "action": "正常",
        }
        mock_inst.check_concentration.side_effect = RuntimeError("data error")
        mock_cls = MagicMock(return_value=mock_inst)
        mock_mod = MagicMock()
        mock_mod.KillSwitch = mock_cls
        monkeypatch.setitem(sys.modules, "utils.kill_switch", mock_mod)

        report = _make_pnl_report()
        plan = _make_plan()
        result = integrator.guard_kill_switch(report, plan)
        # 不崩溃, margin level OK
        assert result is not None

    @pytest.mark.unit
    def test_concentration_with_est_market_value(self, integrator, monkeypatch):
        """positions 使用 est_market_value fallback"""
        mock_inst = MagicMock()
        mock_inst._estimate_margin_from_positions.return_value = 0.30
        mock_inst.check_margin_status.return_value = {
            "level": "OK",
            "margin_usage_ratio": 0.30,
            "can_trade": True,
            "can_open": True,
            "action": "正常",
        }
        mock_inst.check_concentration.return_value = {
            "level": "OK",
            "max_concentration": 0.1,
        }
        mock_cls = MagicMock(return_value=mock_inst)
        mock_mod = MagicMock()
        mock_mod.KillSwitch = mock_cls
        monkeypatch.setitem(sys.modules, "utils.kill_switch", mock_mod)

        report = {
            "portfolio_pnl": {
                "summary": {"margin_used": 300_000, "total_equity": 1_000_000},
                "details": [{"code": "600519.SH", "est_market_value": 200_000}],
            }
        }
        plan = _make_plan()
        result = integrator.guard_kill_switch(report, plan)
        assert result is not None
        mock_inst.check_concentration.assert_called_once()


# ============================================================
# guard_market_circuit_breaker 测试
# ============================================================


class TestGuardMarketCircuitBreaker:
    """guard_market_circuit_breaker"""

    @pytest.mark.unit
    def test_import_error_skip(self, integrator, monkeypatch):
        _block_module(monkeypatch, "utils.market_circuit_breaker")
        plan = _make_plan()
        result = integrator.guard_market_circuit_breaker(_make_pnl_report(), plan)
        assert result["risk_guard"]["market_circuit_breaker"]["status"] == "SKIP"

    @pytest.mark.unit
    def test_normal_level(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.market_circuit_breaker", "MarketCircuitBreaker"
        )
        mock_inst.check_market_status.return_value = {
            "level": 0,
            "hs300_change_pct": -0.01,
            "data_source": "akshare",
            "actions": [],
        }
        plan = _make_plan()
        result = integrator.guard_market_circuit_breaker(_make_pnl_report(), plan)
        assert result is not None

    @pytest.mark.unit
    def test_l2_triggered(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.market_circuit_breaker", "MarketCircuitBreaker"
        )
        mock_inst.check_market_status.return_value = {
            "level": 2,
            "hs300_change_pct": -0.05,
            "data_source": "akshare",
            "actions": ["halt_new_positions"],
        }
        plan = _make_plan()
        result = integrator.guard_market_circuit_breaker(_make_pnl_report(), plan)
        assert result is not None

    @pytest.mark.unit
    def test_crash_fail_closed(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.market_circuit_breaker", "MarketCircuitBreaker"
        )
        mock_inst.check_market_status.side_effect = OSError("network error")
        plan = _make_plan()
        result = integrator.guard_market_circuit_breaker(_make_pnl_report(), plan)
        # fail-closed: build_allowed=False
        assert result["market_state"]["build_allowed"] is False
        assert result["market_state"]["spot_build_allowed"] is False


# ============================================================
# guard_liquidity_crisis 测试
# ============================================================


class TestGuardLiquidityCrisis:
    """guard_liquidity_crisis: unavailable/triggered/normal/crash"""

    @pytest.mark.unit
    def test_data_unavailable_warning(self, integrator, monkeypatch):
        monkeypatch.setattr(
            integrator, "_fetch_limit_counts", lambda r: (0, 0, "fail_closed")
        )
        plan = _make_plan()
        result = integrator.guard_liquidity_crisis(_make_pnl_report(), plan)
        assert result["risk_guard"]["liquidity_crisis"]["triggered"] is False
        assert (
            result["risk_guard"]["liquidity_crisis"]["action"]
            == "DATA_UNAVAILABLE_NO_NEW_POSITIONS"
        )
        assert result["market_state"]["build_allowed"] is False
        assert result["market_state"]["circuit_level"] == "WARNING"

    @pytest.mark.unit
    def test_triggered_clears_orders(self, integrator, monkeypatch):
        monkeypatch.setattr(
            integrator, "_fetch_limit_counts", lambda r: (1500, 800, "akshare")
        )
        plan = _make_plan()
        result = integrator.guard_liquidity_crisis(_make_pnl_report(), plan)
        assert result["risk_guard"]["liquidity_crisis"]["triggered"] is True
        assert result["market_state"]["circuit_level"] == "CRITICAL"
        assert result["execution_plan"]["morning_orders"] == []
        assert result["execution_plan"]["afternoon_orders"] == []

    @pytest.mark.unit
    def test_normal(self, integrator, monkeypatch):
        monkeypatch.setattr(
            integrator, "_fetch_limit_counts", lambda r: (100, 50, "akshare")
        )
        plan = _make_plan()
        result = integrator.guard_liquidity_crisis(_make_pnl_report(), plan)
        assert result["risk_guard"]["liquidity_crisis"]["triggered"] is False
        assert result["risk_guard"]["liquidity_crisis"]["action"] == "NORMAL"

    @pytest.mark.unit
    def test_crash_fail_closed(self, integrator, monkeypatch):
        monkeypatch.setattr(
            integrator,
            "_fetch_limit_counts",
            lambda r: (_ for _ in ()).throw(ValueError("crash")),
        )
        plan = _make_plan()
        result = integrator.guard_liquidity_crisis(_make_pnl_report(), plan)
        assert result["market_state"]["build_allowed"] is False
        assert result["market_state"]["circuit_level"] == "WARNING"


# ============================================================
# _fetch_limit_counts 测试
# ============================================================


class TestFetchLimitCounts:
    """_fetch_limit_counts: akshare/astock_sample/fail_closed"""

    @pytest.mark.unit
    def test_akshare_available(self, integrator, monkeypatch):
        """akshare 可用 → 返回涨跌停统计"""
        mock_ak = MagicMock()
        # 用 MagicMock 构建 DataFrame-like 对象, 避免 numpy sum 兼容性问题
        mock_df = MagicMock()
        mock_df.empty = False
        mock_df.columns = ["涨跌幅"]
        pct_series = MagicMock()
        ge_result = MagicMock()
        ge_result.sum.return_value = 1
        le_result = MagicMock()
        le_result.sum.return_value = 1
        pct_series.__ge__ = MagicMock(return_value=ge_result)
        pct_series.__le__ = MagicMock(return_value=le_result)
        mock_df.__getitem__ = MagicMock(return_value=pct_series)
        mock_ak.stock_zh_a_spot_em.return_value = mock_df
        monkeypatch.setitem(sys.modules, "akshare", mock_ak)
        limit_up, limit_down, source = integrator._fetch_limit_counts(None)
        assert source == "akshare"
        assert limit_up == 1
        assert limit_down == 1

    @pytest.mark.unit
    def test_akshare_import_error_fallback_to_astock(self, integrator, monkeypatch):
        monkeypatch.setitem(sys.modules, "akshare", None)
        mock_astock = MagicMock()
        mock_astock.get_realtime_quotes.return_value = {
            "510050": {"change_pct": 10.0},
            "510300": {"change_pct": -10.0},
        }
        monkeypatch.setitem(sys.modules, "utils.astock_realtime", mock_astock)
        pnl_report = _make_pnl_report()
        limit_up, limit_down, source = integrator._fetch_limit_counts(pnl_report)
        assert source == "astock_sample"
        assert limit_up == 1
        assert limit_down == 1

    @pytest.mark.unit
    def test_all_sources_fail(self, integrator, monkeypatch):
        """akshare 不可用 + astock_realtime 调用异常 → fail_closed"""
        monkeypatch.setitem(sys.modules, "akshare", None)
        # 注意: 不能 block astock_realtime (源码 Layer2 不捕获 ImportError),
        # 改为 mock 模块让 get_realtime_quotes 抛被捕获的 RuntimeError
        mock_astock = MagicMock()
        mock_astock.get_realtime_quotes = MagicMock(side_effect=RuntimeError("no data"))
        monkeypatch.setitem(sys.modules, "utils.astock_realtime", mock_astock)
        pnl_report = _make_pnl_report()
        limit_up, limit_down, source = integrator._fetch_limit_counts(pnl_report)
        assert source == "fail_closed"
        assert limit_up == 0
        assert limit_down == 0

    @pytest.mark.unit
    def test_akshare_attribute_error(self, integrator, monkeypatch):
        """akshare 存在但 stock_zh_a_spot_em 抛 AttributeError"""
        mock_ak = MagicMock()
        mock_ak.stock_zh_a_spot_em.side_effect = AttributeError("no data")
        monkeypatch.setitem(sys.modules, "akshare", mock_ak)
        mock_astock = MagicMock()
        mock_astock.get_realtime_quotes = MagicMock(side_effect=RuntimeError("no data"))
        monkeypatch.setitem(sys.modules, "utils.astock_realtime", mock_astock)
        pnl_report = _make_pnl_report()
        limit_up, limit_down, source = integrator._fetch_limit_counts(pnl_report)
        assert source == "fail_closed"


# ============================================================
# guard_overnight_gap 测试
# ============================================================


class TestGuardOvernightGap:
    """guard_overnight_gap"""

    @pytest.mark.unit
    def test_import_error_skip(self, integrator, monkeypatch):
        _block_module(monkeypatch, "utils.overnight_gap_monitor")
        plan = _make_plan()
        result = integrator.guard_overnight_gap(_make_pnl_report(), plan)
        assert result["risk_guard"]["overnight_gap"]["status"] == "SKIP"

    @pytest.mark.unit
    def test_normal(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.overnight_gap_monitor", "OvernightGapMonitor"
        )
        mock_inst.evaluate_overnight_risk.return_value = {
            "level": 0,
            "sp500_change_pct": -0.005,
            "adr_deviation_pct": 0.01,
            "data_source": "yahoo",
            "trigger": "none",
        }
        plan = _make_plan()
        result = integrator.guard_overnight_gap(_make_pnl_report(), plan)
        assert result is not None

    @pytest.mark.unit
    def test_l1_warning(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.overnight_gap_monitor", "OvernightGapMonitor"
        )
        mock_inst.evaluate_overnight_risk.return_value = {
            "level": 1,
            "sp500_change_pct": -0.015,
            "adr_deviation_pct": 0.025,
            "data_source": "yahoo",
            "trigger": "sp500",
        }
        plan = _make_plan()
        result = integrator.guard_overnight_gap(_make_pnl_report(), plan)
        assert result is not None

    @pytest.mark.unit
    def test_l2_triggered(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.overnight_gap_monitor", "OvernightGapMonitor"
        )
        mock_inst.evaluate_overnight_risk.return_value = {
            "level": 2,
            "sp500_change_pct": -0.025,
            "adr_deviation_pct": 0.045,
            "data_source": "yahoo",
            "trigger": "sp500",
        }
        plan = _make_plan()
        result = integrator.guard_overnight_gap(_make_pnl_report(), plan)
        assert result is not None

    @pytest.mark.unit
    def test_non_dict_risk_handled(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.overnight_gap_monitor", "OvernightGapMonitor"
        )
        mock_inst.evaluate_overnight_risk.return_value = "not a dict"
        plan = _make_plan()
        result = integrator.guard_overnight_gap(_make_pnl_report(), plan)
        assert result is not None

    @pytest.mark.unit
    def test_crash_fail_closed(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.overnight_gap_monitor", "OvernightGapMonitor"
        )
        mock_inst.evaluate_overnight_risk.side_effect = OSError("network")
        plan = _make_plan()
        result = integrator.guard_overnight_gap(_make_pnl_report(), plan)
        assert result["market_state"]["build_allowed"] is False


# ============================================================
# guard_sentiment_breaking_news 测试
# ============================================================


def _make_insight(
    direction="positive",
    confidence=0.5,
    symbol="600519",
    name="贵州茅台",
    reasons=None,
    news_count=3,
):
    """构造 mock insight 对象"""
    insight = MagicMock()
    insight.direction = direction
    insight.confidence = confidence
    insight.symbol = symbol
    insight.name = name
    insight.reasons = reasons or ["正常波动"]
    insight.news_count = news_count
    return insight


class TestGuardSentimentBreakingNews:
    """guard_sentiment_breaking_news"""

    @pytest.mark.unit
    def test_feature_flag_off(self, integrator, monkeypatch):
        monkeypatch.setenv("USE_SENTIMENT_GUARD", "False")
        plan = _make_plan()
        result = integrator.guard_sentiment_breaking_news(_make_pnl_report(), plan)
        assert result["risk_guard"]["sentiment_breaking_news"]["status"] == "DISABLED"

    @pytest.mark.unit
    def test_no_holdings_skip(self, integrator, monkeypatch):
        monkeypatch.delenv("USE_SENTIMENT_GUARD", raising=False)
        report = {"portfolio_pnl": {"summary": {}}}
        plan = {
            "execution_plan": {"morning_orders": [], "afternoon_orders": []},
            "risk_guard": {},
        }
        result = integrator.guard_sentiment_breaking_news(report, plan)
        assert result["risk_guard"]["sentiment_breaking_news"]["status"] == "SKIP"
        assert (
            result["risk_guard"]["sentiment_breaking_news"]["reason"] == "no_holdings"
        )

    @pytest.mark.unit
    def test_import_error_skip(self, integrator, monkeypatch):
        _block_module(monkeypatch, "utils.ifind_news_analyzer")
        plan = _make_plan()
        result = integrator.guard_sentiment_breaking_news(_make_pnl_report(), plan)
        assert result["risk_guard"]["sentiment_breaking_news"]["status"] == "SKIP"
        assert (
            result["risk_guard"]["sentiment_breaking_news"]["reason"] == "import_failed"
        )

    @pytest.mark.unit
    def test_mcp_unavailable_skip(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.ifind_news_analyzer", "IFinDNewsAnalyzer"
        )
        mock_inst.available.return_value = False
        plan = _make_plan()
        result = integrator.guard_sentiment_breaking_news(_make_pnl_report(), plan)
        assert result["risk_guard"]["sentiment_breaking_news"]["status"] == "SKIP"
        assert (
            result["risk_guard"]["sentiment_breaking_news"]["reason"]
            == "ifind_mcp_unavailable"
        )

    @pytest.mark.unit
    def test_ok_no_critical_negatives(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.ifind_news_analyzer", "IFinDNewsAnalyzer"
        )
        mock_inst.available.return_value = True
        mock_inst.batch_analyze.return_value = [
            _make_insight(direction="positive", confidence=0.9),
            _make_insight(direction="negative", confidence=0.5),
        ]
        plan = _make_plan()
        result = integrator.guard_sentiment_breaking_news(_make_pnl_report(), plan)
        assert result["risk_guard"]["sentiment_breaking_news"]["status"] == "OK"
        assert result["risk_guard"]["sentiment_breaking_news"]["triggered"] is False

    @pytest.mark.unit
    def test_triggered_critical_negatives(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.ifind_news_analyzer", "IFinDNewsAnalyzer"
        )
        mock_inst.available.return_value = True
        mock_inst.batch_analyze.return_value = [
            _make_insight(
                direction="negative",
                confidence=0.95,
                symbol="600519",
                name="贵州茅台",
                reasons=["财务造假", "监管立案"],
            ),
        ]
        plan = _make_plan()
        result = integrator.guard_sentiment_breaking_news(_make_pnl_report(), plan)
        sg = result["risk_guard"]["sentiment_breaking_news"]
        assert sg["status"] == "TRIGGERED"
        assert sg["triggered"] is True
        assert "600519" in sg["triggered_symbols"]
        assert result["market_state"]["build_allowed"] is False
        assert result["market_state"]["spot_build_allowed"] is False
        # BUY 订单被拦截, SELL 保留
        morning = result["execution_plan"]["morning_orders"]
        assert all(
            str(o.get("action", o.get("direction", ""))).upper() in ("SELL", "REDUCE")
            for o in morning
        )

    @pytest.mark.unit
    def test_batch_analyze_exception_error(self, integrator, monkeypatch):
        _, _, mock_inst = _mock_module(
            monkeypatch, "utils.ifind_news_analyzer", "IFinDNewsAnalyzer"
        )
        mock_inst.available.return_value = True
        mock_inst.batch_analyze.side_effect = OSError("API error")
        plan = _make_plan()
        result = integrator.guard_sentiment_breaking_news(_make_pnl_report(), plan)
        assert result["risk_guard"]["sentiment_breaking_news"]["status"] == "ERROR"


# ============================================================
# _extract_holding_symbols 测试
# ============================================================


class TestExtractHoldingSymbols:
    """_extract_holding_symbols: 3种来源"""

    @pytest.mark.unit
    def test_from_pnl_report_positions(self, integrator):
        report = {
            "portfolio_pnl": {"details": [{"code": "600519.SH"}, {"code": "000858.SZ"}]}
        }
        plan = {"execution_plan": {}}
        result = integrator._extract_holding_symbols(report, plan)
        assert result == ["600519", "000858"]

    @pytest.mark.unit
    def test_from_plan_execution_plan(self, integrator):
        report = {"portfolio_pnl": {}}
        plan = {
            "execution_plan": {
                "morning_orders": [{"symbol": "600519.SH"}],
                "afternoon_orders": [{"symbol": "000858.SZ"}],
            }
        }
        result = integrator._extract_holding_symbols(report, plan)
        assert result == ["600519", "000858"]

    @pytest.mark.unit
    def test_from_plan_positions_dict(self, integrator):
        report = {"portfolio_pnl": {}}
        plan = {"execution_plan": {}, "positions": {"600519.SH": {}, "000858.SZ": {}}}
        result = integrator._extract_holding_symbols(report, plan)
        assert result == ["600519", "000858"]

    @pytest.mark.unit
    def test_from_plan_positions_list(self, integrator):
        report = {"portfolio_pnl": {}}
        plan = {"execution_plan": {}, "positions": [{"code": "600519.SH"}]}
        result = integrator._extract_holding_symbols(report, plan)
        assert result == ["600519"]

    @pytest.mark.unit
    def test_dedup_preserves_order(self, integrator):
        report = {
            "portfolio_pnl": {
                "details": [
                    {"code": "600519.SH"},
                    {"code": "000858.SZ"},
                    {"code": "600519.SH"},
                ]
            }
        }
        plan = {"execution_plan": {}}
        result = integrator._extract_holding_symbols(report, plan)
        assert result == ["600519", "000858"]

    @pytest.mark.unit
    def test_empty_returns_empty(self, integrator):
        assert integrator._extract_holding_symbols({}, {}) == []


# ============================================================
# guard_correlation_hedge 测试
# ============================================================


class TestGuardCorrelationHedge:
    """guard_correlation_hedge"""

    @pytest.mark.unit
    def test_import_error_skip(self, integrator, monkeypatch):
        _block_module(monkeypatch, "hedging.correlation_hedger")
        monkeypatch.setitem(sys.modules, "hedging", MagicMock())
        plan = _make_plan()
        result = integrator.guard_correlation_hedge(_make_pnl_report(), plan)
        assert result["risk_guard"]["correlation_hedge"]["status"] == "SKIP"

    @pytest.mark.unit
    def test_no_returns_data(self, integrator, monkeypatch):
        mock_mod, mock_cls, mock_inst = _mock_module(
            monkeypatch, "hedging.correlation_hedger", "CorrelationHedger"
        )
        monkeypatch.setitem(sys.modules, "hedging", MagicMock())
        monkeypatch.setattr(integrator, "_build_position_returns", lambda r, **kw: None)
        plan = _make_plan()
        result = integrator.guard_correlation_hedge(_make_pnl_report(), plan)
        assert result["risk_guard"]["correlation_hedge"]["action"] == "NO_DATA"

    @pytest.mark.unit
    def test_safe_haven_alloc(self, integrator, monkeypatch):
        mock_mod, mock_cls, mock_inst = _mock_module(
            monkeypatch, "hedging.correlation_hedger", "CorrelationHedger"
        )
        monkeypatch.setitem(sys.modules, "hedging", MagicMock())
        import pandas as pd

        returns_df = pd.DataFrame({"600519": [0.01, 0.02], "000858": [0.01, 0.02]})
        monkeypatch.setattr(
            integrator, "_build_position_returns", lambda r, **kw: returns_df
        )
        mock_inst.compute_hedge.return_value = {
            "action": "SAFE_HAVEN_ALLOC",
            "avg_corr": 0.90,
            "baseline_corr": 0.70,
            "jump": 0.20,
            "gold_weight": 0.05,
            "repo_weight": 0.03,
            "gold_value": 50000,
            "repo_value": 30000,
            "gold_etf": "518880",
            "repo_symbol": "GC001",
        }
        plan = _make_plan()
        result = integrator.guard_correlation_hedge(_make_pnl_report(), plan)
        assert result["risk_guard"]["correlation_hedge"]["action"] == "SAFE_HAVEN_ALLOC"
        assert result["risk_guard"]["correlation_hedge"]["gold_weight"] == 0.05
        assert "correlation_hedge_orders" in result
        assert len(result["correlation_hedge_orders"]) == 2

    @pytest.mark.unit
    def test_no_action(self, integrator, monkeypatch):
        mock_mod, mock_cls, mock_inst = _mock_module(
            monkeypatch, "hedging.correlation_hedger", "CorrelationHedger"
        )
        monkeypatch.setitem(sys.modules, "hedging", MagicMock())
        import pandas as pd

        returns_df = pd.DataFrame({"600519": [0.01, 0.02]})
        monkeypatch.setattr(
            integrator, "_build_position_returns", lambda r, **kw: returns_df
        )
        mock_inst.compute_hedge.return_value = {
            "action": "NO_ACTION",
            "avg_corr": 0.50,
            "baseline_corr": 0.50,
            "jump": 0.0,
            "reason": "条件未满足",
        }
        plan = _make_plan()
        result = integrator.guard_correlation_hedge(_make_pnl_report(), plan)
        assert result["risk_guard"]["correlation_hedge"]["action"] == "NO_ACTION"

    @pytest.mark.unit
    def test_crash_logged(self, integrator, monkeypatch):
        mock_mod, mock_cls, mock_inst = _mock_module(
            monkeypatch, "hedging.correlation_hedger", "CorrelationHedger"
        )
        monkeypatch.setitem(sys.modules, "hedging", MagicMock())
        import pandas as pd

        returns_df = pd.DataFrame({"600519": [0.01, 0.02]})
        monkeypatch.setattr(
            integrator, "_build_position_returns", lambda r, **kw: returns_df
        )
        mock_inst.compute_hedge.side_effect = ValueError("crash")
        plan = _make_plan()
        result = integrator.guard_correlation_hedge(_make_pnl_report(), plan)
        assert "correlation_hedge_error" in result["risk_guard"]


# ============================================================
# _build_position_returns 测试
# ============================================================


class TestBuildPositionReturns:
    """_build_position_returns"""

    @pytest.mark.unit
    def test_no_positions_returns_none(self, integrator):
        report = {"portfolio_pnl": {"summary": {}}}
        result = integrator._build_position_returns(report)
        assert result is None

    @pytest.mark.unit
    def test_insufficient_history_returns_none(self, integrator, tmp_path):
        # 只创建 5 个报告 (< 10)
        for i in range(5):
            report = {
                "portfolio_pnl": {
                    "details": [{"code": "600519.SH", "daily_pnl_pct": 1.0}]
                }
            }
            (
                tmp_path / "reports" / f"daily_pnl_report_2026-07-{i+1:02d}.json"
            ).write_text(json.dumps(report), encoding="utf-8")
        report = {"portfolio_pnl": {"details": [{"code": "600519.SH"}]}}
        result = integrator._build_position_returns(report)
        assert result is None

    @pytest.mark.unit
    def test_valid_history_returns_dataframe(self, integrator, tmp_path):
        for i in range(12):
            report = {
                "portfolio_pnl": {
                    "details": [
                        {"code": "600519.SH", "daily_pnl_pct": 1.0 + i * 0.1},
                        {"code": "000858.SZ", "daily_pnl_pct": 0.5 + i * 0.05},
                    ]
                }
            }
            (
                tmp_path / "reports" / f"daily_pnl_report_2026-07-{i+1:02d}.json"
            ).write_text(json.dumps(report), encoding="utf-8")
        report = {
            "portfolio_pnl": {
                "details": [
                    {"code": "600519.SH"},
                    {"code": "000858.SZ"},
                ]
            }
        }
        result = integrator._build_position_returns(report)
        assert result is not None
        assert len(result) == 12
        assert "600519.SH" in result.columns

    @pytest.mark.unit
    def test_fallback_pnl_over_market_value(self, integrator, tmp_path):
        """daily_pnl_pct 不存在时, 用 pnl/market_value"""
        for i in range(12):
            report = {
                "portfolio_pnl": {
                    "details": [
                        {"code": "600519.SH", "pnl": 1000, "market_value": 100000},
                    ]
                }
            }
            (
                tmp_path / "reports" / f"daily_pnl_report_2026-07-{i+1:02d}.json"
            ).write_text(json.dumps(report), encoding="utf-8")
        report = {"portfolio_pnl": {"details": [{"code": "600519.SH"}]}}
        result = integrator._build_position_returns(report)
        assert result is not None
        assert len(result) == 12


# ============================================================
# _build_safe_haven_orders 测试
# ============================================================


class TestBuildSafeHavenOrders:
    """_build_safe_haven_orders"""

    @pytest.mark.unit
    def test_gold_only(self, integrator):
        result = integrator._build_safe_haven_orders(
            {
                "gold_weight": 0.05,
                "gold_value": 50000,
                "repo_weight": 0,
                "repo_value": 0,
            }
        )
        assert len(result) == 1
        assert result[0]["symbol"] == "518880"
        assert result[0]["direction"] == "BUY"

    @pytest.mark.unit
    def test_repo_only(self, integrator):
        result = integrator._build_safe_haven_orders(
            {
                "gold_weight": 0,
                "gold_value": 0,
                "repo_weight": 0.03,
                "repo_value": 30000,
            }
        )
        assert len(result) == 1
        assert result[0]["symbol"] == "GC001"

    @pytest.mark.unit
    def test_both_gold_and_repo(self, integrator):
        result = integrator._build_safe_haven_orders(
            {
                "gold_weight": 0.05,
                "gold_value": 50000,
                "repo_weight": 0.03,
                "repo_value": 30000,
            }
        )
        assert len(result) == 2

    @pytest.mark.unit
    def test_neither(self, integrator):
        result = integrator._build_safe_haven_orders(
            {
                "gold_weight": 0,
                "repo_weight": 0,
            }
        )
        assert result == []


# ============================================================
# _deduplicate_put_orders 边界测试
# ============================================================


class TestDeduplicatePutOrdersEdge:
    """_deduplicate_put_orders: 补充边界"""

    @pytest.mark.unit
    def test_unrecognized_underlying_skip(self, integrator):
        """put_protection_orders 的 underlying 无法识别 → 跳过去重"""
        plan = {
            "put_protection_orders": [{"underlying": "UNKNOWN_ETF"}],
            "hedge_execution": {
                "options_orders": [{"instrument": "510050 Put"}],
                "futures_orders": [],
            },
        }
        original_options = list(plan["hedge_execution"]["options_orders"])
        integrator._deduplicate_put_orders(plan)
        # 无法识别 → 不剔除
        assert plan["hedge_execution"]["options_orders"] == original_options

    @pytest.mark.unit
    def test_empty_options_no_dedup(self, integrator):
        """options_orders 为空 → 无需去重"""
        plan = {
            "put_protection_orders": [{"underlying": "510050"}],
            "hedge_execution": {
                "options_orders": [],
                "futures_orders": [],
            },
        }
        integrator._deduplicate_put_orders(plan)
        assert plan["hedge_execution"]["options_orders"] == []


# ============================================================
# _extract_underlying_code 模糊匹配测试
# ============================================================


class TestExtractUnderlyingCodeFuzzy:
    """_extract_underlying_code: 模糊匹配路径"""

    @pytest.mark.unit
    def test_fuzzy_match_chinese_name(self, integrator):
        """包含但非直接匹配的中文名称"""
        assert integrator._extract_underlying_code("持有50etf") == "510050"
        assert integrator._extract_underlying_code("买入300etf") == "510300"
        assert integrator._extract_underlying_code("持有500etf") == "510500"

    @pytest.mark.unit
    def test_fuzzy_match_with_suffix(self, integrator):
        """带后缀的模糊匹配"""
        assert integrator._extract_underlying_code("上证50etf put") == "510050"
        assert integrator._extract_underlying_code("科创50etf option") == "588080"


# ============================================================
# run_all_guards 测试
# ============================================================


class TestRunAllGuards:
    """run_all_guards: 集成测试 + 一致性校验"""

    @pytest.mark.unit
    def test_run_all_guards_basic(self, integrator, monkeypatch, tmp_path):
        """基本运行: 所有模块不可用 → guards 走 ImportError 路径"""
        _block_all_optional_modules(monkeypatch)
        # Mock _fetch_limit_counts 避免 astock_realtime ImportError 未捕获
        monkeypatch.setattr(
            integrator, "_fetch_limit_counts", lambda r: (0, 0, "fail_closed")
        )

        report = _make_pnl_report(margin_used=300_000, total_equity=1_000_000)
        monkeypatch.setattr(integrator, "_load_pnl_report", lambda: report)

        plan = _make_plan()
        monkeypatch.setattr(integrator, "_load_next_trade_plan", lambda d: plan)

        result = integrator.run_all_guards("2026-07-22")
        assert result is not None
        assert "risk_guard" in result
        assert result["risk_guard"]["report_date"] == "2026-07-21"
        assert "last_run" in result["risk_guard"]
        plan_path = tmp_path / "trade_plans" / "trade_plan_20260722.json"
        assert plan_path.exists()

    @pytest.mark.unit
    def test_consistency_check_critical(self, integrator, monkeypatch, tmp_path):
        """circuit_level=CRITICAL → 强制 build_allowed=False"""
        _block_all_optional_modules(monkeypatch)
        monkeypatch.setattr(
            integrator, "_fetch_limit_counts", lambda r: (0, 0, "fail_closed")
        )

        report = _make_pnl_report(margin_used=960_000, total_equity=1_000_000)
        monkeypatch.setattr(integrator, "_load_pnl_report", lambda: report)

        plan = _make_plan()
        monkeypatch.setattr(integrator, "_load_next_trade_plan", lambda d: plan)

        result = integrator.run_all_guards("2026-07-22")
        # degraded 模式 margin_usage=0.96 → L3 → CRITICAL
        assert result["market_state"]["circuit_level"] == "CRITICAL"
        assert result["market_state"]["build_allowed"] is False
        assert result["market_state"]["spot_build_allowed"] is False

    @pytest.mark.unit
    def test_consistency_check_removes_covered_calls(
        self, integrator, monkeypatch, tmp_path
    ):
        """spot_build_allowed=False → 拦截 Covered Call 订单"""
        _block_all_optional_modules(monkeypatch)
        monkeypatch.setattr(
            integrator, "_fetch_limit_counts", lambda r: (0, 0, "fail_closed")
        )

        report = _make_pnl_report(margin_used=300_000, total_equity=1_000_000)
        monkeypatch.setattr(integrator, "_load_pnl_report", lambda: report)

        plan = _make_plan()
        plan["execution_plan"]["options_orders"] = [
            {
                "direction": "SELL_CALL",
                "name": "CoveredCall_600519",
                "est_premium_total": 500,
            },
            {"direction": "BUY_PUT", "name": "ProtectivePut", "est_premium_total": 300},
        ]
        monkeypatch.setattr(integrator, "_load_next_trade_plan", lambda d: plan)

        result = integrator.run_all_guards("2026-07-22")
        # spot_build_allowed=False (liquidity_crisis data_unavailable)
        assert result["market_state"]["spot_build_allowed"] is False
        remaining = result["execution_plan"]["options_orders"]
        assert all(o["direction"] != "SELL_CALL" for o in remaining)

    @pytest.mark.unit
    def test_no_pnl_report_uses_empty(self, integrator, monkeypatch):
        """无 pnl_report → 使用空报告继续"""
        _block_all_optional_modules(monkeypatch)
        monkeypatch.setattr(
            integrator, "_fetch_limit_counts", lambda r: (0, 0, "fail_closed")
        )

        monkeypatch.setattr(integrator, "_load_pnl_report", lambda: None)
        monkeypatch.setattr(integrator, "_load_next_trade_plan", lambda d: None)

        result = integrator.run_all_guards("2026-07-22")
        assert result is not None


# ============================================================
# _write_guard_log 测试
# ============================================================


class TestWriteGuardLog:
    """_write_guard_log"""

    @pytest.mark.unit
    def test_write_log_creates_file(self, integrator, tmp_path):
        integrator._log("测试条目1")
        integrator._log("测试条目2")
        integrator._write_guard_log("2026-07-22")
        log_file = tmp_path / "logs" / "risk_guard_20260722.log"
        assert log_file.exists()
        content = log_file.read_text(encoding="utf-8")
        assert "测试条目1" in content
        assert "测试条目2" in content


# ============================================================
# main() CLI 入口测试
# ============================================================


class TestMain:
    """main() CLI 入口"""

    @pytest.mark.unit
    def test_main_with_explicit_dates(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["prog", "2026-07-21", "2026-07-22"])
        with patch("utils.risk_guard_integrator.RiskGuardIntegrator") as mock_cls:
            main()
        mock_cls.assert_called_once_with(report_date="2026-07-21")
        mock_cls.return_value.run_all_guards.assert_called_once_with(
            next_trade_date="2026-07-22"
        )

    @pytest.mark.unit
    def test_main_no_next_date_calculated(self, monkeypatch):
        """无 next_date 参数 → 自动计算下一交易日"""
        monkeypatch.setattr(sys, "argv", ["prog", "2026-07-21"])
        with patch("utils.risk_guard_integrator.RiskGuardIntegrator") as mock_cls:
            main()
        mock_cls.assert_called_once_with(report_date="2026-07-21")
        call_kwargs = mock_cls.return_value.run_all_guards.call_args
        # 2026-07-21 是周二, 次日 2026-07-22 是周三
        assert call_kwargs.kwargs["next_trade_date"] == "2026-07-22"

    @pytest.mark.unit
    def test_main_no_args_uses_today(self, monkeypatch):
        """无参数 → 使用今天日期"""
        monkeypatch.setattr(sys, "argv", ["prog"])
        with patch("utils.risk_guard_integrator.RiskGuardIntegrator") as mock_cls:
            main()
        mock_cls.return_value.run_all_guards.assert_called_once()
        # report_date 应为今天
        expected_date = now_bj().strftime("%Y-%m-%d")
        mock_cls.assert_called_once_with(report_date=expected_date)

    @pytest.mark.unit
    def test_main_weekend_skip(self, monkeypatch):
        """周五的次日应跳过周末到周一"""
        monkeypatch.setattr(sys, "argv", ["prog", "2026-07-24"])  # 周五
        with patch("utils.risk_guard_integrator.RiskGuardIntegrator") as mock_cls:
            main()
        call_kwargs = mock_cls.return_value.run_all_guards.call_args
        assert call_kwargs.kwargs["next_trade_date"] == "2026-07-27"  # 周一
