"""trendcast_audit 单元测试（临时目录 + 注入 price_source）。"""

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import trendcast_audit as ta
from trendcast_audit import TrendCastAudit


def test_record_and_stats(tmp_path):
    a = TrendCastAudit(audit_dir=str(tmp_path / "audit"))
    a.record_prediction("600519.SH", "short_term", "看涨", 0.62)
    a.record_prediction("000858.SZ", "mid_term", "看跌", 0.55)
    stats = a.get_stats()
    assert stats["total_predictions"] == 2
    assert stats["verified"] == 0


def test_verify_hit_and_miss(tmp_path):
    a = TrendCastAudit(audit_dir=str(tmp_path / "audit"))
    a.record_prediction(
        "600519.SH", "short_term", "看涨", 0.62, predicted_at="2020-01-01T00:00:00"
    )
    a.record_prediction(
        "000858.SZ", "short_term", "看跌", 0.55, predicted_at="2020-01-01T00:00:00"
    )

    def price_up(symbol, predicted_at, hd):
        return 0.05  # 真实上涨：看涨命中，看跌不命中

    a.verify_predictions(price_source=price_up)
    stats = a.get_stats()
    assert stats["verified"] == 2
    assert stats["hits"] == 1
    assert abs(stats["hit_rate"] - 0.5) < 1e-9


def test_verify_unverified_when_no_price(tmp_path):
    a = TrendCastAudit(audit_dir=str(tmp_path / "audit"))
    a.record_prediction(
        "X.SH", "short_term", "看涨", 0.6, predicted_at="2020-01-01T00:00:00"
    )
    a.verify_predictions(price_source=lambda s, p, h: None)
    assert a.get_stats()["verified"] == 0  # 无行情 -> 保持未验证，不误判


def test_verify_skips_unexpired(tmp_path):
    a = TrendCastAudit(audit_dir=str(tmp_path / "audit"))
    a.record_prediction(
        "X.SH", "long_term", "看涨", 0.6, predicted_at="2099-01-01T00:00:00"
    )
    a.verify_predictions(price_source=lambda s, p, h: 0.1)
    assert a.get_stats()["verified"] == 0


# ---------- 默认 price_source：代码规范化 + 多目录搜索（真实行情判据） ----------
def test_normalize_code():
    assert ta._normalize_code("601088.SH") == "601088"
    assert ta._normalize_code("000858.SZ") == "000858"
    assert ta._normalize_code("510300.SH") == "510300"
    assert ta._normalize_code("600000") == "600000"
    assert ta._normalize_code("") == ""


def _make_kline_parquet(root: Path, code: str, base_close: float = 10.0):
    """造一个标准本地行情 parquet（date/open/high/low/close/volume）。"""
    sub = root / "klines"
    sub.mkdir(parents=True, exist_ok=True)
    dates = pd.bdate_range("2026-01-05", periods=40)
    close = base_close * (1 + 0.01 * pd.Series(range(40)))  # 单调上涨 1%/日
    df = pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1000.0,
        }
    )
    df.to_parquet(sub / f"{code}.parquet")
    return sub / f"{code}.parquet"


def test_default_price_source_strips_suffix_and_finds(tmp_path):
    _make_kline_parquet(tmp_path, "601088")
    # 1 月 5 日起 40 个交易日；预测于 2026-01-06 收盘后 → 之后 5 个交易日收益 > 0
    ret = ta._default_price_source(
        "601088.SH", "2026-01-06T15:00:00", 5, roots=[tmp_path]
    )
    assert ret is not None and ret > 0


def test_default_price_source_exact_name_first(tmp_path):
    _make_kline_parquet(tmp_path, "600519")
    # 同名模糊干扰文件不应影响精确命中（精确模式先于模糊模式入队）
    noise = tmp_path / "klines"
    (noise / "1600519x.parquet").write_bytes(b"not a parquet")
    ret = ta._default_price_source(
        "600519.SH", "2026-01-06T15:00:00", 5, roots=[tmp_path]
    )
    assert ret is not None and ret > 0


def test_default_price_source_missing_returns_none(tmp_path):
    assert (
        ta._default_price_source("999999.SH", "2026-01-06T15:00:00", 5, roots=[tmp_path])
        is None
    )
