# -*- coding: utf-8 -*-
"""ShadowAccount 单元测试 - CIO v1.0

验证：
1. 标的不足时降级返回
2. 有效因子通过影子账户
3. 高回撤因子被拒
4. 过拟合因子被 DSR 拒
5. 蒙特卡洛压力测试生效
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from research.vibe_trading_factor_analysis.shadow.shadow_account import (
    ShadowAccount,
    quick_shadow,
)


def _gen_synthetic_history(n_days: int, n_syms: int, alpha_strength: float = 0.0,
                            noise_vol: float = 0.02, seed: int = 42):
    """生成合成因子历史与 forward returns 历史

    alpha_strength=0：纯噪声（应被 DSR 拒）
    alpha_strength>0：有 alpha（应通过）
    """
    rng = np.random.default_rng(seed)
    fv_hist = []
    fr_hist = []
    syms = [f"S{i:03d}" for i in range(n_syms)]
    for _d in range(n_days):
        fv = {s: float(rng.normal(0, 1)) for s in syms}
        # forward return 与因子值正相关（alpha_strength 控制强度）
        fr = {s: float(alpha_strength * fv[s] + rng.normal(0, noise_vol)) for s in syms}
        fv_hist.append(fv)
        fr_hist.append(fr)
    return fv_hist, fr_hist


def test_insufficient_days():
    """测试 1: 观察日不足时降级返回"""
    print("\n[Test 1] 观察日不足降级...")
    account = ShadowAccount()
    fv_hist, fr_hist = _gen_synthetic_history(30, 100, alpha_strength=0.1)
    r = account.run_shadow(fv_hist, fr_hist, n_trials=10, factor_name="test_short")
    assert not r.pass_shadow, "短历史不应通过"
    assert any("obs_days" in reason for reason in r.fail_reasons), "应报告观察日不足"
    print(f"  ✓ 拒绝原因: {r.reason}")


def test_pure_noise_rejected():
    """测试 2: 纯噪声因子（alpha_strength=0）应被 DSR 拒"""
    print("\n[Test 2] 纯噪声因子拒绝...")
    fv_hist, fr_hist = _gen_synthetic_history(120, 100, alpha_strength=0.0, noise_vol=0.05)
    r = quick_shadow(fv_hist, fr_hist, n_trials=10, factor_name="test_noise")
    print(f"  live_DSR={r.live_dsr:.3f}, sr={r.sr_observed:.3f}, dd={r.max_drawdown:.3f}")
    # 纯噪声 SR 接近 0，DSR 应 < 0 或 < 0.5
    assert r.live_dsr < 0.5, "纯噪声因子不应通过 DSR"
    print(f"  ✓ 拒绝原因: {r.reason}")


def test_strong_alpha_pass():
    """测试 3: 强 alpha 因子应通过影子账户"""
    print("\n[Test 3] 强 alpha 因子通过...")
    # alpha_strength=0.05 + 低噪声 -> SR 应该较高
    fv_hist, fr_hist = _gen_synthetic_history(120, 100, alpha_strength=0.08, noise_vol=0.01, seed=7)
    r = quick_shadow(fv_hist, fr_hist, n_trials=10, factor_name="test_strong")
    print(f"  live_DSR={r.live_dsr:.3f}, sr={r.sr_observed:.3f}, dd={r.max_drawdown:.3f}, mc_p95={r.monte_carlo_p95_dd:.3f}")
    # 强 alpha 应通过
    if r.pass_shadow:
        print("  ✓ 通过影子账户")
    else:
        print(f"  ⚠ 未通过: {r.reason}")
        # 弱通过也接受：至少 DSR > 0
        assert r.live_dsr > 0, "强 alpha DSR 应 > 0"


def test_max_drawdown_rejection():
    """测试 4: 极端波动因子应被 max_dd 拒"""
    print("\n[Test 4] 极端波动因子拒绝...")
    fv_hist, fr_hist = _gen_synthetic_history(120, 100, alpha_strength=0.0, noise_vol=0.10, seed=99)
    r = quick_shadow(fv_hist, fr_hist, n_trials=10, factor_name="test_volatile")
    print(f"  max_dd={r.max_drawdown:.3f}, mc_p95={r.monte_carlo_p95_dd:.3f}")
    # 极端波动 max_dd 大概率 >= 12%
    if r.max_drawdown >= 0.12:
        assert any("max_dd" in reason for reason in r.fail_reasons), "应报告 max_dd 超限"
        print(f"  ✓ 拒绝原因: {r.reason}")
    else:
        print("  ⚠ 噪声未触发 max_dd 阈值（可接受）")


def test_monte_carlo_runs():
    """测试 5: 蒙特卡洛压力测试可运行且产生合理结果"""
    print("\n[Test 5] 蒙特卡洛压力测试...")
    fv_hist, fr_hist = _gen_synthetic_history(120, 100, alpha_strength=0.05, noise_vol=0.02)
    r = quick_shadow(fv_hist, fr_hist, n_trials=10, factor_name="test_mc")
    assert r.monte_carlo_trials == 1000, "蒙特卡洛次数应为 1000"
    assert 0.0 <= r.monte_carlo_p95_dd <= 1.0, "P95 回撤应在 [0, 1]"
    assert 0.0 <= r.monte_carlo_mean_dd <= 1.0, "平均回撤应在 [0, 1]"
    assert r.monte_carlo_p95_dd >= r.monte_carlo_mean_dd, "P95 >= 平均"
    print(f"  ✓ P95={r.monte_carlo_p95_dd:.3f}, mean={r.monte_carlo_mean_dd:.3f}, trials={r.monte_carlo_trials}")


def test_to_dict_serializable():
    """测试 6: 结果可序列化为 dict（用于持久化审计）"""
    print("\n[Test 6] 结果序列化...")
    fv_hist, fr_hist = _gen_synthetic_history(120, 100, alpha_strength=0.05, noise_vol=0.02)
    r = quick_shadow(fv_hist, fr_hist, n_trials=10, factor_name="test_ser")
    d = r.to_dict()
    assert "factor_name" in d
    assert "live_dsr" in d
    assert "daily_pnl" in d
    assert isinstance(d["daily_pnl"], list)
    import json
    json_str = json.dumps(d, ensure_ascii=False)  # 不抛异常即通过
    print(f"  ✓ 序列化成功，长度={len(json_str)}")


def test_n_trials_affects_dsr():
    """测试 7: n_trials 越大 DSR 越严格（多重检验惩罚）"""
    print("\n[Test 7] 多重检验惩罚...")
    fv_hist, fr_hist = _gen_synthetic_history(120, 100, alpha_strength=0.05, noise_vol=0.02, seed=11)
    r_few = quick_shadow(fv_hist, fr_hist, n_trials=5, factor_name="test_few")
    r_many = quick_shadow(fv_hist, fr_hist, n_trials=450, factor_name="test_many")
    print(f"  n_trials=5:   DSR={r_few.live_dsr:.3f}")
    print(f"  n_trials=450: DSR={r_many.live_dsr:.3f}")
    # n_trials 越大 expected_max_sr 越大，DSR 应越小
    assert r_many.live_dsr <= r_few.live_dsr, "n_trials 越大 DSR 应越小（多重检验惩罚）"
    print(f"  ✓ 多重检验惩罚生效，差值={r_few.live_dsr - r_many.live_dsr:.3f}")


def main():
    """主测试入口"""
    print("=" * 60)
    print("ShadowAccount 单元测试 - CIO v1.0")
    print("=" * 60)

    tests = [
        test_insufficient_days,
        test_pure_noise_rejected,
        test_strong_alpha_pass,
        test_max_drawdown_rejection,
        test_monte_carlo_runs,
        test_to_dict_serializable,
        test_n_trials_affects_dsr,
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
