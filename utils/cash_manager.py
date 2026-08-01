# -*- coding: utf-8 -*-
"""
现金管理器 v1.0
====================

实现 v10.0 投资计划的 cash_management (130 万资金, 占总资本 26%):

资金分配:
    - 期货保证金      50 万  (随期货仓位变动, 维持率 ≥ 60%)
    - 期权抵押金      10 万  (备兑期权行权准备)
    - 应急保证金      30 万  (仅在追加保证金通知时动用)
    - 逆回购 / 货基   40 万  (RCO001, 每日自动操作)

收益增厚:
    - 40 万逆回购: 年化约 2.0-2.5%
    - 剩余现金投货币基金 (511990.SH) 或短期国债 (019696.SH), 年化 1.5-2.5%
    - 现金部分贡献组合收益约 0.7%

自动化:
    - 每日 14:30 评估闲置资金, 自动下单逆回购
    - 闲置资金 > 1 万 → 自动投 RCO001
    - 月末季末资金紧张期: 利率通常飙升至 4-8%, 加大投放
    - 应急保证金动用 → 自动记录 + 次日补足

集成路径:
    daily_workflow.py phase_cash_management() → 本模块 allocate_idle_cash()
    输出: reports/cash_management_{date}.json

用法:
    from utils.cash_manager import CashManager
    cm = CashManager()
    result = cm.allocate_idle_cash(
        total_cash=1_300_000,
        futures_margin_used=480_000,
        options_collateral_used=10_000,
        current_repo_rate=0.025,
    )
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Optional, Any

logger = logging.getLogger("cash_manager")

try:
    from utils.v10_config_loader import V10ConfigLoader

    _HAS_V10 = True
except ImportError:
    V10ConfigLoader = None  # type: ignore[assignment,misc]
    _HAS_V10 = False

BASE_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = BASE_DIR / "reports"

# ============================================================
# 默认资金分配 (来自 v10.0 配置)
# ============================================================
DEFAULT_ALLOCATION = {
    "futures_margin": 500_000,  # 期货保证金
    "options_collateral": 100_000,  # 期权抵押金
    "emergency_margin": 300_000,  # 应急保证金
    "reverse_repo": 400_000,  # 逆回购 / 货基
}

# 默认收益目标
DEFAULT_YIELD_TARGET = 0.025

# 工具配置
DEFAULT_INSTRUMENTS = {
    "reverse_repo": "RCO001",  # 交易所逆回购
    "money_market_fund": "511990.SH",  # 货币基金
    "short_term_bond": "019696.SH",  # 短期国债
}

# 阈值
MIN_REPO_AMOUNT = 10_000  # 闲置资金 > 1 万才下单逆回购
HIGH_RATE_THRESHOLD = 0.04  # 利率 > 4% 加大投放
HIGH_RATE_BOOST_PCT = 1.20  # 高利率时投放增加 20%
EMERGENCY_REPLENISH_DAYS = 2  # 应急金动用后 2 日内补足


@dataclass
class CashAllocation:
    """现金分配结果"""

    trade_date: str = ""
    total_cash: float = 0.0
    # 各账户分配
    futures_margin: float = 0.0
    options_collateral: float = 0.0
    emergency_margin: float = 0.0
    reverse_repo: float = 0.0
    idle_cash: float = 0.0  # 闲置资金 (可投逆回购/货基)
    # 逆回购指令
    repo_order: Dict[str, Any] = field(default_factory=dict)
    # 收益预测
    estimated_annual_yield: float = 0.0
    estimated_daily_income: float = 0.0
    # 风控状态
    futures_margin_ratio: float = 0.0  # 期货保证金占用率
    emergency_used: float = 0.0  # 应急金已动用金额
    emergency_replenish_needed: bool = False
    # 元数据
    current_repo_rate: float = 0.0
    is_month_end: bool = False
    is_quarter_end: bool = False
    action: str = ""  # allocate / hold / replenish
    reason: str = ""


class CashManager:
    """现金管理器 — 逆回购 + 货基 + 短期国债自动化

    资金配置 (v10.0):
        - 总资金: 130 万 (占总资本 26%)
        - 期货保证金: 50 万 (随期货仓位变动)
        - 期权抵押金: 10 万
        - 应急保证金: 30 万 (仅在追加保证金通知时动用)
        - 逆回购 / 货基: 40 万 (RCO001, 每日自动操作)

    自动化逻辑:
        1. 每日 14:30 评估闲置资金
        2. 闲置资金 > 1 万 → 自动下单逆回购 RCO001
        3. 月末季末资金紧张期: 利率通常飙升至 4-8%, 加大投放
        4. 应急保证金动用 → 自动记录 + 2 日内补足
        5. 期货保证金维持率 < 60% → 触发追加保证金
    """

    def __init__(
        self,
        total_cash: float = 1_300_000,
        allocation: Optional[Dict[str, float]] = None,
        yield_target: float = DEFAULT_YIELD_TARGET,
        instruments: Optional[Dict[str, str]] = None,
    ):
        self.total_cash = total_cash
        self.allocation = allocation or DEFAULT_ALLOCATION.copy()
        self.yield_target = yield_target
        self.instruments = instruments or DEFAULT_INSTRUMENTS.copy()

        # v10.0 配置覆盖
        if _HAS_V10 and V10ConfigLoader is not None:
            try:
                loader = V10ConfigLoader()
                cash_cfg = loader.get_cash_config()
                if cash_cfg:
                    self.total_cash = float(cash_cfg.get("capital", self.total_cash))
                    if "allocation" in cash_cfg:
                        self.allocation = {k: float(v) for k, v in cash_cfg["allocation"].items()}
                    self.yield_target = float(cash_cfg.get("yield_target", self.yield_target))
                    if "instruments" in cash_cfg:
                        self.instruments = cash_cfg["instruments"]
            except Exception as e:  # P2 模块 fail-safe, 待后续精确化
                logger.warning(f"v10.0 现金配置加载失败, 使用默认值: {e}")

        logger.info(f"[CashManager] 初始化: 总资金 ¥{self.total_cash:,.0f}, 目标年化 {self.yield_target:.2%}")

    # ------------------------------------------------------------
    # 主流程: 闲置资金分配
    # ------------------------------------------------------------
    def allocate_idle_cash(
        self,
        total_cash: Optional[float] = None,
        futures_margin_used: float = 0.0,
        options_collateral_used: float = 0.0,
        emergency_used: float = 0.0,
        current_repo_rate: float = 0.025,
        trade_date: Optional[date] = None,
    ) -> CashAllocation:
        """分配闲置资金到逆回购 / 货基

        Args:
            total_cash: 当前总现金 (可选, 默认使用初始化值)
            futures_margin_used: 期货已用保证金
            options_collateral_used: 期权已用抵押金
            emergency_used: 应急金已动用金额
            current_repo_rate: 当前逆回购年化利率
            trade_date: 交易日期

        Returns:
            CashAllocation: 分配结果
        """
        trade_date = trade_date or date.today()
        total_cash = total_cash or self.total_cash

        result = CashAllocation(
            trade_date=trade_date.isoformat(),
            total_cash=total_cash,
            current_repo_rate=current_repo_rate,
            emergency_used=emergency_used,
            is_month_end=self._is_month_end(trade_date),
            is_quarter_end=self._is_quarter_end(trade_date),
        )

        # 1. 期货保证金分配
        futures_allocated = max(futures_margin_used, self.allocation["futures_margin"])
        # 留出 20% 缓冲
        futures_with_buffer = futures_allocated * 1.20
        result.futures_margin = futures_with_buffer
        result.futures_margin_ratio = futures_margin_used / futures_with_buffer if futures_with_buffer > 0 else 0

        # 2. 期权抵押金
        options_allocated = max(options_collateral_used, self.allocation["options_collateral"])
        result.options_collateral = options_allocated

        # 3. 应急保证金 (扣除已动用)
        emergency_allocated = self.allocation["emergency_margin"]
        result.emergency_margin = emergency_allocated - emergency_used
        if emergency_used > 0:
            result.emergency_replenish_needed = True
            result.reason = f"应急金已动用 ¥{emergency_used:,.0f}, 需在 {EMERGENCY_REPLENISH_DAYS} 日内补足"

        # 4. 计算闲置资金 = 总现金 - 期货 - 期权 - 应急
        allocated_total = result.futures_margin + result.options_collateral + result.emergency_margin
        idle_cash = max(0, total_cash - allocated_total)
        result.idle_cash = idle_cash

        # 5. 逆回购分配
        # 基础分配: 逆回购目标额度 (40 万)
        # 高利率时加大投放: 利率 > 4% 时增加 20%
        base_repo = self.allocation["reverse_repo"]
        if current_repo_rate > HIGH_RATE_THRESHOLD:
            # 高利率期: 加大投放
            repo_amount = min(idle_cash, base_repo * HIGH_RATE_BOOST_PCT)
            result.reason += (
                f" | 高利率 {current_repo_rate * 100:.2f}% > {HIGH_RATE_THRESHOLD * 100:.0f}%, 加大逆回购投放"
            )
        else:
            repo_amount = min(idle_cash, base_repo)

        result.reverse_repo = repo_amount

        # 6. 生成逆回购指令
        if repo_amount >= MIN_REPO_AMOUNT:
            result.repo_order = self._build_repo_order(
                amount=repo_amount,
                rate=current_repo_rate,
                trade_date=trade_date,
                is_month_end=result.is_month_end,
                is_quarter_end=result.is_quarter_end,
            )
            result.action = "allocate"
            if not result.reason:
                result.reason = f"逆回购下单 ¥{repo_amount:,.0f}, 利率 {current_repo_rate * 100:.2f}%"
        else:
            result.repo_order = {
                "action": "skip",
                "reason": f"闲置资金 ¥{idle_cash:,.0f} < 最低限额 ¥{MIN_REPO_AMOUNT:,.0f}",
            }
            result.action = "hold"
            if not result.reason:
                result.reason = "闲置资金不足, 暂不下单"

        # 7. 收益预测
        # 逆回购: 按日计息, T+1 到账
        # 货基: 按日计息, T+1 确认
        daily_repo_income = repo_amount * current_repo_rate / 365
        # 剩余闲置资金 (假设投货基, 年化 1.5%)
        remaining_idle = max(0, idle_cash - repo_amount)
        daily_mmf_income = remaining_idle * 0.015 / 365

        result.estimated_daily_income = daily_repo_income + daily_mmf_income
        # 年化收益预测 (按交易日 250 日)
        result.estimated_annual_yield = (
            (daily_repo_income + daily_mmf_income) * 250 / total_cash if total_cash > 0 else 0
        )

        # 8. 月末/季末加码
        if result.is_month_end or result.is_quarter_end:
            # 月末季末资金紧张, 利率通常飙升
            # 如果当前利率 < 4%, 预警建议持有至季末
            if current_repo_rate < HIGH_RATE_THRESHOLD:
                result.reason += f" | {('季末' if result.is_quarter_end else '月末')}建议持有现金等待利率飙升"

        # 9. 持久化
        self._save_report(result, trade_date)

        logger.info(
            f"[CashManager] 分配: 期货 ¥{result.futures_margin:,.0f}, "
            f"期权 ¥{result.options_collateral:,.0f}, "
            f"应急 ¥{result.emergency_margin:,.0f}, "
            f"逆回购 ¥{result.reverse_repo:,.0f}, "
            f"闲置 ¥{result.idle_cash:,.0f}, "
            f"日收益 ¥{result.estimated_daily_income:.2f}"
        )

        return result

    def _build_repo_order(
        self,
        amount: float,
        rate: float,
        trade_date: date,
        is_month_end: bool,
        is_quarter_end: bool,
    ) -> Dict[str, Any]:
        """生成逆回购下单指令"""
        # 逆回购合约: RCO001 (1日期逆回购)
        # 交易所: 上交所 / 深交所
        # 下单时间: 9:30-15:30 (建议 14:30 后利率通常更高)
        return {
            "action": "place_repo_order",
            "instrument": self.instruments["reverse_repo"],
            "direction": "lend",  # 借出 (融出资金)
            "amount": float(amount),
            "rate": float(rate),
            "term_days": 1,
            "trade_date": trade_date.isoformat(),
            "settlement_date": (trade_date + timedelta(days=1)).isoformat(),
            "expected_income": float(amount * rate / 365),
            "is_month_end": is_month_end,
            "is_quarter_end": is_quarter_end,
            "strategy_note": (
                "月末/季末资金紧张, 利率通常飙升至 4-8%, 建议加大投放"
                if (is_month_end or is_quarter_end) and rate > HIGH_RATE_THRESHOLD
                else "常规投放"
            ),
        }

    def _is_month_end(self, d: date) -> bool:
        """是否月末"""
        return d.day >= 25  # 25 日后视为月末窗口

    def _is_quarter_end(self, d: date) -> bool:
        """是否季末 (3/6/9/12 月的月末)"""
        return d.month in (3, 6, 9, 12) and d.day >= 25

    # ------------------------------------------------------------
    # 应急金补足
    # ------------------------------------------------------------
    def check_emergency_replenish(
        self,
        emergency_used: float,
        last_used_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        """检查应急金是否需要补足

        Args:
            emergency_used: 已动用金额
            last_used_date: 最后一次动用日期

        Returns:
            补足建议
        """
        if emergency_used <= 0:
            return {"action": "no_action", "reason": "应急金未动用"}

        today = date.today()
        last_date = last_used_date or today
        days_since = (today - last_date).days

        if days_since >= EMERGENCY_REPLENISH_DAYS:
            return {
                "action": "replenish_now",
                "amount_needed": emergency_used,
                "days_overdue": days_since - EMERGENCY_REPLENISH_DAYS,
                "reason": f"应急金动用已 {days_since} 日, 超过 {EMERGENCY_REPLENISH_DAYS} 日补足期限",
            }
        else:
            return {
                "action": "schedule_replenish",
                "amount_needed": emergency_used,
                "days_remaining": EMERGENCY_REPLENISH_DAYS - days_since,
                "replenish_date": (last_date + timedelta(days=EMERGENCY_REPLENISH_DAYS)).isoformat(),
                "reason": f"应急金动用 ¥{emergency_used:,.0f}, 计划 {EMERGENCY_REPLENISH_DAYS} 日内补足",
            }

    # ------------------------------------------------------------
    # 期货保证金追加
    # ------------------------------------------------------------
    def check_margin_call(
        self,
        futures_account_value: float,
        futures_margin_used: float,
    ) -> Dict[str, Any]:
        """检查期货保证金是否需要追加

        Args:
            futures_account_value: 期货账户权益
            futures_margin_used: 已用保证金

        Returns:
            追加保证金建议
        """
        if futures_account_value <= 0:
            return {"action": "no_action", "reason": "期货账户权益 <= 0"}

        margin_ratio = futures_margin_used / futures_account_value if futures_account_value > 0 else 0

        # 维持率 < 60% 触发追加
        if margin_ratio > 0.60:
            needed = futures_margin_used * 0.20  # 追加 20%
            # 从应急金中划拨
            if needed <= self.allocation["emergency_margin"]:
                return {
                    "action": "margin_call_from_emergency",
                    "margin_ratio": margin_ratio,
                    "amount_needed": needed,
                    "source": "emergency_margin",
                    "reason": f"期货保证金维持率 {margin_ratio:.1%} > 60%, 从应急金划拨 ¥{needed:,.0f}",
                }
            else:
                return {
                    "action": "margin_call_urgent",
                    "margin_ratio": margin_ratio,
                    "amount_needed": needed,
                    "source": "external",
                    "reason": f"期货保证金维持率 {margin_ratio:.1%} > 60%, 应急金不足, 需外部追加 ¥{needed:,.0f}",
                }

        return {
            "action": "no_action",
            "margin_ratio": margin_ratio,
            "reason": f"保证金维持率 {margin_ratio:.1%} 正常",
        }

    # ------------------------------------------------------------
    # 资金分配摘要
    # ------------------------------------------------------------
    def get_allocation_summary(self) -> Dict[str, Any]:
        """获取当前资金分配摘要"""
        total = sum(self.allocation.values())
        return {
            "total_cash": self.total_cash,
            "yield_target": self.yield_target,
            "allocation": self.allocation,
            "allocation_pct": {k: v / total for k, v in self.allocation.items()} if total > 0 else {},
            "instruments": self.instruments,
        }

    def _save_report(self, result: CashAllocation, trade_date: date):
        """持久化现金管理报告"""
        try:
            REPORTS_DIR.mkdir(parents=True, exist_ok=True)
            report_path = REPORTS_DIR / f"cash_management_{trade_date.isoformat()}.json"
            report_data = asdict(result)
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report_data, f, ensure_ascii=False, indent=2, default=str)
            logger.info(f"[CashManager] 报告已保存: {report_path}")
        except Exception as e:  # P2 模块 fail-safe, 待后续精确化
            logger.error(f"[CashManager] 报告保存失败: {e}")

    def summary(self, result: CashAllocation) -> str:
        """生成现金管理结果摘要"""
        lines = [
            "=" * 60,
            f"现金管理报告 ({result.trade_date})",
            "=" * 60,
            f"总现金: ¥{result.total_cash:,.0f}",
            f"当前逆回购利率: {result.current_repo_rate * 100:.2f}%",
            f"月末: {'是' if result.is_month_end else '否'} | 季末: {'是' if result.is_quarter_end else '否'}",
            "",
            "资金分配:",
            f"  期货保证金: ¥{result.futures_margin:,.0f} (占用率 {result.futures_margin_ratio:.1%})",
            f"  期权抵押金: ¥{result.options_collateral:,.0f}",
            f"  应急保证金: ¥{result.emergency_margin:,.0f} (已动用 ¥{result.emergency_used:,.0f})",
            f"  逆回购:    ¥{result.reverse_repo:,.0f}",
            f"  闲置资金:  ¥{result.idle_cash:,.0f}",
            "",
            "逆回购指令:",
            f"  动作: {result.repo_order.get('action', 'N/A')}",
            f"  金额: ¥{result.repo_order.get('amount', 0):,.0f}",
            f"  期限: {result.repo_order.get('term_days', 0)} 日",
            f"  预期收益: ¥{result.repo_order.get('expected_income', 0):.2f}",
            "",
            f"预期日收益: ¥{result.estimated_daily_income:.2f}",
            f"预期年化: {result.estimated_annual_yield:.2%} (目标 {self.yield_target:.2%})",
            f"动作: {result.action}",
        ]

        if result.reason:
            lines.append(f"说明: {result.reason}")

        if result.emergency_replenish_needed:
            lines.append("")
            lines.append("[警告] 应急金已动用, 需在 2 日内补足")

        lines.append("=" * 60)
        return "\n".join(lines)


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="现金管理器")
    parser.add_argument("--total-cash", type=float, default=1_300_000, help="总现金")
    parser.add_argument("--futures-margin-used", type=float, default=480_000, help="期货已用保证金")
    parser.add_argument("--options-collateral-used", type=float, default=10_000, help="期权已用抵押金")
    parser.add_argument("--repo-rate", type=float, default=0.025, help="逆回购利率")
    parser.add_argument("--emergency-used", type=float, default=0, help="应急金已动用金额")
    args = parser.parse_args()

    cm = CashManager()
    result = cm.allocate_idle_cash(
        total_cash=args.total_cash,
        futures_margin_used=args.futures_margin_used,
        options_collateral_used=args.options_collateral_used,
        emergency_used=args.emergency_used,
        current_repo_rate=args.repo_rate,
    )

    logger.info(cm.summary(result))

    # 演示季末高利率场景
    logger.info("\n" + "=" * 60)
    logger.info("季末高利率场景演示:")
    logger.info("=" * 60)
    result_eom = cm.allocate_idle_cash(
        total_cash=args.total_cash,
        futures_margin_used=args.futures_margin_used,
        options_collateral_used=args.options_collateral_used,
        current_repo_rate=0.065,  # 6.5% 季末高利率
        trade_date=date(2026, 9, 28),  # 季末
    )
    logger.info(cm.summary(result_eom))
