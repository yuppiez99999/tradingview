"""ShadowRealDataFeeder 单元测试 — W1.3a Day 1.

测试覆盖目标:
    - 核心算法 (feed_single_date / dry_run / feed_history)
    - 权重加载 (trade_plan / strategy_plan / positions_json)
    - JSONL 增量写入 (新增 / 更新 / 排序)
    - 安全护栏 (异常收益 / 全失败 / 部分覆盖)
    - 边界场景 (空权重 / 周末 / 数据源失败)
    - CLI 入口

设计原则:
    - 使用 MockMarketDataProvider 避免真实网络调用
    - 使用 tmp_path fixture 隔离 jsonl 文件
    - 不依赖真实 trade_plan 文件 (构造临时 JSON)
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.alpha.shadow_real_data_feeder import (  # noqa: E402
    ABNORMAL_RETURN_THRESHOLD,
    CACHE_TTL_DEFAULT_SEC,
    COVERAGE_THRESHOLD,
    CROSS_SOURCE_DIFF_THRESHOLD,
    DEFAULT_CACHE_MAX_SYMBOLS,
    DEFAULT_HISTORICAL_PERIOD,
    DEFAULT_MAX_WORKERS,
    DEFAULT_OUTPUT_PATH,
    DEFAULT_SOURCE_TAG,
    MAX_MAX_WORKERS,
    MIN_SIGNIFICANT_WEIGHT,
    CacheStats,
    DataProviderUnavailableError,
    FeedResult,
    HistoryFeedSummary,
    ShadowRealDataFeeder,
    ShadowRealDataFeederError,
    ValidationResult,
    WeightsLoadError,
)

# ============================================================
# Mock 工具
# ============================================================


class MockMarketDataProvider:
    """模拟 MarketDataProvider, 仅实现 get_historical_data.

    用于隔离真实数据源, 测试核心算法.
    """

    def __init__(
        self,
        price_data: dict[str, pd.DataFrame] | None = None,
        source_health: dict | None = None,
        raise_on_symbol: set[str] | None = None,
    ) -> None:
        """初始化.

        Args:
            price_data: {symbol: DataFrame} 预置数据; 未预置的 symbol 返回空 df
            source_health: 模拟的 source_health 属性
            raise_on_symbol: 调用 get_historical_data 时对这些 symbol 抛异常
        """
        self._price_data = price_data or {}
        self._raise_on_symbol = raise_on_symbol or set()
        self.source_health = source_health or {
            "tdx": {"ok": True, "last_error": ""},
            "akshare": {"ok": True, "last_error": ""},
            "wind_mcp": {"ok": False, "last_error": "not configured"},
        }

    def get_historical_data(self, symbol: str, period: str = "1y") -> pd.DataFrame:
        if symbol in self._raise_on_symbol:
            raise RuntimeError(f"mock error for {symbol}")
        return self._price_data.get(symbol, pd.DataFrame())


def _make_price_df(prices: list[tuple[str, float]]) -> pd.DataFrame:
    """构造 OHLC DataFrame.

    Args:
        prices: [(date_str, close_price), ...]

    Returns:
        DataFrame with DatetimeIndex and 'close' column
    """
    if not prices:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    dates = [datetime.strptime(d, "%Y-%m-%d") for d, _ in prices]
    closes = [c for _, c in prices]
    df = pd.DataFrame(
        {
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [10000] * len(closes),
        },
        index=pd.DatetimeIndex(dates),
    )
    return df


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def mock_provider_with_prices():
    """构造一个预置价格的 Mock Provider.

    数据:
        600276: 07-27 close=10, 07-28 close=10.5 (ret=+5%)
        588000: 07-27 close=1.0, 07-28 close=1.01 (ret=+1%)
    """
    price_data = {
        "600276": _make_price_df(
            [
                ("2026-07-25", 10.0),
                ("2026-07-26", 10.0),
                ("2026-07-27", 10.0),
                ("2026-07-28", 10.5),
            ]
        ),
        "588000": _make_price_df(
            [
                ("2026-07-25", 1.0),
                ("2026-07-26", 1.0),
                ("2026-07-27", 1.0),
                ("2026-07-28", 1.01),
            ]
        ),
    }
    return MockMarketDataProvider(price_data=price_data)


@pytest.fixture
def feeder_with_mock(mock_provider_with_prices, tmp_path):
    """构造 ShadowRealDataFeeder, 输出到临时路径."""
    output_path = tmp_path / "daily_returns.jsonl"
    return ShadowRealDataFeeder(
        data_provider=mock_provider_with_prices,
        output_path=output_path,
        verbose=True,
    )


@pytest.fixture
def empty_jsonl_path(tmp_path):
    """空 jsonl 文件路径 (尚未创建)."""
    return tmp_path / "daily_returns.jsonl"


@pytest.fixture
def existing_jsonl(tmp_path):
    """已存在的 jsonl 文件 (含 2 条历史记录)."""
    path = tmp_path / "daily_returns.jsonl"
    records = [
        {"date": "2026-07-26", "daily_return": 0.005, "source": "old_source"},
        {"date": "2026-07-27", "daily_return": 0.008, "source": "old_source"},
    ]
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


# ============================================================
# 构造器测试
# ============================================================


class TestShadowRealDataFeederInit:
    """测试 __init__ 参数校验."""

    def test_init_with_valid_provider(self, mock_provider_with_prices, tmp_path):
        """正常初始化."""
        feeder = ShadowRealDataFeeder(
            data_provider=mock_provider_with_prices,
            output_path=tmp_path / "out.jsonl",
        )
        assert feeder._source_tag == DEFAULT_SOURCE_TAG
        assert feeder._skip_weekend is True
        assert feeder._verbose is False

    def test_init_with_none_provider_raises(self, tmp_path):
        """data_provider=None 应抛 ValueError."""
        with pytest.raises(ValueError, match="data_provider 不能为 None"):
            ShadowRealDataFeeder(data_provider=None, output_path=tmp_path / "out.jsonl")

    def test_init_with_invalid_provider_no_method(self, tmp_path):
        """provider 无 get_historical_data 方法应抛 ValueError."""
        with pytest.raises(ValueError, match="必须实现 get_historical_data"):
            ShadowRealDataFeeder(
                data_provider=MagicMock(spec=[]),  # 空 spec, 无任何方法
                output_path=tmp_path / "out.jsonl",
            )

    def test_init_with_invalid_weights_source(
        self, mock_provider_with_prices, tmp_path
    ):
        """非法 weights_source 应抛 ValueError."""
        with pytest.raises(ValueError, match="weights_source 必须为"):
            ShadowRealDataFeeder(
                data_provider=mock_provider_with_prices,
                weights_source="invalid",
                output_path=tmp_path / "out.jsonl",
            )

    def test_init_with_custom_source_tag(self, mock_provider_with_prices, tmp_path):
        """自定义 source_tag."""
        feeder = ShadowRealDataFeeder(
            data_provider=mock_provider_with_prices,
            output_path=tmp_path / "out.jsonl",
            source_tag="custom_tag",
        )
        assert feeder._source_tag == "custom_tag"

    def test_get_source_health_returns_dict(self, mock_provider_with_prices, tmp_path):
        """get_source_health 返回 source_health 字典."""
        feeder = ShadowRealDataFeeder(
            data_provider=mock_provider_with_prices,
            output_path=tmp_path / "out.jsonl",
        )
        health = feeder.get_source_health()
        assert isinstance(health, dict)
        assert "tdx" in health
        assert health["tdx"]["ok"] is True

    def test_get_source_health_with_no_health_attr(self, tmp_path):
        """provider 无 source_health 时返回空字典."""

        class NoHealthProvider:
            def get_historical_data(self, symbol, period="1y"):
                return pd.DataFrame()

        feeder = ShadowRealDataFeeder(
            data_provider=NoHealthProvider(),
            output_path=tmp_path / "out.jsonl",
        )
        assert feeder.get_source_health() == {}


# ============================================================
# feed_single_date 核心算法测试
# ============================================================


class TestFeedSingleDate:
    """测试 feed_single_date 核心算法."""

    def test_success_with_two_symbols(self, feeder_with_mock):
        """2 个标的成功注入, 验证加权收益计算."""
        # 600276: weight=0.5, ret = 10.5/10.0 - 1 = 0.05
        # 588000: weight=0.5, ret = 1.01/1.0 - 1 = 0.01
        # daily_return = 0.5*0.05 + 0.5*0.01 = 0.025 + 0.005 = 0.03
        result = feeder_with_mock.feed_single_date(
            "2026-07-28",
            {"600276": 0.5, "588000": 0.5},
        )
        assert result.is_success
        assert result.skipped is False
        assert result.daily_return == pytest.approx(0.03, abs=1e-6)
        assert result.success_count == 2
        assert result.fail_count == 0
        assert result.total_count == 2
        assert result.coverage == 1.0
        assert result.written is True

    def test_writes_to_jsonl(self, feeder_with_mock, tmp_path):
        """注入后 jsonl 文件应包含当日记录."""
        feeder_with_mock.feed_single_date("2026-07-28", {"600276": 0.5, "588000": 0.5})
        jsonl_path = tmp_path / "daily_returns.jsonl"
        assert jsonl_path.exists()
        with open(jsonl_path, encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert len(lines) == 1
        assert lines[0]["date"] == "2026-07-28"
        assert lines[0]["daily_return"] == pytest.approx(0.03, abs=1e-6)
        assert lines[0]["source"] == DEFAULT_SOURCE_TAG
        assert lines[0]["cross_validated"] is False
        assert lines[0]["source_consistency"] == "high"

    def test_invalid_date_format_raises(self, feeder_with_mock):
        """非法日期格式应抛 ValueError."""
        with pytest.raises(ValueError, match="日期格式错误"):
            feeder_with_mock.feed_single_date("2026/07/28", {"600276": 0.5})

    def test_weekend_skip(self, feeder_with_mock):
        """周末应跳过."""
        # 2026-07-25 是周六
        result = feeder_with_mock.feed_single_date("2026-07-25", {"600276": 0.5})
        assert result.skipped is True
        assert result.is_success is False
        assert "weekend" in result.error
        assert result.success_count == 0

    def test_sunday_skip(self, feeder_with_mock):
        """周日也应跳过."""
        # 2026-07-26 是周日
        result = feeder_with_mock.feed_single_date("2026-07-26", {"600276": 0.5})
        assert result.skipped is True

    def test_no_skip_weekend_when_disabled(self, mock_provider_with_prices, tmp_path):
        """skip_weekend=False 时周末也注入."""
        feeder = ShadowRealDataFeeder(
            data_provider=mock_provider_with_prices,
            output_path=tmp_path / "out.jsonl",
            skip_weekend=False,
        )
        # 2026-07-25 周六, 600276 收盘 10.0, 前一日 10.0, ret=0
        result = feeder.feed_single_date("2026-07-25", {"600276": 1.0})
        assert result.skipped is False
        assert result.is_success

    def test_empty_weights_skipped(self, feeder_with_mock):
        """空权重应跳过."""
        result = feeder_with_mock.feed_single_date("2026-07-28", {})
        assert result.skipped is True
        assert result.error == "empty_target_weights"

    def test_zero_total_weight_skipped(self, feeder_with_mock):
        """总权重为 0 应跳过."""
        result = feeder_with_mock.feed_single_date("2026-07-28", {"600276": 0.0})
        assert result.skipped is True
        assert result.error == "total_abs_weight_zero"

    def test_min_significant_weight_ignored(self, feeder_with_mock):
        """微小权重 (|w|<0.001) 应被忽略."""
        result = feeder_with_mock.feed_single_date(
            "2026-07-28",
            {"600276": 0.5, "588000": 0.0001},  # 588000 权重过小
        )
        # 588000 被忽略, 只算 600276
        assert result.total_count == 2  # total_count 是原始 dict 长度
        assert result.success_count == 1  # 只有 600276 算成功

    def test_symbol_fetch_failure_continues(self, tmp_path):
        """单个标的拉取失败应继续处理其他标的."""
        price_data = {
            "600276": _make_price_df([("2026-07-27", 10.0), ("2026-07-28", 10.5)]),
        }
        provider = MockMarketDataProvider(
            price_data=price_data,
            raise_on_symbol={"588000"},  # 588000 抛异常
        )
        feeder = ShadowRealDataFeeder(
            data_provider=provider,
            output_path=tmp_path / "out.jsonl",
        )
        result = feeder.feed_single_date(
            "2026-07-28",
            {"600276": 0.5, "588000": 0.5},
        )
        assert result.success_count == 1
        assert result.fail_count == 1
        # daily_return = 0.5 * 0.05 = 0.025
        assert result.daily_return == pytest.approx(0.025, abs=1e-6)
        # 覆盖率 50% < 80% 阈值, 应有 warning
        assert result.coverage == 0.5
        assert any("partial_coverage" in w for w in result.warnings)

    def test_all_symbols_fail_skipped(self, tmp_path):
        """所有标的都失败应跳过."""
        provider = MockMarketDataProvider(raise_on_symbol={"600276", "588000"})
        feeder = ShadowRealDataFeeder(
            data_provider=provider,
            output_path=tmp_path / "out.jsonl",
        )
        result = feeder.feed_single_date(
            "2026-07-28",
            {"600276": 0.5, "588000": 0.5},
        )
        assert result.skipped is True
        assert result.is_success is False
        assert result.error == "all_symbols_failed"
        assert result.written is False

    def test_symbols_detail_recorded(self, feeder_with_mock):
        """symbols_detail 应记录每个标的的明细."""
        result = feeder_with_mock.feed_single_date(
            "2026-07-28",
            {"600276": 0.5, "588000": 0.5},
        )
        assert len(result.symbols_detail) == 2
        detail_600276 = next(
            d for d in result.symbols_detail if d["symbol"] == "600276"
        )
        assert detail_600276["weight"] == 0.5
        assert detail_600276["prev_close"] == 10.0
        assert detail_600276["target_close"] == 10.5
        assert detail_600276["ret"] == pytest.approx(0.05, abs=1e-6)
        assert detail_600276["contrib"] == pytest.approx(0.025, abs=1e-6)

    def test_negative_weight_short_contribution(self, feeder_with_mock):
        """负权重 (空头) 应正确计算贡献."""
        # 600276: weight=-0.5 (空头), ret=+0.05, contrib = -0.5 * 0.05 = -0.025
        result = feeder_with_mock.feed_single_date("2026-07-28", {"600276": -0.5})
        assert result.daily_return == pytest.approx(-0.025, abs=1e-6)

    def test_existing_date_updates_record(self, feeder_with_mock, existing_jsonl):
        """jsonl 已存在该日期时应更新而非追加."""
        feeder_with_mock._output_path = existing_jsonl
        result = feeder_with_mock.feed_single_date(
            "2026-07-27",  # jsonl 中已有此日期
            {"600276": 1.0},  # 600276 07-27 close=10, 前一日 10.0, ret=0
        )
        assert result.written is True
        # 读取验证: 应仍是 2 条记录 (原有 07-26 和更新的 07-27), 没有重复
        with open(existing_jsonl, encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        dates = [r["date"] for r in lines]
        assert dates.count("2026-07-27") == 1  # 没有重复
        assert "2026-07-26" in dates
        # 07-27 应被更新 (source 变为 w13a_real_market_feed)
        record_0727 = next(r for r in lines if r["date"] == "2026-07-27")
        assert record_0727["source"] == DEFAULT_SOURCE_TAG

    def test_records_sorted_by_date(self, feeder_with_mock, existing_jsonl):
        """jsonl 应按日期升序排序."""
        feeder_with_mock._output_path = existing_jsonl
        # 注入一个更早的日期 2026-07-25 (周末跳过, 改用 2026-07-24 周五)
        # 2026-07-24 是周五
        feeder_with_mock.feed_single_date("2026-07-24", {"600276": 1.0})
        with open(existing_jsonl, encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        dates = [r["date"] for r in lines]
        assert dates == sorted(dates)
        assert "2026-07-24" in dates

    def test_weights_load_failure_skipped(self, tmp_path, monkeypatch):
        """weights_source 加载失败时应跳过.

        2026-08-11 v8.6.14: 需显式 monkeypatch _POSITIONS_JSON 到不存在路径,
        否则会 fallback 到项目实际 config/positions.json (含 26 个 symbol).
        """
        import utils.alpha.shadow_real_data_feeder as mod

        # 让所有权重源都不可用
        monkeypatch.setattr(
            mod, "_TRADE_PLAN_DIR", tmp_path / "nonexistent_trade_plans"
        )
        monkeypatch.setattr(
            mod, "_STRATEGY_PLAN_DIR", tmp_path / "nonexistent_strategy"
        )
        monkeypatch.setattr(
            mod, "_POSITIONS_JSON", tmp_path / "nonexistent_positions.json"
        )

        provider = MockMarketDataProvider()
        feeder = ShadowRealDataFeeder(
            data_provider=provider,
            weights_source="auto",  # 但无任何权重文件
            output_path=tmp_path / "out.jsonl",
        )
        result = feeder.feed_single_date("2026-07-28", target_weights=None)
        assert result.skipped is True
        assert "weights_load_failed" in result.error


# ============================================================
# dry_run 测试
# ============================================================


class TestDryRun:
    """测试 dry_run 离线模式."""

    def test_dry_run_does_not_write(self, feeder_with_mock, tmp_path):
        """dry_run 不应写盘."""
        result = feeder_with_mock.dry_run("2026-07-28", {"600276": 0.5, "588000": 0.5})
        assert result.is_success
        assert result.written is False
        # jsonl 文件不应存在
        jsonl_path = tmp_path / "daily_returns.jsonl"
        assert not jsonl_path.exists()

    def test_dry_run_computes_return(self, feeder_with_mock):
        """dry_run 仍应正确计算 daily_return."""
        result = feeder_with_mock.dry_run("2026-07-28", {"600276": 0.5, "588000": 0.5})
        assert result.daily_return == pytest.approx(0.03, abs=1e-6)

    def test_dry_run_weekend_skip(self, feeder_with_mock):
        """dry_run 周末应跳过."""
        result = feeder_with_mock.dry_run("2026-07-25", {"600276": 0.5})
        assert result.skipped is True


# ============================================================
# feed_history 测试
# ============================================================


class TestFeedHistory:
    """测试 feed_history 历史回填."""

    def test_feed_history_basic(self, feeder_with_mock):
        """基础历史回填."""
        # 07-27 (周一) 到 07-28 (周二)
        results = feeder_with_mock.feed_history(
            "2026-07-27",
            "2026-07-28",
            weights_history={
                "2026-07-27": {"600276": 1.0},  # 600276: 10→10, ret=0
                "2026-07-28": {"600276": 0.5, "588000": 0.5},  # ret=0.03
            },
        )
        assert len(results) == 2
        assert all(r.is_success for r in results)
        # 07-27: 600276 close 10→10, ret=0
        assert results[0].date == "2026-07-27"
        assert results[0].daily_return == pytest.approx(0.0, abs=1e-6)
        # 07-28: daily_return = 0.03
        assert results[1].date == "2026-07-28"
        assert results[1].daily_return == pytest.approx(0.03, abs=1e-6)

    def test_feed_history_includes_weekend_skip(self, feeder_with_mock):
        """回填范围跨周末应跳过."""
        # 07-24 (周五) 到 07-28 (周二), 包含 07-25/07-26 周末
        results = feeder_with_mock.feed_history(
            "2026-07-24",
            "2026-07-28",
            weights_history={
                "2026-07-24": {"600276": 1.0},
                "2026-07-25": {"600276": 1.0},  # 周末, 跳过
                "2026-07-26": {"600276": 1.0},  # 周末, 跳过
                "2026-07-27": {"600276": 1.0},
                "2026-07-28": {"600276": 1.0},
            },
        )
        assert len(results) == 5  # 5 天的结果都返回
        skipped_count = sum(1 for r in results if r.skipped)
        assert skipped_count == 2  # 周末 2 天跳过

    def test_feed_history_invalid_range_raises(self, feeder_with_mock):
        """start > end 应抛 ValueError."""
        with pytest.raises(ValueError, match="不能晚于"):
            feeder_with_mock.feed_history("2026-07-28", "2026-07-27")

    def test_feed_history_invalid_date_format_raises(self, feeder_with_mock):
        """日期格式错误应抛 ValueError."""
        with pytest.raises(ValueError, match="日期格式错误"):
            feeder_with_mock.feed_history("2026/07/27", "2026-07-28")

    def test_feed_history_auto_load_weights(self, feeder_with_mock, tmp_path):
        """weights_history=None 时每日从 weights_source 加载 (失败时跳过)."""
        # 没有权重文件, 所有日期都应跳过
        results = feeder_with_mock.feed_history("2026-07-27", "2026-07-28")
        assert len(results) == 2
        assert all(r.skipped for r in results)


# ============================================================
# _fetch_symbol_prices 测试
# ============================================================


class TestFetchSymbolPrices:
    """测试 _fetch_symbol_prices 价格查找逻辑."""

    def test_exact_date_match(self, feeder_with_mock):
        """精确日期匹配."""
        prev, target = feeder_with_mock._fetch_symbol_prices("600276", "2026-07-28")
        assert prev == 10.0
        assert target == 10.5

    def test_prev_close_window(self, feeder_with_mock):
        """前一日窗口查找 (周末偏移)."""
        # 07-28 (周二) 的前一日是 07-27 (周一), 数据存在
        prev, target = feeder_with_mock._fetch_symbol_prices("600276", "2026-07-28")
        assert prev == 10.0  # 07-27 close

    def test_empty_df_returns_none(self, tmp_path):
        """空 DataFrame 应返回 (None, None)."""
        provider = MockMarketDataProvider(price_data={"600276": pd.DataFrame()})
        feeder = ShadowRealDataFeeder(
            data_provider=provider,
            output_path=tmp_path / "out.jsonl",
        )
        prev, target = feeder._fetch_symbol_prices("600276", "2026-07-28")
        assert prev is None
        assert target is None

    def test_single_row_df_returns_none_for_prev(self, tmp_path):
        """只有 1 行数据时应无法获取 prev_close."""
        provider = MockMarketDataProvider(
            price_data={"600276": _make_price_df([("2026-07-28", 10.5)])}
        )
        feeder = ShadowRealDataFeeder(
            data_provider=provider,
            output_path=tmp_path / "out.jsonl",
        )
        # len(df) < 2, 直接返回 None
        prev, target = feeder._fetch_symbol_prices("600276", "2026-07-28")
        assert prev is None
        assert target is None

    def test_fallback_to_last_row(self, tmp_path):
        """精确匹配失败时回退到 df 末尾."""
        # 数据中无 2026-08-04, 但有 07-25~07-28
        provider = MockMarketDataProvider(
            price_data={
                "600276": _make_price_df(
                    [
                        ("2026-07-25", 10.0),
                        ("2026-07-26", 10.2),
                        ("2026-07-27", 10.1),
                        ("2026-07-28", 10.5),
                    ]
                )
            }
        )
        feeder = ShadowRealDataFeeder(
            data_provider=provider,
            output_path=tmp_path / "out.jsonl",
        )
        prev, target = feeder._fetch_symbol_prices("600276", "2026-08-04")
        # 精确匹配失败, fallback: target=df.iloc[-1]=10.5, prev=df.iloc[-2]=10.1
        assert target == 10.5
        assert prev == 10.1

    def test_future_date_uses_latest_available_close(self, tmp_path):
        """目标日晚于最新行情时, 应回退到最后一条已知交易日的收盘价."""
        provider = MockMarketDataProvider(
            price_data={
                "600276": _make_price_df(
                    [
                        ("2026-07-25", 10.0),
                        ("2026-07-26", 10.2),
                        ("2026-07-27", 10.1),
                        ("2026-07-28", 10.5),
                    ]
                )
            }
        )
        feeder = ShadowRealDataFeeder(
            data_provider=provider,
            output_path=tmp_path / "out.jsonl",
        )

        prev, target = feeder._fetch_symbol_prices("600276", "2026-07-29")

        assert target == 10.5
        assert prev == 10.1

    def test_string_index_handling(self, tmp_path):
        """字符串 index 也应能处理."""
        df = pd.DataFrame(
            {"close": [10.0, 10.5]},
            index=["2026-07-27", "2026-07-28"],
        )
        provider = MockMarketDataProvider(price_data={"600276": df})
        feeder = ShadowRealDataFeeder(
            data_provider=provider,
            output_path=tmp_path / "out.jsonl",
        )
        prev, target = feeder_with_mock_helper(feeder, "600276", "2026-07-28")
        assert target == 10.5


def feeder_with_mock_helper(feeder, symbol, date):
    """辅助: 直接调用 _fetch_symbol_prices."""
    return feeder._fetch_symbol_prices(symbol, date)


# ============================================================
# 权重加载测试
# ============================================================


class TestWeightsLoading:
    """测试权重加载逻辑."""

    def test_load_trade_plan_format(self, tmp_path):
        """解析 trade_plan_YYYYMMDD.json 格式."""
        trade_plan = {
            "execution_plan": {
                "day_capital": 100000,
                "morning_orders": [
                    {"code": "600276", "est_amount": 5000, "side": "BUY"},
                    {"code": "588000", "est_amount": 3000, "side": "BUY"},
                ],
                "afternoon_orders": [
                    {"code": "600276", "est_amount": 2000, "side": "SELL"},
                ],
            }
        }
        path = tmp_path / "trade_plan_20260728.json"
        path.write_text(json.dumps(trade_plan), encoding="utf-8")

        provider = MockMarketDataProvider()
        feeder = ShadowRealDataFeeder(
            data_provider=provider,
            weights_path=path,
            output_path=tmp_path / "out.jsonl",
        )
        weights = feeder._load_target_weights("2026-07-28")
        # 600276: (5000-2000)/100000 = 0.03
        # 588000: 3000/100000 = 0.03
        assert weights["600276"] == pytest.approx(0.03, abs=1e-6)
        assert weights["588000"] == pytest.approx(0.03, abs=1e-6)

    def test_load_strategy_plan_target_weights_format(self, tmp_path):
        """解析 strategy plan 的 target_weights 格式."""
        plan = {"target_weights": {"600276": 0.05, "588000": 0.03}}
        path = tmp_path / "plan_2026-07-28.json"
        path.write_text(json.dumps(plan), encoding="utf-8")

        provider = MockMarketDataProvider()
        feeder = ShadowRealDataFeeder(
            data_provider=provider,
            weights_path=path,
            output_path=tmp_path / "out.jsonl",
        )
        weights = feeder._load_target_weights("2026-07-28")
        assert weights == {"600276": 0.05, "588000": 0.03}

    def test_load_strategy_plan_positions_list_format(self, tmp_path):
        """解析 strategy plan 的 positions 列表格式."""
        plan = {
            "positions": [
                {"symbol": "600276", "weight": 0.05},
                {"symbol": "588000", "weight": 0.03},
            ]
        }
        path = tmp_path / "plan_2026-07-28.json"
        path.write_text(json.dumps(plan), encoding="utf-8")

        provider = MockMarketDataProvider()
        feeder = ShadowRealDataFeeder(
            data_provider=provider,
            weights_path=path,
            output_path=tmp_path / "out.jsonl",
        )
        weights = feeder._load_target_weights("2026-07-28")
        assert weights == {"600276": 0.05, "588000": 0.03}

    def test_load_positions_json_direct_dict(self, tmp_path):
        """解析 positions.json 的直接字典格式."""
        positions = {"600276": 0.05, "588000": 0.03}
        path = tmp_path / "positions.json"
        path.write_text(json.dumps(positions), encoding="utf-8")

        provider = MockMarketDataProvider()
        feeder = ShadowRealDataFeeder(
            data_provider=provider,
            weights_path=path,
            output_path=tmp_path / "out.jsonl",
        )
        weights = feeder._load_target_weights("2026-07-28")
        assert weights == {"600276": 0.05, "588000": 0.03}

    def test_load_positions_json_list_format(self, tmp_path):
        """解析 positions.json 的列表格式."""
        positions = {
            "positions": [
                {"symbol": "600276", "weight": 0.05},
                {"symbol": "588000", "weight": 0.03},
            ]
        }
        path = tmp_path / "positions.json"
        path.write_text(json.dumps(positions), encoding="utf-8")

        provider = MockMarketDataProvider()
        feeder = ShadowRealDataFeeder(
            data_provider=provider,
            weights_path=path,
            output_path=tmp_path / "out.jsonl",
        )
        weights = feeder._load_target_weights("2026-07-28")
        assert weights == {"600276": 0.05, "588000": 0.03}

    def test_manual_source_without_weights_raises(self, tmp_path):
        """weights_source='manual' 但未传 weights 应抛 WeightsLoadError."""
        provider = MockMarketDataProvider()
        feeder = ShadowRealDataFeeder(
            data_provider=provider,
            weights_source="manual",
            output_path=tmp_path / "out.jsonl",
        )
        with pytest.raises(WeightsLoadError, match="manual"):
            feeder._load_target_weights("2026-07-28")

    def test_file_not_exist_raises(self, tmp_path):
        """显式路径不存在应抛 WeightsLoadError."""
        provider = MockMarketDataProvider()
        feeder = ShadowRealDataFeeder(
            data_provider=provider,
            weights_path=tmp_path / "not_exist.json",
            output_path=tmp_path / "out.jsonl",
        )
        with pytest.raises(WeightsLoadError, match="不存在"):
            feeder._load_target_weights("2026-07-28")

    def test_invalid_json_raises(self, tmp_path):
        """非法 JSON 应抛 WeightsLoadError."""
        path = tmp_path / "positions.json"
        path.write_text("not a json", encoding="utf-8")

        provider = MockMarketDataProvider()
        feeder = ShadowRealDataFeeder(
            data_provider=provider,
            weights_path=path,
            output_path=tmp_path / "out.jsonl",
        )
        with pytest.raises(WeightsLoadError):
            feeder._load_target_weights("2026-07-28")

    def test_auto_mode_fallback_to_positions(self, tmp_path, monkeypatch):
        """auto 模式下应优先 trade_plan, 失败回退 positions_json."""
        # 用 monkeypatch 替换模块常量, 让候选路径指向 tmp_path
        import utils.alpha.shadow_real_data_feeder as mod

        trade_plans_dir = tmp_path / "trade_plans"
        strategy_dir = tmp_path / "strategy"
        positions_path = tmp_path / "positions.json"
        trade_plans_dir.mkdir()
        strategy_dir.mkdir()
        positions_path.write_text(json.dumps({"600276": 0.05}), encoding="utf-8")

        monkeypatch.setattr(mod, "_TRADE_PLAN_DIR", trade_plans_dir)
        monkeypatch.setattr(mod, "_STRATEGY_PLAN_DIR", strategy_dir)
        monkeypatch.setattr(mod, "_POSITIONS_JSON", positions_path)

        provider = MockMarketDataProvider()
        feeder = ShadowRealDataFeeder(
            data_provider=provider,
            weights_source="auto",
            output_path=tmp_path / "out.jsonl",
        )
        weights = feeder._load_target_weights("2026-07-28")
        assert weights == {"600276": 0.05}

    def test_auto_mode_prefers_trade_plan(self, tmp_path, monkeypatch):
        """auto 模式下 positions.json 优先于 trade_plan (2026-08-11 v8.6.14 修复).

        原优先级 trade_plan > strategy_plan > positions.json 会导致:
            - trade_plan 非空时取当日交易标的 (14个) 作为"持仓权重"
            - trade_plan 为空时 fallback 到 positions.json (26个全持仓)
            - symbols_count 在 14/26 间剧烈波动
        修复后: positions.json (持仓快照权威源) 提到首位.
        """
        import utils.alpha.shadow_real_data_feeder as mod

        trade_plans_dir = tmp_path / "trade_plans"
        strategy_dir = tmp_path / "strategy"
        positions_path = tmp_path / "positions.json"
        trade_plans_dir.mkdir()
        strategy_dir.mkdir()

        # 两个来源都有
        (trade_plans_dir / "trade_plan_20260728.json").write_text(
            json.dumps(
                {
                    "execution_plan": {
                        "day_capital": 100000,
                        "morning_orders": [
                            {"code": "600276", "est_amount": 5000, "side": "BUY"}
                        ],
                        "afternoon_orders": [],
                    }
                }
            ),
            encoding="utf-8",
        )
        positions_path.write_text(json.dumps({"588000": 0.03}), encoding="utf-8")

        monkeypatch.setattr(mod, "_TRADE_PLAN_DIR", trade_plans_dir)
        monkeypatch.setattr(mod, "_STRATEGY_PLAN_DIR", strategy_dir)
        monkeypatch.setattr(mod, "_POSITIONS_JSON", positions_path)

        provider = MockMarketDataProvider()
        feeder = ShadowRealDataFeeder(
            data_provider=provider,
            weights_source="auto",
            output_path=tmp_path / "out.jsonl",
        )
        weights = feeder._load_target_weights("2026-07-28")
        # positions.json 优先: 取 588000 (持仓快照)
        assert "588000" in weights
        assert weights["588000"] == pytest.approx(0.03, abs=1e-6)
        # 不应包含 trade_plan 中的 600276 (当日交易标的, 非持仓)
        assert "600276" not in weights


# ============================================================
# JSONL 写入测试
# ============================================================


class TestJsonlWriting:
    """测试 JSONL 增量写入."""

    def test_write_new_record(self, feeder_with_mock, tmp_path):
        """写入新记录."""
        result = FeedResult(
            date="2026-07-28",
            daily_return=0.025,
            success_count=2,
            total_count=2,
            coverage=1.0,
            source_tag=DEFAULT_SOURCE_TAG,
        )
        feeder_with_mock._update_jsonl(result)
        jsonl_path = tmp_path / "daily_returns.jsonl"
        assert jsonl_path.exists()
        with open(jsonl_path, encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert len(lines) == 1
        assert lines[0]["date"] == "2026-07-28"
        assert lines[0]["daily_return"] == 0.025
        assert lines[0]["source"] == DEFAULT_SOURCE_TAG
        assert lines[0]["cross_validated"] is False
        assert "updated_at" in lines[0]

    def test_update_existing_record(self, feeder_with_mock, existing_jsonl):
        """更新已存在的记录."""
        feeder_with_mock._output_path = existing_jsonl
        result = FeedResult(
            date="2026-07-27",  # jsonl 中已有此日期
            daily_return=0.025,
            success_count=2,
            total_count=2,
            coverage=1.0,
            source_tag=DEFAULT_SOURCE_TAG,
        )
        feeder_with_mock._update_jsonl(result)
        with open(existing_jsonl, encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        # 仍应是 2 条 (原 07-26 + 更新的 07-27)
        assert len(lines) == 2
        record_0727 = next(r for r in lines if r["date"] == "2026-07-27")
        assert record_0727["daily_return"] == 0.025
        assert record_0727["source"] == DEFAULT_SOURCE_TAG

    def test_partial_coverage_marks_warning(self, feeder_with_mock, tmp_path):
        """覆盖率不足 80% 时应标记 partial_coverage."""
        feeder_with_mock._output_path = tmp_path / "daily_returns.jsonl"
        result = FeedResult(
            date="2026-07-28",
            daily_return=0.025,
            success_count=1,
            total_count=2,
            coverage=0.5,  # 50% < 80%
            source_tag=DEFAULT_SOURCE_TAG,
        )
        feeder_with_mock._update_jsonl(result)
        with open(tmp_path / "daily_returns.jsonl", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert lines[0]["partial_coverage"] is True

    def test_warnings_written_to_jsonl(self, feeder_with_mock, tmp_path):
        """warnings 应写入 jsonl."""
        feeder_with_mock._output_path = tmp_path / "daily_returns.jsonl"
        result = FeedResult(
            date="2026-07-28",
            daily_return=0.06,  # > 5% 阈值
            success_count=2,
            total_count=2,
            coverage=1.0,
            source_tag=DEFAULT_SOURCE_TAG,
            warnings=["abnormal_return: 6.00%"],
        )
        feeder_with_mock._update_jsonl(result)
        with open(tmp_path / "daily_returns.jsonl", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert "warnings" in lines[0]
        assert "abnormal_return" in lines[0]["warnings"][0]

    def test_creates_parent_directory(self, mock_provider_with_prices, tmp_path):
        """输出目录不存在时应自动创建."""
        nested_path = tmp_path / "nested" / "deep" / "daily_returns.jsonl"
        feeder = ShadowRealDataFeeder(
            data_provider=mock_provider_with_prices,
            output_path=nested_path,
        )
        feeder.feed_single_date("2026-07-28", {"600276": 0.5, "588000": 0.5})
        assert nested_path.exists()


# ============================================================
# 安全护栏测试
# ============================================================


class TestSafetyGuards:
    """测试 _apply_safety_guards."""

    def test_abnormal_return_warning(self, feeder_with_mock):
        """单日 |ret| > 5% 应标记 warning."""
        result = FeedResult(
            date="2026-07-28",
            daily_return=0.06,  # 6% > 5%
            success_count=1,
            total_count=1,
            coverage=1.0,
        )
        feeder_with_mock._apply_safety_guards(result)
        assert any("abnormal_return" in w for w in result.warnings)

    def test_negative_abnormal_return_warning(self, feeder_with_mock):
        """单日 |ret| < -5% 也应标记 warning."""
        result = FeedResult(
            date="2026-07-28",
            daily_return=-0.07,  # -7%, |ret|=7% > 5%
            success_count=1,
            total_count=1,
            coverage=1.0,
        )
        feeder_with_mock._apply_safety_guards(result)
        assert any("abnormal_return" in w for w in result.warnings)

    def test_normal_return_no_warning(self, feeder_with_mock):
        """正常收益不应触发 warning."""
        result = FeedResult(
            date="2026-07-28",
            daily_return=0.02,  # 2% < 5%
            success_count=1,
            total_count=1,
            coverage=1.0,
        )
        feeder_with_mock._apply_safety_guards(result)
        assert not any("abnormal_return" in w for w in result.warnings)

    def test_all_symbols_fail_skipped(self, feeder_with_mock):
        """全部失败应标记 skipped."""
        result = FeedResult(
            date="2026-07-28",
            daily_return=0.0,
            success_count=0,
            total_count=2,
            coverage=0.0,
        )
        feeder_with_mock._apply_safety_guards(result)
        assert result.skipped is True
        assert result.error == "all_symbols_failed"

    def test_partial_coverage_warning(self, feeder_with_mock):
        """覆盖率 < 80% 应标记 warning + source_consistency=medium."""
        result = FeedResult(
            date="2026-07-28",
            daily_return=0.01,
            success_count=1,
            total_count=2,
            coverage=0.5,  # 50% < 80%
        )
        feeder_with_mock._apply_safety_guards(result)
        assert any("partial_coverage" in w for w in result.warnings)
        assert result.source_consistency == "medium"

    def test_full_coverage_no_warning(self, feeder_with_mock):
        """覆盖率 100% 不应触发 warning."""
        result = FeedResult(
            date="2026-07-28",
            daily_return=0.01,
            success_count=2,
            total_count=2,
            coverage=1.0,
        )
        feeder_with_mock._apply_safety_guards(result)
        assert not any("partial_coverage" in w for w in result.warnings)
        assert result.source_consistency == "high"

    def test_boundary_5pct_no_warning(self, feeder_with_mock):
        """正好 5% 不应触发 (使用 > 严格比较)."""
        result = FeedResult(
            date="2026-07-28",
            daily_return=0.05,  # 正好阈值
            success_count=1,
            total_count=1,
            coverage=1.0,
        )
        feeder_with_mock._apply_safety_guards(result)
        assert not any("abnormal_return" in w for w in result.warnings)

    def test_boundary_80pct_coverage_no_warning(self, feeder_with_mock):
        """覆盖率正好 80% 不应触发 warning (使用 < 严格比较)."""
        result = FeedResult(
            date="2026-07-28",
            daily_return=0.01,
            success_count=4,
            total_count=5,
            coverage=0.8,  # 正好阈值
        )
        feeder_with_mock._apply_safety_guards(result)
        assert not any("partial_coverage" in w for w in result.warnings)
        assert result.source_consistency == "high"


# ============================================================
# cross_validate 测试 (Day 2 实现: 多源校验 + 内部一致性检查)
# ============================================================


class TestCrossValidate:
    """测试 cross_validate (Day 2 实现: 多源校验 + 内部一致性检查)."""

    def test_internal_consistency_normal_returns_high(self, feeder_with_mock):
        """Day 2: 无 secondary_provider, 价格正常 → high consistency.

        mock_provider_with_prices 中 600276/588000 在 07-27~07-28 价格正常,
        内部一致性检查应返回 high (无异常 symbol).
        """
        target_weights = {"600276": 0.5, "588000": 0.5}
        result = feeder_with_mock.cross_validate(
            "2026-07-28", 0.025, target_weights=target_weights
        )
        assert isinstance(result, ValidationResult)
        assert result.date == "2026-07-28"
        assert result.primary_return == 0.025
        # 内部模式 secondary_return == primary_return
        assert result.secondary_return == 0.025
        assert result.max_diff == 0.0
        # 无异常 symbol → high consistency
        assert result.source_consistency == "high"
        assert result.is_valid is True
        assert "normal" in result.notes

    def test_no_weights_load_returns_unknown(
        self, feeder_with_mock, tmp_path, monkeypatch
    ):
        """Day 2: 无 target_weights 且 weights 加载失败 → unknown + is_valid=False.

        2026-08-11 v8.6.14: 需显式 monkeypatch _POSITIONS_JSON 到不存在路径,
        否则会 fallback 到项目实际 config/positions.json (含 26 个 symbol),
        导致 cross_validate 处理 26 个 symbol (全 no_price) 返回 inconsistent 而非 unknown.
        """
        import utils.alpha.shadow_real_data_feeder as mod

        monkeypatch.setattr(
            mod, "_TRADE_PLAN_DIR", tmp_path / "nonexistent_trade_plans"
        )
        monkeypatch.setattr(
            mod, "_STRATEGY_PLAN_DIR", tmp_path / "nonexistent_strategy"
        )
        monkeypatch.setattr(
            mod, "_POSITIONS_JSON", tmp_path / "nonexistent_positions.json"
        )

        result = feeder_with_mock.cross_validate("2026-07-28", 0.025)
        assert isinstance(result, ValidationResult)
        assert result.date == "2026-07-28"
        assert result.primary_return == 0.025
        # weights 加载失败时 source_consistency = unknown
        assert result.source_consistency == "unknown"
        assert result.is_valid is False
        assert "weights_load_failed" in result.notes

    def test_empty_weights_returns_unknown(self, feeder_with_mock):
        """Day 2: 空 target_weights → unknown + is_valid=False."""
        result = feeder_with_mock.cross_validate("2026-07-28", 0.025, target_weights={})
        assert isinstance(result, ValidationResult)
        assert result.source_consistency == "unknown"
        assert result.is_valid is False
        assert "empty_target_weights" in result.notes

    def test_invalid_date_raises(self, feeder_with_mock):
        """Day 2: 日期格式错误 → ValueError."""
        with pytest.raises(ValueError, match="日期格式错误"):
            feeder_with_mock.cross_validate("2026/07/28", 0.025)

    def test_secondary_provider_high_consistency(self, feeder_with_mock):
        """Day 2: 两个 provider 结果一致 → high consistency.

        primary_return 与 secondary_provider 重算结果接近 → high.
        """
        # 用相同数据的第二个 provider (结果应一致)
        secondary = MockMarketDataProvider(
            price_data={
                "600276": _make_price_df(
                    [
                        ("2026-07-25", 10.0),
                        ("2026-07-26", 10.0),
                        ("2026-07-27", 10.0),
                        ("2026-07-28", 10.5),
                    ]
                ),
            }
        )
        target_weights = {"600276": 1.0}
        result = feeder_with_mock.cross_validate(
            "2026-07-28",
            0.05,
            target_weights=target_weights,
            secondary_provider=secondary,
        )
        assert isinstance(result, ValidationResult)
        # primary=0.05, secondary 也应≈0.05 (同源同数据)
        assert result.primary_return == 0.05
        assert abs(result.secondary_return - 0.05) < 1e-6
        assert result.max_diff < 1e-6
        assert result.source_consistency == "high"
        assert result.is_valid is True
        # sources_used 应包含 tdx/akshare (两个 provider 的 source_health)
        assert "tdx" in result.sources_used

    def test_secondary_provider_inconsistent(self, feeder_with_mock):
        """Day 2: 主源与次源差异巨大 → inconsistent + is_valid=False."""
        # 次源数据完全不同 (close 100→101, ret=1% vs 主源 10→10.5 ret=5%)
        secondary = MockMarketDataProvider(
            price_data={
                "600276": _make_price_df(
                    [
                        ("2026-07-25", 100.0),
                        ("2026-07-26", 100.0),
                        ("2026-07-27", 100.0),
                        ("2026-07-28", 101.0),
                    ]
                ),
            }
        )
        target_weights = {"600276": 1.0}
        # primary_return=0.05 (5%), secondary 重算 = 0.01 (1%)
        result = feeder_with_mock.cross_validate(
            "2026-07-28",
            0.05,
            target_weights=target_weights,
            secondary_provider=secondary,
        )
        assert isinstance(result, ValidationResult)
        assert result.primary_return == 0.05
        assert result.secondary_return == pytest.approx(0.01, abs=1e-6)
        # relative_diff = |0.05-0.01|/|0.05| = 0.8 = 80% > 20% → inconsistent
        assert result.source_consistency == "inconsistent"
        assert result.is_valid is False
        assert "inconsistent" in result.notes

    def test_internal_with_abnormal_symbol(self, feeder_with_mock):
        """Day 2: 内部模式有异常 symbol → consistency 降级."""
        # 构造一个异常 symbol (价格 0 → 触发 non_positive_price)
        abnormal_provider = MockMarketDataProvider(
            price_data={
                "600276": _make_price_df([("2026-07-27", 10.0), ("2026-07-28", 10.5)]),
                # BAD001: prev_close=0 (异常)
                "BAD001": _make_price_df([("2026-07-27", 0.0), ("2026-07-28", 10.0)]),
            }
        )
        feeder = ShadowRealDataFeeder(
            data_provider=abnormal_provider,
            output_path=feeder_with_mock._output_path,
            cache_enabled=False,  # 避免缓存影响
        )
        target_weights = {"600276": 0.5, "BAD001": 0.5}
        result = feeder.cross_validate(
            "2026-07-28", 0.025, target_weights=target_weights
        )
        assert isinstance(result, ValidationResult)
        # 1/2 异常 → abnormal_ratio=0.5 → inconsistent (>30%)
        assert result.source_consistency in ("low", "inconsistent")
        assert result.is_valid is False


# ============================================================
# 数据类测试
# ============================================================


class TestFeedResult:
    """测试 FeedResult 数据类."""

    def test_is_success_true(self):
        """成功场景."""
        result = FeedResult(
            date="2026-07-28",
            daily_return=0.025,
            success_count=2,
            total_count=2,
        )
        assert result.is_success is True

    def test_is_success_false_when_skipped(self):
        """skipped 时 is_success 为 False."""
        result = FeedResult(
            date="2026-07-28",
            skipped=True,
            success_count=0,
        )
        assert result.is_success is False

    def test_is_success_false_when_zero_success(self):
        """success_count=0 时 is_success 为 False."""
        result = FeedResult(
            date="2026-07-28",
            success_count=0,
            total_count=2,
        )
        assert result.is_success is False

    def test_default_values(self):
        """默认值测试."""
        result = FeedResult(date="2026-07-28")
        assert result.daily_return == 0.0
        assert result.success_count == 0
        assert result.fail_count == 0
        assert result.total_count == 0
        assert result.coverage == 0.0
        assert result.symbols_detail == []
        assert result.source_tag == DEFAULT_SOURCE_TAG
        assert result.cross_validated is False
        assert result.source_consistency == "high"
        assert result.warnings == []
        assert result.written is False
        assert result.skipped is False
        assert result.error is None


# ============================================================
# 异常体系测试
# ============================================================


class TestExceptions:
    """测试异常类继承关系."""

    def test_shadow_feeder_error_is_exception(self):
        assert issubclass(ShadowRealDataFeederError, Exception)

    def test_data_provider_unavailable_is_shadow_error(self):
        assert issubclass(DataProviderUnavailableError, ShadowRealDataFeederError)

    def test_weights_load_error_is_shadow_error(self):
        assert issubclass(WeightsLoadError, ShadowRealDataFeederError)


# ============================================================
# 常量测试
# ============================================================


class TestConstants:
    """测试模块常量."""

    def test_abnormal_return_threshold(self):
        assert ABNORMAL_RETURN_THRESHOLD == 0.05

    def test_coverage_threshold(self):
        assert COVERAGE_THRESHOLD == 0.8

    def test_cross_source_diff_threshold(self):
        assert CROSS_SOURCE_DIFF_THRESHOLD == 0.01

    def test_min_significant_weight(self):
        assert MIN_SIGNIFICANT_WEIGHT == 0.001

    def test_default_source_tag(self):
        assert DEFAULT_SOURCE_TAG == "w13a_real_market_feed"

    def test_default_output_path(self):
        assert DEFAULT_OUTPUT_PATH.name == "daily_returns.jsonl"
        assert "reports" in str(DEFAULT_OUTPUT_PATH)
        assert "shadow" in str(DEFAULT_OUTPUT_PATH)

    def test_day2_constants_exist(self):
        """Day 2 新增常量存在且取值合理."""
        assert DEFAULT_MAX_WORKERS == 4
        assert MAX_MAX_WORKERS == 16
        assert DEFAULT_CACHE_MAX_SYMBOLS == 2000
        assert CACHE_TTL_DEFAULT_SEC == 3600
        # 2026-08-11 v8.6.14: "1m" 被 provider 误解为月线/年度数据,
        # 改为 "1d" 日K线以精确匹配历史日期
        assert DEFAULT_HISTORICAL_PERIOD == "1d"


# ============================================================
# Day 2: 构造函数参数验证测试
# ============================================================


class TestConstructorDay2:
    """测试 Day 2 新增构造参数 (max_workers / cache_enabled / cache_max_symbols)."""

    def test_default_max_workers(self, mock_provider_with_prices, tmp_path):
        """默认 max_workers=4."""
        feeder = ShadowRealDataFeeder(
            data_provider=mock_provider_with_prices,
            output_path=tmp_path / "out.jsonl",
        )
        assert feeder._max_workers == 4
        assert feeder._cache_enabled is True
        assert feeder._cache_max_symbols == 2000

    def test_invalid_max_workers_zero(self, mock_provider_with_prices, tmp_path):
        """max_workers=0 应抛 ValueError."""
        with pytest.raises(ValueError, match="max_workers 必须 >= 1"):
            ShadowRealDataFeeder(
                data_provider=mock_provider_with_prices,
                output_path=tmp_path / "out.jsonl",
                max_workers=0,
            )

    def test_invalid_max_workers_too_large(self, mock_provider_with_prices, tmp_path):
        """max_workers 超过上限应抛 ValueError."""
        with pytest.raises(ValueError, match="超过上限"):
            ShadowRealDataFeeder(
                data_provider=mock_provider_with_prices,
                output_path=tmp_path / "out.jsonl",
                max_workers=100,
            )

    def test_invalid_cache_max_symbols(self, mock_provider_with_prices, tmp_path):
        """cache_max_symbols=0 应抛 ValueError."""
        with pytest.raises(ValueError, match="cache_max_symbols 必须 >= 1"):
            ShadowRealDataFeeder(
                data_provider=mock_provider_with_prices,
                output_path=tmp_path / "out.jsonl",
                cache_max_symbols=0,
            )

    def test_serial_mode_max_workers_1(self, mock_provider_with_prices, tmp_path):
        """max_workers=1 时启用串行路径."""
        feeder = ShadowRealDataFeeder(
            data_provider=mock_provider_with_prices,
            output_path=tmp_path / "out.jsonl",
            max_workers=1,
        )
        assert feeder._max_workers == 1

    def test_cache_disabled_flag(self, mock_provider_with_prices, tmp_path):
        """cache_enabled=False 时禁用缓存."""
        feeder = ShadowRealDataFeeder(
            data_provider=mock_provider_with_prices,
            output_path=tmp_path / "out.jsonl",
            cache_enabled=False,
        )
        assert feeder._cache_enabled is False


# ============================================================
# Day 2: 价格缓存测试
# ============================================================


class TestPriceCache:
    """测试 Symbol 价格缓存 (Day 2 新增)."""

    def test_cache_miss_then_hit(self, mock_provider_with_prices, tmp_path):
        """首次拉取 miss, 第二次命中."""
        feeder = ShadowRealDataFeeder(
            data_provider=mock_provider_with_prices,
            output_path=tmp_path / "out.jsonl",
        )
        # 第一次: miss
        df1 = feeder._get_historical_data_cached("600276", "1m")
        assert df1 is not None
        stats = feeder.get_cache_stats()
        assert stats.misses == 1
        assert stats.hits == 0
        assert stats.size == 1

        # 第二次: hit
        df2 = feeder._get_historical_data_cached("600276", "1m")
        assert df2 is not None
        stats = feeder.get_cache_stats()
        assert stats.misses == 1
        assert stats.hits == 1
        assert stats.size == 1
        assert stats.hit_rate == 0.5

    def test_cache_disabled_bypasses_cache(self, mock_provider_with_prices, tmp_path):
        """cache_enabled=False 时不走缓存."""
        feeder = ShadowRealDataFeeder(
            data_provider=mock_provider_with_prices,
            output_path=tmp_path / "out.jsonl",
            cache_enabled=False,
        )
        df1 = feeder._get_historical_data_cached("600276", "1m")
        df2 = feeder._get_historical_data_cached("600276", "1m")
        assert df1 is not None
        assert df2 is not None
        # 缓存统计应全为 0 (不走缓存)
        stats = feeder.get_cache_stats()
        assert stats.hits == 0
        assert stats.misses == 0
        assert stats.size == 0

    def test_cache_lru_eviction(self, tmp_path):
        """缓存满时按 LRU 淘汰最旧条目."""
        # 用 cache_max_symbols=2 触发淘汰
        provider = MockMarketDataProvider(
            price_data={
                "S1": _make_price_df([("2026-07-27", 1.0), ("2026-07-28", 1.0)]),
                "S2": _make_price_df([("2026-07-27", 2.0), ("2026-07-28", 2.0)]),
                "S3": _make_price_df([("2026-07-27", 3.0), ("2026-07-28", 3.0)]),
            }
        )
        feeder = ShadowRealDataFeeder(
            data_provider=provider,
            output_path=tmp_path / "out.jsonl",
            cache_max_symbols=2,
        )
        # 填充 3 个, 应淘汰 S1
        feeder._get_historical_data_cached("S1", "1m")
        feeder._get_historical_data_cached("S2", "1m")
        feeder._get_historical_data_cached("S3", "1m")

        stats = feeder.get_cache_stats()
        assert stats.size == 2
        assert stats.evictions == 1
        assert stats.misses == 3

    def test_cache_hits_on_repeated_symbol(self, mock_provider_with_prices, tmp_path):
        """重复拉取同一 symbol 应命中缓存."""
        feeder = ShadowRealDataFeeder(
            data_provider=mock_provider_with_prices,
            output_path=tmp_path / "out.jsonl",
        )
        for _ in range(5):
            feeder._get_historical_data_cached("600276", "1m")
        stats = feeder.get_cache_stats()
        # 1 miss + 4 hits
        assert stats.misses == 1
        assert stats.hits == 4
        assert stats.hit_rate == 0.8

    def test_clear_cache(self, mock_provider_with_prices, tmp_path):
        """clear_cache 清空缓存并返回清理前统计."""
        feeder = ShadowRealDataFeeder(
            data_provider=mock_provider_with_prices,
            output_path=tmp_path / "out.jsonl",
        )
        feeder._get_historical_data_cached("600276", "1m")
        feeder._get_historical_data_cached("588000", "1m")
        assert feeder.get_cache_stats().size == 2

        stats_before = feeder.clear_cache()
        assert stats_before.size == 2
        assert feeder.get_cache_stats().size == 0
        # 累计计数不重置
        assert feeder.get_cache_stats().misses == 2

    def test_cache_provider_failure_not_cached(self, tmp_path):
        """provider 抛异常时不缓存失败结果, 下次重试."""
        provider = MockMarketDataProvider(
            price_data={},
            raise_on_symbol={"BAD001"},
        )
        feeder = ShadowRealDataFeeder(
            data_provider=provider,
            output_path=tmp_path / "out.jsonl",
        )
        # 第一次失败
        df1 = feeder._get_historical_data_cached("BAD001", "1m")
        assert df1 is None
        assert feeder.get_cache_stats().misses == 1
        assert feeder.get_cache_stats().size == 0
        # 第二次仍重试 (不缓存失败)
        df2 = feeder._get_historical_data_cached("BAD001", "1m")
        assert df2 is None
        assert feeder.get_cache_stats().misses == 2
        assert feeder.get_cache_stats().size == 0

    def test_get_cache_stats_returns_snapshot(
        self, mock_provider_with_prices, tmp_path
    ):
        """get_cache_stats 返回快照 (修改不影响内部状态)."""
        feeder = ShadowRealDataFeeder(
            data_provider=mock_provider_with_prices,
            output_path=tmp_path / "out.jsonl",
        )
        feeder._get_historical_data_cached("600276", "1m")
        snapshot = feeder.get_cache_stats()
        # 修改快照不影响内部
        snapshot.hits = 999
        assert feeder.get_cache_stats().hits == 0


# ============================================================
# Day 2: feed_history 并行化测试
# ============================================================


class TestFeedHistoryParallel:
    """测试 feed_history 并行模式 (Day 2 新增)."""

    def test_parallel_results_match_serial(self, mock_provider_with_prices, tmp_path):
        """并行模式结果应与串行模式一致 (按日期排序)."""
        weights_history = {
            "2026-07-27": {"600276": 0.5, "588000": 0.5},
            "2026-07-28": {"600276": 0.5, "588000": 0.5},
        }
        # 串行
        feeder_serial = ShadowRealDataFeeder(
            data_provider=mock_provider_with_prices,
            output_path=tmp_path / "serial.jsonl",
            max_workers=1,
        )
        results_serial = feeder_serial.feed_history(
            "2026-07-27",
            "2026-07-28",
            weights_history=weights_history,
        )

        # 并行 (max_workers=2)
        feeder_parallel = ShadowRealDataFeeder(
            data_provider=mock_provider_with_prices,
            output_path=tmp_path / "parallel.jsonl",
            max_workers=2,
        )
        results_parallel = feeder_parallel.feed_history(
            "2026-07-27",
            "2026-07-28",
            weights_history=weights_history,
        )

        # 日期顺序一致
        assert [r.date for r in results_serial] == [r.date for r in results_parallel]
        # daily_return 一致 (允许浮点误差)
        for r_s, r_p in zip(results_serial, results_parallel, strict=True):
            assert r_s.daily_return == pytest.approx(r_p.daily_return, abs=1e-9)

    def test_progress_callback_invoked(self, mock_provider_with_prices, tmp_path):
        """progress_callback 应被调用 total 次."""
        feeder = ShadowRealDataFeeder(
            data_provider=mock_provider_with_prices,
            output_path=tmp_path / "out.jsonl",
            max_workers=2,
        )
        progress_records: list[tuple[int, int]] = []

        def callback(current: int, total: int, result: FeedResult) -> None:
            progress_records.append((current, total))

        weights_history = {
            "2026-07-27": {"600276": 1.0},
            "2026-07-28": {"600276": 1.0},
        }
        feeder.feed_history(
            "2026-07-27",
            "2026-07-28",
            weights_history=weights_history,
            progress_callback=callback,
        )
        assert len(progress_records) == 2
        # current 递增
        currents = [c for c, _ in progress_records]
        assert sorted(currents) == [1, 2]
        # total 都是 2
        assert all(t == 2 for _, t in progress_records)

    def test_parallel_exception_does_not_break(self, tmp_path):
        """单日异常不应中断整体回填 (fail-safe)."""
        # 用一个会抛异常的 symbol 构造异常日
        provider = MockMarketDataProvider(
            price_data={
                "600276": _make_price_df([("2026-07-27", 10.0), ("2026-07-28", 10.5)]),
            },
            raise_on_symbol={"BAD001"},
        )
        feeder = ShadowRealDataFeeder(
            data_provider=provider,
            output_path=tmp_path / "out.jsonl",
            max_workers=2,
        )
        weights_history = {
            "2026-07-27": {"BAD001": 1.0},  # 全失败
            "2026-07-28": {"600276": 1.0},  # 成功
        }
        results = feeder.feed_history(
            "2026-07-27",
            "2026-07-28",
            weights_history=weights_history,
        )
        assert len(results) == 2
        # 07-27 失败 (BAD001 抛异常 → success_count=0 → skipped)
        assert results[0].skipped is True
        # 07-28 成功
        assert results[1].is_success is True

    def test_cache_warmup_reduces_provider_calls(self, tmp_path):
        """启用缓存时 provider 调用次数应远少于禁用."""
        call_count = {"n": 0}

        class CountingProvider(MockMarketDataProvider):
            def get_historical_data(self, symbol, period="1y"):
                call_count["n"] += 1
                return super().get_historical_data(symbol, period)

        provider = CountingProvider(
            price_data={
                "600276": _make_price_df(
                    [
                        ("2026-07-25", 10.0),
                        ("2026-07-26", 10.0),
                        ("2026-07-27", 10.0),
                        ("2026-07-28", 10.5),
                    ]
                ),
            }
        )
        weights_history = {
            "2026-07-27": {"600276": 1.0},
            "2026-07-28": {"600276": 1.0},
        }
        # 启用缓存
        feeder = ShadowRealDataFeeder(
            data_provider=provider,
            output_path=tmp_path / "out.jsonl",
            max_workers=1,
            cache_enabled=True,
        )
        call_count["n"] = 0
        feeder.feed_history(
            "2026-07-27",
            "2026-07-28",
            weights_history=weights_history,
        )
        calls_with_cache = call_count["n"]
        # 600276 应只被拉取 1 次 (缓存命中第二次)
        assert calls_with_cache == 1

    def test_serial_mode_callback(self, mock_provider_with_prices, tmp_path):
        """串行模式也支持 progress_callback."""
        feeder = ShadowRealDataFeeder(
            data_provider=mock_provider_with_prices,
            output_path=tmp_path / "out.jsonl",
            max_workers=1,
        )
        progress_records: list[tuple[int, int]] = []

        def callback(current: int, total: int, result: FeedResult) -> None:
            progress_records.append((current, total))

        weights_history = {
            "2026-07-27": {"600276": 1.0},
            "2026-07-28": {"600276": 1.0},
        }
        feeder.feed_history(
            "2026-07-27",
            "2026-07-28",
            weights_history=weights_history,
            progress_callback=callback,
        )
        assert len(progress_records) == 2
        # 串行按顺序
        currents = [c for c, _ in progress_records]
        assert currents == [1, 2]


# ============================================================
# Day 2: CacheStats 数据类测试
# ============================================================


class TestCacheStats:
    """测试 CacheStats 数据类 (Day 2 新增)."""

    def test_default_values(self):
        stats = CacheStats()
        assert stats.hits == 0
        assert stats.misses == 0
        assert stats.evictions == 0
        assert stats.size == 0
        assert stats.bytes_estimate == 0

    def test_hit_rate_zero_when_empty(self):
        """无任何调用时 hit_rate=0 (避免除零)."""
        stats = CacheStats()
        assert stats.hit_rate == 0.0

    def test_hit_rate_full(self):
        stats = CacheStats(hits=10, misses=0)
        assert stats.hit_rate == 1.0

    def test_hit_rate_half(self):
        stats = CacheStats(hits=5, misses=5)
        assert stats.hit_rate == 0.5


# ============================================================
# Day 2: HistoryFeedSummary 数据类测试
# ============================================================


class TestHistoryFeedSummary:
    """测试 HistoryFeedSummary 数据类 (Day 2 新增)."""

    def test_default_values(self):
        summary = HistoryFeedSummary()
        assert summary.start_date == ""
        assert summary.end_date == ""
        assert summary.total_days == 0
        assert summary.success_days == 0
        assert summary.skipped_days == 0
        assert summary.failed_days == 0
        assert summary.total_daily_return == 0.0
        assert summary.avg_daily_return == 0.0
        assert summary.max_daily_return == 0.0
        assert summary.min_daily_return == 0.0
        assert summary.cache_stats is None
        assert summary.elapsed_sec == 0.0

    def test_with_values(self):
        summary = HistoryFeedSummary(
            start_date="2026-07-23",
            end_date="2026-08-04",
            total_days=13,
            success_days=9,
            skipped_days=4,
            failed_days=0,
            total_daily_return=0.025,
            avg_daily_return=0.0028,
            max_daily_return=0.015,
            min_daily_return=-0.008,
            elapsed_sec=12.5,
        )
        assert summary.start_date == "2026-07-23"
        assert summary.success_days == 9
        assert summary.cache_stats is None  # 默认 None
        assert summary.elapsed_sec == 12.5


# ============================================================
# CLI main 测试
# ============================================================


class TestCliMain:
    """测试 CLI main 入口 (基础场景)."""

    def test_no_args_prints_help(self, capsys):
        """无参数应打印 help 并返回 1."""
        from utils.alpha.shadow_real_data_feeder import main

        # 模拟无参数
        exit_code = main([])
        captured = capsys.readouterr()
        assert exit_code == 1
        assert "usage" in captured.out.lower() or "usage" in captured.err.lower()

    def test_invalid_date_returns_1(self, capsys, monkeypatch):
        """非法日期应返回 1."""
        # Mock MarketDataProvider 避免真实初始化
        import utils.alpha.shadow_real_data_feeder as mod

        class FakeProvider:
            def __init__(self, *args, **kwargs):
                self.source_health = {"tdx": {"ok": True}}

            def get_historical_data(self, symbol, period="1y"):
                return pd.DataFrame()

        monkeypatch.setattr("utils.data_provider.MarketDataProvider", FakeProvider)

        exit_code = mod.main(["--date", "invalid-date"])
        assert exit_code == 1

    def test_dry_run_flag(self, monkeypatch, tmp_path):
        """--dry-run 应不写盘 (但需要 mock provider 避免真实网络)."""
        import utils.alpha.shadow_real_data_feeder as mod

        # Mock provider 返回价格数据
        price_data = {
            "600276": _make_price_df([("2026-07-27", 10.0), ("2026-07-28", 10.5)]),
        }

        class FakeProvider:
            def __init__(self, *args, **kwargs):
                self.source_health = {"tdx": {"ok": True, "last_error": ""}}
                self._price_data = price_data

            def get_historical_data(self, symbol, period="1y"):
                return self._price_data.get(symbol, pd.DataFrame())

        monkeypatch.setattr("utils.data_provider.MarketDataProvider", FakeProvider)

        # 使用 manual 模式 + 手动传入权重 (通过 --weights-source manual 时无法自动加载)
        # 改用 plan_file 模式 + 临时文件
        trade_plan = {
            "execution_plan": {
                "day_capital": 100000,
                "morning_orders": [
                    {"code": "600276", "est_amount": 5000, "side": "BUY"}
                ],
                "afternoon_orders": [],
            }
        }
        weights_file = tmp_path / "trade_plan_20260728.json"
        weights_file.write_text(json.dumps(trade_plan), encoding="utf-8")

        output = tmp_path / "out.jsonl"
        exit_code = mod.main(
            [
                "--date",
                "2026-07-28",
                "--dry-run",
                "--weights-file",
                str(weights_file),
                "--output",
                str(output),
            ]
        )
        # 应成功 (exit_code=0)
        assert exit_code == 0
        # dry-run 模式不应写盘
        assert not output.exists()
