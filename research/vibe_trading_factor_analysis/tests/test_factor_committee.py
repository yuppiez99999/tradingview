"""FactorCommittee 单元测试 - CIO v1.0

验证：
1. 全维度优秀因子应通过
2. DSR 不足应被 AlphaAgent 否决
3. 正交性不足应被 RiskAgent 否决
4. 容量不足应被 ExecutionAgent 否决
5. 经济逻辑不足应被 EconomicAgent 否决
6. Regime 不普适应被 CapacityAgent 否决
7. avg < 6 应触发 Chair 强制否决
8. 中等评分应被拒绝但不算专家否决
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from research.vibe_trading_factor_analysis.committee.factor_committee import (
    quick_review,
)

# ============== 因子报告模板 ==============

def _excellent_factor():
    """优秀因子：全维度达标"""
    return {
        "ic_ir_120d": 0.55,
        "dsr_value": 1.2,
        "ic_decay": 0.85,
        "max_abs_corr": 0.25,
        "risk_contribution": 0.02,
        "tail_corr_max": 0.5,
        "capacity_ratio": 0.08,
        "turnover": 0.25,
        "slippage_bps": 3.0,
        "economic_logic_score": 9.0,
        "a_share_fit": 0.9,
        "has_academic_paper": True,
        "min_regime_ic_ir": 0.45,
        "weakest_regime": "bear",
        "regime_tag": "all_regime",
        "n_regimes_pass": 4,
    }


def _test_factor_with(overrides):
    """基于优秀因子模板，应用 overrides"""
    f = _excellent_factor()
    f.update(overrides)
    return f


def test_excellent_factor_passes():
    """测试 1: 优秀因子应通过委员会"""
    print("\n[Test 1] 优秀因子通过...")
    v = quick_review("VT_EXCELLENT", _excellent_factor())
    print(f"  avg={v.avg_score:.2f}, veto={v.has_veto}, decision={v.chair_decision}")
    assert v.approved, "优秀因子应通过"
    assert not v.has_veto
    assert v.avg_score >= 7.0


def test_low_dsr_vetoed_by_alpha():
    """测试 2: DSR 不足应被 AlphaAgent 否决"""
    print("\n[Test 2] DSR 不足 AlphaAgent 否决...")
    v = quick_review("VT_LOW_DSR", _test_factor_with({"dsr_value": 0.3}))
    print(f"  avg={v.avg_score:.2f}, veto={v.has_veto}, veto_by={v.veto_by}")
    assert not v.approved, "DSR 不足不应通过"
    assert "AlphaAgent" in v.veto_by


def test_high_corr_vetoed_by_risk():
    """测试 3: 正交性不足应被 RiskAgent 否决"""
    print("\n[Test 3] 正交性不足 RiskAgent 否决...")
    v = quick_review("VT_HIGH_CORR", _test_factor_with({"max_abs_corr": 0.65}))
    print(f"  avg={v.avg_score:.2f}, veto={v.has_veto}, veto_by={v.veto_by}")
    assert not v.approved
    assert "RiskAgent" in v.veto_by


def test_low_capacity_vetoed_by_execution():
    """测试 4: 容量不足应被 ExecutionAgent 否决"""
    print("\n[Test 4] 容量不足 ExecutionAgent 否决...")
    v = quick_review("VT_LOW_CAP", _test_factor_with({"capacity_ratio": 0.01}))
    print(f"  avg={v.avg_score:.2f}, veto={v.has_veto}, veto_by={v.veto_by}")
    assert not v.approved
    assert "ExecutionAgent" in v.veto_by


def test_low_economic_vetoed_by_economic():
    """测试 5: 经济逻辑不足应被 EconomicAgent 否决"""
    print("\n[Test 5] 经济逻辑不足 EconomicAgent 否决...")
    v = quick_review("VT_LOW_ECON", _test_factor_with({"economic_logic_score": 3.0}))
    print(f"  avg={v.avg_score:.2f}, veto={v.has_veto}, veto_by={v.veto_by}")
    assert not v.approved
    assert "EconomicAgent" in v.veto_by


def test_low_regime_vetoed_by_capacity():
    """测试 6: Regime 不普适应被 CapacityAgent 否决"""
    print("\n[Test 6] Regime 不普适 CapacityAgent 否决...")
    v = quick_review(
        "VT_LOW_REGIME",
        _test_factor_with({
            "min_regime_ic_ir": 0.1,
            "regime_tag": "bear_only",
            "n_regimes_pass": 1,
        }),
    )
    print(f"  avg={v.avg_score:.2f}, veto={v.has_veto}, veto_by={v.veto_by}")
    assert not v.approved
    assert "CapacityAgent" in v.veto_by


def test_chair_veto_when_avg_below_6():
    """测试 7: avg < 6 应触发 Chair 强制否决"""
    print("\n[Test 7] Chair 强制否决（avg < 6）...")
    # 全维度低分但无单 veto
    low_factor = {
        "ic_ir_120d": 0.25, "dsr_value": 0.6, "ic_decay": 0.7,
        "max_abs_corr": 0.45, "risk_contribution": 0.04, "tail_corr_max": 0.7,
        "capacity_ratio": 0.025, "turnover": 0.5, "slippage_bps": 10.0,
        "economic_logic_score": 6.0, "a_share_fit": 0.5, "has_academic_paper": False,
        "min_regime_ic_ir": 0.22, "weakest_regime": "bear", "regime_tag": "partial",
        "n_regimes_pass": 2,
    }
    v = quick_review("VT_LOW_ALL", low_factor)
    print(f"  avg={v.avg_score:.2f}, chair_decision={v.chair_decision}")
    assert v.avg_score < 6.0, f"avg 应 < 6, 实际 {v.avg_score:.2f}"
    assert v.chair_decision == "chair_veto", "应触发 Chair 强制否决"
    assert not v.approved


def test_medium_score_rejected_no_veto():
    """测试 8: 中等评分（6-7）应被拒绝但不算专家否决"""
    print("\n[Test 8] 中等评分（6-7）被拒...")
    # 微调使所有 veto 不触发但 avg 在 6-7
    medium_factor = _test_factor_with({
        "ic_ir_120d": 0.32, "dsr_value": 0.6,
        "max_abs_corr": 0.45,
        "capacity_ratio": 0.025,
        "economic_logic_score": 6.0, "a_share_fit": 0.5,
        "min_regime_ic_ir": 0.22,
    })
    v = quick_review("VT_MEDIUM", medium_factor)
    print(f"  avg={v.avg_score:.2f}, veto={v.has_veto}, decision={v.chair_decision}")
    assert not v.approved, "中等评分不应通过"
    assert not v.has_veto, "中等评分不应触发专家否决"
    assert v.chair_decision == "reject", "应为普通拒绝"


def test_audit_log_complete():
    """测试 9: 审计日志完整（5 票 + Chair 决议）"""
    print("\n[Test 9] 审计日志完整...")
    v = quick_review("VT_AUDIT", _excellent_factor())
    assert len(v.votes) == 5, "应有 5 张专家票"
    agent_names = {vote.agent_name for vote in v.votes}
    expected = {"AlphaAgent", "RiskAgent", "ExecutionAgent", "EconomicAgent", "CapacityAgent"}
    assert agent_names == expected, f"专家不匹配: {agent_names}"
    for vote in v.votes:
        assert 0.0 <= vote.score <= 10.0, f"{vote.agent_name} 评分越界: {vote.score}"
        assert vote.rationale, f"{vote.agent_name} 缺少 rationale"
        assert "evidence" in vote.to_dict()
    print(f"  ✓ 5 票完整，Chair 决议: {v.chair_decision}")


def test_serializable():
    """测试 10: 委员会决议可序列化"""
    print("\n[Test 10] 序列化...")
    import json
    v = quick_review("VT_SER", _excellent_factor())
    d = v.to_dict()
    json_str = json.dumps(d, ensure_ascii=False)
    assert len(json_str) > 0
    print(f"  ✓ 序列化长度={len(json_str)}")


def main():
    print("=" * 60)
    print("FactorCommittee 单元测试 - CIO v1.0")
    print("=" * 60)

    tests = [
        test_excellent_factor_passes,
        test_low_dsr_vetoed_by_alpha,
        test_high_corr_vetoed_by_risk,
        test_low_capacity_vetoed_by_execution,
        test_low_economic_vetoed_by_economic,
        test_low_regime_vetoed_by_capacity,
        test_chair_veto_when_avg_below_6,
        test_medium_score_rejected_no_veto,
        test_audit_log_complete,
        test_serializable,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"  ✗ FAIL: {e}")
        except Exception as e:
            failed += 1
            print(f"  ✗ ERROR: {type(e).__name__}: {e}")

    print("\n" + "=" * 60)
    print(f"总计: {passed} 通过, {failed} 失败 (共 {len(tests)} 项)")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
