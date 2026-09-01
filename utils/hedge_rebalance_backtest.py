"""
对冲+再平衡联动 — 回测引擎 v2.0 (优化版)

v2.0 改进（基于v1.0 2021-2026回测发现）:
1. 新增 S5: S2(动态再平衡) + 组合自触发尾部对冲(仅vol>28%或DD>12%)
2. S4 改为多指数Beta加权对冲(IC/IM/IF按比例), 替代纯CSI300
3. 新增对冲有效性指标: 对冲收益/成本比, 对冲成功/失败天数
4. 组合自波动率和60日回撤作为对冲触发条件

v8.7 新增:
6. 新增 S6: S2(动态再平衡) + RegimeFolio动态阈值尾部对冲(VIX regime自适应)
   - 与S5唯一差异: 尾部对冲阈值随VIX regime自适应(低波0.35/正常0.28/高波0.22/危机0.15)
   - VIX: CSI300近30日已实现波动率*100 (A股无官方VIX)

对比策略:
  S1: 静态目标权重 + 无对冲 (基准)
  S2: 动态3-8%阈值再平衡 + 无对冲
  S3: 固定50%CSI300对冲 + 静态再平衡
  S4: 多指数Beta加权动态对冲 + 动态再平衡 + 板块轮动 (v2.0优化)
  S5: S2动态再平衡 + 组合自触发尾部对冲 (v2.0新增, v5.9固定阈值)
  S6: S2动态再平衡 + v8.7 RegimeFolio动态阈值尾部对冲 (v8.7新增)
"""
from __future__ import annotations

import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ============================================================
# 配置
# ============================================================

# v5.9 注: 回测基于 15 标的社保基金风格 (与 portfolio.yaml 一致)
# 主系统 v5.9 使用 核心-卫星风格 20 标的 (12 股 + 7 ETF + 现金)，回测标的为历史数据配置
PORTFOLIO_CODES = [
    "300308",
    "688041",
    "002371",
    "688981",
    "300750",
    "000425",
    "601088",
    "600219",
    "600019",
    "518880",
    "000792",
    "600900",
    "600276",
    "603259",
    "002422",
]

CODE_NAMES = {
    "300308": "中际旭创",
    "688041": "海光信息",
    "002371": "北方华创",
    "688981": "中芯国际",
    "300750": "宁德时代",
    "000425": "徐工机械",
    "601088": "中国神华",
    "600219": "南山铝业",
    "600019": "宝钢股份",
    "518880": "华安黄金ETF",
    "000792": "盐湖股份",
    "600900": "长江电力",
    "600276": "恒瑞医药",
    "603259": "药明康德",
    "002422": "科伦药业",
}

CODE_CATEGORIES = {
    "300308": "high_end_manufacturing",
    "688041": "high_end_manufacturing",
    "002371": "high_end_manufacturing",
    "688981": "high_end_manufacturing",
    "300750": "high_end_manufacturing",
    "000425": "high_end_manufacturing",
    "601088": "cyclical",
    "600219": "cyclical",
    "600019": "cyclical",
    "518880": "resources",
    "000792": "resources",
    "600900": "defensive",
    "600276": "defensive",
    "603259": "defensive",
    "002422": "defensive",
}

TARGET_WEIGHTS = {
    "300308": 0.09,
    "688041": 0.07,
    "002371": 0.07,
    "688981": 0.06,
    "300750": 0.06,
    "000425": 0.05,
    "601088": 0.10,
    "600219": 0.05,
    "600019": 0.05,
    "518880": 0.12,
    "000792": 0.08,
    "600900": 0.05,
    "600276": 0.07,
    "603259": 0.05,
    "002422": 0.03,
}

DEFAULT_SECTOR_WEIGHTS = {
    "high_end_manufacturing": 0.40,
    "cyclical": 0.20,
    "resources": 0.20,
    "defensive": 0.20,
}

SECTOR_ROTATION = {
    "recovery": {
        "high_end_manufacturing": 0.50,
        "cyclical": 0.15,
        "resources": 0.20,
        "defensive": 0.15,
    },
    "prosperity": {
        "high_end_manufacturing": 0.40,
        "cyclical": 0.20,
        "resources": 0.20,
        "defensive": 0.20,
    },
    "stagflation": {
        "high_end_manufacturing": 0.30,
        "cyclical": 0.20,
        "resources": 0.30,
        "defensive": 0.20,
    },
    "recession": {
        "high_end_manufacturing": 0.25,
        "cyclical": 0.15,
        "resources": 0.25,
        "defensive": 0.35,
    },
}

# ── v2.0 多指数对冲参数 ──
# 指数期货年化波动率近似值
INDEX_VOL = {"IF": 0.20, "IC": 0.24, "IM": 0.28, "IH": 0.19}

# 简化版指数日收益率 (baostock可下载)
INDEX_CODES = {
    "IF": "sh.000300",
    "IC": "sh.000905",
    "IM": "sh.000852",
    "IH": "sh.000016",
}

# v2.0 尾部对冲触发阈值
TAIL_VOL_TRIGGER = 0.28  # 年化波动率>28%
TAIL_DD_TRIGGER = 0.12  # 60日最大回撤>12%
TAIL_MIN_HEDGE = 0.25  # 触发后最小对冲

# 对冲成本参数
HEDGE_ROLL_COST_ANNUAL = 0.025
HEDGE_MARGIN_OPP_COST = 0.020
HEDGE_TOTAL_COST_ANNUAL = HEDGE_ROLL_COST_ANNUAL + HEDGE_MARGIN_OPP_COST

# v2.0 统一对冲暴露上限
HEDGE_EXPOSURE_CAP = 0.40

# 交易成本
COMMISSION_RATE = 0.0003
STAMP_TAX_RATE = 0.0010
SLIPPAGE = 0.0010

# 回测参数
INITIAL_CAPITAL = 1_000_000
RISK_FREE_RATE = 0.03
REBALANCE_INTERVAL_DAYS = 15

# v2.0 换手率预算
TURNOVER_BUDGET = 0.20  # 年化换手率预算 20%
TURNOVER_WINDOW = 20  # 近20日换手率窗口

START_DATE = "2021-01-01"
END_DATE = "2026-06-29"


# ============================================================
# 数据类
# ============================================================


@dataclass
class BacktestResult:
    name: str
    equity_curve: list[float]
    dates: list[pd.Timestamp]
    daily_returns: list[float]
    trade_count: int
    hedge_costs: list[float]
    transaction_costs: list[float]
    total_return: float = 0.0
    annual_return: float = 0.0
    annual_volatility: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    calmar_ratio: float = 0.0
    win_rate: float = 0.0
    total_hedge_cost: float = 0.0
    total_transaction_cost: float = 0.0
    yearly_stats: list[dict] = field(default_factory=list)
    # v2.0 新增
    hedge_pnl_total: float = 0.0  # 对冲总盈亏
    hedge_days: int = 0  # 对冲激活天数
    hedge_effective_ratio: float = 0.0  # 对冲有效性(正收益天数/总天数)
    n_days: int = 0  # 用于辅助计算
    # v2.2 成本拆分
    roll_cost: float = 0.0  # 展期成本
    margin_cost: float = 0.0  # 保证金机会成本
    slippage_cost: float = 0.0  # 滑点成本
    # v2.1 换手率
    turnover_budget: float = 0.0  # 年化换手率预算
    turnover_window: int = 0  # 换手率窗口
    annual_turnover: float = 0.0  # 实际年化换手率
    window_turnover: float = 0.0  # 窗口内换手率
    turnover_daily: list[float] = field(default_factory=list)  # 每日换手率


@dataclass
class MultiStrategyResult:
    strategies: list[BacktestResult]


# ============================================================
# 个股Beta估算 (用于多指数对冲分配)
# ============================================================

STOCK_BETAS = {
    "300308": (1.35, 1.50, 1.60, 1.20),
    "688041": (1.40, 1.55, 1.70, 1.25),
    "002371": (1.25, 1.40, 1.50, 1.15),
    "688981": (1.30, 1.45, 1.55, 1.20),
    "300750": (1.20, 1.35, 1.45, 1.10),
    "000425": (1.05, 1.15, 1.25, 0.95),
    "601088": (0.85, 0.80, 0.75, 0.90),
    "600219": (0.90, 0.95, 1.05, 0.85),
    "600019": (0.95, 1.00, 1.10, 0.90),
    "518880": (0.40, 0.35, 0.30, 0.45),
    "000792": (0.80, 0.90, 1.00, 0.75),
    "600276": (0.75, 0.70, 0.65, 0.80),
    "603259": (0.90, 0.95, 1.00, 0.85),
    "002422": (0.70, 0.65, 0.60, 0.75),
}
# idx: CSI300=0, CSI500=1, CSI1000=2, SSE50=3


# ============================================================
# 数据加载 (baostock)
# ============================================================


class BacktestDataLoader:
    def __init__(self, cache_dir: str | None = None) -> None:
        if cache_dir is None:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.cache_dir = os.path.join(base, "..", "data", "cache")
        else:
            self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)
        self._price_data: dict[str, pd.DataFrame] = {}
        self._csi300: pd.DataFrame | None = None
        self._index_data: dict[str, pd.Series] = {}  # v2.0 多指数数据

    def load_or_download(
        self, codes: list[str], start: str, end: str, retry: bool = False
    ) -> dict[str, pd.DataFrame]:
        result = {}
        missing = []
        for code in codes:
            cpath = os.path.join(self.cache_dir, f"kline_{code}_daily.parquet")
            if os.path.exists(cpath) and not retry:
                df = pd.read_parquet(cpath)
                df.index = pd.to_datetime(df.index).normalize()
                if len(df) > 100 and df.index.min() <= pd.Timestamp(start):
                    result[code] = df
                    continue
            missing.append(code)

        if missing:
            self._bs_download(missing, start, end)
            for code in missing:
                cpath = os.path.join(self.cache_dir, f"kline_{code}_daily.parquet")
                if os.path.exists(cpath):
                    df = pd.read_parquet(cpath)
                    df.index = pd.to_datetime(df.index).normalize()
                    if len(df) > 50:
                        result[code] = df

        self._price_data = result
        return result

    def load_csi300(self, start: str, end: str) -> pd.DataFrame:
        """加载沪深300指数 (保留向后兼容, 优先复用multi_index结果)"""
        if self._csi300 is not None and len(self._csi300) > 50:
            return self._csi300
        if "IF" in self._index_data:
            csi_series = self._index_data["IF"]
            self._csi300 = pd.DataFrame({"close": csi_series})
            return self._csi300
        df = self._load_index_data("sh.000300", "000300", start, end)
        if df is not None:
            self._csi300 = df
        return df or pd.DataFrame()

    def load_multi_index(self, start: str, end: str) -> dict[str, pd.Series]:
        """v2.0: 加载多指数数据 (CSI300/500/1000)"""
        indices = {
            "IF": ("sh.000300", "000300"),
            "IC": ("sh.000905", "000905"),
            "IM": ("sh.000852", "000852"),
        }
        for tag, (bs_code, cache_name) in indices.items():
            df = self._load_index_data(bs_code, cache_name, start, end)
            if df is not None and not df.empty:
                self._index_data[tag] = df["close"]

        # 设置CSI300用于向后兼容 (优先从已加载的IF)
        if "IF" in self._index_data:
            # 重建为DataFrame格式兼容原有代码
            csi_series = self._index_data["IF"]
            self._csi300 = pd.DataFrame({"close": csi_series})
            self._csi300.index = pd.to_datetime(self._csi300.index).normalize()

        return self._index_data

    def _load_index_data(
        self, bs_code: str, cache_name: str, start: str, end: str
    ) -> pd.DataFrame | None:
        cpath = os.path.join(self.cache_dir, f"kline_{cache_name}_daily.parquet")
        if os.path.exists(cpath):
            df = pd.read_parquet(cpath)
            df.index = pd.to_datetime(df.index).normalize()
            if len(df) > 50:
                return df

        logger.info(f"  下载指数 {bs_code} ...")
        import baostock as bs

        bs.login()
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,close",
            start_date=start,
            end_date=end,
            frequency="d",
            adjustflag="3",
        )
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        bs.logout()

        if rows:
            df = pd.DataFrame(rows, columns=["date", "close"])
            df["date"] = pd.to_datetime(df["date"])
            df["close"] = df["close"].astype(float)
            df = df.set_index("date").sort_index()
            df.to_parquet(cpath)
            logger.info(f"  OK {cache_name}: {len(df)} 条")
            return df
        return None

    def _bs_download(self, codes: list[str], start: str, end: str) -> None:
        import baostock as bs

        bs.login()
        n = len(codes)
        for i, code in enumerate(codes):
            name = CODE_NAMES.get(code, code)
            mkt = "sh" if code.startswith(("6", "9")) else "sz"
            bs_code = f"{mkt}.{code}"
            try:
                rs = bs.query_history_k_data_plus(
                    bs_code,
                    "date,close",
                    start_date=start,
                    end_date=end,
                    frequency="d",
                    adjustflag="2",
                )
                rows = []
                while rs.error_code == "0" and rs.next():
                    rows.append(rs.get_row_data())
                if rows:
                    df = pd.DataFrame(rows, columns=["date", "close"])
                    df["date"] = pd.to_datetime(df["date"])
                    df["close"] = df["close"].astype(float)
                    df = df.set_index("date").sort_index()
                    cpath = os.path.join(self.cache_dir, f"kline_{code}_daily.parquet")
                    df.to_parquet(cpath)
                    logger.info(f"  [{i + 1}/{n}] OK {name} ({code}): {len(df)} 条")
                else:
                    logger.info(f"  [{i + 1}/{n}] -- {name} ({code}): 无数据")
            except (
                ValueError,
                KeyError,
                TypeError,
                AttributeError,
                OSError,
                RuntimeError,
            ) as e:
                logger.info(f"  [{i + 1}/{n}] XX {name} ({code}): {e}")
        bs.logout()

    def build_unified_dataframe(
        self, start: str, end: str
    ) -> tuple[pd.DataFrame, pd.Series, dict[str, pd.Series]]:
        """构建统一价格矩阵"""
        all_dates = set()
        for df in self._price_data.values():
            all_dates.update(df.index)
        if self._csi300 is not None:
            all_dates.update(self._csi300.index)
        for s in self._index_data.values():
            all_dates.update(s.index)

        dates = sorted(
            [d for d in all_dates if pd.Timestamp(start) <= d <= pd.Timestamp(end)]
        )

        price_df = pd.DataFrame(index=dates)
        for code in PORTFOLIO_CODES:
            if code in self._price_data:
                df = self._price_data[code]
                price_df[code] = df["close"].reindex(dates).ffill()

        # CSI300 for backward compat
        if self._csi300 is not None:
            csi = self._csi300["close"].reindex(dates).ffill()
            csi300_ret = csi.pct_change().fillna(0)
        else:
            csi300_ret = pd.Series(0, index=dates)

        # v2.0: Multi-index returns
        index_rets = {}
        for tag, idx_series in self._index_data.items():
            aligned = idx_series.reindex(dates).ffill()
            index_rets[tag] = aligned.pct_change().fillna(0)

        return price_df, csi300_ret, index_rets


# ============================================================
# 市场状态判断
# ============================================================


def determine_regime_from_csi300(csi300_ret: pd.Series, idx: int) -> str:
    """原有CSI300判断 (v1.0保留)"""
    if idx < 60:
        return "recovery"
    vol = csi300_ret.iloc[max(0, idx - 30) : idx + 1].std() * np.sqrt(252)
    cum_ret = (1 + csi300_ret.iloc[max(0, idx - 60) : idx + 1]).prod() - 1
    if cum_ret > 0.15 and vol < 0.20:
        return "prosperity"
    if cum_ret > 0.05:
        return "recovery"
    if cum_ret < -0.15 and vol > 0.25:
        return "recession"
    if cum_ret < -0.05 or vol > 0.25:
        return "stagflation"
    return "recovery"


def compute_portfolio_vol_30d(
    daily_rets: list[float], idx: int, min_len: int = 10
) -> float:
    """v2.0: 计算组合自身30日年化波动率"""
    if idx < min_len:
        return 0.18
    window = daily_rets[max(0, idx - 29) : idx + 1]
    if len(window) < min_len:
        return 0.18
    return float(np.std(window) * np.sqrt(252))


def compute_portfolio_dd_60d(equity: list[float], idx: int) -> float:
    """v2.0: 计算组合自身60日最大回撤"""
    if idx < 20:
        return 0.0
    start = max(0, idx - 59)
    window = np.array(equity[start : idx + 1])
    if len(window) < 5:
        return 0.0
    peak = np.maximum.accumulate(window)
    dd = (window - peak) / peak
    return float(abs(np.min(dd)))


def get_tail_hedge_ratio(vol_30d: float, dd_60d: float) -> float:
    """v2.0: 组合自触发尾部对冲比率

    仅在波动率>28%或回撤>12%时激活
    """
    ratio = 0.0

    if vol_30d > TAIL_VOL_TRIGGER:
        excess_vol = vol_30d - TAIL_VOL_TRIGGER
        ratio = TAIL_MIN_HEDGE + excess_vol * 2.0
        ratio = min(ratio, HEDGE_EXPOSURE_CAP)

    if dd_60d > TAIL_DD_TRIGGER:
        excess_dd = dd_60d - TAIL_DD_TRIGGER
        dd_ratio = TAIL_MIN_HEDGE + excess_dd * 3.0
        dd_ratio = min(dd_ratio, HEDGE_EXPOSURE_CAP)
        ratio = max(ratio, dd_ratio)

    return ratio


def compute_vix_from_csi300(csi300_ret: pd.Series, idx: int, window: int = 30) -> float:
    """v8.7: 从 CSI300 收益率计算 VIX 替代值 (已实现波动率 * 100)

    A股无官方VIX, 用 CSI300 近30日年化已实现波动率 * 100 作为替代.
    """
    if idx < window:
        return 18.0
    window_rets = csi300_ret.iloc[max(0, idx - window + 1) : idx + 1]
    if len(window_rets) < 10:
        return 18.0
    rv = float(np.std(window_rets) * np.sqrt(252) * 100)
    if 5.0 < rv < 100.0:
        return rv
    return 18.0


def compute_vix_trend(csi300_ret: pd.Series, idx: int, lookback: int = 5) -> float:
    """v8.8: 计算 VIX 5日变化率 — VIX 趋势触发

    返回 (vix_today - vix_5d_ago) / vix_5d_ago.
    正值表示 VIX 上升(恐慌加剧), 负值表示 VIX 下降(恐慌缓解).
    """
    if idx < lookback + 30:
        return 0.0
    vix_now = compute_vix_from_csi300(csi300_ret, idx)
    vix_past = compute_vix_from_csi300(csi300_ret, idx - lookback)
    if vix_past <= 0:
        return 0.0
    return (vix_now - vix_past) / vix_past


def get_tail_hedge_ratio_v87(vol_30d: float, dd_60d: float, vix: float) -> float:
    """v8.7: RegimeFolio 动态阈值尾部对冲比率

    触发阈值随 VIX regime 自适应 (低波提高门槛, 危机大幅降低).
    向后兼容: v8.7 动态阈值, 与 hedge_engine._get_regime_triggers 一致.
    """
    try:
        from utils.hedge_engine import _get_regime_triggers

        triggers = _get_regime_triggers(vix)
        vol_trigger = triggers["vol_trigger"]
        dd_trigger = triggers["dd_trigger"]
        min_hedge = triggers["min_hedge_ratio"]
        max_hedge = triggers["max_hedge_ratio"]
    except (ImportError, KeyError, TypeError):
        vol_trigger = TAIL_VOL_TRIGGER
        dd_trigger = TAIL_DD_TRIGGER
        min_hedge = TAIL_MIN_HEDGE
        max_hedge = HEDGE_EXPOSURE_CAP

    ratio = 0.0

    if vol_30d > vol_trigger:
        excess_vol = vol_30d - vol_trigger
        ratio = min_hedge + excess_vol * 2.0
        ratio = min(ratio, max_hedge)

    if dd_60d > dd_trigger:
        excess_dd = dd_60d - dd_trigger
        dd_ratio = min_hedge + excess_dd * 3.0
        dd_ratio = min(dd_ratio, max_hedge)
        ratio = max(ratio, dd_ratio)

    if 0 < ratio < 0.10:
        return 0.0

    return ratio


def compute_multi_index_beta_weights() -> tuple[dict[str, float], float]:
    """v2.0: 计算组合对各指数的加权Beta

    Returns:
        ({'IF': beta_csi300, 'IC': beta_csi500, 'IM': beta_csi1000}, total_beta)
    """
    betas = {"IF": 0.0, "IC": 0.0, "IM": 0.0}
    total_w = 0.0

    for code, tw in TARGET_WEIGHTS.items():
        if code in STOCK_BETAS:
            b300, b500, b1000, _ = STOCK_BETAS[code]
            betas["IF"] += tw * b300
            betas["IC"] += tw * b500
            betas["IM"] += tw * b1000
            total_w += tw

    if total_w > 0:
        for k in betas:
            betas[k] /= total_w

    total_beta = betas["IF"] + betas["IC"] + betas["IM"]
    return betas, total_beta


def get_dynamic_hedge_ratio_v1(vol_30d: float, regime: str) -> float:
    """v1.0 动态对冲(CSI300驱动) — 保留用于S4对比"""
    if vol_30d < 0.12:
        base = 0.0
    elif vol_30d < 0.18:
        base = 0.25
    elif vol_30d < 0.28:
        base = 0.50
    else:
        base = 0.75
    if regime == "recession":
        base = min(HEDGE_EXPOSURE_CAP, base + 0.15)
    elif regime == "stagflation":
        base = min(HEDGE_EXPOSURE_CAP, base + 0.10)
    return min(base, HEDGE_EXPOSURE_CAP)


def get_dynamic_rebalance_threshold(vol_30d: float) -> float:
    if vol_30d < 0.15:
        return 0.03
    if vol_30d < 0.25:
        return 0.05
    return 0.08


# ============================================================
# 回测引擎 v2.0
# ============================================================


class HedgeRebalanceBacktest:
    def __init__(
        self,
        price_df: pd.DataFrame,
        csi300_ret: pd.Series,
        index_rets: dict[str, pd.Series] | None = None,
    ) -> None:
        self.price_df = price_df
        self.csi300_ret = csi300_ret
        self.index_rets = index_rets or {}
        self.dates = list(price_df.index)
        self.n_days = len(self.dates)
        # v2.0: 多指数Beta权重 (预计算)
        self.multi_betas, self.total_beta = compute_multi_index_beta_weights()

    @staticmethod
    def _compute_turnover(
        prev_pos: dict[str, int], pos: dict[str, int], px: dict[str, float]
    ) -> float:
        if prev_pos is None:
            return 0.0
        turnover = 0.0
        all_codes = set(prev_pos) | set(pos)
        for code in all_codes:
            prev_shares = prev_pos.get(code, 0)
            cur_shares = pos.get(code, 0)
            p = px.get(code, 0)
            if p <= 0:
                continue
            traded_value = abs(cur_shares - prev_shares) * p
            turnover += traded_value
        return turnover

    def run_all(self) -> MultiStrategyResult:
        logger.info("  [1/6] 策略1: 静态基准 ...")
        s1 = self._run_s1()
        self._metrics(s1)

        logger.info("  [2/6] 策略2: 仅动态再平衡 ...")
        s2 = self._run_s2()
        self._metrics(s2)

        logger.info("  [3/6] 策略3: 仅固定对冲(CSI300) ...")
        s3 = self._run_s3()
        self._metrics(s3)

        logger.info("  [4/6] 策略4: 多指数Beta加权联动 ...")
        s4 = self._run_s4_v2()
        self._metrics(s4)

        logger.info("  [5/6] 策略5: S2+组合自触发尾部对冲(v5.9固定阈值) ...")
        s5 = self._run_s5()
        self._metrics(s5)

        logger.info("  [6/6] 策略6: S2+v8.7 RegimeFolio动态阈值尾部对冲 ...")
        s6 = self._run_s6_v87()
        self._metrics(s6)

        return MultiStrategyResult(strategies=[s1, s2, s3, s4, s5, s6])

    # ---- 策略1: 静态基准 ----
    def _run_s1(self) -> BacktestResult:
        eq = [INITIAL_CAPITAL]
        trades = 0
        hc, tc = [0.0], [0.0]
        last_rb = 0
        pos = {}
        cash = INITIAL_CAPITAL

        for i in range(1, self.n_days):
            sv = sum(
                pos.get(c, 0) * self._px(c, i)
                for c in PORTFOLIO_CODES
                if self._px(c, i) > 0
            )
            total = cash + sv
            eq.append(total)
            hc.append(0.0)
            tc.append(0.0)

            if total <= 0 or (i - last_rb) < REBALANCE_INTERVAL_DAYS:
                continue
            last_rb = i

            px = {c: self._px(c, i) for c in PORTFOLIO_CODES}
            px = {k: v for k, v in px.items() if v > 0}
            if not px:
                continue

            # order-independent: 先统一计算，再先卖后买
            adjustments = []
            for code, tw in TARGET_WEIGHTS.items():
                if code not in px:
                    continue
                p = px[code]
                target_mv = total * tw
                cur_shares = pos.get(code, 0)
                cur_mv = cur_shares * p
                diff = target_mv - cur_mv
                if abs(diff) / total < 0.02:
                    continue
                shares = int(abs(diff) / p / 100) * 100
                if shares <= 0:
                    continue
                adjustments.append((code, shares, diff, p))

            # 第一遍：卖出
            for code, shares, diff, p in adjustments:
                if diff >= 0:
                    continue
                if pos.get(code, 0) >= shares:
                    sell_amt = (
                        shares * p * (1 - COMMISSION_RATE - STAMP_TAX_RATE - SLIPPAGE)
                    )
                    cash += sell_amt
                    pos[code] -= shares
                    if pos[code] <= 0:
                        del pos[code]
                    trades += 1
                    tc[-1] += shares * p * (COMMISSION_RATE + STAMP_TAX_RATE + SLIPPAGE)

            # 第二遍：买入
            for code, shares, diff, p in adjustments:
                if diff <= 0:
                    continue
                cost = shares * p * (1 + COMMISSION_RATE + SLIPPAGE)
                if cost <= cash:
                    cash -= cost
                    pos[code] = pos.get(code, 0) + shares
                    trades += 1
                    tc[-1] += shares * p * (COMMISSION_RATE + SLIPPAGE)

        return BacktestResult(
            name="S1:静态基准",
            equity_curve=eq,
            dates=self.dates,
            daily_returns=self._rets(eq),
            trade_count=trades,
            hedge_costs=hc,
            transaction_costs=tc,
            n_days=self.n_days,
        )

    # ---- 策略2: 仅动态再平衡 ----
    def _run_s2(self) -> BacktestResult:
        eq = [INITIAL_CAPITAL]
        trades = 0
        hc, tc = [0.0], [0.0]
        last_rb = 0
        pos = {}
        cash = INITIAL_CAPITAL
        dr = [0.0]
        turnover_daily = [0.0]

        for i in range(1, self.n_days):
            sv = sum(
                pos.get(c, 0) * self._px(c, i)
                for c in PORTFOLIO_CODES
                if self._px(c, i) > 0
            )
            total = cash + sv
            eq.append(total)
            hc.append(0.0)
            tc.append(0.0)
            dr.append((total - eq[i - 1]) / max(1, eq[i - 1]) if eq[i - 1] > 0 else 0)
            turnover_daily.append(0.0)

            if total <= 0 or (i - last_rb) < REBALANCE_INTERVAL_DAYS:
                continue

            vol30 = compute_portfolio_vol_30d(dr, i - 1)
            threshold = get_dynamic_rebalance_threshold(vol30)

            # v2.1: turnover-aware 约束
            if len(turnover_daily) > TURNOVER_WINDOW:
                window_turnover = sum(
                    turnover_daily[-(TURNOVER_WINDOW + 1) : -1]
                ) / max(1, eq[-2])
                annualized = window_turnover * (252.0 / REBALANCE_INTERVAL_DAYS)
                if annualized > TURNOVER_BUDGET:
                    threshold = min(threshold + 0.02, 0.12)

            last_rb = i

            px = {c: self._px(c, i) for c in PORTFOLIO_CODES}
            px = {k: v for k, v in px.items() if v > 0}
            if not px:
                continue

            prev_pos = dict(pos)
            # order-independent: 先统一计算，再先卖后买
            adjustments = []
            for code, tw in TARGET_WEIGHTS.items():
                if code not in px:
                    continue
                p = px[code]
                target_mv = total * tw
                cur_shares = pos.get(code, 0)
                cur_mv = cur_shares * p
                diff = target_mv - cur_mv
                if abs(diff) / total < threshold:
                    continue
                shares = int(abs(diff) / p / 100) * 100
                if shares <= 0:
                    continue
                adjustments.append((code, shares, diff, p))

            # 第一遍：卖出
            for code, shares, diff, p in adjustments:
                if diff >= 0:
                    continue
                if pos.get(code, 0) >= shares:
                    sell_amt = (
                        shares * p * (1 - COMMISSION_RATE - STAMP_TAX_RATE - SLIPPAGE)
                    )
                    cash += sell_amt
                    pos[code] -= shares
                    if pos[code] <= 0:
                        del pos[code]
                    trades += 1
                    tc[-1] += shares * p * (COMMISSION_RATE + STAMP_TAX_RATE + SLIPPAGE)

            # 第二遍：买入
            for code, shares, diff, p in adjustments:
                if diff <= 0:
                    continue
                cost = shares * p * (1 + COMMISSION_RATE + SLIPPAGE)
                if cost <= cash:
                    cash -= cost
                    pos[code] = pos.get(code, 0) + shares
                    trades += 1
                    tc[-1] += shares * p * (COMMISSION_RATE + SLIPPAGE)

            turnover_daily[-1] = self._compute_turnover(prev_pos, pos, px) / max(
                1, total
            )

        return BacktestResult(
            name="S2:仅再平衡",
            equity_curve=eq,
            dates=self.dates,
            daily_returns=self._rets(eq),
            trade_count=trades,
            hedge_costs=hc,
            transaction_costs=tc,
            n_days=self.n_days,
            turnover_budget=TURNOVER_BUDGET,
            turnover_window=TURNOVER_WINDOW,
            turnover_daily=turnover_daily,
        )

    # ---- 策略3: 仅固定对冲(CSI300) ----
    def _run_s3(self) -> BacktestResult:
        eq = [INITIAL_CAPITAL]
        trades = 0
        hc, tc = [0.0], [0.0]
        last_rb = 0
        pos = {}
        cash = INITIAL_CAPITAL
        FIXED_RATIO = 0.50
        margin_locked = 0.0

        for i in range(1, self.n_days):
            sv = sum(
                pos.get(c, 0) * self._px(c, i)
                for c in PORTFOLIO_CODES
                if self._px(c, i) > 0
            )
            csi_ret = self.csi300_ret.iloc[i] if i < len(self.csi300_ret) else 0
            hedge_exposure = sv * FIXED_RATIO
            hedge_pnl = -hedge_exposure * csi_ret
            daily_cost = hedge_exposure * (HEDGE_TOTAL_COST_ANNUAL / 252)
            target_margin = hedge_exposure * 0.15
            margin_delta = target_margin - margin_locked
            margin_locked = target_margin

            if margin_delta > 0:
                cash -= min(cash, margin_delta)
            else:
                cash += abs(margin_delta)

            total = max(1, cash + sv + hedge_pnl - daily_cost)
            eq.append(total)
            hc.append(daily_cost)
            tc.append(0.0)

            if total <= 0 or (i - last_rb) < REBALANCE_INTERVAL_DAYS:
                continue
            last_rb = i

            px = {c: self._px(c, i) for c in PORTFOLIO_CODES}
            px = {k: v for k, v in px.items() if v > 0}
            if not px:
                continue

            for code, tw in TARGET_WEIGHTS.items():
                if code not in px:
                    continue
                p = px[code]
                target_mv = total * tw
                cur_shares = pos.get(code, 0)
                cur_mv = cur_shares * p
                diff = target_mv - cur_mv
                if abs(diff) / total < 0.02:
                    continue
                shares = int(abs(diff) / p / 100) * 100
                if shares <= 0:
                    continue
                if diff > 0:
                    cost = shares * p * (1 + COMMISSION_RATE + SLIPPAGE)
                    avail = cash - margin_locked
                    if cost <= max(0, avail):
                        cash -= cost
                        pos[code] = pos.get(code, 0) + shares
                        trades += 1
                        tc[-1] += shares * p * (COMMISSION_RATE + SLIPPAGE)
                else:
                    if pos.get(code, 0) >= shares:
                        sell_amt = (
                            shares
                            * p
                            * (1 - COMMISSION_RATE - STAMP_TAX_RATE - SLIPPAGE)
                        )
                        cash += sell_amt
                        pos[code] -= shares
                        if pos[code] <= 0:
                            del pos[code]
                        trades += 1
                        tc[-1] += (
                            shares * p * (COMMISSION_RATE + STAMP_TAX_RATE + SLIPPAGE)
                        )

        cash += margin_locked
        adj_eq = [v + margin_locked for v in eq]

        return BacktestResult(
            name="S3:仅对冲50%",
            equity_curve=adj_eq,
            dates=self.dates,
            daily_returns=self._rets(adj_eq),
            trade_count=trades,
            hedge_costs=hc,
            transaction_costs=tc,
            n_days=self.n_days,
        )

    # ---- 策略4 v2.0: 多指数Beta加权联动 ----
    def _run_s4_v2(self) -> BacktestResult:
        """v2.0: 多指数Beta加权对冲 + 动态再平衡 + 板块轮动

        与v1.0核心区别:
        - 对冲工具: IF/IC/IM按Beta比例分配, 不再纯IF
        - 市场状态: 仍用CSI300(对比用)
        - 对冲比率: 基于CSI300判断(保留用于对比)
        """
        eq = [INITIAL_CAPITAL]
        trades = 0
        hc, tc = [0.0], [0.0]
        roll_costs, margin_costs, slip_costs = [0.0], [0.0], [0.0]
        last_rb = 0
        pos = {}
        cash = INITIAL_CAPITAL
        dr = [0.0]
        margin_locked = 0.0
        current_ratio = 0.0
        prev_ratio = 0.0
        turnover_daily = [0.0]

        # 多指数分配权重
        beta_if = self.multi_betas.get("IF", 0)
        beta_ic = self.multi_betas.get("IC", 0)
        beta_im = self.multi_betas.get("IM", 0)
        total_beta = max(beta_if + beta_ic + beta_im, 0.01)

        for i in range(1, self.n_days):
            sv = sum(
                pos.get(c, 0) * self._px(c, i)
                for c in PORTFOLIO_CODES
                if self._px(c, i) > 0
            )
            regime = determine_regime_from_csi300(self.csi300_ret, i - 1)
            vol30 = compute_portfolio_vol_30d(dr, i - 1)

            target_ratio = get_dynamic_hedge_ratio_v1(
                vol30, regime
            )  # 仍用CSI300判断比率

            # ── v2.0: 多指数对冲盈亏 ──
            hedge_pnl = 0.0
            if current_ratio > 0:
                hedge_exposure = sv * current_ratio
                # IF
                if beta_if > 0 and "IF" in self.index_rets:
                    if_ret = (
                        self.index_rets["IF"].iloc[i]
                        if i < len(self.index_rets["IF"])
                        else 0
                    )
                    if_weight = beta_if / total_beta
                    hedge_pnl -= hedge_exposure * if_weight * if_ret
                # IC
                if beta_ic > 0 and "IC" in self.index_rets:
                    ic_ret = (
                        self.index_rets["IC"].iloc[i]
                        if i < len(self.index_rets["IC"])
                        else 0
                    )
                    ic_weight = beta_ic / total_beta
                    hedge_pnl -= hedge_exposure * ic_weight * ic_ret
                # IM (or fallback)
                if beta_im > 0 and "IM" in self.index_rets:
                    im_ret = (
                        self.index_rets["IM"].iloc[i]
                        if i < len(self.index_rets["IM"])
                        else 0
                    )
                    im_weight = beta_im / total_beta
                    hedge_pnl -= hedge_exposure * im_weight * im_ret
                elif beta_im > 0:
                    # Fallback: use IC
                    ic_ret = (
                        self.index_rets.get("IC", pd.Series(0, index=self.dates)).iloc[
                            i
                        ]
                        if "IC" in self.index_rets
                        else 0
                    )
                    im_weight = beta_im / total_beta
                    hedge_pnl -= hedge_exposure * im_weight * ic_ret
            else:
                hedge_exposure = 0.0

            # v2.2: 成本拆分
            roll_daily = hedge_exposure * (HEDGE_ROLL_COST_ANNUAL / 252)
            margin_daily = hedge_exposure * (HEDGE_MARGIN_OPP_COST / 252)
            # v2.3: 滑点仅在比率变化时产生
            ratio_delta = abs(current_ratio - prev_ratio)
            slip_daily = sv * ratio_delta * SLIPPAGE if ratio_delta > 1e-9 else 0.0
            daily_cost = roll_daily + margin_daily + slip_daily
            target_margin = hedge_exposure * 0.15
            margin_delta = target_margin - margin_locked
            margin_locked = target_margin

            if margin_delta > 0:
                if cash >= margin_delta:
                    cash -= margin_delta
                else:
                    current_ratio = max(0, current_ratio - 0.05)
                    prev_ratio = current_ratio
            else:
                cash += abs(margin_delta)

            total = max(1, cash + sv + hedge_pnl - daily_cost)
            eq.append(total)
            hc.append(daily_cost)
            roll_costs.append(roll_daily)
            margin_costs.append(margin_daily)
            slip_costs.append(slip_daily)
            tc.append(0.0)
            dr.append((total - eq[i - 1]) / max(1, eq[i - 1]) if eq[i - 1] > 0 else 0)
            turnover_daily.append(0.0)

            if total <= 0 or (i - last_rb) < REBALANCE_INTERVAL_DAYS:
                current_ratio = target_ratio
                prev_ratio = current_ratio
                continue

            last_rb = i
            current_ratio = target_ratio
            prev_ratio = current_ratio
            rb_threshold = get_dynamic_rebalance_threshold(vol30)

            # v2.1: turnover-aware 约束
            if len(turnover_daily) > TURNOVER_WINDOW:
                window_turnover = sum(
                    turnover_daily[-(TURNOVER_WINDOW + 1) : -1]
                ) / max(1, eq[-2])
                annualized = window_turnover * (252.0 / REBALANCE_INTERVAL_DAYS)
                if annualized > TURNOVER_BUDGET:
                    rb_threshold = min(rb_threshold + 0.02, 0.12)

            sector_w = SECTOR_ROTATION.get(regime, SECTOR_ROTATION["recovery"])

            px = {c: self._px(c, i) for c in PORTFOLIO_CODES}
            px = {k: v for k, v in px.items() if v > 0}
            if not px:
                continue

            prev_pos = dict(pos)
            # order-independent: 先统一计算，再先卖后买
            adjustments = []
            for code, base_tw in TARGET_WEIGHTS.items():
                if code not in px:
                    continue
                cat = CODE_CATEGORIES.get(code, "high_end_manufacturing")
                cat_w = sector_w.get(cat, 0.25)
                def_cat_w = DEFAULT_SECTOR_WEIGHTS.get(cat, 0.25)
                adj_tw = base_tw * (cat_w / def_cat_w) if def_cat_w > 0 else base_tw

                p = px[code]
                target_mv = total * adj_tw
                cur_shares = pos.get(code, 0)
                cur_mv = cur_shares * p
                diff = target_mv - cur_mv
                if abs(diff) / total < rb_threshold:
                    continue
                shares = int(abs(diff) / p / 100) * 100
                if shares <= 0:
                    continue
                adjustments.append((code, shares, diff, p))

            # 第一遍：卖出
            for code, shares, diff, p in adjustments:
                if diff >= 0:
                    continue
                if pos.get(code, 0) >= shares:
                    sell_amt = (
                        shares * p * (1 - COMMISSION_RATE - STAMP_TAX_RATE - SLIPPAGE)
                    )
                    cash += sell_amt
                    pos[code] -= shares
                    if pos[code] <= 0:
                        del pos[code]
                    trades += 1
                    tc[-1] += shares * p * (COMMISSION_RATE + STAMP_TAX_RATE + SLIPPAGE)

            # 第二遍：买入（注意 margin_locked 约束）
            for code, shares, diff, p in adjustments:
                if diff <= 0:
                    continue
                cost = shares * p * (1 + COMMISSION_RATE + SLIPPAGE)
                avail = cash - margin_locked
                if cost <= max(0, avail):
                    cash -= cost
                    pos[code] = pos.get(code, 0) + shares
                    trades += 1
                    tc[-1] += shares * p * (COMMISSION_RATE + SLIPPAGE)

            turnover_daily[-1] = self._compute_turnover(prev_pos, pos, px) / max(
                1, total
            )

        cash += margin_locked
        adj_eq = [v + margin_locked for v in eq]

        return BacktestResult(
            name="S4:多指数联动",
            equity_curve=adj_eq,
            dates=self.dates,
            daily_returns=self._rets(adj_eq),
            trade_count=trades,
            hedge_costs=hc,
            transaction_costs=tc,
            n_days=self.n_days,
            turnover_budget=TURNOVER_BUDGET,
            turnover_window=TURNOVER_WINDOW,
            turnover_daily=turnover_daily,
            roll_cost=sum(roll_costs),
            margin_cost=sum(margin_costs),
            slippage_cost=sum(slip_costs),
        )

    # ---- 策略5 v2.0: S2+组合自触发尾部对冲 ----
    def _run_s5(self) -> BacktestResult:
        """v2.0 新增: S2动态再平衡 + 组合自触发尾部对冲

        核心理念:
        - 日常: 完全等同于S2 (动态再平衡, 零对冲成本)
        - 极端: 仅当组合自身30日波动率>28% 或 60日回撤>12%时触发25-40%对冲
        - 对冲工具: IC优先(匹配组合的高CSI500 Beta), IF辅助
        """
        eq = [INITIAL_CAPITAL]
        trades = 0
        hc, tc = [0.0], [0.0]
        roll_costs, margin_costs, slip_costs = [0.0], [0.0], [0.0]
        last_rb = 0
        pos = {}
        cash = INITIAL_CAPITAL
        dr = [0.0]
        margin_locked = 0.0
        current_ratio = 0.0
        prev_ratio = 0.0
        hedge_pnl_total = 0.0
        hedge_days = 0
        turnover_daily = [0.0]

        # 多指数分配 (IC优先, 组合Beta_CSI500最高)
        beta_if = self.multi_betas.get("IF", 0)
        beta_ic = self.multi_betas.get("IC", 0)
        beta_im = self.multi_betas.get("IM", 0)
        total_beta = max(beta_if + beta_ic + beta_im, 0.01)

        for i in range(1, self.n_days):
            sv = sum(
                pos.get(c, 0) * self._px(c, i)
                for c in PORTFOLIO_CODES
                if self._px(c, i) > 0
            )

            # ── v2.0 组合自触发 ──
            dd_60d = compute_portfolio_dd_60d(eq, i - 1)  # 用前一日权益
            vol30 = compute_portfolio_vol_30d(dr, i - 1)
            tail_ratio = get_tail_hedge_ratio(vol30, dd_60d)
            target_ratio = tail_ratio  # 尾部对冲, 无CSI300掺杂

            # ── 多指数对冲盈亏 ──
            hedge_pnl = 0.0
            if current_ratio > 0:
                hedge_exposure = sv * current_ratio
                hedge_days += 1

                if "IC" in self.index_rets and beta_ic > 0:
                    ic_ret = (
                        self.index_rets["IC"].iloc[i]
                        if i < len(self.index_rets["IC"])
                        else 0
                    )
                    ic_weight = beta_ic / total_beta
                    hedge_pnl -= hedge_exposure * ic_weight * ic_ret

                if "IF" in self.index_rets and beta_if > 0:
                    if_ret = (
                        self.index_rets["IF"].iloc[i]
                        if i < len(self.index_rets["IF"])
                        else 0
                    )
                    if_weight = beta_if / total_beta
                    hedge_pnl -= hedge_exposure * if_weight * if_ret

                if "IM" in self.index_rets and beta_im > 0:
                    im_ret = (
                        self.index_rets["IM"].iloc[i]
                        if i < len(self.index_rets["IM"])
                        else 0
                    )
                    im_weight = beta_im / total_beta
                    hedge_pnl -= hedge_exposure * im_weight * im_ret

                hedge_pnl_total += hedge_pnl
            else:
                hedge_exposure = 0.0

            # v2.2: 成本拆分
            roll_daily = hedge_exposure * (HEDGE_ROLL_COST_ANNUAL / 252)
            margin_daily = hedge_exposure * (HEDGE_MARGIN_OPP_COST / 252)
            # v2.3: 滑点仅在比率变化时产生
            ratio_delta = abs(current_ratio - prev_ratio)
            slip_daily = sv * ratio_delta * SLIPPAGE if ratio_delta > 1e-9 else 0.0
            daily_cost = roll_daily + margin_daily + slip_daily

            # 保证金管理
            target_margin = hedge_exposure * 0.15
            margin_delta = target_margin - margin_locked
            margin_locked = target_margin
            if margin_delta > 0:
                if cash >= margin_delta:
                    cash -= margin_delta
                else:
                    current_ratio = 0  # 不够钱就不对冲
                    prev_ratio = current_ratio
            else:
                cash += abs(margin_delta)

            total = max(1, cash + sv + hedge_pnl - daily_cost)
            eq.append(total)
            hc.append(daily_cost)
            roll_costs.append(roll_daily)
            margin_costs.append(margin_daily)
            slip_costs.append(slip_daily)
            tc.append(0.0)
            dr.append((total - eq[i - 1]) / max(1, eq[i - 1]) if eq[i - 1] > 0 else 0)
            turnover_daily.append(0.0)

            # 再平衡 (S2逻辑)
            if total <= 0 or (i - last_rb) < REBALANCE_INTERVAL_DAYS:
                current_ratio = target_ratio
                prev_ratio = current_ratio
                continue

            last_rb = i
            current_ratio = target_ratio
            prev_ratio = current_ratio
            rb_threshold = get_dynamic_rebalance_threshold(vol30)

            # v2.1: turnover-aware 约束
            if len(turnover_daily) > TURNOVER_WINDOW:
                window_turnover = sum(
                    turnover_daily[-(TURNOVER_WINDOW + 1) : -1]
                ) / max(1, eq[-2])
                annualized = window_turnover * (252.0 / REBALANCE_INTERVAL_DAYS)
                if annualized > TURNOVER_BUDGET:
                    rb_threshold = min(rb_threshold + 0.02, 0.12)

            px = {c: self._px(c, i) for c in PORTFOLIO_CODES}
            px = {k: v for k, v in px.items() if v > 0}
            if not px:
                continue

            prev_pos = dict(pos)
            # order-independent: 先统一计算，再先卖后买
            adjustments = []
            for code, tw in TARGET_WEIGHTS.items():
                if code not in px:
                    continue
                p = px[code]
                target_mv = total * tw
                cur_shares = pos.get(code, 0)
                cur_mv = cur_shares * p
                diff = target_mv - cur_mv
                if abs(diff) / total < rb_threshold:
                    continue
                shares = int(abs(diff) / p / 100) * 100
                if shares <= 0:
                    continue
                adjustments.append((code, shares, diff, p))

            # 第一遍：卖出
            for code, shares, diff, p in adjustments:
                if diff >= 0:
                    continue
                if pos.get(code, 0) >= shares:
                    sell_amt = (
                        shares * p * (1 - COMMISSION_RATE - STAMP_TAX_RATE - SLIPPAGE)
                    )
                    cash += sell_amt
                    pos[code] -= shares
                    if pos[code] <= 0:
                        del pos[code]
                    trades += 1
                    tc[-1] += shares * p * (COMMISSION_RATE + STAMP_TAX_RATE + SLIPPAGE)

            # 第二遍：买入（注意 margin_locked 约束）
            for code, shares, diff, p in adjustments:
                if diff <= 0:
                    continue
                cost = shares * p * (1 + COMMISSION_RATE + SLIPPAGE)
                avail = cash - margin_locked
                if cost <= max(0, avail):
                    cash -= cost
                    pos[code] = pos.get(code, 0) + shares
                    trades += 1
                    tc[-1] += shares * p * (COMMISSION_RATE + SLIPPAGE)

            turnover_daily[-1] = self._compute_turnover(prev_pos, pos, px) / max(
                1, total
            )

        cash += margin_locked
        adj_eq = [v + margin_locked for v in eq]

        result = BacktestResult(
            name="S5:再平衡+尾保",
            equity_curve=adj_eq,
            dates=self.dates,
            daily_returns=self._rets(adj_eq),
            trade_count=trades,
            hedge_costs=hc,
            transaction_costs=tc,
            n_days=self.n_days,
            turnover_budget=TURNOVER_BUDGET,
            turnover_window=TURNOVER_WINDOW,
            turnover_daily=turnover_daily,
            roll_cost=sum(roll_costs),
            margin_cost=sum(margin_costs),
            slippage_cost=sum(slip_costs),
        )
        result.hedge_pnl_total = hedge_pnl_total
        result.hedge_days = hedge_days
        return result

    def _run_s6_v87(self) -> BacktestResult:
        """v8.7 新增: S2动态再平衡 + RegimeFolio动态阈值尾部对冲

        与S5的唯一差异: 尾部对冲阈值随VIX regime自适应
        - S5 (v5.9): 固定阈值 vol>28%/DD>12%
        - S6 (v8.7): RegimeFolio动态阈值 (低波0.35/正常0.28/高波0.25/危机0.15)
        - VIX: CSI300近30日已实现波动率*100
        - v8.8: 对冲持续性(hysteresis) — 触发后5天内渐减衰减, 避免频繁开关
        """
        HYSTERESIS_DAYS = 5

        eq = [INITIAL_CAPITAL]
        trades = 0
        hc, tc = [0.0], [0.0]
        roll_costs, margin_costs, slip_costs = [0.0], [0.0], [0.0]
        last_rb = 0
        pos = {}
        cash = INITIAL_CAPITAL
        dr = [0.0]
        margin_locked = 0.0
        current_ratio = 0.0
        prev_ratio = 0.0
        hedge_pnl_total = 0.0
        hedge_days = 0
        turnover_daily = [0.0]

        # v8.8 hysteresis 状态
        hedge_cooldown = 0
        last_active_ratio = 0.0

        # v8.8 P3: 对冲成本预算
        cumulative_hedge_cost = 0.0
        HEDGE_COST_BUDGET_ANNUAL = 0.02  # 年化对冲成本预算 2%

        beta_if = self.multi_betas.get("IF", 0)
        beta_ic = self.multi_betas.get("IC", 0)
        beta_im = self.multi_betas.get("IM", 0)
        total_beta = max(beta_if + beta_ic + beta_im, 0.01)

        for i in range(1, self.n_days):
            sv = sum(
                pos.get(c, 0) * self._px(c, i)
                for c in PORTFOLIO_CODES
                if self._px(c, i) > 0
            )

            # ── v8.7 RegimeFolio 动态阈值 + v8.8 hysteresis + VIX趋势 ──
            dd_60d = compute_portfolio_dd_60d(eq, i - 1)
            vol30 = compute_portfolio_vol_30d(dr, i - 1)
            vix = compute_vix_from_csi300(self.csi300_ret, i - 1)

            # v8.8: VIX趋势过滤 — 高VIX但稳定时回退到normal阈值
            vix_trend = compute_vix_trend(self.csi300_ret, i - 1)
            if vix > 20.0 and vix_trend < 0.10:
                effective_vix = 18.0
            else:
                effective_vix = vix

            # v8.8 P3: 对冲成本预算 — 超预算时提高门槛
            n_years_elapsed = max(i / 252, 0.01)
            annual_hedge_cost_rate = (
                cumulative_hedge_cost / max(eq[-1], 1) / n_years_elapsed
            )
            if annual_hedge_cost_rate > HEDGE_COST_BUDGET_ANNUAL:
                effective_vix = max(effective_vix, 25.0)  # 至少high_vol阈值

            tail_ratio = get_tail_hedge_ratio_v87(vol30, dd_60d, effective_vix)

            if tail_ratio > 0:
                target_ratio = tail_ratio
                hedge_cooldown = HYSTERESIS_DAYS
                last_active_ratio = tail_ratio
            elif hedge_cooldown > 0:
                # v8.8: hysteresis 期间渐减衰减
                target_ratio = last_active_ratio * (hedge_cooldown / HYSTERESIS_DAYS)
                hedge_cooldown -= 1
            else:
                target_ratio = 0.0
                last_active_ratio = 0.0

            # ── 多指数对冲盈亏 ──
            hedge_pnl = 0.0
            if current_ratio > 0:
                hedge_exposure = sv * current_ratio
                hedge_days += 1

                if "IC" in self.index_rets and beta_ic > 0:
                    ic_ret = (
                        self.index_rets["IC"].iloc[i]
                        if i < len(self.index_rets["IC"])
                        else 0
                    )
                    ic_weight = beta_ic / total_beta
                    hedge_pnl -= hedge_exposure * ic_weight * ic_ret

                if "IF" in self.index_rets and beta_if > 0:
                    if_ret = (
                        self.index_rets["IF"].iloc[i]
                        if i < len(self.index_rets["IF"])
                        else 0
                    )
                    if_weight = beta_if / total_beta
                    hedge_pnl -= hedge_exposure * if_weight * if_ret

                if "IM" in self.index_rets and beta_im > 0:
                    im_ret = (
                        self.index_rets["IM"].iloc[i]
                        if i < len(self.index_rets["IM"])
                        else 0
                    )
                    im_weight = beta_im / total_beta
                    hedge_pnl -= hedge_exposure * im_weight * im_ret

                hedge_pnl_total += hedge_pnl
            else:
                hedge_exposure = 0.0

            roll_daily = hedge_exposure * (HEDGE_ROLL_COST_ANNUAL / 252)
            margin_daily = hedge_exposure * (HEDGE_MARGIN_OPP_COST / 252)
            ratio_delta = abs(current_ratio - prev_ratio)
            slip_daily = sv * ratio_delta * SLIPPAGE if ratio_delta > 1e-9 else 0.0
            daily_cost = roll_daily + margin_daily + slip_daily
            cumulative_hedge_cost += daily_cost  # v8.8 P3: 累加对冲成本

            target_margin = hedge_exposure * 0.15
            margin_delta = target_margin - margin_locked
            margin_locked = target_margin
            if margin_delta > 0:
                if cash >= margin_delta:
                    cash -= margin_delta
                else:
                    current_ratio = 0
                    prev_ratio = current_ratio
            else:
                cash += abs(margin_delta)

            total = max(1, cash + sv + hedge_pnl - daily_cost)
            eq.append(total)
            hc.append(daily_cost)
            roll_costs.append(roll_daily)
            margin_costs.append(margin_daily)
            slip_costs.append(slip_daily)
            tc.append(0.0)
            dr.append((total - eq[i - 1]) / max(1, eq[i - 1]) if eq[i - 1] > 0 else 0)
            turnover_daily.append(0.0)

            if total <= 0 or (i - last_rb) < REBALANCE_INTERVAL_DAYS:
                current_ratio = target_ratio
                prev_ratio = current_ratio
                continue

            last_rb = i
            current_ratio = target_ratio
            prev_ratio = current_ratio
            rb_threshold = get_dynamic_rebalance_threshold(vol30)

            if len(turnover_daily) > TURNOVER_WINDOW:
                window_turnover = sum(
                    turnover_daily[-(TURNOVER_WINDOW + 1) : -1]
                ) / max(1, eq[-2])
                annualized = window_turnover * (252.0 / REBALANCE_INTERVAL_DAYS)
                if annualized > TURNOVER_BUDGET:
                    rb_threshold = min(rb_threshold + 0.02, 0.12)

            px = {c: self._px(c, i) for c in PORTFOLIO_CODES}
            px = {k: v for k, v in px.items() if v > 0}
            if not px:
                continue

            prev_pos = dict(pos)
            adjustments = []

            for code, tw in TARGET_WEIGHTS.items():
                if code not in px:
                    continue
                p = px[code]
                target_mv = total * tw
                cur_shares = pos.get(code, 0)
                cur_mv = cur_shares * p
                diff = target_mv - cur_mv
                if abs(diff) / total < rb_threshold:
                    continue
                shares = int(abs(diff) / p / 100) * 100
                if shares <= 0:
                    continue
                adjustments.append((code, shares, diff, p))

            for code, shares, diff, p in adjustments:
                if diff >= 0:
                    continue
                if pos.get(code, 0) >= shares:
                    sell_amt = (
                        shares * p * (1 - COMMISSION_RATE - STAMP_TAX_RATE - SLIPPAGE)
                    )
                    cash += sell_amt
                    pos[code] -= shares
                    if pos[code] <= 0:
                        del pos[code]
                    trades += 1
                    tc[-1] += shares * p * (COMMISSION_RATE + STAMP_TAX_RATE + SLIPPAGE)

            for code, shares, diff, p in adjustments:
                if diff <= 0:
                    continue
                cost = shares * p * (1 + COMMISSION_RATE + SLIPPAGE)
                avail = cash - margin_locked
                if cost <= max(0, avail):
                    cash -= cost
                    pos[code] = pos.get(code, 0) + shares
                    trades += 1
                    tc[-1] += shares * p * (COMMISSION_RATE + SLIPPAGE)

            turnover_daily[-1] = self._compute_turnover(prev_pos, pos, px) / max(
                1, total
            )

        cash += margin_locked
        adj_eq = [v + margin_locked for v in eq]

        result = BacktestResult(
            name="S6:v8.7动态阈值",
            equity_curve=adj_eq,
            dates=self.dates,
            daily_returns=self._rets(adj_eq),
            trade_count=trades,
            hedge_costs=hc,
            transaction_costs=tc,
            n_days=self.n_days,
            turnover_budget=TURNOVER_BUDGET,
            turnover_window=TURNOVER_WINDOW,
            turnover_daily=turnover_daily,
            roll_cost=sum(roll_costs),
            margin_cost=sum(margin_costs),
            slippage_cost=sum(slip_costs),
        )
        result.hedge_pnl_total = hedge_pnl_total
        result.hedge_days = hedge_days
        return result

    # ---- 辅助 ----
    def _px(self, code: str, idx: int) -> float:
        if code in self.price_df.columns:
            v = self.price_df.iloc[idx][code]
            return float(v) if pd.notna(v) and v > 0 else 0.0
        return 0.0

    def _rets(self, eq: list[float]) -> list[float]:
        rets = [0.0]
        for i in range(1, len(eq)):
            rets.append((eq[i] - eq[i - 1]) / max(1, eq[i - 1]) if eq[i - 1] > 0 else 0)
        return rets

    def _metrics(self, r: BacktestResult) -> None:
        eq = np.array(r.equity_curve)
        rets = np.array(r.daily_returns)

        r.total_return = (eq[-1] - eq[0]) / eq[0]
        n_years = self.n_days / 252
        r.annual_return = (1 + r.total_return) ** (1 / max(n_years, 0.5)) - 1
        r.annual_volatility = np.std(rets) * np.sqrt(252)
        r.sharpe_ratio = (r.annual_return - RISK_FREE_RATE) / max(
            r.annual_volatility, 0.001
        )

        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / peak
        r.max_drawdown = abs(np.min(dd))
        r.calmar_ratio = r.annual_return / max(r.max_drawdown, 0.001)
        r.win_rate = np.sum(rets > 0) / max(len(rets), 1)
        r.total_hedge_cost = float(
            getattr(r, "roll_cost", 0.0)
            + getattr(r, "margin_cost", 0.0)
            + getattr(r, "slippage_cost", 0.0)
        )
        r.total_transaction_cost = sum(r.transaction_costs)

        # v2.1: 换手率统计
        if getattr(r, "turnover_daily", None):
            td = np.array(r.turnover_daily)
            r.annual_turnover = float(np.sum(td) * (252.0 / max(len(td), 1)))
            w = getattr(r, "turnover_window", TURNOVER_WINDOW)
            if len(td) > w:
                r.window_turnover = float(np.sum(td[-w:]) / max(1, len(td[-w:])))
            else:
                r.window_turnover = float(np.mean(td)) if len(td) > 0 else 0.0

        # 逐年统计
        r.yearly_stats = []
        year_groups = defaultdict(list)
        for idx, d in enumerate(r.dates):
            year_groups[d.year].append(idx)
        for year in sorted(year_groups.keys()):
            idxs = year_groups[year]
            if len(idxs) < 10:
                continue
            yr = (eq[idxs[-1]] - eq[idxs[0]]) / eq[idxs[0]]
            yv = np.std([r.daily_returns[j] for j in idxs]) * np.sqrt(252)

            yeq = eq[idxs[0] : idxs[-1] + 1]
            ypeak = np.maximum.accumulate(yeq)
            ydd = abs(np.min((yeq - ypeak) / ypeak))

            csi_idxs = [j for j in idxs if j < len(self.csi300_ret)]
            csi_yr = (1 + self.csi300_ret.iloc[csi_idxs]).prod() - 1 if csi_idxs else 0
            mt = "牛市" if csi_yr > 0.15 else ("熊市" if csi_yr < -0.05 else "震荡市")

            r.yearly_stats.append(
                {
                    "year": year,
                    "return": yr,
                    "volatility": yv,
                    "max_drawdown": ydd,
                    "csi300_return": csi_yr,
                    "market_type": mt,
                }
            )


# ============================================================
# 报告生成 v2.0
# ============================================================


def format_comparison_report(multi: MultiStrategyResult) -> str:
    lines = []
    lines.append("=" * 90)
    lines.append("  对冲+再平衡联动 — 五策略回测对比报告 v2.0")
    lines.append("=" * 90)
    lines.append(f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"  回测区间: {START_DATE} ~ {END_DATE}")
    lines.append(f"  初始资金: {INITIAL_CAPITAL:,}元 | 标的: {len(PORTFOLIO_CODES)}只")
    lines.append(
        f"  无风险利率: {RISK_FREE_RATE * 100:.0f}% | 对冲成本: {HEDGE_TOTAL_COST_ANNUAL * 100:.1f}%/年"
    )
    lines.append("")
    lines.append("  v2.0 优化要点:")
    lines.append("    - S4: 多指数Beta加权对冲(IC/IM/IF按Beta比例分配)")
    lines.append("    - S5: S2动态再平衡 + 组合自触发尾部对冲(vol>28%或DD>12%)")
    lines.append("  v2.1 优化要点:")
    lines.append("    - 再平衡改为 order-independent (先卖后买)")
    lines.append("    - 统一 HEDGE_EXPOSURE_CAP 约束")
    lines.append("    - 新增 turnover-aware 再平衡阈值自适应")
    lines.append("  v2.2 优化要点:")
    lines.append("    - 对冲成本拆分为 roll/margin/slippage 三行，便于归因")
    lines.append("")

    ss = multi.strategies
    len(ss)

    # 核心指标对比
    lines.append("  [1] 核心绩效指标")
    lines.append("  " + "-" * 86)

    col_width = 16
    header = f"  {'指标':<18s}"
    for s in ss:
        header += f" {s.name:>{col_width}s}"
    lines.append(header)
    lines.append("  " + "-" * 86)

    metric_defs = [
        ("总收益率", lambda r: f"{r.total_return * 100:+.2f}%"),
        ("年化收益率", lambda r: f"{r.annual_return * 100:+.2f}%"),
        ("年化波动率", lambda r: f"{r.annual_volatility * 100:.2f}%"),
        ("夏普比率", lambda r: f"{r.sharpe_ratio:.2f}"),
        ("最大回撤", lambda r: f"{r.max_drawdown * 100:.2f}%"),
        ("Calmar比率", lambda r: f"{r.calmar_ratio:.2f}"),
        ("日胜率", lambda r: f"{r.win_rate * 100:.1f}%"),
        ("交易次数", lambda r: f"{r.trade_count}"),
        ("年化换手率", lambda r: f"{r.annual_turnover * 100:.1f}%"),
        ("窗口换手率", lambda r: f"{r.window_turnover * 100:.1f}%"),
        ("展期成本", lambda r: f"${getattr(r, 'roll_cost', 0.0):,.0f}"),
        ("保证金成本", lambda r: f"${getattr(r, 'margin_cost', 0.0):,.0f}"),
        ("滑点成本", lambda r: f"${getattr(r, 'slippage_cost', 0.0):,.0f}"),
        ("对冲成本", lambda r: f"${r.total_hedge_cost:,.0f}"),
        ("交易成本", lambda r: f"${r.total_transaction_cost:,.0f}"),
        ("最终市值", lambda r: f"${r.equity_curve[-1]:,.0f}"),
    ]
    for name, fn in metric_defs:
        vals = [fn(r) for r in ss]
        row = f"  {name:<18s}"
        for v in vals:
            row += f" {v:>{col_width}s}"
        lines.append(row)

    lines.append("  " + "-" * 86)

    # v2.0 新增: 对冲有效性指标
    lines.append("")
    lines.append("  [1b] 对冲有效性指标 (v2.0新增)")
    lines.append("  " + "-" * 60)
    lines.append(
        f"  {'策略':<18s} {'对冲盈亏':>12s} {'对冲天数':>10s} {'对冲成本':>12s} {'净效果':>12s}"
    )
    lines.append("  " + "-" * 60)
    for s in ss:
        hpnl = getattr(s, "hedge_pnl_total", 0)
        hdays = getattr(s, "hedge_days", 0)
        net_effect = hpnl - s.total_hedge_cost
        lines.append(
            f"  {s.name:<18s} {hpnl:>+12,.0f} {hdays:>10d} {s.total_hedge_cost:>12,.0f} {net_effect:>+12,.0f}"
        )
    lines.append("  " + "-" * 60)

    # 逐年拆分
    lines.append("")
    lines.append("  [2] 逐年绩效")
    lines.append("  " + "-" * 86)
    all_years = sorted(set(y["year"] for st in ss for y in st.yearly_stats))
    for year in all_years:
        lines.append(f"\n  -- {year}年 --")
        lines.append(
            f"  {'策略':<18s} {'年景':>6s} {'收益':>10s} {'波动':>10s} {'回撤':>10s} {'CSI300':>10s}"
        )
        lines.append("  " + "-" * 72)
        for st in ss:
            yd = [y for y in st.yearly_stats if y["year"] == year]
            if yd:
                y = yd[0]
                lines.append(
                    f"  {st.name:<18s} {y['market_type']:>6s} "
                    f"{y['return'] * 100:>+9.2f}% {y['volatility'] * 100:>9.2f}% "
                    f"{y['max_drawdown'] * 100:>9.2f}% {y['csi300_return'] * 100:>+9.2f}%"
                )

    # 市场年景汇总
    lines.append("")
    lines.append("  [3] 各市场年景汇总")
    lines.append("  " + "-" * 60)
    for mtype in ["牛市", "震荡市", "熊市"]:
        lines.append(f"\n  {mtype}年景:")
        lines.append(f"  {'策略':<18s} {'平均收益':>12s} {'平均回撤':>12s}")
        lines.append("  " + "-" * 46)
        for st in ss:
            ylist = [y for y in st.yearly_stats if y["market_type"] == mtype]
            if ylist:
                avg_ret = np.mean([y["return"] for y in ylist])
                avg_dd = np.mean([y["max_drawdown"] for y in ylist])
                lines.append(
                    f"  {st.name:<18s} {avg_ret * 100:>+11.2f}% {avg_dd * 100:>11.2f}%"
                )
            else:
                lines.append(f"  {st.name:<18s} {'N/A':>12s} {'N/A':>12s}")

    # 综合排名
    lines.append("")
    lines.append("  [4] 综合排名")
    lines.append("  " + "-" * 60)
    best_sharpe = max(ss, key=lambda r: r.sharpe_ratio)
    best_calmar = max(ss, key=lambda r: r.calmar_ratio)
    lowest_dd = min(ss, key=lambda r: r.max_drawdown)
    highest_ret = max(ss, key=lambda r: r.annual_return)
    lowest_trades = min(ss, key=lambda r: r.trade_count)
    lines.append(f"  夏普最优:    {best_sharpe.name} ({best_sharpe.sharpe_ratio:.2f})")
    lines.append(f"  Calmar最优:  {best_calmar.name} ({best_calmar.calmar_ratio:.2f})")
    lines.append(
        f"  回撤最小:    {lowest_dd.name} ({lowest_dd.max_drawdown * 100:.2f}%)"
    )
    lines.append(
        f"  收益最高:    {highest_ret.name} ({highest_ret.annual_return * 100:.2f}%)"
    )
    lines.append(f"  交易最少:    {lowest_trades.name} ({lowest_trades.trade_count}次)")

    # S5 vs S2 对比
    lines.append("")
    lines.append("  [5] S5 vs S2 对比 (尾部对冲增益)")
    lines.append("  " + "-" * 60)
    s2 = ss[1]
    s5 = ss[4]
    lines.append(
        f"  年化: {s2.annual_return * 100:.2f}% -> {s5.annual_return * 100:.2f}% "
        f"({(s5.annual_return - s2.annual_return) * 100:+.2f}pp)"
    )
    lines.append(
        f"  回撤: {s2.max_drawdown * 100:.2f}% -> {s5.max_drawdown * 100:.2f}% "
        f"({(s5.max_drawdown - s2.max_drawdown) * 100:+.2f}pp)"
    )
    lines.append(
        f"  夏普: {s2.sharpe_ratio:.2f} -> {s5.sharpe_ratio:.2f} ({s5.sharpe_ratio - s2.sharpe_ratio:+.2f})"
    )
    lines.append(
        f"  对冲成本: ${s2.total_hedge_cost:,.0f} -> ${s5.total_hedge_cost:,.0f}"
    )

    # 结论
    lines.append("")
    lines.append("  [6] 结论与建议")
    lines.append("  " + "-" * 60)
    lines.append("  v2.0 核心发现:")
    lines.append("    1. S5(再平衡+尾保) 对比v1.0 S4, 对冲触发从被动改为组合自驱动")
    lines.append("    2. 多指数对冲改善了对冲工具与组合的匹配度")
    lines.append("    3. 尾部保护仅在极端行情激活, 大幅降低日常对冲成本侵蚀")
    lines.append("  推荐方案: S5 (动态再平衡 + 组合自触发尾部对冲)")

    lines.append("")
    lines.append("  以上回测结果基于历史数据模拟，不构成投资建议。")
    lines.append("=" * 90)

    return "\n".join(lines)


def save_report(report: str, output_dir: str | None = None) -> str:
    if output_dir is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        output_dir = os.path.join(base, "..", "reports")
    os.makedirs(output_dir, exist_ok=True)
    fpath = os.path.join(
        output_dir,
        f"backtest_hedge_rebalance_v2_{datetime.now().strftime('%Y-%m-%d')}.md",
    )
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(report)
    logger.info(f"  报告已保存: {fpath}")
    return fpath


def run_backtest(
    start: str = START_DATE,
    end: str = END_DATE,
    output_dir: str | None = None,
    force_dl: bool = False,
) -> tuple[MultiStrategyResult, str]:
    import time

    t0 = time.time()

    logger.info("=" * 60)
    logger.info("  对冲+再平衡联动 — 六策略对比回测 v2.0+v8.7")
    logger.info("=" * 60)

    logger.info("\n[1/3] 加载数据 (baostock)...")
    loader = BacktestDataLoader()
    loader.load_or_download(PORTFOLIO_CODES, start, end, retry=force_dl)
    loader.load_multi_index(start, end)
    logger.info(f"  已加载 {len(loader._price_data)}/{len(PORTFOLIO_CODES)} 只股票")
    logger.info(f"  已加载 {len(loader._index_data)} 个指数")

    # 计算多指数Beta
    betas, total_b = compute_multi_index_beta_weights()
    logger.info(
        f"  组合多指数Beta: IF={betas['IF']:.2f}, IC={betas['IC']:.2f}, IM={betas['IM']:.2f}"
    )

    logger.info("\n[2/3] 构建价格矩阵...")
    price_df, csi300_ret, index_rets = loader.build_unified_dataframe(start, end)
    logger.info(
        f"  交易日: {len(price_df)} | 日期: {price_df.index[0].date()} ~ {price_df.index[-1].date()}"
    )

    logger.info("\n[3/3] 六策略回测...")
    bt = HedgeRebalanceBacktest(price_df, csi300_ret, index_rets)
    multi = bt.run_all()

    report = format_comparison_report(multi)
    fpath = save_report(report, output_dir)
    logger.info(f"\n  耗时: {time.time() - t0:.1f}秒")

    return multi, fpath


if __name__ == "__main__":
    run_backtest()
