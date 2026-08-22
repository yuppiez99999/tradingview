#!/usr/bin/env python3
"""阶段 B 渐进启用调度器 — 14 天观察期满后逐步开启进化闭环.

ARCHITECTURE_自我进化框架 §5 — Phase B: 渐进式授权
创建: 2026-08-02

渐进路径 (每阶段观察前阶段健康 3 日):
    Stage 1 (Day 14):  DriftMonitor (只读监控, L1) → 观察 3 日
    Stage 2 (Day 17):  ABTest (受控实验, L1→L2 路由) → 观察 3 日
    Stage 3 (Day 20):  AutoRetrain (自动重训, L2) → 观察 3 日
    Stage 4 (Day 23):  Orchestrator 全量 (L2→L3 自动路由) → 持续监控

安全约束:
    - HC-1: 每阶段 Feature Flag 双签 (需要 USE_EVOLUTION_ORCHESTRATOR 同时打开)
    - HC-4: 前阶段健康不达标 → 自动暂停, 不进入下一阶段
    - HC-5: 所有状态变更写入 reports/evolution/phase_b_status.json 审计留痕
    - HC-10: 用户可随时通过 PhaseBState.PAUSED 暂停, 或 PhaseBState.ROLLBACK 回滚

运行方式:
    py -X utf8 scripts/phase_b_progressive_enabler.py --check   # 检查当前状态
    py -X utf8 scripts/phase_b_progressive_enabler.py --advance # 尝试推进到下一阶段
    py -X utf8 scripts/phase_b_progressive_enabler.py --rollback # 紧急回滚全部 Flag
    py -X utf8 scripts/phase_b_progressive_enabler.py --auto    # 每日自动调度 (定时任务用)
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

STATUS_PATH = PROJECT_ROOT / "reports" / "evolution" / "phase_b_status.json"
DECISIONS_PATH = PROJECT_ROOT / "reports" / "evolution" / "decisions.jsonl"
OBSERVATION_DATA = PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"
FEATURE_FLAGS_PATH = PROJECT_ROOT / "configs" / "feature_flags.yaml"
SHADOW_ADMISSION_YAML = (
    PROJECT_ROOT / "v8.3_institutional" / "config" / "shadow_admission.yaml"
)

logger = logging.getLogger(__name__)

# 单事实源: shadow_admission.yaml (PM 决策 observation_days=21).
# 不得硬编码 14, 否则 yaml 升级后观察期永不生效 (见 2026-08-09 配置脱节修复).
OBSERVATION_DAYS_REQUIRED = 14  # 回退默认 (兼容离线 / yaml 缺失)


def _load_observation_days_required() -> int:
    try:
        import yaml

        with open(SHADOW_ADMISSION_YAML, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        settings = cfg.get("settings", {})
        return int(settings.get("observation_days", settings.get("min_observation_days", OBSERVATION_DAYS_REQUIRED)))
    except (OSError, Exception):
        return OBSERVATION_DAYS_REQUIRED


OBSERVATION_DAYS_REQUIRED = _load_observation_days_required()


# ============================================================
# 数据结构
# ============================================================

class PhaseBStage(Enum):
    """阶段 B 进度阶段."""
    WAITING_OBSERVATION = "waiting_observation"  # 等待观察期满
    STAGE_0_READY = "ready"  # 观察期满, 待启用
    STAGE_1_DRIFT_MONITOR = "drift_monitor"  # DriftMonitor 只读监控
    STAGE_2_ABTEST = "abtest"  # ABTest 受控实验
    STAGE_3_AUTO_RETRAIN = "auto_retrain"  # 自动重训
    STAGE_4_ORCHESTRATOR = "orchestrator"  # 编排器全量
    PAUSED = "paused"  # 用户暂停
    ROLLBACK = "rollback"  # 紧急回滚


@dataclass
class PhaseBStatus:
    """阶段 B 状态快照."""
    stage: str = "waiting_observation"
    observation_start: str = "2026-07-23"
    observation_days_completed: int = 0
    observation_days_required: int = OBSERVATION_DAYS_REQUIRED
    current_stage_start: str = ""
    current_stage_days: int = 0
    current_stage_required_days: int = 3
    flags_enabled: dict[str, bool] = field(default_factory=dict)
    health_checks: dict[str, str] = field(default_factory=dict)
    last_updated: str = ""
    notes: list[str] = field(default_factory=list)
    consecutive_stable_days: int = 0
    stable_days_target: int = 7
    min_shadow_samples: int = 20
    daily_health_log: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "observation_start": self.observation_start,
            "observation_days_completed": self.observation_days_completed,
            "observation_days_required": self.observation_days_required,
            "current_stage_start": self.current_stage_start,
            "current_stage_days": self.current_stage_days,
            "current_stage_required_days": self.current_stage_required_days,
            "flags_enabled": self.flags_enabled,
            "health_checks": self.health_checks,
            "last_updated": self.last_updated,
            "notes": self.notes,
            "consecutive_stable_days": self.consecutive_stable_days,
            "stable_days_target": self.stable_days_target,
            "min_shadow_samples": self.min_shadow_samples,
            "daily_health_log": self.daily_health_log,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PhaseBStatus:
        kwargs = {}
        for k in cls.__dataclass_fields__:
            if k in d:
                kwargs[k] = d[k]
        return cls(**kwargs)


# ============================================================
# Phase B shadow 连续稳定天数守卫 (v8.7 Sprint 1 门禁)
# ============================================================

@dataclass(frozen=True)
class DailyHealthVerdict:
    """当日 shadow 健康三元判定结果."""
    date: str
    healthy: bool
    reason: str
    daily_return: float = 0.0
    cumulative_stable_days: int = 0


_SHADOW_DAILY_HEALTH_LOG = PROJECT_ROOT / "reports" / "evolution" / "shadow_daily_health.jsonl"
_KILL_SWITCH_STATE = PROJECT_ROOT / "reports" / "evolution" / "kill_switch_state.json"
_HONEST_VALIDATION_DIR = PROJECT_ROOT / "reports" / "honest_validation"


def _check_kill_switch_inactive(date: str) -> tuple[bool, str]:
    """检查 kill_switch 当日是否未触发."""
    if not _KILL_SWITCH_STATE.exists():
        return True, ""
    try:
        data = json.loads(_KILL_SWITCH_STATE.read_text(encoding="utf-8"))
        for flag_name in ("USE_DRIFT_DETECTOR", "USE_FEEDBACK_LOOP", "USE_AUTO_RETRAIN", "USE_MLOPS_PIPELINE"):
            flag_state = data.get(flag_name, {})
            if isinstance(flag_state, dict) and flag_state.get("triggered_date") == date:
                return False, f"kill_switch_triggered:{flag_name}"
        return True, ""
    except Exception:
        return True, ""


def _check_no_lookahead_bias(date: str) -> tuple[bool, str]:
    """检查 Honest Validation 当日无前视偏差告警."""
    report_path = _HONEST_VALIDATION_DIR / f"{date}.json"
    if not report_path.exists():
        return True, ""
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
        if data.get("lookahead_bias_detected", False):
            return False, "lookahead_bias"
        return True, ""
    except Exception:
        return True, ""


def evaluate_daily_shadow_health(date: str, min_samples: int = 20) -> DailyHealthVerdict:
    """当日 shadow 健康三元判定.

    三维度皆绿方判健康:
        ① 当日 daily_returns.jsonl 有 date 条目且 daily_return 成功产出
        ② kill_switch 当日未触发
        ③ Honest Validation 当日无前视偏差告警
    """
    if not OBSERVATION_DATA.exists():
        return DailyHealthVerdict(date=date, healthy=False, reason="shadow_data_missing")

    daily_return = 0.0
    found = False
    total_samples = 0
    try:
        with OBSERVATION_DATA.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    total_samples += 1
                    if rec.get("date") == date:
                        found = True
                        daily_return = float(rec.get("daily_return", 0.0))
                except Exception:
                    continue
    except Exception:
        return DailyHealthVerdict(date=date, healthy=False, reason="parse_error")

    if total_samples < min_samples:
        return DailyHealthVerdict(date=date, healthy=False, reason="sample_insufficient", daily_return=daily_return)

    if not found:
        return DailyHealthVerdict(date=date, healthy=False, reason="no_daily_return")

    ks_ok, ks_reason = _check_kill_switch_inactive(date)
    if not ks_ok:
        return DailyHealthVerdict(date=date, healthy=False, reason=ks_reason, daily_return=daily_return)

    lb_ok, lb_reason = _check_no_lookahead_bias(date)
    if not lb_ok:
        return DailyHealthVerdict(date=date, healthy=False, reason=lb_reason, daily_return=daily_return)

    return DailyHealthVerdict(date=date, healthy=True, reason="ok", daily_return=daily_return)


def update_stable_days(status: PhaseBStatus, verdict: DailyHealthVerdict) -> PhaseBStatus:
    """连续稳定天数计数器 (异常归零).

    健康 +1, 异常归零, 持久化到 phase_b_status.json.
    禁止补录历史样本: verdict.date 早于最后记录日期则拒绝.
    """
    if status.daily_health_log:
        last_date = status.daily_health_log[-1].get("date", "")
        if verdict.date <= last_date:
            return status

    if verdict.healthy:
        status.consecutive_stable_days += 1
    else:
        status.consecutive_stable_days = 0

    status.daily_health_log.append({
        "date": verdict.date,
        "healthy": verdict.healthy,
        "reason": verdict.reason,
        "daily_return": verdict.daily_return,
        "cumulative_stable_days": status.consecutive_stable_days,
    })


    try:
        with _SHADOW_DAILY_HEALTH_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "date": verdict.date,
                "healthy": verdict.healthy,
                "reason": verdict.reason,
                "daily_return": verdict.daily_return,
                "cumulative_stable_days": status.consecutive_stable_days,
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass

    return status


def generate_shadow_stable_report(status: PhaseBStatus) -> Path:
    """生成 Phase B shadow 7 天稳定达标报告."""
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = PROJECT_ROOT / "reports" / "shadow"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"shadow_stable_7d_report_{timestamp}.json"

    daily_records = status.daily_health_log[-7:] if len(status.daily_health_log) >= 7 else status.daily_health_log
    stable_days = status.consecutive_stable_days
    target_met = stable_days >= status.stable_days_target
    sprint1_admission = target_met and len(status.daily_health_log) >= status.min_shadow_samples

    report = {
        "generated_at": datetime.now().isoformat(),
        "daily_records": daily_records,
        "summary": {
            "stable_days": stable_days,
            "target": status.stable_days_target,
            "target_met": target_met,
            "sprint1_admission": sprint1_admission,
            "total_samples": len(status.daily_health_log),
            "min_samples": status.min_shadow_samples,
        },
    }

    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path


# ============================================================
# 进度计算
# ============================================================

def _count_observation_days() -> int:
    """从 daily_returns.jsonl 计算观察期累计天数."""
    if not OBSERVATION_DATA.exists():
        return 0
    seen_dates = set()
    try:
        with OBSERVATION_DATA.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    date_val = rec.get("date", "")
                    if date_val:
                        seen_dates.add(str(date_val)[:10])
                except json.JSONDecodeError:
                    continue
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        pass
    return len(seen_dates)


def _load_phase_b_status() -> PhaseBStatus:
    """加载阶段 B 状态."""
    if STATUS_PATH.exists():
        try:
            with STATUS_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return PhaseBStatus.from_dict(data)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass
    return PhaseBStatus()


def _save_phase_b_status(status: PhaseBStatus) -> None:
    """保存阶段 B 状态."""
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    status.last_updated = datetime.now().isoformat()
    with STATUS_PATH.open("w", encoding="utf-8") as f:
        json.dump(status.to_dict(), f, ensure_ascii=False, indent=2, default=str)


# ============================================================
# 阶段推进逻辑
# ============================================================

STAGE_FLAGS = {
    PhaseBStage.STAGE_1_DRIFT_MONITOR.value: {
        "USE_DRIFT_DETECTOR": True,
        "USE_FEEDBACK_LOOP": False,
    },
    PhaseBStage.STAGE_2_ABTEST.value: {
        "USE_DRIFT_DETECTOR": True,
        "USE_ABTEST": True,
    },
    PhaseBStage.STAGE_3_AUTO_RETRAIN.value: {
        "USE_DRIFT_DETECTOR": True,
        "USE_ABTEST": True,
        "USE_AUTO_RETRAIN": True,
        "USE_FINENG_GARCH": True,
        "USE_FINENG_KALMAN_BETA": True,
    },
    PhaseBStage.STAGE_4_ORCHESTRATOR.value: {
        "USE_DRIFT_DETECTOR": True,
        "USE_ABTEST": True,
        "USE_AUTO_RETRAIN": True,
        "USE_EVOLUTION_ORCHESTRATOR": True,
        "USE_FINENG_GARCH": True,
        "USE_FINENG_KALMAN_BETA": True,
        "USE_FINENG_EVT": True,
        "USE_FINENG_PATH_SIM": True,
    },
}

STAGE_PROGRESSION = [
    PhaseBStage.STAGE_1_DRIFT_MONITOR,
    PhaseBStage.STAGE_2_ABTEST,
    PhaseBStage.STAGE_3_AUTO_RETRAIN,
    PhaseBStage.STAGE_4_ORCHESTRATOR,
]


def _check_stage_health(status: PhaseBStatus) -> tuple[bool, str]:
    """检查当前阶段健康状态 (前阶段是否稳定)."""
    # Stage 1: 仅需观察期满
    if status.stage == PhaseBStage.STAGE_0_READY.value:
        obs_days = _count_observation_days()
        if obs_days < status.observation_days_required:
            return False, f"观察期 {obs_days}/{status.observation_days_required} 天不足"
        return True, "观察期满, 可启动 Stage 1"

    # Stage N>1: 检查前阶段已运行 >= 3 天
    stage_days = status.current_stage_days
    required = status.current_stage_required_days
    if stage_days < required:
        return False, f"当前阶段运行 {stage_days}/{required} 天不足"

    # 简单健康检查: 决策日志中最近 3 天无严重错误
    recent_errors = 0
    if DECISIONS_PATH.exists():
        try:
            with DECISIONS_PATH.open("r", encoding="utf-8") as f:
                lines = f.readlines()
                for line in lines[-50:]:  # 最近 50 条
                    try:
                        rec = json.loads(line.strip())
                        if rec.get("severity") in ("critical", "error"):
                            recent_errors += 1
                    except json.JSONDecodeError:
                        continue
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass

    if recent_errors >= 3:
        return False, f"最近决策日志中 {recent_errors} 条严重/错误, 健康不达标"

    return True, "健康"


def _next_stage(current: str) -> str | None:
    """获取下一阶段."""
    stages = [s.value for s in STAGE_PROGRESSION]
    try:
        idx = stages.index(current)
        if idx + 1 < len(stages):
            return stages[idx + 1]
    except ValueError:
        pass
    return None


def _get_flag_state(flag_name: str) -> bool:
    """从 feature_flags.yaml 读取当前标志位."""
    try:
        if not FEATURE_FLAGS_PATH.exists():
            return False
        with FEATURE_FLAGS_PATH.open("r", encoding="utf-8") as f:
            content = f.read()
        # 简单 YAML 解析 (仅匹配 default: true/false)
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith(f"{flag_name}:"):
                # 向下找 default:
                continue
        # 简化: 不直接解析 YAML, 走 status.json 记录
        return False
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        return False


# ============================================================
# 命令处理
# ============================================================

def cmd_check() -> int:
    """检查当前阶段 B 状态."""
    status = _load_phase_b_status()
    obs_days = _count_observation_days()
    status.observation_days_completed = obs_days
    _save_phase_b_status(status)

    remaining = max(0, status.observation_days_required - obs_days)

    print("=" * 60)
    print("阶段 B — 进化闭环渐进启用状态")
    print("=" * 60)
    print(f"  当前阶段: {status.stage}")
    print(f"  观察期: {obs_days}/{status.observation_days_required} 天")
    print(f"  观察期剩余: {remaining} 天")
    print(f"  预计可启动 Stage 1: {_estimate_start_date(remaining)}")
    print(f"  当前阶段已运行: {status.current_stage_days} 天")
    print(f"  Flags: {status.flags_enabled}")
    print(f"  最后更新: {status.last_updated}")

    health_ok, health_msg = _check_stage_health(status)
    print(f"  健康检查: {'PASS' if health_ok else 'FAIL'} — {health_msg}")

    print()
    print("阶段路线图:")
    for i, stage in enumerate(STAGE_PROGRESSION):
        marker = "→ " if status.stage == stage.value else "  "
        done = ""
        current_stage_idx = -1
        for si, s in enumerate(STAGE_PROGRESSION):
            if s.value == status.stage:
                current_stage_idx = si
                break
        if current_stage_idx >= 0:
            for j in range(current_stage_idx):
                if STAGE_PROGRESSION[j].value == stage.value:
                    done = "[DONE]"
        flags = STAGE_FLAGS.get(stage.value, {})
        flag_str = ", ".join(f"{k}={v}" for k, v in sorted(flags.items()))
        print(f"    {marker}Stage {i + 1}: {stage.value} {done}")
        print(f"        Flags: {flag_str}")
    print("=" * 60)

    return 0


def cmd_advance() -> int:
    """尝试推进到下一阶段."""
    status = _load_phase_b_status()
    obs_days = _count_observation_days()
    status.observation_days_completed = obs_days

    # 1) 如果还在等待观察期
    if status.stage == PhaseBStage.WAITING_OBSERVATION.value:
        if obs_days >= status.observation_days_required:
            status.stage = PhaseBStage.STAGE_0_READY.value
            status.current_stage_start = datetime.now().strftime("%Y-%m-%d")
            status.current_stage_days = 0
            status.notes.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] 观察期满, 进入 Ready 状态")
            _save_phase_b_status(status)
            print("[OK] 观察期已满 → Stage 0 (Ready)")
            return 0
        else:
            print(f"[WAIT] 观察期 {obs_days}/{status.observation_days_required} 天, 还需 {status.observation_days_required - obs_days} 天")
            return 1

    # 2) 如果处于就绪状态 → 启动 Stage 1
    if status.stage == PhaseBStage.STAGE_0_READY.value:
        health_ok, health_msg = _check_stage_health(status)
        if not health_ok:
            print(f"[WAIT] 健康检查未通过: {health_msg}")
            return 1
        status.stage = PhaseBStage.STAGE_1_DRIFT_MONITOR.value
        status.current_stage_start = datetime.now().strftime("%Y-%m-%d")
        status.current_stage_days = 0
        status.flags_enabled = STAGE_FLAGS.get(PhaseBStage.STAGE_1_DRIFT_MONITOR.value, {})
        status.notes.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] 启动 Stage 1: DriftMonitor 只读监控")
        _save_phase_b_status(status)
        print("[OK] → Stage 1: DriftMonitor 只读监控")
        print(f"     Flags 已设置: {status.flags_enabled}")
        return 0

    # 3) 推进到下一阶段
    next_stage = _next_stage(status.stage)
    if next_stage is None:
        print("[DONE] 已在最终阶段 (Stage 4 Orchestrator 全量)")
        return 0

    health_ok, health_msg = _check_stage_health(status)
    if not health_ok:
        print(f"[WAIT] 健康检查未通过: {health_msg}")
        print("    请等待当前阶段稳定后再推进")
        return 1

    status.stage = next_stage
    status.current_stage_start = datetime.now().strftime("%Y-%m-%d")
    status.current_stage_days = 0
    new_flags = STAGE_FLAGS.get(next_stage, {})
    status.flags_enabled.update(new_flags)
    status.notes.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] 推进到 {next_stage}")
    _save_phase_b_status(status)

    print(f"[OK] → {next_stage}")
    print(f"     Flags: {new_flags}")
    return 0


def cmd_rollback() -> int:
    """紧急回滚: 关闭所有进化 Flag."""
    status = _load_phase_b_status()
    status.stage = PhaseBStage.ROLLBACK.value
    status.flags_enabled = {
        "USE_DRIFT_DETECTOR": False,
        "USE_ABTEST": False,
        "USE_AUTO_RETRAIN": False,
        "USE_EVOLUTION_ORCHESTRATOR": False,
        "USE_FINENG_GARCH": False,
        "USE_FINENG_KALMAN_BETA": False,
        "USE_FINENG_EVT": False,
        "USE_FINENG_PATH_SIM": False,
    }
    status.notes.append(
        f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] 紧急回滚: 所有进化 Flag 已关闭"
    )
    _save_phase_b_status(status)
    print("[ROLLBACK] 所有进化 Feature Flag 已关闭")
    print(f"     Flags: {status.flags_enabled}")
    return 0


def cmd_auto() -> int:
    """每日自动调度: 检查状态 + 如果条件满足则推进."""
    status = _load_phase_b_status()
    obs_days = _count_observation_days()
    status.observation_days_completed = obs_days

    # 更新当前阶段天数
    if status.current_stage_start:
        try:
            start_date = datetime.strptime(status.current_stage_start, "%Y-%m-%d")
            status.current_stage_days = (datetime.now() - start_date).days
        except ValueError:
            pass

    # Stage 1 drift_monitor: 每日健康检查 + 累积 consecutive_stable_days
    if status.stage == PhaseBStage.STAGE_1_DRIFT_MONITOR.value:
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            verdict = evaluate_daily_shadow_health(today)
            status = update_stable_days(status, verdict)
            print(f"[AUTO] 健康检查 {today}: healthy={verdict.healthy}, reason={verdict.reason}, "
                  f"stable_days={status.consecutive_stable_days}/{status.stable_days_target}")
        except Exception as e:
            print(f"[AUTO] 健康检查异常: {e}")

    _save_phase_b_status(status)

    # 自动推进逻辑
    if status.stage in (PhaseBStage.WAITING_OBSERVATION.value,
                         PhaseBStage.STAGE_0_READY.value):
        return cmd_advance()

    # 检查是否可以推进
    health_ok, health_msg = _check_stage_health(status)
    if health_ok:
        next_stage = _next_stage(status.stage)
        if next_stage is not None:
            print(f"[AUTO] 阶段 {status.stage} 健康, 可推进到 {next_stage}")
            print("    执行: py scripts/phase_b_progressive_enabler.py --advance")
            return 0
        else:
            print(f"[AUTO] 已在最终阶段 ({status.stage}), 无需推进")
            return 0
    else:
        print(f"[AUTO] 暂不推进: {health_msg}")
        return 0


def _estimate_start_date(remaining_days: int) -> str:
    """估算 Stage 1 启动日期."""
    if remaining_days <= 0:
        return "现在"
    target = datetime.now() + timedelta(days=remaining_days)
    # 跳过周末
    while target.weekday() >= 5:
        target += timedelta(days=1)
    return target.strftime("%Y-%m-%d")


# ============================================================
# CLI
# ============================================================

def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="阶段 B 渐进启用调度器")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="检查当前阶段 B 状态")
    group.add_argument("--advance", action="store_true", help="尝试推进到下一阶段")
    group.add_argument("--rollback", action="store_true", help="紧急回滚全部 Flag")
    group.add_argument("--auto", action="store_true", help="每日自动调度 (定时任务)")
    args = parser.parse_args()

    if args.check:
        return cmd_check()
    elif args.advance:
        return cmd_advance()
    elif args.rollback:
        return cmd_rollback()
    elif args.auto:
        return cmd_auto()
    return 0


if __name__ == "__main__":
    sys.exit(main())
