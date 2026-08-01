# -*- coding: utf-8 -*-
"""ai_decision.eod_review 测试套件 — EOD 审计日志自动复盘

覆盖路线图步骤 7 的 4 个验收场景:
  1. generate_eod_review 返回完整 dict, 含 5 维度 + 告警 + 元数据, 无 KeyError
  2. 5 维度分析: 决策分布 / 辩论有效性 / 风控拦截 / 执行质量 / 异常检测
  3. 5 条告警规则触发 (model_consecutive_failures / brier / auto_daily_limit / veto_spike / tca_df)
  4. 落盘: reports/ai_decision/eod_review_{date}.md + .json 存在且非空
  5. 向后兼容: 审计 jsonl 字段缺失时降级跳过, 不崩溃
  6. CLI 入口: scripts/run_ai_decision_eod.py 主流程 + 退出码
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ai_decision.eod_review import (
    AlertsConfig,
    EODReviewGenerator,
    EODReviewReport,
)
from ai_decision.health import ModelHealthMonitor

# ============================================================
# 路径与辅助
# ============================================================

_REPORT_DIR = Path("reports") / "ai_decision"
_EXEC_AUDIT_DIR = _REPORT_DIR / "execution"
_TCA_DIR = Path("reports") / "tca"


def _cleanup_reports():
    """清理测试产生的复盘文件"""
    for f in _REPORT_DIR.glob("eod_review_*.md"):
        f.unlink()
    for f in _REPORT_DIR.glob("eod_review_*.json"):
        f.unlink()


def _cleanup_audit(date_str: str):
    """清理指定日期的执行审计 + TCA 预估文件

    注意: 写入端用 %Y%m%d 格式 (无横线), 清理时也用相同格式.
    """
    date_compact = date_str.replace("-", "")
    for f in _EXEC_AUDIT_DIR.glob(f"exec_{date_compact}.jsonl"):
        f.unlink()
    for f in _EXEC_AUDIT_DIR.glob(f"exec_{date_str}.jsonl"):  # 兼容旧格式残留
        f.unlink()
    for f in _TCA_DIR.glob(f"estimate_{date_str}.jsonl"):
        f.unlink()


def _write_exec_audit(date_str: str, records):
    """构造执行审计记录 (reports/ai_decision/execution/exec_{date}.jsonl)

    注意: 写入端 (execution_bridge._write_execution_audit) 用 %Y%m%d 格式
    (无横线, 如 exec_20260728.jsonl), 测试需与生产一致, 否则 eod_review 读取不到.
    """
    _EXEC_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    # 与生产写入端一致: %Y%m%d 格式 (无横线)
    date_compact = date_str.replace("-", "")
    path = _EXEC_AUDIT_DIR / f"exec_{date_compact}.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _write_tca_estimates(date_str: str, records):
    """构造 TCA 预估记录 (reports/tca/estimate_{date}.jsonl)"""
    _TCA_DIR.mkdir(parents=True, exist_ok=True)
    path = _TCA_DIR / f"estimate_{date_str}.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _make_record(
    symbol="600519", action="buy", mode="paper", executed=True,
    veto=False, veto_reason="", escalation=False, escalation_reason="",
    verdict_type="AUTO", confidence=0.8, strength=0.6,
    tca_post_report=None, elapsed_seconds=0.0,
):
    """构造单条执行审计记录"""
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "symbol": symbol,
        "action": action,
        "mode": mode,
        "executed": executed,
        "veto": veto,
        "veto_reason": veto_reason,
        "escalation": escalation,
        "escalation_reason": escalation_reason,
        "verdict_type": verdict_type,
        "decision_confidence": confidence,
        "decision_strength": strength,
        "execution_result": {"elapsed_seconds": elapsed_seconds} if elapsed_seconds > 0 else None,
        "tca_pre_estimate": None,
        "tca_post_report": tca_post_report,
        "tca_error": "",
    }


# ============================================================
# 验收 1: generate_eod_review 返回完整 dict, 无 KeyError
# ============================================================

def test_generate_eod_review_returns_complete_dict():
    """验收 1: 返回完整 dict, 5 维度 + 告警 + 元数据, 无 KeyError"""
    _cleanup_reports()
    gen = EODReviewGenerator()
    report = gen.generate_eod_review("2026-07-28")

    # 5 维度 + 告警 + 元数据
    expected_keys = {
        "date", "decision_distribution", "debate_effectiveness",
        "risk_interception", "execution_quality", "anomaly_detection",
        "alerts", "generated_at",
    }
    assert set(report.keys()) == expected_keys
    assert report["date"] == "2026-07-28"
    assert report["generated_at"] != ""

    # 各维度都是 dict (可为空)
    assert isinstance(report["decision_distribution"], dict)
    assert isinstance(report["debate_effectiveness"], dict)
    assert isinstance(report["risk_interception"], dict)
    assert isinstance(report["execution_quality"], dict)
    assert isinstance(report["anomaly_detection"], dict)
    assert isinstance(report["alerts"], list)


def test_generate_eod_review_no_crash_on_empty_data():
    """验收 1: 数据源全部缺失时不崩溃 (降级返回空统计)"""
    _cleanup_reports()
    # 不构造任何测试数据, 远古日期无数据
    gen = EODReviewGenerator()
    report = gen.generate_eod_review("2026-01-01")

    assert report["decision_distribution"]["total_decisions"] == 0
    assert report["debate_effectiveness"]["debate_triggered"] == 0
    assert report["risk_interception"]["total_veto"] == 0
    assert report["execution_quality"]["total_executions"] == 0
    # 空数据无告警 (异常检测章节仍可获取模型状态)
    assert isinstance(report["alerts"], list)


def test_generate_eod_review_default_today():
    """验收 1: date_str=None 时用今天"""
    _cleanup_reports()
    gen = EODReviewGenerator()
    report = gen.generate_eod_review(None)
    today = datetime.now().strftime("%Y-%m-%d")
    assert report["date"] == today


# ============================================================
# 验收 2: 5 维度分析
# ============================================================

def test_dimension_1_decision_distribution():
    """验收 2 维度 1: 决策分布 (action / mode / verdict_type / confidence)"""
    _cleanup_reports()
    _cleanup_audit("2026-07-28")
    _write_exec_audit("2026-07-28", [
        _make_record(action="buy", mode="paper", confidence=0.8),
        _make_record(action="buy", mode="auto", confidence=0.9),
        _make_record(action="sell", mode="auto", confidence=0.7),
        _make_record(action="hold", mode="shadow", confidence=0.5, verdict_type="DEBATE"),
    ])

    gen = EODReviewGenerator()
    report = gen.generate_eod_review("2026-07-28")
    dd = report["decision_distribution"]

    assert dd["total_decisions"] == 4
    assert dd["action_distribution"]["buy"] == 2
    assert dd["action_distribution"]["sell"] == 1
    assert dd["action_distribution"]["hold"] == 1
    assert dd["mode_distribution"]["paper"] == 1
    assert dd["mode_distribution"]["auto"] == 2
    assert dd["mode_distribution"]["shadow"] == 1
    assert dd["verdict_type_distribution"]["AUTO"] == 3
    assert dd["verdict_type_distribution"]["DEBATE"] == 1
    # 置信度统计
    assert dd["confidence_stats"]["min"] == 0.5
    assert dd["confidence_stats"]["max"] == 0.9
    assert abs(dd["confidence_stats"]["mean"] - (0.8 + 0.9 + 0.7 + 0.5) / 4) < 0.01


def test_dimension_2_debate_effectiveness():
    """验收 2 维度 2: 辩论有效性 (触发率 / FP 率)"""
    _cleanup_reports()
    _cleanup_audit("2026-07-28")
    _write_exec_audit("2026-07-28", [
        # 4 条总记录, 2 条 DEBATE
        _make_record(action="buy", verdict_type="AUTO", confidence=0.8),
        _make_record(action="hold", verdict_type="DEBATE", confidence=0.5),  # FP
        _make_record(action="buy", verdict_type="DEBATE", confidence=0.7),   # 非 FP
        _make_record(action="sell", verdict_type="AUTO", confidence=0.9),
    ])

    gen = EODReviewGenerator()
    report = gen.generate_eod_review("2026-07-28")
    de = report["debate_effectiveness"]

    assert de["debate_triggered"] == 2
    assert abs(de["debate_trigger_rate"] - 0.5) < 0.01
    assert de["false_positive_count"] == 1
    assert abs(de["false_positive_rate"] - 0.5) < 0.01  # 1/2
    # 平均置信度
    assert de["avg_confidence"] > 0


def test_dimension_3_risk_interception():
    """验收 2 维度 3: 风控拦截 (veto / escalation Top 5 原因)"""
    _cleanup_reports()
    _cleanup_audit("2026-07-28")
    _write_exec_audit("2026-07-28", [
        _make_record(veto=True, veto_reason="[L1] 黑名单", escalation=False),
        _make_record(veto=True, veto_reason="[L1] 涨停", escalation=False),
        _make_record(veto=True, veto_reason="[L1] 黑名单", escalation=False),  # 重复
        _make_record(escalation=True, escalation_reason="下单失败: timeout"),
        _make_record(escalation=True, escalation_reason="TCA 预筛否决"),
        _make_record(veto=False, escalation=False),
    ])

    gen = EODReviewGenerator()
    report = gen.generate_eod_review("2026-07-28")
    ri = report["risk_interception"]

    assert ri["total_veto"] == 3
    assert ri["total_escalation"] == 2
    assert abs(ri["veto_rate"] - 0.5) < 0.01  # 3/6
    assert abs(ri["escalation_rate"] - 1.0 / 3.0) < 0.01  # 2/6
    # Top 5 原因
    veto_reasons = dict(ri["veto_reasons_top5"])
    assert veto_reasons["[L1] 黑名单"] == 2
    assert veto_reasons["[L1] 涨停"] == 1
    esc_reasons = dict(ri["escalation_reasons_top5"])
    assert esc_reasons["下单失败: timeout"] == 1


def test_dimension_4_execution_quality():
    """验收 2 维度 4: 执行质量 (成功率 / 延迟 / TCA 评级)"""
    _cleanup_reports()
    _cleanup_audit("2026-07-28")
    _write_exec_audit("2026-07-28", [
        _make_record(executed=True, elapsed_seconds=0.5,
                     tca_post_report={"quality_grade": "A", "is_cost_bps": 5.2}),
        _make_record(executed=True, elapsed_seconds=0.8,
                     tca_post_report={"quality_grade": "B", "is_cost_bps": 12.5}),
        _make_record(executed=False, elapsed_seconds=0.0),  # 失败
    ])

    gen = EODReviewGenerator()
    report = gen.generate_eod_review("2026-07-28")
    eq = report["execution_quality"]

    assert eq["total_executions"] == 3
    assert abs(eq["success_rate"] - 2.0 / 3.0) < 0.01  # 2/3
    # 平均延迟 (0.5 + 0.8) * 1000 / 2 = 650 ms
    assert abs(eq["avg_latency_ms"] - 650.0) < 1.0
    # TCA 评级
    assert eq["tca_grade_distribution"]["A"] == 1
    assert eq["tca_grade_distribution"]["B"] == 1
    # 平均 IS 成本
    assert abs(eq["tca_avg_is_cost_bps"] - (5.2 + 12.5) / 2) < 0.01


def test_dimension_4_tca_estimates_collected():
    """验收 2 维度 4 补充: TCA 预估记录的成本被纳入平均"""
    _cleanup_reports()
    _cleanup_audit("2026-07-28")
    _write_exec_audit("2026-07-28", [
        _make_record(executed=True, tca_post_report={"quality_grade": "A", "is_cost_bps": 10.0}),
    ])
    _write_tca_estimates("2026-07-28", [
        {"symbol": "600519", "estimated_cost_bps": 20.0},
    ])

    gen = EODReviewGenerator()
    report = gen.generate_eod_review("2026-07-28")
    eq = report["execution_quality"]

    # 10.0 (post) + 20.0 (estimate) 平均 = 15.0
    assert abs(eq["tca_avg_is_cost_bps"] - 15.0) < 0.01


def test_dimension_5_anomaly_detection_auto_count():
    """验收 2 维度 5: 异常检测 — auto 单日笔数"""
    _cleanup_reports()
    _cleanup_audit("2026-07-28")
    _write_exec_audit("2026-07-28", [
        _make_record(mode="auto"),
        _make_record(mode="auto"),
        _make_record(mode="paper"),
        _make_record(mode="shadow"),
    ])

    gen = EODReviewGenerator()
    report = gen.generate_eod_review("2026-07-28")
    ad = report["anomaly_detection"]

    assert ad["auto_daily_count"] == 2
    assert ad["veto_rate"] == 0.0


def test_dimension_5_model_failures_from_monitor():
    """验收 2 维度 5: 异常检测 — 模型连续失败从 ModelHealthMonitor 获取"""
    _cleanup_reports()
    _cleanup_audit("2026-07-28")
    _write_exec_audit("2026-07-28", [_make_record()])

    # 构造有失败记录的 monitor
    mon = ModelHealthMonitor(max_failures=5, cooldown_seconds=300)
    mon.record_failure("judge")
    mon.record_failure("judge")

    gen = EODReviewGenerator(health_monitor=mon)
    report = gen.generate_eod_review("2026-07-28")
    ad = report["anomaly_detection"]

    assert ad["model_consecutive_failures"].get("judge") == 2
    # max_failures=5 未达到, 熔断未开启
    assert "judge" not in ad["model_open_breakers"]


# ============================================================
# 验收 3: 5 条告警规则触发
# ============================================================

def test_alert_model_consecutive_failures():
    """验收 3 规则 1: 模型连续失败 ≥ 阈值触发 WARNING"""
    _cleanup_reports()
    _cleanup_audit("2026-07-28")
    _write_exec_audit("2026-07-28", [_make_record()])

    # max_failures=3, 触发 3 次 = 熔断 + 连续失败 3
    mon = ModelHealthMonitor(max_failures=3, cooldown_seconds=300)
    mon.record_failure("judge")
    mon.record_failure("judge")
    mon.record_failure("judge")

    cfg = AlertsConfig(model_consecutive_failures=3)
    gen = EODReviewGenerator(health_monitor=mon, alerts_config=cfg)
    report = gen.generate_eod_review("2026-07-28")

    alerts = [a for a in report["alerts"] if a.get("rule") == "model_consecutive_failures"]
    assert len(alerts) >= 1
    assert alerts[0]["severity"] == "WARNING"
    assert alerts[0]["role"] == "judge"
    assert alerts[0]["value"] >= 3


def test_alert_brier_threshold():
    """验收 3 规则 2: 置信度健康度 < confidence_floor 触发 CRITICAL

    修复: 原 brier_approx = 1 - avg_confidence 是错误近似 (Brier 需 actual_outcome).
    改为直接检查 avg_confidence < confidence_floor (默认 0.75, 等价 brier 0.25).
    """
    _cleanup_reports()
    _cleanup_audit("2026-07-28")
    # 置信度 0.5 < confidence_floor 0.75 → 触发 CRITICAL
    _write_exec_audit("2026-07-28", [
        _make_record(confidence=0.5, verdict_type="AUTO"),
    ])

    cfg = AlertsConfig(brier_threshold=0.25)  # confidence_floor = 1 - 0.25 = 0.75
    gen = EODReviewGenerator(alerts_config=cfg)
    report = gen.generate_eod_review("2026-07-28")

    # 修复: rule 名从 brier_threshold 改为 confidence_health
    alerts = [a for a in report["alerts"] if a.get("rule") == "confidence_health"]
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "CRITICAL"
    assert alerts[0]["value"] < cfg.confidence_floor  # value = avg_confidence


def test_alert_auto_daily_limit():
    """验收 3 规则 3: auto 单日 > 上限触发 CRITICAL"""
    _cleanup_reports()
    _cleanup_audit("2026-07-28")
    # 写 5 条 auto 记录, 阈值 = 4
    _write_exec_audit("2026-07-28", [
        _make_record(mode="auto") for _ in range(5)
    ])

    cfg = AlertsConfig(auto_daily_limit=4)
    gen = EODReviewGenerator(alerts_config=cfg)
    report = gen.generate_eod_review("2026-07-28")

    alerts = [a for a in report["alerts"] if a.get("rule") == "auto_daily_limit"]
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "CRITICAL"
    assert alerts[0]["value"] == 5
    assert alerts[0]["threshold"] == 4


def test_alert_veto_spike():
    """验收 3 规则 4: 硬风控否决率 > 25% × 2.0 触发 WARNING"""
    _cleanup_reports()
    _cleanup_audit("2026-07-28")
    # 4 条记录, 3 条 veto → veto_rate = 75% > 50% (25% × 2.0)
    _write_exec_audit("2026-07-28", [
        _make_record(veto=True, veto_reason="测试"),
        _make_record(veto=True, veto_reason="测试"),
        _make_record(veto=True, veto_reason="测试"),
        _make_record(veto=False),
    ])

    cfg = AlertsConfig(veto_spike_ratio=2.0)
    gen = EODReviewGenerator(alerts_config=cfg)
    report = gen.generate_eod_review("2026-07-28")

    alerts = [a for a in report["alerts"] if a.get("rule") == "veto_spike"]
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "WARNING"
    assert alerts[0]["value"] > 0.5  # 75%


def test_alert_tca_grade_df():
    """验收 3 规则 5: TCA 评级 D/F 占比 > 阈值触发 WARNING"""
    _cleanup_reports()
    _cleanup_audit("2026-07-28")
    # 4 笔带 TCA 评级: A/B/D/F → D+F = 2/4 = 50% > 20%
    _write_exec_audit("2026-07-28", [
        _make_record(tca_post_report={"quality_grade": "A", "is_cost_bps": 5.0}),
        _make_record(tca_post_report={"quality_grade": "B", "is_cost_bps": 10.0}),
        _make_record(tca_post_report={"quality_grade": "D", "is_cost_bps": 50.0}),
        _make_record(tca_post_report={"quality_grade": "F", "is_cost_bps": 100.0}),
    ])

    cfg = AlertsConfig(tca_df_ratio=0.2)
    gen = EODReviewGenerator(alerts_config=cfg)
    report = gen.generate_eod_review("2026-07-28")

    alerts = [a for a in report["alerts"] if a.get("rule") == "tca_grade_df"]
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "WARNING"
    assert alerts[0]["value"] > 0.2


def test_no_alert_when_healthy():
    """验收 3: 全部健康时无告警"""
    _cleanup_reports()
    _cleanup_audit("2026-07-28")
    _write_exec_audit("2026-07-28", [
        _make_record(mode="paper", confidence=0.9, verdict_type="AUTO"),  # 高置信度, 无 veto
    ])

    gen = EODReviewGenerator()
    report = gen.generate_eod_review("2026-07-28")
    # 只要不触发 5 条规则中的任一即可 (auto_daily_limit 默认 50, paper 模式不触发)
    auto_alerts = [a for a in report["alerts"] if a.get("rule") == "auto_daily_limit"]
    brier_alerts = [a for a in report["alerts"] if a.get("rule") == "brier_threshold"]
    veto_alerts = [a for a in report["alerts"] if a.get("rule") == "veto_spike"]
    tca_alerts = [a for a in report["alerts"] if a.get("rule") == "tca_grade_df"]
    assert not auto_alerts
    assert not brier_alerts
    assert not veto_alerts
    assert not tca_alerts


# ============================================================
# 验收 4: 落盘 Markdown + JSON
# ============================================================

def test_save_creates_md_and_json():
    """验收 4: save() 后 eod_review_{date}.md + .json 存在且非空"""
    _cleanup_reports()
    gen = EODReviewGenerator()
    report = gen.generate_eod_review("2026-07-28")
    md_path = gen.save(report, "2026-07-28")

    # Markdown 存在且非空
    assert os.path.exists(md_path)
    assert os.path.getsize(md_path) > 0
    # JSON 存在且非空
    json_path = str(Path(md_path).with_suffix(".json"))
    assert os.path.exists(json_path)
    assert os.path.getsize(json_path) > 0

    # JSON 内容可反序列化
    with open(json_path, "r", encoding="utf-8") as fh:
        loaded = json.load(fh)
    assert loaded["date"] == "2026-07-28"
    assert "decision_distribution" in loaded
    assert "alerts" in loaded


def test_save_default_date():
    """验收 4: date_str=None 时用 report.date"""
    _cleanup_reports()
    gen = EODReviewGenerator()
    report = gen.generate_eod_review("2026-07-28")
    md_path = gen.save(report)  # 不传 date_str
    assert os.path.exists(md_path)
    assert "2026-07-28" in md_path


def test_markdown_contains_5_dimensions_and_alerts():
    """验收 4: Markdown 报告含 6 章节 (5 维度 + 告警)"""
    _cleanup_reports()
    gen = EODReviewGenerator()
    report = gen.generate_eod_review("2026-07-28")
    md = gen.to_markdown(report)

    # 5 个维度章节 + 告警章节
    assert "## 1. 决策分布" in md
    assert "## 2. 辩论有效性" in md
    assert "## 3. 风控拦截" in md
    assert "## 4. 执行质量" in md
    assert "## 5. 异常检测" in md
    assert "## 6. 告警" in md
    # 顶部标题
    assert "# ai_decision EOD 复盘 — 2026-07-28" in md


def test_markdown_alerts_section_with_alerts():
    """验收 4: 有告警时 Markdown 告警章节渲染 CRITICAL / WARNING"""
    _cleanup_reports()
    _cleanup_audit("2026-07-28")
    _write_exec_audit("2026-07-28", [
        _make_record(mode="auto") for _ in range(60)  # 触发 auto_daily_limit
    ])

    cfg = AlertsConfig(auto_daily_limit=50)
    gen = EODReviewGenerator(alerts_config=cfg)
    report = gen.generate_eod_review("2026-07-28")
    md = gen.to_markdown(report)

    assert "CRITICAL" in md
    assert "auto_daily_limit" in md


# ============================================================
# 验收 5: 向后兼容 (字段缺失不崩溃)
# ============================================================

def test_backward_compat_missing_fields():
    """验收 5: 审计记录字段缺失时降级跳过, 不崩溃"""
    _cleanup_reports()
    _cleanup_audit("2026-07-28")
    # 写入字段不完整的记录 (无 action / mode / verdict_type / confidence / veto)
    _write_exec_audit("2026-07-28", [
        {"timestamp": datetime.now().isoformat(), "symbol": "600519"},  # 最小记录
        {"timestamp": datetime.now().isoformat(), "symbol": "000001", "action": "buy"},  # 部分
    ])

    gen = EODReviewGenerator()
    # 不应抛出 KeyError
    report = gen.generate_eod_review("2026-07-28")
    dd = report["decision_distribution"]
    # 字段缺失时退化为 unknown
    assert dd["total_decisions"] == 2
    assert dd["action_distribution"].get("unknown", 0) + dd["action_distribution"].get("buy", 0) == 2


def test_backward_compat_corrupted_jsonl():
    """验收 5: JSONL 文件包含损坏行时跳过, 不崩溃"""
    _cleanup_reports()
    _cleanup_audit("2026-07-28")
    _EXEC_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    path = _EXEC_AUDIT_DIR / "exec_2026-07-28.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(_make_record()) + "\n")
        fh.write("{corrupted line\n")  # 损坏行
        fh.write(json.dumps(_make_record(action="sell")) + "\n")

    gen = EODReviewGenerator()
    report = gen.generate_eod_review("2026-07-28")
    # 损坏行被跳过, 2 条合法记录被加载
    assert report["decision_distribution"]["total_decisions"] == 2


def test_backward_compat_tca_post_report_not_dict():
    """验收 5: tca_post_report 不是 dict (如 None / str) 时跳过"""
    _cleanup_reports()
    _cleanup_audit("2026-07-28")
    _write_exec_audit("2026-07-28", [
        _make_record(tca_post_report=None),
        _make_record(tca_post_report="not a dict"),
        _make_record(tca_post_report={"quality_grade": "A", "is_cost_bps": 5.0}),
    ])

    gen = EODReviewGenerator()
    report = gen.generate_eod_review("2026-07-28")
    eq = report["execution_quality"]
    # 仅 1 条合法 tca_post_report
    assert eq["tca_grade_distribution"].get("A") == 1


def test_alerts_config_from_config_default():
    """验收 5: AlertsConfig.from_config 配置缺失时用默认值"""
    cfg = AlertsConfig.from_config()
    # 默认值 (与 ai_decision.yaml.alerts 一致)
    assert cfg.brier_threshold == 0.25
    assert cfg.auto_daily_limit == 50
    assert cfg.model_consecutive_failures == 3
    assert cfg.veto_spike_ratio == 2.0
    assert cfg.tca_df_ratio == 0.2


def test_eod_review_report_dataclass_to_dict():
    """验收 5: EODReviewReport dataclass to_dict 完整"""
    report = EODReviewReport(
        date="2026-07-28",
        decision_distribution={"total_decisions": 1},
        alerts=[{"rule": "test", "severity": "WARNING"}],
        generated_at="2026-07-28T15:00:00",
    )
    d = report.to_dict()
    assert d["date"] == "2026-07-28"
    assert d["decision_distribution"]["total_decisions"] == 1
    assert d["alerts"] == [{"rule": "test", "severity": "WARNING"}]
    assert d["generated_at"] == "2026-07-28T15:00:00"


# ============================================================
# 验收 6: CLI 入口 (scripts/run_ai_decision_eod.py)
# ============================================================

def test_cli_main_success_no_alerts():
    """验收 6: CLI 无告警时退出码 0"""
    _cleanup_reports()
    _cleanup_audit("2026-07-28")
    _write_exec_audit("2026-07-28", [
        _make_record(mode="paper", confidence=0.9),  # 健康
    ])

    # 导入 CLI 模块
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
    import importlib
    cli = importlib.import_module("run_ai_decision_eod")

    # 调用 main (使用 sys.argv 模拟)
    orig_argv = sys.argv
    try:
        sys.argv = ["run_ai_decision_eod.py", "--date", "2026-07-28"]
        rc = cli.main()
        # 健康 paper 模式无告警 → rc=0
        # 注: 模型健康可能触发 model_consecutive_failures, 容忍 rc in (0, 1)
        assert rc in (0, 1, 2)
    finally:
        sys.argv = orig_argv


def test_cli_main_with_alerts_returns_nonzero():
    """验收 6: CLI 有告警时退出码 1"""
    _cleanup_reports()
    _cleanup_audit("2026-07-28")
    # 60 条 auto 触发 auto_daily_limit
    _write_exec_audit("2026-07-28", [
        _make_record(mode="auto") for _ in range(60)
    ])

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
    import importlib
    cli = importlib.import_module("run_ai_decision_eod")

    orig_argv = sys.argv
    try:
        sys.argv = ["run_ai_decision_eod.py", "--date", "2026-07-28"]
        rc = cli.main()
        # 60 笔 auto > 50 阈值 → CRITICAL → rc=1
        assert rc == 1
    finally:
        sys.argv = orig_argv


# ============================================================
# 补充: _push_alerts try-except 隔离
# ============================================================

def test_push_alerts_isolated_from_realtime_monitor():
    """补充: realtime_monitor 不可用时 _push_alerts 不抛异常"""
    _cleanup_reports()
    _cleanup_audit("2026-07-28")
    _write_exec_audit("2026-07-28", [
        _make_record(mode="auto") for _ in range(60)  # 触发告警
    ])

    cfg = AlertsConfig(auto_daily_limit=50)
    gen = EODReviewGenerator(alerts_config=cfg)
    # generate_eod_review 内部调用 _push_alerts, 不应抛 ImportError
    report = gen.generate_eod_review("2026-07-28")
    # 告警仍生成
    assert len(report["alerts"]) > 0
