# -*- coding: utf-8 -*-
"""
多因子横截面均衡打分

复用：
- utils/vibe_trading_adapter.VibeFactorAdapter: 458 因子计算
- utils/akshare_data_source.AKShareDataSource.get_historical_klines: K线获取

因子均衡权重（用户选择）：
- 动量 momentum     30%
- 反转 reversal      20%
- 量价 volume        20%
- 波动率 volatility  15%
- 流动性 liquidity   15%
"""

from __future__ import annotations

import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 确保项目根在 sys.path
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ============================================================
# 因子主题均衡权重（对冲基金多因子模型标准）
# ============================================================
DEFAULT_THEME_WEIGHTS: Dict[str, float] = {
    "momentum": 0.30,  # 动量
    "reversal": 0.20,  # 反转
    "volume": 0.20,  # 量价
    "volatility": 0.15,  # 波动率
    "liquidity": 0.15,  # 流动性
}


@dataclass
class ScoringConfig:
    """打分配置"""

    theme_weights: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_THEME_WEIGHTS))
    # 每个主题选取的因子数上限（避免某主题因子数过多压制其他主题）
    max_factors_per_theme: int = 30
    # 并行计算
    max_workers: int = 4
    # K线历史长度
    kline_count: int = 300
    # K线缓存目录
    cache_dir: str = str(_REPO_ROOT / "data" / "cache" / "klines")


# ============================================================
# 单股因子计算（可在进程池中运行）
# ============================================================
def _compute_single_stock_factor(
    symbol: str,
    kline_df: pd.DataFrame,
    factor_ids: List[str],
) -> Tuple[str, Dict[str, float]]:
    """计算单只股票的指定因子（独立函数，可 pickle）

    Args:
        symbol: 股票代码
        kline_df: K线数据 (columns: open/high/low/close/volume/amount)
        factor_ids: 要计算的因子 ID 列表

    Returns:
        (symbol, {factor_id: value, ...})
    """
    if kline_df is None or kline_df.empty or len(kline_df) < 30:
        return symbol, {}

    try:
        # 延迟导入避免循环依赖
        from utils.vibe_trading_adapter import get_vibe_adapter

        adapter = get_vibe_adapter()
        result = adapter.compute_single_stock(kline_df, factor_ids=factor_ids)
        return symbol, dict(result.values)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"因子计算失败 {symbol}: {e}")
        return symbol, {}


# ============================================================
# 主入口
# ============================================================
def select_factor_ids(adapter, config: ScoringConfig) -> Dict[str, List[str]]:
    """按主题选择因子 ID

    Returns:
        {theme: [factor_id, ...]}
    """
    theme_factors: Dict[str, List[str]] = {}
    _all_factors = adapter.list_factors()  # noqa: F841  保留调用以触发适配器初始化

    # 按 zoo 优先级：gtja191 > qlib158 > alpha101 > academic > fundamental
    zoo_priority = ["gtja191", "qlib158", "alpha101", "academic", "fundamental"]

    for theme, weight in config.theme_weights.items():
        if weight <= 0:
            continue
        # 在所有 zoo 中找该主题的因子
        candidates: List[str] = []
        for zoo in zoo_priority:
            ids = adapter.list_factors(zoo=zoo, theme=theme)
            # 标准化 ID（加 zoo 前缀避免歧义）
            candidates.extend(ids)
            if len(candidates) >= config.max_factors_per_theme * 2:
                break

        # 去重 + 截断
        seen = set()
        unique = []
        for fid in candidates:
            if fid not in seen:
                seen.add(fid)
                unique.append(fid)
        theme_factors[theme] = unique[: config.max_factors_per_theme]
        logger.info(f"  主题 {theme:12s} 权重 {weight * 100:.0f}%  选取 {len(theme_factors[theme])} 个因子")

    return theme_factors


def batch_compute_factors(
    symbols: List[str],
    klines_loader,
    config: Optional[ScoringConfig] = None,
    progress_callback=None,
) -> Tuple[pd.DataFrame, Dict[str, List[str]]]:
    """批量计算多只股票的多因子值

    Args:
        symbols: 股票代码列表
        klines_loader: 可调用对象，signature: klines_loader(symbol) -> pd.DataFrame
        config: 打分配置
        progress_callback: 进度回调 callback(done, total, current_symbol)

    Returns:
        (factor_df, theme_factors)
        factor_df: DataFrame, index=symbol, columns=[factor_id, ...]
        theme_factors: {theme: [factor_id, ...]}
    """
    if config is None:
        config = ScoringConfig()

    # 延迟导入 vibe adapter
    from utils.vibe_trading_adapter import get_vibe_adapter

    adapter = get_vibe_adapter()

    # 1. 选因子
    logger.info("=" * 60)
    logger.info("步骤 1: 按主题均衡选择因子")
    logger.info("=" * 60)
    theme_factors = select_factor_ids(adapter, config)
    all_factor_ids = sorted({fid for fids in theme_factors.values() for fid in fids})
    total_factors = len(all_factor_ids)
    logger.info(f"共选取 {total_factors} 个因子 (跨 {len(theme_factors)} 个主题)")

    # 2. 批量计算
    logger.info("\n" + "=" * 60)
    logger.info(f"步骤 2: 批量计算因子 ({len(symbols)} 只股票 × {total_factors} 因子)")
    logger.info("=" * 60)

    factor_values: Dict[str, Dict[str, float]] = {}
    start_time = time.time()

    # 用线程池（避免进程池的 pickle 问题）
    completed = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
        # 预加载 K 线
        futures = {}
        for symbol in symbols:
            try:
                kline_df = klines_loader(symbol)
                if kline_df is None or kline_df.empty:
                    failed += 1
                    continue
                future = executor.submit(_compute_single_stock_factor, symbol, kline_df, all_factor_ids)
                futures[future] = symbol
            except Exception as e:  # noqa: BLE001
                logger.debug(f"K线加载失败 {symbol}: {e}")
                failed += 1

        for future in as_completed(futures):
            symbol = futures[future]
            try:
                sym, values = future.result()
                if values:
                    factor_values[sym] = values
                    completed += 1
                else:
                    failed += 1
            except Exception as e:  # noqa: BLE001
                logger.debug(f"因子计算异常 {symbol}: {e}")
                failed += 1

            # 进度回调
            if progress_callback:
                progress_callback(completed + failed, len(symbols), symbol)

            # 日志（每 50 只打印一次）
            if (completed + failed) % 50 == 0 or (completed + failed) == len(symbols):
                elapsed = time.time() - start_time
                rate = (completed + failed) / max(elapsed, 0.1)
                eta = (len(symbols) - completed - failed) / max(rate, 0.01)
                logger.info(
                    f"  进度: {completed + failed}/{len(symbols)}  "
                    f"成功: {completed}  失败: {failed}  "
                    f"速率: {rate:.1f}只/秒  ETA: {eta:.0f}秒"
                )

    elapsed = time.time() - start_time
    logger.info(f"因子计算完成: {completed} 成功 / {failed} 失败  耗时: {elapsed:.1f}秒")

    # 3. 构建 DataFrame
    if not factor_values:
        logger.error("无有效因子数据")
        return pd.DataFrame(), theme_factors

    factor_df = pd.DataFrame.from_dict(factor_values, orient="index")
    factor_df.index.name = "symbol"

    # 替换 inf 为 nan
    factor_df = factor_df.replace([np.inf, -np.inf], np.nan)

    logger.info(f"因子矩阵: {factor_df.shape[0]} 只 × {factor_df.shape[1]} 个因子")
    return factor_df, theme_factors


def cross_sectional_score(
    factor_df: pd.DataFrame,
    theme_factors: Dict[str, List[str]],
    theme_weights: Optional[Dict[str, float]] = None,
) -> pd.DataFrame:
    """横截面均衡多因子打分

    流程：
    1. 每个因子做横截面 z-score
    2. 同主题因子等权合成 → 主题得分
    3. 主题得分按权重加权 → 综合得分

    Args:
        factor_df: 因子值矩阵 (index=symbol, columns=factor_id)
        theme_factors: {theme: [factor_id, ...]}
        theme_weights: {theme: weight}, None 使用默认

    Returns:
        DataFrame: index=symbol, columns=[theme1_score, theme2_score, ..., composite_score, rank]
    """
    if theme_weights is None:
        theme_weights = DEFAULT_THEME_WEIGHTS

    scores = pd.DataFrame(index=factor_df.index)
    stats = {}

    # 1. 每个主题：因子 z-score + 等权合成
    for theme, factor_ids in theme_factors.items():
        # 只保留实际存在的因子
        valid_ids = [fid for fid in factor_ids if fid in factor_df.columns]
        if not valid_ids:
            scores[f"{theme}_score"] = 0.0
            stats[theme] = 0
            continue

        # 横截面 z-score
        sub = factor_df[valid_ids].copy()
        # 去极值（MAD 法）
        median = sub.median(axis=0, skipna=True)
        mad = (sub - median).abs().median(axis=0, skipna=True)
        mad_safe = mad.where(mad > 1e-10, 1.0)
        sub = (sub - median) / (1.4826 * mad_safe)
        # 再次去极值（±3）
        sub = sub.clip(-3, 3)
        # z-score
        mean = sub.mean(axis=0, skipna=True)
        std = sub.std(axis=0, skipna=True).where(lambda s: s > 1e-10, 1.0)
        sub = (sub - mean) / std

        # 同主题等权合成（axis=1 求均值）
        theme_score = sub.mean(axis=1, skipna=True)
        scores[f"{theme}_score"] = theme_score
        stats[theme] = len(valid_ids)

    # 2. 加权综合得分
    scores["composite_score"] = 0.0
    total_weight = 0.0
    for theme, weight in theme_weights.items():
        col = f"{theme}_score"
        if col in scores.columns:
            # 归一化主题得分到 [0,1]
            theme_vals = scores[col]
            if theme_vals.std() > 1e-10:
                theme_norm = (theme_vals - theme_vals.min()) / (theme_vals.max() - theme_vals.min() + 1e-10)
            else:
                theme_norm = pd.Series(0.5, index=theme_vals.index)
            scores["composite_score"] += theme_norm * weight
            total_weight += weight

    if total_weight > 0:
        scores["composite_score"] /= total_weight

    # 3. 排名（NaN 得分视为最低，避免 NaN 导致 astype(int) 崩溃）
    comp_filled = scores["composite_score"].fillna(scores["composite_score"].min())
    scores["rank"] = comp_filled.rank(ascending=False, method="min").astype(int)
    scores = scores.sort_values("composite_score", ascending=False)

    logger.info("\n横截面打分完成:")
    logger.info(f"  各主题因子数: {stats}")
    logger.info("  Top 10:")
    for sym, row in scores.head(10).iterrows():
        logger.info(f"    {sym}: 综合={row['composite_score']:.4f}  排名={row['rank']}")

    scores.attrs["theme_stats"] = stats
    return scores


def industry_neutralize(
    scores: pd.Series,
    industry_map: Dict[str, str],
) -> pd.Series:
    """行业中性化：在每个行业内做 z-score

    Args:
        scores: 综合得分 (index=symbol)
        industry_map: {symbol: industry}

    Returns:
        Series: 行业中性化后的得分
    """
    if not industry_map:
        logger.warning("行业映射为空，跳过行业中性化")
        return scores

    ind_series = pd.Series(industry_map)
    # 对齐
    aligned = ind_series.reindex(scores.index)
    # 对每个行业做 z-score
    result = scores.copy().astype(float)
    for industry, group_idx in aligned.groupby(aligned).groups.items():
        if industry is None or pd.isna(industry):
            continue
        group = scores.loc[group_idx]
        if len(group) < 2:
            continue
        mu = group.mean()
        sd = group.std()
        if sd > 1e-10:
            result.loc[group_idx] = (group - mu) / sd
        else:
            result.loc[group_idx] = 0.0

    logger.info(f"行业中性化完成: {result.notna().sum()} 只股票分布在 {aligned.nunique()} 个行业")
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # 自测
    logger.info(ScoringConfig())
    logger.info(DEFAULT_THEME_WEIGHTS)
