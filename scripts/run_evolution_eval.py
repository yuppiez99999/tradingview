#!/usr/bin/env python3
"""自我进化编排器评估入口 — v84_EvolutionEval 任务专用.

=================================================================
功能:
    观察期内每日盘后运行, 调用 EvolutionOrchestrator.run_observation_cycle()
    只读收集 Shadow daily_returns, 调用 StrategyEvaluator 评估, 写入决策日志.

执行时序 (盘后):
    15:30  v84_PostMarket (EOD 工作流, 写入 daily_returns.jsonl)
    16:00  v84_DailyPnlReport (生成 PnL 报告)
    16:05  v84_EvolutionEval (本脚本, 只读评估)  ← 在 PnL 报告后运行

安全设计:
    1. HC-1: Feature Flag USE_EVOLUTION_ORCHESTRATOR 默认 False
       - 关闭时直接退出, 不执行任何评估
    2. HC-4: 只读模式, 不修改 V9 基线 / positions.json / 任何生产路径
    3. 失败容错: 任何异常都不应影响其他定时任务
       - 异常时记录到 stderr, 退出码 1
       - 正常完成退出码 0
    4. 日志: 同时输出到 stdout 和 reports/evolution/runs/{date}.log

使用方式:
    python scripts/run_evolution_eval.py                # 标准运行
    python scripts/run_evolution_eval.py --dry-run      # 试运行 (不写决策日志)
    python scripts/run_evolution_eval.py --verbose      # 详细日志

退出码:
    0 = 成功 (或 Flag 关闭时的正常降级)
    1 = 异常 (应记录到任务计划程序的 Last Result)
=================================================================
"""
from __future__ import annotations

import argparse
import logging
import sys
import traceback
from datetime import datetime
from pathlib import Path

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 单事实源: shadow_admission.yaml (settings.observation_days, PM 决策=21).
# 不得硬编码 14, 否则 yaml 升级后观察期口径永不生效 (见 2026-08-09 配置脱节修复).
_SHADOW_ADMISSION_YAML = (
    _PROJECT_ROOT / "v8.3_institutional" / "config" / "shadow_admission.yaml"
)


def _load_observation_days() -> int:
    try:
        import yaml

        with open(_SHADOW_ADMISSION_YAML, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        settings = cfg.get("settings", {})
        return int(settings.get("observation_days", settings.get("min_observation_days", 14)))
    except (OSError, Exception):
        return 14


OBSERVATION_DAYS = _load_observation_days()

logger = logging.getLogger("run_evolution_eval")


def setup_logging(verbose: bool = False) -> logging.Logger:
    """配置日志, 同时输出到 stdout 和文件.

    Args:
        verbose: 是否启用 DEBUG 级别日志

    Returns:
        配置好的 Logger
    """
    log_level = logging.DEBUG if verbose else logging.INFO
    log_format = "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    # 创建 root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # 清除已有 handlers (避免重复)
    root_logger.handlers.clear()

    # stdout handler
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(log_level)
    stdout_handler.setFormatter(logging.Formatter(log_format, date_format))
    root_logger.addHandler(stdout_handler)

    # 文件 handler (写入 reports/evolution/runs/)
    runs_log_dir = _PROJECT_ROOT / "reports" / "evolution" / "runs"
    runs_log_dir.mkdir(parents=True, exist_ok=True)

    today_str = datetime.now().strftime("%Y%m%d")
    log_file = runs_log_dir / f"evolution_eval_{today_str}.log"

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(log_level)
    file_handler.setFormatter(logging.Formatter(log_format, date_format))
    root_logger.addHandler(file_handler)

    return logging.getLogger("evolution_eval")


def collect_progress_snapshot() -> dict:
    """收集当前自我进化框架的完整进度快照 (不依赖 Flag, 始终可运行).

    读取:
        - Shadow daily_returns.jsonl 累计天数
        - evolution/decisions.jsonl 累计评估次数
        - v84 定时任务最近运行状态
        - Feature Flag 当前值

    Returns:
        进度快照字典
    """
    import json as _json

    snapshot = {
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "shadow_data": {},
        "evolution_log": {},
        "feature_flags": {},
        "next_action": "",
        "blockers": [],
    }

    # 1. Shadow 数据进度
    daily_returns_path = _PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"
    if daily_returns_path.exists():
        records = []
        with daily_returns_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(_json.loads(line))
                    except _json.JSONDecodeError:
                        continue
        snapshot["shadow_data"] = {
            "total_days": len(records),
            "first_date": records[0].get("date", "") if records else "",
            "last_date": records[-1].get("date", "") if records else "",
            "min_required": 20,  # MIN_SAMPLES_FOR_EVALUATION
            "observation_total": OBSERVATION_DAYS,
            "observation_progress": f"{len(records)}/{OBSERVATION_DAYS}",
            "observation_complete": len(records) >= OBSERVATION_DAYS,
            "data_sufficient": len(records) >= 20,
        }
    else:
        snapshot["shadow_data"] = {"error": "daily_returns.jsonl 不存在"}
        snapshot["blockers"].append("Shadow 数据未开始收集 (daily_returns.jsonl 缺失)")

    # 2. 进化日志进度
    decisions_log = _PROJECT_ROOT / "reports" / "evolution" / "decisions.jsonl"
    if decisions_log.exists():
        with decisions_log.open("r", encoding="utf-8") as f:
            decision_count = sum(1 for line in f if line.strip())
        snapshot["evolution_log"] = {
            "total_decisions": decision_count,
            "log_path": str(decisions_log),
        }
    else:
        snapshot["evolution_log"] = {"total_decisions": 0}

    # 3. Feature Flag 状态
    try:
        from utils.infra.feature_flags import is_enabled
        snapshot["feature_flags"] = {
            "USE_STRATEGY_EVALUATOR": is_enabled("USE_STRATEGY_EVALUATOR"),
            "USE_EVOLUTION_ORCHESTRATOR": is_enabled("USE_EVOLUTION_ORCHESTRATOR"),
            "USE_MLOPS_PIPELINE": is_enabled("USE_MLOPS_PIPELINE"),
            "USE_DRIFT_DETECTOR": is_enabled("USE_DRIFT_DETECTOR"),
            "USE_AUTO_RETRAIN": is_enabled("USE_AUTO_RETRAIN"),
        }
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        snapshot["feature_flags"] = {"error": str(e)}

    # 4. 自动推断下一步动作
    shadow_days = snapshot["shadow_data"].get("total_days", 0)
    flags = snapshot.get("feature_flags", {})

    if shadow_days == 0:
        snapshot["next_action"] = "等待 v84_PostMarket 写入首条 Shadow 数据"
    elif shadow_days < OBSERVATION_DAYS:
        snapshot["next_action"] = (
            f"观察期进行中 ({shadow_days}/{OBSERVATION_DAYS} 天), 等待数据积累. "
            f"当前 {shadow_days} 天, 还需 {OBSERVATION_DAYS - shadow_days} 天"
        )
        snapshot["blockers"].append(f"观察期未满: {shadow_days}/{OBSERVATION_DAYS} 天")
    elif shadow_days < 20:
        snapshot["next_action"] = (
            f"观察期已满 14 天, 但样本不足 ({shadow_days}/20). "
            f"还需 {20 - shadow_days} 天数据才能启用评估"
        )
        snapshot["blockers"].append(f"样本不足: {shadow_days}/20 天")
    elif not flags.get("USE_STRATEGY_EVALUATOR", False):
        snapshot["next_action"] = (
            "数据已充足, 可双签启用 USE_STRATEGY_EVALUATOR=True "
            "(变更发起人 + 风控负责人)"
        )
    elif not flags.get("USE_EVOLUTION_ORCHESTRATOR", False):
        snapshot["next_action"] = (
            "评估器已启用, 可双签启用 USE_EVOLUTION_ORCHESTRATOR=True "
            "启动完整外层循环"
        )
    else:
        snapshot["next_action"] = "进化框架已启用, 监控 decisions.jsonl 的 recommendation 字段"

    return snapshot


def ensure_today_shadow_data(logger: logging.Logger) -> None:
    """兜底: 确保当日 Shadow 数据已写入 daily_returns.jsonl (W1.3a Day 3).

    v84_EvolutionEval 在 16:05 运行, 晚于 v84_PostMarket (15:30). 若 PostMarket
    阶段四点五失败 (数据源不可用 / 权重缺失 / 脚本异常), 当日 daily_returns 可能缺失,
    此处兜底重试一次, 避免评估在空数据上降级.

    HC 合规:
        - HC-1: 不切 Feature Flag (始终可运行, 不依赖 USE_EVOLUTION_ORCHESTRATOR)
        - HC-4: 只读评估前的数据补充, 不修改 V9 基线 / positions.json
        - fail-safe: 兜底失败不影响 EvolutionEval 主流程 (catch 所有异常)
    """
    import json as _json

    today = datetime.now().strftime("%Y-%m-%d")
    daily_returns_path = _PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"

    # 1. 检查当日数据是否已存在 (避免重复注入)
    if daily_returns_path.exists():
        try:
            with daily_returns_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = _json.loads(line)
                        if rec.get("date") == today:
                            logger.debug(
                                "[兜底] 当日 Shadow 数据已存在 (date=%s, source=%s), 跳过注入",
                                today,
                                rec.get("source", "unknown"),
                            )
                            return
                    except _json.JSONDecodeError:
                        continue
        except OSError as e:
            logging.getLogger("run_evolution_eval").warning("[兜底] 读取 daily_returns.jsonl 失败: %s", e)

    # 2. 当日数据缺失, 兜底调用 ShadowRealDataFeeder 注入
    logging.getLogger("run_evolution_eval").info("[兜底] 当日 Shadow 数据缺失 (date=%s), 尝试注入", today)
    try:
        from utils.alpha.shadow_real_data_feeder import ShadowRealDataFeeder
        from utils.data_provider import MarketDataProvider

        provider = MarketDataProvider(backtest_mode=False)
        feeder = ShadowRealDataFeeder(data_provider=provider)
        result = feeder.feed_single_date(today)

        if result.is_success:
            logger.info(
                "[兜底] 注入成功: daily_return=%.4f%%, coverage=%.2f%%, written=%s",
                result.daily_return * 100,
                result.coverage * 100,
                result.written,
            )
        elif result.skipped:
            logger.info(
                "[兜底] 注入跳过: %s (周末 / 无权重 / 数据源全失败)",
                result.error,
            )
        else:
            logger.warning(
                "[兜底] 注入失败: success=%d/%d, error=%s",
                result.success_count,
                result.total_count,
                result.error,
            )
    except (ImportError, RuntimeError, ValueError, OSError, ConnectionError, TimeoutError) as e:
        # fail-safe: 兜底失败不影响 EvolutionEval 主流程, 评估将在空数据上降级
        logger.warning("[兜底] ShadowRealDataFeeder 兜底调用失败 (fail-safe): %s", e)


def ensure_today_drift_integration(logger: logging.Logger) -> None:
    """兜底: 确保当日漂移检测已执行 (W1.3b Day 4).

    v84_EvolutionEval 在 16:05 运行, 晚于 v84_PostMarket (15:30). 若 PostMarket
    阶段四点七 (DriftShadowIntegrator) 失败, 当日漂移报告可能缺失,
    此处兜底重试一次, 确保评估前漂移数据已更新.

    HC 合规:
        - HC-1: 不切 Feature Flag (sim_mode=True)
        - HC-4: 只读评估, 不修改 V9 基线
        - fail-safe: 兜底失败不影响 EvolutionEval 主流程
    """
    import subprocess

    today = datetime.now().strftime("%Y-%m-%d")
    logger.info("[兜底] 尝试 DriftShadowIntegrator 当日集成 (date=%s)", today)
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(_PROJECT_ROOT / "scripts" / "drift_shadow_integrator.py"),
                "--date",
                today,
            ],
            cwd=str(_PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=300,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            logger.info("[兜底] DriftShadowIntegrator 集成成功")
        else:
            logger.warning(
                "[兜底] DriftShadowIntegrator 集成失败 (exit=%d): %s",
                result.returncode,
                result.stderr[:200] if result.stderr else "",
            )
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
        logger.warning("[兜底] DriftShadowIntegrator 兜底调用失败 (fail-safe): %s", e)


def print_progress_summary(snapshot: dict, logger: logging.Logger) -> None:
    """打印醒目的进度摘要到日志.

    Args:
        snapshot: collect_progress_snapshot() 返回的快照
        logger: 日志记录器
    """
    logger.info("")
    logger.info("=" * 60)
    logger.info("[自我进化框架进度快照] %s", snapshot["collected_at"])
    logger.info("=" * 60)

    # Shadow 数据进度
    sd = snapshot.get("shadow_data", {})
    if "error" not in sd:
        days = sd.get("total_days", 0)
        obs_progress = sd.get("observation_progress", "?")
        logger.info(
            "[Shadow 数据] %s/%s 天 (观察期) | %s/20 条 (最小评估样本) | %s ~ %s",
            days, OBSERVATION_DAYS, days, sd.get("first_date", ""), sd.get("last_date", ""),
        )
        # 进度条
        bar_len = 20
        filled = min(int(days / 14 * bar_len), bar_len)
        bar = "#" * filled + "-" * (bar_len - filled)
        logger.info("  观察期进度: [%s] %s", bar, obs_progress)
    else:
        logger.warning("[Shadow 数据] %s", sd.get("error"))

    # 进化日志
    el = snapshot.get("evolution_log", {})
    logger.info("[决策日志] 累计 %s 条评估记录", el.get("total_decisions", 0))

    # Feature Flags
    flags = snapshot.get("feature_flags", {})
    if "error" not in flags:
        logger.info(
            "[Feature Flags] EVALUATOR=%s, ORCHESTRATOR=%s, MLOPS=%s, DRIFT=%s, RETRAIN=%s",
            flags.get("USE_STRATEGY_EVALUATOR", False),
            flags.get("USE_EVOLUTION_ORCHESTRATOR", False),
            flags.get("USE_MLOPS_PIPELINE", False),
            flags.get("USE_DRIFT_DETECTOR", False),
            flags.get("USE_AUTO_RETRAIN", False),
        )

    # 下一步动作 (最关键)
    logger.info("")
    logger.info("[下一步] %s", snapshot.get("next_action", "未知"))

    # 阻塞项
    blockers = snapshot.get("blockers", [])
    if blockers:
        logger.warning("[阻塞项]")
        for b in blockers:
            logger.warning("  - %s", b)
    else:
        logger.info("[阻塞项] 无")

    logger.info("=" * 60)
    logger.info("")


def parse_args() -> argparse.Namespace:
    """解析命令行参数.

    Returns:
        参数对象
    """
    parser = argparse.ArgumentParser(
        description="自我进化编排器评估入口 (v84_EvolutionEval 任务专用)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="试运行模式: 执行评估但不写决策日志",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="详细日志 (DEBUG 级别)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="仅输出进度快照并退出 (不运行评估, 不依赖 Flag)",
    )
    return parser.parse_args()


def main() -> int:
    """主入口.

    Returns:
        退出码 (0=成功, 1=异常)
    """
    args = parse_args()
    setup_logging(verbose=args.verbose)

    # --status 快捷模式: 只输出进度, 不跑评估
    if args.status:
        snapshot = collect_progress_snapshot()
        print_progress_summary(snapshot, logger)
        return 0

    logger.info("=" * 60)
    logger.info("v84_EvolutionEval 启动")
    logger.info("=" * 60)
    logger.info("模式: %s", "dry-run" if args.dry_run else "standard")
    logger.info("项目根: %s", _PROJECT_ROOT)

    try:
        # W1.3a Day 3: 兜底确保当日 Shadow 数据存在 (PostMarket 阶段四点五失败时重试)
        # 在 collect_progress_snapshot 之前执行, 确保快照能反映当日最新数据
        ensure_today_shadow_data(logger)

        # W1.3b Day 4: 兜底确保当日漂移检测已执行 (PostMarket 阶段四点七失败时重试)
        ensure_today_drift_integration(logger)

        # 始终输出进度快照 (无论 Flag 是否启用, 让每天的日志都有可读状态)
        snapshot = collect_progress_snapshot()
        print_progress_summary(snapshot, logger)

        # 持久化进度快照到 status.json (供其他工具/脚本查询)
        status_file = _PROJECT_ROOT / "reports" / "evolution" / "status.json"
        status_file.parent.mkdir(parents=True, exist_ok=True)
        import json as _json
        with status_file.open("w", encoding="utf-8") as f:
            _json.dump(snapshot, f, ensure_ascii=False, indent=2)

        # 导入编排器
        from utils.alpha.evolution_orchestrator import EvolutionOrchestrator

        # 实例化编排器 (使用默认路径)
        orchestrator = EvolutionOrchestrator()

        # 检查 Feature Flag (HC-1)
        if not orchestrator.enabled:
            logger.info(
                "[HC-1] Feature Flag USE_EVOLUTION_ORCHESTRATOR=False, "
                "编排器禁用, 仅记录进度快照, 正常退出 (exit_code=0)"
            )
            return 0

        logger.info("编排器已启用, 开始观察期循环")

        # 执行观察期循环: collect → evaluate → log
        result = orchestrator.run_observation_cycle()

        # 输出结果摘要
        logger.info("-" * 60)
        logger.info("循环结果摘要:")
        logger.info("  status:              %s", result.get("status"))
        logger.info("  observation_day:     %s", result.get("observation_day"))
        logger.info("  total_evaluations:   %s", result.get("total_evaluations"))
        logger.info("  has_report:          %s", result.get("has_report"))
        logger.info("  public_score:        %.4f", result.get("public_score", 0.0))
        logger.info("  private_score:       %.4f", result.get("private_score", 0.0))
        logger.info("  recommendation:      %s", result.get("recommendation", ""))

        metrics_snapshot = result.get("metrics_snapshot", {})
        logger.info("  metrics.sample_count: %s", metrics_snapshot.get("sample_count"))
        logger.info("  metrics.source:       %s", metrics_snapshot.get("source"))
        logger.info("  metrics.is_degraded:  %s", metrics_snapshot.get("is_degraded"))
        if metrics_snapshot.get("is_degraded"):
            logger.warning(
                "  metrics.degraded_reason: %s",
                metrics_snapshot.get("degraded_reason"),
            )

        # dry-run 模式提示
        if args.dry_run:
            logger.info("[dry-run] 试运行模式, 决策日志可能已写入 (HC-4 强制 evaluate_only)")

        # 检查决策日志是否已生成
        decisions_log = _PROJECT_ROOT / "reports" / "evolution" / "decisions.jsonl"
        if decisions_log.exists():
            # 统计总记录数
            with decisions_log.open("r", encoding="utf-8") as f:
                line_count = sum(1 for line in f if line.strip())
            logger.info("决策日志: %s (累计 %d 条)", decisions_log, line_count)
        else:
            logger.warning("决策日志未生成: %s", decisions_log)

        logger.info("=" * 60)
        logger.info("[OK] v84_EvolutionEval 完成")
        logger.info("=" * 60)
        return 0

    except ImportError as e:
        logger.error("[FAIL] 导入 EvolutionOrchestrator 失败: %s", e)
        logger.error(traceback.format_exc())
        return 1

    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:

        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        logger.error("[FAIL] v84_EvolutionEval 异常: %s", e)
        logger.error(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
