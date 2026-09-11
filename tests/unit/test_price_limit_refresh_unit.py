"""test_price_limit_refresh_unit.py — 涨跌停状态刷新 (Issue #13: S-2)

覆盖要点:
    - 主链注入路径 (风险上下文经 price_limit_status 生效)
    - 快照 limit_up/limit_down 优先, 缺失时按板块规则回退计算
    - ST ±5% 口径
    - 状态持久化 + as_of_date 过期判定 (stale)
    - 行情不可用 → 回退缓存且标记 stale, 不抛异常
"""

from __future__ import annotations

import pytest

from utils.price_limit_refresh import (
    STATUS_LIMIT_DOWN,
    STATUS_LIMIT_UP,
    STATUS_NORMAL,
    STATUS_UNKNOWN,
    compute_price_limit_status,
    load_price_limit_status,
    refresh_price_limit_status,
    save_price_limit_status,
)


class TestComputePriceLimitStatus:
    @pytest.mark.unit
    def test_uses_snapshot_limit_fields(self):
        quotes = {
            "600519": {"price": 1700.0, "pre_close": 1700.0, "limit_up": 1870.0, "limit_down": 1530.0},
            "000001": {"price": 11.0, "pre_close": 10.0, "limit_up": 11.0, "limit_down": 9.0},
        }
        status = compute_price_limit_status(["600519", "000001"], quotes=quotes)
        assert status["600519"] == STATUS_NORMAL
        assert status["000001"] == STATUS_LIMIT_UP

    @pytest.mark.unit
    def test_falls_back_to_board_rule_when_limit_fields_missing(self):
        # 创业板 ±20%: 前收 120 → 涨停 144 / 跌停 96
        quotes = {"300750": {"price": 144.0, "pre_close": 120.0}}
        assert compute_price_limit_status(["300750"], quotes=quotes)["300750"] == STATUS_LIMIT_UP
        quotes = {"300750": {"price": 95.0, "pre_close": 120.0}}
        assert (
            compute_price_limit_status(["300750"], quotes=quotes)["300750"]
            == STATUS_LIMIT_DOWN
        )

    @pytest.mark.unit
    def test_st_board_uses_five_percent(self):
        """ST 标的 ±5%: 前收 10 → 涨停 10.5。"""
        quotes = {"600519": {"price": 10.5, "pre_close": 10.0}}
        assert (
            compute_price_limit_status(["600519"], quotes=quotes, st_codes={"600519"})[
                "600519"
            ]
            == STATUS_LIMIT_UP
        )
        # 非 ST 口径下 10.5 未到 ±10% 涨停
        assert (
            compute_price_limit_status(["600519"], quotes=quotes)["600519"]
            == STATUS_NORMAL
        )

    @pytest.mark.unit
    def test_unknown_when_quote_missing(self):
        status = compute_price_limit_status(["999999"], quotes={})
        assert status["999999"] == STATUS_UNKNOWN

    @pytest.mark.unit
    def test_empty_codes(self):
        assert compute_price_limit_status([]) == {}


class TestPersistence:
    @pytest.mark.unit
    def test_save_and_load_fresh(self, tmp_path):
        path = str(tmp_path / "pls.json")
        save_price_limit_status({"600519": STATUS_NORMAL}, path=path)
        status, stale = load_price_limit_status(path)
        assert status == {"600519": STATUS_NORMAL}
        assert stale is False

    @pytest.mark.unit
    def test_stale_when_date_differs(self, tmp_path):
        """S-2 核心: 非本交易日状态必须标记 stale (涨停可能已消失)。"""
        path = str(tmp_path / "pls.json")
        save_price_limit_status({"600519": STATUS_NORMAL}, path=path)
        status, stale = load_price_limit_status(path, trade_date="2020-01-01")
        assert status == {"600519": STATUS_NORMAL}
        assert stale is True

    @pytest.mark.unit
    def test_missing_file_is_stale(self, tmp_path):
        status, stale = load_price_limit_status(str(tmp_path / "nope.json"))
        assert status == {}
        assert stale is True

    @pytest.mark.unit
    def test_corrupt_file_is_stale(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not-json", encoding="utf-8")
        status, stale = load_price_limit_status(str(path))
        assert status == {}
        assert stale is True

    @pytest.mark.unit
    def test_refresh_persists_and_merges(self, tmp_path, monkeypatch):
        """刷新结果落盘并与既有缓存合并 (保留未刷新标的的历史状态)。"""
        path = str(tmp_path / "pls.json")
        save_price_limit_status({"000001": STATUS_NORMAL}, path=path)
        monkeypatch.setattr("utils.price_limit_refresh._STATUS_FILE", path)

        status, stale = refresh_price_limit_status(
            ["600519"],
            quotes={
                "600519": {
                    "price": 1870.0,
                    "pre_close": 1700.0,
                    "limit_up": 1870.0,
                    "limit_down": 1530.0,
                }
            },
        )
        assert status["600519"] == STATUS_LIMIT_UP
        assert status["000001"] == STATUS_NORMAL
        assert stale is False

    @pytest.mark.unit
    def test_refresh_falls_back_to_cache_when_all_unknown(self, tmp_path, monkeypatch):
        """行情完全不可用 → 回退缓存并标记 stale, 不抛异常 (fail-open)。"""
        path = str(tmp_path / "pls.json")
        save_price_limit_status({"600519": STATUS_NORMAL}, path=path)
        monkeypatch.setattr("utils.price_limit_refresh._STATUS_FILE", path)

        # 无论回退路径如何, 关键不变量是: 不抛异常, 且状态不可信 → stale 必须为 True
        status, stale = refresh_price_limit_status(["999999"], quotes={})
        assert status == {"600519": STATUS_NORMAL}
        assert stale is True, "无法确认当日状态时必须标记 stale (保守拒绝)"
