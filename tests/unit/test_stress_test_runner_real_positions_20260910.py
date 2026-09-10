"""test_stress_test_runner_real_positions_20260910.py — D1 压力测试空仓缺陷回归

缺陷 (2026-09-10, assert_data_validity D1 FAIL):
    reports/stress_test_20260909.json 四个场景 ``actual_pnl`` 全为 0,
    ``asset_class_pnl={"other": 0.0}`` —— 26 个真实持仓一个都没被识别出资产类别。

根因 (两层, 缺一不可):
    1. ``utils/stress_test_runner.py`` 的 CLI 只把 positions.json 的 ``style``
       (行业名"科技/宽基/金融") 塞进 ``strategy``, **未透传 ``type``** (ETF/STOCK);
    2. ``_run_scenario`` 仅按 ``strategy`` 里的英文关键词 + 一份很窄的 style 白名单
       判断资产类别 → 全部落 ``other``、``impact_pct=0`` → ``actual_pnl`` 恒为 0。

修复:
    - 新增 ``classify_asset_class()`` 确定性映射
      (显式 asset_class > strategy 关键词 > type > style/sector > 名称含 ETF);
    - 新增 ``build_positions_from_positions_json()`` 透传 type/style/sector;
    - 未分类金额写入报告 ``unclassified_amount`` 并告警, 不再静默记 0 损益;
    - CLI 补 ``sys.path`` 项目根引导, 使 ``python utils/stress_test_runner.py`` 可直跑。

本文件即"修复前会失败"的回归用例: 回滚上述任一改动 → 必红。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.stress_test_runner import (
    ASSET_CLASSES,
    StressTestRunner,
    build_positions_from_positions_json,
    classify_asset_class,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POSITIONS_FILE = PROJECT_ROOT / "config" / "positions.json"


@pytest.fixture(autouse=True)
def _isolate_report_dir(tmp_path, monkeypatch):
    """报告目录重定向到 tmp_path — 单测绝不能写 reports/ 生产目录。

    2026-09-10 实证: 兄弟用例把 ``stress_test_{today}.json`` 写成空场景,
    覆盖真实报告并使 assert_data_validity D1 变成空场景假 PASS。
    """
    monkeypatch.setattr("utils.stress_test_runner.REPORT_DIR", tmp_path)

# config/positions.json 实测出现过的全部 style 值 (2026-09-10)
_REAL_STYLES = (
    "科技",
    "宽基",
    "金融",
    "新能源",
    "医药",
    "资源",
    "制造",
    "顺周期",
    "防御",
    "成长",
    "国债",
)


# ============================================================
# 分类器 — 真实 positions.json 字段形态
# ============================================================


class TestClassifyRealPositionsJsonShape:
    @pytest.fixture
    def runner(self):
        return StressTestRunner()

    @pytest.mark.unit
    def test_etf_type_with_sector_style_is_etf(self):
        """type=ETF + style=科技 (真实持仓形态) → etf, 不得落 other。"""
        pos = {"type": "ETF", "style": "科技", "name": "科创50ETF易方达"}
        assert classify_asset_class(pos) == "etf"

    @pytest.mark.unit
    def test_stock_type_with_sector_style_is_stock(self):
        pos = {"type": "STOCK", "style": "科技", "name": "某某科技"}
        assert classify_asset_class(pos) == "stock"

    @pytest.mark.unit
    @pytest.mark.parametrize("style", _REAL_STYLES)
    def test_every_real_style_maps_to_known_class(self, style):
        """positions.json 出现过的每个 style 都必须落到已知资产类别。"""
        pos = {"type": "ETF", "style": style, "name": f"{style}ETF"}
        assert classify_asset_class(pos) in ASSET_CLASSES

    @pytest.mark.unit
    def test_backward_compat_sector_style_without_type(self):
        """保持既有语义 (见 test_stress_test_runner_unit): 无 type 时 style=科技 → stock。"""
        assert classify_asset_class({"strategy": "", "style": "科技"}) == "stock"

    @pytest.mark.unit
    def test_strategy_keywords_still_win(self):
        """既有调用方传 strategy='stock_long' 等仍须识别 (向后兼容)。"""
        assert classify_asset_class({"strategy": "stock_long"}) == "stock"
        assert classify_asset_class({"strategy": "etf"}) == "etf"
        assert classify_asset_class({"strategy": "quant_neutral"}) == "quant_neutral"
        assert classify_asset_class({"strategy": "options_tail"}) == "options_tail"
        assert classify_asset_class({"strategy": "futures_hedge"}) == "futures_hedge"
        assert classify_asset_class({"strategy": "cash"}) == "cash"

    @pytest.mark.unit
    def test_explicit_asset_class_wins(self):
        assert classify_asset_class({"asset_class": "cash", "type": "ETF"}) == "cash"

    @pytest.mark.unit
    def test_unknown_falls_to_other(self):
        assert classify_asset_class({"type": "MYSTERY", "style": "未定义行业"}) == "other"


# ============================================================
# 映射函数 — 回归点: 原 CLI 不传 type
# ============================================================


class TestBuildPositionsFromPositionsJson:
    @pytest.mark.unit
    def test_passes_type_and_style_through(self):
        data = {
            "meta": {"total_capital": 5_000_000},
            "positions": {
                "588080.SH": {
                    "name": "科创50ETF易方达",
                    "amount": 27_003.6,
                    "type": "ETF",
                    "style": "科技",
                    "sector": "科技",
                },
            },
        }
        positions, value = build_positions_from_positions_json(data)
        assert value == 5_000_000
        assert len(positions) == 1
        assert positions[0]["type"] == "ETF"
        assert positions[0]["style"] == "科技"
        # 关键断言: 透传后必须能分类成功
        assert classify_asset_class(positions[0]) == "etf"

    @pytest.mark.unit
    def test_skips_zero_and_negative_amount(self):
        data = {
            "positions": {
                "a": {"amount": 0, "type": "ETF"},
                "b": {"amount": -1, "type": "ETF"},
                "c": {"amount": 100, "type": "STOCK"},
            }
        }
        positions, _ = build_positions_from_positions_json(data)
        assert [p["code"] for p in positions] == ["c"]

    @pytest.mark.unit
    def test_default_portfolio_value_when_meta_missing(self):
        positions, value = build_positions_from_positions_json(
            {"positions": {"a": {"amount": 1, "type": "ETF"}}}
        )
        assert positions and value == 5_000_000

    @pytest.mark.unit
    def test_tolerates_non_dict_entry(self):
        positions, _ = build_positions_from_positions_json(
            {"positions": {"a": None, "b": {"amount": 1, "type": "ETF"}}}
        )
        assert [p["code"] for p in positions] == ["b"]


# ============================================================
# 端到端 — 真实 positions.json 必须产出非零 pnl (D1 判据)
# ============================================================


class TestRealPositionsProduceNonzeroPnl:
    @pytest.fixture
    def runner(self):
        return StressTestRunner()

    @pytest.mark.unit
    def test_real_positions_json_nonzero_actual_pnl(self, runner):
        """D1 回归: 真实 positions.json → 四个场景 actual_pnl 均非 0。"""
        if not POSITIONS_FILE.exists():
            pytest.skip("config/positions.json 不存在")
        data = json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
        positions, value = build_positions_from_positions_json(data)
        assert positions, "真实持仓不应为空"

        result = runner.run_all_scenarios(positions, value, is_simulated=False)
        for sid, sres in result["scenarios"].items():
            assert sres["actual_pnl"] != 0, f"{sid} actual_pnl 为 0 (资产类别分类未生效)"

    @pytest.mark.unit
    def test_real_positions_have_no_unclassified_amount(self, runner):
        """真实持仓不得出现未分类金额 (否则说明映射表与 positions.json 脱节)。"""
        if not POSITIONS_FILE.exists():
            pytest.skip("config/positions.json 不存在")
        data = json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
        positions, value = build_positions_from_positions_json(data)
        result = runner.run_all_scenarios(positions, value, is_simulated=False)
        assert result["unclassified_amount"] == 0
        for sid, sres in result["scenarios"].items():
            assert sres["unclassified_amount"] == 0, f"{sid} 存在未分类持仓"

    @pytest.mark.unit
    def test_report_carries_scope_and_unclassified_fields(self, runner):
        """报告契约: 必须带 unclassified_amount / positions_count / positions_amount。"""
        if not POSITIONS_FILE.exists():
            pytest.skip("config/positions.json 不存在")
        data = json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
        positions, value = build_positions_from_positions_json(data)
        result = runner.run_all_scenarios(positions, value, is_simulated=False)
        for key in ("unclassified_amount", "positions_count", "positions_amount", "scope_note"):
            assert key in result, f"报告缺字段 {key}"
        assert result["positions_count"] == len(positions)

        saved = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
        assert "unclassified_amount" in saved
        assert saved["is_simulated"] is False


# ============================================================
# 未分类持仓必须显式暴露, 不得静默记 0 损益
# ============================================================


class TestUnclassifiedIsNotSilent:
    @pytest.fixture
    def runner(self):
        return StressTestRunner()

    @pytest.mark.unit
    def test_unclassified_amount_surfaced(self, runner):
        positions = [
            {"code": "x", "amount": 1_000_000, "type": "MYSTERY", "style": "未定义行业"}
        ]
        result = runner.run_all_scenarios(positions, 1_000_000)
        assert result["unclassified_amount"] == 1_000_000
        for sres in result["scenarios"].values():
            assert sres["unclassified_amount"] == 1_000_000
            assert sres["asset_class_pnl"].get("other") == 0.0

    @pytest.mark.unit
    def test_mixed_positions_only_unclassified_part_reported(self, runner):
        positions = [
            {"code": "ok", "amount": 600_000, "type": "ETF", "style": "科技"},
            {"code": "bad", "amount": 400_000, "type": "MYSTERY", "style": "未定义"},
        ]
        result = runner.run_all_scenarios(positions, 1_000_000)
        assert result["unclassified_amount"] == 400_000
        assert result["positions_amount"] == 1_000_000


# ============================================================
# D1 门禁负向验证 — 空场景报告必须 FAIL, 不得静默 PASS
# ============================================================


def _load_assert_data_validity():
    """按文件路径加载 scripts/assert_data_validity.py (scripts 非包)。"""
    import importlib.util

    path = PROJECT_ROOT / "scripts" / "assert_data_validity.py"
    spec = importlib.util.spec_from_file_location("_adv_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestD1GateRejectsEmptyScenarios:
    """空场景 = 空产物/被覆盖 → D1 必须 FAIL (修复前为静默假 PASS)。"""

    @pytest.fixture
    def _reports_dir(self, tmp_path, monkeypatch):
        adv = _load_assert_data_validity()
        reports = tmp_path / "reports"
        reports.mkdir()
        monkeypatch.setattr(adv, "_PROJECT_ROOT", tmp_path)
        return adv, reports

    @pytest.mark.unit
    def test_empty_scenarios_report_fails(self, _reports_dir):
        adv, reports = _reports_dir
        (reports / "stress_test_20260910.json").write_text(
            json.dumps(
                {"timestamp": "2026-09-10", "is_simulated": False, "scenarios": {}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        result = adv.check_d1_stress_test_nonzero("20260910")
        assert result.passed is False
        assert "无任何场景" in result.detail

    @pytest.mark.unit
    def test_all_zero_scenarios_report_fails(self, _reports_dir):
        adv, reports = _reports_dir
        payload = {
            "timestamp": "2026-09-10",
            "is_simulated": False,
            "scenarios": {
                "crash_2015": {"actual_pnl": 0},
                "v_shock_2020": {"actual_pnl": 0},
            },
        }
        (reports / "stress_test_20260910.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        result = adv.check_d1_stress_test_nonzero("20260910")
        assert result.passed is False

    @pytest.mark.unit
    def test_nonzero_scenarios_report_passes(self, _reports_dir):
        adv, reports = _reports_dir
        payload = {
            "timestamp": "2026-09-10",
            "is_simulated": False,
            "scenarios": {"crash_2015": {"actual_pnl": -982916.37}},
        }
        (reports / "stress_test_20260910.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        result = adv.check_d1_stress_test_nonzero("20260910")
        assert result.passed is True

    @pytest.mark.unit
    def test_simulated_report_is_skipped_not_failed(self, _reports_dir):
        """--simulate 报告按既有约定跳过 (不阻断), 但仍须先排除候选。"""
        adv, reports = _reports_dir
        (reports / "stress_test_SIMULATED_20260910.json").write_text(
            json.dumps(
                {"timestamp": "2026-09-10", "is_simulated": True, "scenarios": {}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (reports / "stress_test_20260910.json").write_text(
            json.dumps(
                {
                    "timestamp": "2026-09-10",
                    "is_simulated": False,
                    "scenarios": {"crash_2015": {"actual_pnl": -1.0}},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        result = adv.check_d1_stress_test_nonzero("20260910")
        assert result.passed is True
