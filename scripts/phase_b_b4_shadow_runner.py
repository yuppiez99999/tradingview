"""Phase B B4 shadow 模式运行器 — USE_MLOPS_PIPELINE 完整管线外层循环 shadow 入口.

shadow 模式运行 MLOps 管线外层循环 (LLM 反馈闭环) 但不切任何 flag (USE_MLOPS_PIPELINE 保持 False),
产出 shadow 路径 LLM 闭环验证报告, 供 08-24 决策.

LLM 反馈闭环验证:
    KnowledgeBase (G6 Phase D) 写入 → DualLoopOrchestrator 读取 → 策略 ideation 反哺
    shadow 路径不触发真实下单, 仅验证闭环数据流完整性.

降级护栏:
    - LLM 不可用 → 降级至 B3 (仅自动重训, 无 LLM 反馈)
    - 知识库写入失败 → 记录错误, 闭环标记为 broken
    - 连续失败 ≥3 次 → 自动回退 B3

用法:
    # 检查 flag 不变式
    py -X utf8 scripts/phase_b_b4_shadow_runner.py --check-invariant

    # 每日 shadow 运行
    py -X utf8 scripts/phase_b_b4_shadow_runner.py

    # 指定日期
    py -X utf8 scripts/phase_b_b4_shadow_runner.py --date 2026-08-15

对齐 spec §5.5 + design §2.4 + tasks T1.3.
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

logger = logging.getLogger("B4Shadow")

FLAG_NAME = "USE_MLOPS_PIPELINE"
SHADOW_REPORT_DIR = Path(_PROJECT_ROOT) / "reports" / "shadow"
SHADOW_STATUS_FILE = SHADOW_REPORT_DIR / "b4_shadow_status.json"
SYSTEM_CONFIG_FILE = Path(_PROJECT_ROOT) / "system_config.json"
WARMUP_TARGET_DAYS = 7
LLM_FAILURE_THRESHOLD = 3
SHADOW_DAYS_DEFAULT = 7


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ============================================================
# B3StatusChecker — B3 状态前置检查器
# ============================================================


@dataclass
class B3Status:
    enabled: bool
    healthy: bool
    shadow_days: int
    message: str


def check_b3_status() -> B3Status:
    """确认 B3 已就绪 (优先 b3_shadow_status.json, 缺失时回退 enabler 阶段轨判定).

    口径修正 (2026-09-01): B3 (USE_AUTO_RETRAIN) 已于 08-27 经 enabler 阶段轨评估
    启用 (阶段内稳定 3/3 达标后推进 Stage 4), b3_shadow_runner 独立 shadow 机制
    已被阶段轨吸收 — b3_shadow_status.json 缺失不再阻断 B4 shadow 启动.
    指针: cairn/LOG.md 2026-09-01 条目
    """
    b3_status_file = SHADOW_REPORT_DIR / "b3_shadow_status.json"
    if not b3_status_file.exists():
        return _check_b3_via_enabler_stage()
    try:
        with open(b3_status_file, encoding="utf-8") as f:
            data = json.load(f)
        warmup_days = data.get("warmup_days", 0)
        rollback_count = data.get("rollback_count", 0)
        if warmup_days >= WARMUP_TARGET_DAYS and rollback_count == 0:
            return B3Status(
                True, True, warmup_days, f"B3 shadow 已跑 {warmup_days} 天, 无回退"
            )
        return B3Status(
            False,
            False,
            warmup_days,
            f"B3 未稳定: warmup_days={warmup_days}/{WARMUP_TARGET_DAYS}, rollback_count={rollback_count}",
        )
    except (json.JSONDecodeError, OSError) as e:
        return B3Status(False, False, 0, f"读取 B3 状态失败: {e}")


_PHASE_B_STATUS_FILE = _PROJECT_ROOT / "reports" / "evolution" / "phase_b_status.json"


def _check_b3_via_enabler_stage() -> B3Status:
    """B3 前置回退判定 (口径修正 2026-09-01): 读 phase_b_status.json 阶段轨状态.

    判定条件:
        1. USE_AUTO_RETRAIN=True (B3 flag 未被回滚)
        2. stage ∈ {auto_retrain, orchestrator} (B3 阶段已到达或已越过)
        3. auto_retrain 阶段需阶段内稳定日 >= 3; orchestrator 视为推进门禁
           已验证 (阶段轨吸收), 直接就绪
    """
    try:
        if not _PHASE_B_STATUS_FILE.exists():
            return B3Status(False, False, 0, "B3 前置失败: phase_b_status.json 不存在")
        data = json.loads(
            _PHASE_B_STATUS_FILE.read_text(encoding="utf-8", errors="replace")
        )
        flags = data.get("flags_enabled", {})
        stage = str(data.get("stage", ""))
        if not flags.get("USE_AUTO_RETRAIN", False):
            return B3Status(False, False, 0, "B3 前置失败: USE_AUTO_RETRAIN 未启用")
        if stage not in ("auto_retrain", "orchestrator"):
            return B3Status(
                False, False, 0, f"B3 前置失败: 当前阶段 {stage} 未到达 auto_retrain"
            )
        if stage == "orchestrator":
            return B3Status(
                True,
                True,
                -1,
                "B3 经 enabler 阶段轨就绪 (已推进 orchestrator, 推进门禁已验证 B3 稳定)",
            )
        stage_start = str(data.get("current_stage_start", ""))
        stage_stable_days = sum(
            1
            for rec in data.get("daily_health_log", [])
            if str(rec.get("date", "")) >= stage_start and rec.get("healthy")
        )
        if stage_stable_days >= 3:
            return B3Status(
                True,
                True,
                stage_stable_days,
                f"B3 经 enabler 阶段轨就绪 (阶段内稳定 {stage_stable_days}/3)",
            )
        return B3Status(
            False,
            False,
            stage_stable_days,
            f"B3 阶段内稳定 {stage_stable_days}/3 不足",
        )
    except (json.JSONDecodeError, OSError, TypeError) as e:
        return B3Status(False, False, 0, f"B3 阶段轨状态读取失败: {e}")


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
    """确认 USE_MLOPS_PIPELINE 仍为 False."""
    val = read_flag_default(FLAG_NAME)
    if val:
        logger.error("FLAG 不变式被破坏: %s=True (应为 False)!", FLAG_NAME)
        return False
    logger.info("FLAG 不变式校验通过: %s=False", FLAG_NAME)
    return True


# ============================================================
# LLMFeedbackLoopValidator — LLM 反馈闭环验证器
# ============================================================


@dataclass
class LLMLoopResult:
    loop_closed: bool = False
    kb_write_success: bool = True
    kb_read_success: bool = True
    ideation_feedback_received: bool = False
    error_message: str = ""
    entries_written: int = 0
    entries_read: int = 0


def _validate_llm_feedback_loop(
    knowledge_base_path: Path | None = None,
) -> LLMLoopResult:
    """验证 LLM 反馈闭环: KnowledgeBase 写入 → DualLoopOrchestrator 读取 → ideation 反哺.

    shadow 模式下不真正调用 LLM, 仅验证数据流完整性.
    """
    try:
        from utils.llm_evolution.knowledge_base import KnowledgeBase, KnowledgeEntry

        kb = KnowledgeBase()

        test_entry = KnowledgeEntry(
            entry_id=f"b4_shadow_test_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            timestamp=_utc_now_iso(),
            hypothesis_id="b4_shadow_hypothesis",
            description="B4 shadow 闭环验证测试条目",
            factor_name="mom_20d",
            strategy_style="mid",
            factor_direction="long",
            status="validated",
            rank_ic_mean=0.05,
            icir=0.8,
            ic_positive_ratio=0.7,
            attribution="B4 shadow 闭环验证",
            lessons="shadow 模式 LLM 反馈闭环测试",
            llm_model="shadow",
            cycle_id="b4_shadow",
        )

        try:
            kb.persist(test_entry)
            kb_write_success = True
            entries_written = 1
        except Exception as e:
            kb_write_success = False
            entries_written = 0
            return LLMLoopResult(
                loop_closed=False,
                kb_write_success=False,
                error_message=f"知识库写入失败: {e}",
            )

        try:
            ctx = kb.load_context_for_ideation()
            kb_read_success = True
            # 修复 (2026-09-01): load_context_for_ideation 返回 str (格式化上下文文本),
            # 原判定 isinstance(ctx, (list, tuple)) 恒 False → entries_read 恒 0 →
            # 闭环恒判未闭合 (连续 3 次将误触发回退 B3)。改为按实际返回类型判定。
            if isinstance(ctx, (list, tuple)):
                entries_read = len(ctx)
                ideation_feedback_received = entries_read > 0
            elif isinstance(ctx, str):
                entries_read = len(ctx)
                ideation_feedback_received = bool(ctx.strip())
            else:
                entries_read = 0
                ideation_feedback_received = False
        except Exception as e:
            kb_read_success = False
            entries_read = 0
            ideation_feedback_received = False
            return LLMLoopResult(
                loop_closed=False,
                kb_write_success=True,
                kb_read_success=False,
                error_message=f"知识库读取失败: {e}",
                entries_written=entries_written,
            )

        loop_closed = (
            kb_write_success and kb_read_success and ideation_feedback_received
        )

        return LLMLoopResult(
            loop_closed=loop_closed,
            kb_write_success=kb_write_success,
            kb_read_success=kb_read_success,
            ideation_feedback_received=ideation_feedback_received,
            entries_written=entries_written,
            entries_read=entries_read,
        )

    except ImportError as e:
        return LLMLoopResult(
            loop_closed=False,
            error_message=f"LLM 模块不可用 (降级至 B3): {e}",
        )
    except Exception as e:
        return LLMLoopResult(
            loop_closed=False,
            error_message=f"LLM 反馈闭环验证异常: {e}",
        )


# ============================================================
# ShadowRunner — B4 shadow 运行器
# ============================================================


@dataclass
class B4ShadowResult:
    loop_closed: bool = False
    kb_write_success: bool = True
    kb_read_success: bool = True
    ideation_feedback_received: bool = False
    llm_available: bool = True
    need_rollback: bool = False
    rollback_reason: str = ""
    suggestion: str = ""
    entries_written: int = 0
    entries_read: int = 0


def run_shadow(
    b3_shadow_result_path: Path | None = None,
    knowledge_base_path: Path | None = None,
    shadow_days: int = SHADOW_DAYS_DEFAULT,
) -> B4ShadowResult:
    """B4 shadow 主入口: 验证 LLM 反馈闭环完整性.

    Args:
        b3_shadow_result_path: B3 shadow 结果路径
        knowledge_base_path: 知识库路径
        shadow_days: shadow 运行天数

    Returns:
        B4ShadowResult: shadow 运行结果
    """
    loop_result = _validate_llm_feedback_loop(knowledge_base_path)

    if not loop_result.kb_write_success:
        return B4ShadowResult(
            loop_closed=False,
            kb_write_success=False,
            llm_available=True,
            need_rollback=True,
            rollback_reason=f"知识库写入失败: {loop_result.error_message}",
            suggestion="需人工介入 (知识库写入失败)",
        )

    if not loop_result.kb_read_success:
        return B4ShadowResult(
            loop_closed=False,
            kb_write_success=True,
            kb_read_success=False,
            llm_available=True,
            need_rollback=True,
            rollback_reason=f"知识库读取失败: {loop_result.error_message}",
            suggestion="需人工介入 (知识库读取失败)",
        )

    if not loop_result.loop_closed and "不可用" in loop_result.error_message:
        return B4ShadowResult(
            loop_closed=False,
            llm_available=False,
            need_rollback=False,
            rollback_reason="",
            suggestion=f"LLM 降级至 B3: {loop_result.error_message}",
        )

    if loop_result.loop_closed:
        suggestion = f"闭环完整 (写入 {loop_result.entries_written} / 读取 {loop_result.entries_read})"
    else:
        suggestion = f"闭环未闭合: {loop_result.error_message}"

    return B4ShadowResult(
        loop_closed=loop_result.loop_closed,
        kb_write_success=loop_result.kb_write_success,
        kb_read_success=loop_result.kb_read_success,
        ideation_feedback_received=loop_result.ideation_feedback_received,
        llm_available=True,
        need_rollback=False,
        suggestion=suggestion,
        entries_written=loop_result.entries_written,
        entries_read=loop_result.entries_read,
    )


# ============================================================
# ShadowStatus — shadow 状态独立存储
# ============================================================


def load_shadow_status() -> dict:
    if not SHADOW_STATUS_FILE.exists():
        return {
            "run_count": 0,
            "last_run": "",
            "warmup_days": 0,
            "consecutive_failures": 0,
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
            "consecutive_failures": 0,
            "history": [],
        }


def update_shadow_status(result: B4ShadowResult, date: str) -> dict:
    """原位更新 b4_shadow_status.json."""
    status = load_shadow_status()

    history = status.get("history", [])
    # 同日幂等 (2026-09-05 治理): 同日重跑替换旧条目而非重复 append,
    # 与 run_count/warmup_days 幂等语义对齐 (此前 09-01~09-03 同日重跑
    # 曾产生 09-01x5/09-02x3/09-03x3 共 12 条 history vs run_count=4 的错位)
    history = [h for h in history if h.get("date") != date]
    history.append(
        {
            "date": date,
            "loop_closed": result.loop_closed,
            "llm_available": result.llm_available,
            "need_rollback": result.need_rollback,
            "suggestion": result.suggestion,
        }
    )
    if len(history) > 30:
        history = history[-30:]

    # 修复 (2026-09-01): 同日重跑幂等 — last_run == date 时不累加 warmup_days/run_count
    # (避免手动调试或 cron 重试导致预热天数虚高, 误判 7 天达标提前)
    is_same_day = status.get("last_run") == date
    if not is_same_day:
        status["run_count"] = status.get("run_count", 0) + 1
        status["warmup_days"] = status.get("warmup_days", 0) + 1
    status["last_run"] = date

    if not result.loop_closed:
        status["consecutive_failures"] = status.get("consecutive_failures", 0) + 1
    else:
        status["consecutive_failures"] = 0

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
class B4ShadowVerificationResult:
    verification_time: str = ""
    loop_closed: bool = False
    kb_write_success: bool = True
    kb_read_success: bool = True
    ideation_feedback_received: bool = False
    llm_available: bool = True
    need_rollback: bool = False
    rollback_reason: str = ""
    suggestion: str = ""
    entries_written: int = 0
    entries_read: int = 0
    warmup_days: int = 0
    flag_invariant: bool = True
    b3_status: dict = field(default_factory=dict)
    warmup_sufficient: bool = False
    consecutive_failures: int = 0


def save_verification_result(result: B4ShadowVerificationResult, date: str) -> str:
    """JSON 原子写入到 reports/shadow/b4_shadow_verification_{date}.json."""
    SHADOW_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = SHADOW_REPORT_DIR / f"b4_shadow_verification_{date}.json"

    data = {
        "verification_time": result.verification_time,
        "loop_closed": result.loop_closed,
        "kb_write_success": result.kb_write_success,
        "kb_read_success": result.kb_read_success,
        "ideation_feedback_received": result.ideation_feedback_received,
        "llm_available": result.llm_available,
        "need_rollback": result.need_rollback,
        "rollback_reason": result.rollback_reason,
        "suggestion": result.suggestion,
        "entries_written": result.entries_written,
        "entries_read": result.entries_read,
        "warmup_days": result.warmup_days,
        "warmup_sufficient": result.warmup_sufficient,
        "warmup_target": WARMUP_TARGET_DAYS,
        "flag_invariant": result.flag_invariant,
        "b3_status": result.b3_status,
        "consecutive_failures": result.consecutive_failures,
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
        description="Phase B B4 shadow 模式运行器 (MLOps 管线)"
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
            f"\n[FLAG 不变式] {'PASS' if ok else 'FAIL'}: {FLAG_NAME}={'False' if not read_flag_default(FLAG_NAME) else 'True'}"  # noqa: E501
        )
        return 0 if ok else 1

    logger.info("B3 状态前置检查...")
    b3 = check_b3_status()
    logger.info(
        "B3 状态: enabled=%s, healthy=%s, shadow_days=%s",
        b3.enabled,
        b3.healthy,
        b3.shadow_days,
    )
    if not b3.healthy:
        print(f"\n[B3 检查] FAIL — {b3.message}")
        print("[B4 Shadow] 拒绝启动: B3 未稳定或预热不足")
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

    logger.info("B4 Shadow 路径运行 (LLM 反馈闭环验证)...")
    run_result = run_shadow(shadow_days=args.shadow_days)
    logger.info(
        "loop_closed=%s, llm_available=%s, need_rollback=%s",
        run_result.loop_closed,
        run_result.llm_available,
        run_result.need_rollback,
    )

    logger.info("FLAG 不变式校验 (运行后)...")
    flag_ok = check_flag_invariant()

    status = update_shadow_status(run_result, date_str)
    warmup_days = status.get("warmup_days", 0)
    warmup_sufficient = warmup_days >= WARMUP_TARGET_DAYS
    consecutive_failures = status.get("consecutive_failures", 0)

    if consecutive_failures >= LLM_FAILURE_THRESHOLD:
        run_result.need_rollback = True
        run_result.rollback_reason = (
            f"连续失败 {consecutive_failures} >= {LLM_FAILURE_THRESHOLD}, 自动回退 B3"
        )

    result = B4ShadowVerificationResult(
        verification_time=_utc_now_iso(),
        loop_closed=run_result.loop_closed,
        kb_write_success=run_result.kb_write_success,
        kb_read_success=run_result.kb_read_success,
        ideation_feedback_received=run_result.ideation_feedback_received,
        llm_available=run_result.llm_available,
        need_rollback=run_result.need_rollback,
        rollback_reason=run_result.rollback_reason,
        suggestion=run_result.suggestion,
        entries_written=run_result.entries_written,
        entries_read=run_result.entries_read,
        warmup_days=warmup_days,
        flag_invariant=flag_ok,
        b3_status={
            "enabled": b3.enabled,
            "healthy": b3.healthy,
            "shadow_days": b3.shadow_days,
        },
        warmup_sufficient=warmup_sufficient,
        consecutive_failures=consecutive_failures,
    )

    report_path = save_verification_result(result, date_str)

    print(f"\n[B4 Shadow 完成] 日期={date_str}")
    print(
        f"[LLM 闭环] {'闭合' if run_result.loop_closed else '未闭合'} / LLM {'可用' if run_result.llm_available else '降级'}"  # noqa: E501
    )
    print(
        f"[知识库] 写入={run_result.entries_written} / 读取={run_result.entries_read}"
    )
    print(f"[降级护栏] {'触发回退' if run_result.need_rollback else '未触发'}")
    if run_result.need_rollback:
        print(f"[回退原因] {run_result.rollback_reason}")
    print(f"[建议] {run_result.suggestion}")
    print(f"[FLAG 不变式] {'PASS' if flag_ok else 'FAIL'}")
    print(
        f"[预热] warmup_days={warmup_days}/{WARMUP_TARGET_DAYS} {'✅ 达标' if warmup_sufficient else '⏳ 不足'}"
    )
    print(f"[连续失败] {consecutive_failures}/{LLM_FAILURE_THRESHOLD}")
    print(f"[报告] {report_path}")
    print(f"[状态] {SHADOW_STATUS_FILE}")

    if not warmup_sufficient:
        print(
            f"[风险] 预热不足 ({warmup_days} < {WARMUP_TARGET_DAYS} 天), 需继续每日 EOD 运行"
        )

    return 0 if (flag_ok and b3.healthy and not run_result.need_rollback) else 1


if __name__ == "__main__":
    sys.exit(main())
