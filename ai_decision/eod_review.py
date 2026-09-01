"""
ai_decision.eod_review — 审计日志自动复盘
==========================================

任务: 阶段五步骤 7 — 每日 EOD 自动解析审计 jsonl, 生成 5 维度复盘报告 + 告警推送
责任层: L0 运维监控 (每日收盘后触发, 不阻断主链路)

设计原则 (路线图 ai_decision_roadmap_execution_plan.md 步骤 7):
  - 5 维度复盘: 决策分布 / 辩论有效性 / 风控拦截 / 执行质量 / 异常检测
  - 5 条告警规则: 模型连续失败 / Brier 跌破 / auto 异常放量 / 硬风控突增 / TCA D/F 占比
  - 告警推送 try-except 隔离: realtime_monitor 接口变化不影响复盘
  - 落盘双格式: Markdown (人读) + JSON (机读)
  - 向后兼容: 审计 jsonl 字段缺失时降级跳过

告警阈值 (v8.3_institutional/config/ai_decision.yaml.alerts):
  - brier_threshold: 0.25          # Brier < 此值 → CRITICAL
  - auto_daily_limit: 50           # auto 单日 > 此值 → CRITICAL
  - model_consecutive_failures: 3  # 连续失败 ≥ 此值 → WARNING
  - veto_spike_ratio: 2.0          # 否决率 > 均值 × 此值 → WARNING
  - tca_df_ratio: 0.2              # TCA D/F 占比 > 此值 → WARNING

接口:
  - EODReviewReport: 复盘报告 dataclass (5 维度 + 告警)
  - AlertsConfig: 告警阈值配置
  - EODReviewGenerator: 复盘生成器
    - generate_eod_review(date_str) -> Dict   主入口
    - to_markdown(report) -> str              Markdown 输出
    - save(report, date_str) -> str           落盘

接入点:
  - scripts/run_ai_decision_eod.py (CLI 入口, 每日收盘后触发)
  - 步骤 4 dashboard 可消费此报告的告警

用法:
    from ai_decision.eod_review import EODReviewGenerator

    gen = EODReviewGenerator()
    report = gen.generate_eod_review("2026-07-28")
    path = gen.save(report, "2026-07-28")
    logger.info(f"EOD 复盘已生成: {path}")
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ai_decision.config import get_config
from ai_decision.health import ModelHealthMonitor, get_default_monitor

logger = logging.getLogger("ai_decision.eod_review")

# ============================================================
# 路径常量
# ============================================================
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_REPORT_DIR = _PROJECT_ROOT / "reports" / "ai_decision"
_EXEC_AUDIT_DIR = _REPORT_DIR / "execution"
_TCA_ESTIMATE_DIR = _PROJECT_ROOT / "reports" / "tca"


# ============================================================
# 数据结构
# ============================================================


@dataclass
class AlertsConfig:
    """告警阈值配置 (从 ai_decision.yaml.alerts 加载)"""

    brier_threshold: float = 0.25
    auto_daily_limit: int = 50
    model_consecutive_failures: int = 3
    veto_spike_ratio: float = 2.0
    tca_df_ratio: float = 0.2
    # 正常否决率基线 (用于 veto_spike 规则; 历史均值, 可被配置覆盖)
    # 原硬编码 0.25 不可审计, 提升为配置项
    normal_veto_rate: float = 0.25
    # 置信度健康度阈值 (brier 规则的等价置信度下限)
    # 当 avg_confidence < confidence_floor → 触发 brier 告警
    # 默认 0.75 (等价 brier_threshold=0.25, 即 1 - 0.75 = 0.25)
    confidence_floor: float = 0.75

    @classmethod
    def from_config(cls) -> AlertsConfig:
        """从 ai_decision.yaml 加载 alerts 段 (缺失时用默认值)"""
        try:
            alerts = get_config("alerts", {})
            if isinstance(alerts, dict):
                # brier_threshold 默认 0.25, 等价 confidence_floor = 1 - 0.25 = 0.75
                brier_th = float(alerts.get("brier_threshold", 0.25))
                return cls(
                    brier_threshold=brier_th,
                    auto_daily_limit=int(alerts.get("auto_daily_limit", 50)),
                    model_consecutive_failures=int(
                        alerts.get("model_consecutive_failures", 3)
                    ),
                    veto_spike_ratio=float(alerts.get("veto_spike_ratio", 2.0)),
                    tca_df_ratio=float(alerts.get("tca_df_ratio", 0.2)),
                    normal_veto_rate=float(alerts.get("normal_veto_rate", 0.25)),
                    confidence_floor=float(
                        alerts.get("confidence_floor", 1.0 - brier_th)
                    ),
                )
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError) as e:
            # float/int 转换可能抛 ValueError/TypeError;
            # dict 操作可能抛 KeyError/AttributeError; get_config 可能抛 RuntimeError
            logger.debug("[EOD] alerts 配置加载失败, 用默认值: %s", e)
        return cls()


@dataclass
class EODReviewReport:
    """EOD 复盘报告 (5 维度 + 告警 + 元数据)

    Attributes:
        date: 报告日期 (YYYY-MM-DD)
        decision_distribution: 维度 1 — 决策分布 (action/mode/verdict_type/confidence)
        debate_effectiveness: 维度 2 — 辩论有效性 (触发率/置信度提升/FP 率)
        risk_interception: 维度 3 — 风控拦截 (veto/escalation 原因 Top 5)
        execution_quality: 维度 4 — 执行质量 (成功率/延迟/TCA 评级)
        anomaly_detection: 维度 5 — 异常检测 (模型失败/Brier/auto 放量)
        alerts: 告警列表 (5 条规则)
        generated_at: 生成时间 ISO
    """

    date: str = ""
    decision_distribution: dict[str, Any] = field(default_factory=dict)
    debate_effectiveness: dict[str, Any] = field(default_factory=dict)
    risk_interception: dict[str, Any] = field(default_factory=dict)
    execution_quality: dict[str, Any] = field(default_factory=dict)
    anomaly_detection: dict[str, Any] = field(default_factory=dict)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "decision_distribution": self.decision_distribution,
            "debate_effectiveness": self.debate_effectiveness,
            "risk_interception": self.risk_interception,
            "execution_quality": self.execution_quality,
            "anomaly_detection": self.anomaly_detection,
            "alerts": self.alerts,
            "generated_at": self.generated_at,
        }


# ============================================================
# EOD 复盘生成器
# ============================================================


class EODReviewGenerator:
    """每日 EOD 审计日志复盘生成器

    用法:
        gen = EODReviewGenerator()
        report = gen.generate_eod_review("2026-07-28")
        gen.save(report, "2026-07-28")
    """

    def __init__(
        self,
        health_monitor: ModelHealthMonitor | None = None,
        alerts_config: AlertsConfig | None = None,
    ) -> None:
        """
        Args:
            health_monitor: 模型健康监控器 (None 时用全局单例)
            alerts_config: 告警阈值配置 (None 时从 ai_decision.yaml 加载)
        """
        self._monitor = health_monitor or get_default_monitor()
        self._alerts_cfg = alerts_config or AlertsConfig.from_config()

    # ------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------

    def generate_eod_review(self, date_str: str | None = None) -> dict[str, Any]:
        """生成每日 EOD 复盘报告

        Args:
            date_str: 日期 (YYYY-MM-DD), None 时用今天
        Returns:
            完整复盘报告 dict (含 5 维度 + 告警), 无 KeyError
        """
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")

        logger.info("[EOD] 生成 %s 复盘报告", date_str)

        # 聚合审计日志
        exec_records = self._aggregate_exec_audit(date_str)
        tca_records = self._aggregate_tca_estimates(date_str)

        # 5 维度分析
        decision_dist = self._analyze_decision_distribution(exec_records)
        debate_eff = self._analyze_debate_effectiveness(exec_records)
        risk_inter = self._analyze_risk_interception(exec_records)
        exec_quality = self._analyze_execution_quality(exec_records, tca_records)
        anomaly = self._detect_anomalies(exec_records, decision_dist)

        # 告警
        alerts = self._generate_alerts(
            decision_dist, debate_eff, risk_inter, exec_quality, anomaly
        )

        # 推送告警 (try-except 隔离)
        self._push_alerts(alerts, date_str)

        report = EODReviewReport(
            date=date_str,
            decision_distribution=decision_dist,
            debate_effectiveness=debate_eff,
            risk_interception=risk_inter,
            execution_quality=exec_quality,
            anomaly_detection=anomaly,
            alerts=alerts,
            generated_at=datetime.now().isoformat(timespec="seconds"),
        )
        return report.to_dict()

    # ------------------------------------------------------------
    # 审计日志聚合
    # ------------------------------------------------------------

    def _aggregate_exec_audit(self, date_str: str) -> list[dict[str, Any]]:
        """聚合指定日期的执行审计记录

        文件路径: reports/ai_decision/execution/exec_{date}.jsonl
        降级: 如果精确日期文件不存在, 扫描所有 exec_*.jsonl 按时间戳过滤

        注意: 写入端 (execution_bridge._write_execution_audit) 用 %Y%m%d 格式
        (如 exec_20260728.jsonl, 无横线), 这里需把 date_str (YYYY-MM-DD) 转为
        %Y%m%d 才能匹配, 否则永远读不到当日审计文件.
        """
        # 写入端用 %Y%m%d (无横线), 这里统一格式
        date_compact = date_str.replace("-", "")
        path = _EXEC_AUDIT_DIR / f"exec_{date_compact}.jsonl"
        if path.exists():
            return self._load_jsonl(path)
        return self._load_all_exec_by_date(date_str)

    def _load_all_exec_by_date(self, date_str: str) -> list[dict[str, Any]]:
        """扫描所有 exec_*.jsonl 按时间戳过滤"""
        if not _EXEC_AUDIT_DIR.exists():
            return []
        records: list[dict[str, Any]] = []
        for f in _EXEC_AUDIT_DIR.glob("exec_*.jsonl"):
            for r in self._load_jsonl(f):
                ts = r.get("timestamp", "")
                if ts.startswith(date_str):
                    records.append(r)
        return records

    def _aggregate_tca_estimates(self, date_str: str) -> list[dict[str, Any]]:
        """聚合指定日期的 TCA 预估记录"""
        path = _TCA_ESTIMATE_DIR / f"estimate_{date_str}.jsonl"
        if not path.exists():
            return []
        return self._load_jsonl(path)

    def _load_jsonl(self, path: Path) -> list[dict[str, Any]]:
        """加载 JSONL 文件"""
        records: list[dict[str, Any]] = []
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except (OSError, ValueError, TypeError, AttributeError, RuntimeError) as exc:
            # open() 失败抛 OSError (含 FileNotFoundError/PermissionError);
            # line.strip() 对非字符串抛 AttributeError; 文件编码可能抛 ValueError
            logger.error("[EOD] JSONL 加载失败 %s: %s", path, exc)
        return records

    # ------------------------------------------------------------
    # 维度 1: 决策分布
    # ------------------------------------------------------------

    def _analyze_decision_distribution(
        self, records: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """维度 1: 决策分布 (action / mode / verdict_type / confidence)

        Returns:
            {
                "total_decisions": int,
                "action_distribution": {"buy": int, "sell": int, "hold": int},
                "mode_distribution": {"shadow": int, "paper": int, "auto": int},
                "verdict_type_distribution": Dict[str, int],
                "confidence_stats": {"mean": float, "min": float, "max": float},
            }
        """
        if not records:
            return {
                "total_decisions": 0,
                "action_distribution": {},
                "mode_distribution": {},
                "verdict_type_distribution": {},
                "confidence_stats": {"mean": 0.0, "min": 0.0, "max": 0.0},
            }

        action_dist: dict[str, int] = {}
        mode_dist: dict[str, int] = {}
        verdict_dist: dict[str, int] = {}
        confidences: list[float] = []

        for r in records:
            action = r.get("action", "unknown")
            action_dist[action] = action_dist.get(action, 0) + 1
            mode = r.get("mode", "unknown")
            mode_dist[mode] = mode_dist.get(mode, 0) + 1
            verdict = r.get("verdict_type", "unknown")
            verdict_dist[verdict] = verdict_dist.get(verdict, 0) + 1
            conf = r.get("decision_confidence", 0.0)
            if conf > 0:
                confidences.append(float(conf))

        conf_stats = {"mean": 0.0, "min": 0.0, "max": 0.0}
        if confidences:
            conf_stats = {
                "mean": sum(confidences) / len(confidences),
                "min": min(confidences),
                "max": max(confidences),
            }

        return {
            "total_decisions": len(records),
            "action_distribution": action_dist,
            "mode_distribution": mode_dist,
            "verdict_type_distribution": verdict_dist,
            "confidence_stats": conf_stats,
        }

    # ------------------------------------------------------------
    # 维度 2: 辩论有效性
    # ------------------------------------------------------------

    def _analyze_debate_effectiveness(
        self, records: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """维度 2: 辩论有效性 (触发率 / 置信度提升 / FP 率)

        从审计记录中提取辩论相关信息:
        - verdict_type="DEBATE" 表示触发了辩论
        - 辩论后 action="hold" 视为 False Positive (辩论后仍不交易)

        Returns:
            {
                "debate_triggered": int,
                "debate_trigger_rate": float,
                "avg_confidence": float,
                "false_positive_count": int,
                "false_positive_rate": float,
            }
        """
        if not records:
            return {
                "debate_triggered": 0,
                "debate_trigger_rate": 0.0,
                "avg_confidence": 0.0,
                "false_positive_count": 0,
                "false_positive_rate": 0.0,
            }

        debate_count = sum(1 for r in records if r.get("verdict_type", "") == "DEBATE")
        # 辩论后仍 hold = False Positive
        fp_count = sum(
            1
            for r in records
            if r.get("verdict_type", "") == "DEBATE" and r.get("action", "") == "hold"
        )
        confidences = [
            float(r.get("decision_confidence", 0.0))
            for r in records
            if r.get("decision_confidence", 0.0) > 0
        ]
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

        return {
            "debate_triggered": debate_count,
            "debate_trigger_rate": debate_count / len(records) if records else 0.0,
            "avg_confidence": avg_conf,
            "false_positive_count": fp_count,
            "false_positive_rate": fp_count / debate_count if debate_count > 0 else 0.0,
        }

    # ------------------------------------------------------------
    # 维度 3: 风控拦截
    # ------------------------------------------------------------

    def _analyze_risk_interception(
        self, records: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """维度 3: 风控拦截 (veto / escalation 原因 Top 5)

        Returns:
            {
                "total_veto": int,
                "total_escalation": int,
                "veto_rate": float,
                "escalation_rate": float,
                "veto_reasons_top5": List[Tuple[str, int]],
                "escalation_reasons_top5": List[Tuple[str, int]],
            }
        """
        if not records:
            return {
                "total_veto": 0,
                "total_escalation": 0,
                "veto_rate": 0.0,
                "escalation_rate": 0.0,
                "veto_reasons_top5": [],
                "escalation_reasons_top5": [],
            }

        veto_count = sum(1 for r in records if r.get("veto", False))
        esc_count = sum(1 for r in records if r.get("escalation", False))

        veto_reasons = [
            r.get("veto_reason", "")
            for r in records
            if r.get("veto", False) and r.get("veto_reason")
        ]
        esc_reasons = [
            r.get("escalation_reason", "")
            for r in records
            if r.get("escalation", False) and r.get("escalation_reason")
        ]

        return {
            "total_veto": veto_count,
            "total_escalation": esc_count,
            "veto_rate": veto_count / len(records) if records else 0.0,
            "escalation_rate": esc_count / len(records) if records else 0.0,
            "veto_reasons_top5": Counter(veto_reasons).most_common(5),
            "escalation_reasons_top5": Counter(esc_reasons).most_common(5),
        }

    # ------------------------------------------------------------
    # 维度 4: 执行质量
    # ------------------------------------------------------------

    def _analyze_execution_quality(
        self,
        exec_records: list[dict[str, Any]],
        tca_records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """维度 4: 执行质量 (成功率 / 延迟 / TCA 评级)

        Returns:
            {
                "total_executions": int,
                "success_rate": float,
                "avg_latency_ms": float,
                "tca_grade_distribution": Dict[str, int],
                "tca_avg_is_cost_bps": float,
            }
        """
        if not exec_records:
            return {
                "total_executions": 0,
                "success_rate": 0.0,
                "avg_latency_ms": 0.0,
                "tca_grade_distribution": {},
                "tca_avg_is_cost_bps": 0.0,
            }

        executed = sum(1 for r in exec_records if r.get("executed", False))
        # 延迟从 execution_result.elapsed_seconds 提取
        latencies: list[float] = []
        for r in exec_records:
            er = r.get("execution_result")
            if isinstance(er, dict):
                elapsed = er.get("elapsed_seconds", 0.0)
                if elapsed > 0:
                    latencies.append(float(elapsed) * 1000.0)  # 转 ms

        # TCA 评级 (从事后归因报告提取)
        grade_dist: dict[str, int] = {}
        is_costs: list[float] = []
        for r in exec_records:
            post = r.get("tca_post_report")
            if isinstance(post, dict):
                grade = post.get("quality_grade", "")
                if grade:
                    grade_dist[grade] = grade_dist.get(grade, 0) + 1
                is_cost = post.get("is_cost_bps", 0.0)
                if is_cost != 0:
                    is_costs.append(float(is_cost))

        # TCA 预估成本 (从事前预估提取)
        for r in tca_records:
            cost = r.get("estimated_cost_bps", 0.0)
            if cost > 0:
                is_costs.append(float(cost))

        return {
            "total_executions": len(exec_records),
            "success_rate": executed / len(exec_records) if exec_records else 0.0,
            "avg_latency_ms": sum(latencies) / len(latencies) if latencies else 0.0,
            "tca_grade_distribution": grade_dist,
            "tca_avg_is_cost_bps": sum(is_costs) / len(is_costs) if is_costs else 0.0,
        }

    # ------------------------------------------------------------
    # 维度 5: 异常检测
    # ------------------------------------------------------------

    def _detect_anomalies(
        self,
        exec_records: list[dict[str, Any]],
        decision_dist: dict[str, Any],
    ) -> dict[str, Any]:
        """维度 5: 异常检测 (模型连续失败 / Brier / auto 放量)

        Returns:
            {
                "model_consecutive_failures": Dict[str, int],  # role -> 连续失败次数
                "model_open_breakers": List[str],
                "auto_daily_count": int,
                "veto_rate": float,
            }
        """
        # 模型健康 (从 ModelHealthMonitor 获取)
        model_failures: dict[str, int] = {}
        open_breakers: list[str] = []
        try:
            stats = self._monitor.get_stats()
            for role, role_stats in stats.get("roles", {}).items():
                cf = role_stats.get("consecutive_failures", 0)
                if cf > 0:
                    model_failures[role] = cf
                if role_stats.get("is_open", False):
                    open_breakers.append(role)
        except (
            RuntimeError,
            KeyError,
            TypeError,
            AttributeError,
            ValueError,
            OSError,
        ) as exc:
            # monitor.get_stats() 可能抛: 运行时错误/字段缺失/类型不匹配/
            # 属性缺失/值错误/IO 错误
            logger.error("[EOD] 模型健康状态获取失败: %s", exc)

        # auto 模式单日笔数
        mode_dist = decision_dist.get("mode_distribution", {})
        auto_count = mode_dist.get("auto", 0)

        # 否决率
        veto_count = sum(1 for r in exec_records if r.get("veto", False))
        veto_rate = veto_count / len(exec_records) if exec_records else 0.0

        return {
            "model_consecutive_failures": model_failures,
            "model_open_breakers": open_breakers,
            "auto_daily_count": auto_count,
            "veto_rate": veto_rate,
        }

    # ------------------------------------------------------------
    # 告警生成 (5 条规则)
    # ------------------------------------------------------------

    def _generate_alerts(
        self,
        decision_dist: dict[str, Any],
        debate_eff: dict[str, Any],
        risk_inter: dict[str, Any],
        exec_quality: dict[str, Any],
        anomaly: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """生成告警列表 (5 条规则)

        规则:
        1. 模型连续失败 ≥ model_consecutive_failures → WARNING
        2. Brier < brier_threshold → CRITICAL
        3. auto 单日 > auto_daily_limit → CRITICAL
        4. 硬风控拦截率 > veto_spike_ratio × 平均 → WARNING
        5. TCA 评级 D/F 占比 > tca_df_ratio → WARNING
        """
        alerts: list[dict[str, Any]] = []

        # 规则 1: 模型连续失败
        model_failures = anomaly.get("model_consecutive_failures", {})
        for role, cf in model_failures.items():
            if cf >= self._alerts_cfg.model_consecutive_failures:
                alerts.append(
                    {
                        "rule": "model_consecutive_failures",
                        "severity": "WARNING",
                        "role": role,
                        "value": cf,
                        "threshold": self._alerts_cfg.model_consecutive_failures,
                        "message": f"{role} 连续失败 {cf} 次 ≥ {self._alerts_cfg.model_consecutive_failures}",
                    }
                )

        # 规则 2: 置信度健康度 (原"Brier 跌破阈值"近似修正)
        # 原 brier_approx = 1 - avg_confidence 是错误近似:
        #   Brier = (forecast_prob - actual_outcome)^2 均值, 需要 actual_outcome
        #   审计日志无 actual_outcome 字段, 无法准确计算 Brier
        # 修正: 改为置信度健康度告警, 直接检查 avg_confidence < confidence_floor
        #   confidence_floor 默认 0.75 (等价原 brier_threshold=0.25)
        #   即模型平均置信度低于 75% → 触发 CRITICAL
        avg_conf = debate_eff.get("avg_confidence", 0.0)
        if avg_conf > 0 and avg_conf < self._alerts_cfg.confidence_floor:
            # 等价 brier 值 (仅用于展示, 非真实 Brier)
            brier_display = 1.0 - avg_conf
            alerts.append(
                {
                    "rule": "confidence_health",
                    "severity": "CRITICAL",
                    "value": round(avg_conf, 4),
                    "threshold": self._alerts_cfg.confidence_floor,
                    "message": (
                        f"平均置信度 {avg_conf:.4f} < 阈值 {self._alerts_cfg.confidence_floor:.4f}"
                        f" (等价 Brier 近似 {brier_display:.4f} > {self._alerts_cfg.brier_threshold}, "
                        f"真实 Brier 需 actual_outcome 数据)"
                    ),
                }
            )

        # 规则 3: auto 异常放量
        auto_count = anomaly.get("auto_daily_count", 0)
        if auto_count > self._alerts_cfg.auto_daily_limit:
            alerts.append(
                {
                    "rule": "auto_daily_limit",
                    "severity": "CRITICAL",
                    "value": auto_count,
                    "threshold": self._alerts_cfg.auto_daily_limit,
                    "message": f"auto 模式单日 {auto_count} 笔 > 上限 {self._alerts_cfg.auto_daily_limit}",
                }
            )

        # 规则 4: 硬风控拦截突增
        # veto_spike_ratio 默认 2.0, 含义是 veto_rate > 正常水平的 2 倍
        # normal_veto_rate 提升为配置项 (原硬编码 0.25 不可审计)
        veto_rate = anomaly.get("veto_rate", 0.0)
        normal_veto_rate = self._alerts_cfg.normal_veto_rate
        veto_spike_threshold = normal_veto_rate * self._alerts_cfg.veto_spike_ratio
        if veto_rate > veto_spike_threshold:
            alerts.append(
                {
                    "rule": "veto_spike",
                    "severity": "WARNING",
                    "value": round(veto_rate, 4),
                    "threshold": round(veto_spike_threshold, 4),
                    "message": (
                        f"硬风控否决率 {veto_rate:.1%} > 正常 {normal_veto_rate:.0%} "
                        f"× {self._alerts_cfg.veto_spike_ratio} = {veto_spike_threshold:.1%}"
                    ),
                }
            )

        # 规则 5: TCA 评级 D/F 占比
        grade_dist = exec_quality.get("tca_grade_distribution", {})
        total_graded = sum(grade_dist.values())
        d_f_count = grade_dist.get("D", 0) + grade_dist.get("F", 0)
        if total_graded > 0:
            df_ratio = d_f_count / total_graded
            if df_ratio > self._alerts_cfg.tca_df_ratio:
                alerts.append(
                    {
                        "rule": "tca_grade_df",
                        "severity": "WARNING",
                        "value": round(df_ratio, 4),
                        "threshold": self._alerts_cfg.tca_df_ratio,
                        "message": f"TCA 评级 D/F 占比 {d_f_count}/{total_graded} = {df_ratio:.1%} > {self._alerts_cfg.tca_df_ratio:.0%}",  # noqa: E501
                    }
                )

        return alerts

    # ------------------------------------------------------------
    # 告警推送 (try-except 隔离)
    # ------------------------------------------------------------

    def _push_alerts(self, alerts: list[dict[str, Any]], date_str: str) -> None:
        """推送告警到 realtime_monitor (try-except 隔离)

        设计: realtime_monitor 接口变化不影响复盘主路径
        推送失败仅记日志, 不阻断复盘报告生成
        """
        if not alerts:
            return

        critical_count = sum(1 for a in alerts if a.get("severity") == "CRITICAL")
        warning_count = sum(1 for a in alerts if a.get("severity") == "WARNING")

        # 尝试推送 (try-except 隔离)
        try:
            # 尝试导入 realtime_monitor 告警接口
            # 未来可对接飞书/钉钉/邮件等通道
            import importlib

            rm = importlib.import_module("realtime_monitor")
            # 如果 realtime_monitor 有推送接口, 调用它
            push_fn = getattr(rm, "push_alert", None) or getattr(rm, "send_alert", None)
            if push_fn and callable(push_fn):
                for alert in alerts:
                    try:
                        push_fn(
                            title=f"[ai_decision EOD {date_str}] {alert.get('severity', '')}",
                            message=alert.get("message", ""),
                            severity=alert.get("severity", "WARNING"),
                        )
                    except (
                        RuntimeError,
                        OSError,
                        ConnectionError,
                        TimeoutError,
                        ValueError,
                        TypeError,
                        KeyError,
                        AttributeError,
                    ):
                        # push_fn 可能抛: 网络/超时/参数格式/字段缺失/类型不匹配
                        # 单条失败不影响其他告警推送
                        pass  # pragma: no cover
                logger.info(
                    "[EOD] 告警已推送: CRITICAL=%d WARNING=%d",
                    critical_count,
                    warning_count,
                )
            else:
                logger.info(
                    "[EOD] 告警未推送 (realtime_monitor 无 push_alert 接口): "
                    "CRITICAL=%d WARNING=%d",
                    critical_count,
                    warning_count,
                )
        except ImportError:
            logger.info(
                "[EOD] 告警未推送 (realtime_monitor 未安装): " "CRITICAL=%d WARNING=%d",
                critical_count,
                warning_count,
            )
        except (
            RuntimeError,
            OSError,
            ConnectionError,
            TimeoutError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
        ) as exc:
            # 告警推送可能抛: 网络/超时/参数格式/字段缺失/类型不匹配/属性缺失
            logger.warning("[EOD] 告警推送异常 (不影响复盘): %s", exc)

    # ------------------------------------------------------------
    # Markdown 输出 (5 维度 + 告警)
    # ------------------------------------------------------------

    def to_markdown(self, report: dict[str, Any]) -> str:
        """将报告转为 Markdown (5 维度 + 告警)"""
        date = report.get("date", "")
        lines: list[str] = [
            f"# ai_decision EOD 复盘 — {date}",
            "",
            f"> 生成时间: {report.get('generated_at', '')}",
            "",
        ]

        lines.extend(
            self._md_decision_distribution(report.get("decision_distribution", {}))
        )
        lines.extend(
            self._md_debate_effectiveness(report.get("debate_effectiveness", {}))
        )
        lines.extend(self._md_risk_interception(report.get("risk_interception", {})))
        lines.extend(self._md_execution_quality(report.get("execution_quality", {})))
        lines.extend(self._md_anomaly_detection(report.get("anomaly_detection", {})))
        lines.extend(self._md_alerts(report.get("alerts", [])))

        return "\n".join(lines)

    def _md_decision_distribution(self, dd: dict[str, Any]) -> list[str]:
        """维度 1: 决策分布"""
        lines = ["## 1. 决策分布", ""]
        total = dd.get("total_decisions", 0)
        lines.append(f"- 决策总数: **{total}**")
        action_dist = dd.get("action_distribution", {})
        if action_dist:
            lines.append(
                "- Action: " + " / ".join(f"{k}={v}" for k, v in action_dist.items())
            )
        mode_dist = dd.get("mode_distribution", {})
        if mode_dist:
            lines.append(
                "- Mode: " + " / ".join(f"{k}={v}" for k, v in mode_dist.items())
            )
        verdict_dist = dd.get("verdict_type_distribution", {})
        if verdict_dist:
            lines.append(
                "- Verdict: " + " / ".join(f"{k}={v}" for k, v in verdict_dist.items())
            )
        conf = dd.get("confidence_stats", {})
        if conf.get("mean", 0) > 0:
            lines.append(
                f"- 置信度: 均值={conf['mean']:.3f} 最小={conf['min']:.3f} 最大={conf['max']:.3f}"
            )
        lines.append("")
        return lines

    def _md_debate_effectiveness(self, de: dict[str, Any]) -> list[str]:
        """维度 2: 辩论有效性"""
        lines = ["## 2. 辩论有效性", ""]
        triggered = de.get("debate_triggered", 0)
        rate = de.get("debate_trigger_rate", 0.0)
        fp = de.get("false_positive_count", 0)
        fp_rate = de.get("false_positive_rate", 0.0)
        lines.append(f"- 辩论触发: {triggered} 次 (触发率 {rate:.1%})")
        lines.append(f"- False Positive: {fp} 次 (FP 率 {fp_rate:.1%})")
        avg_conf = de.get("avg_confidence", 0.0)
        if avg_conf > 0:
            lines.append(f"- 平均置信度: {avg_conf:.3f}")
        lines.append("")
        return lines

    def _md_risk_interception(self, ri: dict[str, Any]) -> list[str]:
        """维度 3: 风控拦截"""
        lines = ["## 3. 风控拦截", ""]
        veto = ri.get("total_veto", 0)
        esc = ri.get("total_escalation", 0)
        veto_rate = ri.get("veto_rate", 0.0)
        esc_rate = ri.get("escalation_rate", 0.0)
        lines.append(f"- 硬风控否决: {veto} 次 (否决率 {veto_rate:.1%})")
        lines.append(f"- 升级人工: {esc} 次 (升级率 {esc_rate:.1%})")
        # veto 原因 Top 5
        veto_top5 = ri.get("veto_reasons_top5", [])
        if veto_top5:
            lines.append("- 否决原因 Top 5:")
            for reason, count in veto_top5:
                lines.append(f"  - ({count}) {reason[:80]}")
        # escalation 原因 Top 5
        esc_top5 = ri.get("escalation_reasons_top5", [])
        if esc_top5:
            lines.append("- 升级原因 Top 5:")
            for reason, count in esc_top5:
                lines.append(f"  - ({count}) {reason[:80]}")
        lines.append("")
        return lines

    def _md_execution_quality(self, eq: dict[str, Any]) -> list[str]:
        """维度 4: 执行质量"""
        lines = ["## 4. 执行质量", ""]
        total = eq.get("total_executions", 0)
        success_rate = eq.get("success_rate", 0.0)
        avg_latency = eq.get("avg_latency_ms", 0.0)
        lines.append(f"- 执行总数: **{total}**")
        lines.append(f"- 成功率: {success_rate:.1%}")
        if avg_latency > 0:
            lines.append(f"- 平均延迟: {avg_latency:.1f} ms")
        grade_dist = eq.get("tca_grade_distribution", {})
        if grade_dist:
            lines.append(
                "- TCA 评级: "
                + " / ".join(f"{k}={v}" for k, v in sorted(grade_dist.items()))
            )
        avg_cost = eq.get("tca_avg_is_cost_bps", 0.0)
        if avg_cost > 0:
            lines.append(f"- 平均 TCA 成本: {avg_cost:.2f} bps")
        lines.append("")
        return lines

    def _md_anomaly_detection(self, ad: dict[str, Any]) -> list[str]:
        """维度 5: 异常检测"""
        lines = ["## 5. 异常检测", ""]
        model_failures = ad.get("model_consecutive_failures", {})
        open_breakers = ad.get("model_open_breakers", [])
        auto_count = ad.get("auto_daily_count", 0)
        veto_rate = ad.get("veto_rate", 0.0)
        lines.append(f"- auto 模式单日: {auto_count} 笔")
        lines.append(f"- 否决率: {veto_rate:.1%}")
        if model_failures:
            lines.append(
                "- 模型连续失败: "
                + " / ".join(f"{role}={cf}" for role, cf in model_failures.items())
            )
        if open_breakers:
            lines.append(f"- 熔断开启: {', '.join(open_breakers)}")
        if not model_failures and not open_breakers:
            lines.append("- 模型健康: ✅ 无异常")
        lines.append("")
        return lines

    def _md_alerts(self, alerts: list[dict[str, Any]]) -> list[str]:
        """告警章节"""
        lines = ["## 6. 告警", ""]
        if not alerts:
            lines.append("✅ 无告警")
            lines.append("")
            return lines
        critical = [a for a in alerts if a.get("severity") == "CRITICAL"]
        warning = [a for a in alerts if a.get("severity") == "WARNING"]
        lines.append(f"- CRITICAL: **{len(critical)}** | WARNING: **{len(warning)}**")
        lines.append("")
        if critical:
            lines.append("### CRITICAL")
            for a in critical:
                lines.append(f"- 🔴 [{a.get('rule', '')}] {a.get('message', '')}")
            lines.append("")
        if warning:
            lines.append("### WARNING")
            for a in warning:
                lines.append(f"- 🟡 [{a.get('rule', '')}] {a.get('message', '')}")
            lines.append("")
        return lines

    # ------------------------------------------------------------
    # 落盘
    # ------------------------------------------------------------

    def save(self, report: dict[str, Any], date_str: str | None = None) -> str:
        """落盘 Markdown + JSON 双格式

        文件路径:
        - reports/ai_decision/eod_review_{date}.md
        - reports/ai_decision/eod_review_{date}.json

        Returns:
            Markdown 文件路径
        """
        if not date_str:
            date_str = report.get("date", datetime.now().strftime("%Y-%m-%d"))

        _REPORT_DIR.mkdir(parents=True, exist_ok=True)

        # JSON
        json_path = _REPORT_DIR / f"eod_review_{date_str}.json"
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)

        # Markdown
        md_path = _REPORT_DIR / f"eod_review_{date_str}.md"
        md_content = self.to_markdown(report)
        with open(md_path, "w", encoding="utf-8") as fh:
            fh.write(md_content)

        logger.info("[EOD] 复盘报告已落盘: %s + %s", md_path, json_path)
        return str(md_path)
