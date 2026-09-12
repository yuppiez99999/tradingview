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
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from utils.datetime_utils import now_bj

logger = logging.getLogger("lgb_enhanced")


# ============================================================
# 路径/配置 (由主模块注入)
# ============================================================
BASE_DIR: Path = Path(__file__).resolve().parent.parent
CACHE_DIR: Path = BASE_DIR / "cache" / "ohlcv"
LGB_ENHANCED_CONFIG: dict[str, Any] = {
    "news_cache_hours": 6,
    "news_lookback_days": 250,  # v4.1: 30→250天, 匹配 OHLCV 回看周期, 确保滚动特征有统计意义
}


def configure_paths(base_dir: Path, cache_dir: Path, config: dict[str, Any]) -> None:
    """由主模块注入路径和配置。"""
    global BASE_DIR, CACHE_DIR, LGB_ENHANCED_CONFIG
    BASE_DIR = base_dir
    CACHE_DIR = cache_dir
    LGB_ENHANCED_CONFIG = config


# ============================================================
# 分级加权情感词典 (v4: 从平权关键词升级为三级权重 + 多词短语)
# ============================================================
# 设计原则:
#   强信号 (1.0): 对公司价值有直接、重大影响的事件 (回购/涨停/退市/暴雷等)
#   中信号 (0.6): 中等重要但需要更多上下文验证 (增长/突破/下滑/诉讼等)
#   弱信号 (0.3): 方向性提示但单独不足以决策 (提升/改善/承压/回落等)
#
# 多词短语优先匹配 (如 "创历史新高" 优先于单独的 "新高" 避免重复计数)
# 权重标准化: 每条新闻总分 = sum(weight) / max(1, log2(1 + matched_count))
#   以此避免 "堆砌关键词的垃圾新闻" 获得虚高分数
# ============================================================

NEWS_KEYWORDS_POSITIVE: dict[str, float] = {
    # ── 强利好 (1.0) ──
    "回购": 1.0,
    "涨停": 1.0,
    "超预期": 1.0,
    "创历史新高": 1.0,
    "业绩大增": 1.0,
    "营收增长": 0.9,
    "净利增长": 0.9,
    "毛利率提升": 0.9,
    # ── 中等利好 (0.6) ──
    "预增": 0.8,
    "中标": 0.8,
    "增持": 0.7,
    "突破": 0.7,
    "新高": 0.7,
    "盈利": 0.6,
    "扩产": 0.6,
    "订单": 0.6,
    "合作": 0.6,
    "放量": 0.6,
    "增长": 0.5,
    "景气": 0.5,
    "上调": 0.6,
    "龙头": 0.6,
    "技术领先": 0.6,
    "股权激励": 0.7,
    "并购": 0.7,
    "重组": 0.7,
    "获批": 0.6,
    # ── 弱利好 (0.3) ──
    "提升": 0.3,
    "改善": 0.3,
    "研发": 0.3,
    "向好": 0.3,
    "成长": 0.3,
    "启动": 0.3,
    "上线": 0.3,
    "推出": 0.3,
    "入选": 0.3,
    "份额提升": 0.4,
    "投产": 0.3,
    "出海": 0.4,
    "强势": 0.3,
    "推荐": 0.2,
    "加仓": 0.3,
    "战略协议": 0.5,
    "护城河": 0.4,
    "补贴": 0.4,
    "首破": 0.5,
}

NEWS_KEYWORDS_NEGATIVE: dict[str, float] = {
    # ── 强利空 (1.0) ──
    "退市": 1.0,
    "跌停": 1.0,
    "暴雷": 1.0,
    "爆雷": 1.0,
    "资金链断裂": 1.0,
    "债务危机": 1.0,
    "业绩大降": 0.9,
    "不及预期": 0.9,
    # ── 中等利空 (0.6) ──
    "亏损": 0.8,
    "处罚": 0.8,
    "减持": 0.7,
    "诉讼": 0.7,
    "违规": 0.7,
    "暴跌": 0.7,
    "下滑": 0.6,
    "下降": 0.5,
    "萎缩": 0.6,
    "重挫": 0.7,
    "质押": 0.6,
    "问询": 0.6,
    "调查": 0.7,
    "立案": 0.8,
    "风险": 0.5,
    "警告": 0.6,
    "破发": 0.5,
    "破净": 0.5,
    "过剩": 0.5,
    "利空": 0.6,
    "ST": 0.9,
    "风险警示": 0.8,
    "停牌核查": 0.7,
    # ── 弱利空 (0.3) ──
    "承压": 0.3,
    "走弱": 0.3,
    "回落": 0.3,
    "下跌": 0.3,
    "断供": 0.4,
    "降价": 0.3,
    "下调": 0.4,
    "经营困难": 0.5,
    "高管辞职": 0.5,
    "预减": 0.8,  # 业绩预告亏损属于强信号
}

# 新闻发布时间日期格式
_PUB_TIME_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y年%m月%d日",
)


# ============================================================
# 缓存
# ============================================================
def _load_news_sentiment_cache(cache_file: Path) -> dict[str, pd.DataFrame] | None:
    """加载新闻情绪因子缓存。

    Args:
        cache_file: 缓存文件路径

    Returns:
        缓存的 DataFrame 字典, 失败返回 None
    """
    try:
        with open(cache_file, encoding="utf-8") as f:
            cache = json.load(f)
        return _sentiment_cache_to_df(cache)
    except Exception as e:
        logger.warning(f"加载新闻情绪缓存失败: {e}", exc_info=True)
        return None


def _save_news_sentiment_cache(
    cache_file: Path, sentiment_dict: dict[str, pd.DataFrame]
) -> None:
    """保存新闻情绪因子到缓存文件 (v4: 含 news_count)。"""
    try:
        cache_data = {
            code: {
                "sentiment_score": [
                    round(float(v), 4) for v in df["sentiment_score"].tolist()
                ],
                "news_count": [int(v) for v in df["news_count"].tolist()],
                "index": [str(d) for d in df.index],
            }
            for code, df in sentiment_dict.items()
        }
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"保存新闻情绪缓存失败: {e}", exc_info=True)


def _sentiment_cache_to_df(cache: dict) -> dict[str, pd.DataFrame]:
    """缓存转 DataFrame (v4: 含 news_count)。"""
    out: dict[str, pd.DataFrame] = {}
    for code, data in cache.items():
        try:
            dates = pd.to_datetime(data["index"])
            df = pd.DataFrame(
                {
                    "sentiment_score": data["sentiment_score"],
                    "news_count": data.get(
                        "news_count", [0] * len(data["sentiment_score"])
                    ),
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
def _init_news_data_sources() -> tuple[Callable | None, Any | None]:
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
    wind_search_news_fn: Callable | None,
    ifind_analyzer: Any | None,
    lookback_days: int,
) -> tuple[list[tuple[str, str, str]], str]:
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
    normalized_news: list[tuple[str, str, str]] = []
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
def _parse_news_date(pub_time: str) -> pd.Timestamp | None:
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


# ── v4 加权情感函数 ──


def _score_article_weighted(article: dict[str, str]) -> dict[str, Any]:
    """分级加权单条新闻情感打分 (v4 升级)。

    与旧版 (pos-neg)/(pos+neg) 的核心区别:
      - 用权重替代了简单计数: "回购"(1.0) 的信号强度是 "提升"(0.3) 的 3 倍
      - 对数衰减防堆砌: 一条新闻堆 20 个弱利好词不应获得虚高分数
      - 区间去重: 多词短语如 "创历史新高" 匹配后, "新高" 不再重复计数
      - 信号强度标记: strong/medium/weak/none 可用于因子加权

    返回:
        {"score": float, "weighted_pos": float, "weighted_neg": float,
         "matched_count": int, "signal_strength": str}
    """
    title = article.get("title", "")
    snippet = article.get("snippet", "")
    text = f"{title} {snippet}"

    weighted_pos = 0.0
    weighted_neg = 0.0
    matched_spans: set[tuple[int, int]] = set()

    # 长短语优先匹配 (避免 "创历史新高" 被拆成 "历史" + "新高")
    pos_items = sorted(NEWS_KEYWORDS_POSITIVE.items(), key=lambda x: -len(x[0]))
    neg_items = sorted(NEWS_KEYWORDS_NEGATIVE.items(), key=lambda x: -len(x[0]))

    for kw, weight in pos_items:
        idx = text.find(kw)
        while idx != -1:
            span = (idx, idx + len(kw))
            if span not in matched_spans:
                weighted_pos += weight
                matched_spans.add(span)
            idx = text.find(kw, idx + 1)

    for kw, weight in neg_items:
        idx = text.find(kw)
        while idx != -1:
            span = (idx, idx + len(kw))
            if span not in matched_spans:
                weighted_neg += weight
                matched_spans.add(span)
            idx = text.find(kw, idx + 1)

    match_count = len(matched_spans)
    if match_count == 0:
        return {
            "score": 0.0,
            "weighted_pos": 0.0,
            "weighted_neg": 0.0,
            "matched_count": 0,
            "signal_strength": "none",
        }

    # 对数衰减: 10 个关键词的信息量 ≠ 1 个关键词的 10 倍
    decay = max(1.0, np.log2(1 + match_count))
    net = (weighted_pos - weighted_neg) / decay
    max_val = max(weighted_pos + weighted_neg, decay) / decay
    score = np.clip(net / max_val, -1.0, 1.0) if max_val > 0 else 0.0

    abs_net = abs(weighted_pos - weighted_neg)
    if abs_net >= 1.5:
        strength = "strong"
    elif abs_net >= 0.5:
        strength = "medium"
    else:
        strength = "weak"

    return {
        "score": round(float(score), 4),
        "weighted_pos": round(weighted_pos, 4),
        "weighted_neg": round(weighted_neg, 4),
        "matched_count": match_count,
        "signal_strength": strength,
    }


def _aggregate_daily_sentiment_v4(
    normalized_news: list[tuple[str, str, str]],
    dates: pd.DatetimeIndex,
    lookback_days: int,
) -> tuple[np.ndarray, np.ndarray]:
    """v4 按日聚合新闻情感 (分级加权)。

    Returns:
        (daily_sent_score, daily_news_count) — 每日期望情感分和新闻数
    """
    # 每条新闻的加权分
    daily_scores_sum = np.zeros(lookback_days)
    daily_weights = np.zeros(lookback_days)  # 总匹配权重用于加权平均
    daily_news_count = np.zeros(lookback_days, dtype=int)

    for title, snippet, pub_time in normalized_news:
        pub_date = _parse_news_date(pub_time)
        if pub_date is None:
            continue

        idx = dates.searchsorted(pub_date.normalize())
        if idx >= lookback_days:
            continue
        if dates[idx] != pub_date.normalize():
            if idx > 0 and dates[idx - 1] == pub_date.normalize():
                idx = idx - 1
            else:
                continue

        result = _score_article_weighted({"title": title, "snippet": snippet})
        daily_news_count[idx] += 1

        if result["matched_count"] > 0:
            # 用匹配关键词数作为置信度权重
            conf = result["matched_count"]
            daily_scores_sum[idx] += result["score"] * conf
            daily_weights[idx] += conf

    # 按日加权平均
    daily_sent = np.zeros(lookback_days)
    for i in range(lookback_days):
        if daily_weights[i] > 0:
            daily_sent[i] = daily_scores_sum[i] / daily_weights[i]

    return daily_sent, daily_news_count


def _compute_sentiment_scores_v4(
    daily_sent: np.ndarray,
    daily_news_count: np.ndarray,
    lookback_days: int,
) -> np.ndarray:
    """v4.2: 哨兵值替代合成填充 — 无新闻日填 -999, LightGBM 原生分裂。

    核心原则:
      - 97% 天数为合成的前向填充值 → 模型正确识别为噪声
      - 解决: 无新闻日设为 -999 (哨兵, 远低于正常情绪范围 [-1,1])
      - LightGBM 将 -999 作为独立分裂节点, 等价于 \"有新闻 vs 无新闻\"
      - has_news 二值特征配合使用, 双重编码
    """
    result = daily_sent.copy().astype(float)
    for i in range(lookback_days):
        if daily_news_count[i] == 0:
            result[i] = -999.0
    return result


# ============================================================
# 主入口
# ============================================================
def compute_news_sentiment_factors(
    symbols: list[tuple],
    lookback_days: int = 250,
    use_cache: bool = True,
) -> dict[str, pd.DataFrame]:
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
    cache_file = CACHE_DIR / f"news_sentiment_v2_{now_bj():%Y%m%d}.json"

    # 1. 尝试加载缓存
    if use_cache and cache_file.exists():
        mtime = datetime.fromtimestamp(cache_file.stat().st_mtime)
        age_hours = (now_bj() - mtime).total_seconds() / 3600
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

    sentiment_dict: dict[str, pd.DataFrame] = {}
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

            # v4 按日聚合 (分级加权) + 计算平滑情绪分
            daily_sent, daily_news_count = _aggregate_daily_sentiment_v4(
                normalized_news, dates, lookback_days
            )
            daily_scores = _compute_sentiment_scores_v4(
                daily_sent, daily_news_count, lookback_days
            )

            df = pd.DataFrame(
                {
                    "sentiment_score": daily_scores,
                    "news_count": daily_news_count.astype(int),
                },
                index=dates,
            )
            df.index.name = "date"
            sentiment_dict[code] = df

            total_news = int(daily_news_count.sum())
            pos_days = int((daily_scores > 0.05).sum())
            neg_days = int((daily_scores < -0.05).sum())
            logger.info(
                f"  {code} ({name}) [{source}]: 总新闻={total_news}, "
                f"利好日(>0.05)={pos_days}, 利空日(<-0.05)={neg_days}, "
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
    ohlcv_dict: dict[str, pd.DataFrame],
    sentiment_dict: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """v4.2: NaN原生缺失 + 市场聚合情绪指数。

    核心重构:
      不再合成填充 → 让 LightGBM 原生处理 NaN (作为独立分裂方向)
      新增 market_sentiment: 全市场聚合情绪指数 (每日有值, ~60%+ 覆盖率)
      个股仅保留 has_news (二值) + news_sentiment_raw (NaN on 无新闻日)

    因子列表 (v4.2):
        market_sentiment      — 全市场聚合情绪指数 (宏观情绪/风险偏好)
        market_sent_change    — 市场情绪5日变化 (情绪拐点)
        has_news              — 当日是否有该股相关新闻 (0/1, 关注度代理)
        news_sentiment_raw    — 新闻日情绪分 (NaN on 无新闻日, LightGBM native)
    """
    out: dict[str, pd.DataFrame] = {}

    # ── Step 1: 构建全市场聚合情绪指数 ──
    market_sent_series: pd.Series | None = None
    if sentiment_dict:
        all_scores = {}
        all_news = {}
        for code, sdf in sentiment_dict.items():
            all_scores[code] = sdf["sentiment_score"]
            all_news[code] = sdf["news_count"]
        if all_scores:
            scores_df = pd.DataFrame(all_scores)
            news_df = pd.DataFrame(all_news)
            # 每日有新闻的标的数
            (news_df > 0).sum(axis=1)
            # 市场情绪 = 当日所有有新闻标的的情绪均值 (仅用真实新闻日)
            market_raw = scores_df.where(news_df > 0)  # 无新闻日 NaN
            market_sent_series = market_raw.mean(axis=1, skipna=True)
            # 市场情绪5日变化
            market_sent_change = market_sent_series.diff(5)
            # EMA 平滑填充无新闻的市场日 (市场层面, 有宏观意义)
            market_sent_series = market_sent_series.ffill(limit=5).fillna(0)
            market_sent_change = market_sent_change.fillna(0).clip(-0.5, 0.5)

    # ── Step 2: 逐标的构建特征 ──
    for code, df in ohlcv_dict.items():
        new_df = df.copy()
        n = len(new_df)

        # ── 宏观情绪 (所有标的共享) ──
        if market_sent_series is not None:
            aligned = market_sent_series.reindex(new_df.index).fillna(0)
            new_df["market_sentiment"] = aligned
            if market_sent_change is not None:
                aligned_chg = market_sent_change.reindex(new_df.index).fillna(0)
                new_df["market_sent_change"] = aligned_chg
        else:
            new_df["market_sentiment"] = 0.0
            new_df["market_sent_change"] = 0.0

        # ── 个股新闻信号 ──
        if code in sentiment_dict:
            sdf = sentiment_dict[code].reindex(new_df.index)
            sent_raw = sdf["sentiment_score"].fillna(-999).values  # -999 = 无新闻哨兵
            news_count = sdf["news_count"].values
        else:
            sent_raw = np.full(n, -999.0)
            news_count = np.zeros(n)

        # F3: 是否有新闻 (0/1 二值, 与 sent_raw=-999 形成双重编码)
        has_news = np.where(np.array(news_count) > 0, 1, 0).astype(np.float64)
        new_df["has_news"] = has_news

        # F4: 新闻日情绪分 (无新闻=-999, LightGBM 原生分裂为独立分支)
        new_df["news_sentiment_raw"] = sent_raw

        out[code] = new_df

    return out
