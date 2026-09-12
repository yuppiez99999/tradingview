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

from utils.datetime_utils import now_bj

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
        "日期": now_bj().strftime("%Y-%m-%d"),
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


def _decision_signals(result) -> list:
    """读取交易信号, 兼容 DecisionResult.trading_signals / 历史 signals 两种命名"""
    sigs = getattr(result, "trading_signals", None)
    if sigs is None:
        sigs = getattr(result, "signals", None)
    return sigs or []


def _decision_raw(result) -> str:
    """读取 LLM 原文, 兼容 raw_analysis / llm_response 两种命名"""
    raw = getattr(result, "raw_analysis", "") or getattr(result, "llm_response", "") or ""
    return str(raw).strip()


def _classify_decision(result) -> tuple[str, bool]:
    """判定盘中决策状态.

    Returns:
        (状态文本, 是否需要按失败/空壳计入告警序列)
    空壳判定依据: LLM 无任何原文输出 且 无信号 且 无风险预警
    (DecisionResult.raw_analysis 为空 == 引擎没有拿到任何可用的模型输出)
    """
    if result is None:
        return "⛔ 决策结果为空 (result=None)", True
    summary = str(getattr(result, "market_summary", "") or "").strip()
    # 引擎 fail-open 的错误结果会把错误信息写在 market_summary (旧代码渲染时被静默丢弃)
    if summary.startswith("决策生成失败") or "无可用模型客户端" in summary:
        return f"⛔ 决策失败: {summary[:150]}", True
    if not _decision_raw(result) and not _decision_signals(result):
        alerts = getattr(result, "risk_alerts", None) or []
        if not alerts:
            return "⚠️ LLM 无输出 (空决策: 无信号/无预警/无分析原文)", True
    return "✅ 正常", False


def _format_decision_report(
    result, market_data: dict, mode: str, status: str = "✅ 正常"
) -> str:
    """把 DecisionResult 格式化为 Markdown 报告

    v1.1 (2026-09-08): 修复字段契约错位 —
    旧代码读取 result.signals / result.llm_response (DecisionResult 无此字段),
    导致交易信号与 LLM 原文永远渲染为空; 现读取 trading_signals / raw_analysis,
    并保留历史命名降级兼容, 同时把 market_summary 中的引擎错误显式呈现。
    """
    ts = now_bj().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# 盘中 LLM 决策报告\n",
        f"\n**生成时间**: {ts}",
        f"**模式**: {mode}",
        "**场景**: intraday_decision",
        f"**决策状态**: {status}\n",
        "\n## 一、市场快照\n",
    ]
    for idx, info in market_data.get("指数行情", {}).items():
        lines.append(f"- **{idx}**: 收盘={info.get('收盘', 'N/A')}, 涨跌幅={info.get('涨跌幅', 'N/A')}")

    lines.append("\n## 二、交易信号\n")
    signals = _decision_signals(result)
    if signals:
        for s in signals:
            code = getattr(s, "code", "") or getattr(s, "symbol", "")
            name = getattr(s, "name", "")
            action = getattr(s, "action", "")
            qty = getattr(s, "quantity", "")
            price = getattr(s, "price", "")
            conf = getattr(s, "confidence", "N/A")
            urg = getattr(s, "urgency", "")
            reason = getattr(s, "reason", "") or getattr(s, "reasoning", "")
            delta = getattr(s, "weight_change", "")
            lines.append(
                f"- **[{action}]** {name}({code}) 数量={qty} 价格={price} "
                f"仓位变动={delta} 信心度={conf} 紧急度={urg}"
            )
            if reason:
                lines.append(f"  理由: {reason}")
    else:
        lines.append("无交易信号")

    lines.append("\n## 三、风险预警\n")
    alerts = getattr(result, "risk_alerts", None) or []
    if alerts:
        for a in alerts:
            severity = getattr(a, "severity", None) or getattr(a, "level", "WARN")
            code = getattr(a, "code", "") or ""
            message = getattr(a, "message", "") or str(a)
            head = f"- [{severity}]"
            if code:
                head += f" {code}:"
            lines.append(f"{head} {message}")
    else:
        lines.append("无风险预警")

    lines.append("\n## 四、LLM 原文\n")
    raw = _decision_raw(result)
    lines.append(raw if raw else "(无)")

    # 引擎错误/市场总结写在 market_summary, 旧代码静默丢弃导致失败不可见, 现显式呈现
    summary = str(getattr(result, "market_summary", "") or "").strip()
    if summary and not raw.startswith(summary):
        lines.append("\n## 五、AI 市场总结\n")
        lines.append(summary)
    conf = getattr(result, "ai_confidence", 0.0) or 0.0
    try:
        conf_txt = f"{float(conf):.2f}"
    except Exception:
        conf_txt = str(conf)
    lines.append("\n---")
    lines.append("*盘中LLM决策引擎 v1.1 | GLM5DecisionEngine*")
    lines.append(f"**AI 置信度**: {conf_txt}")
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
            f"**时间**: {now_bj():%Y-%m-%d %H:%M:%S}\n"
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
    today = now_bj().strftime("%Y-%m-%d")
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
            f"**时间**: {now_bj():%Y-%m-%d %H:%M:%S}\n"
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
        ts = now_bj().strftime("%H%M%S")
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
        status, needs_alert = _classify_decision(result)
        report = _format_decision_report(result, market_data, mode, status)
        if needs_alert:
            # 空壳决策 (LLM 无输出 / 引擎静默失败) 同样计入失败序列,
            # 连续 >=3 次时产出 数据源告警.md, 供 EOD 审计识别而非"文件生成成功"假阳性
            _record_fail_and_alert(archive, f"llm_empty_or_error: {status[:120]}")
        else:
            _reset_fail_count(archive)
    except Exception as e:
        report = (
            f"# 盘中 LLM 决策报告\n\n"
            f"**时间**: {now_bj():%Y-%m-%d %H:%M:%S}\n"
            f"**状态**: 失败 ({e})\n"
            f"**数据质量**: {quality}\n"
        )
        _record_fail_and_alert(archive, f"llm_error: {e}")

    ts = now_bj().strftime("%H%M%S")
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
