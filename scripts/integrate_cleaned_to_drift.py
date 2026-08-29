"""Shadow 清洗数据 → 因子漂移告警集成器 (W1.3c Day 5)

将 daily_returns_cleaned.jsonl 中的真实市场数据自动写入 drift_alerts.jsonl,
并刷新 observation_progress.json 的最新观察期状态.

背景:
    历史 drift_alerts.jsonl 中的告警基于"原始 daily_returns.jsonl"计算, 其中
    混有回测回填 / 零收益占位 / 修复值, 导致 2026-08-02 的告警 PSI=4.37 (critical)
    是虚假漂移信号. 本脚本改用清洗后的真实数据, 确保告警反映真实分布变化.

工作流:
    1. 读取 daily_returns_cleaned.jsonl (由 clean_shadow_returns.py 产出)
       若不存在, 自动触发一次清洗
    2. 过滤 quality=="real" 的记录 (剔除 backtest/fixed/missing/unknown)
    3. 按时间切分基线(前 60%) / 当前(后 40%), 调用 compute_prediction_drift
    4. 幂等写入 drift_alerts.jsonl: 同一天重复运行替换当日告警, 不追加
    5. 调用 generate_snapshot() 刷新 observation_progress.json
    6. 输出人类可读摘要

设计原则:
    - HC-1: 不切任何 Feature Flag
    - HC-4: 只读评估 + 追加告警, 不修改 V9 基线 / positions.json
    - 数据质量透明: 告警条目内嵌 data_quality 字段, 标注 real/backtest/fixed/missing 计数
    - 幂等: 同日重复运行不堆积告警
    - fail-safe: 数据不足时降级为 skipped 状态, 不抛异常

用法:
    # 标准运行 (读清洗文件 → 写告警 → 更新进度)
    python scripts/integrate_cleaned_to_drift.py

    # 试运行 (仅打印, 不写盘)
    python scripts/integrate_cleaned_to_drift.py --dry-run

    # 强制重跑 (即使当日已有告警也覆盖)
    python scripts/integrate_cleaned_to_drift.py --force

    # 指定清洗文件路径
    python scripts/integrate_cleaned_to_drift.py --input reports/shadow/daily_returns_cleaned.jsonl

输出文件:
    - reports/evolution/drift_alerts.jsonl          (追加/替换当日告警)
    - reports/evolution/observation_progress.json   (刷新快照)
    - reports/evolution/integration_log.jsonl       (集成运行日志)
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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

DEFAULT_CLEANED_INPUT = _PROJ / "reports" / "shadow" / "daily_returns_cleaned.jsonl"
RAW_SHADOW_INPUT = _PROJ / "reports" / "shadow" / "daily_returns.jsonl"
EVOLUTION_DIR = _PROJ / "reports" / "evolution"
DRIFT_ALERTS_FILE = EVOLUTION_DIR / "drift_alerts.jsonl"
OBSERVATION_PROGRESS_FILE = EVOLUTION_DIR / "observation_progress.json"
INTEGRATION_LOG_FILE = EVOLUTION_DIR / "integration_log.jsonl"

# 漂移检测的最小真实样本数 (基线 + 当前合计)
MIN_REAL_SAMPLES_FOR_DRIFT = 5

# 基线/当前切分比例 (与 daily_evolution_check.py 保持一致)
BASELINE_RATIO = 0.6

# 模型标识 (与现有告警保持一致)
MODEL_NAME = "v9_lgb"
MODEL_VERSION = "observation_period"


# ============================================================
# 数据加载
# ============================================================


def load_cleaned_records(file_path: Path) -> list[dict[str, Any]]:
    """加载清洗后的 jsonl 文件.

    Args:
        file_path: daily_returns_cleaned.jsonl 路径

    Returns:
        记录列表 (按文件顺序)

    Raises:
        FileNotFoundError: 文件不存在
    """
    if not file_path.exists():
        raise FileNotFoundError(f"清洗文件不存在: {file_path}")

    records: list[dict[str, Any]] = []
    with open(file_path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                logger.warning("第 %d 行 JSON 解析失败, 跳过: %s", line_no, exc)

    logger.info("加载 %d 条清洗记录 from %s", len(records), file_path.name)
    return records


def filter_real_records(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """过滤出 quality=="real" 的记录, 并统计质量分布.

    Args:
        records: 清洗后的完整记录列表

    Returns:
        (real_records, quality_stats)
        - real_records: 仅含 quality=="real" 的记录, 按日期升序
        - quality_stats: {real, backtest, fixed, missing, unknown: 计数}
    """
    quality_stats: dict[str, int] = {
        "real": 0,
        "backtest": 0,
        "fixed": 0,
        "missing": 0,
        "unknown": 0,
    }
    real_records: list[dict[str, Any]] = []

    for r in records:
        q = r.get("quality", "unknown")
        quality_stats[q] = quality_stats.get(q, 0) + 1
        if q == "real":
            # 仅保留有收益值的真实记录 (剔除 None)
            if r.get("daily_return") is not None:
                real_records.append(r)

    # 按日期升序排序 (确保切分基线/当前时序正确)
    real_records.sort(key=lambda x: x.get("date", ""))

    return real_records, quality_stats


def compute_file_hash(file_path: Path) -> str:
    """计算文件 SHA256 (前 16 位), 用于告警条目溯源.

    Args:
        file_path: 文件路径

    Returns:
        16 位 hex 字符串, 文件不存在返回 "unknown"
    """
    try:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except OSError:
        return "unknown"


# ============================================================
# 漂移检测 (复用 utils.alpha.drift_monitor)
# ============================================================


def run_drift_detection(real_records: list[dict[str, Any]]) -> dict[str, Any]:
    """对真实数据运行漂移检测.

    切分策略: 前 60% 为基线, 后 40% 为当前 (与 daily_evolution_check.py 一致).
    样本数不足时返回 skipped 状态.

    Args:
        real_records: 真实市场数据记录列表 (已按日期升序)

    Returns:
        告警条目字典 (可序列化为 jsonl 一行)
    """
    n = len(real_records)
    timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    base_entry: dict[str, Any] = {
        "timestamp": timestamp,
        "model_name": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "feature_name": "__prediction__",
        "data_source": "shadow_daily_returns_cleaned",
        "severity": "low",
        "drift_score": 0.0,
        "psi": 0.0,
        "baseline_mean": 0.0,
        "current_mean": 0.0,
        "baseline_size": 0,
        "current_size": 0,
        "owner": "",
        "runbook_url": "docs/runbooks/MODEL_DRIFT_RUNBOOK.md",
        "n_baseline": 0,
        "n_current": 0,
    }

    if n < MIN_REAL_SAMPLES_FOR_DRIFT:
        base_entry["status"] = "skipped"
        base_entry["reason"] = (
            f"insufficient_real_data ({n} < {MIN_REAL_SAMPLES_FOR_DRIFT})"
        )
        logger.warning(
            "真实数据不足 (%d < %d), 跳过漂移检测", n, MIN_REAL_SAMPLES_FOR_DRIFT
        )
        return base_entry

    try:
        import numpy as np

        from utils.alpha.drift_monitor import compute_prediction_drift
    except ImportError as exc:
        base_entry["status"] = "import_error"
        base_entry["reason"] = str(exc)
        logger.exception("导入 drift_monitor 失败: %s", exc)
        return base_entry

    try:
        # 切分基线/当前
        split_idx = max(2, int(n * BASELINE_RATIO))
        if split_idx >= n:
            # 样本太少无法切分, 至少给当前留 1 条
            split_idx = n - 1

        baseline_returns = np.array(
            [float(r["daily_return"]) for r in real_records[:split_idx]],
            dtype=float,
        )
        current_returns = np.array(
            [float(r["daily_return"]) for r in real_records[split_idx:]],
            dtype=float,
        )

        logger.info(
            "漂移检测: baseline=%d 条 (%s~%s), current=%d 条 (%s~%s)",
            len(baseline_returns),
            real_records[0].get("date", "?"),
            real_records[split_idx - 1].get("date", "?"),
            len(current_returns),
            real_records[split_idx].get("date", "?"),
            real_records[-1].get("date", "?"),
        )

        drift_report = compute_prediction_drift(
            baseline=baseline_returns,
            current=current_returns,
            model_name=MODEL_NAME,
            model_version=MODEL_VERSION,
        )

        alert_entry = drift_report.to_dict()
        # 补充非 DriftReport 标准字段
        alert_entry["timestamp"] = timestamp  # 用本脚本时间戳保证幂等
        alert_entry["data_source"] = "shadow_daily_returns_cleaned"
        alert_entry["n_baseline"] = int(len(baseline_returns))
        alert_entry["n_current"] = int(len(current_returns))
        alert_entry["baseline_dates"] = [
            r.get("date", "") for r in real_records[:split_idx]
        ]
        alert_entry["current_dates"] = [
            r.get("date", "") for r in real_records[split_idx:]
        ]
        alert_entry["status"] = "completed"

        logger.info(
            "漂移检测完成: severity=%s, KS=%.4f, PSI=%.4f, "
            "baseline_mean=%.6f (n=%d), current_mean=%.6f (n=%d)",
            drift_report.severity.value,
            drift_report.drift_score,
            drift_report.psi,
            drift_report.baseline_mean,
            len(baseline_returns),
            drift_report.current_mean,
            len(current_returns),
        )
        return alert_entry

    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
    ) as exc:
        base_entry["status"] = "error"
        base_entry["reason"] = str(exc)
        logger.exception("漂移检测异常: %s", exc)
        return base_entry


# ============================================================
# 告警持久化 (幂等: 同日替换)
# ============================================================


def upsert_alert(alert_entry: dict[str, Any], output_file: Path, force: bool) -> bool:
    """幂等写入告警: 同一天已有告警则替换, 否则追加.

    判定依据: 告警 timestamp 的 UTC 日期部分 (YYYY-MM-DD).

    Args:
        alert_entry: 告警条目
        output_file: drift_alerts.jsonl 路径
        force: True 时即使当日已有告警也替换 (默认行为, 与幂等一致)

    Returns:
        True=写入成功, False=写入失败
    """
    output_file.parent.mkdir(parents=True, exist_ok=True)

    today_str = datetime.now(UTC).strftime("%Y-%m-%d")
    # 也匹配 YYYY-MM-DD 前缀 (从 timestamp 提取)
    today_local = datetime.now().strftime("%Y-%m-%d")

    # 读取现有告警
    existing_lines: list[str] = []
    replaced_count = 0
    if output_file.exists():
        with open(output_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    rec_date = str(rec.get("timestamp", ""))[:10]
                    # 当日告警: 替换 (UTC 或本地时区都算)
                    if rec_date == today_str or rec_date == today_local:
                        replaced_count += 1
                        continue  # 跳过旧告警
                    existing_lines.append(line)
                except json.JSONDecodeError:
                    existing_lines.append(line)  # 保留无法解析的行

    # 追加新告警
    new_line = json.dumps(alert_entry, ensure_ascii=False, default=str)
    all_lines = existing_lines + [new_line]

    try:
        with open(output_file, "w", encoding="utf-8") as f:
            for line in all_lines:
                f.write(line + "\n")
        action = "replaced" if replaced_count > 0 else "appended"
        logger.info(
            "告警已 %s -> %s (替换 %d 条当日旧告警, 当前共 %d 条)",
            action,
            output_file.name,
            replaced_count,
            len(all_lines),
        )
        return True
    except OSError as exc:
        logger.exception("告警写入失败: %s", exc)
        return False


def write_integration_log(
    log_entry: dict[str, Any],
    log_file: Path = INTEGRATION_LOG_FILE,
) -> None:
    """写集成运行日志 (追加, 供审计).

    Args:
        log_entry: 运行日志条目
        log_file: integration_log.jsonl 路径
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:
        logger.warning("集成日志写入失败: %s", exc)


# ============================================================
# 观察期进度刷新
# ============================================================


def refresh_observation_progress() -> dict[str, Any]:
    """刷新 observation_progress.json (复用 observation_tracker).

    Returns:
        快照字典, 失败时返回 {"error": ...}
    """
    try:
        from scripts.observation_tracker import generate_snapshot

        snapshot = generate_snapshot()

        with open(OBSERVATION_PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2, default=str)

        obs = snapshot.get("observation", {})
        logger.info(
            "观察期进度已刷新: %d/%d 天 (%.1f%%), 样本 %d/%d, ready=%s",
            obs.get("days_completed", 0),
            obs.get("required_days", 0),
            obs.get("progress_pct", 0),
            obs.get("samples_collected", 0),
            obs.get("min_samples", 0),
            obs.get("ready_for_phase_b", False),
        )
        return snapshot

    except (
        ImportError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
    ) as exc:
        logger.exception("观察期进度刷新失败: %s", exc)
        return {"error": str(exc)}


# ============================================================
# 自动触发清洗 (若清洗文件不存在)
# ============================================================


def ensure_cleaned_file(cleaned_path: Path) -> bool:
    """确保清洗文件存在, 不存在则自动触发一次清洗.

    Args:
        cleaned_path: 清洗文件路径

    Returns:
        True=文件就绪, False=无法生成
    """
    if cleaned_path.exists():
        return True

    logger.info("清洗文件不存在, 自动触发清洗: %s", cleaned_path)
    try:
        from scripts.clean_shadow_returns import run_cleaning

        run_cleaning(
            input_file=RAW_SHADOW_INPUT,
            output_dir=cleaned_path.parent,
        )
        return cleaned_path.exists()
    except (ImportError, FileNotFoundError, OSError, ValueError) as exc:
        logger.exception("自动清洗失败: %s", exc)
        return False


# ============================================================
# 人类可读摘要
# ============================================================


def print_summary(
    alert: dict[str, Any],
    quality_stats: dict[str, int],
    obs_snapshot: dict[str, Any],
    cleaned_file: Path,
    dry_run: bool,
) -> None:
    """打印人类可读摘要.

    Args:
        alert: 告警条目
        quality_stats: 数据质量统计
        obs_snapshot: 观察期快照
        cleaned_file: 清洗文件路径
        dry_run: 是否试运行
    """
    print("\n" + "=" * 60)
    print(f"[清洗数据→漂移告警集成] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    mode_str = "DRY-RUN (未写盘)" if dry_run else "PRODUCTION (已写盘)"
    print(f"模式: {mode_str}")
    print(f"清洗文件: {cleaned_file}")
    print()

    print("─" * 60)
    print("一、数据质量分布")
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
    print(f"  {'合计':<12} {total:>4}")
    print()

    print("─" * 60)
    print("二、漂移检测结果")
    print("─" * 60)
    status = alert.get("status", "unknown")
    if status == "skipped":
        print(f"  状态: SKIPPED — {alert.get('reason', '')}")
    elif status == "error":
        print(f"  状态: ERROR — {alert.get('reason', '')}")
    elif status == "import_error":
        print(f"  状态: IMPORT_ERROR — {alert.get('reason', '')}")
    else:
        severity = alert.get("severity", "unknown")
        severity_emoji = {
            "low": "🟢",
            "medium": "🟡",
            "high": "🟠",
            "critical": "🔴",
        }.get(severity, "❓")
        print("  状态: COMPLETED")
        print(f"  {severity_emoji} 严重等级: {severity}")
        print(f"  KS Score: {alert.get('drift_score', 0):.4f}")
        print(f"  PSI:      {alert.get('psi', 0):.4f}")
        print(
            f"  基线均值: {alert.get('baseline_mean', 0):+.6f} (n={alert.get('n_baseline', 0)})"
        )
        print(
            f"  当前均值: {alert.get('current_mean', 0):+.6f} (n={alert.get('n_current', 0)})"
        )
        baseline_dates = alert.get("baseline_dates", [])
        current_dates = alert.get("current_dates", [])
        if baseline_dates:
            print(f"  基线日期: {baseline_dates[0]} ~ {baseline_dates[-1]}")
        if current_dates:
            print(f"  当前日期: {current_dates[0]} ~ {current_dates[-1]}")
    print()

    print("─" * 60)
    print("三、观察期进度")
    print("─" * 60)
    obs = obs_snapshot.get("observation", {})
    if "error" in obs_snapshot:
        print(f"  刷新失败: {obs_snapshot['error']}")
    else:
        days_done = obs.get("days_completed", 0)
        days_req = obs.get("required_days", 0)
        pct = obs.get("progress_pct", 0)
        samples = obs.get("samples_collected", 0)
        min_samples = obs.get("min_samples", 0)
        ready = obs.get("ready_for_phase_b", False)
        est = obs.get("estimated_completion", "?")
        print(f"  进度: {days_done}/{days_req} 天 ({pct:.1f}%)")
        print(f"  样本: {samples}/{min_samples}")
        print(f"  预计完成: {est}")
        print(f"  阶段 B 就绪: {'✓ YES' if ready else '✗ NO'}")
    print()

    print("─" * 60)
    print("四、输出文件")
    print("─" * 60)
    if dry_run:
        print("  (试运行, 无文件写入)")
    else:
        print(f"  ✓ {DRIFT_ALERTS_FILE.name}      (漂移告警)")
        print(f"  ✓ {OBSERVATION_PROGRESS_FILE.name} (观察期进度)")
        print(f"  ✓ {INTEGRATION_LOG_FILE.name}   (集成日志)")
    print("=" * 60 + "\n")


# ============================================================
# 主入口
# ============================================================


def run_integration(
    cleaned_input: Path,
    dry_run: bool,
    force: bool,
) -> tuple[dict[str, Any], dict[str, int], dict[str, Any]]:
    """执行完整集成流程.

    Args:
        cleaned_input: 清洗文件路径
        dry_run: 试运行 (不写盘)
        force: 强制覆盖当日告警

    Returns:
        (alert_entry, quality_stats, obs_snapshot)
    """
    # 1. 确保清洗文件存在
    if not ensure_cleaned_file(cleaned_input):
        raise FileNotFoundError(f"无法获取清洗文件: {cleaned_input}")

    # 2. 加载并过滤真实数据
    records = load_cleaned_records(cleaned_input)
    real_records, quality_stats = filter_real_records(records)
    logger.info(
        "真实数据过滤: %d 条 real (剔除 backtest=%d, fixed=%d, missing=%d, unknown=%d)",
        quality_stats["real"],
        quality_stats["backtest"],
        quality_stats["fixed"],
        quality_stats["missing"],
        quality_stats["unknown"],
    )

    # 3. 文件溯源 hash
    cleaned_hash = compute_file_hash(cleaned_input)

    # 4. 漂移检测
    alert = run_drift_detection(real_records)
    # 嵌入数据质量元信息
    alert["data_quality"] = {
        "real": quality_stats["real"],
        "backtest": quality_stats["backtest"],
        "fixed": quality_stats["fixed"],
        "missing": quality_stats["missing"],
        "unknown": quality_stats["unknown"],
        "excluded_non_real": sum(v for k, v in quality_stats.items() if k != "real"),
    }
    alert["cleaned_file_hash"] = cleaned_hash
    alert["cleaned_file_path"] = str(cleaned_input.relative_to(_PROJ))
    alert["integration_version"] = "w13c_day5_v1"

    # 5. 持久化告警
    if not dry_run:
        upsert_alert(alert, DRIFT_ALERTS_FILE, force=force)

    # 6. 刷新观察期进度
    if dry_run:
        # 试运行时也调用 (但不写盘)
        try:
            from scripts.observation_tracker import generate_snapshot

            obs_snapshot = generate_snapshot()
        except (ImportError, RuntimeError, OSError, ValueError) as exc:
            obs_snapshot = {"error": str(exc)}
    else:
        obs_snapshot = refresh_observation_progress()

    # 7. 写集成日志
    log_entry = {
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "dry_run": dry_run,
        "alert_status": alert.get("status"),
        "alert_severity": alert.get("severity"),
        "alert_psi": alert.get("psi", 0),
        "alert_ks": alert.get("drift_score", 0),
        "quality_stats": quality_stats,
        "cleaned_file_hash": cleaned_hash,
        "obs_ready_for_phase_b": obs_snapshot.get("observation", {}).get(
            "ready_for_phase_b", False
        ),
        "obs_days_completed": obs_snapshot.get("observation", {}).get(
            "days_completed", 0
        ),
    }
    if not dry_run:
        write_integration_log(log_entry)

    return alert, quality_stats, obs_snapshot


def parse_args() -> argparse.Namespace:
    """解析命令行参数.

    Returns:
        参数对象
    """
    parser = argparse.ArgumentParser(
        description="Shadow 清洗数据 → 因子漂移告警集成器 (W1.3c Day 5)",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_CLEANED_INPUT,
        help=f"清洗后 jsonl 文件 (默认: {DEFAULT_CLEANED_INPUT})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="试运行: 仅打印, 不写 drift_alerts / observation_progress / integration_log",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制覆盖当日告警 (默认即为覆盖, 显式参数便于脚本调用)",
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
        alert, quality_stats, obs_snapshot = run_integration(
            cleaned_input=args.input,
            dry_run=args.dry_run,
            force=args.force,
        )
    except FileNotFoundError as exc:
        logger.error("文件缺失: %s", exc)
        sys.exit(1)
    except (ValueError, TypeError, KeyError, RuntimeError, OSError) as exc:
        logger.exception("集成失败: %s", exc)
        sys.exit(1)

    print_summary(alert, quality_stats, obs_snapshot, args.input, args.dry_run)

    # 退出码: skipped/error 仍返回 0 (fail-safe, 不阻断定时任务链)
    # 仅在 import_error 或数据完全缺失时返回 1
    if alert.get("status") in ("import_error",):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
