#!/usr/bin/env python3
"""gradual_rollout_manager.py — 进化编排器灰度发布管理器

管理 USE_EVOLUTION_ORCHESTRATOR 的三阶段灰度发布:
    Stage 1 (10%):  10% 流量启用, 观察 3 日健康度
    Stage 2 (50%):  50% 流量启用, 观察 3 日健康度
    Stage 3 (100%): 全量启用, 持续监控

灰度判断: hash(date) % 100 < rollout_percent
健康度检查: EvolutionOrchestratorV2.get_loop_health_metrics()
自动暂停: l2_promote_rate < 阈值 或 avg_latency_ms > 阈值 → 暂停推进

安全约束:
    - HC-1: 每阶段推进需前阶段健康度达标
    - HC-2: 任何阶段可一键回滚 (disable flag)
    - HC-3: 所有状态变更写入 reports/evolution/rollout_status.json 审计

运行方式:
    python scripts/gradual_rollout_manager.py --check     # 检查当前灰度状态
    python scripts/gradual_rollout_manager.py --advance   # 尝试推进到下一阶段
    python scripts/gradual_rollout_manager.py --rollback  # 紧急回滚
    python scripts/gradual_rollout_manager.py --auto      # 每日自动调度
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logger = logging.getLogger(__name__)

STATUS_PATH = _PROJECT_ROOT / "reports" / "evolution" / "rollout_status.json"
FLAG_NAME = "USE_EVOLUTION_ORCHESTRATOR"


class RolloutStage(Enum):
    """灰度发布阶段."""

    NOT_STARTED = 0
    STAGE_1_10PCT = 1
    STAGE_2_50PCT = 2
    STAGE_3_100PCT = 3
    PAUSED = -1
    ROLLED_BACK = -2


STAGE_PERCENTS = {
    RolloutStage.NOT_STARTED: 0,
    RolloutStage.STAGE_1_10PCT: 30,  # Stage 1 调整为 30% (2026-08-22, 已有140测试+18 E2E验证)
    RolloutStage.STAGE_2_50PCT: 50,
    RolloutStage.STAGE_3_100PCT: 100,
    RolloutStage.PAUSED: 0,
    RolloutStage.ROLLED_BACK: 0,
}

STAGE_ORDER = [
    RolloutStage.NOT_STARTED,
    RolloutStage.STAGE_1_10PCT,
    RolloutStage.STAGE_2_50PCT,
    RolloutStage.STAGE_3_100PCT,
]

DEFAULT_HEALTH_THRESHOLDS = {
    "min_l2_promote_rate": 0.3,
    "max_avg_latency_ms": 5000.0,
    "min_evolution_trigger_rate": 0.1,
}

DEFAULT_OBSERVATION_DAYS = 3


@dataclass
class RolloutStatus:
    """灰度发布状态."""

    stage: RolloutStage = RolloutStage.NOT_STARTED
    percent: int = 0
    stage_start_date: str = ""
    observation_days_required: int = DEFAULT_OBSERVATION_DAYS
    health_thresholds: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_HEALTH_THRESHOLDS)
    )
    last_health_check: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.name,
            "stage_value": self.stage.value,
            "percent": self.percent,
            "stage_start_date": self.stage_start_date,
            "observation_days_required": self.observation_days_required,
            "health_thresholds": self.health_thresholds,
            "last_health_check": self.last_health_check,
            "history": self.history,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RolloutStatus:
        stage_name = data.get("stage", "NOT_STARTED")
        try:
            stage = RolloutStage[stage_name]
        except KeyError:
            stage = RolloutStage.NOT_STARTED
        return cls(
            stage=stage,
            percent=data.get("percent", STAGE_PERCENTS.get(stage, 0)),
            stage_start_date=data.get("stage_start_date", ""),
            observation_days_required=data.get(
                "observation_days_required", DEFAULT_OBSERVATION_DAYS
            ),
            health_thresholds=data.get(
                "health_thresholds", dict(DEFAULT_HEALTH_THRESHOLDS)
            ),
            last_health_check=data.get("last_health_check", {}),
            history=data.get("history", []),
        )


def should_run_on_date(date_str: str, percent: int) -> bool:
    """灰度判断: hash(date) % 100 < percent → 该日期启用进化.

    Args:
        date_str: 日期字符串 (如 "2026-08-22")
        percent: 灰度比例 (0-100)

    Returns:
        True 如果该日期应启用进化编排器
    """
    if percent <= 0:
        return False
    if percent >= 100:
        return True
    h = int(hashlib.md5(date_str.encode(), usedforsecurity=False).hexdigest()[:8], 16)
    return (h % 100) < percent


def load_status() -> RolloutStatus:
    """加载灰度发布状态."""
    if not STATUS_PATH.exists():
        return RolloutStatus()
    try:
        data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        return RolloutStatus.from_dict(data)
    except (json.JSONDecodeError, OSError, KeyError, TypeError) as e:
        logger.warning("加载 rollout_status.json 失败, 使用默认状态: %s", e)
        return RolloutStatus()


def save_status(status: RolloutStatus) -> None:
    """保存灰度发布状态."""
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(
        json.dumps(status.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def collect_health_metrics() -> dict[str, Any]:
    """收集闭环健康度指标.

    Returns:
        健康度指标字典, 失败时返回空字典
    """
    try:
        from utils.evolution.orchestrator import EvolutionOrchestratorV2

        orch = EvolutionOrchestratorV2()
        return orch.get_loop_health_metrics()
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        ImportError,
    ) as e:
        logger.warning("收集健康度指标失败: %s", e)
        return {}


def check_health(status: RolloutStatus) -> tuple[bool, str]:
    """检查当前健康度是否达标.

    Returns:
        (passed, reason)
    """
    metrics = collect_health_metrics()
    if not metrics:
        return False, "metrics_unavailable"

    status.last_health_check = metrics
    thresholds = status.health_thresholds

    total_cycles = metrics.get("total_cycles", 0)
    if total_cycles < 3:
        return False, f"insufficient_cycles ({total_cycles} < 3)"

    l2_promote_rate = metrics.get("l2_promote_rate", 0.0)
    min_promote = thresholds.get("min_l2_promote_rate", 0.3)
    if l2_promote_rate < min_promote:
        return False, f"l2_promote_rate {l2_promote_rate:.2f} < {min_promote}"

    avg_latency = metrics.get("avg_latency_ms", 0.0)
    max_latency = thresholds.get("max_avg_latency_ms", 5000.0)
    if avg_latency > max_latency:
        return False, f"avg_latency {avg_latency:.0f}ms > {max_latency:.0f}ms"

    trigger_rate = metrics.get("evolution_trigger_rate", 0.0)
    min_trigger = thresholds.get("min_evolution_trigger_rate", 0.1)
    if trigger_rate < min_trigger:
        return False, f"evolution_trigger_rate {trigger_rate:.2f} < {min_trigger}"

    return True, "all_checks_passed"


def observation_days_elapsed(status: RolloutStatus, today: str | None = None) -> int:
    """计算当前阶段观察天数.

    优先使用实际进化循环次数 (loop_health.total_cycles),
    若不可用则回退到日历天数.
    """
    # 优先: 实际进化循环次数 (低灰度比例下更合理)
    metrics = collect_health_metrics()
    if metrics and metrics.get("total_cycles", 0) > 0:
        return int(metrics["total_cycles"])

    # 回退: 日历天数
    if not status.stage_start_date:
        return 0
    try:
        start = datetime.fromisoformat(status.stage_start_date).date()
        current = datetime.fromisoformat(
            today or datetime.now().date().isoformat()
        ).date()
        return (current - start).days
    except (ValueError, TypeError) as e:
        logger.warning("计算观察天数失败: %s", e)
        return 0


def advance_stage(status: RolloutStatus) -> tuple[bool, str]:
    """尝试推进到下一灰度阶段.

    Returns:
        (success, reason)
    """
    if status.stage in (RolloutStage.PAUSED, RolloutStage.ROLLED_BACK):
        return False, f"cannot advance from {status.stage.name}"

    current_idx = STAGE_ORDER.index(status.stage) if status.stage in STAGE_ORDER else 0
    if current_idx >= len(STAGE_ORDER) - 1:
        return False, "already_at_max_stage (100%)"

    if status.stage != RolloutStage.NOT_STARTED:
        days = observation_days_elapsed(status)
        if days < status.observation_days_required:
            return (
                False,
                f"observation_days {days} < {status.observation_days_required}",
            )

        passed, reason = check_health(status)
        if not passed:
            return False, f"health_check_failed: {reason}"

    next_stage = STAGE_ORDER[current_idx + 1]
    old_stage = status.stage
    status.stage = next_stage
    status.percent = STAGE_PERCENTS[next_stage]
    status.stage_start_date = datetime.now().date().isoformat()
    status.history.append(
        {
            "action": "advance",
            "from": old_stage.name,
            "to": next_stage.name,
            "percent": status.percent,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
    )
    save_status(status)
    logger.info(
        "灰度发布推进: %s → %s (%d%%)", old_stage.name, next_stage.name, status.percent
    )
    return True, f"advanced_to_{next_stage.name}"


def rollback(status: RolloutStatus, reason: str = "manual_rollback") -> None:
    """紧急回滚灰度发布."""
    old_stage = status.stage
    status.stage = RolloutStage.ROLLED_BACK
    status.percent = 0
    status.stage_start_date = ""
    status.history.append(
        {
            "action": "rollback",
            "from": old_stage.name,
            "to": RolloutStage.ROLLED_BACK.name,
            "reason": reason,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
    )
    save_status(status)

    try:
        from utils.infra.feature_flags import disable

        disable(FLAG_NAME, signer="rollout_manager", reason=reason)
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
    ) as e:
        logger.warning("禁用 Feature Flag 失败 (容错): %s", e)

    logger.warning(
        "灰度发布已回滚: %s → ROLLED_BACK (reason: %s)", old_stage.name, reason
    )


def get_current_percent() -> int:
    """获取当前灰度比例."""
    status = load_status()
    return status.percent


def should_run_today(date_str: str | None = None) -> bool:
    """判断今天是否应启用进化编排器.

    Args:
        date_str: 日期字符串, None=今天

    Returns:
        True 如果今天应启用
    """
    if date_str is None:
        date_str = datetime.now().date().isoformat()
    percent = get_current_percent()
    return should_run_on_date(date_str, percent)


def print_status() -> None:
    """打印当前灰度发布状态."""
    status = load_status()
    print("灰度发布状态:")
    print(f"  阶段: {status.stage.name} ({status.percent}%)")
    print(f"  开始日期: {status.stage_start_date or 'N/A'}")
    print(f"  观察期: {status.observation_days_required} 日")
    if status.stage not in (RolloutStage.NOT_STARTED, RolloutStage.ROLLED_BACK):
        days = observation_days_elapsed(status)
        print(f"  已观察: {days} 日")
    if status.last_health_check:
        h = status.last_health_check
        print("  健康度:")
        print(f"    total_cycles: {h.get('total_cycles', 0)}")
        print(f"    l2_promote_rate: {h.get('l2_promote_rate', 0):.2f}")
        print(f"    avg_latency_ms: {h.get('avg_latency_ms', 0):.1f}")
    print(f"  历史记录: {len(status.history)} 条")


def main() -> int:
    """CLI 入口."""
    import argparse

    parser = argparse.ArgumentParser(description="灰度发布管理器")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="检查当前状态")
    group.add_argument("--advance", action="store_true", help="推进到下一阶段")
    group.add_argument("--rollback", action="store_true", help="紧急回滚")
    group.add_argument("--auto", action="store_true", help="每日自动调度")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )

    if args.check:
        print_status()
        return 0

    if args.advance:
        status = load_status()
        success, reason = advance_stage(status)
        print(f"推进结果: {'成功' if success else '失败'} — {reason}")
        return 0 if success else 1

    if args.rollback:
        status = load_status()
        rollback(status, "cli_manual_rollback")
        print("已回滚")
        return 0

    if args.auto:
        status = load_status()
        if status.stage in (
            RolloutStage.NOT_STARTED,
            RolloutStage.STAGE_1_10PCT,
            RolloutStage.STAGE_2_50PCT,
        ):
            success, reason = advance_stage(status)
            if success:
                logger.info("自动推进成功: %s", reason)
            else:
                logger.info("自动推进跳过: %s", reason)
        print_status()
        return 0

    print_status()
    return 0


if __name__ == "__main__":
    sys.exit(main())
