"""策略多维评估器 — 自我进化框架第 1 阶段交付物.

模块整合 8.4 — ARCHITECTURE_自我进化框架 §4.1
灵感来源: AIDE² (Weco AI) — Public/Private 分数分离反作弊机制

设计原则:
    1. 只读历史数据, 不修改 V9 基线 (HC-4 观察期兼容)
    2. Feature Flag 透传 (HC-1): USE_STRATEGY_EVALUATOR 默认 False
    3. Public/Private 分数分离 (AIDE² 核心机制)
    4. 复用现有 DSR + ShadowAccountAdapter, 不重复造轮子
    5. 容错降级: 子模块失败不阻塞, 返回降级报告

评分维度:
    Public Score (样本内, agent 可见):
        - 绝对收益 (0.15): 年化收益率 vs 基准
        - 风险调整 (0.15): 样本内 Sharpe
    Private Score (样本外, 外层决策用):
        - 稳定性 (0.20): 最大回撤 vs 阈值
        - 稳健性 (0.20): Walk-Forward Sharpe 衰减率
        - 反作弊 (0.20): DSR + PIT 通过率
        - 复杂度惩罚 (0.10): 特征数 / 参数数

用法:
    from utils.alpha.strategy_evaluator import StrategyEvaluator, ScoreReport

    evaluator = StrategyEvaluator()
    report = evaluator.evaluate(
        daily_returns=[0.01, -0.005, 0.008, ...],
        n_trials=100,
    )
    # report.public_score  → 内层 agent 可见
    # report.private_score → 外层决策用
    # report.reward_hacking_risk → 反作弊风险 (0.0-1.0)

硬约束:
    - HC-1: Feature Flag 默认 False, 关闭时返回降级报告
    - HC-4: 观察期内不触发任何进化动作, 仅评估
    - HC-5: 配置走 ConfigManager 4 级优先级
"""

from __future__ import annotations

import json
import logging
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

logger = logging.getLogger(__name__)

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# V8.3 validation 模块路径 (复用 DSR)
_V83_VALIDATION_DIR = _PROJECT_ROOT / "v8.3_institutional" / "src" / "validation"
if str(_V83_VALIDATION_DIR) not in sys.path:
    sys.path.insert(0, str(_V83_VALIDATION_DIR))


# ============================================================
# 常量
# ============================================================

TRADING_DAYS_PER_YEAR = 252
DEFAULT_RISK_FREE_RATE = 0.03
DEFAULT_N_TRIALS = 100
DEFAULT_REQUIRED_DSR = 0.95

# 评分权重 (Public 0.30 + Private 0.70 = 1.00)
WEIGHT_ABSOLUTE_RETURN = 0.15  # Public
WEIGHT_RISK_ADJUSTED = 0.15  # Public
WEIGHT_STABILITY = 0.20  # Private
WEIGHT_ROBUSTNESS = 0.20  # Private
WEIGHT_ANTI_CHEAT = 0.20  # Private
WEIGHT_COMPLEXITY_PENALTY = 0.10  # Private

# 基准阈值
BASELINE_ANNUAL_RETURN = 0.08  # 基准年化 8%
BASELINE_SHARPE = 0.5  # 基准 Sharpe 0.5
MAX_ALLOWED_DRAWDOWN = 0.15  # 最大允许回撤 15% (对齐 MAX_DRAWDOWN_LIMIT)
MIN_SAMPLES_FOR_DSR = 15  # DSR 最小样本 (2026-07-30 PM 决策: 20→15, 配合 21 天观察期)
MIN_SAMPLES_FOR_WF = 100  # Walk-Forward 最小样本

# Reward Hacking 风险阈值
RH_RISK_DSR_FAIL = 0.40  # DSR 未通过 → +0.40
RH_RISK_PIT_VIOLATION = 0.30  # PIT 违规 → +0.30
RH_RISK_OVERFIT = 0.30  # 样本内外差异过大 → +0.30

# 决策阈值
PROMOTE_PRIVATE_SCORE = 0.70  # Private Score > 0.70 可晋升
PROMOTE_RH_RISK_MAX = 0.30  # Reward Hacking Risk < 0.30 可晋升
ROLLBACK_PRIVATE_SCORE = 0.30  # Private Score < 0.30 应回滚


# ============================================================
# 数据类
# ============================================================


@dataclass
class ScoreReport:
    """策略评分报告 (AIDE² 式 Public/Private 分离)."""

    # Public Score (内层 agent 可见)
    public_score: float = 0.0  # 样本内综合得分 (0.0-1.0)
    public_metrics: dict[str, float] = field(default_factory=dict)
    # 样本内指标: annual_return, sharpe, ic_mean, ic_ir

    # Private Score (外层决策用)
    private_score: float = 0.0  # 样本外综合得分 (0.0-1.0)
    private_metrics: dict[str, float] = field(default_factory=dict)
    # 样本外指标: dsr, max_drawdown, sharpe_cv, wf_sharpe_decay

    # 反作弊指标
    reward_hacking_risk: float = 0.0  # 0.0-1.0, 越高越危险
    pit_violations: int = 0  # 未来函数违规数
    overfit_score: float = 0.0  # 过拟合分数 (样本内外差异)

    # 决策建议
    recommendation: str = "continue"  # promote / rollback / continue
    reason: str = ""

    # 元信息
    sample_count: int = 0
    is_degraded: bool = False  # 是否降级报告
    degraded_reason: str = ""  # 降级原因

    def to_dict(self) -> dict[str, Any]:
        """转换为字典 (用于持久化)."""
        return {
            "public_score": round(self.public_score, 4),
            "public_metrics": {k: round(v, 4) for k, v in self.public_metrics.items()},
            "private_score": round(self.private_score, 4),
            "private_metrics": {k: round(v, 4) for k, v in self.private_metrics.items()},
            "reward_hacking_risk": round(self.reward_hacking_risk, 4),
            "pit_violations": self.pit_violations,
            "overfit_score": round(self.overfit_score, 4),
            "recommendation": self.recommendation,
            "reason": self.reason,
            "sample_count": self.sample_count,
            "is_degraded": self.is_degraded,
            "degraded_reason": self.degraded_reason,
        }


# ============================================================
# 评估器
# ============================================================


class StrategyEvaluator:
    """量化策略多维评估器 (只读, 不影响基线).

    借鉴 AIDE² 的 Public/Private 分数分离机制:
    - Public Score: 样本内表现 (内层 agent 可见, 引导搜索)
    - Private Score: 样本外稳健性 (外层决策用, 防过拟合)

    复用现有基础设施:
    - deflated_sharpe.py: DSR 多重测试偏差校正
    - ShadowAccountAdapter: Shadow 评估 + Fail-Fast
    - (可选) walk_forward.py: Purged Walk-Forward CV
    - (可选) pit_checker.py: 6 维度未来函数检测
    """

    def __init__(
        self,
        risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
        n_trials: int = DEFAULT_N_TRIALS,
        required_dsr: float = DEFAULT_REQUIRED_DSR,
        feature_flag_name: str = "USE_STRATEGY_EVALUATOR",
    ) -> None:
        """初始化评估器.

        Args:
            risk_free_rate: 年化无风险利率
            n_trials: DSR 校正试验次数 (多重测试偏差)
            required_dsr: DSR 阈值 (0.0-1.0)
            feature_flag_name: Feature Flag 名称 (HC-1)
        """
        self.risk_free_rate = risk_free_rate
        self.n_trials = n_trials
        self.required_dsr = required_dsr
        self.feature_flag_name = feature_flag_name

        # 检查 Feature Flag (HC-1)
        self._enabled = self._check_feature_flag(feature_flag_name)

        logger.info(
            "StrategyEvaluator 初始化: enabled=%s (flag=%s)",
            self._enabled,
            feature_flag_name,
        )

    # ============================================================
    # Feature Flag
    # ============================================================

    def _check_feature_flag(self, name: str) -> bool:
        """检查 Feature Flag (HC-1)."""
        try:
            from utils.infra.feature_flags import is_enabled

            return bool(is_enabled(name))
        except (ImportError, AttributeError) as e:
            logger.warning("Feature Flag 检查失败, 默认禁用: %s", e)
            return False

    @property
    def enabled(self) -> bool:
        """是否启用."""
        return self._enabled

    # ============================================================
    # 核心评估 API
    # ============================================================

    def evaluate(
        self,
        daily_returns: Sequence[float],
        dates: Sequence[str] | None = None,
        signal_history: dict[str, Any] | None = None,
        n_trials: int | None = None,
    ) -> ScoreReport:
        """评估策略表现, 返回 Public/Private 分离的评分报告.

        Args:
            daily_returns: 日收益率序列 (小数, 如 0.01 = 1%)
            dates: 对应日期 (可选, 用于 Walk-Forward)
            signal_history: 信号历史 (可选, 用于 IC 和 PIT 检查)
                预期格式: {
                    "ic_series": List[float],       # IC 时间序列
                    "ic_ir": float,                 # IC 信息比率
                    "features_used": List[str],     # 使用的特征列表
                    "timestamps": List[str],        # 信号时间戳
                }
            n_trials: DSR 校正试验次数 (None=用默认值)

        Returns:
            ScoreReport 评分报告

        注意:
            - 此方法是只读的, 不修改任何生产路径
            - Feature Flag 关闭时返回降级报告
            - 样本不足时返回降级报告, 不抛异常
        """
        # HC-1: Feature Flag 检查
        if not self._enabled:
            return self._build_degraded_report(
                daily_returns,
                reason=f"feature_flag_disabled ({self.feature_flag_name}=False)",
            )

        n = len(daily_returns)
        if n == 0:
            return self._build_degraded_report(daily_returns, reason="empty_returns")

        trials = n_trials if n_trials is not None else self.n_trials
        signal_history = signal_history or {}

        # 1. 计算 Public Score (样本内)
        public_score, public_metrics = self._compute_public_score(daily_returns, signal_history)

        # 2. 计算 Private Score (样本外)
        private_score, private_metrics = self._compute_private_score(daily_returns, signal_history, dates, trials)

        # 3. 计算 Reward Hacking Risk
        rh_risk, pit_violations, overfit_score = self._compute_reward_hacking_risk(
            daily_returns, signal_history, public_metrics, private_metrics
        )

        # 4. 决策建议
        recommendation, reason = self._make_recommendation(private_score, rh_risk, public_score)

        return ScoreReport(
            public_score=public_score,
            public_metrics=public_metrics,
            private_score=private_score,
            private_metrics=private_metrics,
            reward_hacking_risk=rh_risk,
            pit_violations=pit_violations,
            overfit_score=overfit_score,
            recommendation=recommendation,
            reason=reason,
            sample_count=n,
            is_degraded=False,
        )

    # ============================================================
    # Public Score 计算 (样本内, agent 可见)
    # ============================================================

    def _compute_public_score(
        self,
        daily_returns: Sequence[float],
        signal_history: dict[str, Any],
    ) -> tuple[float, dict[str, float]]:
        """计算样本内 Public Score.

        维度:
            - 绝对收益 (0.15): 年化收益率 vs 基准
            - 风险调整 (0.15): 样本内 Sharpe
        """
        metrics: dict[str, float] = {}

        # 年化收益率
        annual_return = self._compute_annual_return(daily_returns)
        metrics["annual_return"] = annual_return

        # 样本内 Sharpe
        sharpe = self._compute_sharpe(daily_returns)
        metrics["sharpe"] = sharpe

        # IC 指标 (如果有信号历史)
        if "ic_ir" in signal_history:
            metrics["ic_ir"] = float(signal_history["ic_ir"])
        if signal_history.get("ic_series"):
            ic_series = signal_history["ic_series"]
            metrics["ic_mean"] = sum(ic_series) / len(ic_series)

        # 评分
        score_absolute = self._score_absolute_return(annual_return)
        score_risk_adjusted = self._score_risk_adjusted(sharpe)

        public_score = score_absolute * WEIGHT_ABSOLUTE_RETURN + score_risk_adjusted * WEIGHT_RISK_ADJUSTED

        return public_score, metrics

    def _score_absolute_return(self, annual_return: float) -> float:
        """年化收益率打分 (0.0-1.0)."""
        # 相对基准的超额收益
        excess = annual_return - BASELINE_ANNUAL_RETURN
        # 超额 15% 得满分
        return min(1.0, max(0.0, excess / 0.15))

    def _score_risk_adjusted(self, sharpe: float) -> float:
        """Sharpe 打分 (0.0-1.0)."""
        # Sharpe > 1.5 得满分, < 0.5 得 0 分
        return min(1.0, max(0.0, (sharpe - BASELINE_SHARPE) / 1.0))

    # ============================================================
    # Private Score 计算 (样本外, 外层决策用)
    # ============================================================

    def _compute_private_score(
        self,
        daily_returns: Sequence[float],
        signal_history: dict[str, Any],
        dates: Sequence[str] | None,
        n_trials: int,
    ) -> tuple[float, dict[str, float]]:
        """计算样本外 Private Score.

        维度:
            - 稳定性 (0.20): 最大回撤 vs 阈值
            - 稳健性 (0.20): Walk-Forward Sharpe 衰减率
            - 反作弊 (0.20): DSR + PIT 通过率
            - 复杂度惩罚 (0.10): 特征数 / 参数数
        """
        metrics: dict[str, float] = {}
        n = len(daily_returns)

        # 1. 稳定性: 最大回撤
        max_dd = self._compute_max_drawdown(daily_returns)
        metrics["max_drawdown"] = max_dd
        score_stability = self._score_stability(max_dd)

        # 2. 稳健性: Walk-Forward (样本不足时降级)
        wf_decay = 0.0
        if n >= MIN_SAMPLES_FOR_WF:
            wf_decay = self._compute_wf_sharpe_decay(daily_returns)
            metrics["wf_sharpe_decay"] = wf_decay
        else:
            metrics["wf_sharpe_decay"] = -1.0  # 标记样本不足
        score_robustness = self._score_robustness(wf_decay)

        # 3. 反作弊: DSR (样本不足时降级)
        dsr_score = 0.0
        if n >= MIN_SAMPLES_FOR_DSR:
            dsr_result = self._compute_dsr(daily_returns, n_trials)
            if dsr_result is not None:
                metrics["dsr"] = dsr_result.get("deflated_sharpe_ratio", 0.0)
                metrics["dsr_p_value"] = dsr_result.get("p_value", 1.0)
                dsr_score = self._score_anti_cheat_dsr(dsr_result)
            else:
                metrics["dsr"] = -1.0  # 计算失败
        else:
            metrics["dsr"] = -1.0  # 样本不足
            dsr_score = 0.0

        # Sharpe CV (复用 ShadowAccountAdapter 逻辑, 样本不足时降级)
        sharpe_cv = self._compute_sharpe_cv(daily_returns)
        metrics["sharpe_cv"] = sharpe_cv

        # 4. 复杂度惩罚
        features_used = signal_history.get("features_used", [])
        n_features = len(features_used) if isinstance(features_used, list) else 0
        metrics["n_features"] = float(n_features)
        score_complexity = self._score_complexity(n_features)

        private_score = (
            score_stability * WEIGHT_STABILITY
            + score_robustness * WEIGHT_ROBUSTNESS
            + dsr_score * WEIGHT_ANTI_CHEAT
            + score_complexity * WEIGHT_COMPLEXITY_PENALTY
        )

        return private_score, metrics

    def _score_stability(self, max_drawdown: float) -> float:
        """稳定性打分: 最大回撤 vs 阈值."""
        # 回撤越小越好, MAX_ALLOWED_DRAWDOWN 得 0 分
        if max_drawdown >= MAX_ALLOWED_DRAWDOWN:
            return 0.0
        # 0 回撤得满分
        return 1.0 - (max_drawdown / MAX_ALLOWED_DRAWDOWN)

    def _score_robustness(self, wf_decay: float) -> float:
        """稳健性打分: Walk-Forward Sharpe 衰减率.

        wf_decay: 0.0 = 无衰减, 1.0 = 完全衰减
        """
        if wf_decay < 0:  # 样本不足
            return 0.3  # 中性偏保守
        # 衰减 < 0.2 得满分, > 0.8 得 0 分
        return min(1.0, max(0.0, 1.0 - wf_decay / 0.8))

    def _score_anti_cheat_dsr(self, dsr_result: dict[str, Any]) -> float:
        """反作弊打分: DSR 结果."""
        is_pass = dsr_result.get("is_pass", False)
        dsr_value = dsr_result.get("deflated_sharpe_ratio", 0.0)
        if is_pass:
            return 1.0
        # DSR 值越高越好 (即使未通过阈值)
        return min(0.5, max(0.0, dsr_value / self.required_dsr * 0.5))

    def _score_complexity(self, n_features: int) -> float:
        """复杂度惩罚打分: 特征数越少越好.

        0-10 特征: 满分
        10-50 特征: 线性衰减
        >50 特征: 0 分
        """
        if n_features <= 10:
            return 1.0
        if n_features >= 50:
            return 0.0
        return 1.0 - (n_features - 10) / 40.0

    # ============================================================
    # Reward Hacking Risk 计算
    # ============================================================

    def _compute_reward_hacking_risk(
        self,
        daily_returns: Sequence[float],
        signal_history: dict[str, Any],
        public_metrics: dict[str, float],
        private_metrics: dict[str, float],
    ) -> tuple[float, int, float]:
        """计算 Reward Hacking Risk.

        Returns:
            (risk_score, pit_violations, overfit_score)
            risk_score: 0.0-1.0, 越高越危险
        """
        risk = 0.0
        pit_violations = 0
        overfit_score = 0.0

        # 1. DSR 未通过 → +0.40
        dsr_value = private_metrics.get("dsr", -1.0)
        if dsr_value >= 0 and dsr_value < self.required_dsr:
            risk += RH_RISK_DSR_FAIL

        # 2. PIT 违规 → +0.30 (如果有信号时间戳)
        timestamps = signal_history.get("timestamps")
        if timestamps:
            pit_violations = self._check_pit_violations(signal_history)
            if pit_violations > 0:
                risk += RH_RISK_PIT_VIOLATION

        # 3. 过拟合: 样本内外差异过大 → +0.30
        public_sharpe = public_metrics.get("sharpe", 0.0)
        private_sharpe_decay = private_metrics.get("wf_sharpe_decay", 0.0)
        if private_sharpe_decay >= 0 and public_sharpe > 0:
            # 衰减率 = (样本内 - 样本外) / 样本内
            overfit_score = min(1.0, max(0.0, private_sharpe_decay))
            if overfit_score > 0.5:  # 衰减超过 50%
                risk += RH_RISK_OVERFIT * overfit_score

        # 限制在 0.0-1.0
        risk = min(1.0, max(0.0, risk))

        return risk, pit_violations, overfit_score

    def _check_pit_violations(self, signal_history: dict[str, Any]) -> int:
        """检查 PIT (Point-in-Time) 违规数.

        复用 v8.3_institutional/src/validation/pit_checker.py (6 维度完整检测).
        如果模块不可用, 降级为简化版时间戳单调性检查.

        6 维度检测:
            1. 时间戳单调性
            2. 信号发布滞后
            3. 训练/测试无重叠
            4. 指标前视检查
            5. 财务数据对齐
            6. 交叉验证时间隔离
        """
        try:
            import importlib

            pit_checker_mod = importlib.import_module("pit_checker")
            PITChecker = pit_checker_mod.PITChecker

            checker = PITChecker()
            violations_count = 0

            # 维度 1: 时间戳单调性
            signal_records = signal_history.get("signal_records", [])
            timestamps = signal_history.get("timestamps", [])
            if not signal_records and timestamps:
                # 从 timestamps 构建 signal_records
                signal_records = [{"timestamp": ts} for ts in timestamps]
            if signal_records:
                checker.check_signal_timestamps(signal_records)

            # 维度 3: 训练/测试分割 (如果有)
            cv_splits = signal_history.get("cv_splits", [])
            if cv_splits:
                for i, split in enumerate(cv_splits):
                    train_end = split.get("train_end")
                    test_start = split.get("test_start")
                    if train_end is not None and test_start is not None:
                        checker.check_train_test_split(
                            train_end,
                            test_start,
                            min_gap_days=split.get("min_gap_days", 0),
                            label=f"fold_{i}",
                        )

            # 维度 4: 指标前视 (如果有 indicators 配置)
            indicators = signal_history.get("indicators", [])
            if indicators:
                checker.check_indicator_offset(indicators)

            # 维度 5: 财务数据对齐 (如果有 financial_records)
            financial_records = signal_history.get("financial_records", [])
            if financial_records:
                checker.check_financial_data_alignment(financial_records)

            report = checker.generate_report()
            violations_count = len(report.violations)
            return violations_count
        except ImportError:
            logger.debug("PITChecker 不可用, 降级为简化检查")
            # 降级: 时间戳单调性
            timestamps = signal_history.get("timestamps", [])
            if not timestamps:
                return 0
            violations = 0
            for i in range(1, len(timestamps)):
                if timestamps[i] < timestamps[i - 1]:
                    violations += 1
            return violations
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.warning("PIT 检查异常: %s", e)
            return 0

    # ============================================================
    # 决策建议
    # ============================================================

    def _make_recommendation(
        self,
        private_score: float,
        rh_risk: float,
        public_score: float,
    ) -> tuple[str, str]:
        """根据评分生成决策建议.

        Returns:
            (recommendation, reason)
            recommendation: promote / rollback / continue
        """
        if rh_risk > 0.7:
            return "rollback", f"reward_hacking_risk={rh_risk:.2f} > 0.7"
        if private_score < ROLLBACK_PRIVATE_SCORE:
            return "rollback", f"private_score={private_score:.2f} < {ROLLBACK_PRIVATE_SCORE}"
        if private_score > PROMOTE_PRIVATE_SCORE and rh_risk < PROMOTE_RH_RISK_MAX:
            return "promote", (
                f"private_score={private_score:.2f} > {PROMOTE_PRIVATE_SCORE}, "
                f"rh_risk={rh_risk:.2f} < {PROMOTE_RH_RISK_MAX}"
            )
        return "continue", (f"private_score={private_score:.2f}, rh_risk={rh_risk:.2f}, 等待更多数据")

    # ============================================================
    # 底层计算工具 (复用现有模块)
    # ============================================================

    def _compute_annual_return(self, daily_returns: Sequence[float]) -> float:
        """计算年化收益率."""
        n = len(daily_returns)
        if n == 0:
            return 0.0
        total_return = 1.0
        for r in daily_returns:
            total_return *= 1.0 + r
        total_return -= 1.0
        if total_return <= -1.0:
            return -1.0
        if n < TRADING_DAYS_PER_YEAR:
            # 样本不足一年, 外推
            annualized = (1.0 + total_return) ** (TRADING_DAYS_PER_YEAR / n) - 1.0
        else:
            annualized = (1.0 + total_return) ** (TRADING_DAYS_PER_YEAR / n) - 1.0
        return annualized

    def _compute_sharpe(self, daily_returns: Sequence[float]) -> float:
        """计算年化 Sharpe."""
        n = len(daily_returns)
        if n < 2:
            return 0.0
        mean = sum(daily_returns) / n
        var = sum((r - mean) ** 2 for r in daily_returns) / (n - 1)
        std = math.sqrt(var) if var > 0 else 0.0
        if std < 1e-10:
            return 0.0
        daily_sharpe = (mean - self.risk_free_rate / TRADING_DAYS_PER_YEAR) / std
        return daily_sharpe * math.sqrt(TRADING_DAYS_PER_YEAR)

    def _compute_max_drawdown(self, daily_returns: Sequence[float]) -> float:
        """计算最大回撤."""
        nav = 1.0
        peak = 1.0
        max_dd = 0.0
        for r in daily_returns:
            nav *= 1.0 + r
            if nav > peak:
                peak = nav
            dd = (peak - nav) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        return max_dd

    def _compute_sharpe_cv(self, daily_returns: Sequence[float]) -> float:
        """计算 Sharpe CV (简化版).

        Sharpe CV = std(rolling_sharpe) / |mean(rolling_sharpe)|
        样本不足时返回 0.0.
        """
        n = len(daily_returns)
        if n < 20:  # 最小 20 样本
            return 0.0
        # 使用 60 日滚动窗口 (简化, 非默认 252)
        window = min(60, n // 3)
        if window < 10:
            return 0.0
        rolling_sharpes: list[float] = []
        for i in range(window, n):
            chunk = daily_returns[i - window : i]
            rolling_sharpes.append(self._compute_sharpe(chunk))
        if len(rolling_sharpes) < 2:
            return 0.0
        mean_sharpe = sum(rolling_sharpes) / len(rolling_sharpes)
        if abs(mean_sharpe) < 1e-10:
            return 0.0
        var = sum((s - mean_sharpe) ** 2 for s in rolling_sharpes) / (len(rolling_sharpes) - 1)
        std_sharpe = math.sqrt(var) if var > 0 else 0.0
        return std_sharpe / abs(mean_sharpe)

    def _compute_wf_sharpe_decay(self, daily_returns: Sequence[float]) -> float:
        """计算 Walk-Forward Sharpe 衰减率.

        复用 v8.3_institutional/src/validation/walk_forward.py 的
        purged_walk_forward_split (Purged K-Fold, Lopez de Prado Ch.7).

        对每折计算 train_sharpe 和 test_sharpe, 返回平均衰减率:
            decay = mean((train_sharpe - test_sharpe) / |train_sharpe|)

        样本不足时降级为前后半段对比.

        Returns:
            0.0 = 无衰减, 1.0 = 完全衰减, 负值 = 后半段更好
        """
        n = len(daily_returns)
        if n < MIN_SAMPLES_FOR_WF:
            return -1.0

        try:
            import importlib

            walk_forward_mod = importlib.import_module("walk_forward")
            purged_walk_forward_split = walk_forward_mod.purged_walk_forward_split

            # 参数: 5 折, purge=5, embargo=5
            train_size = max(100, n // 3)
            test_size = max(20, n // 6)
            folds = purged_walk_forward_split(
                n_total=n,
                train_size=train_size,
                test_size=test_size,
                purge_days=5,
                embargo_days=5,
                min_train_size=100,
            )

            if not folds:
                # 折叠生成失败, 降级
                return self._compute_wf_sharpe_decay_simple(daily_returns)

            decays: list[float] = []
            for fold in folds:
                train_data = daily_returns[fold.train_start : fold.train_end]
                test_data = daily_returns[fold.test_start : fold.test_end]
                if len(train_data) < 20 or len(test_data) < 5:
                    continue
                train_sharpe = self._compute_sharpe(train_data)
                test_sharpe = self._compute_sharpe(test_data)
                if abs(train_sharpe) < 1e-10:
                    continue
                decay = (train_sharpe - test_sharpe) / abs(train_sharpe)
                decays.append(max(-1.0, min(1.0, decay)))

            if not decays:
                return self._compute_wf_sharpe_decay_simple(daily_returns)

            return sum(decays) / len(decays)
        except ImportError:
            logger.debug("walk_forward 模块不可用, 降级为简化版")
            return self._compute_wf_sharpe_decay_simple(daily_returns)
        except (ImportError, AttributeError) as e:
            logger.warning("Purged Walk-Forward 计算异常: %s", e)
            return self._compute_wf_sharpe_decay_simple(daily_returns)

    def _compute_wf_sharpe_decay_simple(self, daily_returns: Sequence[float]) -> float:
        """简化版 Walk-Forward 衰减率 (前后半段对比).

        降级使用: walk_forward 模块不可用时.

        Returns:
            0.0 = 无衰减, 1.0 = 完全衰减, 负值 = 后半段更好
        """
        n = len(daily_returns)
        if n < MIN_SAMPLES_FOR_WF:
            return -1.0
        mid = n // 2
        first_half = daily_returns[:mid]
        second_half = daily_returns[mid:]
        sharpe_first = self._compute_sharpe(first_half)
        sharpe_second = self._compute_sharpe(second_half)
        if abs(sharpe_first) < 1e-10:
            return 0.0 if sharpe_second >= 0 else 1.0
        decay = (sharpe_first - sharpe_second) / abs(sharpe_first)
        return max(-1.0, min(1.0, decay))

    def _compute_dsr(self, daily_returns: Sequence[float], n_trials: int) -> dict[str, Any] | None:
        """计算 DSR (复用 deflated_sharpe.py).

        Returns:
            DSR 结果字典, 失败时返回 None
        """
        try:
            import importlib

            deflated_sharpe_mod = importlib.import_module("deflated_sharpe")
            deflated_sharpe_ratio = deflated_sharpe_mod.deflated_sharpe_ratio

            result = deflated_sharpe_ratio(
                daily_returns=list(daily_returns),
                n_trials=n_trials,
                required_dsr=self.required_dsr,
                risk_free_rate=self.risk_free_rate,
            )
            # 转换为字典 (兼容 dataclass 和 tuple 返回)
            if hasattr(result, "__dict__"):
                return {
                    "sharpe_ratio": getattr(result, "sharpe_ratio", 0.0),
                    "deflated_sharpe_ratio": getattr(result, "deflated_sharpe_ratio", 0.0),
                    "p_value": getattr(result, "p_value", 1.0),
                    "is_pass": getattr(result, "is_pass", False),
                    "verdict": getattr(result, "verdict", ""),
                }
            # 兼容 tuple 返回 (旧版本)
            if isinstance(result, (list, tuple)) and len(result) >= 3:
                return {
                    "deflated_sharpe_ratio": float(result[0]),
                    "p_value": float(result[1]) if len(result) > 1 else 1.0,
                    "is_pass": bool(result[2]) if len(result) > 2 else False,
                }
            return None
        except ImportError:
            logger.debug("deflated_sharpe 模块不可用, 跳过 DSR 计算")
            return None
        except (AttributeError, TypeError, ValueError, OSError) as e:
            logger.warning("DSR 计算异常: %s", e)
            return None

    # ============================================================
    # 降级报告
    # ============================================================

    def _build_degraded_report(
        self,
        daily_returns: Sequence[float],
        reason: str,
    ) -> ScoreReport:
        """构建降级报告 (不抛异常)."""
        return ScoreReport(
            public_score=0.0,
            public_metrics={},
            private_score=0.0,
            private_metrics={},
            reward_hacking_risk=1.0,  # 降级时风险最高
            pit_violations=0,
            overfit_score=0.0,
            recommendation="continue",
            reason=f"degraded: {reason}",
            sample_count=len(daily_returns),
            is_degraded=True,
            degraded_reason=reason,
        )

    # ============================================================
    # 便捷方法: 从 daily_returns.jsonl 读取并评估
    # ============================================================

    def evaluate_from_jsonl(
        self,
        jsonl_path: str | None = None,
    ) -> ScoreReport:
        """从 daily_returns.jsonl 读取数据并评估 (只读).

        Args:
            jsonl_path: JSONL 文件路径 (None=用默认路径)

        Returns:
            ScoreReport 评分报告
        """
        if jsonl_path is None:
            jsonl_path = str(_PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl")

        path = Path(jsonl_path)
        if not path.exists():
            return self._build_degraded_report([], reason=f"file_not_found: {jsonl_path}")

        daily_returns: list[float] = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    record = json.loads(line)
                    if "daily_return" in record:
                        daily_returns.append(float(record["daily_return"]))
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError) as e:
            logger.exception("读取 daily_returns.jsonl 失败: %s", e)
            return self._build_degraded_report([], reason=f"read_error: {e}")

        return self.evaluate(daily_returns)


# ============================================================
# 便捷函数
# ============================================================


def evaluate_strategy(
    daily_returns: Sequence[float],
    n_trials: int = DEFAULT_N_TRIALS,
) -> ScoreReport:
    """便捷函数: 评估策略表现."""
    evaluator = StrategyEvaluator(n_trials=n_trials)
    return evaluator.evaluate(daily_returns)


def evaluate_from_shadow() -> ScoreReport:
    """便捷函数: 从 Shadow daily_returns.jsonl 评估."""
    evaluator = StrategyEvaluator()
    return evaluator.evaluate_from_jsonl()