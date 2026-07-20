# -*- coding: utf-8 -*-
import pathlib

p = pathlib.Path(r"E:\各种PY程序\28-终极量化交易系统7.1\institutional_pipeline_runner.py")
text = p.read_text(encoding="utf-8")

old_block = """    def _step_signal_fusion(self, alpha_report: Any) -> List[FusionSignal]:
        logger.info("[Pipeline] Step 3: 信号融合")
        alpha_signals = self._real_alpha_signals()
        llm_signals = self._mock_llm_signals()
        etf_signals = self._mock_etf_signals()
        macro_signals = self._real_macro_signals()
        return self.signal_fusion.fuse(alpha_signals, llm_signals, etf_signals, macro_signals)"""

new_block = """    def _step_signal_fusion(self, alpha_report: Any) -> List[FusionSignal]:
        logger.info("[Pipeline] Step 3: 信号融合")
        alpha_signals = self._real_alpha_signals()
        llm_signals = self._real_llm_signals()
        etf_signals = self._real_etf_signals()
        macro_signals = self._real_macro_signals()
        return self.signal_fusion.fuse(alpha_signals, llm_signals, etf_signals, macro_signals)"""

if old_block not in text:
    raise SystemExit("未找到目标代码块")

text = text.replace(old_block, new_block)

anchor = """    def _real_macro_signals(self) -> Dict[str, Dict[str, Any]]:
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

extra = """
    def _real_llm_signals(self) -> Dict[str, Dict[str, Any]]:
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
        return signals

    def _real_etf_signals(self) -> Dict[str, Dict[str, Any]]:
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
        return signals
"""

if anchor not in text:
    raise SystemExit("未找到插入锚点")

text = text.replace(anchor, anchor + "\n\n" + extra)
p.write_text(text, encoding="utf-8")
print("patched pipeline signal fusion")
