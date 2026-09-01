"""Deflated Sharpe Ratio 单元测试 — 口径修复回归 (2026-08-31)

修复背景: 原实现用年化 SR 参与与日频 E[SR_max] 配套的 z_score,
z 被放大 sqrt(252) 倍, DSR 系统性高估 → 弱策略恒 PASS。
参考: Bailey & López de Prado (2014) "The Deflated Sharpe Ratio"。
"""

import numpy as np
import pytest

from utils.backtest.deflated_sharpe import DSRResult, deflated_sharpe_ratio


class TestDeflatedSharpeRatio:
    """deflated_sharpe_ratio 数值行为 (AAA 模式)"""

    def test_weak_strategy_rejected(self):
        """弱策略 (年化SR~0.3, 有多重尝试) 必须拒绝 — 修复前恒 PASS"""
        # Arrange: 日频微弱正漂移, 10 次策略尝试
        rng = np.random.default_rng(42)
        weak = rng.normal(0.0008, 0.011, 252)
        # Act
        result = deflated_sharpe_ratio(weak, n_trials=10)
        # Assert: 修复前 DSR=1.0 (bug), 修复后必须 < 0.95
        assert result.is_pass is False
        assert result.deflated_sharpe_ratio < 0.95

    def test_strong_strategy_passes(self):
        """强技能策略 (年化SR>3, 单次尝试) 必须通过"""
        # Arrange
        strong = np.random.default_rng(1).normal(0.012, 0.011, 252)
        # Act
        result = deflated_sharpe_ratio(strong, n_trials=1)
        # Assert
        assert result.is_pass is True
        assert result.deflated_sharpe_ratio >= 0.95

    def test_insufficient_samples(self):
        """样本 < MIN_SAMPLES(20) → 直接 FAIL"""
        # Arrange
        returns = [0.01] * 15
        # Act
        result = deflated_sharpe_ratio(returns)
        # Assert
        assert result.is_pass is False
        assert result.p_value == 1.0
        assert "样本不足" in result.verdict

    def test_zero_volatility_fails(self):
        """零波动序列 → SR 无意义, 必须 FAIL (不崩溃)"""
        # Arrange
        flat = [0.001] * 30
        # Act
        result = deflated_sharpe_ratio(flat)
        # Assert
        assert result.is_pass is False

    def test_output_contract(self):
        """输出契约: DSRResult 字段完整, float() 返回 DSR 值"""
        # Arrange
        rng = np.random.default_rng(7)
        returns = rng.normal(0.001, 0.01, 252)
        # Act
        result = deflated_sharpe_ratio(returns, n_trials=5)
        # Assert
        assert isinstance(result, DSRResult)
        assert float(result) == result.deflated_sharpe_ratio
        assert 0.0 <= result.deflated_sharpe_ratio <= 1.0
        assert 0.0 <= result.p_value <= 1.0
        assert result.n_trials == 5
        assert result.n_observations == 252
        assert result.as_dict()["deflated_sharpe_ratio"] == result.deflated_sharpe_ratio

    def test_more_trials_more_conservative(self):
        """多重尝试惩罚: n_trials 越大, DSR 单调不增"""
        # Arrange
        rng = np.random.default_rng(11)
        returns = rng.normal(0.0015, 0.011, 252)
        # Act
        dsr_1 = deflated_sharpe_ratio(returns, n_trials=1).deflated_sharpe_ratio
        dsr_50 = deflated_sharpe_ratio(returns, n_trials=50).deflated_sharpe_ratio
        # Assert: 数据窥探惩罚使 DSR 下降
        assert dsr_50 <= dsr_1


class TestMSDeflatedSharpeRatio:
    """ms_strategy DeflatedSharpeRatio.compute() — PSR 分母修正回归"""

    def test_compute_with_psr_denominator(self):
        """compute() 输出在 [0,1], 且含分母修正后日频口径结果合理"""
        # Arrange
        from ms_strategy.src.backtest.metrics import DeflatedSharpeRatio

        dsr = DeflatedSharpeRatio(
            sharpe_ratio=0.026,  # 日频未年化
            n_trials=10,
            n_observations=252,
        )
        # Act
        value = dsr.compute()
        # Assert
        assert 0.0 <= value <= 1.0

    def test_signature_contract(self):
        """summary() 契约字段不变"""
        # Arrange
        from ms_strategy.src.backtest.metrics import DeflatedSharpeRatio

        dsr = DeflatedSharpeRatio(sharpe_ratio=0.05, n_trials=1, n_observations=100)
        # Act
        summary = dsr.summary()
        # Assert
        assert set(summary.keys()) == {
            "sharpe_ratio",
            "expected_max_sr",
            "dsr",
            "significant",
            "n_trials",
            "n_observations",
        }


@pytest.mark.parametrize("bad_input", [None, "abc"])
def test_bad_input_no_crash(bad_input):
    """异常输入不崩溃 (None/非数值)"""
    with pytest.raises((ValueError, TypeError)):
        deflated_sharpe_ratio(bad_input)


def test_empty_input_fails_gracefully():
    """空序列走样本不足早退分支, 优雅 FAIL 不崩溃"""
    # Act
    result = deflated_sharpe_ratio([])
    # Assert
    assert result.is_pass is False
    assert "样本不足" in result.verdict
