#!/usr/bin/env python3
"""N1-N6 进化框架验证脚本 (v2, 对齐现有实现)

运行: python scripts/verify_evolution_v2.py
"""

import os
import sys
from datetime import date

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "scripts"))
sys.path.insert(0, os.path.join(BASE, "v8.3_institutional", "src"))


# ============================================================
# N1: 漂移检测 + 重训触发
# ============================================================
def test_n1_drift_detector_interface():
    """N1: 验证 ModelDriftDetector 接口可用 (不触发真实重训)

    注: 实际加载版本为 v8.3_institutional/src/ml/drift_detector.py (旧版, 无 T17 OOS Gap).
    四要素齐全: IC衰减 + ADWIN + KS + PSI.
    """
    from ml.drift_detector import ModelDriftDetector

    detector = ModelDriftDetector()
    today = date.today()
    for _i in range(25):
        detector.update_ic(today, 0.05)
    alerts = detector.check_all()
    assert isinstance(alerts, list), f"check_all 应返回 list, 实际 {type(alerts)}"
    need, reason = detector.should_retrain()
    assert isinstance(need, bool) and isinstance(reason, str)
    detector.update_adwin(0.05)
    report = detector.generate_report()
    assert "ic_stats" in report and "should_retrain" in report
    print(f"✓ N1 ModelDriftDetector 接口通过: need_retrain={need}, alerts={len(alerts)}")


def test_n1_positions_and_capital():
    """N1: 验证 positions.json 与总资金可读取 (N2 依赖)"""
    import json

    pos_path = os.path.join(BASE, "config", "positions.json")
    assert os.path.exists(pos_path), "config/positions.json 不存在"
    with open(pos_path, encoding="utf-8") as f:
        data = json.load(f)
    assert "positions" in data and isinstance(data["positions"], dict), "positions 字段应为 dict"
    total_capital = float(data.get("meta", {}).get("total_capital", 0))
    assert total_capital > 0, f"total_capital 应 > 0, 实际 {total_capital}"
    n_pos = len(data["positions"])
    print(f"✓ N1 持仓与资金通过: {n_pos} 个持仓, total_capital={total_capital:,.0f}")


def test_n1_report_dir():
    """N1: 验证 report_dir 路径与 QLib 报告查找逻辑"""
    report_dir = os.path.join(BASE, "reports")
    assert os.path.isdir(report_dir), "reports/ 目录不存在"
    candidates = []
    for fname in os.listdir(report_dir):
        if fname.startswith("qlib_") and fname.endswith(".json"):
            full = os.path.join(report_dir, fname)
            candidates.append((os.path.getmtime(full), full))
    candidates.sort(reverse=True)
    if candidates:
        print(f"✓ N1 report_dir 通过: 找到 {len(candidates)} 个 QLib 报告, 最新={os.path.basename(candidates[0][1])}")
    else:
        print("✓ N1 report_dir 通过: 目录存在但无 QLib 报告 (N1 会回退到 None)")


# ============================================================
# N2: 策略多维评分器
# ============================================================
def test_n2_strategy_evaluator():
    """N2: 测试多维评分器 (实际实现位于 scripts/strategy_evaluator.py)"""
    try:
        from scripts.strategy_evaluator import StrategyEvaluator
    except ImportError:
        try:
            from strategy_evaluator import StrategyEvaluator
        except ImportError:
            print("⚠ N2 StrategyEvaluator 未导入 (尚未实施), 跳过")
            return

    # 启用 Feature Flag
    os.environ["USE_STRATEGY_EVALUATOR"] = "True"

    evaluator = StrategyEvaluator()
    report = evaluator.evaluate()

    # 验证 ScoreReport dataclass 关键字段
    assert hasattr(report, "overall_score"), "report 应包含 overall_score"
    assert hasattr(report, "is_degraded"), "report 应包含 is_degraded"
    assert hasattr(report, "return_metrics"), "report 应包含 return_metrics"
    assert hasattr(report, "divers_metrics"), "report 应包含 divers_metrics"

    score = float(report.overall_score or 0)
    assert 0.0 <= score <= 1.0, f"overall_score [{score}] 超出 [0,1] 范围"

    print(f"✓ N2 StrategyEvaluator 通过: overall={score:.4f}, degraded={report.is_degraded}")


# ============================================================
# N3: IC 数据管道
# ============================================================
def test_n3_ic_recorder():
    """N3: 测试 IC 记录器 (scripts/ic_recorder.py)"""
    try:
        from scripts.ic_recorder import (
            compute_ic_from_signals,
            load_ic_store,
            record_daily_ic,
        )
    except ImportError:
        try:
            from ic_recorder import (
                compute_ic_from_signals,
                load_ic_store,
                record_daily_ic,
            )
        except ImportError:
            print("⚠ N3 ICRecorder 未导入 (尚未实施), 跳过")
            return

    signals = [{"predicted_return": 0.01 * i, "actual_return": 0.012 * i} for i in range(15)]
    ic = compute_ic_from_signals(signals, min_samples=10)
    assert ic is not None and 0.0 <= abs(ic) <= 1.0
    record_daily_ic(ic, trade_date=date(2026, 7, 29), source="verify_v2")
    store = load_ic_store()
    assert store["latest_ic"] == ic
    assert any(h["source"] == "verify_v2" for h in store["history"])
    print(f"✓ N3 ICRecorder 通过: IC={ic:.4f}")


# ============================================================
# N4: 超参数自适应搜索
# ============================================================
def test_n4_adaptive_optimize():
    """N4: 验证 adaptive_optimize (scripts/adaptive_optimize.py)"""
    try:
        from scripts.adaptive_optimize import (
            adaptive_optimize,
        )
    except ImportError:
        try:
            from adaptive_optimize import (
                adaptive_optimize,
            )  # noqa: F401
        except ImportError:
            print("⚠ N4 adaptive_optimize 未导入 (尚未实施), 跳过")
            return

    base_config = {"lgb_params": {"max_depth": 8, "num_leaves": 31, "learning_rate": 0.05}}
    result = adaptive_optimize("NONEXISTENT_SYMBOL", base_config)
    assert "lgb_params" in result, "返回应包含 lgb_params"
    assert "_adaptive_meta" in result, "返回应包含 _adaptive_meta"

    # 验证不原地修改原配置
    assert base_config["lgb_params"]["max_depth"] == 8, "原配置不应被修改"

    meta = result["_adaptive_meta"]
    print(
        f"✓ N4 adaptive_optimize 通过: severity={meta.get('severity')}, "
        f"level={meta.get('level')}, "
        f"lr={result['lgb_params'].get('learning_rate')}"
    )


# ============================================================
# N5: 经验沉淀管理器
# ============================================================
def test_n5_skill_manager():
    """N5: 验证经验沉淀 (scripts/skill_manager.py)"""
    try:
        from scripts.skill_manager import SkillManager, record_lesson
    except ImportError:
        try:
            from skill_manager import SkillManager, record_lesson
        except ImportError:
            print("⚠ N5 SkillManager 未导入 (尚未实施), 跳过")
            return

    # 启用 Feature Flag
    os.environ["USE_SKILL_MANAGER"] = "True"

    sm = SkillManager()
    sm.save_experience(
        "588080.SH",
        {
            "type": "retrain_success",
            "description": "verify_v2 测试经验记录",
            "impact": "positive",
            "action_taken": "adaptive_retrain",
            "verified": True,
        },
        enabled=True,
    )
    recent = sm.get_recent_lessons(symbol="588080.SH", days=1)
    assert any(
        lesson.get("description") == "verify_v2 测试经验记录" for lesson in recent
    ), f"未找到测试记录, 实际: {[lesson.get('description') for lesson in recent]}"

    # 验证便捷函数 record_lesson
    ok = record_lesson(
        "PORTFOLIO",
        "strategy_degradation",
        "verify_v2 便捷函数测试",
        impact="negative",
        action_taken="alert_only",
        verified=False,
    )
    assert ok is True

    print(f"✓ N5 SkillManager 通过: 最近记录数={len(recent)}")


# ============================================================
# N6: 调度器评估流注入
# ============================================================
def test_n6_evaluation_task():
    """N6: 验证评估任务函数可调用 (dry-run)"""
    try:
        from live_scheduler import run_strategy_evaluation
    except ImportError:
        print("⚠ N6 run_strategy_evaluation 未导入 (尚未实施), 跳过")
        return

    # dry_run=True + Feature Flag 开启
    os.environ["USE_STRATEGY_EVALUATION"] = "True"
    result = run_strategy_evaluation(dry_run=True)
    assert result["status"] == "OK", f"dry-run 应返回 OK, 实际 {result}"
    assert result["data"].get("dry_run") is True, f"dry-run data 应含 dry_run=True, 实际 {result['data']}"

    # 关闭 Feature Flag, 验证优雅降级
    os.environ.pop("USE_STRATEGY_EVALUATION", None)
    result2 = run_strategy_evaluation(dry_run=False)
    assert result2["status"] == "OK", "Flag 关闭时应优雅降级而非失败"
    assert result2["data"].get("enabled") is False

    print("✓ N6 run_strategy_evaluation 通过: dry-run OK, 降级 OK")


# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("  v8.4 自我进化框架 v2 集成验证")
    print("=" * 60)
    test_n1_drift_detector_interface()
    test_n1_positions_and_capital()
    test_n1_report_dir()
    test_n2_strategy_evaluator()
    test_n3_ic_recorder()
    test_n4_adaptive_optimize()
    test_n5_skill_manager()
    test_n6_evaluation_task()
    print("=" * 60)
    print("  ✅ 所有可验证组件通过!")
    print("=" * 60)
