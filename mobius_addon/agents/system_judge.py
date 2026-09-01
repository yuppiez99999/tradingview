"""系统判断层（纯只读）：对研报建议做四维交叉校验。

不修改任何现有文件，仅只读读取：
- config/positions.json（持仓 / 权重）
- reports/ 下最新 alpha_signals_*.json（系统信号方向）
- 风控规则常量（单标的 / 行业集中度上限）
- 可选：GLM-5 独立打分（失败自动跳过）
"""

from __future__ import annotations

import json
import os
from typing import Any

import _common as _c
import llm

SINGLE_POS_LIMIT = 0.10  # 单标的权重上限 10%
INDUSTRY_LIMIT = 0.30  # 单行业集中度上限 30%


def load_positions() -> dict[str, Any] | None:
    path = os.path.join(_c.get_sys_root(), "config", "positions.json")
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:  # noqa: BLE001
        _c.get_logger().warning("读取 positions.json 失败(只读): %s", exc)
        return None


def _signal_check(code: str):  # noqa: ANN202
    """返回 (status, message)。status ∈ {'agree','conflict','skip'}。"""
    try:
        reports_dir = os.path.join(_c.get_sys_root(), "reports")
        if not os.path.isdir(reports_dir):
            return ("skip", "信号数据不可用, 跳过一致性校验")
        files = sorted(
            f
            for f in os.listdir(reports_dir)
            if f.startswith("alpha_signals_") and f.endswith(".json")
        )
        if not files:
            return ("skip", "未找到 alpha_signals 产物, 跳过信号一致性校验")
        latest = files[-1]
        with open(os.path.join(reports_dir, latest), encoding="utf-8") as handle:
            data = json.load(handle)
        sig = None
        for key, val in data.items():
            if code in key or key.startswith(code):
                sig = val
                break
        if sig is None:
            return ("skip", f"最新 {latest} 中无该标的信号, 跳过一致性校验")
        val = (
            sig
            if isinstance(sig, (int, float))
            else sig.get("signal", sig.get("zscore", 0))
        )
        if isinstance(val, (int, float)):
            if val > 0:
                return ("agree", f"系统信号为正({val:.3f}), 与研报看多方向一致")
            if val < 0:
                return ("conflict", f"系统信号为负({val:.3f}), 与研报看多方向相反")
        return ("skip", "信号值无法解析, 跳过一致性校验")
    except Exception as exc:  # noqa: BLE001
        _c.get_logger().warning("信号校验异常: %s", exc)
        return ("skip", "信号校验异常, 跳过")


def _glm5_score(report: dict[str, Any]) -> str:
    prompt = (
        "作为量化风控, 用一句话评估该研报推荐的可行性(看多逻辑强度/主要风险), 不超过60字: "
        + json.dumps(report, ensure_ascii=False)[:1500]
    )
    return llm.chat(prompt) or "GLM-5 不可用, 跳过独立打分"


def _industry_check(report: dict[str, Any], pos: dict[str, Any] | None) -> str:
    if not pos:
        return "行业集中度校验跳过(持仓数据不可用)"
    industry = (report.get("industry") or "").strip()
    if not industry:
        return "研报未标注行业, 跳过行业集中度校验"
    positions = pos.get("positions", {})
    total_w = 0.0
    for _key, val in positions.items():
        sec = (val.get("sector", "") or "") + (val.get("style", "") or "")
        if industry in sec or sec in industry:
            total_w += float(val.get("target_weight", 0) or 0)
    if total_w >= INDUSTRY_LIMIT:
        return f"当前行业权重 {total_w * 100:.1f}% >= 上限 {INDUSTRY_LIMIT * 100:.0f}%, 加仓将超行业集中度限制"
    return f"当前行业权重 {total_w * 100:.1f}% < 上限 {INDUSTRY_LIMIT * 100:.0f}%, 可行"


def judge(report: dict[str, Any]) -> dict[str, Any]:
    """对一份研报做系统判断，返回 {verdict, judge_details, action_hint}。"""
    details = {}
    verdict = "neutral"
    code = report.get("stock_code", "")
    pos = load_positions()

    # 1) 持仓校验
    if pos:
        positions = pos.get("positions", {})
        match = None
        for key, val in positions.items():
            if key.startswith(code) or code in key:
                match = val
                break
        if match:
            w = float(match.get("target_weight", 0) or 0)
            pct = w * 100
            if w >= SINGLE_POS_LIMIT * 0.8:
                verdict = "position_conflict"
                details["position_check"] = (
                    f"已持仓 目标权重 {pct:.2f}% (接近单标的上限 {SINGLE_POS_LIMIT * 100:.0f}%, 加仓空间有限)"
                )
            else:
                details["position_check"] = f"已持仓 目标权重 {pct:.2f}% (加仓空间充足)"
        else:
            details["position_check"] = "未持仓, 可作新增机会"
    else:
        details["position_check"] = "持仓数据不可用, 跳过持仓校验"

    # 2) 信号一致性
    sstatus, smsg = _signal_check(code)
    details["signal_check"] = smsg
    if sstatus == "conflict":
        verdict = "conflict"
    elif sstatus == "agree" and verdict == "neutral":
        verdict = "agree"

    # 3) 行业集中度
    details["risk_rule"] = _industry_check(report, pos)

    # 4) GLM-5 独立打分（可选）
    details["glm5_score"] = _glm5_score(report)

    return {
        "verdict": verdict,
        "judge_details": details,
        "action_hint": _action_hint(verdict, report),
    }


def _action_hint(verdict: str, report: dict[str, Any]) -> str:  # noqa: ARG001
    if verdict == "position_conflict":
        return "建议: 当前持仓已近上限, 研报加仓建议需谨慎或换仓, 不自动执行"
    if verdict == "conflict":
        return "建议: 与系统信号方向相反, 人工复核后再决策, 不自动执行"
    if verdict == "agree":
        return "建议: 与系统信号方向一致, 可纳入观察池, 必要时接入 auto_research 验证"
    return "建议: 独立研报观点, 无系统冲突, 作为研究参考"
