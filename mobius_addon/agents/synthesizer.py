"""卡片合成：调研卡 / 决策卡生成 + 因子库对照（只读扫描 utils/alpha_factor/）。"""

import json
import os
from typing import Any

import _common as _c


def scan_factor_coverage(text: str) -> str:
    """只读扫描 utils/alpha_factor/ 的模块名与类名，判断论文方法是否已有实现。"""
    root = os.path.join(_c.get_sys_root(), "utils", "alpha_factor")
    if not os.path.isdir(root):
        return "unknown (因子库目录不可用)"
    tokens = set()
    try:
        for fn in os.listdir(root):
            if fn.endswith(".py") and not fn.startswith("_"):
                tokens.add(fn[:-3].lower())
                with open(
                    os.path.join(root, fn), encoding="utf-8", errors="replace"
                ) as fh:
                    for line in fh:
                        line = line.strip()
                        if line.startswith("class "):
                            name = line[6:].split("(")[0].split(":")[0].strip().lower()
                            if name and name != "object":
                                tokens.add(name)
    except Exception:  # noqa: BLE001
        pass
    low = (text or "").lower()
    hits = [t for t in tokens if len(t) > 3 and t in low]
    if hits:
        return "existing (命中因子库: {})".format(", ".join(sorted(set(hits))[:6]))
    return "new_opportunity (未在当前因子库发现对应实现)"


def _safe_name(s: str) -> str:
    s = (s or "untitled").strip().replace("\n", " ")
    s = "".join(c if c.isalnum() or c in "-_ " else "_" for c in s)
    return s[:60] or "untitled"


def build_paper_card(insight: dict[str, Any]) -> str:
    title = insight.get("title", "")
    lines = [
        f"# 论文调研卡: {title}",
        "",
        f"- 来源: {insight.get('source', '')} | ID: {insight.get('paper_id', '')}",
        "- 作者: {}".format(", ".join(insight.get("authors", []) or [])),
        "- 年份: {} | 引用: {}".format(
            insight.get("year", ""), insight.get("citations", 0)
        ),
        "- URL: {}".format(insight.get("url", "")),
        "",
        "## 方法类型",
        insight.get("method_type", ""),
        "",
        "## 因子/方法构造逻辑",
        insight.get("factor_logic", "") or "(LLM 不可用, 已降级为原文摘要)",
        "",
        "## 回测设定",
        insight.get("backtest_setup", "") or "-",
        "",
        "## 关键结论",
        insight.get("key_findings", "") or "-",
        "",
        "## 可复现性",
        insight.get("reproducibility", "") or "-",
        "",
        "## 与现有因子库对照",
        insight.get("coverage_status", "") or "-",
        "",
    ]
    return "\n".join(lines)


def build_report_card(report: dict[str, Any], judge: dict[str, Any]) -> str:
    code = report.get("stock_code", "")
    name = report.get("stock_name", "")
    lines = [
        f"# 研报决策卡: {code} {name}",
        "",
        "- 机构: {} | 评级: {} | 目标价: {}".format(
            report.get("institution", ""),
            report.get("rating", ""),
            report.get("target_price", ""),
        ),
        "- 行业: {}".format(report.get("industry", "")),
        "- EPS 预测: {}".format(
            json.dumps(report.get("eps_forecast", {}), ensure_ascii=False)
        ),
        "",
        "## 核心逻辑",
        report.get("core_logic", "") or "-",
        "",
        "## 风险提示",
        report.get("risk_warnings", "") or "-",
        "",
        "## 系统判断",
        "**Verdict: {}**".format(judge.get("verdict", "")),
        "",
    ]
    for key, val in judge.get("judge_details", {}).items():
        lines.append(f"- {key}: {val}")
    lines += [
        "",
        "## 行动建议 (action_hint)",
        judge.get("action_hint", "") or "仅建议, 不自动执行",
        "",
    ]
    return "\n".join(lines)
