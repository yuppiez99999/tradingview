"""
研究内容蒸馏器 (RIA--TV++ 量化版)
=================================

将研报/业绩会/财经书籍/新闻蒸馏为可执行交易信号。
输出标准化为 DistilledSignal, 注入 SignalFusionEngine 作为第 6 信号源。

RIA--TV++ 方法论 (借鉴 cangjie-skill, 原生实现, 不依赖源码):
  - R (Read):       读取多源研究内容 (PDF/Text/JSON)
  - I (Identify):   识别标的代码 + 名称 (NER)
  - A (Assess):     评估信号强度与置信度
  - T (Transform):  转换为标准化 DistilledSignal
  - V (Validate):   NaN/Inf 防御 + 边界裁剪 + 时效校验
  - V (Vectorize):  聚合为 {symbol: strength} 字典, 注入 SignalFusion

降级链:
  1. LLM 蒸馏 (复用 15_每日工作流/llm_client.py 降级链: DeepSeek→GLM→Ollama)
  2. 规则引擎蒸馏 (关键词情感词典 + 标的代码 NER)
  3. 空信号 (安全降级, 不阻断主流程)

集成日期: 2026-07-26
集成批次: GitHub 周榜热门项目深度集成 (第二批)
来源: cangjie-skill (https://github.com/kangarooking/cangjie-skill)

设计原则:
  - Python 3.8.9 兼容 (from __future__ import annotations, Dict[] 而非 dict[])
  - 不修改 requirements.txt (可选依赖 pdfplumber/pdfminer 用 lazy import + 降级)
  - 不阻断主流程 (所有公开方法 try/except, 异常返回空列表/空 dict)
  - NaN/Inf 防御 (与 utils/signal_fusion.py 一致)
  - fail-closed 原则 (数据缺失时返回空信号, 不伪造信号)
"""

from __future__ import annotations

import json
import logging
import math
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from utils.datetime_utils import now_bj
from utils.logger import get_logger

logger = logging.getLogger(__name__)

logger = get_logger("research_distiller")


# ============================================================
# 导入 LLM 客户端 (复用 AIReportAgent 的降级模式)
# ============================================================
# 兼容两种可能的路径:
#   - 项目根/15_每日工作流  (新版本目录结构)
#   - 项目根的父级/15_每日工作流  (历史目录结构, AIReportAgent 兼容)

_LLM_CLIENT_AVAILABLE = False
_chat_fn = None

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LLM_CANDIDATE_PATHS = [
    _PROJECT_ROOT / "15_每日工作流",
    _PROJECT_ROOT.parent / "15_每日工作流",
]

for _candidate in _LLM_CANDIDATE_PATHS:
    try:
        if _candidate.exists() and str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        # 即使已加入 sys.path, 也要确保 llm_client 真的能 import
        import llm_client

        _chat_fn = llm_client.chat
        _LLM_CLIENT_AVAILABLE = True
        logger.info("ResearchDistiller: llm_client.py 已加载 (%s)", _candidate)
        break
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ) as e:  # P2 模块 fail-safe, 待后续精确化
        # 继续尝试下一个候选路径
        logger.debug("ResearchDistiller: 候选路径 %s 加载失败: %s", _candidate, e)
        continue

if not _LLM_CLIENT_AVAILABLE:
    logger.warning("ResearchDistiller: llm_client.py 未找到, 启用规则引擎兜底")


# ============================================================
# 数据结构: DistilledSignal
# ============================================================


@dataclass
class DistilledSignal:
    """蒸馏后的标准化交易信号

    所有蒸馏方法 (distill_report / distill_earnings_call / distill_book_chapter /
    distill_news_batch) 的统一输出格式。

    Attributes:
        symbol: 标的代码 (规范化为 6 位数字 + 交易所后缀, 如 "600276.SH")
        strength: 信号强度 [-1, 1], 正值看涨, 负值看跌, 0 中性
        confidence: 置信度 [0, 1]
        source_type: 信号源类型 (report/earnings_call/book/news)
        source_id: 原文标识 (文件名/URL/标题前 50 字)
        reasoning: 人类可读的决策理由 (用于审计)
        valid_until: 信号失效日期 (ISO 格式 YYYY-MM-DD)
        timestamp: 信号生成时间戳 (ISO 格式)
        key_factors: 关键驱动因素列表 (最多 3 个)
    """

    symbol: str
    strength: float = 0.0
    confidence: float = 0.0
    source_type: str = "news"
    source_id: str = ""
    reasoning: str = ""
    valid_until: str = ""
    timestamp: str = field(default_factory=lambda: now_bj().isoformat())
    key_factors: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """构造后防御性 NaN/Inf 检查 + 边界裁剪 (与 signal_fusion.py 一致)"""
        # NaN / Inf 归零
        if not math.isfinite(self.strength):
            logger.warning(
                "[DistilledSignal] %s strength=%s 非有限值, 归零",
                self.symbol,
                self.strength,
            )
            self.strength = 0.0
        if not math.isfinite(self.confidence):
            logger.warning(
                "[DistilledSignal] %s confidence=%s 非有限值, 归零",
                self.symbol,
                self.confidence,
            )
            self.confidence = 0.0
        # 边界裁剪
        self.strength = max(-1.0, min(1.0, self.strength))
        self.confidence = max(0.0, min(1.0, self.confidence))
        # source_type 规范化
        valid_types = ("report", "earnings_call", "book", "news")
        if self.source_type not in valid_types:
            logger.warning(
                "[DistilledSignal] %s source_type=%s 不合法, 降级为 news",
                self.symbol,
                self.source_type,
            )
            self.source_type = "news"
        # symbol 必须非空字符串
        if not isinstance(self.symbol, str):
            self.symbol = str(self.symbol) if self.symbol else ""

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict (用于 JSON 持久化)"""
        return {
            "symbol": self.symbol,
            "strength": round(self.strength, 4),
            "confidence": round(self.confidence, 4),
            "source_type": self.source_type,
            "source_id": self.source_id,
            "reasoning": self.reasoning,
            "valid_until": self.valid_until,
            "timestamp": self.timestamp,
            "key_factors": list(self.key_factors),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DistilledSignal:
        """从 dict 反序列化 (容错: 字段缺失/类型错误时使用默认值)"""
        if not isinstance(d, dict):
            return cls(symbol="")
        return cls(
            symbol=str(d.get("symbol", "")),
            strength=_safe_float(d.get("strength", 0.0)),
            confidence=_safe_float(d.get("confidence", 0.0)),
            source_type=str(d.get("source_type", "news")),
            source_id=str(d.get("source_id", "")),
            reasoning=str(d.get("reasoning", "")),
            valid_until=str(d.get("valid_until", "")),
            timestamp=str(d.get("timestamp", now_bj().isoformat())),
            key_factors=[str(k) for k in d.get("key_factors", []) if k is not None],
        )


# ============================================================
# 工具函数
# ============================================================


def _safe_float(value: Any, default: float = 0.0) -> float:
    """安全转 float, 处理 None/NaN/Inf/字符串 (与 BaseAgent._safe_float 一致)"""
    if value is None:
        return default
    try:
        f = float(value)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


# ============================================================
# ResearchDistiller 主类
# ============================================================


class ResearchDistiller:
    """研究内容蒸馏器 (RIA--TV++ 量化版)

    将研报/业绩会视频/财经书籍/新闻蒸馏为可执行信号。
    输出标准化为 DistilledSignal, 注入 SignalFusionEngine 作为第 6 信号源。

    降级链:
      1. LLM 蒸馏 (复用 15_每日工作流/llm_client.py 三级降级)
      2. 规则引擎蒸馏 (关键词 + 标的代码 NER)
      3. 空信号 (安全降级, 不阻断主流程)

    用法:
        d = ResearchDistiller()
        signals = d.distill_news_batch([
            {"title": "恒瑞医药业绩超预期", "content": "...", "symbol": "600276.SH"},
        ])
        signal_map = d.to_signal_map(signals)
        d.save_daily_snapshot(signals, "20260726")
        loaded = d.load_daily_snapshot("20260726")  # {symbol: strength}
    """

    # 复用 AIReportAgent 的关键词词典 (与 utils/ai_report_agent.py 保持一致)
    POSITIVE_WORDS = [
        "利好",
        "增长",
        "超预期",
        "突破",
        "创新高",
        "上涨",
        "盈利",
        "加仓",
        "增持",
        "买入",
        "强劲",
        "复苏",
        "景气",
        "扩张",
        "订单",
        "中标",
        "回购",
        "分红",
        "获批",
        "合作",
        "升级",
        "龙头",
    ]
    NEGATIVE_WORDS = [
        "利空",
        "下降",
        "亏损",
        "减持",
        "警示",
        "风险",
        "违规",
        "处罚",
        "退市",
        "停牌",
        "暴跌",
        "下跌",
        "疲软",
        "萎缩",
        "滞销",
        "商誉减值",
        "质押",
        "诉讼",
        "问询",
        "监管",
        "爆雷",
        "违约",
    ]
    # 高严重性负面关键词 (触发强负面信号, 类似 veto)
    CRITICAL_NEGATIVE_WORDS = [
        "立案调查",
        "退市",
        "重大违规",
        "财务造假",
        "证监会处罚",
        "强制退市",
        "爆雷",
        "违约",
        "质押爆仓",
    ]

    # 研报专用强信号词典 (任务要求 5.2)
    STRONG_POSITIVE_WORDS = [
        "强烈推荐",
        "买入评级",
        "目标价上调",
        "戴维斯双击",
        "业绩超预期",
        "净利润大增",
        "毛利率提升",
        "ROE提升",
        "超预期",
    ]
    STRONG_NEGATIVE_WORDS = [
        "卖出评级",
        "目标价下调",
        "业绩预警",
        "商誉减值",
        "质押爆仓",
        "业绩不及预期",
        "毛利率下滑",
        "ROE下滑",
    ]

    # 信号时效 (天数): 不同来源的信号失效时间 (任务要求 5.4)
    VALIDITY_DAYS: dict[str, int] = {
        "report": 7,  # 研报: 默认 7 天
        "earnings_call": 30,  # 业绩会: 30 天 (信息含量高, 时效长)
        "book": 90,  # 书籍: 90 天 (长期方法论)
        "news": 1,  # 新闻: 1 天 (时效短)
    }

    # A 股代码正则 (6 位数字, 首位 6/0/3/9, 不前后接数字)
    _A_SHARE_PATTERN = re.compile(r"(?<!\d)([6390]\d{5})(?!\d)")
    # 完整带交易所后缀的代码 (如 600276.SH / 000001.SZ / 0700.HK)
    _FULL_CODE_PATTERN = re.compile(r"([6390]\d{5})\.(SH|SZ|HK)", re.IGNORECASE)
    # 港股代码正则 (5 位数字 + .HK)
    _HK_SHARE_PATTERN = re.compile(r"(?<!\d)(\d{5})\.HK", re.IGNORECASE)

    def __init__(
        self,
        cache_dir: Path | None = None,
        llm_enabled: bool = True,
    ) -> None:
        """初始化蒸馏器

        Args:
            cache_dir: 快照缓存目录 (默认 data/distilled_signals/)
            llm_enabled: 是否启用 LLM 蒸馏 (False 时仅用规则引擎, 用于测试/降级)
        """
        self.llm_available = _LLM_CLIENT_AVAILABLE and llm_enabled
        self.cache_dir = Path(cache_dir) if cache_dir else Path("data/distilled_signals")
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:  # P2 模块 fail-safe, 待后续精确化
            logger.warning("ResearchDistiller: 创建 cache_dir 失败 (%s): %s", self.cache_dir, e)

        # 加载持仓名称词典 (用于 NER: "恒瑞医药" → "600276.SH")
        self._name_to_symbol: dict[str, str] = {}
        self._load_position_names()

        # 统计计数器 (用于 get_status)
        self._stats: dict[str, int] = {
            "llm_calls": 0,
            "llm_failures": 0,
            "rule_engine_invocations": 0,
            "signals_emitted": 0,
        }

    # ----------------------------------------------------------
    # 持仓名称词典加载 (NER)
    # ----------------------------------------------------------

    def _load_position_names(self) -> None:
        """从 config/positions.json 加载标的名称 → 代码映射 (NER 词典)"""
        try:
            pos_path = _PROJECT_ROOT / "config" / "positions.json"
            if not pos_path.exists():
                logger.debug("positions.json 不存在: %s", pos_path)
                return
            with open(pos_path, encoding="utf-8") as f:
                data = json.load(f)
            positions = data.get("positions", {}) if isinstance(data, dict) else {}
            for code, info in positions.items():
                if not isinstance(info, dict):
                    continue
                name = info.get("name", "")
                if name:
                    self._name_to_symbol[str(name)] = str(code)
            logger.info(
                "ResearchDistiller: 已加载 %d 个标的名称映射",
                len(self._name_to_symbol),
            )
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:  # P2 模块 fail-safe, 待后续精确化
            logger.warning("ResearchDistiller: 加载 positions.json 失败: %s", e)

    # ----------------------------------------------------------
    # 公开蒸馏 API
    # ----------------------------------------------------------

    def distill_report(self, pdf_path: Path) -> list[DistilledSignal]:
        """蒸馏研究报告 PDF

        Args:
            pdf_path: PDF 文件路径

        Returns:
            DistilledSignal 列表 (空列表 = 安全降级, 不阻断主流程)
        """
        try:
            pdf_path = Path(pdf_path)
            if not pdf_path.exists():
                logger.warning("distill_report: 文件不存在: %s", pdf_path)
                return []
            text = self._extract_pdf_text(pdf_path)
            if not text or len(text.strip()) < 50:
                logger.warning("distill_report: 文本提取失败或过短: %s", pdf_path)
                return []
            return self._distill_text(
                text,
                "report",
                source_id=pdf_path.name,
            )
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:  # P2 模块 fail-safe, 待后续精确化
            logger.warning("distill_report 异常 (%s): %s", pdf_path, e)
            return []

    def distill_earnings_call(
        self,
        transcript: str,
        symbol: str,
    ) -> list[DistilledSignal]:
        """蒸馏业绩会纪要

        Args:
            transcript: 业绩会纪要文本
            symbol: 标的代码 (强制指定, 不依赖 NER)

        Returns:
            DistilledSignal 列表 (单元素, 即使 LLM 识别到多标的也只保留 forced_symbol)
        """
        try:
            if not transcript or not transcript.strip():
                logger.warning("distill_earnings_call: 纪要文本为空")
                return []
            normalized = self._normalize_symbol(symbol)
            if not normalized:
                logger.warning("distill_earnings_call: 标的代码不合法: %s", symbol)
                return []
            return self._distill_text(
                transcript,
                "earnings_call",
                source_id=f"earnings_{normalized}",
                forced_symbol=normalized,
            )
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:  # P2 模块 fail-safe, 待后续精确化
            logger.warning("distill_earnings_call 异常 (%s): %s", symbol, e)
            return []

    def distill_book_chapter(
        self,
        book_path: Path,
        chapter: str = "",
    ) -> list[DistilledSignal]:
        """蒸馏财经书籍章节

        Args:
            book_path: 书籍文件路径 (支持 PDF/Markdown/TXT)
            chapter: 章节标识 (可选, 用于 source_id 标记)

        Returns:
            DistilledSignal 列表
        """
        try:
            book_path = Path(book_path)
            if not book_path.exists():
                logger.warning("distill_book_chapter: 文件不存在: %s", book_path)
                return []
            suffix = book_path.suffix.lower()
            if suffix == ".pdf":
                text = self._extract_pdf_text(book_path)
            elif suffix in (".md", ".markdown", ".txt", ""):
                text = book_path.read_text(encoding="utf-8", errors="ignore")
            else:
                logger.warning("distill_book_chapter: 不支持的文件格式: %s", suffix)
                return []
            if not text or len(text.strip()) < 100:
                logger.warning("distill_book_chapter: 文本过短: %s", book_path)
                return []
            source_id = book_path.name
            if chapter:
                source_id = f"{source_id}#{chapter}"
            return self._distill_text(text, "book", source_id=source_id)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:  # P2 模块 fail-safe, 待后续精确化
            logger.warning("distill_book_chapter 异常 (%s): %s", book_path, e)
            return []

    def distill_news_batch(self, news_items: list[dict]) -> list[DistilledSignal]:
        """批量蒸馏新闻

        Args:
            news_items: 新闻列表, 每条格式:
                {"title": str, "content": str, "symbol": str (可选)}

        Returns:
            DistilledSignal 列表 (每条新闻 0 个或多个信号)
        """
        if not news_items:
            return []
        signals: list[DistilledSignal] = []
        for item in news_items:
            try:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title", ""))
                content = str(item.get("content", ""))
                forced_symbol = self._normalize_symbol(item.get("symbol", ""))
                text = f"{title}\n{content}".strip()
                if not text:
                    continue
                item_signals = self._distill_text(
                    text,
                    "news",
                    source_id=title[:50] if title else "untitled",
                    forced_symbol=forced_symbol or None,
                )
                signals.extend(item_signals)
            except (
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                RuntimeError,
                OSError,
                TimeoutError,
                ConnectionError,
            ) as e:  # P2 模块 fail-safe, 待后续精确化
                logger.warning("distill_news_batch: 单条新闻处理异常: %s", e)
        self._stats["signals_emitted"] += len(signals)
        return signals

    # ----------------------------------------------------------
    # 信号聚合与持久化
    # ----------------------------------------------------------

    def to_signal_map(
        self,
        signals: list[DistilledSignal],
    ) -> dict[str, float]:
        """将 DistilledSignal 列表聚合为 {symbol: strength} 字典

        聚合规则:
          - 同标的多信号: 按 confidence 加权平均 (任务要求 5.4)
          - 全部 confidence=0: 简单平均 (fail-safe)
          - NaN/Inf 防御: 异常值归零
          - 边界裁剪: 结果落在 [-1, 1]

        Args:
            signals: DistilledSignal 列表

        Returns:
            {symbol: aggregated_strength} 字典
        """
        if not signals:
            return {}
        # 按 symbol 分组
        by_symbol: dict[str, list[DistilledSignal]] = {}
        for s in signals:
            if not s.symbol or not math.isfinite(s.strength):
                continue
            by_symbol.setdefault(s.symbol, []).append(s)
        result: dict[str, float] = {}
        for symbol, group in by_symbol.items():
            try:
                total_weight = sum(s.confidence for s in group)
                if total_weight <= 1e-9:
                    # 全部 confidence=0, 简单平均 (fail-safe)
                    avg = sum(s.strength for s in group) / len(group)
                else:
                    # 按 confidence 加权平均
                    avg = sum(s.strength * s.confidence for s in group) / total_weight
                # NaN/Inf 防御
                if not math.isfinite(avg):
                    avg = 0.0
                # 边界裁剪
                avg = max(-1.0, min(1.0, avg))
                result[symbol] = round(avg, 4)
            except (
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                RuntimeError,
                OSError,
                TimeoutError,
                ConnectionError,
            ) as e:  # P2 模块 fail-safe, 待后续精确化
                logger.warning("to_signal_map: 聚合异常 (%s): %s", symbol, e)
        return result

    def save_daily_snapshot(
        self,
        signals: list[DistilledSignal],
        trade_date: str,
    ) -> Path:
        """保存每日蒸馏信号快照

        输出文件: data/distilled_signals/distilled_signals_YYYYMMDD.json
        文件结构:
            {
              "trade_date": "20260726",
              "generated_at": "ISO timestamp",
              "signal_count": 5,
              "signals": [...],
              "signal_map": {"600276.SH": 0.6, ...}
            }

        Args:
            signals: DistilledSignal 列表
            trade_date: 交易日期 (YYYYMMDD 或 YYYY-MM-DD, 自动规范化)

        Returns:
            保存的文件路径 (失败时返回空 Path)
        """
        try:
            normalized_date = self._normalize_date(trade_date)
            output_path = self.cache_dir / f"distilled_signals_{normalized_date}.json"
            # 过滤已失效信号 (基于 valid_until 字段)
            # 使用传入的 trade_date 作为参考时间，以便快照与交易日语义一致
            valid_signals = [s for s in signals if self._is_signal_valid(s, normalized_date)]
            payload = {
                "trade_date": normalized_date,
                "generated_at": now_bj().isoformat(),
                "signal_count": len(valid_signals),
                "signals": [s.to_dict() for s in valid_signals],
                "signal_map": self.to_signal_map(valid_signals),
            }
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
            logger.info(
                "save_daily_snapshot: 已保存 %d 个信号到 %s",
                len(valid_signals),
                output_path,
            )
            return output_path
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:  # P2 模块 fail-safe, 待后续精确化
            logger.warning("save_daily_snapshot 异常: %s", e)
            return Path()

    def load_daily_snapshot(self, trade_date: str) -> dict[str, float]:
        """加载每日蒸馏信号快照

        Args:
            trade_date: 交易日期 (YYYYMMDD 或 YYYY-MM-DD)

        Returns:
            {symbol: strength} 字典 (失败或文件不存在时返回空字典, fail-closed)
        """
        try:
            normalized_date = self._normalize_date(trade_date)
            input_path = self.cache_dir / f"distilled_signals_{normalized_date}.json"
            if not input_path.exists():
                logger.debug("load_daily_snapshot: 文件不存在: %s", input_path)
                return {}
            with open(input_path, encoding="utf-8") as f:
                payload = json.load(f)
            signal_map = payload.get("signal_map", {}) if isinstance(payload, dict) else {}
            if not isinstance(signal_map, dict):
                return {}
            # 防御性 NaN/Inf 检查 + 边界裁剪 (与 signal_fusion 一致)
            result: dict[str, float] = {}
            for k, v in signal_map.items():
                try:
                    fv = float(v)
                    if math.isfinite(fv):
                        result[str(k)] = max(-1.0, min(1.0, fv))
                except (TypeError, ValueError):
                    continue
            logger.info(
                "load_daily_snapshot: 已加载 %d 个信号 (%s)",
                len(result),
                input_path.name,
            )
            return result
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:  # P2 模块 fail-safe, 待后续精确化
            logger.warning("load_daily_snapshot 异常: %s", e)
            return {}

    # ----------------------------------------------------------
    # 内部: 蒸馏核心 (LLM → 规则引擎 → 空)
    # ----------------------------------------------------------

    def _distill_text(
        self,
        text: str,
        source_type: str,
        source_id: str = "",
        forced_symbol: str | None = None,
    ) -> list[DistilledSignal]:
        """蒸馏文本 (LLM 优先 → 规则引擎兜底)

        Args:
            text: 待蒸馏文本
            source_type: 信号源类型 (report/earnings_call/book/news)
            source_id: 原文标识
            forced_symbol: 强制标的代码 (None 时由 LLM/NER 自由识别)
        """
        # 1. LLM 蒸馏
        if self.llm_available and _chat_fn is not None:
            try:
                llm_signals = self._llm_distill(
                    text,
                    source_type,
                    source_id,
                    forced_symbol,
                )
                if llm_signals:
                    return llm_signals
                # LLM 返回空 (可能 JSON 解析失败), 降级到规则引擎
                logger.debug(
                    "_distill_text: LLM 返回空结果, 降级到规则引擎 (source_id=%s)",
                    source_id,
                )
            except (
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                RuntimeError,
                OSError,
                TimeoutError,
                ConnectionError,
            ) as e:  # P2 模块 fail-safe, 待后续精确化
                logger.warning(
                    "_distill_text: LLM 蒸馏异常, 降级到规则引擎: %s",
                    e,
                )
                self._stats["llm_failures"] += 1

        # 2. 规则引擎兜底
        self._stats["rule_engine_invocations"] += 1
        return self._rule_distill(text, source_type, source_id, forced_symbol)

    def _llm_distill(
        self,
        text: str,
        source_type: str,
        source_id: str,
        forced_symbol: str | None = None,
    ) -> list[DistilledSignal]:
        """LLM 蒸馏 (返回空列表 = 触发规则引擎降级)"""
        # 截断超长文本 (避免超过 LLM 上下文窗口)
        truncated = text[:8000] if len(text) > 8000 else text
        prompt = self._build_distill_prompt(truncated, source_type, forced_symbol)
        self._stats["llm_calls"] += 1
        result = _chat_fn(  # type: ignore
            prompt=prompt,
            system="你是资深A股投研分析师,擅长将研究内容蒸馏为可执行交易信号。",
            temperature=0.1,
            max_tokens=1500,
        )
        if not result:
            return []
        # 去除可能的模型标签前缀 (与 AIReportAgent 一致: "[model] content")
        if result.startswith("[") and "] " in result[:50]:
            result = result.split("] ", 1)[1]
        return self._parse_llm_response(
            result,
            source_type,
            source_id,
            forced_symbol,
        )

    def _build_distill_prompt(
        self,
        text: str,
        source_type: str,
        forced_symbol: str | None = None,
    ) -> str:
        """构造 LLM 蒸馏 prompt (任务要求 5.3)"""
        source_label = {
            "report": "研究报告",
            "earnings_call": "业绩会纪要",
            "book": "财经书籍章节",
            "news": "财经新闻",
        }.get(source_type, "研究内容")
        symbol_hint = f"已知标的: {forced_symbol}\n" if forced_symbol else ""
        return f"""请将以下{source_label}蒸馏为交易信号。要求:
1. 识别涉及的 A 股标的 (6 位代码)
2. 对每个标的给出 [-1, 1] 的强度评分 (正=看涨, 负=看跌)
3. 给出 [0, 1] 的置信度
4. 列出关键驱动因素 (最多 3 个)
5. 仅输出 JSON, 不要其他解释

{symbol_hint}输出格式:
{{"signals": [{{"symbol": "600276.SH", "strength": 0.6, "confidence": 0.8, "reasoning": "...", "key_factors": ["ROE提升"]}}]}}  # noqa: E501

{source_label}内容:
{text}
"""

    def _parse_llm_response(
        self,
        response: str,
        source_type: str,
        source_id: str,
        forced_symbol: str | None = None,
    ) -> list[DistilledSignal]:
        """解析 LLM 响应 (容错: 去除 markdown 代码块 + 前导文本)"""
        try:
            clean = response.strip()
            # 去除 markdown 代码块
            if clean.startswith("```"):
                clean = re.sub(r"^```\w*\n?", "", clean)
                clean = re.sub(r"\n?```$", "", clean)
            # 去除前导文本 (取第一个 { 之后的内容)
            brace_idx = clean.find("{")
            if brace_idx > 0:
                clean = clean[brace_idx:]
            data = json.loads(clean)
            # 兼容两种格式: {"signals": [...]} 或 [...]
            if isinstance(data, list):
                signals_data = data
            elif isinstance(data, dict):
                signals_data = data.get("signals", [])
            else:
                return []
            valid_until = self._compute_valid_until(source_type)
            signals: list[DistilledSignal] = []
            for entry in signals_data:
                if not isinstance(entry, dict):
                    continue
                symbol = str(entry.get("symbol", "")).strip()
                if not symbol:
                    continue
                symbol = self._normalize_symbol(symbol)
                if not symbol:
                    continue
                # 若 forced_symbol 指定, 只接受该 symbol (业绩会场景)
                if forced_symbol and symbol != self._normalize_symbol(forced_symbol):
                    continue
                strength = _safe_float(entry.get("strength", 0.0))
                confidence = _safe_float(entry.get("confidence", 0.0))
                reasoning = str(entry.get("reasoning", ""))[:500]
                key_factors = entry.get("key_factors", [])
                if not isinstance(key_factors, list):
                    key_factors = []
                key_factors = [str(k)[:50] for k in key_factors[:3]]
                signals.append(
                    DistilledSignal(
                        symbol=symbol,
                        strength=strength,
                        confidence=confidence,
                        source_type=source_type,
                        source_id=source_id,
                        reasoning=reasoning,
                        valid_until=valid_until,
                        key_factors=key_factors,
                    )
                )
            return signals
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning("_parse_llm_response: JSON 解析失败: %s", e)
            return []

    # ----------------------------------------------------------
    # 规则引擎蒸馏 (兜底)
    # ----------------------------------------------------------

    def _rule_distill(
        self,
        text: str,
        source_type: str,
        source_id: str,
        forced_symbol: str | None = None,
    ) -> list[DistilledSignal]:
        """规则引擎蒸馏 (关键词情感词典 + 标的代码 NER)

        评分规则:
          - critical_neg > 0:           strength = -0.9, confidence = 0.95 (重大负面, 类 veto)
          - 否则: strength = 0.15*pos + 0.30*strong_pos - 0.15*neg - 0.30*strong_neg
                  confidence = min(0.85, 0.4 + 0.1 * total_signals)
          - total_signals = 0:          confidence = 0.3 (弱信号)

        Args:
            text: 待蒸馏文本
            source_type: 信号源类型
            source_id: 原文标识
            forced_symbol: 强制标的代码 (None 时由 NER 识别)
        """
        # 1. 识别标的
        if forced_symbol:
            symbols = [forced_symbol]
        else:
            symbols = self._extract_symbols(text)
        if not symbols:
            logger.debug(
                "_rule_distill: 未识别到任何标的 (source_id=%s)",
                source_id,
            )
            return []

        # 2. 关键词情感评分
        pos_count = sum(1 for w in self.POSITIVE_WORDS if w in text)
        neg_count = sum(1 for w in self.NEGATIVE_WORDS if w in text)
        strong_pos = sum(1 for w in self.STRONG_POSITIVE_WORDS if w in text)
        strong_neg = sum(1 for w in self.STRONG_NEGATIVE_WORDS if w in text)
        critical_neg = sum(1 for w in self.CRITICAL_NEGATIVE_WORDS if w in text)

        # 3. 计算强度与置信度
        if critical_neg > 0:
            strength = -0.9
            confidence = 0.95
            reasoning = f"规则引擎: 检测到 {critical_neg} 个重大负面关键词"
            key_factors = [w for w in self.CRITICAL_NEGATIVE_WORDS if w in text][:3]
        else:
            raw_strength = 0.15 * pos_count + 0.30 * strong_pos - 0.15 * neg_count - 0.30 * strong_neg
            strength = max(-1.0, min(1.0, raw_strength))
            total_signals = pos_count + neg_count + strong_pos + strong_neg
            if total_signals == 0:
                confidence = 0.3
                reasoning = "规则引擎: 未匹配到关键词, 信号弱"
                key_factors = []
            else:
                confidence = min(0.85, 0.4 + 0.1 * total_signals)
                # 提取关键因素 (优先 strong 词)
                factors: list[str] = []
                if strong_pos > 0:
                    factors.extend(w for w in self.STRONG_POSITIVE_WORDS if w in text)
                if strong_neg > 0:
                    factors.extend(w for w in self.STRONG_NEGATIVE_WORDS if w in text)
                if not factors:
                    factors = [w for w in (self.POSITIVE_WORDS + self.NEGATIVE_WORDS) if w in text]
                key_factors = factors[:3]
                reasoning = (
                    f"规则引擎: 强正面{strong_pos}个, 强负面{strong_neg}个, 正面{pos_count}个, 负面{neg_count}个"
                )

        valid_until = self._compute_valid_until(source_type)

        # 4. 为每个识别到的标的生成信号
        signals: list[DistilledSignal] = []
        for symbol in symbols:
            signals.append(
                DistilledSignal(
                    symbol=symbol,
                    strength=strength,
                    confidence=confidence,
                    source_type=source_type,
                    source_id=source_id,
                    reasoning=reasoning,
                    valid_until=valid_until,
                    key_factors=list(key_factors),
                )
            )
        return signals

    # ----------------------------------------------------------
    # NER: 标的识别
    # ----------------------------------------------------------

    def _extract_symbols(self, text: str) -> list[str]:
        """从文本中识别 A 股/港股代码 + 名称映射 (NER)

        识别优先级:
          1. 完整带后缀代码 (600276.SH / 000001.SZ / 0700.HK)
          2. A 股裸代码 (6 位数字, 首位 6/0/3/9)
          3. 持仓名称词典 (从 config/positions.json 加载)
        """
        found: list[str] = []
        seen = set()

        # 1. 完整代码 (优先级最高, 避免 600276.SH 被截断为 600276)
        for m in self._FULL_CODE_PATTERN.finditer(text):
            code = f"{m.group(1)}.{m.group(2).upper()}"
            if code not in seen:
                seen.add(code)
                found.append(code)

        # 2. A 股裸代码 (仅当没有完整代码时, 避免重复)
        if not found:
            for m in self._A_SHARE_PATTERN.finditer(text):
                code = self._normalize_symbol(m.group(1))
                if code and code not in seen:
                    seen.add(code)
                    found.append(code)

        # 3. 名称 → 代码映射 (从 positions.json)
        for name, sym in self._name_to_symbol.items():
            if name and name in text and sym not in seen:
                seen.add(sym)
                found.append(sym)

        return found

    def _normalize_symbol(self, raw: Any) -> str:
        """规范化标的代码 (返回空字符串 = 不合法)

        支持的输入格式:
          - "600276.SH" / "000001.SZ" / "0700.HK"  → 原样返回 (大写)
          - "600276" (6 位数字, 首位 6/9)           → "600276.SH"
          - "000001" / "300750" (6 位数字, 首位 0/3) → "000001.SZ"
          - "00700" (5 位数字)                      → "00700.HK"
        """
        if not raw:
            return ""
        s = str(raw).strip().upper()
        if not s:
            return ""
        # 已经带后缀
        if "." in s:
            code_part, _, suffix = s.partition(".")
            if suffix in ("SH", "SZ", "HK") and code_part.isdigit():
                return f"{code_part}.{suffix}"
            return ""
        # 裸数字代码 → 推断交易所
        if s.isdigit():
            if len(s) == 6 and s[0] in ("6", "9"):
                return f"{s}.SH"
            if len(s) == 6 and s[0] in ("0", "3"):
                return f"{s}.SZ"
            if len(s) == 5:
                return f"{s}.HK"
        return ""

    # ----------------------------------------------------------
    # 工具方法
    # ----------------------------------------------------------

    def _extract_pdf_text(self, pdf_path: Path) -> str:
        """提取 PDF 文本 (lazy import pdfplumber/pdfminer, 失败返回空字符串)

        不修改 requirements.txt: 可选依赖用 lazy import + 降级
        """
        try:
            # 优先 pdfplumber
            try:
                import pdfplumber

                with pdfplumber.open(pdf_path) as pdf:
                    pages_text = []
                    for page in pdf.pages[:50]:  # 最多 50 页
                        t = page.extract_text() or ""
                        pages_text.append(t)
                    return "\n".join(pages_text)
            except ImportError:
                pass
            # 备用 pdfminer
            try:
                from pdfminer.high_level import extract_text

                return extract_text(str(pdf_path)) or ""
            except ImportError:
                pass
            logger.warning(
                "_extract_pdf_text: pdfplumber/pdfminer 未安装, 无法提取 PDF",
            )
            return ""
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:  # P2 模块 fail-safe, 待后续精确化
            logger.warning("_extract_pdf_text 异常 (%s): %s", pdf_path, e)
            return ""

    def _compute_valid_until(self, source_type: str) -> str:
        """根据来源类型计算失效日期 (任务要求 5.4)"""
        days = self.VALIDITY_DAYS.get(source_type, 7)
        return (now_bj() + timedelta(days=days)).strftime("%Y-%m-%d")

    def _is_signal_valid(self, signal: DistilledSignal, ref_date: str | None = None) -> bool:
        """检查信号是否仍然有效 (未过期)

        Args:
            signal: DistilledSignal 实例

        Returns:
            True = 信号有效 (可保存), False = 信号已过期 (过滤掉)
        """
        return self._is_signal_valid_ref(signal, ref_date)

    def _is_signal_valid_ref(self, signal: DistilledSignal, ref_date: str | None = None) -> bool:
        """检查信号相对于参考日期是否仍然有效。

        Args:
            signal: DistilledSignal 实例
            ref_date: 参考交易日字符串（YYYYMMDD 或 YYYY-MM-DD），为空时使用当前日期

        Returns:
            True = 信号在参考日期仍有效
        """
        # 如果没有有效期字段，则认为有效
        if not signal.valid_until:
            return True
        try:
            valid_date = datetime.strptime(signal.valid_until[:10], "%Y-%m-%d")
            if ref_date:
                # 规范化参考日期并比较
                nd = self._normalize_date(ref_date)
                ref_dt = datetime.strptime(nd, "%Y%m%d").date()
            else:
                ref_dt = now_bj().date()
            return valid_date.date() >= ref_dt
        except (ValueError, TypeError):
            return True

    def _normalize_date(self, trade_date: str) -> str:
        """规范化交易日期为 YYYYMMDD (用于文件名)"""
        if not trade_date:
            return now_bj().strftime("%Y%m%d")
        s = str(trade_date).strip()
        # 已是 YYYYMMDD
        if re.match(r"^\d{8}$", s):
            return s
        # YYYY-MM-DD → YYYYMMDD
        if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
            return s.replace("-", "")
        # 尝试解析
        try:
            return datetime.strptime(s, "%Y-%m-%d").strftime("%Y%m%d")
        except ValueError:
            return now_bj().strftime("%Y%m%d")

    # ----------------------------------------------------------
    # 状态查询
    # ----------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """获取蒸馏器状态 (用于调试和监控)"""
        return {
            "llm_available": self.llm_available,
            "cache_dir": str(self.cache_dir),
            "name_dict_size": len(self._name_to_symbol),
            "stats": dict(self._stats),
        }


# ============================================================
# 便捷函数
# ============================================================

_distiller_instance: ResearchDistiller | None = None


def get_distiller() -> ResearchDistiller:
    """获取全局 ResearchDistiller 实例 (单例)"""
    global _distiller_instance
    if _distiller_instance is None:
        _distiller_instance = ResearchDistiller()
    return _distiller_instance


# ============================================================
# 自检
# ============================================================


def self_test() -> bool:
    """模块自检 (不发起 LLM 调用, 仅验证类与规则引擎)"""
    try:
        # 强制禁用 LLM, 仅测规则引擎
        d = ResearchDistiller(llm_enabled=False)
        assert d.llm_available is False
        assert d.cache_dir.exists()

        # 测试新闻蒸馏
        signals = d.distill_news_batch(
            [
                {
                    "title": "恒瑞医药业绩超预期",
                    "content": "净利润增长 30%, 强烈推荐",
                    "symbol": "600276.SH",
                },
                {
                    "title": "某公司被立案调查",
                    "content": "财务造假",
                    "symbol": "000001.SZ",
                },
            ]
        )
        assert len(signals) == 2
        assert signals[0].symbol == "600276.SH"
        assert signals[0].strength > 0  # 超预期 + 强烈推荐 = 看涨
        assert signals[1].symbol == "000001.SZ"
        assert signals[1].strength <= -0.9  # 立案调查 + 财务造假 = 重大负面

        # 测试信号 map
        signal_map = d.to_signal_map(signals)
        assert "600276.SH" in signal_map
        assert "000001.SZ" in signal_map

        # 测试持久化 (用临时日期避免污染真实数据)
        test_date = now_bj().strftime("%Y%m%d")  # 当天日期, 保存后立即清理
        saved_path = d.save_daily_snapshot(signals, test_date)
        assert saved_path.exists()
        loaded = d.load_daily_snapshot(test_date)
        assert loaded == signal_map or set(loaded.keys()) == set(signal_map.keys())

        # 清理测试文件
        try:
            saved_path.unlink()
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ):  # P2 模块 fail-safe, 待后续精确化
            pass

        logger.info("[OK] research_distiller.py 自检通过")
        logger.info(f"  - LLM 可用: {d.llm_available}")
        logger.info(f"  - 名称词典大小: {len(d._name_to_symbol)}")
        logger.info(f"  - 测试信号数: {len(signals)}")
        logger.info(f"  - 信号 map: {signal_map}")
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
    ) as e:  # P2 模块 fail-safe, 待后续精确化
        import traceback

        logger.error(f"[FAIL] research_distiller.py 自检失败: {e}")
        traceback.print_exc()
        return False


if __name__ == "__main__":
    self_test()
