"""
外部报告解析器 — 消费 15_每日工作流生成的早间报告
====================================================

目标文件:
  E:\\各种PY程序\\每日报告归档\\YYYY-MM-DD\\

支持的报告:
  - 晨间行情摘要_YYYYMMDD.md
  - 实时ETF资金流向_YYYYMMDD_*.md
  - 综合日报_YYYYMMDD.md / .txt
  - 舆情综合日报_YYYYMMDD.md / .txt
  - 康波周期分析_YYYYMMDD.md
  - iFinD自动标的研判报告_YYYYMMDD.md
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


class ExternalReportLoader:
    """外部报告加载器

    负责定位并解析 15_每日工作流生成的早间报告，
    提取结构化决策信号，供 daily_workflow.phase_signal() 使用。
    """

    BASE_DIR = Path(r"E:\各种PY程序\每日报告归档")

    # 报告文件名模式（按优先级排序）
    REPORT_PATTERNS = {
        "morning_market": [
            "晨间行情摘要_{date}.md",
            "晨间行情摘要_{date}.txt",
        ],
        "etf_flow": [
            "实时ETF资金流向_{date}_*.md",
            "实时ETF资金流向_{date}_*.txt",
        ],
        "daily_report": [
            "综合日报_{date}.md",
            "综合日报_{date}.txt",
        ],
        "sentiment": [
            "舆情综合日报_{date}.md",
            "舆情综合日报_{date}.txt",
        ],
        "kondratiev": [
            "康波周期分析_{date}.md",
            "康波周期分析_{date}.txt",
        ],
        "ifind": [
            "iFinD自动标的研判报告_{date}.md",
            "iFinD自动标的研判报告_{date}.txt",
        ],
    }

    def __init__(self, base_dir: str | None = None):
        self.base_dir = Path(base_dir) if base_dir else self.BASE_DIR

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------
    def load_reports(self, trade_date: str) -> dict[str, Any]:
        """加载指定日期的所有外部报告

        Args:
            trade_date: 交易日期，格式 YYYY-MM-DD

        Returns:
            {
                "loaded": bool,
                "reports": {
                    "morning_market": {"path": ..., "content": ..., "parsed": {...}},
                    "etf_flow": {...},
                    "daily_report": {...},
                    "sentiment": {...},
                    "kondratiev": {...},
                    "ifind": {...},
                },
                "sentiment_score": float,   # 综合情绪 [-1, 1]
                "risk_events": [...],        # 风险事件
                "boost_symbols": [...],      # 建议加仓标的
                "cut_symbols": [...],        # 建议减仓标的
            }
        """
        date_compact = trade_date.replace("-", "")
        date_dir = self.base_dir / trade_date

        result: dict[str, Any] = {
            "loaded": False,
            "reports": {},
            "sentiment_score": 0.0,
            "risk_events": [],
            "boost_symbols": [],
            "cut_symbols": [],
        }

        if not date_dir.exists():
            result["reason"] = f"report directory not found: {date_dir}"
            return result

        # 逐类加载报告
        loaders = {
            "morning_market": self._load_single_report,
            "etf_flow": self._load_glob_report,
            "daily_report": self._load_single_report,
            "sentiment": self._load_single_report,
            "kondratiev": self._load_single_report,
            "ifind": self._load_single_report,
        }

        parsers = {
            "morning_market": self.parse_morning_market,
            "etf_flow": self.parse_etf_flow,
            "daily_report": self.parse_daily_report,
            "sentiment": self.parse_sentiment,
            "kondratiev": self.parse_kondratiev,
            "ifind": self.parse_ifind_report,
        }

        loaded_count = 0
        total_count = len(loaders)

        for report_type, loader in loaders.items():
            patterns = self.REPORT_PATTERNS[report_type]
            report_info = loader(date_dir, date_compact, patterns)

            if not report_info:
                continue

            content = report_info.get("content", "")
            parsed: dict[str, Any] = {}

            try:
                parser = parsers.get(report_type)
                if parser and content:
                    parsed = parser(content) or {}
            except Exception as exc:
                # 解析失败不影响整体流程
                parsed = {"error": str(exc)}

            result["reports"][report_type] = {
                "path": str(report_info.get("path", "")),
                "content": content[:2000] if content else "",  # 只保留前 2000 字符
                "parsed": parsed,
            }
            loaded_count += 1

        # 汇总情绪与信号
        if result["reports"]:
            result["loaded"] = True
            result["sentiment_score"] = self._aggregate_sentiment(result["reports"])
            result["risk_events"] = self._aggregate_risk_events(result["reports"])
            result["boost_symbols"] = self._aggregate_boost_symbols(result["reports"])
            result["cut_symbols"] = self._aggregate_cut_symbols(result["reports"])

        result["loaded_count"] = loaded_count
        result["total_count"] = total_count

        return result

    # ------------------------------------------------------------------
    # 解析器：晨间行情摘要
    # ------------------------------------------------------------------
    def parse_morning_market(self, content: str) -> dict[str, Any]:
        """解析晨间行情摘要

        提取重点:
          - 隔夜外盘涨跌（原油、铜、天然气、美元、美债）
          - 动力煤数据
          - 沪铜数据
          - 碳市场数据
        """
        if not content:
            return {}

        result: dict[str, Any] = {
            "sentiment": 0.0,
            "events": [],
            "boost_symbols": [],
            "cut_symbols": [],
        }

        # 提取隔夜外盘关键价格
        oil_price = self._extract_first(content, [
            r"WTI原油[：:]\s*\*\*([0-9.]+)美元/桶\*\*",
            r"WTI原油[：:]\s*([0-9.]+)美元/桶",
        ])
        copper_price = self._extract_first(content, [
            r"LME铜[：:]\s*\*\*([0-9.]+)美元/吨\*\*",
            r"LME铜[：:]\s*([0-9.]+)美元/吨",
        ])
        dollar_index = self._extract_first(content, [
            r"美元指数[：:]\s*\*\*([0-9.]+)\*\*",
            r"美元指数[：:]\s*([0-9.]+)",
        ])

        # 简化的情绪评分: 原油 > 70 看多，铜 > 1.3 看多，美元 > 105 看空
        sentiment = 0.0
        if oil_price:
            oil_f = float(oil_price)
            if oil_f > 75:
                sentiment += 0.2
            elif oil_f < 60:
                sentiment -= 0.2
        if copper_price:
            copper_f = float(copper_price)
            if copper_f > 1.4:
                sentiment += 0.2
            elif copper_f < 1.2:
                sentiment -= 0.2
        if dollar_index:
            dollar_f = float(dollar_index)
            if dollar_f > 105:
                sentiment -= 0.2
            elif dollar_f < 98:
                sentiment += 0.2

        result["sentiment"] = max(-1.0, min(1.0, sentiment))
        return result

    # ------------------------------------------------------------------
    # 解析器：ETF 资金流向
    # ------------------------------------------------------------------
    def parse_etf_flow(self, content: str) -> dict[str, Any]:
        """解析 ETF 资金流向

        提取重点:
          - 整体态势（净流入/净流出）
          - 强信号 ETF
          - 板块轮动建议
        """
        if not content:
            return {}

        result: dict[str, Any] = {
            "sentiment": 0.0,
            "events": [],
            "boost_symbols": [],
            "cut_symbols": [],
            "net_inflow": 0.0,
            "strong_signals": 0,
        }

        # 整体态势
        flow_match = re.search(r"\*\*整体态势\*\*[：:]\s*([^\n]+)", content)
        if flow_match:
            overall = flow_match.group(1).strip()
            if "净流入" in overall:
                result["sentiment"] = 0.5
            elif "净流出" in overall:
                result["sentiment"] = -0.5

        # 净流入金额
        inflow_match = re.search(r"\*\*今日净流入\*\*[：:]\s*([+-]?[0-9.]+)\s*亿元", content)
        if inflow_match:
            result["net_inflow"] = float(inflow_match.group(1))

        # 强信号数量
        strong_match = re.search(r"\*\*强信号数量\*\*[：:]\s*([0-9]+)", content)
        if strong_match:
            result["strong_signals"] = int(strong_match.group(1))
            if result["strong_signals"] >= 2:
                result["sentiment"] += 0.3

        # 提取加仓/减仓建议
        for line in content.splitlines():
            if "加仓" in line or "增持" in line:
                code = self._extract_etf_code(line)
                if code:
                    result["boost_symbols"].append(code)
            if "减仓" in line or "减持" in line or "规避" in line:
                code = self._extract_etf_code(line)
                if code:
                    result["cut_symbols"].append(code)

        result["sentiment"] = max(-1.0, min(1.0, result["sentiment"]))
        return result

    # ------------------------------------------------------------------
    # 解析器：综合日报
    # ------------------------------------------------------------------
    def parse_daily_report(self, content: str) -> dict[str, Any]:
        """解析综合日报

        提取重点:
          - 市场总判
          - 关键事件
          - 操作基调
        """
        if not content:
            return {}

        result: dict[str, Any] = {
            "sentiment": 0.0,
            "events": [],
            "boost_symbols": [],
            "cut_symbols": [],
        }

        # 市场总判关键词
        bullish_keywords = ["强烈看多", "看多", "积极进攻", "多头", "超配"]
        bearish_keywords = ["看空", "空头", "防御", "减仓", "规避"]

        bullish_hits = sum(content.count(kw) for kw in bullish_keywords)
        bearish_hits = sum(content.count(kw) for kw in bearish_keywords)

        if bullish_hits > bearish_hits:
            result["sentiment"] = 0.3
        elif bearish_hits > bullish_hits:
            result["sentiment"] = -0.3

        return result

    # ------------------------------------------------------------------
    # 解析器：舆情综合日报
    # ------------------------------------------------------------------
    def parse_sentiment(self, content: str) -> dict[str, Any]:
        """解析舆情综合日报

        提取重点:
          - 市场情绪评分
          - 风险事件
          - 利好/利空标的
        """
        if not content:
            return {}

        result: dict[str, Any] = {
            "sentiment": 0.0,
            "events": [],
            "boost_symbols": [],
            "cut_symbols": [],
        }

        # 从 AI 总结中提取情绪
        ai_summary = re.search(r"## [^\n]*AI[^\n]*\n+([\s\S]+?)(?:\n##|\Z)", content)
        if ai_summary:
            summary_text = ai_summary.group(1)
            if "看多" in summary_text or "利好" in summary_text:
                result["sentiment"] += 0.3
            if "看空" in summary_text or "利空" in summary_text:
                result["sentiment"] -= 0.3

        # 从资讯标题提取风险事件
        for line in content.splitlines():
            line_stripped = line.strip()
            if not line_stripped or line_stripped.startswith("#"):
                continue
            if any(kw in line_stripped for kw in ["风险", "预警", "利空", "下跌", "减持"]):
                result["events"].append(line_stripped[:200])
                result["sentiment"] -= 0.1
            if any(kw in line_stripped for kw in ["利好", "上涨", "增持", "加仓"]):
                result["events"].append(line_stripped[:200])
                result["sentiment"] += 0.1

        result["sentiment"] = max(-1.0, min(1.0, result["sentiment"]))
        return result

    # ------------------------------------------------------------------
    # 解析器：康波周期分析
    # ------------------------------------------------------------------
    def parse_kondratiev(self, content: str) -> dict[str, Any]:
        """解析康波周期分析

        提取重点:
          - 当前周期阶段
          - 行业配置建议
          - 大宗商品信号
        """
        if not content:
            return {}

        result: dict[str, Any] = {
            "sentiment": 0.0,
            "events": [],
            "boost_symbols": [],
            "cut_symbols": [],
        }

        # 周期阶段
        phase_match = re.search(r"\| 当前阶段 \| \*\*([^\*]+)\*\* \|", content)
        if phase_match:
            phase = phase_match.group(1).strip()
            if "繁荣" in phase or "复苏" in phase:
                result["sentiment"] = 0.4
            elif "衰退" in phase or "萧条" in phase:
                result["sentiment"] = -0.4

        # 推荐风格
        style_match = re.search(r"\| 推荐风格 \| ([^\n]+) \|", content)
        if style_match:
            styles = style_match.group(1)
            if "成长" in styles:
                result["sentiment"] += 0.2
            if "防御" in styles:
                result["sentiment"] -= 0.2

        result["sentiment"] = max(-1.0, min(1.0, result["sentiment"]))
        return result

    # ------------------------------------------------------------------
    # 解析器：iFinD 自动标的研判报告
    # ------------------------------------------------------------------
    def parse_ifind_report(self, content: str) -> dict[str, Any]:
        """解析 iFinD 自动标的研判报告

        提取重点:
          - 个股情绪方向
          - 新闻事件
          - 置信度
        """
        if not content:
            return {}

        result: dict[str, Any] = {
            "sentiment": 0.0,
            "events": [],
            "boost_symbols": [],
            "cut_symbols": [],
            "insights": [],
        }

        # 尝试提取标的研判表格
        for line in content.splitlines():
            line_stripped = line.strip()
            if not line_stripped or line_stripped.startswith("#"):
                continue

            # 简单规则：包含“看多/看空/增持/减持”等关键词
            if any(kw in line_stripped for kw in ["看多", "增持", "买入", "利好"]):
                result["sentiment"] += 0.1
                code = self._extract_stock_code(line_stripped)
                if code:
                    result["boost_symbols"].append(code)
            if any(kw in line_stripped for kw in ["看空", "减持", "卖出", "利空"]):
                result["sentiment"] -= 0.1
                code = self._extract_stock_code(line_stripped)
                if code:
                    result["cut_symbols"].append(code)

        result["sentiment"] = max(-1.0, min(1.0, result["sentiment"]))
        return result

    # ------------------------------------------------------------------
    # 内部工具方法
    # ------------------------------------------------------------------
    def _load_single_report(
        self,
        date_dir: Path,
        date_compact: str,
        patterns: list[str],
    ) -> dict[str, str] | None:
        """加载单个报告文件"""
        for pattern in patterns:
            filename = pattern.format(date=date_compact)
            path = date_dir / filename
            if path.exists():
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")
                    return {"path": str(path), "content": content}
                except Exception:
                    continue
        return None

    def _load_glob_report(
        self,
        date_dir: Path,
        date_compact: str,
        patterns: list[str],
    ) -> dict[str, str] | None:
        """加载 glob 模式报告文件（ETF 资金流向等）"""
        for pattern in patterns:
            search_pattern = pattern.format(date=date_compact)
            matches = list(date_dir.glob(search_pattern))
            if not matches:
                continue
            # 取最新生成的一个
            matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            path = matches[0]
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
                return {"path": str(path), "content": content}
            except Exception:
                continue
        return None

    def _aggregate_sentiment(self, reports: dict[str, dict[str, Any]]) -> float:
        """汇总多报告的情绪评分"""
        scores = []
        for info in reports.values():
            parsed = info.get("parsed", {})
            score = parsed.get("sentiment")
            if isinstance(score, (int, float)):
                scores.append(float(score))
        if not scores:
            return 0.0
        return max(-1.0, min(1.0, sum(scores) / len(scores)))

    def _aggregate_risk_events(self, reports: dict[str, dict[str, Any]]) -> list[str]:
        """汇总风险事件"""
        events: list[str] = []
        for info in reports.values():
            parsed = info.get("parsed", {})
            for event in parsed.get("events", []):
                if event and event not in events:
                    events.append(event)
        return events[:20]

    def _aggregate_boost_symbols(self, reports: dict[str, dict[str, Any]]) -> list[str]:
        """汇总建议加仓标的"""
        symbols: list[str] = []
        for info in reports.values():
            parsed = info.get("parsed", {})
            for sym in parsed.get("boost_symbols", []):
                if sym and sym not in symbols:
                    symbols.append(sym)
        return symbols[:20]

    def _aggregate_cut_symbols(self, reports: dict[str, dict[str, Any]]) -> list[str]:
        """汇总建议减仓标的"""
        symbols: list[str] = []
        for info in reports.values():
            parsed = info.get("parsed", {})
            for sym in parsed.get("cut_symbols", []):
                if sym and sym not in symbols:
                    symbols.append(sym)
        return symbols[:20]

    @staticmethod
    def _extract_first(text: str, patterns: list[str]) -> str | None:
        """按优先级尝试多个正则，返回第一个匹配值"""
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip()
        return None

    @staticmethod
    def _extract_etf_code(text: str) -> str | None:
        """从文本中提取 ETF 代码"""
        match = re.search(r"(510\d{3}|512\d{3}|515\d{3}|518\d{3}|159\d{3}|588\d{3})", text)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _extract_stock_code(text: str) -> str | None:
        """从文本中提取股票代码"""
        match = re.search(r"(60\d{4}|00\d{4}|30\d{4})", text)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def parse_report_meta(content: str) -> dict[str, Any]:
        """解析报告统一元数据头部

        支持格式:
          <!--
          REPORT_META:
            report_type: 晨间行情摘要
            trade_date: 2026-07-12
            generated_at: 2026-07-12 07:05:23
            generator: morning_market_fetcher
          -->
        """
        if not content:
            return {}
        match = re.search(r"<!--\s*REPORT_META:\s*([\s\S]*?)-->", content)
        if not match:
            return {}
        meta_text = match.group(1)
        result: dict[str, Any] = {}
        for line in meta_text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, value = line.split(":", 1)
                result[key.strip()] = value.strip()
        return result

    @staticmethod
    def _strip_report_meta(content: str) -> str:
        """从报告内容中剥离元数据头部"""
        if not content:
            return content
        return re.sub(r"<!--\s*REPORT_META:[\s\S]*?-->\n*", "", content)
