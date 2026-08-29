"""broker_factory + FillsStore 单测.

验证 G1 装配门控 (四重门控 fail-open) 与 G4 成交落盘事实源 (无重复计数)。
broker_factory: 默认 disabled -> SimulatedBroker; 单测用最小 config 覆盖。
FillsStore: 进程内单例, 落盘 JSONL, load_day 不重复计数; 用 tmp_path 隔离落盘。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import utils.execution.fills_store as fs_mod
from utils.execution.broker_factory import get_broker


# ---------- broker_factory ----------
def test_get_broker_default_disabled_returns_simulated():
    broker = get_broker({"enabled": False})
    assert broker.__class__.__name__ == "SimulatedBroker"


def test_get_broker_dry_run_returns_simulated():
    broker = get_broker({"enabled": True, "dry_run": True})
    assert broker.__class__.__name__ == "SimulatedBroker"


def test_get_broker_enabled_not_production_returns_simulated(monkeypatch):
    monkeypatch.setenv("TRADING_ENV", "sim")
    broker = get_broker({"enabled": True, "dry_run": False})
    assert broker.__class__.__name__ == "SimulatedBroker"


def test_get_broker_production_without_xtquant_returns_simulated(monkeypatch):
    # 即使 hard 条件满足, xtquant 未装 -> QMT 降级 SimulatedBroker (不裸实盘)
    monkeypatch.setenv("TRADING_ENV", "production")
    broker = get_broker({"enabled": True, "dry_run": False})
    assert broker.__class__.__name__ == "SimulatedBroker"


# ---------- FillsStore ----------
def _make_file_path(tmp_path):
    def _fp(date):
        p = Path(tmp_path) / "reports" / "fills" / f"fills_{date}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    return _fp


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """隔离 FillsStore 单例的落盘目录, 避免污染 reports/."""
    monkeypatch.setattr(fs_mod, "_PROJECT_ROOT", str(tmp_path))
    fs_mod.FillsStore._instance = None
    store = fs_mod.FillsStore()
    monkeypatch.setattr(store, "_file_path", _make_file_path(tmp_path))
    yield store
    fs_mod.FillsStore._instance = None


def test_record_fill_returns_record(isolated_store):
    rec = isolated_store.record_fill("600519.SH", "BUY", 1000, 100.5)
    assert rec["symbol"] == "600519.SH"
    assert rec["filled_qty"] == 1000.0
    assert rec["avg_price"] == 100.5


def test_load_day_no_duplicate_counting(isolated_store):
    # G4 修复验证: 同一进程内 record 后 load, 不应重复
    for i in range(15):
        isolated_store.record_fill(f"SYM{i}.SH", "BUY", 100, 10.0 + i)
    records = isolated_store.load_day()
    assert len(records) == 15


def test_load_day_persists_across_instances(isolated_store, tmp_path, monkeypatch):
    isolated_store.record_fill("600519.SH", "SELL", 500, 200.0)
    fs_mod.FillsStore._instance = None
    monkeypatch.setattr(fs_mod, "_PROJECT_ROOT", str(tmp_path))
    store2 = fs_mod.FillsStore()
    monkeypatch.setattr(store2, "_file_path", _make_file_path(tmp_path))
    recs = store2.load_day()
    assert len(recs) == 1
    assert recs[0]["side"] == "SELL"


def test_latest_avg_price_by_symbol(isolated_store):
    isolated_store.record_fill("600519.SH", "BUY", 100, 100.0)
    isolated_store.record_fill("600519.SH", "BUY", 100, 110.0)
    prices = isolated_store.latest_avg_price_by_symbol()
    assert prices["600519.SH"] == pytest.approx(110.0)


def test_record_fill_then_latest_avg(isolated_store):
    assert isolated_store.latest_avg_price_by_symbol() == {}
