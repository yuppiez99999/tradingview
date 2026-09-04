"""U2 回测涨跌停/停牌数据接入 — 单元测试

测试覆盖:
    1. 代码归一化 + 板块识别 (主板/创业板/科创板/北交所/ETF/可转债)
    2. 涨跌停比例 (±10%/±5% ST/±20%/±30%)
    3. 涨跌停价计算 (含四舍五入到分 ROUND_HALF_UP)
    4. 停牌检测 (price<=0 / volume<=0+open<=0 / 一字板不误判)
    5. enrich_day_data_list (轻量格式富化, 首日无 limit, prev_close 链式)
    6. build_backtest_data_from_ohlcv (OHLCV 构建, 停牌日估值冻结)
    7. 端到端: 回测引擎涨停禁买/跌停禁卖/停牌冻结生效
    8. 向后兼容: 无 limit 字段时回测行为不变
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

_PROJ = Path(__file__).resolve().parent.parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from utils.price_limit_calculator import (  # noqa: E402
    BoardType,
    build_backtest_data_from_ohlcv,
    calc_limit_prices,
    detect_suspended_from_row,
    enrich_day_data_list,
    get_board_type,
    get_limit_pct,
    is_at_limit_down,
    is_at_limit_up,
    normalize_code,
)
from utils.wt_backtest_engine import BacktestDataLoader, BacktestEngine  # noqa: E402


# ============================================================
# 1. 代码归一化 + 板块识别
# ============================================================
class TestNormalizeCode:
    def test_strip_suffix(self):
        assert normalize_code("300750.SZ") == "300750"
        assert normalize_code("600519.SH") == "600519"
        assert normalize_code("688981.SH") == "688981"

    def test_strip_prefix(self):
        assert normalize_code("sh600519") == "600519"
        assert normalize_code("sz300750") == "300750"

    def test_already_digits(self):
        assert normalize_code("000001") == "000001"

    def test_empty(self):
        assert normalize_code("") == ""
        assert normalize_code(None) == ""  # type: ignore[arg-type]


class TestGetBoardType:
    @pytest.mark.parametrize(
        "code,expected",
        [
            ("600519.SH", BoardType.MAIN_SH),  # 沪市主板
            ("601318.SH", BoardType.MAIN_SH),
            ("000001.SZ", BoardType.MAIN_SZ),  # 深市主板
            ("002594.SZ", BoardType.MAIN_SZ),  # 原中小板已合并主板
            ("300750.SZ", BoardType.GEM),  # 创业板
            ("301266.SZ", BoardType.GEM),  # 创业板 (301)
            ("688981.SH", BoardType.STAR),  # 科创板
            ("689009.SH", BoardType.STAR),
            ("830879.BJ", BoardType.BSE),  # 北交所
            ("430047.BJ", BoardType.BSE),
            ("510050.SH", BoardType.ETF),  # 沪市 ETF
            ("588080.SH", BoardType.ETF),
            ("159915.SZ", BoardType.ETF),  # 深市 ETF
            ("161725.SZ", BoardType.ETF),  # LOF
            ("113001.SH", BoardType.BOND),  # 可转债
            ("123001.SZ", BoardType.BOND),
        ],
    )
    def test_board_classification(self, code, expected):
        assert get_board_type(code) == expected

    def test_unknown_code_fallback(self):
        assert get_board_type("999999") == BoardType.UNKNOWN


# ============================================================
# 2. 涨跌停比例
# ============================================================
class TestGetLimitPct:
    def test_main_board_10pct(self):
        assert get_limit_pct("600519.SH") == 0.10
        assert get_limit_pct("000001.SZ") == 0.10

    def test_gem_20pct(self):
        assert get_limit_pct("300750.SZ") == 0.20

    def test_star_20pct(self):
        assert get_limit_pct("688981.SH") == 0.20

    def test_bse_30pct(self):
        assert get_limit_pct("830879.BJ") == 0.30

    def test_st_5pct(self):
        # ST 统一 ±5%, 无论板块
        assert get_limit_pct("600519.SH", is_st=True) == 0.05
        assert get_limit_pct("300750.SZ", is_st=True) == 0.05
        assert get_limit_pct("688981.SH", is_st=True) == 0.05

    def test_bond_no_limit(self):
        assert get_limit_pct("113001.SH") == 0.0

    def test_etf_10pct(self):
        assert get_limit_pct("510050.SH") == 0.10


# ============================================================
# 3. 涨跌停价计算 (含四舍五入)
# ============================================================
class TestCalcLimitPrices:
    def test_main_board(self):
        # 10.00 → 11.00 / 9.00
        lu, ld = calc_limit_prices(10.00, "600519.SH")
        assert lu == 11.00
        assert ld == 9.00

    def test_gem_20pct(self):
        # 100.00 → 120.00 / 80.00
        lu, ld = calc_limit_prices(100.00, "300750.SZ")
        assert lu == 120.00
        assert ld == 80.00

    def test_st_5pct(self):
        # 10.00 → 10.50 / 9.50
        lu, ld = calc_limit_prices(10.00, "600519.SH", is_st=True)
        assert lu == 10.50
        assert ld == 9.50

    def test_bse_30pct(self):
        # 10.00 → 13.00 / 7.00
        lu, ld = calc_limit_prices(10.00, "830879.BJ")
        assert lu == 13.00
        assert ld == 7.00

    def test_rounding_half_up(self):
        # 9.99 × 1.10 = 10.989 → 四舍五入到 10.99
        # Python round(10.989, 2) = 10.99 (此处无歧义)
        # 但 9.95 × 1.10 = 10.945 → ROUND_HALF_UP = 10.95 (银行家舍入也是 10.95, 无差异)
        # 用 10.005 测试: 主板涨停 10.005 × 1.10 = 11.0055 → 11.01 (ROUND_HALF_UP)
        #   但 Python round(11.0055, 2) 可能因浮点表现不同, 用 Decimal 确保
        lu, ld = calc_limit_prices(10.005, "600519.SH")
        # 10.005 × 1.10 = 11.0055 → 11.01 (四舍五入)
        # 10.005 × 0.90 = 9.0045 → 9.00
        assert lu == 11.01, f"11.0055 应四舍五入为 11.01, 实际 {lu}"
        assert ld == 9.00

    def test_rounding_classic_case(self):
        # 经典: 9.83 × 1.10 = 10.813 → 10.81
        lu, ld = calc_limit_prices(9.83, "000001.SZ")
        assert lu == 10.81
        assert ld == 8.85  # 9.83 × 0.90 = 8.847 → 8.85

    def test_prev_close_zero(self):
        lu, ld = calc_limit_prices(0.0, "600519.SH")
        assert lu == 0.0
        assert ld == 0.0

    def test_prev_close_negative(self):
        lu, ld = calc_limit_prices(-1.0, "600519.SH")
        assert lu == 0.0
        assert ld == 0.0

    def test_bond_no_limit(self):
        # 可转债无涨跌幅限制, 返回 (0.0, 0.0)
        lu, ld = calc_limit_prices(100.00, "113001.SH")
        assert lu == 0.0
        assert ld == 0.0


class TestIsAtLimit:
    def test_at_limit_up(self):
        assert is_at_limit_up(11.00, 11.00) is True
        assert is_at_limit_up(11.001, 11.00) is True  # 容差内

    def test_below_limit_up(self):
        assert is_at_limit_up(10.99, 11.00) is False

    def test_at_limit_down(self):
        assert is_at_limit_down(9.00, 9.00) is True
        assert is_at_limit_down(8.999, 9.00) is True

    def test_above_limit_down(self):
        assert is_at_limit_down(9.01, 9.00) is False

    def test_zero_limit(self):
        assert is_at_limit_up(100, 0.0) is False
        assert is_at_limit_down(100, 0.0) is False


# ============================================================
# 4. 停牌检测
# ============================================================
class TestDetectSuspended:
    def test_normal_trading(self):
        assert (
            detect_suspended_from_row(close=10.0, volume=1000, open_price=10.0) is False
        )

    def test_zero_close(self):
        assert detect_suspended_from_row(close=0.0, volume=0, open_price=0) is True

    def test_zero_volume_with_open(self):
        # 一字涨停: volume=0 但 open>0, 不是停牌
        assert detect_suspended_from_row(close=11.0, volume=0, open_price=11.0) is False

    def test_zero_volume_zero_open(self):
        # volume=0 且 open=0 → 停牌
        assert detect_suspended_from_row(close=10.0, volume=0, open_price=0) is True

    def test_negative_close(self):
        assert detect_suspended_from_row(close=-1.0) is True


# ============================================================
# 5. enrich_day_data_list (轻量格式)
# ============================================================
class TestEnrichDayDataList:
    def test_basic_enrichment(self):
        data = [
            {"date": "2024-01-01", "prices": {"600519.SH": 10.0}},
            {"date": "2024-01-02", "prices": {"600519.SH": 11.0}},  # 涨停
            {"date": "2024-01-03", "prices": {"600519.SH": 9.0}},  # 跌停
        ]
        result = enrich_day_data_list(data)

        # 首日无 limit 字段 (无前收盘价)
        assert result[0]["limit_up_prices"] == {}
        assert result[0]["limit_down_prices"] == {}

        # 第二日: prev_close=10.0 → limit_up=11.0, limit_down=9.0
        assert result[1]["limit_up_prices"]["600519.SH"] == 11.0
        assert result[1]["limit_down_prices"]["600519.SH"] == 9.0

        # 第三日: prev_close=11.0 → limit_up=12.1, limit_down=9.9
        assert result[2]["limit_up_prices"]["600519.SH"] == 12.1
        assert result[2]["limit_down_prices"]["600519.SH"] == 9.9

    def test_suspended_detection_lightweight(self):
        data = [
            {"date": "2024-01-01", "prices": {"600519.SH": 10.0, "000001.SZ": 0.0}},
            {"date": "2024-01-02", "prices": {"600519.SH": 11.0, "000001.SZ": 0.0}},
        ]
        result = enrich_day_data_list(data)
        # 000001.SZ 价格为 0 → 停牌
        assert result[0]["suspended"]["000001.SZ"] is True
        assert result[1]["suspended"]["000001.SZ"] is True
        assert result[0]["suspended"]["600519.SH"] is False

    def test_st_codes(self):
        data = [
            {"date": "2024-01-01", "prices": {"600519.SH": 10.0}},
            {"date": "2024-01-02", "prices": {"600519.SH": 10.5}},
        ]
        # 600519 标记为 ST → ±5%
        result = enrich_day_data_list(data, st_codes={"600519.SH"})
        assert result[1]["limit_up_prices"]["600519.SH"] == 10.5  # 10 × 1.05
        assert result[1]["limit_down_prices"]["600519.SH"] == 9.5  # 10 × 0.95

    def test_gem_20pct_in_enrichment(self):
        data = [
            {"date": "2024-01-01", "prices": {"300750.SZ": 100.0}},
            {"date": "2024-01-02", "prices": {"300750.SZ": 120.0}},
        ]
        result = enrich_day_data_list(data)
        assert result[1]["limit_up_prices"]["300750.SZ"] == 120.0  # 100 × 1.20
        assert result[1]["limit_down_prices"]["300750.SZ"] == 80.0  # 100 × 0.80

    def test_empty_data(self):
        assert enrich_day_data_list([]) == []

    def test_does_not_overwrite_existing(self):
        # setdefault: 不覆盖调用方预先提供的字段
        data = [
            {
                "date": "2024-01-01",
                "prices": {"600519.SH": 10.0},
                "limit_up_prices": {"600519.SH": 99.99},
            },
            {"date": "2024-01-02", "prices": {"600519.SH": 11.0}},
        ]
        result = enrich_day_data_list(data)
        # 首日已有 limit_up_prices, 不覆盖
        assert result[0]["limit_up_prices"]["600519.SH"] == 99.99

    def test_inplace_modification(self):
        # 返回同一对象 (原地修改)
        data = [{"date": "2024-01-01", "prices": {"600519.SH": 10.0}}]
        result = enrich_day_data_list(data)
        assert result is data


# ============================================================
# 6. build_backtest_data_from_ohlcv
# ============================================================
class TestBuildFromOhlcv:
    def _make_price_df(self, closes, volumes=None, opens=None):
        dates = pd.date_range("2024-01-01", periods=len(closes), freq="D")
        df = pd.DataFrame(
            {
                "open": opens or closes,
                "high": [c * 1.02 for c in closes],
                "low": [c * 0.98 for c in closes],
                "close": closes,
                "volume": volumes if volumes else [1000] * len(closes),
            },
            index=dates,
        )
        return df

    def test_basic_build(self):
        price_data = {
            "600519.SH": self._make_price_df([10.0, 11.0, 9.0]),
        }
        data = build_backtest_data_from_ohlcv(price_data)

        assert len(data) == 3
        assert "limit_up_prices" in data[0]
        assert "limit_down_prices" in data[0]
        assert "suspended" in data[0]

        # 首日无前收盘价 → 无 limit
        assert data[0]["limit_up_prices"] == {}
        # 次日: prev_close=10 → limit_up=11
        assert data[1]["limit_up_prices"]["600519.SH"] == 11.0
        assert data[1]["limit_down_prices"]["600519.SH"] == 9.0

    def test_suspended_day_freeze(self):
        # 600519 在第 2 日停牌 (无数据), 第 3 日恢复;
        # 加入第二个标的 000001 撑起完整 3 日交易日历, 使第 2 日进入 union
        dates = pd.date_range("2024-01-01", periods=3, freq="D")
        df_main = pd.DataFrame(
            {
                "open": [10.0, 9.0],
                "high": [10.2, 9.2],
                "low": [9.8, 8.8],
                "close": [10.0, 9.0],
                "volume": [1000, 1000],
            },
            index=[dates[0], dates[2]],
        )  # 缺第 2 日 → 停牌
        # 第二个标的完整 3 日数据, 确保第 2 日进入交易日历 union
        df_aux = pd.DataFrame(
            {
                "open": [5.0, 5.5, 5.2],
                "high": [5.1, 5.6, 5.3],
                "low": [4.9, 5.4, 5.1],
                "close": [5.0, 5.5, 5.2],
                "volume": [2000, 2000, 2000],
            },
            index=dates,
        )
        price_data = {"600519.SH": df_main, "000001.SZ": df_aux}
        data = build_backtest_data_from_ohlcv(price_data)

        # 共 3 个交易日 (union)
        assert len(data) == 3
        # 第 2 日 (dates[1]) 600519 停牌, 价格应冻结为前一日 close=10.0
        assert data[1]["suspended"]["600519.SH"] is True
        assert data[1]["prices"]["600519.SH"] == 10.0
        # 000001 第 2 日正常交易
        assert data[1]["suspended"]["000001.SZ"] is False

    def test_zero_volume_suspension(self):
        # volume=0 且 open=0 → 停牌
        price_data = {
            "600519.SH": self._make_price_df(
                closes=[10.0, 10.0],
                volumes=[1000, 0],
                opens=[10.0, 0.0],
            ),
        }
        data = build_backtest_data_from_ohlcv(price_data)
        assert data[0]["suspended"]["600519.SH"] is False
        assert data[1]["suspended"]["600519.SH"] is True

    def test_one_word_board_not_suspended(self):
        # 一字涨停: volume=0 但 open=close=high=low>0, 不是停牌
        price_data = {
            "600519.SH": self._make_price_df(
                closes=[10.0, 11.0],
                volumes=[1000, 0],
                opens=[10.0, 11.0],
            ),
        }
        data = build_backtest_data_from_ohlcv(price_data)
        # 第 2 日 volume=0 但 open=11.0>0 → 非停牌
        assert data[1]["suspended"]["600519.SH"] is False

    def test_multiple_symbols(self):
        price_data = {
            "600519.SH": self._make_price_df([10.0, 11.0]),
            "300750.SZ": self._make_price_df([100.0, 120.0]),
        }
        data = build_backtest_data_from_ohlcv(price_data)
        # 主板 ±10%, 创业板 ±20%
        assert data[1]["limit_up_prices"]["600519.SH"] == 11.0
        assert data[1]["limit_up_prices"]["300750.SZ"] == 120.0

    def test_empty_input(self):
        assert build_backtest_data_from_ohlcv({}) == []

    def test_with_etf_signals(self):
        price_data = {"600519.SH": self._make_price_df([10.0, 11.0])}
        signals = {"2024-01-02": {"600519.SH": {"signal": "强加仓", "inflow": 50}}}
        data = build_backtest_data_from_ohlcv(price_data, etf_signals_by_date=signals)
        assert data[1]["etf_signals"]["600519.SH"]["signal"] == "强加仓"


# ============================================================
# 7. 端到端: 回测引擎约束生效
# ============================================================
class TestBacktestEngineConstraints:
    def test_limit_up_blocks_buy(self):
        """涨停 (price >= limit_up) 不可买入——P0-3 延迟成交下在执行日拦截"""
        # T 日(01-01)收盘生成强加仓 BUY 信号, T+1 日(01-02)开盘撮合时涨停
        data = [
            {
                "date": "2024-01-01",
                "prices": {"600519.SH": 10.0},
                "etf_signals": {"600519.SH": {"signal": "强加仓", "inflow": 100}},
            },
            {"date": "2024-01-02", "prices": {"600519.SH": 11.0}},
        ]
        enrich_day_data_list(data)
        # 验证 limit_up 已注入 (执行日涨停价)
        assert data[1]["limit_up_prices"]["600519.SH"] == 11.0

        engine = BacktestEngine(initial_capital=1_000_000)
        strategy = __import__(
            "utils.wt_backtest_engine", fromlist=["ETFSignalStrategy"]
        ).ETFSignalStrategy()

        def strategy_func(day_data, positions):
            return strategy.generate_signals(day_data, positions)

        result = engine.run(data, strategy_func, verbose=False)
        # T 日信号存在(挂起), 但执行日涨停买入被拦截 → 无 BUY 交易
        assert result["buy_trades"] == 0, "执行日涨停不应有买入交易"

    def test_limit_down_blocks_sell(self):
        """跌停 (price <= limit_down) 不可卖出——P0-3 延迟成交下在执行日拦截"""
        # 01-01 收盘 BUY 信号 → 01-02 撮合成功建仓 → 01-02 收盘 SELL 信号
        # → 01-03 执行日开盘跌停, SELL 被拦截
        data = [
            {
                "date": "2024-01-01",
                "prices": {"600519.SH": 10.0},
                "etf_signals": {"600519.SH": {"signal": "强加仓", "inflow": 100}},
            },
            {
                "date": "2024-01-02",
                "prices": {"600519.SH": 10.0},
                "etf_signals": {"600519.SH": {"signal": "强减仓", "inflow": -100}},
            },
            {"date": "2024-01-03", "prices": {"600519.SH": 9.0}},
        ]
        enrich_day_data_list(data)
        # 验证 limit_down 已注入 (01-03 跌停价: 前收 10.0 × 0.90)
        assert data[2]["limit_down_prices"]["600519.SH"] == 9.0

        engine = BacktestEngine(initial_capital=1_000_000)
        strategy = __import__(
            "utils.wt_backtest_engine", fromlist=["ETFSignalStrategy"]
        ).ETFSignalStrategy()

        def strategy_func(day_data, positions):
            return strategy.generate_signals(day_data, positions)

        result = engine.run(data, strategy_func, verbose=False)
        # 01-02 撮合 BUY 成功; 01-03 跌停撮合 SELL 被拦截 → 有 BUY 无 SELL
        assert result["buy_trades"] >= 1, "首日信号应在次日撮合成功"
        assert result["sell_trades"] == 0, "执行日跌停不应有卖出交易"

    def test_suspended_blocks_trading(self):
        """停牌标的不可交易——P0-3 延迟成交下在执行日拦截"""
        # T 日(01-01)收盘产生强加仓 BUY 信号, T+1 日(01-02)停牌无法撮合
        data = [
            {
                "date": "2024-01-01",
                "prices": {"600519.SH": 10.0},
                "etf_signals": {"600519.SH": {"signal": "强加仓", "inflow": 100}},
            },
            # 第二日停牌 (价格为 0)
            {"date": "2024-01-02", "prices": {"600519.SH": 0.0}},
        ]
        enrich_day_data_list(data)
        # 验证 suspended 已注入
        assert data[1]["suspended"]["600519.SH"] is True

        engine = BacktestEngine(initial_capital=1_000_000)
        strategy = __import__(
            "utils.wt_backtest_engine", fromlist=["ETFSignalStrategy"]
        ).ETFSignalStrategy()

        def strategy_func(day_data, positions):
            return strategy.generate_signals(day_data, positions)

        result = engine.run(data, strategy_func, verbose=False)
        # 执行日停牌/无行情, BUY 无法撮合
        assert result["buy_trades"] == 0


# ============================================================
# 8. 向后兼容
# ============================================================
class TestBackwardCompatibility:
    def test_no_limit_fields_unchanged(self):
        """无 limit 字段时回测不施加 A 股约束 (向后兼容)"""
        # 不调用 enrich, 直接构造无 limit 字段的数据
        data = [
            {
                "date": "2024-01-01",
                "prices": {"600519.SH": 10.0},
                "etf_signals": {"600519.SH": {"signal": "强加仓", "inflow": 100}},
            },
            # 第二日"涨停" 11.0 但无 limit_up_prices → 不约束, 次日撮合应成交
            {
                "date": "2024-01-02",
                "prices": {"600519.SH": 11.0},
                "etf_signals": {"600519.SH": {"signal": "强加仓", "inflow": 100}},
            },
        ]
        # 不调用 enrich_day_data_list

        engine = BacktestEngine(initial_capital=1_000_000)
        strategy = __import__(
            "utils.wt_backtest_engine", fromlist=["ETFSignalStrategy"]
        ).ETFSignalStrategy()

        def strategy_func(day_data, positions):
            return strategy.generate_signals(day_data, positions)

        result = engine.run(data, strategy_func, verbose=False)
        # 无约束 → T 日信号在次日撮合成功; 第二日信号因窗口结束未撮合
        assert result["buy_trades"] >= 1, "无 limit 字段时不应约束买入"

    def test_synthetic_with_limit_flag(self):
        """generate_synthetic_data(with_limit_constraints=True) 注入字段"""
        data = BacktestDataLoader.generate_synthetic_data(
            "2024-01-01",
            "2024-01-15",
            ["600519.SH", "300750.SZ"],
            with_limit_constraints=True,
        )
        # 首日无 limit, 次日起有
        assert "limit_up_prices" in data[0]
        assert "suspended" in data[0]
        # 至少有一天有限制字段 (非首日)
        has_limit = any(d["limit_up_prices"] for d in data[1:])
        assert has_limit, "应有非首日注入 limit_up_prices"

    def test_synthetic_without_limit_flag_backward_compat(self):
        """generate_synthetic_data(with_limit_constraints=False) 默认不注入 (向后兼容)"""
        data = BacktestDataLoader.generate_synthetic_data(
            "2024-01-01",
            "2024-01-05",
            ["600519.SH"],
        )
        # 默认不注入 limit 字段
        assert "limit_up_prices" not in data[0]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
