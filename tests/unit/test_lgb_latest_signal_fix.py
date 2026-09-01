"""P1-1 回归测试: LGB 训练「当前信号」必须使用 dropna 前的真实最新特征行.

背景 (2026-09-01 代码质量扫描):
    trainer 在构造 target = pct_change(h).shift(-h) 后立即 dropna(),
    丢弃了最后 h 行 (target=NaN)。若推理时用 df.iloc[-1:] 取"最新行",
    实际取到的是 T-h 行 —— 信号系统性滞后 label_horizon 个交易日,
    且预测的是已经实现的历史收益。

测试设计 (末行敏感性):
    末行在 dropna 后被丢弃、不参与训练, 因此固定随机种子下修改末行特征
    不会改变训练出的模型, 只应改变"当前信号"的推理输入。
    - 若代码正确 (用 T 行): 两次 raw_prediction 不同
    - 若代码有 bug (用 T-5 行): 两次 raw_prediction 完全相同
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("lightgbm")

from lgb_trainer.trainer import train_symbol_enhanced  # noqa: E402


def _make_df(n: int = 260, last_feat_a: float | None = None) -> pd.DataFrame:
    """构造确定性 OHLCV + 1 个特征的 DataFrame (无随机性)."""
    rng = np.arange(n, dtype=float)
    close = 100.0 + np.sin(rng / 10.0) * 5.0 + rng * 0.05
    feat_a = np.sin(rng / 5.0)
    df = pd.DataFrame(
        {
            "open": close * 0.99,
            "high": close * 1.01,
            "low": close * 0.98,
            "close": close,
            "volume": 1_000_000.0 + rng * 100.0,
            "feat_a": feat_a,
        },
        index=pd.date_range("2025-01-01", periods=n, freq="D"),
    )
    if last_feat_a is not None:
        df.iloc[-1, df.columns.get_loc("feat_a")] = last_feat_a
    return df


def _make_config(label_horizon: int = 5) -> dict:
    return {
        "label_horizon": label_horizon,
        "min_samples": 50,
        "n_splits": 3,
        "test_ratio": 0.2,
        "feature_selection_threshold": 0.0,  # 保留全部特征
        "top_n_features": 10,
        "early_stopping_rounds": 10,
        "lgb_params": {
            "n_estimators": 40,
            "learning_rate": 0.1,
            "max_depth": 3,
            "verbose": -1,
            "random_state": 42,
        },
    }


class TestLatestSignalUsesTrueLatestRow:
    def test_signal_sensitive_to_latest_row(self):
        """当前信号必须对末行特征敏感 (bug 场景下末行被丢弃, 信号不变)."""
        r1 = train_symbol_enhanced("TEST.SH", _make_df(last_feat_a=0.999), _make_config())
        r2 = train_symbol_enhanced("TEST.SH", _make_df(last_feat_a=-0.999), _make_config())
        assert r1["status"] == "OK" and r2["status"] == "OK"

        # 判别力前提: 同一模型对 +0.999 / -0.999 的输出必须不同
        model = r1["model"]
        p_hi = float(model.predict(np.asarray([[0.999]], dtype=np.float64))[0])
        p_lo = float(model.predict(np.asarray([[-0.999]], dtype=np.float64))[0])
        assert p_hi != pytest.approx(p_lo, abs=1e-9), "测试无判别力: 模型对两个极端特征值输出相同"

        # 核心: 末行特征不同 → 当前信号必须不同
        assert r1["raw_prediction"] != pytest.approx(r2["raw_prediction"], abs=1e-9), (
            "当前信号对末行特征不敏感: 推理用的不是真实最新行 (P1-1 bug)"
        )

    def test_signal_matches_prediction_on_true_latest_row(self):
        """raw_prediction 必须等于模型对原始 df 最后一行的预测."""
        df = _make_df(last_feat_a=0.999)
        result = train_symbol_enhanced("TEST.SH", df, _make_config())
        assert result["status"] == "OK"

        model, feats = result["model"], result["selected_features"]
        latest = np.nan_to_num(
            df[feats].iloc[[-1]].values.astype(np.float64), nan=0.0, posinf=0.0, neginf=0.0
        )
        # raw_prediction 保留 6 位小数, 容差取 1e-6 (当前数据下 T 与 T-5 预测差 4e-6, 可区分)
        assert result["raw_prediction"] == pytest.approx(
            float(model.predict(latest)[0]), abs=1e-6
        )

    def test_tscv_trainer_signal_sensitive_to_latest_row(self):
        """lgb_tscv_trainer 存在同构 bug (horizon=1), 同样验证末行敏感性."""
        from lgb_tscv_trainer import train_symbol_with_cv  # noqa: PLC0415

        r1 = train_symbol_with_cv("TEST.SH", _make_df(last_feat_a=0.999), _make_config(label_horizon=1))
        r2 = train_symbol_with_cv("TEST.SH", _make_df(last_feat_a=-0.999), _make_config(label_horizon=1))
        if r1["status"] == "SKIP" or r2["status"] == "SKIP":
            pytest.skip("tscv trainer 样本不足")
        assert r1["raw_prediction"] != pytest.approx(r2["raw_prediction"], abs=1e-9), (
            "tscv 当前信号对末行特征不敏感: 推理用的不是真实最新行 (P1-1 bug)"
        )
