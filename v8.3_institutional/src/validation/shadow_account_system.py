"""
影子账户验证系统
根据审计建议 #P2-8 创建

核心原则：
1. 所有策略上线前在影子账户跟踪至少2周
2. 影子账户绩效与回测预期偏差>30%则拒绝上线
3. 生产策略更新用灰度发布：10%→50%→全量
4. 每个阶段有明确的回滚触发条件

2026-07-25 顶级对冲基金审计 P0-11(shadow) 修复:
  新增 FailFastMonitor — 影子账户 fail-fast 触发器
    - 单日回撤 > 3% → 立即终止
    - 3 日累计回撤 > 5% → 立即终止
  审计问题: 原系统仅有绩效偏差检查 (30%), 无日内/3日 fail-fast,
            极端行情下影子账户可能持续亏损而未被终止
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class ShadowStatus(Enum):
    """影子账户状态"""

    RUNNING = "running"
    PASS = "pass"
    FAIL = "fail"
    ROLLBACK = "rollback"
    TERMINATED = "terminated"  # P0-11(shadow): fail-fast 触发后的终止状态


@dataclass
class ShadowMetrics:
    """影子账户指标"""

    strategy_id: str
    period_start: str
    period_end: str
    backtest_cagr: float  # 回测CAGR
    shadow_cagr: float  # 影子账户CAGR
    backtest_sharpe: float  # 回测夏普
    shadow_sharpe: float  # 影子账户夏普
    backtest_max_dd: float  # 回测最大回撤
    shadow_max_dd: float  # 影子账户最大回撤
    deviation_pct: float  # 偏差百分比
    days_traded: int  # 交易天数
    total_trades: int  # 总交易次数
    avg_daily_turnover: float  # 日均换手率
    status: str  # 状态


class FailFastMonitor:
    """P0-11(shadow): 影子账户 fail-fast 触发器 (2026-07-25 顶级对冲基金审计)

    审计问题: 原系统仅有 30% 绩效偏差检查 (需 2 周累积), 无日内/3日 fail-fast,
              极端行情下影子账户可能持续亏损而未被终止, 导致策略缺陷暴露不足。

    顶级对冲基金标准 (用户硬约束):
      - 单日回撤 > 3%  → 立即终止 (日内异常波动)
      - 3日累计回撤 > 5% → 立即终止 (持续性回撤, 非单日噪音)

    触发后动作:
      1. 设置 ShadowAccount.status = TERMINATED
      2. 记录终止原因到 fail_fast_log
      3. 触发 GrayReleaseManager._rollback() 回滚
    """

    def __init__(self, daily_drawdown_threshold: float = 0.03, cumulative_3d_drawdown_threshold: float = 0.05):
        """初始化 fail-fast 监控器

        Args:
            daily_drawdown_threshold: 单日回撤阈值 (默认 3%)
            cumulative_3d_drawdown_threshold: 3日累计回撤阈值 (默认 5%)
        """
        self.daily_dd_threshold = float(daily_drawdown_threshold)
        self.cumulative_3d_threshold = float(cumulative_3d_drawdown_threshold)
        self.triggered: bool = False
        self.trigger_reason: Optional[str] = None
        self.trigger_date: Optional[str] = None
        self.trigger_details: Dict[str, Any] = {}
        self._history: List[Dict[str, Any]] = []

    def check(self, daily_nav: List[Dict[str, Any]]) -> Dict[str, Any]:
        """检查 fail-fast 触发条件

        Args:
            daily_nav: 影子账户的每日净值记录列表
                       [{"date": "2026-07-25", "nav": 1.02, ...}, ...]

        Returns:
            {
                "triggered": bool,
                "reason": str (若触发),
                "details": dict,
            }
        """
        if self.triggered:
            # 一旦触发, 持续返回触发状态 (latch)
            return {
                "triggered": True,
                "reason": self.trigger_reason,
                "details": self.trigger_details,
                "note": "fail-fast 已触发 (latch), 影子账户已终止",
            }

        if len(daily_nav) < 1:
            return {"triggered": False, "reason": "", "details": {}}

        latest = daily_nav[-1]
        latest_nav = float(latest.get("nav", 1.0))
        latest_date = str(latest.get("date", ""))

        # === 检查 1: 单日回撤 > 3% ===
        if len(daily_nav) >= 2:
            prev_nav = float(daily_nav[-2].get("nav", 1.0))
            if prev_nav > 0:
                daily_dd = (prev_nav - latest_nav) / prev_nav
                if daily_dd > self.daily_dd_threshold:
                    self._trigger(
                        reason="daily_drawdown_exceeded",
                        date=latest_date,
                        details={
                            "daily_drawdown": round(daily_dd, 4),
                            "threshold": self.daily_dd_threshold,
                            "prev_nav": prev_nav,
                            "latest_nav": latest_nav,
                            "prev_date": daily_nav[-2].get("date", ""),
                        },
                    )
                    return self._triggered_result()

        # === 检查 2: 3日累计回撤 > 5% ===
        if len(daily_nav) >= 4:
            nav_3d_ago = float(daily_nav[-4].get("nav", 1.0))
            if nav_3d_ago > 0:
                cumulative_3d_dd = (nav_3d_ago - latest_nav) / nav_3d_ago
                if cumulative_3d_dd > self.cumulative_3d_threshold:
                    self._trigger(
                        reason="cumulative_3d_drawdown_exceeded",
                        date=latest_date,
                        details={
                            "cumulative_3d_drawdown": round(cumulative_3d_dd, 4),
                            "threshold": self.cumulative_3d_threshold,
                            "nav_3d_ago": nav_3d_ago,
                            "latest_nav": latest_nav,
                            "start_date": daily_nav[-4].get("date", ""),
                        },
                    )
                    return self._triggered_result()

        # 未触发: 记录检查历史
        self._history.append(
            {
                "date": latest_date,
                "nav": latest_nav,
                "status": "ok",
            }
        )
        return {"triggered": False, "reason": "", "details": {}}

    def _trigger(self, reason: str, date: str, details: Dict[str, Any]):
        """触发 fail-fast (latch, 不可重置)"""
        self.triggered = True
        self.trigger_reason = reason
        self.trigger_date = date
        self.trigger_details = details
        logger.critical(
            "[FailFast] 影子账户 fail-fast 触发! reason=%s date=%s details=%s",
            reason,
            date,
            details,
        )

    def _triggered_result(self) -> Dict[str, Any]:
        return {
            "triggered": True,
            "reason": self.trigger_reason,
            "date": self.trigger_date,
            "details": self.trigger_details,
        }

    def get_status(self) -> Dict[str, Any]:
        """获取 fail-fast 监控状态"""
        return {
            "triggered": self.triggered,
            "reason": self.trigger_reason,
            "date": self.trigger_date,
            "details": self.trigger_details,
            "daily_dd_threshold": self.daily_dd_threshold,
            "cumulative_3d_threshold": self.cumulative_3d_threshold,
            "checks_performed": len(self._history),
        }


class ShadowAccount:
    """影子账户"""

    def __init__(self, account_id: str, strategy_id: str, initial_capital: float = 1_000_000):
        self.account_id = account_id
        self.strategy_id = strategy_id
        self.initial_capital = initial_capital
        self.current_capital = initial_capital

        # 交易日志
        self.trade_log: List[Dict[str, Any]] = []
        self.daily_nav: List[Dict[str, Any]] = []

        # 启动时间
        self.start_time = datetime.now()
        self.status = ShadowStatus.RUNNING

        # P0-11(shadow): fail-fast 监控器 (单日>3% / 3日>5% 立即终止)
        self.fail_fast_monitor = FailFastMonitor(
            daily_drawdown_threshold=0.03,
            cumulative_3d_drawdown_threshold=0.05,
        )
        self.fail_fast_log: List[Dict[str, Any]] = []

    def record_trade(self, trade: Dict[str, Any]):
        """记录交易"""
        self.trade_log.append({**trade, "shadow_account": self.account_id, "recorded_at": datetime.now().isoformat()})

        # 更新资金
        if trade.get("side") == "buy":
            self.current_capital -= trade.get("amount", 0)
        elif trade.get("side") == "sell":
            self.current_capital += trade.get("amount", 0)

    def record_daily_nav(self, date: str, nav: float):
        """记录每日净值 (P0-11(shadow): 集成 fail-fast 检查)

        每次记录净值后自动检查 fail-fast 触发条件:
          - 单日回撤 > 3% → 立即终止
          - 3日累计回撤 > 5% → 立即终止
        触发后设置 status=TERMINATED, 后续 record_* 调用将被拒绝。
        """
        # 已终止的影子账户拒绝记录新数据
        if self.status == ShadowStatus.TERMINATED:
            logger.warning(
                "[ShadowAccount %s] 已被 fail-fast 终止, 拒绝记录净值 (原因: %s)",
                self.account_id,
                self.fail_fast_monitor.trigger_reason,
            )
            return

        self.daily_nav.append(
            {
                "date": date,
                "nav": nav,
                "capital": nav * self.initial_capital / self.daily_nav[-1]["nav"] if self.daily_nav else nav,
                "recorded_at": datetime.now().isoformat(),
            }
        )

        # P0-11(shadow): 检查 fail-fast 触发条件
        ff_result = self.fail_fast_monitor.check(self.daily_nav)
        if ff_result.get("triggered", False):
            self._terminate(ff_result)

    def _terminate(self, fail_fast_result: Dict[str, Any]):
        """终止影子账户 (fail-fast 触发)"""
        self.status = ShadowStatus.TERMINATED
        termination_record = {
            "terminated_at": datetime.now().isoformat(),
            "reason": fail_fast_result.get("reason", "unknown"),
            "trigger_date": fail_fast_result.get("date", ""),
            "details": fail_fast_result.get("details", {}),
            "final_nav": self.daily_nav[-1].get("nav", 0) if self.daily_nav else 0,
            "total_trades": len(self.trade_log),
            "days_tracked": len(self.daily_nav),
        }
        self.fail_fast_log.append(termination_record)
        logger.critical(
            "[ShadowAccount %s] fail-fast 终止! reason=%s, final_nav=%.4f, days_tracked=%d",
            self.account_id,
            termination_record["reason"],
            termination_record["final_nav"],
            termination_record["days_tracked"],
        )

    def get_performance(self) -> Dict[str, Any]:
        """获取绩效数据"""
        if not self.daily_nav:
            return {}

        start_nav = self.daily_nav[0]["nav"]
        end_nav = self.daily_nav[-1]["nav"]
        days = (
            datetime.strptime(self.daily_nav[-1]["date"], "%Y-%m-%d")
            - datetime.strptime(self.daily_nav[0]["date"], "%Y-%m-%d")
        ).days

        # 计算CAGR
        if days > 0:
            cagr = (end_nav / start_nav) ** (365 / days) - 1
        else:
            cagr = 0

        # 计算夏普比率（简化）
        returns = [
            (nav["nav"] - self.daily_nav[i - 1]["nav"]) / self.daily_nav[i - 1]["nav"]
            for i, nav in enumerate(self.daily_nav[1:], 1)
        ]

        if returns:
            avg_return = sum(returns) / len(returns)
            std_return = (sum((r - avg_return) ** 2 for r in returns) / len(returns)) ** 0.5
            sharpe = avg_return / std_return if std_return > 0 else 0
        else:
            sharpe = 0

        # 计算最大回撤
        peak = self.daily_nav[0]["nav"]
        max_dd = 0

        for nav_data in self.daily_nav:
            peak = max(peak, nav_data["nav"])
            dd = (peak - nav_data["nav"]) / peak
            max_dd = max(max_dd, dd)

        return {
            "cagr": cagr,
            "sharpe": sharpe,
            "max_dd": max_dd,
            "days": days,
            "trades": len(self.trade_log),
            "current_nav": end_nav,
        }


class GrayReleaseManager:
    """灰度发布管理器"""

    STAGES = [
        {"name": "stage_1", "capital_pct": 0.1, "duration_days": 3, "threshold": "10%资金"},
        {"name": "stage_2", "capital_pct": 0.5, "duration_days": 7, "threshold": "50%资金"},
        {"name": "stage_3", "capital_pct": 1.0, "duration_days": 0, "threshold": "全量上线"},
    ]

    def __init__(self, strategy_id: str, total_capital: float = 10_000_000):
        self.strategy_id = strategy_id
        self.total_capital = total_capital
        self.current_stage = 0
        self.release_capital = 0
        self.start_time = None
        self.shadow_accounts: List[ShadowAccount] = []

        # 回测基准
        self.backtest_benchmark: Optional[Dict[str, Any]] = None

    def set_backtest_benchmark(self, benchmark: Dict[str, Any]):
        """设置回测基准"""
        self.backtest_benchmark = benchmark
        logger.info(f"[GRAY-RELEASE] Backtest benchmark set for {self.strategy_id}")

    def create_shadow_accounts(self, num_accounts: int = 3):
        """创建影子账户"""
        for i in range(num_accounts):
            account = ShadowAccount(
                account_id=f"shadow_{self.strategy_id}_{i}", strategy_id=self.strategy_id, initial_capital=1_000_000
            )
            self.shadow_accounts.append(account)
            logger.info(f"[GRAY-RELEASE] Created shadow account: {account.account_id}")

    def evaluate_shadow_performance(self) -> Tuple[bool, str]:
        """
        评估影子账户表现 (P0-11(shadow): 集成 fail-fast 检查)

        Returns:
            (是否通过, 评估理由)
        """
        if not self.backtest_benchmark:
            return False, "No backtest benchmark set"

        if len(self.shadow_accounts) == 0:
            return False, "No shadow accounts running"

        # P0-11(shadow): 优先检查 fail-fast 触发状态
        # 任一影子账户被 fail-fast 终止 → 立即评估失败 + 触发回滚
        for account in self.shadow_accounts:
            if account.status == ShadowStatus.TERMINATED:
                ff_status = account.fail_fast_monitor.get_status()
                reason = ff_status.get("reason", "unknown")
                details = ff_status.get("details", {})
                logger.critical(
                    "[GRAY-RELEASE] 影子账户 %s 已被 fail-fast 终止! reason=%s, 立即回滚",
                    account.account_id,
                    reason,
                )
                return False, (
                    f"FAIL_FAST_TERMINATED: {account.account_id} reason={reason} details={details} — 立即回滚"
                )

        # 检查运行时间是否>=2周
        min_days = 14
        for account in self.shadow_accounts:
            days_running = (datetime.now() - account.start_time).days
            if days_running < min_days:
                return (
                    False,
                    f"Shadow account {account.account_id} running for only {days_running} days (min {min_days})",
                )

        # 计算平均影子账户绩效
        avg_shadow_cagr = 0
        avg_shadow_sharpe = 0
        avg_shadow_dd = 0

        for account in self.shadow_accounts:
            perf = account.get_performance()
            if perf:
                avg_shadow_cagr += perf.get("cagr", 0)
                avg_shadow_sharpe += perf.get("sharpe", 0)
                avg_shadow_dd += perf.get("max_dd", 0)

        n = len(self.shadow_accounts)
        avg_shadow_cagr /= n
        avg_shadow_sharpe /= n
        avg_shadow_dd /= n

        # 计算偏差
        backtest_cagr = self.backtest_benchmark.get("cagr", 0)
        deviation = abs(avg_shadow_cagr - backtest_cagr) / abs(backtest_cagr) * 100 if backtest_cagr != 0 else 0

        # 判断标准
        if deviation > 30:
            return False, f"Deviation {deviation:.1f}% > 30% threshold"

        if avg_shadow_sharpe < 0.5:
            return False, f"Shadow Sharpe {avg_shadow_sharpe:.2f} < 0.5"

        if avg_shadow_dd > backtest_cagr * 2:
            return False, f"Shadow DD {avg_shadow_dd:.2%} > 2x backtest CAGR"

        return True, f"All metrics within tolerance (deviation: {deviation:.1f}%)"

    def advance_stage(self) -> bool:
        """推进到下一阶段"""
        if self.current_stage >= len(self.STAGES) - 1:
            logger.info(f"[GRAY-RELEASE] Strategy {self.strategy_id} reached final stage")
            return True

        current_stage = self.STAGES[self.current_stage]

        # 检查当前阶段持续时间
        if self.start_time:
            elapsed = (datetime.now() - self.start_time).days
            if elapsed < current_stage["duration_days"]:
                logger.info(
                    f"[GRAY-RELEASE] Stage {current_stage['name']} not yet complete: "
                    f"{elapsed}/{current_stage['duration_days']} days"
                )
                return False

        # 评估影子账户
        passed, reason = self.evaluate_shadow_performance()

        if not passed:
            logger.warning(f"[GRAY-RELEASE] Shadow evaluation FAILED: {reason}")
            self._rollback()
            return False

        # 推进阶段
        self.current_stage += 1
        self.release_capital = self.total_capital * self.STAGES[self.current_stage]["capital_pct"]
        self.start_time = datetime.now()

        logger.info(
            f"[GRAY-RELEASE] Advanced to {self.STAGES[self.current_stage]['name']}: "
            f"{self.STAGES[self.current_stage]['threshold']}"
        )

        return True

    def _rollback(self):
        """执行回滚"""
        logger.warning(f"[GRAY-RELEASE] ROLLBACK triggered for {self.strategy_id}")
        # TODO: 实现实际的回滚逻辑
        # - 停止策略执行
        # - 平仓
        # - 恢复至上一个稳定版本
        self.current_stage = max(0, self.current_stage - 1)

    def get_status(self) -> Dict[str, Any]:
        """获取灰度发布状态"""
        return {
            "strategy_id": self.strategy_id,
            "current_stage": self.current_stage,
            "stage_name": self.STAGES[self.current_stage]["name"] if self.STAGES[self.current_stage] else None,
            "capital_allocated": self.release_capital,
            "shadow_accounts": len(self.shadow_accounts),
            "shadow_metrics": [account.get_performance() for account in self.shadow_accounts],
        }


class StrategyReleaseManager:
    """策略发布管理器（主入口）"""

    def __init__(self, base_dir: str = "./strategy_releases"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.releases: Dict[str, Dict[str, Any]] = {}

    def register_strategy(self, strategy_id: str, backtest_results: Dict[str, Any]):
        """注册待发布的策略"""
        release_info = {
            "strategy_id": strategy_id,
            "backtest_results": backtest_results,
            "gray_release": GrayReleaseManager(strategy_id),
            "shadow_accounts_created": False,
            "registered_at": datetime.now().isoformat(),
            "release_history": [],
        }

        self.releases[strategy_id] = release_info

        # 保存注册信息
        reg_file = self.base_dir / f"{strategy_id}_registration.json"
        with open(reg_file, "w", encoding="utf-8") as f:
            json.dump(release_info, f, ensure_ascii=False, indent=2, default=str)

        logger.info(f"[STRATEGY-RELEASE] Registered strategy: {strategy_id}")

    def create_shadow_accounts(self, strategy_id: str, num_accounts: int = 3):
        """创建影子账户"""
        if strategy_id not in self.releases:
            raise ValueError(f"Strategy {strategy_id} not registered")

        release = self.releases[strategy_id]
        gray_mgr = release["gray_release"]

        # 设置回测基准
        gray_mgr.set_backtest_benchmark(release["backtest_results"])

        # 创建影子账户
        gray_mgr.create_shadow_accounts(num_accounts)
        release["shadow_accounts_created"] = True

        logger.info(f"[STRATEGY-RELEASE] Created {num_accounts} shadow accounts for {strategy_id}")

    def evaluate_and_advance(self, strategy_id: str) -> Dict[str, Any]:
        """评估并推进策略发布"""
        if strategy_id not in self.releases:
            return {"status": "error", "message": "Strategy not registered"}

        release = self.releases[strategy_id]
        gray_mgr = release["gray_release"]

        # 推进阶段
        advanced = gray_mgr.advance_stage()

        if advanced:
            # 记录发布历史
            release["release_history"].append(
                {"timestamp": datetime.now().isoformat(), "stage": gray_mgr.current_stage, "status": "advanced"}
            )

            status = gray_mgr.get_status()
            status["overall_status"] = "approved" if gray_mgr.current_stage == 2 else "in_progress"

            return status
        else:
            return {"status": "pending", "message": "Shadow evaluation not yet complete or failed"}

    def get_release_status(self, strategy_id: str) -> Dict[str, Any]:
        """获取策略发布状态"""
        if strategy_id not in self.releases:
            return {"status": "error", "message": "Strategy not registered"}

        return self.releases[strategy_id]["gray_release"].get_status()


# 使用示例：
# manager = StrategyReleaseManager()
# manager.register_strategy("momentum_v1", {"cagr": 0.25, "sharpe": 1.8, "max_dd": 0.08})
# manager.create_shadow_accounts("momentum_v1", num_accounts=3)
# result = manager.evaluate_and_advance("momentum_v1")
