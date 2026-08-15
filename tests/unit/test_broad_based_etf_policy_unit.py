"""broad_based_etf_policy 单元测试.

被测模块: utils/broad_based_etf_policy.py
覆盖目标: >=90%

测试宽基ETF政策合规校验、加减仓信号映射、计划应用、国家队资金流接入。
所有外部依赖 (macro_policy_scoring / etf_flow_monitor) 均通过 monkeypatch mock。
"""
from __future__ import annotations

import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import utils.broad_based_etf_policy as bbep  # noqa: E402
from utils.broad_based_etf_policy import (  # noqa: E402
    ADJUST_BANDS,
    BROAD_BASED_ETFS,
    MAX_SCALE,
    MIN_SCALE,
    apply_broad_based_adjustments_to_plan,
    adjust_plan_with_national_team_flow,
    compute_broad_based_adjustments,
    fetch_national_team_flow_signals,
    flow_to_adjustment,
    get_broad_based_codes,
    normalize_code,
    validate_portfolio_compliance,
)


# ============================================================
# 辅助: 构造 mock _MACRO
# ============================================================
def _make_macro_mock(ff_scores, kc_scores, style_weights=None):
    """构造 mock _MACRO 对象.

    Args:
        ff_scores: {code: score} — 对每个查表键, 若键包含某 code 则用其分数
        kc_scores: {code: (cycle_score, note)}
        style_weights: dict
    """
    def score_fifteen_five(all_keys):
        result = {}
        for key in all_keys:
            score = 1.0
            for code, s in ff_scores.items():
                if code in key:
                    score = s
                    break
            result[key] = SimpleNamespace(symbols=[key], score=score)
        return result

    def score_kondratiev(all_keys):
        result = {}
        for key in all_keys:
            for code, (cs, note) in kc_scores.items():
                if code in key:
                    result[key] = SimpleNamespace(cycle_score=cs, note=note)
                    break
        return result

    return SimpleNamespace(
        score_fifteen_five=score_fifteen_five,
        score_kondratiev=score_kondratiev,
        KONDRATIEV_STYLE_WEIGHTS=style_weights or {},
    )


class BroadBasedEtfPolicyTest:
    """broad_based_etf_policy 单元测试."""

    # ------ 常量 ------
    def test_broad_based_etfs_nonempty(self):
        assert len(BROAD_BASED_ETFS) == 4
        codes = {e["code"] for e in BROAD_BASED_ETFS}
        assert codes == {"510300", "510500", "510050", "512100"}

    def test_broad_based_etfs_structure(self):
        for etf in BROAD_BASED_ETFS:
            assert "code" in etf
            assert "name" in etf
            assert "base_weight" in etf
            assert "est_price" in etf
            assert "lots" in etf
            assert etf["style"] == "宽基"

    def test_adjust_bands_count(self):
        assert len(ADJUST_BANDS) == 7

    def test_scale_constants(self):
        assert MIN_SCALE == 0.5
        assert MAX_SCALE == 1.5

    # ------ normalize_code ------
    def test_normalize_code_basic(self):
        result = normalize_code("510300")
        assert result == ["510300", "sz510300", "sh510300", "510300"]

    def test_normalize_code_with_stripped_zeros(self):
        # lstrip("0") 再 zfill(6)
        result = normalize_code("000001")
        assert result[0] == "000001"

    def test_normalize_code_short(self):
        result = normalize_code("510300")
        assert len(result) == 4

    def test_normalize_code_strips_whitespace(self):
        result = normalize_code("  510300  ")
        assert result[0] == "510300"
        # 最后一个元素是 str(code).strip()
        assert result[-1] == "510300"

    # ------ flow_to_adjustment: 各档位 ------
    def test_flow_strong_add(self):
        r = flow_to_adjustment(100.0)
        assert r["action"] == "强加仓"
        assert r["factor"] == 0.30
        assert r["direction"] == "in"

    def test_flow_add(self):
        r = flow_to_adjustment(25.0)
        assert r["action"] == "加仓"
        assert r["factor"] == 0.15

    def test_flow_small_add(self):
        r = flow_to_adjustment(5.0)
        assert r["action"] == "小幅加仓"
        assert r["factor"] == 0.05

    def test_flow_hold(self):
        r = flow_to_adjustment(0.0)
        assert r["action"] == "持有"
        assert r["factor"] == 0.0
        assert r["direction"] == "hold"

    def test_flow_small_reduce(self):
        r = flow_to_adjustment(-5.0)
        assert r["action"] == "小幅减仓"
        assert r["factor"] == -0.05

    def test_flow_reduce(self):
        r = flow_to_adjustment(-25.0)
        assert r["action"] == "减仓"
        assert r["factor"] == -0.15

    def test_flow_strong_reduce(self):
        r = flow_to_adjustment(-100.0)
        assert r["action"] == "强减仓"
        assert r["factor"] == -0.30
        assert r["direction"] == "out"

    # ------ flow_to_adjustment: 边界 ------
    def test_flow_boundary_50(self):
        # 50 是强加仓 lo
        r = flow_to_adjustment(50.0)
        assert r["action"] == "强加仓"

    def test_flow_boundary_10(self):
        r = flow_to_adjustment(10.0)
        assert r["action"] == "加仓"

    def test_flow_boundary_2(self):
        r = flow_to_adjustment(2.0)
        assert r["action"] == "小幅加仓"

    def test_flow_boundary_neg2(self):
        r = flow_to_adjustment(-2.0)
        assert r["action"] == "持有"

    def test_flow_boundary_neg10(self):
        r = flow_to_adjustment(-10.0)
        assert r["action"] == "小幅减仓"

    def test_flow_boundary_neg50(self):
        r = flow_to_adjustment(-50.0)
        assert r["action"] == "减仓"

    # ------ get_broad_based_codes ------
    def test_get_broad_based_codes_by_style(self):
        plan = {
            "target_portfolio": {
                "510300": {"style": "宽基"},
                "600519": {"style": "白酒"},
            }
        }
        codes = get_broad_based_codes(plan)
        assert codes == ["510300"]

    def test_get_broad_based_codes_by_adjustable(self):
        plan = {
            "target_portfolio": {
                "510300": {"style": "宽基"},
                "600519": {"style": "白酒", "adjustable": True},
            }
        }
        codes = get_broad_based_codes(plan)
        assert set(codes) == {"510300", "600519"}

    def test_get_broad_based_codes_empty(self):
        codes = get_broad_based_codes({})
        assert codes == []

    def test_get_broad_based_codes_no_target_portfolio(self):
        codes = get_broad_based_codes({"other_key": {}})
        assert codes == []

    # ------ compute_broad_based_adjustments ------
    def test_compute_adjustments_default_etfs(self):
        flow = {"510300": {"net_flow_yi": 100.0}}
        result = compute_broad_based_adjustments(flow)
        assert len(result) == 4
        for item in result:
            assert "code" in item
            assert "target_weight" in item
            assert "scale" in item

    def test_compute_adjustments_strong_add_scale(self):
        flow = {"510300": {"net_flow_yi": 100.0}}
        result = compute_broad_based_adjustments(flow)
        item300 = next(i for i in result if i["code"] == "510300")
        # factor 0.30 → scale 1.3
        assert item300["scale"] == round(1.3, 4)
        assert item300["target_weight"] == round(0.05 * 1.3, 6)

    def test_compute_adjustments_strong_reduce_scale(self):
        flow = {"510300": {"net_flow_yi": -100.0}}
        result = compute_broad_based_adjustments(flow)
        item300 = next(i for i in result if i["code"] == "510300")
        # factor -0.30 → scale 0.7
        assert item300["scale"] == round(0.7, 4)

    def test_compute_adjustments_hold_scale(self):
        flow = {"510300": {"net_flow_yi": 0.0}}
        result = compute_broad_based_adjustments(flow)
        item300 = next(i for i in result if i["code"] == "510300")
        assert item300["scale"] == 1.0
        assert item300["target_weight"] == 0.05

    def test_compute_adjustments_missing_flow(self):
        # 无资金流数据 → net_flow=0 → 持有
        result = compute_broad_based_adjustments({})
        for item in result:
            assert item["action"] == "持有"
            assert item["net_flow_yi"] == 0.0

    def test_compute_adjustments_custom_broad_based(self):
        custom = [
            {"code": "510300", "name": "测试", "base_weight": 0.10},
        ]
        flow = {"510300": {"net_flow_yi": 100.0}}
        result = compute_broad_based_adjustments(flow, custom)
        assert len(result) == 1
        assert result[0]["code"] == "510300"
        assert result[0]["base_weight"] == 0.10

    def test_compute_adjustments_none_net_flow(self):
        # net_flow_yi 为 None → 0.0
        flow = {"510300": {"net_flow_yi": None}}
        result = compute_broad_based_adjustments(flow)
        item300 = next(i for i in result if i["code"] == "510300")
        assert item300["net_flow_yi"] == 0.0
        assert item300["action"] == "持有"

    # ------ apply_broad_based_adjustments_to_plan ------
    def _make_plan(self):
        return {
            "target_portfolio": {
                "510300": {
                    "name": "沪深300ETF华泰柏瑞",
                    "style": "宽基",
                    "weight": 0.05,
                    "est_price": 4.10,
                    "lots": 100,
                    "target_amount": 150000.0,
                },
            },
            "position_plan": {
                "510300": {
                    "base_weight": 0.05,
                    "target_weight": 0.05,
                    "target_amount": 150000.0,
                    "phases": [
                        {"target_amount": 75000.0},
                        {"target_amount": 75000.0},
                    ],
                },
            },
            "phase_summary": [
                {
                    "assets": [
                        {"code": "510300", "shares": 36000, "amount": 150000.0},
                    ],
                },
            ],
        }

    def test_apply_adjustments_applied_true(self):
        plan = self._make_plan()
        flow = {"510300": {"net_flow_yi": 100.0}}
        result = apply_broad_based_adjustments_to_plan(plan, flow)
        assert result["applied"] is True
        assert len(result["adjustments"]) == 4
        assert "summary" in result

    def test_apply_adjustments_target_portfolio_updated(self):
        plan = self._make_plan()
        flow = {"510300": {"net_flow_yi": 100.0}}
        apply_broad_based_adjustments_to_plan(plan, flow)
        info = plan["target_portfolio"]["510300"]
        # 强加仓 scale=1.3, target_weight=0.05*1.3=0.065
        assert info["weight"] == round(0.05 * 1.3, 6)
        assert info["target_amount"] == round(3_000_000 * round(0.05 * 1.3, 6), 2)
        assert info["total_shares"] > 0
        assert info["actual_amount"] > 0
        assert info["last_flow_signal"] == "国家队强加仓信号"
        assert info["last_adjust_action"] == "强加仓"

    def test_apply_adjustments_position_plan_updated(self):
        plan = self._make_plan()
        flow = {"510300": {"net_flow_yi": 100.0}}
        apply_broad_based_adjustments_to_plan(plan, flow)
        pos = plan["position_plan"]["510300"]
        assert pos["target_weight"] == round(0.05 * 1.3, 6)
        # phases base_amount 被记录
        for ph in pos["phases"]:
            assert "base_amount" in ph

    def test_apply_adjustments_phase_summary_updated(self):
        plan = self._make_plan()
        flow = {"510300": {"net_flow_yi": 100.0}}
        apply_broad_based_adjustments_to_plan(plan, flow)
        asset = plan["phase_summary"][0]["assets"][0]
        assert "base_shares" in asset
        assert "base_amount" in asset
        # shares = round(36000 * 1.3) = 46800
        assert asset["shares"] == round(36000 * 1.3)

    def test_apply_adjustments_no_matching_code(self):
        # plan 中没有宽基ETF代码
        plan = {"target_portfolio": {"600519": {"style": "白酒"}}, "position_plan": {}, "phase_summary": []}
        flow = {"510300": {"net_flow_yi": 100.0}}
        result = apply_broad_based_adjustments_to_plan(plan, flow)
        assert result["applied"] is False

    def test_apply_adjustments_no_target_amount_key(self):
        # info 没有 target_amount 键 → 不计算
        plan = {
            "target_portfolio": {
                "510300": {"name": "测试", "style": "宽基", "est_price": 0, "lots": 100},
            },
            "position_plan": {},
            "phase_summary": [],
        }
        flow = {"510300": {"net_flow_yi": 100.0}}
        result = apply_broad_based_adjustments_to_plan(plan, flow)
        assert result["applied"] is True
        # est_price=0 → 不计算 shares
        assert "total_shares" not in plan["target_portfolio"]["510300"]

    def test_apply_adjustments_custom_broad_based(self):
        custom = [{"code": "510300", "name": "测试", "base_weight": 0.10}]
        plan = {
            "target_portfolio": {"510300": {"style": "宽基", "est_price": 0}},
            "position_plan": {},
            "phase_summary": [],
        }
        flow = {"510300": {"net_flow_yi": 100.0}}
        result = apply_broad_based_adjustments_to_plan(plan, flow, custom)
        assert len(result["adjustments"]) == 1

    def test_apply_adjustments_idempotent(self):
        # 二次调用: base_amount/base_shares 已存在, 走 False 分支
        plan = self._make_plan()
        flow = {"510300": {"net_flow_yi": 100.0}}
        apply_broad_based_adjustments_to_plan(plan, flow)
        # 第二次调用, base 字段已存在
        result2 = apply_broad_based_adjustments_to_plan(plan, flow)
        assert result2["applied"] is True
        # 验证 base_amount/base_shares 已记录
        pos = plan["position_plan"]["510300"]
        for ph in pos["phases"]:
            assert "base_amount" in ph
        asset = plan["phase_summary"][0]["assets"][0]
        assert "base_shares" in asset
        assert "base_amount" in asset

    def test_apply_adjustments_phase_summary_non_matching_asset(self):
        # phase_summary 中包含不匹配 code 的 asset
        plan = {
            "target_portfolio": {
                "510300": {"name": "沪深300", "style": "宽基", "est_price": 0},
            },
            "position_plan": {},
            "phase_summary": [
                {
                    "assets": [
                        {"code": "600519", "shares": 100, "amount": 10000.0},
                        {"code": "510300", "shares": 36000, "amount": 150000.0},
                    ],
                },
            ],
        }
        flow = {"510300": {"net_flow_yi": 100.0}}
        result = apply_broad_based_adjustments_to_plan(plan, flow)
        assert result["applied"] is True
        # 600519 不在 target_portfolio, 不被调整
        asset519 = plan["phase_summary"][0]["assets"][0]
        assert asset519["shares"] == 100

    # ------ validate_portfolio_compliance: _MACRO is None ------
    def test_validate_compliance_macro_none(self, monkeypatch):
        monkeypatch.setattr(bbep, "_MACRO", None)
        portfolio = {
            "510300": {"name": "沪深300", "style": "宽基"},
        }
        result = validate_portfolio_compliance(portfolio)
        assert "holdings" in result
        assert "summary" in result
        assert len(result["holdings"]) == 1
        # _MACRO None → ff_score=1.0, kc_score=1.0 → combined=1.0 → 强对齐
        h = result["holdings"][0]
        assert h["combined"] == 1.0
        assert h["level"] == "强对齐"
        assert h["passed"] is True
        assert result["summary"]["total"] == 1
        assert result["summary"]["passed"] == 1
        assert result["summary"]["weak"] == 0

    def test_validate_compliance_macro_none_weak(self, monkeypatch):
        monkeypatch.setattr(bbep, "_MACRO", None)
        portfolio = {
            "600519": {"name": "茅台", "style": "白酒"},
        }
        result = validate_portfolio_compliance(portfolio)
        # _MACRO None → combined=1.0 仍强对齐
        assert result["holdings"][0]["combined"] == 1.0

    # ------ validate_portfolio_compliance: 强对齐 ------
    def test_validate_compliance_strong_alignment(self, monkeypatch):
        macro = _make_macro_mock(
            ff_scores={"510300": 1.2},
            kc_scores={"510300": (1.0, "康波中性")},
        )
        monkeypatch.setattr(bbep, "_MACRO", macro)
        portfolio = {"510300": {"name": "沪深300", "style": "宽基"}}
        result = validate_portfolio_compliance(portfolio)
        h = result["holdings"][0]
        # combined = (1.2 + 1.0)/2 = 1.1 → 强对齐
        assert h["combined"] == round((1.2 + 1.0) / 2, 4)
        assert h["level"] == "强对齐"
        assert h["passed"] is True
        assert h["action"] == "维持/可加配"

    # ------ validate_portfolio_compliance: 中性 ------
    def test_validate_compliance_neutral(self, monkeypatch):
        macro = _make_macro_mock(
            ff_scores={"510300": 0.9},
            kc_scores={"510300": (0.9, "康波中性")},
        )
        monkeypatch.setattr(bbep, "_MACRO", macro)
        portfolio = {"510300": {"name": "沪深300", "style": "宽基"}}
        result = validate_portfolio_compliance(portfolio)
        h = result["holdings"][0]
        # combined = 0.9 → 中性
        assert h["combined"] == 0.9
        assert h["level"] == "中性"
        assert h["passed"] is True
        assert h["action"] == "维持"

    # ------ validate_portfolio_compliance: 偏弱 ------
    def test_validate_compliance_weak(self, monkeypatch):
        macro = _make_macro_mock(
            ff_scores={"510300": 0.8},
            kc_scores={"510300": (0.8, "康波偏弱")},
        )
        monkeypatch.setattr(bbep, "_MACRO", macro)
        portfolio = {"510300": {"name": "沪深300", "style": "宽基"}}
        result = validate_portfolio_compliance(portfolio)
        h = result["holdings"][0]
        # combined = 0.8 → 偏弱
        assert h["combined"] == 0.8
        assert h["level"] == "偏弱"
        assert h["passed"] is False
        assert h["action"] == "建议减配"
        assert result["summary"]["weak"] == 1
        assert "510300" in result["summary"]["weak_codes"]

    # ------ validate_portfolio_compliance: kc 走 style 回退分支 ------
    def test_validate_compliance_kc_style_fallback(self, monkeypatch):
        # kc_map 不包含 code → 走 KONDRATIEV_STYLE_WEIGHTS 回退
        macro = _make_macro_mock(
            ff_scores={"510300": 1.0},
            kc_scores={},
            style_weights={"宽基": 1.0},
        )
        monkeypatch.setattr(bbep, "_MACRO", macro)
        portfolio = {"510300": {"name": "沪深300", "style": "宽基"}}
        result = validate_portfolio_compliance(portfolio)
        h = result["holdings"][0]
        # kc_score = 1.0 (style weight), combined = (1.0+1.0)/2 = 1.0
        assert h["kc_score"] == 1.0
        assert "康波中性/防御" in h["note"]

    def test_validate_compliance_kc_style_fallback_weak(self, monkeypatch):
        # style weight < 0.95 → "康波偏弱"
        macro = _make_macro_mock(
            ff_scores={"600519": 1.0},
            kc_scores={},
            style_weights={"白酒": 0.8},
        )
        monkeypatch.setattr(bbep, "_MACRO", macro)
        portfolio = {"600519": {"name": "茅台", "style": "白酒"}}
        result = validate_portfolio_compliance(portfolio)
        h = result["holdings"][0]
        assert h["kc_score"] == 0.8
        assert "康波偏弱" in h["note"]

    # ------ validate_portfolio_compliance: 多标的汇总 ------
    def test_validate_compliance_multi_assets(self, monkeypatch):
        macro = _make_macro_mock(
            ff_scores={"510300": 1.2, "600519": 0.8},
            kc_scores={
                "510300": (1.0, "康波中性"),
                "600519": (0.8, "康波偏弱"),
            },
        )
        monkeypatch.setattr(bbep, "_MACRO", macro)
        portfolio = {
            "510300": {"name": "沪深300", "style": "宽基"},
            "600519": {"name": "茅台", "style": "白酒"},
        }
        result = validate_portfolio_compliance(portfolio)
        assert result["summary"]["total"] == 2
        assert result["summary"]["passed"] == 1
        assert result["summary"]["weak"] == 1
        assert "600519" in result["summary"]["weak_codes"]
        # avg_combined = (1.1 + 0.8)/2
        assert result["summary"]["avg_combined"] == round((1.1 + 0.8) / 2, 4)

    def test_validate_compliance_empty_portfolio(self, monkeypatch):
        macro = _make_macro_mock(ff_scores={}, kc_scores={})
        monkeypatch.setattr(bbep, "_MACRO", macro)
        result = validate_portfolio_compliance({})
        assert result["summary"]["total"] == 0
        assert result["summary"]["avg_combined"] == 0.0

    # ------ fetch_national_team_flow_signals ------
    def test_fetch_flow_signals_success(self, monkeypatch):
        class _MockTracker:
            def __init__(self):
                pass
            def get_all_etf_fund_flows(self):
                return {"510300": {"net_flow_yi": 100.0}}

        monkeypatch.setattr("utils.etf_flow_monitor.ETFRealTimeTracker", _MockTracker)
        result = fetch_national_team_flow_signals()
        assert result == {"510300": {"net_flow_yi": 100.0}}

    def test_fetch_flow_signals_init_exception(self, monkeypatch):
        class _BadTracker:
            def __init__(self):
                raise RuntimeError("init failed")

        monkeypatch.setattr("utils.etf_flow_monitor.ETFRealTimeTracker", _BadTracker)
        result = fetch_national_team_flow_signals()
        assert result == {}

    def test_fetch_flow_signals_method_exception(self, monkeypatch):
        class _BadTracker:
            def __init__(self):
                pass
            def get_all_etf_fund_flows(self):
                raise ConnectionError("network down")

        monkeypatch.setattr("utils.etf_flow_monitor.ETFRealTimeTracker", _BadTracker)
        result = fetch_national_team_flow_signals()
        assert result == {}

    # ------ adjust_plan_with_national_team_flow ------
    def test_adjust_plan_no_flow_data(self, monkeypatch):
        monkeypatch.setattr(bbep, "fetch_national_team_flow_signals", lambda: {})
        plan = {"target_portfolio": {"510300": {"style": "宽基"}}}
        result = adjust_plan_with_national_team_flow(plan)
        assert result["applied"] is False
        assert result["reason"] == "无资金流数据"
        assert result["adjustments"] == []

    def test_adjust_plan_with_flow_data(self, monkeypatch):
        monkeypatch.setattr(
            bbep,
            "fetch_national_team_flow_signals",
            lambda: {"510300": {"net_flow_yi": 100.0}},
        )
        plan = {
            "target_portfolio": {"510300": {"style": "宽基", "est_price": 0}},
            "position_plan": {},
            "phase_summary": [],
        }
        result = adjust_plan_with_national_team_flow(plan)
        assert result["applied"] is True
        assert len(result["adjustments"]) == 4

    def test_adjust_plan_exception(self, monkeypatch):
        def _raise():
            raise RuntimeError("fetch failed")
        monkeypatch.setattr(bbep, "fetch_national_team_flow_signals", _raise)
        plan = {"target_portfolio": {}}
        result = adjust_plan_with_national_team_flow(plan)
        assert result["applied"] is False
        assert "fetch failed" in result["reason"]
        assert result["adjustments"] == []