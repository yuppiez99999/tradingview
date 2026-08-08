"""U3 复权因子支持 — 单元测试

测试覆盖:
    1. 纯函数转换: unadjusted_to_hfq / hfq_to_unadjusted / compute_adjusted_return
    2. _to_daily_symbol 代码格式转换 (sh/sz/bj 前缀)
    3. AdjustFactorProvider: 因子获取 (mock akshare) / 缓存 / 历史日期查询
    4. 除权日检测 (因子变化)
    5. 除权日对齐: 消除未复权价跳空偏差 (核心验收)
    6. 优雅降级: akshare 不可用 → factor=1.0
    7. data_provider 集成: enrich_realtime_with_hfq
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from utils.adjust_factor_provider import (  # noqa: E402
    AdjustFactorProvider,
    compute_adjusted_return,
    compute_aligned_return,
    align_realtime_to_hfq,
    get_adjust_factor_provider,
    hfq_to_unadjusted,
    unadjusted_to_hfq,
)


# ============================================================
# 1. 纯函数转换
# ============================================================
class TestPureFunctions:
    def test_unadjusted_to_hfq(self):
        assert unadjusted_to_hfq(10.0, 1.5) == 15.0
        assert unadjusted_to_hfq(9.0, 1.0) == 9.0

    def test_hfq_to_unadjusted(self):
        assert hfq_to_unadjusted(15.0, 1.5) == 10.0
        assert hfq_to_unadjusted(9.0, 1.0) == 9.0

    def test_roundtrip(self):
        # hfq → unadjusted → hfq 应还原
        price = 12.34
        factor = 1.2345
        hfq = unadjusted_to_hfq(price, factor)
        assert hfq_to_unadjusted(hfq, factor) == pytest.approx(price)

    def test_zero_factor_returns_original(self):
        assert unadjusted_to_hfq(10.0, 0.0) == 10.0
        assert hfq_to_unadjusted(10.0, 0.0) == 10.0

    def test_negative_factor_returns_original(self):
        assert unadjusted_to_hfq(10.0, -1.0) == 10.0

    def test_zero_price(self):
        assert unadjusted_to_hfq(0.0, 1.5) == 0.0
        assert hfq_to_unadjusted(0.0, 1.5) == 0.0

    def test_compute_adjusted_return_no_jump(self):
        """除权日对齐: 真实收益率=0 (未复权跳空被因子抵消)."""
        # 昨日 hfq 收盘 10.0, 今日未复权 9.0 (分红 1.0), 因子=10/9
        factor = 10.0 / 9.0
        ret = compute_adjusted_return(hfq_prev_close=10.0, unadjusted_realtime=9.0, hfq_factor=factor)
        # 9.0 × (10/9) = 10.0, return = 0
        assert ret == pytest.approx(0.0, abs=1e-9)

    def test_compute_adjusted_return_no_dividend(self):
        """非除权日: 因子=1.0, 等同普通收益率."""
        ret = compute_adjusted_return(10.0, 10.5, 1.0)
        assert ret == pytest.approx(0.05)

    def test_compute_adjusted_return_invalid_inputs(self):
        assert compute_adjusted_return(0.0, 10.0, 1.5) == 0.0
        assert compute_adjusted_return(10.0, 0.0, 1.5) == 0.0
        assert compute_adjusted_return(10.0, -1.0, 1.5) == 0.0


# ============================================================
# 2. _to_daily_symbol 代码格式转换
# ============================================================
class TestToDailySymbol:
    def test_sh_suffix(self):
        assert AdjustFactorProvider._to_daily_symbol("600519.SH") == "sh600519"

    def test_sz_suffix(self):
        assert AdjustFactorProvider._to_daily_symbol("000001.SZ") == "sz000001"

    def test_bj_suffix(self):
        assert AdjustFactorProvider._to_daily_symbol("830879.BJ") == "bj830879"

    def test_already_prefixed(self):
        assert AdjustFactorProvider._to_daily_symbol("sh600519") == "sh600519"
        assert AdjustFactorProvider._to_daily_symbol("SZ000001") == "sz000001"

    def test_pure_digits(self):
        assert AdjustFactorProvider._to_daily_symbol("600519") == "sh600519"
        assert AdjustFactorProvider._to_daily_symbol("000001") == "sz000001"
        assert AdjustFactorProvider._to_daily_symbol("300750") == "sz300750"
        assert AdjustFactorProvider._to_daily_symbol("688981") == "sh688981"
        assert AdjustFactorProvider._to_daily_symbol("830879") == "bj830879"

    def test_etf_codes(self):
        assert AdjustFactorProvider._to_daily_symbol("510050") == "sh510050"
        assert AdjustFactorProvider._to_daily_symbol("159915") == "sz159915"

    def test_invalid(self):
        assert AdjustFactorProvider._to_daily_symbol("abc") is None
        assert AdjustFactorProvider._to_daily_symbol("") is None


# ============================================================
# 3. AdjustFactorProvider (mock akshare)
# ============================================================
def _make_factor_series(factors: list[float], start: str = "2024-01-01") -> pd.DataFrame:
    """构造因子序列 DataFrame."""
    dates = pd.date_range(start, periods=len(factors), freq="D")
    return pd.DataFrame({"date": dates, "hfq_factor": factors})


@pytest.fixture
def fresh_provider(monkeypatch):
    """每个测试用独立的 provider (清除单例)."""
    monkeypatch.setattr(AdjustFactorProvider, "_instance", None)
    p = AdjustFactorProvider()
    return p


class TestAdjustFactorProvider:
    def test_get_factor_with_mock(self, fresh_provider):
        """mock akshare 返回因子序列, 验证 get_hfq_factor 取最新值."""
        series = _make_factor_series([1.0, 1.0, 1.1111])  # 第3日除权
        with patch.object(fresh_provider, "_fetch_hfq_factor_series", return_value=series):
            factor = fresh_provider.get_hfq_factor("600519.SH")
        assert factor == pytest.approx(1.1111, abs=1e-4)

    def test_get_factor_by_date(self, fresh_provider):
        """按日期查询: 返回 <= date 的最新因子 (point-in-time)."""
        series = _make_factor_series([1.0, 1.0, 1.1111])
        with patch.object(fresh_provider, "_fetch_hfq_factor_series", return_value=series):
            # 第 2 日 (2024-01-02): 因子仍为 1.0 (除权前)
            f2 = fresh_provider.get_hfq_factor("600519.SH", date="2024-01-02")
            # 第 3 日 (2024-01-03): 因子变为 1.1111 (除权后)
            f3 = fresh_provider.get_hfq_factor("600519.SH", date="2024-01-03")
        assert f2 == pytest.approx(1.0)
        assert f3 == pytest.approx(1.1111, abs=1e-4)

    def test_get_factor_date_before_records(self, fresh_provider):
        """查询日期早于所有记录: 返回最早因子."""
        series = _make_factor_series([1.5, 1.6, 1.7], start="2024-06-01")
        with patch.object(fresh_provider, "_fetch_hfq_factor_series", return_value=series):
            f = fresh_provider.get_hfq_factor("600519.SH", date="2024-01-01")
        assert f == pytest.approx(1.5)

    def test_cache_hit(self, fresh_provider):
        """缓存命中: 第二次调用不重新拉取."""
        series = _make_factor_series([1.0, 1.2])
        mock_fetch = MagicMock(return_value=series)
        with patch.object(fresh_provider, "_fetch_hfq_factor_series", mock_fetch):
            fresh_provider.get_hfq_factor("600519.SH")
            fresh_provider.get_hfq_factor("600519.SH")
        # 只拉取一次
        assert mock_fetch.call_count == 1

    def test_force_refresh_bypasses_cache(self, fresh_provider):
        series = _make_factor_series([1.0, 1.2])
        mock_fetch = MagicMock(return_value=series)
        with patch.object(fresh_provider, "_fetch_hfq_factor_series", mock_fetch):
            fresh_provider.get_hfq_factor("600519.SH")
            fresh_provider.get_hfq_factor("600519.SH", force_refresh=True)
        assert mock_fetch.call_count == 2

    def test_degradation_returns_one(self, fresh_provider):
        """akshare 不可用 → 返回 1.0 (安全降级)."""
        with patch.object(fresh_provider, "_fetch_hfq_factor_series", return_value=None):
            factor = fresh_provider.get_hfq_factor("600519.SH")
        assert factor == 1.0

    def test_empty_series_returns_one(self, fresh_provider):
        with patch.object(fresh_provider, "_fetch_hfq_factor_series", return_value=pd.DataFrame()):
            factor = fresh_provider.get_hfq_factor("600519.SH")
        assert factor == 1.0

    def test_get_factors_batch(self, fresh_provider):
        series_a = _make_factor_series([1.0, 1.5])
        series_b = _make_factor_series([1.0, 1.2])

        def fake_fetch(symbol):
            return series_a if "600519" in symbol else series_b

        with patch.object(fresh_provider, "_fetch_hfq_factor_series", side_effect=fake_fetch):
            batch = fresh_provider.get_factors_batch(["600519.SH", "000001.SZ"])
        assert batch["600519.SH"] == pytest.approx(1.5)
        assert batch["000001.SZ"] == pytest.approx(1.2)

    def test_clear_cache(self, fresh_provider):
        series = _make_factor_series([1.0, 1.5])
        mock_fetch = MagicMock(return_value=series)
        with patch.object(fresh_provider, "_fetch_hfq_factor_series", mock_fetch):
            fresh_provider.get_hfq_factor("600519.SH")
            fresh_provider.clear_cache()
            fresh_provider.get_hfq_factor("600519.SH")
        assert mock_fetch.call_count == 2


# ============================================================
# 4. 除权日检测
# ============================================================
class TestExDividendDetection:
    def test_ex_dividend_detected(self, fresh_provider):
        """因子变化 > 0.1% → 检测为除权日."""
        series = _make_factor_series([1.0, 1.0, 1.1111])  # 第3日除权
        with patch.object(fresh_provider, "_fetch_hfq_factor_series", return_value=series):
            # 第3日 (2024-01-03) 是除权日
            is_ex = fresh_provider.is_ex_dividend_date("600519.SH", date="2024-01-03")
        assert is_ex is True

    def test_non_ex_dividend(self, fresh_provider):
        """因子不变 → 非除权日."""
        series = _make_factor_series([1.0, 1.0, 1.0])
        with patch.object(fresh_provider, "_fetch_hfq_factor_series", return_value=series):
            is_ex = fresh_provider.is_ex_dividend_date("600519.SH", date="2024-01-03")
        assert is_ex is False

    def test_insufficient_data(self, fresh_provider):
        """数据不足 → 保守返回 False."""
        series = _make_factor_series([1.0])
        with patch.object(fresh_provider, "_fetch_hfq_factor_series", return_value=series):
            is_ex = fresh_provider.is_ex_dividend_date("600519.SH")
        assert is_ex is False

    def test_degradation_returns_false(self, fresh_provider):
        """akshare 不可用 → 保守返回 False."""
        with patch.object(fresh_provider, "_fetch_hfq_factor_series", return_value=None):
            is_ex = fresh_provider.is_ex_dividend_date("600519.SH")
        assert is_ex is False


# ============================================================
# 5. 除权日对齐 (核心验收) — 消除跳空偏差
# ============================================================
class TestExDividendAlignment:
    """U3 验收: 除权日前后 hfq 历史价与未复权实时价通过因子对齐, 无跳空偏差."""

    def test_no_fake_drop_on_ex_dividend(self, fresh_provider):
        """除权日未复权价跳空下跌, 但因子对齐后 hfq 基准价无跳空.

        场景: 股票 10.0 元, 分红 1.0 元
            - 除权前 (2024-01-02): 未复权 10.0, 因子 1.0, hfq=10.0
            - 除权日 (2024-01-03): 未复权 9.0 (跳空 -10%), 因子 10/9≈1.1111, hfq=10.0
        对齐后 hfq 基准价 = 9.0 × 1.1111 = 10.0, 与昨日 hfq 10.0 一致, 无跳空.
        """
        factor_before = 1.0
        factor_ex_date = 10.0 / 9.0  # ≈ 1.1111
        series = _make_factor_series([factor_before, factor_before, factor_ex_date])

        hfq_prev_close = 10.0  # 昨日 hfq 收盘
        unadjusted_realtime_ex_date = 9.0  # 除权日未复权实时价

        with patch.object(fresh_provider, "_fetch_hfq_factor_series", return_value=series):
            # 不对齐 (直接比较未复权): 虚假回撤 -10%
            naive_return = (unadjusted_realtime_ex_date - hfq_prev_close) / hfq_prev_close
            # 对齐后: 真实收益 = 0
            aligned_return = fresh_provider.get_hfq_factor("600519.SH", date="2024-01-03")
            aligned = compute_adjusted_return(
                hfq_prev_close, unadjusted_realtime_ex_date, aligned_return
            )

        assert naive_return == pytest.approx(-0.10)  # 虚假回撤
        assert aligned == pytest.approx(0.0, abs=1e-6)  # 对齐后无跳空

    def test_align_realtime_to_hfq_function(self, fresh_provider):
        """align_realtime_to_hfq 便捷函数: 未复权 → hfq 基准."""
        factor = 10.0 / 9.0
        series = _make_factor_series([1.0, 1.0, factor])

        with patch.object(fresh_provider, "_fetch_hfq_factor_series", return_value=series), \
             patch("utils.adjust_factor_provider.get_adjust_factor_provider", return_value=fresh_provider):
            aligned = align_realtime_to_hfq(9.0, "600519.SH", date="2024-01-03")
        # 9.0 × (10/9) = 10.0
        assert aligned == pytest.approx(10.0, abs=1e-6)

    def test_compute_aligned_return_function(self, fresh_provider):
        """compute_aligned_return 便捷函数: 一键对齐收益率."""
        factor = 10.0 / 9.0
        series = _make_factor_series([1.0, 1.0, factor])

        with patch.object(fresh_provider, "_fetch_hfq_factor_series", return_value=series), \
             patch("utils.adjust_factor_provider.get_adjust_factor_provider", return_value=fresh_provider):
            ret = compute_aligned_return(
                hfq_prev_close=10.0,
                unadjusted_realtime=9.0,
                symbol="600519.SH",
                date="2024-01-03",
            )
        assert ret == pytest.approx(0.0, abs=1e-6)

    def test_real_price_move_preserved(self, fresh_provider):
        """除权日 + 真实上涨: 对齐后保留真实涨幅 (非 0).

        场景: 昨日 hfq 10.0, 除权日未复权 9.5 (分红1.0, 真实涨 0.5/9 = +5.56%)
            因子 = 10/9, hfq 基准 = 9.5 × 10/9 = 10.5556
            真实收益 = (10.5556 - 10.0) / 10.0 = +5.56%
        """
        factor = 10.0 / 9.0
        series = _make_factor_series([1.0, 1.0, factor])
        with patch.object(fresh_provider, "_fetch_hfq_factor_series", return_value=series):
            f = fresh_provider.get_hfq_factor("600519.SH", date="2024-01-03")
            ret = compute_adjusted_return(10.0, 9.5, f)
        assert ret == pytest.approx(0.0556, abs=1e-3)  # 真实 +5.56%

    def test_degradation_no_alignment(self, fresh_provider):
        """akshare 不可用 → factor=1.0, 退化为未对齐 (与原行为一致)."""
        with patch.object(fresh_provider, "_fetch_hfq_factor_series", return_value=None):
            ret = compute_adjusted_return(10.0, 9.0, fresh_provider.get_hfq_factor("600519.SH"))
        # factor=1.0 → 等同未对齐: -10%
        assert ret == pytest.approx(-0.10)


# ============================================================
# 6. _normalize_factor_df 兼容性
# ============================================================
class TestNormalizeFactorDf:
    def test_chinese_columns(self):
        """akshare 中文列名归一化."""
        df = pd.DataFrame({
            "日期": ["2024-01-01", "2024-01-02"],
            "hfq_factor": [1.0, 1.1111],
        })
        out = AdjustFactorProvider._normalize_factor_df(df, "600519.SH")
        assert out is not None
        assert "date" in out.columns
        assert "hfq_factor" in out.columns
        assert len(out) == 2

    def test_english_columns(self):
        df = pd.DataFrame({
            "date": ["2024-01-01", "2024-01-02"],
            "hfq_factor": [1.0, 1.2],
        })
        out = AdjustFactorProvider._normalize_factor_df(df, "600519.SH")
        assert out is not None
        assert float(out["hfq_factor"].iloc[-1]) == pytest.approx(1.2)

    def test_zero_factor_replaced_with_one(self):
        """因子<=0 替换为 1.0 (安全)."""
        df = pd.DataFrame({
            "date": ["2024-01-01", "2024-01-02"],
            "hfq_factor": [0.0, -1.0],
        })
        out = AdjustFactorProvider._normalize_factor_df(df, "600519.SH")
        assert out is not None
        assert (out["hfq_factor"] == 1.0).all()

    def test_no_factor_column(self):
        """无因子列 → 返回 None."""
        df = pd.DataFrame({"date": ["2024-01-01"], "close": [10.0]})
        out = AdjustFactorProvider._normalize_factor_df(df, "600519.SH")
        assert out is None


# ============================================================
# 7. data_provider 集成
# ============================================================
class TestDataProviderIntegration:
    def test_enrich_realtime_with_hfq(self, fresh_provider):
        """enrich_realtime_with_hfq 注入 hfq_factor + hfq_equivalent_price."""
        from utils.data_provider import MarketDataProvider

        series = _make_factor_series([1.0, 1.0, 1.5])
        quote = {"index_price": 10.0, "prev_close": 9.0, "source": "sina_http"}

        with patch.object(fresh_provider, "_fetch_hfq_factor_series", return_value=series), \
             patch("utils.adjust_factor_provider.get_adjust_factor_provider", return_value=fresh_provider):
            provider = MarketDataProvider.__new__(MarketDataProvider)
            enriched = provider.enrich_realtime_with_hfq(quote, "600519.SH")

        assert "hfq_factor" in enriched
        assert "hfq_equivalent_price" in enriched
        assert "is_ex_dividend" in enriched
        # 10.0 × 1.5 = 15.0
        assert enriched["hfq_equivalent_price"] == pytest.approx(15.0)

    def test_enrich_degradation(self, fresh_provider):
        """akshare 不可用 → enrich 仍返回带默认值的 quote."""
        from utils.data_provider import MarketDataProvider

        quote = {"index_price": 10.0}
        with patch.object(fresh_provider, "_fetch_hfq_factor_series", return_value=None), \
             patch("utils.adjust_factor_provider.get_adjust_factor_provider", return_value=fresh_provider):
            provider = MarketDataProvider.__new__(MarketDataProvider)
            enriched = provider.enrich_realtime_with_hfq(quote, "600519.SH")

        assert enriched["hfq_factor"] == 1.0
        assert enriched["hfq_equivalent_price"] == pytest.approx(10.0)
        assert enriched["is_ex_dividend"] is False

    def test_get_hfq_factor_method(self, fresh_provider):
        """MarketDataProvider.get_hfq_factor 委托给 AdjustFactorProvider."""
        from utils.data_provider import MarketDataProvider

        series = _make_factor_series([1.0, 1.3])
        with patch.object(fresh_provider, "_fetch_hfq_factor_series", return_value=series), \
             patch("utils.adjust_factor_provider.get_adjust_factor_provider", return_value=fresh_provider):
            provider = MarketDataProvider.__new__(MarketDataProvider)
            factor = provider.get_hfq_factor("600519.SH")
        assert factor == pytest.approx(1.3)


# ============================================================
# 8. 单例
# ============================================================
class TestSingleton:
    def test_singleton(self, monkeypatch):
        monkeypatch.setattr(AdjustFactorProvider, "_instance", None)
        p1 = AdjustFactorProvider()
        p2 = AdjustFactorProvider()
        assert p1 is p2

    def test_get_adjust_factor_provider(self, monkeypatch):
        monkeypatch.setattr(AdjustFactorProvider, "_instance", None)
        p = get_adjust_factor_provider()
        assert isinstance(p, AdjustFactorProvider)


# ============================================================
# 9. get_aligned_prev_close (U3 新增)
# ============================================================
class TestGetAlignedPrevClose:
    """测试除权日 prev_close 对齐逻辑."""

    def test_non_ex_dividend_returns_original(self, fresh_provider):
        """非除权日: today_factor == yesterday_factor, 返回原 prev_close."""
        # 因子恒定 1.0, 无除权
        series = _make_factor_series([1.0, 1.0, 1.0])
        with patch.object(fresh_provider, "_fetch_hfq_factor_series", return_value=series):
            aligned = fresh_provider.get_aligned_prev_close(
                "600519.SH", prev_close=10.0, date="2024-01-03"
            )
        assert aligned == pytest.approx(10.0)

    def test_ex_dividend_adjusts_prev_close(self, fresh_provider):
        """除权日: prev_close 按因子比调整到今日口径."""
        # Day1: factor=1.0 (建仓)
        # Day2: factor=1.0 (除权前一日, prev_close=11.0)
        # Day3: factor=1.1 (除权日, 因子上升)
        # 对齐: aligned_prev = 11.0 * (1.0 / 1.1) = 10.0
        series = _make_factor_series([1.0, 1.0, 1.1])
        with patch.object(fresh_provider, "_fetch_hfq_factor_series", return_value=series):
            aligned = fresh_provider.get_aligned_prev_close(
                "600519.SH", prev_close=11.0, date="2024-01-03"
            )
        assert aligned == pytest.approx(10.0, abs=1e-6)

    def test_zero_prev_close_returns_original(self, fresh_provider):
        """prev_close=0 时返回原值 (不计算)."""
        series = _make_factor_series([1.0, 1.1])
        with patch.object(fresh_provider, "_fetch_hfq_factor_series", return_value=series):
            aligned = fresh_provider.get_aligned_prev_close(
                "600519.SH", prev_close=0.0, date="2024-01-02"
            )
        assert aligned == 0.0

    def test_negative_prev_close_returns_original(self, fresh_provider):
        """prev_close 负数 (异常输入) 返回原值."""
        series = _make_factor_series([1.0, 1.1])
        with patch.object(fresh_provider, "_fetch_hfq_factor_series", return_value=series):
            aligned = fresh_provider.get_aligned_prev_close(
                "600519.SH", prev_close=-5.0, date="2024-01-02"
            )
        assert aligned == -5.0

    def test_degradation_returns_original(self, fresh_provider):
        """akshare 不可用 → 返回原 prev_close (安全降级)."""
        with patch.object(fresh_provider, "_fetch_hfq_factor_series", return_value=None):
            aligned = fresh_provider.get_aligned_prev_close(
                "600519.SH", prev_close=10.0, date="2024-01-03"
            )
        assert aligned == pytest.approx(10.0)

    def test_module_level_helper(self, fresh_provider):
        """align_prev_close_to_today 模块级便捷函数."""
        from utils.adjust_factor_provider import align_prev_close_to_today

        series = _make_factor_series([1.0, 1.0, 1.1])
        with patch.object(fresh_provider, "_fetch_hfq_factor_series", return_value=series), \
             patch("utils.adjust_factor_provider.get_adjust_factor_provider", return_value=fresh_provider):
            aligned = align_prev_close_to_today(11.0, "600519.SH", date="2024-01-03")
        assert aligned == pytest.approx(10.0, abs=1e-6)


# ============================================================
# 10. pnl_calculator.calculate_pnl 集成 (U3 接入验证)
# ============================================================
class TestPnlCalculatorHfqAlign:
    """U3: 验证 calculate_pnl 接入复权因子对齐."""

    def _make_positions(self, code="600519.SH", shares=100, cost_price=10.0, buy_date=None):
        """构造 positions.json 测试数据."""
        pos = {
            "code": code,
            "name": "测试标的",
            "style": "科技",
            "risk": "medium",
            "actual_shares": shares,
            "est_price": cost_price,
            "actual_avg_cost": cost_price,
            "stop_loss": -0.15,
        }
        if buy_date:
            pos["buy_date"] = buy_date
        return {"positions": {code: pos}}

    def _make_market_prices(self, code="600519.SH", close=10.0, prev_close=11.0):
        """构造 market_prices 测试数据 (未复权实时行情)."""
        return {
            code: {
                "close": close,
                "prev_close": prev_close,
                "change_pct": (close - prev_close) / prev_close * 100,
                "source": "live",
            }
        }

    def test_zero_behavior_change_when_disabled(self, fresh_provider):
        """align_hfq=False 时 detail 不含任何 hfq 字段 (零行为变更)."""
        from reporting.pnl_calculator import calculate_pnl

        positions = self._make_positions()
        prices = self._make_market_prices()

        with patch("utils.adjust_factor_provider.get_adjust_factor_provider", return_value=fresh_provider):
            result = calculate_pnl(positions, prices, align_hfq=False)

        detail = result["details"][0]
        # 不应含任何 U3 新增字段
        assert "hfq_factor" not in detail
        assert "is_ex_dividend" not in detail
        assert "aligned_prev_close" not in detail
        assert "aligned_daily_pnl" not in detail
        assert "aligned_daily_pnl_pct" not in detail
        assert "aligned_cost_price" not in detail
        assert "aligned_pnl_pct" not in detail
        # summary 不含对齐总计
        assert "total_aligned_daily_pnl" not in result["summary"]
        assert "aligned_position_count" not in result["summary"]

    def test_non_ex_dividend_only_injects_factor(self, fresh_provider):
        """非除权日 + align_hfq=True: 仅注入 hfq_factor, 不计算 aligned_*."""
        from reporting.pnl_calculator import calculate_pnl

        # 因子恒定 1.0, 无除权
        series = _make_factor_series([1.0, 1.0, 1.0])
        positions = self._make_positions()
        prices = self._make_market_prices(close=10.5, prev_close=10.0)

        with patch.object(fresh_provider, "_fetch_hfq_factor_series", return_value=series), \
             patch("utils.adjust_factor_provider.get_adjust_factor_provider", return_value=fresh_provider):
            result = calculate_pnl(positions, prices, align_hfq=True, hfq_date="2024-01-03")

        detail = result["details"][0]
        assert "hfq_factor" in detail
        assert detail["hfq_factor"] == pytest.approx(1.0)
        assert detail["is_ex_dividend"] is False
        # 非除权日不应有对齐字段
        assert "aligned_prev_close" not in detail
        assert "aligned_daily_pnl" not in detail

    def test_ex_dividend_aligned_no_jump(self, fresh_provider):
        """除权日 + align_hfq=True: aligned_daily_pnl_pct 消除跳空偏差 (核心验收)."""
        from reporting.pnl_calculator import calculate_pnl

        # Day1: factor=1.0 (建仓, cost_price=10.0)
        # Day2: factor=1.0 (除权前一日, prev_close=11.0)
        # Day3: factor=1.1 (除权日, 未复权 close=10.0, 跳空 -9.1%)
        # 对齐后: aligned_prev = 11.0 * (1.0/1.1) = 10.0, aligned_pct = (10-10)/10 = 0%
        series = _make_factor_series([1.0, 1.0, 1.1])
        positions = self._make_positions(cost_price=10.0, buy_date="2024-01-01")
        prices = self._make_market_prices(close=10.0, prev_close=11.0)

        with patch.object(fresh_provider, "_fetch_hfq_factor_series", return_value=series), \
             patch("utils.adjust_factor_provider.get_adjust_factor_provider", return_value=fresh_provider):
            result = calculate_pnl(positions, prices, align_hfq=True, hfq_date="2024-01-03")

        detail = result["details"][0]
        assert detail["is_ex_dividend"] is True
        # 原始 daily_pnl_pct 显示虚假跳空 (-9.1%)
        assert detail["daily_pnl_pct"] == pytest.approx(-9.09, abs=0.1)
        # 对齐后 daily_pnl_pct ≈ 0 (无跳空)
        assert detail["aligned_daily_pnl_pct"] == pytest.approx(0.0, abs=1e-6)
        # aligned_prev_close = 11.0 * (1.0/1.1) ≈ 10.0
        assert detail["aligned_prev_close"] == pytest.approx(10.0, abs=1e-6)
        # aligned_daily_pnl = 100 * (10.0 - 10.0) = 0
        assert detail["aligned_daily_pnl"] == pytest.approx(0.0, abs=1e-6)

    def test_ex_dividend_aligned_cost_price(self, fresh_provider):
        """除权日 + 建仓日期 + align_hfq=True: 输出 aligned_cost_price / aligned_pnl_pct."""
        from reporting.pnl_calculator import calculate_pnl

        # Day1: factor=1.0 (建仓, cost_price=10.0)
        # Day2: factor=1.0 (除权前一日, prev_close=11.0)
        # Day3: factor=1.1 (除权日, close=10.0)
        # aligned_cost = 10.0 * (1.0 / 1.1) = 9.0909...
        # aligned_pnl_pct = (10.0 - 9.0909) / 9.0909 * 100 = 10.0%
        series = _make_factor_series([1.0, 1.0, 1.1])
        positions = self._make_positions(cost_price=10.0, buy_date="2024-01-01")
        prices = self._make_market_prices(close=10.0, prev_close=11.0)

        with patch.object(fresh_provider, "_fetch_hfq_factor_series", return_value=series), \
             patch("utils.adjust_factor_provider.get_adjust_factor_provider", return_value=fresh_provider):
            result = calculate_pnl(positions, prices, align_hfq=True, hfq_date="2024-01-03")

        detail = result["details"][0]
        # aligned_cost_price = 10.0 * (1.0/1.1) ≈ 9.09
        assert detail["aligned_cost_price"] == pytest.approx(9.09, abs=0.01)
        # aligned_pnl_pct = (10.0 - 9.09) / 9.09 * 100 ≈ 10.0%
        assert detail["aligned_pnl_pct"] == pytest.approx(10.0, abs=0.1)

    def test_summary_includes_aligned_total(self, fresh_provider):
        """除权日标的时 summary 输出 total_aligned_daily_pnl / aligned_position_count."""
        from reporting.pnl_calculator import calculate_pnl

        series = _make_factor_series([1.0, 1.0, 1.1])
        positions = self._make_positions(shares=200, cost_price=10.0)
        prices = self._make_market_prices(close=10.0, prev_close=11.0)

        with patch.object(fresh_provider, "_fetch_hfq_factor_series", return_value=series), \
             patch("utils.adjust_factor_provider.get_adjust_factor_provider", return_value=fresh_provider):
            result = calculate_pnl(positions, prices, align_hfq=True, hfq_date="2024-01-03")

        # summary 应含对齐总计
        assert "total_aligned_daily_pnl" in result["summary"]
        assert result["summary"]["aligned_position_count"] == 1
        # 200 shares * (10.0 - 10.0) = 0
        assert result["summary"]["total_aligned_daily_pnl"] == pytest.approx(0.0, abs=1e-6)

    def test_degradation_when_provider_unavailable(self, fresh_provider, monkeypatch):
        """hfq_provider 不可用时 align_hfq=True 安全降级, 行为同 align_hfq=False."""
        from reporting.pnl_calculator import calculate_pnl

        positions = self._make_positions()
        prices = self._make_market_prices()

        # 模拟 get_adjust_factor_provider 抛 ImportError
        def _raise_import_error():
            raise ImportError("adjust_factor_provider not available")

        monkeypatch.setattr(
            "utils.adjust_factor_provider.get_adjust_factor_provider",
            _raise_import_error,
        )

        # 不应抛异常, 降级为 align_hfq=False 行为
        result = calculate_pnl(positions, prices, align_hfq=True)

        detail = result["details"][0]
        assert "hfq_factor" not in detail
        assert "is_ex_dividend" not in detail

    def test_per_symbol_degradation_does_not_block(self, fresh_provider):
        """单个标的对齐失败不影响其他标的 (fail-safe)."""
        from reporting.pnl_calculator import calculate_pnl

        # 标的 A: 因子正常
        # 标的 B: 因子查询抛异常 (模拟 akshare 失败)
        series_a = _make_factor_series([1.0, 1.0, 1.1])

        def fake_fetch(symbol):
            if "600519" in symbol:
                return series_a
            raise RuntimeError("akshare timeout")

        positions = {
            "positions": {
                "600519.SH": {
                    "code": "600519.SH",
                    "name": "标的A",
                    "actual_shares": 100,
                    "est_price": 10.0,
                    "actual_avg_cost": 10.0,
                    "stop_loss": -0.15,
                },
                "000001.SZ": {
                    "code": "000001.SZ",
                    "name": "标的B",
                    "actual_shares": 100,
                    "est_price": 15.0,
                    "actual_avg_cost": 15.0,
                    "stop_loss": -0.15,
                },
            }
        }
        prices = {
            "600519.SH": {"close": 10.0, "prev_close": 11.0, "change_pct": -9.09, "source": "live"},
            "000001.SZ": {"close": 15.5, "prev_close": 15.0, "change_pct": 3.33, "source": "live"},
        }

        with patch.object(fresh_provider, "_fetch_hfq_factor_series", side_effect=fake_fetch), \
             patch("utils.adjust_factor_provider.get_adjust_factor_provider", return_value=fresh_provider):
            result = calculate_pnl(positions, prices, align_hfq=True, hfq_date="2024-01-03")

        # 两个标的都应正常输出 (不抛异常)
        assert len(result["details"]) == 2
        # 找到标的 A (有对齐字段)
        detail_a = next(d for d in result["details"] if d["code"] == "600519.SH")
        assert "hfq_factor" in detail_a
        assert detail_a["is_ex_dividend"] is True
        # 标的 B 因子查询失败, 但仍应有原始字段 (无 hfq 字段)
        detail_b = next(d for d in result["details"] if d["code"] == "000001.SZ")
        assert "close_price" in detail_b


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
