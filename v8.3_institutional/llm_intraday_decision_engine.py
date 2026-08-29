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
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# NO_PROXY: 系统代理会拒绝转发国内金融API域名, 必须在导入 akshare/requests 前设置
if not os.environ.get("NO_PROXY"):
    os.environ["NO_PROXY"] = (
        "push2his.eastmoney.com,push2.eastmoney.com,eastmoney.com,sinajs.cn,sina.com.cn,finance.sina.com.cn,hq.sinajs.cn"
    )

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
ARCHIVE_DIR = PROJECT_ROOT / "每日报告归档"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# logger for this module
logger = logging.getLogger(__name__)


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
    """获取实时市场快照 (Wind MCP → AKShare → sina 降级)

    返回的 dict 包含 ``_data_quality`` 字段:
      - "real"      : 至少 2 个指数有真实行情
      - "partial"   : 仅 1 个指数有数据
      - "all_failed": 全部数据源失败
    """
    market_data: dict = {
        "日期": datetime.now().strftime("%Y-%m-%d"),
        "指数行情": {},
        "板块表现": {},
        "资金流向": {},
    }

    sources_tried: list[str] = []
    sources_ok: list[str] = []

    # --- P1: Wind MCP ---
    try:
        from tools.wind_mcp_fetcher import wind_get_quote

        sources_tried.append("Wind MCP")
        for code, name in [
            ("000001.SH", "上证指数"),
            ("399001.SZ", "深证成指"),
            ("399006.SZ", "创业板指"),
            ("000300.SH", "沪深300"),
        ]:
            q = wind_get_quote(code)
            if q and q.get("price"):
                market_data["指数行情"][name] = {
                    "收盘": q["price"],
                    "涨跌幅": f"{q.get('change', 0):+.2f}%",
                }
        if market_data["指数行情"]:
            sources_ok.append("Wind MCP")
    except Exception:
        pass

    # --- P3: AKShare 降级 ---
    if len(market_data["指数行情"]) < 2:
        try:
            import akshare as ak

            sources_tried.append("AKShare")
            for code, name in [
                ("sh000001", "上证指数"),
                ("sz399001", "深证成指"),
                ("sz399006", "创业板指"),
                ("sh000300", "沪深300"),
            ]:
                df = ak.stock_zh_index_spot_em(symbol=code)
                if df is not None and not df.empty:
                    row = df.iloc[0]
                    market_data["指数行情"].setdefault(
                        name,
                        {
                            "收盘": float(row.get("最新价", 0)),
                            "涨跌幅": f"{float(row.get('涨跌幅', 0)):+.2f}%",
                        },
                    )
            if len(market_data["指数行情"]) >= 2 and "AKShare" not in sources_ok:
                sources_ok.append("AKShare")
        except Exception:
            pass

    # --- 数据质量标记 ---
    n = len(market_data["指数行情"])
    if n >= 2:
        quality = "real"
    elif n == 1:
        quality = "partial"
    else:
        quality = "all_failed"

    market_data["_data_quality"] = quality
    market_data["_sources_tried"] = sources_tried
    market_data["_sources_ok"] = sources_ok

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
        lines.append(f"- **{idx}**: 收盘={info.get('收盘', 'N/A')}, 涨跌幅={info.get('涨跌幅', 'N/A')}")
    lines.append("\n## 二、交易信号\n")
    signals = getattr(result, "signals", []) or []
    if signals:
        for s in signals:
            lines.append(
                f"- {getattr(s, 'symbol', '')} {getattr(s, 'action', '')} "
                f"信心度={getattr(s, 'confidence', 'N/A')}: {getattr(s, 'reasoning', '')}"
            )
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


FAIL_THRESHOLD = 3


def _record_fail_and_alert(archive: Path, reason: str) -> int:
    """记录连续失败次数，超过阈值写告警文件，返回当前连续失败次数"""
    counter_file = archive / "_intraday_fail_count.txt"
    count = 0
    if counter_file.exists():
        try:
            count = int(counter_file.read_text().strip())
        except Exception:
            count = 0
    count += 1
    counter_file.write_text(str(count), encoding="utf-8")

    if count >= FAIL_THRESHOLD:
        alert_file = archive / "数据源告警.md"
        alert_file.write_text(
            f"# ⚠️ 盘中决策连续失败告警\n\n"
            f"**时间**: {datetime.now():%Y-%m-%d %H:%M:%S}\n"
            f"**连续失败次数**: {count}\n"
            f"**最近原因**: {reason}\n\n"
            f"## 紧急处理建议\n\n"
            f"1. 检查数据源连接 (Wind MCP / AKShare / 网络)\n"
            f"2. 确认 NO_PROXY 环境变量配置\n"
            f"3. 如数据源持续不可用，考虑暂停盘中决策任务\n"
            f"4. 检查 EOD 报告数据可信度 (NOSIGNAL_PARTIAL 标记)\n",
            encoding="utf-8",
        )
    return count


def _reset_fail_count(archive: Path) -> None:
    """成功时重置失败计数"""
    counter_file = archive / "_intraday_fail_count.txt"
    if counter_file.exists():
        counter_file.unlink()


def run_intraday_decision(mode: str = "live") -> bool:
    """执行一次盘中 LLM 决策"""
    today = datetime.now().strftime("%Y-%m-%d")
    archive = ARCHIVE_DIR / today
    archive.mkdir(parents=True, exist_ok=True)

    market_data = _fetch_market_snapshot()
    portfolio_data = _load_positions()
    quality = market_data.get("_data_quality", "unknown")

    if mode == "dry-run":
        return True

    # --- 数据质量门禁: 全挂时不调 LLM，直接写降级报告 ---
    if quality == "all_failed":
        sources_tried = market_data.get("_sources_tried", [])
        report = (
            f"# 盘中 LLM 决策报告\n\n"
            f"**时间**: {datetime.now():%Y-%m-%d %H:%M:%S}\n"
            f"**状态**: ⛔ 数据源全部失败，跳过 LLM 决策\n\n"
            f"## 数据源降级链\n\n"
            f"- 尝试: {', '.join(sources_tried) or '无'}\n"
            f"- 成功: 无\n\n"
            f"## 建议\n\n"
            f"1. 检查 Wind MCP 连接 (WIND_API_KEY)\n"
            f"2. 检查网络/NO_PROXY 配置\n"
            f"3. 检查 AKShare 可用性\n"
            f"4. 确认是否在交易时段\n"
        )
        ts = datetime.now().strftime("%H%M%S")
        out_file = archive / f"盘中LLM决策_{ts}.md"
        out_file.write_text(report, encoding="utf-8")
        _record_fail_and_alert(archive, "all_data_sources_failed")
        return False

    # --- 正常决策流程 ---
    try:
        from utils.glm5_decision_engine import GLM5DecisionEngine

        engine = GLM5DecisionEngine()
        result = engine.make_decisions(
            market_data=market_data,
            portfolio_data=portfolio_data,
            scene="intraday_decision",
        )
        report = _format_decision_report(result, market_data, mode)
        _reset_fail_count(archive)
    except Exception as e:
        report = (
            f"# 盘中 LLM 决策报告\n\n"
            f"**时间**: {datetime.now():%Y-%m-%d %H:%M:%S}\n"
            f"**状态**: 失败 ({e})\n"
            f"**数据质量**: {quality}\n"
        )
        _record_fail_and_alert(archive, f"llm_error: {e}")

    ts = datetime.now().strftime("%H%M%S")
    out_file = archive / f"盘中LLM决策_{ts}.md"
    out_file.write_text(report, encoding="utf-8")
    logger.info("[OK] 盘中决策已归档: %s", out_file)
    return True


def main():
    parser = argparse.ArgumentParser(description="盘中 LLM 决策引擎")
    parser.add_argument(
        "--mode",
        type=str,
        default="live",
        choices=["live", "dry-run", "shadow"],
        help="运行模式 (默认: live)",
    )
    args = parser.parse_args()
    ok = run_intraday_decision(mode=args.mode)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
