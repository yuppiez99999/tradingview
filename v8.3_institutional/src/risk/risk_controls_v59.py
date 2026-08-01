"""
风险控制模块 — 对冲基金级交易风控 (Pre-Trade / Post-Trade)

世界顶级对冲基金的风险管理核心原则:
1. 永不违反仓位上限 — 即使信号再强
2. 组合层面 VaR / 最大回撤实时监控
3. 单日亏损熔断 (Daily Loss Limit)
4. 流动性检查 — 避免大单冲击成本
5. 相关性集中度监控 — 避免同向暴露过度

用法:
    risk_mgr = RiskControlManager(portfolio_config)

    # 交易前检查
    approved, reason = risk_mgr.pre_trade_check(code='688041.SH', action='BUY', amount=50000)

    # 组合级风险检查
    risk_report = risk_mgr.portfolio_risk_report(positions, prices)
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd

# [V75] from ..utils.alert_notifier import  # 需在v7.5创建alert_notifier AlertNotifier, AlertLevel, AlertChannel
# TODO(v8.5): 创建独立的 alert_notifier 模块, 替换下方 stub
try:
    from utils.stop_loss import AlertLevel  # type: ignore  # 已在 stop_loss.py 中定义
except ImportError:

    class AlertLevel(Enum):  # type: ignore  # stub: 与 utils.stop_loss.AlertLevel 保持一致
        """预警级别 (stub — 后续替换为独立 alert_notifier 模块)"""

        NORMAL = "normal"
        WARNING = "warning"
        CRITICAL = "critical"
        TRIGGERED = "triggered"


class AlertNotifier:
    """告警通知器 stub — 后续替换为独立 alert_notifier 模块

    TODO(v8.5): 实现真实的告警通道 (短信/邮件/飞书), 当前仅日志输出
    """

    def quick_alert(
        self, title: str = "", content: str = "", level: AlertLevel = AlertLevel.NORMAL, source: str = ""
    ) -> None:
        logger.info(f"[{source}] [{level.value if hasattr(level, 'value') else level}] {title}: {content}")


logger = logging.getLogger("risk_controls")


class OrderAction(Enum):
    BUY = "BUY"
    SELL = "SELL"


class RiskLevel(Enum):
    GREEN = "green"  # 正常交易
    YELLOW = "yellow"  # 需审批
    RED = "red"  # 禁止交易


@dataclass
class PreTradeResult:
    """交易前检查结果"""

    approved: bool
    risk_level: RiskLevel
    reason: str = ""
    position_after_pct: float = 0.0
    exposure_after_pct: float = 0.0


@dataclass
class PositionLimit:
    """持仓限制"""

    max_single_position_pct: float = 0.20  # 单标的最大仓位
    max_sector_exposure_pct: float = 0.40  # 单板块最大暴露
    max_total_exposure_pct: float = 0.90  # 最大总仓位
    min_cash_reserve_pct: float = 0.10  # 最低现金储备
    max_daily_turnover_pct: float = 0.30  # 单日最大换手率


@dataclass
class RiskLimits:
    """风险限额"""

    daily_loss_limit_pct: float = -0.03  # 单日亏损上限 (组合层面)
    weekly_loss_limit_pct: float = -0.05  # 单周亏损上限
    max_drawdown_pct: float = -0.15  # 最大回撤限制
    var_95_daily_limit_pct: float = 0.02  # 日 VaR 95% 上限
    position_limits: PositionLimit = field(default_factory=PositionLimit)
    kill_switch_activated: bool = False  # 紧急熔断 (手动触发)


class KillSwitch:
    """紧急熔断开关 — 全局组合级保护"""

    def __init__(self):
        self._activated = False
        self._activated_at: datetime | None = None
        self._reason = ""
        self._lock = threading.Lock()

    @property
    def activated(self) -> bool:
        return self._activated

    def activate(self, reason: str):
        with self._lock:
            if not self._activated:
                self._activated = True
                self._activated_at = datetime.now()
                self._reason = reason
                logger.critical(f"[KillSwitch] 紧急熔断触发! 原因: {reason}")

    def deactivate(self):
        with self._lock:
            self._activated = False
            self._reason = ""
            logger.warning("[KillSwitch] 紧急熔断已解除")

    def check(self) -> tuple[bool, str]:
        """检查熔断状态"""
        if self._activated:
            return True, f"熔断中 ({self._activated_at}): {self._reason}"
        return False, ""


class RiskControlManager:
    """风险控制管理器 — 交易前/后完整风控"""

    def __init__(self, total_capital: float, risk_limits: RiskLimits = None):
        """
        Args:
            total_capital: 组合总资金
            risk_limits: 风险限额配置
        """
        self.total_capital = max(total_capital, 1.0)
        self.risk_limits = risk_limits or RiskLimits()
        self.kill_switch = KillSwitch()
        self._alert_notifier = AlertNotifier()

        # ── 状态追踪 ──
        self._daily_pnl: float = 0.0
        self._cumulative_pnl: float = 0.0
        self._peak_equity: float = total_capital
        self._current_drawdown: float = 0.0
        self._today: date = date.today()
        self._lock = threading.Lock()

    # ── 交易前检查 ──

    def pre_trade_check(
        self,
        code: str,
        action: OrderAction,
        amount: float,
        current_positions: dict[str, float],
        current_prices: dict[str, float],
        sector_map: dict[str, str] | None = None,
    ) -> PreTradeResult:
        """
        交易前综合检查。

        Args:
            code: 标的代码
            action: BUY/SELL
            amount: 交易金额
            current_positions: {code: 持仓市值}
            current_prices: {code: 最新价格}
            sector_map: {code: 板块名}
        """
        with self._lock:
            # 1. 紧急熔断检查
            killed, reason = self.kill_switch.check()
            if killed:
                self._alert_notifier.quick_alert(
                    title="紧急熔断触发",
                    content=f"原因: {reason}",
                    level=AlertLevel.CRITICAL,
                    source="risk_controls",
                )
                return PreTradeResult(False, RiskLevel.RED, f"紧急熔断: {reason}")

            # 2. 单标的最大仓位检查
            current_value = current_positions.get(code, 0)
            new_value = current_value + amount if action == OrderAction.BUY else current_value - amount
            position_pct = new_value / self.total_capital

            if action == OrderAction.BUY:
                if position_pct > self.risk_limits.position_limits.max_single_position_pct:
                    self._alert_notifier.quick_alert(
                        title="单标仓位超限",
                        content=f"{code} 仓位 {position_pct:.1%} > {self.risk_limits.position_limits.max_single_position_pct:.1%}",
                        level=AlertLevel.CRITICAL,
                        source="risk_controls",
                    )
                    return PreTradeResult(
                        False,
                        RiskLevel.RED,
                        f"仓位超限: {position_pct:.1%} > {self.risk_limits.position_limits.max_single_position_pct:.1%}",
                        position_after_pct=position_pct,
                    )

            # 3. 总仓位检查
            total_exposure = sum(current_positions.values())
            if action == OrderAction.BUY:
                new_total = total_exposure + amount
            else:
                new_total = total_exposure - amount

            total_pct = new_total / self.total_capital
            if total_pct > self.risk_limits.position_limits.max_total_exposure_pct:
                self._alert_notifier.quick_alert(
                    title="总仓位超限",
                    content=f"总仓位 {total_pct:.1%} > {self.risk_limits.position_limits.max_total_exposure_pct:.1%}",
                    level=AlertLevel.CRITICAL,
                    source="risk_controls",
                )
                return PreTradeResult(
                    False,
                    RiskLevel.RED,
                    f"总仓位超限: {total_pct:.1%} > {self.risk_limits.position_limits.max_total_exposure_pct:.1%}",
                    position_after_pct=position_pct,
                    exposure_after_pct=total_pct,
                )

            # 4. 板块集中度检查
            if sector_map and code in sector_map and action == OrderAction.BUY:
                sector = sector_map[code]
                sector_exposure = sum(v for c, v in current_positions.items() if sector_map.get(c) == sector)
                new_sector_pct = (sector_exposure + amount) / self.total_capital
                if new_sector_pct > self.risk_limits.position_limits.max_sector_exposure_pct:
                    self._alert_notifier.quick_alert(
                        title="板块集中度超限",
                        content=f"板块 '{sector}' 集中度 {new_sector_pct:.1%} > {self.risk_limits.position_limits.max_sector_exposure_pct:.1%}",
                        level=AlertLevel.WARNING,
                        source="risk_controls",
                    )
                    return PreTradeResult(
                        False,
                        RiskLevel.YELLOW,
                        f"板块 '{sector}' 集中度偏高: {new_sector_pct:.1%} > {self.risk_limits.position_limits.max_sector_exposure_pct:.1%}",
                        position_after_pct=position_pct,
                        exposure_after_pct=total_pct,
                    )

            # 5. 现金储备检查
            if action == OrderAction.BUY:
                cash_after = self.total_capital - new_total
                cash_pct = cash_after / self.total_capital
                if cash_pct < self.risk_limits.position_limits.min_cash_reserve_pct:
                    self._alert_notifier.quick_alert(
                        title="现金储备不足",
                        content=f"现金储备 {cash_pct:.1%} < {self.risk_limits.position_limits.min_cash_reserve_pct:.1%}",
                        level=AlertLevel.WARNING,
                        source="risk_controls",
                    )
                    return PreTradeResult(
                        False,
                        RiskLevel.YELLOW,
                        f"现金储备不足: {cash_pct:.1%} < {self.risk_limits.position_limits.min_cash_reserve_pct:.1%}",
                        position_after_pct=position_pct,
                        exposure_after_pct=total_pct,
                    )

            # 6. 每日亏损限制检查
            if self._daily_pnl / self.total_capital <= self.risk_limits.daily_loss_limit_pct:
                self._alert_notifier.quick_alert(
                    title="日亏损已达上限",
                    content=f"日亏损 {self._daily_pnl / self.total_capital:.2%} 超过上限 {self.risk_limits.daily_loss_limit_pct:.2%}",
                    level=AlertLevel.CRITICAL,
                    source="risk_controls",
                )
                return PreTradeResult(
                    False,
                    RiskLevel.RED,
                    f"日亏损已达上限: {self._daily_pnl / self.total_capital:.2%}",
                    position_after_pct=position_pct,
                    exposure_after_pct=total_pct,
                )

            return PreTradeResult(
                True, RiskLevel.GREEN, "通过", position_after_pct=position_pct, exposure_after_pct=total_pct
            )

    # ── 组合风险报告 ──

    def portfolio_risk_report(
        self, positions: dict[str, float], prices: dict[str, float], sector_map: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """生成组合风险报告"""
        with self._lock:
            total_value = sum(positions.values())
            cash = self.total_capital - total_value

            report = {
                "total_capital": self.total_capital,
                "total_exposure": total_value,
                "exposure_pct": total_value / self.total_capital if self.total_capital > 0 else 0,
                "cash": cash,
                "cash_pct": cash / self.total_capital if self.total_capital > 0 else 0,
                "daily_pnl": self._daily_pnl,
                "daily_pnl_pct": self._daily_pnl / self.total_capital if self.total_capital > 0 else 0,
                "current_drawdown": self._current_drawdown,
                "kill_switch": self.kill_switch.activated,
            }

            # 板块集中度
            if sector_map:
                sector_breakdown: dict[str, float] = {}
                for code, value in positions.items():
                    sector = sector_map.get(code, "unknown")
                    sector_breakdown[sector] = sector_breakdown.get(sector, 0) + value
                report["sector_exposure"] = {
                    s: {"value": v, "pct": v / self.total_capital}
                    for s, v in sorted(sector_breakdown.items(), key=lambda x: -x[1])
                }

            # 风控违规
            violations = []
            pl = self.risk_limits.position_limits
            for code, value in positions.items():
                pct = value / self.total_capital
                if pct > pl.max_single_position_pct * 0.8:  # 80%预警
                    violations.append(f"{code} 仓位 {pct:.1%} 接近上限")
                    self._alert_notifier.quick_alert(
                        title="单标仓位接近上限",
                        content=f"{code} 仓位 {pct:.1%}，接近上限 {pl.max_single_position_pct:.1%}",
                        level=AlertLevel.WARNING,
                        source="risk_controls",
                    )
            report["violations"] = violations

            # 组合级风控告警
            if report["daily_pnl_pct"] <= self.risk_limits.daily_loss_limit_pct:
                self._alert_notifier.quick_alert(
                    title="组合日亏损告警",
                    content=f"组合日亏损 {report['daily_pnl_pct']:.2%}，请关注风险",
                    level=AlertLevel.CRITICAL,
                    source="risk_controls",
                )

            if self.kill_switch.activated:
                self._alert_notifier.quick_alert(
                    title="熔断器已激活",
                    content="组合触发紧急熔断，建议暂停新开仓",
                    level=AlertLevel.CRITICAL,
                    source="risk_controls",
                )

            return report

    # ── 日终/时段结算 ──

    def update_pnl(self, pnl_delta: float):
        """更新日盈亏"""
        with self._lock:
            # 新的一天重置
            if date.today() != self._today:
                self._daily_pnl = 0.0
                self._today = date.today()

            self._daily_pnl += pnl_delta

            # 更新最大回撤 (修复: 峰值权益追踪 + 回撤实时计算)
            current_equity = self.total_capital + self._cumulative_pnl
            if current_equity > self._peak_equity:
                self._peak_equity = current_equity
            if self._peak_equity > 0:
                self._current_drawdown = (current_equity - self._peak_equity) / self._peak_equity
            self._cumulative_pnl += pnl_delta

            # 日亏损熔断
            daily_pnl_pct = self._daily_pnl / self.total_capital
            if daily_pnl_pct <= self.risk_limits.daily_loss_limit_pct:
                self.kill_switch.activate(
                    f"日亏损{daily_pnl_pct:.2%} 超过上限{self.risk_limits.daily_loss_limit_pct:.2%}"
                )
                self._alert_notifier.quick_alert(
                    title="日亏损触发熔断",
                    content=f"日亏损 {daily_pnl_pct:.2%} 超过上限 {self.risk_limits.daily_loss_limit_pct:.2%}",
                    level=AlertLevel.CRITICAL,
                    source="risk_controls",
                )

    def daily_reset(self):
        """日终重置"""
        with self._lock:
            self._daily_pnl = 0.0
            self._today = date.today()


# ── 便捷函数 ──


def calculate_position_weights(positions: dict[str, float], total_capital: float) -> dict[str, float]:
    """计算持仓权重"""
    if total_capital <= 0:
        return {}
    return {code: value / total_capital for code, value in positions.items()}


def calculate_var_parametric(
    positions: dict[str, float],
    volatilities: dict[str, float],
    correlation_matrix: dict[tuple[str, str], float] | None = None,
    confidence: float = 0.95,
    horizon_days: int = 1,
) -> float:
    """
    参数法 VaR 估算（基于协方差矩阵，P0-5/P0-7 修复）.

    Args:
        positions: {code: 市值}
        volatilities: {code: 日波动率}
        correlation_matrix: {(code1, code2): 相关系数}
        confidence: 置信水平
        horizon_days: 持有期天数
    """
    if not positions or not volatilities:
        return 0.0

    z_score = 1.645  # 95% confidence
    total_value = sum(positions.values())
    if total_value <= 0:
        return 0.0

    codes = list(positions.keys())
    n = len(codes)

    # P0-5 修复：使用协方差矩阵计算组合 VaR
    if correlation_matrix and n > 1:
        # 构建协方差矩阵 Σ = D × ρ × D，其中 D 是波动率对角矩阵
        cov_matrix = np.zeros((n, n))
        for i, code_i in enumerate(codes):
            for j, code_j in enumerate(codes):
                if i == j:
                    cov_matrix[i, j] = volatilities.get(code_i, 0.02) ** 2
                else:
                    # 确保 correlation_matrix 的键顺序一致
                    key1 = (code_i, code_j)
                    key2 = (code_j, code_i)
                    corr = correlation_matrix.get(key1, correlation_matrix.get(key2, 0.0))
                    cov_matrix[i, j] = corr * volatilities.get(code_i, 0.02) * volatilities.get(code_j, 0.02)

        # 权重向量
        weights = np.array([positions[code] / total_value for code in codes])

        # 组合方差 = w^T × Σ × w
        portfolio_var = weights @ cov_matrix @ weights
    else:
        # 回退：假设独立（单资产或缺少相关性数据）
        portfolio_var = 0.0
        for code, value in positions.items():
            weight = value / total_value
            vol = volatilities.get(code, 0.02)
            portfolio_var += (weight * vol) ** 2

    daily_var = math.sqrt(portfolio_var) * z_score
    return daily_var * math.sqrt(horizon_days) * total_value


def calculate_marginal_risk_contribution(
    positions: dict[str, float],
    volatilities: dict[str, float],
    correlation_matrix: dict[tuple[str, str], float] | None = None,
) -> dict[str, float]:
    """
    计算各标的的边际风险贡献 (MRC, Marginal Risk Contribution).

    MRC_i = w_i × (Σ × w)_i / σ_p

    P0-6 修复：用于真正的风险平价配置
    """
    if not positions or not volatilities:
        return {}

    codes = list(positions.keys())
    n = len(codes)

    if n == 0:
        return {}

    # 计算总市值
    total_value = sum(positions.values())
    if total_value <= 0:
        return {}

    # 计算权重
    weights = np.array([positions[code] / total_value for code in codes])

    # 构建协方差矩阵
    if correlation_matrix and n > 1:
        cov_matrix = np.zeros((n, n))
        for i, code_i in enumerate(codes):
            for j, code_j in enumerate(codes):
                if i == j:
                    cov_matrix[i, j] = volatilities.get(code_i, 0.02) ** 2
                else:
                    key1 = (code_i, code_j)
                    key2 = (code_j, code_i)
                    corr = correlation_matrix.get(key1, correlation_matrix.get(key2, 0.0))
                    cov_matrix[i, j] = corr * volatilities.get(code_i, 0.02) * volatilities.get(code_j, 0.02)
    else:
        # 对角矩阵（独立假设）
        cov_matrix = np.diag([volatilities.get(code, 0.02) ** 2 for code in codes])

    # 计算协方差权重
    cov_weights = cov_matrix @ weights

    # 计算组合波动率
    if n == 1:
        # 单资产情况
        portfolio_vol = math.sqrt(cov_matrix[0, 0])
        mrc = {codes[0]: 1.0}  # 单资产的边际风险贡献为1
    else:
        # 多资产情况
        portfolio_vol = math.sqrt(weights @ cov_weights)
        if portfolio_vol <= 0:
            return {code: 0.0 for code in codes}

        # MRC_i = w_i × (Σ × w)_i / σ_p
        mrc = {}
        for i, code in enumerate(codes):
            mrc[code] = weights[i] * cov_weights[i] / portfolio_vol

    return mrc


def check_correlation_risk(
    positions: dict[str, float],
    returns_df: pd.DataFrame = None,
    lookback_days: int = 60,
    high_corr_threshold: float = 0.7,
    correlation_spike_threshold: float = 0.15,
) -> list[str]:
    """
    P0-7 修复：检查组合内相关性风险。

    Args:
        positions: 当前持仓 {code: 市值}
        returns_df: 收益率DataFrame，index为日期，columns为代码
        lookback_days: 滚动窗口天数
        high_corr_threshold: 高相关性阈值
        correlation_spike_threshold: 相关性突增阈值（当前vs历史）
    """
    alerts = []
    codes = [code for code in positions.keys() if code in returns_df.columns]

    if len(codes) < 2 or returns_df is None:
        return alerts

    # 取最近N天的收益率
    recent_returns = returns_df[codes].tail(lookback_days)

    # 计算滚动相关系数矩阵
    corr_matrix = recent_returns.corr()

    # 检查高相关性对
    for i, code_i in enumerate(codes):
        for j, code_j in enumerate(codes):
            if i >= j:
                continue
            corr = (
                corr_matrix.loc[code_i, code_j]
                if code_i in corr_matrix.columns and code_j in corr_matrix.columns
                else 0
            )
            if corr > high_corr_threshold:
                alerts.append(
                    f"[相关性告警] {code_i} ↔ {code_j}: 相关系数 {corr:.2f} > {high_corr_threshold} (集中度风险)"
                )

    # 检查相关性突增（当前窗口 vs 更长历史窗口）
    if len(returns_df) > lookback_days * 2:
        historical_returns = returns_df[codes].tail(lookback_days * 2).head(lookback_days)
        hist_corr = historical_returns[codes].corr()

        for i, code_i in enumerate(codes):
            for j, code_j in enumerate(codes):
                if i >= j:
                    continue
                current_corr = (
                    corr_matrix.loc[code_i, code_j]
                    if code_i in corr_matrix.columns and code_j in corr_matrix.columns
                    else 0
                )
                hist_c = (
                    hist_corr.loc[code_i, code_j] if code_i in hist_corr.columns and code_j in hist_corr.columns else 0
                )
                spike = current_corr - hist_c
                if spike > correlation_spike_threshold:
                    alerts.append(
                        f"[相关性突增] {code_i} ↔ {code_j}: "
                        f"近期 {current_corr:.2f} vs 历史 {hist_c:.2f} (Δ={spike:.2f})"
                    )

    return alerts


def check_concentration_risk(
    positions: dict[str, float], sector_map: dict[str, str], max_sector_pct: float = 0.40, total_capital: float = 1.0
) -> list[str]:
    """检查行业集中度风险"""
    alerts = []
    sector_values: dict[str, float] = {}

    for code, value in positions.items():
        sector = sector_map.get(code, "unknown")
        sector_values[sector] = sector_values.get(sector, 0) + value

    for sector, value in sector_values.items():
        pct = value / total_capital if total_capital > 0 else 0
        if pct > max_sector_pct:
            alerts.append(f"板块 '{sector}' 集中度 {pct:.1%} 超限")

    return alerts
