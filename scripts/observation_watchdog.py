"""观察期达标看门狗 — 监控观察期天数, 达标后自动触发漂移判定 (W1.3c Day 6)

背景:
    小样本 PSI 不可靠 (6 天样本 PSI=8.48 是统计噪音, 非真实漂移). 本脚本作为
    "看门狗", 在观察期未满 14 天前禁止触发漂移判定, 仅记录监控状态; 达标后
    自动调用 integrate_cleaned_to_drift.run_integration() 执行真实漂移判定.

工作流:
    1. 读取 observation_progress.json 获取 days_completed / required_days
    2. 读取 daily_returns_cleaned.jsonl 统计 quality=="real" 的记录数
    3. 双重门槛判定:
         GATE-A: days_completed >= required_days (默认 14)
         GATE-B: real_records >= required_days (防止天数达标但数据断档)
    4. 未达标: 记录监控日志, 输出剩余天数/预计达标日期/断档告警, 不触发漂移判定
    5. 达标: 调用 run_integration() 触发漂移判定 (幂等, 同日重复安全)
    6. 持久化到 observation_watchdog.jsonl

设计原则:
    - HC-1: 不切任何 Feature Flag
    - HC-4: 只读监控 + 条件触发, 不修改 V9 基线
    - 双重门槛: 天数 + 真实数据量同时达标才触发 (防误触发)
    - 断档检测: 连续 2 个交易日无新数据则告警
    - fail-safe: 任何异常都不阻断, 仅记录 error 状态

定时任务集成:
    建议接入 v84_EvolutionEval (16:05) 任务, 在 EvolutionEval 之前运行:
      16:04  observation_watchdog.py   (本脚本, 达标则触发漂移判定)
      16:05  run_evolution_eval.py     (读取漂移告警做决策)

用法:
    # 标准运行 (每日盘后)
    python scripts/observation_watchdog.py

    # 试运行 (不触发漂移判定, 仅打印状态)
    python scripts/observation_watchdog.py --dry-run

    # 自定义达标天数 (测试用)
    python scripts/observation_watchdog.py --required-days 5

    # 强制触发 (跳过门槛检查, 用于手动重跑漂移判定)
    python scripts/observation_watchdog.py --force-trigger

输出文件:
    - reports/evolution/observation_watchdog.jsonl  (每次运行的监控记录)
    - 达标时额外写入: drift_alerts.jsonl / observation_progress.json (由 run_integration 产出)
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from utils.datetime_utils import now_bj

# 路径处理 (兼容直接运行 / -m 运行)
_DIR = Path(__file__).resolve().parent
_PROJ = _DIR.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

# Windows 控制台 UTF-8 输出 (幂等)
for _name in ("stdout", "stderr"):
    _stream = getattr(sys, _name, None)
    if _stream is not None and getattr(_stream, "encoding", "").lower() != "utf-8":
        _buffer = getattr(_stream, "buffer", None)
        if _buffer is not None:
            try:
                setattr(
                    sys,
                    _name,
                    io.TextIOWrapper(_buffer, encoding="utf-8", errors="replace"),
                )
            except (OSError, ValueError):
                pass

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================

EVOLUTION_DIR = _PROJ / "reports" / "evolution"
OBSERVATION_PROGRESS_FILE = EVOLUTION_DIR / "observation_progress.json"
CLEANED_FILE = _PROJ / "reports" / "shadow" / "daily_returns_cleaned.jsonl"
RAW_SHADOW_FILE = _PROJ / "reports" / "shadow" / "daily_returns.jsonl"
WATCHDOG_LOG_FILE = EVOLUTION_DIR / "observation_watchdog.jsonl"

# 默认观察期达标天数 (与 observation_tracker.OBSERVATION_DAYS / yaml settings.observation_days=21 一致)
DEFAULT_REQUIRED_DAYS = 21

# 断档告警阈值: 连续 N 个交易日无新数据则告警
DATA_STALL_THRESHOLD_DAYS = 2

# A 股已知节假日 (2026, 非周末). 未列出的法定假日可能被误判为交易日,
# 从而低估断档天数. 后续维护: 节假日临近时补充.
HOLIDAYS_2026: set[str] = {
    # 元旦
    "2026-01-01",
    # 春节 (2026-02-16 ~ 2026-02-22, 此处仅列工作日)
    "2026-02-16",
    "2026-02-17",
    "2026-02-18",
    "2026-02-19",
    "2026-02-20",
    # 清明
    "2026-04-06",
    # 劳动节
    "2026-05-01",
    # 端午
    "2026-06-19",
    # 中秋 (2026-09-25 调休)
    "2026-09-25",
    # 国庆 + 中秋连休
    "2026-10-01",
    "2026-10-02",
    "2026-10-05",
    "2026-10-06",
    "2026-10-07",
    "2026-10-08",
}


# ============================================================
# 工具函数
# ============================================================


def is_trading_day(d: date) -> bool:
    """判断是否为 A 股交易日 (排除周末和已知节假日).

    Args:
        d: 日期

    Returns:
        True 为交易日
    """
    if d.weekday() >= 5:  # 周六=5, 周日=6
        return False
    if d.isoformat() in HOLIDAYS_2026:
        return False
    return True


def count_trading_days_between(start: date, end: date) -> int:
    """计算两个日期之间的交易日数 (含 start, 不含 end).

    Args:
        start: 起始日期
        end: 结束日期

    Returns:
        交易日数
    """
    if start >= end:
        return 0
    count = 0
    current = start
    while current < end:
        if is_trading_day(current):
            count += 1
        current += timedelta(days=1)
    return count


def utc_now_iso() -> str:
    """返回 UTC 时间 ISO 字符串 (带 Z 后缀)."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


# ============================================================
# 状态读取
# ============================================================


def load_observation_progress() -> dict[str, Any]:
    """读取 observation_progress.json.

    Returns:
        进度快照字典, 文件不存在返回空字典
    """
    if not OBSERVATION_PROGRESS_FILE.exists():
        logger.warning(
            "observation_progress.json 不存在: %s", OBSERVATION_PROGRESS_FILE
        )
        return {}
    try:
        with open(OBSERVATION_PROGRESS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.exception("读取 observation_progress.json 失败: %s", exc)
        return {}


def load_cleaned_real_records() -> tuple[list[dict[str, Any]], dict[str, int]]:
    """加载清洗文件并统计真实记录.

    Returns:
        (real_records, quality_stats)
        - real_records: quality=="real" 且有 daily_return 值的记录 (按日期升序)
        - quality_stats: 各质量标签的计数
    """
    quality_stats: dict[str, int] = {
        "real": 0,
        "backtest": 0,
        "fixed": 0,
        "missing": 0,
        "unknown": 0,
    }
    real_records: list[dict[str, Any]] = []

    if not CLEANED_FILE.exists():
        logger.warning("清洗文件不存在: %s", CLEANED_FILE)
        return real_records, quality_stats

    try:
        with open(CLEANED_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                q = rec.get("quality", "unknown")
                quality_stats[q] = quality_stats.get(q, 0) + 1
                if q == "real" and rec.get("daily_return") is not None:
                    real_records.append(rec)
    except OSError as exc:
        logger.exception("读取清洗文件失败: %s", exc)
        return real_records, quality_stats

    real_records.sort(key=lambda x: x.get("date", ""))
    return real_records, quality_stats


# ============================================================
# 达标判定
# ============================================================


def check_gates(
    progress: dict[str, Any],
    real_records: list[dict[str, Any]],
    required_days: int,
) -> dict[str, Any]:
    """执行双重门槛判定.

    Args:
        progress: observation_progress.json 内容
        real_records: 真实数据记录列表
        required_days: 达标所需天数

    Returns:
        判定结果字典:
        - gate_a_passed: 天数门槛
        - gate_b_passed: 真实数据量门槛
        - all_passed: 双重门槛
        - days_completed: 已完成天数
        - real_count: 真实数据条数
        - days_remaining: 剩余天数
        - samples_remaining: 剩余样本数
    """
    obs = progress.get("observation", {})
    days_completed = int(obs.get("days_completed", 0))
    real_count = len(real_records)

    gate_a_passed = days_completed >= required_days
    gate_b_passed = real_count >= required_days
    all_passed = gate_a_passed and gate_b_passed

    return {
        "gate_a_passed": gate_a_passed,
        "gate_b_passed": gate_b_passed,
        "all_passed": all_passed,
        "days_completed": days_completed,
        "real_count": real_count,
        "required_days": required_days,
        "days_remaining": max(0, required_days - days_completed),
        "samples_remaining": max(0, required_days - real_count),
    }


def detect_data_stall(real_records: list[dict[str, Any]]) -> dict[str, Any]:
    """检测数据断档 (连续交易日无新数据).

    判定逻辑: 从最新记录日期到今天之间的交易日数, 超过阈值则视为断档.

    Args:
        real_records: 真实数据记录列表 (按日期升序)

    Returns:
        断档检测结果:
        - is_stalled: 是否断档
        - last_data_date: 最新数据日期
        - stalled_trading_days: 断档交易日数
        - threshold: 告警阈值
    """
    if not real_records:
        return {
            "is_stalled": True,
            "last_data_date": None,
            "stalled_trading_days": -1,  # -1 表示从未有数据
            "threshold": DATA_STALL_THRESHOLD_DAYS,
            "reason": "no_real_data_at_all",
        }

    last_date_str = real_records[-1].get("date", "")
    try:
        last_date = date.fromisoformat(last_date_str)
    except ValueError:
        return {
            "is_stalled": True,
            "last_data_date": last_date_str,
            "stalled_trading_days": -1,
            "threshold": DATA_STALL_THRESHOLD_DAYS,
            "reason": "invalid_date_format",
        }

    today = date.today()
    # 如果最新数据就是今天或更晚 (时区差), 不算断档
    if last_date >= today:
        return {
            "is_stalled": False,
            "last_data_date": last_date_str,
            "stalled_trading_days": 0,
            "threshold": DATA_STALL_THRESHOLD_DAYS,
            "reason": "up_to_date",
        }

    # 计算 last_date+1 到 today 之间的交易日数 (不含 last_date, 含今天若为交易日)
    stalled = count_trading_days_between(
        last_date + timedelta(days=1), today + timedelta(days=1)
    )
    return {
        "is_stalled": stalled >= DATA_STALL_THRESHOLD_DAYS,
        "last_data_date": last_date_str,
        "stalled_trading_days": stalled,
        "threshold": DATA_STALL_THRESHOLD_DAYS,
        "reason": "stalled" if stalled >= DATA_STALL_THRESHOLD_DAYS else "normal",
    }


def estimate_completion_date(
    days_completed: int,
    required_days: int,
    start_date_str: str,
) -> str:
    """估算达标日期 (基于交易日推算).

    Args:
        days_completed: 已完成天数
        required_days: 所需天数
        start_date_str: 观察期起始日期 (YYYY-MM-DD)

    Returns:
        预计达标日期 (YYYY-MM-DD), 无法估算返回 "unknown"
    """
    remaining = max(0, required_days - days_completed)
    if remaining == 0:
        return now_bj().strftime("%Y-%m-%d")

    try:
        date.fromisoformat(start_date_str)
    except (ValueError, TypeError):
        return "unknown"

    # 从今天起往前找, 直到攒够 remaining 个交易日
    today = date.today()
    future = today
    found = 0
    # 上限 60 天, 防止无限循环
    while found < remaining and future < today + timedelta(days=60):
        future += timedelta(days=1)
        if is_trading_day(future):
            found += 1
    return future.strftime("%Y-%m-%d") if found == remaining else "unknown"


# ============================================================
# 漂移判定触发 (复用 integrate_cleaned_to_drift)
# ============================================================


def trigger_drift_evaluation(force: bool) -> dict[str, Any]:
    """触发漂移判定 (复用 integrate_cleaned_to_drift.run_integration).

    Args:
        force: 强制触发 (跳过门槛检查, 用于 --force-trigger)

    Returns:
        触发结果摘要:
        - triggered: 是否真正触发了
        - alert_status: 告警状态
        - alert_severity: 告警严重等级
        - alert_psi: PSI 值
        - alert_ks: KS 值
        - error: 错误信息 (失败时)
    """
    try:
        from scripts.integrate_cleaned_to_drift import (
            DEFAULT_CLEANED_INPUT,
            run_integration,
        )
    except ImportError as exc:
        logger.exception("导入 run_integration 失败: %s", exc)
        return {"triggered": False, "error": f"import_error: {exc}"}

    try:
        alert, quality_stats, obs_snapshot = run_integration(
            cleaned_input=DEFAULT_CLEANED_INPUT,
            dry_run=False,
            force=force,
        )
        return {
            "triggered": True,
            "alert_status": alert.get("status"),
            "alert_severity": alert.get("severity"),
            "alert_psi": alert.get("psi", 0),
            "alert_ks": alert.get("drift_score", 0),
            "quality_stats": quality_stats,
            "obs_ready_for_phase_b": obs_snapshot.get("observation", {}).get(
                "ready_for_phase_b", False
            ),
        }
    except (
        FileNotFoundError,
        ValueError,
        TypeError,
        KeyError,
        RuntimeError,
        OSError,
    ) as exc:
        logger.exception("漂移判定触发失败: %s", exc)
        return {"triggered": False, "error": str(exc)}


# ============================================================
# 监控日志持久化
# ============================================================


def write_watchdog_log(log_entry: dict[str, Any]) -> None:
    """追加监控日志到 observation_watchdog.jsonl.

    Args:
        log_entry: 监控日志条目
    """
    WATCHDOG_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(WATCHDOG_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:
        logger.warning("监控日志写入失败: %s", exc)


# ============================================================
# 人类可读摘要
# ============================================================


def render_progress_bar(current: int, total: int, width: int = 20) -> str:
    """渲染进度条.

    Args:
        current: 当前进度
        total: 总量
        width: 进度条宽度

    Returns:
        形如 [######--------------] 6/14 的字符串
    """
    filled = min(int(current / total * width), width) if total else 0
    bar = "#" * filled + "-" * (width - filled)
    return f"[{bar}] {current}/{total}"


def print_summary(
    gates: dict[str, Any],
    stall: dict[str, Any],
    quality_stats: dict[str, int],
    trigger_result: dict[str, Any],
    est_completion: str,
    dry_run: bool,
    force_trigger: bool,
) -> None:
    """打印人类可读摘要.

    Args:
        gates: 门槛判定结果
        stall: 断档检测结果
        quality_stats: 数据质量统计
        trigger_result: 漂移判定触发结果
        est_completion: 预计达标日期
        dry_run: 是否试运行
        force_trigger: 是否强制触发
    """
    print("\n" + "=" * 60)
    print(f"[观察期达标看门狗] {now_bj().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    mode = (
        "DRY-RUN" if dry_run else ("FORCE-TRIGGER" if force_trigger else "PRODUCTION")
    )
    print(f"模式: {mode}")
    print()

    print("─" * 60)
    print("一、观察期进度")
    print("─" * 60)
    days_done = gates["days_completed"]
    days_req = gates["required_days"]
    real_count = gates["real_count"]
    print(f"  观察期进度: {render_progress_bar(days_done, days_req)}")
    print(f"  真实数据量: {render_progress_bar(real_count, days_req)}")
    print(f"  剩余天数:   {gates['days_remaining']}")
    print(f"  剩余样本:   {gates['samples_remaining']}")
    print(f"  预计达标:   {est_completion}")
    print()

    print("─" * 60)
    print("二、门槛判定")
    print("─" * 60)
    gate_a_icon = "✓ PASS" if gates["gate_a_passed"] else "✗ FAIL"
    gate_b_icon = "✓ PASS" if gates["gate_b_passed"] else "✗ FAIL"
    all_icon = "✓ PASS" if gates["all_passed"] else "✗ FAIL"
    print(f"  GATE-A (天数 >= {days_req}):     {gate_a_icon}  ({days_done} 天)")
    print(f"  GATE-B (真实数据 >= {days_req}): {gate_b_icon}  ({real_count} 条)")
    print(f"  双重门槛:                {all_icon}")
    print()

    print("─" * 60)
    print("三、数据质量分布")
    print("─" * 60)
    total = sum(quality_stats.values())
    for label in ["real", "backtest", "fixed", "missing", "unknown"]:
        cnt = quality_stats.get(label, 0)
        pct = (cnt / total * 100) if total else 0
        marker = (
            "✓"
            if label == "real"
            else ("✗" if label in ("backtest", "missing") else "·")
        )
        print(f"  {marker} {label:<10} {cnt:>4} ({pct:5.1f}%)")
    print()

    print("─" * 60)
    print("四、断档检测")
    print("─" * 60)
    if stall["is_stalled"]:
        last = stall.get("last_data_date") or "N/A"
        stalled_days = stall.get("stalled_trading_days", -1)
        print(
            f"  ⚠️  断档告警: {stalled_days} 个交易日无新数据 (阈值 {stall['threshold']})"
        )
        print(f"  最新数据日期: {last}")
        print(f"  原因: {stall.get('reason', 'unknown')}")
        print("  建议: 检查 v84_PostMarket 是否正常运行, 数据源是否可用")
    else:
        last = stall.get("last_data_date", "N/A")
        print(
            f"  ✓ 数据正常: 最新日期 {last}, 断档天数 {stall.get('stalled_trading_days', 0)}"
        )
    print()

    print("─" * 60)
    print("五、漂移判定触发")
    print("─" * 60)
    if force_trigger:
        print("  ⚡ 强制触发模式: 跳过门槛检查")
    if dry_run:
        if gates["all_passed"] or force_trigger:
            print("  (试运行) 本应触发漂移判定, 但 dry-run 模式未执行")
        else:
            print(f"  未达标, 未触发漂移判定 (剩余 {gates['days_remaining']} 天)")
    else:
        if trigger_result.get("triggered"):
            status = trigger_result.get("alert_status", "unknown")
            severity = trigger_result.get("alert_severity", "unknown")
            psi = trigger_result.get("alert_psi", 0)
            ks = trigger_result.get("alert_ks", 0)
            severity_emoji = {
                "low": "🟢",
                "medium": "🟡",
                "high": "🟠",
                "critical": "🔴",
            }.get(severity, "❓")
            print("  ✓ 已触发漂移判定")
            print(f"    告警状态: {status}")
            print(f"    {severity_emoji} 严重等级: {severity}")
            print(f"    PSI: {psi:.4f}")
            print(f"    KS:  {ks:.4f}")
            print(
                f"    阶段 B 就绪: {trigger_result.get('obs_ready_for_phase_b', False)}"
            )
        elif trigger_result.get("error"):
            print(f"  ✗ 触发失败: {trigger_result['error']}")
        else:
            print("  ⏸  未触发漂移判定 (门槛未达标)")
            print(
                f"     原因: 还需 {gates['days_remaining']} 天 + {gates['samples_remaining']} 条样本"
            )
    print()

    print("─" * 60)
    print("六、输出文件")
    print("─" * 60)
    if dry_run:
        print("  (试运行, 无文件写入)")
    else:
        print(f"  ✓ {WATCHDOG_LOG_FILE.name}  (看门狗监控日志)")
        if trigger_result.get("triggered"):
            print("  ✓ drift_alerts.jsonl       (漂移告警, 由 run_integration 产出)")
            print("  ✓ observation_progress.json (观察期进度, 由 run_integration 刷新)")
    print("=" * 60 + "\n")


# ============================================================
# 主流程
# ============================================================


def run_watchdog(
    required_days: int,
    dry_run: bool,
    force_trigger: bool,
) -> dict[str, Any]:
    """执行看门狗主流程.

    Args:
        required_days: 达标所需天数
        dry_run: 试运行 (不触发漂移判定)
        force_trigger: 强制触发 (跳过门槛检查)

    Returns:
        运行结果摘要字典
    """
    timestamp = utc_now_iso()

    # 1. 读取观察期进度
    progress = load_observation_progress()
    if not progress:
        logger.warning("observation_progress.json 为空或读取失败, 尝试刷新")
        # 兜底: 调用 observation_tracker 刷新一次
        try:
            from scripts.observation_tracker import generate_snapshot

            progress = generate_snapshot()
        except (ImportError, RuntimeError, OSError, ValueError) as exc:
            logger.exception("observation_tracker 刷新失败: %s", exc)
            progress = {}

    # 2. 加载清洗数据
    real_records, quality_stats = load_cleaned_real_records()

    # 3. 门槛判定
    gates = check_gates(progress, real_records, required_days)

    # 4. 断档检测
    stall = detect_data_stall(real_records)

    # 5. 估算达标日期
    obs = progress.get("observation", {})
    start_date_str = obs.get("start_date", "")
    est_completion = estimate_completion_date(
        gates["days_completed"],
        required_days,
        start_date_str,
    )

    # 6. 决定是否触发漂移判定
    should_trigger = (gates["all_passed"] or force_trigger) and not dry_run
    trigger_result: dict[str, Any] = {"triggered": False}

    if should_trigger:
        logger.info(
            "门槛达标 (days=%d, real=%d), 触发漂移判定",
            gates["days_completed"],
            gates["real_count"],
        )
        trigger_result = trigger_drift_evaluation(force=force_trigger)
    else:
        if dry_run and (gates["all_passed"] or force_trigger):
            logger.info("试运行模式: 本应触发漂移判定, 但 dry-run 未执行")
        else:
            logger.info(
                "门槛未达标 (days=%d/%d, real=%d/%d), 跳过漂移判定",
                gates["days_completed"],
                required_days,
                gates["real_count"],
                required_days,
            )

    # 7. 持久化监控日志
    log_entry = {
        "timestamp": timestamp,
        "dry_run": dry_run,
        "force_trigger": force_trigger,
        "required_days": required_days,
        "gate_a_passed": gates["gate_a_passed"],
        "gate_b_passed": gates["gate_b_passed"],
        "all_passed": gates["all_passed"],
        "days_completed": gates["days_completed"],
        "real_count": gates["real_count"],
        "days_remaining": gates["days_remaining"],
        "samples_remaining": gates["samples_remaining"],
        "estimated_completion": est_completion,
        "data_stall": stall,
        "quality_stats": quality_stats,
        "triggered": trigger_result.get("triggered", False),
        "trigger_result": trigger_result,
    }
    if not dry_run:
        write_watchdog_log(log_entry)

    return {
        "gates": gates,
        "stall": stall,
        "quality_stats": quality_stats,
        "trigger_result": trigger_result,
        "est_completion": est_completion,
        "log_entry": log_entry,
    }


def parse_args() -> argparse.Namespace:
    """解析命令行参数.

    Returns:
        参数对象
    """
    parser = argparse.ArgumentParser(
        description="观察期达标看门狗 — 监控天数, 达标后触发漂移判定",
    )
    parser.add_argument(
        "--required-days",
        type=int,
        default=DEFAULT_REQUIRED_DAYS,
        help=f"达标所需天数 (默认: {DEFAULT_REQUIRED_DAYS})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="试运行: 仅打印状态, 不触发漂移判定, 不写监控日志",
    )
    parser.add_argument(
        "--force-trigger",
        action="store_true",
        help="强制触发漂移判定 (跳过门槛检查, 用于手动重跑)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="启用 DEBUG 级别日志",
    )
    return parser.parse_args()


def main() -> None:
    """命令行入口."""
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        result = run_watchdog(
            required_days=args.required_days,
            dry_run=args.dry_run,
            force_trigger=args.force_trigger,
        )
    except (ValueError, TypeError, KeyError, RuntimeError, OSError) as exc:
        logger.exception("看门狗运行失败: %s", exc)
        sys.exit(1)

    print_summary(
        gates=result["gates"],
        stall=result["stall"],
        quality_stats=result["quality_stats"],
        trigger_result=result["trigger_result"],
        est_completion=result["est_completion"],
        dry_run=args.dry_run,
        force_trigger=args.force_trigger,
    )

    # 退出码: 0 = 正常 (无论是否触发漂移判定)
    #         1 = 漂移判定触发失败 (已记录日志, 但需人工排查)
    if not args.dry_run and result["trigger_result"].get("error"):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
