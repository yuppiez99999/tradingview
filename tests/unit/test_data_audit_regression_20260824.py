"""data 模块 2026-08-24 审查回归测试 — 修复 DT-1/DT-2

覆盖:
    DT-1  get_price 支持 max_age stale 校验, 陈旧数据返回 None 而非静默旧价 (Q4)
    DT-2  _on_tick 过滤无效价 (lastPrice<=0 不缓存为真实价)
"""

from __future__ import annotations

import time

from ms_strategy.src.data.qmt_data_feed import QmtDataFeed


class TestQmtDataFeedPriceValidity:
    def setup_method(self):
        self.feed = QmtDataFeed(symbols=["510300.SH"])

    def test_invalid_price_filtered(self):
        """DT-2: lastPrice<=0 不缓存为真实价."""
        self.feed._on_tick([{"code": "510300.SH", "lastPrice": 0.0, "volume": 100}])
        assert self.feed.get_price("510300.SH") is None

    def test_missing_price_filtered(self):
        """DT-2: lastPrice 缺失不缓存."""
        self.feed._on_tick([{"code": "510300.SH", "volume": 100}])
        assert self.feed.get_price("510300.SH") is None

    def test_valid_price_cached(self):
        """DT-2: 有效价正常缓存."""
        self.feed._on_tick([{"code": "510300.SH", "lastPrice": 4.5, "volume": 100}])
        assert self.feed.get_price("510300.SH") == 4.5


class TestQmtDataFeedStale:
    def setup_method(self):
        self.feed = QmtDataFeed(symbols=["510300.SH"])
        self.feed._on_tick([{"code": "510300.SH", "lastPrice": 4.5, "volume": 100}])

    def test_fresh_price(self):
        """DT-1: max_age 内正常返回."""
        assert self.feed.get_price("510300.SH", max_age=200) == 4.5

    def test_stale_price_none(self):
        """DT-1: 陈旧数据返回 None 而非静默旧价."""
        self.feed._price_ts["510300.SH"] = time.time() - 100
        assert self.feed.get_price("510300.SH", max_age=30) is None

    def test_stale_by_default_no_age(self):
        """DT-1: 不传 max_age 时仍返回价 (向后兼容)."""
        assert self.feed.get_price("510300.SH") == 4.5

    def test_price_ts_cleared_on_remove(self):
        """DT-1: remove_symbols 清理时间戳."""
        self.feed.remove_symbols(["510300.SH"])
        assert self.feed.get_price_ts("510300.SH") is None
