"""trendcast_client 单元测试（mock HTTP，无网络）。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trendcast_client import TrendCastClient


class _FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, routes):
        self._routes = routes
        self.get_calls = []
        self.post_calls = []

    def get(self, url, params=None, timeout=None):
        self.get_calls.append((url, params))
        for key, payload in self._routes.items():
            if url.endswith(key):
                return _FakeResp(200, payload)
        return _FakeResp(404, {})

    def post(self, url, json=None, timeout=None):
        self.post_calls.append((url, json))
        for key, payload in self._routes.items():
            if url.endswith(key):
                return _FakeResp(200, payload)
        return _FakeResp(404, {})


def _make_client(routes):
    c = TrendCastClient(base_url="http://fake:8800")
    c.session = _FakeSession(routes)
    return c


def test_health_ok():
    c = _make_client({"/health": {"status": "ok", "models_loaded": 3}})
    h = c.health_check()
    assert h["status"] == "ok"
    assert "error" not in h


def test_health_unreachable_returns_error():
    c = _make_client({})  # 无任何路由 -> 404 -> error
    h = c.health_check()
    assert "error" in h


def test_portfolio_summary_normalizes():
    routes = {
        "/api/v1/portfolio/summary": {
            "generated_at": "2026-09-09T15:00:00",
            "model_type": "lightgbm",
            "predictions": [
                {
                    "symbol": "600519.SH",
                    "sector": "",
                    "horizons": {
                        "short_term": {
                            "direction": "看涨",
                            "probability": 0.62,
                            "model": "lightgbm_short_term_5d",
                        },
                        "mid_term": {
                            "direction": "看涨",
                            "probability": 0.71,
                            "model": "lightgbm_mid_term_10d",
                        },
                        "long_term": {
                            "direction": "看跌",
                            "probability": 0.55,
                            "model": "lightgbm_long_term_20d",
                        },
                    },
                }
            ],
            "meta": {"models_loaded": 3, "error_symbols": []},
        }
    }
    c = _make_client(routes)
    s = c.get_portfolio_summary(["600519.SH"])
    assert "error" not in s
    pred = s["predictions"][0]
    assert pred["horizons"]["short_term"]["direction"] == "看涨"
    assert pred["horizons"]["short_term"]["probability"] == 0.62


def test_portfolio_summary_fallback_to_batch():
    routes = {
        "/api/v1/predict/batch": {
            "count": 1,
            "results": [
                {
                    "symbol": "600519.SH",
                    "predictions": {
                        "short_term": {
                            "direction": "看涨",
                            "probability": 0.6,
                            "horizon_days": 5,
                        },
                        "mid_term": {"error": "无数据"},
                    },
                }
            ],
        }
    }
    c = _make_client(routes)
    s = c.get_portfolio_summary(["600519.SH"])
    assert "error" not in s
    pred = s["predictions"][0]
    assert pred["horizons"]["short_term"]["direction"] == "看涨"
    assert "mid_term" not in pred["horizons"]  # 错误周期被跳过


def test_load_position_symbols_dict(tmp_path):
    pos = tmp_path / "config" / "positions.json"
    pos.parent.mkdir(parents=True, exist_ok=True)
    pos.write_text(
        '{"positions": {"600519.SH": {"name": "茅台", "shares": 100}, '
        '"000858.SZ": {"name": "五粮液", "shares": 50}}}',
        encoding="utf-8",
    )
    syms = TrendCastClient.load_position_symbols(str(tmp_path))
    assert syms == ["600519.SH", "000858.SZ"]


def test_load_position_symbols_missing():
    syms = TrendCastClient.load_position_symbols(str(Path("/nonexistent_dir_xyz")))
    assert syms == []
