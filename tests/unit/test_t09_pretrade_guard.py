"""T09 单元测试 — PreTradeGuard 预交易风控门."""
from __future__ import annotations

import pytest

from utils.risk.pretrade_guard import (
    GuardOrderRequest,
    GuardResult,
    PreTradeGuard,
)


class TestPreTradeGuardConfigValidation:
    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="BLOCK 或 WARN"):
            PreTradeGuard(mode="INVALID")

    def test_invalid_price_limit_pct_raises(self):
        with pytest.raises(ValueError):
            PreTradeGuard(price_limit_pct=0.6)

    def test_invalid_notional_cap_raises(self):
        with pytest.raises(ValueError):
            PreTradeGuard(notional_cap=-1)


class TestLotSizeRule:
    def setup_method(self):
        self.g = PreTradeGuard()

    def test_100_shares_pass(self):
        r = self.g.check(GuardOrderRequest("sh1", "buy", 100, 10.0))
        assert r.is_pass

    def test_150_shares_reject(self):
        r = self.g.check(GuardOrderRequest("sh1", "buy", 150, 10.0))
        assert r.rejected
        assert any("LOT_SIZE" in s for s in r.reasons)

    def test_zero_shares_reject(self):
        r = self.g.check(GuardOrderRequest("sh1", "buy", 0, 10.0))
        assert r.rejected
        assert any("LOT_SIZE" in s for s in r.reasons)

    def test_custom_lot_200(self):
        g = PreTradeGuard(lot_size=200)
        assert g.check(GuardOrderRequest("s1", "buy", 200, 10.0)).is_pass
        r = g.check(GuardOrderRequest("s1", "buy", 100, 10.0))
        assert r.rejected


class TestPriceBandRule:
    def setup_method(self):
        self.g = PreTradeGuard(price_limit_pct=0.10)

    def test_price_floor_breach(self):
        r = self.g.check(GuardOrderRequest("s1", "sell", 100, 8.9, prev_close=10.0))
        assert r.rejected
        assert any("PRICE_BAND" in s for s in r.reasons)

    def test_price_ceiling_breach(self):
        r = self.g.check(GuardOrderRequest("s1", "buy", 100, 11.01, prev_close=10.0))
        assert r.rejected

    def test_at_limit_pass(self):
        # 正好 10.00 → 下边界 9.00 上边界 11.00
        assert self.g.check(GuardOrderRequest("s1", "buy", 100, 11.0, prev_close=10.0)).is_pass
        assert self.g.check(GuardOrderRequest("s1", "sell", 100, 9.0, prev_close=10.0)).is_pass

    def test_no_prev_close_skipped(self):
        # 没有昨收, PRICE_BAND 规则跳过 (不拦截); 同时确保 NOTIONAL 不超上限 (100 * 10 = 1000 << 50 万)
        r = self.g.check(GuardOrderRequest("s1", "buy", 100, 10.0))
        assert r.is_pass
        # 证明 PRICE_BAND 确实被标记 SKIPPED (非缺失)
        assert any("PRICE_BAND" in rule and "SKIPPED" in rule for rule in r.checked_rules)


class TestNotionalCapRule:
    def test_under_cap_pass(self):
        g = PreTradeGuard(notional_cap=1_000_000)
        r = g.check(GuardOrderRequest("s1", "buy", 100, 1000.0))  # 10 万
        assert r.is_pass

    def test_over_cap_reject(self):
        g = PreTradeGuard(notional_cap=100_000)
        r = g.check(GuardOrderRequest("s1", "buy", 100, 2000.0))  # 20 万 > 10 万
        assert r.rejected
        assert any("NOTIONAL_CAP" in s for s in r.reasons)

    def test_notional_uses_explicit(self):
        # 显式 notional=150k > 100k, 虽然 shares*price=10k
        g = PreTradeGuard(notional_cap=100_000)
        r = g.check(GuardOrderRequest("s1", "buy", 100, 100.0, notional=150_000))
        assert r.rejected


class TestSTFilterRule:
    def setup_method(self):
        self.g = PreTradeGuard()

    def test_st_buy_reject(self):
        r = self.g.check(GuardOrderRequest("s1", "buy", 100, 5.0, symbol_name="ST 康美"))
        assert r.rejected
        assert any("ST_FILTER" in s for s in r.reasons)

    def test_st_sell_pass(self):
        r = self.g.check(GuardOrderRequest("s1", "sell", 100, 5.0, symbol_name="*ST 康美"))
        assert r.is_pass

    def test_non_st_pass(self):
        r = self.g.check(GuardOrderRequest("s1", "buy", 100, 5.0, symbol_name="贵州茅台"))
        assert r.is_pass

    def test_disabled_st_filter_pass(self):
        g = PreTradeGuard(enable_st_filter=False)
        r = g.check(GuardOrderRequest("s1", "buy", 100, 5.0, symbol_name="ST 某某"))
        assert r.is_pass


class TestWhitelistRule:
    def test_no_whitelist_all_pass(self):
        g = PreTradeGuard()
        assert g.check(GuardOrderRequest("any", "buy", 100, 10.0)).is_pass

    def test_whitelist_enabled_reject_unknown(self):
        g = PreTradeGuard(whitelist=["SH600519"])
        r = g.check(GuardOrderRequest("SH600000", "buy", 100, 10.0))
        assert r.rejected
        assert any("SYMBOL_WHITELIST" in s for s in r.reasons)

    def test_whitelist_case_insensitive(self):
        g = PreTradeGuard(whitelist=["sh600519"])
        assert g.check(GuardOrderRequest("SH600519", "buy", 100, 10.0)).is_pass


class TestSuspendFilterRule:
    def test_suspended_reject(self):
        g = PreTradeGuard()
        r = g.check(GuardOrderRequest("s1", "buy", 100, 10.0, is_suspended=True))
        assert r.rejected
        assert any("SUSPEND_FILTER" in s for s in r.reasons)

    def test_not_suspended_pass(self):
        g = PreTradeGuard()
        assert g.check(GuardOrderRequest("s1", "buy", 100, 10.0)).is_pass

    def test_disabled_filter_pass(self):
        g = PreTradeGuard(enable_suspend_filter=False)
        assert g.check(GuardOrderRequest("s1", "buy", 100, 10.0, is_suspended=True)).is_pass


class TestBlockVsWarnMode:
    def test_block_mode_rejects(self):
        g = PreTradeGuard(mode="BLOCK")
        r = g.check(GuardOrderRequest("s1", "buy", 150, 10.0))
        assert r.rejected

    def test_warn_mode_passes_but_records(self):
        g = PreTradeGuard(mode="WARN")
        r = g.check(GuardOrderRequest("s1", "buy", 150, 10.0))
        assert not r.rejected  # WARN 模式放行
        assert r.reasons  # 但记录原因


class TestBatchCheck:
    def test_batch_heterogeneous(self):
        g = PreTradeGuard()
        reqs = [
            GuardOrderRequest("good", "buy", 100, 10.0),
            GuardOrderRequest("bad1", "buy", 150, 10.0),
            GuardOrderRequest("bad2", "buy", 100, 99.0, prev_close=10.0),
        ]
        results = g.check_batch(reqs)
        assert len(results) == 3
        assert results[0].is_pass
        assert results[1].rejected
        assert results[2].rejected
