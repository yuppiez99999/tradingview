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
from typing import Any, Optional

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


def _load_rebalance_report(date: str) -> Optional[dict[str, Any]]:
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


def _read_fills(date: str) -> list[dict[str, Any]]:
    """从 FillsStore 读回当日全部成交回报 (单一事实源)。"""
    try:
        from utils.execution.fills_store import FillsStore

        return FillsStore().load_day(date or None)
    except Exception as e:  # noqa: BLE001  # fail-open, 观测路径
        logger.warning("读取 FillsStore 成交回报失败: %s", e)
        return []


# positions.json 路径 (与 hedge_order_executor 对齐)
_POSITIONS_FILE = _PROJECT_ROOT / "config" / "positions.json"


def _load_positions() -> dict[str, Any]:
    """安全加载 positions.json (fail-open)。"""
    try:
        if _POSITIONS_FILE.exists():
            with open(_POSITIONS_FILE, encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:  # noqa: BLE001
        logger.warning("读取 positions.json 失败: %s", e)
    return {}


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """原子写入 JSON (先写临时文件再替换), 遵循不可变性."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def apply_fills_to_positions(fills: list[dict[str, Any]], date: str) -> int:
    """G2 补齐: 将再平衡撮合成交回报回写 positions.json 真实持仓口径。

    设计铁律 (对齐 hedge_order_executor._update_positions_state):
        - 遵循不可变性: 深拷贝 positions_data, 不原地修改
        - 原子写回: _atomic_write_json 先写 .tmp 再 replace
        - fail-open: 任何异常只记日志, 不阻断执行链路
        - symbol 精确匹配: 带/不带后缀均尝试归一化 (6位代码)

    Args:
        fills: FillsStore 读回的当日成交回报列表
        date: 交易日 YYYY-MM-DD

    Returns:
        实际更新持仓的标的数 (0 表示无更新)
    """
    if not fills:
        logger.info("apply_fills_to_positions: 无成交回报, 跳过持仓回写")
        return 0

    positions_data = _load_positions()
    if not positions_data:
        logger.warning("apply_fills_to_positions: positions.json 为空/读取失败, 跳过")
        return 0

    new_data = json.loads(json.dumps(positions_data))  # 深拷贝, 不可变
    positions = new_data.setdefault("positions", {})

    updated = 0
    for rec in fills:
        symbol = str(rec.get("symbol", "") or "").strip()
        side = str(rec.get("side", "") or "").strip().upper()
        try:
            qty = float(rec.get("filled_qty", 0) or 0)
            price = float(rec.get("avg_price", 0) or 0)
        except (TypeError, ValueError):
            continue
        if not symbol or qty <= 0 or price <= 0:
            continue

        # symbol 归一化: 提取 6 位纯数字代码 (忽略 .SH/.SZ 后缀)
        sym_num = symbol.split(".")[0]
        # 双向匹配: 直接 key 命中, 或 positions key 的数字前缀命中
        target_key = None
        if symbol in positions:
            target_key = symbol
        elif sym_num in positions:
            target_key = sym_num
        else:
            for k in positions:
                if k.split(".")[0] == sym_num:
                    target_key = k
                    break
        if target_key is None:
            logger.debug("apply_fills_to_positions: 标的 %s 不在 positions, 跳过", symbol)
            continue
        target = positions[target_key]

        old_shares = float(target.get("shares", 0) or 0)
        if side == "BUY":
            new_shares = old_shares + qty
        elif side == "SELL":
            new_shares = old_shares - qty
        else:
            continue

        # 不可变更新: 新建内层 dict
        updated_target = dict(target)
        updated_target["shares"] = new_shares
        updated_target["amount"] = round(new_shares * price, 2)
        updated_target["last_rebalance_update"] = date
        updated_target["last_rebalance_price"] = price
        positions[target_key] = updated_target
        updated += 1
        logger.info(
            "持仓回写 %s: %s %.0f 股 @ %.4f -> shares %.0f -> %.0f",
            target_key, side, qty, price, old_shares, new_shares,
        )

    if updated:
        # 更新 meta 时间戳
        meta = new_data.setdefault("meta", {})
        meta["last_rebalance_execution"] = date
        meta["last_modified"] = datetime.now().isoformat()
        _atomic_write_json(_POSITIONS_FILE, new_data)
        logger.info("positions.json 已更新 %d 个标的持仓 (再平衡撮合)", updated)

    return updated


def _run_tca_on_fills(fills: list[dict[str, Any]], date: str) -> dict[str, Any]:
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


def execute_rebalance_orders(date: Optional[str] = None, dry_run: bool = False) -> dict[str, Any]:
    """执行再平衡撮合闭环。返回汇总 dict。

    Args:
        date: 目标交易日 YYYY-MM-DD, None 用今日
        dry_run: True 仅生成+评估, 不更新持仓 (撮合记录仍会落盘 fills 观测)

    Returns:
        summary: {generated, valid, routed, filled, fills, tca, report}
    """
    trade_date = date or datetime.now().strftime("%Y-%m-%d")
    result: dict[str, Any] = {
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
            # G2 补齐: 非 dry-run 模式, 将成交回报回写 positions.json 真实持仓
            updated = apply_fills_to_positions(fills, trade_date)
            result["positions_updated"] = updated
            logger.info("再平衡撮合闭环完成, 成交 %d 笔已落盘 FillsStore, 持仓回写 %d 个标的",
                        result["filled"], updated)
        else:
            logger.info("DRY-RUN: 已完成生成+撮合评估, 未更新持仓")
    except Exception as e:  # noqa: BLE001  # fail-close, 记录但不下断言崩溃
        logger.error("再平衡执行失败: %s", e)
        result["error"] = str(e)

    return result


def print_result(result: dict[str, Any]) -> None:
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
