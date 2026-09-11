"""v9.1「守正」组合配置护栏 + 回测成本口径回归测试.

对应《ETF期权组合诊脉书_20260911》指出的七处硬伤。本文件的核心价值是**回归护栏**:

修复前会失败的口径 (改动回滚即变红):
    1. v9.1 配置不存在 / 硬伤未被护栏覆盖 → test_production_v91_config_passes_guardrails
       与 test_hard_issue_* 参数化用例
    2. 回测把期权成本当作**一次性乘数** → 年化拖累 ≈ 0 (保护显得免费)
       → test_hedge_cost_must_drag_annual_return
    3. 回测 _build_weight_map 漏计 ballast_holdings → 25% 权重被静默丢弃
       → test_weight_map_includes_ballast
    4. 成本口径回落「本金划拨」语义 → test_legacy_config_marks_deprecated_basis
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(PROJECT_ROOT), str(PROJECT_ROOT / "scripts")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import run_200w_etf_backtest as bt  # noqa: E402
from validate_configs import check_portfolio_v91  # noqa: E402

V91_CONFIG = PROJECT_ROOT / "config" / "portfolio_200w_etf_v91.yaml"
V10_CONFIG = PROJECT_ROOT / "config" / "portfolio_200w_etf.yaml"


def _load(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def v91() -> dict:
    if not V91_CONFIG.exists():
        pytest.skip(f"v9.1 配置不存在: {V91_CONFIG}")
    return _load(V91_CONFIG)


@pytest.fixture(scope="module")
def v10() -> dict:
    if not V10_CONFIG.exists():
        pytest.skip(f"v1.0 配置不存在: {V10_CONFIG}")
    return _load(V10_CONFIG)


# ---------------------------------------------------------------- 硬伤变异算子
def _drop_ballast(cfg: dict) -> bool:
    cfg.pop("ballast_holdings", None)
    return True


def _sink_588000_into_core(cfg: dict) -> bool:
    cfg["core_holdings"].append({"code": "588000", "name": "科创50ETF", "type": "etf", "weight": 0.0})
    return True


def _revert_budget_basis(cfg: dict) -> bool:
    cfg["options_strategy"]["budget"]["basis"] = "principal_allocation"
    return True


def _add_bear_market_clause(cfg: dict) -> bool:
    cfg["options_strategy"]["bear_market"] = {"put_notional_pct": 0.35, "cash_pct": 0.05}
    return True


def _enable_alpha_aggregation(cfg: dict) -> bool:
    cfg["satellite_rotation"]["alpha_aggregation"] = True
    return True


def _drop_data_chain(cfg: dict) -> bool:
    cfg.pop("data_chain", None)
    return True


def _drop_regime_state_machine(cfg: dict) -> bool:
    cfg.pop("regime_state_machine", None)
    return True


def _share_state_file_with_v10(cfg: dict) -> bool:
    cfg["state_file"] = "config/portfolio_200w_etf_state.json"
    return True


def _overflow_hedge_cost(cfg: dict) -> bool:
    cfg["options_strategy"]["collar"]["cost_target_pct"] = [0.02, 0.05]
    return True


def _inflate_target_above_basis(cfg: dict) -> bool:
    """把目标抬回 6% (高于自下而上净中枢 4.3%) — 诊脉书卷四 vs 卷六的原矛盾。"""
    cfg["target"]["annual_return"] = 0.06
    return True


def _break_net_center_consistency(cfg: dict) -> bool:
    cfg["target"]["net_return_center"] = 0.054
    return True


def _drop_target_basis(cfg: dict) -> bool:
    """删掉基据块只留数字 —— 目标失去可追溯推导链。"""
    cfg["target"].pop("target_basis", None)
    return True


def _detach_target_from_net_center(cfg: dict) -> bool:
    """目标与净中枢脱钩 (0.035 仍在区间内且低于净中枢, 只有「第二口径」能拦住)。"""
    cfg["target"]["annual_return"] = 0.035
    return True


def _stale_strike_floor(cfg: dict) -> bool:
    """备兑认购行权价地板退回 0.14 (旧 6% 目标的派生值, 未随新目标联动)。"""
    cfg["options_strategy"]["collar"]["call_leg"]["strike_floor_pct"] = 0.14
    return True


def _inflate_scenario_expectation(cfg: dict) -> bool:
    """基准情景退回 [5.5,6.5] —— 情景预期另立第二口径。"""
    cfg["scenario_analysis"]["base"]["annual_return_pct"] = [5.5, 6.5]
    return True


def _drop_legacy_evidence(cfg: dict) -> bool:
    """删掉旧口径并存证 —— 不可达成目标有回潮空间。"""
    cfg["target"].pop("legacy_target_infeasible", None)
    return True


#: (用例名, 变异算子, 期望命中的硬伤标签) —— 七处硬伤 + 口径单一性负向验证
HARD_ISSUE_CASES = (
    ("跨资产压舱缺失", _drop_ballast, "[硬伤一]"),
    ("科创50 重回核心", _sink_588000_into_core, "[硬伤二]"),
    ("预算口径回退本金划拨", _revert_budget_basis, "[硬伤三]"),
    ("熊市 Put 35% 名义条款", _add_bear_market_clause, "[硬伤四]"),
    ("保护成本超出硬上限", _overflow_hedge_cost, "[硬伤四]"),
    ("启用未实证 alpha 聚合", _enable_alpha_aggregation, "[硬伤五]"),
    ("缺失期权数据链契约", _drop_data_chain, "[硬伤六]"),
    ("回退康波纪年驱动", _drop_regime_state_machine, "[硬伤七]"),
    ("与 v1.0 共用状态文件", _share_state_file_with_v10, "[口径]"),
    ("目标高于自下而上测算", _inflate_target_above_basis, "不可达成目标"),
    ("净收益中枢与毛收益脱钩", _break_net_center_consistency, "[口径]"),
    ("删掉自下而上基据块", _drop_target_basis, "基据块缺失"),
    ("目标与净中枢脱钩(第二口径)", _detach_target_from_net_center, "存在第二口径"),
    ("认购行权价地板未随目标联动", _stale_strike_floor, "派生值未随目标口径联动"),
    ("情景预期另立口径", _inflate_scenario_expectation, "情景预期另立口径"),
    ("删掉旧口径并存证", _drop_legacy_evidence, "并存证缺失"),
)


def test_production_v91_config_passes_guardrails(v91: dict) -> None:
    """在产的 v9.1 配置必须零护栏告警。"""
    assert check_portfolio_v91(v91) == []


@pytest.mark.parametrize(
    "label,mutate,expected",
    HARD_ISSUE_CASES,
    ids=[case[0] for case in HARD_ISSUE_CASES],
)
def test_hard_issue_is_guarded(v91: dict, label: str, mutate, expected: str) -> None:
    """每处硬伤被重新引入时, 护栏必须拦下 (非空转证据)。"""
    broken = copy.deepcopy(v91)
    assert mutate(broken) is True
    errors = check_portfolio_v91(broken)
    assert any(expected in err for err in errors), f"{label} 未被 {expected} 拦下: {errors}"


def test_target_is_backed_by_bottom_up_math(v91: dict) -> None:
    """目标必须完全取自下而上基据: 基据块完整 + 目标=净中枢 + 派生值联动 + 情景加权自洽。"""
    tgt = v91["target"]
    assert tgt["annual_return"] == pytest.approx(tgt["net_return_center"])
    assert tgt["net_return_center"] == pytest.approx(
        tgt["gross_return_center"] - tgt["hedge_cost_target_pct"], abs=0.0005
    )
    assert tgt["annual_return"] <= tgt["annual_return_range"][1]

    # 基据块必须显式存在 (single source of truth, 不是注释)
    basis = tgt["target_basis"]
    assert basis["method"] == "bottom_up"
    for key in ("gross_formula", "net_formula", "cross_check"):
        assert str(basis[key]).strip(), f"target_basis.{key} 为空"

    # 派生值联动: 备兑认购行权价地板 = 目标 + 8pp (旧值 0.14 是 6% 目标的残留)
    floor = v91["options_strategy"]["collar"]["call_leg"]["strike_floor_pct"]
    assert floor == pytest.approx(tgt["annual_return"] + 0.08, abs=0.005)

    # 情景概率加权中枢 = 净中枢 (否则情景预期另立第二口径)
    scen = v91["scenario_analysis"]
    exp = 0.0
    for name in ("optimistic", "base", "pessimistic"):
        node = scen[name]
        lo, hi = node["annual_return_pct"]
        exp += node["probability"] * (lo + hi) / 2 / 100.0
    assert exp == pytest.approx(tgt["net_return_center"], abs=0.005)

    # 旧口径并存证不得被删 (诊断书卷六 6%~6.5% 的原罪必须留在案卷里)
    assert str(tgt["legacy_target_infeasible"]["verdict"]).strip()


def test_legacy_v10_config_is_rejected(v10: dict) -> None:
    """v1.0 旧口径配置必须被护栏全面拦下 (证明口径确实变了, 不是无差别通过)。"""
    joined = "\n".join(check_portfolio_v91(v10))
    for tag in ("[硬伤一]", "[硬伤三]", "[硬伤四]", "[硬伤五]", "[硬伤六]", "[硬伤七]"):
        assert tag in joined, f"旧口径配置应命中 {tag}, 实际: {joined}"


# ------------------------------------------------------------------ 回测消费侧
def test_weight_map_includes_ballast(v91: dict) -> None:
    """回测权重必须含压舱资产, 否则 25% 权重被静默丢弃后重归一 (虚高波动)。"""
    weights = bt._build_weight_map(v91)
    assert "511260" in weights, "国债压舱未纳入回测权重"
    assert "518880" in weights, "黄金压舱未纳入回测权重"
    assert sum(weights.values()) == pytest.approx(1.0, abs=0.005)


def test_hedge_cost_must_drag_annual_return() -> None:
    """期权成本必须逐日计提: 1.5%/年 的成本应带来约 1.5pp 的年化拖累。

    修复前成本以一次性乘数施加, 拖累仅 ~1e-4pp, 该断言必然失败。
    """
    dates = pd.date_range("2021-01-04", periods=504, freq="B")
    prices = pd.DataFrame({"510300": 4.0}, index=dates)
    config = {
        "portfolio": {"total_capital": 2_000_000},
        "options_strategy": {"protective_put": {"budget": {"annual_pct_nav": [0.015, 0.015]}}},
    }
    weights = {"510300": 1.0}

    base = bt._run_backtest(prices, weights, config, "无对冲")
    hedged = bt._run_backtest(prices, weights, config, "仅Put", use_protective_put=True)

    drag = base.annual_return - hedged.annual_return
    assert 0.008 < drag < 0.025, f"1.5%/年 成本未真实拖累年化收益 (实测拖累 {drag:.4%})"
    assert hedged.hedge_cost_annual_pct == pytest.approx(0.015)
    assert base.hedge_cost_annual_pct == 0.0


def test_collar_cost_uses_net_target(v91: dict) -> None:
    """collar 双腿模式取 cost_target_pct 净成本, 不叠加备兑认购收入 (避免重复计价)。"""
    pct, spec = bt._resolve_annual_hedge_pct(v91, True, True, False)
    assert pct == pytest.approx(0.0125)
    assert "collar.cost_target_pct" in spec
    assert "认购收入" in spec


def test_no_legs_means_no_cost(v91: dict) -> None:
    assert bt._resolve_annual_hedge_pct(v91, False, False, False) == (0.0, "none (无对冲腿)")


def test_legacy_config_marks_deprecated_basis(v10: dict) -> None:
    """v1.0 走 legacy budget_annual_pct 时, 成本口径来源必须显式标注「本金划拨」。"""
    pct, spec = bt._resolve_annual_hedge_pct(v10, True, False, False)
    assert pct > 0
    assert "本金划拨" in spec
