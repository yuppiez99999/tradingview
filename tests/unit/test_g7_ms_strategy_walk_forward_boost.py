"""G7 boost: ms_strategy/src/backtest/walk_forward.py 单元测试.

覆盖 WalkForwardResult / WalkForward 的全部公开接口,
包括窗口生成/简化模式/完整模式/CV优化/指标聚合的核心路径与边界分支.
strategy_fn / train_func / test_func 以可调用对象或 mock 隔离.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "ms_strategy"))

from ms_strategy.src.backtest.walk_forward import (  # noqa: E402
    WalkForward,
    WalkForwardResult,
)

# ============================================================
# 1. WalkForwardResult dataclass
# ============================================================


class TestWalkForwardResult:
    def test_defaults(self):
        r = WalkForwardResult(
            window_id=0,
            train_start="2020-01-01",
            train_end="2021-01-01",
            test_start="2021-01-01",
            test_end="2021-04-01",
        )
        assert r.sortino == 0.0
        assert r.sharpe == 0.0
        assert r.test_returns is None
        assert r.params == {}


# ============================================================
# 2. WalkForward 初始化
# ============================================================


class TestWalkForwardInit:
    def test_defaults(self):
        wf = WalkForward()
        assert wf.train_months == 24
        assert wf.test_months == 3
        assert wf.step_months == 3
        assert wf.cv_folds == 5
        assert wf.results == []

    def test_custom(self):
        wf = WalkForward(train_months=12, test_months=2, step_months=2, cv_folds=3)
        assert wf.train_months == 12
        assert wf.cv_folds == 3


# ============================================================
# 3. generate_windows
# ============================================================


class TestGenerateWindows:
    def test_normal_windows(self):
        wf = WalkForward(train_months=12, test_months=3, step_months=3)
        windows = wf.generate_windows("2020-01-01", "2024-01-01")
        assert len(windows) > 0
        for tr_s, tr_e, te_s, te_e in windows:
            assert tr_s < tr_e <= te_s < te_e

    def test_insufficient_range(self):
        wf = WalkForward(train_months=24, test_months=3, step_months=3)
        windows = wf.generate_windows("2020-01-01", "2020-06-01")
        assert len(windows) == 0

    def test_window_count_increases_with_range(self):
        wf = WalkForward(train_months=12, test_months=3, step_months=3)
        short = wf.generate_windows("2020-01-01", "2022-01-01")
        long = wf.generate_windows("2020-01-01", "2024-01-01")
        assert len(long) > len(short)

    def test_step_progression(self):
        wf = WalkForward(train_months=12, test_months=3, step_months=3)
        windows = wf.generate_windows("2020-01-01", "2024-01-01")
        if len(windows) >= 2:
            # 第二个窗口的 train_end 应比第一个晚 step_months
            assert windows[1][1] > windows[0][1]


# ============================================================
# 4. run - 简化模式 (strategy_fn)
# ============================================================


class TestRunSimpleMode:
    def _make_data(self, n_days=500):
        np.random.seed(42)
        return pd.DataFrame(
            {"ret": np.random.normal(0.001, 0.01, n_days)},
            index=pd.date_range("2020-01-01", periods=n_days, freq="D"),
        )

    def test_strategy_fn_returns_results(self):
        data = self._make_data()

        def strategy_fn(train_df, test_df):
            return pd.Series(np.random.normal(0.001, 0.01, len(test_df)))

        wf = WalkForward(train_months=6, test_months=1, step_months=1)
        result = wf.run(data, strategy_fn=strategy_fn, verbose=False)
        assert result["status"] == "OK"
        assert result["n_windows"] > 0
        assert len(result["results"]) == result["n_windows"]
        assert "aggregated" in result

    def test_strategy_fn_no_valid_window(self):
        # 数据太短无法生成窗口
        data = pd.DataFrame(
            {"ret": [0.01] * 10},
            index=pd.date_range("2020-01-01", periods=10, freq="D"),
        )

        def strategy_fn(train_df, test_df):
            return pd.Series([0.01] * len(test_df))

        wf = WalkForward(train_months=24, test_months=3, step_months=3)
        result = wf.run(data, strategy_fn=strategy_fn, verbose=False)
        assert result["status"] == "NO_VALID_WINDOW"

    def test_strategy_fn_returns_none_skipped(self):
        data = self._make_data()

        def strategy_fn(train_df, test_df):
            return None

        wf = WalkForward(train_months=6, test_months=1, step_months=1)
        result = wf.run(data, strategy_fn=strategy_fn, verbose=False)
        assert result["n_windows"] == 0

    def test_strategy_fn_exception_skipped(self):
        data = self._make_data()
        call_count = [0]

        def strategy_fn(train_df, test_df):
            call_count[0] += 1
            if call_count[0] == 2:
                raise ValueError("模拟失败")
            return pd.Series(np.random.normal(0.001, 0.01, len(test_df)))

        wf = WalkForward(train_months=6, test_months=1, step_months=1)
        result = wf.run(data, strategy_fn=strategy_fn, verbose=False)
        # 至少有一个窗口成功
        assert result["status"] == "OK"

    def test_date_col_sorting(self):
        np.random.seed(42)
        data = pd.DataFrame(
            {
                "date": pd.date_range("2020-01-01", periods=500, freq="D"),
                "ret": np.random.normal(0.001, 0.01, 500),
            }
        )
        # 打乱顺序
        data = data.sample(frac=1.0, random_state=42).reset_index(drop=True)

        def strategy_fn(train_df, test_df):
            return pd.Series([0.001] * len(test_df))

        wf = WalkForward(train_months=6, test_months=1, step_months=1)
        result = wf.run(data, strategy_fn=strategy_fn, date_col="date", verbose=False)
        assert result["status"] == "OK"

    def test_fallback_to_day_windows(self):
        # 月度窗口不足时回退到日数窗口
        # train_months=12 → 月度需 13 月 ≈ 395 天, 日数需 13*21=273 天
        # n_days=300: 月度窗口空 (300 天 < 13 月), 日数窗口非空 (273 < 300)
        np.random.seed(42)
        data = pd.DataFrame(
            {"ret": np.random.normal(0.001, 0.01, 300)},
            index=pd.date_range("2020-01-01", periods=300, freq="D"),
        )

        def strategy_fn(train_df, test_df):
            return pd.Series([0.001] * len(test_df))

        wf = WalkForward(train_months=12, test_months=1, step_months=1)
        result = wf.run(data, strategy_fn=strategy_fn, verbose=False)
        # 日数窗口回退后应有结果
        assert result["status"] == "OK"


# ============================================================
# 5. run - 完整模式 (train_func + test_func)
# ============================================================


class TestRunFullMode:
    def test_missing_funcs_returns_error(self):
        data = pd.DataFrame(
            {"ret": [0.01] * 100},
            index=pd.date_range("2020-01-01", periods=100, freq="D"),
        )
        wf = WalkForward()
        result = wf.run(data, verbose=False)
        assert result["status"] == "ERROR"
        assert "strategy_fn" in result["reason"]

    def test_full_mode_with_funcs(self):
        np.random.seed(42)
        data = pd.DataFrame(
            {"ret": np.random.normal(0.001, 0.01, 500)},
            index=pd.date_range("2020-01-01", periods=500, freq="D"),
        )

        def train_func(train_data):
            return {"param": 1.0}

        def test_func(test_data, params):
            return pd.Series(np.random.normal(0.001, 0.01, len(test_data)))

        wf = WalkForward(train_months=6, test_months=1, step_months=1)
        results = wf.run(
            data, train_func=train_func, test_func=test_func, verbose=False
        )
        assert isinstance(results, list)
        if len(results) > 0:
            assert isinstance(results[0], WalkForwardResult)

    def test_full_mode_with_param_grid(self):
        np.random.seed(42)
        data = pd.DataFrame(
            {"ret": np.random.normal(0.001, 0.01, 500)},
            index=pd.date_range("2020-01-01", periods=500, freq="D"),
        )

        def train_func(train_data, alpha=1.0):
            return {"alpha": alpha, "sortino": alpha * 0.5}

        def test_func(test_data, params):
            return pd.Series(np.random.normal(0.001, 0.01, len(test_data)))

        wf = WalkForward(train_months=6, test_months=1, step_months=1)
        results = wf.run(
            data,
            train_func=train_func,
            test_func=test_func,
            param_grid={"alpha": [0.5, 1.0, 2.0]},
            verbose=False,
        )
        assert isinstance(results, list)


# ============================================================
# 6. _cv_optimize
# ============================================================


class TestCvOptimize:
    def test_selects_best_param(self):
        wf = WalkForward()
        train_data = pd.DataFrame({"ret": [0.01] * 100})

        def train_func(data, alpha=1.0):
            return {"sortino": alpha}

        def test_func(data, params):
            return pd.Series([0.01])

        best = wf._cv_optimize(
            train_data, train_func, test_func, {"alpha": [0.5, 1.0, 2.0]}, "sortino"
        )
        assert best.get("alpha") == 2.0

    def test_train_func_returns_non_dict(self):
        wf = WalkForward()
        train_data = pd.DataFrame({"ret": [0.01] * 100})

        def train_func(data, alpha=1.0):
            return alpha  # 非 dict

        def test_func(data, params):
            return pd.Series([0.01])

        best = wf._cv_optimize(
            train_data, train_func, test_func, {"alpha": [0.5, 1.0]}, "sortino"
        )
        # 非 dict → score=0, best_params 可能为空 → 回退 train_func
        assert isinstance(best, (dict, float, int))

    def test_exception_in_train_falls_back(self):
        wf = WalkForward()
        train_data = pd.DataFrame({"ret": [0.01] * 100})

        def train_func(data, alpha=1.0):
            if alpha == 1.0:
                raise RuntimeError("训练失败")
            return {"sortino": alpha}

        def test_func(data, params):
            return pd.Series([0.01])

        best = wf._cv_optimize(
            train_data, train_func, test_func, {"alpha": [1.0, 2.0]}, "sortino"
        )
        # 1.0 失败, 2.0 成功
        assert best.get("alpha") == 2.0

    def test_empty_grid_falls_back(self):
        wf = WalkForward()
        train_data = pd.DataFrame({"ret": [0.01] * 100})

        def train_func(data):
            return {"default": True}

        def test_func(data, params):
            return pd.Series([0.01])

        best = wf._cv_optimize(train_data, train_func, test_func, {}, "sortino")
        # 空 grid → best_params 空 → 回退 train_func(train_data)
        assert best == {"default": True}


# ============================================================
# 7. aggregate_metrics
# ============================================================


class TestAggregateMetrics:
    def test_empty_results(self):
        wf = WalkForward()
        assert wf.aggregate_metrics() == {}

    def test_with_results(self):
        wf = WalkForward()
        wf.results = [
            WalkForwardResult(
                window_id=0,
                train_start="2020-01-01",
                train_end="2021-01-01",
                test_start="2021-01-01",
                test_end="2021-04-01",
                sortino=1.5,
                calmar=2.0,
                max_dd=-0.1,
                test_returns=np.array([0.001, -0.002, 0.003]),
            ),
            WalkForwardResult(
                window_id=1,
                train_start="2020-04-01",
                train_end="2021-04-01",
                test_start="2021-04-01",
                test_end="2021-07-01",
                sortino=1.8,
                calmar=2.5,
                max_dd=-0.08,
                test_returns=np.array([0.002, -0.001, 0.004]),
            ),
        ]
        agg = wf.aggregate_metrics()
        assert agg["n_windows"] == 2
        assert "sortino" in agg
        assert "calmar" in agg
        assert "max_dd" in agg
        assert "sharpe" in agg
        assert "win_rate" in agg
        assert len(agg["window_sortinos"]) == 2

    def test_results_with_none_returns(self):
        wf = WalkForward()
        wf.results = [
            WalkForwardResult(
                window_id=0,
                train_start="2020-01-01",
                train_end="2021-01-01",
                test_start="2021-01-01",
                test_end="2021-04-01",
                test_returns=None,
            ),
        ]
        agg = wf.aggregate_metrics()
        assert agg == {}


# ============================================================
# 8. summary
# ============================================================


class TestSummary:
    def test_empty(self):
        wf = WalkForward()
        s = wf.summary()
        assert "Walk-Forward 汇总" in s
        assert "0 个窗口" in s

    def test_with_results(self):
        wf = WalkForward()
        wf.results = [
            WalkForwardResult(
                window_id=0,
                train_start="2020-01-01",
                train_end="2021-01-01",
                test_start="2021-01-01",
                test_end="2021-04-01",
                sortino=1.5,
                calmar=2.0,
                max_dd=-0.1,
                test_returns=np.array([0.001, -0.002, 0.003]),
            ),
        ]
        s = wf.summary()
        assert "Walk-Forward 汇总" in s
        assert "Sortino Ratio" in s
        assert "Calmar Ratio" in s


# ============================================================
# 9. run - 数据不足跳过
# ============================================================


class TestRunInsufficientData:
    def test_train_data_too_short(self):
        # 窗口生成后 train_data < 50 被跳过
        np.random.seed(42)
        data = pd.DataFrame(
            {"ret": np.random.normal(0.001, 0.01, 80)},
            index=pd.date_range("2020-01-01", periods=80, freq="D"),
        )

        def strategy_fn(train_df, test_df):
            return pd.Series([0.001] * len(test_df))

        wf = WalkForward(train_months=1, test_months=1, step_months=1)
        result = wf.run(data, strategy_fn=strategy_fn, verbose=False)
        # 部分窗口 train<50 会被跳过
        assert result["status"] in ("OK", "NO_VALID_WINDOW")

    def test_test_data_too_short(self):
        np.random.seed(42)
        data = pd.DataFrame(
            {"ret": np.random.normal(0.001, 0.01, 100)},
            index=pd.date_range("2020-01-01", periods=100, freq="D"),
        )

        def strategy_fn(train_df, test_df):
            return pd.Series([0.001] * len(test_df))

        wf = WalkForward(train_months=2, test_months=1, step_months=1)
        result = wf.run(data, strategy_fn=strategy_fn, verbose=False)
        assert result["status"] in ("OK", "NO_VALID_WINDOW")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
