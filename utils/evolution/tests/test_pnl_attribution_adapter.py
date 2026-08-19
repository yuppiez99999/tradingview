"""T2.2 P&L 归因适配器测试 — 将归因结果接入 FeedbackLoop.

任务编号: T2.2 (Phase 2 反馈闭环)
验收标准 (TASK T2.2):
    1. 能从 reports/attribution/daily_*.json 读取因子贡献度
    2. 贡献度归一化后传入权重更新算法
    3. 归因数据缺失时降级为中性 (不调整)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from utils.evolution.pnl_attribution_adapter import (
    STATUS_DEGRADED,
    STATUS_EMPTY,
    STATUS_OK,
    AttributionConversionResult,
    PnLAttributionAdapter,
    convert_to_feedback_loop_format,
    from_attribution_result,
    from_factor_attribution_result,
    from_report_file,
)

# ============================================================
# Mock 归因结果对象
# ============================================================


@dataclass
class MockFactorAttribution:
    """模拟 FactorAttribution (单因子归因结果)."""

    factor_name: str = ""
    factor_category: str = "style"
    contribution_to_pnl: float = 0.0
    portfolio_exposure: float = 0.0
    benchmark_exposure: float = 0.0
    active_exposure: float = 0.0
    factor_return: float = 0.0
    contribution_pct: float = 0.0
    is_significant: bool = False
    is_concentrated: bool = False
    is_missing: bool = False
    ic_metrics: dict[str, Any] | None = None


@dataclass
class MockFactorAttributionResult:
    """模拟 FactorAttributionResult."""

    style_factor_attributions: list[MockFactorAttribution] = field(default_factory=list)
    sector_factor_attributions: list[MockFactorAttribution] = field(default_factory=list)
    total_pnl: float = 0.0
    attribution_date: str = ""
    portfolio_value: float = 1_000_000.0
    total_return_pct: float = 0.0
    active_return: float = 0.0
    factor_pnl: float = 0.0
    specific_pnl: float = 0.0
    residual: float = 0.0
    status: str = "ok"
    reason: str = ""
    benchmark_code: str = "hs300"


@dataclass
class MockFactorContribution:
    """模拟 FactorContribution (PnLAttributionEngine 输出)."""

    factor_name: str = ""
    contribution: float = 0.0
    contribution_pct: float = 0.0
    exposure: float = 0.0
    factor_return: float = 0.0
    is_significant: bool = False


@dataclass
class MockAttributionResult:
    """模拟 AttributionResult (PnLAttributionEngine 输出)."""

    factor_contributions: list[MockFactorContribution] = field(default_factory=list)
    total_pnl: float = 0.0
    attribution_date: str = ""
    alpha: float = 0.0
    beta: float = 0.0
    style_pnl: float = 0.0
    sector_pnl: float = 0.0
    timing_pnl: float = 0.0
    hedge_cost: float = 0.0
    trading_cost: float = 0.0
    funding_cost: float = 0.0


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def tmp_report_dir(tmp_path: Path) -> Path:
    """临时报告目录."""
    report_dir = tmp_path / "attribution"
    report_dir.mkdir(parents=True)
    return report_dir


@pytest.fixture
def adapter(tmp_report_dir: Path) -> PnLAttributionAdapter:
    """带临时报告目录的适配器."""
    return PnLAttributionAdapter(report_dir=tmp_report_dir)


def make_style_fa(name: str, contrib: float) -> MockFactorAttribution:
    """构造风格因子归因."""
    return MockFactorAttribution(
        factor_name=name,
        factor_category="style",
        contribution_to_pnl=contrib,
    )


def make_sector_fa(name: str, contrib: float) -> MockFactorAttribution:
    """构造行业因子归因."""
    return MockFactorAttribution(
        factor_name=name,
        factor_category="sector",
        contribution_to_pnl=contrib,
    )


def make_factor_result(
    style: dict[str, float] | None = None,
    sector: dict[str, float] | None = None,
    total_pnl: float = 0.0,
    date: str = "2026-08-02",
) -> MockFactorAttributionResult:
    """构造完整的 MockFactorAttributionResult."""
    style_factors = [make_style_fa(k, v) for k, v in (style or {}).items()]
    sector_factors = [make_sector_fa(k, v) for k, v in (sector or {}).items()]
    return MockFactorAttributionResult(
        style_factor_attributions=style_factors,
        sector_factor_attributions=sector_factors,
        total_pnl=total_pnl,
        attribution_date=date,
    )


# ============================================================
# 测试1: 核心转换函数 convert_to_feedback_loop_format
# ============================================================


class TestConvertToFeedbackLoopFormat:
    """核心转换函数测试."""

    def test_style_only(self):
        """仅风格因子: 添加 style_ 前缀."""
        result = convert_to_feedback_loop_format(
            style_contributions={"momentum": 0.001, "value": -0.0005},
        )
        assert result.status == STATUS_OK
        assert result.n_factors == 2
        assert result.n_style_factors == 2
        assert result.n_sector_factors == 0
        assert result.factor_contributions["style_momentum"] == pytest.approx(0.001)
        assert result.factor_contributions["style_value"] == pytest.approx(-0.0005)

    def test_sector_only(self):
        """仅行业因子: 添加 sector_ 前缀."""
        result = convert_to_feedback_loop_format(
            sector_contributions={"tech": 0.002, "defensive": -0.001},
        )
        assert result.status == STATUS_OK
        assert result.n_factors == 2
        assert result.n_sector_factors == 2
        assert result.factor_contributions["sector_tech"] == pytest.approx(0.002)

    def test_mixed_style_and_sector(self):
        """混合风格和行业因子."""
        result = convert_to_feedback_loop_format(
            style_contributions={"momentum": 0.001},
            sector_contributions={"tech": 0.002},
        )
        assert result.status == STATUS_OK
        assert result.n_factors == 2
        assert result.n_style_factors == 1
        assert result.n_sector_factors == 1
        assert "style_momentum" in result.factor_contributions
        assert "sector_tech" in result.factor_contributions

    def test_total_pnl_preserved(self):
        """total_pnl 和 attribution_date 透传."""
        result = convert_to_feedback_loop_format(
            style_contributions={"m": 0.001},
            total_pnl=1234.56,
            attribution_date="2026-08-02",
        )
        assert result.total_pnl == pytest.approx(1234.56)
        assert result.attribution_date == "2026-08-02"

    def test_empty_inputs_returns_empty_status(self):
        """空输入: 返回 status=empty."""
        result = convert_to_feedback_loop_format()
        assert result.status == STATUS_EMPTY
        assert "为空" in result.reason
        assert result.factor_contributions == {}

    def test_null_inputs_returns_empty(self):
        """None 输入: 视为空."""
        result = convert_to_feedback_loop_format(
            style_contributions=None,
            sector_contributions=None,
        )
        assert result.status == STATUS_EMPTY

    def test_skip_empty_factor_name(self):
        """空因子名跳过."""
        result = convert_to_feedback_loop_format(
            style_contributions={"": 0.001, "valid": 0.002},
        )
        # 空名被跳过, 只有 valid
        assert result.n_factors == 1
        assert "style_" not in result.factor_contributions  # 空名被跳过
        assert result.factor_contributions["style_valid"] == pytest.approx(0.002)

    def test_custom_prefix(self):
        """自定义前缀."""
        result = convert_to_feedback_loop_format(
            style_contributions={"m": 0.001},
            prefix_style="custom_",
            prefix_sector="cs_",
        )
        assert "custom_m" in result.factor_contributions

    def test_to_feedback_loop_format_returns_dict(self):
        """to_feedback_loop_format() 返回可用的 dict 格式."""
        result = convert_to_feedback_loop_format(
            style_contributions={"m": 0.001, "v": 0.002},
        )
        fc = result.to_feedback_loop_format()
        assert isinstance(fc, dict)
        assert fc["style_m"] == pytest.approx(0.001)
        assert fc["style_v"] == pytest.approx(0.002)

    def test_to_dict_serializable(self):
        """to_dict() 输出可序列化."""
        result = convert_to_feedback_loop_format(
            style_contributions={"m": 0.001},
            total_pnl=1000.0,
            attribution_date="2026-08-02",
        )
        d = result.to_dict()
        assert d["total_pnl"] == pytest.approx(1000.0)
        assert d["attribution_date"] == "2026-08-02"
        assert d["n_factors"] == 1
        # 可 JSON 序列化
        json.dumps(d)


# ============================================================
# 测试2: 从 FactorAttributionResult 转换
# ============================================================


class TestFromFactorAttributionResult:
    """从 FactorAttributionResult 转换."""

    def test_style_and_sector(self):
        """同时提取风格和行业因子."""
        result = make_factor_result(
            style={"momentum": 0.001, "value": -0.0005},
            sector={"tech": 0.002},
            total_pnl=5000.0,
            date="2026-08-02",
        )
        conv = from_factor_attribution_result(result)
        assert conv.status == STATUS_OK
        assert conv.n_factors == 3
        assert conv.n_style_factors == 2
        assert conv.n_sector_factors == 1
        assert conv.factor_contributions["style_momentum"] == pytest.approx(0.001)
        assert conv.factor_contributions["style_value"] == pytest.approx(-0.0005)
        assert conv.factor_contributions["sector_tech"] == pytest.approx(0.002)
        assert conv.total_pnl == pytest.approx(5000.0)
        assert conv.attribution_date == "2026-08-02"

    def test_none_input_returns_degraded(self):
        """None 输入: 返回 degraded."""
        conv = from_factor_attribution_result(None)
        assert conv.status == STATUS_DEGRADED
        assert "result is None" in conv.reason

    def test_empty_attributions_returns_empty(self):
        """空归因列表: 返回 empty."""
        result = make_factor_result()
        conv = from_factor_attribution_result(result)
        assert conv.status == STATUS_EMPTY
        assert conv.n_factors == 0

    def test_overrides_attribution_date(self):
        """覆盖归因日期."""
        result = make_factor_result(
            style={"m": 0.001},
            date="2026-08-01",
        )
        conv = from_factor_attribution_result(result, attribution_date="2026-08-02")
        assert conv.attribution_date == "2026-08-02"

    def test_uses_result_date_when_no_override(self):
        """未覆盖时使用结果中的日期."""
        result = make_factor_result(
            style={"m": 0.001},
            date="2026-08-01",
        )
        conv = from_factor_attribution_result(result)
        assert conv.attribution_date == "2026-08-01"

    def test_dict_input(self):
        """字典格式输入."""
        data = {
            "style_factor_attributions": [
                {"factor_name": "momentum", "contribution_to_pnl": 0.001},
            ],
            "sector_factor_attributions": [
                {"factor_name": "tech", "contribution_to_pnl": 0.002},
            ],
            "total_pnl": 3000.0,
            "attribution_date": "2026-08-02",
        }
        conv = from_factor_attribution_result(data)
        assert conv.status == STATUS_OK
        assert conv.n_factors == 2
        assert conv.total_pnl == pytest.approx(3000.0)

    def test_dict_empty_attributions(self):
        """字典格式空归因."""
        data = {"style_factor_attributions": [], "sector_factor_attributions": []}
        conv = from_factor_attribution_result(data)
        assert conv.status == STATUS_EMPTY

    def test_source_field_set(self):
        """source 字段正确标识."""
        result = make_factor_result(style={"m": 0.001})
        conv = from_factor_attribution_result(result)
        assert conv.source == "from_factor_attribution_result"

    def test_is_degraded_false_on_ok(self):
        """status=ok 时 is_degraded() 返回 False."""
        result = make_factor_result(style={"m": 0.001})
        conv = from_factor_attribution_result(result)
        assert conv.is_degraded() is False

    def test_is_degraded_true_on_none(self):
        """None 输入时 is_degraded() 返回 True."""
        conv = from_factor_attribution_result(None)
        assert conv.is_degraded() is True


# ============================================================
# 测试3: 从报告文件读取
# ============================================================


class TestFromReportFile:
    """从归因报告文件读取."""

    def test_read_flat_format(self, tmp_report_dir: Path):
        """读取扁平化 factor_contributions 格式."""
        data = {
            "factor_contributions": {
                "style_momentum": 0.001,
                "style_value": -0.0005,
                "sector_tech": 0.002,
            },
            "total_pnl": 5000.0,
            "attribution_date": "2026-08-02",
        }
        file_path = tmp_report_dir / "daily_2026-08-02.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

        conv = from_report_file(file_path)
        assert conv.status == STATUS_OK
        assert conv.n_factors == 3
        assert conv.factor_contributions["style_momentum"] == pytest.approx(0.001)
        assert conv.factor_contributions["sector_tech"] == pytest.approx(0.002)
        assert conv.total_pnl == pytest.approx(5000.0)

    def test_read_factor_attribution_format(self, tmp_report_dir: Path):
        """读取 FactorAttributionResult 格式."""
        data = {
            "style_factor_attributions": [
                {"factor_name": "momentum", "contribution_to_pnl": 0.001},
            ],
            "sector_factor_attributions": [
                {"factor_name": "tech", "contribution_to_pnl": 0.002},
            ],
            "total_pnl": 3000.0,
            "attribution_date": "2026-08-02",
        }
        file_path = tmp_report_dir / "factor_attribution_2026-08-02.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

        conv = from_report_file(file_path)
        assert conv.status == STATUS_OK
        assert conv.n_factors == 2
        assert conv.total_pnl == pytest.approx(3000.0)

    def test_file_not_found(self):
        """文件不存在: 返回 degraded."""
        conv = from_report_file("/nonexistent/path.json")
        assert conv.status == STATUS_DEGRADED
        assert "文件不存在" in conv.reason

    def test_invalid_json(self, tmp_report_dir: Path):
        """无效 JSON: 返回 degraded."""
        file_path = tmp_report_dir / "bad.json"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("not json")

        conv = from_report_file(file_path)
        assert conv.status == STATUS_DEGRADED
        assert "读取失败" in conv.reason

    def test_empty_flat_format(self, tmp_report_dir: Path):
        """扁平格式空贡献: 返回 empty."""
        data = {
            "factor_contributions": {},
            "total_pnl": 0.0,
            "attribution_date": "2026-08-02",
        }
        file_path = tmp_report_dir / "empty.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

        conv = from_report_file(file_path)
        assert conv.status == STATUS_EMPTY


# ============================================================
# 测试4: 从 AttributionResult 转换 (PnLAttributionEngine)
# ============================================================


class TestFromAttributionResult:
    """从 PnLAttributionEngine 的 AttributionResult 转换."""

    def test_factor_contributions_extracted(self):
        """提取 factor_contributions 列表."""
        result = MockAttributionResult(
            factor_contributions=[
                MockFactorContribution(factor_name="momentum", contribution=0.001),
                MockFactorContribution(factor_name="value", contribution=-0.0005),
            ],
            total_pnl=2000.0,
            attribution_date="2026-08-02",
        )
        conv = from_attribution_result(result)
        assert conv.status == STATUS_OK
        assert conv.n_factors == 2
        assert conv.factor_contributions["momentum"] == pytest.approx(0.001)
        assert conv.factor_contributions["value"] == pytest.approx(-0.0005)
        assert conv.total_pnl == pytest.approx(2000.0)

    def test_none_input_returns_degraded(self):
        """None 输入: 返回 degraded."""
        conv = from_attribution_result(None)
        assert conv.status == STATUS_DEGRADED
        assert "result is None" in conv.reason

    def test_empty_contributions_returns_empty(self):
        """空贡献列表: 返回 empty."""
        result = MockAttributionResult(factor_contributions=[])
        conv = from_attribution_result(result)
        assert conv.status == STATUS_EMPTY

    def test_dict_input(self):
        """字典格式输入."""
        data = {
            "factor_contributions": [
                {"factor_name": "momentum", "contribution": 0.001},
            ],
            "total_pnl": 1000.0,
            "attribution_date": "2026-08-02",
        }
        conv = from_attribution_result(data)
        assert conv.status == STATUS_OK
        assert conv.n_factors == 1
        assert conv.factor_contributions["momentum"] == pytest.approx(0.001)


# ============================================================
# 测试5: 适配器类 (行为)
# ============================================================


class TestPnLAttributionAdapter:
    """适配器类行为测试."""

    def test_load_from_report_found(self, tmp_report_dir: Path, adapter: PnLAttributionAdapter):
        """load_from_report 找到文件."""
        data = {
            "factor_contributions": {"style_momentum": 0.001},
            "total_pnl": 1000.0,
            "attribution_date": "2026-08-02",
        }
        file_path = tmp_report_dir / "daily_2026-08-02.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

        conv = adapter.load_from_report("2026-08-02")
        assert conv.status == STATUS_OK
        assert conv.n_factors == 1

    def test_load_from_report_not_found(self, adapter: PnLAttributionAdapter):
        """load_from_report 文件不存在: 返回 degraded."""
        conv = adapter.load_from_report("2099-01-01")
        assert conv.status == STATUS_DEGRADED
        assert "未找到归因报告" in conv.reason

    def test_load_from_result_factor_attribution(self, adapter: PnLAttributionAdapter):
        """load_from_result 从 FactorAttributionResult 加载."""
        result = make_factor_result(style={"m": 0.001})
        conv = adapter.load_from_result(result, source="factor_attribution")
        assert conv.status == STATUS_OK
        assert conv.n_factors == 1

    def test_load_from_result_pnl_engine(self, adapter: PnLAttributionAdapter):
        """load_from_result 从 AttributionResult 加载."""
        result = MockAttributionResult(
            factor_contributions=[
                MockFactorContribution(factor_name="alpha", contribution=0.002),
            ],
        )
        conv = adapter.load_from_result(result, source="pnl_engine")
        assert conv.status == STATUS_OK

    def test_load_dict(self, adapter: PnLAttributionAdapter):
        """load_dict 直接加载贡献字典."""
        conv = adapter.load_dict(
            {"style_momentum": 0.001, "style_value": -0.0005},
            total_pnl=3000.0,
            attribution_date="2026-08-02",
        )
        assert conv.status == STATUS_OK
        assert conv.n_factors == 2
        assert conv.total_pnl == pytest.approx(3000.0)

    def test_get_latest(self, adapter: PnLAttributionAdapter):
        """get_latest 返回最近一次转换."""
        adapter.load_dict({"a": 0.001}, attribution_date="2026-08-01")
        adapter.load_dict({"b": 0.002}, attribution_date="2026-08-02")
        latest = adapter.get_latest()
        assert latest is not None
        assert latest.attribution_date == "2026-08-02"

    def test_get_latest_empty(self, adapter: PnLAttributionAdapter):
        """无历史时 get_latest 返回 None."""
        assert adapter.get_latest() is None

    def test_history_max_size(self, adapter: PnLAttributionAdapter):
        """历史记录受 max_history 限制."""
        for i in range(40):
            adapter.load_dict({f"f{i}": 0.001}, attribution_date=f"2026-08-{(i % 30) + 1:02d}")
        # max_history=30, 所以历史记录数 ≤ 30
        assert len(adapter.history) <= 30

    def test_clear_history(self, adapter: PnLAttributionAdapter):
        """clear_history 清空历史."""
        adapter.load_dict({"a": 0.001})
        adapter.clear_history()
        assert len(adapter.history) == 0

    def test_report_dir_property(self, tmp_report_dir: Path):
        """report_dir 属性."""
        adapter = PnLAttributionAdapter(report_dir=tmp_report_dir)
        assert adapter.report_dir == tmp_report_dir

    def test_try_multiple_paths(self, tmp_report_dir: Path, adapter: PnLAttributionAdapter):
        """尝试多个路径, 找到第一个."""
        # 只创建第三个候选路径的文件
        data = {"factor_contributions": {"m": 0.001}, "total_pnl": 500.0}
        file_path = tmp_report_dir / "2026-08-02.json"  # 第三个候选
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

        conv = adapter.load_from_report("2026-08-02")
        assert conv.status == STATUS_OK
        assert conv.total_pnl == pytest.approx(500.0)

    def test_history_tracks_all(self, adapter: PnLAttributionAdapter):
        """历史记录所有转换."""
        adapter.load_dict({"a": 0.001}, attribution_date="2026-08-01")
        adapter.load_dict({"b": 0.002}, attribution_date="2026-08-02")
        adapter.load_dict({"c": 0.003}, attribution_date="2026-08-03")
        assert len(adapter.history) == 3


# ============================================================
# 测试6: 降级与中性行为
# ============================================================


class TestDegradationBehavior:
    """归因缺失时降级为中性."""

    def test_empty_input_returns_empty_status(self):
        """空输入: status=empty, 不抛异常."""
        result = convert_to_feedback_loop_format()
        assert result.status == STATUS_EMPTY
        assert result.factor_contributions == {}
        assert result.is_degraded()

    def test_none_factor_result_returns_degraded(self):
        """None FactorAttributionResult: degraded."""
        conv = from_factor_attribution_result(None)
        assert conv.is_degraded()
        assert conv.factor_contributions == {}

    def test_none_attribution_result_returns_degraded(self):
        """None AttributionResult: degraded."""
        conv = from_attribution_result(None)
        assert conv.is_degraded()
        assert conv.factor_contributions == {}

    def test_file_not_found_returns_degraded(self):
        """文件不存在: degraded."""
        conv = from_report_file("/nonexistent.json")
        assert conv.is_degraded()
        assert conv.factor_contributions == {}

    def test_adapter_handles_degraded_gracefully(self, adapter: PnLAttributionAdapter):
        """适配器降级后不崩溃, 可继续使用."""
        conv = adapter.load_from_report("2099-01-01")
        assert conv.is_degraded()

        # 后续调用正常
        conv2 = adapter.load_dict({"m": 0.001})
        assert conv2.status == STATUS_OK

    def test_degraded_to_feedback_loop_format_returns_empty(self):
        """降级结果 to_feedback_loop_format() 返回空字典."""
        conv = from_factor_attribution_result(None)
        assert conv.to_feedback_loop_format() == {}


# ============================================================
# 测试7: 不可变性
# ============================================================


class TestImmutability:
    """对象不可变性."""

    def test_original_result_not_modified(self):
        """原始归因结果不被修改."""
        result = make_factor_result(style={"m": 0.001})
        conv = from_factor_attribution_result(result)
        # 修改转换结果不影响原始对象
        conv.factor_contributions["modified"] = 0.999
        # 原始对象应不受影响
        assert len(result.style_factor_attributions) == 1

    def test_to_dict_isolation(self):
        """to_dict() 返回独立副本."""
        result = convert_to_feedback_loop_format(
            style_contributions={"m": 0.001},
        )
        d = result.to_dict()
        # 修改字典不影响原对象
        d["total_pnl"] = 9999.0
        assert result.total_pnl == pytest.approx(0.0)

    def test_to_feedback_loop_format_isolation(self):
        """to_feedback_loop_format() 返回独立副本."""
        result = convert_to_feedback_loop_format(
            style_contributions={"m": 0.001},
        )
        fc = result.to_feedback_loop_format()
        fc["modified"] = 0.999
        assert "modified" not in result.factor_contributions

    def test_adapter_history_isolation(self, adapter: PnLAttributionAdapter):
        """adapter.history 返回独立副本 (不可变性)."""
        adapter.load_dict({"a": 0.001})
        h = adapter.history
        h.append(AttributionConversionResult())  # 修改副本
        assert len(adapter.history) == 1  # 原对象不受影响


# ============================================================
# 测试8: 集成场景
# ============================================================


class TestIntegration:
    """集成场景测试."""

    def test_full_flow_factor_attribution_to_feedback_loop(self, adapter: PnLAttributionAdapter):
        """完整流程: FactorAttributionResult → FeedbackLoop 输入."""
        # 模拟归因结果
        result = make_factor_result(
            style={"momentum": 0.002, "value": -0.001, "quality": 0.0005},
            sector={"tech": 0.003, "defensive": -0.0005},
            total_pnl=5000.0,
            date="2026-08-02",
        )

        # 1. 适配器转换
        conv = adapter.load_from_result(result, source="factor_attribution")
        assert conv.status == STATUS_OK
        assert conv.n_factors == 5
        assert conv.total_pnl == pytest.approx(5000.0)

        # 2. 转换为 FeedbackLoop 输入格式
        fc = conv.to_feedback_loop_format()
        assert len(fc) == 5
        assert "style_momentum" in fc
        assert "sector_tech" in fc

        # 3. 验证贡献方向
        assert fc["style_momentum"] > 0  # 正贡献 → 增配
        assert fc["style_value"] < 0  # 负贡献 → 减配

    def test_full_flow_report_file_to_feedback_loop(self, tmp_report_dir: Path, adapter: PnLAttributionAdapter):
        """完整流程: 报告文件 → FeedbackLoop 输入."""
        # 1. 写入归因报告
        data = {
            "factor_contributions": {
                "style_momentum": 0.001,
                "style_value": -0.0005,
                "sector_tech": 0.002,
            },
            "total_pnl": 3000.0,
            "attribution_date": "2026-08-02",
        }
        file_path = tmp_report_dir / "daily_2026-08-02.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

        # 2. 适配器读取
        conv = adapter.load_from_report("2026-08-02")
        assert conv.status == STATUS_OK

        # 3. 转换为 FeedbackLoop 输入
        fc = conv.to_feedback_loop_format()
        assert len(fc) == 3

    def test_realistic_multi_factor_attribution(self, adapter: PnLAttributionAdapter):
        """真实场景: 多因子混合归因."""
        result = make_factor_result(
            style={
                "Size": 0.0015,
                "Beta": 0.0008,
                "Momentum": 0.0020,
                "Volatility": -0.0012,
                "Liquidity": -0.0003,
                "EarningsQuality": 0.0010,
                "Growth": 0.0005,
                "Valuation": -0.0008,
            },
            sector={
                "tech": 0.0030,
                "manufacturing": 0.0015,
                "cyclical": -0.0005,
                "resources": 0.0002,
                "defensive": 0.0008,
                "finance": -0.0003,
                "consumer": -0.0010,
                "healthcare": 0.0005,
            },
            total_pnl=8500.0,
            date="2026-08-02",
        )

        conv = from_factor_attribution_result(result)
        assert conv.status == STATUS_OK
        assert conv.n_factors == 16
        assert conv.n_style_factors == 8
        assert conv.n_sector_factors == 8
        assert conv.total_pnl == pytest.approx(8500.0)

        # 验证因子前缀
        fc = conv.to_feedback_loop_format()
        assert "style_Size" in fc
        assert "style_Momentum" in fc
        assert "sector_tech" in fc
        assert "sector_healthcare" in fc

        # 验证正负方向
        assert fc["style_Momentum"] > 0
        assert fc["style_Volatility"] < 0
        assert fc["sector_consumer"] < 0

    def test_attribution_gap_handled_gracefully(self, adapter: PnLAttributionAdapter):
        """归因数据缺失时优雅降级, 不阻塞后续调用."""
        # 1. 第一天: 归因缺失
        conv1 = adapter.load_from_report("2026-08-01")
        assert conv1.is_degraded()

        # 2. 第二天: 归因正常
        conv2 = adapter.load_dict(
            {"style_momentum": 0.001},
            attribution_date="2026-08-02",
        )
        assert conv2.status == STATUS_OK

        # 3. 历史记录包含两次转换
        assert len(adapter.history) == 2


# ============================================================
# 测试9: 边界条件
# ============================================================


class TestEdgeCases:
    """边界条件测试."""

    def test_zero_contributions(self):
        """贡献值全零."""
        result = convert_to_feedback_loop_format(
            style_contributions={"m": 0.0, "v": 0.0},
        )
        assert result.status == STATUS_OK
        assert result.n_factors == 2
        assert result.factor_contributions["style_m"] == pytest.approx(0.0)

    def test_very_large_contributions(self):
        """极大贡献值."""
        result = convert_to_feedback_loop_format(
            style_contributions={"m": 1e6, "v": -1e6},
        )
        assert result.status == STATUS_OK
        assert result.factor_contributions["style_m"] == pytest.approx(1e6)
        assert result.factor_contributions["style_v"] == pytest.approx(-1e6)

    def test_very_small_contributions(self):
        """极小贡献值 (接近零)."""
        result = convert_to_feedback_loop_format(
            style_contributions={"m": 1e-10},
        )
        assert result.status == STATUS_OK
        assert result.factor_contributions["style_m"] == pytest.approx(1e-10)

    def test_many_factors(self):
        """大量因子."""
        style = {f"f{i}": 0.001 * i for i in range(100)}
        result = convert_to_feedback_loop_format(style_contributions=style)
        assert result.status == STATUS_OK
        assert result.n_factors == 100
        assert result.n_style_factors == 100

    def test_factor_name_with_special_chars(self):
        """因子名含特殊字符."""
        result = convert_to_feedback_loop_format(
            style_contributions={"factor-1": 0.001, "factor_2": 0.002, "factor.3": 0.003},
        )
        assert result.status == STATUS_OK
        assert result.factor_contributions["style_factor-1"] == pytest.approx(0.001)
        assert result.factor_contributions["style_factor_2"] == pytest.approx(0.002)
        assert result.factor_contributions["style_factor.3"] == pytest.approx(0.003)

    def test_report_file_non_dict_content(self, tmp_report_dir: Path):
        """报告文件内容非 dict."""
        file_path = tmp_report_dir / "bad.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump([1, 2, 3], f)

        conv = from_report_file(file_path)
        assert conv.status == STATUS_DEGRADED
        assert "非 dict" in conv.reason

    def test_missing_attribution_date_falls_back(self):
        """缺失 attribution_date 时使用默认空字符串."""
        conv = convert_to_feedback_loop_format(
            style_contributions={"m": 0.001},
        )
        assert conv.attribution_date == ""


# ============================================================
# 测试10: AttributionConversionResult 数据类
# ============================================================


class TestAttributionConversionResult:
    """AttributionConversionResult 数据类行为."""

    def test_default_values(self):
        """默认值验证."""
        r = AttributionConversionResult()
        assert r.factor_contributions == {}
        assert r.total_pnl == pytest.approx(0.0)
        assert r.status == STATUS_OK
        assert r.is_degraded() is False

    def test_status_empty_is_degraded(self):
        """status=empty 时 is_degraded() 返回 True."""
        r = AttributionConversionResult(status=STATUS_EMPTY)
        assert r.is_degraded() is True

    def test_status_degraded_is_degraded(self):
        """status=degraded 时 is_degraded() 返回 True."""
        r = AttributionConversionResult(status=STATUS_DEGRADED)
        assert r.is_degraded() is True

    def test_to_dict_rounds_floats(self):
        """to_dict() 对浮点数做 6 位四舍五入."""
        r = AttributionConversionResult(
            factor_contributions={"m": 0.123456789},
            total_pnl=1234.56789,
        )
        d = r.to_dict()
        assert d["factor_contributions"]["m"] == pytest.approx(0.123457, abs=1e-6)
        assert d["total_pnl"] == pytest.approx(1234.57, abs=1e-2)
