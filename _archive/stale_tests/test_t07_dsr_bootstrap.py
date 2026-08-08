"""T07: DSR Bootstrap 估计器单元测试."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 添加 v8.3_institutional 使其可作为包导入
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "v8.3_institutional"))

# 通过 importlib 直接加载模块 (路径包含数字开头, 无法用 import)
import importlib.util  # noqa: E402


def _load_dsr_bootstrap():
    spec = importlib.util.spec_from_file_location(
        "dsr_bootstrap",
        str(PROJECT_ROOT / "v8.3_institutional" / "src" / "validation" / "dsr_bootstrap.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    # BUG 修复: 必须先注册到 sys.modules, 否则 dataclass 在解析类型注解时
    # 会通过 sys.modules.get(cls.__module__).__dict__ 查找, 返回 None 导致 AttributeError
    sys.modules["dsr_bootstrap"] = mod
    spec.loader.exec_module(mod)
    return mod


dsr_mod = _load_dsr_bootstrap()
BootstrapEMaxResult = dsr_mod.BootstrapEMaxResult
estimate_e_max_sr_bootstrap = dsr_mod.estimate_e_max_sr_bootstrap
deflated_sharpe_ratio_bootstrap = dsr_mod.deflated_sharpe_ratio_bootstrap


class TestBootstrapEMax:
    def test_returns_valid_result(self):
        np.random.seed(42)
        rets = np.random.normal(0.001, 0.02, 252).tolist()
        result = estimate_e_max_sr_bootstrap(rets, n_trials=50, n_bootstrap=100, random_seed=42)
        assert isinstance(result, BootstrapEMaxResult)
        assert result.n_bootstrap == 100
        assert result.n_trials == 50
        assert result.sample_length == 252

    def test_e_max_positive(self):
        np.random.seed(42)
        rets = np.random.normal(0, 0.02, 252).tolist()
        result = estimate_e_max_sr_bootstrap(rets, n_trials=100, n_bootstrap=200, random_seed=42)
        assert result.e_max_sr > 0, f"E[SR_max] 应 > 0, 实际 {result.e_max_sr}"

    def test_e_max_increases_with_n_trials(self):
        np.random.seed(42)
        rets = np.random.normal(0, 0.02, 252).tolist()
        r10 = estimate_e_max_sr_bootstrap(rets, n_trials=10, n_bootstrap=200, random_seed=42)
        r100 = estimate_e_max_sr_bootstrap(rets, n_trials=100, n_bootstrap=200, random_seed=42)
        r500 = estimate_e_max_sr_bootstrap(rets, n_trials=500, n_bootstrap=200, random_seed=42)
        assert r10.e_max_sr < r100.e_max_sr < r500.e_max_sr, \
            f"E[max] 应随 N 递增: N=10:{r10.e_max_sr:.3f}, N=100:{r100.e_max_sr:.3f}, N=500:{r500.e_max_sr:.3f}"

    def test_quantile_ordering(self):
        np.random.seed(42)
        rets = np.random.normal(0, 0.02, 252).tolist()
        r = estimate_e_max_sr_bootstrap(rets, n_trials=50, n_bootstrap=200, random_seed=42)
        assert r.p5 <= r.median <= r.p95
        assert r.p25 <= r.median <= r.p75

    def test_insufficient_samples_returns_zeros(self):
        result = estimate_e_max_sr_bootstrap([0.01] * 10, n_trials=10, n_bootstrap=10)
        assert result.e_max_sr == 0.0
        assert result.n_bootstrap == 0

    def test_reproducible_with_seed(self):
        np.random.seed(42)
        rets = np.random.normal(0, 0.02, 252).tolist()
        r1 = estimate_e_max_sr_bootstrap(rets, n_trials=20, n_bootstrap=50, random_seed=123)
        r2 = estimate_e_max_sr_bootstrap(rets, n_trials=20, n_bootstrap=50, random_seed=123)
        assert r1.e_max_sr == r2.e_max_sr


class TestDSRBootstrap:
    def test_good_strategy_passes(self):
        np.random.seed(42)
        rets = np.random.normal(0.0015, 0.012, 756).tolist()
        dsr, _p_value, is_pass, _boot = deflated_sharpe_ratio_bootstrap(
            rets, n_trials=50, n_bootstrap=200, required_dsr=0.95, random_seed=42
        )
        assert is_pass, f"正夏普策略应通过, DSR={dsr:.4f}"
        assert dsr > 0.5

    def test_noise_strategy_fails(self):
        np.random.seed(42)
        rets = np.random.normal(0, 0.02, 252).tolist()
        dsr, _p_value, is_pass, _boot = deflated_sharpe_ratio_bootstrap(
            rets, n_trials=200, n_bootstrap=200, required_dsr=0.95, random_seed=42
        )
        assert not is_pass, f"噪音策略不应通过, DSR={dsr:.4f}"

    def test_dsr_increases_with_better_sr(self):
        np.random.seed(42)
        weak_rets = np.random.normal(0.0003, 0.015, 504).tolist()
        strong_rets = np.random.normal(0.0015, 0.012, 504).tolist()

        dsr_weak, _, _, _ = deflated_sharpe_ratio_bootstrap(
            weak_rets, n_trials=50, n_bootstrap=100, random_seed=42
        )
        dsr_strong, _, _, _ = deflated_sharpe_ratio_bootstrap(
            strong_rets, n_trials=50, n_bootstrap=100, random_seed=42
        )
        assert dsr_strong > dsr_weak

    def test_insufficient_samples(self):
        dsr, p_value, is_pass, _boot = deflated_sharpe_ratio_bootstrap(
            [0.01] * 10, n_trials=10, n_bootstrap=10
        )
        assert not is_pass
        assert dsr == 0.0
        assert p_value == 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
