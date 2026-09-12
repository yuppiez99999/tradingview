"""每日 EOD 触发脚本 (进化 + 再平衡挂进 EOD 工作流)
=====================================================

G3 机构管道集成 (Wave 7-ERL Sprint 2 — ER-2.1/2.2/2.3)
将自我进化 + ETF期权对冲再平衡挂进 institutional pipeline EOD 工作流,
受 Feature Flag 控制 (USE_EVOLUTION_ORCHESTRATOR / USE_EOD_REBALANCE)。

用法:
    # 默认 (flag 关闭, 仅跑主链路, evolution/rebalance 跳过)
    python run_eod_evolution_rebalance.py --mode dry_run

    # 临时启用进化 (双签, 运行后自动恢复)
    python run_eod_evolution_rebalance.py --mode live \\
        --enable-evolution --signer alice --co-signer bob

    # 临时启用进化 + 再平衡
    python run_eod_evolution_rebalance.py --mode live \\
        --enable-evolution --enable-rebalance \\
        --signer alice --co-signer bob

    # 指定标的 + 资本
    python run_eod_evolution_rebalance.py --mode dry_run \\
        --symbols 600519 000858 --capital 3000000

设计原则:
    1. Flag 关闭时主链路字节级不变 (新增 phase 返回 disabled 字典)
    2. 临时启用走双签审计 (feature_flags.enable/disable), 运行后恢复
    3. 任何 phase 失败不阻塞主链路 (fail-safe 降级)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from utils.datetime_utils import now_bj

# 确保项目根在 sys.path (兼容从任意目录启动)
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from institutional_pipeline_runner import (  # noqa: E402
    InstitutionalPipelineRunner,
    PipelineContext,
)

logger = logging.getLogger("eod_evolution_rebalance")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="每日 EOD 触发脚本 (进化 + 再平衡挂进 EOD, 受 Flag 控制)"
    )
    parser.add_argument(
        "--mode",
        default="dry_run",
        choices=["smoke", "backtest", "live", "dry_run"],
        help="运行模式 (默认 dry_run)",
    )
    parser.add_argument(
        "--symbols",
        nargs="*",
        default=[
            "600519",
            "000858",
            "601318",
            "000001",
            "600036",
            "601398",
            "600276",
            "000063",
        ],
        help="标的列表",
    )
    parser.add_argument("--capital", type=float, default=3_000_000.0, help="总资本")
    parser.add_argument(
        "--enable-evolution",
        action="store_true",
        help="临时启用 USE_EVOLUTION_ORCHESTRATOR (需双签)",
    )
    parser.add_argument(
        "--enable-rebalance",
        action="store_true",
        help="临时启用 USE_EOD_REBALANCE (需双签)",
    )
    parser.add_argument("--signer", default="eod_trigger", help="变更发起人 (双签)")
    parser.add_argument("--co-signer", default="risk_officer", help="风控负责人 (双签)")
    parser.add_argument(
        "--keep-enabled",
        action="store_true",
        help="运行后不恢复 flag (默认运行后自动 disable)",
    )
    return parser.parse_args()


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _temp_enable_flags(args: argparse.Namespace) -> list[str]:
    """临时启用 flag (双签审计), 返回已启用列表."""
    enabled: list[str] = []
    if not (args.enable_evolution or args.enable_rebalance):
        return enabled
    try:
        from utils.infra.feature_flags import enable

        if args.enable_evolution:
            enable(
                "USE_EVOLUTION_ORCHESTRATOR",
                signer=args.signer,
                co_signer=args.co_signer,
                reason=f"EOD trigger 临时启用 {now_bj().isoformat()}",
            )
            enabled.append("USE_EVOLUTION_ORCHESTRATOR")
            logger.info(
                "已启用 USE_EVOLUTION_ORCHESTRATOR (双签: %s/%s)",
                args.signer,
                args.co_signer,
            )
        if args.enable_rebalance:
            enable(
                "USE_EOD_REBALANCE",
                signer=args.signer,
                co_signer=args.co_signer,
                reason=f"EOD trigger 临时启用 {now_bj().isoformat()}",
            )
            enabled.append("USE_EOD_REBALANCE")
            logger.info(
                "已启用 USE_EOD_REBALANCE (双签: %s/%s)", args.signer, args.co_signer
            )
    except Exception as e:
        logger.error("启用 flag 失败: %s", e, exc_info=True)
    return enabled


def _restore_flags(flags: list[str], signer: str) -> None:
    """运行后恢复 flag (单签)."""
    if not flags:
        return
    try:
        from utils.infra.feature_flags import disable

        for name in flags:
            disable(
                name,
                signer=signer,
                reason=f"EOD trigger 运行后恢复 {now_bj().isoformat()}",
            )
            logger.info("已恢复 %s = false", name)
    except Exception as e:
        logger.warning("恢复 flag 失败 (需手动 disable): %s", e)


def _print_summary(result: dict) -> None:
    """输出运行摘要 (含 evolution + eod_rebalance 阶段状态)."""
    steps = result.get("steps", {})
    logger.info("=" * 60)
    logger.info(
        "EOD 运行摘要 | date=%s | mode=%s | status=%s",
        result.get("report_date"),
        result.get("mode"),
        result.get("status"),
    )
    logger.info("=" * 60)

    # 主链路阶段
    main_steps = [
        "data_gate",
        "alpha_evaluation",
        "signal_fusion",
        "portfolio_decision",
        "market_regime",
        "risk_budget",
        "execution_plans",
    ]
    for key in main_steps:
        if key in steps:
            data = steps[key]
            if isinstance(data, dict):
                status = data.get("status", "OK")
            elif isinstance(data, list):
                status = f"{len(data)} 项"
            else:
                status = "OK"
            logger.info("  %-22s: %s", key, status)

    # G3 新增阶段
    logger.info("-" * 60)
    logger.info("  G3 新增阶段:")
    evolution = steps.get("evolution", {})
    if evolution:
        logger.info(
            "  %-22s: status=%s %s",
            "evolution (Step 4.6)",
            evolution.get("status", "?"),
            evolution.get("reason", ""),
        )
    else:
        logger.info("  %-22s: 未执行 (flag 关闭)", "evolution (Step 4.6)")

    rebalance = steps.get("eod_rebalance", {})
    if rebalance:
        logger.info(
            "  %-22s: status=%s %s",
            "eod_rebalance (Step 6.6)",
            rebalance.get("status", "?"),
            rebalance.get("reason", ""),
        )
    else:
        logger.info("  %-22s: 未执行 (flag 关闭)", "eod_rebalance (Step 6.6)")

    # AI 复盘 + 报告
    if "ai_eod_review" in steps:
        logger.info("  %-22s: 已生成", "ai_eod_review (Step 6.5)")
    if result.get("report_path"):
        logger.info("  %-22s: %s", "report (Step 7)", result["report_path"])
    logger.info("=" * 60)


def main() -> None:
    _setup_logging()
    args = parse_args()

    logger.info("启动 EOD 工作流 (进化+再平衡, 受 Flag 控制)")
    logger.info(
        "  mode=%s | symbols=%s | capital=%.0f", args.mode, args.symbols, args.capital
    )
    logger.info(
        "  enable_evolution=%s | enable_rebalance=%s",
        args.enable_evolution,
        args.enable_rebalance,
    )

    # 临时启用 flag (双签)
    enabled_flags = _temp_enable_flags(args)

    try:
        ctx = PipelineContext(
            mode=args.mode,
            symbols=args.symbols,
            total_capital=args.capital,
        )
        runner = InstitutionalPipelineRunner(ctx)
        result = runner.run()
        _print_summary(result)

        # 完整结果落盘 (供审计)
        try:
            output_dir = Path("reports/eod_evolution_rebalance")
            output_dir.mkdir(parents=True, exist_ok=True)
            output_file = output_dir / f"eod_{ctx.report_date}_{ctx.mode}.json"
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2, default=str)
            logger.info("完整结果已保存: %s", output_file)
        except (OSError, ValueError, TypeError) as e:
            logger.warning("结果保存失败: %s", e)

    finally:
        # 运行后恢复 flag (除非 --keep-enabled)
        if enabled_flags and not args.keep_enabled:
            _restore_flags(enabled_flags, signer=args.signer)
        elif enabled_flags and args.keep_enabled:
            logger.warning("flag 保持启用 (需手动 disable): %s", enabled_flags)

    logger.info("EOD 工作流完成")


if __name__ == "__main__":
    main()
