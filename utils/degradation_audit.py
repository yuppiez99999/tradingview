"""统一降级审计 (P1-2, 2026-09-01) — 配置缺失静默降级闭环

背景 ("跑不通"诊断第 5 项): 系统大量 "配置/数据缺失 → log warning → 默认值继续执行"
的静默降级 (fail-safe 设计本身合理), 但降级事件无统一记录、无事后可见性 —
实锤: config/trade_execution.yaml 不存在, daily_trade_executor 全部风控参数
(止损熔断线/回撤熔断线/单日限额) 长期走硬编码默认值, 仅一条无人看的日志。

本模块补齐 "降级可观测" 这一环 (与止损水位线持久化 reports/stop_loss_water_marks.json
同模式):

    配置缺失 → 默认值兜底 (行为不变, fail-safe 保留)
            → record_degradation() 追加写 reports/degradation_log.jsonl
            → 关键路径可查 pending_degradations() 决定是否继续/告警

与 strict 硬失败 (config_manager.get(strict=True)) 互补:
- 审计 = 默认, 记录一切降级, 不阻断 (生产兼容)
- strict = 关键配置显式要求硬失败 (调用方决定)

使用:
    from utils.degradation_audit import record_degradation, pending_degradations

    cfg = get_config("trade_execution") or {}
    if not cfg:
        record_degradation(
            scope="daily_trade_executor",
            key="trade_execution.yaml",
            default="(硬编码风控默认值)",
            reason="配置文件不存在",
        )
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from utils.datetime_utils import now_bj

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_FILE = _PROJECT_ROOT / "reports" / "degradation_log.jsonl"

_lock = threading.Lock()
# 同一 (scope, key) 每进程只记一次 — 避免 import 频繁模块导致的重复刷屏
_recorded: set[tuple[str, str]] = set()


def record_degradation(
    scope: str,
    key: str,
    default: str = "",
    reason: str = "",
    *,
    dedupe: bool = True,
) -> bool:
    """记录一次降级事件 (append-only jsonl)

    Args:
        scope: 降级发生的模块/子系统 (如 "daily_trade_executor")
        key: 缺失的配置/数据名 (如 "trade_execution.yaml")
        default: 兜底使用的默认值描述
        reason: 降级原因 (如 "配置文件不存在")
        dedupe: 同 (scope,key) 每进程去重 (默认 True)

    Returns:
        是否实际写入 (False = 去重跳过或写失败静默)
    """
    if dedupe and (scope, key) in _recorded:
        return False
    event = {
        "ts": now_bj().isoformat(timespec="seconds"),
        "scope": scope,
        "key": key,
        "default": default,
        "reason": reason,
    }
    try:
        with _lock:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
            _recorded.add((scope, key))
        return True
    except OSError:
        # 审计自身 fail-safe: 记不住不能阻断业务
        return False


def pending_degradations() -> list[dict]:
    """读取降级日志 (诊断/报告用; 文件不存在返回空)"""
    try:
        with open(LOG_FILE, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except (OSError, json.JSONDecodeError):
        return []


def reset_dedupe() -> None:
    """清除进程内去重标记 (测试用)"""
    with _lock:
        _recorded.clear()


# ============================================================
# 项目外输出泄漏检测 (2026-09-01, 继 reports 目录泄漏事件后的防护)
# 背景: utils/hedge_rebalance_backtest.save_report 路径多拼一层 "..",
# 导致报告长期写到项目外 E:\各种PY程序\reports\ (项目内均有更新版本)。
# 该 bug 类难以在测试中暴露 (沙箱拦截 ≠ 代码报错), 故加启动时运行时检测。
# ============================================================

# 启动时在项目根父目录检测的输出类目录名 (与项目内 reports/ 同名即高度可疑)
_STRAY_DIR_NAMES = ("reports", "output", "data")


def check_stray_output_dirs(recent_days: int = 7) -> list[str]:
    """检测项目根父目录下同名输出目录中是否有近期写入的文件

    扫描 PROJECT_ROOT.parent 下名为 reports/output/data 的兄弟目录,
    若含 mtime 在 recent_days 内的文件 → 该目录疑似项目外泄漏,
    记录降级审计 + 返回路径列表 (调用方可决定是否告警/阻断)。

    设计要点:
    - 只读不写, 无权限目录 (PermissionError) 静默跳过 — 检测自身 fail-safe
    - 只报"近期有写入"的目录: 历史遗留且不再增长的不刷屏
    - 每进程每目录去重 (record_degradation dedupe=True)

    Returns:
        疑似泄漏目录的绝对路径列表 (无则空)
    """
    import time as _time

    from utils.logging_manager import get_logger

    logger = get_logger("degradation_audit")

    parent = _PROJECT_ROOT.parent
    cutoff = _time.time() - recent_days * 86400
    strays: list[str] = []

    for name in _STRAY_DIR_NAMES:
        candidate = parent / name
        if not candidate.is_dir():
            continue
        has_recent = False
        try:
            for f in candidate.rglob("*"):
                try:
                    if f.is_file() and f.stat().st_mtime >= cutoff:
                        has_recent = True
                        break
                except OSError:
                    continue
        except OSError:
            continue
        if has_recent:
            strays.append(str(candidate))
            record_degradation(
                scope="degradation_audit",
                key=str(candidate),
                default="(项目外输出目录)",
                reason=f"项目根父目录存在同名输出目录且 {recent_days} 天内有文件写入 — "
                f"疑似代码路径泄漏到项目外 (如 save_report 多拼 '..'), 请排查",
            )
            logger.warning(
                "[项目外泄漏检测] %s 存在近期写入文件 — 疑似输出路径泄漏到项目外, "
                "已记录 reports/degradation_log.jsonl",
                candidate,
            )
    return strays
