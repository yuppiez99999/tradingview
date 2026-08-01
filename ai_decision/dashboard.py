"""
ai_decision.dashboard — 延迟/成本基准看板
==========================================

任务: 阶段一步骤 4 — 每日延迟/成本基准报告 + 预算告警 + 超预算降级
责任层: L0 运维监控 (每日 EOD 触发, 不阻断主链路)

设计原则 (路线图 ai_decision_roadmap_execution_plan.md 步骤 4):
  - 4 数据源聚合: ModelHealthMonitor / TCA 预估 / 决策审计 / 执行审计
  - 5 章节报告: 模型健康 / TCA 成本 / 决策分布 / 执行质量 / 告警
  - 预算熔断: 超阈值时 maybe_degrade_on_budget() 返回 role → 降级标记
  - 向后兼容: 审计 jsonl 字段缺失时降级跳过 (步骤 1 新增 escalation 字段)
  - 落盘双格式: Markdown (人读) + JSON (机读)

预算阈值 (v8.3_institutional/config/ai_decision.yaml.budget):
  - monthly_token_limit: 5_000_000
  - per_call_latency_p99_ms: 5000
  - daily_cost_limit_usd: 10.0

接口:
  - DashboardReport: 报告数据结构 dataclass
  - DashboardGenerator: 看板生成器
    - generate_daily_dashboard(date_str) -> Dict   主入口
    - check_budget_alert() -> List[Dict]           预算告警
    - maybe_degrade_on_budget() -> Dict[str,str]   超预算降级
    - to_markdown(report) -> str                   Markdown 输出
    - save(report, date_str) -> str                落盘

接入点:
  - scripts/run_ai_decision_dashboard.py (CLI 入口, 每日 EOD 触发)
  - 步骤 7 EOD 复盘可消费此报告

用法:
    from ai_decision.dashboard import DashboardGenerator

    gen = DashboardGenerator()
    report = gen.generate_daily_dashboard("2026-07-28")
    path = gen.save(report, "2026-07-28")
    logger.info(f"看板已生成: {path}")

    # 预算告警
    alerts = gen.check_budget_alert()
    for a in alerts:
        if a["severity"] == "CRITICAL":
            send_alert(a)

    # 超预算降级
    degrade = gen.maybe_degrade_on_budget()
    if "judge" in degrade:
        # 切换 judge 到 MockProvider
        ...
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ai_decision.config import get_config
from ai_decision.health import ModelHealthMonitor, get_default_monitor

logger = logging.getLogger("ai_decision.dashboard")

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
class BudgetConfig:
    """预算阈值配置 (从 ai_decision.yaml.budget 加载)"""
    monthly_token_limit: int = 5_000_000
    per_call_latency_p99_ms: float = 5000.0
    daily_cost_limit_usd: float = 10.0

    @classmethod
    def from_config(cls) -> BudgetConfig:
        """从 ai_decision.yaml 加载 budget 段 (缺失时用默认值)"""
        try:
            budget = get_config("budget", {})
            if isinstance(budget, dict):
                return cls(
                    monthly_token_limit=int(budget.get("monthly_token_limit", 5_000_000)),
                    per_call_latency_p99_ms=float(budget.get("per_call_latency_p99_ms", 5000.0)),
                    daily_cost_limit_usd=float(budget.get("daily_cost_limit_usd", 10.0)),
                )
        except Exception as e:
            logger.debug("[Dashboard] budget 配置加载失败, 用默认值: %s", e)
        return cls()


@dataclass
class DashboardReport:
    """每日看板报告 (5 章节 + 元数据)

    Attributes:
        date: 报告日期 (YYYY-MM-DD)
        model_health: 模型健康章节 (延迟/成功率/熔断状态)
        tca_summary: TCA 成本章节 (预估成本/评级分布)
        decision_summary: 决策分布章节 (action/mode/verdict_type 分布)
        execution_summary: 执行质量章节 (成功率/escalation 分布)
        alerts: 告警列表 (预算超限/熔断/异常)
        generated_at: 生成时间 ISO
    """
    date: str = ""
    model_health: dict[str, Any] = field(default_factory=dict)
    tca_summary: dict[str, Any] = field(default_factory=dict)
    decision_summary: dict[str, Any] = field(default_factory=dict)
    execution_summary: dict[str, Any] = field(default_factory=dict)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转为 dict (用于 JSON 序列化)"""
        return {
            "date": self.date,
            "model_health": self.model_health,
            "tca_summary": self.tca_summary,
            "decision_summary": self.decision_summary,
            "execution_summary": self.execution_summary,
            "alerts": self.alerts,
            "generated_at": self.generated_at,
        }


# ============================================================
# 看板生成器
# ============================================================

class DashboardGenerator:
    """每日延迟/成本基准看板生成器

    用法:
        gen = DashboardGenerator()
        report = gen.generate_daily_dashboard("2026-07-28")
        gen.save(report, "2026-07-28")
    """

    def __init__(
        self,
        health_monitor: ModelHealthMonitor | None = None,
        budget: BudgetConfig | None = None,
    ) -> None:
        """
        Args:
            health_monitor: 模型健康监控器 (None 时用全局单例)
            budget: 预算配置 (None 时从 ai_decision.yaml 加载)
        """
        self._monitor = health_monitor or get_default_monitor()
        self._budget = budget or BudgetConfig.from_config()

    # ------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------

    def generate_daily_dashboard(self, date_str: str | None = None) -> dict[str, Any]:
        """生成每日看板报告

        Args:
            date_str: 日期 (YYYY-MM-DD), None 时用今天
        Returns:
            完整报告 dict (含 5 章节 + 告警), 无 KeyError
        """
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")

        logger.info("[Dashboard] 生成 %s 看板", date_str)

        # 1. 模型健康
        model_health = self._collect_model_health()

        # 2. TCA 成本
        tca_summary = self._collect_tca_summary(date_str)

        # 3. 决策分布
        decision_summary = self._collect_decision_summary(date_str)

        # 4. 执行质量
        execution_summary = self._collect_execution_summary(date_str)

        # 5. 告警 (基于前 4 章节数据)
        alerts = self._check_budget_alerts(model_health, tca_summary, execution_summary)

        report = DashboardReport(
            date=date_str,
            model_health=model_health,
            tca_summary=tca_summary,
            decision_summary=decision_summary,
            execution_summary=execution_summary,
            alerts=alerts,
            generated_at=datetime.now().isoformat(timespec="seconds"),
        )
        return report.to_dict()

    # ------------------------------------------------------------
    # 数据源 1: 模型健康 (从 ModelHealthMonitor.get_stats() 收集)
    # ------------------------------------------------------------

    def _collect_model_health(self) -> dict[str, Any]:
        """收集模型健康数据 (延迟/成功率/熔断状态)

        Returns:
            {
                "total_roles": int,
                "healthy_roles": List[str],
                "unhealthy_roles": List[str],
                "open_breakers": List[str],
                "roles": {role: {latency_ms, healthy, circuit_open, ...}},
            }
        """
        try:
            stats = self._monitor.get_stats()
            summary = self._monitor.get_health_summary()
            roles_detail: dict[str, Any] = {}
            for role, role_stats in stats.get("roles", {}).items():
                last_status = role_stats.get("last_status", {})
                roles_detail[role] = {
                    "healthy": last_status.get("healthy", True),
                    "latency_ms": last_status.get("latency_ms", 0.0),
                    "circuit_open": role_stats.get("is_open", False),
                    "consecutive_failures": role_stats.get("consecutive_failures", 0),
                    "provider_name": last_status.get("provider_name", ""),
                }
            return {
                "total_roles": summary.get("total_roles", 0),
                "healthy_roles": summary.get("healthy_roles", []),
                "unhealthy_roles": summary.get("unhealthy_roles", []),
                "open_breakers": summary.get("open_breakers", []),
                "roles": roles_detail,
                "config": stats.get("config", {}),
            }
        except Exception as exc:
            logger.error("[Dashboard] 模型健康收集失败: %s", exc)
            return {"error": str(exc), "total_roles": 0, "roles": {}}

    # ------------------------------------------------------------
    # 数据源 2: TCA 成本 (从 reports/tca/estimate_*.jsonl 收集)
    # ------------------------------------------------------------

    def _collect_tca_summary(self, date_str: str) -> dict[str, Any]:
        """收集 TCA 预估成本数据

        Returns:
            {
                "total_estimates": int,
                "approved_count": int,
                "rejected_count": int,
                "avg_cost_bps": float,
                "max_cost_bps": float,
                "grade_distribution": Dict[str, int],
            }
        """
        estimates = self._load_tca_estimates(date_str)
        if not estimates:
            return {
                "total_estimates": 0,
                "approved_count": 0,
                "rejected_count": 0,
                "avg_cost_bps": 0.0,
                "max_cost_bps": 0.0,
            }

        costs = [e.get("estimated_cost_bps", 0.0) for e in estimates]
        approved = sum(1 for e in estimates if e.get("approved", True))
        rejected = len(estimates) - approved

        return {
            "total_estimates": len(estimates),
            "approved_count": approved,
            "rejected_count": rejected,
            "avg_cost_bps": sum(costs) / len(costs) if costs else 0.0,
            "max_cost_bps": max(costs) if costs else 0.0,
            "symbols": list({e.get("symbol", "") for e in estimates}),
        }

    def _load_tca_estimates(self, date_str: str) -> list[dict[str, Any]]:
        """加载指定日期的 TCA 预估记录

        文件路径: reports/tca/estimate_{date}.jsonl
        """
        path = _TCA_ESTIMATE_DIR / f"estimate_{date_str}.jsonl"
        if not path.exists():
            return []
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
        except Exception as exc:
            logger.error("[Dashboard] TCA 预估记录加载失败: %s", exc)
        return records

    # ------------------------------------------------------------
    # 数据源 3: 决策分布 (从 reports/ai_decision/audit_*.jsonl 收集)
    # ------------------------------------------------------------

    def _collect_decision_summary(self, date_str: str) -> dict[str, Any]:
        """收集决策分布数据 (action / mode / verdict_type)

        Returns:
            {
                "total_decisions": int,
                "action_distribution": {"buy": int, "sell": int, "hold": int},
                "mode_distribution": {"shadow": int, "paper": int, "auto": int},
                "verdict_type_distribution": Dict[str, int],
            }
        """
        records = self._load_decision_audit(date_str)
        if not records:
            return {
                "total_decisions": 0,
                "action_distribution": {},
                "mode_distribution": {},
                "verdict_type_distribution": {},
            }

        action_dist: dict[str, int] = {}
        mode_dist: dict[str, int] = {}
        verdict_dist: dict[str, int] = {}

        for r in records:
            action = r.get("action", "unknown")
            action_dist[action] = action_dist.get(action, 0) + 1
            mode = r.get("mode", "unknown")
            mode_dist[mode] = mode_dist.get(mode, 0) + 1
            verdict = r.get("verdict_type", "unknown")
            verdict_dist[verdict] = verdict_dist.get(verdict, 0) + 1

        return {
            "total_decisions": len(records),
            "action_distribution": action_dist,
            "mode_distribution": mode_dist,
            "verdict_type_distribution": verdict_dist,
        }

    def _load_decision_audit(self, date_str: str) -> list[dict[str, Any]]:
        """加载指定日期的决策审计记录

        文件路径: reports/ai_decision/audit_{date}.jsonl 或 exec_{date}.jsonl
        向后兼容: 如果 audit_*.jsonl 不存在, 从 execution/exec_*.jsonl 提取决策字段

        注意: 写入端 (orchestrator._write_audit) 用 %Y%m%d 格式
        (如 audit_20260728.jsonl, 无横线), 这里需把 date_str (YYYY-MM-DD) 转为
        %Y%m%d 才能匹配, 否则永远读不到当日审计文件.
        """
        # 优先读 audit_*.jsonl (写入端用 %Y%m%d 格式, 无横线)
        date_compact = date_str.replace("-", "")
        audit_path = _REPORT_DIR / f"audit_{date_compact}.jsonl"
        if audit_path.exists():
            return self._load_jsonl(audit_path)

        # 降级: 从执行审计提取决策字段
        exec_records = self._load_execution_audit(date_str)
        # 执行审计中包含 decision_confidence / decision_strength / verdict_type
        return exec_records

    # ------------------------------------------------------------
    # 数据源 4: 执行质量 (从 reports/ai_decision/execution/exec_*.jsonl 收集)
    # ------------------------------------------------------------

    def _collect_execution_summary(self, date_str: str) -> dict[str, Any]:
        """收集执行质量数据 (成功率 / escalation 分布 / TCA 评级)

        Returns:
            {
                "total_executions": int,
                "executed_count": int,
                "veto_count": int,
                "escalation_count": int,
                "escalation_reasons": List[str],
                "tca_grade_distribution": Dict[str, int],
            }
        """
        records = self._load_execution_audit(date_str)
        if not records:
            return {
                "total_executions": 0,
                "executed_count": 0,
                "veto_count": 0,
                "escalation_count": 0,
                "escalation_reasons": [],
                "tca_grade_distribution": {},
            }

        executed = sum(1 for r in records if r.get("executed", False))
        veto = sum(1 for r in records if r.get("veto", False))
        escalation = sum(1 for r in records if r.get("escalation", False))
        escalation_reasons = [
            r.get("escalation_reason", "")
            for r in records
            if r.get("escalation", False) and r.get("escalation_reason")
        ]

        # TCA 评级分布 (从 tca_post_report 提取)
        grade_dist: dict[str, int] = {}
        for r in records:
            post_report = r.get("tca_post_report")
            if isinstance(post_report, dict):
                grade = post_report.get("quality_grade", "")
                if grade:
                    grade_dist[grade] = grade_dist.get(grade, 0) + 1

        return {
            "total_executions": len(records),
            "executed_count": executed,
            "veto_count": veto,
            "escalation_count": escalation,
            "escalation_reasons": escalation_reasons,
            "tca_grade_distribution": grade_dist,
        }

    def _load_execution_audit(self, date_str: str) -> list[dict[str, Any]]:
        """加载指定日期的执行审计记录

        文件路径: reports/ai_decision/execution/exec_{date}.jsonl
        向后兼容: 字段缺失时降级跳过 (步骤 1 新增 escalation 字段)

        注意: 写入端 (execution_bridge._write_execution_audit) 用 %Y%m%d 格式
        (如 exec_20260728.jsonl, 无横线), 这里需把 date_str (YYYY-MM-DD) 转为
        %Y%m%d 才能匹配, 否则永远读不到当日审计文件.
        """
        # 写入端用 %Y%m%d (无横线), 这里统一格式
        date_compact = date_str.replace("-", "")
        path = _EXEC_AUDIT_DIR / f"exec_{date_compact}.jsonl"
        if not path.exists():
            # 降级: 读所有 exec_*.jsonl 按时间戳过滤
            return self._load_all_exec_audit_by_date(date_str)
        return self._load_jsonl(path)

    def _load_all_exec_audit_by_date(self, date_str: str) -> list[dict[str, Any]]:
        """从所有 exec_*.jsonl 文件按 timestamp 过滤指定日期"""
        if not _EXEC_AUDIT_DIR.exists():
            return []
        records: list[dict[str, Any]] = []
        for f in _EXEC_AUDIT_DIR.glob("exec_*.jsonl"):
            for r in self._load_jsonl(f):
                ts = r.get("timestamp", "")
                if ts.startswith(date_str):
                    records.append(r)
        return records

    def _load_jsonl(self, path: Path) -> list[dict[str, Any]]:
        """加载 JSONL 文件 (逐行 JSON)"""
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
        except Exception as exc:
            logger.error("[Dashboard] JSONL 加载失败 %s: %s", path, exc)
        return records

    # ------------------------------------------------------------
    # 预算告警
    # ------------------------------------------------------------

    def _check_budget_alerts(
        self,
        model_health: dict[str, Any],
        tca_summary: dict[str, Any],
        execution_summary: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """检查预算阈值, 返回告警列表

        告警规则:
        1. 模型延迟 p99 > per_call_latency_p99_ms → WARNING
        2. 模型熔断开启 → CRITICAL
        3. TCA 预估成本 > 50bps → WARNING
        4. TCA 否决率 > 30% → WARNING
        5. 执行 escalation 率 > 50% → WARNING
        6. TCA 评级 D/F 占比 > 20% → WARNING
        """
        alerts: list[dict[str, Any]] = []

        # 1. 模型延迟
        roles = model_health.get("roles", {})
        for role, detail in roles.items():
            latency = detail.get("latency_ms", 0.0)
            if latency > self._budget.per_call_latency_p99_ms:
                alerts.append({
                    "dimension": "model_latency",
                    "severity": "WARNING",
                    "role": role,
                    "value": latency,
                    "threshold": self._budget.per_call_latency_p99_ms,
                    "message": f"{role} 延迟 {latency:.0f}ms > 阈值 {self._budget.per_call_latency_p99_ms:.0f}ms",
                })

        # 2. 模型熔断
        open_breakers = model_health.get("open_breakers", [])
        for role in open_breakers:
            alerts.append({
                "dimension": "model_circuit_open",
                "severity": "CRITICAL",
                "role": role,
                "message": f"{role} 熔断开启, 已降级 MockProvider",
            })

        # 3. TCA 预估成本
        max_cost = tca_summary.get("max_cost_bps", 0.0)
        if max_cost > 50.0:
            alerts.append({
                "dimension": "tca_cost",
                "severity": "WARNING",
                "value": max_cost,
                "threshold": 50.0,
                "message": f"TCA 最高预估成本 {max_cost:.1f}bps > 50bps",
            })

        # 4. TCA 否决率
        total_est = tca_summary.get("total_estimates", 0)
        rejected = tca_summary.get("rejected_count", 0)
        if total_est > 0 and rejected / total_est > 0.3:
            alerts.append({
                "dimension": "tca_reject_rate",
                "severity": "WARNING",
                "value": rejected / total_est,
                "threshold": 0.3,
                "message": f"TCA 否决率 {rejected}/{total_est} = {rejected/total_est:.0%} > 30%",
            })

        # 5. 执行 escalation 率
        total_exec = execution_summary.get("total_executions", 0)
        esc_count = execution_summary.get("escalation_count", 0)
        if total_exec > 0 and esc_count / total_exec > 0.5:
            alerts.append({
                "dimension": "escalation_rate",
                "severity": "WARNING",
                "value": esc_count / total_exec,
                "threshold": 0.5,
                "message": f"escalation 率 {esc_count}/{total_exec} = {esc_count/total_exec:.0%} > 50%",
            })

        # 6. TCA 评级 D/F 占比
        grade_dist = execution_summary.get("tca_grade_distribution", {})
        total_graded = sum(grade_dist.values())
        d_f_count = grade_dist.get("D", 0) + grade_dist.get("F", 0)
        if total_graded > 0 and d_f_count / total_graded > 0.2:
            alerts.append({
                "dimension": "tca_grade_df",
                "severity": "WARNING",
                "value": d_f_count / total_graded,
                "threshold": 0.2,
                "message": f"TCA 评级 D/F 占比 {d_f_count}/{total_graded} = {d_f_count/total_graded:.0%} > 20%",
            })

        return alerts

    def check_budget_alert(self) -> list[dict[str, Any]]:
        """公开接口: 检查预算告警 (生成今日看板后提取告警)"""
        report = self.generate_daily_dashboard()
        return report.get("alerts", [])

    # ------------------------------------------------------------
    # 超预算降级
    # ------------------------------------------------------------

    def maybe_degrade_on_budget(self) -> dict[str, str]:
        """超预算时返回 role → 降级标记

        降级规则:
        1. 模型熔断开启 → 该 role 降级 Mock
        2. 模型延迟 p99 > 阈值 × 2 → 该 role 降级 Mock

        Returns:
            {role: "mock"} 需降级的 role 字典
        """
        report = self.generate_daily_dashboard()
        model_health = report.get("model_health", {})
        roles = model_health.get("roles", {})
        degrade: dict[str, str] = {}

        for role, detail in roles.items():
            # 熔断开启 → 降级
            if detail.get("circuit_open", False):
                degrade[role] = "mock"
                logger.warning("[Dashboard] %s 熔断, 降级 Mock", role)
                continue
            # 延迟超阈值 2 倍 → 降级
            latency = detail.get("latency_ms", 0.0)
            if latency > self._budget.per_call_latency_p99_ms * 2:
                degrade[role] = "mock"
                logger.warning(
                    "[Dashboard] %s 延迟 %.0fms > 2×阈值, 降级 Mock",
                    role, latency,
                )

        return degrade

    # ------------------------------------------------------------
    # Markdown 输出 (5 章节)
    # ------------------------------------------------------------

    def to_markdown(self, report: dict[str, Any]) -> str:
        """将报告转为 Markdown (5 章节)

        章节:
        1. 模型健康 (延迟/成功率/熔断状态)
        2. TCA 成本 (预估成本/否决率)
        3. 决策分布 (action/mode/verdict_type)
        4. 执行质量 (成功率/escalation/TCA 评级)
        5. 告警 (预算超限/熔断/异常)
        """
        date = report.get("date", "")
        lines: list[str] = [
            f"# ai_decision 每日看板 — {date}",
            "",
            f"> 生成时间: {report.get('generated_at', '')}",
            "",
        ]

        # 章节 1: 模型健康
        lines.extend(self._md_model_health(report.get("model_health", {})))
        # 章节 2: TCA 成本
        lines.extend(self._md_tca_summary(report.get("tca_summary", {})))
        # 章节 3: 决策分布
        lines.extend(self._md_decision_summary(report.get("decision_summary", {})))
        # 章节 4: 执行质量
        lines.extend(self._md_execution_summary(report.get("execution_summary", {})))
        # 章节 5: 告警
        lines.extend(self._md_alerts(report.get("alerts", [])))

        return "\n".join(lines)

    def _md_model_health(self, mh: dict[str, Any]) -> list[str]:
        """章节 1: 模型健康"""
        lines = ["## 1. 模型健康", ""]
        total = mh.get("total_roles", 0)
        healthy = mh.get("healthy_roles", [])
        unhealthy = mh.get("unhealthy_roles", [])
        open_brk = mh.get("open_breakers", [])
        lines.append(f"- 监控角色数: **{total}**")
        lines.append(f"- 健康角色: {', '.join(healthy) if healthy else 'N/A'}")
        lines.append(f"- 不健康角色: {', '.join(unhealthy) if unhealthy else '无'}")
        lines.append(f"- 熔断开启: {', '.join(open_brk) if open_brk else '无'}")
        lines.append("")
        # 角色详情表
        roles = mh.get("roles", {})
        if roles:
            lines.append("| 角色 | 健康 | 延迟(ms) | 熔断 | 连续失败 | Provider |")
            lines.append("|------|------|---------|------|---------|----------|")
            for role, d in roles.items():
                lines.append(
                    f"| {role} | {'✅' if d.get('healthy') else '❌'} | "
                    f"{d.get('latency_ms', 0):.1f} | "
                    f"{'🔴' if d.get('circuit_open') else '🟢'} | "
                    f"{d.get('consecutive_failures', 0)} | "
                    f"{d.get('provider_name', '')} |"
                )
            lines.append("")
        return lines

    def _md_tca_summary(self, tca: dict[str, Any]) -> list[str]:
        """章节 2: TCA 成本"""
        lines = ["## 2. TCA 成本", ""]
        total = tca.get("total_estimates", 0)
        approved = tca.get("approved_count", 0)
        rejected = tca.get("rejected_count", 0)
        avg_cost = tca.get("avg_cost_bps", 0.0)
        max_cost = tca.get("max_cost_bps", 0.0)
        lines.append(f"- 预估总数: **{total}**")
        lines.append(f"- 通过: {approved} | 否决: {rejected}")
        lines.append(f"- 平均成本: {avg_cost:.2f} bps | 最高成本: {max_cost:.2f} bps")
        symbols = tca.get("symbols", [])
        if symbols:
            lines.append(f"- 涉及标的: {', '.join(symbols[:10])}{'...' if len(symbols) > 10 else ''}")
        lines.append("")
        return lines

    def _md_decision_summary(self, dec: dict[str, Any]) -> list[str]:
        """章节 3: 决策分布"""
        lines = ["## 3. 决策分布", ""]
        total = dec.get("total_decisions", 0)
        lines.append(f"- 决策总数: **{total}**")
        action_dist = dec.get("action_distribution", {})
        if action_dist:
            lines.append("- Action 分布: " + " / ".join(
                f"{k}={v}" for k, v in action_dist.items()
            ))
        mode_dist = dec.get("mode_distribution", {})
        if mode_dist:
            lines.append("- Mode 分布: " + " / ".join(
                f"{k}={v}" for k, v in mode_dist.items()
            ))
        verdict_dist = dec.get("verdict_type_distribution", {})
        if verdict_dist:
            lines.append("- Verdict 分布: " + " / ".join(
                f"{k}={v}" for k, v in verdict_dist.items()
            ))
        lines.append("")
        return lines

    def _md_execution_summary(self, exe: dict[str, Any]) -> list[str]:
        """章节 4: 执行质量"""
        lines = ["## 4. 执行质量", ""]
        total = exe.get("total_executions", 0)
        executed = exe.get("executed_count", 0)
        veto = exe.get("veto_count", 0)
        esc = exe.get("escalation_count", 0)
        lines.append(f"- 执行总数: **{total}**")
        lines.append(f"- 成功执行: {executed} | 硬风控否决: {veto} | 升级人工: {esc}")
        # TCA 评级分布
        grade_dist = exe.get("tca_grade_distribution", {})
        if grade_dist:
            lines.append("- TCA 评级分布: " + " / ".join(
                f"{k}={v}" for k, v in sorted(grade_dist.items())
            ))
        # escalation 原因 Top 5
        reasons = exe.get("escalation_reasons", [])
        if reasons:
            from collections import Counter
            top5 = Counter(reasons).most_common(5)
            lines.append("- Escalation 原因 Top 5:")
            for reason, count in top5:
                lines.append(f"  - ({count}) {reason[:80]}")
        lines.append("")
        return lines

    def _md_alerts(self, alerts: list[dict[str, Any]]) -> list[str]:
        """章节 5: 告警"""
        lines = ["## 5. 告警", ""]
        if not alerts:
            lines.append("✅ 无告警")
            lines.append("")
            return lines
        # 按严重级别排序
        critical = [a for a in alerts if a.get("severity") == "CRITICAL"]
        warning = [a for a in alerts if a.get("severity") == "WARNING"]
        lines.append(f"- CRITICAL: **{len(critical)}** | WARNING: **{len(warning)}**")
        lines.append("")
        if critical:
            lines.append("### CRITICAL")
            for a in critical:
                lines.append(f"- 🔴 [{a.get('dimension', '')}] {a.get('message', '')}")
            lines.append("")
        if warning:
            lines.append("### WARNING")
            for a in warning:
                lines.append(f"- 🟡 [{a.get('dimension', '')}] {a.get('message', '')}")
            lines.append("")
        return lines

    # ------------------------------------------------------------
    # 落盘
    # ------------------------------------------------------------

    def save(self, report: dict[str, Any], date_str: str | None = None) -> str:
        """落盘 Markdown + JSON 双格式

        文件路径:
        - reports/ai_decision/dashboard_{date}.md
        - reports/ai_decision/dashboard_{date}.json

        Returns:
            Markdown 文件路径
        """
        if not date_str:
            date_str = report.get("date", datetime.now().strftime("%Y-%m-%d"))

        _REPORT_DIR.mkdir(parents=True, exist_ok=True)

        # JSON
        json_path = _REPORT_DIR / f"dashboard_{date_str}.json"
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)

        # Markdown
        md_path = _REPORT_DIR / f"dashboard_{date_str}.md"
        md_content = self.to_markdown(report)
        with open(md_path, "w", encoding="utf-8") as fh:
            fh.write(md_content)

        logger.info("[Dashboard] 看板已落盘: %s + %s", md_path, json_path)
        return str(md_path)
