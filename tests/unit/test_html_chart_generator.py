"""
HTMLChartGenerator 单元测试
===========================

测试 W.A.3 新增的自包含 HTML 图表生成器:
- 流程图生成 (mermaid flowchart)
- 架构图生成 (echarts graph)
- 甘特图生成 (mermaid gantt)
- 自包含 HTML 包装 (render_standalone)
- 项目特定图表 (对冲五阶段 / 数据源降级 / AI 路由 / 信号融合)
- HTML 转义安全性
"""

from __future__ import annotations

import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from reporting.html_chart_generator import (  # noqa: E402
    ChartComponent,
    ChartLink,
    ChartStep,
    GanttTask,
    HTMLChartGenerator,
)

# ============================================================
# HTML well-formed 检查
# ============================================================


class _HTMLValidator(HTMLParser):
    """简单 HTML well-formed 检查器"""

    def __init__(self) -> None:
        super().__init__()
        self.errors: list[str] = []
        self.tag_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag not in ("br", "hr", "img", "meta", "link", "input"):
            self.tag_stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.tag_stack and self.tag_stack[-1] == tag:
            self.tag_stack.pop()
        elif tag not in ("br", "hr", "img", "meta", "link", "input"):
            self.errors.append(f"未匹配的结束标签: </{tag}>")


def _validate_html(html_str: str) -> bool:
    """验证 HTML 是否 well-formed"""
    validator = _HTMLValidator()
    try:
        validator.feed(html_str)
        return len(validator.errors) == 0
    except (ValueError, TypeError, AttributeError):
        return False


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def gen():
    return HTMLChartGenerator()


@pytest.fixture
def sample_steps():
    return [
        ChartStep("a", "步骤 A", "round", ["b"]),
        ChartStep("b", "步骤 B", "rect", ["c"]),
        ChartStep("c", "步骤 C", "diamond"),
    ]


@pytest.fixture
def sample_components():
    return [
        ChartComponent("a", "组件 A", "core", 100, 100),
        ChartComponent("b", "组件 B", "service", 300, 100),
    ]


@pytest.fixture
def sample_links():
    return [ChartLink("a", "b", "调用")]


@pytest.fixture
def sample_tasks():
    return [
        GanttTask(
            name="任务 1",
            start="2026-08-17",
            end="2026-08-20",
            section="Sprint A",
            status="active",
        ),
        GanttTask(
            name="任务 2",
            start="2026-08-21",
            end="2026-08-25",
            section="Sprint A",
            status="done",
        ),
    ]


# ============================================================
# 测试组 1: 流程图
# ============================================================


class TestFlowchart:
    def test_generate_flowchart_fragment(self, gen, sample_steps):
        body = gen.generate_flowchart(sample_steps, title="测试流程")
        assert "chart-container" in body
        assert "flowchart" in body
        assert "步骤 A" in body
        assert "a --> b" in body

    def test_generate_flowchart_standalone(self, gen, sample_steps):
        html_str = gen.generate_flowchart_standalone(sample_steps, title="测试流程")
        assert html_str.startswith("<!DOCTYPE html>")
        assert "mermaid" in html_str
        assert "cdn.jsdelivr.net" in html_str
        assert _validate_html(html_str)

    def test_different_shapes(self, gen):
        steps = [
            ChartStep("r", "矩形", "rect", ["d"]),
            ChartStep("d", "菱形", "diamond", ["s"]),
            ChartStep("s", "圆角", "round"),
        ]
        body = gen.generate_flowchart(steps)
        assert "[" in body and "]" in body
        assert "{" in body and "}" in body
        assert "(" in body and ")" in body

    def test_direction_lr(self, gen, sample_steps):
        body = gen.generate_flowchart(sample_steps, direction="LR")
        assert "flowchart LR" in body


# ============================================================
# 测试组 2: 架构图
# ============================================================


class TestArchitecture:
    def test_generate_architecture_fragment(self, gen, sample_components, sample_links):
        body = gen.generate_architecture(
            sample_components, sample_links, title="测试架构"
        )
        assert "echarts" in body
        assert "组件 A" in body
        assert "graph" in body

    def test_generate_architecture_standalone(
        self, gen, sample_components, sample_links
    ):
        html_str = gen.generate_architecture_standalone(
            sample_components, sample_links, title="测试架构"
        )
        assert html_str.startswith("<!DOCTYPE html>")
        assert "echarts" in html_str
        assert _validate_html(html_str)

    def test_multiple_categories(self, gen):
        comps = [
            ChartComponent("a", "A", "core", 0, 0),
            ChartComponent("b", "B", "service", 100, 0),
            ChartComponent("c", "C", "input", 200, 0),
        ]
        body = gen.generate_architecture(comps, [])
        assert "core" in body
        assert "service" in body
        assert "input" in body


# ============================================================
# 测试组 3: 甘特图
# ============================================================


class TestGantt:
    def test_generate_gantt_fragment(self, gen, sample_tasks):
        body = gen.generate_gantt(sample_tasks, title="测试甘特")
        assert "gantt" in body
        assert "section" in body
        assert "任务 1" in body
        assert "2026-08-17" in body

    def test_generate_gantt_standalone(self, gen, sample_tasks):
        html_str = gen.generate_gantt_standalone(sample_tasks, title="测试甘特")
        assert html_str.startswith("<!DOCTYPE html>")
        assert "mermaid" in html_str
        assert _validate_html(html_str)

    def test_task_status(self, gen):
        tasks = [
            GanttTask(
                name="已完成", start="2026-08-01", end="2026-08-05", status="done"
            ),
            GanttTask(
                name="进行中", start="2026-08-06", end="2026-08-10", status="active"
            ),
            GanttTask(
                name="关键", start="2026-08-11", end="2026-08-15", status="critical"
            ),
        ]
        body = gen.generate_gantt(tasks)
        assert "done " in body
        assert "active " in body
        assert "crit " in body


# ============================================================
# 测试组 4: 自包含 HTML
# ============================================================


class TestStandaloneHTML:
    def test_render_standalone_basic(self, gen):
        html_str = gen.render_standalone("<p>测试</p>", title="基本测试")
        assert "<!DOCTYPE html>" in html_str
        assert "<html" in html_str
        assert "</html>" in html_str
        assert "基本测试" in html_str
        assert _validate_html(html_str)

    def test_render_standalone_with_mermaid(self, gen):
        html_str = gen.render_standalone(
            "<div class='mermaid'>flowchart TD</div>", include_mermaid=True
        )
        assert "mermaid" in html_str
        assert "cdn.jsdelivr.net" in html_str

    def test_render_standalone_with_echarts(self, gen):
        html_str = gen.render_standalone("<div id='chart'></div>", include_echarts=True)
        assert "echarts" in html_str
        assert "cdn.jsdelivr.net" in html_str

    def test_meta_timestamp(self, gen):
        html_str = gen.render_standalone("<p>测试</p>")
        assert "生成时间" in html_str


# ============================================================
# 测试组 5: 项目特定图表
# ============================================================


class TestProjectCharts:
    def test_hedge_phases_chart(self, gen):
        html_str = gen.generate_hedge_phases_chart()
        assert "对冲五阶段" in html_str
        assert "风险评估" in html_str
        assert "收益归因" in html_str
        assert _validate_html(html_str)

    def test_data_source_degradation_chart(self, gen):
        html_str = gen.generate_data_source_degradation_chart()
        assert "数据源降级" in html_str
        assert "Wind" in html_str
        assert "akshare" in html_str
        assert _validate_html(html_str)

    def test_ai_decision_routing_chart(self, gen):
        html_str = gen.generate_ai_decision_routing_chart()
        assert "AI 决策路由" in html_str
        assert "LLMRouter" in html_str
        assert "DeepSeek" in html_str
        assert "Ollama" in html_str
        assert _validate_html(html_str)

    def test_signal_fusion_chart(self, gen):
        html_str = gen.generate_signal_fusion_chart()
        assert "信号源融合" in html_str
        assert "SignalFusionEngine" in html_str
        assert "舆情情感" in html_str
        assert "W.A.1" in html_str
        assert _validate_html(html_str)


# ============================================================
# 测试组 6: HTML 转义安全
# ============================================================


class TestHtmlEscaping:
    def test_label_with_html_chars(self, gen):
        steps = [ChartStep("a", "<script>alert(1)</script>", "rect")]
        body = gen.generate_flowchart(steps)
        assert "<script>" not in body or "&lt;script&gt;" in body

    def test_title_with_special_chars(self, gen, sample_steps):
        html_str = gen.generate_flowchart_standalone(
            sample_steps, title='测试 & <特殊> "字符"'
        )
        assert _validate_html(html_str)
