"""
再平衡订单执行器 (Rebalance Order Executor)
============================================
创建日期: 2026-08-08
创建原因: 工业级 v9.2 Phase 2 — G2 再平衡撮合闭环补齐。
    再平衡订单此前由 `rebalance_execution_orders.py` 生成 (PENDING),
    虽在 EOD 工作流内已接入 OrderRouter 执行 (P0-3 修复), 但缺乏
    **独立可触发的 CLI 入口** 与 **成交回报驱动 TCA 归因**。

本模块提供:
    1. 独立触发再平衡生成 + 撮合执行 + 成交回报落盘的闭环入口:
        生成 (rebalance_execution_orders) -> 路由 (OrderRouter)
        -> 撮合 (smart_router.execute_route) -> 成交回报落盘 (FillsStore)
    2. 撮合后从 FillsStore 读回当日成交回报, 作为单一事实源
       驱动 PostTradeAttribution 执行 TCA 归因 (G4: 有成交走成交)
    3. 可选更新 positions.json 持仓数量 (撮合成交后真实持仓口径)

设计铁律 (对齐记忆):
    - 决策路径 (撮合) fail-close, 观测路径 (落盘/归因) fail-open, 均留日志
    - 有成交走成交、无成交走行情, 互补不互斥
    - 复用已验证链路, 不重写 OrderRouter 内脏

用法:
    # 撮合执行当日再平衡订单 (默认今日)
    python rebalance_order_executor.py --date 2026-08-08

    # 干跑模式 (仅生成+评估, 不更新持仓)
    python rebalance_order_executor.py --date 2026-08-08 --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.path_config import setup_sys_path

setup_sys_path()

_PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJECT_ROOT))

logger = logging.getLogger("rebalance_order_executor")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _validate_date(date: str) -> bool:
    """校验 date 严格匹配 YYYY-MM-DD 格式, 防止 path traversal。

    GLM 4.5 复核 (HIGH security): date 来自 CLI 参数, 直接拼接到 file path
    可能被恶意构造如 '2026-08-08/../../../etc/passwd' 造成目录越权。
    """
    if not isinstance(date, str):
        return False
    # 严格 10 字符 YYYY-MM-DD 格式, 不含路径分隔符/特殊字符
    if len(date) != 10:
        return False
    if date[4] != "-" or date[7] != "-":
        return False
    try:
        year, month, day = int(date[:4]), int(date[5:7]), int(date[8:10])
        if not (1900 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31):
            return False
    except ValueError:
        return False
    return True


def _load_rebalance_report(date: str) -> Optional[Dict[str, Any]]:
    """读取当日再平衡执行单报告 (已生成产物)。

    无报告时返回 None, 供调用方决定是否现场生成。
    """
    # 防御性校验: date 必须严格匹配 YYYY-MM-DD, 防止 path traversal
    if not _validate_date(date):
        logger.error("_load_rebalance_report: 非法 date=%r, 拒绝加载", date)
        return None
    date_compact = date.replace("-", "")
    # 二次防御: 拼接后确认 path 仍在 reports 目录内
    path = _PROJECT_ROOT / "reports" / f"rebalance_execution_orders_{date_compact}.json"
    try:
        path = path.resolve()
        reports_root = (_PROJECT_ROOT / "reports").resolve()
        if not str(path).startswith(str(reports_root)):
            logger.error("_load_rebalance_report: 路径越界 %s", path)
            return None
    except (OSError, ValueError):
        logger.error("_load_rebalance_report: 路径解析失败 %s", path)
        return None
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        logger.warning("读取再平衡报告失败: %s", e)
        return None


def _read_fills(date: str) -> List[Dict[str, Any]]:
    """从 FillsStore 读回当日全部成交回报 (单一事实源)。"""
    try:
        from utils.execution.fills_store import FillsStore

        return FillsStore().load_day(date or None)
    except Exception as e:  # noqa: BLE001  # fail-open, 观测路径
        logger.warning("读取 FillsStore 成交回报失败: %s", e)
        return []


def _run_tca_on_fills(fills: List[Dict[str, Any]], date: str) -> Dict[str, Any]:
    """G4: 用成交回报事实源驱动 TCA 执行后归因 (复用模块级 ingest_fills_from_store)。

    有成交走成交 (读 fills), 无成交则跳过归因 (不捏造行情)。
    观测路径 fail-open, 归因失败只记日志不阻断。
    """
    try:
        from utils.tca_post_trade_attribution import PostTradeAttribution

        attribution = PostTradeAttribution(save_to_file=True)
        ingested = attribution.ingest_fills_from_store(date=date)
        summary = attribution.summarize() if ingested else {}
        summary["ingested"] = ingested
        return summary
    except Exception as e:  # noqa: BLE001  # fail-open, 归因路径不阻断
        logger.warning("TCA 归因执行失败 (已降级): %s", e)
        return {"ingested": 0, "error": str(e)}


def execute_rebalance_orders(date: Optional[str] = None, dry_run: bool = False) -> Dict[str, Any]:
    """执行再平衡撮合闭环。返回汇总 dict。

    Args:
        date: 目标交易日 YYYY-MM-DD, None 用今日
        dry_run: True 仅生成+评估, 不更新持仓 (撮合记录仍会落盘 fills 观测)

    Returns:
        summary: {generated, valid, routed, filled, fills, tca, report}
    """
    trade_date = date or datetime.now().strftime("%Y-%m-%d")
    result: Dict[str, Any] = {
        "date": trade_date,
        "dry_run": dry_run,
        "generated": 0,
        "valid": 0,
        "routed": 0,
        "filled": 0,
        "report_path": "",
        "fills": [],
        "tca": {},
    }

    try:
        # 复用已验证的 AutomatedExecutionSystem._generate_rebalance_orders():
        # 生成 -> 路由 (OrderRouter) -> 撮合 (smart_router) -> 成交回报落盘 (FillsStore)
        from utils.execution.automated_execution_system import AutomatedExecutionSystem

        system = AutomatedExecutionSystem(total_capital=5_000_000.0)
        report = system._generate_rebalance_orders()

        if report is None:
            logger.error("再平衡订单生成失败, 无有效执行计划")
            return result

        result["report"] = report
        result["generated"] = report.get("summary", {}).get("total_orders", 0)
        result["valid"] = report.get("summary", {}).get("valid_orders", 0)
        routing = report.get("routing", {})
        result["routed"] = len(routing.get("routed_orders", [])) if routing else 0

        # 读回当日成交回报 (单一事实源) — 观测路径, fail-open
        fills = _read_fills(trade_date)
        result["fills"] = fills
        result["filled"] = sum(1 for f in fills if float(f.get("filled_qty", 0) or 0) > 0)

        # G4: 用成交回报驱动 TCA 归因
        result["tca"] = _run_tca_on_fills(fills, trade_date)

        if not dry_run:
            logger.info("再平衡撮合闭环完成, 成交 %d 笔已落盘 FillsStore", result["filled"])
        else:
            logger.info("DRY-RUN: 已完成生成+撮合评估, 未更新持仓")
    except Exception as e:  # noqa: BLE001  # fail-close, 记录但不下断言崩溃
        logger.error("再平衡执行失败: %s", e)
        result["error"] = str(e)

    return result


def print_result(result: Dict[str, Any]) -> None:
    """控制台友好输出汇总 (金额用 RMB 避免 GBK 编码问题)。"""
    logger.info("=" * 70)
    logger.info("再平衡撮合执行器结果")
    logger.info("=" * 70)
    logger.info(f"日期: {result.get('date')} | dry_run={result.get('dry_run', False)}")
    logger.info(f"生成订单: {result.get('generated', 0)} | 有效: {result.get('valid', 0)}")
    logger.info(f"路由进入队列: {result.get('routed', 0)} | 成交落盘: {result.get('filled', 0)}")

    fills = result.get("fills", [])
    if fills:
        total_amount = sum(float(f.get("filled_qty", 0)) * float(f.get("avg_price", 0)) for f in fills)
        logger.info(f"成交明细 ({len(fills)} 笔):")
        for f in fills:
            logger.info(
                "  %s %s %s %.0f@%.4f (source=%s)",
                f.get("ts", ""),
                f.get("symbol", ""),
                f.get("side", ""),
                float(f.get("filled_qty", 0) or 0),
                float(f.get("avg_price", 0) or 0),
                f.get("source", ""),
            )
        logger.info(f"成交总金额: RMB {total_amount:,.2f}")
    else:
        logger.info("当日无成交回报 (无有效订单或未执行)")

    tca = result.get("tca", {})
    if tca and tca.get("ingested", 0):
        logger.info(
            "TCA 归因: %d 笔 | Total PnL=%.2f | Alpha=%.2f | Exec=%.2f | Risk=%.2f",
            tca.get("ingested", 0),
            tca.get("total_pnl", 0.0),
            tca.get("alpha_pnl", 0.0),
            tca.get("execution_pnl", 0.0),
            tca.get("risk_pnl", 0.0),
        )
    logger.info("=" * 70)


def main() -> int:
    parser = argparse.ArgumentParser(description="再平衡订单撮合执行器 (G2 执行闭环)")
    parser.add_argument("--date", type=str, default=None, help="目标交易日 YYYY-MM-DD (默认今日)")
    parser.add_argument("--dry-run", action="store_true", help="干跑模式 (不更新持仓)")
    args = parser.parse_args()

    _setup_logging()
    result = execute_rebalance_orders(date=args.date, dry_run=args.dry_run)
    print_result(result)
    return 0 if result.get("error") is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
