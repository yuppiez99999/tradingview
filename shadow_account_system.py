"""影子账户系统核心模块 — ShadowAccount + FailFastMonitor.

=================================================================
创建: 2026-08-04 (P0 日任务 — 修复 shadow_admission_launcher DSR 计算失败)

背景:
    utils/alpha/shadow_account_adapter.py 的 _create_shadow_account() 延迟导入
    `from shadow_account_system import FailFastMonitor, ShadowAccount`,
    但该模块从未被创建, 导致 ShadowAccountAdapter 初始化即 ModuleNotFoundError.
    本模块补齐这一基础设施缺口.

设计原则:
    - 最小化实现: 只覆盖 adapter 实际使用的接口 (零行为变更)
    - fail-fast 语义: 单日回撤>阈值 或 3日累计回撤>阈值 → TERMINATED + latch
    - 状态可查询: status.value / fail_fast_monitor.get_status()
    - 与 launch_shadow_account.py 的 shadow_state.json 字段对齐

接口契约 (adapter 依赖):
    ShadowAccount:
        - __init__(account_id, strategy_id, initial_capital)
        - status: AccountStatus (有 .value, "running" | "terminated")
        - fail_fast_monitor: FailFastMonitor (可被外部覆盖)
        - record_daily_nav(date, nav): 记录每日净值 + 检查 fail-fast
        - get_performance(): 返回 {current_nav, ...}
        - trade_log: list
        - account_id / strategy_id: str

    FailFastMonitor:
        - __init__(daily_drawdown_threshold, cumulative_3d_drawdown_threshold)
        - get_status(): 返回 {triggered, reason, date}
        - check(nav_history, date, nav): 检查并更新内部状态

用法:
    from shadow_account_system import ShadowAccount, FailFastMonitor
    account = ShadowAccount(account_id="shadow_001", strategy_id="V9", initial_capital=500000)
    account.record_daily_nav(date="2026-08-04", nav=0.9965)
    if account.status.value == "terminated":
        print(f"Fail-Fast: {account.fail_fast_monitor.get_status()}")
=================================================================
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ============================================================
# 账户状态枚举
# ============================================================
class AccountStatus(Enum):
    """影子账户状态."""

    RUNNING = "running"
    TERMINATED = "terminated"


# ============================================================
# Fail-Fast 监控器
# ============================================================
@dataclass
class FailFastMonitor:
    """Fail-Fast 监控器 — 检查回撤阈值.

    触发条件 (用户硬约束):
        - 单日回撤 > daily_drawdown_threshold (默认 3%)
        - 3 日累计回撤 > cumulative_3d_drawdown_threshold (默认 5%)

    触发后 latch (不可恢复), 需人工介入.
    """

    daily_drawdown_threshold: float = 0.03
    cumulative_3d_drawdown_threshold: float = 0.05
    latch: bool = True

    # 内部状态
    _triggered: bool = field(default=False, init=False)
    _reason: str | None = field(default=None, init=False)
    _triggered_date: str | None = field(default=None, init=False)

    def check(self, nav_history: list[dict], date: str, nav: float) -> bool:
        """检查是否触发 fail-fast.

        Args:
            nav_history: 历史净值列表 (含当日), 每条 {date, nav}
            date: 当日日期
            nav: 当日净值

        Returns:
            True 若触发 (或已触发), False 若正常
        """
        if self._triggered and self.latch:
            return True

        if len(nav_history) < 1:
            return False

        # 单日回撤: 相对前一日净值
        if len(nav_history) >= 2:
            prev_nav = nav_history[-2]["nav"]
            if prev_nav > 0:
                daily_ret = (nav / prev_nav) - 1
                if daily_ret < -self.daily_drawdown_threshold:
                    self._trigger(
                        reason=(
                            f"单日回撤 {daily_ret*100:.2f}% 超过阈值 "
                            f"-{self.daily_drawdown_threshold*100:.0f}% (date={date})"
                        ),
                        date=date,
                    )
                    return True

        # 3 日累计回撤: 相对 3 天前净值
        if len(nav_history) >= 3:
            nav_3d_ago = nav_history[-3]["nav"]
            if nav_3d_ago > 0:
                cum_ret = (nav / nav_3d_ago) - 1
                if cum_ret < -self.cumulative_3d_drawdown_threshold:
                    self._trigger(
                        reason=(
                            f"3日累计回撤 {cum_ret*100:.2f}% 超过阈值 "
                            f"-{self.cumulative_3d_drawdown_threshold*100:.0f}% "
                            f"(date={date}, 3天前={nav_history[-3]['date']})"
                        ),
                        date=date,
                    )
                    return True

        return False

    def _trigger(self, reason: str, date: str) -> None:
        """触发 fail-fast (latch)."""
        self._triggered = True
        self._reason = reason
        self._triggered_date = date
        logger.warning(
            "[FailFastMonitor] ⚠️ Fail-Fast 触发: %s (date=%s)", reason, date
        )

    def get_status(self) -> dict[str, Any]:
        """获取 fail-fast 状态."""
        return {
            "triggered": self._triggered,
            "reason": self._reason,
            "date": self._triggered_date,
            "thresholds": {
                "daily_drawdown": self.daily_drawdown_threshold,
                "cumulative_3d": self.cumulative_3d_drawdown_threshold,
            },
        }


# ============================================================
# 影子账户
# ============================================================
@dataclass
class ShadowAccount:
    """影子账户 — 记录每日净值 + fail-fast 监控.

    生命周期:
        1. __init__: 初始化资金/状态/fail_fast_monitor
        2. record_daily_nav: 每日调用, 记录净值并检查 fail-fast
        3. status: RUNNING → TERMINATED (fail-fast 触发时)
    """

    account_id: str
    strategy_id: str
    initial_capital: float = 1_000_000

    # 运行时状态 (不参与 __init__ 参数)
    status: AccountStatus = field(default=AccountStatus.RUNNING, init=False)
    fail_fast_monitor: FailFastMonitor = field(
        default_factory=FailFastMonitor, init=False
    )
    daily_nav: list[dict[str, Any]] = field(default_factory=list, init=False)
    trade_log: list[dict[str, Any]] = field(default_factory=list, init=False)
    current_nav: float = field(default=1.0, init=False)
    current_capital: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        """初始化运行时状态."""
        self.current_capital = float(self.initial_capital)
        # 默认 fail_fast_monitor (可被外部覆盖, 如 adapter _create_shadow_account)
        self.fail_fast_monitor = FailFastMonitor(
            daily_drawdown_threshold=0.03,
            cumulative_3d_drawdown_threshold=0.05,
        )
        logger.info(
            "[ShadowAccount] 初始化 | account_id=%s strategy_id=%s capital=¥%.0f",
            self.account_id,
            self.strategy_id,
            self.initial_capital,
        )

    def record_daily_nav(self, date: str, nav: float) -> None:
        """记录每日净值并检查 fail-fast.

        Args:
            date: 日期 (YYYY-MM-DD)
            nav: 当日净值 (从 1.0 开始累乘)
        """
        if self.status == AccountStatus.TERMINATED:
            logger.warning(
                "[ShadowAccount] 已终止, 忽略 record_daily_nav(date=%s)", date
            )
            return

        # 计算日收益 (相对前一日)
        daily_return = 0.0
        if self.daily_nav:
            prev_nav = self.daily_nav[-1]["nav"]
            if prev_nav > 0:
                daily_return = (nav / prev_nav) - 1

        # 记录净值
        entry = {
            "date": date,
            "nav": float(nav),
            "daily_return": float(daily_return),
            "capital": float(self.initial_capital) * float(nav),
            "recorded_at": datetime.now().isoformat(),
        }
        self.daily_nav.append(entry)
        self.current_nav = float(nav)
        self.current_capital = float(self.initial_capital) * float(nav)

        # 检查 fail-fast
        triggered = self.fail_fast_monitor.check(self.daily_nav, date, nav)
        if triggered:
            self.status = AccountStatus.TERMINATED
            logger.error(
                "[ShadowAccount] ⚠️ Fail-Fast 触发, 账户已终止 (date=%s, nav=%.6f)",
                date,
                nav,
            )

    def get_performance(self) -> dict[str, Any]:
        """获取绩效摘要."""
        total_return = (self.current_nav - 1.0) if self.current_nav > 0 else 0

        # 计算最大回撤
        peak = 1.0
        max_dd = 0.0
        for entry in self.daily_nav:
            n = entry["nav"]
            if n > peak:
                peak = n
            dd = (peak - n) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        return {
            "current_nav": self.current_nav,
            "current_capital": self.current_capital,
            "total_return": total_return,
            "max_drawdown": max_dd,
            "days_tracked": len(self.daily_nav),
            "initial_capital": self.initial_capital,
        }

    def consume_fills_from_store(
        self,
        dates: Collection[str],
        strategies: Collection[str] = ("build",),
    ) -> dict[str, Any]:
        """从 FillsStore 读取多日指定策略成交, 填入 trade_log, 构建 cumulative holdings, 算 NAV.

        P3.0 门禁 ① (2026-08-26): shadow 实读 strategy=build fills 出 NAV.
        影子账户此前 trade_log 恒空 (NAV 全靠外部注入或行情估值), 本方法打通
        "fills → trade_log → holdings → NAV" 链路, 使影子账户能消费真实成交.

        Args:
            dates: 交易日集合 (将按时间顺序处理, 构建 cumulative holdings)
            strategies: 策略标签, 默认 ("build",) — 只消费建仓成交

        Returns:
            {fills_count, holdings, nav, trade_log_len, dates_processed}
            fail-open: FillsStore 不可用时返回零值, 不抛异常.
        """
        try:
            from utils.execution.fills_store import FillsStore
        except ImportError:
            logger.warning("[ShadowAccount] consume_fills: FillsStore 不可用")
            return {
                "fills_count": 0,
                "holdings": {},
                "nav": 1.0,
                "trade_log_len": len(self.trade_log),
                "dates_processed": [],
            }

        if self.status == AccountStatus.TERMINATED:
            logger.warning("[ShadowAccount] 已终止, 忽略 consume_fills")
            return {
                "fills_count": 0,
                "holdings": {},
                "nav": self.current_nav,
                "trade_log_len": len(self.trade_log),
                "dates_processed": [],
            }

        store = FillsStore()
        holdings: dict[str, float] = {}
        cash = float(self.initial_capital)
        fills_count = 0
        latest_prices: dict[str, float] = {}
        nav = 1.0
        dates_processed: list[str] = []

        for date in sorted(dates):
            day_fills = store.load_day(date, strategies=strategies)
            if not day_fills:
                continue
            dates_processed.append(date)
            for fill in day_fills:
                sym = fill.get("symbol", "")
                qty = fill.get("filled_qty", 0)
                price = fill.get("avg_price", 0)
                side = fill.get("side", "")
                self.trade_log.append(
                    {
                        "date": fill.get("date", date),
                        "symbol": sym,
                        "side": side,
                        "qty": qty,
                        "price": price,
                        "strategy": fill.get("strategy"),
                        "source": "fills_store",
                    }
                )
                if side == "BUY":
                    holdings[sym] = holdings.get(sym, 0) + qty
                    cash -= qty * price
                elif side == "SELL":
                    holdings[sym] = holdings.get(sym, 0) - qty
                    cash += qty * price
                latest_prices[sym] = price
                fills_count += 1

            # 逐日 NAV (用当日成交均价估值持仓)
            position_value = sum(
                qty * latest_prices.get(sym, 0)
                for sym, qty in holdings.items()
                if qty > 0
            )
            nav = (
                (cash + position_value) / self.initial_capital
                if self.initial_capital > 0
                else 0
            )
            self.record_daily_nav(date, nav)

        result = {
            "fills_count": fills_count,
            "holdings": {s: q for s, q in holdings.items() if q != 0},
            "nav": nav,
            "trade_log_len": len(self.trade_log),
            "dates_processed": dates_processed,
        }
        logger.info(
            "[ShadowAccount] consume_fills: fills=%d holdings=%d nav=%.6f dates=%s",
            fills_count,
            len(result["holdings"]),
            nav,
            dates_processed,
        )
        return result

    def to_state_dict(self) -> dict[str, Any]:
        """导出为可序列化的状态字典 (供 shadow_state.json 持久化)."""
        ff_status = self.fail_fast_monitor.get_status()
        return {
            "account_id": self.account_id,
            "strategy_id": self.strategy_id,
            "status": self.status.value,
            "initial_capital": self.initial_capital,
            "current_capital": self.current_capital,
            "current_nav": self.current_nav,
            "daily_nav": self.daily_nav,
            "trade_log": self.trade_log,
            "fail_fast_log": (
                [
                    {
                        "terminated_at": datetime.now().isoformat(),
                        "reason": ff_status.get("reason"),
                        "date": ff_status.get("date"),
                    }
                ]
                if ff_status.get("triggered")
                else []
            ),
            "fail_fast_config": {
                "daily_drawdown_threshold": self.fail_fast_monitor.daily_drawdown_threshold,
                "cumulative_3d_drawdown_threshold": self.fail_fast_monitor.cumulative_3d_drawdown_threshold,
                "latch": self.fail_fast_monitor.latch,
            },
            "last_updated": datetime.now().isoformat(),
        }


# ============================================================
# 便捷函数
# ============================================================
def create_shadow_account(
    account_id: str,
    strategy_id: str,
    initial_capital: float = 500_000,
    daily_dd_threshold: float = 0.03,
    cumulative_3d_threshold: float = 0.05,
) -> ShadowAccount:
    """创建带自定义 fail-fast 阈值的影子账户."""
    account = ShadowAccount(
        account_id=account_id,
        strategy_id=strategy_id,
        initial_capital=initial_capital,
    )
    account.fail_fast_monitor = FailFastMonitor(
        daily_drawdown_threshold=daily_dd_threshold,
        cumulative_3d_drawdown_threshold=cumulative_3d_threshold,
    )
    return account
