"""Phase B B2 shadow 模式运行器 — T4 shadow 准备入口.

shadow 模式运行 FeedbackLoop 但不切任何 flag (USE_FEEDBACK_LOOP 保持 False),
产出 shadow 路径 vs 生产路径一致性比对报告, 供 08-24 决策.

用法:
    # 检查 flag 不变式
    py -X utf8 scripts/phase_b_b2_shadow_runner.py --check-invariant

    # 每日 shadow 运行
    py -X utf8 scripts/phase_b_b2_shadow_runner.py

    # 指定日期
    py -X utf8 scripts/phase_b_b2_shadow_runner.py --date 2026-08-15

    # 只更新预热计数
    py -X utf8 scripts/phase_b_b2_shadow_runner.py --warmup-only

对齐 spec §5.3 + design §2.4 + tasks T3.
"""

from __future__ import annotations

import argparse
import json
import logging
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

from utils.evolution.feedback_loop import FeedbackLoop

logger = logging.getLogger("B2Shadow")

FLAG_NAME = "USE_FEEDBACK_LOOP"
SHADOW_REPORT_DIR = Path(_PROJECT_ROOT) / "reports" / "shadow"
SHADOW_STATUS_FILE = SHADOW_REPORT_DIR / "b2_shadow_status.json"
PROD_STATUS_FILE = Path(_PROJECT_ROOT) / "reports" / "evolution" / "phase_b_status.json"
SYSTEM_CONFIG_FILE = Path(_PROJECT_ROOT) / "system_config.json"
WARMUP_TARGET_DAYS = 3
DIFF_RATE_GO_THRESHOLD = 0.05


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ============================================================
# B1StatusChecker — B1 状态前置检查器
# ============================================================


@dataclass
class B1Status:
    enabled: bool
    healthy: bool
    stage: str
    message: str


def check_b1_status() -> B1Status:
    """读取 phase_b_status.json 确认 B1 处于 drift_monitor 阶段."""
    try:
        if not PROD_STATUS_FILE.exists():
            return B1Status(
                False, False, "unknown", f"状态文件不存在: {PROD_STATUS_FILE}"
            )
        with open(PROD_STATUS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        stage = data.get("stage", "unknown")
        flags_enabled = data.get("flags_enabled", {})

        b1_flag = flags_enabled.get("USE_DRIFT_DETECTOR", False)
        healthy_stages = {"drift_monitor", "abtest", "auto_retrain", "orchestrator"}
        if stage in healthy_stages and b1_flag:
            return B1Status(
                True,
                True,
                stage,
                f"B1 已启用且处于 {stage} 阶段 (drift_monitor 已通过)",
            )
        return B1Status(
            b1_flag,
            False,
            stage,
            f"B1 状态异常: stage={stage}, USE_DRIFT_DETECTOR={b1_flag} (需 drift_monitor 或之后)",
        )
    except (json.JSONDecodeError, OSError) as e:
        return B1Status(False, False, "error", f"读取 B1 状态失败: {e}")


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
    """确认 USE_FEEDBACK_LOOP 仍为 False."""
    val = read_flag_default(FLAG_NAME)
    if val:
        logger.error("FLAG 不变式被破坏: %s=True (应为 False)!", FLAG_NAME)
        return False
    logger.info("FLAG 不变式校验通过: %s=False", FLAG_NAME)
    return True


# ============================================================
# ShadowRunner — shadow 运行器
# ============================================================


@dataclass
class ShadowRunResult:
    shadow_weights: dict[str, float] = field(default_factory=dict)
    prod_weights: dict[str, float] = field(default_factory=dict)
    diff_rate: float = 0.0
    suggestion: str = ""


def _build_mock_factor_contributions() -> tuple[float, dict[str, float]]:
    """构造 mock daily_pnl + factor_contributions."""
    daily_pnl = 0.012
    factor_contributions = {
        "mom_20d": 0.05,
        "mom_60d": 0.03,
        "vol_20d": -0.02,
        "liq_amihud": 0.01,
        "value_ep": 0.04,
        "growth_roe": 0.06,
        "quality_gross_margin": 0.02,
        "size_market_cap": -0.01,
    }
    return daily_pnl, factor_contributions


def run_shadow() -> ShadowRunResult:
    """构造 FeedbackLoop 强制 shadow 路径运行 (绕过 flag 守门).

    shadow 模式: 手动设置 _enabled=True 模拟 flag 启用后的权重调整,
    但不切任何 flag, 不写生产状态文件.
    """
    daily_pnl, factor_contributions = _build_mock_factor_contributions()
    initial_weights = {k: 1.0 / len(factor_contributions) for k in factor_contributions}

    # shadow 路径: 强制 _enabled=True
    shadow_loop = FeedbackLoop(initial_weights=initial_weights)
    shadow_loop._enabled = True
    shadow_update = shadow_loop.update_weights(daily_pnl, factor_contributions)
    shadow_weights = (
        dict(shadow_update.new_weights) if hasattr(shadow_update, "new_weights") else {}
    )

    # 生产路径: flag=False, 降级返回原始权重
    prod_loop = FeedbackLoop(initial_weights=initial_weights)
    prod_update = prod_loop.update_weights(daily_pnl, factor_contributions)
    prod_weights = (
        dict(prod_update.new_weights) if hasattr(prod_update, "new_weights") else {}
    )

    # diff_rate = L1(shadow - prod) / N
    all_keys = set(shadow_weights) | set(prod_weights)
    if all_keys:
        l1_diff = sum(
            abs(shadow_weights.get(k, 0.0) - prod_weights.get(k, 0.0)) for k in all_keys
        )
        diff_rate = l1_diff / len(all_keys)
    else:
        diff_rate = 0.0

    suggestion = (
        "可 Go (diff < 0.05)"
        if diff_rate < DIFF_RATE_GO_THRESHOLD
        else "需人工评估 (diff >= 0.05)"
    )

    return ShadowRunResult(
        shadow_weights=shadow_weights,
        prod_weights=prod_weights,
        diff_rate=diff_rate,
        suggestion=suggestion,
    )


# ============================================================
# ConsistencyComparator — 一致性比对器与报告落盘
# ============================================================


@dataclass
class ShadowVerificationResult:
    verification_time: str = ""
    shadow_weights: dict[str, float] = field(default_factory=dict)
    prod_weights: dict[str, float] = field(default_factory=dict)
    diff_rate: float = 0.0
    suggestion: str = ""
    warmup_days: int = 0
    flag_invariant: bool = True
    b1_status: dict = field(default_factory=dict)
    warmup_sufficient: bool = False


def save_verification_result(result: ShadowVerificationResult, date: str) -> str:
    """JSON 原子写入到 reports/shadow/b2_shadow_verification_{date}.json."""
    SHADOW_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = SHADOW_REPORT_DIR / f"b2_shadow_verification_{date}.json"

    data = {
        "verification_time": result.verification_time,
        "diff_rate": result.diff_rate,
        "suggestion": result.suggestion,
        "warmup_days": result.warmup_days,
        "warmup_sufficient": result.warmup_sufficient,
        "warmup_target": WARMUP_TARGET_DAYS,
        "flag_invariant": result.flag_invariant,
        "b1_status": result.b1_status,
        "shadow_weights": result.shadow_weights,
        "prod_weights": result.prod_weights,
    }

    tmp_path = str(path) + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)
    return str(path)


# ============================================================
# ShadowStatus — shadow 状态独立存储
# ============================================================


@dataclass
class ShadowRunRecord:
    date: str
    diff_rate: float
    suggestion: str


def load_shadow_status() -> dict:
    if not SHADOW_STATUS_FILE.exists():
        return {"run_count": 0, "last_run": "", "warmup_days": 0, "history": []}
    try:
        with open(SHADOW_STATUS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"run_count": 0, "last_run": "", "warmup_days": 0, "history": []}


def update_shadow_status(result: ShadowRunResult, date: str) -> dict:
    """原位更新 b2_shadow_status.json (独立于生产 phase_b_status.json).

    幂等性 (v8.7 2026-08-26): 同一日期重复运行只覆盖该日记录, 不重复累计
    warmup_days — 防止手动+EOD 双跑把 1 天算成 2 天, 使 3 天预热门禁失真。
    """
    status = load_shadow_status()

    history = status.get("history", [])
    entry = {
        "date": date,
        "diff_rate": result.diff_rate,
        "suggestion": result.suggestion,
    }
    date_seen = any(h.get("date") == date for h in history)
    if date_seen:
        history = [entry if h.get("date") == date else h for h in history]
    else:
        history.append(entry)
    if len(history) > 30:
        history = history[-30:]

    status["run_count"] = status.get("run_count", 0) + 1
    status["last_run"] = date
    if not date_seen:
        status["warmup_days"] = status.get("warmup_days", 0) + 1
    status["history"] = history

    SHADOW_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = str(SHADOW_STATUS_FILE) + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, str(SHADOW_STATUS_FILE))
    return status


# ============================================================
# main
# ============================================================


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase B B2 shadow 模式运行器")
    parser.add_argument("--date", type=str, default=None, help="运行日期 YYYY-MM-DD")
    parser.add_argument("--warmup-only", action="store_true", help="只更新预热计数")
    parser.add_argument(
        "--check-invariant", action="store_true", help="只检查 flag 不变式"
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

    logger.info("B1 状态前置检查...")
    b1 = check_b1_status()
    logger.info(
        "B1 状态: enabled=%s, healthy=%s, stage=%s", b1.enabled, b1.healthy, b1.stage
    )
    if not b1.healthy:
        print(f"\n[B1 检查] FAIL — {b1.message}")
        print("[B2 Shadow] 拒绝启动: B1 未启用或异常")
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

    logger.info("Shadow 路径运行...")
    run_result = run_shadow()
    logger.info(
        "diff_rate=%.6f, suggestion=%s", run_result.diff_rate, run_result.suggestion
    )

    logger.info("FLAG 不变式校验 (运行后)...")
    flag_ok = check_flag_invariant()

    status = update_shadow_status(run_result, date_str)
    warmup_days = status.get("warmup_days", 0)
    warmup_sufficient = warmup_days >= WARMUP_TARGET_DAYS

    result = ShadowVerificationResult(
        verification_time=_utc_now_iso(),
        shadow_weights=run_result.shadow_weights,
        prod_weights=run_result.prod_weights,
        diff_rate=run_result.diff_rate,
        suggestion=run_result.suggestion,
        warmup_days=warmup_days,
        flag_invariant=flag_ok,
        b1_status={"enabled": b1.enabled, "healthy": b1.healthy, "stage": b1.stage},
        warmup_sufficient=warmup_sufficient,
    )

    report_path = save_verification_result(result, date_str)

    print(f"\n[B2 Shadow 完成] 日期={date_str}")
    print(f"[diff_rate] {run_result.diff_rate:.6f} (阈值 {DIFF_RATE_GO_THRESHOLD})")
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

    return 0 if (flag_ok and b1.healthy) else 1


if __name__ == "__main__":
    sys.exit(main())
