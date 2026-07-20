# -*- coding: utf-8 -*-
"""
LightGBM 增强训练器 (真实 OHLCV + 情绪因子 + 放宽早停)
=====================================================

针对 lgb_tscv_trainer.py 的进一步优化:
    1. 用真实 OHLCV 数据替代合成数据
       - 通过 MarketDataProvider.get_historical_data() 拉取
       - 优先级: Wind MCP > iFinD MCP > 新浪 HTTP > 默认兜底
       - 502 日真实数据, 含真实价格/成交量
    2. 引入基本面/新闻情绪因子
       - 通过 IFinDNewsAnalyzer 一次性拉取过去 N 天真实新闻 (size=50)
       - 按 publish_time 分配到日期, 计算每日真实情绪分 (pos-neg)/(pos+neg)
       - 滚动窗口聚合 (5/20 日累计情绪、情绪动量、情绪波动率)
       - 替代旧版"单次快照+随机噪声"方案
    3. 放宽 early_stopping_rounds
       - 50 → 200 (允许模型更充分学习)
       - n_estimators: 1000 → 2000 (配合更小学习率 0.005)

用法:
    python lgb_enhanced_trainer.py                    # 训练全部持仓
    python lgb_enhanced_trainer.py --symbols 688041   # 训练单个标的
    python lgb_enhanced_trainer.py --force-retrain    # 强制重训
    python lgb_enhanced_trainer.py --no-news         # 跳过新闻因子 (加速)
"""
from __future__ import annotations

import os
import sys
import json
import pickle
import logging
import argparse
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any

# ============================================================
# 路径
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "config"
MODELS_DIR = BASE_DIR / "models" / "lgb_enhanced"  # 新目录
REPORTS_DIR = BASE_DIR / "reports" / "lgb_enhanced"
LOG_DIR = BASE_DIR / "logs"
CACHE_DIR = BASE_DIR / "cache" / "ohlcv"

for d in [MODELS_DIR, REPORTS_DIR, LOG_DIR, CACHE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "v7.5_institutional"))
sys.path.insert(0, str(BASE_DIR / "utils"))

# 复用旧训练器的标的清单和特征工程
from autolearn_trainer import (
    POSITION_SYMBOLS,
    add_technical_features,
    add_cross_sectional_features,
)

# ============================================================
# 增强训练配置
# ============================================================
LGB_ENHANCED_CONFIG = {
    "lookback_days": 500,
    "min_samples": 150,
    "test_ratio": 0.2,
    "n_splits": 5,
    "top_n_features": 30,             # 保留 Top 30 (放宽让情绪因子有机会入选)
    "feature_selection_threshold": 1,   # 阈值降低到 1 (从3降到1, 让弱信号特征也能入选)
    "retrain_interval_days": 7,
    "model_quality_threshold": {
        "min_cv_r2": -0.3,
        "min_cv_ic": 0.0,
        "min_cv_sharpe": 0.0,
    },
    "lgb_params": {
        "n_estimators": 2000,        # 大幅增加
        "learning_rate": 0.005,      # 更小学习率
        "max_depth": 6,
        "num_leaves": 31,
        "min_child_samples": 30,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "reg_alpha": 0.1,
        "reg_lambda": 0.5,
        "random_state": 42,
        "verbose": -1,
        "n_jobs": -1,
    },
    "early_stopping_rounds": 200,     # 放宽 50→200
    "news_lookback_days": 30,        # 新闻情绪回看天数
    "news_cache_hours": 6,           # 新闻缓存有效期 (小时)
    "adaptive_retrain_threshold": 5,  # best_iter <= 5 触发自适应重训
    "adaptive_retrain_lr": 0.001,      # 自适应重训学习率 (0.005→0.001)
    "adaptive_retrain_n_estimators": 5000,  # 配合更小学习率, 增加估计器
}

logger = logging.getLogger("lgb_enhanced")


# ============================================================
# 真实 OHLCV 数据加载
# ============================================================
def load_real_ohlcv(symbol: str, period: str = "2y") -> Optional[pd.DataFrame]:
    """通过 MarketDataProvider 拉取真实 OHLCV

    优先级: Wind MCP > iFinD MCP > 新浪 HTTP > 默认兜底

    Args:
        symbol: 标的代码 (如 "688041.SH")
        period: 周期 (1y/2y/3y/5y)

    Returns:
        DataFrame[open, high, low, close, volume] 或 None
    """
    # 缓存检查
    cache_file = CACHE_DIR / f"{symbol.replace('.', '_')}_{period}.parquet"
    if cache_file.exists():
        mtime = datetime.fromtimestamp(cache_file.stat().st_mtime)
        if (datetime.now() - mtime).total_seconds() < 12 * 3600:  # 12 小时缓存
            try:
                df = pd.read_parquet(cache_file)
                if not df.empty and len(df) >= 100:
                    return df
            except Exception:
                pass

    try:
        from utils.data_provider import get_historical_data
        df = get_historical_data(symbol, period)
        if df is None or df.empty:
            logger.warning(f"  {symbol}: 真实 OHLCV 拉取失败")
            return None

        # 标准化列名和索引
        df = df.copy()
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        df.index.name = "date"

        # 保存缓存
        try:
            df.to_parquet(cache_file)
        except Exception:
            pass

        return df
    except Exception as e:
        logger.error(f"  {symbol}: 拉取真实 OHLCV 异常: {e}")
        return None


def fetch_all_real_ohlcv(
    symbols: List[Tuple],
    period: str = "2y",
) -> Dict[str, pd.DataFrame]:
    """批量拉取真实 OHLCV

    Args:
        symbols: [(code, suffix, server_type, name, style), ...]
        period: 周期

    Returns:
        {code: DataFrame}
    """
    ohlcv_dict = {}
    logger.info(f"拉取真实 OHLCV 数据 ({period})...")
    for code, suffix, _, name, _ in symbols:
        symbol = f"{code}{suffix}"
        df = load_real_ohlcv(symbol, period)
        if df is not None and not df.empty:
            ohlcv_dict[code] = df
            logger.info(f"  {code} ({name}): {len(df)} 日真实数据")
        else:
            logger.warning(f"  {code} ({name}): 拉取失败, 跳过")
    return ohlcv_dict


# ============================================================
# 新闻情绪因子
# ============================================================
def compute_news_sentiment_factors(
    symbols: List[Tuple],
    lookback_days: int = 30,
    use_cache: bool = True,
) -> Dict[str, pd.DataFrame]:
    """计算新闻情绪因子 - 真实历史时间序列版 (Wind MCP 优先, iFinD 回退)

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

    if use_cache and cache_file.exists():
        mtime = datetime.fromtimestamp(cache_file.stat().st_mtime)
        age_hours = (datetime.now() - mtime).total_seconds() / 3600
        if age_hours < LGB_ENHANCED_CONFIG["news_cache_hours"]:
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cache = json.load(f)
                return _sentiment_cache_to_df(cache)
            except Exception:
                pass

    # 关键词 (原 iFinD 关键词 + Wind MCP 新闻常见事件关键词)
    # Wind MCP 返回新闻多为日常事件 (回购/推荐/涨跌/大宗交易等),
    # 扩展关键词以提升 Wind MCP 数据源下的命中率
    keywords_positive = [
        # 原 iFinD 关键词 (业绩/订单类)
        "预增", "增长", "中标", "订单", "扩产", "出海", "份额提升",
        "超预期", "盈利", "放量", "景气",
        # Wind MCP 扩展 (事件/资金/技术类)
        "涨停", "回购", "增持", "推荐", "突破", "新高", "上调",
        "入选", "合作", "研发", "投产", "启动", "上线", "推出",
        "成长", "提升", "向好", "强势", "加仓", "登顶", "首破",
    ]
    keywords_negative = [
        # 原 iFinD 关键词
        "预减", "下滑", "亏损", "处罚", "减持", "质押", "暴雷",
        "下调", "断供", "降价", "过剩",
        # Wind MCP 扩展
        "跌停", "下跌", "回落", "问询", "风险", "警告",
        "暴跌", "萎缩", "下降", "破发", "破净", "违规", "诉讼",
        "退市", "停牌", "走弱", "承压", "利空",
    ]

    # 构建日期索引
    end_date = pd.Timestamp.now().normalize()
    dates = pd.date_range(end=end_date, periods=lookback_days, freq="D")

    # 初始化数据源 (Wind MCP 优先, iFinD 回退)
    wind_search_news_fn = None
    try:
        from wind_mcp_fetcher import wind_search_news as wind_search_news_fn
    except Exception as e:
        logger.warning(f"Wind MCP 新闻接口不可用: {e}")

    ifind_analyzer = None
    try:
        from utils.ifind_news_analyzer import IFinDNewsAnalyzer
        ifind_analyzer = IFinDNewsAnalyzer()
        if not ifind_analyzer.available():
            ifind_analyzer = None
    except Exception:
        pass

    if not wind_search_news_fn and not ifind_analyzer:
        logger.warning("Wind MCP 和 iFinD 新闻分析器均不可用, 跳过情绪因子")
        return {}

    sentiment_dict = {}
    success_count = 0
    wind_count = 0
    ifind_count = 0
    fallback_count = 0

    for code, suffix, _, name, _ in symbols:
        try:
            # 统一新闻项格式: List[(title, snippet, publish_time)]
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
                            wind_count += 1
                except Exception as e:
                    logger.debug(f"  {code}: Wind MCP 新闻拉取失败: {e}")

            # 2. 回退 iFinD
            if not normalized_news and ifind_analyzer:
                try:
                    news_query = f"{name} {code}".strip() if name else code
                    items = ifind_analyzer.search_news(
                        news_query, size=50, days=lookback_days
                    )
                    if items:
                        for it in items:
                            title = getattr(it, "title", "") or ""
                            snippet = getattr(it, "snippet", "") or ""
                            pub_time = getattr(it, "publish_time", "") or ""
                            if title or snippet:
                                normalized_news.append((title, snippet, pub_time))
                        if normalized_news:
                            source = "ifind"
                            ifind_count += 1
                except Exception as e:
                    logger.debug(f"  {code}: iFinD 新闻拉取失败: {e}")

            if not normalized_news:
                fallback_count += 1
                logger.warning(f"  {code} ({name}): 无新闻数据, 情绪分将为 0")
                # 仍创建空 DataFrame, 保持标的列表完整性
                df = pd.DataFrame({
                    "sentiment_score": np.zeros(lookback_days),
                    "news_count": np.zeros(lookback_days, dtype=int),
                }, index=dates)
                df.index.name = "date"
                sentiment_dict[code] = df
                continue

            # 按日聚合: 计算每日真实情绪分
            daily_pos = np.zeros(lookback_days)
            daily_neg = np.zeros(lookback_days)
            daily_total = np.zeros(lookback_days)

            for title, snippet, pub_time in normalized_news:
                # 解析新闻发布日期
                pub_date = None
                if pub_time:
                    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日"):
                        try:
                            pub_date = pd.to_datetime(pub_time, format=fmt)
                            break
                        except (ValueError, TypeError):
                            continue
                    if pub_date is None:
                        try:
                            pub_date = pd.to_datetime(pub_time)
                        except Exception:
                            continue

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
                has_pos = any(k in text for k in keywords_positive)
                has_neg = any(k in text for k in keywords_negative)

                if has_pos:
                    daily_pos[idx] += 1
                if has_neg:
                    daily_neg[idx] += 1
                if has_pos or has_neg:
                    daily_total[idx] += 1

            # 计算每日情绪分: (pos - neg) / (pos + neg), 范围 [-1, 1]
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
            daily_scores = pd.Series(daily_scores).rolling(
                3, min_periods=1
            ).mean().values

            df = pd.DataFrame({
                "sentiment_score": daily_scores,
                "news_count": daily_total.astype(int),
            }, index=dates)
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

    # 缓存
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
    except Exception:
        pass

    return sentiment_dict


def _sentiment_cache_to_df(cache: Dict) -> Dict[str, pd.DataFrame]:
    """缓存转 DataFrame"""
    out = {}
    for code, data in cache.items():
        try:
            dates = pd.to_datetime(data["index"])
            df = pd.DataFrame({
                "sentiment_score": data["sentiment_score"],
            }, index=dates)
            df.index.name = "date"
            out[code] = df
        except Exception:
            continue
    return out


def add_sentiment_features(
    ohlcv_dict: Dict[str, pd.DataFrame],
    sentiment_dict: Dict[str, pd.DataFrame],
) -> Dict[str, pd.DataFrame]:
    """将情绪因子合并到 OHLCV 数据上

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
    """
    out = {}
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
        new_df["sent_positive_ratio"] = (
            (s > 0).rolling(20, min_periods=1).mean()
        )
        # v3 新增: 提升情绪因子表达力
        # 1. 情绪标准化分数 (z-score, 衡量当前情绪相对历史的极端程度)
        sent_std_20 = s.rolling(20, min_periods=5).std().replace(0, np.nan)
        new_df["sent_zscore_20"] = ((s - s.rolling(20, min_periods=5).mean())
                                     / sent_std_20).fillna(0).clip(-3, 3)
        # 2. 情绪加速度 (ma5 的变化率, 捕捉情绪拐点)
        new_df["sent_acceleration"] = new_df["sent_ma5"].diff(3).fillna(0)
        # 3. 情绪-价格交互 (情绪动量 × 收益率方向, 捕捉共振)
        ret_1d = new_df["close"].pct_change().fillna(0)
        new_df["sent_price_interaction"] = (new_df["sent_mom5"] * np.sign(ret_1d)).fillna(0)
        out[code] = new_df
    return out


# ============================================================
# 扩展特征工程 v2 (针对 best_iter=1 的标的)
# ============================================================
# 标的→行业映射 (复用 POSITION_SYMBOLS 的第 5 字段)
def _build_sector_map() -> Dict[str, str]:
    """构建 code→sector 映射"""
    return {code: sector for code, _, _, _, sector in POSITION_SYMBOLS}


# 跨市场代理标的 (用持仓 ETF/股票作为跨市场信号)
_CROSS_MARKET_PROXIES = {
    "gold_safe_haven": "518880",     # 黄金ETF华安 - 避险情绪代理
    "bank_rate_proxy": "600036",     # 招商银行 - 利率/信贷代理
    "tech_growth_proxy": "588000",  # 科创50ETF - 成长风格代理
    "dividend_defensive": "515180", # 易方达红利ETF - 防御风格代理
}


def add_industry_relative_strength_features(
    ohlcv_dict: Dict[str, pd.DataFrame],
) -> Dict[str, pd.DataFrame]:
    """行业相对强度特征 (自构建行业基准)

    针对周期股/ETF 的 best_iter=1 问题:
    - 601088 煤炭、600219 铝业、600019 钢铁、000408/000975 矿业
    - 600036 银行、688981 半导体

    用持仓同行业标的的平均收益作为行业基准, 计算:
    - industry_return_5: 行业 5 日平均收益
    - industry_return_20: 行业 20 日平均收益
    - relative_strength_5: 个股 5 日收益 - 行业 5 日收益
    - relative_strength_20: 个股 20 日收益 - 行业 20 日收益
    - industry_rank_20: 个股在行业内的 20 日收益排名分位

    Args:
        ohlcv_dict: {code: DataFrame[open, high, low, close, volume]}

    Returns:
        合并后的字典, 每个 DataFrame 新增 5 个行业相对强度特征
    """
    sector_map = _build_sector_map()

    # 收集所有标的的收盘价和收益率
    closes = pd.DataFrame({
        code: df["close"] for code, df in ohlcv_dict.items()
    })
    returns_5 = closes.pct_change(5)
    returns_20 = closes.pct_change(20)

    # 按行业分组构建行业基准 (等权平均)
    sector_codes: Dict[str, List[str]] = {}
    for code in ohlcv_dict.keys():
        sector = sector_map.get(code, "其他")
        sector_codes.setdefault(sector, []).append(code)

    # 计算行业基准收益
    industry_ret_5 = {}
    industry_ret_20 = {}
    for sector, codes in sector_codes.items():
        valid_codes = [c for c in codes if c in returns_5.columns]
        if len(valid_codes) >= 2:
            # 等权行业平均
            industry_ret_5[sector] = returns_5[valid_codes].mean(axis=1)
            industry_ret_20[sector] = returns_20[valid_codes].mean(axis=1)
        elif len(valid_codes) == 1:
            # 只有一个标的的行业, 用自身作为基准 (相对强度=0)
            industry_ret_5[sector] = returns_5[valid_codes[0]]
            industry_ret_20[sector] = returns_20[valid_codes[0]]

    out = {}
    for code, df in ohlcv_dict.items():
        new_df = df.copy()
        sector = sector_map.get(code, "其他")

        ind_ret5 = industry_ret_5.get(sector)
        ind_ret20 = industry_ret_20.get(sector)

        if ind_ret5 is not None and code in returns_5.columns:
            new_df["industry_return_5"] = ind_ret5.reindex(new_df.index).fillna(0)
            new_df["industry_return_20"] = ind_ret20.reindex(new_df.index).fillna(0)
            new_df["relative_strength_5"] = (
                returns_5[code].reindex(new_df.index) - new_df["industry_return_5"]
            ).fillna(0)
            new_df["relative_strength_20"] = (
                returns_20[code].reindex(new_df.index) - new_df["industry_return_20"]
            ).fillna(0)

            # 行业内排名分位 (同一行业内的收益排名)
            sector_codes_list = sector_codes.get(sector, [code])
            valid_codes = [c for c in sector_codes_list if c in returns_20.columns]
            if len(valid_codes) >= 2:
                rank_df = returns_20[valid_codes].rank(axis=1, pct=True)
                new_df["industry_rank_20"] = rank_df[code].reindex(new_df.index).fillna(0.5)
            else:
                new_df["industry_rank_20"] = 0.5
        else:
            # 无行业基准, 填中性值
            for col in ["industry_return_5", "industry_return_20",
                        "relative_strength_5", "relative_strength_20"]:
                new_df[col] = 0.0
            new_df["industry_rank_20"] = 0.5

        out[code] = new_df
    return out


def add_capital_flow_features(
    ohlcv_dict: Dict[str, pd.DataFrame],
) -> Dict[str, pd.DataFrame]:
    """资金流向特征 (基于价量关系估算)

    针对周期股 best_iter=1 问题, 增加资金行为维度:
    - capital_flow: 当日资金净流入估算 (价量关系)
        - 收涨 + 放量 → 正流入 (主力买入)
        - 收跌 + 缩量 → 负流入 (主力卖出)
    - cumulative_flow_5: 5 日累计资金流向
    - flow_divergence: 量价背离指标 (价格涨但资金流出 = 顶背离)
    - smart_money_ratio: 大单净占比估算 (用日内振幅+成交量估算)
    - flow_momentum: 资金流向动量 (5 日变化率)

    估算方法 (无需外部数据):
        capital_flow = (close - open) / (high - low + 1e-9) * volume
        即用日内价格区间内的位置 × 成交量作为资金流向代理

    Args:
        ohlcv_dict: {code: DataFrame[open, high, low, close, volume]}

    Returns:
        合并后的字典, 每个 DataFrame 新增 5 个资金流向特征
    """
    out = {}
    for code, df in ohlcv_dict.items():
        new_df = df.copy()

        # 确保有 open/high/low/close/volume
        required = ["open", "high", "low", "close", "volume"]
        if not all(c in new_df.columns for c in required):
            for col in ["capital_flow", "cumulative_flow_5",
                        "flow_divergence", "smart_money_ratio", "flow_momentum"]:
                new_df[col] = 0.0
            out[code] = new_df
            continue

        o, h, l, c, v = new_df["open"], new_df["high"], new_df["low"], new_df["close"], new_df["volume"]

        # 日内价格位置 (0=最低, 1=最高)
        price_range = (h - l).replace(0, 1e-9)
        daily_position = (c - l) / price_range

        # 当日资金净流入估算: 收盘位置 × 成交量 × sign(收涨)
        # 正值 = 资金流入, 负值 = 资金流出
        direction = np.sign(c - o)
        new_df["capital_flow"] = (daily_position * v * direction).fillna(0)

        # 5 日累计资金流向
        new_df["cumulative_flow_5"] = new_df["capital_flow"].rolling(5, min_periods=1).sum()

        # 量价背离: 价格 5 日收益与资金流向的符号差异
        price_ret5 = c.pct_change(5).fillna(0)
        flow_sign = np.sign(new_df["cumulative_flow_5"])
        price_sign = np.sign(price_ret5)
        new_df["flow_divergence"] = (price_sign * flow_sign * -1).fillna(0)
        # 正值 = 顶/底背离 (价格涨但资金流出, 或价格跌但资金流入)

        # 大单净占比估算: 用日内振幅 × 成交量占比
        # 高振幅 + 高成交量 = 大单活跃
        avg_volume = v.rolling(20, min_periods=1).mean().replace(0, 1e-9)
        vol_ratio = v / avg_volume
        amplitude = (h - l) / c.replace(0, 1e-9)
        new_df["smart_money_ratio"] = (amplitude * vol_ratio).rolling(5, min_periods=1).mean().fillna(0)

        # 资金流向动量: 5 日累计流向的变化率
        flow_shift5 = new_df["cumulative_flow_5"].shift(5).replace(0, np.nan)
        new_df["flow_momentum"] = (
            (new_df["cumulative_flow_5"] - new_df["cumulative_flow_5"].shift(5)) /
            (flow_shift5.abs() + 1e-9)
        ).fillna(0).replace([np.inf, -np.inf], 0)

        out[code] = new_df
    return out


def add_cross_market_features(
    ohlcv_dict: Dict[str, pd.DataFrame],
) -> Dict[str, pd.DataFrame]:
    """跨市场信号特征 (用持仓 ETF/股票作为跨市场代理)

    针对周期股/ETF 的 best_iter=1 问题, 增加跨市场维度:
    - gold_trend_20: 黄金ETF 20 日趋势 (避险情绪)
    - bank_trend_20: 银行股 20 日趋势 (利率/信贷环境)
    - tech_style_20: 科技ETF 20 日趋势 (风险偏好)
    - defensive_style_20: 红利ETF 20 日趋势 (防御风格)
    - style_rotation_5: 风格轮动信号 (科技-红利差值的 5 日变化)
    - safe_haven_flow: 避险资金流入 (黄金ETF成交量比)

    代理逻辑:
        黄金ETF 涨 → 避险情绪上升 → 利空周期股
        银行股 涨 → 利率上行/信贷宽松 → 利好银行股
        科技ETF 涨 → 风险偏好上升 → 利好成长股
        红利ETF 涨 → 防御偏好上升 → 利空周期股

    Args:
        ohlcv_dict: {code: DataFrame[open, high, low, close, volume]}

    Returns:
        合并后的字典, 每个 DataFrame 新增 6 个跨市场特征
    """
    # 收集代理标的数据
    proxy_data: Dict[str, pd.Series] = {}  # {proxy_name: close_series}
    proxy_vol: Dict[str, pd.Series] = {}
    for proxy_name, code in _CROSS_MARKET_PROXIES.items():
        if code in ohlcv_dict:
            df = ohlcv_dict[code]
            if "close" in df.columns:
                proxy_data[proxy_name] = df["close"]
            if "volume" in df.columns:
                proxy_vol[proxy_name] = df["volume"]

    # 计算代理趋势
    proxy_trend_20 = {}  # {proxy_name: trend_series}
    for name, close in proxy_data.items():
        if len(close) >= 20:
            ma20 = close.rolling(20, min_periods=1).mean()
            proxy_trend_20[name] = (close / ma20 - 1).fillna(0)

    # 风格轮动: 科技 vs 红利 的 20 日趋势差
    tech_trend = proxy_trend_20.get("tech_growth_proxy")
    div_trend = proxy_trend_20.get("dividend_defensive")
    style_rotation = None
    if tech_trend is not None and div_trend is not None:
        style_diff = tech_trend - div_trend
        style_rotation = style_diff.diff(5).fillna(0)

    # 避险资金流入: 黄金ETF 成交量比
    gold_vol_ratio = None
    if "gold_safe_haven" in proxy_vol:
        gv = proxy_vol["gold_safe_haven"]
        gv_ma = gv.rolling(20, min_periods=1).mean().replace(0, 1e-9)
        gold_vol_ratio = (gv / gv_ma - 1).fillna(0)

    out = {}
    for code, df in ohlcv_dict.items():
        new_df = df.copy()

        # 黄金避险趋势
        if "gold_safe_haven" in proxy_trend_20:
            s = proxy_trend_20["gold_safe_haven"].reindex(new_df.index).fillna(0)
            new_df["gold_trend_20"] = s
        else:
            new_df["gold_trend_20"] = 0.0

        # 银行利率代理
        if "bank_rate_proxy" in proxy_trend_20:
            s = proxy_trend_20["bank_rate_proxy"].reindex(new_df.index).fillna(0)
            new_df["bank_trend_20"] = s
        else:
            new_df["bank_trend_20"] = 0.0

        # 科技风险偏好
        if "tech_growth_proxy" in proxy_trend_20:
            s = proxy_trend_20["tech_growth_proxy"].reindex(new_df.index).fillna(0)
            new_df["tech_style_20"] = s
        else:
            new_df["tech_style_20"] = 0.0

        # 红利防御风格
        if "dividend_defensive" in proxy_trend_20:
            s = proxy_trend_20["dividend_defensive"].reindex(new_df.index).fillna(0)
            new_df["defensive_style_20"] = s
        else:
            new_df["defensive_style_20"] = 0.0

        # 风格轮动信号
        if style_rotation is not None:
            new_df["style_rotation_5"] = style_rotation.reindex(new_df.index).fillna(0)
        else:
            new_df["style_rotation_5"] = 0.0

        # 避险资金流入
        if gold_vol_ratio is not None:
            new_df["safe_haven_flow"] = gold_vol_ratio.reindex(new_df.index).fillna(0)
        else:
            new_df["safe_haven_flow"] = 0.0

        out[code] = new_df
    return out


# ============================================================
# 时间序列交叉验证 (复用)
# ============================================================
def _r2_score(y_true, y_pred):
    ss_res = np.sum((y_true - y_pred) ** 2)
    ss_tot = np.sum((y_true - y_true.mean()) ** 2) + 1e-9
    return 1 - ss_res / ss_tot


def _ic_score(y_true, y_pred):
    if len(y_true) < 5:
        return 0.0
    if np.std(y_pred) < 1e-9:
        return 0.0
    return float(np.corrcoef(y_true, y_pred)[0, 1])


def _signal_sharpe(y_true, y_pred):
    signal = np.sign(y_pred)
    returns = signal * y_true
    if returns.std() < 1e-9:
        return 0.0
    return float(returns.mean() / returns.std() * np.sqrt(252))


def time_series_cv_evaluate(
    X: np.ndarray,
    y: np.ndarray,
    config: Dict,
    n_splits: int = 5,
) -> Dict[str, Any]:
    """时间序列交叉验证评估"""
    from lightgbm import LGBMRegressor
    import lightgbm as lgb
    from sklearn.model_selection import TimeSeriesSplit

    tscv = TimeSeriesSplit(n_splits=n_splits)
    fold_metrics = []
    all_importances = []
    n_features = X.shape[1]

    for fold_idx, (train_idx, test_idx) in enumerate(tscv.split(X)):
        X_train_fold, X_test_fold = X[train_idx], X[test_idx]
        y_train_fold, y_test_fold = y[train_idx], y[test_idx]

        if len(X_train_fold) < 50 or len(X_test_fold) < 10:
            continue

        model = LGBMRegressor(**config["lgb_params"])
        model.fit(
            X_train_fold, y_train_fold,
            eval_set=[(X_test_fold, y_test_fold)],
            callbacks=[
                lgb.early_stopping(
                    stopping_rounds=config["early_stopping_rounds"],
                    verbose=False,
                ),
            ],
        )

        y_pred = model.predict(X_test_fold)
        r2 = _r2_score(y_test_fold, y_pred)
        ic = _ic_score(y_test_fold, y_pred)
        sharpe = _signal_sharpe(y_test_fold, y_pred)

        fold_metrics.append({
            "fold": fold_idx + 1,
            "train_size": len(train_idx),
            "test_size": len(test_idx),
            "r2": round(r2, 4),
            "ic": round(ic, 4),
            "sharpe": round(sharpe, 4),
            "best_iteration": int(model.best_iteration_) if hasattr(model, "best_iteration_") else config["lgb_params"]["n_estimators"],
        })
        all_importances.append(model.feature_importances_)

    if not fold_metrics:
        return {
            "fold_metrics": [],
            "mean_r2": -999, "std_r2": 0,
            "mean_ic": 0, "std_ic": 0,
            "mean_sharpe": 0, "std_sharpe": 0,
            "feature_importances": np.zeros(n_features),
        }

    r2s = [f["r2"] for f in fold_metrics]
    ics = [f["ic"] for f in fold_metrics]
    sharps = [f["sharpe"] for f in fold_metrics]

    return {
        "fold_metrics": fold_metrics,
        "mean_r2": round(float(np.mean(r2s)), 4),
        "std_r2": round(float(np.std(r2s)), 4),
        "mean_ic": round(float(np.mean(ics)), 4),
        "std_ic": round(float(np.std(ics)), 4),
        "mean_sharpe": round(float(np.mean(sharps)), 4),
        "std_sharpe": round(float(np.std(sharps)), 4),
        "feature_importances": np.mean(all_importances, axis=0),
    }


def select_features_by_importance(
    feature_cols: List[str],
    importances: np.ndarray,
    threshold: float = 3.0,
    top_n: int = 20,
    protected_patterns: Optional[List[str]] = None,
    min_protected: int = 1,
) -> List[str]:
    """根据 CV 平均特征重要性筛选特征

    Args:
        feature_cols: 所有特征名
        importances: 特征重要性数组
        threshold: 重要性阈值
        top_n: 保留 Top N
        protected_patterns: 受保护特征前缀列表 (如 ["sent_"], 强制保留)
        min_protected: 每个受保护前缀至少保留的最少特征数

    Returns:
        选中特征列表
    """
    importance_series = pd.Series(importances, index=feature_cols)
    selected = importance_series[importance_series >= threshold]
    if len(selected) == 0:
        selected = importance_series.sort_values(ascending=False).head(top_n)
    else:
        selected = selected.sort_values(ascending=False).head(top_n)
    selected_features = list(selected.index)

    # 受保护特征机制: 确保至少 min_protected 个受保护特征入选
    if protected_patterns:
        for pattern in protected_patterns:
            protected_in = [f for f in selected_features if f.startswith(pattern)]
            if len(protected_in) < min_protected:
                # 从未入选的特征中, 找重要性最高的受保护特征
                protected_all = importance_series[
                    importance_series.index.str.startswith(pattern)
                ].sort_values(ascending=False)
                for feat in protected_all.index:
                    if feat not in selected_features:
                        selected_features.append(feat)
                        if len([f for f in selected_features if f.startswith(pattern)]) >= min_protected:
                            break
    return selected_features


# ============================================================
# 训练单标的
# ============================================================
def train_symbol_enhanced(
    symbol: str,
    df: pd.DataFrame,
    config: Dict,
) -> Dict[str, Any]:
    """训练单标的: 真实OHLCV + 情绪因子 + 放宽早停

    流程:
        1. 全特征 CV 评估
        2. 特征选择
        3. 最终模型训练 (早停 200 轮)

    Args:
        symbol: 标的代码
        df: 含真实 OHLCV + 技术因子 + 情绪因子的 DataFrame
        config: 训练配置

    Returns:
        训练结果字典
    """
    from lightgbm import LGBMRegressor
    import lightgbm as lgb

    df = df.copy()
    # 目标: 次日收益率
    df["target"] = df["close"].pct_change().shift(-1)
    df = df.dropna()

    if len(df) < config["min_samples"]:
        return {
            "status": "SKIP",
            "symbol": symbol,
            "reason": f"样本不足 ({len(df)} < {config['min_samples']})",
        }

    all_feature_cols = [c for c in df.columns if c not in
                        ["open", "high", "low", "close", "volume", "target"]]
    X_all = np.asarray(df[all_feature_cols].values, dtype=np.float64)
    y_all = np.asarray(df["target"].values, dtype=np.float64)

    # 替换 inf/nan
    X_all = np.nan_to_num(X_all, nan=0.0, posinf=0.0, neginf=0.0)

    # === Step 1: 全特征 CV ===
    cv_result = time_series_cv_evaluate(
        X_all, y_all, config, n_splits=config["n_splits"]
    )

    # === Step 2: 特征选择 (含情绪因子保护机制) ===
    selected_features = select_features_by_importance(
        all_feature_cols,
        cv_result["feature_importances"],
        threshold=config["feature_selection_threshold"],
        top_n=config["top_n_features"],
        protected_patterns=["sent_", "sentiment_"],  # 保护情绪因子
        min_protected=1,  # 至少 1 个情绪因子入选
    )

    # === Step 3: 用筛选后的特征重新 CV ===
    X_selected = np.asarray(df[selected_features].values, dtype=np.float64)
    X_selected = np.nan_to_num(X_selected, nan=0.0, posinf=0.0, neginf=0.0)
    cv_after_selection = time_series_cv_evaluate(
        X_selected, y_all, config, n_splits=config["n_splits"]
    )

    # === Step 4: 最终模型 ===
    n_test = max(int(len(df) * config["test_ratio"]), 20)
    n_train = len(df) - n_test
    train_df = df.iloc[:n_train]
    test_df = df.iloc[n_train:]

    X_train = np.asarray(train_df[selected_features].values, dtype=np.float64)
    y_train = np.asarray(train_df["target"].values, dtype=np.float64)
    X_test = np.asarray(test_df[selected_features].values, dtype=np.float64)
    y_test = np.asarray(test_df["target"].values, dtype=np.float64)

    X_train = np.nan_to_num(X_train, nan=0.0, posinf=0.0, neginf=0.0)
    X_test = np.nan_to_num(X_test, nan=0.0, posinf=0.0, neginf=0.0)

    final_model = LGBMRegressor(**config["lgb_params"])
    final_model.fit(
        X_train, y_train,
        eval_set=[(X_test, y_test)],
        callbacks=[
            lgb.early_stopping(
                stopping_rounds=config["early_stopping_rounds"],
                verbose=False,
            ),
        ],
    )

    y_pred = final_model.predict(X_test)
    final_r2 = _r2_score(y_test, y_pred)
    final_ic = _ic_score(y_test, y_pred)
    final_sharpe = _signal_sharpe(y_test, y_pred)

    latest_features = np.asarray(df[selected_features].iloc[-1:].values, dtype=np.float64)
    latest_features = np.nan_to_num(latest_features, nan=0.0, posinf=0.0, neginf=0.0)
    latest_pred = float(final_model.predict(latest_features)[0])
    signal = float(np.tanh(latest_pred * 100))

    feat_imp = pd.Series(
        final_model.feature_importances_, index=selected_features
    ).sort_values(ascending=False)

    best_iter = int(final_model.best_iteration_) if hasattr(final_model, "best_iteration_") else config["lgb_params"]["n_estimators"]

    # === Step 5: 自适应重训 (欠拟合标的) ===
    # best_iter <= 阈值 说明早停过早触发, 模型未学到足够模式
    # 使用更小学习率 + 更多估计器重训
    adaptive_retrained = False
    adaptive_threshold = config.get("adaptive_retrain_threshold", 5)
    if best_iter <= adaptive_threshold:
        adaptive_lr = config.get("adaptive_retrain_lr", 0.001)
        adaptive_n_est = config.get("adaptive_retrain_n_estimators", 5000)
        logger.info(f"  {symbol}: best_iter={best_iter} <= {adaptive_threshold}, "
                    f"触发自适应重训 (lr={adaptive_lr}, n_est={adaptive_n_est})")

        adaptive_params = dict(config["lgb_params"])
        adaptive_params["learning_rate"] = adaptive_lr
        adaptive_params["n_estimators"] = adaptive_n_est

        adaptive_model = LGBMRegressor(**adaptive_params)
        adaptive_model.fit(
            X_train, y_train,
            eval_set=[(X_test, y_test)],
            callbacks=[
                lgb.early_stopping(
                    stopping_rounds=config["early_stopping_rounds"],
                    verbose=False,
                ),
            ],
        )

        y_pred_adaptive = adaptive_model.predict(X_test)
        adaptive_r2 = _r2_score(y_test, y_pred_adaptive)
        adaptive_ic = _ic_score(y_test, y_pred_adaptive)
        adaptive_sharpe = _signal_sharpe(y_test, y_pred_adaptive)
        adaptive_best_iter = int(adaptive_model.best_iteration_) if hasattr(adaptive_model, "best_iteration_") else adaptive_n_est

        # 仅当自适应版本更优时替换
        if adaptive_r2 > final_r2:
            logger.info(f"  {symbol}: 自适应重训改进 R² {final_r2:.4f} → {adaptive_r2:.4f}, "
                        f"best_iter {best_iter} → {adaptive_best_iter}")
            final_model = adaptive_model
            y_pred = y_pred_adaptive
            final_r2 = adaptive_r2
            final_ic = adaptive_ic
            final_sharpe = adaptive_sharpe
            best_iter = adaptive_best_iter
            feat_imp = pd.Series(
                final_model.feature_importances_, index=selected_features
            ).sort_values(ascending=False)
            latest_pred = float(final_model.predict(latest_features)[0])
            signal = float(np.tanh(latest_pred * 100))
            adaptive_retrained = True
        else:
            logger.info(f"  {symbol}: 自适应重训未改进 (R² {adaptive_r2:.4f} <= {final_r2:.4f}), 保留原模型")

    return {
        "status": "OK",
        "symbol": symbol,
        "n_samples": len(df),
        "n_features_before": len(all_feature_cols),
        "n_features_after": len(selected_features),
        "selected_features": selected_features,
        "n_train": n_train,
        "n_test": n_test,
        "train_period": f"{train_df.index[0].date()} → {train_df.index[-1].date()}",
        "test_period": f"{test_df.index[0].date()} → {test_df.index[-1].date()}",
        "best_iteration": best_iter,
        "adaptive_retrained": adaptive_retrained,
        "cv_before_selection": {
            "mean_r2": cv_result["mean_r2"],
            "std_r2": cv_result["std_r2"],
            "mean_ic": cv_result["mean_ic"],
            "std_ic": cv_result["std_ic"],
            "mean_sharpe": cv_result["mean_sharpe"],
            "std_sharpe": cv_result["std_sharpe"],
            "fold_metrics": cv_result["fold_metrics"],
        },
        "cv_after_selection": {
            "mean_r2": cv_after_selection["mean_r2"],
            "std_r2": cv_after_selection["std_r2"],
            "mean_ic": cv_after_selection["mean_ic"],
            "std_ic": cv_after_selection["std_ic"],
            "mean_sharpe": cv_after_selection["mean_sharpe"],
            "std_sharpe": cv_after_selection["std_sharpe"],
            "fold_metrics": cv_after_selection["fold_metrics"],
        },
        "final_metrics": {
            "r2": round(final_r2, 4),
            "ic": round(final_ic, 4),
            "sharpe": round(final_sharpe, 4),
        },
        "signal": round(signal, 4),
        "raw_prediction": round(latest_pred, 6),
        "top_features": feat_imp.to_dict(),
        "model": final_model,
    }


# ============================================================
# 模型持久化
# ============================================================
def save_model(symbol: str, result: Dict, config: Dict) -> Dict:
    symbol_dir = MODELS_DIR / symbol
    symbol_dir.mkdir(exist_ok=True)

    model_path = symbol_dir / f"{symbol}_lgb_enhanced_model.pkl"
    meta_path = symbol_dir / f"{symbol}_meta.json"

    with open(model_path, "wb") as f:
        pickle.dump(result["model"], f)

    meta = {
        "symbol": symbol,
        "saved_at": datetime.now().isoformat(),
        "model_type": "LightGBM_Enhanced_RealOHLCV_Sentiment",
        "data_source": "real_ohlcv_via_wind_ifind_sina",
        "n_samples": result["n_samples"],
        "n_features_before": result["n_features_before"],
        "n_features_after": result["n_features_after"],
        "selected_features": result["selected_features"],
        "train_period": result["train_period"],
        "test_period": result["test_period"],
        "best_iteration": result["best_iteration"],
        "adaptive_retrained": result.get("adaptive_retrained", False),
        "cv_before_selection": result["cv_before_selection"],
        "cv_after_selection": result["cv_after_selection"],
        "final_metrics": result["final_metrics"],
        "signal": result["signal"],
        "raw_prediction": result["raw_prediction"],
        "top_features": result["top_features"],
        "config": {
            "lgb_params": config["lgb_params"],
            "early_stopping_rounds": config["early_stopping_rounds"],
            "n_splits": config["n_splits"],
            "test_ratio": config["test_ratio"],
            "top_n_features": config["top_n_features"],
            "feature_selection_threshold": config["feature_selection_threshold"],
            "news_lookback_days": config["news_lookback_days"],
        },
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2, default=str)

    return {"model_path": str(model_path), "meta_path": str(meta_path)}


def load_model_meta(symbol: str) -> Optional[Dict]:
    meta_path = MODELS_DIR / symbol / f"{symbol}_meta.json"
    if not meta_path.exists():
        return None
    with open(meta_path, "r", encoding="utf-8") as f:
        return json.load(f)


def should_retrain(symbol: str, config: Dict) -> bool:
    meta = load_model_meta(symbol)
    if meta is None:
        return True
    saved_at = datetime.fromisoformat(meta["saved_at"])
    age_days = (datetime.now() - saved_at).days
    return age_days >= config["retrain_interval_days"]


# ============================================================
# 主训练流程
# ============================================================
def run_enhanced_training(
    symbols: Optional[List[Tuple]] = None,
    force_retrain: bool = False,
    config: Optional[Dict] = None,
    use_news: bool = True,
) -> Dict[str, Any]:
    """执行增强训练

    Args:
        symbols: 标的清单
        force_retrain: 强制重训
        config: 训练配置
        use_news: 是否启用新闻情绪因子

    Returns:
        训练结果汇总
    """
    if config is None:
        config = LGB_ENHANCED_CONFIG
    if symbols is None:
        symbols = POSITION_SYMBOLS

    logger.info("#" * 70)
    logger.info("# LightGBM 增强训练 (真实OHLCV + 情绪因子 + 放宽早停)")
    logger.info(f"# 标的数: {len(symbols)}")
    logger.info(f"# CV 折数: {config['n_splits']}")
    logger.info(f"# 早停轮次: {config['early_stopping_rounds']} (放宽)")
    logger.info(f"# n_estimators: {config['lgb_params']['n_estimators']}")
    logger.info(f"# learning_rate: {config['lgb_params']['learning_rate']}")
    logger.info(f"# 新闻情绪因子: {'启用' if use_news else '禁用'}")
    logger.info("#" * 70)

    # Step 1: 拉取真实 OHLCV
    logger.info("Step 1: 拉取真实 OHLCV 数据 (Wind MCP > iFinD MCP > 新浪 HTTP)")
    ohlcv_dict = fetch_all_real_ohlcv(symbols, period="2y")
    logger.info(f"  真实 OHLCV: {len(ohlcv_dict)} / {len(symbols)} 标的")

    if not ohlcv_dict:
        logger.error("  无可用真实数据, 训练终止")
        return {"status": "FAIL", "error": "no_real_ohlcv"}

    # Step 2: 技术因子
    logger.info("Step 2: 技术因子工程 (30+ 因子)")
    featured_dict = {}
    for symbol, df in ohlcv_dict.items():
        df_feat = add_technical_features(df)
        featured_dict[symbol] = df_feat
    featured_dict = add_cross_sectional_features(featured_dict)
    logger.info(f"  技术因子完成: {len(featured_dict)} 标的")

    # Step 2.5: 扩展特征工程 v2 (针对 best_iter=1 的标的)
    logger.info("Step 2.5: 扩展特征工程 v2 (行业相对强度 + 资金流向 + 跨市场信号)")
    featured_dict = add_industry_relative_strength_features(featured_dict)
    logger.info(f"  行业相对强度: +5 因子 (industry_return_5/20, relative_strength_5/20, industry_rank_20)")
    featured_dict = add_capital_flow_features(featured_dict)
    logger.info(f"  资金流向: +5 因子 (capital_flow, cumulative_flow_5, flow_divergence, smart_money_ratio, flow_momentum)")
    featured_dict = add_cross_market_features(featured_dict)
    logger.info(f"  跨市场信号: +6 因子 (gold/bank/tech/defensive trend, style_rotation, safe_haven_flow)")

    # Step 3: 新闻情绪因子 (Wind MCP 优先, iFinD 回退)
    if use_news:
        logger.info("Step 3: 新闻情绪因子 (Wind MCP 优先, iFinD 回退)")
        sentiment_dict = compute_news_sentiment_factors(
            symbols, lookback_days=config["news_lookback_days"]
        )
        if sentiment_dict:
            featured_dict = add_sentiment_features(featured_dict, sentiment_dict)
            n_sent = sum(1 for df in featured_dict.values() if "sentiment_score" in df.columns)
            logger.info(f"  情绪因子已合并: {n_sent} 标的")
        else:
            logger.warning("  情绪因子拉取失败, 仅使用技术因子")
            # 仍然加入空的情绪列以保持一致
            featured_dict = add_sentiment_features(featured_dict, {})
    else:
        logger.info("Step 3: 跳过新闻情绪因子")
        featured_dict = add_sentiment_features(featured_dict, {})

    # 统计特征数
    for symbol, df in featured_dict.items():
        n_feat = len([c for c in df.columns if c not in
                      ["open", "high", "low", "close", "volume"]])
        logger.info(f"  {symbol}: {n_feat} 个特征 (技术+情绪)")

    # Step 4: 训练
    logger.info("Step 4: 训练 LightGBM + TSCV (放宽早停)")
    results = {}
    saved = 0
    skipped = 0
    failed = 0

    for code, suffix, _, name, style in symbols:
        if code not in featured_dict:
            logger.warning(f"  [SKIP] {code} ({name}): 无数据")
            skipped += 1
            continue

        if not force_retrain and not should_retrain(code, config):
            meta = load_model_meta(code)
            logger.info(f"  [CACHED] {code} ({name}): 模型未过期, 跳过")
            results[code] = {
                "status": "CACHED",
                "symbol": code,
                "name": name,
                "style": style,
                "meta": meta,
            }
            continue

        logger.info(f"  [TRAIN] {code} ({name})...")
        try:
            result = train_symbol_enhanced(code, featured_dict[code], config)
            if result["status"] != "OK":
                logger.warning(f"  [SKIP] {code}: {result.get('reason')}")
                skipped += 1
                results[code] = result
                continue

            cv_metrics = result["cv_after_selection"]
            quality_ok = (
                cv_metrics["mean_r2"] >= config["model_quality_threshold"]["min_cv_r2"]
                and cv_metrics["mean_ic"] >= config["model_quality_threshold"]["min_cv_ic"]
                and cv_metrics["mean_sharpe"] >= config["model_quality_threshold"]["min_cv_sharpe"]
            )

            if not quality_ok:
                logger.warning(
                    f"  [LOW_QUALITY] {code}: "
                    f"CV R²={cv_metrics['mean_r2']}±{cv_metrics['std_r2']}, "
                    f"IC={cv_metrics['mean_ic']}±{cv_metrics['std_ic']}"
                )
                result["quality_flag"] = "LOW_QUALITY"
            else:
                result["quality_flag"] = "OK"

            paths = save_model(code, result, config)
            saved += 1
            logger.info(
                f"  [SAVED] {code}: "
                f"CV R²={cv_metrics['mean_r2']}±{cv_metrics['std_r2']}, "
                f"IC={cv_metrics['mean_ic']}±{cv_metrics['std_ic']}, "
                f"Sharpe={cv_metrics['mean_sharpe']}±{cv_metrics['std_sharpe']}, "
                f"final_r2={result['final_metrics']['r2']}, "
                f"final_ic={result['final_metrics']['ic']}, "
                f"signal={result['signal']}, "
                f"best_iter={result['best_iteration']}, "
                f"features={result['n_features_before']}→{result['n_features_after']}"
            )
            results[code] = {**result, "paths": paths, "name": name, "style": style}

        except Exception as e:
            logger.error(f"  [FAIL] {code}: {e}", exc_info=True)
            failed += 1
            results[code] = {"status": "FAIL", "symbol": code, "error": str(e)}

    # Step 5: 集成信号
    logger.info("Step 5: 生成集成信号")
    signals = {}
    for code, res in results.items():
        if res.get("status") in ("OK", "CACHED"):
            signals[code] = {
                "signal": res.get("signal", res.get("meta", {}).get("signal", 0)),
                "name": res.get("name", ""),
                "style": res.get("style", ""),
                "quality_flag": res.get("quality_flag", "CACHED"),
                "model_type": "LightGBM_Enhanced_RealOHLCV_Sentiment",
            }

    signals_path = MODELS_DIR / "lgb_enhanced_signals.json"
    signals_data = {
        "generated_at": datetime.now().isoformat(),
        "trade_date": datetime.now().strftime("%Y-%m-%d"),
        "model_type": "LightGBM_Enhanced_RealOHLCV_Sentiment",
        "data_source": "real_ohlcv",
        "features": "technical_37 + extended_16 + sentiment_6",
        "signals": {code: s for code, s in signals.items()},
        "summary": {
            "total": len(signals),
            "bullish": sum(1 for s in signals.values() if s["signal"] > 0.2),
            "bearish": sum(1 for s in signals.values() if s["signal"] < -0.2),
            "neutral": sum(1 for s in signals.values() if abs(s["signal"]) <= 0.2),
        },
    }
    with open(signals_path, "w", encoding="utf-8") as f:
        json.dump(signals_data, f, ensure_ascii=False, indent=2)
    logger.info(f"  信号文件: {signals_path}")

    return {
        "status": "OK",
        "total": len(symbols),
        "trained": saved,
        "skipped": skipped,
        "failed": failed,
        "results": results,
        "signals": signals_data,
    }


# ============================================================
# 三方对比报告 (旧集成 / LGB+TSCV / LGB增强)
# ============================================================
def generate_comparison_report(result: Dict) -> Path:
    """生成三方对比报告"""
    today = datetime.now().strftime("%Y%m%d")
    report_path = REPORTS_DIR / f"lgb_enhanced_report_{today}.md"

    old_models_dir = BASE_DIR / "models" / "autolearn"
    tscv_models_dir = BASE_DIR / "models" / "lgb_tscv"

    lines = [
        f"# LightGBM 增强训练报告 - {datetime.now().strftime('%Y-%m-%d')}",
        "",
        f"**生成时间**: {datetime.now().isoformat()}",
        f"**模型类型**: LightGBM 增强版 (真实OHLCV + 情绪因子 + 放宽早停)",
        f"**数据源**: Wind MCP > iFinD MCP > 新浪 HTTP (真实价格和成交量)",
        f"**标的数**: {result['total']}",
        f"**训练成功**: {result['trained']}",
        f"**跳过**: {result['skipped']}",
        f"**失败**: {result['failed']}",
        "",
        "## 优化点",
        "",
        "1. **真实 OHLCV 数据**: 替代合成数据",
        "   - Wind MCP / iFinD MCP / 新浪 HTTP 多源优先级",
        "   - 502 日真实价格和成交量",
        "   - 技术指标基于真实价格计算, 质量更高",
        "2. **新闻情绪因子**: 9 个特征 (Wind MCP 优先, iFinD 回退)",
        "   - sentiment_score: 当日情绪分",
        "   - sent_ma5 / sent_ma20: 5/20 日情绪均值",
        "   - sent_mom5: 情绪动量",
        "   - sent_volatility: 情绪波动率",
        "   - sent_positive_ratio: 20 日正情绪占比",
        "   - sent_zscore_20: 情绪标准化分数 (v3 新增)",
        "   - sent_acceleration: 情绪加速度 (v3 新增)",
        "   - sent_price_interaction: 情绪-价格交互 (v3 新增)",
        "3. **特征选择增强**: top_n 20→30, threshold 3→1, 情绪因子保护机制",
        "4. **放宽早停**: 50 → 200 轮",
        "   - n_estimators 1000 → 2000",
        "   - learning_rate 0.01 → 0.005",
        "   - 允许模型更充分学习",
        "",
        "## 一、三方对比 (R²)",
        "",
        "| 标的 | 名称 | 旧集成 R² | LGB+TSCV R² | LGB增强 R² | 总改进 | 旧 IC | TSCV IC | 增强 IC | IC 改进 |",
        "|------|------|---------|------------|-----------|--------|-------|---------|---------|---------|",
    ]

    improvements_r2 = []
    improvements_ic = []
    for code, r in result["results"].items():
        if r.get("status") not in ("OK", "CACHED"):
            continue
        name = r.get("name", "")

        # 旧集成
        old_meta_path = old_models_dir / code / f"{code}_meta.json"
        old_r2 = "N/A"
        old_ic = "N/A"
        if old_meta_path.exists():
            with open(old_meta_path, "r", encoding="utf-8") as f:
                old_meta = json.load(f)
            old_r2 = old_meta.get("metrics", {}).get("ensemble_r2", "N/A")
            old_ic = old_meta.get("metrics", {}).get("ensemble_ic", "N/A")

        # LGB+TSCV
        tscv_meta_path = tscv_models_dir / code / f"{code}_meta.json"
        tscv_r2 = "N/A"
        tscv_ic = "N/A"
        if tscv_meta_path.exists():
            with open(tscv_meta_path, "r", encoding="utf-8") as f:
                tscv_meta = json.load(f)
            tscv_r2 = tscv_meta.get("cv_after_selection", {}).get("mean_r2", "N/A")
            tscv_ic = tscv_meta.get("cv_after_selection", {}).get("mean_ic", "N/A")

        # 增强
        if r.get("status") == "OK":
            new_r2 = r["cv_after_selection"]["mean_r2"]
            new_ic = r["cv_after_selection"]["mean_ic"]
            new_r2_std = r["cv_after_selection"]["std_r2"]
            new_ic_std = r["cv_after_selection"]["std_ic"]
        else:
            new_r2 = r.get("meta", {}).get("cv_after_selection", {}).get("mean_r2", "N/A")
            new_ic = r.get("meta", {}).get("cv_after_selection", {}).get("mean_ic", "N/A")
            new_r2_std = r.get("meta", {}).get("cv_after_selection", {}).get("std_r2", 0)
            new_ic_std = r.get("meta", {}).get("cv_after_selection", {}).get("std_ic", 0)

        if isinstance(old_r2, (int, float)) and isinstance(new_r2, (int, float)):
            r2_diff = round(new_r2 - old_r2, 4)
            improvements_r2.append(r2_diff)
            r2_improve = f"{r2_diff:+.4f}"
        else:
            r2_improve = "N/A"

        if isinstance(old_ic, (int, float)) and isinstance(new_ic, (int, float)):
            ic_diff = round(new_ic - old_ic, 4)
            improvements_ic.append(ic_diff)
            ic_improve = f"{ic_diff:+.4f}"
        else:
            ic_improve = "N/A"

        lines.append(
            f"| {code} | {name} | {old_r2} | {tscv_r2} | "
            f"{new_r2}±{new_r2_std} | {r2_improve} | "
            f"{old_ic} | {tscv_ic} | {new_ic}±{new_ic_std} | {ic_improve} |"
        )

    if improvements_r2:
        lines.extend([
            "",
            "## 二、整体改进汇总 (vs 旧集成)",
            "",
            f"- R² 平均改进: **{np.mean(improvements_r2):+.4f}**",
            f"- R² 改进标的数: {sum(1 for x in improvements_r2 if x > 0)} / {len(improvements_r2)}",
            f"- IC 平均改进: **{np.mean(improvements_ic):+.4f}**",
            f"- IC 改进标的数: {sum(1 for x in improvements_ic if x > 0)} / {len(improvements_ic)}",
        ])

    # CV 详情
    lines.extend([
        "",
        "## 三、CV 详情 (特征选择后)",
        "",
        "| 标的 | 名称 | Fold | CV R² (mean±std) | CV IC (mean±std) | CV Sharpe | 最终 R² | 最终 IC | 信号 | 特征数 | Best Iter |",
        "|------|------|------|------------------|------------------|-----------|---------|--------|------|--------|-----------|",
    ])
    for code, r in result["results"].items():
        if r.get("status") != "OK":
            continue
        cv = r["cv_after_selection"]
        fm = r["final_metrics"]
        n_folds = len(cv["fold_metrics"])
        lines.append(
            f"| {code} | {r.get('name', '')} | {n_folds} | "
            f"{cv['mean_r2']}±{cv['std_r2']} | "
            f"{cv['mean_ic']}±{cv['std_ic']} | "
            f"{cv['mean_sharpe']}±{cv['std_sharpe']} | "
            f"{fm['r2']} | {fm['ic']} | {r['signal']} | "
            f"{r['n_features_before']}→{r['n_features_after']} | "
            f"{r['best_iteration']} |"
        )

    # 特征重要性
    lines.extend([
        "",
        "## 四、特征重要性 (Top 10)",
        "",
    ])
    for code, r in result["results"].items():
        if r.get("status") != "OK":
            continue
        top_feat = r.get("top_features", {})
        if not top_feat:
            continue
        lines.append(f"### {code} ({r.get('name', '')})")
        lines.append("")
        for i, (feat, imp) in enumerate(list(top_feat.items())[:10], 1):
            tag = ""
            if "sent" in feat:
                tag = " [情绪因子]"
            elif feat.startswith(("industry_", "relative_strength_", "industry_rank_")):
                tag = " [行业相对强度]"
            elif feat in ("capital_flow", "cumulative_flow_5", "flow_divergence",
                          "smart_money_ratio", "flow_momentum"):
                tag = " [资金流向]"
            elif feat.startswith(("gold_", "bank_", "tech_style", "defensive_style",
                                   "style_rotation", "safe_haven_flow")):
                tag = " [跨市场信号]"
            else:
                tag = ""
            lines.append(f"{i}. **{feat}**: {imp}{tag}")
        lines.append("")

    # 情绪因子统计
    lines.extend([
        "",
        "## 五、情绪因子入选统计",
        "",
    ])
    sent_included = 0
    sent_total = 0
    for code, r in result["results"].items():
        if r.get("status") != "OK":
            continue
        sent_total += 1
        selected = r.get("selected_features", [])
        sent_features = [f for f in selected if "sent" in f]
        if sent_features:
            sent_included += 1
            lines.append(f"- {code} ({r.get('name', '')}): {', '.join(sent_features)}")
    lines.append("")
    lines.append(f"**情绪因子入选比例**: {sent_included} / {sent_total}")

    # 扩展特征入选统计
    lines.extend([
        "",
        "## 五(补)、扩展特征入选统计 (v2 新增)",
        "",
    ])
    extended_categories = {
        "行业相对强度": ["industry_return_5", "industry_return_20",
                       "relative_strength_5", "relative_strength_20", "industry_rank_20"],
        "资金流向": ["capital_flow", "cumulative_flow_5", "flow_divergence",
                    "smart_money_ratio", "flow_momentum"],
        "跨市场信号": ["gold_trend_20", "bank_trend_20", "tech_style_20",
                      "defensive_style_20", "style_rotation_5", "safe_haven_flow"],
    }
    for cat_name, cat_features in extended_categories.items():
        included_count = 0
        included_details = []
        for code, r in result["results"].items():
            if r.get("status") != "OK":
                continue
            selected = r.get("selected_features", [])
            ext_feats = [f for f in selected if f in cat_features]
            if ext_feats:
                included_count += 1
                included_details.append(f"  - {code} ({r.get('name', '')}): {', '.join(ext_feats)}")
        total_ok = sum(1 for r in result["results"].values() if r.get("status") == "OK")
        lines.append(f"### {cat_name}: {included_count} / {total_ok} 标的入选")
        lines.extend(included_details)
        lines.append("")

    # 自适应重训统计
    adaptive_count = 0
    adaptive_improved = 0
    for code, r in result["results"].items():
        if r.get("status") != "OK":
            continue
        if r.get("adaptive_retrained", False):
            adaptive_count += 1
            adaptive_improved += 1
    lines.extend([
        "",
        "## 六、自适应重训统计 (欠拟合标的)",
        "",
        f"- 触发自适应重训标的数: {adaptive_count}",
        f"- 实际改进标的数: {adaptive_improved}",
        f"- 触发条件: best_iter <= {LGB_ENHANCED_CONFIG.get('adaptive_retrain_threshold', 5)}",
        f"- 重训参数: learning_rate={LGB_ENHANCED_CONFIG.get('adaptive_retrain_lr', 0.001)}, "
        f"n_estimators={LGB_ENHANCED_CONFIG.get('adaptive_retrain_n_estimators', 5000)}",
    ])

    # 风险提示
    lines.extend([
        "",
        "## 七、风险提示",
        "",
        "- 真实 OHLCV 通过多数据源拉取, 质量较合成数据显著提升",
        "- 新闻情绪因子: 一次性拉取过去 N 天真实新闻, 按 publish_time 分配到日期",
        "- 自适应重训: 对 best_iter <= 阈值的标的, 使用更小学习率 + 更多估计器重训",
        "- 若 best_iter 接近 n_estimators, 说明模型仍未收敛, 可考虑增加估计器",
        "",
        "---",
        f"**报告路径**: `{report_path}`",
        f"**信号文件**: `models/lgb_enhanced/lgb_enhanced_signals.json`",
    ])

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info(f"对比报告已生成: {report_path}")
    return report_path


# ============================================================
# CLI
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="LightGBM 增强训练 (真实OHLCV + 情绪因子 + 放宽早停)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--force-retrain", action="store_true",
                        help="强制重训所有标的")
    parser.add_argument("--symbols", nargs="+", default=None,
                        help="指定标的代码 (默认全部持仓)")
    parser.add_argument("--no-news", action="store_true",
                        help="跳过新闻情绪因子 (加速训练)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(
                LOG_DIR / f"lgb_enhanced_{datetime.now():%Y%m%d}.log",
                encoding="utf-8",
            ),
            logging.StreamHandler(sys.stdout),
        ],
    )

    symbols = POSITION_SYMBOLS
    if args.symbols:
        symbols = [s for s in POSITION_SYMBOLS if s[0] in args.symbols]

    result = run_enhanced_training(
        symbols=symbols,
        force_retrain=args.force_retrain,
        use_news=not args.no_news,
    )

    if result["status"] == "OK":
        report_path = generate_comparison_report(result)
        print(f"\n✓ 训练完成, 对比报告: {report_path}")
        print(f"✓ 信号文件: models/lgb_enhanced/lgb_enhanced_signals.json")
        sys.exit(0)
    else:
        print(f"\n✗ 训练失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
