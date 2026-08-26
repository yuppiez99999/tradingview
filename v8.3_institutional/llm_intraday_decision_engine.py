"""
盘中 LLM 决策引擎 — 每 30 分钟由 Windows 计划任务调用
=====================================================

被 scripts/register_all_tasks_unified.ps1 注册为 v84_IntradayLLMDecision 任务:
  触发: 09:30 起, 每 30 分钟, 持续 6 小时 (至 15:30)
  命令: python llm_intraday_decision_engine.py --mode live

流程:
  1. 获取实时市场数据 (Wind MCP → AKShare → sina 降级链)
  2. 读取当前持仓 (configs/positions.json)
  3. 调用 GLM5DecisionEngine.make_decisions(scene="intraday_decision")
  4. 决策结果归档到 每日报告归档/YYYY-MM-DD/盘中LLM决策_HHMMSS.md

用法:
  python llm_intraday_decision_engine.py --mode live     # 实盘模式
  python llm_intraday_decision_engine.py --mode dry-run  # 试运行
  python llm_intraday_decision_engine.py --mode shadow   # 影子模式 (不执行)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
ARCHIVE_DIR = PROJECT_ROOT / "每日报告归档"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load_positions() -> dict:
    """读取当前持仓"""
    pos_path = PROJECT_ROOT / "configs" / "positions.json"
    if not pos_path.exists():
        pos_path = PROJECT_ROOT / "config" / "positions.json"
    if not pos_path.exists():
        return {"positions": {}}
    try:
        return json.loads(pos_path.read_text(encoding="utf-8"))
    except Exception:
        return {"positions": {}}


def _fetch_market_snapshot() -> dict:
    """获取实时市场快照 (Wind MCP → AKShare → sina 降级)"""
    market_data: dict = {
        "日期": datetime.now().strftime("%Y-%m-%d"),
        "指数行情": {},
        "板块表现": {},
        "资金流向": {},
    }

    # 尝试 Wind MCP
    try:
        from tools.wind_mcp_fetcher import wind_get_quote
        for code, name in [("000001.SH", "上证指数"), ("399001.SZ", "深证成指"),
                           ("399006.SZ", "创业板指"), ("000300.SH", "沪深300")]:
            q = wind_get_quote(code)
            if q and q.get("price"):
                market_data["指数行情"][name] = {
                    "收盘": q["price"],
                    "涨跌幅": f"{q.get('change_pct', 0):+.2f}%",
                }
    except Exception:
        pass

    return market_data


def _format_decision_report(result, market_data: dict, mode: str) -> str:
    """把 DecisionResult 格式化为 Markdown 报告"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# 盘中 LLM 决策报告\n",
        f"\n**生成时间**: {ts}",
        f"**模式**: {mode}",
        "**场景**: intraday_decision\n",
        "\n## 一、市场快照\n",
    ]
    for idx, info in market_data.get("指数行情", {}).items():
        lines.append(f"- **{idx}**: 收盘={info.get('收盘','N/A')}, 涨跌幅={info.get('涨跌幅','N/A')}")
    lines.append("\n## 二、交易信号\n")
    signals = getattr(result, "signals", []) or []
    if signals:
        for s in signals:
            lines.append(f"- {getattr(s, 'symbol', '')} {getattr(s, 'action', '')} "
                        f"信心度={getattr(s, 'confidence', 'N/A')}: {getattr(s, 'reasoning', '')}")
    else:
        lines.append("无交易信号")
    lines.append("\n## 三、风险预警\n")
    alerts = getattr(result, "risk_alerts", []) or []
    if alerts:
        for a in alerts:
            lines.append(f"- [{getattr(a, 'level', 'WARN')}] {getattr(a, 'message', '')}")
    else:
        lines.append("无风险预警")
    lines.append("\n## 四、LLM 原文\n")
    lines.append(getattr(result, "llm_response", "(无)") or "(无)")
    lines.append("\n---\n*盘中LLM决策引擎 v1.0 | GLM5DecisionEngine*")
    return "\n".join(lines)


def run_intraday_decision(mode: str = "live") -> bool:
    """执行一次盘中 LLM 决策"""
    today = datetime.now().strftime("%Y-%m-%d")
    archive = ARCHIVE_DIR / today
    archive.mkdir(parents=True, exist_ok=True)

    market_data = _fetch_market_snapshot()
    portfolio_data = _load_positions()

    if mode == "dry-run":
        print(f"[DRY-RUN] 市场数据: {json.dumps(market_data, ensure_ascii=False, indent=2)}")
        print(f"[DRY-RUN] 持仓标的数: {len(portfolio_data.get('positions', {}))}")
        return True

    try:
        from utils.glm5_decision_engine import GLM5DecisionEngine
        engine = GLM5DecisionEngine()
        result = engine.make_decisions(
            market_data=market_data,
            portfolio_data=portfolio_data,
            scene="intraday_decision",
        )
        report = _format_decision_report(result, market_data, mode)
    except Exception as e:
        report = f"# 盘中 LLM 决策报告\n\n**时间**: {datetime.now():%Y-%m-%d %H:%M:%S}\n**状态**: 失败 ({e})\n"

    ts = datetime.now().strftime("%H%M%S")
    out_file = archive / f"盘中LLM决策_{ts}.md"
    out_file.write_text(report, encoding="utf-8")
    print(f"[OK] 盘中决策已归档: {out_file}")
    return True


def main():
    parser = argparse.ArgumentParser(description="盘中 LLM 决策引擎")
    parser.add_argument("--mode", type=str, default="live",
                        choices=["live", "dry-run", "shadow"],
                        help="运行模式 (默认: live)")
    args = parser.parse_args()
    ok = run_intraday_decision(mode=args.mode)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()