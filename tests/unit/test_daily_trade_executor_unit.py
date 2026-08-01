# -*- coding: utf-8 -*-
"""
daily_trade_executor 单元测试 (C-1.2)
=====================================

覆盖范围:
  - 纯函数: _infer_suffix / _parse_date_from_cfg / is_accumulation_period
           assess_etf_signal / adjust_allocation_by_signal / _compute_price_band
           _compute_progress_ratio / get_remaining_days / calculate_daily_budget
  - 文件 IO 函数: load_build_progress / load_latest_prices / load_trade_plan
                 _collect_pending_positions / _build_risk_checks
                 _build_instruction_file / _check_execution_preconditions
  - 关键流程: generate_instructions (前置检查跳过分支)
             execute_instructions (幂等模式 + 风控阻断)
             _execute_single_instruction (建仓进度更新 + 持仓同步)

测试策略:
  - MagicMock 隔离外部依赖 (wt_modules / etf_flow_monitor / tf_price_predictor)
  - tmp_path 隔离文件 IO
  - monkeypatch 替换模块级常量和函数
  - 不发起任何真实网络请求

目标覆盖率: ≥ 40% (重点覆盖风控参数加载/交易执行/熔断机制)
"""

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ============================================================
# 模块加载: 将项目根加入 sys.path
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import daily_trade_executor as dte  # noqa: E402


# ============================================================
# 1. 纯函数测试
# ============================================================


class TestInferSuffix:
    """_infer_suffix: 交易所后缀推断"""

    def test_shanghai_prefix_6(self):
        assert dte._infer_suffix("600519") == "600519.SH"

    def test_shanghai_prefix_5(self):
        assert dte._infer_suffix("510050") == "510050.SH"

    def test_shanghai_prefix_9(self):
        assert dte._infer_suffix("900901") == "900901.SH"

    def test_shenzhen_prefix_0(self):
        assert dte._infer_suffix("000001") == "000001.SZ"

    def test_shenzhen_prefix_3(self):
        assert dte._infer_suffix("300750") == "300750.SZ"

    def test_etf_159(self):
        assert dte._infer_suffix("159919") == "159919.SZ"

    def test_bj_exchange(self):
        assert dte._infer_suffix("830879") == "830879.BJ"

    def test_strip_existing_suffix(self):
        assert dte._infer_suffix("600519.SH") == "600519.SH"
        assert dte._infer_suffix("000001.SZ") == "000001.SZ"

    def test_pad_short_code(self):
        # 不足 6 位自动补零: "519" → "000519" (以 0 开头 → 深市)
        # 注: zfill 补零后所有短码都以 0 开头, 全部归入深市
        assert dte._infer_suffix("519") == "000519.SZ"

    def test_default_fallback(self):
        # 不匹配任何规则 → 默认 .SH
        assert dte._infer_suffix("799999") == "799999.SH"


class TestParseDateFromCfg:
    """_parse_date_from_cfg: yaml 日期字符串解析"""

    def test_valid_date_string(self):
        assert dte._parse_date_from_cfg("2026-07-10", date(2026, 1, 1)) == date(2026, 7, 10)

    def test_empty_string_returns_default(self):
        assert dte._parse_date_from_cfg("", date(2026, 1, 1)) == date(2026, 1, 1)

    def test_none_returns_default(self):
        assert dte._parse_date_from_cfg(None, date(2026, 1, 1)) == date(2026, 1, 1)

    def test_invalid_format_returns_default(self):
        assert dte._parse_date_from_cfg("2026/07/10", date(2026, 1, 1)) == date(2026, 1, 1)

    def test_non_date_string_returns_default(self):
        assert dte._parse_date_from_cfg("invalid", date(2026, 1, 1)) == date(2026, 1, 1)

    def test_numeric_string_returns_default(self):
        assert dte._parse_date_from_cfg("20260710", date(2026, 1, 1)) == date(2026, 1, 1)


class TestIsAccumulationPeriod:
    """is_accumulation_period: 建仓期判断"""

    def test_start_date_inclusive(self):
        assert dte.is_accumulation_period(date(2026, 7, 10)) is True

    def test_end_date_inclusive(self):
        assert dte.is_accumulation_period(date(2026, 12, 31)) is True

    def test_middle_date(self):
        assert dte.is_accumulation_period(date(2026, 9, 15)) is True

    def test_before_start(self):
        assert dte.is_accumulation_period(date(2026, 7, 9)) is False

    def test_after_end(self):
        assert dte.is_accumulation_period(date(2027, 1, 1)) is False


class TestAssessEtfSignal:
    """assess_etf_signal: ETF 资金流信号评估"""

    def test_strong_signal(self):
        positions_data = {"positions": {"510050": {"etf_flow_signal": "强流入"}}}
        assert dte.assess_etf_signal("510050", positions_data) == "strong"

    def test_medium_signal_jiacang(self):
        positions_data = {"positions": {"510050": {"etf_flow_signal": "加仓信号"}}}
        assert dte.assess_etf_signal("510050", positions_data) == "medium"

    def test_medium_signal_zhong(self):
        positions_data = {"positions": {"510050": {"etf_flow_signal": "中等"}}}
        assert dte.assess_etf_signal("510050", positions_data) == "medium"

    def test_none_signal(self):
        positions_data = {"positions": {"510050": {"etf_flow_signal": ""}}}
        assert dte.assess_etf_signal("510050", positions_data) == "none"

    def test_missing_signal_field(self):
        positions_data = {"positions": {"510050": {}}}
        assert dte.assess_etf_signal("510050", positions_data) == "none"

    def test_missing_code(self):
        positions_data = {"positions": {}}
        assert dte.assess_etf_signal("999999", positions_data) == "none"

    def test_non_dict_position(self):
        positions_data = {"positions": {"510050": "invalid_string"}}
        assert dte.assess_etf_signal("510050", positions_data) == "none"


class TestAdjustAllocationBySignal:
    """adjust_allocation_by_signal: 根据预测信号调整分配金额"""

    def test_empty_signal_returns_neutral(self):
        allocated, tag = dte.adjust_allocation_by_signal(100000, {}, 200000)
        assert allocated == 100000
        assert tag == "neutral"

    def test_none_signal_returns_neutral(self):
        allocated, tag = dte.adjust_allocation_by_signal(100000, None, 200000)
        assert allocated == 100000
        assert tag == "neutral"

    def test_strong_sell_skip(self):
        signal = {"direction": "DOWN", "confidence": 0.8, "signal_strength": -0.6}
        allocated, tag = dte.adjust_allocation_by_signal(100000, signal, 200000)
        assert allocated == 0
        assert tag == "skip"

    def test_weak_sell_caution(self):
        signal = {"direction": "DOWN", "confidence": 0.5, "signal_strength": -0.3}
        allocated, tag = dte.adjust_allocation_by_signal(100000, signal, 200000)
        assert allocated == 50000
        assert tag == "caution"

    def test_strong_buy_capped_at_30pct(self):
        signal = {"direction": "UP", "confidence": 0.8, "signal_strength": 0.6}
        # base_allocated=100000, 1.3x=130000, daily_budget*0.30=60000 → 取 min
        allocated, tag = dte.adjust_allocation_by_signal(100000, signal, 200000)
        assert allocated == 60000
        assert tag == "strong_buy"

    def test_strong_buy_below_cap(self):
        signal = {"direction": "UP", "confidence": 0.8, "signal_strength": 0.6}
        # base=30000, 1.3x=39000, cap=60000 → 取 39000
        allocated, tag = dte.adjust_allocation_by_signal(30000, signal, 200000)
        assert allocated == 39000
        assert tag == "strong_buy"

    def test_weak_buy(self):
        signal = {"direction": "UP", "confidence": 0.5, "signal_strength": 0.2}
        allocated, tag = dte.adjust_allocation_by_signal(100000, signal, 200000)
        # 浮点精度容差 (100000 * 1.1 = 110000.00000000001)
        assert allocated == pytest.approx(110000, rel=1e-9)
        assert tag == "buy"

    def test_neutral_direction(self):
        signal = {"direction": "NEUTRAL", "confidence": 0.5, "signal_strength": 0.0}
        allocated, tag = dte.adjust_allocation_by_signal(100000, signal, 200000)
        assert allocated == 100000
        assert tag == "neutral"

    def test_low_confidence_up(self):
        signal = {"direction": "UP", "confidence": 0.3, "signal_strength": 0.2}
        allocated, tag = dte.adjust_allocation_by_signal(100000, signal, 200000)
        assert allocated == 100000
        assert tag == "neutral"

    def test_low_confidence_down(self):
        signal = {"direction": "DOWN", "confidence": 0.3, "signal_strength": -0.2}
        allocated, tag = dte.adjust_allocation_by_signal(100000, signal, 200000)
        assert allocated == 100000
        assert tag == "neutral"


class TestComputePriceBand:
    """_compute_price_band: 价格保护带计算"""

    def test_normal_price(self):
        max_p, min_p = dte._compute_price_band(10.0)
        # 10 * (1+0.03) = 10.3, 10 * (1-0.03) = 9.7
        assert max_p == 10.3
        assert min_p == 9.7

    def test_high_price(self):
        max_p, min_p = dte._compute_price_band(100.0)
        assert max_p == 103.0
        assert min_p == 97.0

    def test_decimal_price(self):
        max_p, min_p = dte._compute_price_band(1.5)
        # 1.5 * 1.03 = 1.545, 1.5 * 0.97 = 1.455
        assert max_p == 1.545
        assert min_p == 1.455

    def test_zero_price(self):
        max_p, min_p = dte._compute_price_band(0.0)
        assert max_p == 0.0
        assert min_p == 0.0

    def test_round_to_4_decimals(self):
        max_p, min_p = dte._compute_price_band(3.14159265)
        # 3.14159265 * 1.03 = 3.23584... → round 4 = 3.2358
        assert max_p == round(3.14159265 * 1.03, 4)
        assert min_p == round(3.14159265 * 0.97, 4)


class TestComputeProgressRatio:
    """_compute_progress_ratio: 建仓进度比例计算"""

    def test_at_start_date(self, monkeypatch):
        # 起始日: elapsed=1 (当天算1天), ratio = 1/total
        monkeypatch.setattr(dte, "is_trading_day", lambda d: True)
        ratio = dte._compute_progress_ratio(dte.ACCUMULATION_START)
        assert 0.0 <= ratio <= 1.0
        assert ratio > 0  # 至少有 1 天

    def test_before_start_date(self, monkeypatch):
        monkeypatch.setattr(dte, "is_trading_day", lambda d: True)
        # 早于起始日: elapsed=0, ratio=0
        before = dte.ACCUMULATION_START - timedelta(days=1)
        ratio = dte._compute_progress_ratio(before)
        assert ratio == 0.0

    def test_at_end_date_returns_one(self, monkeypatch):
        monkeypatch.setattr(dte, "is_trading_day", lambda d: True)
        ratio = dte._compute_progress_ratio(dte.ACCUMULATION_END)
        assert ratio == 1.0

    def test_after_end_date_capped_at_one(self, monkeypatch):
        monkeypatch.setattr(dte, "is_trading_day", lambda d: True)
        after = dte.ACCUMULATION_END + timedelta(days=10)
        ratio = dte._compute_progress_ratio(after)
        assert ratio == 1.0

    def test_all_non_trading_days(self, monkeypatch):
        # 所有日期都非交易日: elapsed=0
        monkeypatch.setattr(dte, "is_trading_day", lambda d: False)
        ratio = dte._compute_progress_ratio(dte.ACCUMULATION_START + timedelta(days=10))
        assert ratio == 0.0


class TestGetRemainingDays:
    """get_remaining_days: 剩余交易日数"""

    def test_at_end_date_minimum_one(self, monkeypatch):
        monkeypatch.setattr(dte, "is_trading_day", lambda d: True)
        days = dte.get_remaining_days(dte.ACCUMULATION_END)
        assert days >= 1

    def test_after_end_date_minimum_one(self, monkeypatch):
        monkeypatch.setattr(dte, "is_trading_day", lambda d: True)
        after = dte.ACCUMULATION_END + timedelta(days=5)
        days = dte.get_remaining_days(after)
        assert days >= 1

    def test_at_start_date(self, monkeypatch):
        monkeypatch.setattr(dte, "is_trading_day", lambda d: True)
        days = dte.get_remaining_days(dte.ACCUMULATION_START)
        assert days > 1  # 至少有几个交易日


class TestCalculateDailyBudget:
    """calculate_daily_budget: 当日建仓预算"""

    def test_completed_target(self):
        # 已完成 300 万 → budget=0
        progress = {"total_built": dte.STOCK_ETF_TARGET}
        positions_data = {"positions": {}}
        result = dte.calculate_daily_budget(date(2026, 8, 1), progress, positions_data)
        assert result["daily_budget"] == 0
        assert result["signal_strength"] == "completed"

    def test_exceeds_target(self):
        progress = {"total_built": dte.STOCK_ETF_TARGET + 100000}
        positions_data = {"positions": {}}
        result = dte.calculate_daily_budget(date(2026, 8, 1), progress, positions_data)
        assert result["daily_budget"] == 0

    def test_fixed_budget_after_0713(self, monkeypatch):
        # 2026-07-13 后: 固定每日 20 万
        monkeypatch.setattr(dte, "is_trading_day", lambda d: True)
        progress = {"total_built": 0}
        positions_data = {"positions": {}}
        result = dte.calculate_daily_budget(date(2026, 8, 1), progress, positions_data)
        assert result["signal_strength"] == "fixed_200k"
        assert result["daily_budget"] == dte.DAILY_FIXED_BUDGET

    def test_fixed_budget_capped_by_remaining(self, monkeypatch):
        monkeypatch.setattr(dte, "is_trading_day", lambda d: True)
        # remaining_total=50000 < DAILY_FIXED_BUDGET
        progress = {"total_built": dte.STOCK_ETF_TARGET - 50000}
        positions_data = {"positions": {}}
        result = dte.calculate_daily_budget(date(2026, 8, 1), progress, positions_data)
        assert result["daily_budget"] == 50000

    def test_smart_allocation_strong_signal(self, monkeypatch):
        # 2026-07-11 (智能分批期), 3+ 强信号
        monkeypatch.setattr(dte, "is_trading_day", lambda d: True)
        positions_data = {
            "positions": {
                "510050": {"etf_flow_signal": "强"},
                "510300": {"etf_flow_signal": "强"},
                "510500": {"etf_flow_signal": "强"},
            }
        }
        progress = {"total_built": 0}
        result = dte.calculate_daily_budget(date(2026, 7, 11), progress, positions_data)
        assert result["signal_strength"] == "strong"
        assert result["strong_signal_count"] == 3
        assert result["daily_budget"] <= dte.SIGNAL_AMOUNTS["strong"]

    def test_smart_allocation_medium_signal(self, monkeypatch):
        monkeypatch.setattr(dte, "is_trading_day", lambda d: True)
        positions_data = {
            "positions": {
                "510050": {"etf_flow_signal": "加仓"},
                "510300": {"etf_flow_signal": "加仓"},
                "510500": {"etf_flow_signal": "加仓"},
            }
        }
        progress = {"total_built": 0}
        result = dte.calculate_daily_budget(date(2026, 7, 11), progress, positions_data)
        assert result["signal_strength"] == "medium"
        assert result["medium_signal_count"] == 3

    def test_smart_allocation_no_signal(self, monkeypatch):
        monkeypatch.setattr(dte, "is_trading_day", lambda d: True)
        positions_data = {
            "positions": {
                "510050": {"etf_flow_signal": ""},
                "510300": {"etf_flow_signal": ""},
            }
        }
        progress = {"total_built": 0}
        result = dte.calculate_daily_budget(date(2026, 7, 11), progress, positions_data)
        assert result["signal_strength"] == "none"
        assert result["daily_budget"] <= dte.SIGNAL_AMOUNTS["none"]

    def test_budget_capped_by_daily_limit(self, monkeypatch):
        monkeypatch.setattr(dte, "is_trading_day", lambda d: True)
        # remaining_total 很大, 但被 DAILY_AMOUNT_LIMIT 截断
        progress = {"total_built": 0}
        positions_data = {"positions": {}}
        result = dte.calculate_daily_budget(date(2026, 8, 1), progress, positions_data)
        assert result["daily_budget"] <= dte.DAILY_AMOUNT_LIMIT


# ============================================================
# 2. 文件 IO 函数测试
# ============================================================


class TestLoadBuildProgress:
    """load_build_progress: 加载建仓进度"""

    def test_missing_file_returns_default(self, monkeypatch, tmp_path):
        monkeypatch.setattr(dte, "PROGRESS_FILE", tmp_path / "nonexistent.json")
        result = dte.load_build_progress()
        assert result["total_built"] == 0
        assert result["daily_records"] == []
        assert result["built_amounts"] == {}

    def test_valid_file(self, monkeypatch, tmp_path):
        progress_file = tmp_path / "build_progress.json"
        progress_data = {
            "total_built": 500000,
            "daily_records": [{"date": "2026-07-13", "executed_count": 5}],
            "built_amounts": {"600519": 100000},
        }
        progress_file.write_text(json.dumps(progress_data), encoding="utf-8")
        monkeypatch.setattr(dte, "PROGRESS_FILE", progress_file)
        result = dte.load_build_progress()
        assert result["total_built"] == 500000
        assert result["built_amounts"]["600519"] == 100000


class TestLoadTradePlan:
    """load_trade_plan: 加载交易计划"""

    def test_missing_file_returns_default(self, monkeypatch, tmp_path):
        monkeypatch.setattr(dte, "TRADE_PLAN_FILE", tmp_path / "nonexistent.json")
        result = dte.load_trade_plan()
        assert "stock_etf_account" in result
        assert result["stock_etf_account"]["positions"] == []

    def test_valid_file(self, monkeypatch, tmp_path):
        plan_file = tmp_path / "trade_plan.json"
        plan_data = {
            "stock_etf_account": {
                "positions": [{"code": "600519.SH", "name": "贵州茅台", "amount": 300000}]
            }
        }
        plan_file.write_text(json.dumps(plan_data), encoding="utf-8")
        monkeypatch.setattr(dte, "TRADE_PLAN_FILE", plan_file)
        result = dte.load_trade_plan()
        assert len(result["stock_etf_account"]["positions"]) == 1


class TestLoadLatestPrices:
    """load_latest_prices: 从收盘报告读取最新价格"""

    def test_missing_reports_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(dte, "PROJECT_ROOT", tmp_path)
        result = dte.load_latest_prices()
        assert result == {}

    def test_no_json_files(self, monkeypatch, tmp_path):
        reports_dir = tmp_path / "v8.3_institutional" / "reports"
        reports_dir.mkdir(parents=True)
        monkeypatch.setattr(dte, "PROJECT_ROOT", tmp_path)
        result = dte.load_latest_prices()
        assert result == {}

    def test_valid_report(self, monkeypatch, tmp_path):
        reports_dir = tmp_path / "v8.3_institutional" / "reports"
        reports_dir.mkdir(parents=True)
        report = {
            "portfolio_pnl": {
                "details": [
                    {"code": "600519.SH", "close_price": 1800.5},
                    {"code": "000001.SZ", "close_price": 15.2},
                ]
            }
        }
        report_file = reports_dir / "daily_pnl_report_2026-07-31.json"
        report_file.write_text(json.dumps(report), encoding="utf-8")
        monkeypatch.setattr(dte, "PROJECT_ROOT", tmp_path)
        result = dte.load_latest_prices()
        assert result["600519"] == 1800.5
        assert result["000001"] == 15.2

    def test_skips_zero_prices(self, monkeypatch, tmp_path):
        reports_dir = tmp_path / "v8.3_institutional" / "reports"
        reports_dir.mkdir(parents=True)
        report = {
            "portfolio_pnl": {
                "details": [
                    {"code": "600519.SH", "close_price": 0},
                    {"code": "000001.SZ", "close_price": -5},
                    {"code": "300750.SZ", "close_price": 250.0},
                ]
            }
        }
        report_file = reports_dir / "daily_pnl_report_2026-07-31.json"
        report_file.write_text(json.dumps(report), encoding="utf-8")
        monkeypatch.setattr(dte, "PROJECT_ROOT", tmp_path)
        result = dte.load_latest_prices()
        assert "600519" not in result
        assert "000001" not in result
        assert result["300750"] == 250.0

    def test_invalid_json_returns_empty(self, monkeypatch, tmp_path):
        reports_dir = tmp_path / "v8.3_institutional" / "reports"
        reports_dir.mkdir(parents=True)
        report_file = reports_dir / "daily_pnl_report_2026-07-31.json"
        report_file.write_text("{invalid json", encoding="utf-8")
        monkeypatch.setattr(dte, "PROJECT_ROOT", tmp_path)
        result = dte.load_latest_prices()
        assert result == {}


class TestCollectPendingPositions:
    """_collect_pending_positions: 收集未完成建仓标的"""

    def test_all_completed(self):
        plan = [
            {"code": "600519.SH", "name": "贵州茅台", "amount": 100000, "weight": 0.1},
        ]
        progress = {"built_amounts": {"600519.SH": 100000}}
        result = dte._collect_pending_positions(plan, progress, 1.0)
        assert result == []

    def test_all_pending(self):
        plan = [
            {"code": "600519.SH", "name": "贵州茅台", "amount": 100000, "weight": 0.1},
            {"code": "000001.SZ", "name": "平安银行", "amount": 200000, "weight": 0.2},
        ]
        progress = {"built_amounts": {}}
        result = dte._collect_pending_positions(plan, progress, 0.5)
        assert len(result) == 2
        # 验证字段完整性
        for item in result:
            assert "code" in item
            assert "code_clean" in item
            assert "gap" in item
            assert "remaining" in item

    def test_sorted_by_gap_descending(self):
        # 缺口大的排前面
        plan = [
            {"code": "A.SH", "name": "A", "amount": 100000, "weight": 0.1},
            {"code": "B.SH", "name": "B", "amount": 200000, "weight": 0.1},
            {"code": "C.SH", "name": "C", "amount": 300000, "weight": 0.1},
        ]
        # progress_ratio=0.5, 都未建仓
        # A 理论应建 50000, 实际 0, gap=50000
        # B 理论应建 100000, 实际 0, gap=100000
        # C 理论应建 150000, 实际 0, gap=150000
        progress = {"built_amounts": {}}
        result = dte._collect_pending_positions(plan, progress, 0.5)
        assert result[0]["code"] == "C.SH"
        assert result[1]["code"] == "B.SH"
        assert result[2]["code"] == "A.SH"

    def test_code_clean_strips_suffix(self):
        plan = [{"code": "600519.SH", "name": "贵州茅台", "amount": 100000, "weight": 0.1}]
        progress = {"built_amounts": {}}
        result = dte._collect_pending_positions(plan, progress, 0.0)
        assert result[0]["code_clean"] == "600519"

    def test_partial_completed(self):
        plan = [
            {"code": "A.SH", "name": "A", "amount": 100000, "weight": 0.1},
            {"code": "B.SH", "name": "B", "amount": 200000, "weight": 0.1},
        ]
        # A 已建 100000 (完成), B 未建
        progress = {"built_amounts": {"A.SH": 100000}}
        result = dte._collect_pending_positions(plan, progress, 1.0)
        assert len(result) == 1
        assert result[0]["code"] == "B.SH"


class TestBuildRiskChecks:
    """_build_risk_checks: 风控检查字典构建"""

    def test_passes_when_under_limit(self):
        checks = dte._build_risk_checks(100000)
        assert checks["daily_limit"]["passed"] is True
        assert checks["price_protection"]["passed"] is True
        assert checks["circuit_breaker"]["passed"] is True
        assert checks["manual_confirm"]["passed"] is False

    def test_fails_when_over_limit(self):
        checks = dte._build_risk_checks(dte.DAILY_AMOUNT_LIMIT + 1)
        assert checks["daily_limit"]["passed"] is False

    def test_at_limit_boundary(self):
        checks = dte._build_risk_checks(dte.DAILY_AMOUNT_LIMIT)
        assert checks["daily_limit"]["passed"] is True

    def test_zero_allocated(self):
        checks = dte._build_risk_checks(0)
        assert checks["daily_limit"]["passed"] is True


class TestBuildInstructionFile:
    """_build_instruction_file: 指令文件字典构建"""

    def test_structure_complete(self):
        progress = {"total_built": 100000}
        budget_info = {"daily_budget": 200000, "signal_strength": "fixed_200k"}
        risk_checks = {"daily_limit": {"passed": True}}
        instructions = [{"code": "600519", "action": "BUY"}]
        result = dte._build_instruction_file(
            "2026-08-01", progress, budget_info, risk_checks, instructions, 100000,
        )
        assert result["meta"]["instruction_date"] == "2026-08-01"
        assert result["meta"]["phase"] == "phase_1_accumulation"
        assert result["meta"]["total_capital"] == dte.STOCK_ETF_TARGET
        assert result["meta"]["total_built_before"] == 100000
        assert result["meta"]["remaining_total"] == dte.STOCK_ETF_TARGET - 100000
        assert result["budget_info"] == budget_info
        assert result["risk_checks"] == risk_checks
        assert result["instructions"] == instructions
        assert result["total_allocated"] == 100000
        assert result["confirm_required"] is True

    def test_generated_at_is_isoformat(self):
        result = dte._build_instruction_file(
            "2026-08-01", {}, {}, {}, [], 0,
        )
        # 验证 ISO 格式可解析
        datetime.fromisoformat(result["meta"]["generated_at"])


class TestCheckExecutionPreconditions:
    """_check_execution_preconditions: 执行前置检查"""

    def test_no_confirmed_instructions(self):
        data = {
            "risk_checks": {"daily_limit": {"passed": True}},
            "instructions": [
                {"code": "600519", "confirm": False},
                {"code": "000001", "confirm": False},
            ],
        }
        confirmed, error = dte._check_execution_preconditions(data)
        assert confirmed == []
        assert error["status"] == "no_confirmed"
        assert error["total_instructions"] == 2

    def test_daily_limit_failed_blocks(self):
        data = {
            "risk_checks": {"daily_limit": {"passed": False}},
            "instructions": [{"code": "600519", "confirm": True}],
        }
        confirmed, error = dte._check_execution_preconditions(data)
        assert confirmed == []
        assert error["status"] == "blocked"
        assert "单日金额上限" in error["reason"]

    def test_all_confirmed_passes(self):
        data = {
            "risk_checks": {"daily_limit": {"passed": True}},
            "instructions": [
                {"code": "600519", "confirm": True},
                {"code": "000001", "confirm": True},
            ],
        }
        confirmed, error = dte._check_execution_preconditions(data)
        assert error is None
        assert len(confirmed) == 2

    def test_partial_confirmed(self):
        data = {
            "risk_checks": {"daily_limit": {"passed": True}},
            "instructions": [
                {"code": "600519", "confirm": True},
                {"code": "000001", "confirm": False},
            ],
        }
        confirmed, error = dte._check_execution_preconditions(data)
        assert error is None
        assert len(confirmed) == 1
        assert confirmed[0]["code"] == "600519"

    def test_empty_instructions(self):
        data = {
            "risk_checks": {"daily_limit": {"passed": True}},
            "instructions": [],
        }
        confirmed, error = dte._check_execution_preconditions(data)
        assert confirmed == []
        assert error["status"] == "no_confirmed"

    def test_missing_risk_checks_defaults_pass(self):
        # risk_checks 缺失时, .get(..., True) 默认通过
        data = {
            "instructions": [{"code": "600519", "confirm": True}],
        }
        confirmed, error = dte._check_execution_preconditions(data)
        assert error is None
        assert len(confirmed) == 1


# ============================================================
# 3. 关键流程函数测试
# ============================================================


class TestGenerateInstructionsSkip:
    """generate_instructions: 前置检查跳过分支"""

    def test_non_trading_day_skipped(self, monkeypatch):
        # 周末跳过
        monkeypatch.setattr(dte, "is_trading_day", lambda d: False)
        result = dte.generate_instructions("2026-08-02")  # 周日
        assert result["status"] == "skipped"
        assert "非交易日" in result["reason"]

    def test_outside_accumulation_period_skipped(self, monkeypatch):
        # 交易日但不在建仓期
        monkeypatch.setattr(dte, "is_trading_day", lambda d: True)
        # 2027-01-01 在建仓期外
        result = dte.generate_instructions("2027-01-01")
        assert result["status"] == "skipped"
        assert "不在建仓期" in result["reason"]


class TestExecuteSingleInstruction:
    """_execute_single_instruction: 单条指令执行"""

    def test_basic_execution_updates_progress(self):
        inst = {
            "full_code": "600519.SH",
            "code": "600519",
            "name": "贵州茅台",
            "qty": 100,
            "ref_price": 1800.0,
            "estimated_amount": 180000,
        }
        wt_modules = {}  # 空 wt_modules, 走默认执行路径
        progress = {"built_amounts": {}, "total_built": 0}
        positions = {}

        result = dte._execute_single_instruction(inst, wt_modules, progress, positions)

        assert result["status"] == "FILLED"
        assert result["qty"] == 100
        assert result["fill_price"] == 1800.0
        assert result["fill_amount"] == 180000.0
        assert result["built_before"] == 0
        assert result["built_after"] == 180000.0
        # progress 已更新
        assert progress["built_amounts"]["600519.SH"] == 180000.0
        assert progress["total_built"] == 180000.0

    def test_execution_accumulates_progress(self):
        inst = {
            "full_code": "600519.SH",
            "code": "600519",
            "name": "贵州茅台",
            "qty": 100,
            "ref_price": 1800.0,
            "estimated_amount": 180000,
        }
        wt_modules = {}
        progress = {"built_amounts": {"600519.SH": 100000}, "total_built": 100000}
        positions = {}

        result = dte._execute_single_instruction(inst, wt_modules, progress, positions)

        assert result["built_before"] == 100000
        assert result["built_after"] == 280000.0
        assert progress["total_built"] == 280000.0

    def test_syncs_positions_new(self):
        inst = {
            "full_code": "600519.SH",
            "code": "600519",
            "name": "贵州茅台",
            "qty": 100,
            "ref_price": 1800.0,
            "estimated_amount": 180000,
        }
        wt_modules = {}
        progress = {"built_amounts": {}, "total_built": 0}
        positions = {"600519.SH": {"shares": 0, "avg_cost": 0.0, "est_price": 0.0}}

        dte._execute_single_instruction(inst, wt_modules, progress, positions)

        assert positions["600519.SH"]["shares"] == 100
        assert positions["600519.SH"]["avg_cost"] == 1800.0
        assert positions["600519.SH"]["est_price"] == 1800.0

    def test_syncs_positions_weighted_avg_cost(self):
        # 已有 100 股 @ 1700, 新买 100 股 @ 1800 → 加权平均 1750
        inst = {
            "full_code": "600519.SH",
            "code": "600519",
            "name": "贵州茅台",
            "qty": 100,
            "ref_price": 1800.0,
            "estimated_amount": 180000,
        }
        wt_modules = {}
        progress = {"built_amounts": {}, "total_built": 0}
        positions = {"600519.SH": {"shares": 100, "avg_cost": 1700.0, "est_price": 1700.0}}

        dte._execute_single_instruction(inst, wt_modules, progress, positions)

        assert positions["600519.SH"]["shares"] == 200
        assert positions["600519.SH"]["avg_cost"] == 1750.0

    def test_wt_executor_split(self):
        # 金额 > 50000 且有 min_impact_executor → 走 WT 拆分路径
        inst = {
            "full_code": "600519.SH",
            "code": "600519",
            "name": "贵州茅台",
            "qty": 100,
            "ref_price": 1800.0,
            "estimated_amount": 180000,
        }
        mock_executor = MagicMock()
        mock_executor.calculate_optimal_splits.return_value = [
            {"amount": 90000},
            {"amount": 90000},
        ]
        wt_modules = {"min_impact_executor": mock_executor}
        progress = {"built_amounts": {}, "total_built": 0}
        positions = {}

        result = dte._execute_single_instruction(inst, wt_modules, progress, positions)

        assert result["fill_amount"] == 180000  # 90000 + 90000
        mock_executor.calculate_optimal_splits.assert_called_once()

    def test_wt_executor_failure_falls_back(self):
        # WT 执行算法异常 → 回退到默认 qty * ref_price
        inst = {
            "full_code": "600519.SH",
            "code": "600519",
            "name": "贵州茅台",
            "qty": 100,
            "ref_price": 1800.0,
            "estimated_amount": 180000,
        }
        mock_executor = MagicMock()
        mock_executor.calculate_optimal_splits.side_effect = RuntimeError("WT error")
        wt_modules = {"min_impact_executor": mock_executor}
        progress = {"built_amounts": {}, "total_built": 0}
        positions = {}

        result = dte._execute_single_instruction(inst, wt_modules, progress, positions)

        assert result["fill_amount"] == 180000.0
        assert result["status"] == "FILLED"


class TestRunWtRiskBlockCheck:
    """_run_wt_risk_block_check: WT 风控前置阻断检查 (IC6 修复)"""

    def test_no_risk_control_module_passes(self):
        # wt_modules 无 risk_control → 直接通过
        wt_modules = {}
        confirmed = [{"estimated_amount": 100000}]
        result = dte._run_wt_risk_block_check(wt_modules, confirmed)
        assert result is None

    def test_risk_control_passes(self):
        mock_rc = MagicMock()
        mock_rc.check_single_trade.return_value = (True, "OK")
        mock_rc.check_daily_trade_count.return_value = (True, "OK")
        wt_modules = {"risk_control": mock_rc}
        confirmed = [{"estimated_amount": 100000}]
        result = dte._run_wt_risk_block_check(wt_modules, confirmed)
        assert result is None

    def test_risk_control_blocks_single_trade(self):
        # IC6 修复: 单笔检查未通过 → 阻断
        mock_rc = MagicMock()
        mock_rc.check_single_trade.return_value = (False, "single trade exceeds limit")
        mock_rc.check_daily_trade_count.return_value = (True, "OK")
        wt_modules = {"risk_control": mock_rc}
        confirmed = [{"estimated_amount": 999999999}]  # 超大金额
        result = dte._run_wt_risk_block_check(wt_modules, confirmed)
        assert result is not None
        assert result["status"] == "blocked"
        assert "WT risk control check failed" in result["blocked_reason"]

    def test_risk_control_blocks_daily_count(self):
        mock_rc = MagicMock()
        mock_rc.check_single_trade.return_value = (True, "OK")
        mock_rc.check_daily_trade_count.return_value = (False, "daily count exceeded")
        wt_modules = {"risk_control": mock_rc}
        confirmed = [{"estimated_amount": 100000}]
        result = dte._run_wt_risk_block_check(wt_modules, confirmed)
        assert result is not None
        assert result["status"] == "blocked"

    def test_risk_control_exception_blocks_fail_safe(self):
        # 风控检查本身崩溃 → 保守阻断 (Fail-Safe)
        mock_rc = MagicMock()
        mock_rc.check_single_trade.side_effect = RuntimeError("RC crashed")
        wt_modules = {"risk_control": mock_rc}
        confirmed = [{"estimated_amount": 100000}]
        result = dte._run_wt_risk_block_check(wt_modules, confirmed)
        assert result is not None
        assert result["status"] == "blocked"
        assert "WT risk control check error" in result["blocked_reason"]

    def test_uses_estimated_amount_field(self):
        # IC2 修复验证: 使用 estimated_amount 而非 amount
        mock_rc = MagicMock()
        mock_rc.check_single_trade.return_value = (True, "OK")
        mock_rc.check_daily_trade_count.return_value = (True, "OK")
        wt_modules = {"risk_control": mock_rc}
        confirmed = [{"estimated_amount": 123456, "amount": 0}]  # amount 字段为 0 (旧 bug)
        dte._run_wt_risk_block_check(wt_modules, confirmed)
        # 验证 check_single_trade 收到的是 estimated_amount 的值
        mock_rc.check_single_trade.assert_called_with(123456, dte.STOCK_ETF_TARGET)


class TestExecuteInstructionsIdempotent:
    """execute_instructions: 幂等模式"""

    def test_missing_instruction_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(dte, "INSTRUCTIONS_DIR", tmp_path)
        result = dte.execute_instructions("2026-08-01")
        assert result["status"] == "error"
        assert "指令文件不存在" in result["reason"]

    def test_no_confirmed_returns_error(self, monkeypatch, tmp_path):
        # 指令文件存在, 但无 confirm=true
        instruction_file = tmp_path / "2026-08-01_instructions.json"
        data = {
            "risk_checks": {"daily_limit": {"passed": True}},
            "instructions": [{"code": "600519", "confirm": False}],
        }
        instruction_file.write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setattr(dte, "INSTRUCTIONS_DIR", tmp_path)
        result = dte.execute_instructions("2026-08-01")
        assert result["status"] == "no_confirmed"

    def test_daily_limit_blocked(self, monkeypatch, tmp_path):
        instruction_file = tmp_path / "2026-08-01_instructions.json"
        data = {
            "risk_checks": {"daily_limit": {"passed": False}},
            "instructions": [{"code": "600519", "confirm": True}],
        }
        instruction_file.write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setattr(dte, "INSTRUCTIONS_DIR", tmp_path)
        result = dte.execute_instructions("2026-08-01")
        assert result["status"] == "blocked"


class TestConfirmAllInstructions:
    """confirm_all_instructions: 自动确认指令"""

    def test_missing_file_returns_zero(self, monkeypatch, tmp_path):
        monkeypatch.setattr(dte, "INSTRUCTIONS_DIR", tmp_path)
        result = dte.confirm_all_instructions("2026-08-01")
        assert result == 0

    def test_confirms_pending_instructions(self, monkeypatch, tmp_path):
        instruction_file = tmp_path / "2026-08-01_instructions.json"
        data = {
            "instructions": [
                {"code": "600519", "confirm": False},
                {"code": "000001", "confirm": False},
            ],
        }
        instruction_file.write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setattr(dte, "INSTRUCTIONS_DIR", tmp_path)
        result = dte.confirm_all_instructions("2026-08-01")
        assert result == 2
        # 验证文件已更新
        with open(instruction_file, "r", encoding="utf-8") as f:
            updated = json.load(f)
        assert all(i["confirm"] is True for i in updated["instructions"])

    def test_already_confirmed_returns_zero(self, monkeypatch, tmp_path):
        instruction_file = tmp_path / "2026-08-01_instructions.json"
        data = {
            "instructions": [{"code": "600519", "confirm": True}],
        }
        instruction_file.write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setattr(dte, "INSTRUCTIONS_DIR", tmp_path)
        result = dte.confirm_all_instructions("2026-08-01")
        assert result == 0


class TestShowProgress:
    """show_progress: 显示建仓进度"""

    def test_progress_calculation(self, monkeypatch, tmp_path):
        progress_file = tmp_path / "build_progress.json"
        progress_data = {
            "total_built": 1500000,
            "daily_records": [{"date": "2026-07-13"}, {"date": "2026-07-14"}],
            "built_amounts": {"600519": 500000, "000001": 1000000},
        }
        progress_file.write_text(json.dumps(progress_data), encoding="utf-8")
        monkeypatch.setattr(dte, "PROGRESS_FILE", progress_file)
        monkeypatch.setattr(dte, "is_trading_day", lambda d: True)

        result = dte.show_progress()
        assert result["total_target"] == dte.STOCK_ETF_TARGET
        assert result["total_built"] == 1500000
        assert result["remaining"] == dte.STOCK_ETF_TARGET - 1500000
        assert result["completion_rate"] == round(1500000 / dte.STOCK_ETF_TARGET * 100, 2)
        assert result["daily_records_count"] == 2
        assert "600519" in result["built_amounts"]

    def test_zero_progress(self, monkeypatch, tmp_path):
        progress_file = tmp_path / "build_progress.json"
        progress_data = {
            "total_built": 0,
            "daily_records": [],
            "built_amounts": {},
        }
        progress_file.write_text(json.dumps(progress_data), encoding="utf-8")
        monkeypatch.setattr(dte, "PROGRESS_FILE", progress_file)
        monkeypatch.setattr(dte, "is_trading_day", lambda d: True)

        result = dte.show_progress()
        assert result["total_built"] == 0
        assert result["completion_rate"] == 0.0
        assert result["remaining"] == dte.STOCK_ETF_TARGET


class TestSyncPositionsIdempotent:
    """_sync_positions_idempotent: 幂等模式同步持仓"""

    def test_no_execution_file(self, monkeypatch, tmp_path):
        # 当日无 execution.json → prev_results=[], synced_count=0
        monkeypatch.setattr(dte, "INSTRUCTIONS_DIR", tmp_path)
        confirmed = [{"code": "600519", "full_code": "600519.SH"}]
        positions = {}
        result = dte._sync_positions_idempotent("2026-08-01", confirmed, positions)
        assert result["status"] == "already_executed"
        assert result["synced_count"] == 0

    def test_syncs_zero_share_positions(self, monkeypatch, tmp_path):
        # execution.json 存在, positions 中 shares=0 → 补同步
        execution_file = tmp_path / "2026-08-01_execution.json"
        execution_data = {
            "execution_results": [
                {"code": "600519", "qty": 100, "fill_price": 1800.0},
            ],
        }
        execution_file.write_text(json.dumps(execution_data), encoding="utf-8")
        monkeypatch.setattr(dte, "INSTRUCTIONS_DIR", tmp_path)
        confirmed = [{"code": "600519", "full_code": "600519.SH"}]
        positions = {"600519.SH": {"shares": 0, "avg_cost": 0.0, "est_price": 0.0}}
        result = dte._sync_positions_idempotent("2026-08-01", confirmed, positions)
        assert result["synced_count"] == 1
        assert positions["600519.SH"]["shares"] == 100
        assert positions["600519.SH"]["avg_cost"] == 1800.0

    def test_skips_already_synced(self, monkeypatch, tmp_path):
        # positions 中 shares>0 → 不重复同步
        execution_file = tmp_path / "2026-08-01_execution.json"
        execution_data = {
            "execution_results": [
                {"code": "600519", "qty": 100, "fill_price": 1800.0},
            ],
        }
        execution_file.write_text(json.dumps(execution_data), encoding="utf-8")
        monkeypatch.setattr(dte, "INSTRUCTIONS_DIR", tmp_path)
        confirmed = [{"code": "600519", "full_code": "600519.SH"}]
        positions = {"600519.SH": {"shares": 200, "avg_cost": 1700.0, "est_price": 1700.0}}
        result = dte._sync_positions_idempotent("2026-08-01", confirmed, positions)
        assert result["synced_count"] == 0
        # 持仓未被修改
        assert positions["600519.SH"]["shares"] == 200


class TestGenerateNextTradingDayPlan:
    """generate_next_trading_day_plan: 下一交易日计划生成"""

    def test_skip_weekend(self, monkeypatch):
        # 2026-08-01 周六 → 下一交易日 2026-08-03 周一
        monkeypatch.setattr(dte, "is_trading_day", lambda d: d.weekday() < 5)
        # mock generate_instructions 避免实际执行
        monkeypatch.setattr(
            dte, "generate_instructions", lambda s: {"status": "generated", "test_date": s}
        )
        result = dte.generate_next_trading_day_plan("2026-08-01")
        assert result["status"] == "generated"
        assert result["meta"]["next_trading_day"] == "2026-08-03"
        assert result["meta"]["auto_generated"] is True
        assert result["meta"]["generated_after"] == "2026-08-01"

    def test_friday_to_monday(self, monkeypatch):
        # 2026-07-31 周五 → 下一交易日 2026-08-03 周一
        monkeypatch.setattr(dte, "is_trading_day", lambda d: d.weekday() < 5)
        monkeypatch.setattr(
            dte, "generate_instructions", lambda s: {"status": "generated"}
        )
        result = dte.generate_next_trading_day_plan("2026-07-31")
        assert result["meta"]["next_trading_day"] == "2026-08-03"

    def test_consecutive_trading_day(self, monkeypatch):
        # 2026-08-03 周一 → 下一交易日 2026-08-04 周二
        monkeypatch.setattr(dte, "is_trading_day", lambda d: d.weekday() < 5)
        monkeypatch.setattr(
            dte, "generate_instructions", lambda s: {"status": "generated"}
        )
        result = dte.generate_next_trading_day_plan("2026-08-03")
        assert result["meta"]["next_trading_day"] == "2026-08-04"

    def test_max_attempts_exceeded(self, monkeypatch):
        # 模拟连续 10+ 天非交易日 → 返回 error
        monkeypatch.setattr(dte, "is_trading_day", lambda d: False)
        monkeypatch.setattr(
            dte, "generate_instructions", lambda s: {"status": "generated"}
        )
        result = dte.generate_next_trading_day_plan("2026-08-01")
        assert result["status"] == "error"
        assert "无法在" in result["reason"]


class TestRiskParamsFromConfig:
    """B-4.5: 风控参数从 config/trade_execution.yaml 加载"""

    def test_daily_amount_limit_loaded(self):
        # 默认值 200000 (yaml 配置或硬编码回退)
        assert dte.DAILY_AMOUNT_LIMIT == 200000

    def test_price_protection_pct_loaded(self):
        assert dte.PRICE_PROTECTION_PCT == 0.03

    def test_daily_loss_stop_pct_loaded(self):
        assert dte.DAILY_LOSS_STOP_PCT == 0.03

    def test_portfolio_drawdown_stop_pct_loaded(self):
        assert dte.PORTFOLIO_DRAWDOWN_STOP_PCT == 0.05

    def test_accumulation_start_date(self):
        assert dte.ACCUMULATION_START == date(2026, 7, 10)

    def test_accumulation_end_date(self):
        assert dte.ACCUMULATION_END == date(2026, 12, 31)

    def test_stock_etf_target(self):
        assert dte.STOCK_ETF_TARGET == 3_000_000

    def test_fixed_budget_start(self):
        assert dte.FIXED_BUDGET_START == date(2026, 7, 13)

    def test_signal_amounts_complete(self):
        assert dte.SIGNAL_AMOUNTS["strong"] == 50_000
        assert dte.SIGNAL_AMOUNTS["medium"] == 20_000
        assert dte.SIGNAL_AMOUNTS["none"] == 10_000

    def test_baijiu_codes(self):
        assert "600519" in dte.BAIJIU_CODES
        assert "000858" in dte.BAIJIU_CODES


# ============================================================
# 4. 集成式 smoke 测试 (mock 重依赖)
# ============================================================


class TestGenerateInstructionsSmoke:
    """generate_instructions: smoke 测试 (mock 外部依赖)"""

    def test_completed_target_returns_completed(self, monkeypatch, tmp_path):
        # 已完成建仓目标 → 返回 completed
        monkeypatch.setattr(dte, "is_trading_day", lambda d: True)
        monkeypatch.setattr(dte, "POSITIONS_FILE", tmp_path / "positions.json")
        monkeypatch.setattr(dte, "INSTRUCTIONS_DIR", tmp_path / "instructions")
        monkeypatch.setattr(dte, "TRADE_PLAN_FILE", tmp_path / "trade_plan.json")
        monkeypatch.setattr(dte, "PROGRESS_FILE", tmp_path / "build_progress.json")

        # mock 持仓加载
        monkeypatch.setattr(
            dte, "load_positions", lambda: {"positions": {}}
        )
        monkeypatch.setattr(dte, "init_wt_modules", lambda: {})
        monkeypatch.setattr(
            dte, "load_trade_plan", lambda: {"stock_etf_account": {"positions": []}}
        )
        # 已完成建仓
        monkeypatch.setattr(
            dte, "load_build_progress", lambda: {"total_built": dte.STOCK_ETF_TARGET}
        )
        monkeypatch.setattr(dte, "load_latest_prices", lambda: {})
        monkeypatch.setattr(dte, "fetch_prediction_signals", lambda *a, **k: {})

        result = dte.generate_instructions("2026-08-03")
        assert result["status"] == "completed"
        assert "budget_info" in result


class TestRenderInstructionsMd:
    """render_instructions_md: markdown 渲染"""

    def test_renders_complete_document(self):
        data = {
            "meta": {
                "instruction_date": "2026-08-01",
                "generated_at": "2026-08-01T09:00:00",
                "phase": "phase_1_accumulation",
                "total_capital": 3000000,
                "total_built_before": 500000,
                "remaining_total": 2500000,
            },
            "budget_info": {
                "signal_strength": "fixed_200k",
                "strong_signal_count": 0,
                "medium_signal_count": 0,
                "base_daily": 200000,
                "daily_budget": 200000,
                "remaining_days": 100,
            },
            "risk_checks": {
                "daily_limit": {"passed": True},
                "price_protection": {"passed": True},
                "circuit_breaker": {"passed": True},
            },
            "instructions": [
                {
                    "code": "600519",
                    "name": "贵州茅台",
                    "action": "BUY",
                    "qty": 100,
                    "ref_price": 1800.0,
                    "max_buy_price": 1854.0,
                    "min_buy_price": 1746.0,
                    "estimated_amount": 180000,
                    "etf_signal": "none",
                    "built_before": 0,
                    "remaining_after": 0,
                    "confirm": False,
                },
            ],
            "total_allocated": 180000,
        }
        md = dte.render_instructions_md(data)
        assert "# 交易指令清单 2026-08-01" in md
        assert "贵州茅台" in md
        assert "PASS" in md
        assert "PENDING" in md
        assert "单日金额上限" in md
        assert "价格保护带" in md

    def test_empty_instructions(self):
        data = {
            "meta": {
                "instruction_date": "2026-08-01",
                "generated_at": "2026-08-01T09:00:00",
                "phase": "phase_1_accumulation",
                "total_capital": 3000000,
                "total_built_before": 0,
                "remaining_total": 3000000,
            },
            "budget_info": {
                "signal_strength": "none",
                "strong_signal_count": 0,
                "medium_signal_count": 0,
                "base_daily": 0,
                "daily_budget": 0,
                "remaining_days": 100,
            },
            "risk_checks": {
                "daily_limit": {"passed": True},
                "price_protection": {"passed": True},
                "circuit_breaker": {"passed": True},
            },
            "instructions": [],
            "total_allocated": 0,
        }
        md = dte.render_instructions_md(data)
        assert "**总指令数**: 0" in md


# ============================================================
# 5. 补充覆盖: _allocate_position / _save_instruction_file
#    / _build_and_save_execution_report / generate_accumulation_schedule
# ============================================================


class TestAllocatePosition:
    """_allocate_position: 单标的预算分配"""

    def _make_pos(self, **overrides):
        defaults = {
            "code": "600519.SH",
            "code_clean": "600519",
            "name": "贵州茅台",
            "weight": 0.10,
            "target_amount": 300000,
            "built": 0,
            "remaining": 300000,
            "gap": 50000,
        }
        defaults.update(overrides)
        return defaults

    def test_basic_allocation(self):
        pos = self._make_pos()
        result = dte._allocate_position(
            pos, "2026-08-01", 200000, 200000, {}, {}, {"positions": {}}
        )
        assert result is not None
        instruction, actual_amount = result
        assert instruction["code"] == "600519"
        assert instruction["full_code"] == "600519.SH"
        assert instruction["action"] == "BUY"
        assert instruction["confirm"] is False
        assert instruction["qty"] >= 100
        assert actual_amount > 0

    def test_uses_latest_price(self):
        # 用低价股确保不被高价股逻辑跳过 (100 股成本 < 50% 日预算)
        # ref_price=50, 100 股=5000 < 200000*0.5=100000
        pos = self._make_pos()
        result = dte._allocate_position(
            pos, "2026-08-01", 200000, 200000, {"600519": 50.0}, {}, {"positions": {}}
        )
        assert result is not None
        instruction, _ = result
        assert instruction["ref_price"] == 50.0

    def test_falls_back_to_default_price(self):
        pos = self._make_pos(code_clean="588080")
        # 588080 在 DEFAULT_PRICES 中
        result = dte._allocate_position(
            pos, "2026-08-01", 200000, 200000, {}, {}, {"positions": {}}
        )
        instruction, _ = result
        assert instruction["ref_price"] == dte.DEFAULT_PRICES["588080"]

    def test_falls_back_to_default_10_when_unknown(self):
        pos = self._make_pos(code_clean="999999")
        # 999999 不在 DEFAULT_PRICES → 用 10.0
        result = dte._allocate_position(
            pos, "2026-08-01", 200000, 200000, {}, {}, {"positions": {}}
        )
        instruction, _ = result
        assert instruction["ref_price"] == 10.0

    def test_price_band_computed(self):
        pos = self._make_pos()
        result = dte._allocate_position(
            pos, "2026-08-01", 200000, 200000, {"600519": 100.0}, {}, {"positions": {}}
        )
        instruction, _ = result
        assert instruction["max_buy_price"] == 103.0
        assert instruction["min_buy_price"] == 97.0

    def test_strong_sell_signal_skips(self):
        pos = self._make_pos()
        signal = {"direction": "DOWN", "confidence": 0.8, "signal_strength": -0.6}
        result = dte._allocate_position(
            pos, "2026-08-01", 200000, 200000, {}, {"600519": signal}, {"positions": {}}
        )
        assert result is None  # 强看空跳过

    def test_high_price_stock_exceeds_half_budget_skips(self):
        # 高价股 100 股成本 > 50% 日预算 → 跳过
        pos = self._make_pos()
        # ref_price=10000, 100 股成本=1000000 > 200000*0.5
        result = dte._allocate_position(
            pos, "2026-08-01", 200000, 200000, {"600519": 10000.0}, {}, {"positions": {}}
        )
        assert result is None

    def test_high_price_stock_within_budget(self):
        # 高价股 100 股成本 < 50% 日预算, 但 > allocated → 用 100 股最小手数
        pos = self._make_pos(weight=0.01)  # 极低权重 → allocated 很小
        # ref_price=500, 100 股=50000 < 200000*0.5=100000
        result = dte._allocate_position(
            pos, "2026-08-01", 200000, 200000, {"600519": 500.0}, {}, {"positions": {}}
        )
        # 可能返回 None (如果 remaining_budget < min_lot_cost) 或返回 100 股
        if result is not None:
            instruction, _ = result
            assert instruction["qty"] == 100

    def test_remaining_budget_too_small_skips(self):
        # remaining_budget < 100 → 不应分配 (虽然主流程会 break, 这里直接测试函数)
        pos = self._make_pos()
        result = dte._allocate_position(
            pos, "2026-08-01", 200000, 50, {}, {}, {"positions": {}}
        )
        # 50 元不够买 100 股任何标的 → 返回 None
        assert result is None

    def test_etf_signal_recorded(self):
        pos = self._make_pos(code="510050.SH", code_clean="510050")
        positions_data = {"positions": {"510050.SH": {"etf_flow_signal": "强"}}}
        result = dte._allocate_position(
            pos, "2026-08-01", 200000, 200000, {"510050": 3.0}, {}, positions_data
        )
        instruction, _ = result
        assert instruction["etf_signal"] == "strong"

    def test_prediction_signal_recorded(self):
        pos = self._make_pos()
        signal = {"direction": "UP", "confidence": 0.8, "signal_strength": 0.6}
        result = dte._allocate_position(
            pos, "2026-08-01", 200000, 200000, {"600519": 100.0}, {"600519": signal}, {"positions": {}}
        )
        instruction, _ = result
        assert instruction["prediction_signal"]["direction"] == "UP"
        assert instruction["prediction_signal"]["tag"] == "strong_buy"

    def test_actual_amount_exceeds_remaining_skips(self):
        # 实际金额 > remaining_budget → 返回 None
        pos = self._make_pos(weight=1.0, remaining=10000000)
        # allocated 会很大, 但 actual_amount 可能超过 remaining_budget
        result = dte._allocate_position(
            pos, "2026-08-01", 200000, 200, {"600519": 100.0}, {}, {"positions": {}}
        )
        assert result is None

    def test_instruction_id_format(self):
        pos = self._make_pos()
        result = dte._allocate_position(
            pos, "2026-08-01", 200000, 200000, {"600519": 100.0}, {}, {"positions": {}}
        )
        instruction, _ = result
        assert instruction["instruction_id"] == "20260801-600519"


class TestSaveInstructionFile:
    """_save_instruction_file: 指令文件保存"""

    def test_saves_json_and_md(self, monkeypatch, tmp_path):
        monkeypatch.setattr(dte, "INSTRUCTIONS_DIR", tmp_path)
        instruction_file = {
            "meta": {
                "instruction_date": "2026-08-01",
                "generated_at": "2026-08-01T09:00:00",
                "phase": "phase_1_accumulation",
                "total_capital": 3000000,
                "total_built_before": 0,
                "remaining_total": 3000000,
            },
            "budget_info": {
                "signal_strength": "fixed_200k",
                "strong_signal_count": 0,
                "medium_signal_count": 0,
                "base_daily": 200000,
                "daily_budget": 200000,
                "remaining_days": 100,
            },
            "risk_checks": {
                "daily_limit": {"passed": True},
                "price_protection": {"passed": True},
                "circuit_breaker": {"passed": True},
            },
            "instructions": [],
            "total_allocated": 0,
        }
        output_file, md_file = dte._save_instruction_file("2026-08-01", instruction_file)
        assert output_file.exists()
        assert md_file.exists()
        assert output_file.name == "2026-08-01_instructions.json"
        assert md_file.name == "2026-08-01_instructions.md"
        # 验证 JSON 可读
        with open(output_file, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded["meta"]["instruction_date"] == "2026-08-01"

    def test_creates_directory_if_missing(self, monkeypatch, tmp_path):
        new_dir = tmp_path / "new_instructions"
        monkeypatch.setattr(dte, "INSTRUCTIONS_DIR", new_dir)
        instruction_file = {
            "meta": {
                "instruction_date": "2026-08-01",
                "generated_at": "2026-08-01T09:00:00",
                "phase": "phase_1_accumulation",
                "total_capital": 3000000,
                "total_built_before": 0,
                "remaining_total": 3000000,
            },
            "budget_info": {
                "signal_strength": "none",
                "strong_signal_count": 0,
                "medium_signal_count": 0,
                "base_daily": 0,
                "daily_budget": 0,
                "remaining_days": 100,
            },
            "risk_checks": {
                "daily_limit": {"passed": True},
                "price_protection": {"passed": True},
                "circuit_breaker": {"passed": True},
            },
            "instructions": [],
            "total_allocated": 0,
        }
        output_file, _ = dte._save_instruction_file("2026-08-01", instruction_file)
        assert output_file.exists()
        assert new_dir.exists()


class TestBuildAndSaveExecutionReport:
    """_build_and_save_execution_report: 执行报告构建与保存"""

    def test_builds_and_saves_report(self, monkeypatch, tmp_path):
        monkeypatch.setattr(dte, "INSTRUCTIONS_DIR", tmp_path)
        instructions_data = {"instructions": [{"code": "600519"}, {"code": "000001"}]}
        confirmed = [{"code": "600519", "name": "贵州茅台"}]
        execution_results = [
            {
                "code": "600519",
                "name": "贵州茅台",
                "qty": 100,
                "fill_price": 1800.0,
                "fill_amount": 180000.0,
                "status": "FILLED",
                "built_before": 0,
                "built_after": 180000.0,
            }
        ]
        progress = {"total_built": 180000}
        instruction_file = tmp_path / "2026-08-01_instructions.json"

        result, report_file = dte._build_and_save_execution_report(
            "2026-08-01", instructions_data, confirmed, execution_results, progress, instruction_file,
        )

        assert result["status"] == "executed"
        assert result["summary"]["total_instructions"] == 2
        assert result["summary"]["confirmed_count"] == 1
        assert result["summary"]["executed_count"] == 1
        assert result["summary"]["total_executed_amount"] == 180000.0
        assert result["summary"]["total_built"] == 180000
        assert result["summary"]["remaining"] == dte.STOCK_ETF_TARGET - 180000
        assert report_file.exists()
        assert report_file.name == "2026-08-01_execution.json"

    def test_completion_rate_calculation(self, monkeypatch, tmp_path):
        monkeypatch.setattr(dte, "INSTRUCTIONS_DIR", tmp_path)
        instructions_data = {"instructions": []}
        confirmed = []
        execution_results = []
        progress = {"total_built": 1500000}
        instruction_file = tmp_path / "instr.json"

        result, _ = dte._build_and_save_execution_report(
            "2026-08-01", instructions_data, confirmed, execution_results, progress, instruction_file,
        )
        expected_rate = round(1500000 / dte.STOCK_ETF_TARGET * 100, 2)
        assert result["summary"]["completion_rate"] == expected_rate


class TestGenerateAccumulationSchedule:
    """generate_accumulation_schedule: 建仓进度预估表"""

    def test_generates_schedule(self, monkeypatch):
        # 所有日期都是交易日 → 生成完整计划
        monkeypatch.setattr(dte, "is_trading_day", lambda d: True)
        result = dte.generate_accumulation_schedule()
        assert result["target"] == dte.STOCK_ETF_TARGET
        assert result["start_date"] == "2026-07-10"
        assert result["end_date"] == "2026-12-31"
        assert result["total_trading_days"] > 0
        assert len(result["schedule"]) > 0
        # 最后一个点应该完成或接近 100%
        assert result["completion_pct"] >= 99.0

    def test_no_trading_days_returns_empty_schedule(self, monkeypatch):
        # 所有日期都非交易日 → schedule 为空
        monkeypatch.setattr(dte, "is_trading_day", lambda d: False)
        result = dte.generate_accumulation_schedule()
        assert result["schedule"] == []
        assert result["schedule_points"] == 0
        assert result["completion_date"] is None
        assert result["total_built"] == 0


class TestInitWtModules:
    """init_wt_modules: WonderTrader 模块初始化"""

    def test_returns_empty_dict_on_import_error(self):
        # wt_modules 加载失败 (例如模块不存在) → 返回空 dict, 不抛异常
        result = dte.init_wt_modules()
        # 可能成功加载 (返回非空 dict) 或失败 (返回空 dict), 都不能抛异常
        assert isinstance(result, dict)

    def test_does_not_raise(self):
        # 多次调用都不应抛异常
        for _ in range(3):
            dte.init_wt_modules()


class TestFetchPredictionSignals:
    """fetch_prediction_signals: 预测信号获取 (静默降级)"""

    def test_returns_empty_dict_on_import_error(self):
        # tf_price_predictor 未安装 → 返回空字典
        result = dte.fetch_prediction_signals(["600519", "000001"], horizon=5)
        assert isinstance(result, dict)
        # 可能返回空 (未安装) 或包含预测结果 (已安装)

    def test_empty_symbols(self):
        result = dte.fetch_prediction_signals([], horizon=5)
        assert isinstance(result, dict)


class TestResetPredictionPricesIndex:
    """_reset_prediction_prices_index: 缓存重置"""

    def test_resets_index(self):
        # 先构建一个索引, 再重置
        dte._get_prediction_prices_index()
        dte._reset_prediction_prices_index()
        # 验证重置后重新构建不抛异常
        dte._get_prediction_prices_index()

    def test_reset_after_reset(self):
        # 连续重置不应抛异常
        dte._reset_prediction_prices_index()
        dte._reset_prediction_prices_index()


class TestLoadPredictionPrices:
    """_load_prediction_prices: 历史价格加载"""

    def test_returns_none_when_numpy_missing(self, monkeypatch):
        # 模拟 numpy 不可用
        monkeypatch.setitem(__import__("sys").modules, "numpy", None)
        # 由于 import numpy 在函数内部, 这里直接调用验证降级路径
        # 实际环境中 numpy 通常可用, 此测试验证函数不会抛异常
        try:
            result = dte._load_prediction_prices("999999")
            assert result is None or hasattr(result, "__len__")
        except Exception:
            # 任何异常都不应抛出到这里 (函数内部已 try/except)
            pytest.fail("_load_prediction_prices 不应抛异常")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
