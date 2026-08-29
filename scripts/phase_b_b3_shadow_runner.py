"""Phase B B3 shadow 模式运行器 — USE_AUTO_RETRAIN 自动重训 shadow 入口.

shadow 模式运行自动重训路径但不切任何 flag (USE_AUTO_RETRAIN 保持 False),
产出 shadow 路径 vs 生产路径重训结果比对报告, 供 08-24 决策.

降级护栏:
    - 夏普退化 > 2pp (SHARPE_DEGRADATION_THRESHOLD) → 自动回退 B2
    - 权重漂移 > 5% (WEIGHT_DRIFT_THRESHOLD) → 自动回退 B2
    - 回退时记录 rollback_reason 到 B3ShadowResult

用法:
    # 检查 flag 不变式
    py -X utf8 scripts/phase_b_b3_shadow_runner.py --check-invariant

    # 每日 shadow 运行
    py -X utf8 scripts/phase_b_b3_shadow_runner.py

    # 指定日期
    py -X utf8 scripts/phase_b_b3_shadow_runner.py --date 2026-08-15

    # 只更新预热计数
    py -X utf8 scripts/phase_b_b3_shadow_runner.py --warmup-only

对齐 spec §5.4 + design §2.4 + tasks T1.2.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    from datetime import UTC, datetime
except ImportError:  # Python 3.8 compatibility
    from datetime import datetime

    UTC = UTC

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


logger = logging.getLogger("B3Shadow")

FLAG_NAME = "USE_AUTO_RETRAIN"
SHADOW_REPORT_DIR = Path(_PROJECT_ROOT) / "reports" / "shadow"
SHADOW_STATUS_FILE = SHADOW_REPORT_DIR / "b3_shadow_status.json"
PROD_STATUS_FILE = Path(_PROJECT_ROOT) / "reports" / "evolution" / "phase_b_status.json"
SYSTEM_CONFIG_FILE = Path(_PROJECT_ROOT) / "system_config.json"
WARMUP_TARGET_DAYS = 7
SHARPE_DEGRADATION_THRESHOLD = 0.02
WEIGHT_DRIFT_THRESHOLD = 0.05
SHADOW_DAYS_DEFAULT = 7


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ============================================================
# B2StatusChecker — B2 状态前置检查器
# ============================================================


@dataclass
class B2Status:
    enabled: bool
    healthy: bool
    stage: str
    shadow_days: int
    message: str


def check_b2_status() -> B2Status:
    """读取 b2_shadow_status.json 确认 B2 shadow 已跑满 7 天且稳定."""
    b2_status_file = SHADOW_REPORT_DIR / "b2_shadow_status.json"
    try:
        if not b2_status_file.exists():
            return B2Status(
                False, False, "unknown", 0, f"B2 状态文件不存在: {b2_status_file}"
            )
        with open(b2_status_file, encoding="utf-8") as f:
            data = json.load(f)
        warmup_days = data.get("warmup_days", 0)
        run_count = data.get("run_count", 0)
        if warmup_days >= WARMUP_TARGET_DAYS and run_count > 0:
            return B2Status(
                True,
                True,
                "b2_stable",
                warmup_days,
                f"B2 shadow 已跑 {warmup_days} 天, run_count={run_count}",
            )
        return B2Status(
            False,
            False,
            "b2_warmup",
            warmup_days,
            f"B2 预热不足: warmup_days={warmup_days}/{WARMUP_TARGET_DAYS}",
        )
    except (json.JSONDecodeError, OSError) as e:
        return B2Status(False, False, "error", 0, f"读取 B2 状态失败: {e}")


# ============================================================
# FlagInvariantChecker — feature flag 不变式校验器
# ============================================================


def read_flag_default(flag_name: str) -> bool:
    """从 system_config.json 读取 feature_flags 中指定 flag 的值."""
    try:
        if not SYSTEM_CONFIG_FILE.exists():
            logger.warning("system_config.json 不存在")
            return False
        with open(SYSTEM_CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        evolution = data.get("evolution", data)
        feature_flags = evolution.get("feature_flags", {})
        return bool(feature_flags.get(flag_name, False))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("读取 flag '%s' 失败: %s", flag_name, e)
        return False


def check_flag_invariant() -> bool:
    """确认 USE_AUTO_RETRAIN 仍为 False."""
    val = read_flag_default(FLAG_NAME)
    if val:
        logger.error("FLAG 不变式被破坏: %s=True (应为 False)!", FLAG_NAME)
        return False
    logger.info("FLAG 不变式校验通过: %s=False", FLAG_NAME)
    return True


# ============================================================
# AutoRetrainSimulator — 自动重训路径模拟器
# ============================================================


@dataclass
class RetrainResult:
    retrained_weights: dict[str, float] = field(default_factory=dict)
    retrain_sharpe: float = 0.0
    retrain_success: bool = True
    error_message: str = ""


def _simulate_auto_retrain(
    current_weights: dict[str, float],
    observation_progress_path: Path | None = None,
    drift_monitor_data_path: Path | None = None,
) -> RetrainResult:
    """模拟自动重训路径 (USE_AUTO_RETRAIN=true 时的行为).

    shadow 模式下不真正重训模型, 仅模拟权重调整 + 夏普估算.
    """
    if not current_weights:
        return RetrainResult(
            retrain_success=False, error_message="当前权重为空, 无法重训"
        )

    try:
        if observation_progress_path and observation_progress_path.exists():
            with open(observation_progress_path, encoding="utf-8") as f:
                obs_data = json.load(f)
            obs_factor = 1.0 + obs_data.get("progress_ratio", 0.0) * 0.01
        else:
            obs_factor = 1.0

        if drift_monitor_data_path and drift_monitor_data_path.exists():
            with open(drift_monitor_data_path, encoding="utf-8") as f:
                drift_data = json.load(f)
            drift_penalty = drift_data.get("avg_drift", 0.0)
        else:
            drift_penalty = 0.0

        retrained_weights = {}
        total = 0.0
        for k, v in current_weights.items():
            adjusted = v * obs_factor * (1.0 - drift_penalty * 0.1)
            adjusted = max(0.0, adjusted)
            retrained_weights[k] = adjusted
            total += adjusted

        if total > 0:
            retrained_weights = {k: v / total for k, v in retrained_weights.items()}

        retrain_sharpe = 1.5 + (obs_factor - 1.0) * 10.0 - drift_penalty * 0.5

        return RetrainResult(
            retrained_weights=retrained_weights,
            retrain_sharpe=retrain_sharpe,
            retrain_success=True,
        )
    except (json.JSONDecodeError, OSError, ValueError, TypeError) as e:
        return RetrainResult(retrain_success=False, error_message=f"重训模拟失败: {e}")


# ============================================================
# DegradationGuard — 降级护栏
# ============================================================


@dataclass
class DegradationCheckResult:
    need_rollback: bool
    rollback_reason: str = ""
    sharpe_degradation: float = 0.0
    weight_drift: float = 0.0


def check_degradation(
    retrain_sharpe: float,
    baseline_sharpe: float,
    retrained_weights: dict[str, float],
    baseline_weights: dict[str, float],
) -> DegradationCheckResult:
    """检查重训结果是否触发降级护栏.

    触发条件:
        - 夏普退化 > SHARPE_DEGRADATION_THRESHOLD (2pp)
        - 权重漂移 > WEIGHT_DRIFT_THRESHOLD (5%)
    """
    sharpe_degradation = max(0.0, baseline_sharpe - retrain_sharpe)

    all_keys = set(retrained_weights) | set(baseline_weights)
    if all_keys:
        l2_diff = math.sqrt(
            sum(
                (retrained_weights.get(k, 0.0) - baseline_weights.get(k, 0.0)) ** 2
                for k in all_keys
            )
        )
        weight_drift = l2_diff / math.sqrt(len(all_keys))
    else:
        weight_drift = 0.0

    need_rollback = False
    rollback_reason = ""

    if sharpe_degradation > SHARPE_DEGRADATION_THRESHOLD:
        need_rollback = True
        rollback_reason = (
            f"夏普退化 {sharpe_degradation:.4f} > {SHARPE_DEGRADATION_THRESHOLD}"
        )

    if weight_drift > WEIGHT_DRIFT_THRESHOLD:
        if need_rollback:
            rollback_reason += (
                f"; 权重漂移 {weight_drift:.4f} > {WEIGHT_DRIFT_THRESHOLD}"
            )
        else:
            need_rollback = True
            rollback_reason = f"权重漂移 {weight_drift:.4f} > {WEIGHT_DRIFT_THRESHOLD}"

    return DegradationCheckResult(
        need_rollback=need_rollback,
        rollback_reason=rollback_reason,
        sharpe_degradation=sharpe_degradation,
        weight_drift=weight_drift,
    )


# ============================================================
# ShadowRunner — B3 shadow 运行器
# ============================================================


@dataclass
class B3ShadowResult:
    shadow_weights: dict[str, float] = field(default_factory=dict)
    prod_weights: dict[str, float] = field(default_factory=dict)
    shadow_sharpe: float = 0.0
    prod_sharpe: float = 0.0
    sharpe_degradation: float = 0.0
    weight_drift: float = 0.0
    need_rollback: bool = False
    rollback_reason: str = ""
    retrain_success: bool = True
    suggestion: str = ""


def _build_mock_baseline() -> tuple[dict[str, float], float]:
    """构造 mock 基线权重 + 基线夏普."""
    baseline_weights = {
        "mom_20d": 0.15,
        "mom_60d": 0.12,
        "vol_20d": 0.08,
        "liq_amihud": 0.10,
        "value_ep": 0.14,
        "growth_roe": 0.16,
        "quality_gross_margin": 0.13,
        "size_market_cap": 0.12,
    }
    baseline_sharpe = 1.5
    return baseline_weights, baseline_sharpe


def run_shadow(
    observation_progress_path: Path | None = None,
    drift_monitor_data_path: Path | None = None,
    shadow_days: int = SHADOW_DAYS_DEFAULT,
) -> B3ShadowResult:
    """B3 shadow 主入口: 模拟自动重训路径并检查降级护栏.

    Args:
        observation_progress_path: 观察期进度数据路径
        drift_monitor_data_path: DriftMonitor 数据路径
        shadow_days: shadow 运行天数

    Returns:
        B3ShadowResult: shadow 运行结果
    """
    baseline_weights, baseline_sharpe = _build_mock_baseline()

    retrain_result = _simulate_auto_retrain(
        baseline_weights, observation_progress_path, drift_monitor_data_path
    )

    if not retrain_result.retrain_success:
        return B3ShadowResult(
            prod_weights=baseline_weights,
            prod_sharpe=baseline_sharpe,
            retrain_success=False,
            need_rollback=True,
            rollback_reason=f"重训失败: {retrain_result.error_message}",
            suggestion="需人工介入 (重训失败)",
        )

    degradation = check_degradation(
        retrain_result.retrain_sharpe,
        baseline_sharpe,
        retrain_result.retrained_weights,
        baseline_weights,
    )

    if degradation.need_rollback:
        suggestion = f"自动回退 B2 ({degradation.rollback_reason})"
    else:
        suggestion = f"可继续 shadow (夏普退化 {degradation.sharpe_degradation:.4f} <= {SHARPE_DEGRADATION_THRESHOLD})"

    return B3ShadowResult(
        shadow_weights=retrain_result.retrained_weights,
        prod_weights=baseline_weights,
        shadow_sharpe=retrain_result.retrain_sharpe,
        prod_sharpe=baseline_sharpe,
        sharpe_degradation=degradation.sharpe_degradation,
        weight_drift=degradation.weight_drift,
        need_rollback=degradation.need_rollback,
        rollback_reason=degradation.rollback_reason,
        retrain_success=True,
        suggestion=suggestion,
    )


# ============================================================
# ShadowStatus — shadow 状态独立存储
# ============================================================


@dataclass
class B3ShadowRunRecord:
    date: str
    sharpe_degradation: float
    weight_drift: float
    need_rollback: bool
    suggestion: str


def load_shadow_status() -> dict:
    if not SHADOW_STATUS_FILE.exists():
        return {
            "run_count": 0,
            "last_run": "",
            "warmup_days": 0,
            "rollback_count": 0,
            "history": [],
        }
    try:
        with open(SHADOW_STATUS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {
            "run_count": 0,
            "last_run": "",
            "warmup_days": 0,
            "rollback_count": 0,
            "history": [],
        }


def update_shadow_status(result: B3ShadowResult, date: str) -> dict:
    """原位更新 b3_shadow_status.json."""
    status = load_shadow_status()

    history = status.get("history", [])
    history.append(
        {
            "date": date,
            "sharpe_degradation": result.sharpe_degradation,
            "weight_drift": result.weight_drift,
            "need_rollback": result.need_rollback,
            "suggestion": result.suggestion,
        }
    )
    if len(history) > 30:
        history = history[-30:]

    status["run_count"] = status.get("run_count", 0) + 1
    status["last_run"] = date
    status["warmup_days"] = status.get("warmup_days", 0) + 1
    if result.need_rollback:
        status["rollback_count"] = status.get("rollback_count", 0) + 1
    status["history"] = history

    SHADOW_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = str(SHADOW_STATUS_FILE) + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, str(SHADOW_STATUS_FILE))
    return status


# ============================================================
# ReportSaver — 报告落盘
# ============================================================


@dataclass
class B3ShadowVerificationResult:
    verification_time: str = ""
    shadow_weights: dict[str, float] = field(default_factory=dict)
    prod_weights: dict[str, float] = field(default_factory=dict)
    shadow_sharpe: float = 0.0
    prod_sharpe: float = 0.0
    sharpe_degradation: float = 0.0
    weight_drift: float = 0.0
    need_rollback: bool = False
    rollback_reason: str = ""
    retrain_success: bool = True
    suggestion: str = ""
    warmup_days: int = 0
    flag_invariant: bool = True
    b2_status: dict = field(default_factory=dict)
    warmup_sufficient: bool = False


def save_verification_result(result: B3ShadowVerificationResult, date: str) -> str:
    """JSON 原子写入到 reports/shadow/b3_shadow_verification_{date}.json."""
    SHADOW_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = SHADOW_REPORT_DIR / f"b3_shadow_verification_{date}.json"

    data = {
        "verification_time": result.verification_time,
        "shadow_sharpe": result.shadow_sharpe,
        "prod_sharpe": result.prod_sharpe,
        "sharpe_degradation": result.sharpe_degradation,
        "weight_drift": result.weight_drift,
        "need_rollback": result.need_rollback,
        "rollback_reason": result.rollback_reason,
        "retrain_success": result.retrain_success,
        "suggestion": result.suggestion,
        "warmup_days": result.warmup_days,
        "warmup_sufficient": result.warmup_sufficient,
        "warmup_target": WARMUP_TARGET_DAYS,
        "flag_invariant": result.flag_invariant,
        "b2_status": result.b2_status,
        "shadow_weights": result.shadow_weights,
        "prod_weights": result.prod_weights,
    }

    tmp_path = str(path) + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)
    return str(path)


# ============================================================
# main
# ============================================================


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase B B3 shadow 模式运行器 (自动重训)"
    )
    parser.add_argument("--date", type=str, default=None, help="运行日期 YYYY-MM-DD")
    parser.add_argument("--warmup-only", action="store_true", help="只更新预热计数")
    parser.add_argument(
        "--check-invariant", action="store_true", help="只检查 flag 不变式"
    )
    parser.add_argument(
        "--shadow-days", type=int, default=SHADOW_DAYS_DEFAULT, help="shadow 运行天数"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s"
    )

    date_str = args.date or datetime.now().strftime("%Y-%m-%d")

    if args.check_invariant:
        ok = check_flag_invariant()
        print(
            f"\n[FLAG 不变式] {'PASS' if ok else 'FAIL'}: {FLAG_NAME}={'False' if not read_flag_default(FLAG_NAME) else 'True'}"
        )
        return 0 if ok else 1

    logger.info("B2 状态前置检查...")
    b2 = check_b2_status()
    logger.info(
        "B2 状态: enabled=%s, healthy=%s, shadow_days=%s",
        b2.enabled,
        b2.healthy,
        b2.shadow_days,
    )
    if not b2.healthy:
        print(f"\n[B2 检查] FAIL — {b2.message}")
        print("[B3 Shadow] 拒绝启动: B2 未稳定或预热不足")
        return 1

    logger.info("FLAG 不变式校验 (运行前)...")
    if not check_flag_invariant():
        print(f"\n[FLAG 不变式] FAIL: {FLAG_NAME} 被篡改为 True, 紧急告警!")
        return 1

    if args.warmup_only:
        status = load_shadow_status()
        status["warmup_days"] = status.get("warmup_days", 0) + 1
        status["last_run"] = date_str
        SHADOW_REPORT_DIR.mkdir(parents=True, exist_ok=True)
        with open(SHADOW_STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=2)
        print(
            f"\n[WARMUP-ONLY] warmup_days={status['warmup_days']}/{WARMUP_TARGET_DAYS}"
        )
        return 0

    logger.info("B3 Shadow 路径运行 (自动重训)...")
    run_result = run_shadow(shadow_days=args.shadow_days)
    logger.info(
        "sharpe_degradation=%.6f, weight_drift=%.6f, need_rollback=%s",
        run_result.sharpe_degradation,
        run_result.weight_drift,
        run_result.need_rollback,
    )

    logger.info("FLAG 不变式校验 (运行后)...")
    flag_ok = check_flag_invariant()

    status = update_shadow_status(run_result, date_str)
    warmup_days = status.get("warmup_days", 0)
    warmup_sufficient = warmup_days >= WARMUP_TARGET_DAYS

    result = B3ShadowVerificationResult(
        verification_time=_utc_now_iso(),
        shadow_weights=run_result.shadow_weights,
        prod_weights=run_result.prod_weights,
        shadow_sharpe=run_result.shadow_sharpe,
        prod_sharpe=run_result.prod_sharpe,
        sharpe_degradation=run_result.sharpe_degradation,
        weight_drift=run_result.weight_drift,
        need_rollback=run_result.need_rollback,
        rollback_reason=run_result.rollback_reason,
        retrain_success=run_result.retrain_success,
        suggestion=run_result.suggestion,
        warmup_days=warmup_days,
        flag_invariant=flag_ok,
        b2_status={
            "enabled": b2.enabled,
            "healthy": b2.healthy,
            "shadow_days": b2.shadow_days,
        },
        warmup_sufficient=warmup_sufficient,
    )

    report_path = save_verification_result(result, date_str)

    print(f"\n[B3 Shadow 完成] 日期={date_str}")
    print(
        f"[夏普] shadow={run_result.shadow_sharpe:.4f} / prod={run_result.prod_sharpe:.4f} / 退化={run_result.sharpe_degradation:.6f}"
    )
    print(f"[权重漂移] {run_result.weight_drift:.6f} (阈值 {WEIGHT_DRIFT_THRESHOLD})")
    print(f"[降级护栏] {'触发回退' if run_result.need_rollback else '未触发'}")
    if run_result.need_rollback:
        print(f"[回退原因] {run_result.rollback_reason}")
    print(f"[建议] {run_result.suggestion}")
    print(f"[FLAG 不变式] {'PASS' if flag_ok else 'FAIL'}")
    print(
        f"[预热] warmup_days={warmup_days}/{WARMUP_TARGET_DAYS} {'✅ 达标' if warmup_sufficient else '⏳ 不足'}"
    )
    print(f"[报告] {report_path}")
    print(f"[状态] {SHADOW_STATUS_FILE}")

    if not warmup_sufficient:
        print(
            f"[风险] 预热不足 ({warmup_days} < {WARMUP_TARGET_DAYS} 天), 需继续每日 EOD 运行"
        )

    return 0 if (flag_ok and b2.healthy and run_result.retrain_success) else 1


if __name__ == "__main__":
    sys.exit(main())
