# -*- coding: utf-8 -*-
"""策略层根因诊断器 — 三层面自我进化 Stage 2.

模块整合 8.4 — ARCHITECTURE_三层面进化 §第2阶段
复用: DriftMonitor 告警持久化 + decisions.jsonl 评估历史 + HealthReport 策略层子指标

设计原则:
    1. Feature Flag (HC-1): USE_ROOT_CAUSE_ANALYZER 透传 (父级控制)
    2. 只读历史 (HC-4): 不触发新评估/检查, 仅读 reports/ 和 decisions.jsonl
    3. 容错降级: 文件不存在/解析失败返回空列表, 不阻塞
    4. 结构化证据: evidence 含原始告警/决策字段 (非文本)
    5. 复用修复建议: remediation_commands 复用 retrain/rollback 脚本

诊断逻辑:
    1. 漂移告警诊断 (reports/drift_alerts/*.jsonl 只读扫描):
       - 每条告警 → RootCause(category="drift_alert", severity 由告警 severity 决定)
       - evidence: {drift_score, psi, severity, feature_name, model_name, recorded_at}
       - suggested_fix: action_type=retrain, remediation_commands=["python scripts/run_retrain.py ..."]

    2. 策略评估低分诊断 (reports/evolution/decisions.jsonl 最新一条):
       - private_score < 0.3 → RootCause(category="private_score_low", severity=critical/high)
       - reward_hacking_risk > 0.5 → RootCause(category="reward_hacking_risk_high", severity=high)
       - pit_violations > 0 → RootCause(category="pit_violation", severity=critical)
       - recommendation == "rollback" → RootCause(category="strategy_rollback_recommended", severity=critical)
       - evidence: 原始决策字段 (含 timestamp, sample_count, evaluator_report)

    3. 从 HealthReport 策略层子指标补充诊断:
       - drift_health < 0.5 → RootCause(category="drift_health_low")
       - observation_progress < 0.3 → RootCause(category="observation_insufficient")
       - anti_cheat < 0.5 → RootCause(category="anti_cheat_low")

用法:
    from utils.alpha.layers.strategy_diagnoser import StrategyDiagnoser
    diagnoser = StrategyDiagnoser()
    causes = diagnoser.diagnose(health_report)
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.alpha.root_cause import (  # noqa: E402
    ACTION_CONFIG_ROLLBACK,
    ACTION_MANUAL,
    ACTION_RETRAIN,
    FixSuggestion,
    LAYER_STRATEGY,
    RootCause,
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
)


# ============================================================
# 常量
# ============================================================

# 漂移告警目录 (与 DriftMonitor.alerts_dir 一致)
DEFAULT_DRIFT_ALERTS_DIR = _PROJECT_ROOT / "reports" / "drift_alerts"

# 决策日志路径 (与 StrategyHealthLayer.decisions_path 一致)
DEFAULT_DECISIONS_PATH = _PROJECT_ROOT / "reports" / "evolution" / "decisions.jsonl"

# 告警新鲜度窗口 (秒, 超过此窗口的告警不诊断)
DEFAULT_ALERT_RECENCY_WINDOW = 86400  # 24h

# 诊断阈值 (从 evolution.yaml 读取, 此处为默认值)
DEFAULT_PRIVATE_SCORE_LOW = 0.3  # private_score < 此值 → 根因
DEFAULT_RH_RISK_HIGH = 0.5  # reward_hacking_risk > 此值 → 根因
DEFAULT_DRIFT_HEALTH_LOW = 0.5  # drift_health < 此值 → 根因
DEFAULT_OBSERVATION_INSUFFICIENT = 0.3  # observation_progress < 此值 → 根因
DEFAULT_ANTI_CHEAT_LOW = 0.5  # anti_cheat < 此值 → 根因

# 漂移告警 severity → RootCause severity 映射
_DRIFT_SEVERITY_MAP: Dict[str, str] = {
    "critical": SEVERITY_CRITICAL,
    "high": SEVERITY_HIGH,
    "medium": SEVERITY_MEDIUM,
    "low": SEVERITY_LOW,
    "CRITICAL": SEVERITY_CRITICAL,
    "HIGH": SEVERITY_HIGH,
    "MEDIUM": SEVERITY_MEDIUM,
    "LOW": SEVERITY_LOW,
}


class StrategyDiagnoser:
    """策略层根因诊断器 (只读历史, HC-4).

    接口: diagnose(health_report) -> List[RootCause]

    Feature Flag: USE_ROOT_CAUSE_ANALYZER 透传 (父级控制, HC-1)
    配置: evolution.yaml → diagnostics.strategy (HC-5)
    """

    def __init__(
        self,
        drift_alerts_dir: Optional[Path] = None,
        decisions_path: Optional[Path] = None,
        alert_recency_window: int = DEFAULT_ALERT_RECENCY_WINDOW,
    ) -> None:
        """初始化.

        Args:
            drift_alerts_dir: 漂移告警目录. None=默认 reports/drift_alerts/.
            decisions_path: 决策日志路径. None=默认 reports/evolution/decisions.jsonl.
            alert_recency_window: 告警新鲜度窗口 (秒). 默认 86400 (24h).
        """
        self._project_root = _PROJECT_ROOT
        self.drift_alerts_dir = (
            Path(drift_alerts_dir) if drift_alerts_dir else DEFAULT_DRIFT_ALERTS_DIR
        )
        self.decisions_path = (
            Path(decisions_path) if decisions_path else DEFAULT_DECISIONS_PATH
        )
        self.alert_recency_window = int(alert_recency_window)
        # 诊断阈值 (HC-5: 从 evolution.yaml 读取, 失败用默认)
        self.thresholds = self._load_thresholds()

    # ============================================================
    # 配置加载 (HC-5)
    # ============================================================
    def _load_thresholds(self) -> Dict[str, float]:
        """从 evolution.yaml 读取诊断阈值 (HC-5)."""
        defaults = {
            "private_score_low": DEFAULT_PRIVATE_SCORE_LOW,
            "rh_risk_high": DEFAULT_RH_RISK_HIGH,
            "drift_health_low": DEFAULT_DRIFT_HEALTH_LOW,
            "observation_insufficient": DEFAULT_OBSERVATION_INSUFFICIENT,
            "anti_cheat_low": DEFAULT_ANTI_CHEAT_LOW,
        }
        try:
            from utils.config_manager import get_config
            cfg = get_config("evolution") or {}
            diag = (cfg.get("diagnostics", {}) or {}).get("strategy", {}) or {}
            if diag:
                return {
                    k: float(diag.get(k, defaults[k])) for k in defaults
                }
        except Exception as e:
            logger.warning("Strategy 诊断阈值加载失败, 用默认值: %s", e)
        return defaults

    # ============================================================
    # 核心诊断
    # ============================================================
    def diagnose(self, health_report: Any) -> List[RootCause]:
        """诊断策略层根因.

        Args:
            health_report: Stage 1 HealthReport (对象或字典)

        Returns:
            List[RootCause] 策略层根因列表
        """
        now = datetime.now(timezone.utc).isoformat()
        causes: List[RootCause] = []

        # 1. 漂移告警诊断 (只读 reports/drift_alerts/)
        causes.extend(self._diagnose_from_drift_alerts(now))

        # 2. 策略评估低分诊断 (只读 decisions.jsonl)
        causes.extend(self._diagnose_from_decisions(now))

        # 3. 从 health_report 补充诊断
        causes.extend(self._diagnose_from_health(health_report, now))

        return causes

    # ============================================================
    # 子诊断 1: 漂移告警 (reports/drift_alerts/*.jsonl)
    # ============================================================
    def _diagnose_from_drift_alerts(self, now: str) -> List[RootCause]:
        """从漂移告警持久化文件生成 RootCause (只读).

        DriftMonitor 将告警持久化为 {model_name}_{date}.jsonl,
        每行一个 dict, 字段: severity, drift_type, recorded_at, model_name, 等.
        """
        causes: List[RootCause] = []
        try:
            if not self.drift_alerts_dir.exists():
                return causes

            # 扫描最近 24h 内修改的告警文件
            cutoff_ts = datetime.now(timezone.utc).timestamp() - self.alert_recency_window
            alert_files = [
                f for f in self.drift_alerts_dir.glob("*.jsonl")
                if f.stat().st_mtime >= cutoff_ts
            ]
            if not alert_files:
                return causes

            # 收集所有告警 (去重: 同 model_name + feature_name 仅保留最新)
            seen_keys: Dict[str, Dict[str, Any]] = {}
            for f in sorted(alert_files, key=lambda x: x.stat().st_mtime, reverse=True):
                try:
                    lines = f.read_text(encoding="utf-8").strip().splitlines()
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        alert = json.loads(line)
                        if not isinstance(alert, dict):
                            continue
                        key = self._alert_dedup_key(alert)
                        if key not in seen_keys:
                            seen_keys[key] = alert
                except Exception as e:
                    logger.warning("解析漂移告警文件失败 %s: %s", f.name, e)
                    continue

            # 为每条告警生成 RootCause
            for alert in seen_keys.values():
                cause = self._build_drift_cause(alert, now)
                if cause is not None:
                    causes.append(cause)
        except Exception as e:
            logger.warning("漂移告警诊断失败 (降级为空): %s", e)
        return causes

    def _build_drift_cause(self, alert: Dict[str, Any], now: str) -> Optional[RootCause]:
        """从单条漂移告警构建 RootCause."""
        try:
            severity_str = str(alert.get("severity", "medium")).lower()
            severity = _DRIFT_SEVERITY_MAP.get(severity_str, SEVERITY_MEDIUM)
            model_name = str(alert.get("model_name", "unknown"))
            feature_name = str(alert.get("feature_name", ""))
            drift_score = float(alert.get("drift_score", 0.0))
            psi = float(alert.get("psi", 0.0))
            recorded_at = str(alert.get("recorded_at", ""))
            drift_type = str(alert.get("drift_type", ""))

            alert_id = f"strategy-drift-{model_name}-{feature_name}-{recorded_at}"
            description = (
                f"模型 {model_name} 特征 {feature_name} 发生 {severity_str} 级漂移 "
                f"(KS={drift_score:.3f}, PSI={psi:.3f})"
            )
            return RootCause(
                cause_id=alert_id,
                layer=LAYER_STRATEGY,
                category="drift_alert",
                severity=severity,
                evidence={
                    "model_name": model_name,
                    "feature_name": feature_name,
                    "drift_score": drift_score,
                    "psi": psi,
                    "severity": severity_str,
                    "drift_type": drift_type,
                    "recorded_at": recorded_at,
                    "source": "DriftMonitor",
                },
                suggested_fix=FixSuggestion(
                    action_type=ACTION_RETRAIN,
                    target_file=f"models/{model_name}",
                    description=description,
                    estimated_risk=0.8 if severity == SEVERITY_CRITICAL else 0.6,
                    requires_human_approval=True,  # HC-3: retrain 必须人工审批
                    remediation_commands=[
                        f"# 触发模型 {model_name} 重训练 (人工审批后执行)",
                        f"python scripts/run_retrain.py --model {model_name} --reason drift_alert",
                    ],
                ),
                confidence=0.85,
                detected_at=now,
            )
        except Exception as e:
            logger.warning("构建漂移根因失败 (跳过): %s", e)
            return None

    @staticmethod
    def _alert_dedup_key(alert: Dict[str, Any]) -> str:
        """告警去重键 (同 model + feature 仅保留最新)."""
        return (
            f"{alert.get('model_name', 'unknown')}:"
            f"{alert.get('feature_name', 'unknown')}:"
            f"{alert.get('severity', 'unknown')}"
        )

    # ============================================================
    # 子诊断 2: 策略评估低分 (decisions.jsonl)
    # ============================================================
    def _diagnose_from_decisions(self, now: str) -> List[RootCause]:
        """从最新决策记录诊断策略层问题 (只读 decisions.jsonl)."""
        causes: List[RootCause] = []
        try:
            decision = self._read_latest_decision()
            if decision is None:
                return causes

            timestamp = str(decision.get("timestamp", ""))
            sample_count = int(decision.get("sample_count", 0))
            private_score = float(decision.get("private_score", 0.0))
            public_score = float(decision.get("public_score", 0.0))
            rh_risk = float(decision.get("reward_hacking_risk", 0.0))
            recommendation = str(decision.get("recommendation", "")).lower()

            # evaluator_report 内嵌字段 (pit_violations 等可能在此)
            evaluator_report = decision.get("evaluator_report", {}) or {}
            if not isinstance(evaluator_report, dict):
                evaluator_report = {}
            pit_violations = int(
                evaluator_report.get("pit_violations", decision.get("pit_violations", 0))
            )
            overfit_score = float(
                evaluator_report.get("overfit_score", decision.get("overfit_score", 0.0))
            )

            # 2a. private_score 低 → 根因
            if private_score < self.thresholds["private_score_low"]:
                severity = SEVERITY_CRITICAL if private_score < 0.15 else SEVERITY_HIGH
                causes.append(RootCause(
                    cause_id=f"strategy-private_low-{timestamp}",
                    layer=LAYER_STRATEGY,
                    category="private_score_low",
                    severity=severity,
                    evidence={
                        "private_score": private_score,
                        "public_score": public_score,
                        "threshold": self.thresholds["private_score_low"],
                        "sample_count": sample_count,
                        "timestamp": timestamp,
                        "source": "decisions.jsonl",
                    },
                    suggested_fix=FixSuggestion(
                        action_type=ACTION_CONFIG_ROLLBACK,
                        description=(
                            f"Private Score 过低 ({private_score:.3f} < "
                            f"{self.thresholds['private_score_low']}), 建议回滚到上一个稳定版本"
                        ),
                        estimated_risk=0.7,
                        requires_human_approval=True,
                        remediation_commands=[
                            "# 回滚策略到上一个稳定版本 (人工审批后执行)",
                            f"python scripts/run_strategy_rollback.py --reason private_score_low --score {private_score}",
                        ],
                    ),
                    confidence=0.8,
                    detected_at=now,
                ))

            # 2b. reward_hacking_risk 高 → 根因
            if rh_risk > self.thresholds["rh_risk_high"]:
                causes.append(RootCause(
                    cause_id=f"strategy-rh_risk_high-{timestamp}",
                    layer=LAYER_STRATEGY,
                    category="reward_hacking_risk_high",
                    severity=SEVERITY_HIGH,
                    evidence={
                        "reward_hacking_risk": rh_risk,
                        "threshold": self.thresholds["rh_risk_high"],
                        "overfit_score": overfit_score,
                        "pit_violations": pit_violations,
                        "timestamp": timestamp,
                        "source": "decisions.jsonl",
                    },
                    suggested_fix=FixSuggestion(
                        action_type=ACTION_MANUAL,
                        description=(
                            f"Reward Hacking 风险偏高 ({rh_risk:.3f} > "
                            f"{self.thresholds['rh_risk_high']}), 需审查过拟合/PIT"
                        ),
                        estimated_risk=0.6,
                        requires_human_approval=True,
                        remediation_commands=[
                            "# 审查策略过拟合与未来函数",
                            "python scripts/run_pit_check.py --strict",
                            "python scripts/run_dsr_check.py",
                        ],
                    ),
                    confidence=0.75,
                    detected_at=now,
                ))

            # 2c. pit_violations > 0 → 根因 (未来函数违规是阻断性问题)
            if pit_violations > 0:
                causes.append(RootCause(
                    cause_id=f"strategy-pit_violation-{timestamp}",
                    layer=LAYER_STRATEGY,
                    category="pit_violation",
                    severity=SEVERITY_CRITICAL,
                    evidence={
                        "pit_violations": pit_violations,
                        "timestamp": timestamp,
                        "source": "decisions.jsonl.evaluator_report",
                    },
                    suggested_fix=FixSuggestion(
                        action_type=ACTION_MANUAL,
                        description=(
                            f"检测到 {pit_violations} 处 PIT 违规 (未来函数), "
                            "必须立即修复后重新评估"
                        ),
                        estimated_risk=0.9,
                        requires_human_approval=True,
                        remediation_commands=[
                            "# 定位并修复未来函数 (人工审核)",
                            "python -c \"from v8.3_institutional.src.validation.pit_checker import PITChecker; PITChecker().generate_report()\"",
                        ],
                    ),
                    confidence=0.95,
                    detected_at=now,
                ))

            # 2d. recommendation == "rollback" → 根因
            if recommendation == "rollback":
                causes.append(RootCause(
                    cause_id=f"strategy-rollback_recommended-{timestamp}",
                    layer=LAYER_STRATEGY,
                    category="strategy_rollback_recommended",
                    severity=SEVERITY_CRITICAL,
                    evidence={
                        "recommendation": recommendation,
                        "private_score": private_score,
                        "public_score": public_score,
                        "rh_risk": rh_risk,
                        "timestamp": timestamp,
                        "source": "decisions.jsonl",
                    },
                    suggested_fix=FixSuggestion(
                        action_type=ACTION_CONFIG_ROLLBACK,
                        description="策略评估器建议回滚, 需立即审查并回滚到稳定版本",
                        estimated_risk=0.8,
                        requires_human_approval=True,
                        remediation_commands=[
                            "# 立即回滚策略 (人工审批后执行)",
                            "python scripts/run_strategy_rollback.py --reason evaluator_recommended",
                        ],
                    ),
                    confidence=0.9,
                    detected_at=now,
                ))
        except Exception as e:
            logger.warning("决策记录诊断失败 (降级为空): %s", e)
        return causes

    def _read_latest_decision(self) -> Optional[Dict[str, Any]]:
        """读取 decisions.jsonl 最新一条记录 (只读)."""
        try:
            if not self.decisions_path.exists():
                return None
            lines = self.decisions_path.read_text(encoding="utf-8").strip().splitlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    if isinstance(d, dict):
                        return d
                except Exception:
                    continue
            return None
        except Exception as e:
            logger.warning("读取 decisions.jsonl 失败: %s", e)
            return None

    # ============================================================
    # 子诊断 3: 从 HealthReport 补充诊断
    # ============================================================
    def _diagnose_from_health(self, health_report: Any, now: str) -> List[RootCause]:
        """从 HealthReport 的 strategy 层子指标补充诊断 (低分指标 → 根因)."""
        causes: List[RootCause] = []
        try:
            layer_score = self._get_strategy_layer_score(health_report)
            if layer_score is None:
                return causes
            sub_metrics = getattr(layer_score, "sub_metrics", {}) or {}

            # 3a. drift_health 低 → 根因
            drift_health = float(sub_metrics.get("drift_health", 1.0))
            if drift_health < self.thresholds["drift_health_low"]:
                causes.append(RootCause(
                    cause_id=f"strategy-drift_health_low-{now}",
                    layer=LAYER_STRATEGY,
                    category="drift_health_low",
                    severity=SEVERITY_MEDIUM if drift_health >= 0.3 else SEVERITY_HIGH,
                    evidence={
                        "drift_health": drift_health,
                        "threshold": self.thresholds["drift_health_low"],
                        "source": "HealthReport.strategy_health",
                    },
                    suggested_fix=FixSuggestion(
                        action_type=ACTION_RETRAIN,
                        description=(
                            f"漂移健康度偏低 ({drift_health:.2f} < "
                            f"{self.thresholds['drift_health_low']}), 需检查模型漂移"
                        ),
                        estimated_risk=0.5,
                        requires_human_approval=True,
                        remediation_commands=[
                            "python scripts/run_drift_check.py",
                        ],
                    ),
                    confidence=0.65,
                    detected_at=now,
                ))

            # 3b. observation_progress 低 → 根因 (观察期不足, 非问题而是状态)
            obs_progress = float(sub_metrics.get("observation_progress", 1.0))
            if obs_progress < self.thresholds["observation_insufficient"]:
                causes.append(RootCause(
                    cause_id=f"strategy-observation_insufficient-{now}",
                    layer=LAYER_STRATEGY,
                    category="observation_insufficient",
                    severity=SEVERITY_LOW,  # 观察期不足是正常状态, 低严重度
                    evidence={
                        "observation_progress": obs_progress,
                        "threshold": self.thresholds["observation_insufficient"],
                        "source": "HealthReport.strategy_health",
                    },
                    suggested_fix=FixSuggestion(
                        action_type=ACTION_MANUAL,
                        description=(
                            f"观察期进度不足 ({obs_progress:.1%} < "
                            f"{self.thresholds['observation_insufficient']:.0%}), "
                            "继续观察, 暂不晋升"
                        ),
                        estimated_risk=0.2,
                        requires_human_approval=True,
                        remediation_commands=[
                            "# 继续观察期, 无需动作",
                        ],
                    ),
                    confidence=0.6,
                    detected_at=now,
                ))

            # 3c. anti_cheat 低 → 根因
            anti_cheat = float(sub_metrics.get("anti_cheat", 1.0))
            if anti_cheat < self.thresholds["anti_cheat_low"]:
                causes.append(RootCause(
                    cause_id=f"strategy-anti_cheat_low-{now}",
                    layer=LAYER_STRATEGY,
                    category="anti_cheat_low",
                    severity=SEVERITY_HIGH,
                    evidence={
                        "anti_cheat": anti_cheat,
                        "threshold": self.thresholds["anti_cheat_low"],
                        "source": "HealthReport.strategy_health",
                    },
                    suggested_fix=FixSuggestion(
                        action_type=ACTION_MANUAL,
                        description=(
                            f"反作弊分数偏低 ({anti_cheat:.2f} < "
                            f"{self.thresholds['anti_cheat_low']}), 需审查 reward hacking"
                        ),
                        estimated_risk=0.6,
                        requires_human_approval=True,
                        remediation_commands=[
                            "python scripts/run_dsr_check.py",
                            "python scripts/run_pit_check.py --strict",
                        ],
                    ),
                    confidence=0.7,
                    detected_at=now,
                ))
        except Exception as e:
            logger.warning("从 HealthReport 诊断策略层失败: %s", e)
        return causes

    # ============================================================
    # 辅助
    # ============================================================
    @staticmethod
    def _get_strategy_layer_score(health_report: Any) -> Optional[Any]:
        """从 HealthReport 提取 strategy 层 LayerScore (兼容对象/字典)."""
        try:
            if hasattr(health_report, "layer_scores"):
                return health_report.layer_scores.get("strategy")
            if isinstance(health_report, dict):
                ls = health_report.get("layer_scores", {}).get("strategy")
                if ls is not None:
                    class _Wrap:
                        def __init__(self, d: Dict[str, Any]) -> None:
                            self.sub_metrics = d.get("sub_metrics", {})
                    return _Wrap(ls)
        except Exception:
            pass
        return None
