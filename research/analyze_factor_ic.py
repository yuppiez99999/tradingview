"""
因子对选股系统是否有效 - 实证 IC 分析

方法：以过去 ~12 个月为样本，按固定调仓周期（20 交易日）做横截面检验：
  1. 取某调仓日 T 的全市场因子截面得分 (composite + 5 大主题)
  2. 计算 T 之后 20 交易日的真实收益率（前视收益）
  3. 计算因子得分与前视收益的 Spearman 秩相关 = 信息系数 IC
  4. 汇总多个调仓日的 IC 序列，给出：
       - mean IC / IC 标准差 / ICIR (= mean/std) / t 值 / 正值占比
       - 各主题独立 IC
       - 与随机基准 (IC~0) 对比

判定标准（业界）：
  |mean IC| >= 0.05 且 t >= 2   -> 因子有效（有稳定预测力）
  0.03 <= |mean IC| < 0.05      -> 弱有效
  |mean IC| < 0.03 或 t < 2     -> 基本无效

用法：
  py research/analyze_factor_ic.py --universe hs300 --n-stocks 80 --horizon 20
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scipy import stats as scipy_stats  # noqa: E402

from utils.universe.factor_scorer import (  # noqa: E402
    ScoringConfig,
    cross_sectional_score,
    select_factor_ids,
)
from utils.universe.stock_universe import get_universe  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("factor_ic")

# 调仓周期（交易日）
REBALANCE_STEP = 20
# 因子需要的最小历史长度（用于剔除早期不稳定期）
MIN_LOOKBACK = 120
# 因子缓存目录
FACTOR_CACHE_DIR = _REPO_ROOT / "data" / "cache" / "factor_series"


def fetch_factor_series(symbol: str, kline_df: pd.DataFrame, factor_ids, adapter) -> pd.DataFrame | None:
    """为单只股票计算全部因子的完整时间序列（对齐到 kline 索引）"""
    if kline_df is None or len(kline_df) < 60:
        return None
    try:
        result = adapter.compute_single_stock(kline_df, factor_ids=list(factor_ids))
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001
        logger.debug(f"因子计算失败 {symbol}: {e}")
        return None

    # 时间序列字典 -> DataFrame，index 对齐到 kline
    series_dict = {fid: s for fid, s in result.series.items() if fid in factor_ids}
    if not series_dict:
        return None
    df = pd.DataFrame(series_dict, index=kline_df.index)
    # 与收盘价对齐（用于计算前视收益）
    if "close" in kline_df.columns:
        df["_close"] = kline_df["close"].values
    return df


def _parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", default="hs300", choices=["hs300", "zz500", "hs300_zz500"])
    parser.add_argument("--n-stocks", type=int, default=80)
    parser.add_argument("--horizon", type=int, default=20, help="前视收益窗口（交易日）")
    parser.add_argument("--kline-count", type=int, default=600)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def _load_stock_codes(universe_type: str, n_stocks: int) -> list:
    """获取股票池代码列表"""
    logger.info("=" * 70)
    logger.info("因子有效性 IC 分析")
    logger.info("=" * 70)
    univ_df = get_universe(universe_type)
    if "code" in univ_df.columns:
        codes = univ_df["code"].tolist()
    else:
        codes = univ_df.index.tolist()
    codes = codes[:n_stocks]
    if not codes:
        logger.error("未能获取股票池")
        return []
    logger.info(f"股票池: {universe_type}  取样 {len(codes)} 只")
    return codes


def _prepare_factor_setup(kline_count: int):
    """初始化 K 线加载器与因子适配器，返回 (loader, adapter, theme_factors, all_factor_ids)"""
    # 2. K 线加载器 + 因子适配器
    from utils.universe.scheduler import KlinesLoader  # noqa: E402
    from utils.vibe_trading_adapter import get_vibe_adapter  # noqa: E402

    loader = KlinesLoader(count=kline_count)
    adapter = get_vibe_adapter()
    config = ScoringConfig(kline_count=kline_count)
    theme_factors = select_factor_ids(adapter, config)
    all_factor_ids = sorted({fid for fids in theme_factors.values() for fid in fids})
    logger.info(f"选取因子 {len(all_factor_ids)} 个 / 主题: " +
                ", ".join(f"{k}={len(v)}" for k, v in theme_factors.items()))
    return loader, adapter, theme_factors, all_factor_ids


def _compute_stock_factor_series(codes, loader, adapter, all_factor_ids) -> dict:
    """计算并缓存每只股票的因子时间序列"""
    logger.info("-" * 70)
    logger.info("计算因子时间序列（带缓存）")
    stock_data: dict[str, pd.DataFrame] = {}
    for i, code in enumerate(codes):
        cache_file = FACTOR_CACHE_DIR / f"{code}.parquet"
        df = None
        if cache_file.exists():
            try:
                df = pd.read_parquet(cache_file)
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                df = None
        if df is None or "_close" not in df.columns:
            kline = loader(code)
            if kline is None or len(kline) < 60:
                continue
            df = fetch_factor_series(code, kline, all_factor_ids, adapter)
            if df is None:
                continue
            try:
                df.to_parquet(cache_file)
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                pass
        stock_data[code] = df
        if (i + 1) % 20 == 0:
            logger.info(f"  已处理 {i+1}/{len(codes)} 只")
    logger.info(f"有效股票 {len(stock_data)} 只")
    return stock_data


def _build_rebalance_dates(stock_data: dict) -> tuple:
    """构建统一日期轴并按 REBALANCE_STEP 取调仓日；返回 (all_dates, rebal_dates)"""
    # 统一日期轴：取所有股票日期的并集，按交易日步进
    # 每只股票用自己的日期索引查因子值与收盘价，天然兼容不同长度
    all_dates = set()
    for df in stock_data.values():
        all_dates.update(df.index.tolist())
    all_dates = sorted(all_dates)
    logger.info(f"全样本日期范围: {all_dates[0].date()} -> {all_dates[-1].date()}  (共 {len(all_dates)} 交易日)")

    # 调仓日：每隔 REBALANCE_STEP 取一个，且需留出前视窗口
    rebal_dates = all_dates[::REBALANCE_STEP]
    logger.info(f"候选调仓日: {len(rebal_dates)} 个")
    return all_dates, rebal_dates


def _compute_window_forward_returns(d, d_h, stock_data, all_factor_ids) -> tuple:
    """计算单个调仓日窗口内的因子值与前视收益"""
    rows = {}
    fwd_ret = {}
    for code, df in stock_data.items():
        if d not in df.index or d_h not in df.index:
            continue
        fvals = df.loc[d]
        fdict = {fid: fvals.get(fid, np.nan) for fid in all_factor_ids}
        if np.all([pd.isna(v) for v in fdict.values()]):
            continue
        rows[code] = fdict
        c0 = float(df.loc[d, "_close"])
        c1 = float(df.loc[d_h, "_close"])
        if c0 <= 0 or c1 <= 0:
            continue
        fwd_ret[code] = c1 / c0 - 1.0
    return rows, fwd_ret


def _compute_window_ic(d, all_dates, stock_data, all_factor_ids, theme_factors, horizon: int):
    """计算单个调仓日的综合 IC 及各主题 IC；返回 (ic, theme_ics) 或 None"""
    d_pos = all_dates.index(d)
    # 前视窗口末端日期
    h_pos = d_pos + horizon
    if h_pos >= len(all_dates):
        return None
    d_h = all_dates[h_pos]

    rows, fwd_ret = _compute_window_forward_returns(d, d_h, stock_data, all_factor_ids)
    if len(rows) < 30:
        return None

    fdf = pd.DataFrame(rows).T  # index=code, columns=factor
    # 横截面打分（要求每只股票有足够历史 -> 用因子值非空的行）
    # 过滤掉因子几乎全空的股票
    valid_codes = [c for c in fdf.index if fdf.loc[c].notna().sum() >= len(all_factor_ids) * 0.3]
    if len(valid_codes) < 30:
        return None
    fdf = fdf.loc[valid_codes]
    scores = cross_sectional_score(fdf, theme_factors)
    composite = scores["composite_score"]

    common = composite.index.intersection(list(fwd_ret.keys()))
    if len(common) < 30:
        return None
    y = pd.Series(fwd_ret)[common]
    x = composite[common]

    mask = x.notna() & y.notna()
    if mask.sum() < 20:
        return None
    ic = scipy_stats.spearmanr(x[mask], y[mask]).correlation

    # 各主题 IC
    theme_ics = {}
    for theme in theme_factors:
        col = f"{theme}_score"
        if col in scores.columns:
            xt = scores[col][common]
            m2 = xt.notna() & y.notna()
            if m2.sum() >= 20:
                theme_ics[theme] = scipy_stats.spearmanr(xt[m2], y[m2]).correlation
    return ic, theme_ics


def _summarize_composite_ic(ic_records: list) -> tuple:
    """汇总综合 IC 指标；返回 (ic_arr, ic_mean, ic_std, icir, t_stat, pct_pos)"""
    ic_arr = np.array(ic_records)
    ic_mean = ic_arr.mean()
    ic_std = ic_arr.std(ddof=1)
    icir = ic_mean / ic_std if ic_std > 1e-9 else 0.0
    t_stat = ic_mean / (ic_std / np.sqrt(len(ic_arr)))
    pct_pos = (ic_arr > 0).mean()

    logger.info("\n" + "=" * 70)
    logger.info(" 综合因子得分 (composite) 的信息系数 IC")
    logger.info("=" * 70)
    logger.info(f"  调仓样本数     : {len(ic_arr)}")
    logger.info(f"  平均 IC        : {ic_mean:+.4f}")
    logger.info(f"  IC 标准差      : {ic_std:.4f}")
    logger.info(f"  ICIR           : {icir:+.3f}")
    logger.info(f"  t 统计量       : {t_stat:+.2f}")
    logger.info(f"  正值占比       : {pct_pos*100:.1f}%")
    logger.info(f"  IC 范围        : [{ic_arr.min():+.4f}, {ic_arr.max():+.4f}]")
    return ic_arr, ic_mean, ic_std, icir, t_stat, pct_pos


def _summarize_theme_ic(theme_ic_records: dict) -> dict:
    """汇总各主题 IC；返回 {theme: (ic_mean, icir, t_stat, pct_positive)}"""
    logger.info("\n" + "-" * 70)
    logger.info(" 各主题独立 IC")
    logger.info("-" * 70)
    theme_summary = {}
    for theme, recs in theme_ic_records.items():
        if len(recs) < 3:
            continue
        a = np.array(recs)
        tm = a.mean()
        ts = a.std(ddof=1)
        tir = tm / ts if ts > 1e-9 else 0.0
        tt = tm / (ts / np.sqrt(len(a)))
        theme_summary[theme] = (tm, tir, tt, (a > 0).mean())
        logger.info(f"  {theme:12s}: IC={tm:+.4f}  ICIR={tir:+.3f}  t={tt:+.2f}  正值={(a>0).mean()*100:.0f}%")
    return theme_summary


def _judge_verdict(ic_mean: float, t_stat: float) -> str:
    """根据 IC 均值与 t 统计量给出判定结论"""
    if abs(ic_mean) >= 0.05 and t_stat >= 2.0:
        return "有效 - 因子对系统有稳定预测力"
    if abs(ic_mean) >= 0.03 and t_stat >= 1.5:
        return "弱有效 - 因子有边际预测力，但偏弱"
    return "基本无效 - 因子得分与未来收益无显著相关"


def _save_results(ic_arr, ic_mean, ic_std, icir, t_stat, pct_pos, theme_summary, verdict) -> None:
    """保存 IC 分析结果到 JSON 文件"""
    out = {
        "composite": {
            "n_windows": len(ic_arr), "ic_mean": ic_mean, "ic_std": ic_std,
            "icir": icir, "t_stat": t_stat, "pct_positive": pct_pos,
        },
        "themes": {k: {"ic_mean": v[0], "icir": v[1], "t_stat": v[2], "pct_positive": v[3]}
                   for k, v in theme_summary.items()},
        "verdict": verdict,
    }
    import json
    out_path = _REPO_ROOT / "reports" / "universe" / "factor_ic_result.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    logger.info(f"\n  结果已保存: {out_path}")


def main():
    args = _parse_args()

    np.random.seed(args.seed)
    FACTOR_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # 1. 股票池
    codes = _load_stock_codes(args.universe, args.n_stocks)
    if not codes:
        return

    # 2. K 线加载器 + 因子适配器
    loader, adapter, theme_factors, all_factor_ids = _prepare_factor_setup(args.kline_count)

    # 3. 计算并缓存每只股票因子时间序列
    stock_data = _compute_stock_factor_series(codes, loader, adapter, all_factor_ids)
    if len(stock_data) < 20:
        logger.error("有效股票数过少，无法做 IC 分析")
        return

    # 4. 逐调仓日做横截面 IC（按日期对齐，兼容不同长度）
    logger.info("-" * 70)
    logger.info(f"逐调仓日 IC 计算（前视窗口={args.horizon} 交易日）")

    ic_records = []  # 每个调仓日一条
    theme_ic_records: dict[str, list] = {t: [] for t in theme_factors}

    all_dates, rebal_dates = _build_rebalance_dates(stock_data)

    for d in rebal_dates:
        result = _compute_window_ic(d, all_dates, stock_data, all_factor_ids, theme_factors, args.horizon)
        if result is None:
            continue
        ic, theme_ics = result
        ic_records.append(ic)
        for theme, tic in theme_ics.items():
            theme_ic_records[theme].append(tic)

    # 5. 汇总
    logger.info("=" * 70)
    logger.info("IC 分析结果")
    logger.info("=" * 70)
    if len(ic_records) < 3:
        logger.error("调仓样本不足，无法得出结论")
        return

    ic_arr, ic_mean, ic_std, icir, t_stat, pct_pos = _summarize_composite_ic(ic_records)
    theme_summary = _summarize_theme_ic(theme_ic_records)

    # 6. 判定
    logger.info("\n" + "=" * 70)
    logger.info(" 结论")
    logger.info("=" * 70)
    verdict = _judge_verdict(ic_mean, t_stat)
    logger.info(f"  综合因子: 平均IC={ic_mean:+.4f}, t={t_stat:+.2f} -> {verdict}")
    # 找出最有效主题
    best_theme = max(theme_summary.items(), key=lambda kv: abs(kv[1][0]), default=None)
    if best_theme:
        logger.info(f"  最有效主题: {best_theme[0]} (IC={best_theme[1][0]:+.4f}, t={best_theme[1][2]:+.2f})")
    worst_theme = min(theme_summary.items(), key=lambda kv: abs(kv[1][0]), default=None)
    if worst_theme:
        logger.info(f"  最弱主题:   {worst_theme[0]} (IC={worst_theme[1][0]:+.4f}, t={worst_theme[1][2]:+.2f})")

    # 7. 保存结果
    _save_results(ic_arr, ic_mean, ic_std, icir, t_stat, pct_pos, theme_summary, verdict)


if __name__ == "__main__":
    t0 = time.time()
    main()
    logger.info(f"总耗时: {time.time()-t0:.1f} 秒")
