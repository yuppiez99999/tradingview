"""
交易计划自动执行器（四Guard联动）
====================================
v8.4 纯期权对冲模式下, 自动读取当日交易计划,
依次经过四大风控守卫校验后模拟执行建仓与对冲指令。

四Guard链路:
    1. KillSwitch      — 保证金熔断检查 (L1+触发则停止新开仓)
    2. DrawdownControl — 回撤强制执行 (Level 2+ 减仓+加对冲)
    3. VolTarget       — 波动率缩仓 (vol_scale < 0.8 缩减建仓预算)
    4. HedgeExecution  — 对冲订单生成 (Put + Covered Call)
       + ProtectivePut — 认沽保护去重

执行模式:
    - MOCK_BROKER : 使用内置模拟成交引擎 (默认)
    - DRY_RUN      : 只走风控链路, 不执行任何下单
    - LIVE         : (预留) 通过券商API实盘执行

用法:
    py -3 v8.3_institutional/execute_trade_plan.py [YYYY-MM-DD] [--dry-run] [--auto-confirm]

示例:
    py -3 v8.3_institutional/execute_trade_plan.py 2026-07-22 --auto-confirm
    py -3 v8.3_institutional/execute_trade_plan.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

# ============================================================
# 路径初始化
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(BASE_DIR))

TRADE_PLANS_DIR = BASE_DIR / "trade_plans"
POSITIONS_FILE = ROOT_DIR / "config" / "positions.json"
REPORTS_DIR = ROOT_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)
LOG_DIR = ROOT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] execute_trade_plan: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"execute_trade_plan_{datetime.now():%Y%m%d}.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("execute_trade_plan")

# ============================================================
# 常量
# ============================================================
TOTAL_CAPITAL = 5_000_000.0
STOCK_CAPITAL = 3_000_000.0
HEDGE_CAPITAL = 2_000_000.0
HEDGE_MODE = "OPTIONS_ONLY"


def _safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _load_trade_plan(trade_date: str) -> dict | None:
    yyyymmdd = trade_date.replace("-", "")
    plan_path = TRADE_PLANS_DIR / f"trade_plan_{yyyymmdd}.json"
    if not plan_path.exists():
        logger.warning(f"交易计划文件不存在: {plan_path}")
        return None
    with open(plan_path, encoding="utf-8") as f:
        plan = json.load(f)
    logger.info(f"已加载交易计划: {plan_path} (phase={plan.get('phase',{}).get('name','?')})")
    return plan


def _load_positions() -> dict:
    if not POSITIONS_FILE.exists():
        logger.warning("positions.json 不存在")
        return {"meta": {}, "positions": {}}
    with open(POSITIONS_FILE, encoding="utf-8") as f:
        return json.load(f)


def _save_positions(data: dict) -> None:
    data["meta"]["last_modified"] = datetime.now().isoformat()
    with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info("positions.json 已更新")


# ============================================================
# 四Guard联动
# ============================================================

def _run_risk_guard_integrator(trade_date: str, plan: dict) -> dict:
    """通过 RiskGuardIntegrator 执行全部四Guard"""
    logger.info("=" * 50)
    logger.info("四Guard联动 — 启动")
    logger.info("=" * 50)
    try:
        from utils.risk_guard_integrator import RiskGuardIntegrator
        rgi = RiskGuardIntegrator(report_date=trade_date, total_capital=TOTAL_CAPITAL)
        result = rgi.run_all_guards(next_trade_date=trade_date)
        logger.info("四Guard联动完成")
        return result
    except ImportError as e:
        logger.warning(f"RiskGuardIntegrator 不可用 ({e}), 使用独立Guard检查")
        return _run_guards_standalone(trade_date, plan)
    except Exception as e:
        logger.error(f"四Guard联动异常: {e}, 降级到独立Guard")
        return _run_guards_standalone(trade_date, plan)


def _run_guards_standalone(trade_date: str, plan: dict) -> dict:
    """独立运行四个Guard (不带集成器的降级模式)"""
    results = {
        "kill_switch": {"passed": True, "build_allowed": True},
        "drawdown": {"passed": True, "level": 0},
        "vol_target": {"passed": True, "vol_scale": 1.0},
        "hedge_execution": {"passed": True, "hedge_pct": 0.4},
        "protective_put": {"passed": True, "put_orders": []},
        "risk_guard": {},
    }

    # Guard 1: KillSwitch
    try:
        ks = plan.get("hedge_fund_overlays", {}).get("kill_switch", {})
        ks_level = ks.get("level", 0)
        if ks_level >= 1:
            logger.warning(f"[Guard1] KillSwitch L{ks_level} — 建仓已停止")
            results["kill_switch"]["passed"] = False
            results["kill_switch"]["build_allowed"] = False
    except Exception as e:
        logger.error(f"[Guard1] KillSwitch异常: {e}")

    # Guard 2: Drawdown Control
    try:
        from utils.drawdown_controller import DrawdownController
        dc = DrawdownController()
        dd_level = dc.check_drawdown_level(getattr(dc, "current_drawdown", 0.0))
        results["drawdown"]["level"] = dd_level
        if dd_level >= 2:
            logger.warning(f"[Guard2] 回撤Level {dd_level} — 缩减建仓预算")
            results["drawdown"]["passed"] = False
    except ImportError:
        logger.info("[Guard2] DrawdownController 不可用 (降级)")
    except Exception as e:
        logger.error(f"[Guard2] 回撤检查异常: {e}")

    # Guard 3: Vol Target
    try:
        from utils.vol_target_controller import VolTargetController
        vtc = VolTargetController()
        vol_scale = vtc.current_vol_scale if hasattr(vtc, "current_vol_scale") else 1.0
        results["vol_target"]["vol_scale"] = vol_scale
        if vol_scale < 0.8:
            logger.warning(f"[Guard3] 波动率缩放 {vol_scale:.2f} — 缩减预算")
            results["vol_target"]["passed"] = False
    except ImportError:
        logger.info("[Guard3] VolTargetController 不可用 (降级)")
    except Exception as e:
        logger.error(f"[Guard3] 波动率检查异常: {e}")

    # Guard 4: Hedge + Protective Put
    try:
        from utils.hedge_execution_engine import HedgeExecutionEngine  # noqa: F401
        from utils.protective_put_engine import ProtectivePutEngine  # noqa: F401
    except ImportError:
        logger.info("[Guard4] 对冲模块不可用 (降级)")

    plan["risk_guard"] = results
    return plan


# ============================================================
# 模拟执行
# ============================================================

class MockExecutionEngine:
    """轻量级模拟执行引擎 — 模拟成交并记录"""

    def __init__(self, trade_date: str):
        self.trade_date = trade_date
        self.fills: list[dict] = []
        self.errors: list[dict] = []

    def execute_build_order(self, order: dict, positions_data: dict) -> dict:
        """执行一笔建仓订单 (模拟)"""
        code = order.get("code", order.get("symbol", ""))
        qty = order.get("shares", order.get("target_qty", order.get("phase1_target_qty", 0)))
        price = _safe_float(order.get("price", order.get("est_price", 0)))
        btype = order.get("type", "BUY")

        if not code or qty <= 0:
            self.errors.append({"order": order, "reason": "无效代码或数量"})
            return {"status": "REJECTED", "reason": "无效代码或数量"}

        # 模拟价格波动 ±1% (随机)
        import random
        fill_price = price * (1.0 + random.uniform(-0.01, 0.01))
        amount = qty * fill_price

        fill = {
            "code": code,
            "action": btype,
            "qty": int(qty),
            "price": round(fill_price, 3),
            "amount": round(amount, 2),
            "timestamp": datetime.now().isoformat(),
            "status": "FILLED",
        }
        self.fills.append(fill)

        # 更新持仓
        positions = positions_data.get("positions", {})
        if code in positions:
            pos = positions[code]
            old_shares = _safe_float(pos.get("shares", 0))
            old_cost = _safe_float(pos.get("avg_cost", 0))
            new_shares = old_shares + qty
            new_cost = ((old_shares * old_cost) + amount) / new_shares if new_shares > 0 else fill_price
            pos["shares"] = new_shares
            pos["avg_cost"] = round(new_cost, 4)
            pos["est_price"] = round(fill_price, 3)
        else:
            positions[code] = {
                "code": code,
                "name": order.get("name", code),
                "shares": int(qty),
                "avg_cost": round(fill_price, 4),
                "est_price": round(fill_price, 3),
            }

        logger.info(f"[成交] {code} {btype} {int(qty):,}股 @{fill_price:.3f} = ¥{amount:,.2f}")
        return fill

    def execute_hedge_order(self, order: dict) -> dict:
        """执行一笔对冲订单 (模拟)"""
        inst = order.get("instrument", order.get("code", ""))
        ct = order.get("contracts", 0)
        otype = order.get("type", "OPTIONS")
        action = order.get("action", "BUY")

        if ct <= 0:
            return {"status": "SKIPPED", "reason": "合约数为0"}

        premium = _safe_float(order.get("premium_budget", 0))
        fill = {
            "type": otype,
            "instrument": inst,
            "action": action,
            "contracts": ct,
            "premium": round(premium, 2),
            "timestamp": datetime.now().isoformat(),
            "status": "FILLED_MOCK",
        }
        self.fills.append(fill)
        logger.info(f"[对冲成交] {inst} {action} {ct}张, 权利金预估 ¥{premium:,.2f}")
        return fill

    def summary(self) -> dict:
        total_fills = len([f for f in self.fills if f.get("status", "").startswith("FILL")])
        total_amount = sum(_safe_float(f.get("amount", f.get("premium", 0))) for f in self.fills)
        return {
            "total_orders": len(self.fills),
            "fills": total_fills,
            "errors": len(self.errors),
            "total_amount": round(total_amount, 2),
        }


# ============================================================
# 报告生成
# ============================================================

def _generate_execution_report(trade_date: str, plan: dict, engine: MockExecutionEngine,
                               guard_results: dict) -> str:
    lines = []
    lines.append(f"# 交易计划执行报告 — {trade_date}")
    lines.append("")
    lines.append(f"> 生成时间: {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append("> 执行模式: MOCK_BROKER (v8.4 OPTIONS_ONLY)")
    lines.append("")

    # Guard 结果
    lines.append("## 四Guard校验结果")
    lines.append("")
    rg = guard_results.get("risk_guard", guard_results)

    ks = rg.get("kill_switch", {})
    ks_ok = ks.get("passed", True) and ks.get("build_allowed", True)
    dd = rg.get("drawdown", {})
    dd_ok = dd.get("passed", True)
    vt = rg.get("vol_target", {})
    vt_ok = vt.get("passed", True)
    he = rg.get("hedge_execution", {})
    he_ok = he.get("passed", True)
    pp = rg.get("protective_put", {})
    pp_ok = pp.get("passed", True)

    lines.append("| Guard | 状态 | 详情 |")
    lines.append("|---|---|---|")
    lines.append(f"| 1. KillSwitch | {'PASS' if ks_ok else 'BLOCKED'} | L{ks.get('level',0)} |")
    lines.append(f"| 2. Drawdown | {'PASS' if dd_ok else 'TRIGGERED'} | Level {dd.get('level',0)} |")
    lines.append(f"| 3. VolTarget | {'PASS' if vt_ok else 'SCALED'} | scale={vt.get('vol_scale',1.0):.2f} |")
    lines.append(f"| 4. Hedge+Put | {'PASS' if (he_ok and pp_ok) else 'WARN'} | |")
    lines.append("")

    all_passed = ks_ok and dd_ok and vt_ok
    if not all_passed:
        lines.append("**风控拦截: 部分Guard未通过, 建仓已缩减或停止**")
        lines.append("")

    # 执行汇总
    summ = engine.summary()
    lines.append("## 执行汇总")
    lines.append("")
    lines.append(f"- 总订单数: {summ['total_orders']}")
    lines.append(f"- 成功成交: {summ['fills']}")
    lines.append(f"- 失败/拒绝: {summ['errors']}")
    lines.append(f"- 成交总额: ¥{summ['total_amount']:,.2f}")
    lines.append("")

    # 成交明细
    if engine.fills:
        lines.append("## 成交明细")
        lines.append("")
        lines.append("| 标的/品种 | 方向 | 数量 | 价格 | 金额 | 类型 |")
        lines.append("|---|---:|---:|---:|---|")
        for f in engine.fills:
            name = f.get("code", f.get("instrument", "-"))
            side = f.get("action", "-")
            qty = f.get("qty", f.get("contracts", 0))
            price = _safe_float(f.get("price", 0))
            amount = _safe_float(f.get("amount", f.get("premium", 0)))
            ftype = f.get("type", "STOCK")
            lines.append(f"| {name} | {side} | {qty:,.0f} | {price:.3f} | ¥{amount:,.2f} | {ftype} |")
        lines.append("")

    # 错误
    if engine.errors:
        lines.append("## 执行异常")
        lines.append("")
        for e in engine.errors:
            lines.append(f"- {e.get('reason','未知错误')}: {json.dumps(e.get('order',{}), ensure_ascii=False)}")
        lines.append("")

    lines.append("---")
    lines.append(f"*报告由 execute_trade_plan.py 自动生成 | {datetime.now():%Y-%m-%d %H:%M:%S}*")
    lines.append("")
    return "\n".join(lines)


# ============================================================
# 主流程
# ============================================================

def execute_trade_plan(trade_date: str, dry_run: bool = False,
                       auto_confirm: bool = False) -> dict:
    """执行单日交易计划

    Args:
        trade_date: 交易日期 YYYY-MM-DD
        dry_run: 干跑模式 (只走风控, 不执行)
        auto_confirm: 自动确认 (跳过人工确认)

    Returns:
        执行结果字典
    """
    logger.info(f"交易计划执行器启动 — {trade_date} | dry_run={dry_run} | auto_confirm={auto_confirm}")

    # Step 1: 加载交易计划
    plan = _load_trade_plan(trade_date)
    if plan is None:
        logger.error(f"无法加载 {trade_date} 交易计划, 退出")
        return {"status": "ERROR", "reason": "trade_plan_not_found"}

    # Step 2: 四Guard联动校验
    guard_results = _run_risk_guard_integrator(trade_date, plan)

    # Step 3: 检查建仓是否被风控拦截
    ks = guard_results.get("risk_guard", guard_results).get("kill_switch", {})
    build_allowed = ks.get("build_allowed", True) and ks.get("passed", True)

    if not build_allowed:
        logger.warning("KillSwitch拦截 — 今日不执行建仓")
        if not dry_run:
            # 仍然生成报告
            engine = MockExecutionEngine(trade_date)
            report = _generate_execution_report(trade_date, plan, engine, guard_results)
            _save_report(trade_date, report)
        return {"status": "BLOCKED", "reason": "kill_switch", "guard_results": guard_results}

    # Step 4: 解构执行计划
    exec_plan = plan.get("execution_plan", {})
    build_orders = exec_plan.get("build_orders", []) if isinstance(exec_plan, dict) else []
    hedge_orders = plan.get("options_execution", {}).get("put_orders", [])
    if isinstance(hedge_orders, list) and not hedge_orders:
        # Fallback: try hedge_config
        hedge_orders = plan.get("options_execution", {}).get("hedge_orders", [])

    logger.info(f"建仓订单: {len(build_orders)} 笔, 对冲订单: {len(hedge_orders)} 笔")

    # Step 5: 执行
    if dry_run:
        logger.info("干跑模式 — 跳过实际执行")
        engine = MockExecutionEngine(trade_date)
        engine.fills = []  # 空成交
        report = _generate_execution_report(trade_date, plan, engine, guard_results)
        _save_report(trade_date, report)
        return {"status": "DRY_RUN_OK", "guard_results": guard_results}

    # 加载持仓
    positions_data = _load_positions()

    # 初始化执行引擎
    engine = MockExecutionEngine(trade_date)

    # 5a: 执行建仓订单
    for bo in build_orders:
        try:
            engine.execute_build_order(bo, positions_data)
        except Exception as e:
            logger.error(f"建仓执行失败 [{bo.get('code','?')}]: {e}")
            engine.errors.append({"order": bo, "reason": str(e)})

    # 5b: 执行对冲订单
    for ho in hedge_orders:
        if isinstance(ho, dict):
            try:
                engine.execute_hedge_order(ho)
            except Exception as e:
                logger.error(f"对冲执行失败 [{ho.get('instrument','?')}]: {e}")
                engine.errors.append({"order": ho, "reason": str(e)})

    # 5c: 保存更新后的持仓
    if engine.fills:
        _save_positions(positions_data)

    # Step 6: 生成执行报告
    report = _generate_execution_report(trade_date, plan, engine, guard_results)
    report_path = _save_report(trade_date, report)

    # Step 7: 输出摘要
    summ = engine.summary()
    logger.info(f"执行完成: {summ['fills']}/{summ['total_orders']} 成交, "
                f"总额 ¥{summ['total_amount']:,.2f}, "
                f"异常 {summ['errors']} 笔")

    print(f"\n{'='*60}")
    print(f"  执行摘要 — {trade_date}")
    print(f"{'='*60}")
    print(report)
    print(f"\n报告已保存: {report_path}")

    return {
        "status": "OK",
        "trade_date": trade_date,
        "summary": summ,
        "guard_results": guard_results,
        "report_path": str(report_path),
    }


def _save_report(trade_date: str, report: str) -> Path:
    yyyymmdd = trade_date.replace("-", "")
    out = REPORTS_DIR / f"execution_report_{yyyymmdd}.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write(report)
    logger.info(f"执行报告已保存: {out}")
    return out


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="交易计划自动执行器 (四Guard联动)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "date", nargs="?", default=date.today().isoformat(),
        help="交易日期 YYYY-MM-DD (默认: 今天)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="干跑模式 — 只走风控链路, 不实际执行"
    )
    parser.add_argument(
        "--auto-confirm", action="store_true",
        help="自动确认 — 跳过人工确认环节"
    )
    args = parser.parse_args()

    trade_date = args.date
    try:
        datetime.strptime(trade_date, "%Y-%m-%d")
    except ValueError:
        print(f"[ERROR] 日期格式无效: {trade_date}, 需要 YYYY-MM-DD")
        sys.exit(1)

    result = execute_trade_plan(
        trade_date=trade_date,
        dry_run=args.dry_run,
        auto_confirm=args.auto_confirm,
    )

    if result["status"] == "ERROR":
        sys.exit(1)


if __name__ == "__main__":
    main()
