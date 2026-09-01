"""
增强版因子挖掘工具 - QLib大数据样本 + 基本面因子 + 分层回测
终极量化交易系统 8.4

相比 factor_discovery.py 的增强:
1. 使用 QLib 全市场 3800+ 股票 25 年历史数据扩大样本
2. 从量价数据衍生基本面代理因子 (市值/估值/质量代理)
3. 实现分层回测验证因子单调性 (5/10分组)
4. 计算更多技术面因子 (Qlib158 + 自定义)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("factor_discovery_enhanced")


# ============================================================
# QLib 数据加载
# ============================================================

QLIB_DATA_DIR = str(Path(__file__).resolve().parent.parent / "qlib_data" / "cn_data")


def init_qlib():
    """初始化 QLib"""
    try:
        import qlib

        qlib.init(provider_uri=QLIB_DATA_DIR, region="cn")
        logger.info("QLib 初始化成功")
        return True
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        logger.warning(f"QLib 初始化失败: {e}")
        return False


def load_qlib_data(
    instruments: str = "csi500",
    start_time: str = "2020-01-01",
    end_time: str = None,
) -> pd.DataFrame:
    """从 QLib 加载全市场量价数据

    Args:
        instruments: 股票池 (all/csi300/csi500/csi800)
        start_time: 起始日期
        end_time: 结束日期

    Returns:
        MultiIndex DataFrame (instrument, datetime) x [open,high,low,close,volume,factor]
    """
    from qlib.data import D

    if end_time is None:
        end_time = datetime.now().strftime("%Y-%m-%d")

    fields = ["$open", "$high", "$low", "$close", "$volume", "$factor"]
    logger.info(f"加载 QLib 数据: {instruments}, {start_time}~{end_time}")

    df = D.features(
        D.instruments(instruments),
        fields,
        start_time=start_time,
        end_time=end_time,
    )

    df.columns = [c.replace("$", "") for c in df.columns]
    df = df.dropna(subset=["close", "volume"])
    logger.info(
        f"加载完成: {len(df)} 行, {df.index.get_level_values('instrument').nunique()} 只股票"
    )

    return df


# ============================================================
# 扩展因子计算 (50+ 因子)
# ============================================================


def _compute_momentum_factors(close: pd.Series, ret: pd.Series, n: int) -> dict:
    """计算动量类与反转类因子"""
    factors = {}
    # === 动量类 ===
    for w in [5, 10, 20, 60, 120, 252]:
        if n > w:
            factors[f"MOM_{w}D"] = close.iloc[-1] / close.iloc[-w] - 1.0

    # 12-1 月动量
    if n > 252:
        factors["MOM_12_1M"] = close.iloc[-21] / close.iloc[-252] - 1.0

    # 反转因子
    for w in [5, 20]:
        if n > w:
            factors[f"REVERSAL_{w}D"] = -(close.iloc[-1] / close.iloc[-w] - 1.0)

    # 上涨下跌日比
    for w in [20, 60]:
        if n > w:
            ret_w = ret.iloc[-w:]
            factors[f"UP_DOWN_RATIO_{w}D"] = (ret_w > 0).sum() / max(
                (ret_w < 0).sum(), 1
            )
    return factors


def _compute_volatility_factors(ret: pd.Series, n: int) -> dict:
    """计算波动率类因子 (含下行波动/偏度/峰度)"""
    factors = {}
    # === 波动率类 ===
    for w in [5, 20, 60, 120, 252]:
        if n > w:
            factors[f"VOL_{w}D"] = ret.iloc[-w:].std()

    # 下行波动
    for w in [20, 60]:
        if n > w:
            ret_w = ret.iloc[-w:]
            downside = ret_w[ret_w < 0]
            factors[f"DOWNSIDE_VOL_{w}D"] = downside.std() if len(downside) > 1 else 0.0

    # 收益偏度
    for w in [60, 120]:
        if n > w:
            factors[f"SKEW_{w}D"] = ret.iloc[-w:].skew()

    # 收益峰度
    for w in [60, 120]:
        if n > w:
            factors[f"KURT_{w}D"] = ret.iloc[-w:].kurtosis()
    return factors


def _compute_liquidity_factors(volume: pd.Series, ret: pd.Series, n: int) -> dict:
    """计算流动性类因子 (换手/Amihud/量变化/Z-Score)"""
    factors = {}
    # === 流动性类 ===
    for w in [5, 20, 60]:
        if n > w:
            factors[f"TURNOVER_{w}D"] = volume.iloc[-w:].mean()

    # Amihud 非流动性
    for w in [20, 60]:
        if n > w:
            ret_abs = ret.iloc[-w:].abs()
            vol_w = volume.iloc[-w:]
            amihud = (ret_abs / vol_w.replace(0, np.nan)).mean()
            factors[f"AMIHUD_{w}D"] = amihud if np.isfinite(amihud) else 0.0

    # 成交量变化率
    for w in [5, 20]:
        if n > w * 2:
            factors[f"VOLUME_CHG_{w}D"] = (
                volume.iloc[-w:].mean() / volume.iloc[-2 * w : -w].mean() - 1.0
            )

    # 成交量 Z-Score
    for w in [20, 60]:
        if n > w:
            vol_w = volume.iloc[-w:]
            factors[f"VOLUME_Z_{w}D"] = (volume.iloc[-1] - vol_w.mean()) / max(
                vol_w.std(), 1e-12
            )
    return factors


def _compute_technical_indicators(
    close: pd.Series, high: pd.Series, low: pd.Series, ret: pd.Series, n: int
) -> dict:
    """计算技术指标类因子 (MA偏离/MACD/RSI/布林带/ATR)"""
    factors = {}
    # === 技术指标类 ===
    # MA 均线偏离
    for w in [10, 20, 60, 120]:
        if n > w:
            ma = close.rolling(w).mean()
            factors[f"MA_DEV_{w}D"] = close.iloc[-1] / ma.iloc[-1] - 1.0

    # MACD
    if n > 26:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9, adjust=False).mean()
        factors["MACD"] = (dif.iloc[-1] - dea.iloc[-1]) / max(close.iloc[-1], 1e-12)

    # RSI
    for w in [6, 14, 28]:
        if n > w:
            delta = ret.iloc[-w:]
            gain = delta.where(delta > 0, 0).mean()
            loss = -delta.where(delta < 0, 0).mean()
            rs = gain / max(loss, 1e-12)
            factors[f"RSI_{w}D"] = 100 - (100 / (1 + rs))

    # 布林带宽度
    for w in [20, 60]:
        if n > w:
            ma_w = close.rolling(w).mean()
            std_w = close.rolling(w).std()
            factors[f"BB_WIDTH_{w}D"] = (std_w.iloc[-1] * 2) / max(ma_w.iloc[-1], 1e-12)

    # 真实波幅 ATR
    for w in [14, 28]:
        if n > w:
            tr = pd.concat(
                [
                    high - low,
                    (high - close.shift(1)).abs(),
                    (low - close.shift(1)).abs(),
                ],
                axis=1,
            ).max(axis=1)
            factors[f"ATR_{w}D"] = tr.iloc[-w:].mean() / max(close.iloc[-1], 1e-12)
    return factors


def _compute_price_volume_factors(
    close: pd.Series, volume: pd.Series, ret: pd.Series, n: int
) -> dict:
    """计算价量关系类因子 (OBV/价量背离)"""
    factors = {}
    # === 价量关系类 ===
    # OBV 能量潮变化
    if n > 20:
        obv = (np.sign(ret.fillna(0)) * volume).cumsum()
        factors["OBV_CHG"] = obv.iloc[-1] / max(obv.iloc[-20], 1e-12) - 1.0

    # 价量背离 (价格新高但成交量未新高)
    for w in [20, 60]:
        if n > w:
            price_new_high = close.iloc[-1] >= close.iloc[-w:].max()
            vol_new_high = volume.iloc[-1] >= volume.iloc[-w:].max()
            factors[f"PRICE_VOL_DIVERG_{w}D"] = (
                1.0 if (price_new_high and not vol_new_high) else 0.0
            )
    return factors


def _compute_fundamental_proxy_factors(
    close: pd.Series, ret: pd.Series, n: int
) -> dict:
    """计算基本面代理因子 (从量价衍生)"""
    factors = {}
    # === 基本面代理因子 (从量价衍生) ===
    # 市值代理 (价格相对位置)
    factors["SIZE_PROXY"] = close.iloc[-1]

    # 长期收益 (价值代理)
    if n > 252:
        factors["LONG_TERM_RET"] = close.iloc[-1] / close.iloc[-252] - 1.0

    # 盈利稳定性代理 (低波动)
    if n > 252:
        factors["EARNING_STABILITY"] = -ret.iloc[-252:].std()

    # 质量代理 (夏普比率)
    if n > 120:
        ret_120 = ret.iloc[-120:]
        factors["QUALITY_PROXY"] = ret_120.mean() / max(ret_120.std(), 1e-12)
    return factors


def compute_technical_factors(df_group: pd.DataFrame) -> pd.Series:
    """对单只股票计算所有技术因子

    Args:
        df_group: 单只股票的日线数据 (按日期排序)

    Returns:
        最新时间点的因子值 Series
    """
    factors = {}
    close = df_group["close"]
    volume = df_group["volume"]
    high = df_group["high"]
    low = df_group["low"]
    df_group["open"]
    n = len(close)

    if n < 20:
        return pd.Series(factors)

    ret = close.pct_change()

    factors.update(_compute_momentum_factors(close, ret, n))
    factors.update(_compute_volatility_factors(ret, n))
    factors.update(_compute_liquidity_factors(volume, ret, n))
    factors.update(_compute_technical_indicators(close, high, low, ret, n))
    factors.update(_compute_price_volume_factors(close, volume, ret, n))
    factors.update(_compute_fundamental_proxy_factors(close, ret, n))

    return pd.Series(factors)


def _collect_date_factors(stock_groups: dict, date: pd.Timestamp) -> dict:
    """遍历股票分组, 计算指定日期的因子值; 返回 {inst: factor_series}"""
    date_factors = {}
    for inst, hist in stock_groups.items():
        # 取截止到 date 的历史数据
        hist_slice = hist[hist.index <= date]
        if len(hist_slice) < 60:
            continue
        try:
            fv = compute_technical_factors(hist_slice)
            if len(fv) > 0:
                date_factors[inst] = fv
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            continue
    return date_factors


def _merge_date_factors_into_panel(
    date_factors: dict,
    factor_data: dict,
    date: pd.Timestamp,
    calc_dates: pd.Index,
    instruments: pd.Index,
) -> None:
    """将单日因子值合并进 factor_data 面板 (原地修改)"""
    if not date_factors:
        return
    df_f = pd.DataFrame(date_factors).T
    for fname in df_f.columns:
        if fname not in factor_data:
            factor_data[fname] = pd.DataFrame(
                index=calc_dates, columns=instruments, dtype=float
            )
        for inst in df_f.index:
            if inst in factor_data[fname].columns:
                factor_data[fname].loc[date, inst] = df_f.loc[inst, fname]


def _clean_factor_panels(factor_data: dict) -> dict:
    """清理空值并剔除样本过少的因子面板"""
    factor_panels = {}
    for fname, panel in factor_data.items():
        panel = panel.dropna(how="all")
        if len(panel) > 5:
            factor_panels[fname] = panel
    return factor_panels


def _compute_forward_returns_panel(
    stock_groups: dict, calc_dates: pd.Index, instruments: pd.Index
) -> dict:
    """计算多周期远期收益面板 {fwd_day: DataFrame(date x stock)}"""
    forward_returns = {}
    for fwd in [1, 5, 10, 20]:
        fr = pd.DataFrame(index=calc_dates, columns=instruments, dtype=float)
        for inst, hist in stock_groups.items():
            close = hist["close"].reindex(calc_dates)
            fwd_ret = close.shift(-fwd) / close - 1.0
            fr[inst] = fwd_ret
        forward_returns[fwd] = fr.dropna(how="all")
    return forward_returns


def compute_factors_panel_qlib(
    df: pd.DataFrame,
    step: int = 20,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """从 QLib 数据计算因子面板

    Args:
        df: QLib 原始数据 MultiIndex (instrument, datetime)
        step: 因子计算步长 (交易日)

    Returns:
        factor_panels: {factor_name: DataFrame(date x stock)}
        forward_returns: {forward_day: DataFrame(date x stock)}
    """
    instruments = df.index.get_level_values("instrument").unique()
    all_dates = df.index.get_level_values("datetime").unique().sort_values()

    # 选择计算时点 (间隔 step 天)
    calc_dates = all_dates[::step]
    logger.info(f"计算因子面板: {len(instruments)} 只股票, {len(calc_dates)} 个时点")

    # 按股票分组
    stock_groups = {
        inst: df.xs(inst, level="instrument").sort_index() for inst in instruments
    }

    factor_data = {}
    count = 0

    for date in calc_dates:
        date_factors = _collect_date_factors(stock_groups, date)
        _merge_date_factors_into_panel(
            date_factors, factor_data, date, calc_dates, instruments
        )

        count += 1
        if count % 10 == 0:
            logger.info(f"  进度: {count}/{len(calc_dates)}")

    # 清理空值
    factor_panels = _clean_factor_panels(factor_data)

    logger.info(f"因子面板计算完成: {len(factor_panels)} 个因子")

    # 计算远期收益
    forward_returns = _compute_forward_returns_panel(
        stock_groups, calc_dates, instruments
    )

    return factor_panels, forward_returns


# ============================================================
# 因子有效性验证
# ============================================================


@dataclass
class FactorValidationResult:
    factor_name: str
    category: str
    ic_mean: float = 0.0
    ic_std: float = 0.0
    ic_ir: float = 0.0
    ic_positive_ratio: float = 0.0
    long_short_return: float = 0.0
    group_returns: list[float] = field(default_factory=list)
    monotonicity: float = 0.0
    decay_5d: float = 0.0
    decay_10d: float = 0.0
    effective: bool = False
    direction: str = "positive"
    score: float = 0.0


def _compute_ic_for_date(
    fvals: pd.Series, date: pd.Timestamp, fwd_1d: pd.DataFrame, fwd_5d: pd.DataFrame
) -> tuple[list, list]:
    """计算单日 1d/5d IC 值; 返回 (ics_1d_single, ics_5d_single) 各含 0 或 1 个元素"""
    ics_1d_single = []
    ics_5d_single = []
    for _fwd, ic_list, fr_panel in [
        (1, ics_1d_single, fwd_1d),
        (5, ics_5d_single, fwd_5d),
    ]:
        if fr_panel is not None and len(fr_panel) > 0 and date in fr_panel.index:
            rets = fr_panel.loc[date].reindex(fvals.index).dropna()
            common = fvals.index.intersection(rets.index)
            if len(common) >= 5:
                try:
                    ic = float(fvals[common].corr(rets[common], method="spearman"))
                    if not np.isnan(ic):
                        ic_list.append(ic)
                except (
                    ValueError,
                    TypeError,
                    KeyError,
                    AttributeError,
                    RuntimeError,
                    OSError,
                    TimeoutError,
                    ConnectionError,
                ):
                    # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                    pass
    return ics_1d_single, ics_5d_single


def _compute_group_returns_for_date(
    fvals: pd.Series, date: pd.Timestamp, fwd_1d: pd.DataFrame, n_groups: int
) -> list:
    """计算单日 n_groups 分层回测各组平均收益; 失败返回空列表"""
    if not (len(fwd_1d) > 0 and date in fwd_1d.index):
        return []
    rets_1d = fwd_1d.loc[date].reindex(fvals.index).dropna()
    common = fvals.index.intersection(rets_1d.index)
    if len(common) < n_groups * 2:
        return []
    try:
        ranked = fvals[common].rank()
        group_size = len(ranked) // n_groups
        group_rets = []
        for g in range(n_groups):
            start = g * group_size
            end = (g + 1) * group_size if g < n_groups - 1 else len(ranked)
            group_stocks = ranked[(ranked > start) & (ranked <= end)].index
            group_rets.append(rets_1d[group_stocks].mean())
        return group_rets
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ):
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        return []


def _aggregate_factor_statistics(
    result: FactorValidationResult,
    ics_1d: list,
    ics_5d: list,
    group_rets_by_date: list,
    n_groups: int,
) -> bool:
    """汇总单因子 IC 统计、分层回测统计与评分; 返回是否成功聚合"""
    if len(ics_1d) < 5:
        return False

    ics_arr = np.array(ics_1d)
    result.ic_mean = float(np.mean(ics_arr))
    result.ic_std = float(np.std(ics_arr)) if len(ics_arr) > 1 else 1e-12
    result.ic_ir = result.ic_mean / result.ic_std if result.ic_std > 1e-12 else 0.0
    result.ic_positive_ratio = float(np.mean(ics_arr > 0))
    result.direction = "positive" if result.ic_mean >= 0 else "negative"

    if len(ics_5d) >= 5:
        result.decay_5d = abs(np.mean(ics_5d)) / max(abs(result.ic_mean), 1e-12)

    # 分层回测统计
    if group_rets_by_date:
        gr_arr = np.array(group_rets_by_date)
        avg_group_rets = gr_arr.mean(axis=0)
        result.group_returns = avg_group_rets.tolist()

        # 多空收益 (多头最高组 - 空头最低组)
        if result.ic_mean >= 0:
            result.long_short_return = float(avg_group_rets[-1] - avg_group_rets[0])
        else:
            result.long_short_return = float(avg_group_rets[0] - avg_group_rets[-1])

        # 单调性 (分组收益与组号的相关性)
        corr = np.corrcoef(range(n_groups), avg_group_rets)[0, 1]
        result.monotonicity = float(corr) if not np.isnan(corr) else 0.0

    # 有效性判定
    result.effective = abs(result.ic_mean) >= 0.02 and abs(result.ic_ir) >= 0.3

    # 综合评分
    result.score = (
        abs(result.ic_mean) * 20
        + min(abs(result.ic_ir), 3.0) * 10
        + result.ic_positive_ratio * 10
        + abs(result.monotonicity) * 15
        + max(result.long_short_return, 0) * 500
        + result.decay_5d * 5
    )
    return True


def validate_factors(
    factor_panels: dict[str, pd.DataFrame],
    forward_returns: dict[int, pd.DataFrame],
    n_groups: int = 5,
) -> list[FactorValidationResult]:
    """验证因子有效性 (IC + 分层回测)

    Args:
        factor_panels: {factor_name: DataFrame(date x stock)}
        forward_returns: {forward_day: DataFrame(date x stock)}
        n_groups: 分层回测组数

    Returns:
        验证结果列表
    """
    logger.info(f"开始验证 {len(factor_panels)} 个因子, {n_groups} 分组回测")

    results = []
    fwd_1d = forward_returns.get(1, pd.DataFrame())
    fwd_5d = forward_returns.get(5, pd.DataFrame())

    for fname, fpanel in factor_panels.items():
        category = fname.split("_")[0] if "_" in fname else "Other"
        result = FactorValidationResult(factor_name=fname, category=category)

        ics_1d = []
        ics_5d = []
        group_rets_by_date = []

        for date in fpanel.index:
            fvals = fpanel.loc[date].dropna()
            if len(fvals) < n_groups * 2:
                continue

            # IC 计算
            ic_1d_single, ic_5d_single = _compute_ic_for_date(
                fvals, date, fwd_1d, fwd_5d
            )
            ics_1d.extend(ic_1d_single)
            ics_5d.extend(ic_5d_single)

            # 分层回测
            group_rets = _compute_group_returns_for_date(fvals, date, fwd_1d, n_groups)
            if group_rets:
                group_rets_by_date.append(group_rets)

        # 统计 IC 与评分
        if _aggregate_factor_statistics(
            result, ics_1d, ics_5d, group_rets_by_date, n_groups
        ):
            results.append(result)

    results.sort(key=lambda r: r.score, reverse=True)

    eff = sum(1 for r in results if r.effective)
    logger.info(f"验证完成: 有效因子 {eff}/{len(results)}")

    return results


# ============================================================
# 报告生成
# ============================================================


def generate_report(
    results: list[FactorValidationResult],
    n_stocks: int,
    n_dates: int,
    start_date: str,
    end_date: str,
    output_dir: Path,
) -> str:
    """生成 Markdown 报告"""

    effective = [r for r in results if r.effective]
    strong = [r for r in results if abs(r.ic_ir) >= 0.5]

    lines = []
    lines.append("# 增强版因子挖掘报告")
    lines.append("")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("**数据源**: QLib 全市场数据")
    lines.append(f"**标的数**: {n_stocks} 只股票")
    lines.append(f"**数据区间**: {start_date} ~ {end_date}")
    lines.append(f"**测试因子数**: {len(results)}")
    lines.append(f"**有效因子数**: {len(effective)} (|IC|≥0.02 且 |IR|≥0.3)")
    lines.append(f"**强因子数**: {len(strong)} (|IR|≥0.5)")
    lines.append("")

    # 强因子
    lines.append("## 强因子 TOP 10")
    lines.append("")
    display = strong[:10] if strong else results[:10]
    lines.append(
        "| 排名 | 因子名 | 类别 | IC | IC_IR | 正IC% | 多空收益 | 单调性 | 5日衰减 | 评分 | 方向 |"
    )
    lines.append(
        "|------|--------|------|-----|-------|-------|----------|--------|---------|------|------|"
    )
    for i, r in enumerate(display):
        ls_ret = f"{r.long_short_return*100:.2f}%" if r.long_short_return else "N/A"
        mono = f"{r.monotonicity:.3f}" if r.group_returns else "N/A"
        lines.append(
            f"| {i+1} | {r.factor_name} | {r.category} | {r.ic_mean:.4f} | "
            f"{r.ic_ir:.3f} | {r.ic_positive_ratio:.0%} | {ls_ret} | "
            f"{mono} | {r.decay_5d:.2f} | {r.score:.1f} | {r.direction} |"
        )
    lines.append("")

    # 分层回测可视化 (文字表)
    lines.append("## 分层回测分析 (TOP 5 因子)")
    lines.append("")
    for r in results[:5]:
        if r.group_returns:
            lines.append(
                f"### {r.factor_name} (IC={r.ic_mean:.4f}, IR={r.ic_ir:.3f}, 方向={r.direction})"
            )
            lines.append("")
            lines.append("| 分组 | 平均日收益 | 累计收益 |")
            lines.append("|------|-----------|----------|")
            cum = 1.0
            for gi, gr in enumerate(r.group_returns):
                cum *= 1 + gr
                label = (
                    "空头"
                    if (gi == 0 and r.direction == "positive")
                    or (gi == len(r.group_returns) - 1 and r.direction == "negative")
                    else (
                        "多头"
                        if (
                            gi == len(r.group_returns) - 1 and r.direction == "positive"
                        )
                        or (gi == 0 and r.direction == "negative")
                        else f"G{gi+1}"
                    )
                )
                lines.append(f"| {label} | {gr*100:.3f}% | {cum*100-100:.2f}% |")
            lines.append("")
            lines.append(
                f"  **单调性**: {r.monotonicity:.3f} | **多空日收益**: {r.long_short_return*100:.3f}%"
            )
            lines.append("")

    # 全因子排名
    lines.append("## 全因子排名")
    lines.append("")
    lines.append(
        "| 排名 | 因子名 | 类别 | IC | IC_IR | 正IC% | 多空收益 | 有效 | 评分 |"
    )
    lines.append(
        "|------|--------|------|-----|-------|-------|----------|------|------|"
    )
    for i, r in enumerate(results):
        ls_ret = f"{r.long_short_return*100:.3f}%" if r.long_short_return else "N/A"
        eff_marker = "✅" if r.effective else "❌"
        lines.append(
            f"| {i+1} | {r.factor_name} | {r.category} | {r.ic_mean:.4f} | "
            f"{r.ic_ir:.3f} | {r.ic_positive_ratio:.0%} | {ls_ret} | "
            f"{eff_marker} | {r.score:.1f} |"
        )
    lines.append("")

    lines.append("## 备注")
    lines.append("")
    lines.append("- **IC**: Spearman 秩相关系数, 衡量因子与未来收益的单调关系")
    lines.append("- **IC_IR**: IC均值 / IC标准差, 衡量因子的稳定性 (越高越稳定)")
    lines.append("- **多空收益**: 最高分组 - 最低分组的平均日收益")
    lines.append("- **单调性**: 分组收益与组号的相关系数, |值|越大单调性越好")
    lines.append("- **5日衰减**: 5日IC / 1日IC, 衡量因子信息衰减速度")
    lines.append("- **基本面代理因子**: 从量价数据衍生, 非真实PE/PB/ROE")
    lines.append("")

    content = "\n".join(lines)
    output_path = output_dir / f"factor_discovery_enhanced_{start_date}_{end_date}.md"
    output_path.write_text(content, encoding="utf-8")
    logger.info(f"报告已保存: {output_path}")

    # JSON 数据
    json_path = output_dir / f"factor_discovery_enhanced_{start_date}_{end_date}.json"
    json_data = {
        "n_stocks": n_stocks,
        "start_date": start_date,
        "end_date": end_date,
        "n_factors": len(results),
        "n_effective": len(effective),
        "effective_factors": [
            {
                "factor_name": r.factor_name,
                "category": r.category,
                "ic_mean": r.ic_mean,
                "ic_ir": r.ic_ir,
                "ic_positive_ratio": r.ic_positive_ratio,
                "long_short_return": r.long_short_return,
                "group_returns": r.group_returns,
                "monotonicity": r.monotonicity,
                "direction": r.direction,
                "effective": r.effective,
                "score": r.score,
            }
            for r in effective
        ],
        "all_factors": [
            {
                "factor_name": r.factor_name,
                "category": r.category,
                "ic_mean": r.ic_mean,
                "ic_ir": r.ic_ir,
                "effective": r.effective,
                "score": r.score,
            }
            for r in results
        ],
    }
    json_path.write_text(
        json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return content


# ============================================================
# 主流程
# ============================================================


def run_enhanced_discovery(
    instruments: str = "csi500",
    start_date: str = "2021-01-01",
    end_date: str | None = None,
    step: int = 20,
    n_groups: int = 5,
    output_dir: str | None = None,
):
    """运行增强版因子挖掘"""

    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")

    output_path = (
        Path(output_dir) if output_dir else Path(__file__).resolve().parent / "outputs"
    )
    output_path.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 60)
    logger.info("增强版因子挖掘启动")
    logger.info(f"  股票池: {instruments}")
    logger.info(f"  时间区间: {start_date} ~ {end_date}")
    logger.info(f"  计算步长: {step} 交易日")
    logger.info(f"  分层组数: {n_groups}")
    logger.info("=" * 60)

    if not init_qlib():
        logger.error("QLib 不可用, 退出")
        return

    df = load_qlib_data(
        instruments=instruments, start_time=start_date, end_time=end_date
    )
    n_stocks = df.index.get_level_values("instrument").nunique()

    factor_panels, forward_returns = compute_factors_panel_qlib(df, step=step)

    results = validate_factors(factor_panels, forward_returns, n_groups=n_groups)

    generate_report(
        results=results,
        n_stocks=n_stocks,
        n_dates=len(df.index.get_level_values("datetime").unique()),
        start_date=start_date,
        end_date=end_date,
        output_dir=output_path,
    )

    eff = [r for r in results if r.effective]
    strong = [r for r in results if abs(r.ic_ir) >= 0.5]

    logger.info("")
    logger.info("=" * 60)
    logger.info("因子挖掘完成!")
    logger.info(f"  股票数: {n_stocks}")
    logger.info(f"  测试因子: {len(results)}")
    logger.info(f"  有效因子: {len(eff)}")
    logger.info(f"  强因子: {len(strong)}")
    if eff:
        logger.info("  TOP 有效因子:")
        for i, r in enumerate(eff[:5]):
            logger.info(
                f"    {i+1}. {r.factor_name} (IC={r.ic_mean:.4f}, IR={r.ic_ir:.3f}, 多空={r.long_short_return*100:.3f}%/日)"  # noqa: E501
            )
    logger.info("=" * 60)

    return results


def main():
    parser = argparse.ArgumentParser(description="增强版因子挖掘工具 (QLib)")
    parser.add_argument(
        "--universe",
        default="csi500",
        choices=["all", "csi300", "csi500", "csi800"],
        help="股票池",
    )
    parser.add_argument("--start", default="2021-01-01", help="起始日期")
    parser.add_argument("--end", default=None, help="结束日期")
    parser.add_argument("--step", type=int, default=20, help="计算步长 (交易日)")
    parser.add_argument("--groups", type=int, default=5, help="分层回测组数")
    args = parser.parse_args()

    run_enhanced_discovery(
        instruments=args.universe,
        start_date=args.start,
        end_date=args.end,
        step=args.step,
        n_groups=args.groups,
    )


if __name__ == "__main__":
    main()
