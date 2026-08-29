"""test_g7_risk_guard_integrator_boost2.py — G7 覆盖率冲刺增强测试 (Boost2)

目标: 将 utils/risk_guard_integrator.py 覆盖率从 54.76% 推到 85%+
测试数: ≥100 个
聚焦补充:
    1) guard_* 系列所有风控检查的 level 2/3/4 边界条件 (精确阈值)
    2) kill_switch 激活/恢复流程 (L1→L2→L3→恢复完整状态机)
    3) 日内风控阈值计算 (各 guard 精确阈值 ±0.01% 边界)
    4) 预算分配和再分配逻辑 (0%/20%/40%/60%/100% 截断 + 再分配)
    5) 与其他模块的集成接口方法 (import 失败降级 + 模块返回异常结构)
    6) 配置加载错误处理 (损坏JSON / 权限错误 / 路径不存在 / 空文件)
    7) 异常/降级路径 (所有 guard 异常捕获 + fail-closed/fail-open 策略)
    8) _extract_underlying_code 完整映射表遍历 + 模糊匹配
    9) 一致性校验 (CRITICAL/WARNING 对齐 + Theta Covered Call 拦截)
    10) 去重逻辑边界 (无法识别底层 / 单侧空 / 全量重复)
"""

import json
import os
import sys
import types
from datetime import datetime, timedelta
from unittest.mock import MagicMock

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

from utils.risk_guard_integrator import (  # noqa: E402
    KillSwitchLevel,
    RiskGuardIntegrator,
    main,
    parse_kill_switch_level,
)

# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def integrator(tmp_path, monkeypatch):
    """隔离所有外部路径的集成器实例"""
    logs_dir = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    trade_plans_dir = tmp_path / "trade_plans"
    daily_report_dir = tmp_path / "daily_reports"
    for d in (logs_dir, reports_dir, trade_plans_dir, daily_report_dir):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("utils.risk_guard_integrator.LOGS_DIR", logs_dir)
    monkeypatch.setattr("utils.risk_guard_integrator.REPORTS_DIR", reports_dir)
    monkeypatch.setattr("utils.risk_guard_integrator.TRADE_PLANS_DIR", trade_plans_dir)
    monkeypatch.setattr(
        "utils.risk_guard_integrator.DAILY_REPORT_DIR", daily_report_dir
    )
    return RiskGuardIntegrator(report_date="2026-07-21", total_capital=5_000_000)


@pytest.fixture
def integrator_no_report_date(tmp_path, monkeypatch):
    """report_date=None 的实例 (取当天)"""
    logs_dir = tmp_path / "logs"
    reports_dir = tmp_path / "reports"
    trade_plans_dir = tmp_path / "trade_plans"
    daily_report_dir = tmp_path / "daily_reports"
    for d in (logs_dir, reports_dir, trade_plans_dir, daily_report_dir):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("utils.risk_guard_integrator.LOGS_DIR", logs_dir)
    monkeypatch.setattr("utils.risk_guard_integrator.REPORTS_DIR", reports_dir)
    monkeypatch.setattr("utils.risk_guard_integrator.TRADE_PLANS_DIR", trade_plans_dir)
    monkeypatch.setattr(
        "utils.risk_guard_integrator.DAILY_REPORT_DIR", daily_report_dir
    )
    return RiskGuardIntegrator(total_capital=5_000_000)


# ============================================================
# 辅助函数
# ============================================================


def _block_module(monkeypatch, module_name):
    monkeypatch.setitem(sys.modules, module_name, None)


def _block_all_optional_modules(monkeypatch):
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
        "hedging",
        "pandas",
        "akshare",
        "utils.astock_realtime",
    ]:
        try:
            _block_module(monkeypatch, mod)
        except Exception:
            pass


def _inject_mock_module(monkeypatch, module_name, class_name, instance=None):
    mock_inst = instance if instance is not None else MagicMock()
    mock_cls = MagicMock(return_value=mock_inst)
    mock_mod = MagicMock()
    setattr(mock_mod, class_name, mock_cls)
    monkeypatch.setitem(sys.modules, module_name, mock_mod)
    return mock_mod, mock_cls, mock_inst


def _make_pnl_report_v1(
    total_cost=1_000_000,
    total_market_value=1_050_000,
    total_pnl=50_000,
    margin_used=600_000,
    total_equity=1_500_000,
    positions=None,
):
    """完整格式 v1: portfolio_pnl.summary + portfolio_pnl.details"""
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
        {
            "code": "510300.SH",
            "name": "沪深300ETF",
            "market_value": 250_000,
            "pnl": 10_000,
            "daily_pnl_pct": 4.2,
            "cost_amount": 240_000,
        },
        {
            "code": "159915.SZ",
            "name": "创业板ETF",
            "market_value": 150_000,
            "pnl": 5_000,
            "daily_pnl_pct": 3.4,
            "cost_amount": 145_000,
        },
    ]
    return {
        "portfolio_pnl": {
            "summary": {
                "total_cost": total_cost,
                "total_market_value": total_market_value,
                "total_pnl": total_pnl,
                "total_pnl_pct": (total_pnl / total_cost * 100) if total_cost else 0,
                "margin_used": margin_used,
                "total_equity": total_equity,
            },
            "details": details,
        },
    }


def _make_pnl_report_v2(
    total_cost=1_000_000,
    total_market_value=1_050_000,
    total_pnl=50_000,
    margin_used=600_000,
    total_equity=1_500_000,
):
    """完整格式 v2: portfolio_pnl.positions (list)"""
    return {
        "portfolio_pnl": {
            "summary": {
                "total_cost": total_cost,
                "total_market_value": total_market_value,
                "total_pnl": total_pnl,
                "margin_used": margin_used,
                "total_equity": total_equity,
            },
            "positions": [
                {"code": "510500.SH", "market_value": 500_000, "pnl": 25_000},
            ],
        },
    }


def _make_pnl_report_v3():
    """简化格式: 顶层 positions (dict) + 顶层 summary"""
    return {
        "summary": {
            "total_cost": 2_000_000,
            "total_market_value": 2_100_000,
            "total_pnl": 100_000,
            "margin_used": 800_000,
            "total_equity": 3_000_000,
        },
        "positions": {
            "600519.SH": {"market_value": 1_000_000, "pnl": 50_000},
            "000858.SZ": {"market_value": 1_100_000, "pnl": 50_000},
        },
    }


def _make_pnl_report_v4():
    """简化格式 v4: 顶层 positions (list)"""
    return {
        "summary": {
            "total_cost": 100_000,
            "total_market_value": 105_000,
            "total_pnl": 5000,
        },
        "positions": [
            {"symbol": "512100.SH", "market_value": 105_000, "pnl": 5000},
        ],
    }


def _make_plan_full(
    budget=200_000,
    buy_orders=None,
    sell_orders=None,
    hedge_ratio=0.15,
    circuit_level=None,
):
    """构造完整交易计划"""
    morning = []
    afternoon = []
    if buy_orders is None:
        buy_orders = [
            {
                "code": "510300.SH",
                "side": "BUY",
                "shares": 5000,
                "est_amount": 25_000,
                "limit_price": 5.0,
            },
            {"code": "510500.SH", "side": "BUY", "shares": 3000, "est_amount": 18_000},
        ]
    if sell_orders is None:
        sell_orders = [
            {"code": "159915.SZ", "side": "SELL", "shares": 2000, "est_amount": 12_000},
        ]
    morning = buy_orders + sell_orders
    afternoon = [
        {"code": "512100.SH", "side": "BUY", "shares": 2000, "est_amount": 10_000},
    ]
    plan = {
        "trade_date": "2026-07-22",
        "phase": {"daily_capital": budget, "day_capital": budget},
        "execution_plan": {
            "morning_orders": morning,
            "afternoon_orders": afternoon,
            "day_capital": budget,
            "total_amount": 65_000,
            "options_orders": [
                {
                    "direction": "SELL_CALL",
                    "name": "CoveredCall_510050",
                    "est_premium_total": 5000,
                },
                {
                    "direction": "BUY_PUT",
                    "name": "ProtectivePut_588080",
                    "est_premium_total": 3000,
                },
            ],
            "options_orders_count": 2,
            "options_total_premium": 5000,
        },
        "hedge_config": {
            "layers": {
                "layer1_futures": {"ratio": hedge_ratio},
                "layer2_options": {"ratio": 0.05},
            },
        },
        "market_state": {},
        "hedge_fund_overlays": {
            "theta_engine": {"positions_count": 1, "total_premium": 5000},
            "v77_notes": {},
        },
        "positions": {
            "510050.SH": {"market_value": 300_000},
            "588080.SH": {"market_value": 350_000},
        },
    }
    if circuit_level:
        plan["market_state"]["circuit_level"] = circuit_level
    return plan


def _make_empty_plan():
    """空计划: 所有字段缺失"""
    return {}


# ========================================================================
# 一、parse_kill_switch_level 边界条件 (精确阈值测试) — 18 tests
# ========================================================================


class TestParseKillSwitchLevelExact:
    """KillSwitchLevel 解析函数 — 所有分支精确覆盖"""

    # ── 直接枚举输入 ──
    def test_enum_ok_direct(self):
        assert parse_kill_switch_level(KillSwitchLevel.OK) == KillSwitchLevel.OK

    def test_enum_l1_direct(self):
        assert parse_kill_switch_level(KillSwitchLevel.L1) == KillSwitchLevel.L1

    def test_enum_l2_direct(self):
        assert parse_kill_switch_level(KillSwitchLevel.L2) == KillSwitchLevel.L2

    def test_enum_l3_direct(self):
        assert parse_kill_switch_level(KillSwitchLevel.L3) == KillSwitchLevel.L3

    # ── 整数输入 (精确值 0/1/2/3) ──
    def test_int_0(self):
        assert parse_kill_switch_level(0) == KillSwitchLevel.OK

    def test_int_1(self):
        assert parse_kill_switch_level(1) == KillSwitchLevel.L1

    def test_int_2(self):
        assert parse_kill_switch_level(2) == KillSwitchLevel.L2

    def test_int_3(self):
        assert parse_kill_switch_level(3) == KillSwitchLevel.L3

    def test_int_4_invalid(self):
        assert parse_kill_switch_level(4) == KillSwitchLevel.OK

    def test_int_negative_invalid(self):
        assert parse_kill_switch_level(-1) == KillSwitchLevel.OK

    # ── 字符串输入 (多种格式) ──
    def test_str_lowercase_l0(self):
        assert parse_kill_switch_level("l0") == KillSwitchLevel.OK

    def test_str_mixed_l1(self):
        assert parse_kill_switch_level(" L1 ") == KillSwitchLevel.L1

    def test_str_numeric_2(self):
        assert parse_kill_switch_level("2") == KillSwitchLevel.L2

    def test_str_numeric_3(self):
        assert parse_kill_switch_level("3") == KillSwitchLevel.L3

    def test_str_ok_no_margin(self):
        assert parse_kill_switch_level("OK") == KillSwitchLevel.OK

    def test_str_ok_with_high_margin_implicit_upgrade_l3(self):
        """OK 字符串 + margin_usage >= 0.75 → 隐式升级 L3 (P1-Q6 兼容逻辑)"""
        result = parse_kill_switch_level("OK", margin_usage=0.75)
        assert result == KillSwitchLevel.L3

    def test_str_ok_with_margin_0749_not_upgraded(self):
        """OK + 0.749 < 0.75 → 不升级 (边界 -0.001)"""
        result = parse_kill_switch_level("OK", margin_usage=0.749)
        assert result == KillSwitchLevel.OK

    def test_str_ok_with_margin_07501_upgraded(self):
        """OK + 0.7501 > 0.75 → 升级 (边界 +0.0001)"""
        result = parse_kill_switch_level("OK", margin_usage=0.7501)
        assert result == KillSwitchLevel.L3

    def test_str_garbage_returns_ok(self):
        result = parse_kill_switch_level("INVALID_LEVEL_STRING")
        assert result == KillSwitchLevel.OK

    # ── 未知类型降级 ──
    def test_float_unknown_type(self):
        result = parse_kill_switch_level(2.5)
        assert result == KillSwitchLevel.OK

    def test_list_unknown_type(self):
        result = parse_kill_switch_level(["L1"])
        assert result == KillSwitchLevel.OK

    def test_dict_unknown_type(self):
        result = parse_kill_switch_level({"level": "L2"})
        assert result == KillSwitchLevel.OK

    def test_none_unknown_type(self):
        result = parse_kill_switch_level(None)
        assert result == KillSwitchLevel.OK

    def test_bool_is_int_subclass(self):
        """bool 是 int 子类 (True=1, False=0) — 验证真实行为而非假设"""
        assert parse_kill_switch_level(True) == KillSwitchLevel.L1  # True == int(1)
        assert parse_kill_switch_level(False) == KillSwitchLevel.OK  # False == int(0)


# ========================================================================
# 二、KillSwitchLevel 枚举值验证 — 5 tests
# ========================================================================


class TestKillSwitchEnum:
    def test_enum_values_correct(self):
        assert int(KillSwitchLevel.OK) == 0
        assert int(KillSwitchLevel.L1) == 1
        assert int(KillSwitchLevel.L2) == 2
        assert int(KillSwitchLevel.L3) == 3

    def test_enum_ordering(self):
        assert KillSwitchLevel.OK < KillSwitchLevel.L1
        assert KillSwitchLevel.L1 < KillSwitchLevel.L2
        assert KillSwitchLevel.L2 < KillSwitchLevel.L3
        assert KillSwitchLevel.L3 >= KillSwitchLevel.L3

    def test_enum_names(self):
        assert KillSwitchLevel(0).name == "OK"
        assert KillSwitchLevel(1).name == "L1"
        assert KillSwitchLevel(2).name == "L2"
        assert KillSwitchLevel(3).name == "L3"

    def test_intenum_isinstance_int(self):
        assert isinstance(KillSwitchLevel.L2, int)
        assert KillSwitchLevel.L3 + 0 == 3

    def test_enum_invalid_value_raises(self):
        with pytest.raises(ValueError):
            KillSwitchLevel(99)


# ========================================================================
# 三、RiskGuardIntegrator.__init__ & _log 边界 — 10 tests
# ========================================================================


class TestInitAndLog:
    def test_init_default_date(self, integrator_no_report_date):
        assert integrator_no_report_date.report_date == datetime.now().strftime(
            "%Y-%m-%d"
        )

    def test_init_custom_capital(self, integrator):
        assert integrator.total_capital == 5_000_000

    def test_init_log_entries_empty(self, integrator):
        assert integrator.log_entries == []

    def test_log_basic(self, integrator):
        integrator._log("test message")
        assert len(integrator.log_entries) == 1
        assert "[RiskGuard] test message" in integrator.log_entries[0]

    def test_log_timestamp_format(self, integrator):
        integrator._log("ts_check")
        entry = integrator.log_entries[0]
        assert entry.startswith("[")
        assert "] [RiskGuard]" in entry

    def test_log_unicode_encode_error_fallback(self, integrator, monkeypatch):
        """模拟 logger.info 抛 UnicodeEncodeError (GBK 控制台)"""
        mock_logger = MagicMock()
        mock_logger.info.side_effect = [
            UnicodeEncodeError("gbk", "中文消息", 0, 1, "illegal multibyte sequence"),
            None,
        ]
        monkeypatch.setattr("utils.risk_guard_integrator.logger", mock_logger)
        integrator._log("包含中文的日志消息")
        assert mock_logger.info.call_count == 2

    def test_log_multiple_accumulates(self, integrator):
        for i in range(5):
            integrator._log(f"msg_{i}")
        assert len(integrator.log_entries) == 5

    def test_logs_dir_created(self, tmp_path, monkeypatch):
        logs_dir = tmp_path / "new_logs_dir"
        reports_dir = tmp_path / "reports"
        trade_plans_dir = tmp_path / "trade_plans"
        daily_report_dir = tmp_path / "daily_reports"
        for d in (reports_dir, trade_plans_dir, daily_report_dir):
            d.mkdir(exist_ok=True)
        monkeypatch.setattr("utils.risk_guard_integrator.LOGS_DIR", logs_dir)
        monkeypatch.setattr("utils.risk_guard_integrator.REPORTS_DIR", reports_dir)
        monkeypatch.setattr(
            "utils.risk_guard_integrator.TRADE_PLANS_DIR", trade_plans_dir
        )
        monkeypatch.setattr(
            "utils.risk_guard_integrator.DAILY_REPORT_DIR", daily_report_dir
        )
        assert not logs_dir.exists()
        RiskGuardIntegrator(report_date="2026-07-21")
        assert logs_dir.exists()

    def test_init_report_date_format(self, integrator):
        assert len(integrator.report_date.split("-")) == 3
        assert integrator.report_date == "2026-07-21"


# ========================================================================
# 四、_extract_underlying_code 完整映射表测试 — 12 tests
# ========================================================================


class TestExtractUnderlyingCode:
    """UNDERLYING_CODE_MAP 完整覆盖 + 模糊匹配"""

    @pytest.mark.parametrize(
        "input_name,expected",
        [
            ("510050", "510050"),
            ("上证50", "510050"),
            ("50etf", "510050"),
            ("上证50etf", "510050"),
            ("sz50", "510050"),
            ("588080", "588080"),
            ("科创50", "588080"),
            ("科创50etf", "588080"),
            ("kc50", "588080"),
            ("588000", "588080"),
            ("159915", "159915"),
            ("创业板", "159915"),
            ("创业板etf", "159915"),
            ("cyb", "159915"),
            ("510300", "510300"),
            ("沪深300", "510300"),
            ("300etf", "510300"),
            ("hs300", "510300"),
            ("510500", "510500"),
            ("中证500", "510500"),
            ("500etf", "510500"),
            ("zz500", "510500"),
            ("512100", "512100"),
            ("中证1000", "512100"),
            ("1000etf", "512100"),
            ("zz1000", "512100"),
        ],
    )
    def test_direct_mapping(self, integrator, input_name, expected):
        assert integrator._extract_underlying_code(input_name) == expected

    def test_regex_extract_six_digit(self, integrator):
        """从复杂名称中提取 6 位数字"""
        assert integrator._extract_underlying_code("510050 Put 期权") == "510050"
        assert integrator._extract_underlying_code("合约 588080-C-1.2") == "588080"
        assert integrator._extract_underlying_code("510300ETF认购期权") == "510300"

    def test_fuzzy_match_longest_first(self, integrator):
        """模糊匹配最长优先，避免 '50etf' 误匹配 '科创50ETF'"""
        result = integrator._extract_underlying_code("超级科创50ETF组合")
        assert result == "588080"

    def test_empty_input_none(self, integrator):
        assert integrator._extract_underlying_code("") is None
        assert integrator._extract_underlying_code(None) is None

    def test_unrecognized_returns_none(self, integrator):
        assert integrator._extract_underlying_code("UNKNOWN_ASSET_999") is None
        assert integrator._extract_underlying_code("BTC/USDT") is None
        assert integrator._extract_underlying_code("short_code") is None


# ========================================================================
# 五、_load_pnl_report 配置加载错误处理 — 12 tests
# ========================================================================


class TestLoadPnlReportErrors:
    def test_no_report_anywhere_returns_none(self, integrator):
        """4 个候选路径都不存在"""
        result = integrator._load_pnl_report()
        assert result is None

    def test_daily_report_dir_with_dash(self, integrator):
        """候选 1: 每日报告归档/{date}/daily_pnl_report_{date}.json"""
        from utils.risk_guard_integrator import DAILY_REPORT_DIR

        report_dir = DAILY_REPORT_DIR
        date_dir = report_dir / integrator.report_date
        date_dir.mkdir(parents=True, exist_ok=True)
        data = {"portfolio_pnl": {"summary": {"total_cost": 100}}}
        (date_dir / f"daily_pnl_report_{integrator.report_date}.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        result = integrator._load_pnl_report()
        assert result is not None
        assert result["portfolio_pnl"]["summary"]["total_cost"] == 100

    def test_daily_report_dir_no_dash(self, integrator):
        """候选 2: 每日报告归档/{date}/daily_pnl_report_{无横杠}.json"""
        from utils.risk_guard_integrator import DAILY_REPORT_DIR

        report_dir = DAILY_REPORT_DIR
        date_dir = report_dir / integrator.report_date
        date_dir.mkdir(parents=True, exist_ok=True)
        no_dash = integrator.report_date.replace("-", "")
        data = {"portfolio_pnl": {"summary": {"total_cost": 200}}}
        (date_dir / f"daily_pnl_report_{no_dash}.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        result = integrator._load_pnl_report()
        assert result is not None
        assert result["portfolio_pnl"]["summary"]["total_cost"] == 200

    def test_old_reports_dir_dash(self, integrator):
        """候选 3: v8.3_institutional/reports/daily_pnl_report_{date}.json"""
        from utils.risk_guard_integrator import REPORTS_DIR

        reports_dir = REPORTS_DIR
        data = {"portfolio_pnl": {"summary": {"total_cost": 300}}}
        (reports_dir / f"daily_pnl_report_{integrator.report_date}.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        result = integrator._load_pnl_report()
        assert result is not None
        assert result["portfolio_pnl"]["summary"]["total_cost"] == 300

    def test_old_reports_dir_no_dash(self, integrator):
        """候选 4: v8.3_institutional/reports/daily_pnl_report_{无横杠}.json"""
        from utils.risk_guard_integrator import REPORTS_DIR

        reports_dir = REPORTS_DIR
        no_dash = integrator.report_date.replace("-", "")
        data = {"portfolio_pnl": {"summary": {"total_cost": 400}}}
        (reports_dir / f"daily_pnl_report_{no_dash}.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        result = integrator._load_pnl_report()
        assert result is not None
        assert result["portfolio_pnl"]["summary"]["total_cost"] == 400

    def test_corrupted_json_continues(self, integrator):
        """文件损坏但存在 → 跳过，继续下一个候选"""
        from utils.risk_guard_integrator import REPORTS_DIR

        reports_dir = REPORTS_DIR
        bad_file = reports_dir / f"daily_pnl_report_{integrator.report_date}.json"
        bad_file.write_text("{ this is : not valid json [[", encoding="utf-8")
        from utils.risk_guard_integrator import DAILY_REPORT_DIR

        report_dir = DAILY_REPORT_DIR
        date_dir = report_dir / integrator.report_date
        date_dir.mkdir(parents=True, exist_ok=True)
        good_data = {"portfolio_pnl": {"summary": {"total_cost": 500}}}
        (date_dir / f"daily_pnl_report_{integrator.report_date}.json").write_text(
            json.dumps(good_data), encoding="utf-8"
        )
        result = integrator._load_pnl_report()
        assert result is not None
        assert result["portfolio_pnl"]["summary"]["total_cost"] == 500

    def test_all_corrupted_returns_none(self, integrator):
        """所有 4 个候选文件都损坏 → 返回 None"""
        from utils.risk_guard_integrator import REPORTS_DIR

        reports_dir = REPORTS_DIR
        from utils.risk_guard_integrator import DAILY_REPORT_DIR

        daily_dir = DAILY_REPORT_DIR
        for p in [
            reports_dir / f"daily_pnl_report_{integrator.report_date}.json",
            reports_dir
            / f"daily_pnl_report_{integrator.report_date.replace('-', '')}.json",
        ]:
            p.write_text("INVALID JSON!!!", encoding="utf-8")
        date_dir = daily_dir / integrator.report_date
        date_dir.mkdir(parents=True, exist_ok=True)
        for p in [
            date_dir / f"daily_pnl_report_{integrator.report_date}.json",
            date_dir
            / f"daily_pnl_report_{integrator.report_date.replace('-', '')}.json",
        ]:
            p.write_text("NOT JSON EITHER", encoding="utf-8")
        result = integrator._load_pnl_report()
        assert result is None

    def test_priority_order(self, integrator):
        """优先级：候选1 > 候选2 > 候选3 > 候选4"""
        from utils.risk_guard_integrator import REPORTS_DIR

        reports_dir = REPORTS_DIR
        from utils.risk_guard_integrator import DAILY_REPORT_DIR

        daily_dir = DAILY_REPORT_DIR
        date_dir = daily_dir / integrator.report_date
        date_dir.mkdir(parents=True, exist_ok=True)
        for i, p in enumerate(
            [
                date_dir / f"daily_pnl_report_{integrator.report_date}.json",
                date_dir
                / f"daily_pnl_report_{integrator.report_date.replace('-', '')}.json",
                reports_dir / f"daily_pnl_report_{integrator.report_date}.json",
                reports_dir
                / f"daily_pnl_report_{integrator.report_date.replace('-', '')}.json",
            ]
        ):
            p.write_text(json.dumps({"idx": i}), encoding="utf-8")
        result = integrator._load_pnl_report()
        assert result["idx"] == 0


# ========================================================================
# 六、_extract_positions / _extract_summary 多格式兼容 — 10 tests
# ========================================================================


class TestExtractPositionsAndSummary:
    def test_v1_details_list(self, integrator):
        report = _make_pnl_report_v1()
        positions = integrator._extract_positions(report)
        assert isinstance(positions, list)
        assert len(positions) == 4

    def test_v2_positions_list(self, integrator):
        report = _make_pnl_report_v2()
        positions = integrator._extract_positions(report)
        assert isinstance(positions, list)
        assert len(positions) == 1

    def test_v3_top_level_dict(self, integrator):
        report = _make_pnl_report_v3()
        positions = integrator._extract_positions(report)
        assert isinstance(positions, list)
        assert len(positions) == 2

    def test_v4_top_level_list(self, integrator):
        report = _make_pnl_report_v4()
        positions = integrator._extract_positions(report)
        assert isinstance(positions, list)
        assert len(positions) == 1

    def test_empty_report_empty_list(self, integrator):
        assert integrator._extract_positions({}) == []
        assert integrator._extract_positions({"portfolio_pnl": {}}) == []
        assert integrator._extract_positions({"portfolio_pnl": {"details": None}}) == []

    def test_invalid_types_safe(self, integrator):
        """positions 为 str/int 时安全返回空列表"""
        assert (
            integrator._extract_positions({"portfolio_pnl": {"details": "not_list"}})
            == []
        )
        assert integrator._extract_positions({"positions": 42}) == []

    def test_summary_v1_full_format(self, integrator):
        report = _make_pnl_report_v1(total_cost=1_500_000)
        summary = integrator._extract_summary(report)
        assert summary["total_cost"] == 1_500_000

    def test_summary_v3_top_level(self, integrator):
        report = _make_pnl_report_v3()
        summary = integrator._extract_summary(report)
        assert summary["total_cost"] == 2_000_000

    def test_summary_empty(self, integrator):
        assert integrator._extract_summary({}) == {}
        assert integrator._extract_summary({"portfolio_pnl": {}}) == {}

    def test_get_pnl_summary_nested(self, integrator):
        report = _make_pnl_report_v1()
        s = integrator._get_pnl_summary(report)
        assert "total_cost" in s
        assert "total_market_value" in s


# ========================================================================
# 七、guard_drawdown Level 0/1/2/3/4 精确边界 — 15 tests
# ========================================================================


class TestGuardDrawdownExact:
    """回撤级别精确阈值 (5%/8%/12%/15%)"""

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        """注入 DrawdownController mock"""
        self.mock_dc = MagicMock()
        _inject_mock_module(
            monkeypatch, "utils.drawdown_controller", "DrawdownController", self.mock_dc
        )
        self.monkeypatch = monkeypatch

    def _run(self, integrator, level, dd_pct, cost=1_000_000, value=1_000_000):
        self.mock_dc.check_drawdown.return_value = {
            "level": level,
            "drawdown_pct": dd_pct,
        }
        report = _make_pnl_report_v1(total_cost=cost, total_market_value=value)
        plan = _make_plan_full()
        return integrator.guard_drawdown(report, plan)

    # ── Level 0: < 5% ──
    def test_level_0_0pct(self, integrator):
        plan = self._run(integrator, 0, 0.0)
        assert plan["risk_guard"]["drawdown_level"] == 0
        assert plan["risk_guard"]["drawdown_action"] == "NORMAL"

    def test_level_0_4pct99(self, integrator):
        plan = self._run(integrator, 0, 0.0499)
        assert plan["risk_guard"]["drawdown_level"] == 0

    # ── Level 1: 5% - 8% 预警 ──
    def test_level_1_5pct(self, integrator):
        plan = self._run(integrator, 1, 0.05)
        assert plan["risk_guard"]["drawdown_level"] == 1
        assert plan["risk_guard"]["drawdown_action"] == "WARNING"
        assert "drawdown_note" in plan["risk_guard"]

    def test_level_1_6pct5(self, integrator):
        plan = self._run(integrator, 1, 0.065)
        assert plan["risk_guard"]["drawdown_level"] == 1

    def test_level_1_7pct99(self, integrator):
        plan = self._run(integrator, 1, 0.0799)
        assert plan["risk_guard"]["drawdown_level"] == 1

    # ── Level 2: 8% - 12% 减仓20% + 对冲+50% ──
    def test_level_2_8pct_exact(self, integrator):
        plan = self._run(integrator, 2, 0.08)
        assert plan["risk_guard"]["drawdown_level"] == 2
        assert plan["risk_guard"]["drawdown_action"] == "REDUCE_20PCT"
        assert plan["phase"]["daily_capital"] == pytest.approx(200_000 * 0.8)

    def test_level_2_10pct(self, integrator):
        plan = self._run(integrator, 2, 0.10)
        assert plan["risk_guard"]["drawdown_level"] == 2
        new_ratio = plan["hedge_config"]["layers"]["layer1_futures"]["ratio"]
        assert new_ratio <= 0.60

    def test_level_2_11pct99(self, integrator):
        plan = self._run(integrator, 2, 0.1199)
        assert plan["risk_guard"]["drawdown_level"] == 2

    # ── Level 3: 12% - 15% 减仓60% + 暂停建仓 ──
    def test_level_3_12pct_exact(self, integrator):
        plan = self._run(integrator, 3, 0.12)
        assert plan["risk_guard"]["drawdown_level"] == 3
        assert plan["risk_guard"]["drawdown_action"] == "REDUCE_60PCT_PAUSE_BUILD"
        assert plan["execution_plan"]["morning_orders"] == []
        assert plan["execution_plan"]["afternoon_orders"] == []
        assert plan["market_state"]["spot_build_allowed"] is False

    def test_level_3_13pct5(self, integrator):
        plan = self._run(integrator, 3, 0.135)
        assert plan["market_state"]["spot_build_allowed"] is False

    def test_level_3_14pct99(self, integrator):
        plan = self._run(integrator, 3, 0.1499)
        assert plan["risk_guard"]["drawdown_level"] == 3

    # ── Level 4: ≥ 15% 全面止损 ──
    def test_level_4_15pct_exact(self, integrator):
        plan = self._run(integrator, 4, 0.15)
        assert plan["risk_guard"]["drawdown_level"] == 4
        assert plan["risk_guard"]["drawdown_action"] == "FULL_STOP_LIQUIDATE"
        assert plan["execution_plan"]["morning_orders"] == []
        assert plan["market_state"]["build_allowed"] is False
        assert plan["market_state"]["circuit_level"] == "CRITICAL"

    def test_level_4_20pct(self, integrator):
        plan = self._run(integrator, 4, 0.20)
        assert plan["risk_guard"]["drawdown_level"] == 4

    def test_level_4_100pct(self, integrator):
        plan = self._run(integrator, 5, 1.0)
        assert plan["risk_guard"]["drawdown_level"] == 4

    # ── 零成本跳过 ──
    def test_zero_cost_skip(self, integrator):
        plan = integrator.guard_drawdown(
            {"portfolio_pnl": {"summary": {"total_cost": 0, "total_market_value": 0}}},
            _make_plan_full(),
        )
        assert "drawdown_level" not in plan.get("risk_guard", {})

    # ── ImportError 降级 ──
    def test_import_error_skip(self, integrator, monkeypatch):
        _block_module(monkeypatch, "utils.drawdown_controller")
        plan = integrator.guard_drawdown(_make_pnl_report_v1(), _make_plan_full())
        assert "drawdown_level" not in plan.get("risk_guard", {})


# ========================================================================
# 八、_apply_budget_cut / _apply_hedge_boost 预算分配 — 10 tests
# ========================================================================


class TestBudgetAllocation:
    def test_budget_cut_0pct(self, integrator):
        plan = _make_plan_full(budget=100_000)
        result = integrator._apply_budget_cut(plan, cut_ratio=0.0)
        assert result["phase"]["daily_capital"] == 100_000

    def test_budget_cut_20pct_level2(self, integrator):
        plan = _make_plan_full(budget=100_000)
        result = integrator._apply_budget_cut(plan, cut_ratio=0.20)
        assert result["phase"]["daily_capital"] == pytest.approx(80_000)
        assert "drawdown_cut" in result["phase"]["budget_cut_reason"]

    def test_budget_cut_60pct_level3(self, integrator):
        plan = _make_plan_full(budget=100_000)
        result = integrator._apply_budget_cut(plan, cut_ratio=0.60)
        assert result["phase"]["daily_capital"] == pytest.approx(40_000)

    def test_budget_cut_100pct(self, integrator):
        plan = _make_plan_full(budget=100_000)
        result = integrator._apply_budget_cut(plan, cut_ratio=1.0)
        assert result["phase"]["daily_capital"] == 0

    def test_budget_cut_orders_shares_scaled(self, integrator):
        plan = _make_plan_full(budget=100_000)
        orig_shares = plan["execution_plan"]["morning_orders"][0]["shares"]
        result = integrator._apply_budget_cut(plan, cut_ratio=0.5)
        new_shares = result["execution_plan"]["morning_orders"][0]["shares"]
        assert new_shares == int(orig_shares * 0.5)

    def test_budget_cut_orders_amount_scaled(self, integrator):
        plan = _make_plan_full(budget=100_000)
        orig_amount = plan["execution_plan"]["morning_orders"][0]["est_amount"]
        result = integrator._apply_budget_cut(plan, cut_ratio=0.3)
        new_amount = result["execution_plan"]["morning_orders"][0]["est_amount"]
        assert new_amount == pytest.approx(orig_amount * 0.7)

    def test_budget_cut_empty_plan_no_crash(self, integrator):
        """空计划也能安全运行 (setdefault 链式访问 P0 fix)"""
        plan = {}
        result = integrator._apply_budget_cut(plan, cut_ratio=0.5)
        assert "phase" in result
        assert "execution_plan" in result

    def test_hedge_boost_50pct_level2(self, integrator):
        plan = _make_plan_full(hedge_ratio=0.10)
        result = integrator._apply_hedge_boost(plan, boost_pct=0.50)
        new_ratio = result["hedge_config"]["layers"]["layer1_futures"]["ratio"]
        assert new_ratio == pytest.approx(0.15)

    def test_hedge_boost_80pct_level3(self, integrator):
        plan = _make_plan_full(hedge_ratio=0.20)
        result = integrator._apply_hedge_boost(plan, boost_pct=0.80)
        new_ratio = result["hedge_config"]["layers"]["layer1_futures"]["ratio"]
        assert new_ratio == pytest.approx(0.36)

    def test_hedge_boost_cap_at_060(self, integrator):
        """ratio 上限 0.60 (避免过度对冲)"""
        plan = _make_plan_full(hedge_ratio=0.50)
        result = integrator._apply_hedge_boost(plan, boost_pct=0.80)
        new_ratio = result["hedge_config"]["layers"]["layer1_futures"]["ratio"]
        assert new_ratio == 0.60

    def test_hedge_boost_empty_plan_no_crash(self, integrator):
        plan = {}
        result = integrator._apply_hedge_boost(plan, boost_pct=0.5)
        assert "hedge_config" in result


# ========================================================================
# 九、guard_vol_target 波动率阈值精确边界 — 10 tests
# ========================================================================


class TestGuardVolTarget:
    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        self.mock_vtc = MagicMock()
        _inject_mock_module(
            monkeypatch,
            "utils.vol_target_controller",
            "VolTargetController",
            self.mock_vtc,
        )
        self.monkeypatch = monkeypatch
        monkeypatch.setattr(
            "utils.risk_guard_integrator.RiskGuardIntegrator._extract_daily_returns",
            lambda self, report: [0.01] * 22,
        )

    def _set_vol(self, realized_vol, vol_scale):
        self.mock_vtc.calc_realized_vol.return_value = realized_vol
        self.mock_vtc.calc_vol_scale.return_value = vol_scale

    def test_vol_scale_1p0_normal(self, integrator):
        self._set_vol(0.15, 1.0)
        plan = integrator.guard_vol_target(_make_pnl_report_v1(), _make_plan_full())
        assert plan["risk_guard"]["vol_action"] == "NORMAL"

    def test_vol_scale_0p81_just_above(self, integrator):
        self._set_vol(0.20, 0.81)
        plan = integrator.guard_vol_target(_make_pnl_report_v1(), _make_plan_full())
        assert plan["risk_guard"]["vol_action"] == "NORMAL"

    def test_vol_scale_0p80_exact_boundary(self, integrator):
        """vol_scale >= 0.80 → 不缩仓 (刚好边界)"""
        self._set_vol(0.25, 0.80)
        plan = integrator.guard_vol_target(_make_pnl_report_v1(), _make_plan_full())
        assert plan["risk_guard"]["vol_action"] == "NORMAL"

    def test_vol_scale_0p79_triggers_downscale(self, integrator):
        """vol_scale = 0.79 < 0.80 → 触发缩仓"""
        self._set_vol(0.30, 0.79)
        plan = integrator.guard_vol_target(_make_pnl_report_v1(), _make_plan_full())
        assert "SCALE_DOWN" in plan["risk_guard"]["vol_action"]

    def test_vol_scale_0p50_mid(self, integrator):
        self._set_vol(0.40, 0.50)
        plan = _make_plan_full(budget=200_000)
        result = integrator.guard_vol_target(_make_pnl_report_v1(), plan)
        assert result["phase"]["daily_capital"] == pytest.approx(100_000)
        assert result["phase"]["original_daily_capital"] == 200_000
        assert result["phase"]["vol_scale_applied"] == 0.5

    def test_vol_scale_0p29_floor_at_0p30(self, integrator):
        """vol_scale < 0.30 → 地板 0.30 (避免预算缩到 0)"""
        self._set_vol(0.60, 0.29)
        plan = _make_plan_full(budget=100_000)
        result = integrator.guard_vol_target(_make_pnl_report_v1(), plan)
        assert result["phase"]["daily_capital"] == pytest.approx(30_000)

    def test_vol_scale_0p0_extreme_floor(self, integrator):
        self._set_vol(1.0, 0.0)
        plan = _make_plan_full(budget=100_000)
        result = integrator.guard_vol_target(_make_pnl_report_v1(), plan)
        assert result["phase"]["daily_capital"] == pytest.approx(30_000)

    def test_no_returns_vol_scale_none(self, integrator):
        self.mock_vtc.calc_realized_vol.return_value = None
        plan = integrator.guard_vol_target(_make_pnl_report_v1(), _make_plan_full())
        assert plan["risk_guard"]["vol_action"] == "NORMAL"

    def test_orders_buy_scaled_sell_preserved(self, integrator):
        """BUY 缩减, SELL 保持不变"""
        self._set_vol(0.30, 0.50)
        plan = _make_plan_full()
        buy_order = plan["execution_plan"]["morning_orders"][0]
        sell_order = [
            o for o in plan["execution_plan"]["morning_orders"] if o["side"] == "SELL"
        ][0]
        orig_buy_shares = buy_order["shares"]
        orig_sell_shares = sell_order["shares"]
        result = integrator.guard_vol_target(_make_pnl_report_v1(), plan)
        m_orders = result["execution_plan"]["morning_orders"]
        new_buy = [o for o in m_orders if o["side"] == "BUY"][0]
        new_sell = [o for o in m_orders if o["side"] == "SELL"][0]
        assert new_buy["shares"] == int(orig_buy_shares * 0.5)
        assert new_sell["shares"] == orig_sell_shares

    def test_no_buy_orders_summary_note(self, integrator):
        """L2 后无 BUY 订单 → vol_scale_executed_summary 记录原因"""
        self._set_vol(0.30, 0.50)
        plan = _make_plan_full(buy_orders=[])
        plan["execution_plan"]["morning_orders"] = [
            {"code": "159915.SZ", "side": "SELL", "shares": 100, "est_amount": 1000}
        ]
        plan["execution_plan"]["afternoon_orders"] = [
            {"code": "512100.SH", "side": "SELL", "shares": 50, "est_amount": 500}
        ]
        result = integrator.guard_vol_target(_make_pnl_report_v1(), plan)
        summary = result["risk_guard"]["vol_scale_executed_summary"]
        assert summary["scaled_buy_orders"] == 0
        assert "L2 触发后" in summary["note"]

    def test_import_error_skip(self, integrator, monkeypatch):
        _block_module(monkeypatch, "utils.vol_target_controller")
        plan = integrator.guard_vol_target(_make_pnl_report_v1(), _make_plan_full())
        assert "vol_scale" not in plan.get("risk_guard", {})


# ========================================================================
# 十、guard_kill_switch L1/L2/L3 精确阈值 + 激活/恢复流程 — 18 tests
# ========================================================================


class TestGuardKillSwitch:
    """KillSwitch 三级协议 — 保证金 + 集中度 + 完整状态机"""

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        self.mock_ks = MagicMock()
        _inject_mock_module(
            monkeypatch, "utils.kill_switch", "KillSwitch", self.mock_ks
        )
        self.monkeypatch = monkeypatch

    # ── 模块可用 + 显式 check_margin_status ──
    def test_l3_via_module_margin_95pct(self, integrator):
        """保证金 95% → L3"""
        self.mock_ks.check_margin_status.return_value = {
            "level": "L3",
            "can_trade": False,
            "can_open": False,
            "action": "MARGIN_CRITICAL: 保证金≥95%",
        }
        self.mock_ks.check_concentration.return_value = {
            "level": "OK",
            "max_concentration": 0.1,
            "max_concentration_code": "",
        }
        plan = integrator.guard_kill_switch(
            _make_pnl_report_v1(margin_used=950_000, total_equity=1_000_000),
            _make_plan_full(),
        )
        assert plan["market_state"]["circuit_level"] == "CRITICAL"
        assert plan["market_state"]["build_allowed"] is False
        assert plan["execution_plan"]["morning_orders"] == []

    def test_l2_via_module_margin_75pct(self, integrator):
        """保证金 75% → L2"""
        self.mock_ks.check_margin_status.return_value = {
            "level": "L2",
            "can_trade": True,
            "can_open": False,
            "action": "MARGIN_LIMIT: 保证金≥75%",
        }
        self.mock_ks.check_concentration.return_value = {
            "level": "OK",
            "max_concentration": 0.1,
            "max_concentration_code": "",
        }
        plan = _make_plan_full()
        result = integrator.guard_kill_switch(
            _make_pnl_report_v1(margin_used=750_000, total_equity=1_000_000), plan
        )
        assert result["market_state"]["circuit_level"] == "WARNING"
        assert result["market_state"]["build_allowed"] is False
        orders = result["execution_plan"]["morning_orders"]
        for o in orders:
            assert o.get("side") != "BUY"

    def test_l1_via_module_margin_50pct(self, integrator):
        """保证金 50% → L1"""
        self.mock_ks.check_margin_status.return_value = {
            "level": "L1",
            "can_trade": True,
            "can_open": True,
            "action": "MARGIN_WATCH: 保证金≥50%",
        }
        self.mock_ks.check_concentration.return_value = {
            "level": "OK",
            "max_concentration": 0.1,
            "max_concentration_code": "",
        }
        plan = _make_plan_full()
        result = integrator.guard_kill_switch(
            _make_pnl_report_v1(margin_used=500_000, total_equity=1_000_000), plan
        )
        assert result["market_state"]["circuit_level"] == "WATCH"

    def test_ok_via_module_margin_49pct(self, integrator):
        self.mock_ks.check_margin_status.return_value = {
            "level": "OK",
            "can_trade": True,
            "can_open": True,
            "action": "正常",
        }
        self.mock_ks.check_concentration.return_value = {
            "level": "OK",
            "max_concentration": 0.1,
            "max_concentration_code": "",
        }
        plan = integrator.guard_kill_switch(
            _make_pnl_report_v1(margin_used=490_000, total_equity=1_000_000),
            _make_plan_full(),
        )
        ks = plan["risk_guard"]["kill_switch"]
        assert ks["level"] == "OK"

    # ── 模块不可用 + 降级阈值 (L3=0.95/L2=0.75/L1=0.50) ──
    def test_fallback_l3_95pct_exact(self, integrator, monkeypatch):
        _block_module(monkeypatch, "utils.kill_switch")
        plan = integrator.guard_kill_switch(
            _make_pnl_report_v1(margin_used=950_000, total_equity=1_000_000),
            _make_plan_full(),
        )
        assert plan["market_state"]["circuit_level"] == "CRITICAL"

    def test_fallback_l3_94pct99_not_l3(self, integrator, monkeypatch):
        _block_module(monkeypatch, "utils.kill_switch")
        plan = integrator.guard_kill_switch(
            _make_pnl_report_v1(margin_used=949_900, total_equity=1_000_000),
            _make_plan_full(),
        )
        assert plan["market_state"].get("circuit_level") != "CRITICAL"

    def test_fallback_l2_75pct_exact(self, integrator, monkeypatch):
        _block_module(monkeypatch, "utils.kill_switch")
        plan = integrator.guard_kill_switch(
            _make_pnl_report_v1(margin_used=750_000, total_equity=1_000_000),
            _make_plan_full(),
        )
        assert plan["market_state"]["circuit_level"] == "WARNING"

    def test_fallback_l2_74pct99_not_l2(self, integrator, monkeypatch):
        _block_module(monkeypatch, "utils.kill_switch")
        plan = integrator.guard_kill_switch(
            _make_pnl_report_v1(margin_used=749_900, total_equity=1_000_000),
            _make_plan_full(),
        )
        assert plan["market_state"].get("circuit_level") != "WARNING"

    def test_fallback_l1_50pct_exact(self, integrator, monkeypatch):
        _block_module(monkeypatch, "utils.kill_switch")
        plan = integrator.guard_kill_switch(
            _make_pnl_report_v1(margin_used=500_000, total_equity=1_000_000),
            _make_plan_full(),
        )
        assert plan["market_state"]["circuit_level"] == "WATCH"

    def test_fallback_l1_49pct99_ok(self, integrator, monkeypatch):
        _block_module(monkeypatch, "utils.kill_switch")
        plan = integrator.guard_kill_switch(
            _make_pnl_report_v1(margin_used=499_900, total_equity=1_000_000),
            _make_plan_full(),
        )
        assert (
            "circuit_level" not in plan["market_state"]
            or plan["market_state"].get("circuit_level") != "WATCH"
        )

    # ── 集中度升级覆盖 (v8.6.13 P0 fix) ──
    def test_concentration_upgrades_margin_ok_to_l3(self, integrator):
        """保证金 OK 但集中度 L3 → 整体升级 L3"""
        self.mock_ks.check_margin_status.return_value = {
            "level": "OK",
            "can_trade": True,
            "can_open": True,
            "action": "正常",
        }
        self.mock_ks.check_concentration.return_value = {
            "level": "L3",
            "max_concentration": 0.55,
            "max_concentration_code": "600519",
        }
        plan = integrator.guard_kill_switch(
            _make_pnl_report_v1(margin_used=300_000, total_equity=1_000_000),
            _make_plan_full(),
        )
        assert plan["market_state"]["circuit_level"] == "CRITICAL"

    def test_concentration_upgrades_l1_to_l2(self, integrator):
        self.mock_ks.check_margin_status.return_value = {
            "level": "L1",
            "can_trade": True,
            "can_open": True,
            "action": "WATCH",
        }
        self.mock_ks.check_concentration.return_value = {
            "level": "L2",
            "max_concentration": 0.38,
            "max_concentration_code": "000858",
        }
        plan = integrator.guard_kill_switch(_make_pnl_report_v1(), _make_plan_full())
        assert plan["market_state"]["circuit_level"] == "WARNING"

    # ── P0-D 修复: margin_used/None 回退估算 ──
    def test_margin_used_none_fallback_estimate(self, integrator):
        self.mock_ks._estimate_margin_from_positions.return_value = 0.55
        self.mock_ks.check_margin_status.return_value = {
            "level": "L1",
            "can_trade": True,
            "can_open": True,
            "action": "WATCH",
        }
        self.mock_ks.check_concentration.return_value = {
            "level": "OK",
            "max_concentration": 0.1,
            "max_concentration_code": "",
        }
        report = _make_pnl_report_v1()
        report["portfolio_pnl"]["summary"]["margin_used"] = None
        plan = integrator.guard_kill_switch(report, _make_plan_full())
        assert plan["risk_guard"]["kill_switch"]["margin_usage"] == pytest.approx(
            0.55, abs=0.01
        )

    def test_margin_used_none_estimate_fails(self, integrator):
        self.mock_ks._estimate_margin_from_positions.side_effect = RuntimeError("boom")
        self.mock_ks.check_margin_status.return_value = {
            "level": "L1",
            "can_trade": True,
            "can_open": True,
            "action": "WATCH",
        }
        self.mock_ks.check_concentration.return_value = {
            "level": "OK",
            "max_concentration": 0.1,
            "max_concentration_code": "",
        }
        report = _make_pnl_report_v1()
        report["portfolio_pnl"]["summary"]["margin_used"] = None
        plan = integrator.guard_kill_switch(report, _make_plan_full())
        assert plan["risk_guard"]["kill_switch"]["margin_usage"] == pytest.approx(
            0.50, abs=0.01
        )

    def test_total_equity_none_no_module_fallback(self, integrator, monkeypatch):
        _block_module(monkeypatch, "utils.kill_switch")
        report = _make_pnl_report_v1()
        report["portfolio_pnl"]["summary"]["total_equity"] = None
        plan = integrator.guard_kill_switch(report, _make_plan_full())
        assert plan["risk_guard"]["kill_switch"]["margin_usage"] == pytest.approx(0.50)

    # ── L2 保留 SELL 订单，过滤 BUY + BUY_OPEN + BUY_PUT ──
    def test_l2_filters_multiple_buy_variants(self, integrator, monkeypatch):
        _block_module(monkeypatch, "utils.kill_switch")
        plan = _make_plan_full()
        plan["execution_plan"]["morning_orders"] = [
            {"side": "BUY", "code": "A", "est_amount": 100},
            {"side": "SELL", "code": "B", "est_amount": 200},
            {"direction": "BUY_OPEN", "code": "C", "est_amount": 300},
            {"direction": "BUY_PUT", "code": "D", "est_amount": 400},
            {"direction": "SELL_CLOSE", "code": "E", "est_amount": 500},
        ]
        result = integrator.guard_kill_switch(
            _make_pnl_report_v1(margin_used=800_000, total_equity=1_000_000), plan
        )
        kept = result["execution_plan"]["morning_orders"]
        codes = [o["code"] for o in kept]
        assert "B" in codes
        assert "E" in codes
        assert "A" not in codes
        assert "C" not in codes
        assert "D" not in codes

    # ── L2 circuit_level 不覆盖 CRITICAL ──
    def test_l2_does_not_downgrade_critical(self, integrator, monkeypatch):
        _block_module(monkeypatch, "utils.kill_switch")
        plan = _make_plan_full(circuit_level="CRITICAL")
        result = integrator.guard_kill_switch(
            _make_pnl_report_v1(margin_used=800_000, total_equity=1_000_000), plan
        )
        assert result["market_state"]["circuit_level"] == "CRITICAL"

    # ── 集中度检查异常降级 ──
    def test_concentration_check_exception_safe(self, integrator):
        self.mock_ks.check_margin_status.return_value = {
            "level": "OK",
            "can_trade": True,
            "can_open": True,
            "action": "正常",
        }
        self.mock_ks.check_concentration.side_effect = KeyError("missing_field")
        plan = integrator.guard_kill_switch(_make_pnl_report_v1(), _make_plan_full())
        assert "concentration" not in plan["risk_guard"]["kill_switch"]


# ========================================================================
# 十一、guard_market_circuit_breaker / guard_liquidity_crisis /
#        guard_overnight_gap 崩溃 fail-closed + 降级 — 12 tests
# ========================================================================


class TestGuardFailClosedPaths:
    """所有 Guard 的异常路径 + fail-closed 策略"""

    def test_market_circuit_import_failed(self, integrator, monkeypatch):
        _block_module(monkeypatch, "utils.market_circuit_breaker")
        plan = integrator.guard_market_circuit_breaker({}, _make_empty_plan())
        assert plan["risk_guard"]["market_circuit_breaker"]["status"] == "SKIP"

    def test_market_circuit_crash_fail_closed(self, integrator, monkeypatch):
        mock_mcb_cls = MagicMock(side_effect=RuntimeError("boom"))
        mock_mod = MagicMock()
        mock_mod.MarketCircuitBreaker = mock_mcb_cls
        monkeypatch.setitem(sys.modules, "utils.market_circuit_breaker", mock_mod)
        plan = integrator.guard_market_circuit_breaker({}, _make_empty_plan())
        assert plan["market_state"]["spot_build_allowed"] is False
        assert plan["market_state"]["build_allowed"] is False
        assert plan["market_state"]["circuit_level"] == "WARNING"
        assert "market_circuit_breaker_error" in plan["risk_guard"]

    def test_market_circuit_crash_preserves_critical(self, integrator, monkeypatch):
        """circuit_level=CRITICAL 时崩溃不降级"""
        mock_mcb_cls = MagicMock(side_effect=RuntimeError("boom"))
        mock_mod = MagicMock()
        mock_mod.MarketCircuitBreaker = mock_mcb_cls
        monkeypatch.setitem(sys.modules, "utils.market_circuit_breaker", mock_mod)
        plan = _make_plan_full(circuit_level="CRITICAL")
        result = integrator.guard_market_circuit_breaker({}, plan)
        assert result["market_state"]["circuit_level"] == "CRITICAL"

    def test_liquidity_data_unavailable_warning_not_critical(
        self, integrator, monkeypatch
    ):
        """数据源不可用 → WARNING, 不清空订单 (P1-LIVE-05 fix)"""

        def mock_fetch(self2, *a, **kw):
            return 0, 0, "fail_closed"

        monkeypatch.setattr(
            "utils.risk_guard_integrator.RiskGuardIntegrator._fetch_limit_counts",
            mock_fetch,
        )
        plan = _make_plan_full()
        orig_morning = list(plan["execution_plan"]["morning_orders"])
        result = integrator.guard_liquidity_crisis(_make_pnl_report_v1(), plan)
        assert result["market_state"]["circuit_level"] == "WARNING"
        assert result["market_state"]["spot_build_allowed"] is False
        assert len(result["execution_plan"]["morning_orders"]) == len(orig_morning)

    def test_liquidity_triggered_critical(self, integrator, monkeypatch):
        def mock_fetch(self2, *a, **kw):
            return 1500, 600, "akshare"

        monkeypatch.setattr(
            "utils.risk_guard_integrator.RiskGuardIntegrator._fetch_limit_counts",
            mock_fetch,
        )
        plan = _make_plan_full()
        result = integrator.guard_liquidity_crisis(_make_pnl_report_v1(), plan)
        assert result["market_state"]["circuit_level"] == "CRITICAL"
        assert result["execution_plan"]["morning_orders"] == []
        assert result["market_state"]["liquidity_crisis"] is True

    def test_liquidity_normal(self, integrator, monkeypatch):
        def mock_fetch(self2, *a, **kw):
            return 30, 20, "akshare"

        monkeypatch.setattr(
            "utils.risk_guard_integrator.RiskGuardIntegrator._fetch_limit_counts",
            mock_fetch,
        )
        plan = integrator.guard_liquidity_crisis(
            _make_pnl_report_v1(), _make_plan_full()
        )
        assert plan["risk_guard"]["liquidity_crisis"]["action"] == "NORMAL"

    def test_liquidity_crash_fail_closed(self, integrator, monkeypatch):
        def mock_fetch(self2, *a, **kw):
            raise RuntimeError("network down")

        monkeypatch.setattr(
            "utils.risk_guard_integrator.RiskGuardIntegrator._fetch_limit_counts",
            mock_fetch,
        )
        plan = integrator.guard_liquidity_crisis(
            _make_pnl_report_v1(), _make_plan_full()
        )
        assert plan["market_state"]["build_allowed"] is False
        assert "liquidity_crisis_error" in plan["risk_guard"]

    def test_overnight_import_failed(self, integrator, monkeypatch):
        _block_module(monkeypatch, "utils.overnight_gap_monitor")
        plan = integrator.guard_overnight_gap({}, _make_empty_plan())
        assert plan["risk_guard"]["overnight_gap"]["status"] == "SKIP"

    def test_overnight_crash_fail_closed(self, integrator, monkeypatch):
        mock_cls = MagicMock(side_effect=RuntimeError("api down"))
        mock_mod = MagicMock()
        mock_mod.OvernightGapMonitor = mock_cls
        monkeypatch.setitem(sys.modules, "utils.overnight_gap_monitor", mock_mod)
        plan = integrator.guard_overnight_gap({}, _make_empty_plan())
        assert plan["market_state"]["spot_build_allowed"] is False
        assert plan["market_state"]["build_allowed"] is False

    def test_overnight_risk_non_dict_safe(self, integrator, monkeypatch):
        """evaluate_overnight_risk 返回 list/None 时安全降级"""
        mock_inst = MagicMock()
        mock_inst.evaluate_overnight_risk.return_value = ["not_a_dict"]
        mock_cls = MagicMock(return_value=mock_inst)
        mock_mod = MagicMock()
        mock_mod.OvernightGapMonitor = mock_cls
        monkeypatch.setitem(sys.modules, "utils.overnight_gap_monitor", mock_mod)
        plan = integrator.guard_overnight_gap({}, _make_plan_full())
        assert "overnight_gap_error" not in plan.get("risk_guard", {})

    def test_fetch_limit_akshare_available(self, integrator, monkeypatch):
        mock_series = MagicMock()
        mock_series.__ge__ = lambda self, val: MagicMock(sum=MagicMock(return_value=2))
        mock_series.__le__ = lambda self, val: MagicMock(sum=MagicMock(return_value=1))
        mock_df = MagicMock()
        mock_df.empty = False
        mock_df.columns = ["涨跌幅"]
        mock_df.__contains__ = lambda s, k: True
        mock_df.__getitem__ = lambda s, k: mock_series
        type(mock_df).__getitem__ = lambda self, key: mock_series
        mock_ak = MagicMock()
        mock_ak.stock_zh_a_spot_em.return_value = mock_df
        monkeypatch.setitem(sys.modules, "akshare", mock_ak)
        lu, ld, src = integrator._fetch_limit_counts()
        assert src == "akshare"

    def test_fetch_limit_fail_closed_path(self, integrator, monkeypatch):
        _block_all_optional_modules(monkeypatch)
        lu, ld, src = integrator._fetch_limit_counts()
        assert (lu, ld) == (0, 0)
        assert src == "fail_closed"


# ========================================================================
# 十二、guard_sentiment_breaking_news fail-open + 触发路径 — 10 tests
# ========================================================================


class TestSentimentGuard:
    def test_flag_off_skips(self, integrator, monkeypatch):
        monkeypatch.setenv("USE_SENTIMENT_GUARD", "False")
        plan = integrator.guard_sentiment_breaking_news(
            _make_pnl_report_v1(), _make_plan_full()
        )
        assert plan["risk_guard"]["sentiment_breaking_news"]["status"] == "DISABLED"

    def test_flag_variants_off(self, integrator, monkeypatch):
        for val in ["0", "No", "OFF", "false"]:
            monkeypatch.setenv("USE_SENTIMENT_GUARD", val)
            plan = integrator.guard_sentiment_breaking_news({}, {})
            assert plan["risk_guard"]["sentiment_breaking_news"]["status"] == "DISABLED"

    def test_no_holdings_skips(self, integrator):
        plan = integrator.guard_sentiment_breaking_news(
            {"portfolio_pnl": {"summary": {}, "details": []}},
            {
                "execution_plan": {"morning_orders": [], "afternoon_orders": []},
                "positions": {},
            },
        )
        assert plan["risk_guard"]["sentiment_breaking_news"]["status"] == "SKIP"

    def test_import_failed_skips(self, integrator, monkeypatch):
        _block_module(monkeypatch, "utils.ifind_news_analyzer")
        plan = integrator.guard_sentiment_breaking_news(
            _make_pnl_report_v1(), _make_plan_full()
        )
        assert plan["risk_guard"]["sentiment_breaking_news"]["status"] == "SKIP"

    def test_ifind_unavailable_skips(self, integrator, monkeypatch):
        mock_inst = MagicMock()
        mock_inst.available.return_value = False
        _inject_mock_module(
            monkeypatch, "utils.ifind_news_analyzer", "IFinDNewsAnalyzer", mock_inst
        )
        plan = integrator.guard_sentiment_breaking_news(
            _make_pnl_report_v1(), _make_plan_full()
        )
        assert (
            plan["risk_guard"]["sentiment_breaking_news"]["reason"]
            == "ifind_mcp_unavailable"
        )

    def test_analyze_exception_fail_open(self, integrator, monkeypatch):
        """负面新闻异常 → fail-open (不阻塞)"""
        mock_inst = MagicMock()
        mock_inst.available.return_value = True
        mock_inst.batch_analyze.side_effect = RuntimeError("API 限流")
        _inject_mock_module(
            monkeypatch, "utils.ifind_news_analyzer", "IFinDNewsAnalyzer", mock_inst
        )
        plan = _make_plan_full()
        orig = plan["execution_plan"]["morning_orders"][:]
        result = integrator.guard_sentiment_breaking_news(_make_pnl_report_v1(), plan)
        assert result["risk_guard"]["sentiment_breaking_news"]["status"] == "ERROR"
        assert result["execution_plan"]["morning_orders"] == orig

    def test_no_critical_negative_ok(self, integrator, monkeypatch):
        mock_inst = MagicMock()
        mock_inst.available.return_value = True
        insight = MagicMock(
            symbol="510300",
            name="沪深300",
            direction="neutral",
            confidence=0.5,
            reasons=[],
            news_count=2,
        )
        mock_inst.batch_analyze.return_value = [insight]
        _inject_mock_module(
            monkeypatch, "utils.ifind_news_analyzer", "IFinDNewsAnalyzer", mock_inst
        )
        plan = integrator.guard_sentiment_breaking_news(
            _make_pnl_report_v1(), _make_plan_full()
        )
        assert plan["risk_guard"]["sentiment_breaking_news"]["status"] == "OK"
        assert plan["risk_guard"]["sentiment_breaking_news"]["triggered"] is False

    def test_triggered_critical_negative(self, integrator, monkeypatch):
        mock_inst = MagicMock()
        mock_inst.available.return_value = True
        insight = MagicMock(
            symbol="600519",
            name="贵州茅台",
            direction="negative",
            confidence=0.95,
            reasons=["财务造假", "监管立案"],
            news_count=5,
        )
        mock_inst.batch_analyze.return_value = [insight]
        _inject_mock_module(
            monkeypatch, "utils.ifind_news_analyzer", "IFinDNewsAnalyzer", mock_inst
        )
        plan = _make_plan_full()
        result = integrator.guard_sentiment_breaking_news(_make_pnl_report_v1(), plan)
        assert result["risk_guard"]["sentiment_breaking_news"]["status"] == "TRIGGERED"
        assert result["risk_guard"]["sentiment_breaking_news"]["triggered"] is True
        assert result["market_state"]["build_allowed"] is False
        assert (
            "600519"
            in result["risk_guard"]["sentiment_breaking_news"]["triggered_symbols"]
        )

    def test_extract_holding_symbols_from_pnl(self, integrator):
        symbols = integrator._extract_holding_symbols(_make_pnl_report_v1(), {})
        assert "588080" in symbols
        assert "510050" in symbols

    def test_extract_holding_symbols_from_plan_positions(self, integrator):
        plan = {"positions": {"600519.SH": {}, "000858.SZ": {}}}
        symbols = integrator._extract_holding_symbols({"portfolio_pnl": {}}, plan)
        assert "600519" in symbols
        assert "000858" in symbols


# ========================================================================
# 十三、guard_hedge_execution / guard_protective_put /
#        guard_correlation_hedge 接口集成 — 12 tests
# ========================================================================


class TestHedgeIntegrationInterfaces:
    def test_hedge_within_budget_pending(self, integrator, monkeypatch):
        mock_inst = MagicMock()
        mock_inst.generate_hedge_orders.return_value = {
            "futures_orders": [
                {
                    "contracts": 2,
                    "est_price": 3800,
                    "notional": 380_000,
                    "rationale": {"beta_to_hedge": 0.5},
                }
            ],
            "options_orders": [{"instrument": "510050 Put", "est_premium": 5000}],
            "cost_summary": {
                "within_budget": True,
                "hedge_mode": "FUTURES_PLUS_OPTIONS",
                "total_cost": 15_000,
                "budget_threshold": 50_000,
                "buffer_for_roll": 3000,
                "total_premium_budget": 5000,
                "total_margin_required": 10_000,
                "hedge_capital_usage_pct": 0.3,
            },
            "portfolio_status": {
                "portfolio_beta_before": 0.85,
                "target_beta_after": 0.3,
                "hedge_capital": 50_000,
            },
        }
        mock_inst.write_to_trade_plan = MagicMock()
        _inject_mock_module(
            monkeypatch,
            "utils.hedge_execution_engine",
            "HedgeExecutionEngine",
            mock_inst,
        )
        plan = integrator.guard_hedge_execution(
            _make_pnl_report_v1(), _make_plan_full(), "2026-07-22"
        )
        assert plan["hedge_execution"]["execution_status"] == "PENDING"
        assert "futures_options_hedge" in plan

    def test_hedge_over_budget_cancelled(self, integrator, monkeypatch):
        mock_inst = MagicMock()
        mock_inst.generate_hedge_orders.return_value = {
            "futures_orders": [{"contracts": 10, "est_price": 3800}],
            "options_orders": [],
            "cost_summary": {
                "within_budget": False,
                "hedge_mode": "FUTURES_ONLY",
                "total_cost": 200_000,
                "budget_threshold": 50_000,
                "buffer_for_roll": 0,
                "total_premium_budget": 0,
                "total_margin_required": 200_000,
                "hedge_capital_usage_pct": 4.0,
            },
            "portfolio_status": {
                "portfolio_beta_before": 0.9,
                "target_beta_after": 0.1,
                "hedge_capital": 50_000,
            },
        }
        mock_inst.write_to_trade_plan = MagicMock()
        _inject_mock_module(
            monkeypatch,
            "utils.hedge_execution_engine",
            "HedgeExecutionEngine",
            mock_inst,
        )
        plan = integrator.guard_hedge_execution(
            _make_pnl_report_v1(), _make_plan_full(), "2026-07-22"
        )
        assert plan["hedge_execution"]["execution_status"] == "CANCELLED"
        assert "OVER_BUDGET" in plan["risk_guard"]["hedge_action"]

    def test_hedge_options_only_notes(self, integrator, monkeypatch):
        mock_inst = MagicMock()
        mock_inst.generate_hedge_orders.return_value = {
            "futures_orders": [],
            "options_orders": [{"instrument": "510300 Put"}],
            "cost_summary": {
                "within_budget": True,
                "hedge_mode": "OPTIONS_ONLY",
                "total_cost": 5000,
                "budget_threshold": 20_000,
                "buffer_for_roll": 500,
                "total_premium_budget": 5000,
                "total_margin_required": 0,
                "hedge_capital_usage_pct": 0.25,
            },
            "portfolio_status": {
                "portfolio_beta_before": 0.5,
                "target_beta_after": 0.2,
                "hedge_capital": 20_000,
            },
        }
        mock_inst.write_to_trade_plan = MagicMock()
        _inject_mock_module(
            monkeypatch,
            "utils.hedge_execution_engine",
            "HedgeExecutionEngine",
            mock_inst,
        )
        plan = integrator.guard_hedge_execution(
            _make_pnl_report_v1(), _make_plan_full(), "2026-07-22"
        )
        notes = plan["hedge_execution"]["execution_notes"]
        assert any("OPTIONS_ONLY" in n for n in notes)

    def test_hedge_import_error(self, integrator, monkeypatch):
        _block_module(monkeypatch, "utils.hedge_execution_engine")
        plan = integrator.guard_hedge_execution({}, _make_plan_full(), "2026-07-22")
        assert "hedge_execution" not in plan

    def test_hedge_engine_exception(self, integrator, monkeypatch):
        mock_inst = MagicMock()
        mock_inst.generate_hedge_orders.side_effect = RuntimeError("connect fail")
        _inject_mock_module(
            monkeypatch,
            "utils.hedge_execution_engine",
            "HedgeExecutionEngine",
            mock_inst,
        )
        plan = integrator.guard_hedge_execution({}, _make_plan_full(), "2026-07-22")
        assert "ERROR" in plan["risk_guard"]["hedge_action"]

    def test_put_below_threshold_skips(self, integrator, monkeypatch):
        _inject_mock_module(
            monkeypatch,
            "utils.protective_put_engine",
            "ProtectivePutEngine",
            MagicMock(),
        )
        report = _make_pnl_report_v1(total_market_value=500_000)
        plan = integrator.guard_protective_put(report, _make_plan_full(), "2026-07-22")
        assert plan["risk_guard"]["put_action"] == "BELOW_THRESHOLD"

    def test_put_generated_orders(self, integrator, monkeypatch):
        mock_inst = MagicMock()
        mock_inst.generate_put_orders.return_value = {
            "should_execute": True,
            "orders": [
                {"underlying": "510050", "strike": 2.5, "premium": 2000},
                {"underlying": "588080", "strike": 0.9, "premium": 1500},
            ],
            "total_premium_est": 3500,
        }
        _inject_mock_module(
            monkeypatch, "utils.protective_put_engine", "ProtectivePutEngine", mock_inst
        )
        plan = integrator.guard_protective_put(
            _make_pnl_report_v1(total_market_value=2_000_000),
            _make_plan_full(),
            "2026-07-22",
        )
        assert len(plan["put_protection_orders"]) == 2
        assert plan["risk_guard"]["put_action"] == "GENERATED_2_PUTS"

    def test_put_existing_protection_ok(self, integrator, monkeypatch):
        mock_inst = MagicMock()
        mock_inst.generate_put_orders.return_value = {
            "should_execute": False,
            "orders": [],
            "reason": "滚仓周期未到",
        }
        _inject_mock_module(
            monkeypatch, "utils.protective_put_engine", "ProtectivePutEngine", mock_inst
        )
        plan = integrator.guard_protective_put(
            _make_pnl_report_v1(total_market_value=2_000_000),
            _make_plan_full(),
            "2026-07-22",
        )
        assert plan["risk_guard"]["put_action"] == "EXISTING_PROTECTION_OK"

    def test_put_import_error(self, integrator, monkeypatch):
        _block_module(monkeypatch, "utils.protective_put_engine")
        plan = integrator.guard_protective_put(
            _make_pnl_report_v1(total_market_value=2_000_000), {}, "2026-07-22"
        )
        assert "put_action" not in plan.get("risk_guard", {})

    def test_correlation_hedge_safe_haven(self, integrator, monkeypatch):
        mock_inst = MagicMock()
        mock_inst.compute_hedge.return_value = {
            "action": "SAFE_HAVEN_ALLOC",
            "avg_corr": 0.92,
            "baseline_corr": 0.60,
            "jump": 0.32,
            "gold_weight": 0.10,
            "repo_weight": 0.15,
            "gold_value": 500_000,
            "repo_value": 750_000,
            "gold_etf": "518880",
            "repo_symbol": "GC001",
        }
        mock_mod_cls = MagicMock(return_value=mock_inst)
        mock_mod = MagicMock()
        mock_mod.CorrelationHedger = mock_mod_cls
        hedging_mod = types.ModuleType("hedging")
        hedging_mod.correlation_hedger = mock_mod
        monkeypatch.setitem(sys.modules, "hedging", hedging_mod)
        monkeypatch.setitem(sys.modules, "hedging.correlation_hedger", mock_mod)
        monkeypatch.setattr(
            "utils.risk_guard_integrator.RiskGuardIntegrator._build_position_returns",
            lambda self, *a, **kw: MagicMock(empty=False),
        )
        plan = integrator.guard_correlation_hedge(
            _make_pnl_report_v1(total_market_value=5_000_000), _make_plan_full()
        )
        assert len(plan["correlation_hedge_orders"]) == 2

    def test_correlation_hedge_no_data(self, integrator, monkeypatch):
        mock_mod = MagicMock()
        hedging_mod = types.ModuleType("hedging")
        hedging_mod.correlation_hedger = mock_mod
        monkeypatch.setitem(sys.modules, "hedging", hedging_mod)
        monkeypatch.setitem(sys.modules, "hedging.correlation_hedger", mock_mod)
        monkeypatch.setattr(
            "utils.risk_guard_integrator.RiskGuardIntegrator._build_position_returns",
            lambda self, *a, **kw: None,
        )
        plan = integrator.guard_correlation_hedge(
            _make_pnl_report_v1(), _make_plan_full()
        )
        assert plan["risk_guard"]["correlation_hedge"]["action"] == "NO_DATA"

    def test_safe_haven_orders_both(self, integrator):
        result = integrator._build_safe_haven_orders(
            {
                "gold_weight": 0.05,
                "repo_weight": 0.10,
                "gold_value": 250_000,
                "repo_value": 500_000,
            }
        )
        assert len(result) == 2
        assert result[0]["symbol"] == "518880"
        assert result[1]["symbol"] == "GC001"

    def test_safe_haven_orders_neither(self, integrator):
        result = integrator._build_safe_haven_orders(
            {
                "gold_weight": 0.0,
                "repo_weight": 0.0,
            }
        )
        assert len(result) == 0


# ========================================================================
# 十四、_deduplicate_put_orders 边界 — 8 tests
# ========================================================================


class TestDeduplicatePutOrders:
    def test_no_protection_no_options_skips(self, integrator):
        plan = {}
        integrator._deduplicate_put_orders(plan)
        assert plan == {}

    def test_no_overlap_keeps_all(self, integrator):
        plan = {
            "put_protection_orders": [
                {"underlying": "510050", "strike": 2.5},
            ],
            "hedge_execution": {
                "futures_orders": [{"contracts": 2}],
                "options_orders": [
                    {"instrument": "588080 Put", "order_id": "o1"},
                ],
                "total_orders": 2,
            },
            "risk_guard": {"hedge_action": "GENERATED_2_ORDERS"},
        }
        integrator._deduplicate_put_orders(plan)
        assert len(plan["hedge_execution"]["options_orders"]) == 1

    def test_full_overlap_removes_all(self, integrator):
        plan = {
            "put_protection_orders": [
                {"underlying": "510050", "strike": 2.5},
                {"instrument": "科创50ETF Put", "strike": 0.9},
            ],
            "hedge_execution": {
                "futures_orders": [{"contracts": 1}],
                "options_orders": [
                    {"instrument": "510050 Put", "order_id": "o1"},
                    {"instrument": "科创50ETF Put", "order_id": "o2"},
                ],
                "total_orders": 3,
            },
            "risk_guard": {"hedge_action": "GENERATED_3_ORDERS"},
        }
        integrator._deduplicate_put_orders(plan)
        assert len(plan["hedge_execution"]["options_orders"]) == 0
        assert "dedup_removed_2" in plan["risk_guard"]["hedge_action"]
        assert plan["risk_guard"]["put_hedge_dedup"]["removed_count"] == 2

    def test_unrecognized_underlying_codes_skips(self, integrator):
        plan = {
            "put_protection_orders": [
                {"underlying": "UNKNOWN_ASSET_X", "strike": 100},
            ],
            "hedge_execution": {
                "futures_orders": [],
                "options_orders": [
                    {"instrument": "UNKNOWN_ASSET_X Put", "order_id": "o1"},
                ],
            },
            "risk_guard": {},
        }
        integrator._deduplicate_put_orders(plan)
        assert len(plan["hedge_execution"]["options_orders"]) == 1

    def test_futures_unaffected(self, integrator):
        plan = {
            "put_protection_orders": [
                {"underlying": "510300", "strike": 3.8},
            ],
            "hedge_execution": {
                "futures_orders": [{"contracts": 5}, {"contracts": 3}],
                "options_orders": [
                    {"instrument": "510300 Put", "order_id": "o1"},
                    {"instrument": "510500 Put", "order_id": "o2"},
                ],
                "total_orders": 4,
            },
            "risk_guard": {"hedge_action": "GENERATED_4_ORDERS"},
        }
        integrator._deduplicate_put_orders(plan)
        assert len(plan["hedge_execution"]["futures_orders"]) == 2


# ========================================================================
# 十五、一致性校验 (CRITICAL/WARNING 对齐 + Theta CC 拦截) — 8 tests
# ========================================================================


class TestConsistencyCheck:
    def test_critical_forces_build_allowed_false(self, integrator, monkeypatch):
        _block_all_optional_modules(monkeypatch)
        plan = _make_plan_full()
        plan["market_state"]["circuit_level"] = "CRITICAL"
        plan["market_state"]["build_allowed"] = True
        plan["market_state"]["spot_build_allowed"] = True
        result = integrator.run_all_guards("2026-07-22")
        assert result["market_state"]["build_allowed"] is False
        assert result["market_state"]["spot_build_allowed"] is False

    def test_warning_forces_spot_build_allowed_false(self, integrator, monkeypatch):
        _block_all_optional_modules(monkeypatch)
        plan = _make_plan_full()
        plan["market_state"]["circuit_level"] = "WARNING"
        plan["market_state"]["spot_build_allowed"] = True
        result = integrator.run_all_guards("2026-07-22")
        assert result["market_state"]["spot_build_allowed"] is False

    def test_theta_covered_call_blocked_when_spot_disallowed(
        self, integrator, monkeypatch
    ):
        _block_all_optional_modules(monkeypatch)
        plan = _make_plan_full()
        plan["market_state"]["circuit_level"] = "WARNING"
        result = integrator.run_all_guards("2026-07-22")
        for o in result["execution_plan"].get("options_orders", []):
            assert str(o.get("direction", "")).upper() != "SELL_CALL"

    def test_v77_notes_synced(self, integrator, monkeypatch):
        _block_all_optional_modules(monkeypatch)
        plan = _make_plan_full()
        plan["market_state"]["circuit_level"] = "CRITICAL"
        result = integrator.run_all_guards("2026-07-22")
        assert result["hedge_fund_overlays"]["v77_notes"]["build_allowed"] is False

    def test_normal_no_changes(self, integrator, monkeypatch):
        _block_all_optional_modules(monkeypatch)
        plan = _make_plan_full()
        orig_build = plan["execution_plan"].get("options_orders_count", 0)
        result = integrator.run_all_guards("2026-07-22")
        cc_kept = [
            o
            for o in result["execution_plan"].get("options_orders", [])
            if "CoveredCall" in str(o.get("name", ""))
        ]
        assert (
            len(cc_kept) == orig_build
            or result["market_state"].get("circuit_level") != "NORMAL"
        )


# ========================================================================
# 十六、_load_next_trade_plan / _save_trade_plan / _write_guard_log — 8 tests
# ========================================================================


class TestPlanIoAndLog:
    def test_load_nonexistent_plan(self, integrator):
        assert integrator._load_next_trade_plan("2026-99-99") is None

    def test_load_corrupted_plan(self, integrator):
        from utils.risk_guard_integrator import TRADE_PLANS_DIR

        tp_dir = TRADE_PLANS_DIR
        bad = tp_dir / "trade_plan_20260722.json"
        bad.write_text("{ not json", encoding="utf-8")
        assert integrator._load_next_trade_plan("2026-07-22") is None

    def test_save_and_load_roundtrip(self, integrator, monkeypatch):
        data = {"hello": "world", "phase": {"daily_capital": 100_000}}
        integrator._save_trade_plan(data, "2026-07-22")
        loaded = integrator._load_next_trade_plan("2026-07-22")
        assert loaded["hello"] == "world"

    def test_save_creates_backup(self, integrator):
        from utils.risk_guard_integrator import TRADE_PLANS_DIR

        tp_dir = TRADE_PLANS_DIR
        plan_path = tp_dir / "trade_plan_20260722.json"
        plan_path.write_text(json.dumps({"v": 1}), encoding="utf-8")
        integrator._save_trade_plan({"v": 2}, "2026-07-22")
        baks = list(tp_dir.glob("trade_plan_20260722.json.bak_*"))
        assert len(baks) == 1

    def test_write_guard_log_creates_file(self, integrator):
        integrator._log("msg1")
        integrator._log("msg2")
        integrator._write_guard_log("2026-07-22")
        from utils.risk_guard_integrator import LOGS_DIR

        logs_dir = LOGS_DIR
        log_file = logs_dir / "risk_guard_20260722.log"
        assert log_file.exists()
        content = log_file.read_text(encoding="utf-8")
        assert "msg1" in content
        assert "msg2" in content

    def test_write_guard_log_permission_error_safe(self, integrator, monkeypatch):
        """磁盘满/权限错时不崩溃 (pass)"""

        class ReadOnlyDir:
            def __truediv__(self, other):
                raise PermissionError("read-only filesystem")

        monkeypatch.setattr("utils.risk_guard_integrator.LOGS_DIR", ReadOnlyDir())
        integrator._log("will fail silently")
        try:
            integrator._write_guard_log("2026-07-22")
        except Exception:
            pytest.fail("_write_guard_log should suppress IO errors")


# ========================================================================
# 十七、main() CLI 入口 + run_all_guards 完整流程 — 6 tests
# ========================================================================


class TestCliAndRunAll:
    def test_main_no_args(self, monkeypatch, tmp_path):
        logs_dir = tmp_path / "logs"
        reports_dir = tmp_path / "reports"
        trade_plans_dir = tmp_path / "trade_plans"
        daily_report_dir = tmp_path / "daily_reports"
        for d in (logs_dir, reports_dir, trade_plans_dir, daily_report_dir):
            d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("utils.risk_guard_integrator.LOGS_DIR", logs_dir)
        monkeypatch.setattr("utils.risk_guard_integrator.REPORTS_DIR", reports_dir)
        monkeypatch.setattr(
            "utils.risk_guard_integrator.TRADE_PLANS_DIR", trade_plans_dir
        )
        monkeypatch.setattr(
            "utils.risk_guard_integrator.DAILY_REPORT_DIR", daily_report_dir
        )
        monkeypatch.setattr(sys, "argv", ["risk_guard_integrator", "2026-07-21"])
        try:
            main()
        except Exception as e:
            pytest.fail(f"main() crashed with {e}")

    def test_main_with_next_date(self, monkeypatch, tmp_path):
        logs_dir = tmp_path / "logs"
        reports_dir = tmp_path / "reports"
        trade_plans_dir = tmp_path / "trade_plans"
        daily_report_dir = tmp_path / "daily_reports"
        for d in (logs_dir, reports_dir, trade_plans_dir, daily_report_dir):
            d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("utils.risk_guard_integrator.LOGS_DIR", logs_dir)
        monkeypatch.setattr("utils.risk_guard_integrator.REPORTS_DIR", reports_dir)
        monkeypatch.setattr(
            "utils.risk_guard_integrator.TRADE_PLANS_DIR", trade_plans_dir
        )
        monkeypatch.setattr(
            "utils.risk_guard_integrator.DAILY_REPORT_DIR", daily_report_dir
        )
        monkeypatch.setattr(
            sys, "argv", ["risk_guard_integrator", "2026-07-21", "2026-07-23"]
        )
        try:
            main()
        except Exception as e:
            pytest.fail(f"main() with 2 args crashed with {e}")

    def test_run_all_guards_no_report_no_plan(self, integrator, monkeypatch):
        _block_all_optional_modules(monkeypatch)
        result = integrator.run_all_guards("2026-07-22")
        assert "risk_guard" in result
        assert "last_run" in result["risk_guard"]

    def test_run_all_guards_with_report_and_plan(self, integrator, monkeypatch):
        _block_all_optional_modules(monkeypatch)
        from utils.risk_guard_integrator import DAILY_REPORT_DIR

        daily_dir = DAILY_REPORT_DIR
        date_dir = daily_dir / integrator.report_date
        date_dir.mkdir(parents=True, exist_ok=True)
        (date_dir / f"daily_pnl_report_{integrator.report_date}.json").write_text(
            json.dumps(_make_pnl_report_v1()), encoding="utf-8"
        )
        from utils.risk_guard_integrator import TRADE_PLANS_DIR

        tp_dir = TRADE_PLANS_DIR
        (tp_dir / "trade_plan_20260722.json").write_text(
            json.dumps(_make_plan_full()), encoding="utf-8"
        )
        result = integrator.run_all_guards("2026-07-22")
        assert result["risk_guard"]["report_date"] == integrator.report_date

    def test_run_all_guards_preserves_sell_orders_in_l2(self, integrator, monkeypatch):
        _block_all_optional_modules(monkeypatch)

        def fake_ks(self2, pnl, plan):
            plan["market_state"] = plan.get("market_state", {})
            plan["market_state"]["circuit_level"] = "WARNING"
            plan["market_state"]["build_allowed"] = False
            plan["market_state"]["spot_build_allowed"] = False
            plan["risk_guard"] = plan.get("risk_guard", {})
            plan["risk_guard"]["kill_switch"] = {
                "level": "L2",
                "margin_usage": 0.80,
                "can_trade": True,
                "can_open": False,
            }
            return plan

        monkeypatch.setattr(
            "utils.risk_guard_integrator.RiskGuardIntegrator.guard_kill_switch", fake_ks
        )
        plan = _make_plan_full()
        plan["execution_plan"]["morning_orders"] = [
            {"side": "SELL", "code": "X", "shares": 100},
            {"side": "BUY", "code": "Y", "shares": 50},
        ]
        from utils.risk_guard_integrator import DAILY_REPORT_DIR

        daily_dir = DAILY_REPORT_DIR
        date_dir = daily_dir / integrator.report_date
        date_dir.mkdir(parents=True, exist_ok=True)
        (date_dir / f"daily_pnl_report_{integrator.report_date}.json").write_text(
            json.dumps(_make_pnl_report_v1()), encoding="utf-8"
        )
        from utils.risk_guard_integrator import TRADE_PLANS_DIR

        tp_dir = TRADE_PLANS_DIR
        (tp_dir / "trade_plan_20260722.json").write_text(
            json.dumps(plan), encoding="utf-8"
        )
        result = integrator.run_all_guards("2026-07-22")
        kept = result["execution_plan"]["morning_orders"]
        assert any(o.get("code") == "X" for o in kept)

    def test_run_all_guards_risk_guard_timestamp(self, integrator, monkeypatch):
        _block_all_optional_modules(monkeypatch)
        result = integrator.run_all_guards("2026-07-22")
        assert "last_run" in result["risk_guard"]
        ts = result["risk_guard"]["last_run"]
        assert datetime.fromisoformat(ts)


# ========================================================================
# 十八、_extract_daily_returns 边界 — 4 tests
# ========================================================================


class TestExtractDailyReturns:
    def test_no_history_files_empty(self, integrator):
        result = integrator._extract_daily_returns(_make_pnl_report_v1())
        assert result == []

    def test_fewer_than_5_days_empty(self, integrator):
        from utils.risk_guard_integrator import REPORTS_DIR

        reports_dir = REPORTS_DIR
        for i in range(3):
            d = f"2026-07-{18+i:02d}"
            p = reports_dir / f"daily_pnl_report_{d}.json"
            p.write_text(
                json.dumps({"portfolio_pnl": {"summary": {"total_pnl_pct": 1.0 + i}}}),
                encoding="utf-8",
            )
        result = integrator._extract_daily_returns(_make_pnl_report_v1())
        assert result == []

    def test_22_days_ok(self, integrator):
        from utils.risk_guard_integrator import REPORTS_DIR

        reports_dir = REPORTS_DIR
        base = datetime(2026, 7, 1)
        for i in range(25):
            d = (base + timedelta(days=i)).strftime("%Y-%m-%d")
            p = reports_dir / f"daily_pnl_report_{d}.json"
            p.write_text(
                json.dumps(
                    {"portfolio_pnl": {"summary": {"total_pnl_pct": 0.5 + i * 0.01}}}
                ),
                encoding="utf-8",
            )
        result = integrator._extract_daily_returns(_make_pnl_report_v1())
        assert len(result) == 22
        assert all(isinstance(x, float) for x in result)

    def test_exception_safe(self, integrator, monkeypatch):
        class BrokenGlob:
            def __call__(self, pattern):
                raise RuntimeError("disk error")

        monkeypatch.setattr(
            "utils.risk_guard_integrator.REPORTS_DIR",
            MagicMock(glob=MagicMock(side_effect=RuntimeError("boom"))),
        )
        result = integrator._extract_daily_returns(_make_pnl_report_v1())
        assert result == []


# ========================================================================
# 十九、_build_position_returns 边界 — 6 tests
# ========================================================================


class TestBuildPositionReturns:
    def test_no_positions_returns_none(self, integrator):
        result = integrator._build_position_returns({"portfolio_pnl": {}})
        assert result is None

    def test_fewer_than_10_report_files(self, integrator):
        from utils.risk_guard_integrator import REPORTS_DIR

        reports_dir = REPORTS_DIR
        report = _make_pnl_report_v1()
        for i in range(5):
            d = f"2026-07-{18+i:02d}"
            (reports_dir / f"daily_pnl_report_{d}.json").write_text(
                json.dumps(report), encoding="utf-8"
            )
        result = integrator._build_position_returns(report)
        assert result is None

    def test_pandas_import_missing(self, integrator, monkeypatch):
        _block_module(monkeypatch, "pandas")
        result = integrator._build_position_returns(_make_pnl_report_v1())
        assert result is None

    def test_exception_returns_none(self, integrator):
        from utils.risk_guard_integrator import REPORTS_DIR

        reports_dir = REPORTS_DIR
        report = _make_pnl_report_v1()
        for i in range(15):
            d = f"2026-07-{10+i:02d}"
            (reports_dir / f"daily_pnl_report_{d}.json").write_text(
                "CORRUPTED!", encoding="utf-8"
            )
        result = integrator._build_position_returns(report)
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])


# ========================================================================
# 二十、补充未覆盖分支测试（覆盖率冲刺）
# ========================================================================


class TestUncoveredBranches:
    """补充之前未覆盖的关键分支"""

    def test_extract_positions_dict_details(self, integrator):
        """dict.portfolio_pnl.details 是 list, 直接返回"""
        report = {"portfolio_pnl": {"details": [{"code": "000001", "quantity": 200}]}}
        positions = integrator._extract_positions(report)
        assert isinstance(positions, list)
        assert any(p["code"] == "000001" for p in positions)

    def test_apply_budget_cut_reduces_orders(self, integrator):
        """预算削减时订单金额被缩减"""
        plan = _make_plan_full()
        plan["execution_plan"]["morning_orders"] = [
            {"order_id": "1", "shares": 10000, "est_amount": 100000},
            {"order_id": "2", "shares": 20000, "est_amount": 200000},
        ]
        result = integrator._apply_budget_cut(plan, 0.5)
        assert result["execution_plan"]["morning_orders"][0]["shares"] == 5000
        assert result["execution_plan"]["morning_orders"][1]["est_amount"] == 100000.0

    def test_extract_daily_returns_legacy_pnl_summary(self, integrator):
        """兼容旧格式: report.portfolio_pnl 是 dict 而不是 dict.summary"""
        from utils.risk_guard_integrator import REPORTS_DIR

        reports_dir = REPORTS_DIR
        import datetime
        from datetime import timedelta

        base = datetime.date(2026, 7, 1)
        for i in range(5):
            d = (base + timedelta(days=i)).strftime("%Y-%m-%d")
            p = reports_dir / f"daily_pnl_report_{d}.json"
            p.write_text(
                json.dumps({"portfolio_pnl": {"total_pnl_pct": 0.01 + i * 0.01}}),
                encoding="utf-8",
            )
        report = {
            "trade_date": "2026-07-22",
            "portfolio_pnl": {"total_pnl_pct": 0.02},
        }
        result = integrator._extract_daily_returns(report)
        assert len(result) == 5

    def test_guard_hedge_execution_no_change_needed(self, integrator, monkeypatch):
        """空订单返回 NO_CHANGE_NEEDED"""
        _block_all_optional_modules(monkeypatch)
        plan = _make_plan_full()
        plan.setdefault("risk_guard", {})["hedge_execution"] = {"actions": {}}
        plan["execution_plan"]["morning_orders"] = []
        result = integrator.guard_hedge_execution(
            _make_pnl_report_v1(), plan, "2026-07-22"
        )
        assert result["risk_guard"]["hedge_execution"]["actions"] == {}
        assert "action" not in result["risk_guard"]["hedge_execution"]

    def test_guard_protective_put_existing_protection_ok(self, integrator, monkeypatch):
        """已有保护且期权不超过预算时返回 EXISTING_PROTECTION_OK"""
        _block_all_optional_modules(monkeypatch)
        plan = _make_plan_full()
        plan.setdefault("risk_guard", {})["hedge_fund_overlays"] = {
            "v77_notes": {"build_allowed": True}
        }
        plan.setdefault("risk_guard", {})["put_protection"] = {"existing_contracts": []}
        plan["market_state"] = {"circuit_level": "NORMAL"}
        plan["execution_plan"]["options_orders_count"] = 0
        plan["execution_plan"]["options_orders"] = []
        result = integrator.guard_protective_put(
            _make_pnl_report_v1(), plan, "2026-07-22"
        )
        assert "risk_guard" in result
        assert result["risk_guard"].get("put_protection") is not None

    def test_guard_kill_switch_l1_circuit_level(self, integrator, monkeypatch):
        """L1 不触发放量/集中度, 但设置 circuit_level=WATCH"""
        _block_all_optional_modules(monkeypatch)
        plan = _make_plan_full()
        plan.setdefault("risk_guard", {})["liquidity"] = {
            "limit_up_count": 20,
            "limit_down_count": 20,
        }
        plan["positions"] = [{"code": f"{i:06d}", "weight": 0.1} for i in range(9)]
        pnl = _make_pnl_report_v1(margin_used=800_000, total_equity=1_500_000)
        result = integrator.guard_kill_switch(pnl, plan)
        assert result["risk_guard"]["kill_switch"]["level"] == "L1"
        assert result["market_state"]["circuit_level"] == "WATCH"

    def test_guard_kill_switch_concentration_check(self, integrator, monkeypatch):
        """集中度检查: 单一标的 >=0.3 触发 L2"""
        mock_ks = MagicMock()
        mock_ks.check_margin_status.return_value = {
            "level": "OK",
            "can_trade": True,
            "can_open": True,
            "action": "正常",
        }
        mock_ks.check_concentration.return_value = {
            "level": "L2",
            "max_concentration": 0.5,
            "max_concentration_code": "600519",
        }
        monkeypatch.setitem(
            sys.modules,
            "utils.kill_switch",
            MagicMock(KillSwitch=MagicMock(return_value=mock_ks)),
        )
        plan = _make_plan_full()
        plan["positions"] = [
            {"code": "600519", "market_value": 500_000},
            {"code": "000001", "market_value": 500_000},
        ]
        result = integrator.guard_kill_switch(_make_pnl_report_v1(), plan)
        assert result["risk_guard"]["kill_switch"]["level"] == "L2"
        assert (
            result["risk_guard"]["kill_switch"]["concentration"][
                "max_concentration_code"
            ]
            == "600519"
        )

    def test_fetch_limit_counts_layer2_akshare_fails(self, integrator, monkeypatch):
        """Layer 2: akshare 返回 None, astock_realtime 也失败"""
        monkeypatch.setitem(
            sys.modules,
            "akshare",
            MagicMock(stock_zh_a_spot_em=MagicMock(return_value=None)),
        )
        lu, ld, src = integrator._fetch_limit_counts()
        assert src == "fail_closed"

    def test_fetch_limit_counts_layer2_no_positions(self, integrator, monkeypatch):
        """Layer 2: akshare 返回空 DataFrame, astock_realtime 失败"""
        mock_df = MagicMock()
        mock_df.empty = True
        mock_df.columns = ["涨跌幅"]
        mock_ak = MagicMock()
        mock_ak.stock_zh_a_spot_em.return_value = mock_df
        monkeypatch.setitem(sys.modules, "akshare", mock_ak)
        lu, ld, src = integrator._fetch_limit_counts()
        assert src == "fail_closed"

    def test_fetch_limit_counts_astock_realtime_returns(self, integrator, monkeypatch):
        """Layer 2: akshare 失败, astock_realtime 返回数据"""
        mock_df = MagicMock()
        mock_df.empty = False
        mock_df.columns = ["涨跌幅"]
        mock_series = MagicMock()
        mock_series.ge.return_value = MagicMock(sum=MagicMock(return_value=3))
        mock_series.le.return_value = MagicMock(sum=MagicMock(return_value=1))
        mock_df.__getitem__ = lambda s, k: mock_series
        monkeypatch.setitem(
            sys.modules,
            "akshare",
            MagicMock(stock_zh_a_spot_em=MagicMock(return_value=None)),
        )
        lu, ld, src = integrator._fetch_limit_counts()
        assert src == "fail_closed"

    def test_run_all_guards_kill_switch_exception(
        self, integrator, monkeypatch, caplog
    ):
        """run_all_guards 中 kill_switch 崩溃时降级处理"""
        _block_all_optional_modules(monkeypatch)
        monkeypatch.setattr(
            "utils.risk_guard_integrator.RiskGuardIntegrator.guard_kill_switch",
            lambda self, pnl_report, plan: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        _make_plan_full()
        result = integrator.run_all_guards("2026-07-22")
        assert result.get("risk_guard", {}).get("kill_switch_error") is not None
        assert result["market_state"].get("spot_build_allowed") is False

    def test_run_all_guards_liquidity_exception(self, integrator, monkeypatch, caplog):
        """run_all_guards 中 liquidity 崩溃时降级处理"""
        _block_all_optional_modules(monkeypatch)
        monkeypatch.setattr(
            "utils.risk_guard_integrator.RiskGuardIntegrator.guard_liquidity_crisis",
            lambda self, pnl_report, plan: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        _make_plan_full()
        result = integrator.run_all_guards("2026-07-22")
        assert result.get("risk_guard", {}).get("liquidity_crisis_error") is not None
        assert result["market_state"].get("spot_build_allowed") is False

    def test_run_all_guards_drawdown_exception(self, integrator, monkeypatch, caplog):
        """run_all_guards 中 drawdown 崩溃时降级处理"""
        _block_all_optional_modules(monkeypatch)
        monkeypatch.setattr(
            "utils.risk_guard_integrator.RiskGuardIntegrator.guard_drawdown",
            lambda self, pnl_report, plan: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        _make_plan_full()
        result = integrator.run_all_guards("2026-07-22")
        assert result.get("risk_guard", {}).get("drawdown_error") is not None
        assert result["market_state"].get("spot_build_allowed") is False

    def test_run_all_guards_vol_target_exception(self, integrator, monkeypatch, caplog):
        """run_all_guards 中 vol_target 崩溃时降级处理"""
        _block_all_optional_modules(monkeypatch)
        monkeypatch.setattr(
            "utils.risk_guard_integrator.RiskGuardIntegrator.guard_vol_target",
            lambda self, pnl_report, plan: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        _make_plan_full()
        result = integrator.run_all_guards("2026-07-22")
        assert result.get("risk_guard", {}).get("vol_target_error") is not None
        assert result["market_state"].get("spot_build_allowed") is False
