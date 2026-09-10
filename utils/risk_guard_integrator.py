"""
风控守卫集成器 (Risk Guard Integrator)
=======================================
创建日期: 2026-07-21
创建原因: P1 回撤防护强制执行 + P0/P2/P3模块联动

核心功能:
    在 run_daily_eod.py 盘后流程中，串联所有风控模块，
    将"纸面规则"变成"代码强制执行"。

执行链路:
    1. 回撤检查 → 如果 Level≥2，自动修改次日计划（减仓+加对冲）
    2. 波动率目标 → 如果 vol_scale<0.8，缩减次日建仓预算
    3. 对冲引擎 → 计算并写入次日对冲订单
    4. 认沽保护 → 检查并生成保护性认沽订单

设计原则:
    - 每个模块独立失败不影响其他模块
    - 所有决策写入日志 + trade_plan
    - 强制性：Level 3+ 回撤直接覆写次日计划

用法:
    from utils.risk_guard_integrator import RiskGuardIntegrator
    rgi = RiskGuardIntegrator(report_date="2026-07-21")
    rgi.run_all_guards(next_trade_date="2026-07-22")
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from utils.datetime_utils import now_bj
from utils.risk.guards import plan_context
from utils.risk.guards.drawdown import DrawdownGuardMixin
from utils.risk.guards.hedge import HedgeGuardMixin
from utils.risk.guards.correlation import CorrelationGuardMixin
from utils.risk.guards.margin_kill_switch import MarginKillSwitchGuardMixin
from utils.risk.guards.market import MarketGuardMixin
from utils.risk.guards.sentiment import SentimentGuardMixin
from utils.risk.guards.vol_target import VolTargetGuardMixin


# 审计 item 11 (2026-09-10) 拆解: KillSwitchLevel / parse_kill_switch_level 已迁至
# utils/risk/guards/kill_switch_level.py; 落盘路径常量 (BASE_DIR/TRADE_PLANS_DIR/
# REPORTS_DIR/DAILY_REPORT_DIR/LOGS_DIR) 与 UNDERLYING_CODE_MAP 已迁至
# utils/risk/guards/plan_context.py。


class RiskGuardIntegrator(
    DrawdownGuardMixin,
    VolTargetGuardMixin,
    HedgeGuardMixin,
    MarginKillSwitchGuardMixin,
    MarketGuardMixin,
    SentimentGuardMixin,
    CorrelationGuardMixin,
):
    """风控守卫集成器 - 串联所有风控模块并强制执行

    审计 item 11 拆解 (2026-09-10): 本类改为多继承 mixin 组合, 编排骨架
    (run_all_guards / _run_guard_step / _enforce_risk_field_consistency /
    _write_guard_log / main) 保留在本模块; 各 guard_* 按域迁至 utils/risk/guards/。

    注意: 三个 Guard mixin 均继承 ``PlanContextMixin``, 故此处**不再显式列出**
    ``PlanContextMixin`` —— 显式列出会与各 mixin 的基类产生 MRO 冲突
    (TypeError: Cannot create a consistent MRO)。最终 MRO 仍为
    ``RiskGuardIntegrator → Drawdown → VolTarget → Hedge → PlanContextMixin → object``,
    ``_log`` 与数据 IO 由 MRO 末端的 ``PlanContextMixin`` 统一提供。
    """

    def __init__(self, report_date: str | None = None, total_capital: float = 5_000_000):
        """初始化风控守卫集成器。

        Args:
            report_date: 报告日期 (YYYY-MM-DD)；None 时取当天
            total_capital: 总资金规模，默认 500 万
        """
        self.report_date = report_date or now_bj().strftime("%Y-%m-%d")
        self.total_capital = total_capital
        self.log_entries: list[str] = []
        # 属性式访问: 使测试的 monkeypatch.setattr(plan_context, "LOGS_DIR", tmp) 生效
        plan_context.LOGS_DIR.mkdir(parents=True, exist_ok=True)






    # ============================================================
    # 主执行入口
    # ============================================================
    # run_all_guards 兜底 except 的异常集合 (v8.6.6 起 8-Guard 各块统一;
    # [7/8] 块原书写顺序 ValueError, KeyError, TypeError — 集合相同, except 语义等价)
    _GUARD_EXC_TYPES: tuple[type[Exception], ...] = (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        OSError,
        RuntimeError,
        ImportError,
    )

    def _run_guard_step(
        self,
        label: str,
        guard_call: Callable[[], dict],
        crash_log: str,
        error_key: str,
        fail_closed: bool,
        plan: dict,
    ) -> dict:
        """执行单个 Guard 并兜底异常 (提取自 run_all_guards 的 10 段重复模式).

        2026-09-08 重构 (零行为变化, 特征测试保护下按 8-Guard 分步提取):
        - fail_closed=False: 崩溃仅记录 error key, 不阻塞链路
            ([1/8] 负面新闻 fail-open / [8/8] 对冲 + 认沽 + 相关性仅记录,
             日志级别差异 ([WARNING]/[CRITICAL]) 由调用点 crash_log 前缀给出)
        - fail_closed=True: fail-closed (v8.6.13 P1 FIX 语义) — 崩溃时保守禁止开仓:
            spot_build_allowed=False + build_allowed=False + circuit_level 升 WARNING
            (不覆盖已存在的 CRITICAL)

        Args:
            label: 守卫标签 (写入 "--- {label} ---" 分隔日志, 与原文逐字节一致)
            guard_call: 无参 callable, 返回修改后的 plan (闭包包装 guard_xxx 调用)
            crash_log: 崩溃日志前缀 (含级别, 调用点保证与原文本逐字节一致)
            error_key: plan["risk_guard"] 下的崩溃错误键
            fail_closed: 崩溃时是否执行保守禁开仓
            plan: 当前交易计划 (就地修改 risk_guard/market_state)

        Returns:
            处理后的 plan (正常路径 = guard 返回值; 崩溃路径 = 传入 plan 本体)
        """
        self._log(f"--- {label} ---")
        try:
            return guard_call()
        except self._GUARD_EXC_TYPES as e:
            self._log(f"{crash_log}: {e}")
            plan.setdefault("risk_guard", {})[error_key] = str(e)
            if fail_closed:
                # v8.6.13 P1 FIX (2026-08-01 AI 扫描): 崩溃时只设 spot_build_allowed=False
                # 不够 — 下游执行器读 build_allowed 会绕过 Guard 崩溃限制, 在风控异常时
                # 仍允许开仓. 修复: 同步双 False + 升级 circuit_level 到 WARNING
                # (不覆盖 CRITICAL).
                ms = plan.setdefault("market_state", {})
                ms["spot_build_allowed"] = False
                ms["build_allowed"] = False
                if str(ms.get("circuit_level", "NORMAL")).upper() != "CRITICAL":
                    ms["circuit_level"] = "WARNING"
            return plan

    def _enforce_risk_field_consistency(self, plan: dict) -> None:
        """风控字段最终一致性校验 (defense-in-depth, 提取自 run_all_guards 收尾).

        v8.6.8 P0-01 FIX (2026-07-26): 顶级对冲基金标准 — 任何风控字段的最终状态
        必须自洽, 防止 Guard 链中某个 Guard 设了 circuit_level=CRITICAL 但遗漏
        build_allowed=False, 导致 trade_plan 出现 build_allowed=true vs
        circuit_level=CRITICAL 的矛盾. 此校验作为最后兜底, 在保存前强制对齐
        所有派生字段. 崩溃仅记日志, 不中断风控链路 (原行为保持).

        Args:
            plan: 交易计划 (就地修改 market_state/execution_plan/hedge_fund_overlays)
        """
        try:
            ms = plan.setdefault("market_state", {})
            circuit_lvl = str(ms.get("circuit_level", "NORMAL")).upper()
            if circuit_lvl == "CRITICAL":
                if ms.get("build_allowed", True) or ms.get("spot_build_allowed", True):
                    self._log(
                        f"[一致性校验] circuit_level=CRITICAL 但 build_allowed="
                        f"{ms.get('build_allowed')}, spot_build_allowed="
                        f"{ms.get('spot_build_allowed')}, 强制对齐为 False"
                    )
                ms["build_allowed"] = False
                ms["spot_build_allowed"] = False
            elif circuit_lvl == "WARNING":
                if ms.get("spot_build_allowed", True):
                    self._log(
                        f"[一致性校验] circuit_level=WARNING, spot_build_allowed="
                        f"{ms.get('spot_build_allowed')}, 强制对齐为 False"
                    )
                ms["spot_build_allowed"] = False

            # v8.6.8 P0-04 FIX (2026-07-26): spot_build_allowed=False 时必须清空 Theta Covered Call
            # 原始 bug: spot_build_allowed=false (WARNING) 时仅阻止现货建仓, 但 execution_plan.options_orders
            # 仍包含 Theta 引擎生成的 Covered Call 订单 (SELL_CALL). Covered Call 必须先持有现货才能卖出,
            # 无现货时执行 SELL_CALL 是裸卖出, 风险无限 (类似 GME 逼空事件).
            # 顶级对冲基金标准: 备兑策略必须有底层多头支撑, 否则一律禁止
            if not ms.get("spot_build_allowed", True):
                exec_plan = plan.get("execution_plan", {}) or {}
                cc_orders = exec_plan.get("options_orders", []) or []
                if cc_orders:
                    removed_cc = [
                        o
                        for o in cc_orders
                        if str(o.get("direction", "")).upper() == "SELL_CALL" or "CoveredCall" in str(o.get("name", ""))
                    ]
                    kept_cc = [
                        o
                        for o in cc_orders
                        if not (
                            str(o.get("direction", "")).upper() == "SELL_CALL"
                            or "CoveredCall" in str(o.get("name", ""))
                        )
                    ]
                    if removed_cc:
                        exec_plan["options_orders"] = kept_cc
                        exec_plan["options_orders_count"] = len(kept_cc)
                        # 重算总权利金 (仅 SELL_CALL 收入, 买入 PUT 是支出)
                        new_premium = sum(
                            float(o.get("est_premium_total", 0))
                            for o in kept_cc
                            if str(o.get("direction", "")).upper() == "SELL_CALL"
                        )
                        exec_plan["options_total_premium"] = round(new_premium, 2)
                        plan["execution_plan"] = exec_plan
                        self._log(
                            f"[一致性校验] [P0-04] spot_build_allowed=False, "
                            f"已拦截 {len(removed_cc)} 笔 Theta Covered Call 订单 "
                            f"(裸卖出 Call 风险无限, 必须有现货备兑)"
                        )
                        # 同步 hedge_fund_overlays.theta_engine
                        theta = plan.get("hedge_fund_overlays", {}).get("theta_engine", {})
                        if theta:
                            theta["positions_count"] = len(kept_cc)
                            theta["total_premium"] = round(new_premium, 2)
                            theta["blocked_reason"] = "spot_build_allowed=False, Covered Call 已拦截"

            # 同步 hedge_fund_overlays.v77_notes 与 market_state 一致
            hfo = plan.setdefault("hedge_fund_overlays", {})
            notes = hfo.setdefault("v77_notes", {})
            notes["build_allowed"] = ms.get("build_allowed", True)
            notes["spot_build_allowed"] = ms.get("spot_build_allowed", True)
            if not ms.get("build_allowed", True) and not notes.get("reason_if_blocked"):
                notes["reason_if_blocked"] = f"circuit_level={circuit_lvl}"
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as _e_consistency:
            self._log(f"[一致性校验] 异常: {_e_consistency}")

    def run_all_guards(self, next_trade_date: str) -> dict:
        """执行所有风控守卫

        Args:
            next_trade_date: 下一交易日 (YYYY-MM-DD)

        Returns:
            修改后的交易计划
        """
        self._log("=" * 60)
        self._log(f"风控守卫集成器启动 | 报告日:{self.report_date} → 次日:{next_trade_date}")
        self._log("=" * 60)

        # 加载数据 (显式注解: 闭包引用不作 flow 收窄, 声明的 pnl_report 必须非 Optional)
        loaded_pnl = self._load_pnl_report()
        if loaded_pnl:
            pnl_report: dict[Any, Any] = loaded_pnl
        else:
            self._log("[WARN] 无法加载盈亏报告，使用空报告继续")
            pnl_report = {
                "portfolio_pnl": {
                    "summary": {
                        "total_cost": 0,
                        "total_market_value": 0,
                        "total_pnl": 0,
                    }
                }
            }

        # plan 显式注解为 dict: 闭包引用不作 flow 收窄, 必须让声明的 plan 为非 Optional
        loaded_plan = self._load_next_trade_plan(next_trade_date)
        if loaded_plan:
            plan: dict[Any, Any] = loaded_plan
        else:
            self._log("[WARN] 无法加载次日计划，风控守卫将只输出日志")
            plan = {
                "phase": {"daily_capital": 150000},
                "execution_plan": {},
                "risk_guard": {},
            }

        # 按优先级执行 (重大负面新闻最高优先级, 其次 KillSwitch, 然后回撤)
        # 每个 guard 独立 try-except, 防止单个 guard 崩溃中断整个风控链路
        # v8.6.6 (P1-H/J/I/K): EOD Guard 链从 5 个扩展为 7 个, 覆盖大盘级/组合级/对冲级三层风控
        # v8.4 (P2-增强, 2026-07-30): 新增 [1/8] 重大负面新闻 Guard (P2-增强项, fail-open)
        # 审计: docs/HEDGE_FUND_AUDIT_V2_2026-07-26.md

        # [1/8] 重大负面新闻 (P2-增强, v8.4 2026-07-30) — 最高优先级, 重大利空立即停建仓
        # 必须在 KillSwitch 之前执行, 因为负面新闻是突发事件, 比 KillSwitch 的保证金检查更紧急
        # 但采用 fail-open 设计: 检查失败不阻塞核心 P0/P1 风控链路
        plan = self._run_guard_step(
            "[1/8] 重大负面新闻 (P2-增强)",
            lambda: self.guard_sentiment_breaking_news(pnl_report, plan),
            "[WARNING] 重大负面新闻检查崩溃 (fail-open, 不阻塞)",
            "sentiment_breaking_news_error",
            fail_closed=False,
            plan=plan,
        )

        # [2/8] 保证金熔断 (KillSwitch) — 最高优先级 (原有)
        plan = self._run_guard_step(
            "[2/8] 保证金熔断 (KillSwitch)",
            lambda: self.guard_kill_switch(pnl_report, plan),
            "[CRITICAL] 保证金熔断检查崩溃",
            "kill_switch_error",
            fail_closed=True,
            plan=plan,
        )

        # [3/8] 大盘熔断 (P1-H 新增) — 大盘级
        plan = self._run_guard_step(
            "[3/8] 大盘熔断 (P1-H)",
            lambda: self.guard_market_circuit_breaker(pnl_report, plan),
            "[CRITICAL] 大盘熔断检查崩溃",
            "market_circuit_breaker_error",
            fail_closed=True,
            plan=plan,
        )

        # [4/8] 流动性危机 (P1-J 新增) — 全市场涨跌停
        plan = self._run_guard_step(
            "[4/8] 流动性危机 (P1-J)",
            lambda: self.guard_liquidity_crisis(pnl_report, plan),
            "[CRITICAL] 流动性危机检查崩溃",
            "liquidity_crisis_error",
            fail_closed=True,
            plan=plan,
        )

        # [5/8] 隔夜跳空 (P1-I 新增) — 隔夜外盘风险
        plan = self._run_guard_step(
            "[5/8] 隔夜跳空 (P1-I)",
            lambda: self.guard_overnight_gap(pnl_report, plan),
            "[CRITICAL] 隔夜跳空检查崩溃",
            "overnight_gap_error",
            fail_closed=True,
            plan=plan,
        )

        # [6/8] 回撤检查 — 组合级 (原有)
        plan = self._run_guard_step(
            "[6/8] 回撤检查",
            lambda: self.guard_drawdown(pnl_report, plan),
            "[CRITICAL] 回撤检查崩溃",
            "drawdown_error",
            fail_closed=True,
            plan=plan,
        )

        # [7/8] 波动率控制 — 组合级 (原有)
        plan = self._run_guard_step(
            "[7/8] 波动率控制",
            lambda: self.guard_vol_target(pnl_report, plan),
            "[CRITICAL] 波动率控制崩溃",
            "vol_target_error",
            fail_closed=True,
            plan=plan,
        )

        # [8/8] 对冲执行 + 认沽保护 + 相关性对冲 — 对冲动作 (原有 + P1-K 新增)
        plan = self._run_guard_step(
            "[8/8] 对冲执行",
            lambda: self.guard_hedge_execution(pnl_report, plan, next_trade_date),
            "[CRITICAL] 对冲执行崩溃",
            "hedge_error",
            fail_closed=False,
            plan=plan,
        )

        plan = self._run_guard_step(
            "[7/7] 认沽保护",
            lambda: self.guard_protective_put(pnl_report, plan, next_trade_date),
            "[CRITICAL] 认沽保护崩溃",
            "put_error",
            fail_closed=False,
            plan=plan,
        )

        plan = self._run_guard_step(
            "[7/7] 相关性对冲 (P1-K)",
            lambda: self.guard_correlation_hedge(pnl_report, plan),
            "[CRITICAL] 相关性对冲崩溃",
            "correlation_hedge_error",
            fail_closed=False,
            plan=plan,
        )

        # v7.7: 去重 — 避免对冲引擎与认沽保护引擎对同一底层重复生成PUT
        self._log("--- [去重] 检查 PUT 订单重叠 ---")
        self._deduplicate_put_orders(plan)

        # v8.6.8 P0-01 FIX (2026-07-26): 风控字段最终一致性校验 (defense-in-depth)
        # 提取为独立方法 _enforce_risk_field_consistency (2026-09-08 重构, 零行为变化)
        self._enforce_risk_field_consistency(plan)

        # 写入时间戳
        plan.setdefault("risk_guard", {})["last_run"] = now_bj().isoformat()
        plan["risk_guard"]["report_date"] = self.report_date

        # 保存修改后的计划
        self._save_trade_plan(plan, next_trade_date)

        # 写入风控日志
        self._write_guard_log(next_trade_date)

        self._log("=" * 60)
        self._log("风控守卫集成器完成")
        self._log("=" * 60)

        return plan

    def _write_guard_log(self, next_date: str) -> None:
        """写入风控日志"""
        try:
            log_file = plan_context.LOGS_DIR / f"risk_guard_{next_date.replace('-', '')}.log"
            with open(log_file, "w", encoding="utf-8") as f:
                f.write("\n".join(self.log_entries))
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError):
            pass


# ============================================================
# CLI 入口
# ============================================================
def main() -> None:
    """命令行入口: python -m utils.risk_guard_integrator [report_date] [next_date]"""
    import sys
    from datetime import timedelta

    report_date = sys.argv[1] if len(sys.argv) > 1 else now_bj().strftime("%Y-%m-%d")

    if len(sys.argv) > 2:
        next_date = sys.argv[2]
    else:
        # 计算下一交易日
        from datetime import datetime as _dt

        today = _dt.strptime(report_date, "%Y-%m-%d")
        next_day = today + timedelta(days=1)
        while next_day.weekday() >= 5:
            next_day += timedelta(days=1)
        next_date = next_day.strftime("%Y-%m-%d")

    integrator = RiskGuardIntegrator(report_date=report_date)
    integrator.run_all_guards(next_trade_date=next_date)


if __name__ == "__main__":
    main()
