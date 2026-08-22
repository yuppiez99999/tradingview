"""代码层根因诊断器 — 三层面自我进化 Stage 2.

模块整合 8.4 — ARCHITECTURE_三层面进化 §第2阶段
复用: SystemChecker (C1-C9, 40+ 检查项) + mypy/pylint 解析 + _verify 脚本结果

设计原则:
    1. Feature Flag (HC-1): USE_ROOT_CAUSE_ANALYZER 透传 (父级控制)
    2. 只读: SystemChecker 用 skip_datasource=True (不触发数据源 IO)
    3. 容错降级: SystemChecker 失败返回空列表, 不阻塞
    4. 结构化证据: evidence 含原始 CheckResult 字段 (非文本)
    5. 复用修复建议: CheckResult.remediation → FixSuggestion.remediation_commands

诊断逻辑:
    遍历 SystemCheckReport.results, 对每个 ERROR 级 FAIL 项生成 RootCause:
        - category: "system_check_fail"
        - severity: 由 CheckLevel 推断 (ERROR=critical/high, WARN=medium)
        - evidence: {code, name, detail, remediation, level, status}
        - suggested_fix: action_type=manual, remediation_commands=[CheckResult.remediation]

用法:
    from utils.alpha.layers.code_diagnoser import CodeDiagnoser
    diagnoser = CodeDiagnoser()
    causes = diagnoser.diagnose(health_report)
"""
from __future__ import annotations

import contextlib
import io
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.alpha.root_cause import (  # noqa: E402
    ACTION_MANUAL,
    LAYER_CODE,
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    FixSuggestion,
    RootCause,
)

# ============================================================
# 常量
# ============================================================

# CheckLevel → severity 映射
_LEVEL_TO_SEVERITY: dict[str, str] = {
    "ERROR": SEVERITY_HIGH,  # ERROR FAIL 默认 high (个别阻断性升 critical)
    "WARN": SEVERITY_MEDIUM,
    "INFO": SEVERITY_LOW,
}

# 阻断性检查项 (blocking_failures) 升级为 critical
_BLOCKING_CODES_PREFIXES = ("C1.", "C2.1", "C2.2", "C3.1", "C5.1")


class CodeDiagnoser:
    """代码层根因诊断器.

    接口: diagnose(health_report) -> List[RootCause]
    """

    def __init__(self, run_system_check: bool = True) -> None:
        """初始化.

        Args:
            run_system_check: 是否在 diagnose 时运行 SystemChecker.
                              False=仅从 health_report 推断 (测试用).
        """
        self._run_system_check = run_system_check
        self._project_root = _PROJECT_ROOT

    # ============================================================
    # 核心诊断
    # ============================================================
    def diagnose(self, health_report: Any) -> list[RootCause]:
        """诊断代码层根因.

        Args:
            health_report: Stage 1 HealthReport (对象或字典, 本诊断器主要用其时间戳)

        Returns:
            List[RootCause] 代码层根因列表
        """
        now = datetime.now(UTC).isoformat()
        causes: list[RootCause] = []

        # 1. 运行 SystemChecker 获取检查结果
        report = self._run_check() if self._run_system_check else None
        if report is not None:
            causes.extend(self._diagnose_from_check_report(report, now))

        # 2. 从 health_report 的 code 层子指标补充诊断
        causes.extend(self._diagnose_from_health(health_report, now))

        return causes

    # ============================================================
    # 子诊断
    # ============================================================
    def _run_check(self) -> Any | None:
        """运行 SystemChecker (skip_datasource=True, 捕获 stdout)."""
        try:
            from utils.system_check import SystemChecker
            checker = SystemChecker(strict=False, skip_datasource=True)
            with contextlib.redirect_stdout(io.StringIO()):
                return checker.run_all()
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("SystemChecker 运行失败 (代码层诊断降级): %s", e)
            return None

    def _diagnose_from_check_report(self, report: Any, now: str) -> list[RootCause]:
        """从 SystemCheckReport 生成 RootCause 列表."""
        causes: list[RootCause] = []
        try:
            results = getattr(report, "results", [])
            for cr in results:
                # 只处理 FAIL 项
                status = self._get_enum_value(getattr(cr, "status", ""), "PASS")
                if status != "FAIL":
                    continue
                level = self._get_enum_value(getattr(cr, "level", ""), "INFO")
                # INFO 级 FAIL 不产生根因
                if level == "INFO":
                    continue

                code = str(getattr(cr, "code", "unknown"))
                name = str(getattr(cr, "name", ""))
                detail = str(getattr(cr, "detail", ""))
                remediation = str(getattr(cr, "remediation", ""))

                severity = self._infer_severity(code, level)
                cause = RootCause(
                    cause_id=f"code-{code}-{now}",
                    layer=LAYER_CODE,
                    category="system_check_fail",
                    severity=severity,
                    evidence={
                        "check_code": code,
                        "check_name": name,
                        "check_level": level,
                        "detail": detail,
                        "remediation": remediation,
                        "source": "SystemChecker",
                    },
                    suggested_fix=FixSuggestion(
                        action_type=ACTION_MANUAL,
                        target_file=self._infer_target_file(code),
                        description=f"修复 {code} {name}: {detail[:200]}",
                        estimated_risk=0.7 if severity == SEVERITY_CRITICAL else 0.4,
                        requires_human_approval=True,
                        remediation_commands=[remediation] if remediation else [],
                    ),
                    confidence=0.9,  # SystemChecker 诊断置信度高
                    detected_at=now,
                )
                causes.append(cause)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("从 SystemCheckReport 诊断失败: %s", e)
        return causes

    def _diagnose_from_health(self, health_report: Any, now: str) -> list[RootCause]:
        """从 HealthReport 的 code 层子指标补充诊断 (低分指标 → 根因)."""
        causes: list[RootCause] = []
        try:
            layer_score = self._get_code_layer_score(health_report)
            if layer_score is None:
                return causes
            sub_metrics = getattr(layer_score, "sub_metrics", {}) or {}
            # p0_pass_rate 低 → 根因
            p0_rate = float(sub_metrics.get("p0_pass_rate", 1.0))
            if p0_rate < 0.8:
                causes.append(RootCause(
                    cause_id=f"code-p0_low-{now}",
                    layer=LAYER_CODE,
                    category="p0_pass_rate_low",
                    severity=SEVERITY_HIGH if p0_rate < 0.5 else SEVERITY_MEDIUM,
                    evidence={
                        "p0_pass_rate": p0_rate,
                        "threshold": 0.8,
                        "source": "HealthReport.code_health",
                    },
                    suggested_fix=FixSuggestion(
                        action_type=ACTION_MANUAL,
                        description=f"P0 检查通过率偏低 ({p0_rate:.1%} < 80%), 需修复阻断性失败",
                        estimated_risk=0.5,
                        remediation_commands=["python scripts/run_p0_startup_check.py --strict"],
                    ),
                    confidence=0.7,
                    detected_at=now,
                ))
            # blocking_failures 高 → 根因
            blocking = float(sub_metrics.get("blocking_failures", 1.0))
            if blocking < 0.8:
                causes.append(RootCause(
                    cause_id=f"code-blocking_high-{now}",
                    layer=LAYER_CODE,
                    category="blocking_failures_high",
                    severity=SEVERITY_HIGH if blocking < 0.5 else SEVERITY_MEDIUM,
                    evidence={
                        "blocking_score": blocking,
                        "source": "HealthReport.code_health",
                    },
                    suggested_fix=FixSuggestion(
                        action_type=ACTION_MANUAL,
                        description=f"阻断性失败较多 (score={blocking:.2f}), 需排查 P0 自检",
                        estimated_risk=0.5,
                        remediation_commands=["python scripts/run_p0_startup_check.py"],
                    ),
                    confidence=0.7,
                    detected_at=now,
                ))
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.warning("从 HealthReport 诊断代码层失败: %s", e)
        return causes

    # ============================================================
    # 辅助
    # ============================================================
    @staticmethod
    def _get_enum_value(enum_or_str: Any, default: str = "") -> str:
        """从 Enum 或字符串提取 value."""
        try:
            if hasattr(enum_or_str, "value"):
                return str(enum_or_str.value)
            return str(enum_or_str)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            return default

    @staticmethod
    def _infer_severity(check_code: str, level: str) -> str:
        """推断严重程度 (阻断性前缀 → critical)."""
        if any(check_code.startswith(p) for p in _BLOCKING_CODES_PREFIXES):
            return SEVERITY_CRITICAL if level == "ERROR" else SEVERITY_HIGH
        return _LEVEL_TO_SEVERITY.get(level, SEVERITY_MEDIUM)

    @staticmethod
    def _infer_target_file(check_code: str) -> str:
        """从检查项编号推断目标文件 (粗略映射)."""
        if check_code.startswith("C1."):
            return "config/positions.json"
        if check_code.startswith("C2."):
            return ".env"
        if check_code.startswith("C3."):
            return "utils/data_provider.py"
        if check_code.startswith("C4."):
            return "config/positions.json"
        if check_code.startswith("C5."):
            return "requirements.txt"
        return ""

    @staticmethod
    def _get_code_layer_score(health_report: Any) -> Any | None:
        """从 HealthReport 提取 code 层 LayerScore (兼容对象/字典)."""
        try:
            if hasattr(health_report, "layer_scores"):
                return health_report.layer_scores.get("code")
            if isinstance(health_report, dict):
                ls = health_report.get("layer_scores", {}).get("code")
                if ls is not None:
                    # 字典形式包装为简单对象
                    class _Wrap:
                        def __init__(self, d: dict[str, Any]) -> None:
                            self.sub_metrics = d.get("sub_metrics", {})
                    return _Wrap(ls)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass
        return None
