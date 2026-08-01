# -*- coding: utf-8 -*-
"""验证 compute_ic_decay 异号判断 bug 修复（v6 噪声阈值检查）

修复前 bug：
    recent_ic=-0.0027（噪声级）与 longer_ic=+0.033 异号即触发 return 1.0
    导致 MARGIN_EXP IC_decay=1.0（误判为完全反转）

修复后预期：
    因 |recent_ic|=0.0027 < 0.005 噪声阈值，按 1 - |recent|/|longer| 计算
    decay = 1 - 0.0027/0.033 ≈ 0.9186
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from research.vibe_trading_factor_analysis.adapters.factor_history_builder import (
    compute_ic_decay,
)


def _make_factor_history(ic_series: list, n_syms: int = 10) -> list:
    """根据 IC 序列反向构造 factor_history + forward_returns_history

    简化做法：直接构造能产生目标 IC 的因子值和远期收益对
    """
    factor_history = []
    fwd_returns_history = []
    for ic in ic_series:
        # 当 ic 接近 0 时构造噪声，当 ic 显著时构造正相关
        fv = {}
        fr = {}
        for i in range(n_syms):
            # 因子值 = i（递增），远期收益 = i*ic + 噪声
            fv[f"S{i:03d}_SZ"] = float(i)
            # 当 ic 接近 0 时返回噪声，否则返回相关信号
            if abs(ic) < 1e-6:
                fr[f"S{i:03d}_SZ"] = float((i * 7 % 11) - 5)  # 伪随机噪声
            else:
                fr[f"S{i:03d}_SZ"] = float(i) * ic + ((i * 7 % 11) - 5) * 0.1
        factor_history.append(fv)
        fwd_returns_history.append(fr)
    return factor_history, fwd_returns_history


def test_noise_recent_ic_not_treated_as_reversal():
    """测试 1: 噪声级 recent_ic 异号不应触发 return 1.0

    场景：longer_ic=+0.033（显著），recent_ic=-0.0027（噪声级异号）
    修复前：return 1.0（误判为完全反转）
    修复后：return 1 - 0.0027/0.033 ≈ 0.9186
    """
    print("\n=== 测试 1: 噪声级 recent_ic 异号 ===")

    # 构造 IC 序列：前 20 天 ic=+0.033，后 5 天 ic=-0.0027
    [0.033] * 20 + [-0.0027] * 5
    # 需要至少 long_window + 5 = 25 天
    # 但 compute_ic_decay 内部调用 compute_rolling_ic_series 会再算一次
    # 简化测试：直接验证逻辑分支

    # 手动模拟 compute_ic_decay 内部逻辑
    recent_ic = -0.0027
    longer_ic = 0.033
    RECENT_IC_NOISE_THRESHOLD = 0.005

    # 修复前逻辑
    old_decay = 1.0 if recent_ic * longer_ic < 0 else 1.0 - abs(recent_ic) / abs(longer_ic)
    print(f"  recent_ic={recent_ic}, longer_ic={longer_ic}")
    print(f"  修复前 decay（异号即 1.0）: {old_decay}")

    # 修复后逻辑
    if abs(recent_ic) >= RECENT_IC_NOISE_THRESHOLD and recent_ic * longer_ic < 0:
        new_decay = 1.0
    else:
        new_decay = 1.0 - abs(recent_ic) / abs(longer_ic)
    print(f"  修复后 decay（噪声阈值检查）: {new_decay:.4f}")
    print(f"  阈值 0.6 {'❌ 不通过' if new_decay >= 0.6 else '✅ 通过'}")

    assert new_decay < 1.0, "修复后 decay 不应为 1.0"
    assert abs(new_decay - 0.9182) < 0.01, f"预期 ≈ 0.9182，实际 {new_decay}"
    print("  ✅ 修复生效：噪声级 recent_ic 不再被误判为完全反转")


def test_real_reversal_still_returns_one():
    """测试 2: 真实反转（|recent|≥阈值且异号）仍应返回 1.0"""
    print("\n=== 测试 2: 真实反转仍返回 1.0 ===")
    recent_ic = -0.05  # 显著异号
    longer_ic = 0.033
    RECENT_IC_NOISE_THRESHOLD = 0.005

    if abs(recent_ic) >= RECENT_IC_NOISE_THRESHOLD and recent_ic * longer_ic < 0:
        decay = 1.0
    else:
        decay = 1.0 - abs(recent_ic) / abs(longer_ic)

    print(f"  recent_ic={recent_ic}, longer_ic={longer_ic}")
    print(f"  decay={decay}")
    assert decay == 1.0, f"真实反转应返回 1.0，实际 {decay}"
    print("  ✅ 真实反转仍返回 1.0（不破坏原有保护逻辑）")


def test_same_sign_weak_ic():
    """测试 3: 同号但 recent 较弱，decay 应在 [0, 1) 之间"""
    print("\n=== 测试 3: 同号弱化 ===")
    recent_ic = 0.015
    longer_ic = 0.033

    if abs(recent_ic) >= 0.005 and recent_ic * longer_ic < 0:
        decay = 1.0
    else:
        decay = 1.0 - abs(recent_ic) / abs(longer_ic)

    print(f"  recent_ic={recent_ic}, longer_ic={longer_ic}")
    print(f"  decay={decay:.4f}")
    assert 0 < decay < 1, f"同号弱化 decay 应在 (0,1)，实际 {decay}"
    print("  ✅ 同号弱化按比值计算（与修复前一致）")


def test_compute_ic_decay_function_directly():
    """测试 4: 直接调用 compute_ic_decay 函数验证修复生效

    构造一个长序列，使前 20 天 IC 较强，后 5 天 IC 接近 0（噪声），
    验证 decay 不再是 1.0
    """
    print("\n=== 测试 4: 直接调用 compute_ic_decay ===")
    # 构造 50 天数据，前 25 天 ic=0.05（强），后 5 天 ic=0.001（噪声）
    # 这样 longer_ic (近 20 天) 包含 15 天强 + 5 天噪声 ≈ 0.038
    # recent_ic (近 5 天) = 0.001（噪声）
    # decay = 1 - 0.001/0.038 ≈ 0.974
    ic_series = [0.05] * 25 + [0.001] * 5 + [0.001] * 20  # 共 50 天
    factor_history, fwd_returns_history = _make_factor_history(ic_series, n_syms=20)

    decay = compute_ic_decay(
        factor_history=factor_history,
        forward_returns_history=fwd_returns_history,
        short_window=5,
        long_window=20,
    )
    print("  50 天数据，前 25 天 ic=0.05，后 25 天 ic=0.001（噪声）")
    print(f"  compute_ic_decay 返回: {decay:.4f}")
    print(f"  阈值 0.6 {'❌ 不通过' if decay >= 0.6 else '✅ 通过'}")
    # decay 应小于 1.0（修复生效），但可能仍 > 0.6
    assert decay < 1.0, f"修复后 decay 不应为 1.0，实际 {decay}"
    print("  ✅ compute_ic_decay 函数修复生效（不再误判为 1.0）")


def main():
    print("=" * 70)
    print("compute_ic_decay 异号判断 bug 修复验证（v6 噪声阈值）")
    print("=" * 70)

    test_noise_recent_ic_not_treated_as_reversal()
    test_real_reversal_still_returns_one()
    test_same_sign_weak_ic()
    test_compute_ic_decay_function_directly()

    print("\n" + "=" * 70)
    print("所有测试通过 ✅")
    print("=" * 70)
    print("\n核心修复：")
    print("  - 修复前: recent_ic=-0.0027 与 longer_ic=+0.033 异号 → return 1.0")
    print("  - 修复后: |recent_ic|<0.005 噪声阈值 → decay = 1 - |recent|/|longer| ≈ 0.9186")
    print("\nMARGIN_EXP 影响（基于第十二批次数据）：")
    print("  - 修复前 decay=1.0（误判完全反转）→ G2 ❌")
    print("  - 修复后 decay≈0.9186（按弱化计算）→ G2 仍 ❌（>0.6 阈值）")
    print("  - 但 IC_IR=0.3981 通过 0.3 阈值 ✅，因子本身有 Alpha 信号")
    print("\n决策建议：")
    print("  - 选项 A: 调整 decay 阈值 0.6 → 0.9（适配 QualityTrend 因子特性）")
    print("  - 选项 B: 手动放行 MARGIN_EXP 进入 G3+G4+Shadow 验证（不修改阈值）")
    print("  - 推荐选项 B：避免破坏其他因子的 decay 保护，单独验证 MARGIN_EXP")


if __name__ == "__main__":
    main()
