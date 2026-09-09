"""TrendCast 信号卡片追加逻辑单测 (2026-09-09).

锁定三条不变量:
  1. 有快照 -> 追加卡片, 内容含标题与标的;
  2. 重复调用幂等 —— 日报重跑不会追加两份卡片;
  3. 快照缺失/无预测 -> 静默跳过 (fail-open), 不抛异常、不改文件。
"""

from __future__ import annotations

import json

from utils.reporting import trendcast_card
from utils.reporting.trendcast_card import CARD_TITLE, append_trendcast_card


def _write_snapshot(root, date_str: str, predictions):
    snap_dir = root / "logs" / "trendcast"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap = snap_dir / f"signals_{date_str}.json"
    snap.write_text(
        json.dumps(
            {
                "generated_at": f"{date_str}T18:51:46",
                "model_type": "lightgbm",
                "predictions": predictions,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return snap


def _prediction(symbol: str = "588080.SH") -> dict:
    return {
        "symbol": symbol,
        "sector": "",
        "horizons": {
            "short_term": {"direction": "看跌", "probability": 0.5, "model": "m1"},
            "mid_term": {"direction": "看涨", "probability": 0.62, "model": "m2"},
            "long_term": {"direction": "看跌", "probability": 0.5, "model": "m3"},
        },
    }


def test_append_card_then_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(trendcast_card, "_project_root", lambda: tmp_path)
    report = tmp_path / "daily_pnl_report_2026-09-09.md"
    report.write_text("# 日报\n\n正文\n", encoding="utf-8")

    _write_snapshot(tmp_path, "2026-09-09", [_prediction()])

    assert append_trendcast_card([str(report)], "2026-09-09") is True
    content = report.read_text(encoding="utf-8")
    assert CARD_TITLE in content
    assert "588080.SH" in content
    assert "看涨" in content

    # 幂等: 第二次调用不得再追加一份
    assert append_trendcast_card([str(report)], "2026-09-09") is False
    assert report.read_text(encoding="utf-8").count(CARD_TITLE) == 1


def test_missing_snapshot_skips_silently(tmp_path, monkeypatch):
    monkeypatch.setattr(trendcast_card, "_project_root", lambda: tmp_path)
    report = tmp_path / "daily_pnl_report_2026-09-09.md"
    original = "# 日报\n"
    report.write_text(original, encoding="utf-8")

    assert append_trendcast_card([str(report)], "2026-09-09") is False
    assert report.read_text(encoding="utf-8") == original


def test_empty_predictions_skips(tmp_path, monkeypatch):
    monkeypatch.setattr(trendcast_card, "_project_root", lambda: tmp_path)
    report = tmp_path / "daily_pnl_report_2026-09-09.md"
    report.write_text("# 日报\n", encoding="utf-8")

    _write_snapshot(tmp_path, "2026-09-09", [])

    assert append_trendcast_card([str(report)], "2026-09-09") is False
    assert CARD_TITLE not in report.read_text(encoding="utf-8")


def test_degenerate_probabilities_warned(tmp_path, monkeypatch):
    """概率恒 50% 时必须打警告, 提示该信号不可用于打分/下单."""
    monkeypatch.setattr(trendcast_card, "_project_root", lambda: tmp_path)
    flat = {
        "symbol": "588080.SH",
        "horizons": {
            h: {"direction": "看跌", "probability": 0.5} for h in ("short_term", "mid_term", "long_term")
        },
    }

    card = trendcast_card.build_card(
        {"generated_at": "t", "model_type": "lightgbm", "predictions": [flat]}
    )

    assert "警告" in card and "禁止用于打分/下单" in card


def test_build_card_truncates_to_20(tmp_path, monkeypatch):
    monkeypatch.setattr(trendcast_card, "_project_root", lambda: tmp_path)
    preds = [_prediction(f"{600000 + i}.SH") for i in range(26)]

    card = trendcast_card.build_card(
        {"generated_at": "t", "model_type": "lightgbm", "predictions": preds}
    )

    assert card.count("- **") == 20
    assert "仅显示前 20 只" in card
