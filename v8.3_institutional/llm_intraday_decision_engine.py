# -*- coding: utf-8 -*-
"""
LLM 盘中自动决策引擎 (v8.0 顶级对冲基金视角)
============================================

目标:
  - 年化收益率 >= 8%
  - 最大回撤 <= 15%
  - 盘中实时监控 + 自动决策，减少人工干预

功能:
  1. 盘中实时读取持仓/行情/对冲状态
  2. 每 15 分钟生成一次决策建议
  3. 自动触发场景:
     - 单票跌破止损线 -> 减仓/止损
     - 组合回撤逼近 -10% -> 提升对冲/减仓
     - 对冲端亏损扩大 -> 调整期货手数
     - 波动率骤升 -> 启动 Gamma 尾部保护
  4. 决策输出到 trade_plan 的 llm_intraday_decisions
  5. 可对接执行器自动下单 (MOCK / 实盘)

用法:
  py -3.8 llm_intraday_decision_engine.py [--date 2026-07-20] [--mode mock|live]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ============================================================
# 路径 (v8.0 fix: 以项目根目录为基准)
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BASE = PROJECT_ROOT / "v8.3_institutional"
PLAN_DIR = BASE / "trade_plans"
REPORTS_DIR = BASE / "reports"
CONFIG_DIR = PROJECT_ROOT / "config"

# ============================================================
# 目标参数 (顶级对冲基金标准)
# ============================================================
TARGET_ANNUAL_RETURN = 0.08
# B1.3: 从 config/risk_params.yaml 统一读取 (fail-safe 兜底 0.15)
from utils.risk_params import get_max_drawdown_limit as _get_max_drawdown_limit  # noqa: E402

MAX_DRAWDOWN_LIMIT = _get_max_drawdown_limit()
PORTFOLIO_STOP_LOSS = -0.10
SINGLE_STOP_LOSS = -0.10
SINGLE_DAY_LOSS_PAUSE = -0.02
HEDGE_MIN_BETA = 0.30
HEDGE_TARGET_BETA = 0.30
INTRADAY_CHECK_INTERVAL_MIN = 15

# ============================================================
# 导入 LLM 客户端
# ============================================================
LLM_READY = False
LLM_DEEP_READY = False
try:
    sys.path.insert(0, str(PROJECT_ROOT / "15_每日工作流"))
    from llm_client import chat as llm_chat
    from llm_client import chat_deep as llm_chat_deep
    LLM_READY = True
    LLM_DEEP_READY = True
except Exception as _e:
    try:
        sys.path.insert(0, str(PROJECT_ROOT / "15_每日工作流"))
        from llm_client import chat as llm_chat
        LLM_READY = True
    except Exception as _e2:
        print(f"[WARN] LLM客户端导入失败，使用规则引擎: {_e2}", file=sys.stderr)


# ============================================================
# 数据加载
# ============================================================
def _load_positions() -> Dict[str, Any]:
    path = CONFIG_DIR / "positions.json"
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("positions", {})
    except Exception:
        return {}


def _load_latest_pnl_report() -> Optional[Dict[str, Any]]:
    if not REPORTS_DIR.exists():
        return None

    files = list(REPORTS_DIR.glob("daily_pnl_report_*.json"))
    if not files:
        return None

    # Filter out invalid filenames like daily_pnl_report_--date.json
    valid_files = [f for f in files if "--" not in f.stem.replace("daily_pnl_report_", "")]
    if not valid_files:
        return None

    # Support both YYYY-MM-DD and YYYYMMDD formats in filename
    def _extract_date(p: Path) -> str:
        stem = p.stem.replace("daily_pnl_report_", "")
        return stem.replace("-", "")

    files_sorted = sorted(valid_files, key=_extract_date, reverse=True)
    try:
        with open(files_sorted[0], "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _load_trade_plan(trade_date: str) -> Optional[Dict[str, Any]]:
    path = PLAN_DIR / f"trade_plan_{trade_date.replace('-', '')}.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _load_latest_etf_flow_report() -> Optional[Dict[str, Any]]:
    if not REPORTS_DIR.exists():
        return None

    files = list(REPORTS_DIR.glob("etf_flow_report_*.json"))
    if not files:
        return None

    def _extract_date(p: Path) -> str:
        stem = p.stem.replace("etf_flow_report_", "")
        return stem.replace("-", "")

    files_sorted = sorted(files, key=_extract_date, reverse=True)
    try:
        with open(files_sorted[0], "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ============================================================
# 规则引擎 (LLM 不可用时降级)
# ============================================================
def _rule_based_decisions(positions: Dict[str, Any], pnl_report: Optional[Dict[str, Any]], etf_report: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    decisions: List[Dict[str, Any]] = []

    # Build quick lookup from pnl_report details if available
    pnl_details: Dict[str, Dict[str, Any]] = {}
    if pnl_report:
        for detail in pnl_report.get("portfolio_pnl", {}).get("details", []):
            pnl_details[detail.get("code", "")] = detail

    # 1. 单票止损检查
    for code, pos in positions.items():
        shares = pos.get("shares", 0)
        if shares <= 0:
            continue

        detail = pnl_details.get(code)
        if detail:
            cost = detail.get("cost_price", 0)
            close = detail.get("close_price", 0)
        else:
            cost = pos.get("avg_cost", 0)
            close = pos.get("est_price", 0)

        if cost <= 0 or close <= 0:
            continue
        pnl_pct = (close - cost) / cost
        if pnl_pct <= SINGLE_STOP_LOSS:
            decisions.append({
                "type": "SINGLE_STOP_LOSS",
                "code": code,
                "name": pos.get("name", code),
                "pnl_pct": round(pnl_pct, 4),
                "action": "减仓或止损",
                "trigger": f"pnl_pct={pnl_pct:.2%} <= {SINGLE_STOP_LOSS:.2%}",
            })

    # 2. 组合回撤检查
    if pnl_report:
        net_pnl_pct = pnl_report.get("net_performance", {}).get("net_pnl_pct", 0)
        if abs(net_pnl_pct) > 1:
            net_pnl_pct = net_pnl_pct / 100.0
        if net_pnl_pct <= PORTFOLIO_STOP_LOSS:
            decisions.append({
                "type": "PORTFOLIO_STOP_LOSS",
                "net_pnl_pct": round(net_pnl_pct, 4),
                "action": "提升对冲/降低仓位",
                "trigger": f"net_pnl_pct={net_pnl_pct:.2%} <= {PORTFOLIO_STOP_LOSS:.2%}",
            })

    # 3. ETF 资金流向信号检查
    if etf_report:
        strong_signals = [s for s in etf_report.get("signals", []) if "强加仓" in s.get("signal_type", "")]
        medium_signals = [s for s in etf_report.get("signals", []) if "加仓" in s.get("signal_type", "") and "强" not in s.get("signal_type", "")]
        total_flow = etf_report.get("total_flow_yi", 0)

        # 国家队强加仓 -> 建议增配相关板块
        if strong_signals:
            decisions.append({
                "type": "ETF_STRONG_INFLOW",
                "signal_count": len(strong_signals),
                "total_flow_yi": total_flow,
                "action": "积极进攻/增配强势板块",
                "trigger": f"国家队强加仓信号 {len(strong_signals)} 条，累计净流入 {total_flow:.2f} 亿",
            })

        # 中等加仓信号 -> 建议逢低建仓
        if medium_signals:
            decisions.append({
                "type": "ETF_MEDIUM_INFLOW",
                "signal_count": len(medium_signals),
                "action": "适度增配/逢低建仓",
                "trigger": f"国家队加仓信号 {len(medium_signals)} 条",
            })

    return decisions


# ============================================================
# 深度思考触发判断
# ============================================================
def _needs_deep_analysis(rule_decisions: List[Dict[str, Any]],
                         pnl_report: Optional[Dict[str, Any]],
                         positions: Dict[str, Any]) -> tuple:
    """判断当前是否需要触发深度思考模式

    返回: (need_deep: bool, reason: str)
    """
    if not LLM_DEEP_READY:
        return (False, "")

    # 触发条件 1：规则引擎检测到组合止损
    for d in rule_decisions:
        if d.get("type") == "PORTFOLIO_STOP_LOSS":
            return (True, "组合回撤触及止损线，需要深度风险评估")

    # 触发条件 2：多只个股同时止损（>=2 只）
    stop_loss_count = sum(1 for d in rule_decisions if d.get("type") == "SINGLE_STOP_LOSS")
    if stop_loss_count >= 2:
        return (True, f"{stop_loss_count}只个股同时止损，需要深度分析系统性风险")

    # 触发条件 3：ETF 强加仓信号（可能涉及板块轮动决策）
    for d in rule_decisions:
        if d.get("type") == "ETF_STRONG_INFLOW":
            return (True, "ETF强加仓信号，需要深度分析板块轮动机会")

    # 触发条件 4：对冲比率偏离目标
    if pnl_report:
        hedge_data = pnl_report.get("hedge_pnl", {})
        if isinstance(hedge_data, dict):
            portfolio_beta = hedge_data.get("portfolio_beta", 0)
            if abs(portfolio_beta - HEDGE_TARGET_BETA) > 0.15:
                return (True, f"组合Beta({portfolio_beta:.2f})偏离目标({HEDGE_TARGET_BETA})，需要深度对冲决策")

    # 触发条件 5：持仓数量较多且 PnL 分散（复杂联动场景）
    if pnl_report and len(positions) >= 10:
        net_pnl_pct = pnl_report.get("net_performance", {}).get("net_pnl_pct", 0)
        if abs(net_pnl_pct) > 1:
            net_pnl_pct = net_pnl_pct / 100.0
        if abs(net_pnl_pct) > 0.03:
            return (True, f"组合净盈亏{net_pnl_pct:.2%}偏离较大，需要深度归因分析")

    return (False, "")


# ============================================================
# LLM 决策
# ============================================================
def _llm_decisions(positions: Dict[str, Any], pnl_report: Optional[Dict[str, Any]], trade_plan: Optional[Dict[str, Any]], etf_report: Optional[Dict[str, Any]], rule_decisions: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    if not LLM_READY or not pnl_report:
        return []

    # 判断是否需要深度思考
    need_deep, deep_reason = _needs_deep_analysis(rule_decisions or [], pnl_report, positions)
    if need_deep:
        print(f"[DEEP] 触发深度思考模式: {deep_reason}", file=sys.stderr)

    # 构建精简上下文
    pnl_details: Dict[str, Dict[str, Any]] = {}
    if pnl_report:
        for detail in pnl_report.get("portfolio_pnl", {}).get("details", []):
            pnl_details[detail.get("code", "")] = detail

    ctx_positions = []
    for code, pos in list(positions.items())[:20]:
        detail = pnl_details.get(code)
        if detail:
            cost = detail.get("cost_price", 0)
            close = detail.get("close_price", 0)
            pnl_pct = detail.get("pnl_pct", 0)
        else:
            cost = pos.get("avg_cost", 0)
            close = pos.get("est_price", 0)
            pnl_pct = round((close - cost) / cost, 4) if cost > 0 else 0
        ctx_positions.append({
            "code": code,
            "name": pos.get("name", code),
            "shares": pos.get("shares", 0),
            "avg_cost": cost,
            "est_price": close,
            "pnl_pct": pnl_pct,
        })

    ctx_hedge = {}
    if trade_plan:
        foh = trade_plan.get("futures_options_hedge", {})
        ctx_hedge = {
            "portfolio_beta": foh.get("portfolio_beta", 0),
            "hedge_pct": foh.get("hedge_pct", 0),
            "if_contracts": next((o.get("contracts", 0) for o in foh.get("orders", []) if o.get("code") == "IF"), 0),
            "put_protection": [o.get("code") for o in foh.get("orders", []) if o.get("direction") == "BUY_PUT"],
        }

    ctx_etf = {}
    if etf_report:
        ctx_etf = {
            "market_stance": etf_report.get("market_stance"),
            "total_flow_yi": etf_report.get("total_flow_yi", 0),
            "signal_count": etf_report.get("signal_count", 0),
            "strong_buy_signals": [
                {
                    "code": s.get("code"),
                    "name": s.get("name"),
                    "net_flow_yi": s.get("net_flow_yi"),
                    "confidence": s.get("confidence"),
                }
                for s in etf_report.get("signals", [])[:8]
                if "加仓" in s.get("signal_type", "")
            ],
        }

    if need_deep:
        # 深度思考模式：使用 deepseek-r1:14b
        # prompt 更详尽，引导模型进行多步推理
        rule_summary = json.dumps(rule_decisions or [], ensure_ascii=False)
        prompt = (
            "你是一位顶级对冲基金经理，目标年化>=8%，最大回撤<=15%。\n"
            f"当前场景触发深度分析：{deep_reason}\n\n"
            "请基于以下信息，进行严谨的多步推理分析：\n"
            "1. 风险归因：当前亏损/回撤的根因（系统性 vs 个股特质性）\n"
            "2. 对冲有效性：现有对冲是否充分？是否需要调整 Beta 或加入尾部保护？\n"
            "3. 板块联动：跨板块相关性变化、资金流信号含义\n"
            "4. 操作建议：给出具体的盘中决策\n\n"
            f"组合净值: {pnl_report.get('net_performance', {}).get('net_pnl_pct', 0)}\n"
            f"规则引擎已触发: {rule_summary}\n"
            f"对冲状态: {json.dumps(ctx_hedge, ensure_ascii=False)}\n"
            f"ETF资金面: {json.dumps(ctx_etf, ensure_ascii=False)}\n"
            f"持仓明细: {json.dumps(ctx_positions, ensure_ascii=False)}\n\n"
            "最终输出必须是 JSON 数组，字段：type, code, name, action, reason。\n"
            "type 可选: SINGLE_STOP_LOSS, PORTFOLIO_HEDGE_ADJUST, ETF_REBALANCE, "
            "TAIL_PROTECTION, SECTOR_ROTATION, RISK_OFF, OTHER。\n"
            "先推理，再输出 JSON。"
        )
        system = (
            "你是顶级对冲基金的深度决策AI。请先进行严谨的分析推理，"
            "最后给出 JSON 数组形式的决策。决策必须包含思考过程。"
        )
        raw = llm_chat_deep(prompt, system=system, temperature=0.2, max_tokens=4000)
    else:
        # 快速模式：使用 qwen2.5:7b
        prompt = (
            "你是一位顶级对冲基金经理，目标年化>=8%，最大回撤<=15%。\n"
            "根据以下持仓、对冲状态与ETF资金流向，输出盘中应执行的决策（JSON数组）：\n"
            f"组合净值: {pnl_report.get('net_performance', {}).get('net_pnl_pct', 0)}\n"
            f"对冲状态: {json.dumps(ctx_hedge, ensure_ascii=False)}\n"
            f"ETF资金面: {json.dumps(ctx_etf, ensure_ascii=False)}\n"
            f"持仓明细: {json.dumps(ctx_positions, ensure_ascii=False)}\n"
            "仅输出JSON，字段：type, code, name, action, reason。"
        )
        system = "只返回JSON数组，不要解释。"
        raw = llm_chat(prompt, system=system, temperature=0.3, max_tokens=1200)

    if not raw:
        return []

    # 深度模式可能包含思考过程，提取最后的 JSON 数组
    if need_deep:
        raw = _extract_json_from_deep(raw)

    try:
        data = json.loads(raw)
        if isinstance(data, list):
            # 给深度决策打标记
            if need_deep:
                for d in data:
                    if isinstance(d, dict):
                        d["source"] = "deep_r1"
                        d["deep_reason"] = deep_reason
            return data[:20]
    except Exception:
        pass
    return []


def _extract_json_from_deep(text: str) -> str:
    """从深度思考模型的输出中提取 JSON 数组

    deepseek-r1 的输出可能包含思考过程和最终结论，
    JSON 数组通常在最后部分。
    """
    if not text:
        return text
    # 找最后一个 [ 开头到最后 ] 结尾的部分
    last_arr_start = text.rfind("[")
    last_arr_end = text.rfind("]")
    if last_arr_start >= 0 and last_arr_end > last_arr_start:
        return text[last_arr_start:last_arr_end + 1]
    return text


# ============================================================
# 决策合并与输出
# ============================================================
def _merge_decisions(rule_decisions: List[Dict[str, Any]], llm_decisions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged = list(rule_decisions)
    seen = {(d.get("code") or "") + "|" + (d.get("type") or "") for d in merged}
    for d in llm_decisions:
        key = (d.get("code") or "") + "|" + (d.get("type") or "")
        if key not in seen:
            merged.append(d)
            seen.add(key)
    return merged


def _write_decisions_to_plan(trade_plan: Dict[str, Any], decisions: List[Dict[str, Any]], trade_date: str,
                             deep_mode: bool = False, deep_reason: str = "") -> None:
    trade_plan["llm_intraday_decisions"] = {
        "updated_at": datetime.now().isoformat(),
        "decisions": decisions,
        "count": len(decisions),
        "mode": "deep_r1+规则引擎" if deep_mode else "LLM+规则引擎",
        "deep_mode": deep_mode,
        "deep_reason": deep_reason,
    }
    path = PLAN_DIR / f"trade_plan_{trade_date.replace('-', '')}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(trade_plan, f, ensure_ascii=False, indent=2)


# ============================================================
# 主流程
# ============================================================
def run_intraday_decision(trade_date: str, mode: str = "mock") -> Dict[str, Any]:
    positions = _load_positions()
    pnl_report = _load_latest_pnl_report()
    trade_plan = _load_trade_plan(trade_date)
    etf_report = _load_latest_etf_flow_report()

    rule_decisions = _rule_based_decisions(positions, pnl_report, etf_report)
    llm_decisions = _llm_decisions(positions, pnl_report, trade_plan, etf_report,
                                   rule_decisions=rule_decisions) if mode != "rule-only" else []
    decisions = _merge_decisions(rule_decisions, llm_decisions)

    # 判断是否触发了深度思考（与 _llm_decisions 内部判断逻辑一致，用于结果展示）
    deep_mode, deep_reason = _needs_deep_analysis(rule_decisions, pnl_report, positions) if mode != "rule-only" else (False, "")

    result = {
        "trade_date": trade_date,
        "mode": mode,
        "position_count": len(positions),
        "pnl_report_date": pnl_report.get("meta", {}).get("report_date") if pnl_report else None,
        "etf_report_date": etf_report.get("report_date") if etf_report else None,
        "decisions": decisions,
        "decision_count": len(decisions),
        "deep_mode": deep_mode,
        "deep_reason": deep_reason,
        "llm_model": "deepseek-r1:14b" if deep_mode else "qwen2.5:7b",
    }

    if trade_plan is not None:
        _write_decisions_to_plan(trade_plan, decisions, trade_date,
                                  deep_mode=deep_mode, deep_reason=deep_reason)
        result["plan_updated"] = True
    else:
        result["plan_updated"] = False

    return result


def main():
    parser = argparse.ArgumentParser(description="LLM 盘中自动决策引擎")
    parser.add_argument("date", nargs="?", default=None, help="交易日期 YYYY-MM-DD (默认: 今天)")
    parser.add_argument("--mode", choices=["mock", "live", "rule-only", "eod"], default="mock", help="运行模式: mock/live/rule-only/eod")
    args = parser.parse_args()

    trade_date = args.date or datetime.now().strftime("%Y-%m-%d")
    result = run_intraday_decision(trade_date, args.mode)

    print(f"LLM 盘中决策引擎 [{trade_date}] mode={args.mode}")
    print(f"持仓数: {result['position_count']}")
    print(f"决策数: {result['decision_count']}")
    for d in result["decisions"]:
        print(f"  - [{d.get('type')}] {d.get('code')} {d.get('name', '')} | {d.get('action')} | {d.get('trigger', d.get('reason', ''))}")

    if result.get("plan_updated"):
        print(f"[OK] 决策已写入 trade_plan_{trade_date.replace('-', '')}.json")
    else:
        print("[WARN] 未找到 trade_plan，仅返回决策，未写入文件")


if __name__ == "__main__":
    main()
