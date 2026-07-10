# -*- coding: utf-8 -*-
"""
AI 报告代理 (AI Report Agent)
==============================

功能:
  - 自动化分析: 对抓取的公告/新闻/研报进行 AI 情感分析与摘要
  - 自动化报告: 生成每日投资分析报告 (基于持仓+行情+舆情)
  - 交易信号解读: 将多源信号 (价格预测+新闻+ETF资金流) 融合为可读建议
  - 多 LLM 降级链: 豆包 Speed → DeepSeek → Ollama (复用 15_每日工作流/llm_client.py)

依赖:
  - 15_每日工作流/llm_client.py (LLM 三级降级)
  - utils/web_scraper.py (新闻/公告/研报抓取)
  - utils/tf_price_predictor.py (价格预测, 可选)

使用:
  from utils.ai_report_agent import AIReportAgent
  agent = AIReportAgent()
  daily_report = agent.generate_daily_report(symbols=["002371", "688041"])
  sentiment = agent.analyze_news_sentiment(news_items)
  signal_explain = agent.explain_trade_signals(predictions)

设计原则:
  - 优雅降级: LLM 不可用时返回规则引擎兜底分析
  - 成本控制: 批量合并 prompt, 单次调用处理多标的
  - 可审计: 每次分析记录输入/输出/模型/耗时
"""

import os
import sys
import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

from utils.logger import get_logger

logger = get_logger('ai_report_agent')

# ============================================================
# 导入 LLM 客户端 (15_每日工作流/llm_client.py)
# ============================================================

_LLM_CLIENT_AVAILABLE = False
_chat_fn = None
_generate_analysis_fn = None
_test_connection_fn = None

# 将 15_每日工作流 加入 sys.path
_LLM_CLIENT_PATH = Path(__file__).resolve().parent.parent.parent / "15_每日工作流"
if _LLM_CLIENT_PATH.exists():
    if str(_LLM_CLIENT_PATH) not in sys.path:
        sys.path.insert(0, str(_LLM_CLIENT_PATH))
    try:
        import llm_client  # type: ignore
        _chat_fn = llm_client.chat
        _generate_analysis_fn = llm_client.generate_analysis
        _test_connection_fn = llm_client.test_connection
        _LLM_CLIENT_AVAILABLE = True
        logger.info("AIReportAgent: llm_client.py 已加载 (豆包→DeepSeek→Ollama)")
    except Exception as e:
        logger.warning(f"AIReportAgent: llm_client 加载失败 ({e}), 启用规则引擎兜底")
else:
    logger.warning(f"AIReportAgent: llm_client.py 路径不存在 ({_LLM_CLIENT_PATH}), 启用规则引擎兜底")


# ============================================================
# 数据结构
# ============================================================

@dataclass
class SentimentResult:
    """情感分析结果"""
    title: str
    sentiment: str = "neutral"          # positive/negative/neutral
    score: float = 0.0                  # [-1, 1]
    summary: str = ""                   # AI 摘要
    keywords: List[str] = field(default_factory=list)
    confidence: float = 0.0             # [0, 1]


@dataclass
class AnalysisRecord:
    """分析审计记录"""
    timestamp: str
    analysis_type: str                 # sentiment/daily_report/signal_explain
    model: str = "unknown"
    input_summary: str = ""            # 输入摘要 (前 200 字)
    output_summary: str = ""           # 输出摘要 (前 200 字)
    elapsed_ms: float = 0.0
    success: bool = True
    error: str = ""


@dataclass
class DailyReport:
    """每日投资分析报告"""
    report_date: str
    generated_at: str
    market_overview: str = ""          # 市场总览 (AI 生成)
    portfolio_analysis: str = ""       # 组合分析
    news_highlights: List[Dict] = field(default_factory=list)  # 重要新闻
    sentiment_summary: str = ""        # 情感汇总
    trade_signals: List[Dict] = field(default_factory=list)    # 交易信号
    risk_warnings: List[str] = field(default_factory=list)     # 风险提示
    recommendations: List[str] = field(default_factory=list)   # 建议
    model_used: str = "rule_engine"     # llm 名称 / rule_engine
    raw_llm_output: str = ""


# ============================================================
# AI 报告代理
# ============================================================

class AIReportAgent:
    """AI 自动化分析/报告代理

    降级链:
      1. LLM (豆包/DeepSeek/Ollama) — 智能 AI 分析
      2. 规则引擎 — 关键词匹配 + 简单统计兜底
    """

    # 情感关键词词典 (规则引擎兜底用)
    POSITIVE_WORDS = [
        "利好", "增长", "超预期", "突破", "创新高", "上涨", "盈利", "加仓",
        "增持", "买入", "强劲", "复苏", "景气", "扩张", "订单", "中标",
        "回购", "分红", "获批", "合作", "升级", "龙头",
    ]
    NEGATIVE_WORDS = [
        "利空", "下降", "亏损", "减持", "警示", "风险", "违规", "处罚",
        "退市", "停牌", "暴跌", "下跌", "疲软", "萎缩", "滞销", "商誉减值",
        "质押", "诉讼", "问询", "监管", "爆雷", "违约",
    ]

    # 高严重性负面关键词 (触发交易暂停)
    CRITICAL_NEGATIVE_WORDS = [
        "立案调查", "退市", "重大违规", "财务造假", "证监会处罚",
        "强制退市", "爆雷", "违约", "质押爆仓",
    ]

    def __init__(self, audit_log_dir: Optional[Path] = None):
        self.llm_available = _LLM_CLIENT_AVAILABLE
        self.audit_log_dir = audit_log_dir or Path("data/ai_audit_logs")
        self.audit_log_dir.mkdir(parents=True, exist_ok=True)
        self.audit_records: List[AnalysisRecord] = []

    # ----------------------------------------------------------
    # LLM 调用封装
    # ----------------------------------------------------------

    def _call_llm(self, prompt: str, system: str = "",
                  temperature: float = 0.3, max_tokens: int = 2000) -> Optional[str]:
        """调用 LLM (失败返回 None, 触发降级)"""
        if not self.llm_available or _chat_fn is None:
            return None
        try:
            t0 = time.time()
            result = _chat_fn(
                prompt=prompt,
                system=system or "你是资深A股投研分析师,擅长基本面分析与舆情解读。",
                temperature=temperature,
                max_tokens=max_tokens,
            )
            elapsed_ms = (time.time() - t0) * 1000
            # 提取模型标签
            model = "unknown"
            if result.startswith("[") and "] " in result[:50]:
                model = result.split("] ", 1)[0][1:]
                result = result.split("] ", 1)[1]
            self._record_audit("llm_call", model, prompt, result, elapsed_ms, True)
            return result
        except Exception as e:
            logger.warning(f"LLM 调用失败: {e}")
            self._record_audit("llm_call", "error", prompt, str(e), 0, False, str(e))
            return None

    def _record_audit(self, analysis_type: str, model: str,
                      input_text: str, output_text: str,
                      elapsed_ms: float, success: bool, error: str = ""):
        """记录审计"""
        record = AnalysisRecord(
            timestamp=datetime.now().isoformat(),
            analysis_type=analysis_type,
            model=model,
            input_summary=input_text[:200],
            output_summary=output_text[:200] if output_text else "",
            elapsed_ms=elapsed_ms,
            success=success,
            error=error,
        )
        self.audit_records.append(record)
        # 保留最近 100 条
        if len(self.audit_records) > 100:
            self.audit_records = self.audit_records[-100:]

    # ----------------------------------------------------------
    # 情感分析
    # ----------------------------------------------------------

    def analyze_news_sentiment(self, news_items: List[Dict],
                               use_llm: bool = True) -> List[SentimentResult]:
        """批量分析新闻情感

        Args:
            news_items: NewsItem.to_dict() 列表
            use_llm: 是否使用 LLM (False 时仅用规则引擎)

        Returns:
            情感分析结果列表
        """
        if not news_items:
            return []

        results: List[SentimentResult] = []

        # 批量调用 LLM (合并多条新闻为单次 prompt, 节省成本)
        if use_llm and self.llm_available and len(news_items) <= 10:
            batch_results = self._llm_batch_sentiment(news_items)
            if batch_results is not None:
                return batch_results

        # 规则引擎兜底 (关键词匹配)
        for item in news_items:
            result = self._rule_sentiment(item)
            results.append(result)

        return results

    def _llm_batch_sentiment(self, news_items: List[Dict]) -> Optional[List[SentimentResult]]:
        """LLM 批量情感分析 (单次调用处理多条新闻)"""
        # 构造批量 prompt
        items_text = []
        for i, item in enumerate(news_items, 1):
            title = item.get("title", "")
            content = item.get("content", "")[:200]
            items_text.append(f"{i}. 标题: {title}\n   内容: {content}")
        items_block = "\n".join(items_text)

        prompt = f"""请对以下{len(news_items)}条新闻逐条进行情感分析, 返回 JSON 数组格式:

{items_block}

输出格式 (严格 JSON, 不要 markdown 代码块):
[
  {{"index": 1, "sentiment": "positive/negative/neutral", "score": 0.8, "summary": "一句话摘要", "keywords": ["关键词1", "关键词2"]}},
  ...
]

要求:
- sentiment: positive(利好)/negative(利空)/neutral(中性)
- score: [-1, 1], 正数=利好, 负数=利空
- summary: 不超过50字
- keywords: 2-5个关键词
"""

        result = self._call_llm(prompt, temperature=0.1, max_tokens=1500)
        if not result:
            return None

        try:
            # 尝试解析 JSON (容错: 去除可能的 markdown 代码块标记)
            clean = result.strip()
            if clean.startswith("```"):
                clean = re.sub(r"^```\w*\n?", "", clean)
                clean = re.sub(r"\n?```$", "", clean)
            data = json.loads(clean)
            results = []
            for entry in data:
                idx = int(entry.get("index", 0)) - 1
                if 0 <= idx < len(news_items):
                    results.append(SentimentResult(
                        title=news_items[idx].get("title", ""),
                        sentiment=entry.get("sentiment", "neutral"),
                        score=float(entry.get("score", 0)),
                        summary=entry.get("summary", ""),
                        keywords=entry.get("keywords", []),
                        confidence=0.8,
                    ))
            return results if results else None
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(f"LLM 情感分析 JSON 解析失败: {e}")
            return None

    def _rule_sentiment(self, item: Dict) -> SentimentResult:
        """规则引擎情感分析 (关键词匹配)"""
        title = item.get("title", "")
        content = item.get("content", "")
        text = title + " " + content

        pos_count = sum(1 for w in self.POSITIVE_WORDS if w in text)
        neg_count = sum(1 for w in self.NEGATIVE_WORDS if w in text)
        critical_count = sum(1 for w in self.CRITICAL_NEGATIVE_WORDS if w in text)

        # 重大负面 → 强负面
        if critical_count > 0:
            sentiment = "negative"
            score = -0.9
            confidence = 0.95
        elif neg_count > pos_count:
            sentiment = "negative"
            score = -min(0.3 * neg_count, 0.9)
            confidence = min(0.5 + 0.1 * neg_count, 0.85)
        elif pos_count > neg_count:
            sentiment = "positive"
            score = min(0.3 * pos_count, 0.9)
            confidence = min(0.5 + 0.1 * pos_count, 0.85)
        else:
            sentiment = "neutral"
            score = 0.0
            confidence = 0.5

        # 提取关键词 (匹配到的词典词)
        keywords = [w for w in self.POSITIVE_WORDS + self.NEGATIVE_WORDS if w in text][:5]

        return SentimentResult(
            title=title,
            sentiment=sentiment,
            score=score,
            summary=f"规则引擎: 利好词{pos_count}个, 利空词{neg_count}个",
            keywords=keywords,
            confidence=confidence,
        )

    # ----------------------------------------------------------
    # 每日报告生成
    # ----------------------------------------------------------

    def generate_daily_report(
        self,
        symbols: List[str],
        positions_data: Optional[Dict] = None,
        predictions: Optional[List[Dict]] = None,
        news_items: Optional[List[Dict]] = None,
        report_date: Optional[str] = None,
    ) -> DailyReport:
        """生成每日投资分析报告

        Args:
            symbols: 持仓标的代码列表
            positions_data: 持仓数据 (positions.json 内容)
            predictions: 价格预测结果列表 (tf_price_predictor 输出)
            news_items: 新闻/公告列表 (web_scraper 输出)
            report_date: 报告日期 (默认今天)

        Returns:
            DailyReport 对象
        """
        report_date = report_date or datetime.now().strftime("%Y-%m-%d")
        report = DailyReport(
            report_date=report_date,
            generated_at=datetime.now().isoformat(),
        )

        # 情感分析 (无论 LLM 是否可用都执行)
        if news_items:
            sentiments = self.analyze_news_sentiment(news_items, use_llm=True)
            report.sentiment_summary = self._summarize_sentiments(sentiments)
            # 筛选重要新闻 (强负面/强正面)
            for s in sentiments:
                if abs(s.score) >= 0.5:
                    report.news_highlights.append({
                        "title": s.title,
                        "sentiment": s.sentiment,
                        "score": s.score,
                        "summary": s.summary,
                    })

        # 风险预警 (检测重大负面新闻)
        for item in news_items or []:
            text = item.get("title", "") + " " + item.get("content", "")
            for word in self.CRITICAL_NEGATIVE_WORDS:
                if word in text:
                    report.risk_warnings.append(
                        f"⚠️ 重大负面: 检测到 '{word}' (标的: {item.get('symbol', '未知')})"
                    )
                    break

        # 交易信号整理
        if predictions:
            for pred in predictions:
                direction = pred.get("direction", "NEUTRAL")
                confidence = pred.get("confidence", 0)
                symbol = pred.get("symbol", "")
                if direction != "NEUTRAL" and confidence >= 0.6:
                    report.trade_signals.append({
                        "symbol": symbol,
                        "direction": direction,
                        "confidence": confidence,
                        "target_price": pred.get("target_price", 0),
                        "method": pred.get("method", "unknown"),
                    })

        # LLM 生成市场总览与组合分析
        if self.llm_available:
            prompt = self._build_daily_report_prompt(
                symbols, positions_data, predictions, report.news_highlights,
                report.risk_warnings, report.trade_signals,
            )
            llm_output = self._call_llm(prompt, temperature=0.3, max_tokens=2500)
            if llm_output:
                report.market_overview = llm_output
                report.model_used = "llm"
                # 简单提取建议 (以数字开头的行)
                for line in llm_output.split("\n"):
                    line = line.strip()
                    if re.match(r"^\d+[\.、)] ", line):
                        report.recommendations.append(line)
            else:
                # LLM 不可用, 规则引擎兜底
                report.market_overview = self._rule_market_overview(
                    symbols, predictions, report.risk_warnings
                )
                report.model_used = "rule_engine"
        else:
            report.market_overview = self._rule_market_overview(
                symbols, predictions, report.risk_warnings
            )
            report.model_used = "rule_engine"

        report.portfolio_analysis = self._rule_portfolio_analysis(
            symbols, positions_data, predictions
        )

        return report

    def _build_daily_report_prompt(
        self, symbols: List[str], positions_data: Optional[Dict],
        predictions: Optional[List[Dict]], news_highlights: List[Dict],
        risk_warnings: List[str], trade_signals: List[Dict],
    ) -> str:
        """构造每日报告 prompt"""
        # 持仓摘要
        pos_summary = "无持仓数据"
        if positions_data and "positions" in positions_data:
            lines = []
            for code, pos in list(positions_data["positions"].items())[:10]:
                if not isinstance(pos, dict):
                    continue
                name = pos.get("name", code)
                shares = pos.get("shares", 0)
                weight = pos.get("weight", 0)
                lines.append(f"  - {code} {name}: {shares}股, 权重{weight:.0%}")
            pos_summary = "\n".join(lines) if lines else "持仓为空"

        # 预测摘要
        pred_summary = "无预测数据"
        if predictions:
            lines = []
            for p in predictions[:5]:
                lines.append(
                    f"  - {p.get('symbol','')}: {p.get('direction','')} "
                    f"目标价{p.get('target_price',0):.2f} "
                    f"置信度{p.get('confidence',0):.0%} ({p.get('method','')})"
                )
            pred_summary = "\n".join(lines)

        # 新闻摘要
        news_summary = "无重要新闻" if not news_highlights else "\n".join(
            f"  - [{n['sentiment']}] {n['title']} (score={n['score']:.2f})"
            for n in news_highlights[:5]
        )

        # 风险
        risk_summary = "无风险预警" if not risk_warnings else "\n".join(
            f"  - {w}" for w in risk_warnings
        )

        # 信号
        signal_summary = "无交易信号" if not trade_signals else "\n".join(
            f"  - {s['symbol']}: {s['direction']} 置信度{s['confidence']:.0%}"
            for s in trade_signals[:5]
        )

        return f"""请生成今日投资分析报告。以下是市场数据摘要:

【持仓标的】
{pos_summary}

【价格预测】
{pred_summary}

【重要新闻】
{news_summary}

【风险预警】
{risk_summary}

【交易信号】
{signal_summary}

请按以下结构输出 (不要 markdown 代码块):
1. 市场总览 (2-3句, 描述今日市场情绪与主线)
2. 组合分析 (2-3句, 评估持仓表现与建议)
3. 操作建议 (3-5条编号建议, 每条一句话)
4. 风险提示 (1-2句)
"""

    def _rule_market_overview(self, symbols: List[str],
                              predictions: Optional[List[Dict]],
                              risk_warnings: List[str]) -> str:
        """规则引擎市场总览 (LLM 不可用时兜底)"""
        if risk_warnings:
            return (
                f"今日检测到 {len(risk_warnings)} 条重大风险预警, "
                f"建议暂停相关标的交易, 优先排查负面新闻。"
            )
        if predictions:
            up_count = sum(1 for p in predictions if p.get("direction") == "UP")
            down_count = sum(1 for p in predictions if p.get("direction") == "DOWN")
            if up_count > down_count:
                return f"今日 {up_count}/{len(predictions)} 个标的价格预测上涨, 市场情绪偏多。"
            elif down_count > up_count:
                return f"今日 {down_count}/{len(predictions)} 个标的价格预测下跌, 市场情绪偏空。"
            return "多空均衡, 市场震荡。"
        return "暂无足够数据生成市场总览。"

    def _rule_portfolio_analysis(self, symbols: List[str],
                                  positions_data: Optional[Dict],
                                  predictions: Optional[List[Dict]]) -> str:
        """规则引擎组合分析"""
        if not positions_data or "positions" not in positions_data:
            return "持仓数据不可用。"

        total_symbols = len(symbols)
        built_count = sum(
            1 for s in symbols
            if positions_data["positions"].get(s, {}).get("shares", 0) > 0
        ) if positions_data else 0

        pred_up = sum(1 for p in (predictions or []) if p.get("direction") == "UP")
        return (
            f"组合共 {total_symbols} 个标的, 已建仓 {built_count} 个 "
            f"({built_count/total_symbols:.0%}), "
            f"其中 {pred_up} 个预测上涨。"
        )

    def _summarize_sentiments(self, sentiments: List[SentimentResult]) -> str:
        """汇总情感分析结果"""
        if not sentiments:
            return "无新闻数据"
        pos = sum(1 for s in sentiments if s.sentiment == "positive")
        neg = sum(1 for s in sentiments if s.sentiment == "negative")
        neu = sum(1 for s in sentiments if s.sentiment == "neutral")
        avg_score = sum(s.score for s in sentiments) / len(sentiments)
        return (
            f"共分析 {len(sentiments)} 条新闻: "
            f"利好 {pos} 条, 利空 {neg} 条, 中性 {neu} 条, "
            f"平均情感分 {avg_score:+.2f}"
        )

    # ----------------------------------------------------------
    # 交易信号解读
    # ----------------------------------------------------------

    def explain_trade_signals(self, predictions: List[Dict],
                              news_sentiments: Optional[List[Dict]] = None) -> str:
        """将交易信号转化为可读建议

        Args:
            predictions: 价格预测结果
            news_sentiments: 新闻情感 (可选)

        Returns:
            可读的信号解读文本
        """
        if not predictions:
            return "暂无交易信号。"

        # 构造 prompt
        pred_text = []
        for p in predictions[:8]:
            pred_text.append(
                f"- {p.get('symbol','')}: 方向={p.get('direction','')} "
                f"目标价={p.get('target_price',0):.2f} "
                f"置信度={p.get('confidence',0):.0%} "
                f"方法={p.get('method','')}"
            )
        pred_block = "\n".join(pred_text)

        sentiment_block = "无新闻情感数据"
        if news_sentiments:
            sent_lines = []
            for s in news_sentiments[:5]:
                sent_lines.append(
                    f"- {s.get('title','')}: {s.get('sentiment','')} "
                    f"score={s.get('score',0):.2f}"
                )
            sentiment_block = "\n".join(sent_lines)

        prompt = f"""请解读以下交易信号, 给出操作建议 (不要 markdown 代码块):

【价格预测信号】
{pred_block}

【新闻情感信号】
{sentiment_block}

请输出:
1. 信号汇总 (2-3句)
2. 强信号标的 (置信度>0.7 的标的, 列出建议操作)
3. 弱信号标的 (置信度<0.5 的标的, 观望建议)
4. 风险提示 (1-2句)
"""

        result = self._call_llm(prompt, temperature=0.3, max_tokens=1200)
        if result:
            return result
        # 兜底
        strong = [p for p in predictions if p.get("confidence", 0) >= 0.7]
        weak = [p for p in predictions if p.get("confidence", 0) < 0.5]
        lines = [
            f"信号汇总: 共 {len(predictions)} 个预测, 强信号 {len(strong)} 个, 弱信号 {len(weak)} 个。",
        ]
        if strong:
            lines.append("强信号标的:")
            for p in strong:
                lines.append(f"  - {p.get('symbol','')}: {p.get('direction','')} 目标价{p.get('target_price',0):.2f}")
        if weak:
            lines.append("弱信号标的 (建议观望):")
            for p in weak:
                lines.append(f"  - {p.get('symbol','')}: {p.get('direction','')}")
        return "\n".join(lines)

    # ----------------------------------------------------------
    # 持久化
    # ----------------------------------------------------------

    def save_report(self, report: DailyReport, output_dir: Optional[Path] = None) -> Path:
        """保存报告到文件 (JSON + Markdown)"""
        output_dir = output_dir or Path("data/ai_reports")
        output_dir.mkdir(parents=True, exist_ok=True)

        # JSON
        json_path = output_dir / f"ai_report_{report.report_date}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report.__dict__, f, ensure_ascii=False, indent=2, default=str)

        # Markdown
        md_path = output_dir / f"ai_report_{report.report_date}.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# AI 投资分析报告 {report.report_date}\n\n")
            f.write(f"**生成时间**: {report.generated_at}  \n")
            f.write(f"**模型**: {report.model_used}\n\n")
            f.write("## 一、市场总览\n\n")
            f.write(report.market_overview + "\n\n")
            f.write("## 二、组合分析\n\n")
            f.write(report.portfolio_analysis + "\n\n")
            f.write("## 三、情感汇总\n\n")
            f.write(report.sentiment_summary + "\n\n")
            if report.news_highlights:
                f.write("## 四、重要新闻\n\n")
                for n in report.news_highlights:
                    f.write(f"- [{n['sentiment']}] {n['title']} (score={n['score']:.2f})\n")
                f.write("\n")
            if report.trade_signals:
                f.write("## 五、交易信号\n\n")
                for s in report.trade_signals:
                    f.write(f"- {s['symbol']}: {s['direction']} 置信度{s['confidence']:.0%}\n")
                f.write("\n")
            if report.risk_warnings:
                f.write("## 六、风险预警\n\n")
                for w in report.risk_warnings:
                    f.write(f"- {w}\n")
                f.write("\n")
            if report.recommendations:
                f.write("## 七、操作建议\n\n")
                for r in report.recommendations:
                    f.write(f"{r}\n")
                f.write("\n")

        return md_path

    def save_audit_logs(self) -> Path:
        """保存审计日志"""
        log_path = self.audit_log_dir / f"audit_{datetime.now().strftime('%Y%m%d')}.json"
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(
                [r.__dict__ for r in self.audit_records],
                f, ensure_ascii=False, indent=2, default=str,
            )
        return log_path

    # ----------------------------------------------------------
    # 状态查询
    # ----------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """获取 AI 代理状态"""
        status = {
            "llm_available": self.llm_available,
            "audit_records_count": len(self.audit_records),
            "audit_log_dir": str(self.audit_log_dir),
        }
        if self.llm_available and _test_connection_fn is not None:
            try:
                status["llm_status"] = _test_connection_fn()
            except Exception as e:
                status["llm_status"] = {"error": str(e)}
        return status


# ============================================================
# 便捷函数
# ============================================================

_agent_instance: Optional[AIReportAgent] = None


def get_agent() -> AIReportAgent:
    """获取全局 AIReportAgent 实例 (单例)"""
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = AIReportAgent()
    return _agent_instance


def analyze_sentiment(news_items: List[Dict]) -> List[Dict]:
    """便捷函数: 批量情感分析"""
    results = get_agent().analyze_news_sentiment(news_items)
    return [r.__dict__ for r in results]


def generate_daily_report(symbols: List[str], **kwargs) -> Dict:
    """便捷函数: 生成每日报告"""
    report = get_agent().generate_daily_report(symbols, **kwargs)
    return report.__dict__


# ============================================================
# 自检
# ============================================================

import re  # 延迟导入 (自检时需要)

def self_test() -> bool:
    """模块自检 (不发起 LLM 调用, 仅验证类与规则引擎)"""
    try:
        agent = AIReportAgent()
        assert agent.llm_available in (True, False)

        # 测试规则引擎情感分析
        fake_news = [
            {"title": "某公司获重大订单 利好业绩", "content": "签订10亿合同", "symbol": "002371"},
            {"title": "某公司被立案调查", "content": "财务造假", "symbol": "688041"},
            {"title": "普通公告", "content": "董事会决议", "symbol": "600900"},
        ]
        # 强制用规则引擎 (use_llm=False)
        results = agent.analyze_news_sentiment(fake_news, use_llm=False)
        assert len(results) == 3
        assert results[0].sentiment == "positive"  # "利好"
        assert results[1].sentiment == "negative"  # "立案调查"
        assert results[1].score <= -0.9  # 重大负面
        assert results[2].sentiment == "neutral"

        # 测试每日报告生成 (规则引擎)
        report = agent.generate_daily_report(
            symbols=["002371", "688041"],
            positions_data={"positions": {"002371": {"name": "北方华创", "shares": 100, "weight": 0.04}}},
            predictions=[
                {"symbol": "002371", "direction": "UP", "target_price": 825.0, "confidence": 0.8, "method": "tensorflow"},
                {"symbol": "688041", "direction": "DOWN", "target_price": 340.0, "confidence": 0.6, "method": "arima"},
            ],
            news_items=fake_news,
        )
        assert report.report_date
        assert report.market_overview
        assert report.portfolio_analysis
        assert report.model_used in ("llm", "rule_engine")
        # 重大负面应触发风险预警
        assert len(report.risk_warnings) > 0

        # 测试信号解读 (规则引擎兜底)
        explain = agent.explain_trade_signals([
            {"symbol": "002371", "direction": "UP", "target_price": 825.0, "confidence": 0.8, "method": "tf"},
        ])
        assert "信号汇总" in explain

        # 测试状态
        status = agent.get_status()
        assert "llm_available" in status

        print("[OK] ai_report_agent.py 自检通过")
        print(f"  - LLM 可用: {status['llm_available']}")
        print(f"  - 审计记录数: {status['audit_records_count']}")
        print(f"  - 测试报告模型: {report.model_used}")
        print(f"  - 风险预警数: {len(report.risk_warnings)}")
        return True
    except Exception as e:
        import traceback
        print(f"[FAIL] ai_report_agent.py 自检失败: {e}")
        traceback.print_exc()
        return False


if __name__ == "__main__":
    self_test()
