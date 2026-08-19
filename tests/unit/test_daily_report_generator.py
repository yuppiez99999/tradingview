"""T5.4 单元测试 — utils/reporting/daily_report_generator.py + report_sections.py.

测试覆盖:
  1. 常量定义完整性
  2. 异常体系可抛可捕
  3. 数据类 (ReportInput / ReportResult) 字段与序列化
  4. 纯函数 (report_sections.py):
     - build_report_header
     - build_phase_execution_summary
     - render_phase_summary
     - render_pnl_attribution
     - render_barra_decomposition
     - render_eod_guard_chain
     - build_report_summary
     - write_report_with_retry
     - save_state_json
  5. 主类 (DailyReportGenerator):
     - 初始化与配置加载
     - Feature Flag 透传 (HC-1)
     - 完整报告生成
     - 空输入处理
     - 异常处理
  6. 便捷函数
  7. 边界条件
  8. HC 合规 (HC-1 / HC-2 / HC-5)
  9. 性能 (<30s)

设计原则:
  - 纯函数独立测试, 不依赖 Feature Flag
  - 主类通过 mock FeatureFlag 控制
  - 文件写入使用 tmp_path fixture
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.reporting.daily_report_generator import (  # noqa: E402
    DEFAULT_CONFIG_NAME,
    DEFAULT_JSON_TEMPLATE,
    DEFAULT_MD_TEMPLATE,
    DEFAULT_REPORT_DIR,
    FLAG_NAME,
    STATUS_EMPTY_INPUT,
    STATUS_ERROR,
    STATUS_FEATURE_FLAG_DISABLED,
    STATUS_OK,
    STATUS_PARTIAL,
    DailyReportGenerator,
    DailyReportGeneratorError,
    FeatureFlagError,
    ReportInput,
    ReportResult,
    ReportWriteError,
    create_default_generator,
    generate_daily_report,
    is_daily_report_generator_enabled,
)
from utils.reporting.report_sections import (  # noqa: E402
    ALLOW_DEGRADE_PHASES,
    EMPTY_SECTION_PLACEHOLDER,
    NA_PLACEHOLDER,
    PHASE_NAMES,
    REPORT_SEPARATOR,
    build_phase_execution_summary,
    build_report_header,
    build_report_summary,
    render_barra_decomposition,
    render_eod_guard_chain,
    render_phase_summary,
    render_pnl_attribution,
    save_state_json,
    write_report_with_retry,
)

# ============================================================
# 1. 常量定义测试
# ============================================================

class TestConstants:
    """常量定义完整性测试."""

    def test_flag_name(self):
        """Feature Flag 名称正确."""
        assert FLAG_NAME == "USE_DAILY_REPORT_GENERATOR"

    def test_config_name(self):
        """ConfigManager 配置名正确."""
        assert DEFAULT_CONFIG_NAME == "daily_report_generator"

    def test_default_report_dir(self):
        """默认报告目录."""
        assert "reports" in DEFAULT_REPORT_DIR

    def test_md_template(self):
        """Markdown 文件名模板."""
        assert "{date}" in DEFAULT_MD_TEMPLATE

    def test_json_template(self):
        """JSON 文件名模板."""
        assert "{date}" in DEFAULT_JSON_TEMPLATE

    def test_status_codes_distinct(self):
        """状态码互不相同."""
        codes = {STATUS_OK, STATUS_FEATURE_FLAG_DISABLED, STATUS_EMPTY_INPUT, STATUS_ERROR, STATUS_PARTIAL}
        assert len(codes) == 5

    def test_phase_names_contains_key_phases(self):
        """阶段名称映射包含关键阶段."""
        for key in ["check", "market", "risk", "hedge", "signal", "execute", "report"]:
            assert key in PHASE_NAMES

    def test_allow_degrade_phases(self):
        """允许降级的阶段集合."""
        assert "calibrate" in ALLOW_DEGRADE_PHASES
        assert "autolearn" in ALLOW_DEGRADE_PHASES
        assert "factor_kill_switch" in ALLOW_DEGRADE_PHASES

    def test_na_placeholder(self):
        """占位符常量."""
        assert NA_PLACEHOLDER == "[N/A]"
        assert EMPTY_SECTION_PLACEHOLDER == "_(暂无数据)_"


# ============================================================
# 2. 异常体系测试
# ============================================================

class TestExceptions:
    """异常体系测试."""

    def test_base_exception_raisable(self):
        """基础异常可抛可捕."""
        with pytest.raises(DailyReportGeneratorError):
            raise DailyReportGeneratorError("test")

    def test_report_write_inherits_base(self):
        """ReportWriteError 继承基础异常."""
        with pytest.raises(DailyReportGeneratorError):
            raise ReportWriteError("write failed")

    def test_feature_flag_inherits_base(self):
        """FeatureFlagError 继承基础异常."""
        with pytest.raises(DailyReportGeneratorError):
            raise FeatureFlagError("flag error")

    def test_exception_with_reason_and_cause(self):
        """异常含 reason 和 cause 字段."""
        cause = ValueError("inner")
        exc = DailyReportGeneratorError("msg", reason="test_reason", cause=cause)
        assert exc.reason == "test_reason"
        assert exc.cause is cause


# ============================================================
# 3. 数据类测试
# ============================================================

class TestDataClasses:
    """数据类字段与序列化测试."""

    def test_report_input_default_empty(self):
        """ReportInput 默认全空."""
        inp = ReportInput()
        assert inp.trade_date == ""
        assert inp.capital == 0.0
        assert inp.phases_state == {}
        assert inp.pnl_attribution_result is None
        assert inp.barra_result is None
        assert inp.guard_results is None
        assert inp.is_empty() is True

    def test_report_input_with_data(self):
        """ReportInput 含数据."""
        inp = ReportInput(
            trade_date="2026-07-27",
            capital=5000000.0,
            phases_state={"check": {"status": "ok"}},
            pnl_attribution_result={"total_pnl": 1000.0},
        )
        assert inp.trade_date == "2026-07-27"
        assert inp.capital == 5000000.0
        assert inp.is_empty() is False

    def test_report_input_partial_empty(self):
        """ReportInput 部分空."""
        inp = ReportInput(trade_date="2026-07-27")
        assert inp.is_empty() is True  # phases_state 为空

    def test_report_result_default_values(self):
        """ReportResult 默认值."""
        r = ReportResult()
        assert r.report_path is None
        assert r.state_path is None
        assert r.markdown_content == ""
        assert r.lines == []
        assert r.status == STATUS_OK
        assert r.feature_flag_name == FLAG_NAME

    def test_report_result_to_dict(self):
        """ReportResult 序列化为字典."""
        r = ReportResult(
            status=STATUS_OK,
            reason="ok",
            generation_time_ms=15.5,
            generated_at="2026-07-27T10:00:00",
        )
        d = r.to_dict()
        assert d["status"] == STATUS_OK
        assert d["reason"] == "ok"
        assert d["generation_time_ms"] == 15.5
        assert d["generated_at"] == "2026-07-27T10:00:00"
        assert d["feature_flag_name"] == FLAG_NAME
        assert d["lines_count"] == 0
        assert d["markdown_length"] == 0


# ============================================================
# 4. 纯函数测试: build_report_header
# ============================================================

class TestBuildReportHeader:
    """报告头部构建测试."""

    def test_basic_header(self):
        """基本头部构建."""
        lines = build_report_header(trade_date="2026-07-27", capital=5000000.0)
        assert len(lines) > 0
        assert any("2026-07-27" in line for line in lines)
        assert any("5,000,000.00" in line for line in lines)

    def test_mode_tags(self):
        """执行模式标注."""
        # 实盘
        lines = build_report_header("2026-07-27", live_mode=True)
        assert any("实盘" in line for line in lines)
        # 模拟盘
        lines = build_report_header("2026-07-27", sim_mode=True)
        assert any("模拟" in line for line in lines)
        # 干跑
        lines = build_report_header("2026-07-27", dry_run=True)
        assert any("干跑" in line for line in lines)
        # 默认
        lines = build_report_header("2026-07-27")
        assert any("默认" in line for line in lines)

    def test_custom_generated_at(self):
        """自定义生成时间."""
        lines = build_report_header("2026-07-27", generated_at="2026-07-27 10:00:00")
        assert any("2026-07-27 10:00:00" in line for line in lines)

    def test_contains_separator(self):
        """包含分隔线."""
        lines = build_report_header("2026-07-27")
        assert REPORT_SEPARATOR in lines


# ============================================================
# 5. 纯函数测试: build_phase_execution_summary
# ============================================================

class TestBuildPhaseExecutionSummary:
    """阶段执行摘要测试."""

    def test_empty_phases_state(self):
        """空状态."""
        lines = build_phase_execution_summary({})
        assert any("阶段执行摘要" in line for line in lines)
        assert any(NA_PLACEHOLDER in line for line in lines)

    def test_with_ok_status(self):
        """OK 状态."""
        phases = {"check": {"status": "ok", "duration_ms": 100.0}}
        lines = build_phase_execution_summary(phases)
        assert any("✅" in line for line in lines)
        assert any("系统自检" in line for line in lines)

    def test_with_failed_status(self):
        """失败状态."""
        phases = {"check": {"status": "error", "duration_ms": 100.0, "reason": "NTP 失败"}}
        lines = build_phase_execution_summary(phases)
        assert any("❌" in line for line in lines)

    def test_with_degraded_status(self):
        """降级状态."""
        phases = {"calibrate": {"status": "skipped", "duration_ms": 0.0}}
        lines = build_phase_execution_summary(phases)
        # calibrate 是允许降级的
        assert any("calibrate" not in line or "⚠️" in line or "SKIP" in line for line in lines)

    def test_table_header(self):
        """表格头."""
        lines = build_phase_execution_summary({"check": {"status": "ok"}})
        assert any("阶段" in line and "状态" in line for line in lines)


# ============================================================
# 6. 纯函数测试: render_phase_summary
# ============================================================

class TestRenderPhaseSummary:
    """各阶段详情渲染测试."""

    def test_empty_state(self):
        """空状态."""
        lines = render_phase_summary({})
        assert any(EMPTY_SECTION_PLACEHOLDER in line for line in lines)

    def test_with_check_phase(self):
        """系统自检阶段."""
        phases = {"check": {"ntp_sync": "ok", "connector": "ready", "risk_manager": "ok"}}
        lines = render_phase_summary(phases)
        assert any("Phase 1: 系统自检" in line for line in lines)
        assert any("NTP" in line for line in lines)

    def test_with_market_phase(self):
        """市场状态阶段."""
        phases = {"market": {"vix": 18.5, "circuit_level": 1}}
        lines = render_phase_summary(phases)
        assert any("Phase 2: 市场状态" in line for line in lines)
        assert any("18.5" in line for line in lines)

    def test_with_risk_phase(self):
        """风险预算阶段."""
        phases = {"risk": {"total_risk_budget": 0.05, "kelly_fraction": 0.3}}
        lines = render_phase_summary(phases)
        assert any("Phase 3: 风险预算" in line for line in lines)

    def test_with_hedge_phase(self):
        """对冲评估阶段."""
        phases = {"hedge": {"beta_hedge": "done", "vol_hedge": "done"}}
        lines = render_phase_summary(phases)
        assert any("Phase 4: 对冲评估" in line for line in lines)

    def test_with_signal_phase(self):
        """交易信号阶段."""
        phases = {
            "signal": {
                "signals": [
                    {"symbol": "000001.SZ", "direction": "BUY", "weight": 0.1},
                    {"symbol": "600000.SH", "direction": "SELL", "weight": 0.05},
                ]
            }
        }
        lines = render_phase_summary(phases)
        assert any("Phase 5: 交易信号" in line for line in lines)
        assert any("000001.SZ" in line for line in lines)

    def test_with_execute_phase(self):
        """执行记录阶段."""
        phases = {
            "execute": {
                "fills": [
                    {"notional": 10000.0},
                    {"notional": 20000.0},
                ]
            }
        }
        lines = render_phase_summary(phases)
        assert any("Phase 6: 智能执行" in line for line in lines)
        assert any("30,000.00" in line for line in lines)


# ============================================================
# 7. 纯函数测试: render_pnl_attribution
# ============================================================

class TestRenderPnLAttribution:
    """P&L 归因渲染测试."""

    def test_none_input(self):
        """None 输入."""
        lines = render_pnl_attribution(None)
        assert any(NA_PLACEHOLDER in line for line in lines)

    def test_empty_dict(self):
        """空字典."""
        lines = render_pnl_attribution({})
        assert any(NA_PLACEHOLDER in line for line in lines)

    def test_basic_attribution(self):
        """基本归因."""
        result = {
            "alpha_pnl": 500.0,
            "execution_pnl": -100.0,
            "risk_pnl": 200.0,
            "total_pnl": 600.0,
        }
        lines = render_pnl_attribution(result)
        assert any("Alpha 贡献" in line for line in lines)
        assert any("+500.00" in line for line in lines)
        assert any("-100.00" in line for line in lines)
        assert any("总 PnL" in line for line in lines)

    def test_with_details(self):
        """带明细."""
        result = {
            "alpha_pnl": 500.0,
            "total_pnl": 600.0,
            "details": {"momentum": 300.0, "value": 200.0},
        }
        lines = render_pnl_attribution(result)
        assert any("归因明细" in line for line in lines)
        assert any("momentum" in line for line in lines)
        assert any("value" in line for line in lines)

    def test_zero_total_pnl(self):
        """零总 PnL (避免除零)."""
        result = {"alpha_pnl": 0.0, "total_pnl": 0.0, "details": {"x": 0.0}}
        lines = render_pnl_attribution(result)
        # 不抛异常即可
        assert len(lines) > 0


# ============================================================
# 8. 纯函数测试: render_barra_decomposition
# ============================================================

class TestRenderBarraDecomposition:
    """Barra 风险分解渲染测试."""

    def test_none_input(self):
        """None 输入."""
        lines = render_barra_decomposition(None)
        assert any(NA_PLACEHOLDER in line for line in lines)

    def test_basic_decomposition(self):
        """基本分解."""
        result = {
            "factor_exposures": {"Size": 0.5, "Beta": 1.2},
            "specific_risk": 0.03,
            "total_risk": 0.05,
        }
        lines = render_barra_decomposition(result)
        assert any("总风险" in line for line in lines)
        assert any("0.0500" in line for line in lines)
        assert any("因子风险" in line for line in lines)

    def test_with_factor_exposures_table(self):
        """因子暴露表格."""
        result = {
            "factor_exposures": {"Size": 0.5, "Momentum": -0.3},
            "specific_risk": 0.0,
            "total_risk": 0.0,
        }
        lines = render_barra_decomposition(result)
        assert any("因子暴露" in line for line in lines)
        assert any("Size" in line for line in lines)
        assert any("Momentum" in line for line in lines)

    def test_negative_specific_risk(self):
        """负特异性风险 (异常值)."""
        result = {
            "factor_exposures": {},
            "specific_risk": -0.01,
            "total_risk": 0.05,
        }
        lines = render_barra_decomposition(result)
        # 不抛异常即可
        assert len(lines) > 0


# ============================================================
# 9. 纯函数测试: render_eod_guard_chain
# ============================================================

class TestRenderEodGuardChain:
    """EOD 七 Guard 风控链渲染测试."""

    def test_none_input(self):
        """None 输入."""
        lines = render_eod_guard_chain(None)
        assert any(NA_PLACEHOLDER in line for line in lines)

    def test_basic_guards(self):
        """基本 Guard 列表."""
        result = {
            "overall_status": "PASS",
            "guards": [
                {"name": "KillSwitch", "status": "ok", "result": "no_trigger"},
                {"name": "CircuitBreaker", "status": "ok", "result": "level_1"},
            ],
        }
        lines = render_eod_guard_chain(result)
        assert any("EOD 七 Guard" in line for line in lines)
        assert any("KillSwitch" in line for line in lines)
        assert any("CircuitBreaker" in line for line in lines)

    def test_with_recommendations(self):
        """带建议."""
        result = {
            "overall_status": "WARN",
            "guards": [],
            "recommendations": ["降低仓位", "检查对冲"],
        }
        lines = render_eod_guard_chain(result)
        assert any("降低仓位" in line for line in lines)

    def test_empty_guards_list(self):
        """空 Guard 列表."""
        result = {"overall_status": "PASS", "guards": []}
        lines = render_eod_guard_chain(result)
        assert any("PASS" in line for line in lines)


# ============================================================
# 10. 纯函数测试: build_report_summary
# ============================================================

class TestBuildReportSummary:
    """报告总结段落测试."""

    def test_empty_state(self):
        """空状态."""
        lines = build_report_summary({})
        assert any("总结" in line for line in lines)

    def test_phase_stats(self):
        """阶段统计."""
        phases = {
            "check": {"status": "ok"},
            "market": {"status": "ok"},
            "execute": {"status": "error"},
        }
        lines = build_report_summary(phases)
        assert any("2/3 成功" in line for line in lines)
        assert any("1 失败" in line for line in lines)

    def test_with_pnl(self):
        """带 PnL."""
        phases = {"check": {"status": "ok"}}
        pnl = {"total_pnl": 1234.56}
        lines = build_report_summary(phases, pnl)
        assert any("1,234.56" in line for line in lines)

    def test_next_trading_day(self):
        """下一交易日提示."""
        lines = build_report_summary({})
        assert any("下一交易日" in line for line in lines)


# ============================================================
# 11. 纯函数测试: write_report_with_retry
# ============================================================

class TestWriteReportWithRetry:
    """报告写入测试."""

    def test_basic_write(self, tmp_path):
        """基本写入."""
        path = tmp_path / "report.md"
        result = write_report_with_retry(path, ["# Test", "", "content"])
        assert result == path
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "# Test" in content
        assert "content" in content

    def test_creates_parent_directory(self, tmp_path):
        """创建父目录."""
        path = tmp_path / "subdir" / "deep" / "report.md"
        write_report_with_retry(path, ["test"])
        assert path.exists()

    def test_retry_on_permission_error(self, tmp_path):
        """PermissionError 重试."""
        path = tmp_path / "report.md"
        call_count = {"n": 0}

        original_write_text = Path.write_text

        def mock_write_text(self, data, *args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise PermissionError("mock")
            return original_write_text(self, data, *args, **kwargs)

        with patch.object(Path, "write_text", mock_write_text):
            result = write_report_with_retry(
                path, ["test"], max_retries=3, retry_delay_seconds=0.01
            )
        assert result == path
        assert call_count["n"] == 2  # 第一次失败, 第二次成功

    def test_all_retries_failed(self, tmp_path):
        """全部重试失败."""
        path = tmp_path / "report.md"

        def always_fail(self, data, *args, **kwargs):
            raise PermissionError("always fail")

        with patch.object(Path, "write_text", always_fail), pytest.raises(OSError):
            write_report_with_retry(
                path, ["test"], max_retries=2, retry_delay_seconds=0.01
            )


# ============================================================
# 12. 纯函数测试: save_state_json
# ============================================================

class TestSaveStateJson:
    """状态 JSON 保存测试."""

    def test_basic_save(self, tmp_path):
        """基本保存."""
        path = tmp_path / "state.json"
        phases = {"check": {"status": "ok"}}
        result = save_state_json(path, phases)
        assert result == path
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "phases" in data
        assert data["phases"]["check"]["status"] == "ok"
        assert "generated_at" in data

    def test_with_extra_fields(self, tmp_path):
        """带额外字段."""
        path = tmp_path / "state.json"
        phases = {"check": {"status": "ok"}}
        save_state_json(path, phases, extra_fields={"trade_date": "2026-07-27"})
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["trade_date"] == "2026-07-27"

    def test_creates_parent_directory(self, tmp_path):
        """创建父目录."""
        path = tmp_path / "sub" / "state.json"
        save_state_json(path, {})
        assert path.exists()

    def test_serializable_with_non_serializable(self, tmp_path):
        """非可序列化对象 (default=str)."""
        path = tmp_path / "state.json"

        class Obj:
            def __str__(self):
                return "obj_str"

        phases = {"obj": Obj()}
        save_state_json(path, phases)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["phases"]["obj"] == "obj_str"


# ============================================================
# 13. 主类测试: DailyReportGenerator
# ============================================================

class TestDailyReportGenerator:
    """DailyReportGenerator 主类测试."""

    def test_init_with_explicit_config(self):
        """显式配置初始化."""
        gen = DailyReportGenerator(
            config={"settings": {"report_dir": "test_reports"}}
        )
        assert gen._config_source == "explicit_dict"
        assert "test_reports" in str(gen._report_dir)

    def test_init_default_config(self):
        """默认配置初始化."""
        gen = DailyReportGenerator()
        assert gen._feature_flag_name == FLAG_NAME
        # report_dir 来自配置文件或默认值
        assert gen._report_dir is not None

    def test_init_with_explicit_report_dir(self, tmp_path):
        """显式 report_dir."""
        gen = DailyReportGenerator(report_dir=tmp_path)
        assert gen._report_dir == tmp_path

    def test_feature_flag_disabled_returns_disabled_result(self):
        """Feature Flag 关闭返回降级结果."""
        gen = DailyReportGenerator(config={})
        with patch.object(gen, "_is_feature_flag_enabled", return_value=False):
            result = gen.generate(trade_date="2026-07-27")
        assert result.status == STATUS_FEATURE_FLAG_DISABLED
        assert FLAG_NAME in result.reason
        assert result.generation_time_ms >= 0

    def test_generate_empty_input(self):
        """空输入."""
        gen = DailyReportGenerator(config={})
        with patch.object(gen, "_is_feature_flag_enabled", return_value=True):
            result = gen.generate(trade_date="2026-07-27")
        assert result.status == STATUS_EMPTY_INPUT
        assert "空" in result.reason

    def test_generate_with_phases_state(self):
        """带阶段状态生成."""
        gen = DailyReportGenerator(config={})
        with patch.object(gen, "_is_feature_flag_enabled", return_value=True):
            result = gen.generate(
                trade_date="2026-07-27",
                capital=5000000.0,
                phases_state={"check": {"status": "ok", "duration_ms": 100.0}},
                save=False,
            )
        assert result.status == STATUS_OK
        assert len(result.lines) > 0
        assert "2026-07-27" in result.markdown_content
        assert "5,000,000.00" in result.markdown_content

    def test_generate_with_pnl_attribution(self):
        """带 PnL 归因."""
        gen = DailyReportGenerator(config={})
        with patch.object(gen, "_is_feature_flag_enabled", return_value=True):
            result = gen.generate(
                trade_date="2026-07-27",
                phases_state={"check": {"status": "ok"}},
                pnl_attribution_result={
                    "alpha_pnl": 500.0,
                    "execution_pnl": -100.0,
                    "risk_pnl": 200.0,
                    "total_pnl": 600.0,
                },
                save=False,
            )
        assert result.status == STATUS_OK
        assert "Alpha 贡献" in result.markdown_content
        assert "+500.00" in result.markdown_content

    def test_generate_with_barra_result(self):
        """带 Barra 分解."""
        gen = DailyReportGenerator(config={})
        with patch.object(gen, "_is_feature_flag_enabled", return_value=True):
            result = gen.generate(
                trade_date="2026-07-27",
                phases_state={"check": {"status": "ok"}},
                barra_result={
                    "factor_exposures": {"Size": 0.5},
                    "specific_risk": 0.03,
                    "total_risk": 0.05,
                },
                save=False,
            )
        assert result.status == STATUS_OK
        assert "Barra" in result.markdown_content
        assert "0.0500" in result.markdown_content

    def test_generate_with_guard_results(self):
        """带 EOD Guard 结果."""
        gen = DailyReportGenerator(config={})
        with patch.object(gen, "_is_feature_flag_enabled", return_value=True):
            result = gen.generate(
                trade_date="2026-07-27",
                phases_state={"check": {"status": "ok"}},
                guard_results={
                    "overall_status": "PASS",
                    "guards": [{"name": "KillSwitch", "status": "ok"}],
                },
                save=False,
            )
        assert result.status == STATUS_OK
        assert "EOD" in result.markdown_content
        assert "KillSwitch" in result.markdown_content

    def test_generate_with_all_inputs(self):
        """全部输入."""
        gen = DailyReportGenerator(config={})
        with patch.object(gen, "_is_feature_flag_enabled", return_value=True):
            result = gen.generate(
                trade_date="2026-07-27",
                capital=5000000.0,
                dry_run=False,
                sim_mode=True,
                phases_state={
                    "check": {"status": "ok", "duration_ms": 50.0},
                    "market": {"vix": 18.5, "circuit_level": 1},
                    "execute": {"fills": [{"notional": 10000.0}]},
                },
                pnl_attribution_result={"total_pnl": 1000.0, "alpha_pnl": 800.0, "execution_pnl": 200.0, "risk_pnl": 0.0},
                barra_result={"factor_exposures": {"Size": 0.5}, "specific_risk": 0.0, "total_risk": 0.05},
                guard_results={"overall_status": "PASS", "guards": []},
                save=False,
            )
        assert result.status == STATUS_OK
        assert "模拟" in result.markdown_content
        assert "10,000.00" in result.markdown_content

    def test_generate_with_save(self, tmp_path):
        """保存到文件."""
        gen = DailyReportGenerator(
            config={},
            report_dir=tmp_path,
        )
        with patch.object(gen, "_is_feature_flag_enabled", return_value=True):
            result = gen.generate(
                trade_date="2026-07-27",
                phases_state={"check": {"status": "ok"}},
                save=True,
            )
        assert result.status == STATUS_OK
        assert result.report_path is not None
        assert result.report_path.exists()
        assert result.state_path is not None
        assert result.state_path.exists()

    def test_generate_with_input_container(self):
        """使用 ReportInput 容器."""
        gen = DailyReportGenerator(config={})
        inp = ReportInput(
            trade_date="2026-07-27",
            capital=3000000.0,
            phases_state={"check": {"status": "ok"}},
        )
        with patch.object(gen, "_is_feature_flag_enabled", return_value=True):
            result = gen.generate(input_data=inp, save=False)
        assert result.status == STATUS_OK
        assert "3,000,000.00" in result.markdown_content

    def test_generate_exception_handled(self):
        """异常处理."""
        gen = DailyReportGenerator(config={})
        # mock 内部方法抛异常
        with patch.object(gen, "_is_feature_flag_enabled", return_value=True), patch.object(
            gen, "_generate_internal", side_effect=RuntimeError("mock error")
        ):
            result = gen.generate(
                trade_date="2026-07-27",
                phases_state={"check": {"status": "ok"}},
            )
        assert result.status == STATUS_ERROR
        assert "mock error" in result.reason

    def test_is_enabled_returns_bool(self):
        """is_enabled 返回布尔值."""
        gen = DailyReportGenerator(config={})
        assert isinstance(gen.is_enabled(), bool)


# ============================================================
# 14. 便捷函数测试
# ============================================================

class TestConvenienceFunctions:
    """便捷函数测试."""

    def test_is_daily_report_generator_enabled_returns_bool(self):
        """查询 Feature Flag 返回布尔值."""
        result = is_daily_report_generator_enabled()
        assert isinstance(result, bool)

    def test_create_default_generator(self):
        """创建默认生成器."""
        gen = create_default_generator()
        assert isinstance(gen, DailyReportGenerator)
        assert gen._feature_flag_name == FLAG_NAME

    def test_generate_daily_report_no_save(self):
        """便捷函数不保存."""
        with patch(
            "utils.reporting.daily_report_generator.DailyReportGenerator._is_feature_flag_enabled",
            return_value=True,
        ):
            result = generate_daily_report(
                trade_date="2026-07-27",
                phases_state={"check": {"status": "ok"}},
                save=False,
            )
        assert isinstance(result, ReportResult)


# ============================================================
# 15. 边界条件测试
# ============================================================

class TestEdgeCases:
    """边界条件测试."""

    def test_empty_phases_state_dict(self):
        """空 phases_state 字典."""
        gen = DailyReportGenerator(config={})
        with patch.object(gen, "_is_feature_flag_enabled", return_value=True):
            result = gen.generate(
                trade_date="2026-07-27",
                phases_state={},
                save=False,
            )
        # phases_state={} 视为空输入
        assert result.status == STATUS_EMPTY_INPUT

    def test_default_trade_date(self):
        """默认交易日期 (今天)."""
        gen = DailyReportGenerator(config={})
        with patch.object(gen, "_is_feature_flag_enabled", return_value=True):
            result = gen.generate(
                phases_state={"check": {"status": "ok"}},
                save=False,
            )
        # 不抛异常即可 (日期默认为今天)
        assert result.status in (STATUS_OK, STATUS_EMPTY_INPUT)

    def test_zero_capital(self):
        """零资金."""
        gen = DailyReportGenerator(config={})
        with patch.object(gen, "_is_feature_flag_enabled", return_value=True):
            result = gen.generate(
                trade_date="2026-07-27",
                capital=0.0,
                phases_state={"check": {"status": "ok"}},
                save=False,
            )
        assert result.status == STATUS_OK

    def test_all_modes_simultaneously(self):
        """所有模式同时启用 (异常但不应崩溃)."""
        gen = DailyReportGenerator(config={})
        with patch.object(gen, "_is_feature_flag_enabled", return_value=True):
            result = gen.generate(
                trade_date="2026-07-27",
                dry_run=True,
                sim_mode=True,
                live_mode=True,
                phases_state={"check": {"status": "ok"}},
                save=False,
            )
        # live_mode 优先级最高
        assert "实盘" in result.markdown_content

    def test_pnl_attribution_with_nan_values(self):
        """PnL 归因含 NaN (不抛异常)."""
        gen = DailyReportGenerator(config={})
        with patch.object(gen, "_is_feature_flag_enabled", return_value=True):
            result = gen.generate(
                trade_date="2026-07-27",
                phases_state={"check": {"status": "ok"}},
                pnl_attribution_result={"total_pnl": float("nan"), "alpha_pnl": 0.0},
                save=False,
            )
        # 不抛异常即可
        assert result.status == STATUS_OK

    def test_barra_with_zero_risk(self):
        """Barra 零风险."""
        gen = DailyReportGenerator(config={})
        with patch.object(gen, "_is_feature_flag_enabled", return_value=True):
            result = gen.generate(
                trade_date="2026-07-27",
                phases_state={"check": {"status": "ok"}},
                barra_result={"total_risk": 0.0, "specific_risk": 0.0, "factor_exposures": {}},
                save=False,
            )
        assert result.status == STATUS_OK


# ============================================================
# 16. HC 合规测试
# ============================================================

class TestHCCompliance:
    """HC 合规测试."""

    def test_hc1_feature_flag_disabled_returns_disabled_result(self):
        """HC-1: Feature Flag 关闭返回降级结果."""
        gen = DailyReportGenerator(config={})
        with patch.object(gen, "_is_feature_flag_enabled", return_value=False):
            result = gen.generate(trade_date="2026-07-27")
        assert result.status == STATUS_FEATURE_FLAG_DISABLED
        assert result.report_path is None  # 不写入文件
        assert result.state_path is None

    def test_hc1_feature_flag_name_correct(self):
        """HC-1: Feature Flag 名称正确."""
        assert FLAG_NAME == "USE_DAILY_REPORT_GENERATOR"

    def test_hc5_config_uses_config_manager(self):
        """HC-5: 配置走 ConfigManager 4 级优先级."""
        # 通过 _NAMED_CONFIGS 注册验证
        from utils.config_manager import _NAMED_CONFIGS
        assert "daily_report_generator" in _NAMED_CONFIGS
        assert _NAMED_CONFIGS["daily_report_generator"] == "daily_report_generator.yaml"

    def test_hc2_main_path_not_blocked_on_module_failure(self):
        """HC-2: 子模块失败不阻塞主路径."""
        gen = DailyReportGenerator(config={})
        # PnL/Barra/Guard 均为 None, 但 phases_state 有数据
        with patch.object(gen, "_is_feature_flag_enabled", return_value=True):
            result = gen.generate(
                trade_date="2026-07-27",
                phases_state={"check": {"status": "ok"}},
                pnl_attribution_result=None,
                barra_result=None,
                guard_results=None,
                save=False,
            )
        # 应该成功生成 (各段落降级为 [N/A])
        assert result.status == STATUS_OK
        assert NA_PLACEHOLDER in result.markdown_content


# ============================================================
# 17. 性能测试
# ============================================================

class TestPerformance:
    """性能测试."""

    def test_generation_time_under_threshold(self):
        """生成时间 <30s (验收标准)."""
        gen = DailyReportGenerator(config={})
        with patch.object(gen, "_is_feature_flag_enabled", return_value=True):
            start = time.perf_counter()
            result = gen.generate(
                trade_date="2026-07-27",
                phases_state={"check": {"status": "ok"}},
                save=False,
            )
            elapsed = time.perf_counter() - start
        assert elapsed < 30.0
        assert result.generation_time_ms < 30_000

    def test_generation_time_recorded(self):
        """报告记录生成耗时."""
        gen = DailyReportGenerator(config={})
        with patch.object(gen, "_is_feature_flag_enabled", return_value=True):
            result = gen.generate(
                trade_date="2026-07-27",
                phases_state={"check": {"status": "ok"}},
                save=False,
            )
        assert result.generation_time_ms > 0
        assert result.generation_time_ms < 1000  # 简单报告应该很快

    def test_generation_time_recorded_empty_input(self):
        """空输入也记录生成耗时."""
        gen = DailyReportGenerator(config={})
        with patch.object(gen, "_is_feature_flag_enabled", return_value=True):
            result = gen.generate(trade_date="2026-07-27")
        assert result.status == STATUS_EMPTY_INPUT
        assert result.generation_time_ms >= 0  # 至少记录了 (可能极小)

    def test_generation_time_recorded_disabled(self):
        """Feature Flag 关闭也记录生成耗时."""
        gen = DailyReportGenerator(config={})
        with patch.object(gen, "_is_feature_flag_enabled", return_value=False):
            result = gen.generate(trade_date="2026-07-27")
        assert result.status == STATUS_FEATURE_FLAG_DISABLED
        assert result.generation_time_ms >= 0


# ============================================================
# 18. 综合场景测试
# ============================================================

class TestIntegrationScenarios:
    """综合场景测试."""

    def test_full_pipeline_with_all_modules(self, tmp_path):
        """完整流水线 (所有模块)."""
        gen = DailyReportGenerator(
            config={},
            report_dir=tmp_path,
        )
        with patch.object(gen, "_is_feature_flag_enabled", return_value=True):
            result = gen.generate(
                trade_date="2026-07-27",
                capital=5000000.0,
                sim_mode=True,
                phases_state={
                    "check": {"status": "ok", "duration_ms": 50.0, "ntp_sync": "ok", "connector": "ready"},
                    "market": {"status": "ok", "vix": 18.5, "circuit_level": 1},
                    "risk": {"status": "ok", "total_risk_budget": 0.05, "kelly_fraction": 0.3},
                    "hedge": {"status": "ok", "beta_hedge": "done", "vol_hedge": "done"},
                    "signal": {"status": "ok", "signals": [{"symbol": "000001.SZ", "direction": "BUY"}]},
                    "execute": {"status": "ok", "fills": [{"notional": 10000.0}]},
                },
                pnl_attribution_result={
                    "alpha_pnl": 500.0, "execution_pnl": -100.0,
                    "risk_pnl": 200.0, "total_pnl": 600.0,
                    "details": {"momentum": 300.0, "value": 200.0},
                },
                barra_result={
                    "factor_exposures": {"Size": 0.5, "Beta": 1.2},
                    "specific_risk": 0.03, "total_risk": 0.05,
                },
                guard_results={
                    "overall_status": "PASS",
                    "guards": [{"name": "KillSwitch", "status": "ok"}],
                    "recommendations": ["保持仓位"],
                },
                save=True,
            )
        assert result.status == STATUS_OK
        assert result.report_path is not None
        assert result.report_path.exists()
        assert result.state_path is not None
        assert result.state_path.exists()
        # 验证 JSON 内容
        json_data = json.loads(result.state_path.read_text(encoding="utf-8"))
        assert json_data["trade_date"] == "2026-07-27"
        assert "phases" in json_data

    def test_module_independence(self):
        """模块独立性 (PnL 缺失不影响 Barra)."""
        gen = DailyReportGenerator(config={})
        with patch.object(gen, "_is_feature_flag_enabled", return_value=True):
            result = gen.generate(
                trade_date="2026-07-27",
                phases_state={"check": {"status": "ok"}},
                pnl_attribution_result=None,  # 缺失
                barra_result={"factor_exposures": {"Size": 0.5}, "total_risk": 0.05, "specific_risk": 0.0},
                guard_results=None,  # 缺失
                save=False,
            )
        assert result.status == STATUS_OK
        # PnL 段落有占位符, Barra 段落正常
        assert NA_PLACEHOLDER in result.markdown_content
        assert "Barra" in result.markdown_content

    def test_config_source_recorded(self):
        """配置来源记录."""
        gen = DailyReportGenerator(config={"settings": {}})
        assert gen._config_source == "explicit_dict"
        with patch.object(gen, "_is_feature_flag_enabled", return_value=True):
            result = gen.generate(
                trade_date="2026-07-27",
                phases_state={"check": {"status": "ok"}},
                save=False,
            )
        assert result.config_source == "explicit_dict"
