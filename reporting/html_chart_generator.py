"""
自包含 HTML 图表生成器 — 把每日报告数据可视化为可独立打开的 HTML 图表

借鉴 diagram-design / archify 项目思路, 生成自包含 HTML (内联 CSS, 引用 CDN JS),
可嵌入每日 Markdown 报告或独立打开。

提供 3 类图表:
- 流程图 (mermaid flowchart): 对冲五阶段 / 数据源降级链路 / AI 决策路由
- 架构图 (echarts graph): 模块依赖 / 信号源融合
- 甘特图 (mermaid gantt): Sprint 排期 / Wave 时间轴

设计依据: docs/1设计计划集成到系统内并能完整运行_20260817.md W.A.3
上游: docs/1 (diagram-design / archify 接入建议) + docs/ 33 个 md 报告
下游: generate_daily_report.py --with-html-charts (后续接入) + docs/ 报告嵌入
"""

from __future__ import annotations

import html
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime

try:
    from ..logging_manager import get_logger
except (ImportError, ValueError):

    def get_logger(name: str):
        return logging.getLogger(name)


logger = get_logger("html_chart_generator")


# ============================================================
# CDN 资源
# ============================================================

MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"
ECHARTS_CDN = "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"


# ============================================================
# 数据类
# ============================================================


@dataclass
class ChartStep:
    """流程图步骤"""

    id: str
    label: str
    shape: str = "rect"  # rect / round / diamond / stadium
    next: list[str] = field(default_factory=list)


@dataclass
class ChartComponent:
    """架构图组件"""

    id: str
    name: str
    category: str = "default"
    x: int = 0
    y: int = 0


@dataclass
class ChartLink:
    """架构图连接"""

    source: str
    target: str
    label: str = ""


@dataclass
class GanttTask:
    """甘特图任务"""

    name: str
    start: str  # YYYY-MM-DD
    end: str  # YYYY-MM-DD
    section: str = "default"
    status: str = "active"  # active / done / critical


# ============================================================
# HTML 图表生成器
# ============================================================


class HTMLChartGenerator:
    """自包含 HTML 图表生成器

    使用方式:
        gen = HTMLChartGenerator()
        html_str = gen.generate_hedge_phases_chart()
        with open("report.html", "w", encoding="utf-8") as f:
            f.write(html_str)
    """

    SHAPE_MAP: dict[str, str] = {
        "rect": "[",
        "round": "(",
        "diamond": "{",
        "stadium": "(",
    }
    SHAPE_CLOSE: dict[str, str] = {
        "rect": "]",
        "round": ")",
        "diamond": "}",
        "stadium": ")",
    }

    def __init__(self, title: str = "量化系统图表") -> None:
        self.title = title

    # ------------------------------------------------------------
    # 通用包装
    # ------------------------------------------------------------

    def render_standalone(
        self,
        body: str,
        title: str | None = None,
        include_mermaid: bool = False,
        include_echarts: bool = False,
    ) -> str:
        """包装为自包含 HTML 文档"""
        t = html.escape(title or self.title)
        head_extra = ""
        if include_mermaid:
            head_extra += f'\n    <script src="{MERMAID_CDN}"></script>'
        if include_echarts:
            head_extra += f'\n    <script src="{ECHARTS_CDN}"></script>'

        mermaid_init = ""
        if include_mermaid:
            mermaid_init = """
    <script>
      if (typeof mermaid !== 'undefined') {
        mermaid.initialize({startOnLoad: true, theme: 'default'});
      }
    </script>"""

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{t}</title>
    <style>
        body {{ font-family: -apple-system, "Microsoft YaHei", sans-serif; margin: 24px; color: #333; }}
        h1 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 8px; }}
        .chart-container {{ margin: 16px 0; padding: 16px; border: 1px solid #e0e0e0; border-radius: 8px; }}
        .mermaid {{ text-align: center; }}
        .meta {{ color: #888; font-size: 12px; margin-top: 16px; }}
    </style>{head_extra}
</head>
<body>
    <h1>{t}</h1>
{body}
    <p class="meta">生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>{mermaid_init}
</body>
</html>"""

    # ------------------------------------------------------------
    # 流程图 (mermaid flowchart)
    # ------------------------------------------------------------

    def generate_flowchart(
        self,
        steps: list[ChartStep],
        title: str = "流程图",
        direction: str = "TD",
    ) -> str:
        """生成 mermaid 流程图 HTML 片段"""
        lines = ["```mermaid", f"flowchart {direction}"]
        for step in steps:
            open_b = self.SHAPE_MAP.get(step.shape, "[")
            close_b = self.SHAPE_CLOSE.get(step.shape, "]")
            label = html.escape(step.label)
            lines.append(f'    {step.id}{open_b}"{label}"{close_b}')
        for step in steps:
            for nxt in step.next:
                lines.append(f"    {step.id} --> {nxt}")
        lines.append("```")
        body = (
            f'<div class="chart-container"><h2>{html.escape(title)}</h2>\n<div class="mermaid">\n'
            + "\n".join(lines[1:-1])
            + "\n</div></div>"
        )
        return body

    def generate_flowchart_standalone(
        self,
        steps: list[ChartStep],
        title: str = "流程图",
        direction: str = "TD",
    ) -> str:
        """生成自包含流程图 HTML 文档"""
        body = self.generate_flowchart(steps, title, direction)
        return self.render_standalone(body, title=title, include_mermaid=True)

    # ------------------------------------------------------------
    # 架构图 (echarts graph)
    # ------------------------------------------------------------

    def generate_architecture(
        self,
        components: list[ChartComponent],
        links: list[ChartLink],
        title: str = "架构图",
    ) -> str:
        """生成 echarts 架构图 HTML 片段

        NEW-8 修复: 用 json.dumps 序列化 JS 对象, 避免 html.escape
        不转义反斜杠导致的 JS 字符串注入.
        """
        categories = list({c.category for c in components})
        cat_data = [{"name": c} for c in categories]
        nodes_data = [
            {
                "name": c.name,
                "category": categories.index(c.category),
                "x": c.x,
                "y": c.y,
            }
            for c in components
        ]
        links_data = [
            {
                "source": link.source,
                "target": link.target,
                "label": {"show": True, "formatter": link.label},
            }
            for link in links
        ]

        cat_json = json.dumps(cat_data, ensure_ascii=False)
        nodes_json = json.dumps(nodes_data, ensure_ascii=False)
        links_json = json.dumps(links_data, ensure_ascii=False)
        title_json = json.dumps(title, ensure_ascii=False)

        script = f"""
    <div id="arch-chart" style="width: 100%; height: 500px;"></div>
    <script>
      if (typeof echarts !== 'undefined') {{
        var chart = echarts.init(document.getElementById('arch-chart'));
        chart.setOption({{
          title: {{ text: {title_json} }},
          tooltip: {{}},
          legend: [{{ data: {cat_json} }}],
          series: [{{
            type: 'graph',
            layout: 'none',
            data: {nodes_json},
            links: {links_json},
            categories: {cat_json},
            roam: true,
            label: {{ show: true }},
            edgeLabel: {{ show: true, fontSize: 10 }}
          }}]
        }});
      }}
    </script>"""
        return (
            f'<div class="chart-container"><h2>{html.escape(title)}</h2>{script}</div>'
        )

    def generate_architecture_standalone(
        self,
        components: list[ChartComponent],
        links: list[ChartLink],
        title: str = "架构图",
    ) -> str:
        """生成自包含架构图 HTML 文档"""
        body = self.generate_architecture(components, links, title)
        return self.render_standalone(body, title=title, include_echarts=True)

    # ------------------------------------------------------------
    # 甘特图 (mermaid gantt)
    # ------------------------------------------------------------

    def generate_gantt(
        self,
        tasks: list[GanttTask],
        title: str = "甘特图",
        date_format: str = "YYYY-MM-DD",
    ) -> str:
        """生成 mermaid 甘特图 HTML 片段"""
        lines = [
            "```mermaid",
            "gantt",
            f"    title {html.escape(title)}",
            f"    dateFormat {date_format}",
        ]
        sections: dict[str, list[GanttTask]] = {}
        for t in tasks:
            sections.setdefault(t.section, []).append(t)
        for section, section_tasks in sections.items():
            lines.append(f"    section {html.escape(section)}")
            for t in section_tasks:
                status_prefix = {
                    "done": "done ",
                    "active": "active ",
                    "critical": "crit ",
                }.get(t.status, "")
                lines.append(
                    f"    {status_prefix}{html.escape(t.name)} : {t.start}, {t.end}"
                )
        lines.append("```")
        body = (
            f'<div class="chart-container"><h2>{html.escape(title)}</h2>\n<div class="mermaid">\n'
            + "\n".join(lines[1:-1])
            + "\n</div></div>"
        )
        return body

    def generate_gantt_standalone(
        self,
        tasks: list[GanttTask],
        title: str = "甘特图",
        date_format: str = "YYYY-MM-DD",
    ) -> str:
        """生成自包含甘特图 HTML 文档"""
        body = self.generate_gantt(tasks, title, date_format)
        return self.render_standalone(body, title=title, include_mermaid=True)

    # ------------------------------------------------------------
    # 项目特定图表
    # ------------------------------------------------------------

    def generate_hedge_phases_chart(self) -> str:
        """对冲五阶段流程图 (项目特定)"""
        steps = [
            ChartStep("p1", "1. 风险评估", "round", ["p2"]),
            ChartStep("p2", "2. 对冲信号生成", "round", ["p3"]),
            ChartStep("p3", "3. 对冲执行", "round", ["p4"]),
            ChartStep("p4", "4. 再平衡检查", "diamond", ["p5", "p2"]),
            ChartStep("p5", "5. 收益归因", "round"),
        ]
        return self.generate_flowchart_standalone(steps, title="对冲五阶段流程")

    def generate_data_source_degradation_chart(self) -> str:
        """数据源降级链路图 (项目特定)"""
        steps = [
            ChartStep("ds1", "Wind API", "round", ["ds2"]),
            ChartStep("ds2", "akshare", "round", ["ds3"]),
            ChartStep("ds3", "sina", "round", ["ds4"]),
            ChartStep("ds4", "yfinance", "round"),
        ]
        return self.generate_flowchart_standalone(
            steps, title="数据源降级链路", direction="LR"
        )

    def generate_ai_decision_routing_chart(self) -> str:
        """AI 决策路由图 (项目特定)"""
        components = [
            ChartComponent("input", "决策请求", "input", 100, 200),
            ChartComponent("router", "LLMRouter", "core", 300, 200),
            ChartComponent("ds", "DeepSeek", "provider", 500, 100),
            ChartComponent("glm", "GLM-5", "provider", 500, 200),
            ChartComponent("sf", "SiliconFlow", "provider", 500, 300),
            ChartComponent("ds4", "DwarfStar 本地", "provider", 500, 400),
            ChartComponent("ollama", "Ollama", "fallback", 700, 250),
        ]
        links = [
            ChartLink("input", "router"),
            ChartLink("router", "ds", "P0"),
            ChartLink("router", "glm", "P1"),
            ChartLink("router", "sf", "P2"),
            ChartLink("router", "ds4", "P3"),
            ChartLink("router", "ollama", "fallback"),
        ]
        return self.generate_architecture_standalone(
            components, links, title="AI 决策路由图"
        )

    def generate_signal_fusion_chart(self) -> str:
        """信号源融合架构图 (项目特定)"""
        components = [
            ChartComponent("ml", "ML 信号", "source", 100, 100),
            ChartComponent("ai", "AI Hedge Fund", "source", 100, 200),
            ChartComponent("glm5", "GLM5 决策", "source", 100, 300),
            ChartComponent("kond", "康波周期", "source", 100, 400),
            ChartComponent("tech", "快速技术", "source", 100, 500),
            ChartComponent("sent", "舆情情感", "new", 100, 600),
            ChartComponent("fusion", "SignalFusionEngine", "core", 400, 350),
            ChartComponent("output", "融合信号", "output", 700, 350),
        ]
        links = [
            ChartLink("ml", "fusion"),
            ChartLink("ai", "fusion"),
            ChartLink("glm5", "fusion"),
            ChartLink("kond", "fusion"),
            ChartLink("tech", "fusion"),
            ChartLink("sent", "fusion", "W.A.1 新增"),
            ChartLink("fusion", "output"),
        ]
        return self.generate_architecture_standalone(
            components, links, title="信号源融合架构"
        )
