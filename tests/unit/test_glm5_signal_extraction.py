"""GLM5 决策引擎信号提取特征测试 (2026-09-08).

背景:
- ``_extract_trading_signals`` 此前零直接测试 (认知复杂度 126 热点);
- 2026-09-08 bug 修复: ``|---|`` 分隔行原被 else 分支当"表格结束",
  导致带分隔线的标准 Markdown 简表 (系统 prompt / few-shot 示例要求的
  输出格式, glm5_decision_engine.py L304/L655) 100% 解析失败并静默降级
  正则回退 (confidence 硬编码 0.7, 权重/理由全部丢失)。修复后分隔行
  跳过且表格继续。修复前行为经探针 + sys.settrace 双重复现实锤。
- 本文件锁定修复后行为与解析语义 (断言值全部来自探针实测)。
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.glm5_decision_engine import GLM5DecisionEngine, TradingSignal


def _make_engine() -> GLM5DecisionEngine:
    """绕过 __init__ (纯解析方法不依赖实例状态)."""
    return GLM5DecisionEngine.__new__(GLM5DecisionEngine)


STANDARD_TABLE = (
    "## 交易信号简表\n"
    "\n"
    "| 代码 | 名称 | 动作 | 当前权重 | 目标权重 | 理由 | 置信度 | 紧急度 |\n"
    "|---|---|---|---|---|---|---|---|\n"
    "| 600519 | 贵州茅台 | BUY | 3.0% | 5.0% | 基本面稳健估值回落 | 0.85 | HIGH |\n"
    "| 000858 | 五粮液 | REDUCE | 4.0% | 2.0% | 估值偏高 | 0.7 | MEDIUM |\n"
    "| 601318 | 中国平安 | HOLD | 5% | 5% | 持有观察 | 0.6 | LOW |\n"
)


class TestStandardMarkdownTable:
    """bug 修复回归: 带 |---| 分隔线的标准简表必须解析成功 (修复前 0 信号)."""

    def test_standard_table_extracts_signals(self):
        engine = _make_engine()
        signals = engine._extract_trading_signals(STANDARD_TABLE)
        assert len(signals) == 3  # 修复前: 0 (静默降级正则回退)

    def test_first_row_action_and_code(self):
        engine = _make_engine()
        s = engine._extract_trading_signals(STANDARD_TABLE)[0]
        assert isinstance(s, TradingSignal)
        assert s.action == "BUY"
        assert s.code == "600519"

    def test_weight_semantics_first_le1_numeric(self):
        """权重语义: 首个 <=1.0 数值 → current (本例落置信度列, 原实现固有语义)."""
        engine = _make_engine()
        s = engine._extract_trading_signals(STANDARD_TABLE)[0]
        assert s.current_weight == 0.85
        assert s.target_weight == 0.0

    def test_confidence_from_reverse_scan(self):
        engine = _make_engine()
        s2 = engine._extract_trading_signals(STANDARD_TABLE)[1]
        assert s2.action == "REDUCE"
        assert s2.confidence == 0.7

    def test_hold_row(self):
        engine = _make_engine()
        s3 = engine._extract_trading_signals(STANDARD_TABLE)[2]
        assert s3.action == "HOLD"
        assert s3.confidence == 0.6


class TestTableState:
    """表格状态机语义."""

    def test_table_ends_at_plain_text_line(self):
        text = (
            "| 代码 | 名称 | 动作 | 当前权重 | 目标权重 | 理由 | 置信度 | 紧急度 |\n"
            "|---|---|---|---|---|---|---|---|\n"
            "| 600519 | a | BUY | 1% | 2% | reason_xx | 0.8 | HIGH |\n"
            "普通文本行。\n"
            "| 300750 | b | SELL | 1% | 0% | reason_yy | 0.9 | HIGH |\n"
        )
        engine = _make_engine()
        signals = engine._extract_trading_signals(text)
        # 表格结束后不再解析后续 | 行
        assert len(signals) == 1
        assert signals[0].code == "600519"

    def test_short_row_skipped_but_table_continues(self):
        text = (
            "| 代码 | 名称 | 动作 | 当前权重 | 目标权重 | 理由 | 置信度 | 紧急度 |\n"
            "|---|---|---|---|---|---|---|---|\n"
            "| bad row |\n"
            "| 600519 | a | BUY | 1% | 2% | reason_xx | 0.8 | HIGH |\n"
        )
        engine = _make_engine()
        signals = engine._extract_trading_signals(text)
        assert len(signals) == 1
        assert signals[0].action == "BUY"

    def test_no_action_cell_defaults_hold(self):
        cells = [
            "600519",
            "贵州茅台",
            "增持",
            "3.0%",
            "5.0%",
            "理由文本较长",
            "0.8",
            "HIGH",
        ]
        engine = _make_engine()
        s = engine._parse_signal_row(cells)
        assert s.action == "HOLD"
        assert s.confidence == 0.5  # action 未命中 → 不解析权重/置信度


class TestTextFallback:
    """无表格 → 正则回退 (原有行为保持)."""

    def test_fallback_when_no_table(self):
        engine = _make_engine()
        signals = engine._extract_trading_signals("BUY 600519 贵州茅台, 建议买入")
        assert len(signals) == 1
        assert signals[0].action == "BUY"
        assert signals[0].confidence == 0.7

    def test_no_fallback_when_table_has_results(self):
        """表格解析有结果时不走正则回退."""
        engine = _make_engine()
        signals = engine._extract_trading_signals(
            STANDARD_TABLE + "\nBUY 600519 extra"
        )
        assert all(s.reason != "AI 自动判断" for s in signals)
