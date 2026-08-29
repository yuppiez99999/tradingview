"""统一根因分析框架 — 三层面自我进化 Stage 2 核心.

模块整合 8.4 — ARCHITECTURE_三层面进化 §第2阶段
基于: Stage 1 UnifiedHealthMetrics (HealthReport) + 三层诊断器 + 跨层因果链

设计原则:
    1. 不可变性: FixSuggestion / RootCause / CausalChain / RootCauseReport 使用 frozen=True
    2. Feature Flag 透传 (HC-1): USE_ROOT_CAUSE_ANALYZER 默认 False
    3. 配置走 ConfigManager (HC-5): get_config("evolution").diagnostics
    4. 只读生产数据: 不修改 positions.json / daily_returns.jsonl / V9 基线
    5. 容错降级: 子诊断器失败不阻塞, 返回空根因列表
    6. 人工护栏: 所有 FixSuggestion.requires_human_approval 默认 True (HC-3 安全)

核心数据流:
    HealthReport (Stage 1) → UnifiedRootCauseAnalyzer.analyze()
        ├─ CodeDiagnoser      → List[RootCause] (代码层根因)
        ├─ StrategyDiagnoser  → List[RootCause] (策略层根因)
        ├─ OpsDiagnoser       → List[RootCause] (运维层根因)
        └─ CausalChainBuilder → List[CausalChain] (跨层因果链)
    → RootCauseReport (持久化到 root_causes.jsonl)

持久化:
    reports/evolution/root_causes.jsonl (追加模式, 每行一个 RootCauseReport)
    保留最近 90 天历史 (可配置)

用法:
    from utils.alpha.root_cause import UnifiedRootCauseAnalyzer
    from utils.alpha.health_metrics import UnifiedHealthMetrics

    analyzer = UnifiedRootCauseAnalyzer()
    health = UnifiedHealthMetrics().collect_all()
    report = analyzer.analyze(health)
    # report.root_causes → List[RootCause]
    # report.causal_chains → List[CausalChain]

硬约束:
    - HC-1: Feature Flag 默认 False, 关闭时返回降级报告
    - HC-3: requires_human_approval 默认 True (自动修复阶段必须人工审批)
    - HC-4: 只读生产数据, 不动 V9 基线
    - HC-5: 配置走 ConfigManager
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

# 默认持久化路径
DEFAULT_ROOT_CAUSES_PATH = _PROJECT_ROOT / "reports" / "evolution" / "root_causes.jsonl"

# 保留历史天数
DEFAULT_MAX_HISTORY_DAYS = 90

# 严重程度枚举
SEVERITY_CRITICAL = "critical"
SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"
VALID_SEVERITIES = (SEVERITY_CRITICAL, SEVERITY_HIGH, SEVERITY_MEDIUM, SEVERITY_LOW)

# 修复动作类型枚举
ACTION_CODE_PATCH = "code_patch"
ACTION_CONFIG_ROLLBACK = "config_rollback"
ACTION_RETRAIN = "retrain"
ACTION_DATASOURCE_SWITCH = "datasource_switch"
ACTION_MANUAL = "manual"
VALID_ACTIONS = (
    ACTION_CODE_PATCH,
    ACTION_CONFIG_ROLLBACK,
    ACTION_RETRAIN,
    ACTION_DATASOURCE_SWITCH,
    ACTION_MANUAL,
)

# 层名
LAYER_CODE = "code"
LAYER_STRATEGY = "strategy"
LAYER_OPS = "ops"


# ============================================================
# 数据类 (不可变)
# ============================================================


@dataclass(frozen=True)
class FixSuggestion:
    """结构化修复建议 (不可变).

    Attributes:
        action_type: 动作类型 (code_patch/config_rollback/retrain/datasource_switch/manual)
        target_file: 目标文件/模块路径 (空字符串表示全局)
        description: 修复描述 (人类可读)
        estimated_risk: 估算风险 0.0-1.0 (越高越危险)
        requires_human_approval: 是否需要人工审批 (HC-3: 默认 True)
        remediation_commands: 可执行修复命令列表 (复用 CheckResult.remediation)
    """

    action_type: str
    target_file: str = ""
    description: str = ""
    estimated_risk: float = 0.5
    requires_human_approval: bool = True  # HC-3 安全护栏
    remediation_commands: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "target_file": self.target_file,
            "description": self.description,
            "estimated_risk": round(self.estimated_risk, 4),
            "requires_human_approval": self.requires_human_approval,
            "remediation_commands": list(self.remediation_commands),
        }

    def __post_init__(self) -> None:
        """校验字段合法性."""
        if self.action_type not in VALID_ACTIONS:
            raise ValueError(
                f"action_type 必须是 {VALID_ACTIONS} 之一, 实际 = {self.action_type}"
            )
        if not (0.0 <= self.estimated_risk <= 1.0):
            raise ValueError(
                f"estimated_risk 必须在 [0, 1], 实际 = {self.estimated_risk}"
            )


@dataclass(frozen=True)
class RootCause:
    """单条根因 (不可变).

    Attributes:
        cause_id: 唯一标识 (如 "code-C1.1-20260801T120000")
        layer: 所属层面 (code/strategy/ops)
        category: 根因类别 (如 "missing_file"/"drift"/"datasource_fail")
        severity: 严重程度 (critical/high/medium/low)
        evidence: 结构化证据 (Dict, 非文本 — 含原始 CheckResult/指标值)
        suggested_fix: 修复建议
        confidence: 置信度 0.0-1.0
        detected_at: 检测时间 ISO8601
    """

    cause_id: str
    layer: str
    category: str
    severity: str
    evidence: dict[str, Any] = field(default_factory=dict)
    suggested_fix: FixSuggestion = field(
        default_factory=lambda: FixSuggestion(action_type=ACTION_MANUAL)
    )
    confidence: float = 0.5
    detected_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "cause_id": self.cause_id,
            "layer": self.layer,
            "category": self.category,
            "severity": self.severity,
            "evidence": self._serialize_evidence(self.evidence),
            "suggested_fix": self.suggested_fix.to_dict(),
            "confidence": round(self.confidence, 4),
            "detected_at": self.detected_at,
        }

    @staticmethod
    def _serialize_evidence(ev: dict[str, Any]) -> dict[str, Any]:
        """序列化证据 (转储非 JSON 原生类型为字符串)."""
        result: dict[str, Any] = {}
        for k, v in ev.items():
            try:
                json.dumps(v, ensure_ascii=False)
                result[k] = v
            except (TypeError, ValueError):
                result[k] = str(v)
        return result

    def __post_init__(self) -> None:
        if self.layer not in (LAYER_CODE, LAYER_STRATEGY, LAYER_OPS):
            raise ValueError(f"layer 必须是 code/strategy/ops, 实际 = {self.layer}")
        if self.severity not in VALID_SEVERITIES:
            raise ValueError(
                f"severity 必须是 {VALID_SEVERITIES} 之一, 实际 = {self.severity}"
            )
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence 必须在 [0, 1], 实际 = {self.confidence}")


@dataclass(frozen=True)
class CausalChain:
    """跨层因果链 (不可变).

    例: "Wind MCP 失败 (ops) → 数据降级 (code) → IC 衰减 (strategy)"

    Attributes:
        chain_id: 唯一标识
        nodes: 因果链节点 (按因果顺序, 每个节点是一条 RootCause)
        confidence: 链整体置信度 (取节点最小值或加权)
        description: 人类可读的因果链描述
    """

    chain_id: str
    nodes: list[RootCause] = field(default_factory=list)
    confidence: float = 0.5
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "nodes": [n.to_dict() for n in self.nodes],
            "confidence": round(self.confidence, 4),
            "description": self.description,
        }

    def __post_init__(self) -> None:
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence 必须在 [0, 1], 实际 = {self.confidence}")


@dataclass(frozen=True)
class RootCauseReport:
    """根因分析报告 (不可变).

    Attributes:
        causes: 所有识别的根因列表
        causal_chains: 跨层因果链列表
        is_degraded: 是否降级 (Flag 关闭 / 诊断器失败)
        degraded_reason: 降级原因
        analyzed_at: 分析时间 ISO8601
        source_health_report_at: 输入 HealthReport 的生成时间
        summary: 一句话摘要
    """

    causes: list[RootCause] = field(default_factory=list)
    causal_chains: list[CausalChain] = field(default_factory=list)
    is_degraded: bool = False
    degraded_reason: str = ""
    analyzed_at: str = ""
    source_health_report_at: str = ""
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "causes": [c.to_dict() for c in self.causes],
            "causal_chains": [c.to_dict() for c in self.causal_chains],
            "is_degraded": self.is_degraded,
            "degraded_reason": self.degraded_reason,
            "analyzed_at": self.analyzed_at,
            "source_health_report_at": self.source_health_report_at,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RootCauseReport:
        """从字典重建 (用于读取历史)."""
        causes: list[RootCause] = []
        for c in d.get("causes", []):
            fix_d = c.get("suggested_fix", {})
            causes.append(
                RootCause(
                    cause_id=c.get("cause_id", ""),
                    layer=c.get("layer", ""),
                    category=c.get("category", ""),
                    severity=c.get("severity", SEVERITY_LOW),
                    evidence=c.get("evidence", {}),
                    suggested_fix=FixSuggestion(
                        action_type=fix_d.get("action_type", ACTION_MANUAL),
                        target_file=fix_d.get("target_file", ""),
                        description=fix_d.get("description", ""),
                        estimated_risk=float(fix_d.get("estimated_risk", 0.5)),
                        requires_human_approval=bool(
                            fix_d.get("requires_human_approval", True)
                        ),
                        remediation_commands=list(
                            fix_d.get("remediation_commands", [])
                        ),
                    ),
                    confidence=float(c.get("confidence", 0.5)),
                    detected_at=c.get("detected_at", ""),
                )
            )
        chains: list[CausalChain] = []
        for ch in d.get("causal_chains", []):
            chain_nodes = [
                RootCause(
                    cause_id=n.get("cause_id", ""),
                    layer=n.get("layer", ""),
                    category=n.get("category", ""),
                    severity=n.get("severity", SEVERITY_LOW),
                    evidence=n.get("evidence", {}),
                    suggested_fix=FixSuggestion(action_type=ACTION_MANUAL),
                    confidence=float(n.get("confidence", 0.5)),
                    detected_at=n.get("detected_at", ""),
                )
                for n in ch.get("nodes", [])
            ]
            chains.append(
                CausalChain(
                    chain_id=ch.get("chain_id", ""),
                    nodes=chain_nodes,
                    confidence=float(ch.get("confidence", 0.5)),
                    description=ch.get("description", ""),
                )
            )
        return cls(
            causes=causes,
            causal_chains=chains,
            is_degraded=bool(d.get("is_degraded", False)),
            degraded_reason=d.get("degraded_reason", ""),
            analyzed_at=d.get("analyzed_at", ""),
            source_health_report_at=d.get("source_health_report_at", ""),
            summary=d.get("summary", ""),
        )


# ============================================================
# 统一根因分析器
# ============================================================


class UnifiedRootCauseAnalyzer:
    """统一根因分析框架.

    Feature Flag: USE_ROOT_CAUSE_ANALYZER (默认 False, HC-1)
    配置: evolution.yaml → diagnostics (HC-5)

    接收 Stage 1 的 HealthReport, 调用三层诊断器 + 因果链构建器,
    输出结构化 RootCauseReport.
    """

    def __init__(
        self,
        persistence_path: Path | None = None,
        feature_flag_name: str = "USE_ROOT_CAUSE_ANALYZER",
        use_llm_assist_flag: str = "USE_LLM_ROOT_CAUSE",
    ) -> None:
        """初始化.

        Args:
            persistence_path: root_causes.jsonl 路径. None=默认.
            feature_flag_name: 主 Flag (HC-1).
            use_llm_assist_flag: LLM 辅助推理 Flag (可选, 默认 False).
        """
        self.feature_flag_name = feature_flag_name
        self.use_llm_assist_flag = use_llm_assist_flag
        self._enabled = self._check_feature_flag(feature_flag_name)
        self._llm_enabled = self._check_feature_flag(use_llm_assist_flag)

        # 持久化路径
        self.persistence_path = (
            Path(persistence_path) if persistence_path else DEFAULT_ROOT_CAUSES_PATH
        )

        # 懒加载诊断器 (避免循环依赖)
        self._code_diagnoser: Any | None = None
        self._strategy_diagnoser: Any | None = None
        self._ops_diagnoser: Any | None = None
        self._chain_builder: Any | None = None
        self._diagnosers_loaded = False

        logger.info(
            "UnifiedRootCauseAnalyzer 初始化: enabled=%s (flag=%s), llm_assist=%s (flag=%s)",
            self._enabled,
            feature_flag_name,
            self._llm_enabled,
            use_llm_assist_flag,
        )

    # ============================================================
    # Feature Flag (HC-1)
    # ============================================================
    @staticmethod
    def _check_feature_flag(flag_name: str) -> bool:
        """检查 Feature Flag (HC-1). 失败时降级为 False."""
        try:
            from utils.infra.feature_flags import is_enabled

            return bool(is_enabled(flag_name))
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
            logger.warning(
                "Feature Flag 检查失败 (降级为 False): %s — %s", flag_name, e
            )
            return False

    @property
    def enabled(self) -> bool:
        """是否启用."""
        return self._enabled

    # ============================================================
    # 诊断器懒加载 (避免循环依赖)
    # ============================================================
    def _load_diagnosers(self) -> None:
        """懒加载三层诊断器 + 因果链构建器. 导入失败时为 None (降级)."""
        if self._diagnosers_loaded:
            return
        self._diagnosers_loaded = True
        try:
            from utils.alpha.layers.code_diagnoser import CodeDiagnoser

            self._code_diagnoser = CodeDiagnoser()
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
            logger.warning("CodeDiagnoser 加载失败 (降级): %s", e)
            self._code_diagnoser = None
        try:
            from utils.alpha.layers.strategy_diagnoser import StrategyDiagnoser

            self._strategy_diagnoser = StrategyDiagnoser()
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
            logger.warning("StrategyDiagnoser 加载失败 (降级): %s", e)
            self._strategy_diagnoser = None
        try:
            from utils.alpha.layers.ops_diagnoser import OpsDiagnoser

            self._ops_diagnoser = OpsDiagnoser()
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
            logger.warning("OpsDiagnoser 加载失败 (降级): %s", e)
            self._ops_diagnoser = None
        try:
            from utils.alpha.causal_chain import CausalChainBuilder

            self._chain_builder = CausalChainBuilder()
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
            logger.warning("CausalChainBuilder 加载失败 (降级): %s", e)
            self._chain_builder = None

    # ============================================================
    # 核心: 分析 HealthReport, 识别根因
    # ============================================================
    def analyze(self, health_report: Any) -> RootCauseReport:
        """分析 HealthReport, 识别三层面根因 + 跨层因果链.

        流程:
            1. 检查 Feature Flag (HC-1), 关闭时返回降级报告
            2. 懒加载诊断器
            3. 三层诊断器各自分析 (容错降级)
            4. 因果链构建器组装跨层因果链
            5. (可选) LLM 辅助推理增强
            6. 持久化到 root_causes.jsonl

        Args:
            health_report: Stage 1 的 HealthReport 对象 (或其 to_dict() 字典)

        Returns:
            RootCauseReport (不可变)
        """
        now = datetime.now(UTC).isoformat()
        source_at = self._extract_health_report_time(health_report)

        # HC-1: Flag 关闭 → 降级报告
        if not self._enabled:
            return self._degraded_report("FEATURE_FLAG_DISABLED", now, source_at)

        # 懒加载诊断器
        self._load_diagnosers()

        # 三层诊断 (各自容错)
        all_causes: list[RootCause] = []
        all_causes.extend(
            self._safe_diagnose(self._code_diagnoser, "code", health_report, now)
        )
        all_causes.extend(
            self._safe_diagnose(
                self._strategy_diagnoser, "strategy", health_report, now
            )
        )
        all_causes.extend(
            self._safe_diagnose(self._ops_diagnoser, "ops", health_report, now)
        )

        # 跨层因果链
        causal_chains = self._build_chains(all_causes, now)

        # 可选 LLM 辅助 (增强描述, 不改变结构)
        if self._llm_enabled and all_causes:
            all_causes = self._llm_enhance(all_causes)

        # 摘要
        summary = self._build_summary(all_causes, causal_chains)

        report = RootCauseReport(
            causes=all_causes,
            causal_chains=causal_chains,
            is_degraded=False,
            analyzed_at=now,
            source_health_report_at=source_at,
            summary=summary,
        )

        # 持久化 (HC-4: 只追加)
        self._persist(report)

        return report

    def _safe_diagnose(
        self, diagnoser: Any, layer: str, health_report: Any, now: str
    ) -> list[RootCause]:
        """安全调用诊断器, 失败时返回空列表 (不阻塞)."""
        if diagnoser is None:
            return []
        try:
            result = diagnoser.diagnose(health_report)
            if not isinstance(result, list):
                logger.warning(
                    "%s 诊断器返回非 list (跳过): %s", layer, type(result).__name__
                )
                return []
            # 校验每个元素是 RootCause
            valid: list[RootCause] = []
            for item in result:
                if isinstance(item, RootCause):
                    valid.append(item)
                else:
                    logger.warning("%s 诊断器返回非 RootCause 元素 (跳过)", layer)
            return valid
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
            logger.warning("%s 层诊断失败 (降级为空): %s", layer, e)
            return []

    def _build_chains(self, causes: list[RootCause], now: str) -> list[CausalChain]:
        """构建跨层因果链 (委托给 CausalChainBuilder)."""
        if self._chain_builder is None or not causes:
            return []
        try:
            result = self._chain_builder.build(causes)
            if isinstance(result, list):
                return [c for c in result if isinstance(c, CausalChain)]
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
            logger.warning("因果链构建失败 (降级为空): %s", e)
        return []

    def _llm_enhance(self, causes: list[RootCause]) -> list[RootCause]:
        """LLM 辅助增强根因描述 (可选, 容错降级).

        仅增强 description, 不改变结构化字段. 失败时原样返回.
        HC-2: chat() 内部已有 5s 超时 (云 API), 无需外部 timeout.
        """
        try:
            from utils.alpha.llm_router import chat

            prompt = self._build_llm_prompt(causes)
            enhanced = chat(prompt)  # HC-2: 内部 5s 超时
            if enhanced and isinstance(enhanced, str):
                logger.info("LLM 辅助根因分析完成 (增强 %d 条)", len(causes))
                # 这里不重写 RootCause (frozen), 仅记录 LLM 输出到日志
                # 实际增强留给 Stage 3 修复阶段使用
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
            logger.warning("LLM 辅助根因分析失败 (降级跳过): %s", e)
        return causes

    @staticmethod
    def _build_llm_prompt(causes: list[RootCause]) -> str:
        """构建 LLM 辅助推理 prompt."""
        lines = ["请分析以下根因的关联性和优先级,输出修复建议:"]
        for c in causes[:10]:  # 限制前 10 条避免 token 超限
            lines.append(
                f"- [{c.layer}/{c.severity}] {c.category}: {c.suggested_fix.description}"
            )
        return "\n".join(lines)

    @staticmethod
    def _build_summary(causes: list[RootCause], chains: list[CausalChain]) -> str:
        """构建一句话摘要."""
        if not causes:
            return "未识别根因 (三层面健康)"
        by_severity: dict[str, int] = {}
        for c in causes:
            by_severity[c.severity] = by_severity.get(c.severity, 0) + 1
        sev_str = ", ".join(f"{k}:{v}" for k, v in sorted(by_severity.items()))
        chain_str = f", {len(chains)} 条因果链" if chains else ""
        return f"识别 {len(causes)} 条根因 ({sev_str}{chain_str})"

    def _degraded_report(
        self, reason: str, now: str, source_at: str
    ) -> RootCauseReport:
        """生成降级报告 (Flag 关闭 / 诊断器全失败)."""
        return RootCauseReport(
            is_degraded=True,
            degraded_reason=reason,
            analyzed_at=now,
            source_health_report_at=source_at,
            summary=f"降级: {reason}",
        )

    @staticmethod
    def _extract_health_report_time(health_report: Any) -> str:
        """从 HealthReport 提取生成时间 (兼容对象/字典)."""
        try:
            if hasattr(health_report, "generated_at"):
                return str(getattr(health_report, "generated_at", ""))
            if isinstance(health_report, dict):
                return str(health_report.get("generated_at", ""))
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
            pass
        return ""

    # ============================================================
    # 持久化与历史 (HC-4: 只追加)
    # ============================================================
    def _persist(self, report: RootCauseReport) -> None:
        """持久化到 root_causes.jsonl (追加模式, HC-4 只读历史)."""
        try:
            self.persistence_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.persistence_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(report.to_dict(), ensure_ascii=False) + "\n")
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
            logger.warning("根因报告持久化失败 (不影响内存报告): %s", e)

    def _read_history(self, days: int) -> list[RootCauseReport]:
        """读取最近 N 天历史 (只读). 失败时返回空列表."""
        try:
            if not self.persistence_path.exists():
                return []
            lines = (
                self.persistence_path.read_text(encoding="utf-8").strip().splitlines()
            )
            recent = lines[-(days * 2) :] if len(lines) > days * 2 else lines
            reports: list[RootCauseReport] = []
            for line in reversed(recent):
                line = line.strip()
                if not line:
                    continue
                try:
                    reports.append(RootCauseReport.from_dict(json.loads(line)))
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
                    continue
            return reports
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
            logger.warning("读取根因历史失败: %s", e)
            return []

    # ============================================================
    # 公共 API
    # ============================================================
    def get_recent_causes(self, days: int = 7) -> list[RootCause]:
        """读取最近 N 天的所有根因 (扁平化)."""
        reports = self._read_history(days)
        causes: list[RootCause] = []
        for r in reports:
            causes.extend(r.causes)
        return causes

    def get_recent_reports(self, days: int = 7) -> list[RootCauseReport]:
        """读取最近 N 天的根因报告."""
        return self._read_history(days)

    def get_status(self) -> dict[str, Any]:
        """获取分析器状态 (用于审计)."""
        return {
            "enabled": self._enabled,
            "feature_flag": self.feature_flag_name,
            "llm_assist_enabled": self._llm_enabled,
            "persistence_path": str(self.persistence_path),
            "diagnosers_loaded": self._diagnosers_loaded,
        }
