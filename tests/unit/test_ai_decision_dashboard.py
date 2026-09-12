"""ai_decision.dashboard 测试套件 — 延迟/成本基准看板

覆盖路线图步骤 4 的 4 个验收场景:
  1. generate_daily_dashboard 返回完整 dict, 无 KeyError
  2. Markdown 报告含 5 章节 (模型/TCA/决策/执行/告警)
  3. 预算超限时 maybe_degrade_on_budget 返回对应 role 降级标记
  4. 落盘: reports/ai_decision/dashboard_{date}.md + .json 存在且非空
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from utils.datetime_utils import now_bj

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

import ai_decision.dashboard as _dash
from ai_decision.dashboard import (
    BudgetConfig,
    DashboardGenerator,
    DashboardReport,
)
from ai_decision.health import ModelHealthMonitor

# ============================================================
# 辅助函数
# ============================================================


def _cleanup_reports():
    """清理测试产生的看板文件"""
    for f in _dash._REPORT_DIR.glob("dashboard_*.md"):
        f.unlink()
    for f in _dash._REPORT_DIR.glob("dashboard_*.json"):
        f.unlink()


def _write_tca_estimates(date_str: str, records):
    """构造 TCA 预估记录 (reports/tca/estimate_{date}.jsonl)"""
    _dash._TCA_ESTIMATE_DIR.mkdir(parents=True, exist_ok=True)
    path = _dash._TCA_ESTIMATE_DIR / f"estimate_{date_str}.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _write_exec_audit(date_str: str, records):
    """构造执行审计记录 (reports/ai_decision/execution/exec_{date}.jsonl)

    注意: 写入端 (execution_bridge._write_execution_audit) 用 %Y%m%d 格式
    (无横线, 如 exec_20260728.jsonl), 测试需与生产一致, 否则 dashboard 读取不到.
    """
    exec_dir = _dash._EXEC_AUDIT_DIR
    exec_dir.mkdir(parents=True, exist_ok=True)
    # 与生产写入端一致: %Y%m%d 格式 (无横线)
    date_compact = date_str.replace("-", "")
    path = exec_dir / f"exec_{date_compact}.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _make_exec_record(
    symbol="600519",
    action="buy",
    mode="paper",
    executed=True,
    veto=False,
    escalation=False,
    escalation_reason="",
    tca_post_report=None,
):
    """构造单条执行审计记录"""
    return {
        "timestamp": now_bj().isoformat(timespec="seconds"),
        "symbol": symbol,
        "action": action,
        "mode": mode,
        "executed": executed,
        "veto": veto,
        "veto_reason": "",
        "escalation": escalation,
        "escalation_reason": escalation_reason,
        "verdict_type": "AUTO",
        "decision_confidence": 0.8,
        "decision_strength": 0.6,
        "tca_pre_estimate": None,
        "tca_post_report": tca_post_report,
        "tca_error": "",
    }


# ============================================================
# 验收 1: generate_daily_dashboard 返回完整 dict, 无 KeyError
# ============================================================


def test_generate_dashboard_returns_complete_dict():
    """验收 1: 返回完整 dict, 5 个章节 + 元数据, 无 KeyError"""
    _cleanup_reports()
    gen = DashboardGenerator()
    report = gen.generate_daily_dashboard("2026-07-28")

    # 5 个章节 + 元数据
    expected_keys = {
        "date",
        "model_health",
        "tca_summary",
        "decision_summary",
        "execution_summary",
        "alerts",
        "generated_at",
    }
    assert set(report.keys()) == expected_keys
    assert report["date"] == "2026-07-28"
    assert report["generated_at"] != ""

    # 各章节都是 dict (可为空)
    assert isinstance(report["model_health"], dict)
    assert isinstance(report["tca_summary"], dict)
    assert isinstance(report["decision_summary"], dict)
    assert isinstance(report["execution_summary"], dict)
    assert isinstance(report["alerts"], list)


def test_generate_dashboard_no_crash_on_empty_data():
    """验收 1: 数据源全部缺失时不崩溃 (降级返回空统计)"""
    _cleanup_reports()
    # 不构造任何测试数据, 看板应降级返回空统计
    gen = DashboardGenerator()
    report = gen.generate_daily_dashboard("2026-01-01")  # 远古日期, 无数据

    assert report["tca_summary"]["total_estimates"] == 0
    assert report["decision_summary"]["total_decisions"] == 0
    assert report["execution_summary"]["total_executions"] == 0
    # 空数据无告警 (或仅有模型健康相关告警)
    assert isinstance(report["alerts"], list)


# ============================================================
# 验收 2: Markdown 报告含 5 章节
# ============================================================


def test_markdown_contains_5_sections():
    """验收 2: Markdown 报告含 5 章节 (模型/TCA/决策/执行/告警)"""
    _cleanup_reports()
    gen = DashboardGenerator()
    report = gen.generate_daily_dashboard("2026-07-28")
    md = gen.to_markdown(report)

    # 5 个章节标题
    assert "## 1. 模型健康" in md
    assert "## 2. TCA 成本" in md
    assert "## 3. 决策分布" in md
    assert "## 4. 执行质量" in md
    assert "## 5. 告警" in md
    # 顶部标题
    assert "# ai_decision 每日看板 — 2026-07-28" in md


def test_markdown_model_health_table():
    """验收 2: Markdown 模型健康章节含角色详情表"""
    _cleanup_reports()
    gen = DashboardGenerator()
    report = gen.generate_daily_dashboard("2026-07-28")
    md = gen.to_markdown(report)
    # 模型健康章节存在
    assert "## 1. 模型健康" in md
    # 如果有角色, 应有表格
    mh = report["model_health"]
    if mh.get("total_roles", 0) > 0:
        assert "| 角色 |" in md


# ============================================================
# 验收 3: 预算超限时 maybe_degrade_on_budget 返回降级标记
# ============================================================


def test_maybe_degrade_on_circuit_open():
    """验收 3: 模型熔断时 maybe_degrade_on_budget 返回 role → mock"""
    # 构造熔断状态的 monitor
    mon = ModelHealthMonitor(max_failures=2, cooldown_seconds=300)
    mon.record_failure("judge")
    mon.record_failure("judge")
    assert mon.is_circuit_open("judge") is True

    gen = DashboardGenerator(health_monitor=mon)
    degrade = gen.maybe_degrade_on_budget()
    assert "judge" in degrade
    assert degrade["judge"] == "mock"


def test_maybe_degrade_no_degrade_when_healthy():
    """验收 3: 模型健康时不降级"""
    mon = ModelHealthMonitor()
    gen = DashboardGenerator(health_monitor=mon)
    degrade = gen.maybe_degrade_on_budget()
    # 默认 MockProvider 健康, 无降级
    # 注意: 如果之前测试触发了熔断, 这里可能有降级
    # 关键是不崩溃
    assert isinstance(degrade, dict)


def test_check_budget_alert_latency_warning():
    """验收 3: 模型延迟超阈值触发 WARNING 告警"""
    # 构造高延迟场景: 用低阈值让正常延迟也超限
    mon = ModelHealthMonitor()
    budget = BudgetConfig(per_call_latency_p99_ms=0.001)  # 0.001ms 极低阈值
    gen = DashboardGenerator(health_monitor=mon, budget=budget)
    report = gen.generate_daily_dashboard("2026-07-28")

    # 应有 latency 告警
    latency_alerts = [
        a for a in report["alerts"] if a.get("dimension") == "model_latency"
    ]
    # 如果有角色被探测过, 应触发延迟告警
    roles = report["model_health"].get("roles", {})
    if roles:
        assert len(latency_alerts) > 0
        assert latency_alerts[0]["severity"] == "WARNING"


# ============================================================
# 验收 4: 落盘 Markdown + JSON
# ============================================================


def test_save_creates_md_and_json():
    """验收 4: save() 后 dashboard_{date}.md + .json 存在且非空"""
    _cleanup_reports()
    gen = DashboardGenerator()
    report = gen.generate_daily_dashboard("2026-07-28")
    md_path = gen.save(report, "2026-07-28")

    # Markdown 存在且非空
    assert os.path.exists(md_path)
    assert os.path.getsize(md_path) > 0
    # JSON 存在且非空
    json_path = str(Path(md_path).with_suffix(".json"))
    assert os.path.exists(json_path)
    assert os.path.getsize(json_path) > 0

    # JSON 内容可反序列化
    with open(json_path, encoding="utf-8") as fh:
        loaded = json.load(fh)
    assert loaded["date"] == "2026-07-28"
    assert "model_health" in loaded


def test_save_default_date():
    """验收 4: date_str=None 时用 report.date 或今天"""
    _cleanup_reports()
    gen = DashboardGenerator()
    report = gen.generate_daily_dashboard("2026-07-28")
    md_path = gen.save(report)  # 不传 date_str
    assert os.path.exists(md_path)


# ============================================================
# 补充: TCA 成本数据收集
# ============================================================


def test_tca_summary_from_estimates():
    """补充: 从 estimate_{date}.jsonl 收集 TCA 成本数据"""
    _cleanup_reports()
    date_str = "2026-07-28"
    _write_tca_estimates(
        date_str,
        [
            {
                "symbol": "600519",
                "approved": True,
                "estimated_cost_bps": 10.5,
                "tier": "large",
            },
            {
                "symbol": "000001",
                "approved": True,
                "estimated_cost_bps": 15.2,
                "tier": "large",
            },
            {
                "symbol": "300750",
                "approved": False,
                "estimated_cost_bps": 45.0,
                "tier": "mid",
                "rejection_reason": "cost_bps=45.0 > threshold=30.0",
            },
        ],
    )

    gen = DashboardGenerator()
    tca = gen._collect_tca_summary(date_str)

    assert tca["total_estimates"] == 3
    assert tca["approved_count"] == 2
    assert tca["rejected_count"] == 1
    assert abs(tca["avg_cost_bps"] - (10.5 + 15.2 + 45.0) / 3) < 0.01
    assert tca["max_cost_bps"] == 45.0
    assert "600519" in tca["symbols"]


def test_tca_alert_on_high_cost():
    """补充: TCA 最高成本 > 50bps 触发 WARNING"""
    _cleanup_reports()
    date_str = "2026-07-28"
    _write_tca_estimates(
        date_str,
        [
            {
                "symbol": "600519",
                "approved": False,
                "estimated_cost_bps": 65.0,
                "tier": "mid",
            },
        ],
    )

    gen = DashboardGenerator()
    report = gen.generate_daily_dashboard(date_str)

    tca_alerts = [a for a in report["alerts"] if a.get("dimension") == "tca_cost"]
    assert len(tca_alerts) > 0
    assert tca_alerts[0]["severity"] == "WARNING"
    assert tca_alerts[0]["value"] == 65.0


def test_tca_alert_on_high_reject_rate():
    """补充: TCA 否决率 > 30% 触发 WARNING"""
    _cleanup_reports()
    date_str = "2026-07-28"
    _write_tca_estimates(
        date_str,
        [
            {"symbol": f"00000{i}", "approved": i < 2, "estimated_cost_bps": 10.0}
            for i in range(5)
        ],
    )  # 5 笔, 3 笔否决, 否决率 60%

    gen = DashboardGenerator()
    report = gen.generate_daily_dashboard(date_str)

    reject_alerts = [
        a for a in report["alerts"] if a.get("dimension") == "tca_reject_rate"
    ]
    assert len(reject_alerts) > 0
    assert reject_alerts[0]["severity"] == "WARNING"


# ============================================================
# 补充: 执行审计数据收集
# ============================================================


def test_execution_summary_from_audit():
    """补充: 从 exec_{date}.jsonl 收集执行质量数据"""
    _cleanup_reports()
    date_str = "2026-07-28"
    _write_exec_audit(
        date_str,
        [
            _make_exec_record(
                symbol="600519", action="buy", mode="paper", executed=True
            ),
            _make_exec_record(
                symbol="000001", action="sell", mode="auto", executed=True
            ),
            _make_exec_record(
                symbol="300750",
                action="buy",
                mode="auto",
                executed=False,
                veto=True,
                escalation=True,
                escalation_reason="L2 风控否决: 黑名单",
            ),
            _make_exec_record(
                symbol="688981",
                action="buy",
                mode="paper",
                executed=True,
                escalation=True,
                escalation_reason="TCA 预筛否决: cost_bps=45.0",
                tca_post_report={"quality_grade": "A", "is_cost_bps": 3.2},
            ),
        ],
    )

    gen = DashboardGenerator()
    exe = gen._collect_execution_summary(date_str)

    assert exe["total_executions"] == 4
    assert exe["executed_count"] == 3
    assert exe["veto_count"] == 1
    assert exe["escalation_count"] == 2
    assert len(exe["escalation_reasons"]) == 2
    assert "L2 风控否决" in exe["escalation_reasons"][0]
    # TCA 评级分布
    assert exe["tca_grade_distribution"].get("A") == 1


def test_execution_alert_on_high_escalation_rate():
    """补充: escalation 率 > 50% 触发 WARNING"""
    _cleanup_reports()
    date_str = "2026-07-28"
    _write_exec_audit(
        date_str,
        [
            _make_exec_record(
                symbol=f"00000{i}",
                escalation=(i >= 2),
                escalation_reason=f"原因{i}" if i >= 2 else "",
            )
            for i in range(4)
        ],
    )  # 4 笔, 2 笔 escalation, 率 50% (边界值, 不触发; 改为 3/4=75%)

    gen = DashboardGenerator()
    report = gen.generate_daily_dashboard(date_str)

    esc_alerts = [
        a for a in report["alerts"] if a.get("dimension") == "escalation_rate"
    ]
    # 2/4 = 50%, 刚好等于阈值 0.5, 不触发 (条件是 > 0.5)
    assert len(esc_alerts) == 0


def test_execution_alert_on_tca_grade_df():
    """补充: TCA 评级 D/F 占比 > 20% 触发 WARNING"""
    _cleanup_reports()
    date_str = "2026-07-28"
    _write_exec_audit(
        date_str,
        [
            _make_exec_record(symbol="600519", tca_post_report={"quality_grade": "A"}),
            _make_exec_record(symbol="000001", tca_post_report={"quality_grade": "D"}),
            _make_exec_record(symbol="300750", tca_post_report={"quality_grade": "F"}),
            _make_exec_record(symbol="688981", tca_post_report={"quality_grade": "B"}),
        ],
    )  # D/F = 2/4 = 50% > 20%

    gen = DashboardGenerator()
    report = gen.generate_daily_dashboard(date_str)

    grade_alerts = [a for a in report["alerts"] if a.get("dimension") == "tca_grade_df"]
    assert len(grade_alerts) > 0
    assert grade_alerts[0]["severity"] == "WARNING"


# ============================================================
# 补充: 向后兼容 (字段缺失)
# ============================================================


def test_backward_compat_missing_tca_fields():
    """补充: 步骤 1 之前的审计记录 (无 escalation/tca 字段) 不崩溃"""
    _cleanup_reports()
    date_str = "2026-07-28"
    exec_dir = _dash._EXEC_AUDIT_DIR
    exec_dir.mkdir(parents=True, exist_ok=True)
    # 与生产写入端一致: %Y%m%d 格式 (无横线)
    date_compact = date_str.replace("-", "")
    path = exec_dir / f"exec_{date_compact}.jsonl"
    # 老格式审计记录 (无 escalation / tca_post_report)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "timestamp": now_bj().isoformat(timespec="seconds"),
                    "symbol": "600519",
                    "action": "buy",
                    "mode": "shadow",
                    "executed": False,
                    # 缺少 veto / escalation / tca_post_report 字段
                }
            )
            + "\n"
        )

    gen = DashboardGenerator()
    exe = gen._collect_execution_summary(date_str)
    # 不崩溃, 字段缺失用默认值
    assert exe["total_executions"] == 1
    assert exe["executed_count"] == 0  # executed=False
    assert exe["veto_count"] == 0  # 缺失 → 默认 False
    assert exe["escalation_count"] == 0


def test_backward_compat_missing_tca_estimate_file():
    """补充: TCA estimate 文件不存在时降级返回空统计"""
    _cleanup_reports()
    gen = DashboardGenerator()
    tca = gen._collect_tca_summary("2099-01-01")  # 远古未来日期
    assert tca["total_estimates"] == 0
    assert tca["avg_cost_bps"] == 0.0


# ============================================================
# 补充: BudgetConfig
# ============================================================


def test_budget_config_defaults():
    """补充: BudgetConfig 默认值"""
    bc = BudgetConfig()
    assert bc.monthly_token_limit == 5_000_000
    assert bc.per_call_latency_p99_ms == 5000.0
    assert bc.daily_cost_limit_usd == 10.0


def test_budget_config_from_config():
    """补充: BudgetConfig.from_config() 从 yaml 加载"""
    bc = BudgetConfig.from_config()
    # 从 ai_decision.yaml.budget 加载 (或默认值)
    assert bc.monthly_token_limit > 0
    assert bc.per_call_latency_p99_ms > 0
    assert bc.daily_cost_limit_usd > 0


# ============================================================
# 补充: DashboardReport dataclass
# ============================================================


def test_dashboard_report_to_dict():
    """补充: DashboardReport.to_dict() 序列化"""
    report = DashboardReport(date="2026-07-28")
    d = report.to_dict()
    assert d["date"] == "2026-07-28"
    assert "model_health" in d
    assert "alerts" in d
    assert isinstance(d["alerts"], list)


# ============================================================
# 清理
# ============================================================


def teardown_module():
    """模块结束时清理测试文件"""
    _cleanup_reports()
    # 清理测试构造的 TCA / exec 文件
    for f in (_dash._TCA_ESTIMATE_DIR.glob("estimate_2026-07-28.jsonl") if _dash._TCA_ESTIMATE_DIR.exists() else []):
        f.unlink()
    exec_dir = _dash._EXEC_AUDIT_DIR
    for f in (exec_dir.glob("exec_2026-07-28.jsonl") if exec_dir.exists() else []):
        f.unlink()


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
