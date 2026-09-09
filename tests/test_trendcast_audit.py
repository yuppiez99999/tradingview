"""trendcast_audit 单元测试（临时目录 + 注入 price_source）。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
