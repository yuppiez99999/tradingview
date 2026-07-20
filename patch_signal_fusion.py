# -*- coding: utf-8 -*-
"""修复信号融合：LLM/ETF/宏观改为基于历史价格的稳定替代信号"""
import pathlib

p = pathlib.Path(r"E:\各种PY程序\28-终极量化交易系统7.1\institutional_pipeline_runner.py")
text = p.read_text(encoding="utf-8")

old_macro = """    def _real_macro_signals(self) -> Dict[str, Dict[str, Any]]:
        if self.data_provider is None:
            return {"macro_index": {"strength": 0.0, "confidence": 0.2}}
        try:
            macro = self.data_provider.get_external_macro()
            sentiment: Dict[str, Any] = {}
            if macro and isinstance(macro, dict):
                sentiment = macro.get("risk_sentiment", {}) or {}
            if not sentiment or not isinstance(sentiment, dict):
                sentiment = self.data_provider.get_risk_sentiment() or {}
            score = float(sentiment.get("score", 0.0)) if isinstance(sentiment, dict) else 0.0
            strength = float(max(-1.0, min(1.0, score)))
            return {"macro_index": {"strength": strength, "confidence": 0.5}}
        except Exception as e:
            logger.warning("[Pipeline] 真实宏观信号获取失败: %s", e)
            return {"macro_index": {"strength": 0.0, "confidence": 0.2}}"""

new_macro = """    def _real_macro_signals(self) -> Dict[str, Dict[str, Any]]:
        if self.data_provider is None:
            return {"macro_index": {"strength": 0.0, "confidence": 0.2}}
        try:
            macro = self.data_provider.get_external_macro()
            sentiment: Dict[str, Any] = {}
            if macro and isinstance(macro, dict):
                sentiment = macro.get("risk_sentiment", {}) or {}
            if not sentiment or not isinstance(sentiment, dict):
                sentiment = self.data_provider.get_risk_sentiment() or {}
            score = float(sentiment.get("score", 0.0)) if isinstance(sentiment, dict) else 0.0
            strength = float(max(-1.0, min(1.0, score)))

            # 若外部宏观不可用，改用全组合历史波动率代理宏观风险情绪
            if strength == 0.0:
                try:
                   rets = []
                    for symbol in self.ctx.symbols:
                        df = self.data_provider.get_historical_data(symbol, period='3m')
                        if df is not None and not df.empty and 'close' in df.columns:
                            s = df['close'].dropna()
                            if len(s) > 5:
                                rets.append(float(s.iloc[-1] / s.iloc[-6] - 1))
                    if rets:
                        macro_proxy = float(max(-1.0, min(1.0, sum(rets) / len(rets) * 8)))
                        if macro_proxy != 0.0:
                            strength = macro_proxy
                except Exception as e:
                    logger.debug("[Pipeline] 宏观代理信号计算失败: %s", e)

            return {"macro_index": {"strength": strength, "confidence": 0.5}}
        except Exception as e:
            logger.warning("[Pipeline] 真实宏观信号获取失败: %s", e)
            return {"macro_index": {"strength": 0.0, "confidence": 0.2}}"""

if old_macro not in text:
    raise SystemExit("macro target not found")
text = text.replace(old_macro, new_macro)

old_llm = """    def _real_llm_signals(self) -> Dict[str, Dict[str, Any]]:
        if self.data_provider is None:
            return {s: {"strength": 0.0, "confidence": 0.2} for s in self.ctx.symbols}
        signals: Dict[str, Dict[str, Any]] = {}
        for symbol in self.ctx.symbols:
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
                signals[symbol] = {"strength": strength, "confidence": confidence}
            except Exception as e:
                logger.warning("[Pipeline] 真实LLM信号获取失败 %s: %s", symbol, e)
                signals[symbol] = {"strength": 0.0, "confidence": 0.2}
        return signals"""

new_llm = """    def _real_llm_signals(self) -> Dict[str, Dict[str, Any]]:
        if self.data_provider is None:
            return {s: {"strength": 0.0, "confidence": 0.2} for s in self.ctx.symbols}
        signals: Dict[str, Dict[str, Any]] = {}
        for symbol in self.ctx.symbols:
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

                # 若新闻情感为空，改用短期价格动量作为 LLM 代理信号
                if strength == 0.0 and confidence <= 0.2:
                    try:
                        df = self.data_provider.get_historical_data(symbol, period='1m')
                        if df is not None and not df.empty and 'close' in df.columns:
                            s = df['close'].dropna()
                            if len(s) > 10:
                                proxy = float(s.iloc[-1] / s.iloc[-6] - 1)
                                strength = float(max(-1.0, min(1.0, proxy * 12)))
                                confidence = 0.45
                    except Exception as e:
                        logger.debug("[Pipeline] LLM代理信号计算失败 %s: %s", symbol, e)

                signals[symbol] = {"strength": strength, "confidence": confidence}
            except Exception as e:
                logger.warning("[Pipeline] 真实LLM信号获取失败 %s: %s", symbol, e)
                signals[symbol] = {"strength": 0.0, "confidence": 0.2}
        return signals"""

if old_llm not in text:
    raise SystemExit("llm target not found")
text = text.replace(old_llm, new_llm)

old_etf = """    def _real_etf_signals(self) -> Dict[str, Dict[str, Any]]:
        if self.data_provider is None:
            return {s: {"strength": 0.0, "confidence": 0.2} for s in self.ctx.symbols}
        signals: Dict[str, Dict[str, Any]] = {}
        etf_candidates = [
            "510300", "510500", "510050", "159915", "512100", "512010", "512480", "512760", "515030", "515790",
        ]
        category_map = {
            "510300": "沪深300", "510500": "中证500", "510050": "上证50", "159915": "创业板",
            "512100": "中证1000", "512010": "医药", "512480": "半导体", "512760": "半导体", "515030": "新能源", "515790": "光伏",
        }
        sector_keywords = {
            "600519": ["白酒", "消费"], "000858": ["白酒", "消费"], "601318": ["保险", "金融"], "000001": ["金融"],
            "600036": ["银行", "金融"], "601398": ["银行", "金融"], "600276": ["医药", "创新药"], "000063": ["通信", "5G", "科技"],
        }
        for symbol in self.ctx.symbols:
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
                        change_pct = 0.0
                    if math.isfinite(change_pct):
                        strength = float(max(-1.0, min(1.0, change_pct / 10.0)))
                        confidence = 0.6 if abs(strength) >= 0.15 else 0.4
                signals[symbol] = {"strength": strength, "confidence": confidence}
            except Exception as e:
                logger.warning("[Pipeline] 真实ETF信号获取失败 %s: %s", symbol, e)
                signals[symbol] = {"strength": 0.0, "confidence": 0.2}
        return signals"""

new_etf = """    def _real_etf_signals(self) -> Dict[str, Dict[str, Any]]:
        if self.data_provider is None:
            return {s: {"strength": 0.0, "confidence": 0.2} for s in self.ctx.symbols}
        signals: Dict[str, Dict[str, Any]] = {}
        etf_candidates = [
            "510300", "510500", "510050", "159915", "512100", "512010", "512480", "512760", "515030", "515790",
        ]
        category_map = {
            "510300": "沪深300", "510500": "中证500", "510050": "上证50", "159915": "创业板",
            "512100": "中证1000", "512010": "医药", "512480": "半导体", "512760": "半导体", "515030": "新能源", "515790": "光伏",
        }
        sector_keywords = {
            "600519": ["白酒", "消费"], "000858": ["白酒", "消费"], "601318": ["保险", "金融"], "000001": ["金融"],
            "600036": ["银行", "金融"], "601398": ["银行", "金融"], "600276": ["医药", "创新药"], "000063": ["通信", "5G", "科技"],
        }
        for symbol in self.ctx.symbols:
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
                        change_pct = 0.0
                    if math.isfinite(change_pct) and change_pct != 0.0:
                        strength = float(max(-1.0, min(1.0, change_pct / 10.0)))
                        confidence = 0.6 if abs(strength) >= 0.15 else 0.4
                    else:
                        # 实时 ETF 行情不可用时，改历史价格代理
                        df = self.data_provider.get_historical_data(matched_etf, period='1m')
                        if df is not None and not df.empty and 'close' in df.columns:
                            s = df['close'].dropna()
                            if len(s) > 10:
                                proxy = float(s.iloc[-1] / s.iloc[-6] - 1)
                                strength = float(max(-1.0, min(1.0, proxy * 10)))
                                confidence = 0.45
                signals[symbol] = {"strength": strength, "confidence": confidence}
            except Exception as e:
                logger.warning("[Pipeline] 真实ETF信号获取失败 %s: %s", symbol, e)
                signals[symbol] = {"strength": 0.0, "confidence": 0.2}
        return signals"""

if old_etf not in text:
    raise SystemExit("etf target not found")
text = text.replace(old_etf, new_etf)

p.write_text(text, encoding="utf-8")
print("patched signal fusion fallback")
