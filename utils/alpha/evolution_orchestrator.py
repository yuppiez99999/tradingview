"""自我进化编排器原型 — 自我进化框架第 1 阶段交付物 (只读模式).

模块整合 8.4 — ARCHITECTURE_自我进化框架 §4.2
灵感来源: AIDE² (Weco AI) — 双层循环架构 (外层策略进化 / 内层策略执行)

设计原则:
    1. 只读模式 (HC-4 兼容): 观察期内不触发任何进化动作
       - 不调用 DriftMonitor / ABTestFramework / AutoRetrainScheduler
       - 不修改 V9 基线 / positions.json / 任何生产路径
    2. Feature Flag 透传 (HC-1): USE_EVOLUTION_ORCHESTRATOR 默认 False
    3. 数据流向单向: 生产 → 编排器 (只读), 编排器 → 日志 (仅持久化决策)
    4. 容错降级: 子模块失败不阻塞, 返回降级状态
    5. 决策可审计: 每次评估写入 decisions.jsonl, 含完整上下文

架构 (双层循环):
    外层循环 (本模块):
        collect_metrics → evaluate_current → log_decision
                                   ↓
                            (观察期结束后)
                                   ↓
        drift_check → ab_test_decision → retrain_trigger → promote/rollback

    内层循环 (V9 基线, 不修改):
        daily_workflow → Signal → Portfolio → Execution → PnL

用法:
    from utils.alpha.evolution_orchestrator import EvolutionOrchestrator

    orchestrator = EvolutionOrchestrator()
    status = orchestrator.get_status()
    if status["enabled"]:
        metrics = orchestrator.collect_metrics()
        report = orchestrator.evaluate_current(metrics)
        orchestrator.log_decision(report, action="evaluate_only")

硬约束:
    - HC-1: Feature Flag 默认 False, 关闭时返回降级状态
    - HC-4: 观察期内不触发任何进化动作, 仅评估 + 日志
    - HC-5: 配置走 ConfigManager 4 级优先级
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================
# 常量
# ============================================================

# 默认数据源 (Shadow daily returns, 由 daily_workflow 写入)
DEFAULT_DAILY_RETURNS_PATH = (
    _PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"
)

# 决策日志路径
DEFAULT_DECISIONS_LOG_PATH = _PROJECT_ROOT / "reports" / "evolution" / "decisions.jsonl"

# 观察期配置 (HC-4)
OBSERVATION_PERIOD_DAYS = 14

# 评估触发阈值 (避免过度评估)
MIN_SAMPLES_FOR_EVALUATION = 20

# 决策动作枚举
ACTION_EVALUATE_ONLY = "evaluate_only"  # 观察期内: 仅评估
ACTION_PROMOTE = "promote"  # 晋升 (观察期后)
ACTION_ROLLBACK = "rollback"  # 回滚 (观察期后)
ACTION_CONTINUE = "continue"  # 继续 (默认)
ACTION_NOOP = "noop"  # 无操作 (Flag 关闭 / 样本不足)

# 编排器状态
STATUS_ENABLED = "enabled"
STATUS_DISABLED = "disabled"  # Flag 关闭
STATUS_DEGRADED = "degraded"  # 子模块失败
STATUS_OBSERVATION = "observation"  # 观察期内 (只读)


# ============================================================
# 数据类
# ============================================================


@dataclass
class MetricsSnapshot:
    """指标快照 (从生产数据只读收集)."""

    daily_returns: list[float] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    sample_count: int = 0
    source: str = ""  # 数据来源标识
    collected_at: str = ""  # ISO8601 时间戳
    is_degraded: bool = False  # 是否降级快照
    degraded_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为字典 (用于日志, 不含原始收益序列避免冗长)."""
        return {
            "sample_count": self.sample_count,
            "source": self.source,
            "collected_at": self.collected_at,
            "is_degraded": self.is_degraded,
            "degraded_reason": self.degraded_reason,
            "first_date": self.dates[0] if self.dates else "",
            "last_date": self.dates[-1] if self.dates else "",
        }


@dataclass
class OrchestratorStatus:
    """编排器状态."""

    status: str = STATUS_DISABLED  # enabled / disabled / degraded / observation
    enabled: bool = False  # Feature Flag 是否启用
    observation_day: int = 0  # 观察期第几天 (0=未开始)
    observation_total: int = OBSERVATION_PERIOD_DAYS
    in_observation: bool = False  # 是否在观察期内
    last_evaluation: str = ""  # 上次评估时间 (ISO8601)
    last_action: str = ACTION_NOOP  # 上次动作
    total_evaluations: int = 0  # 累计评估次数
    degraded_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为字典."""
        return {
            "status": self.status,
            "enabled": self.enabled,
            "observation_day": self.observation_day,
            "observation_total": self.observation_total,
            "in_observation": self.in_observation,
            "last_evaluation": self.last_evaluation,
            "last_action": self.last_action,
            "total_evaluations": self.total_evaluations,
            "degraded_reason": self.degraded_reason,
        }


@dataclass
class DecisionRecord:
    """决策记录 (持久化到 decisions.jsonl)."""

    timestamp: str  # ISO8601
    action: str  # evaluate_only / promote / rollback / continue / noop
    public_score: float = 0.0
    private_score: float = 0.0
    reward_hacking_risk: float = 0.0
    recommendation: str = ""  # 来自 ScoreReport
    sample_count: int = 0
    observation_day: int = 0
    reason: str = ""
    metrics_snapshot: dict[str, Any] = field(default_factory=dict)
    evaluator_report: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典 (JSONL 一行)."""
        return {
            "timestamp": self.timestamp,
            "action": self.action,
            "public_score": round(self.public_score, 4),
            "private_score": round(self.private_score, 4),
            "reward_hacking_risk": round(self.reward_hacking_risk, 4),
            "recommendation": self.recommendation,
            "sample_count": self.sample_count,
            "observation_day": self.observation_day,
            "reason": self.reason,
            "metrics_snapshot": self.metrics_snapshot,
            "evaluator_report": self.evaluator_report,
        }


# ============================================================
# 编排器
# ============================================================


class EvolutionOrchestrator:
    """自我进化编排器 (只读模式, 观察期兼容).

    双层循环架构的外层编排器:
    - 只读收集 V9 基线的生产指标
    - 调用 StrategyEvaluator 进行 Public/Private 分离评估
    - 持久化决策日志 (decisions.jsonl)
    - 观察期内不触发任何进化动作 (HC-4)

    不调用 (观察期内禁用):
    - DriftMonitor: 漂移检测
    - ABTestFramework: A/B 测试
    - AutoRetrainScheduler: 自动重训
    - ModelRegistry: 模型晋升/回滚
    """

    def __init__(
        self,
        daily_returns_path: Path | None = None,
        decisions_log_path: Path | None = None,
        observation_start_date: str | None = None,
        feature_flag_name: str = "USE_EVOLUTION_ORCHESTRATOR",
        evaluator_flag_name: str = "USE_STRATEGY_EVALUATOR",
    ) -> None:
        """初始化编排器.

        Args:
            daily_returns_path: daily_returns.jsonl 路径 (None=默认路径)
            decisions_log_path: decisions.jsonl 路径 (None=默认路径)
            observation_start_date: 观察期开始日期 (YYYY-MM-DD, None=自动从首条记录推断)
            feature_flag_name: 编排器 Flag 名称 (HC-1)
            evaluator_flag_name: 评估器 Flag 名称 (HC-1)
        """
        self.daily_returns_path = (
            Path(daily_returns_path)
            if daily_returns_path
            else DEFAULT_DAILY_RETURNS_PATH
        )
        self.decisions_log_path = (
            Path(decisions_log_path)
            if decisions_log_path
            else DEFAULT_DECISIONS_LOG_PATH
        )
        self.observation_start_date = observation_start_date

        self.feature_flag_name = feature_flag_name
        self.evaluator_flag_name = evaluator_flag_name

        # 检查 Feature Flag (HC-1)
        self._enabled = self._check_feature_flag(feature_flag_name)
        self._evaluator_enabled = self._check_feature_flag(evaluator_flag_name)

        # 内部状态
        self._status = OrchestratorStatus(
            enabled=self._enabled,
            status=STATUS_ENABLED if self._enabled else STATUS_DISABLED,
        )
        self._evaluator: Any | None = None  # 懒加载

        # 确保日志目录存在
        if self._enabled:
            self.decisions_log_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(
            "EvolutionOrchestrator 初始化: enabled=%s (flag=%s), evaluator_enabled=%s (flag=%s)",
            self._enabled,
            feature_flag_name,
            self._evaluator_enabled,
            evaluator_flag_name,
        )

    # ============================================================
    # Feature Flag
    # ============================================================

    def _check_feature_flag(self, name: str) -> bool:
        """检查 Feature Flag (HC-1)."""
        try:
            from utils.infra.feature_flags import is_enabled

            return bool(is_enabled(name))
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("Feature Flag 检查失败, 默认禁用: %s (%s)", name, e)
            return False

    @property
    def enabled(self) -> bool:
        """是否启用."""
        return self._enabled

    # ============================================================
    # 核心方法
    # ============================================================

    def get_status(self) -> dict[str, Any]:
        """获取编排器状态.

        Returns:
            状态字典 (可序列化)
        """
        # 更新观察期状态
        if self._enabled:
            self._update_observation_status()

        return self._status.to_dict()

    def collect_metrics(self) -> MetricsSnapshot:
        """从 daily_returns.jsonl 只读收集指标 (不修改生产数据).

        读取 reports/shadow/daily_returns.jsonl, 解析为 MetricsSnapshot.
        如果文件不存在或为空, 返回降级快照.

        Returns:
            MetricsSnapshot 指标快照
        """
        if not self._enabled:
            return MetricsSnapshot(
                is_degraded=True,
                degraded_reason=f"feature_flag_disabled ({self.feature_flag_name}=False)",
                collected_at=self._now_iso(),
            )

        if not self.daily_returns_path.exists():
            return MetricsSnapshot(
                is_degraded=True,
                degraded_reason=f"file_not_found: {self.daily_returns_path}",
                collected_at=self._now_iso(),
            )

        try:
            daily_returns: list[float] = []
            dates: list[str] = []

            with self.daily_returns_path.open("r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        date_str = record.get("date", "")
                        ret = float(record.get("daily_return", 0.0))
                        dates.append(date_str)
                        daily_returns.append(ret)
                    except (json.JSONDecodeError, ValueError, TypeError) as e:
                        logger.warning(
                            "daily_returns.jsonl 第 %d 行解析失败: %s (line=%s)",
                            line_num,
                            e,
                            line[:100],
                        )
                        continue

            if not daily_returns:
                return MetricsSnapshot(
                    is_degraded=True,
                    degraded_reason="empty_file",
                    collected_at=self._now_iso(),
                    source=str(self.daily_returns_path),
                )

            return MetricsSnapshot(
                daily_returns=daily_returns,
                dates=dates,
                sample_count=len(daily_returns),
                source=str(self.daily_returns_path),
                collected_at=self._now_iso(),
            )

        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.exception("collect_metrics 失败: %s", e)
            return MetricsSnapshot(
                is_degraded=True,
                degraded_reason=f"exception: {type(e).__name__}: {e}",
                collected_at=self._now_iso(),
                source=str(self.daily_returns_path),
            )

    def evaluate_current(
        self,
        metrics: MetricsSnapshot | None = None,
        signal_history: dict[str, Any] | None = None,
    ) -> Any | None:
        """评估当前策略状态 (调用 StrategyEvaluator).

        观察期内仅评估, 不触发任何进化动作 (HC-4).

        Args:
            metrics: 指标快照 (None=自动收集)
            signal_history: 信号历史 (可选, 用于 PIT 和 IC 检查)

        Returns:
            ScoreReport 对象, 或 None (Flag 关闭/降级)
        """
        if not self._enabled:
            logger.info("编排器禁用, 跳过评估 (flag=%s=False)", self.feature_flag_name)
            return None

        if not self._evaluator_enabled:
            logger.info(
                "评估器禁用, 跳过评估 (flag=%s=False)", self.evaluator_flag_name
            )
            self._status.status = STATUS_DEGRADED
            self._status.degraded_reason = (
                f"evaluator_disabled ({self.evaluator_flag_name}=False)"
            )
            return None

        # 收集指标 (如果未提供)
        if metrics is None:
            metrics = self.collect_metrics()

        if metrics.is_degraded:
            logger.warning("指标快照降级, 跳过评估: %s", metrics.degraded_reason)
            self._status.status = STATUS_DEGRADED
            self._status.degraded_reason = (
                f"metrics_degraded: {metrics.degraded_reason}"
            )
            return None

        if metrics.sample_count < MIN_SAMPLES_FOR_EVALUATION:
            logger.info(
                "样本不足 (%d < %d), 跳过评估",
                metrics.sample_count,
                MIN_SAMPLES_FOR_EVALUATION,
            )
            self._status.status = STATUS_OBSERVATION
            return None

        # 懒加载评估器
        try:
            if self._evaluator is None:
                from utils.alpha.strategy_evaluator import StrategyEvaluator

                self._evaluator = StrategyEvaluator()
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.exception("StrategyEvaluator 加载失败: %s", e)
            self._status.status = STATUS_DEGRADED
            self._status.degraded_reason = f"evaluator_import_failed: {e}"
            return None

        # 调用评估器
        try:
            report = self._evaluator.evaluate(
                daily_returns=metrics.daily_returns,
                dates=metrics.dates if metrics.dates else None,
                signal_history=signal_history or {},
            )

            # 更新状态
            self._status.last_evaluation = self._now_iso()
            self._status.total_evaluations += 1
            self._status.last_action = ACTION_EVALUATE_ONLY  # 观察期内仅评估

            # 更新观察期状态
            self._update_observation_status()

            logger.info(
                "评估完成: public=%.4f, private=%.4f, rh_risk=%.4f, recommendation=%s",
                report.public_score,
                report.private_score,
                report.reward_hacking_risk,
                report.recommendation,
            )

            return report

        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.exception("evaluate_current 失败: %s", e)
            self._status.status = STATUS_DEGRADED
            self._status.degraded_reason = f"evaluate_failed: {e}"
            return None

    def log_decision(
        self,
        report: Any | None = None,
        action: str = ACTION_EVALUATE_ONLY,
        reason: str = "",
        metrics: MetricsSnapshot | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> bool:
        """持久化决策记录到 decisions.jsonl.

        Args:
            report: ScoreReport 对象 (None=无评估, 记录 noop)
            action: 决策动作 (默认 evaluate_only, 观察期内强制)
            metrics: 指标快照 (None=不记录)
            extra_payload: 额外载荷 (可选, 合并到 evaluator_report 字段)
                用于其他模块 (如 VolRegimeWeighter) 复用审计链

        Returns:
            是否成功写入
        """
        if not self._enabled:
            logger.debug("编排器禁用, 跳过日志写入")
            return False

        # 观察期内强制 action=evaluate_only (HC-4)
        if self._status.in_observation and action not in (
            ACTION_EVALUATE_ONLY,
            ACTION_NOOP,
        ):
            logger.warning(
                "观察期内强制 action=%s → %s (HC-4)",
                action,
                ACTION_EVALUATE_ONLY,
            )
            action = ACTION_EVALUATE_ONLY

        # 构造决策记录
        record = DecisionRecord(
            timestamp=self._now_iso(),
            action=action,
            observation_day=self._status.observation_day,
            reason=reason or self._status.degraded_reason,
        )

        if report is not None:
            record.public_score = getattr(report, "public_score", 0.0)
            record.private_score = getattr(report, "private_score", 0.0)
            record.reward_hacking_risk = getattr(report, "reward_hacking_risk", 0.0)
            record.recommendation = getattr(report, "recommendation", "")
            record.sample_count = getattr(report, "sample_count", 0)

            # 完整评估报告 (用于审计)
            if hasattr(report, "to_dict"):
                record.evaluator_report = report.to_dict()

        # 额外载荷合并 (HC: 其他模块复用审计链, 如 VolRegimeWeighter)
        if extra_payload:
            if not hasattr(record, "evaluator_report") or not record.evaluator_report:
                record.evaluator_report = {}
            record.evaluator_report.update(extra_payload)

        if metrics is not None:
            record.metrics_snapshot = metrics.to_dict()

        # 写入 JSONL
        try:
            self.decisions_log_path.parent.mkdir(parents=True, exist_ok=True)

            with self.decisions_log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

            self._status.last_action = action
            logger.info(
                "决策已记录: action=%s, private=%.4f, day=%d/%d",
                action,
                record.private_score,
                record.observation_day,
                self._status.observation_total,
            )

            # ECL sink 旁路双写 (CTX-A1, flag 控制常驻注册)
            try:
                from utils.infra.ecl.sinks import iter_registered_sinks

                for sink in iter_registered_sinks():
                    if not sink.write(record.to_dict()):
                        logger.warning("ECL sink 写入失败(已降级, 不影响主流程)")
            except (ImportError, AttributeError, RuntimeError) as e:
                logger.debug("ECL sink 遍历跳过: %s", e)

            return True

        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.exception("log_decision 写入失败: %s", e)
            return False

    def run_observation_cycle(
        self,
        signal_history: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """运行一次观察期循环 (collect → evaluate → log).

        观察期内的标准入口, 封装完整流程:
        1. 收集指标 (只读)
        2. 评估当前策略 (调用 StrategyEvaluator)
        3. 记录决策 (evaluate_only)

        Args:
            signal_history: 信号历史 (可选)

        Returns:
            循环结果摘要字典
        """
        if not self._enabled:
            return {
                "status": "disabled",
                "reason": f"feature_flag_disabled ({self.feature_flag_name}=False)",
            }

        # 1. 收集指标
        metrics = self.collect_metrics()

        # 2. 评估
        report = self.evaluate_current(metrics=metrics, signal_history=signal_history)

        # 3. 记录决策
        reason = ""
        if metrics.is_degraded:
            reason = f"metrics_degraded: {metrics.degraded_reason}"
        elif report is None:
            reason = self._status.degraded_reason or "no_report"
        else:
            reason = getattr(report, "reason", "")

        self.log_decision(
            report=report,
            action=ACTION_EVALUATE_ONLY,
            reason=reason,
            metrics=metrics,
        )

        # ============================================================
        # VolRegimeWeighter 集成 (Feature Flag 控制, 不阻塞主流程)
        # ============================================================
        vol_regime_result: dict[str, Any] | None = None
        if self._is_vol_regime_enabled():
            try:
                vol_regime_result = self._run_vol_regime_weighter(metrics)
            except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
                logger.warning("VolRegimeWeighter 失败, 不阻塞主流程: %s", e)
                vol_regime_result = {"status": "error", "reason": str(e)}

        return {
            "status": self._status.status,
            "observation_day": self._status.observation_day,
            "total_evaluations": self._status.total_evaluations,
            "metrics_snapshot": metrics.to_dict(),
            "has_report": report is not None,
            "public_score": getattr(report, "public_score", 0.0) if report else 0.0,
            "private_score": getattr(report, "private_score", 0.0) if report else 0.0,
            "recommendation": getattr(report, "recommendation", "") if report else "",
            "vol_regime": vol_regime_result,
        }

    # ============================================================
    # VolRegimeWeighter 集成 helper (新增, Phase 0 只读建议)
    # ============================================================

    def _is_vol_regime_enabled(self) -> bool:
        """检查 USE_VOL_REGIME_WEIGHTER Flag."""
        try:
            from utils.infra.feature_flags import is_enabled

            return bool(is_enabled("USE_VOL_REGIME_WEIGHTER"))
        except (ImportError, AttributeError):
            return False

    def _run_vol_regime_weighter(self, metrics: Any) -> dict[str, Any]:
        """运行 VolRegimeWeighter 一次 (Phase 0 只读建议模式).

        Args:
            metrics: 当前 MetricsSnapshot (从中提取 daily_returns)

        Returns:
            VolRegimeWeighter.run_cycle 的结果字典
        """
        from utils.alpha.drawdown_reader import DrawdownReader
        from utils.alpha.vol_regime_weighter import VolRegimeWeighter

        weighter = VolRegimeWeighter()
        portfolio_snapshot = self._read_portfolio_snapshot()
        daily_returns = self._extract_daily_returns(metrics)
        vix_value = self._fetch_vix()
        # 新增: 从 shadow_state.json 读取当前回撤, 用于 sense_regime 的回撤 floor 修正
        current_drawdown = DrawdownReader().get_current_drawdown()

        return weighter.run_cycle(
            portfolio_snapshot=portfolio_snapshot,
            vix_value=vix_value,
            daily_returns=daily_returns,
            current_drawdown=current_drawdown,
            orchestrator=self,
        )

    def _read_portfolio_snapshot(self) -> dict[str, Any]:
        """只读 portfolio.yaml 返回 assets 列表 (不修改文件)."""
        try:
            from pathlib import Path

            import yaml

            portfolio_path = Path("configs/portfolio.yaml")
            if not portfolio_path.exists():
                logger.warning("portfolio.yaml 不存在: %s", portfolio_path)
                return {"assets": []}
            with portfolio_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            logger.debug("portfolio.yaml 快照已读取 (只读)")
            return data
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            logger.warning("读取 portfolio.yaml 失败: %s", e)
            return {"assets": []}

    def _extract_daily_returns(self, metrics: Any) -> list[float] | None:
        """从 MetricsSnapshot 提取 daily_returns 列表."""
        if metrics is None:
            return None
        # 尝试多种字段名
        for attr in ("daily_returns", "returns_history", "returns"):
            val = getattr(metrics, attr, None)
            if val and isinstance(val, (list, tuple)) and len(val) > 0:
                return list(val)
        return None

    def _fetch_vix(self) -> float | None:
        """获取 VIX 替代值 (EOD 强制刷新, 不用缓存).

        v8.6.14 重写:
            - 删除 wind_get_quote("VIX") 错误调用 (Wind 无 "VIX" 代码, iVIX 已停用)
            - 删除 ak.stock_zh_index_vix() 不可靠调用 (iVIX 数据为空或旧数据)
            - 改用 VixDataSource 三级降级链:
                1. shadow_state.json → realized_vol → VIX proxy
                2. Wind MCP 510050 K 线 → 波动率 → VIX proxy
                3. 缓存兜底

        Returns:
            VIX 数值 (如 25.3) 或 None (全失败时, VolRegimeWeighter 降级到 neutral)
        """
        try:
            from utils.alpha.vix_data_source import VixDataSource

            # EOD 强制刷新: use_cache=False 确保获取当日最新数据
            return VixDataSource().fetch_vix(use_cache=False)
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            logger.warning("VixDataSource 调用失败: %s", e)
            return None

    # ============================================================
    # 观察期管理
    # ============================================================

    def _update_observation_status(self) -> None:
        """更新观察期状态 (基于 daily_returns 首条日期)."""
        try:
            if not self.daily_returns_path.exists():
                self._status.in_observation = True
                self._status.observation_day = 0
                self._status.status = STATUS_OBSERVATION
                return

            # 读取首条记录的日期作为观察期起点
            first_date = self._read_first_date()
            if first_date is None:
                self._status.in_observation = True
                self._status.observation_day = 0
                self._status.status = STATUS_OBSERVATION
                return

            start_date = self.observation_start_date or first_date
            start_dt = datetime.fromisoformat(start_date)
            now = datetime.now()
            days_elapsed = (now - start_dt).days

            self._status.observation_day = max(0, days_elapsed)
            self._status.in_observation = days_elapsed < OBSERVATION_PERIOD_DAYS

            if self._status.in_observation:
                self._status.status = STATUS_OBSERVATION
            else:
                self._status.status = STATUS_ENABLED

        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("更新观察期状态失败: %s", e)
            self._status.in_observation = True
            self._status.status = STATUS_OBSERVATION

    def _read_first_date(self) -> str | None:
        """读取 daily_returns.jsonl 首条记录的日期."""
        try:
            with self.daily_returns_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    record = json.loads(line)
                    return record.get("date")
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            return None
        return None

    # ============================================================
    # 工具方法
    # ============================================================

    @staticmethod
    def _now_iso() -> str:
        """当前 UTC 时间 ISO8601 字符串."""
        return datetime.now(UTC).isoformat()

    def get_recent_decisions(self, limit: int = 10) -> list[dict[str, Any]]:
        """读取最近的决策记录 (只读).

        Args:
            limit: 最大返回条数

        Returns:
            决策记录列表 (倒序, 最新在前)
        """
        if not self.decisions_log_path.exists():
            return []

        try:
            records: list[dict[str, Any]] = []
            with self.decisions_log_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

            return records[-limit:][::-1]  # 倒序

        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:

            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.exception("get_recent_decisions 失败: %s", e)
            return []
