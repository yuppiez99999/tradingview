"""P0 训练泄漏修复单元测试 (2026-09-13)

覆盖三项修复:
  1. carve_validation_split: 早停验证集从训练段尾部切出 (带 purge 间隔),
     测试集不再参与早停;
  2. train_symbol_enhanced: 结果携带 n_valid/valid_r2 (早停/选优口径可审计),
     test 只做最终评估;
  3. time_series_cv_evaluate: 折指标在未参与早停的折外测试段上计算。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from lgb_trainer.metrics import time_series_cv_evaluate  # noqa: E402
from lgb_trainer.trainer import (  # noqa: E402
    carve_validation_split,
    train_symbol_enhanced,
)


class TestCarveValidationSplit:
    def _mk(self, n=300, f=4):
        rng = np.random.default_rng(7)
        return rng.normal(size=(n, f)), rng.normal(size=n)

    def test_val_is_tail_with_purge_gap(self):
        X, y = self._mk(300)
        X_sub, y_sub, X_val, y_val = carve_validation_split(X, y, label_horizon=5)
        assert X_val is not None
        n = 300
        n_val = len(X_val)
        # 验证段 = 训练段尾部
        assert np.array_equal(X_val, X[n - n_val :])
        # 子训练段与验证段之间保留 label_horizon 行 purge 间隔
        gap = n - n_val - len(X_sub)
        assert gap >= 5
        # 子训练段 + gap + 验证段 覆盖全部样本
        assert len(X_sub) + gap + n_val == n

    def test_too_small_returns_none(self):
        X, y = self._mk(70)  # 70-20-5 = 45 < min_train=50 → 切不出
        X_sub, y_sub, X_val, y_val = carve_validation_split(X, y, label_horizon=5)
        assert X_val is None and y_val is None
        assert len(X_sub) == len(X)  # 全量训练段返回

    def test_val_ratio_respected(self):
        X, y = self._mk(400)
        _, _, X_val, _ = carve_validation_split(X, y, label_horizon=5, val_ratio=0.2)
        assert 70 <= len(X_val) <= 90  # 20% ≈ 80 (±min_val 影响)


def _make_training_df(n=420, seed=11):
    """合成 OHLCV + 因子 DataFrame (满足 min_samples/CV/特征选择流程)."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, n)))
    df = pd.DataFrame(index=idx)
    df["close"] = close
    df["open"] = df["close"].shift(1).fillna(close[0])
    df["high"] = df[["open", "close"]].max(axis=1) * 1.005
    df["low"] = df[["open", "close"]].min(axis=1) * 0.995
    df["volume"] = rng.uniform(8e5, 2e6, n)
    # 因子列: 有真实信号 + 噪声 (特征选择可分)
    df["mom_5"] = df["close"].pct_change(5)
    df["mom_20"] = df["close"].pct_change(20)
    df["vol_20"] = df["close"].pct_change().rolling(20).std()
    df["sma_ratio"] = df["close"] / df["close"].rolling(20).mean()
    df["noise_a"] = rng.normal(size=n)
    df["noise_b"] = rng.normal(size=n)
    return df


_CONFIG = {
    "label_horizon": 5,
    "min_samples": 200,
    "n_splits": 3,
    "test_ratio": 0.2,
    "feature_selection_threshold": 0.0,
    "top_n_features": 4,
    "early_stopping_rounds": 20,
    "lgb_params": {
        "n_estimators": 60,
        "learning_rate": 0.1,
        "num_leaves": 15,
        "min_child_samples": 20,
        "verbose": -1,
    },
}


class TestTrainSymbolEnhancedSplit:
    def test_result_carries_valid_audit_fields(self):
        df = _make_training_df()
        result = train_symbol_enhanced("TEST.SH", df, dict(_CONFIG))
        assert result["status"] == "OK"
        # n_valid: 早停/选优验证集规模 (从训练段内部切出)
        assert result["n_valid"] > 0
        assert result["n_valid"] + result["n_test"] < result["n_samples"]
        # final_metrics 携带验证集口径 (选优可审计), test 指标独立
        fm = result["final_metrics"]
        assert "valid_r2" in fm
        assert np.isfinite(fm["r2"]) and np.isfinite(fm["valid_r2"])
        # cv_after_selection 显式标注为 in-sample 诊断口径
        assert "in-sample" in result.get("cv_after_selection_note", "")

    def test_final_model_trained_with_internal_valid_not_test(self, monkeypatch):
        """截获 train_lgb_with_fallback: 早停 eval 集不得等于测试段。"""
        import lgb_trainer.trainer as trainer_mod

        captured: dict[str, tuple] = {}
        real_fn = trainer_mod.train_lgb_with_fallback

        def _spy(X_train, y_train, X_eval, y_eval, config, log_tag=""):
            captured.setdefault("evals", []).append((X_eval, y_eval, log_tag))
            return real_fn(X_train, y_train, X_eval, y_eval, config, log_tag=log_tag)

        monkeypatch.setattr(trainer_mod, "train_lgb_with_fallback", _spy)
        df = _make_training_df(seed=23)
        result = train_symbol_enhanced("TEST.SH", df, dict(_CONFIG))
        assert result["status"] == "OK"

        # 重构测试段的特征矩阵 (test 未参与早停 → final/adaptive 的 eval 行数 == n_valid)
        n_test = result["n_test"]
        n_valid = result["n_valid"]
        selected = result["selected_features"]
        x_test = df[selected].iloc[-n_test:].to_numpy(dtype=np.float64)
        final_evals = [
            (X_eval, y_eval_)
            for X_eval, y_eval_, tag in captured["evals"]
            if tag.endswith("-final") or tag.endswith("-adaptive")
        ]
        assert final_evals, "未捕获到 final/adaptive 训练调用"
        for X_eval, _y_eval in final_evals:
            assert len(X_eval) == n_valid
            # eval 尾行 ≠ 测试段尾行 (测试段未用于早停)
            if n_test > 0 and len(X_eval) > 0:
                assert not np.allclose(X_eval[-1], np.nan_to_num(x_test[-1]))


class TestCvEvaluateFoldIsolation:
    def test_fold_metrics_on_untouched_test_segment(self, monkeypatch):
        """每折: 早停 eval 来自折内训练段, 折指标在折外测试段计算。"""
        import lgb_trainer.metrics as metrics_mod

        captured: dict[str, list] = {"eval_sizes": [], "test_sizes": []}

        real_fn = metrics_mod.__dict__.get("time_series_cv_evaluate")

        # 直接拦截 train_fn 注入点 (time_series_cv_evaluate 支持 train_fn 参数)
        def _train_fn(X_train, y_train, X_eval, y_eval, config, log_tag=""):
            captured["eval_sizes"].append(len(X_eval))
            from lightgbm import LGBMRegressor, early_stopping

            model = LGBMRegressor(n_estimators=30, verbose=-1)
            model.fit(X_train, y_train, eval_set=[(X_eval, y_eval)],
                      callbacks=[early_stopping(10, verbose=False)])
            return model, "cpu"

        rng = np.random.default_rng(3)
        n = 400
        X = rng.normal(size=(n, 4))
        y = 0.3 * X[:, 0] + 0.1 * rng.normal(size=n)
        result = time_series_cv_evaluate(X, y, dict(_CONFIG), n_splits=3, code="T", train_fn=_train_fn)
        assert len(result["fold_metrics"]) >= 2
        # 每折 eval 来自折内训练段 (~15%), 折 test_size 与 eval 无关
        for fm in result["fold_metrics"]:
            assert fm["test_size"] >= 10
        assert all(s < 200 for s in captured["eval_sizes"])
        del real_fn
