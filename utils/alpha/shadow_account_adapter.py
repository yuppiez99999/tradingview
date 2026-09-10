"""Shadow 账户适配器 — 模块整合 8.4 (T2.4 接入真实数据).

任务: T2.4 (启动 Shadow 准入流程, P0 阻塞 Phase 3)
硬约束:
    - HC-3: risk_managed=True (单因子 Config_E / 组合 Config_E_plus1)
    - HC-4: 14 天观察期阻塞 Stage 2 推进
    - HC-5: 配置走 ConfigManager 4 级优先级

功能:
    1. 包装现有 ShadowAccount (v8.3_institutional/src/validation/shadow_account_system.py)
    2. 提供 run_shadow(daily_returns) 接口注入真实收益率序列
    3. 计算 DSR (Deflated Sharpe Ratio, Bailey 2014) — 复用 deflated_sharpe.py
    4. 计算 Sharpe CV (12 月滚动变异系数)
    5. 提供 get_metrics() 返回 {dsr, annual_return, max_drawdown, sharpe_cv} 完整指标
    6. 集成 FailFastMonitor (单日>3% / 3日>5% 立即终止)

设计原则 (Adapter 模式):
    - 不修改原 ShadowAccount 代码, 仅做外观包装
    - 支持"重放模式": 从历史 daily_returns 序列重放, 每日记录净值
    - Feature Flag 透传: USE_SHADOW_ACCOUNT_ADAPTER 默认 False

用法:
    adapter = ShadowAccountAdapter(
        account_id="shadow_T2.4",
        strategy_id="LLMRouter+DecisionTheories+MultiFactor",
        initial_capital=1_000_000,
    )
    # 注入真实收益率序列 (14 天观察期)
    adapter.run_shadow(daily_returns=[0.01, -0.005, 0.008, ...])
    metrics = adapter.get_metrics()
    # metrics = {"dsr": 0.96, "annual_return": 0.18, "max_drawdown": 0.06, "sharpe_cv": 0.85}
"""

from __future__ import annotations

import logging
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

from utils.datetime_utils import now_bj

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# V8.3 validation 模块路径
_V83_VALIDATION_DIR = _PROJECT_ROOT / "v8.3_institutional" / "src" / "validation"
if str(_V83_VALIDATION_DIR) not in sys.path:
    sys.path.insert(0, str(_V83_VALIDATION_DIR))

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================

DEFAULT_ACCOUNT_ID = "shadow_T2.4"
DEFAULT_STRATEGY_ID = "T2.4_modules_admission"
DEFAULT_INITIAL_CAPITAL = 1_000_000.0
DEFAULT_RISK_FREE_RATE = 0.03  # 年化无风险利率
DEFAULT_N_TRIALS = 100  # DSR 校正试验次数
DEFAULT_REQUIRED_DSR = 0.95  # DSR 阈值
DEFAULT_SHARPE_CV_WINDOW = 252  # 12 月滚动窗口 (252 交易日)
TRADING_DAYS_PER_YEAR = 252

# Fail-Fast 触发阈值 (用户硬约束)
DAILY_DRAWDOWN_THRESHOLD = 0.03  # 单日回撤 > 3%
CUMULATIVE_3D_DRAWDOWN_THRESHOLD = 0.05  # 3 日累计回撤 > 5%

# 最小样本数: 从 shadow_admission.yaml 单事实源读取 (cairn/observation-period-config-drift-20260809.md §4 教训 2)
# yaml 路径: v8.3_institutional/config/shadow_admission.yaml → admission_criteria.min_samples_for_dsr


def _load_min_samples_for_dsr(default: int = 20) -> int:
    """从 yaml 读取 min_samples_for_dsr, 失败安全降级到 default.

    yaml 实际值=20 (注释: "保留 20 更严格"); 历史 PM 决策 20→15 已被 yaml 推翻为 20.
    """
    try:
        import yaml  # noqa: PLC0415

        yaml_path = (
            _PROJECT_ROOT / "v8.3_institutional" / "config" / "shadow_admission.yaml"
        )
        with open(yaml_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return int(
            cfg.get("admission_criteria", {}).get("min_samples_for_dsr", default)
        )
    except (OSError, ValueError, TypeError, ImportError):
        return default


MIN_SAMPLES_FOR_DSR = _load_min_samples_for_dsr(default=20)
MIN_SAMPLES_FOR_SHARPE_CV = 10


# ============================================================
# 异常体系
# ============================================================


class ShadowAccountAdapterError(Exception):
    """Shadow 账户适配器基础异常."""


class InsufficientReturnsError(ShadowAccountAdapterError):
    """收益率样本不足."""


class FailFastTriggeredError(ShadowAccountAdapterError):
    """Fail-Fast 已触发, 适配器已终止."""


# ============================================================
# 数据类
# ============================================================


@dataclass
class ShadowMetrics:
    """Shadow 账户完整指标 (对齐 T2.4 验收标准)."""

    # 核心指标 (对齐 V9 评估标准)
    dsr: float  # Deflated Sharpe Ratio (Bailey 2014)
    annual_return: float  # 年化收益率
    max_drawdown: float  # 最大回撤
    sharpe_cv: float  # Sharpe CV (12 月滚动变异系数)

    # 辅助诊断
    sharpe_ratio: float  # 年化夏普比率
    total_return: float  # 累计收益率
    days_tracked: int  # 跟踪天数
    total_trades: int  # 总交易次数
    final_nav: float  # 最终净值
    fail_fast_triggered: bool  # Fail-Fast 是否触发
    fail_fast_reason: str | None  # 触发原因
    samples_for_dsr: int  # DSR 计算使用的样本数
    samples_for_sharpe_cv: int  # Sharpe CV 计算使用的样本数
    is_real_data: bool  # 是否为真实数据 (非占位)


@dataclass
class RunShadowResult:
    """run_shadow() 执行结果."""

    success: bool  # 是否成功完成
    days_processed: int  # 处理的天数
    final_nav: float  # 最终净值
    fail_fast_triggered: bool  # 是否触发 fail-fast
    fail_fast_reason: str | None  # 触发原因
    termination_date: str | None  # 终止日期 (若触发)
    error: str | None  # 错误信息 (若失败)


# ============================================================
# 适配器主类
# ============================================================


class ShadowAccountAdapter:
    """Shadow 账户适配器 — 包装 ShadowAccount + DSR/Sharpe CV 计算.

    设计原则 (Adapter 模式):
        - 不修改原 ShadowAccount 代码
        - 提供 run_shadow() / get_metrics() / compute_dsr() / compute_sharpe_cv() 接口
        - 支持重放模式: 从历史 daily_returns 序列重放

    HC 合规:
        - HC-3: risk_managed=True (FailFastMonitor 集成)
        - HC-4: 14 天观察期阻塞 (由 launcher 检查, 适配器仅提供数据)
        - HC-5: 配置走 ConfigManager (由 launcher 加载, 适配器接收参数)
    """

    def __init__(
        self,
        account_id: str = DEFAULT_ACCOUNT_ID,
        strategy_id: str = DEFAULT_STRATEGY_ID,
        initial_capital: float = DEFAULT_INITIAL_CAPITAL,
        risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
        n_trials: int = DEFAULT_N_TRIALS,
        required_dsr: float = DEFAULT_REQUIRED_DSR,
        sharpe_cv_window: int = DEFAULT_SHARPE_CV_WINDOW,
        daily_dd_threshold: float = DAILY_DRAWDOWN_THRESHOLD,
        cumulative_3d_threshold: float = CUMULATIVE_3D_DRAWDOWN_THRESHOLD,
    ) -> None:
        """初始化 Shadow 账户适配器.

        Args:
            account_id: 账户 ID
            strategy_id: 策略 ID
            initial_capital: 初始资金 (元)
            risk_free_rate: 年化无风险利率
            n_trials: DSR 校正试验次数
            required_dsr: DSR 阈值
            sharpe_cv_window: Sharpe CV 滚动窗口 (默认 252 交易日)
            daily_dd_threshold: 单日回撤阈值 (默认 3%)
            cumulative_3d_threshold: 3 日累计回撤阈值 (默认 5%)
        """
        if initial_capital <= 0:
            raise ValueError(f"initial_capital 必须 > 0, 实际 {initial_capital}")
        if not (0 <= risk_free_rate <= 1):
            raise ValueError(f"risk_free_rate 必须在 [0,1], 实际 {risk_free_rate}")
        if n_trials <= 0:
            raise ValueError(f"n_trials 必须 > 0, 实际 {n_trials}")
        if not (0 <= required_dsr <= 1):
            raise ValueError(f"required_dsr 必须在 [0,1], 实际 {required_dsr}")
        if sharpe_cv_window < MIN_SAMPLES_FOR_SHARPE_CV:
            raise ValueError(
                f"sharpe_cv_window 必须 >= {MIN_SAMPLES_FOR_SHARPE_CV}, 实际 {sharpe_cv_window}"
            )

        # 延迟导入 ShadowAccount (避免 import 时报错)
        self._shadow_account = self._create_shadow_account(
            account_id=account_id,
            strategy_id=strategy_id,
            initial_capital=initial_capital,
            daily_dd_threshold=daily_dd_threshold,
            cumulative_3d_threshold=cumulative_3d_threshold,
        )

        self._risk_free_rate = float(risk_free_rate)
        self._n_trials = int(n_trials)
        self._required_dsr = float(required_dsr)
        self._sharpe_cv_window = int(sharpe_cv_window)

        # 收益率序列缓存 (run_shadow 时填充)
        self._daily_returns: list[float] = []
        self._dates: list[str] = []
        self._is_real_data: bool = False

    # ------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------

    def run_shadow(
        self,
        daily_returns: Sequence[float],
        dates: Sequence[str] | None = None,
        is_real_data: bool = True,
    ) -> RunShadowResult:
        """注入每日收益率序列, 模拟运行 Shadow 账户.

        Args:
            daily_returns: 每日收益率序列 (如 [0.01, -0.005, 0.008, ...])
            dates: 对应日期列表 (如 ["2026-07-27", "2026-07-28", ...]); None 则自动生成
            is_real_data: 是否为真实数据 (False 表示占位/模拟数据)

        Returns:
            RunShadowResult 执行结果

        Raises:
            ValueError: 输入参数无效
        """
        if not daily_returns:
            raise ValueError("daily_returns 不能为空")

        # 检查是否已触发 fail-fast
        if self._shadow_account.status.value == "terminated":
            ff_status = self._shadow_account.fail_fast_monitor.get_status()
            raise FailFastTriggeredError(
                f"Fail-Fast 已触发 (reason={ff_status.get('reason')}), 适配器已终止, 不可继续记录"
            )

        # 生成日期列表 (若未提供)
        if dates is None:
            start_date = now_bj() - timedelta(days=len(daily_returns))
            dates = [
                (start_date + timedelta(days=i)).strftime("%Y-%m-%d")
                for i in range(len(daily_returns))
            ]
        if len(dates) != len(daily_returns):
            raise ValueError(
                f"dates 长度 {len(dates)} != daily_returns 长度 {len(daily_returns)}"
            )

        # 重放每日收益率, 记录净值
        nav = 1.0  # 初始净值
        days_processed = 0
        for i, ret in enumerate(daily_returns):
            # 检查是否已被 fail-fast 终止
            if self._shadow_account.status.value == "terminated":
                ff_status = self._shadow_account.fail_fast_monitor.get_status()
                return RunShadowResult(
                    success=False,
                    days_processed=days_processed,
                    final_nav=nav,
                    fail_fast_triggered=True,
                    fail_fast_reason=ff_status.get("reason"),
                    termination_date=dates[i - 1] if i > 0 else None,
                    error=f"Fail-Fast 触发于第 {i} 天 (date={dates[i - 1] if i > 0 else 'N/A'})",
                )

            # 更新净值
            nav = nav * (1.0 + float(ret))

            # 记录每日净值 (ShadowAccount 内部会检查 fail-fast)
            self._shadow_account.record_daily_nav(date=dates[i], nav=nav)

            # 缓存收益率序列
            self._daily_returns.append(float(ret))
            self._dates.append(dates[i])

            days_processed += 1

        self._is_real_data = bool(is_real_data)

        # 检查最终是否被 fail-fast 终止
        ff_triggered = self._shadow_account.status.value == "terminated"
        ff_reason: str | None = None
        termination_date: str | None = None
        if ff_triggered:
            ff_status = self._shadow_account.fail_fast_monitor.get_status()
            ff_reason = ff_status.get("reason")
            termination_date = ff_status.get("date")

        return RunShadowResult(
            success=True,
            days_processed=days_processed,
            final_nav=nav,
            fail_fast_triggered=ff_triggered,
            fail_fast_reason=ff_reason,
            termination_date=termination_date,
            error=None,
        )

    def get_metrics(self) -> ShadowMetrics:
        """获取完整指标 (DSR / 年化 / 回撤 / Sharpe CV).

        Returns:
            ShadowMetrics 完整指标

        Raises:
            InsufficientReturnsError: 收益率样本不足
            FailFastTriggeredError: Fail-Fast 已触发 (但仍可获取指标)
        """
        if len(self._daily_returns) < MIN_SAMPLES_FOR_DSR:
            raise InsufficientReturnsError(
                f"收益率样本不足: {len(self._daily_returns)} < {MIN_SAMPLES_FOR_DSR}"
            )

        # 计算 DSR
        dsr_result = self.compute_dsr()

        # 计算年化收益率
        annual_return = self._compute_annual_return()

        # 计算最大回撤
        max_dd = self._compute_max_drawdown()

        # 计算 Sharpe CV
        sharpe_ratio, sharpe_cv = self.compute_sharpe_cv()

        # 获取 ShadowAccount 基础绩效
        perf = self._shadow_account.get_performance() or {}

        # Fail-Fast 状态
        ff_status = self._shadow_account.fail_fast_monitor.get_status()

        return ShadowMetrics(
            dsr=dsr_result.deflated_sharpe_ratio,
            annual_return=annual_return,
            max_drawdown=max_dd,
            sharpe_cv=sharpe_cv,
            sharpe_ratio=sharpe_ratio,
            total_return=perf.get("cagr", 0.0) if perf else 0.0,
            days_tracked=len(self._daily_returns),
            total_trades=len(self._shadow_account.trade_log),
            final_nav=perf.get("current_nav", 1.0) if perf else 1.0,
            fail_fast_triggered=ff_status.get("triggered", False),
            fail_fast_reason=ff_status.get("reason"),
            samples_for_dsr=len(self._daily_returns),
            samples_for_sharpe_cv=min(len(self._daily_returns), self._sharpe_cv_window),
            is_real_data=self._is_real_data,
        )

    def compute_dsr(self):
        """计算 Deflated Sharpe Ratio (Bailey 2014).

        复用 v8.3_institutional/src/validation/deflated_sharpe.py 的实现.

        Returns:
            DeflatedSharpeResult

        Raises:
            InsufficientReturnsError: 样本不足
        """
        if len(self._daily_returns) < MIN_SAMPLES_FOR_DSR:
            raise InsufficientReturnsError(
                f"DSR 计算需要至少 {MIN_SAMPLES_FOR_DSR} 个样本, 实际 {len(self._daily_returns)}"
            )

        from deflated_sharpe import deflated_sharpe_ratio

        return cast(
            float,
            deflated_sharpe_ratio(
                daily_returns=list(self._daily_returns),
                n_trials=self._n_trials,
                required_dsr=self._required_dsr,
                risk_free_rate=self._risk_free_rate,
            ),
        )

    def compute_sharpe_cv(self) -> tuple[float, float]:
        """计算 Sharpe CV (12 月滚动变异系数).

        Sharpe CV = std(rolling_sharpe) / |mean(rolling_sharpe)|

        - 滚动窗口: 252 交易日 (12 月)
        - 若样本数 < 窗口: 用现有数据计算单一 Sharpe, CV=0
        - 若样本数 < MIN_SAMPLES_FOR_SHARPE_CV: 抛 InsufficientReturnsError

        Returns:
            (sharpe_ratio, sharpe_cv)

        Raises:
            InsufficientReturnsError: 样本不足
        """
        n = len(self._daily_returns)
        if n < MIN_SAMPLES_FOR_SHARPE_CV:
            raise InsufficientReturnsError(
                f"Sharpe CV 计算需要至少 {MIN_SAMPLES_FOR_SHARPE_CV} 个样本, 实际 {n}"
            )

        # 样本数 < 窗口: 计算单一 Sharpe, CV=0
        if n < self._sharpe_cv_window:
            sharpe = self._compute_sharpe_single(self._daily_returns)
            return sharpe, 0.0

        # 计算滚动 Sharpe
        rolling_sharpes: list[float] = []
        for i in range(self._sharpe_cv_window, n + 1):
            window = self._daily_returns[i - self._sharpe_cv_window : i]
            sharpe = self._compute_sharpe_single(window)
            rolling_sharpes.append(sharpe)

        if not rolling_sharpes:
            sharpe = self._compute_sharpe_single(self._daily_returns)
            return sharpe, 0.0

        # 计算变异系数
        mean_sharpe = sum(rolling_sharpes) / len(rolling_sharpes)
        var_sharpe = sum((s - mean_sharpe) ** 2 for s in rolling_sharpes) / len(
            rolling_sharpes
        )
        std_sharpe = math.sqrt(var_sharpe)
        sharpe_cv = (
            std_sharpe / abs(mean_sharpe) if abs(mean_sharpe) > 1e-10 else float("inf")
        )

        return mean_sharpe, sharpe_cv

    # ------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------

    def _create_shadow_account(
        self,
        account_id: str,
        strategy_id: str,
        initial_capital: float,
        daily_dd_threshold: float,
        cumulative_3d_threshold: float,
    ):
        """创建底层 ShadowAccount 实例.

        延迟导入避免 import 时报错, 同时允许自定义 FailFastMonitor 阈值.
        """
        from shadow_account_system import (
            FailFastMonitor,
            ShadowAccount,
        )

        account = ShadowAccount(
            account_id=account_id,
            strategy_id=strategy_id,
            initial_capital=initial_capital,
        )

        # 覆盖默认 FailFastMonitor (使用用户硬约束阈值)
        account.fail_fast_monitor = FailFastMonitor(
            daily_drawdown_threshold=daily_dd_threshold,
            cumulative_3d_drawdown_threshold=cumulative_3d_threshold,
        )

        return account

    def _compute_annual_return(self) -> float:
        """计算年化收益率 (复利).

        annual_return = (1 + total_return) ** (252 / n) - 1
        """
        n = len(self._daily_returns)
        if n == 0:
            return 0.0

        # 累计收益率
        cumulative = 1.0
        for r in self._daily_returns:
            cumulative *= 1.0 + r
        total_return = cumulative - 1.0

        # 年化 (复利)
        if n < TRADING_DAYS_PER_YEAR:
            annual_factor = TRADING_DAYS_PER_YEAR / n
            # P1-12: total_return <= -1.0 时 (1+total_return) <= 0, 幂运算无效
            base = max(1.0 + total_return, 1e-9)
            annual_return = float(base**annual_factor - 1.0)
        else:
            base = max(1.0 + total_return, 1e-9)
            annual_return = float(base ** (TRADING_DAYS_PER_YEAR / n) - 1.0)

        return annual_return

    def _compute_max_drawdown(self) -> float:
        """计算最大回撤."""
        if not self._daily_returns:
            return 0.0

        # 构建净值序列
        nav = 1.0
        peak = 1.0
        max_dd = 0.0

        for r in self._daily_returns:
            nav *= 1.0 + r
            peak = max(peak, nav)
            dd = (peak - nav) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)

        return max_dd

    def _compute_sharpe_single(self, returns: list[float]) -> float:
        """计算单一窗口的年化 Sharpe 比率.

        Args:
            returns: 日收益率序列

        Returns:
            年化 Sharpe 比率
        """
        if not returns:
            return 0.0

        n = len(returns)
        mean_daily = sum(returns) / n

        # 标准差
        var = sum((r - mean_daily) ** 2 for r in returns) / n
        std_daily = math.sqrt(var)

        if std_daily < 1e-15:
            return 0.0

        # 年化 Sharpe (无风险利率扣除)
        excess_daily = mean_daily - self._risk_free_rate / TRADING_DAYS_PER_YEAR
        ann_excess = excess_daily * TRADING_DAYS_PER_YEAR
        ann_vol = std_daily * math.sqrt(TRADING_DAYS_PER_YEAR)
        sharpe = ann_excess / ann_vol if ann_vol > 0 else 0.0

        return sharpe

    # ------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """获取适配器状态摘要."""
        ff_status = self._shadow_account.fail_fast_monitor.get_status()
        return {
            "account_id": self._shadow_account.account_id,
            "strategy_id": self._shadow_account.strategy_id,
            "status": self._shadow_account.status.value,
            "days_tracked": len(self._daily_returns),
            "is_real_data": self._is_real_data,
            "fail_fast_triggered": ff_status.get("triggered", False),
            "fail_fast_reason": ff_status.get("reason"),
            "risk_free_rate": self._risk_free_rate,
            "n_trials": self._n_trials,
            "required_dsr": self._required_dsr,
            "sharpe_cv_window": self._sharpe_cv_window,
        }

    @property
    def shadow_account(self):
        """访问底层 ShadowAccount 实例 (供高级用户使用)."""
        return self._shadow_account

    @property
    def daily_returns(self) -> list[float]:
        """已记录的日收益率序列 (只读视图)."""
        return list(self._daily_returns)


# ============================================================
# 模块级便捷函数
# ============================================================


def create_default_adapter(
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
) -> ShadowAccountAdapter:
    """创建默认配置的 Shadow 账户适配器.

    Args:
        initial_capital: 初始资金 (元)

    Returns:
        ShadowAccountAdapter 实例
    """
    return ShadowAccountAdapter(initial_capital=initial_capital)


def run_shadow_with_returns(
    daily_returns: Sequence[float],
    account_id: str = DEFAULT_ACCOUNT_ID,
    strategy_id: str = DEFAULT_STRATEGY_ID,
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
    is_real_data: bool = True,
) -> tuple[ShadowAccountAdapter, ShadowMetrics]:
    """便捷函数: 创建适配器 + 运行 + 获取指标.

    Args:
        daily_returns: 每日收益率序列
        account_id: 账户 ID
        strategy_id: 策略 ID
        initial_capital: 初始资金
        is_real_data: 是否为真实数据

    Returns:
        (adapter, metrics)

    Raises:
        InsufficientReturnsError: 样本不足
        FailFastTriggeredError: Fail-Fast 触发
    """
    adapter = ShadowAccountAdapter(
        account_id=account_id,
        strategy_id=strategy_id,
        initial_capital=initial_capital,
    )
    adapter.run_shadow(daily_returns=daily_returns, is_real_data=is_real_data)
    metrics = adapter.get_metrics()
    return adapter, metrics


__all__ = [
    "CUMULATIVE_3D_DRAWDOWN_THRESHOLD",
    "DAILY_DRAWDOWN_THRESHOLD",
    # 常量
    "DEFAULT_ACCOUNT_ID",
    "DEFAULT_INITIAL_CAPITAL",
    "DEFAULT_N_TRIALS",
    "DEFAULT_REQUIRED_DSR",
    "DEFAULT_RISK_FREE_RATE",
    "DEFAULT_SHARPE_CV_WINDOW",
    "DEFAULT_STRATEGY_ID",
    "MIN_SAMPLES_FOR_DSR",
    "MIN_SAMPLES_FOR_SHARPE_CV",
    "TRADING_DAYS_PER_YEAR",
    "FailFastTriggeredError",
    "InsufficientReturnsError",
    "RunShadowResult",
    # 主类
    "ShadowAccountAdapter",
    # 异常
    "ShadowAccountAdapterError",
    # 数据类
    "ShadowMetrics",
    # 便捷函数
    "create_default_adapter",
    "run_shadow_with_returns",
]
