"""真实归因输入构建器 (v8.7 零侵入修复, 2026-09-02).

问题背景
--------
``15_每日工作流/run_daily_eod_workflow.py`` 的 ``run_phase4_55_attribution()``
此前把**硬编码合成数据**喂给归因引擎:

    benchmark_returns = [portfolio_ret * 0.8]
    factor_returns    = {k: [portfolio_ret * 固定系数]}
    sector_returns    = {k: [portfolio_ret * 固定系数]}
    trading_costs     = 0.0

后果: 归因报告每天稳定产出、字段齐全、无告警, 但所有维度都是组合收益的算术变形,
不反映真实的 Beta / 行业 / 因子贡献 —— 让"模型赚钱还是行情赚钱"这个问题得到
**确定但错误**的答案。这比"没有归因"更危险, 因为它不会触发任何人的怀疑。

本模块职责
--------
从真实数据源构建归因输入, 数据不可得时 **fail-closed**(标记降级, 绝不回落合成值)。

设计原则 (沿用项目既有铁律)
-------------------------
* **零侵入**: 只产出输入字典, 不改 ``PnLAttributionEngine`` 内脏、不触碰交易链路。
* **边界桥接**: 行情获取通过可注入 ``price_provider``, 便于测试与替换数据源。
* **只报能算的**: 基本面类因子(估值/成长/盈利质量)无免费可靠数据源 → 明确降级,
  不捏造; 流动性因子需成交量, 当前 provider 仅取收盘价 → 同样降级。
* **fail-closed**: 基准(决定 Alpha/Beta 的锚)不可得 → ``benchmark_available=False``,
  调用方应**跳过报告生成**, 而不是产出一份失真的报告。
* **无前视**: 因子分组信号一律使用归因日**之前**的收盘序列。
* **观测路径 fail-open**: 单维度取数失败记降级原因并继续, 由调用方决定是否产出。

已知保留局限 (不属本次修复范围, 已在 ``degraded_reasons`` 中显式标记)
------------------------------------------------------------------
* 风格因子**暴露** (``style_exposures``) 仍由 EOD 按行业查表得到, 是行业代理值而非
  个股真实因子暴露。真实暴露需要个股因子库接入, 属独立改造项。
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger("attribution.real_inputs")

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# ------------------------------------------------------------------
# 常量
# ------------------------------------------------------------------

#: 主基准 (沪深300ETF), 与 utils/attribution/factor_attribution.py 保持一致
DEFAULT_BENCHMARK_CODE = "510300.SH"

#: 可由收盘价序列直接计算的风格因子
PRICE_DERIVED_FACTORS: tuple[str, ...] = ("momentum", "volatility")

#: 需基本面/成交量数据, 当前不可得 -> 明确降级, 不捏造
UNAVAILABLE_FACTORS: tuple[str, ...] = (
    "liquidity",
    "valuation",
    "growth",
    "earnings_quality",
)

FACTOR_WINDOW = 20  # 因子信号回看窗口 (交易日)
FACTOR_QUANTILE = 0.3  # 多头/空头分组比例
MIN_SYMBOLS_FOR_FACTOR = 6  # 截面因子所需最少有效标的数
MIN_SECTOR_COVERAGE = 0.5  # 行业收益的最低行情覆盖率

#: 允许的数据滞后天数 (0=当日收盘已更新; 1=次日凌晨补数场景)
MAX_DATA_LAG_DAYS = 1

# 交易成本费率 —— 与 utils/tca_engine.py TCAModel 默认口径一致
COMMISSION_RATE = 0.0003  # 佣金 万三
MIN_COMMISSION = 5.0  # 单笔最低佣金 (元)
FEE_RATE = 0.000067  # 过户费等
STAMP_DUTY_RATE = 0.0005  # 印花税 (仅卖出)

#: EOD 场景默认回看天数 (需覆盖因子窗口 + 缓冲)
DEFAULT_LOOKBACK_DAYS = 60


# ------------------------------------------------------------------
# 数据契约
# ------------------------------------------------------------------


@dataclass
class RealAttributionInputs:
    """真实归因输入 (可直接解包传给 ``PnLAttributionEngine.attribute``)."""

    benchmark_returns: list[float] = field(default_factory=list)
    market_returns: list[float] = field(default_factory=list)
    factor_returns: dict[str, list[float]] = field(default_factory=dict)
    sector_returns: dict[str, list[float]] = field(default_factory=dict)
    trading_costs: float = 0.0
    funding_cost: float = 0.0
    #: 降级原因列表 (空 = 全部维度均为真实数据)
    degraded_reasons: list[str] = field(default_factory=list)
    #: 各维度数据来源标记, 便于追溯
    data_sources: dict[str, str] = field(default_factory=dict)

    @property
    def is_degraded(self) -> bool:
        return bool(self.degraded_reasons)

    @property
    def benchmark_available(self) -> bool:
        """基准是否可得 —— 不可得时调用方应跳过归因报告生成 (fail-closed)."""
        return bool(self.benchmark_returns)

    def to_engine_kwargs(self) -> dict[str, Any]:
        """转为 ``PnLAttributionEngine.attribute`` 的关键字参数."""
        return {
            "benchmark_returns": list(self.benchmark_returns),
            "market_returns": list(self.market_returns),
            "factor_returns": dict(self.factor_returns),
            "sector_returns": dict(self.sector_returns),
            "trading_costs": float(self.trading_costs),
            "funding_cost": float(self.funding_cost),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark_returns": list(self.benchmark_returns),
            "market_returns": list(self.market_returns),
            "factor_returns": {k: list(v) for k, v in self.factor_returns.items()},
            "sector_returns": {k: list(v) for k, v in self.sector_returns.items()},
            "trading_costs": float(self.trading_costs),
            "funding_cost": float(self.funding_cost),
            "is_degraded": self.is_degraded,
            "degraded_reasons": list(self.degraded_reasons),
            "data_sources": dict(self.data_sources),
        }


# ------------------------------------------------------------------
# 代码工具
# ------------------------------------------------------------------


def _digits6(code: Any) -> str:
    """抽取 6 位数字代码."""
    return "".join(ch for ch in str(code) if ch.isdigit())[:6]


def _is_etf(code: Any) -> bool:
    """判断是否为场内基金 (ETF/LOF)。股票走另一套 akshare 接口。"""
    d = _digits6(code)
    return d.startswith(("51", "58", "56", "15", "16"))


def _is_sh(code: Any) -> bool:
    """上交所判定 (5/6/9 开头)。"""
    d = _digits6(code)
    return bool(d) and d[0] in ("5", "6", "9")


# ------------------------------------------------------------------
# 行情数据源 (可注入, 默认走 Wind MCP -> akshare 链)
# ------------------------------------------------------------------


def _fetch_panel_wind(codes: Sequence[str], days: int) -> pd.DataFrame | None:
    """Wind MCP 前复权日线 -> DataFrame[date x code] (close)."""
    if str(_PROJECT_ROOT / "tools") not in sys.path:
        sys.path.insert(0, str(_PROJECT_ROOT / "tools"))
    try:
        from wind_mcp_fetcher import wind_get_kline
    except (ImportError, OSError) as e:
        logger.warning("[attrib] Wind MCP 不可用: %s", e)
        return None

    cols: dict[str, pd.Series] = {}
    for code in codes:
        try:
            raw = wind_get_kline(
                f"{_digits6(code)}.{'SH' if _is_sh(code) else 'SZ'}",
                days=days,
                is_fund=_is_etf(code),
            )
        except (ValueError, TypeError, KeyError, OSError, RuntimeError) as e:
            logger.warning("[attrib] Wind %s 取数异常: %s", code, e)
            return None
        if not raw:
            return None
        try:
            s = pd.Series(
                {
                    str(r.get("TIME", ""))[:10]: float(r["MATCH"])
                    for r in raw
                    if r.get("MATCH") is not None
                },
                dtype=float,
            )
        except (ValueError, TypeError, KeyError) as e:
            logger.warning("[attrib] Wind %s 解析异常: %s", code, e)
            return None
        if s.empty:
            return None
        cols[code] = s

    if not cols:
        return None
    df = pd.DataFrame(cols)
    df.index = pd.to_datetime(df.index)
    return df.sort_index().dropna(how="all")


def _fetch_panel_akshare(codes: Sequence[str], days: int) -> pd.DataFrame | None:
    """akshare 前复权日线 -> DataFrame[date x code] (close).

    股票走 ``stock_zh_a_hist``, ETF/LOF 走 ``fund_etf_hist_em``;
    两者均使用**前复权**口径 (未复权在份额折算 ETF 上严重失真)。
    """
    try:
        import akshare as ak
    except (ImportError, OSError) as e:
        logger.warning("[attrib] akshare 不可用: %s", e)
        return None

    cols: dict[str, pd.Series] = {}
    for code in codes:
        d6 = _digits6(code)
        try:
            if _is_etf(code):
                df = ak.fund_etf_hist_em(symbol=d6, period="daily", adjust="qfq")
            else:
                df = ak.stock_zh_a_hist(
                    symbol=d6, period="daily", adjust="qfq", start_date="20200101"
                )
        except (ValueError, TypeError, KeyError, OSError, AttributeError) as e:
            logger.warning("[attrib] akshare %s 取数异常: %s", code, e)
            return None
        if df is None or df.empty or "收盘" not in df.columns:
            logger.warning("[attrib] akshare %s 返回为空", code)
            return None
        try:
            s = df.set_index(pd.to_datetime(df["日期"]))["收盘"].astype(float)
            s = s.rename(code)
        except (ValueError, TypeError, KeyError) as e:
            logger.warning("[attrib] akshare %s 解析异常: %s", code, e)
            return None
        cols[code] = s

    if not cols:
        return None
    out = pd.DataFrame(cols).sort_index().dropna(how="all")
    return out.tail(days)


def default_price_provider(
    codes: Sequence[str], days: int = DEFAULT_LOOKBACK_DAYS
) -> tuple[pd.DataFrame | None, str]:
    """默认行情 provider: Wind MCP (前复权, 主) -> akshare (前复权, 备).

    Returns:
        (panel, source_name); panel 为 DataFrame[date x code] 收盘价,
        全部失败时返回 (None, "unavailable")。
    """
    for fn, name in (
        (_fetch_panel_wind, "wind_mcp_qfq"),
        (_fetch_panel_akshare, "akshare_qfq"),
    ):
        try:
            df = fn(list(codes), days)
        except Exception as e:  # noqa: BLE001 - 数据源 fail-safe, 换下一个源
            logger.warning("[attrib] %s 异常: %s", name, e)
            continue
        if df is not None and not df.empty and df.notna().any().any():
            logger.info("[attrib] 行情源 %s: %d 行 x %d 列", name, len(df), df.shape[1])
            return df, name
    return None, "unavailable"


# ------------------------------------------------------------------
# 收益计算 (严格无前视)
# ------------------------------------------------------------------


def _series_upto(panel: pd.DataFrame, code: str, report_date: str) -> pd.Series:
    """取截至归因日的收盘序列 (不含归因日之后的任何数据)."""
    if code not in panel.columns:
        return pd.Series(dtype=float)
    s = panel[code].dropna()
    if s.empty:
        return s
    return s[s.index <= pd.Timestamp(report_date)]


def _return_on_date(
    panel: pd.DataFrame, code: str, report_date: str
) -> tuple[float | None, int]:
    """计算标的在归因日的收益。

    Returns:
        (ret, lag_days); ret=None 表示不可得 (含数据陈旧超限)。
        lag_days 为最后一根 K 线距归因日的天数。
    """
    s = _series_upto(panel, code, report_date)
    if len(s) < 2:
        return None, -1
    prev = float(s.iloc[-2])
    cur = float(s.iloc[-1])
    lag = int((pd.Timestamp(report_date) - s.index[-1]).days)
    if lag < 0 or lag > MAX_DATA_LAG_DAYS:
        return None, lag
    if prev <= 0:
        return None, lag
    return cur / prev - 1.0, lag


# ------------------------------------------------------------------
# 各维度构建
# ------------------------------------------------------------------


def _build_sector_returns(
    panel: pd.DataFrame,
    positions: Sequence[dict],
    report_date: str,
) -> tuple[dict[str, list[float]], list[str], dict[str, str]]:
    """按行业聚合真实持仓收益 -> {行业: [加权收益]}."""
    buckets: dict[str, list[tuple[float, float]]] = {}
    covered = 0.0
    total_w = 0.0
    for pos in positions:
        code = str(pos.get("code", "") or "")
        if not code:
            continue
        sector = str(pos.get("sector") or pos.get("style") or "other")
        weight = float(pos.get("weight", 0.0) or 0.0)
        total_w += weight
        ret, _lag = _return_on_date(panel, code, report_date)
        if ret is None:
            continue
        covered += weight
        buckets.setdefault(sector, []).append((weight, ret))

    reasons: list[str] = []
    if total_w > 0 and covered / total_w < MIN_SECTOR_COVERAGE:
        reasons.append(
            f"sector_price_coverage_low: {covered / total_w:.0%} < "
            f"{MIN_SECTOR_COVERAGE:.0%}"
        )

    out: dict[str, list[float]] = {}
    for sector, items in buckets.items():
        tw = sum(w for w, _ in items)
        if tw <= 0:
            continue
        out[sector] = [sum(w * r for w, r in items) / tw]

    meta = {
        "sector_returns": "real_weighted_by_position" if out else "unavailable",
        "sector_count": str(len(out)),
    }
    return out, reasons, meta


def _build_factor_returns(
    panel: pd.DataFrame,
    codes: Sequence[str],
    report_date: str,
) -> tuple[dict[str, list[float]], list[str], dict[str, str]]:
    """构建截面风格因子收益 (仅收盘价可推导的 momentum / volatility).

    分组信号一律使用归因日**之前**的收盘序列 (无前视);
    因子收益 = 多头组当日均值收益 - 空头组当日均值收益。
    """
    reasons: list[str] = []
    day_ret: dict[str, float] = {}
    mom_sig: dict[str, float] = {}
    vol_sig: dict[str, float] = {}

    for code in codes:
        s = _series_upto(panel, code, report_date)
        if len(s) < FACTOR_WINDOW + 2:
            continue
        prev = float(s.iloc[-2])
        cur = float(s.iloc[-1])
        if prev <= 0:
            continue
        lag = int((pd.Timestamp(report_date) - s.index[-1]).days)
        if lag < 0 or lag > MAX_DATA_LAG_DAYS:
            continue
        day_ret[code] = cur / prev - 1.0

        past = s.iloc[:-1]  # 严格不含归因日 -> 无前视
        if len(past) < FACTOR_WINDOW + 1:
            continue
        base = float(past.iloc[-1 - FACTOR_WINDOW])
        if base > 0:
            mom_sig[code] = float(past.iloc[-1]) / base - 1.0
        vol = past.pct_change().iloc[-FACTOR_WINDOW:].std()
        if vol is not None and pd.notna(vol):
            vol_sig[code] = float(vol)

    out: dict[str, list[float]] = {}
    meta: dict[str, str] = {}

    if len(day_ret) >= MIN_SYMBOLS_FOR_FACTOR:
        if len(mom_sig) >= MIN_SYMBOLS_FOR_FACTOR:
            out["momentum"] = [_long_short(mom_sig, day_ret, descending=True)]
            meta["momentum"] = "real_cross_sectional_long_short"
        else:
            reasons.append("factor_momentum_insufficient_symbols")
        if len(vol_sig) >= MIN_SYMBOLS_FOR_FACTOR:
            # A股低波动异象: 低波动组 - 高波动组
            out["volatility"] = [_long_short(vol_sig, day_ret, descending=False)]
            meta["volatility"] = "real_cross_sectional_low_minus_high"
        else:
            reasons.append("factor_volatility_insufficient_symbols")
    else:
        reasons.append(
            f"factor_cross_section_insufficient: {len(day_ret)} < "
            f"{MIN_SYMBOLS_FOR_FACTOR}"
        )

    # 明确声明不可得因子, 禁止捏造
    reasons.append(
        "factors_unavailable_not_fabricated: " + ", ".join(UNAVAILABLE_FACTORS)
    )
    return out, reasons, meta


def _long_short(
    signal: dict[str, float],
    day_ret: dict[str, float],
    descending: bool,
) -> float:
    """按信号排序取前/后 QUANTILE 分组, 返回多头组减空头组的当日平均收益."""
    keys = [c for c in signal if c in day_ret]
    if len(keys) < MIN_SYMBOLS_FOR_FACTOR:
        return 0.0
    keys.sort(key=lambda c: signal[c], reverse=descending)
    n = max(1, int(len(keys) * FACTOR_QUANTILE))
    top = keys[:n]
    bottom = keys[-n:]
    long_r = sum(day_ret[c] for c in top) / len(top)
    short_r = sum(day_ret[c] for c in bottom) / len(bottom)
    return float(long_r - short_r)


def _build_trading_costs(
    report_date: str,
) -> tuple[float, list[str], dict[str, str]]:
    """从成交回报事实源 (FillsStore) 计算真实交易成本.

    无成交时返回 0.0 是**正确**的 (当天没交易就没有成本);
    取数失败则标记降级 —— 与"硬编码 0" 的区别在于可追溯。
    """
    try:
        from utils.execution.fills_store import load_day

        fills = load_day(report_date)
    except (ImportError, OSError, ValueError, TypeError, RuntimeError) as e:
        logger.warning("[attrib] FillsStore 不可用: %s", e)
        return 0.0, ["trading_cost_unavailable"], {"trading_cost": "unavailable"}

    if not fills:
        return 0.0, [], {"trading_cost": "no_fills"}

    total = 0.0
    n_used = 0
    for f in fills:
        try:
            qty = float(f.get("filled_qty", 0) or 0)
            px = float(f.get("avg_price", 0) or 0)
        except (TypeError, ValueError):
            continue
        notional = qty * px
        if notional <= 0:
            continue
        commission = max(notional * COMMISSION_RATE, MIN_COMMISSION)
        fees = notional * FEE_RATE
        if str(f.get("side", "")).upper().startswith("S"):
            fees += notional * STAMP_DUTY_RATE
        total += commission + fees
        n_used += 1

    if n_used == 0:
        return 0.0, [], {"trading_cost": "no_valid_fills"}
    return float(total), [], {"trading_cost": "fills_store", "n_fills": str(n_used)}


# ------------------------------------------------------------------
# 主入口
# ------------------------------------------------------------------

PriceProvider = Callable[[Sequence[str], int], tuple[pd.DataFrame | None, str]]


def build_real_attribution_inputs(
    report_date: str,
    positions: Sequence[dict],
    portfolio_ret: float,
    benchmark_code: str = DEFAULT_BENCHMARK_CODE,
    price_provider: PriceProvider | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> RealAttributionInputs:
    """构建真实归因输入.

    Args:
        report_date: 归因日期 (YYYY-MM-DD)
        positions: 持仓列表 [{"code", "weight", "sector", ...}]
        portfolio_ret: 组合当日收益 (来自 daily_returns.jsonl, 已是真实值)
        benchmark_code: 基准代码, 默认 510300.SH (沪深300ETF)
        price_provider: 行情 provider, 默认走 Wind MCP -> akshare 链
        lookback_days: 行情回看天数

    Returns:
        RealAttributionInputs; 调用方须检查 ``benchmark_available``,
        为 False 时应跳过报告生成 (fail-closed, 不产出失真数字)。
    """
    inputs = RealAttributionInputs()
    provider = price_provider or default_price_provider

    codes: list[str] = [str(p.get("code", "")) for p in positions if p.get("code")]
    all_codes = list(dict.fromkeys(codes + [benchmark_code]))

    if not codes:
        inputs.degraded_reasons.append("no_position_codes")
        return inputs

    # --- 1. 行情面板 ---
    try:
        panel, source = provider(all_codes, lookback_days)
    except Exception as e:  # noqa: BLE001 - 观测路径 fail-open, 交由降级处理
        logger.warning("[attrib] 行情 provider 异常: %s", e)
        panel, source = None, "provider_error"

    inputs.data_sources["price_panel"] = source
    if panel is None or panel.empty:
        inputs.degraded_reasons.append(f"price_panel_unavailable: {source}")
        return inputs

    # --- 2. 基准收益 (决定 Alpha/Beta 的锚) ---
    bench_ret, bench_lag = _return_on_date(panel, benchmark_code, report_date)
    if bench_ret is None:
        inputs.degraded_reasons.append(
            f"benchmark_return_unavailable: {benchmark_code} (lag={bench_lag})"
        )
    else:
        inputs.benchmark_returns = [bench_ret]
        inputs.market_returns = [bench_ret]
        inputs.data_sources["benchmark"] = f"{benchmark_code}@{source}"
        if bench_lag > 0:
            inputs.degraded_reasons.append(f"benchmark_data_lag_{bench_lag}d")

    # --- 3. 行业收益 (真实持仓聚合) ---
    sec_rets, sec_reasons, sec_meta = _build_sector_returns(
        panel, positions, report_date
    )
    inputs.sector_returns = sec_rets
    inputs.degraded_reasons.extend(sec_reasons)
    inputs.data_sources.update(sec_meta)

    # --- 4. 风格因子收益 (仅收盘价可推导项) ---
    fac_rets, fac_reasons, fac_meta = _build_factor_returns(panel, codes, report_date)
    inputs.factor_returns = fac_rets
    inputs.degraded_reasons.extend(fac_reasons)
    inputs.data_sources.update(fac_meta)

    # --- 5. 交易成本 (成交回报事实源) ---
    cost, cost_reasons, cost_meta = _build_trading_costs(report_date)
    inputs.trading_costs = cost
    inputs.degraded_reasons.extend(cost_reasons)
    inputs.data_sources.update(cost_meta)

    # --- 6. 已知保留局限 (显式声明, 避免被误读为真实暴露) ---
    inputs.degraded_reasons.append(
        "style_exposures_are_sector_proxies: 因子暴露仍按行业查表, 非个股真实暴露"
    )
    inputs.degraded_reasons.append(
        "funding_cost_not_modeled: 当前无融资/逆回购成本模型, 记为 0"
    )
    inputs.data_sources["portfolio_return"] = "daily_returns.jsonl"

    if inputs.is_degraded:
        logger.warning(
            "[attrib] %s 归因输入降级: %s",
            report_date,
            "; ".join(inputs.degraded_reasons),
        )
    return inputs
