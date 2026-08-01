# -*- coding: utf-8 -*-
"""新闻情绪因子 (B3.5: 从 lgb_enhanced_trainer.py 抽取)

本模块集中以下职责:
  - compute_news_sentiment_factors: 主入口, 计算新闻情绪时间序列
  - add_sentiment_features: 将情绪因子合并到 OHLCV 数据 (生成 9 个特征)
  - 数据源初始化 (Wind MCP 优先, iFinD 回退)
  - 新闻日期解析、按日聚合、情绪分计算
  - 缓存加载/保存

数据源优先级:
    1. Wind MCP financial_docs.get_financial_news (主源, 替代配额耗尽的 iFinD)
    2. iFinD news.search_news (回退)

路径/配置由主模块通过 configure_paths 注入。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("lgb_enhanced")


# ============================================================
# 路径/配置 (由主模块注入)
# ============================================================
BASE_DIR: Path = Path(__file__).resolve().parent.parent
CACHE_DIR: Path = BASE_DIR / "cache" / "ohlcv"
LGB_ENHANCED_CONFIG: Dict[str, Any] = {
    "news_cache_hours": 6,
    "news_lookback_days": 30,
}


def configure_paths(
    base_dir: Path, cache_dir: Path, config: Dict[str, Any]
) -> None:
    """由主模块注入路径和配置。"""
    global BASE_DIR, CACHE_DIR, LGB_ENHANCED_CONFIG
    BASE_DIR = base_dir
    CACHE_DIR = cache_dir
    LGB_ENHANCED_CONFIG = config


# ============================================================
# 利好/利空关键词 (原 iFinD 关键词 + Wind MCP 新闻常见事件关键词)
# ============================================================
NEWS_KEYWORDS_POSITIVE: List[str] = [
    # 原 iFinD 关键词 (业绩/订单类)
    "预增", "增长", "中标", "订单", "扩产", "出海", "份额提升", "超预期", "盈利", "放量", "景气",
    # Wind MCP 扩展 (事件/资金/技术类)
    "涨停", "回购", "增持", "推荐", "突破", "新高", "上调", "入选", "合作", "研发",
    "投产", "启动", "上线", "推出", "成长", "提升", "向好", "强势", "加仓", "登顶", "首破",
]

NEWS_KEYWORDS_NEGATIVE: List[str] = [
    # 原 iFinD 关键词
    "预减", "下滑", "亏损", "处罚", "减持", "质押", "暴雷", "下调", "断供", "降价", "过剩",
    # Wind MCP 扩展
    "跌停", "下跌", "回落", "问询", "风险", "警告", "暴跌", "萎缩", "下降", "破发",
    "破净", "违规", "诉讼", "退市", "停牌", "走弱", "承压", "利空",
]

# 新闻发布时间日期格式
_PUB_TIME_FORMATS: Tuple[str, ...] = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y年%m月%d日",
)


# ============================================================
# 缓存
# ============================================================
def _load_news_sentiment_cache(cache_file: Path) -> Optional[Dict[str, pd.DataFrame]]:
    """加载新闻情绪因子缓存。

    Args:
        cache_file: 缓存文件路径

    Returns:
        缓存的 DataFrame 字典, 失败返回 None
    """
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            cache = json.load(f)
        return _sentiment_cache_to_df(cache)
    except Exception as e:
        logger.warning(f"加载新闻情绪缓存失败: {e}", exc_info=True)
        return None


def _save_news_sentiment_cache(
    cache_file: Path, sentiment_dict: Dict[str, pd.DataFrame]
) -> None:
    """保存新闻情绪因子到缓存文件。"""
    try:
        cache_data = {
            code: {
                "sentiment_score": df["sentiment_score"].tolist(),
                "index": [str(d) for d in df.index],
            }
            for code, df in sentiment_dict.items()
        }
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"保存新闻情绪缓存失败: {e}", exc_info=True)


def _sentiment_cache_to_df(cache: Dict) -> Dict[str, pd.DataFrame]:
    """缓存转 DataFrame。"""
    out: Dict[str, pd.DataFrame] = {}
    for code, data in cache.items():
        try:
            dates = pd.to_datetime(data["index"])
            df = pd.DataFrame(
                {
                    "sentiment_score": data["sentiment_score"],
                },
                index=dates,
            )
            df.index.name = "date"
            out[code] = df
        except Exception:
            continue
    return out


# ============================================================
# 数据源初始化
# ============================================================
def _init_news_data_sources() -> Tuple[Optional[Callable], Optional[Any]]:
    """初始化新闻数据源 (Wind MCP 优先, iFinD 回退)。

    Returns:
        (wind_search_news_fn, ifind_analyzer) 元组, 不可用项为 None
    """
    wind_search_news_fn = None
    try:
        import sys as _sys

        _tools_dir = str(BASE_DIR / "tools")
        if _tools_dir not in _sys.path:
            _sys.path.insert(0, _tools_dir)
        from wind_mcp_fetcher import wind_search_news as wind_search_news_fn

        logger.info("Wind MCP 新闻接口已加载 (tools/wind_mcp_fetcher.py)")
    except Exception as e:
        logger.warning(f"Wind MCP 新闻接口不可用: {e}")

    ifind_analyzer = None
    try:
        from utils.ifind_news_analyzer import IFinDNewsAnalyzer

        ifind_analyzer = IFinDNewsAnalyzer()
        if not ifind_analyzer.available():
            ifind_analyzer = None
    except Exception as e:
        logger.warning(f"iFinD 新闻接口加载失败: {e}", exc_info=True)

    return wind_search_news_fn, ifind_analyzer


def _fetch_news_for_symbol(
    code: str,
    name: str,
    wind_search_news_fn: Optional[Callable],
    ifind_analyzer: Optional[Any],
    lookback_days: int,
) -> Tuple[List[Tuple[str, str, str]], str]:
    """拉取单个标的的新闻数据 (Wind MCP 优先, iFinD 回退)。

    Args:
        code: 股票代码
        name: 股票名称
        wind_search_news_fn: Wind MCP 新闻接口
        ifind_analyzer: iFinD 新闻分析器
        lookback_days: 回看天数

    Returns:
        (normalized_news, source) 元组, source 为 "wind"/"ifind"/"none"
    """
    normalized_news: List[Tuple[str, str, str]] = []
    source = "none"

    # 1. 优先 Wind MCP
    if wind_search_news_fn:
        try:
            wind_query = f"{name} {code}".strip() if name else code
            items = wind_search_news_fn(wind_query, top_k=50)
            if items:
                for it in items:
                    title = it.get("title", "") or ""
                    snippet = it.get("snippet", "") or it.get("content", "") or ""
                    pub_time = it.get("publish_time", "") or it.get("date", "") or ""
                    if title or snippet:
                        normalized_news.append((title, snippet, pub_time))
                if normalized_news:
                    source = "wind"
        except Exception as e:
            logger.debug(f"  {code}: Wind MCP 新闻拉取失败: {e}")

    # 2. 回退 iFinD
    if not normalized_news and ifind_analyzer:
        try:
            news_query = f"{name} {code}".strip() if name else code
            items = ifind_analyzer.search_news(news_query, size=50, days=lookback_days)
            if items:
                for it in items:
                    title = getattr(it, "title", "") or ""
                    snippet = getattr(it, "snippet", "") or ""
                    pub_time = getattr(it, "publish_time", "") or ""
                    if title or snippet:
                        normalized_news.append((title, snippet, pub_time))
                if normalized_news:
                    source = "ifind"
        except Exception as e:
            logger.debug(f"  {code}: iFinD 新闻拉取失败: {e}")

    return normalized_news, source


# ============================================================
# 日期解析与按日聚合
# ============================================================
def _parse_news_date(pub_time: str) -> Optional[pd.Timestamp]:
    """解析新闻发布时间为 Timestamp。"""
    if not pub_time:
        return None

    for fmt in _PUB_TIME_FORMATS:
        try:
            return pd.to_datetime(pub_time, format=fmt)
        except (ValueError, TypeError):
            continue

    try:
        return pd.to_datetime(pub_time)
    except Exception:
        return None


def _aggregate_daily_sentiment(
    normalized_news: List[Tuple[str, str, str]],
    dates: pd.DatetimeIndex,
    lookback_days: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """按日聚合新闻情绪分。

    Args:
        normalized_news: 标准化新闻列表 [(title, snippet, pub_time)]
        dates: 日期索引
        lookback_days: 回看天数

    Returns:
        (daily_pos, daily_neg, daily_total) 每日利好/利空/总计数
    """
    daily_pos = np.zeros(lookback_days)
    daily_neg = np.zeros(lookback_days)
    daily_total = np.zeros(lookback_days)

    for title, snippet, pub_time in normalized_news:
        pub_date = _parse_news_date(pub_time)
        if pub_date is None:
            continue

        # 找到对应日期索引
        idx = dates.searchsorted(pub_date.normalize())
        if idx >= lookback_days:
            continue
        # searchsorted 返回插入位置, 需要检查是否精确匹配
        if dates[idx] != pub_date.normalize():
            if idx > 0 and dates[idx - 1] == pub_date.normalize():
                idx = idx - 1
            else:
                continue

        text = f"{title} {snippet}".lower()
        has_pos = any(k in text for k in NEWS_KEYWORDS_POSITIVE)
        has_neg = any(k in text for k in NEWS_KEYWORDS_NEGATIVE)

        if has_pos:
            daily_pos[idx] += 1
        if has_neg:
            daily_neg[idx] += 1
        if has_pos or has_neg:
            daily_total[idx] += 1

    return daily_pos, daily_neg, daily_total


def _compute_sentiment_scores(
    daily_pos: np.ndarray,
    daily_neg: np.ndarray,
    daily_total: np.ndarray,
    lookback_days: int,
) -> np.ndarray:
    """计算每日情绪分并前向填充 + 3日平滑。

    情绪分公式: (pos - neg) / (pos + neg), 范围 [-1, 1]
    """
    daily_scores = np.zeros(lookback_days)
    for i in range(lookback_days):
        total = daily_pos[i] + daily_neg[i]
        if total > 0:
            daily_scores[i] = (daily_pos[i] - daily_neg[i]) / total

    # 前向填充无新闻日 (保持上一天情绪)
    last_valid = 0.0
    for i in range(lookback_days):
        if daily_total[i] > 0:
            last_valid = daily_scores[i]
        else:
            daily_scores[i] = last_valid

    # 3日平滑, 减少单日噪声
    daily_scores = pd.Series(daily_scores).rolling(3, min_periods=1).mean().values
    return daily_scores


# ============================================================
# 主入口
# ============================================================
def compute_news_sentiment_factors(
    symbols: List[Tuple],
    lookback_days: int = 30,
    use_cache: bool = True,
) -> Dict[str, pd.DataFrame]:
    """计算新闻情绪因子 - 真实历史时间序列版 (Wind MCP 优先, iFinD 回退)。

    数据源优先级:
        1. Wind MCP financial_docs.get_financial_news (主源, 替代配额耗尽的 iFinD)
        2. iFinD news.search_news (回退)

    改进:
        1. 优先用 Wind MCP 拉取过去 N 天新闻 (top_k=50)
        2. Wind 失败时回退 iFinD
        3. 用 publish_time 解析每条新闻的日期
        4. 按日聚合: 计算每日真实情绪分 (positive_hits - negative_hits)
        5. 无新闻日使用前向填充, 失败时回退到中性 (0)

    Args:
        symbols: 标的清单
        lookback_days: 回看天数
        use_cache: 是否使用缓存

    Returns:
        {code: DataFrame[date, sentiment_score]}
    """
    cache_file = CACHE_DIR / f"news_sentiment_v2_{datetime.now():%Y%m%d}.json"

    # 1. 尝试加载缓存
    if use_cache and cache_file.exists():
        mtime = datetime.fromtimestamp(cache_file.stat().st_mtime)
        age_hours = (datetime.now() - mtime).total_seconds() / 3600
        if age_hours < LGB_ENHANCED_CONFIG["news_cache_hours"]:
            cached = _load_news_sentiment_cache(cache_file)
            if cached is not None:
                return cached

    # 2. 构建日期索引
    end_date = pd.Timestamp.now().normalize()
    dates = pd.date_range(end=end_date, periods=lookback_days, freq="D")

    # 3. 初始化数据源
    wind_search_news_fn, ifind_analyzer = _init_news_data_sources()
    if not wind_search_news_fn and not ifind_analyzer:
        logger.warning("Wind MCP 和 iFinD 新闻分析器均不可用, 跳过情绪因子")
        return {}

    sentiment_dict: Dict[str, pd.DataFrame] = {}
    success_count = 0
    wind_count = 0
    ifind_count = 0
    fallback_count = 0

    # 4. 逐标的计算情绪分
    for code, _suffix, _, name, _ in symbols:
        try:
            normalized_news, source = _fetch_news_for_symbol(
                code, name, wind_search_news_fn, ifind_analyzer, lookback_days
            )

            if source == "wind":
                wind_count += 1
            elif source == "ifind":
                ifind_count += 1

            if not normalized_news:
                fallback_count += 1
                logger.warning(f"  {code} ({name}): 无新闻数据, 情绪分将为 0")
                df = pd.DataFrame(
                    {
                        "sentiment_score": np.zeros(lookback_days),
                        "news_count": np.zeros(lookback_days, dtype=int),
                    },
                    index=dates,
                )
                df.index.name = "date"
                sentiment_dict[code] = df
                continue

            # 按日聚合 + 计算情绪分
            daily_pos, daily_neg, daily_total = _aggregate_daily_sentiment(
                normalized_news, dates, lookback_days
            )
            daily_scores = _compute_sentiment_scores(
                daily_pos, daily_neg, daily_total, lookback_days
            )

            df = pd.DataFrame(
                {
                    "sentiment_score": daily_scores,
                    "news_count": daily_total.astype(int),
                },
                index=dates,
            )
            df.index.name = "date"
            sentiment_dict[code] = df

            total_news = int(daily_total.sum())
            pos_days = int((daily_pos > 0).sum())
            neg_days = int((daily_neg > 0).sum())
            logger.info(
                f"  {code} ({name}) [{source}]: 总新闻={total_news}, "
                f"利好日={pos_days}, 利空日={neg_days}, "
                f"情绪分范围 [{daily_scores.min():.2f}, {daily_scores.max():.2f}]"
            )
            success_count += 1

        except Exception as e:
            logger.warning(f"  {code}: 新闻分析失败: {e}")
            continue

    logger.info(
        f"新闻情绪因子完成: {success_count}/{len(symbols)} 成功 "
        f"(Wind={wind_count}, iFinD={ifind_count}, 无数据={fallback_count})"
    )

    # 5. 保存缓存
    _save_news_sentiment_cache(cache_file, sentiment_dict)

    return sentiment_dict


def add_sentiment_features(
    ohlcv_dict: Dict[str, pd.DataFrame],
    sentiment_dict: Dict[str, pd.DataFrame],
) -> Dict[str, pd.DataFrame]:
    """将情绪因子合并到 OHLCV 数据上。

    Args:
        ohlcv_dict: {code: DataFrame[open, high, low, close, volume]}
        sentiment_dict: {code: DataFrame[sentiment_score]}

    Returns:
        合并后的字典, 每个 DataFrame 新增:
        - sentiment_score: 当日情绪分
        - sent_ma5: 5日情绪均值
        - sent_ma20: 20日情绪均值
        - sent_mom5: 5日情绪动量 (ma5 - ma20)
        - sent_volatility: 20日情绪波动率
        - sent_positive_ratio: 20日内正情绪占比
        - sent_zscore_20: 情绪标准化分数 (v3 新增)
        - sent_acceleration: 情绪加速度 (v3 新增)
        - sent_price_interaction: 情绪-价格交互 (v3 新增)
    """
    out: Dict[str, pd.DataFrame] = {}
    for code, df in ohlcv_dict.items():
        new_df = df.copy()
        if code in sentiment_dict:
            sent_df = sentiment_dict[code]
            # 对齐索引
            sent_df = sent_df.reindex(new_df.index, method="ffill").fillna(0)
            new_df["sentiment_score"] = sent_df["sentiment_score"]
        else:
            new_df["sentiment_score"] = 0.0

        # 滚动特征
        s = new_df["sentiment_score"]
        new_df["sent_ma5"] = s.rolling(5, min_periods=1).mean()
        new_df["sent_ma20"] = s.rolling(20, min_periods=1).mean()
        new_df["sent_mom5"] = new_df["sent_ma5"] - new_df["sent_ma20"]
        new_df["sent_volatility"] = s.rolling(20, min_periods=1).std().fillna(0)
        new_df["sent_positive_ratio"] = (s > 0).rolling(20, min_periods=1).mean()
        # v3 新增: 提升情绪因子表达力
        # 1. 情绪标准化分数 (z-score, 衡量当前情绪相对历史的极端程度)
        sent_std_20 = s.rolling(20, min_periods=5).std().replace(0, np.nan)
        new_df["sent_zscore_20"] = (
            ((s - s.rolling(20, min_periods=5).mean()) / sent_std_20)
            .fillna(0)
            .clip(-3, 3)
        )
        # 2. 情绪加速度 (ma5 的变化率, 捕捉情绪拐点)
        new_df["sent_acceleration"] = new_df["sent_ma5"].diff(3).fillna(0)
        # 3. 情绪-价格交互 (情绪动量 × 收益率方向, 捕捉共振)
        ret_1d = new_df["close"].pct_change().fillna(0)
        new_df["sent_price_interaction"] = (
            (new_df["sent_mom5"] * np.sign(ret_1d)).fillna(0)
        )
        out[code] = new_df
    return out
