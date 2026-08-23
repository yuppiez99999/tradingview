"""
机构管道信号计算 Mixin
=====================

从 institutional_pipeline_runner.py 抽取的信号计算方法:
- 技术指标计算 (_calc_*)
- Alpha 信号合成 (_compose_momentum_signal, _fuse_with_lgb_signal)
- 真实信号源 (_real_macro_signals, _real_llm_signals, _real_etf_signals)
"""

from __future__ import annotations

import logging
import math
from typing import Any

import pandas as pd

from utils.concurrency import run_io_batch

logger = logging.getLogger("institutional_pipeline")


class SignalMixin:
    """信号计算 Mixin — 技术指标 + Alpha合成 + 多源真实信号。"""

    def _load_symbol_close_volume(self, symbol: str) -> tuple[pd.Series, pd.Series]:
        """加载单个 symbol 的 close / volume 序列 (不足 30 行抛异常)。"""
        df = self._get_or_load_historical(symbol)
        if df is None or df.empty or len(df) < 30:
            raise ValueError("history_too_short")
        close = df["close"].dropna()
        volume = df["volume"].dropna() if "volume" in df.columns else pd.Series(dtype=float)
        return close, volume

    def _compute_alpha_components(self, close: pd.Series, volume: pd.Series) -> dict[str, float]:
        """计算动量、波动率、换手率、RSI、均线、MACD、布林带等组件信号。"""
        return {
            "momentum": self._calc_momentum(close),
            "vol_breakout": self._calc_vol_breakout(close),
            "turnover_signal": self._calc_turnover_signal(volume),
            "rsi_signal": self._calc_rsi_signal(close),
            "ma_breakout": self._calc_ma_breakout(close),
            "macd_signal": self._calc_macd_signal(close),
            "bb_signal": self._calc_bb_signal(close),
        }

    def _calc_momentum(self, close: pd.Series) -> float:
        """多周期动量: 5d/20d/60d 加权。"""
        ret_5d = float(close.iloc[-1] / close.iloc[-6] - 1) if len(close) > 5 else 0.0
        ret_20d = float(close.iloc[-1] / close.iloc[-21] - 1) if len(close) > 20 else 0.0
        ret_60d = float(close.iloc[-1] / close.iloc[-61] - 1) if len(close) > 60 else 0.0
        return 0.40 * ret_5d + 0.35 * ret_20d + 0.25 * ret_60d

    def _calc_vol_breakout(self, close: pd.Series) -> float:
        """波动率突破: 当前 vs 历史波动率。"""
        vol_20d = float(close.pct_change().rolling(20).std().iloc[-1]) if len(close) > 20 else 0.0
        vol_60d = float(close.pct_change().rolling(60).std().iloc[-1]) if len(close) > 60 else vol_20d
        vol_ratio = float(vol_20d / vol_60d) if vol_60d > 1e-12 else 1.0
        return float(max(-1.0, min(1.0, (vol_ratio - 1.0) * 8)))

    def _calc_turnover_signal(self, volume: pd.Series) -> float:
        """换手率异常: 当前成交量 vs 历史均值。"""
        if len(volume) <= 20:
            return 0.0
        vol_ma20 = float(volume.rolling(20).mean().iloc[-1])
        vol_ratio_now = float(volume.iloc[-1] / vol_ma20) if vol_ma20 > 1e-12 else 1.0
        return float(max(-1.0, min(1.0, (vol_ratio_now - 1.0) * 0.5)))

    def _calc_rsi_signal(self, close: pd.Series) -> float:
        """相对强弱: 当前价格 vs 20日最高/最低。"""
        if len(close) <= 20:
            return 0.0
        high_20d = float(close.rolling(20).max().iloc[-1])
        low_20d = float(close.rolling(20).min().iloc[-1])
        rsi_proxy = (
            float((close.iloc[-1] - low_20d) / (high_20d - low_20d))
            if (high_20d - low_20d) > 1e-12
            else 0.5
        )
        return float(max(-1.0, min(1.0, (rsi_proxy - 0.5) * 2)))

    def _calc_ma_breakout(self, close: pd.Series) -> float:
        """均线突破: 价格与 MA5/MA20/MA60 的关系。"""
        if len(close) <= 60:
            return 0.0
        ma5 = float(close.rolling(5).mean().iloc[-1])
        ma20 = float(close.rolling(20).mean().iloc[-1])
        ma60 = float(close.rolling(60).mean().iloc[-1])
        price = float(close.iloc[-1])
        if ma5 > ma20 > ma60 and price > ma5:
            return 1.0
        if ma5 < ma20 < ma60 and price < ma5:
            return -1.0
        return 0.0

    def _calc_macd_signal(self, close: pd.Series) -> float:
        """MACD 信号。"""
        if len(close) <= 26:
            return 0.0
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_val = float(macd_line.iloc[-1] - signal_line.iloc[-1])
        return float(max(-1.0, min(1.0, macd_val * 80)))

    def _calc_bb_signal(self, close: pd.Series) -> float:
        """布林带位置信号。"""
        if len(close) <= 20:
            return 0.0
        bb_mid = float(close.rolling(20).mean().iloc[-1])
        bb_std = float(close.rolling(20).std().iloc[-1])
        if bb_std <= 1e-12:
            return 0.0
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std
        bb_pos = float((close.iloc[-1] - bb_lower) / (bb_upper - bb_lower))
        return float(max(-1.0, min(1.0, (bb_pos - 0.5) * 2)))

    def _compose_momentum_signal(
        self, close: pd.Series, components: dict[str, float]
    ) -> tuple[float, float]:
        """综合 Alpha 信号: 动量为主, 其余为辅; 并计算置信度。"""
        mom_strength = (
            0.35 * components["momentum"]
            + 0.15 * components["vol_breakout"]
            + 0.10 * components["turnover_signal"]
            + 0.10 * components["rsi_signal"]
            + 0.15 * components["ma_breakout"]
            + 0.10 * components["macd_signal"]
            + 0.05 * components["bb_signal"]
        )
        mom_strength = float(max(-1.0, min(1.0, mom_strength * 14)))

        # 置信度: 数据越长越稳, 信号越集中越稳
        mom_confidence = 0.55
        if len(close) > 60:
            mom_confidence += 0.15
        if len(close) > 120:
            mom_confidence += 0.1
        if (
            abs(components["momentum"]) > 0.02
            or abs(components["ma_breakout"]) > 0.5
            or abs(components["macd_signal"]) > 0.3
        ):
            mom_confidence += 0.1
        mom_confidence = float(min(1.0, mom_confidence))
        return mom_strength, mom_confidence

    def _fuse_with_lgb_signal(
        self, symbol: str, mom_strength: float, mom_confidence: float
    ) -> tuple[float, float]:
        """LGB Walk-forward 信号融合: LGB 80% + 动量 20%。

        _lgb_get_signal 只读 self._lgb_models, dict.get 原子操作, 多线程安全。
        LGB 不可用时降级为 100% 动量信号。
        """
        lgb_strength, lgb_confidence = self._lgb_get_signal(symbol)
        if abs(lgb_strength) <= 1e-9:
            return mom_strength, mom_confidence
        strength = float(max(-1.0, min(1.0, 0.8 * lgb_strength + 0.2 * mom_strength)))
        confidence = float(min(1.0, 0.8 * lgb_confidence + 0.2 * mom_confidence))
        return strength, confidence

    def _real_macro_signals(self) -> dict[str, dict[str, Any]]:
        """B2.2: 宏观信号 — sentiment API 失败时并发拉取多 symbol 计算代理"""
        if self.data_provider is None:
            return {"macro_index": {"strength": 0.0, "confidence": 0.2}}
        try:
            sentiment = {}
            macro = self.data_provider.get_external_macro()
            if isinstance(macro, dict):
                sentiment = macro.get("risk_sentiment", {}) or {}
            if not sentiment:
                sentiment = self.data_provider.get_risk_sentiment() or {}
            score = float(sentiment.get("score", 0.0)) if isinstance(sentiment, dict) else 0.0
            strength = float(max(-1.0, min(1.0, score)))

            if strength == 0.0:
                try:
                    # B2.2: 并发拉取每个 symbol 的 5d 收益率 (原 period="1y" 改用 "5y" superset, 命中 cache)
                    def _worker(symbol: str) -> float | None:
                        try:
                            df = self._get_or_load_historical(symbol)
                            if df is None or df.empty or "close" not in df.columns:
                                return None
                            s = df["close"].dropna()
                            if len(s) > 5:
                                return float(s.iloc[-1] / s.iloc[-6] - 1)
                        except Exception as e:
                            logger.debug("[Pipeline] 宏观代理信号计算失败 %s: %s", symbol, e)
                        return None

                    rets_or_none = run_io_batch(
                        self.ctx.symbols,
                        _worker,
                        max_workers=8,
                        timeout=60,
                        fail_default=None,
                        desc="macro_proxy",
                    )
                    rets = [r for r in rets_or_none if r is not None]
                    if rets:
                        macro_proxy = float(max(-1.0, min(1.0, sum(rets) / len(rets) * 8)))
                        if macro_proxy != 0.0:
                            strength = macro_proxy
                except Exception as e:
                    logger.debug("[Pipeline] 宏观代理信号计算失败: %s", e)

            confidence = 0.5 if score != 0.0 else 0.35
            return {"macro_index": {"strength": strength, "confidence": confidence}}
        except Exception as e:
            logger.warning("[Pipeline] 真实宏观信号获取失败: %s", e)
            return {"macro_index": {"strength": 0.0, "confidence": 0.2}}

    def _real_llm_signals(self) -> dict[str, dict[str, Any]]:
        """B2.2: LLM 信号 — 并发拉取多 symbol 新闻情绪 + 历史数据兜底"""
        if self.data_provider is None:
            return {s: {"strength": 0.0, "confidence": 0.2} for s in self.ctx.symbols}

        def _worker(symbol: str) -> tuple[str, dict[str, Any]]:
            try:
                news_sentiment = self.data_provider.get_news_sentiment(symbol, limit=20)
                strength = 0.0
                confidence = 0.2
                if isinstance(news_sentiment, list) and news_sentiment:
                    vals = []
                    confs = []
                    for item in news_sentiment:
                        if isinstance(item, dict):
                            vals.append(float(item.get("sentiment_score", item.get("score", 0.0))))
                            confs.append(float(item.get("confidence", 0.0)))
                    if vals:
                        strength = float(max(-1.0, min(1.0, sum(vals) / len(vals))))
                        confidence = float(min(1.0, (sum(confs) / len(confs)) if confs else 0.2 + 0.1))

                if strength == 0.0 and confidence <= 0.2:
                    try:
                        # B2.2: 原 period="1m" 改用 "5y" superset (命中 cache, 避免重复拉取)
                        df = self._get_or_load_historical(symbol)
                        if df is not None and not df.empty and "close" in df.columns:
                            s = df["close"].dropna()
                            if len(s) > 10:
                                proxy = float(s.iloc[-1] / s.iloc[-6] - 1)
                                strength = float(max(-1.0, min(1.0, proxy * 12)))
                                confidence = 0.45
                    except Exception as e:
                        logger.debug("[Pipeline] LLM代理信号计算失败 %s: %s", symbol, e)

                conf = (
                    0.55
                    if any(
                        isinstance(item, dict) and item.get("sentiment_score", item.get("score", 0.0)) != 0.0
                        for item in (news_sentiment or [])
                    )
                    else confidence
                )
                return (symbol, {"strength": strength, "confidence": conf})
            except Exception as e:
                logger.warning("[Pipeline] 真实LLM信号获取失败 %s: %s", symbol, e)
                return (symbol, {"strength": 0.0, "confidence": 0.2})

        # B2.2: 并发执行 (替代串行 for 循环)
        pairs = run_io_batch(
            self.ctx.symbols,
            _worker,
            max_workers=8,
            timeout=60,
            desc="llm_signals",
        )
        return {s: sig for s, sig in pairs if s is not None}

    def _real_etf_signals(self) -> dict[str, dict[str, Any]]:
        """B2.2: ETF 信号 — 并发拉取多 symbol 的匹配 ETF 行情 + 历史数据兜底"""
        if self.data_provider is None:
            return {s: {"strength": 0.0, "confidence": 0.2} for s in self.ctx.symbols}
        etf_candidates = [
            "510300",
            "510500",
            "510050",
            "159915",
            "512100",
            "512010",
            "512480",
            "512760",
            "515030",
            "515790",
        ]
        category_map = {
            "510300": "沪深300",
            "510500": "中证500",
            "510050": "上证50",
            "159915": "创业板",
            "512100": "中证1000",
            "512010": "医药",
            "512480": "半导体",
            "512760": "半导体",
            "515030": "新能源",
            "515790": "光伏",
        }
        sector_keywords = {
            "600519": ["白酒", "消费"],
            "000858": ["白酒", "消费"],
            "601318": ["保险", "金融"],
            "000001": ["金融"],
            "600036": ["银行", "金融"],
            "601398": ["银行", "金融"],
            "600276": ["医药", "创新药"],
            "000063": ["通信", "5G", "科技"],
        }

        def _worker(symbol: str) -> tuple[str, dict[str, Any]]:
            try:
                matched_etf = None
                for code in etf_candidates:
                    cat = category_map.get(code, "")
                    keywords = sector_keywords.get(symbol, [])
                    if cat and any(cat in kw or kw in cat for kw in keywords):
                        matched_etf = code
                        break
                strength = 0.0
                confidence = 0.2
                if matched_etf:
                    raw = self.data_provider.get_market_data(matched_etf) or {}
                    change_pct = raw.get("change_pct", 0.0)
                    try:
                        change_pct = float(change_pct)
                    except Exception:
                        logger.debug("[Pipeline] change_pct 转换失败 symbol=%s raw=%r", symbol, change_pct)
                        change_pct = 0.0
                    if math.isfinite(change_pct) and change_pct != 0.0:
                        strength = float(max(-1.0, min(1.0, change_pct / 10.0)))
                        confidence = 0.6 if abs(strength) >= 0.15 else 0.4
                    else:
                        # B2.2: 原 period="1m" 改用 "5y" superset (命中 cache, 避免重复拉取)
                        # 多 symbol 可能匹配同一 ETF (e.g. 多只银行股都匹配 510300),
                        # _get_or_load_historical 用双检锁保证只拉取一次
                        df = self._get_or_load_historical(matched_etf)
                        if df is not None and not df.empty and "close" in df.columns:
                            s = df["close"].dropna()
                            if len(s) > 10:
                                proxy = float(s.iloc[-1] / s.iloc[-6] - 1)
                                strength = float(max(-1.0, min(1.0, proxy * 10)))
                                confidence = 0.45
                    # B2.2 修复: 旧实现用 `conf = 0.55 if change_pct != 0.0 else confidence`
                    # 错误覆盖了已经算好的 confidence (0.6/0.4/0.45 三档), 改为直接返回 confidence
                    return (symbol, {"strength": strength, "confidence": confidence})
                else:
                    return (symbol, {"strength": 0.0, "confidence": 0.2})
            except Exception as e:
                logger.warning("[Pipeline] 真实ETF信号获取失败 %s: %s", symbol, e)
                return (symbol, {"strength": 0.0, "confidence": 0.2})

        # B2.2: 并发执行 (替代串行 for 循环)
        pairs = run_io_batch(
            self.ctx.symbols,
            _worker,
            max_workers=8,
            timeout=60,
            desc="etf_signals",
        )
        return {s: sig for s, sig in pairs if s is not None}
