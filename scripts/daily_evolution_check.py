#!/usr/bin/env python3
"""每日演化检查器 — 观察期内仅监控+评估, 不触发任何进化动作.

ARCHITECTURE_自我进化框架 §7 — 观察期 (T-NEXT-1.1 + T-NEXT-1.2)
创建: 2026-08-02

功能:
    1. DriftMonitor (仅监控模式, sim_mode=True, 不连接重训练回调)
    2. StrategyEvaluator (只读评估模式, evaluate_only)
    3. 观察期跟踪器更新

运行:
    py -X utf8 scripts/daily_evolution_check.py
    py -X utf8 scripts/daily_evolution_check.py --json

输出:
    - reports/evolution/drift_alerts.jsonl     → 漂移告警
    - reports/evolution/score_reports/         → 评分报告
    - reports/evolution/observation_progress.json → 进度更新
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from utils.datetime_utils import now_bj

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("daily_evolution_check")

EVOLUTION_DIR = PROJECT_ROOT / "reports" / "evolution"
EVOLUTION_DIR.mkdir(parents=True, exist_ok=True)
SCORE_REPORTS_DIR = EVOLUTION_DIR / "score_reports"
SCORE_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
DRIFT_ALERTS_FILE = EVOLUTION_DIR / "drift_alerts.jsonl"
SCORE_TREND_FILE = EVOLUTION_DIR / "score_trend.json"
DAILY_BRIEFING_FILE = (
    EVOLUTION_DIR / f"daily_briefing_{now_bj().strftime('%Y%m%d')}.md"
)


def load_shadow_returns() -> tuple[list[float], list[str]]:
    """加载 Shadow 账户日收益数据."""
    shadow_path = PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"
    if not shadow_path.exists():
        logger.warning("Shadow daily_returns.jsonl 不存在: %s", shadow_path)
        return [], []

    returns: list[float] = []
    dates: list[str] = []
    try:
        with open(shadow_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if "daily_return" in record:
                    returns.append(float(record["daily_return"]))
                    dates.append(record.get("date", ""))
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
        logger.exception("读取 Shadow 数据失败: %s", e)

    return returns, dates


def run_drift_monitor_check() -> dict[str, Any]:
    """T-NEXT-1.1: DriftMonitor 仅监控模式.

    sim_mode=True: 绕过 Feature Flag 直接激活
    retrain_callback=None: 不触发任何重训练 (监控模式)
    """
    logger.info("=" * 50)
    logger.info("T-NEXT-1.1: DriftMonitor 仅监控模式")
    logger.info("=" * 50)

    try:
        import numpy as np

        from utils.alpha.drift_monitor import (
            SimModeDriftMonitor,
            compute_prediction_drift,
        )
    except ImportError as e:
        logger.exception("DriftMonitor 导入失败: %s", e)
        return {"status": "import_error", "error": str(e)}

    result = {
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "module": "drift_monitor",
        "mode": "monitoring_only",
        "status": "skipped",
        "reason": "",
        "alerts": [],
    }

    try:
        # 创建 sim_mode=True 的监控器 (仅监控, 不连接重训练)
        monitor = SimModeDriftMonitor(
            model_name="v9_lgb",
            model_version="observation_period",
            sim_mode=True,  # 绕过 Feature Flag, 强制激活
            feature_columns=None,
            reports_dir=str(PROJECT_ROOT / "reports" / "drift"),
        )

        if not monitor.is_active():
            result["reason"] = "monitor_not_active"
            logger.info("DriftMonitor: 未激活")
            return result

        # 暂无完整特征 panel, 用预测漂移替代
        daily_returns, dates = load_shadow_returns()
        if len(daily_returns) < 5:
            result["reason"] = (
                f"insufficient_data ({len(daily_returns)} samples, need >=5)"
            )
            logger.warning("DriftMonitor: 数据不足 (n=%d)", len(daily_returns))
            return result

        logger.info("DriftMonitor: 加载 %d 条 Shadow 记录", len(daily_returns))

        # 基线 = 前 60% 数据, 当前 = 后 40% 数据
        split_idx = max(2, len(daily_returns) * 3 // 5)
        baseline_returns = np.array(daily_returns[:split_idx])
        current_returns = np.array(daily_returns[split_idx:])

        # 检查预测漂移 (收益分布变化)
        drift_report = compute_prediction_drift(
            baseline=baseline_returns,
            current=current_returns,
            model_name="v9_lgb",
            model_version="observation_period",
        )

        alert_entry = drift_report.to_dict()
        alert_entry["data_source"] = "shadow_daily_returns"
        alert_entry["n_baseline"] = int(len(baseline_returns))
        alert_entry["n_current"] = int(len(current_returns))

        # 持久化
        try:
            with open(DRIFT_ALERTS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(alert_entry, ensure_ascii=False, default=str) + "\n")
            logger.info("DriftMonitor: 告警已持久化 -> %s", DRIFT_ALERTS_FILE)
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
            logger.warning("告警持久化失败: %s", e)

        result["status"] = "completed"
        result["alerts"] = [alert_entry]
        logger.info(
            "DriftMonitor: 完成 — severity=%s, KS=%.4f, PSI=%.4f",
            drift_report.severity.value,
            drift_report.drift_score,
            drift_report.psi,
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
        logger.exception("DriftMonitor 检查异常: %s", e)
        result["status"] = "error"
        result["reason"] = str(e)

    return result


def run_strategy_evaluation() -> dict[str, Any]:
    """T-NEXT-1.2: StrategyEvaluator 只读评估模式.

    使用 evaluate_from_jsonl() 读取 Shadow 数据
    产出 Public/Private 分离的评分报告
    """
    logger.info("=" * 50)
    logger.info("T-NEXT-1.2: StrategyEvaluator 只读评估模式")
    logger.info("=" * 50)

    result = {
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "module": "strategy_evaluator",
        "mode": "evaluate_only",
        "status": "skipped",
        "reason": "",
        "report": None,
    }

    try:
        from utils.alpha.strategy_evaluator import StrategyEvaluator
    except ImportError as e:
        logger.exception("StrategyEvaluator 导入失败: %s", e)
        return {"status": "import_error", "error": str(e)}

    try:
        # Feature Flag OVERRIDE: 强制评估 (即使 Flag 为 False)
        import os

        os.environ["USE_STRATEGY_EVALUATOR"] = "true"

        evaluator = StrategyEvaluator()
        logger.info("StrategyEvaluator: enabled=%s", evaluator.enabled)

        # 只读: 从 Shadow 数据评估 + 分离性校验
        daily_returns_list = []
        shadow_path = PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"
        if shadow_path.exists():
            with open(shadow_path, encoding="utf-8") as sf:
                for line in sf:
                    line = line.strip()
                    if not line:
                        continue
                    record = json.loads(line)
                    if "daily_return" in record:
                        daily_returns_list.append(float(record["daily_return"]))
        score_report, separation = evaluator.evaluate_with_separation_check(
            daily_returns_list
        )

        report_dict = score_report.to_dict()
        report_dict["separation_check"] = separation
        logger.info(
            "StrategyEvaluator: public=%.4f, private=%.4f, rh_risk=%.4f, rec=%s, separation=%s",
            score_report.public_score,
            score_report.private_score,
            score_report.reward_hacking_risk,
            score_report.recommendation,
            separation.get("valid"),
        )

        # 持久化
        date_str = now_bj().strftime("%Y-%m-%d")
        report_file = SCORE_REPORTS_DIR / f"score_{date_str}.json"
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(report_dict, f, ensure_ascii=False, indent=2, default=str)

        # 写入决策日志 (观察期内 evaluate_only)
        decision_entry = {
            "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "action": "evaluate_only",
            "status": "observation_period",
            "public_score": score_report.public_score,
            "private_score": score_report.private_score,
            "rh_risk": score_report.reward_hacking_risk,
            "recommendation": score_report.recommendation,
            "is_degraded": score_report.is_degraded,
        }
        decisions_file = EVOLUTION_DIR / "decisions.jsonl"
        try:
            with open(decisions_file, "a", encoding="utf-8") as f:
                f.write(
                    json.dumps(decision_entry, ensure_ascii=False, default=str) + "\n"
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
            logger.warning("决策日志写入失败: %s", e)

        result["status"] = "completed"
        result["report"] = report_dict
        logger.info("StrategyEvaluator: 报告已持久化 -> %s", report_file)

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
        logger.exception("StrategyEvaluator 评估异常: %s", e)
        result["status"] = "error"
        result["reason"] = str(e)

    return result


def update_observation_progress() -> dict[str, Any]:
    """T-NEXT-1.3: 更新观察期进度."""
    logger.info("=" * 50)
    logger.info("T-NEXT-1.3: 观察期进度更新")
    logger.info("=" * 50)

    try:
        from scripts.observation_tracker import generate_snapshot

        snapshot = generate_snapshot()
        obs = snapshot["observation"]
        logger.info(
            "观察期进度: %d/%d 天 (%.1f%%), 样本 %d/%d",
            obs["days_completed"],
            obs["required_days"],
            obs["progress_pct"],
            obs["samples_collected"],
            obs["min_samples"],
        )
        logger.info("就绪状态: %s", "YES" if obs["ready_for_phase_b"] else "NO")
        return snapshot
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
        logger.exception("观察期进度更新失败: %s", e)
        return {"error": str(e)}


def generate_score_trend() -> dict[str, Any]:
    """T-NEXT-2.2: 生成评分历史趋势数据.

    加载所有历史评分报告, 产生趋势 JSON 和分离强度分析.
    """
    logger.info("=" * 50)
    logger.info("T-NEXT-2.2: 评分趋势数据生成")
    logger.info("=" * 50)

    result: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "status": "skipped",
        "trend": {},
        "separation_strength": {},
    }

    try:
        from utils.alpha.strategy_evaluator import StrategyEvaluator

        evaluator = StrategyEvaluator()
        reports = evaluator.load_score_history(SCORE_REPORTS_DIR)

        if len(reports) < 2:
            result["reason"] = f"报告不足 (n={len(reports)}, 需要 >=2)"
            logger.info("评分趋势: 报告不足 (n=%d)", len(reports))
            return result

        # 趋势数据
        trend = evaluator.generate_score_trend_data(reports)
        # 分离强度
        separation = evaluator.compute_separation_strength(reports)

        # 持久化
        trend_data = {
            "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "n_reports": len(reports),
            "trend": trend,
            "separation_strength": separation,
        }
        with open(SCORE_TREND_FILE, "w", encoding="utf-8") as f:
            json.dump(trend_data, f, ensure_ascii=False, indent=2, default=str)

        logger.info(
            "评分趋势: %d 份报告, 分离健康度=%s, 相关性=%.4f",
            len(reports),
            separation.get("separation_health", "unknown"),
            separation.get("correlation", 0),
        )
        result["status"] = "completed"
        result["trend"] = trend
        result["separation_strength"] = separation

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
        logger.exception("评分趋势生成失败: %s", e)
        result["status"] = "error"
        result["reason"] = str(e)

    return result


def generate_daily_briefing(drift: dict, eval_result: dict, obs: dict) -> str:
    """生成每日简报 Markdown."""
    now_str = now_bj().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"# 每日演化简报 — {now_bj().strftime('%Y-%m-%d')}",
        "",
        f"**生成时间**: {now_str} | **阶段**: 观察期",
        "",
        "---",
        "",
        "## 1. Drift 监控 (T-NEXT-1.1)",
        "",
    ]

    if drift.get("status") == "completed" and drift.get("alerts"):
        alert = drift["alerts"][0]
        lines.append(f"- **Severity**: `{alert.get('severity', 'N/A')}`")
        lines.append(f"- **KS Score**: {alert.get('drift_score', 0):.4f}")
        lines.append(f"- **PSI**: {alert.get('psi', 0):.4f}")
        lines.append(
            f"- **基线均值**: {alert.get('baseline_mean', 0):.4f} (n={alert.get('n_baseline', 0)})"
        )
        lines.append(
            f"- **当前均值**: {alert.get('current_mean', 0):.4f} (n={alert.get('n_current', 0)})"
        )
    else:
        lines.append(f"- 状态: `{drift.get('status', 'unknown')}`")
        if drift.get("reason"):
            lines.append(f"- 原因: {drift['reason']}")

    lines.extend(
        [
            "",
            "## 2. 策略评估 (T-NEXT-1.2)",
            "",
        ]
    )

    if eval_result.get("report"):
        r = eval_result["report"]
        lines.append(f"- **Public Score**: {r.get('public_score', 0):.4f}")
        lines.append(f"- **Private Score**: {r.get('private_score', 0):.4f}")
        lines.append(
            f"- **Reward Hacking Risk**: {r.get('reward_hacking_risk', 0):.4f}"
        )
        lines.append(f"- **Recommendation**: `{r.get('recommendation', 'N/A')}`")
        lines.append(f"- **Samples**: {r.get('sample_count', 0)}")
        if r.get("is_degraded"):
            lines.append(f"- **WARNING**: 降级报告 — {r.get('degraded_reason', '')}")
    else:
        lines.append(f"- 状态: `{eval_result.get('status', 'unknown')}`")
        if eval_result.get("reason"):
            lines.append(f"- 原因: {eval_result['reason']}")

    if obs and "observation" in obs:
        o = obs["observation"]
        lines.extend(
            [
                "",
                "## 3. 观察期进度 (T-NEXT-1.3)",
                "",
                f"- **进度**: {o.get('days_completed', 0)}/{o.get('required_days', 14)} 天 ({o.get('progress_pct', 0):.1f}%)",  # noqa: E501
                f"- **样本**: {o.get('samples_collected', 0)}/{o.get('min_samples', 20)} 条",
                f"- **预计完成**: {o.get('estimated_completion', 'N/A')}",
                f"- **就绪**: {'YES' if o.get('ready_for_phase_b') else 'NO'}",
            ]
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "*自动生成, 观察期内所有评估均为只读, 不触发进化动作.*",
        ]
    )

    return "\n".join(lines)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="每日演化检查器 (观察期)")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument(
        "--skip-drift", action="store_true", help="跳过 DriftMonitor 检查"
    )
    parser.add_argument(
        "--skip-eval", action="store_true", help="跳过 StrategyEvaluator 评估"
    )
    parser.add_argument("--skip-obs", action="store_true", help="跳过观察期进度更新")
    parser.add_argument(
        "--skip-trend", action="store_true", help="跳过评分趋势数据生成"
    )
    args = parser.parse_args()

    results: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "drift_monitor": {},
        "strategy_evaluator": {},
        "observation_progress": {},
        "score_trend": {},
    }

    # 1. DriftMonitor (T-NEXT-1.1)
    if not args.skip_drift:
        results["drift_monitor"] = run_drift_monitor_check()

    # 2. StrategyEvaluator (T-NEXT-1.2)
    if not args.skip_eval:
        results["strategy_evaluator"] = run_strategy_evaluation()

    # 3. 观察期进度 (T-NEXT-1.3)
    if not args.skip_obs:
        results["observation_progress"] = update_observation_progress()

    # 4. 评分趋势 (T-NEXT-2.2)
    if not args.skip_trend:
        results["score_trend"] = generate_score_trend()

    # 生成每日简报
    try:
        briefing = generate_daily_briefing(
            results["drift_monitor"],
            results["strategy_evaluator"],
            results["observation_progress"],
        )
        with open(DAILY_BRIEFING_FILE, "w", encoding="utf-8") as f:
            f.write(briefing)
        # 更新 latest 软链接
        latest_link = EVOLUTION_DIR / "daily_briefing_latest.md"
        if latest_link.exists() or latest_link.is_symlink():
            latest_link.unlink()
        try:
            latest_link.symlink_to(DAILY_BRIEFING_FILE.name)
        except OSError:
            # Windows 可能不支持 symlink, 直接复制内容
            with open(latest_link, "w", encoding="utf-8") as f:
                f.write(briefing)
        logger.info("每日简报已保存: %s", DAILY_BRIEFING_FILE)
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
        logger.exception("简报生成失败: %s", e)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    else:
        print(briefing)  # noqa: T201

    return 0


if __name__ == "__main__":
    sys.exit(main())
