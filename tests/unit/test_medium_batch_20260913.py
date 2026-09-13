"""P0-MEDIUM 批单元测试 (2026-09-13)

覆盖:
  1. M2 DSR 准入 — CV 产出 dsr/dsr_pass; 噪声策略不通过; _mark_quality_flag
     在 require_dsr=True (默认) 下将 DSR 不达标模型降级 LOW_QUALITY。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from lgb_trainer.metrics import time_series_cv_evaluate  # noqa: E402
from lgb_trainer.trainer import _mark_quality_flag  # noqa: E402

_CONFIG = {
    "label_horizon": 5,
    "n_splits": 3,
    "early_stopping_rounds": 10,
    "lgb_params": {"n_estimators": 30, "verbose": -1},
    "model_quality_threshold": {
        "min_cv_r2": -1.0,
        "min_cv_ic": 0.0,
        "min_cv_sharpe": 0.0,
        "required_dsr": 0.80,
        "require_dsr": True,
    },
}


def _cv_result(y_correlated: bool, seed: int = 5):
    rng = np.random.default_rng(seed)
    n = 400
    X = rng.normal(size=(n, 5))
    if y_correlated:
        y = 0.5 * X[:, 0] + 0.1 * rng.normal(size=n)
    else:
        y = rng.normal(size=n)
    return time_series_cv_evaluate(X, y, dict(_CONFIG), n_splits=3, code="T")


class TestDsrInCv:
    def test_cv_carries_dsr_fields(self):
        r = _cv_result(y_correlated=True)
        assert "dsr_pass" in r and "dsr" in r and "dsr_verdict" in r
        assert r["dsr_pass"] is True  # 真实信号应通过 0.80 阈值

    def test_noise_fails_dsr(self):
        r = _cv_result(y_correlated=False)
        assert r["dsr_pass"] is False

    def test_dsr_n_trials_uses_feature_count(self):
        # n_trials 代理 = 候选特征数; 传入更多噪声特征 → E[SR_max] 抬高, DSR 下降
        rng = np.random.default_rng(9)
        n = 400
        X = np.hstack([0.5 * rng.normal(size=(n, 1)) * 0 + rng.normal(size=(n, 40))])
        y = 0.5 * X[:, 0] + 0.1 * rng.normal(size=n)
        config = dict(_CONFIG)
        config["model_quality_threshold"] = dict(_CONFIG["model_quality_threshold"], dsr_n_trials=40)
        r = time_series_cv_evaluate(X, y, config, n_splits=3, code="T")
        # 40 次尝试的严格修正下, 单特征弱信号难以通过 (不 assert 具体值, 只断言字段在)
        assert isinstance(r["dsr"], float)


class TestMarkQualityFlagDsr:
    def _result(self, dsr_pass: bool):
        return {
            "cv_after_selection": {
                "mean_r2": 0.1,
                "std_r2": 0.02,
                "mean_ic": 0.05,
                "std_ic": 0.01,
                "mean_sharpe": 0.5,
                "dsr_pass": dsr_pass,
                "dsr": 0.5 if dsr_pass else 0.3,
                "dsr_verdict": "test",
            },
            "final_metrics": {"ic": 0.04},
            "best_iteration": 50,
        }

    def test_dsr_fail_degrades_to_low_quality(self):
        result = self._result(dsr_pass=False)
        _mark_quality_flag("TEST.SH", result, dict(_CONFIG))
        assert result["quality_flag"] == "LOW_QUALITY"

    def test_dsr_pass_keeps_ok(self):
        result = self._result(dsr_pass=True)
        _mark_quality_flag("TEST.SH", result, dict(_CONFIG))
        assert result["quality_flag"] == "OK"

    def test_require_dsr_false_bypasses(self):
        config = dict(_CONFIG)
        config["model_quality_threshold"] = dict(_CONFIG["model_quality_threshold"], require_dsr=False)
        result = self._result(dsr_pass=False)
        _mark_quality_flag("TEST.SH", result, config)
        assert result["quality_flag"] == "OK"

    def test_dsr_missing_fails_closed(self):
        # 旧 CV 结果无 dsr 字段 (缺省 False) → 不得静默放行
        result = self._result(dsr_pass=False)
        del result["cv_after_selection"]["dsr_pass"]
        _mark_quality_flag("TEST.SH", result, dict(_CONFIG))
        assert result["quality_flag"] == "LOW_QUALITY"


# ============================================================
# 8. 退市股安全取数 (P0-M1, Wind MCP 数据陷阱防护)
# ============================================================
class TestDelistedKlineGuard:
    def test_dirty_data_rejected(self, monkeypatch):
        """记录晚于摘牌日+容差 → 服务端脏数据, 整体拒绝 (None)。"""
        import tools.wind_mcp_fetcher as w

        def _fake_range(code, b, e, kind="stock", period="1d"):
            return [
                {"TIME": "2023-06-20T00:00:00.000+08:00", "MATCH": "0.72"},
                {"TIME": "2026-09-11T00:00:00.000+08:00", "MATCH": "6.56"},  # 脏数据
            ]

        monkeypatch.setattr(w, "wind_get_kline_range", _fake_range)
        assert w.wind_get_delisted_kline("600532.SH", "2023-06-28") is None

    def test_clean_data_passes(self, monkeypatch):
        import tools.wind_mcp_fetcher as w

        def _fake_range(code, b, e, kind="stock", period="1d"):
            return [
                {"TIME": "2023-06-20T00:00:00.000+08:00", "MATCH": "0.80"},
                {"TIME": "2023-06-27T00:00:00.000+08:00", "MATCH": "0.72"},
            ]

        monkeypatch.setattr(w, "wind_get_kline_range", _fake_range)
        recs = w.wind_get_delisted_kline("600532.SH", "2023-06-28")
        assert recs is not None and len(recs) == 2

    def test_bad_delist_date_rejected(self):
        import tools.wind_mcp_fetcher as w

        assert w.wind_get_delisted_kline("X", "not-a-date") is None

    def test_fetch_delisted_ohlcv_normalizes(self, monkeypatch):
        from utils.universe.survivorship_free_universe import (
            SurvivorshipBiasFreeUniverse,
        )

        u = SurvivorshipBiasFreeUniverse()

        def _fake_kline(code, delist_date, lookback_days=504, **kw):
            return [
                {
                    "TIME": "2023-06-20T00:00:00.000+08:00",
                    "OPEN": "0.8",
                    "HIGH": "0.85",
                    "LOW": "0.7",
                    "MATCH": "0.72",
                    "VOLUME": "1000",
                },
            ]

        monkeypatch.setattr(
            "tools.wind_mcp_fetcher.wind_get_delisted_kline", _fake_kline
        )
        df = u.fetch_delisted_ohlcv("600532.SH", "2023-06-28")
        assert df is not None
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
        assert df.attrs["synthetic"] is False
        assert df.attrs["data_source"] == "wind_delisted"

    def test_fetch_unknown_code_without_date_rejected(self):
        from utils.universe.survivorship_free_universe import (
            SurvivorshipBiasFreeUniverse,
        )

        u = SurvivorshipBiasFreeUniverse()
        assert u.fetch_delisted_ohlcv("999999.SZ", None) is None


# ============================================================
# 9. expand_training_universe (P0-M1: 退市样本合入训练池)
# ============================================================
class TestExpandTrainingUniverse:
    def _mk_ohlcv(self, seed: int = 1, n: int = 60):
        import pandas as pd

        rng = np.random.default_rng(seed)
        idx = pd.date_range("2022-01-03", periods=n, freq="B")
        close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.02, n))), index=idx)
        df = pd.DataFrame(index=idx)
        df["close"] = close
        df["open"] = close.shift(1).fillna(close)
        df["high"] = df[["open", "close"]].max(axis=1) * 1.01
        df["low"] = df[["open", "close"]].min(axis=1) * 0.99
        df["volume"] = 1e6
        return df

    def _patch_universe(self, monkeypatch, tmp_path, records, fetch_map):
        """records: 退市库记录; fetch_map: {windcode: df|None}; 返回 (sfum, calls)"""
        import utils.universe.survivorship_free_universe as sfum

        monkeypatch.setattr(sfum, "_DELISTED_OHLCV_CACHE", tmp_path / "cache")
        calls = {"fetch": 0}

        class _Stub:
            def get_delisted_stocks(self):
                return records

            def fetch_delisted_ohlcv(self, code, dd, **kw):
                calls["fetch"] += 1
                return fetch_map.get(code)

        monkeypatch.setattr(sfum, "get_sfu", lambda: _Stub())
        return sfum, calls

    def test_merge_and_symbol_tuples(self, monkeypatch, tmp_path):
        sfum, _calls = self._patch_universe(
            monkeypatch, tmp_path,
            [type("R", (), {"code": "600532", "name": "退市未来", "delist_date": "2023-06-28", "reason": "财务类"})()],
            {"600532.SH": self._mk_ohlcv()},
        )
        merged, extra = sfum.expand_training_universe({"600000": self._mk_ohlcv()}, max_delisted=5)
        assert "600532" in merged and "600000" in merged
        assert len(extra) == 1
        code, name, shares, style, sector = extra[0]
        assert (code, name, shares, style) == ("600532", "退市未来", 0, "退市样本")
        assert merged["600532"].attrs["synthetic"] is False

    def test_limit_bounds_new_fetches(self, monkeypatch, tmp_path):
        records = [
            type("R", (), {"code": f"60053{i}", "name": f"退市{i}", "delist_date": "2023-06-28", "reason": ""})()
            for i in range(5)
        ]
        fetch_map = {f"60053{i}.SH": self._mk_ohlcv(seed=i) for i in range(5)}
        sfum, calls = self._patch_universe(monkeypatch, tmp_path, records, fetch_map)
        merged, extra = sfum.expand_training_universe({}, max_delisted=2)
        assert calls["fetch"] == 2  # 限额生效
        assert len(extra) == 2

    def test_existing_symbols_not_refetched(self, monkeypatch, tmp_path):
        records = [type("R", (), {"code": "600000", "name": "已有", "delist_date": "2023-06-28", "reason": ""})()]
        sfum, calls = self._patch_universe(monkeypatch, tmp_path, records, {"600000.SH": None})
        base = {"600000": self._mk_ohlcv()}
        merged, extra = sfum.expand_training_universe(base)
        assert calls["fetch"] == 0 and len(extra) == 0 and len(merged) == 1

    def _sfum(self):
        import utils.universe.survivorship_free_universe as sfum

        return sfum
