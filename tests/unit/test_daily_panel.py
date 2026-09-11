"""T5.3 单元测试 — utils/attribution/daily_panel.py.

测试覆盖:
  1. 常量定义完整性
  2. 异常体系可抛可捕
  3. 输入数据类 (BrinsonInput / FactorInput / TCAInput / DailyReportInput)
  4. 输出数据类 (ModuleStatus / DailyAttributionReport)
  5. TCA 聚合算法 (summary / pnl_list / fills_only)
  6. Markdown 生成
  7. DailyAttributionPanel 主类 (初始化 / Feature Flag / 三模块聚合)
  8. 持久化 (JSON + Markdown 双输出)
  9. 便捷函数
  10. 边界条件 (空输入 / 子模块降级 / 子模块异常)
  11. 状态评估 (ok / partial / all_degraded / error)
  12. 性能 (< 30s)

设计原则:
  - 不依赖网络: ConfigManager / Feature Flag 通过 mock 控制
  - 算法正确性优先: 用手工计算的期望值验证 TCA 聚合公式
  - 容错性: 子模块异常不阻塞整体报告生成
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.attribution.daily_panel import (  # noqa: E402
    DEFAULT_BPS_PRECISION,
    DEFAULT_CONFIG_NAME,
    DEFAULT_DECIMAL_PRECISION,
    DEFAULT_GENERATION_TIMEOUT,
    DEFAULT_JSON_TEMPLATE,
    DEFAULT_MARKDOWN_TEMPLATE,
    DEFAULT_PRIMARY_BENCHMARK,
    DEFAULT_REPORT_DIR,
    DEFAULT_RESIDUAL_TOLERANCE,
    DEFAULT_RETURN_PRECISION,
    DEFAULT_SECONDARY_BENCHMARK,
    # 常量
    FLAG_NAME,
    MODULE_STATUS_DEGRADED,
    MODULE_STATUS_EMPTY_INPUT,
    MODULE_STATUS_ERROR,
    MODULE_STATUS_FEATURE_FLAG_DISABLED,
    MODULE_STATUS_OK,
    MODULE_STATUS_SKIPPED,
    STATUS_ALL_DEGRADED,
    STATUS_EMPTY_INPUT,
    STATUS_ERROR,
    STATUS_FEATURE_FLAG_DISABLED,
    STATUS_OK,
    STATUS_PARTIAL,
    # 输入数据类
    BrinsonInput,
    # 主类与便捷函数
    DailyAttributionPanel,
    DailyAttributionReport,
    # 异常
    DailyPanelError,
    DailyReportInput,
    FactorInput,
    ModuleAggregationError,
    # 输出数据类
    ModuleStatus,
    PanelGenerationError,
    PersistenceError,
    TCAInput,
    create_default_panel,
    generate_daily_report,
    is_daily_panel_enabled,
)

# ============================================================
# 1. 常量定义测试
# ============================================================


class TestConstants:
    """常量定义完整性测试."""

    def test_flag_name(self):
        """Feature Flag 名称正确."""
        assert FLAG_NAME == "USE_DAILY_ATTRIBUTION_PANEL"

    def test_config_name(self):
        """ConfigManager 配置名正确."""
        assert DEFAULT_CONFIG_NAME == "daily_panel"

    def test_primary_benchmark(self):
        """默认主基准为沪深300ETF."""
        assert DEFAULT_PRIMARY_BENCHMARK == "510300.SH"

    def test_secondary_benchmark(self):
        """默认次基准为中证500ETF."""
        assert DEFAULT_SECONDARY_BENCHMARK == "510500.SH"

    def test_report_dir(self):
        """默认报告目录为 reports/attribution."""
        assert DEFAULT_REPORT_DIR == "reports/attribution"

    def test_json_template(self):
        """JSON 文件名模板含 {date} 占位符."""
        assert "{date}" in DEFAULT_JSON_TEMPLATE
        assert (
            DEFAULT_JSON_TEMPLATE.format(date="2026-07-27")
            == "daily_panel_2026-07-27.json"
        )

    def test_markdown_template(self):
        """Markdown 文件名模板含 {date} 占位符."""
        assert "{date}" in DEFAULT_MARKDOWN_TEMPLATE
        assert (
            DEFAULT_MARKDOWN_TEMPLATE.format(date="2026-07-27")
            == "daily_panel_2026-07-27.md"
        )

    def test_decimal_precision_positive(self):
        """数值精度为正."""
        assert DEFAULT_DECIMAL_PRECISION > 0
        assert DEFAULT_RETURN_PRECISION > 0
        assert DEFAULT_BPS_PRECISION > 0

    def test_generation_timeout_positive(self):
        """生成超时阈值为正."""
        assert DEFAULT_GENERATION_TIMEOUT > 0
        assert DEFAULT_GENERATION_TIMEOUT == 30

    def test_residual_tolerance_positive(self):
        """残差容差为正且很小."""
        assert DEFAULT_RESIDUAL_TOLERANCE > 0
        assert DEFAULT_RESIDUAL_TOLERANCE < 1e-3

    def test_module_status_codes_distinct(self):
        """子模块状态码互不相同."""
        codes = [
            MODULE_STATUS_OK,
            MODULE_STATUS_DEGRADED,
            MODULE_STATUS_SKIPPED,
            MODULE_STATUS_ERROR,
            MODULE_STATUS_FEATURE_FLAG_DISABLED,
            MODULE_STATUS_EMPTY_INPUT,
        ]
        assert len(set(codes)) == len(codes)

    def test_report_status_codes_distinct(self):
        """报告整体状态码互不相同."""
        codes = [
            STATUS_OK,
            STATUS_FEATURE_FLAG_DISABLED,
            STATUS_PARTIAL,
            STATUS_ALL_DEGRADED,
            STATUS_EMPTY_INPUT,
            STATUS_ERROR,
        ]
        assert len(set(codes)) == len(codes)


# ============================================================
# 2. 异常体系测试
# ============================================================


class TestExceptions:
    """异常体系完整性测试."""

    def test_base_exception_raisable(self):
        """基础异常可被 raise 和 catch."""
        with pytest.raises(DailyPanelError):
            raise DailyPanelError("test")

    def test_panel_generation_inherits_base(self):
        """PanelGenerationError 继承 DailyPanelError."""
        with pytest.raises(DailyPanelError):
            raise PanelGenerationError("gen error")

    def test_persistence_inherits_base(self):
        """PersistenceError 继承 DailyPanelError."""
        with pytest.raises(DailyPanelError):
            raise PersistenceError("persist error")

    def test_module_aggregation_inherits_base(self):
        """ModuleAggregationError 继承 DailyPanelError."""
        with pytest.raises(DailyPanelError):
            raise ModuleAggregationError("agg error")

    def test_specific_exception_catchable(self):
        """具体异常可被自身类型捕获."""
        with pytest.raises(PanelGenerationError):
            raise PanelGenerationError("gen")
        with pytest.raises(PersistenceError):
            raise PersistenceError("persist")
        with pytest.raises(ModuleAggregationError):
            raise ModuleAggregationError("agg")


# ============================================================
# 3. 输入数据类测试
# ============================================================


class TestInputDataClasses:
    """输入数据类测试."""

    def test_brinson_input_default_empty(self):
        """BrinsonInput 默认为空."""
        bi = BrinsonInput()
        assert bi.is_empty() is True

    def test_brinson_input_with_data(self):
        """BrinsonInput 有数据时非空."""
        bi = BrinsonInput(
            portfolio_weights={"finance": 0.4, "tech": 0.6},
            portfolio_returns={"finance": 0.02, "tech": 0.01},
        )
        assert bi.is_empty() is False

    def test_brinson_input_partial_empty(self):
        """BrinsonInput 缺少 portfolio_returns 时为空."""
        bi = BrinsonInput(
            portfolio_weights={"finance": 0.4},
            portfolio_returns={},
        )
        assert bi.is_empty() is True

    def test_factor_input_default_empty(self):
        """FactorInput 默认为空."""
        fi = FactorInput()
        assert fi.is_empty() is True

    def test_factor_input_with_data(self):
        """FactorInput 有数据时非空."""
        fi = FactorInput(
            portfolio_exposures={"Size": 0.5, "Beta": 1.1},
            factor_returns={"Size": 0.001, "Beta": 0.002},
        )
        assert fi.is_empty() is False

    def test_factor_input_default_portfolio_value(self):
        """FactorInput 默认组合价值 100 万."""
        fi = FactorInput()
        assert fi.portfolio_value == 1_000_000.0

    def test_tca_input_default_empty(self):
        """TCAInput 默认为空."""
        ti = TCAInput()
        assert ti.is_empty() is True

    def test_tca_input_with_summary(self):
        """TCAInput 有 summary_dict 时非空."""
        ti = TCAInput(summary_dict={"total_pnl": 1000.0})
        assert ti.is_empty() is False

    def test_tca_input_with_pnl_list(self):
        """TCAInput 有 pnl_attributions 时非空."""
        ti = TCAInput(pnl_attributions=[{"symbol": "AAPL", "total_pnl": 100}])
        assert ti.is_empty() is False

    def test_tca_input_with_fills(self):
        """TCAInput 有 fill_records 时非空."""
        ti = TCAInput(fill_records=[{"symbol": "AAPL", "shares": 100}])
        assert ti.is_empty() is False

    def test_daily_report_input_default_all_empty(self):
        """DailyReportInput 默认全部为空."""
        dri = DailyReportInput()
        assert dri.is_all_empty() is True

    def test_daily_report_input_with_factor(self):
        """DailyReportInput 有 factor 输入时非全空."""
        dri = DailyReportInput(
            factor=FactorInput(
                portfolio_exposures={"Size": 0.5},
                factor_returns={"Size": 0.001},
            )
        )
        assert dri.is_all_empty() is False


# ============================================================
# 4. 输出数据类测试
# ============================================================


class TestOutputDataClasses:
    """输出数据类测试."""

    def test_module_status_default_values(self):
        """ModuleStatus 默认值正确."""
        ms = ModuleStatus()
        assert ms.module_name == ""
        assert ms.status == MODULE_STATUS_OK
        assert ms.reason == ""
        assert ms.feature_flag_enabled is False
        assert ms.generation_time_ms == 0.0
        assert ms.has_data is False

    def test_module_status_to_dict(self):
        """ModuleStatus.to_dict() 字段完整."""
        ms = ModuleStatus(
            module_name="brinson",
            status=MODULE_STATUS_OK,
            reason="",
            feature_flag_enabled=True,
            generation_time_ms=12.5,
            has_data=True,
        )
        d = ms.to_dict()
        assert d["module_name"] == "brinson"
        assert d["status"] == MODULE_STATUS_OK
        assert d["feature_flag_enabled"] is True
        assert d["generation_time_ms"] == 12.5
        assert d["has_data"] is True

    def test_daily_report_default_values(self):
        """DailyAttributionReport 默认值正确."""
        r = DailyAttributionReport()
        assert r.attribution_date == ""
        assert r.benchmark_code == ""
        assert r.brinson_dict == {}
        assert r.factor_dict == {}
        assert r.tca_dict == {}
        assert r.module_statuses == []
        assert r.total_pnl == 0.0
        assert r.active_return == 0.0
        assert r.active_risk == 0.0
        assert r.information_ratio == 0.0
        assert r.status == STATUS_OK

    def test_daily_report_to_dict_structure(self):
        """DailyAttributionReport.to_dict() 结构完整."""
        r = DailyAttributionReport(
            attribution_date="2026-07-27",
            benchmark_code="510300.SH",
            total_pnl=12345.67,
            active_return=0.012,
            active_risk=0.05,
            information_ratio=0.24,
            status=STATUS_OK,
        )
        d = r.to_dict()
        assert d["attribution_date"] == "2026-07-27"
        assert d["benchmark_code"] == "510300.SH"
        assert "brinson" in d
        assert "factor" in d
        assert "tca" in d
        assert "module_statuses" in d
        assert "summary" in d
        assert "metadata" in d
        assert d["summary"]["total_pnl"] == 12345.67
        assert d["summary"]["active_return"] == 0.012
        assert d["metadata"]["status"] == STATUS_OK

    def test_daily_report_to_dict_with_module_statuses(self):
        """DailyAttributionReport.to_dict() 包含子模块状态."""
        r = DailyAttributionReport(
            module_statuses=[
                ModuleStatus(module_name="brinson", status=MODULE_STATUS_OK),
                ModuleStatus(module_name="factor", status=MODULE_STATUS_DEGRADED),
            ]
        )
        d = r.to_dict()
        assert len(d["module_statuses"]) == 2
        assert d["module_statuses"][0]["module_name"] == "brinson"
        assert d["module_statuses"][1]["status"] == MODULE_STATUS_DEGRADED

    def test_daily_report_to_markdown_contains_sections(self):
        """DailyAttributionReport.to_markdown() 包含必要章节."""
        r = DailyAttributionReport(
            attribution_date="2026-07-27",
            benchmark_code="510300.SH",
            total_pnl=1000.0,
            active_return=0.01,
            active_risk=0.05,
            information_ratio=0.2,
            status=STATUS_OK,
            module_statuses=[
                ModuleStatus(module_name="brinson", status=MODULE_STATUS_OK),
            ],
            brinson_markdown="### Brinson 内容",
            factor_markdown="### Factor 内容",
            tca_markdown="### TCA 内容",
        )
        md = r.to_markdown()
        assert "日级归因面板" in md
        assert "2026-07-27" in md
        assert "510300.SH" in md
        assert "摘要" in md
        assert "子模块状态" in md
        assert "Brinson 归因" in md
        assert "Barra 因子归因" in md
        assert "TCA 执行归因" in md
        assert "1000.00" in md  # total_pnl

    def test_daily_report_to_markdown_handles_zero_ir(self):
        """DailyAttributionReport.to_markdown() 处理 NaN/Inf 信息比率."""
        r = DailyAttributionReport(information_ratio=float("nan"))
        md = r.to_markdown()
        assert "N/A" in md

    def test_daily_report_to_markdown_empty(self):
        """DailyAttributionReport.to_markdown() 空报告也能生成."""
        r = DailyAttributionReport()
        md = r.to_markdown()
        assert "日级归因面板" in md
        assert "摘要" in md


# ============================================================
# 5. TCA 聚合算法测试
# ============================================================


class TestTCAAggregation:
    """TCA 聚合算法测试."""

    def test_aggregate_summary_dict(self):
        """聚合 summary_dict 模式."""
        panel = DailyAttributionPanel(
            config={
                "settings": {
                    "enable_brinson": False,
                    "enable_factor": False,
                    "enable_tca": True,
                }
            }
        )
        summary = {
            "total_pnl": 1000.0,
            "alpha_pnl": 600.0,
            "execution_pnl": 200.0,
            "risk_pnl": 200.0,
            "n_fills": 5,
            "n_symbols": 3,
            "alpha_bps": 6.0,
            "execution_bps": 2.0,
            "risk_bps": 2.0,
        }
        result = panel._aggregate_tca(TCAInput(summary_dict=summary))
        assert result["source"] == "post_trade_summarize"
        assert result["total_pnl"] == 1000.0
        assert result["alpha_pnl"] == 600.0
        assert result["execution_pnl"] == 200.0
        assert result["risk_pnl"] == 200.0
        assert result["n_fills"] == 5
        assert result["n_symbols"] == 3
        # 残差验证
        assert abs(result["residual"]) < 1e-6

    def test_aggregate_pnl_list(self):
        """聚合 PnLAttribution 列表模式."""
        panel = DailyAttributionPanel(
            config={
                "settings": {
                    "enable_brinson": False,
                    "enable_factor": False,
                    "enable_tca": True,
                }
            }
        )
        pnl_list = [
            {
                "symbol": "AAPL",
                "alpha_pnl": 100.0,
                "execution_pnl": 20.0,
                "risk_pnl": 10.0,
                "total_pnl": 130.0,
                "notional": 10000.0,
            },
            {
                "symbol": "MSFT",
                "alpha_pnl": 200.0,
                "execution_pnl": 30.0,
                "risk_pnl": 20.0,
                "total_pnl": 250.0,
                "notional": 20000.0,
            },
        ]
        result = panel._aggregate_tca(TCAInput(pnl_attributions=pnl_list))
        assert result["source"] == "aggregated_from_pnl_list"
        assert result["alpha_pnl"] == 300.0
        assert result["execution_pnl"] == 50.0
        assert result["risk_pnl"] == 30.0
        assert result["total_pnl"] == 380.0
        assert result["n_fills"] == 2
        assert result["n_symbols"] == 2
        # bps = (300 / 30000) * 10000 = 100
        assert abs(result["alpha_bps"] - 100.0) < 1e-6

    def test_aggregate_fills_only(self):
        """仅有 fill_records 时返回基础汇总."""
        panel = DailyAttributionPanel(
            config={
                "settings": {
                    "enable_brinson": False,
                    "enable_factor": False,
                    "enable_tca": True,
                }
            }
        )
        fills = [
            {"symbol": "AAPL", "shares": 100, "price": 50.0},
            {"symbol": "MSFT", "shares": 200, "price": 30.0},
        ]
        result = panel._aggregate_tca(TCAInput(fill_records=fills))
        assert result["source"] == "fills_only_no_pnl"
        assert result["total_pnl"] == 0.0
        assert result["alpha_pnl"] == 0.0
        assert result["n_fills"] == 2
        assert result["n_symbols"] == 2
        # notional = 100*50 + 200*30 = 5000 + 6000 = 11000
        assert result["total_notional"] == 11000.0

    def test_aggregate_empty_input(self):
        """空 TCAInput 返回空 dict."""
        panel = DailyAttributionPanel(
            config={
                "settings": {
                    "enable_brinson": False,
                    "enable_factor": False,
                    "enable_tca": True,
                }
            }
        )
        result = panel._aggregate_tca(TCAInput())
        assert result == {}

    def test_aggregate_pnl_list_with_invalid_items(self):
        """PnL 列表含无效项时跳过."""
        panel = DailyAttributionPanel(
            config={
                "settings": {
                    "enable_brinson": False,
                    "enable_factor": False,
                    "enable_tca": True,
                }
            }
        )
        pnl_list = [
            {
                "symbol": "AAPL",
                "alpha_pnl": 100.0,
                "total_pnl": 100.0,
                "notional": 10000.0,
            },
            {"symbol": "MSFT", "alpha_pnl": "invalid", "total_pnl": 50.0},  # 无效项
        ]
        result = panel._aggregate_tca(TCAInput(pnl_attributions=pnl_list))
        # 第一项有效, 第二项跳过 alpha_pnl 但其他字段仍可解析
        assert result["alpha_pnl"] == 100.0
        assert result["n_fills"] == 2

    def test_aggregate_summary_residual_warning(self):
        """summary_dict 残差超过容差时记录日志 (不抛异常)."""
        panel = DailyAttributionPanel(
            config={
                "settings": {
                    "enable_brinson": False,
                    "enable_factor": False,
                    "enable_tca": True,
                },
                "aggregation": {"residual_tolerance": 1e-9},
            }
        )
        summary = {
            "total_pnl": 1000.0,
            "alpha_pnl": 600.0,
            "execution_pnl": 200.0,
            "risk_pnl": 100.0,  # 故意让和 = 900, 残差 = 100
        }
        # 不应抛异常
        result = panel._aggregate_tca(TCAInput(summary_dict=summary))
        assert abs(result["residual"] - 100.0) < 1e-3

    def test_bps_calculation_zero_notional(self):
        """notional 为 0 时 bps 也为 0 (避免除零)."""
        panel = DailyAttributionPanel(
            config={
                "settings": {
                    "enable_brinson": False,
                    "enable_factor": False,
                    "enable_tca": True,
                }
            }
        )
        pnl_list = [
            {"symbol": "AAPL", "alpha_pnl": 100.0, "total_pnl": 100.0, "notional": 0.0},
        ]
        result = panel._aggregate_tca(TCAInput(pnl_attributions=pnl_list))
        assert result["alpha_bps"] == 0.0


# ============================================================
# 6. Markdown 生成测试
# ============================================================


class TestMarkdownGeneration:
    """Markdown 生成测试."""

    def test_tca_markdown_basic(self):
        """TCA Markdown 基础生成."""
        panel = DailyAttributionPanel(
            config={
                "settings": {
                    "enable_brinson": False,
                    "enable_factor": False,
                    "enable_tca": True,
                }
            }
        )
        summary = {
            "source": "post_trade_summarize",
            "total_pnl": 1000.0,
            "alpha_pnl": 600.0,
            "execution_pnl": 200.0,
            "risk_pnl": 200.0,
            "alpha_bps": 6.0,
            "execution_bps": 2.0,
            "risk_bps": 2.0,
            "n_fills": 5,
            "n_symbols": 3,
            "residual": 0.0,
        }
        md = panel._build_tca_markdown(summary, "2026-07-27")
        assert "2026-07-27" in md
        assert "post_trade_summarize" in md
        assert "Alpha (决策)" in md
        assert "Execution (执行)" in md
        assert "Risk (风险)" in md
        assert "600.00" in md
        assert "1000.00" in md

    def test_tca_markdown_empty_summary(self):
        """空 summary 返回空字符串."""
        panel = DailyAttributionPanel()
        md = panel._build_tca_markdown({}, "2026-07-27")
        assert md == ""

    def test_tca_markdown_residual_warning(self):
        """残差超过容差时显示警告."""
        panel = DailyAttributionPanel(
            config={
                "settings": {
                    "enable_brinson": False,
                    "enable_factor": False,
                    "enable_tca": True,
                },
                "aggregation": {"residual_tolerance": 1e-9},
            }
        )
        summary = {
            "total_pnl": 1000.0,
            "alpha_pnl": 600.0,
            "execution_pnl": 200.0,
            "risk_pnl": 100.0,
            "alpha_bps": 6.0,
            "execution_bps": 2.0,
            "risk_bps": 1.0,
            "n_fills": 5,
            "n_symbols": 3,
            "source": "test",
            "residual": 100.0,
        }
        md = panel._build_tca_markdown(summary, "2026-07-27")
        assert "残差" in md
        assert "⚠️" in md

    def test_tca_markdown_zero_total_pct_na(self):
        """total_pnl=0 时占比显示 N/A."""
        panel = DailyAttributionPanel(
            config={
                "settings": {
                    "enable_brinson": False,
                    "enable_factor": False,
                    "enable_tca": True,
                }
            }
        )
        summary = {
            "total_pnl": 0.0,
            "alpha_pnl": 0.0,
            "execution_pnl": 0.0,
            "risk_pnl": 0.0,
            "alpha_bps": 0.0,
            "execution_bps": 0.0,
            "risk_bps": 0.0,
            "n_fills": 0,
            "n_symbols": 0,
            "source": "test",
            "residual": 0.0,
        }
        md = panel._build_tca_markdown(summary, "2026-07-27")
        assert "N/A" in md


# ============================================================
# 7. DailyAttributionPanel 主类测试
# ============================================================


class TestDailyAttributionPanel:
    """DailyAttributionPanel 主类测试."""

    def test_init_with_explicit_config(self):
        """显式配置初始化."""
        panel = DailyAttributionPanel(
            config={
                "settings": {
                    "primary_benchmark": "510500.SH",
                    "report_dir": "reports/test_attribution",
                    "enable_brinson": True,
                    "enable_factor": True,
                    "enable_tca": True,
                }
            }
        )
        assert panel._primary_benchmark == "510500.SH"
        assert panel._report_dir_name == "reports/test_attribution"
        assert panel._enable_brinson_cfg is True

    def test_init_default_config(self):
        """默认配置初始化 (走 ConfigManager 或空配置)."""
        panel = DailyAttributionPanel(config={})
        # 即使配置为空, 也有默认值
        assert panel._primary_benchmark == DEFAULT_PRIMARY_BENCHMARK
        assert panel._report_dir_name == DEFAULT_REPORT_DIR
        assert panel._generation_timeout == DEFAULT_GENERATION_TIMEOUT

    def test_feature_flag_disabled_returns_disabled_report(self):
        """HC-1: Feature Flag 关闭时返回降级报告."""
        panel = DailyAttributionPanel(config={})
        with patch.object(panel, "_is_feature_flag_enabled", return_value=False):
            report = panel.generate(attribution_date="2026-07-27")
        assert report.status == STATUS_FEATURE_FLAG_DISABLED
        assert "False" in report.reason
        assert report.attribution_date == "2026-07-27"

    def test_generate_all_empty_inputs(self):
        """全部输入为空时返回 empty 报告."""
        panel = DailyAttributionPanel(config={})
        with patch.object(panel, "_is_feature_flag_enabled", return_value=True):
            report = panel.generate(attribution_date="2026-07-27")
        assert report.status == STATUS_EMPTY_INPUT
        assert report.attribution_date == "2026-07-27"

    def test_generate_with_only_brinson(self):
        """仅 Brinson 输入时生成 partial 报告."""
        panel = DailyAttributionPanel(
            config={
                "settings": {
                    "enable_brinson": True,
                    "enable_factor": True,
                    "enable_tca": True,
                }
            }
        )
        brinson_input = BrinsonInput(
            portfolio_weights={"finance": 0.5, "tech": 0.5},
            portfolio_returns={"finance": 0.02, "tech": 0.01},
            benchmark_weights={"finance": 0.4, "tech": 0.6},
            benchmark_returns={"finance": 0.01, "tech": 0.015},
        )
        # Mock Feature Flag 与子模块
        with (
            patch.object(panel, "_is_feature_flag_enabled", return_value=True),
            patch.object(panel, "_init_brinson_manager") as mock_init,
        ):
            # 创建模拟 BrinsonAttributionManager
            mock_mgr = MagicMock()
            mock_result = MagicMock()
            mock_result.status = "ok"
            mock_result.to_dict.return_value = {
                "attribution_date": "2026-07-27",
                "total_return": 0.015,
                "excess_return": 0.005,
                "status": "ok",
            }
            mock_result.to_markdown.return_value = "### Brinson 内容"
            mock_mgr.attribute.return_value = mock_result
            mock_init.return_value = mock_mgr

            report = panel.generate(
                attribution_date="2026-07-27",
                brinson_input=brinson_input,
            )

        assert report.attribution_date == "2026-07-27"
        assert len(report.module_statuses) == 3
        # Brinson 应该 OK, factor 和 tca 应该 empty_input
        brinson_status = report.module_statuses[0]
        assert brinson_status.module_name == "brinson"
        assert brinson_status.status == MODULE_STATUS_OK
        assert report.status == STATUS_PARTIAL  # 部分模块降级

    def test_generate_skipped_module(self):
        """配置禁用某个模块时标记为 skipped."""
        panel = DailyAttributionPanel(
            config={
                "settings": {
                    "enable_brinson": False,
                    "enable_factor": True,
                    "enable_tca": True,
                }
            }
        )
        with patch.object(panel, "_is_feature_flag_enabled", return_value=True):
            report = panel.generate(
                attribution_date="2026-07-27",
                brinson_input=BrinsonInput(
                    portfolio_weights={"a": 1.0},
                    portfolio_returns={"a": 0.01},
                ),
            )
        brinson_status = report.module_statuses[0]
        assert brinson_status.status == MODULE_STATUS_SKIPPED
        assert "enable_brinson=False" in brinson_status.reason

    def test_generate_module_exception_handled(self):
        """子模块异常不阻塞整体报告."""
        panel = DailyAttributionPanel(
            config={
                "settings": {
                    "enable_brinson": True,
                    "enable_factor": True,
                    "enable_tca": True,
                }
            }
        )
        brinson_input = BrinsonInput(
            portfolio_weights={"finance": 1.0},
            portfolio_returns={"finance": 0.01},
        )
        with (
            patch.object(panel, "_is_feature_flag_enabled", return_value=True),
            patch.object(panel, "_init_brinson_manager") as mock_init,
        ):
            mock_mgr = MagicMock()
            mock_mgr.attribute.side_effect = RuntimeError("Brinson 内部错误")
            mock_init.return_value = mock_mgr

            report = panel.generate(
                attribution_date="2026-07-27",
                brinson_input=brinson_input,
            )

        brinson_status = report.module_statuses[0]
        assert brinson_status.status == MODULE_STATUS_ERROR
        assert "Brinson 异常" in brinson_status.reason
        assert "RuntimeError" in brinson_status.reason

    def test_generate_with_all_modules_ok(self):
        """全部模块正常时返回 ok 报告."""
        panel = DailyAttributionPanel(
            config={
                "settings": {
                    "enable_brinson": True,
                    "enable_factor": True,
                    "enable_tca": True,
                }
            }
        )
        brinson_input = BrinsonInput(
            portfolio_weights={"finance": 0.5, "tech": 0.5},
            portfolio_returns={"finance": 0.02, "tech": 0.01},
        )
        factor_input = FactorInput(
            portfolio_exposures={"Size": 0.5, "Beta": 1.1},
            factor_returns={"Size": 0.001, "Beta": 0.002},
        )
        tca_input = TCAInput(summary_dict={"total_pnl": 1000.0, "alpha_pnl": 600.0})

        with (
            patch.object(panel, "_is_feature_flag_enabled", return_value=True),
            patch.object(panel, "_init_brinson_manager") as mock_brinson,
            patch.object(panel, "_init_factor_manager") as mock_factor,
        ):
            # Mock Brinson Manager
            mock_b_mgr = MagicMock()
            mock_b_result = MagicMock()
            mock_b_result.status = "ok"
            mock_b_result.to_dict.return_value = {"status": "ok", "total_return": 0.015}
            mock_b_result.to_markdown.return_value = "### Brinson"
            mock_b_mgr.attribute.return_value = mock_b_result
            mock_brinson.return_value = mock_b_mgr

            # Mock Factor Manager
            mock_f_mgr = MagicMock()
            mock_f_result = MagicMock()
            mock_f_result.status = "ok"
            mock_f_result.to_dict.return_value = {
                "status": "ok",
                "total_pnl": 15000.0,
                "active_return": 0.012,
                "active_risk": 0.05,
                "information_ratio": 0.24,
            }
            mock_f_result.to_markdown.return_value = "### Factor"
            mock_f_mgr.attribute.return_value = mock_f_result
            mock_factor.return_value = mock_f_mgr

            report = panel.generate(
                attribution_date="2026-07-27",
                brinson_input=brinson_input,
                factor_input=factor_input,
                tca_input=tca_input,
            )

        assert report.status == STATUS_OK
        assert report.total_pnl == 15000.0
        assert report.active_return == 0.012
        assert report.information_ratio == 0.24
        assert len(report.module_statuses) == 3
        for s in report.module_statuses:
            assert s.status == MODULE_STATUS_OK

    def test_generate_with_inputs_container(self):
        """使用 DailyReportInput 统一容器."""
        panel = DailyAttributionPanel(
            config={
                "settings": {
                    "enable_brinson": True,
                    "enable_factor": True,
                    "enable_tca": True,
                }
            }
        )
        inputs = DailyReportInput(
            tca=TCAInput(summary_dict={"total_pnl": 500.0, "alpha_pnl": 300.0})
        )
        with patch.object(panel, "_is_feature_flag_enabled", return_value=True):
            report = panel.generate(attribution_date="2026-07-27", inputs=inputs)
        # TCA 模块应该 OK, 其他两个 empty_input
        tca_status = report.module_statuses[2]
        assert tca_status.status == MODULE_STATUS_OK
        assert report.status == STATUS_PARTIAL

    def test_evaluate_overall_status_all_ok(self):
        """状态评估: 全部 OK."""
        panel = DailyAttributionPanel(config={})
        statuses = [
            ModuleStatus(module_name="brinson", status=MODULE_STATUS_OK),
            ModuleStatus(module_name="factor", status=MODULE_STATUS_OK),
            ModuleStatus(module_name="tca", status=MODULE_STATUS_OK),
        ]
        status, reason = panel._evaluate_overall_status(statuses)
        assert status == STATUS_OK
        assert "正常" in reason

    def test_evaluate_overall_status_partial(self):
        """状态评估: 部分降级."""
        panel = DailyAttributionPanel(config={})
        statuses = [
            ModuleStatus(module_name="brinson", status=MODULE_STATUS_OK),
            ModuleStatus(module_name="factor", status=MODULE_STATUS_DEGRADED),
            ModuleStatus(module_name="tca", status=MODULE_STATUS_EMPTY_INPUT),
        ]
        status, _reason = panel._evaluate_overall_status(statuses)
        assert status == STATUS_PARTIAL

    def test_evaluate_overall_status_all_degraded(self):
        """状态评估: 全部降级."""
        panel = DailyAttributionPanel(config={})
        statuses = [
            ModuleStatus(module_name="brinson", status=MODULE_STATUS_DEGRADED),
            ModuleStatus(module_name="factor", status=MODULE_STATUS_EMPTY_INPUT),
            ModuleStatus(module_name="tca", status=MODULE_STATUS_SKIPPED),
        ]
        status, _reason = panel._evaluate_overall_status(statuses)
        assert status == STATUS_ALL_DEGRADED

    def test_evaluate_overall_status_all_error(self):
        """状态评估: 全部异常."""
        panel = DailyAttributionPanel(config={})
        statuses = [
            ModuleStatus(module_name="brinson", status=MODULE_STATUS_ERROR),
            ModuleStatus(module_name="factor", status=MODULE_STATUS_ERROR),
            ModuleStatus(module_name="tca", status=MODULE_STATUS_ERROR),
        ]
        status, _reason = panel._evaluate_overall_status(statuses)
        assert status == STATUS_ERROR

    def test_evaluate_overall_status_empty_list(self):
        """状态评估: 空列表."""
        panel = DailyAttributionPanel(config={})
        status, _reason = panel._evaluate_overall_status([])
        assert status == STATUS_EMPTY_INPUT


# ============================================================
# 8. 持久化测试
# ============================================================


class TestPersistence:
    """持久化测试."""

    def test_save_creates_json_and_markdown(self, tmp_path):
        """save() 创建 JSON 和 Markdown 文件."""
        panel = DailyAttributionPanel(config={})
        report = DailyAttributionReport(
            attribution_date="2026-07-27",
            benchmark_code="510300.SH",
            total_pnl=1000.0,
            status=STATUS_OK,
        )
        paths = panel.save(report, report_dir=tmp_path)
        assert "json" in paths
        assert "markdown" in paths
        assert paths["json"].exists()
        assert paths["markdown"].exists()
        # 文件名正确
        assert paths["json"].name == "daily_panel_2026-07-27.json"
        assert paths["markdown"].name == "daily_panel_2026-07-27.md"

    def test_save_json_content_valid(self, tmp_path):
        """JSON 文件内容可解析."""
        panel = DailyAttributionPanel(config={})
        report = DailyAttributionReport(
            attribution_date="2026-07-27",
            benchmark_code="510300.SH",
            total_pnl=1234.56,
            status=STATUS_OK,
        )
        paths = panel.save(report, report_dir=tmp_path)
        with open(paths["json"], encoding="utf-8") as f:
            data = json.load(f)
        assert data["attribution_date"] == "2026-07-27"
        assert data["benchmark_code"] == "510300.SH"
        assert data["summary"]["total_pnl"] == 1234.56

    def test_save_markdown_content_readable(self, tmp_path):
        """Markdown 文件内容可读."""
        panel = DailyAttributionPanel(config={})
        report = DailyAttributionReport(
            attribution_date="2026-07-27",
            benchmark_code="510300.SH",
            total_pnl=1000.0,
            status=STATUS_OK,
        )
        paths = panel.save(report, report_dir=tmp_path)
        with open(paths["markdown"], encoding="utf-8") as f:
            md = f.read()
        assert "日级归因面板" in md
        assert "2026-07-27" in md

    def test_save_only_json(self, tmp_path):
        """仅保存 JSON."""
        panel = DailyAttributionPanel(config={})
        report = DailyAttributionReport(attribution_date="2026-07-27")
        paths = panel.save(
            report, report_dir=tmp_path, save_json=True, save_markdown=False
        )
        assert "json" in paths
        assert "markdown" not in paths

    def test_save_only_markdown(self, tmp_path):
        """仅保存 Markdown."""
        panel = DailyAttributionPanel(config={})
        report = DailyAttributionReport(attribution_date="2026-07-27")
        paths = panel.save(
            report, report_dir=tmp_path, save_json=False, save_markdown=True
        )
        assert "markdown" in paths
        assert "json" not in paths

    def test_save_creates_directory(self, tmp_path):
        """save() 自动创建不存在的目录."""
        panel = DailyAttributionPanel(config={})
        report = DailyAttributionReport(attribution_date="2026-07-27")
        nested_dir = tmp_path / "nested" / "deep" / "path"
        paths = panel.save(report, report_dir=nested_dir)
        assert paths["json"].exists()
        assert nested_dir.exists()

    def test_save_persistence_error_on_invalid_dir(self):
        """无效目录触发 PersistenceError."""
        panel = DailyAttributionPanel(config={})
        report = DailyAttributionReport(attribution_date="2026-07-27")
        # 跨平台安全 (2026-09-09): 此前用 "Z:\\..." Windows 保留盘符构造无效路径,
        # 在 Linux/macOS 上是合法的相对目录名, mkdir 会成功 → PersistenceError
        # 不会触发。改用文件路径 (存在同名文件时 mkdir 必失败), 全平台可复现。
        with tempfile.TemporaryDirectory() as td:
            blocker = Path(td) / "not_a_dir"
            blocker.write_text("occupied", encoding="utf-8")
            invalid_path = blocker / "sub" / "dir"
            with pytest.raises(PersistenceError):
                panel.save(report, report_dir=invalid_path)

    def test_generate_and_save(self, tmp_path):
        """generate_and_save() 一步完成生成和保存."""
        panel = DailyAttributionPanel(
            config={
                "settings": {
                    "enable_brinson": True,
                    "enable_factor": True,
                    "enable_tca": True,
                }
            }
        )
        with patch.object(panel, "_is_feature_flag_enabled", return_value=True):
            report, paths = panel.generate_and_save(
                attribution_date="2026-07-27",
                tca_input=TCAInput(summary_dict={"total_pnl": 100.0}),
                report_dir=tmp_path,
            )
        assert report.attribution_date == "2026-07-27"
        assert "json" in paths
        assert paths["json"].exists()


# ============================================================
# 9. 便捷函数测试
# ============================================================


class TestConvenienceFunctions:
    """便捷函数测试."""

    def test_is_daily_panel_enabled_returns_bool(self):
        """is_daily_panel_enabled() 返回 bool."""
        result = is_daily_panel_enabled()
        assert isinstance(result, bool)

    def test_create_default_panel(self):
        """create_default_panel() 返回 DailyAttributionPanel 实例."""
        panel = create_default_panel()
        assert isinstance(panel, DailyAttributionPanel)
        assert panel._feature_flag_name == FLAG_NAME

    def test_generate_daily_report_no_save(self, tmp_path):
        """generate_daily_report(save=False) 不保存文件."""
        with patch.object(
            DailyAttributionPanel, "_is_feature_flag_enabled", return_value=True
        ):
            report, paths = generate_daily_report(
                attribution_date="2026-07-27",
                tca_input=TCAInput(summary_dict={"total_pnl": 100.0}),
                save=False,
                report_dir=tmp_path,
            )
        assert report.attribution_date == "2026-07-27"
        assert paths == {}

    def test_generate_daily_report_with_save(self, tmp_path):
        """generate_daily_report(save=True) 保存文件."""
        with patch.object(
            DailyAttributionPanel, "_is_feature_flag_enabled", return_value=True
        ):
            report, paths = generate_daily_report(
                attribution_date="2026-07-27",
                tca_input=TCAInput(summary_dict={"total_pnl": 100.0}),
                save=True,
                report_dir=tmp_path,
            )
        assert report.attribution_date == "2026-07-27"
        assert "json" in paths


# ============================================================
# 10. 边界条件测试
# ============================================================


class TestEdgeCases:
    """边界条件测试."""

    def test_empty_brinson_input_dict(self):
        """BrinsonInput 空 dict 视为空."""
        bi = BrinsonInput(portfolio_weights={}, portfolio_returns={})
        assert bi.is_empty() is True

    def test_empty_factor_input_dict(self):
        """FactorInput 空 dict 视为空."""
        fi = FactorInput(portfolio_exposures={}, factor_returns={})
        assert fi.is_empty() is True

    def test_tca_input_with_empty_list(self):
        """TCAInput 空列表视为空."""
        ti = TCAInput(pnl_attributions=[], fill_records=[])
        assert ti.is_empty() is True

    def test_generate_with_default_date(self):
        """未指定日期时使用今天."""
        panel = DailyAttributionPanel(config={})
        with patch.object(panel, "_is_feature_flag_enabled", return_value=True):
            report = panel.generate()  # 无日期
        # 应该是今天日期 (YYYY-MM-DD)
        from utils.datetime_utils import now_bj

        today = now_bj().strftime("%Y-%m-%d")
        assert report.attribution_date == today

    def test_generate_partial_brinson_dict_population(self):
        """Brinson 部分有数据时 brinson_dict 被填充."""
        panel = DailyAttributionPanel(
            config={
                "settings": {
                    "enable_brinson": True,
                    "enable_factor": True,
                    "enable_tca": True,
                }
            }
        )
        brinson_input = BrinsonInput(
            portfolio_weights={"finance": 1.0},
            portfolio_returns={"finance": 0.01},
        )
        with (
            patch.object(panel, "_is_feature_flag_enabled", return_value=True),
            patch.object(panel, "_init_brinson_manager") as mock_init,
        ):
            mock_mgr = MagicMock()
            mock_result = MagicMock()
            mock_result.status = "ok"
            mock_result.to_dict.return_value = {"status": "ok", "excess_return": 0.005}
            mock_result.to_markdown.return_value = "Brinson OK"
            mock_mgr.attribute.return_value = mock_result
            mock_init.return_value = mock_mgr

            report = panel.generate(
                attribution_date="2026-07-27",
                brinson_input=brinson_input,
            )

        assert report.brinson_dict != {}
        assert report.brinson_dict["status"] == "ok"
        assert report.brinson_markdown == "Brinson OK"

    def test_populate_summary_from_empty_factor_dict(self):
        """factor_dict 为空时不影响 report 顶层字段."""
        panel = DailyAttributionPanel(config={})
        report = DailyAttributionReport()
        panel._populate_summary_from_factor(report)
        assert report.total_pnl == 0.0
        assert report.active_return == 0.0

    def test_populate_summary_with_invalid_values(self):
        """factor_dict 含非数值时不抛异常."""
        panel = DailyAttributionPanel(config={})
        report = DailyAttributionReport(
            factor_dict={"total_pnl": "invalid", "active_return": None}
        )
        # 不应抛异常
        panel._populate_summary_from_factor(report)

    def test_module_status_to_dict_round_trip(self):
        """ModuleStatus.to_dict() 可序列化为 JSON."""
        ms = ModuleStatus(
            module_name="brinson",
            status=MODULE_STATUS_OK,
            generation_time_ms=15.5,
            feature_flag_enabled=True,
        )
        d = ms.to_dict()
        # 可序列化为 JSON
        json_str = json.dumps(d)
        parsed = json.loads(json_str)
        assert parsed["module_name"] == "brinson"
        assert parsed["generation_time_ms"] == 15.5

    def test_daily_report_to_dict_json_serializable(self):
        """DailyAttributionReport.to_dict() 可完整 JSON 序列化."""
        r = DailyAttributionReport(
            attribution_date="2026-07-27",
            benchmark_code="510300.SH",
            total_pnl=1234.56,
            module_statuses=[ModuleStatus(module_name="brinson")],
            brinson_dict={"status": "ok", "value": 123},
        )
        d = r.to_dict()
        json_str = json.dumps(d, default=str)
        parsed = json.loads(json_str)
        assert parsed["attribution_date"] == "2026-07-27"
        assert parsed["brinson"]["status"] == "ok"


# ============================================================
# 11. 性能测试
# ============================================================


class TestPerformance:
    """性能测试 (验收标准: 生成时间 < 30s)."""

    def test_generation_time_under_threshold(self):
        """单次报告生成时间 < 30s."""
        panel = DailyAttributionPanel(
            config={
                "settings": {
                    "enable_brinson": True,
                    "enable_factor": True,
                    "enable_tca": True,
                }
            }
        )
        with (
            patch.object(panel, "_is_feature_flag_enabled", return_value=True),
            patch.object(panel, "_init_brinson_manager") as mock_b,
            patch.object(panel, "_init_factor_manager") as mock_f,
        ):
            mock_b_mgr = MagicMock()
            mock_b_result = MagicMock()
            mock_b_result.status = "ok"
            mock_b_result.to_dict.return_value = {"status": "ok"}
            mock_b_result.to_markdown.return_value = "Brinson"
            mock_b_mgr.attribute.return_value = mock_b_result
            mock_b.return_value = mock_b_mgr

            mock_f_mgr = MagicMock()
            mock_f_result = MagicMock()
            mock_f_result.status = "ok"
            mock_f_result.to_dict.return_value = {"status": "ok"}
            mock_f_result.to_markdown.return_value = "Factor"
            mock_f_mgr.attribute.return_value = mock_f_result
            mock_f.return_value = mock_f_mgr

            start = time.time()
            report = panel.generate(
                attribution_date="2026-07-27",
                brinson_input=BrinsonInput(
                    portfolio_weights={"a": 1.0},
                    portfolio_returns={"a": 0.01},
                ),
                factor_input=FactorInput(
                    portfolio_exposures={"Size": 0.5},
                    factor_returns={"Size": 0.001},
                ),
                tca_input=TCAInput(summary_dict={"total_pnl": 100.0}),
            )
            elapsed = time.time() - start

        assert elapsed < 30.0  # 验收标准 < 30s
        assert report.generation_time_ms < 30_000

    def test_generation_time_recorded(self):
        """报告记录生成耗时."""
        panel = DailyAttributionPanel(config={})
        with patch.object(panel, "_is_feature_flag_enabled", return_value=True):
            report = panel.generate(attribution_date="2026-07-27")
        assert report.generation_time_ms > 0
        assert report.generation_time_ms < 1000  # 空报告应该很快


# ============================================================
# 12. HC 合规性测试
# ============================================================


class TestHCCompliance:
    """HC (Hard Constraint) 合规性测试."""

    def test_hc1_feature_flag_disabled_returns_zero_report(self):
        """HC-1: Feature Flag 关闭时返回全零降级报告."""
        panel = DailyAttributionPanel(config={})
        with patch.object(panel, "_is_feature_flag_enabled", return_value=False):
            report = panel.generate(attribution_date="2026-07-27")
        assert report.status == STATUS_FEATURE_FLAG_DISABLED
        assert report.total_pnl == 0.0
        assert report.active_return == 0.0
        assert report.brinson_dict == {}
        assert report.factor_dict == {}
        assert report.tca_dict == {}

    def test_hc1_feature_flag_name_correct(self):
        """HC-1: Feature Flag 名称正确."""
        assert FLAG_NAME == "USE_DAILY_ATTRIBUTION_PANEL"

    def test_hc5_config_uses_config_manager(self):
        """HC-5: 配置走 ConfigManager (验证 _load_config 调用 get_config)."""
        DailyAttributionPanel(config={})  # 显式空配置, 不走 ConfigManager
        # 当 config=None 时应走 ConfigManager
        with patch("utils.attribution.daily_panel.get_config") as mock_get:
            mock_get.return_value = {"settings": {"primary_benchmark": "510500.SH"}}
            panel2 = DailyAttributionPanel()  # 不传 config
            mock_get.assert_called_once_with(DEFAULT_CONFIG_NAME)
            assert panel2._primary_benchmark == "510500.SH"

    def test_hc2_main_path_not_blocked_on_module_failure(self):
        """HC-2: 子模块失败不阻塞主路径."""
        panel = DailyAttributionPanel(
            config={
                "settings": {
                    "enable_brinson": True,
                    "enable_factor": True,
                    "enable_tca": True,
                }
            }
        )
        with (
            patch.object(panel, "_is_feature_flag_enabled", return_value=True),
            patch.object(panel, "_init_brinson_manager") as mock_b,
            patch.object(panel, "_init_factor_manager") as mock_f,
        ):
            # 两个 Manager 都抛异常
            mock_b.side_effect = RuntimeError("Brinson init fail")
            mock_f.side_effect = RuntimeError("Factor init fail")

            report = panel.generate(
                attribution_date="2026-07-27",
                brinson_input=BrinsonInput(
                    portfolio_weights={"a": 1.0},
                    portfolio_returns={"a": 0.01},
                ),
                factor_input=FactorInput(
                    portfolio_exposures={"Size": 0.5},
                    factor_returns={"Size": 0.001},
                ),
                tca_input=TCAInput(summary_dict={"total_pnl": 100.0}),
            )

        # 报告仍能生成, 但状态为 partial 或 error
        assert report.status in (STATUS_PARTIAL, STATUS_ERROR, STATUS_ALL_DEGRADED)
        # TCA 模块应该 OK
        tca_status = report.module_statuses[2]
        assert tca_status.status == MODULE_STATUS_OK


# ============================================================
# 13. 综合场景测试
# ============================================================


class TestIntegrationScenarios:
    """综合场景测试."""

    def test_full_pipeline_with_mocked_managers(self, tmp_path):
        """完整流水线: 生成 + 保存 + 重新加载 JSON."""
        panel = DailyAttributionPanel(
            config={
                "settings": {
                    "enable_brinson": True,
                    "enable_factor": True,
                    "enable_tca": True,
                    "primary_benchmark": "510300.SH",
                }
            }
        )
        brinson_input = BrinsonInput(
            portfolio_weights={"finance": 0.5, "tech": 0.5},
            portfolio_returns={"finance": 0.02, "tech": 0.01},
            benchmark_weights={"finance": 0.4, "tech": 0.6},
            benchmark_returns={"finance": 0.015, "tech": 0.012},
        )
        factor_input = FactorInput(
            portfolio_exposures={"Size": 0.5, "Beta": 1.1, "Momentum": 0.3},
            factor_returns={"Size": 0.001, "Beta": 0.002, "Momentum": 0.005},
            portfolio_value=1_000_000.0,
        )
        tca_input = TCAInput(
            summary_dict={
                "total_pnl": 5000.0,
                "alpha_pnl": 3000.0,
                "execution_pnl": 1000.0,
                "risk_pnl": 1000.0,
                "n_fills": 10,
                "n_symbols": 5,
                "alpha_bps": 6.0,
                "execution_bps": 2.0,
                "risk_bps": 2.0,
            }
        )

        with (
            patch.object(panel, "_is_feature_flag_enabled", return_value=True),
            patch.object(panel, "_init_brinson_manager") as mock_b,
            patch.object(panel, "_init_factor_manager") as mock_f,
        ):
            mock_b_mgr = MagicMock()
            mock_b_result = MagicMock()
            mock_b_result.status = "ok"
            mock_b_result.to_dict.return_value = {
                "status": "ok",
                "attribution_date": "2026-07-27",
                "total_return": 0.015,
                "excess_return": 0.005,
                "total_allocation_effect": -0.001,
                "total_selection_effect": 0.006,
                "total_interaction_effect": 0.0,
            }
            mock_b_result.to_markdown.return_value = "### Brinson 完整内容"
            mock_b_mgr.attribute.return_value = mock_b_result
            mock_b.return_value = mock_b_mgr

            mock_f_mgr = MagicMock()
            mock_f_result = MagicMock()
            mock_f_result.status = "ok"
            mock_f_result.to_dict.return_value = {
                "status": "ok",
                "total_pnl": 15000.0,
                "factor_pnl": 12000.0,
                "specific_pnl": 3000.0,
                "active_return": 0.015,
                "active_risk": 0.06,
                "information_ratio": 0.25,
            }
            mock_f_result.to_markdown.return_value = "### Factor 完整内容"
            mock_f_mgr.attribute.return_value = mock_f_result
            mock_f.return_value = mock_f_mgr

            report = panel.generate(
                attribution_date="2026-07-27",
                brinson_input=brinson_input,
                factor_input=factor_input,
                tca_input=tca_input,
            )
            paths = panel.save(report, report_dir=tmp_path)

        # 验证报告内容
        assert report.status == STATUS_OK
        assert report.total_pnl == 15000.0
        assert report.active_return == 0.015
        assert report.information_ratio == 0.25
        assert report.tca_dict["total_pnl"] == 5000.0
        assert report.tca_dict["alpha_pnl"] == 3000.0

        # 验证 JSON 文件可重新加载
        with open(paths["json"], encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded["attribution_date"] == "2026-07-27"
        assert loaded["summary"]["total_pnl"] == 15000.0
        assert loaded["tca"]["alpha_pnl"] == 3000.0
        assert loaded["metadata"]["status"] == "ok"

        # 验证 Markdown 文件含三章节
        with open(paths["markdown"], encoding="utf-8") as f:
            md = f.read()
        assert "Brinson 归因" in md
        assert "Barra 因子归因" in md
        assert "TCA 执行归因" in md
        assert "15000.00" in md  # total_pnl

    def test_residual_validation_in_tca_aggregation(self):
        """TCA 聚合时残差验证生效."""
        panel = DailyAttributionPanel(
            config={
                "settings": {
                    "enable_brinson": False,
                    "enable_factor": False,
                    "enable_tca": True,
                },
                "aggregation": {"residual_tolerance": 1e-9},
            }
        )
        # 故意构造残差大的 summary
        summary = {
            "total_pnl": 1000.0,
            "alpha_pnl": 500.0,
            "execution_pnl": 200.0,
            "risk_pnl": 100.0,  # 和 = 800, 残差 = 200
        }
        result = panel._aggregate_tca(TCAInput(summary_dict=summary))
        # 残差被记录, 但不抛异常
        assert abs(result["residual"] - 200.0) < 1e-3

    def test_module_independence(self):
        """三个子模块互相独立, 单一失败不影响其他."""
        panel = DailyAttributionPanel(
            config={
                "settings": {
                    "enable_brinson": True,
                    "enable_factor": True,
                    "enable_tca": True,
                }
            }
        )
        # Brinson 输入有效, Factor 输入无效 (空), TCA 输入有效
        brinson_input = BrinsonInput(
            portfolio_weights={"a": 1.0},
            portfolio_returns={"a": 0.01},
        )
        tca_input = TCAInput(summary_dict={"total_pnl": 100.0})

        with (
            patch.object(panel, "_is_feature_flag_enabled", return_value=True),
            patch.object(panel, "_init_brinson_manager") as mock_b,
        ):
            mock_b_mgr = MagicMock()
            mock_b_result = MagicMock()
            mock_b_result.status = "ok"
            mock_b_result.to_dict.return_value = {"status": "ok"}
            mock_b_result.to_markdown.return_value = "Brinson OK"
            mock_b_mgr.attribute.return_value = mock_b_result
            mock_b.return_value = mock_b_mgr

            report = panel.generate(
                attribution_date="2026-07-27",
                brinson_input=brinson_input,
                factor_input=None,  # 无 factor 输入
                tca_input=tca_input,
            )

        # Brinson 和 TCA 正常, Factor 为 empty_input
        assert report.module_statuses[0].status == MODULE_STATUS_OK
        assert report.module_statuses[1].status == MODULE_STATUS_EMPTY_INPUT
        assert report.module_statuses[2].status == MODULE_STATUS_OK
        assert report.status == STATUS_PARTIAL
