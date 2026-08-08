"""运维层根因诊断器 — 三层面自我进化 Stage 2.

模块整合 8.4 — ARCHITECTURE_三层面进化 §第2阶段
复用: reports/ 各子目录只读聚合 (system_check/data_quality/flag_audit/risk_bus_audit)
       + HealthReport.ops 层子指标

设计原则:
    1. Feature Flag (HC-1): USE_ROOT_CAUSE_ANALYZER 透传 (父级控制)
    2. 只读聚合 (HC-4): 仅读取 reports/ 下的归档文件, 不运行新检查
    3. 容错降级: 目录不存在/解析失败返回空列表, 不阻塞
    4. 结构化证据: evidence 含原始归档字段 (非文本)
    5. 复用修复建议: remediation_commands 复用 SystemChecker.remediation

诊断逻辑:
    1. 数据源连通性诊断 (reports/system_check/ 最新归档 C3 项):
       - C3.* FAIL → RootCause(category="datasource_fail", severity 由 level 决定)
       - evidence: {check_code, check_name, detail, remediation, archive_file}

    2. 数据质量报告诊断 (reports/data_quality/):
       - 报告过期 (24h 无新报告) → RootCause(category="data_quality_stale")
       - 报告存在但分数低 → RootCause(category="data_quality_low")

    3. 漂移告警新鲜度诊断 (reports/drift_alerts/):
       - 24h 内有新告警 → RootCause(category="drift_alert_recent", severity=medium)
       - (注意: 策略层诊断器已诊断漂移本身, 运维层诊断"告警运维状态")

    4. Flag 变更稳定性诊断 (reports/flag_audit/):
       - 7 天内 Flag 变更频繁 → RootCause(category="flag_instability")

    5. 风控事件爆发诊断 (reports/risk_bus_audit/):
       - 7 天内风控事件爆发 → RootCause(category="risk_event_burst")

    6. 从 HealthReport 补充诊断 (ops 层子指标低分):
       - datasource_redundancy 低 → RootCause(category="datasource_redundancy_low")
       - data_quality 低 → RootCause(category="data_quality_low")
       - drift_alert_recency 低 → RootCause(category="drift_alert_recency_high")
       - flag_stability 低 → RootCause(category="flag_stability_low")
       - risk_event_rate 低 → RootCause(category="risk_event_rate_high")

用法:
    from utils.alpha.layers.ops_diagnoser import OpsDiagnoser
    diagnoser = OpsDiagnoser()
    causes = diagnoser.diagnose(health_report)
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.alpha.root_cause import (  # noqa: E402
    ACTION_DATASOURCE_SWITCH,
    ACTION_MANUAL,
    LAYER_OPS,
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_MEDIUM,
    FixSuggestion,
    RootCause,
)

# ============================================================
# 常量
# ============================================================

# 运维报告目录 (与 OpsHealthLayer.report_dirs 一致)
DEFAULT_REPORT_DIRS: dict[str, Path] = {
    "system_check": _PROJECT_ROOT / "reports" / "system_check",
    "data_quality": _PROJECT_ROOT / "reports" / "data_quality",
    "drift_alerts": _PROJECT_ROOT / "reports" / "drift_alerts",
    "flag_audit": _PROJECT_ROOT / "reports" / "flag_audit",
    "risk_bus": _PROJECT_ROOT / "reports" / "risk_bus_audit",
}

# 新鲜度窗口 (秒)
DEFAULT_DATA_QUALITY_STALE_WINDOW = 86400  # 24h 无新报告 → stale
DEFAULT_ALERT_RECENCY_WINDOW = 86400  # 24h 内告警 → recent
DEFAULT_FLAG_AUDIT_WINDOW = 604800  # 7 天内变更 → instability
DEFAULT_RISK_EVENT_WINDOW = 604800  # 7 天内事件 → burst

# 诊断阈值 (从 evolution.yaml 读取, 此处为默认值)
DEFAULT_DATASOURCE_REDUNDANCY_LOW = 0.5
DEFAULT_DATA_QUALITY_LOW = 0.5
DEFAULT_DRIFT_RECENCY_HIGH = 0.4  # recency 分数 < 此值 (告警越新扣分越多)
DEFAULT_FLAG_STABILITY_LOW = 0.5
DEFAULT_RISK_EVENT_RATE_HIGH = 0.5

# 数据源检查项编号前缀 (C3 系列)
DATASOURCE_CHECK_PREFIX = "C3"

# 数据源 → 目标文件映射 (复用 CodeDiagnoser._infer_target_file 思路)
_DATASOURCE_TARGET_MAP: dict[str, str] = {
    "C3.1": "utils/data_provider.py",
    "C3.2": "utils/alpha/llm_router.py",
    "C3.3": "11_量化策略/ifind_client.py",
    "C3.4": "quant_modules/tdx_connector.py",
    "C3.5": "量化策略系统 v5.9.py",
}


class OpsDiagnoser:
    """运维层根因诊断器 (只读聚合 reports/, HC-4).

    接口: diagnose(health_report) -> List[RootCause]

    Feature Flag: USE_ROOT_CAUSE_ANALYZER 透传 (父级控制, HC-1)
    配置: evolution.yaml → diagnostics.ops (HC-5)
    """

    def __init__(
        self,
        report_dirs: dict[str, Path] | None = None,
    ) -> None:
        """初始化.

        Args:
            report_dirs: 报告目录映射. None=默认 DEFAULT_REPORT_DIRS.
        """
        self._project_root = _PROJECT_ROOT
        self.report_dirs = dict(DEFAULT_REPORT_DIRS)
        if report_dirs:
            self.report_dirs.update(report_dirs)
        # 诊断阈值 (HC-5: 从 evolution.yaml 读取, 失败用默认)
        self.thresholds = self._load_thresholds()

    # HealthReport ops 层子指标 → RootCause 的诊断规则表
    # 新增规则只需追加一项, 无需复制粘贴 RootCause 构造模板
    _OPS_HEALTH_RULES: list[dict[str, Any]] = [
        {
            "metric_key": "datasource_redundancy",
            "threshold_key": "datasource_redundancy_low",
            "category": "datasource_redundancy_low",
            "cause_id_prefix": "ops-datasource_redundancy_low",
            "evidence_keys": {"metric": "datasource_redundancy"},
            "severity_hi_threshold": 0.3,
            "action_type": ACTION_DATASOURCE_SWITCH,
            "description_tpl": "数据源冗余度偏低 ({val:.2f} < {threshold}), 需增加备用数据源",
            "estimated_risk": 0.5,
            "remediation_commands": [
                "# 检查数据源连通性并启用备用源",
                "python scripts/run_p0_startup_check.py --skip-datasource",
            ],
            "confidence": 0.7,
        },
        {
            "metric_key": "data_quality",
            "threshold_key": "data_quality_low",
            "category": "data_quality_low",
            "cause_id_prefix": "ops-data_quality_low",
            "evidence_keys": {"metric": "data_quality_score"},
            "severity_hi_threshold": None,
            "action_type": ACTION_MANUAL,
            "description_tpl": "数据质量分数偏低 ({val:.2f} < {threshold})",
            "estimated_risk": 0.4,
            "remediation_commands": [
                "python scripts/run_data_quality_check.py",
            ],
            "confidence": 0.65,
        },
        {
            "metric_key": "drift_alert_recency",
            "threshold_key": "drift_recency_high",
            "category": "drift_alert_recency_high",
            "cause_id_prefix": "ops-drift_alert_recency_high",
            "evidence_keys": {"metric": "drift_alert_recency_score"},
            "severity_hi_threshold": None,
            "action_type": ACTION_MANUAL,
            "description_tpl": "24h 内有新漂移告警, 需检查模型运维状态",
            "estimated_risk": 0.5,
            "remediation_commands": [
                "python scripts/run_drift_check.py --summary",
            ],
            "confidence": 0.7,
        },
        {
            "metric_key": "flag_stability",
            "threshold_key": "flag_stability_low",
            "category": "flag_stability_low",
            "cause_id_prefix": "ops-flag_stability_low",
            "evidence_keys": {"metric": "flag_stability"},
            "severity_hi_threshold": None,
            "action_type": ACTION_MANUAL,
            "description_tpl": "Flag 稳定性偏低 ({val:.2f} < {threshold}), 频繁变更影响稳定",
            "estimated_risk": 0.4,
            "remediation_commands": [
                "python scripts/run_flag_audit.py --summary --days 7",
            ],
            "confidence": 0.65,
        },
        {
            "metric_key": "risk_event_rate",
            "threshold_key": "risk_event_rate_high",
            "category": "risk_event_rate_high",
            "cause_id_prefix": "ops-risk_event_rate_high",
            "evidence_keys": {"metric": "risk_event_rate"},
            "severity_hi_threshold": 0.3,
            "action_type": ACTION_MANUAL,
            "description_tpl": "风控事件频率偏高 ({val:.2f} < {threshold}), 需审查风控规则",
            "estimated_risk": 0.7,
            "remediation_commands": [
                "python scripts/run_risk_audit.py --summary --days 7",
            ],
            "confidence": 0.75,
        },
    ]

    # ============================================================
    # 配置加载 (HC-5)
    # ============================================================
    def _load_thresholds(self) -> dict[str, float]:
        """从 evolution.yaml 读取诊断阈值 (HC-5)."""
        defaults = {
            "datasource_redundancy_low": DEFAULT_DATASOURCE_REDUNDANCY_LOW,
            "data_quality_low": DEFAULT_DATA_QUALITY_LOW,
            "drift_recency_high": DEFAULT_DRIFT_RECENCY_HIGH,
            "flag_stability_low": DEFAULT_FLAG_STABILITY_LOW,
            "risk_event_rate_high": DEFAULT_RISK_EVENT_RATE_HIGH,
        }
        try:
            from utils.config_manager import get_config
            cfg = get_config("evolution") or {}
            diag = (cfg.get("diagnostics", {}) or {}).get("ops", {}) or {}
            if diag:
                return {
                    k: float(diag.get(k, defaults[k])) for k in defaults
                }
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("Ops 诊断阈值加载失败, 用默认值: %s", e)
        return defaults

    # ============================================================
    # 核心诊断
    # ============================================================
    def diagnose(self, health_report: Any) -> list[RootCause]:
        """诊断运维层根因.

        Args:
            health_report: Stage 1 HealthReport (对象或字典)

        Returns:
            List[RootCause] 运维层根因列表
        """
        now = datetime.now(timezone.utc).isoformat()
        causes: list[RootCause] = []

        # 1. 数据源连通性诊断 (reports/system_check/ 最新归档 C3 项)
        causes.extend(self._diagnose_datasource_failures(now))

        # 2. 数据质量报告诊断 (reports/data_quality/)
        causes.extend(self._diagnose_data_quality(now))

        # 3. 漂移告警新鲜度诊断 (reports/drift_alerts/)
        causes.extend(self._diagnose_drift_alert_recency(now))

        # 4. Flag 变更稳定性诊断 (reports/flag_audit/)
        causes.extend(self._diagnose_flag_instability(now))

        # 5. 风控事件爆发诊断 (reports/risk_bus_audit/)
        causes.extend(self._diagnose_risk_event_burst(now))

        # 6. 从 health_report 补充诊断
        causes.extend(self._diagnose_from_health(health_report, now))

        # 7. 自检归档回归诊断 (PASS→FAIL 回归点, 2.7 增强工具)
        causes.extend(self._diagnose_regressions(now))

        return causes

    # ============================================================
    # 子诊断 7: 自检归档回归 (reports/system_check/ 对比, 2.7)
    # ============================================================
    def _diagnose_regressions(self, now: str) -> list[RootCause]:
        """对比最新 vs 1天前的自检归档, 识别 PASS→FAIL 回归点 (只读, HC-4).

        复用 SystemCheckDiff 工具 (2.7), 回归项比"当前失败"更具诊断价值:
        "昨天 PASS 今天 FAIL" 明确指向近期变更引入的问题.
        """
        causes: list[RootCause] = []
        try:
            sc_dir = self.report_dirs.get("system_check")
            if not sc_dir or not sc_dir.exists():
                return causes
            from utils.alpha.layers.system_check_diff import SystemCheckDiff
            differ = SystemCheckDiff()
            diff = differ.diff_recent(sc_dir, days_ago=1)
            if not diff.has_regressions:
                return causes
            # 复用 diff 工具的 to_root_causes 转换 (仅回归 + 新失败)
            causes = differ.to_root_causes(diff, now=now)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("自检归档回归诊断失败 (降级为空): %s", e)
        return causes

    # ============================================================
    # 子诊断 1: 数据源连通性 (reports/system_check/ 最新归档)
    # ============================================================
    def _diagnose_datasource_failures(self, now: str) -> list[RootCause]:
        """从最新 system_check 归档提取 C3 数据源失败项 (只读)."""
        causes: list[RootCause] = []
        try:
            sc_dir = self.report_dirs.get("system_check")
            if not sc_dir or not sc_dir.exists():
                return causes

            # 找最新归档
            archive_files = sorted(sc_dir.glob("system_check_*.json"), reverse=True)
            if not archive_files:
                archive_files = sorted(sc_dir.glob("*.json"), reverse=True)
            if not archive_files:
                return causes

            latest_file = archive_files[0]
            try:
                archive = json.loads(latest_file.read_text(encoding="utf-8"))
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                logger.warning("解析 system_check 归档失败 %s: %s", latest_file.name, e)
                return causes

            results = archive.get("results", []) if isinstance(archive, dict) else []
            for r in results:
                if not isinstance(r, dict):
                    continue
                code = str(r.get("code", ""))
                if not code.startswith(DATASOURCE_CHECK_PREFIX):
                    continue
                status = str(r.get("status", "")).upper()
                if status != "FAIL":
                    continue
                level = str(r.get("level", "")).upper()
                # INFO 级 FAIL 不产生根因
                if level == "INFO":
                    continue

                name = str(r.get("name", ""))
                detail = str(r.get("detail", ""))
                remediation = str(r.get("remediation", ""))
                severity = SEVERITY_CRITICAL if level == "ERROR" else SEVERITY_HIGH

                causes.append(RootCause(
                    cause_id=f"ops-datasource_fail-{code}-{latest_file.stem}",
                    layer=LAYER_OPS,
                    category="datasource_fail",
                    severity=severity,
                    evidence={
                        "check_code": code,
                        "check_name": name,
                        "check_level": level,
                        "detail": detail,
                        "remediation": remediation,
                        "archive_file": latest_file.name,
                        "source": "system_check_archive",
                    },
                    suggested_fix=FixSuggestion(
                        action_type=ACTION_DATASOURCE_SWITCH,
                        target_file=_DATASOURCE_TARGET_MAP.get(code, ""),
                        description=f"数据源 {code} {name} 失败: {detail[:200]}",
                        estimated_risk=0.6,
                        requires_human_approval=True,
                        remediation_commands=[remediation] if remediation else [],
                    ),
                    confidence=0.9,
                    detected_at=now,
                ))
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("数据源连通性诊断失败 (降级为空): %s", e)
        return causes

    # ============================================================
    # 子诊断 2: 数据质量报告 (reports/data_quality/)
    # ============================================================
    def _diagnose_data_quality(self, now: str) -> list[RootCause]:
        """诊断数据质量报告新鲜度 (只读)."""
        causes: list[RootCause] = []
        try:
            dq_dir = self.report_dirs.get("data_quality")
            if not dq_dir or not dq_dir.exists():
                # 目录不存在 → 数据质量监控未就绪
                causes.append(RootCause(
                    cause_id=f"ops-data_quality_no_monitoring-{now}",
                    layer=LAYER_OPS,
                    category="data_quality_no_monitoring",
                    severity=SEVERITY_MEDIUM,
                    evidence={
                        "report_dir": str(dq_dir) if dq_dir else "",
                        "source": "data_quality_dir",
                    },
                    suggested_fix=FixSuggestion(
                        action_type=ACTION_MANUAL,
                        description="数据质量监控目录不存在, 需部署数据质量报告生成器",
                        estimated_risk=0.4,
                        requires_human_approval=True,
                        remediation_commands=[
                            "# 部署数据质量监控",
                            "python scripts/run_data_quality_check.py --install",
                        ],
                    ),
                    confidence=0.7,
                    detected_at=now,
                ))
                return causes

            # 检查最新报告新鲜度
            files = list(dq_dir.glob("*.json")) + list(dq_dir.glob("*.md"))
            if not files:
                causes.append(RootCause(
                    cause_id=f"ops-data_quality_stale-{now}",
                    layer=LAYER_OPS,
                    category="data_quality_stale",
                    severity=SEVERITY_MEDIUM,
                    evidence={
                        "report_dir": str(dq_dir),
                        "file_count": 0,
                        "source": "data_quality_dir",
                    },
                    suggested_fix=FixSuggestion(
                        action_type=ACTION_MANUAL,
                        description="数据质量报告目录为空, 需运行数据质量检查",
                        estimated_risk=0.4,
                        requires_human_approval=True,
                        remediation_commands=[
                            "python scripts/run_data_quality_check.py",
                        ],
                    ),
                    confidence=0.7,
                    detected_at=now,
                ))
                return causes

            # 检查最新文件新鲜度
            latest_file = max(files, key=lambda f: f.stat().st_mtime)
            age_seconds = datetime.now(timezone.utc).timestamp() - latest_file.stat().st_mtime
            if age_seconds > DEFAULT_DATA_QUALITY_STALE_WINDOW:
                causes.append(RootCause(
                    cause_id=f"ops-data_quality_stale-{latest_file.name}",
                    layer=LAYER_OPS,
                    category="data_quality_stale",
                    severity=SEVERITY_MEDIUM,
                    evidence={
                        "latest_file": latest_file.name,
                        "age_seconds": int(age_seconds),
                        "stale_threshold": DEFAULT_DATA_QUALITY_STALE_WINDOW,
                        "source": "data_quality_dir",
                    },
                    suggested_fix=FixSuggestion(
                        action_type=ACTION_MANUAL,
                        description=(
                            f"数据质量报告已过期 ({age_seconds/3600:.1f}h 未更新), "
                            "需重新运行数据质量检查"
                        ),
                        estimated_risk=0.4,
                        requires_human_approval=True,
                        remediation_commands=[
                            "python scripts/run_data_quality_check.py",
                        ],
                    ),
                    confidence=0.75,
                    detected_at=now,
                ))
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("数据质量诊断失败 (降级为空): %s", e)
        return causes

    # ============================================================
    # 子诊断 3: 漂移告警新鲜度 (reports/drift_alerts/)
    # ============================================================
    def _diagnose_drift_alert_recency(self, now: str) -> list[RootCause]:
        """诊断漂移告警新鲜度 (24h 内有告警 → 运维状态根因).

        注意: 策略层诊断器已诊断漂移本身的根因 (category=drift_alert),
        运维层诊断"告警运维状态" (category=drift_alert_recent).
        """
        causes: list[RootCause] = []
        try:
            da_dir = self.report_dirs.get("drift_alerts")
            if not da_dir or not da_dir.exists():
                return causes  # 无告警目录 = 无漂移 = 无根因

            files = list(da_dir.glob("*.json")) + list(da_dir.glob("*.jsonl"))
            if not files:
                return causes  # 无告警文件 = 无漂移

            cutoff_ts = datetime.now(timezone.utc).timestamp() - DEFAULT_ALERT_RECENCY_WINDOW
            recent_files = [f for f in files if f.stat().st_mtime >= cutoff_ts]
            if not recent_files:
                return causes  # 无 24h 内告警

            # 统计 24h 内告警数
            alert_count = 0
            for f in recent_files:
                try:
                    lines = f.read_text(encoding="utf-8").strip().splitlines()
                    alert_count += sum(1 for line in lines if line.strip())
                except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
                    # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                    continue

            if alert_count > 0:
                severity = SEVERITY_HIGH if alert_count > 5 else SEVERITY_MEDIUM
                causes.append(RootCause(
                    cause_id=f"ops-drift_alert_recent-{now}",
                    layer=LAYER_OPS,
                    category="drift_alert_recent",
                    severity=severity,
                    evidence={
                        "alert_count_24h": alert_count,
                        "recent_file_count": len(recent_files),
                        "recency_window": DEFAULT_ALERT_RECENCY_WINDOW,
                        "source": "drift_alerts_dir",
                    },
                    suggested_fix=FixSuggestion(
                        action_type=ACTION_MANUAL,
                        description=(
                            f"24h 内有 {alert_count} 条漂移告警, 需检查模型运维状态"
                        ),
                        estimated_risk=0.5,
                        requires_human_approval=True,
                        remediation_commands=[
                            "# 检查漂移告警详情并评估是否需重训练",
                            "python scripts/run_drift_check.py --summary",
                        ],
                    ),
                    confidence=0.8,
                    detected_at=now,
                ))
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("漂移告警新鲜度诊断失败 (降级为空): %s", e)
        return causes

    # ============================================================
    # 子诊断 4: Flag 变更稳定性 (reports/flag_audit/)
    # ============================================================
    def _diagnose_flag_instability(self, now: str) -> list[RootCause]:
        """诊断 Flag 变更稳定性 (7 天内变更频繁 → 根因)."""
        causes: list[RootCause] = []
        try:
            fa_dir = self.report_dirs.get("flag_audit")
            if not fa_dir or not fa_dir.exists():
                return causes  # 无审计目录 = 无变更 = 无根因

            files = list(fa_dir.glob("*.json")) + list(fa_dir.glob("*.jsonl"))
            now_ts = datetime.now(timezone.utc).timestamp()
            recent_changes = sum(
                1 for f in files
                if (now_ts - f.stat().st_mtime) < DEFAULT_FLAG_AUDIT_WINDOW
            )

            # 7 天内变更 > 5 次 → 根因
            if recent_changes > 5:
                severity = SEVERITY_HIGH if recent_changes > 10 else SEVERITY_MEDIUM
                causes.append(RootCause(
                    cause_id=f"ops-flag_instability-{now}",
                    layer=LAYER_OPS,
                    category="flag_instability",
                    severity=severity,
                    evidence={
                        "recent_changes_7d": recent_changes,
                        "audit_window": DEFAULT_FLAG_AUDIT_WINDOW,
                        "source": "flag_audit_dir",
                    },
                    suggested_fix=FixSuggestion(
                        action_type=ACTION_MANUAL,
                        description=(
                            f"7 天内 Flag 变更 {recent_changes} 次, 频繁变更可能影响系统稳定性, "
                            "需审查变更必要性"
                        ),
                        estimated_risk=0.5,
                        requires_human_approval=True,
                        remediation_commands=[
                            "# 审查 Flag 变更历史",
                            "python scripts/run_flag_audit.py --summary --days 7",
                        ],
                    ),
                    confidence=0.75,
                    detected_at=now,
                ))
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("Flag 变更稳定性诊断失败 (降级为空): %s", e)
        return causes

    # ============================================================
    # 子诊断 5: 风控事件爆发 (reports/risk_bus_audit/)
    # ============================================================
    def _diagnose_risk_event_burst(self, now: str) -> list[RootCause]:
        """诊断风控事件爆发 (7 天内事件频繁 → 根因)."""
        causes: list[RootCause] = []
        try:
            rb_dir = self.report_dirs.get("risk_bus")
            if not rb_dir or not rb_dir.exists():
                return causes  # 无事件目录 = 无事件 = 无根因

            files = list(rb_dir.glob("*.json")) + list(rb_dir.glob("*.jsonl"))
            now_ts = datetime.now(timezone.utc).timestamp()
            recent_events = sum(
                1 for f in files
                if (now_ts - f.stat().st_mtime) < DEFAULT_RISK_EVENT_WINDOW
            )

            # 7 天内事件 > 5 次 → 根因
            if recent_events > 5:
                severity = SEVERITY_CRITICAL if recent_events > 15 else SEVERITY_HIGH
                causes.append(RootCause(
                    cause_id=f"ops-risk_event_burst-{now}",
                    layer=LAYER_OPS,
                    category="risk_event_burst",
                    severity=severity,
                    evidence={
                        "recent_events_7d": recent_events,
                        "event_window": DEFAULT_RISK_EVENT_WINDOW,
                        "source": "risk_bus_audit_dir",
                    },
                    suggested_fix=FixSuggestion(
                        action_type=ACTION_MANUAL,
                        description=(
                            f"7 天内风控事件 {recent_events} 次, 需立即审查风控规则与持仓"
                        ),
                        estimated_risk=0.8,
                        requires_human_approval=True,
                        remediation_commands=[
                            "# 立即审查风控事件 (人工审核)",
                            "python scripts/run_risk_audit.py --summary --days 7",
                        ],
                    ),
                    confidence=0.85,
                    detected_at=now,
                ))
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("风控事件爆发诊断失败 (降级为空): %s", e)
        return causes

    # ============================================================
    # 子诊断 6: 从 HealthReport 补充诊断
    # ============================================================
    def _diagnose_from_health(self, health_report: Any, now: str) -> list[RootCause]:
        """从 HealthReport 的 ops 层子指标补充诊断 (低分指标 → 根因).

        规则定义见 ``_OPS_HEALTH_RULES``, 新增规则只需追加配置项。
        """
        causes: list[RootCause] = []
        try:
            layer_score = self._get_ops_layer_score(health_report)
            if layer_score is None:
                return causes
            sub_metrics = getattr(layer_score, "sub_metrics", {}) or {}

            for rule in self._OPS_HEALTH_RULES:
                val = float(sub_metrics.get(rule["metric_key"], 1.0))
                threshold = self.thresholds[rule["threshold_key"]]
                if val >= threshold:
                    continue

                sev_hi = rule.get("severity_hi_threshold")
                if sev_hi is not None and val < sev_hi:
                    severity = SEVERITY_HIGH
                else:
                    severity = SEVERITY_MEDIUM

                desc = rule["description_tpl"].format(val=val, threshold=threshold)

                causes.append(RootCause(
                    cause_id=f"{rule['cause_id_prefix']}-{now}",
                    layer=LAYER_OPS,
                    category=rule["category"],
                    severity=severity,
                    evidence={
                        rule["evidence_keys"]["metric"]: val,
                        "threshold": threshold,
                        "source": "HealthReport.ops_health",
                    },
                    suggested_fix=FixSuggestion(
                        action_type=rule["action_type"],
                        description=desc,
                        estimated_risk=rule["estimated_risk"],
                        requires_human_approval=True,
                        remediation_commands=list(rule["remediation_commands"]),
                    ),
                    confidence=rule["confidence"],
                    detected_at=now,
                ))
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("从 HealthReport 诊断运维层失败: %s", e)
        return causes

    # ============================================================
    # 辅助
    # ============================================================
    @staticmethod
    def _get_ops_layer_score(health_report: Any) -> Any | None:
        """从 HealthReport 提取 ops 层 LayerScore (兼容对象/字典)."""
        try:
            if hasattr(health_report, "layer_scores"):
                return health_report.layer_scores.get("ops")
            if isinstance(health_report, dict):
                ls = health_report.get("layer_scores", {}).get("ops")
                if ls is not None:
                    class _Wrap:
                        def __init__(self, d: dict[str, Any]) -> None:
                            self.sub_metrics = d.get("sub_metrics", {})
                    return _Wrap(ls)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass
        return None
