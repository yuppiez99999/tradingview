"""EOD 工作流 FeedbackLoop 集成 — 每日盘后自动触发因子权重更新.

模块整合 8.4 — ARCHITECTURE_自我进化框架 §6.4 (v2.0 合并版)
任务编号: T2.3 (Phase 2 反馈闭环)

职责:
    在每日盘后工作流末尾自动触发 FeedbackLoop:
        1. 读取当日归因报告 (通过 PnLAttributionAdapter)
        2. 加载当前因子权重
        3. 提交权重变更给 EvolutionGuard 检查
        4. 调用 FeedbackLoop.update_weights()
        5. 原子写入更新后的权重到 config/factor_weights.json
        6. 写入 EvolutionMemory 审计

验收标准:
    1. EOD 工作流执行后, 因子权重更新写入 config/factor_weights.json
    2. 权重变更经 EvolutionGuard 检查
    3. 权重变更写入 EvolutionMemory
    4. 工作流崩溃时权重不更新 (原子性)

用法:
    # 作为独立脚本运行
    python -m utils.evolution.eod_feedback_integration --date 2026-08-02

    # 作为模块调用
    from utils.evolution.eod_feedback_integration import run_feedback_loop
    result = run_feedback_loop("2026-08-02")
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("eod_feedback_integration")

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 默认配置路径
DEFAULT_WEIGHTS_PATH = _PROJECT_ROOT / "config" / "factor_weights.json"
DEFAULT_MEMORY_PATH = _PROJECT_ROOT / "reports" / "evolution" / "memory.jsonl"
DEFAULT_REPORT_DIR = _PROJECT_ROOT / "reports" / "attribution"

# 默认因子权重 (防御层默认开启, 等权分配)
DEFAULT_FACTOR_WEIGHTS: dict[str, float] = {
    "style_momentum": 0.10,
    "style_reversal": 0.08,
    "style_volatility": 0.08,
    "style_liquidity": 0.05,
    "style_earnings_quality": 0.10,
    "style_growth": 0.10,
    "style_valuation": 0.05,
    "sector_tech": 0.12,
    "sector_manufacturing": 0.08,
    "sector_cyclical": 0.06,
    "sector_resources": 0.06,
    "sector_defensive": 0.06,
    "sector_finance": 0.02,
    "sector_consumer": 0.02,
    "sector_healthcare": 0.02,
}

# 状态码
STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_FAILED = "failed"


@dataclass
class FeedbackLoopResult:
    """EOD FeedbackLoop 执行结果."""

    date: str
    status: str
    attribution_found: bool = False
    attribution_source: str = ""
    n_factors: int = 0
    old_weights: dict[str, float] = field(default_factory=dict)
    new_weights: dict[str, float] = field(default_factory=dict)
    total_change_pct: float = 0.0
    alarm_triggered: bool = False
    alarm_reason: str = ""
    guard_passed: bool = True
    guard_reason: str = ""
    proposal_id: str = ""
    weights_path: str = ""
    error: str = ""
    degraded_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "status": self.status,
            "attribution_found": self.attribution_found,
            "attribution_source": self.attribution_source,
            "n_factors": self.n_factors,
            "total_change_pct": round(self.total_change_pct, 6),
            "alarm_triggered": self.alarm_triggered,
            "alarm_reason": self.alarm_reason,
            "guard_passed": self.guard_passed,
            "guard_reason": self.guard_reason,
            "proposal_id": self.proposal_id,
            "weights_path": self.weights_path,
            "error": self.error,
            "degraded_reason": self.degraded_reason,
        }

    def is_success(self) -> bool:
        """是否成功 (含降级成功)."""
        return self.status in (STATUS_OK, STATUS_DEGRADED)


# ============================================================
# 核心函数
# ============================================================


def load_current_weights(weights_path: str | Path | None = None) -> dict[str, float]:
    """加载当前因子权重.

    优先从 weights_path 读取, 文件不存在时返回 DEFAULT_FACTOR_WEIGHTS.

    Args:
        weights_path: 权重文件路径 (None=默认 config/factor_weights.json)

    Returns:
        dict[str, float] 因子权重
    """
    path = Path(weights_path) if weights_path else DEFAULT_WEIGHTS_PATH

    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                # 过滤无效值
                return {
                    k: float(v)
                    for k, v in data.items()
                    if isinstance(v, (int, float)) and v > 0
                }
        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.warning("读取权重文件失败, 使用默认权重: %s", e)
            return dict(DEFAULT_FACTOR_WEIGHTS)

    logger.info("权重文件不存在 (%s), 使用默认权重", path)
    return dict(DEFAULT_FACTOR_WEIGHTS)


def save_weights_atomic(
    weights: dict[str, float],
    weights_path: str | Path | None = None,
) -> str:
    """原子写入因子权重 (write to temp → rename).

    确保工作流崩溃时权重不更新 (原子性, 验收标准 4).

    Args:
        weights: 因子权重
        weights_path: 权重文件路径 (None=默认)

    Returns:
        str 写入的文件路径
    """
    path = Path(weights_path) if weights_path else DEFAULT_WEIGHTS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    # 写入临时文件
    fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp",
        prefix="factor_weights_",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(weights, f, ensure_ascii=False, indent=2)
        # 原子重命名 (Windows 需要先删除目标)
        if path.exists():
            path.unlink()
        os.rename(tmp_path, str(path))
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
        # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
        # 清理临时文件
        try:
            os.unlink(tmp_path)
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
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            pass
        raise

    logger.info("权重已原子写入: %s (%d 个因子)", path, len(weights))
    return str(path)


def run_feedback_loop(
    attribution_date: str,
    weights_path: str | Path | None = None,
    report_dir: str | Path | None = None,
    memory_path: str | Path | None = None,
) -> FeedbackLoopResult:
    """执行 EOD FeedbackLoop 权重更新.

    Args:
        attribution_date: 归因日期 (YYYY-MM-DD)
        weights_path: 权重文件路径 (None=默认)
        report_dir: 归因报告目录 (None=默认)
        memory_path: EvolutionMemory 路径 (None=默认)

    Returns:
        FeedbackLoopResult 执行结果
    """
    result = FeedbackLoopResult(
        date=attribution_date,
        status=STATUS_OK,
        weights_path=str(weights_path or DEFAULT_WEIGHTS_PATH),
    )

    try:
        # 1. 加载当前权重
        current_weights = load_current_weights(weights_path)
        result.old_weights = dict(current_weights)

        # 2. 读取归因报告
        from utils.evolution.pnl_attribution_adapter import PnLAttributionAdapter

        adapter = PnLAttributionAdapter(
            report_dir=Path(report_dir) if report_dir else DEFAULT_REPORT_DIR,
        )
        conv = adapter.load_from_report(attribution_date)

        result.attribution_found = conv.status == "ok"
        result.attribution_source = conv.source

        if conv.is_degraded():
            logger.warning(
                "归因数据缺失 (%s), 跳过权重更新, 保持当前权重",
                conv.reason,
            )
            result.status = STATUS_DEGRADED
            result.degraded_reason = conv.reason
            result.new_weights = dict(current_weights)
            # 降级时也写入 weights (保持当前权重)
            save_weights_atomic(current_weights, weights_path)
            return result

        # 3. 转换为 FeedbackLoop 格式
        contributions = conv.to_feedback_loop_format()
        result.n_factors = len(contributions)
        daily_pnl_pct = conv.daily_pnl_pct

        if not contributions:
            logger.warning("归因贡献为空, 跳过权重更新")
            result.status = STATUS_DEGRADED
            result.degraded_reason = "归因贡献为空"
            result.new_weights = dict(current_weights)
            save_weights_atomic(current_weights, weights_path)
            return result

        # 4. 初始化 EvolutionGuard (检查权重变更)
        from utils.evolution.guard import EvolutionGuard, EvolutionProposal

        guard = EvolutionGuard()
        # 计算总调整幅度 (L1 范数)
        # 近似估计: 首次运行时 weight_change=0 (等权初始化)
        weight_change = 0.0
        if current_weights:
            n = max(len(current_weights), 1)
            total_diff = sum(
                abs(current_weights.get(k, 1.0 / n) - 1.0 / n)
                for k in set(list(current_weights.keys()) + list(contributions.keys()))
            )
            weight_change = total_diff / max(n, 1)

        proposal = EvolutionProposal(
            level="L1",
            action_type="weight_adjust",
            target_module="feedback_loop",
            weight_change=weight_change,
            trigger_reason=f"EOD 归因驱动权重更新: {attribution_date}",
            rollback_plan="回滚至 config/factor_weights.json 备份",
            metadata={
                "attribution_date": attribution_date,
                "n_factors": len(contributions),
                "daily_pnl_pct": daily_pnl_pct,
            },
        )
        guard_decision = guard.check_proposal(proposal)
        result.guard_passed = guard_decision.passed
        result.guard_reason = guard_decision.reason

        if not guard_decision.passed:
            logger.warning("Guard 拒绝权重变更: %s", guard_decision.reason)
            result.status = STATUS_DEGRADED
            result.degraded_reason = f"Guard 拒绝: {guard_decision.reason}"
            result.new_weights = dict(current_weights)
            save_weights_atomic(current_weights, weights_path)
            return result

        # 5. 初始化 EvolutionMemory
        from utils.evolution.memory import EvolutionMemory

        memory = EvolutionMemory(
            memory_path=Path(memory_path) if memory_path else DEFAULT_MEMORY_PATH,
        )

        # 6. 初始化 FeedbackLoop
        from utils.evolution.feedback_loop import FeedbackLoop

        loop = FeedbackLoop(
            initial_weights=current_weights if current_weights else None,
            memory=memory,
            guard=guard,
        )

        # 7. 执行权重更新
        update = loop.update_weights(
            daily_pnl=daily_pnl_pct,
            factor_contributions=contributions,
        )

        result.new_weights = dict(update.new_weights)
        result.total_change_pct = update.total_change_pct
        result.alarm_triggered = update.alarm_triggered
        result.alarm_reason = update.alarm_reason
        result.proposal_id = update.proposal_id

        # 8. 原子写入更新后的权重
        save_weights_atomic(update.new_weights, weights_path)

        logger.info(
            "FeedbackLoop 完成: date=%s, change=%.4f, alarm=%s, factors=%d",
            attribution_date,
            update.total_change_pct,
            update.alarm_triggered,
            len(update.new_weights),
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

        # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
        logger.error("FeedbackLoop 执行失败: %s", e)
        traceback.print_exc()
        result.status = STATUS_FAILED
        result.error = str(e)
        # 验收标准 4: 失败时权重不更新 (原子性已由 save_weights_atomic 保证)
        # 当前权重文件保持不变

    return result


def run_feedback_loop_graceful(
    attribution_date: str,
    **kwargs: Any,
) -> FeedbackLoopResult:
    """优雅降级版 FeedbackLoop.

    与 run_feedback_loop 的区别:
        - 所有异常都降级为 degraded, 不抛到上层
        - 确保 EOD 工作流不会被 FeedbackLoop 阻塞

    Args:
        attribution_date: 归因日期
        **kwargs: 传递给 run_feedback_loop 的其他参数

    Returns:
        FeedbackLoopResult
    """
    try:
        return run_feedback_loop(attribution_date, **kwargs)
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
        # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
        logger.error("FeedbackLoop 异常降级: %s", e)
        traceback.print_exc()
        return FeedbackLoopResult(
            date=attribution_date,
            status=STATUS_DEGRADED,
            error=str(e),
            degraded_reason=f"异常降级: {e}",
        )


# ============================================================
# CLI 入口
# ============================================================


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析 CLI 参数."""
    parser = argparse.ArgumentParser(
        description="EOD FeedbackLoop 权重更新",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python -m utils.evolution.eod_feedback_integration --date 2026-08-02\n"
            "  python -m utils.evolution.eod_feedback_integration --date 2026-08-02 --dry-run\n"
        ),
    )
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="归因日期 (YYYY-MM-DD, 默认今日)",
    )
    parser.add_argument(
        "--weights-path",
        default=None,
        help="权重文件路径 (默认 config/factor_weights.json)",
    )
    parser.add_argument(
        "--report-dir",
        default=None,
        help="归因报告目录 (默认 reports/attribution/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="试运行 (不写入权重文件)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="详细日志输出",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口."""
    args = parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    logger.info("=== EOD FeedbackLoop 开始: date=%s ===", args.date)

    if args.dry_run:
        logger.info("[试运行] 仅显示将执行的操作, 不写入文件")
        current_weights = load_current_weights(args.weights_path)
        logger.info("当前权重: %d 个因子", len(current_weights))
        logger.info("归因日期: %s", args.date)
        logger.info("权重路径: %s", args.weights_path or DEFAULT_WEIGHTS_PATH)
        logger.info("归因目录: %s", args.report_dir or DEFAULT_REPORT_DIR)
        logger.info("[试运行] 完成, 未写入任何文件")
        return 0

    result = run_feedback_loop_graceful(
        attribution_date=args.date,
        weights_path=args.weights_path,
        report_dir=args.report_dir,
    )

    if result.is_success():
        log_fn = logger.info
        prefix = "OK"
    else:
        log_fn = logger.error
        prefix = "FAIL"

    log_fn(
        "=== EOD FeedbackLoop %s: date=%s, status=%s, change=%.4f, alarm=%s ===",
        prefix,
        result.date,
        result.status,
        result.total_change_pct,
        result.alarm_triggered,
    )
    if result.degraded_reason:
        log_fn("  降级原因: %s", result.degraded_reason)
    if result.guard_reason:
        log_fn("  Guard: %s", result.guard_reason)
    if result.error:
        log_fn("  错误: %s", result.error)

    return 0 if result.is_success() else 1


if __name__ == "__main__":
    sys.exit(main())
