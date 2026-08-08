"""检查点 P2: 缓存数据质量门禁 — DQC Phase 1 核心.

位置: 在因子计算前执行 (PipelineOrchestrator._compute_factors 之前)
职责: 检查 parquet 缓存数据的完整性/时效性/准确性/唯一性
阻断: ERROR 级别阻断因子计算 (Feature Flag USE_DQC_P2_GATE 控制)

接入方式:
    >>> from utils.dqc import run_p2_gate
    >>> passed, events = run_p2_gate(target_date, symbols)
    >>> if not passed:
    ...     # 阻断因子计算, 触发降级 (使用昨日因子 / 跳过当日)
    ...     logger.error("DQC P2 阻断, 跳过因子计算")

Feature Flag:
    USE_DQC_P2_GATE (默认 False, critical_path: false)
    - True: ERROR 阻断下游流程
    - False: 仅日志观察, 不阻断 (Phase 1 观察模式)
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from utils.dqc.event_types import DQCCheckpoint, DQCEvent, DQCLevel
from utils.dqc.metrics import (
    check_accuracy,
    check_completeness,
    check_timeliness,
    check_uniqueness,
)

logger = logging.getLogger("dqc.p2_gate")

# Feature Flag 名称
FLAG_USE_P2_GATE = "USE_DQC_P2_GATE"


class P2CacheQualityGate:
    """P2 检查点: 缓存数据质量门禁.

    执行顺序 (六维检查):
        1. 完整性 (C-01~C-06): 数据缺失即阻断
        2. 时效性 (T-01~T-03): 数据延迟告警
        3. 准确性 (A-01~A-06): 业务规则违反即阻断
        4. 唯一性 (U-01, U-03): 主键重复即阻断

    阻断规则:
        - 任何 ERROR/CRITICAL 级别事件 → 阻断
        - Feature Flag USE_DQC_P2_GATE=False 时 → 仅日志不阻断
    """

    BLOCKING_LEVELS = {DQCLevel.ERROR, DQCLevel.CRITICAL}

    def __init__(self) -> None:
        """初始化 P2 门禁."""
        self._checkpoint = DQCCheckpoint.P2_CACHE
        self._published: list[DQCEvent] = []

    def run(
        self,
        target_date: date,
        symbols: list[str],
        df: Optional[pd.DataFrame] = None,
        data_arrival_time: Optional[datetime] = None,
        cache_path: Optional[Path] = None,
    ) -> tuple[bool, list[DQCEvent]]:
        """执行 P2 检查.

        Args:
            target_date: 目标交易日
            symbols: 预期标的池
            df: 待检查的 DataFrame (None 时从 cache_path 加载)
            data_arrival_time: 数据实际到达时间 (None 则用 now)
            cache_path: 缓存文件路径 (df 为 None 时使用)

        Returns:
            tuple[是否通过, 事件列表]
            - 是否通过: True=可继续因子计算, False=应阻断
            - 事件列表: 所有 DQC 事件 (含 INFO/WARN/ERROR/CRITICAL)
        """
        self._published = []
        logger.info("DQC P2 开始检查: target_date=%s, symbols=%d", target_date, len(symbols))

        # 1. 加载数据 (若未提供)
        if df is None:
            df = self._load_cache(target_date, cache_path)
            if df is None:
                # 缓存文件不存在, 视为完整性 ERROR
                event = DQCEvent(
                    metric_id="C-02",
                    level=DQCLevel.ERROR,
                    checkpoint=self._checkpoint,
                    value=0.0,
                    threshold=1.0,
                    message=f"P2 检查失败: 缓存数据未加载 (cache_path={cache_path})",
                    context={"target_date": str(target_date), "cache_path": str(cache_path) if cache_path else None},
                )
                self._publish(event)
                return False, self._published

        # 2. 六维检查
        try:
            # 完整性 (C-01~C-06)
            events_c = check_completeness(df, target_date, symbols, self._checkpoint)
            # 时效性 (T-01~T-03)
            events_t = check_timeliness(df, target_date, self._checkpoint, data_arrival_time)
            # 准确性 (A-01~A-06)
            events_a = check_accuracy(df, self._checkpoint)
            # 唯一性 (U-01, U-03)
            events_u = check_uniqueness(df, self._checkpoint)

            all_events = events_c + events_t + events_a + events_u
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError) as e:
            # HC-DQC4: DQC 自身失败 fail-safe (不阻塞生产, 降级为日志)
            logger.exception("DQC P2 检查异常, 降级为通过 (fail-safe): %s", e)
            event = DQCEvent(
                metric_id="C-01",
                level=DQCLevel.WARN,
                checkpoint=self._checkpoint,
                value=0.0,
                threshold=1.0,
                message=f"DQC P2 自身异常, 降级为通过: {e}",
                context={"exception": str(e), "exception_type": type(e).__name__},
            )
            self._publish(event)
            return True, self._published

        # 3. 发布所有事件
        for e in all_events:
            self._publish(e)

        # 4. 判定是否阻断
        blocking = [e for e in all_events if e.level in self.BLOCKING_LEVELS]
        gate_enabled = self._is_gate_enabled()

        if blocking and not gate_enabled:
            # 观察模式: 仅日志不阻断
            logger.warning(
                "DQC P2 发现 %d 个阻断级问题, 但 Feature Flag %s=False, 观察模式不阻断",
                len(blocking), FLAG_USE_P2_GATE,
            )
            return True, self._published

        passed = len(blocking) == 0
        if not passed:
            self._log_block(target_date, blocking)

        logger.info(
            "DQC P2 完成: passed=%s, events=%d, blocking=%d",
            passed, len(all_events), len(blocking),
        )
        return passed, self._published

    # ============================================================
    # 内部方法
    # ============================================================
    def _load_cache(self, target_date: date, cache_path: Optional[Path]) -> Optional[pd.DataFrame]:
        """从缓存文件加载数据."""
        if cache_path is None or not cache_path.exists():
            return None
        try:
            return pd.read_parquet(cache_path)
        except (OSError, ValueError, RuntimeError) as e:
            logger.exception("加载缓存失败 %s: %s", cache_path, e)
            return None

    def _publish(self, event: DQCEvent) -> None:
        """发布 DQC 事件到 RiskBus + 写入审计日志."""
        self._published.append(event)

        # 日志记录
        log_msg = f"[DQC {event.metric_id}] {event.level.name}: {event.message}"
        if event.level == DQCLevel.CRITICAL:
            logger.critical(log_msg)
        elif event.level == DQCLevel.ERROR:
            logger.error(log_msg)
        elif event.level == DQCLevel.WARN:
            logger.warning(log_msg)
        else:
            logger.info(log_msg)

        # 发布到 RiskBus (复用现有总线, fail-safe)
        try:
            self._publish_to_risk_bus(event)
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as e:
            logger.warning("发布到 RiskBus 失败 (fail-safe): %s", e)

        # 写入审计日志 (fail-safe)
        try:
            self._write_audit_log(event)
        except (OSError, RuntimeError, ValueError) as e:
            logger.warning("写入 DQC 审计日志失败 (fail-safe): %s", e)

    def _publish_to_risk_bus(self, event: DQCEvent) -> None:
        """发布到 RiskBus (复用现有总线)."""
        try:
            from utils.risk.risk_bus import get_risk_bus
            from utils.risk.risk_event import (
                RiskEvent,
                RiskEventType,
                RiskSeverity,
            )

            bus = get_risk_bus()
            # DQC 事件映射到 LIQUIDITY_BREACH (最接近的数据质量事件类型)
            # 或使用 KILL_SWITCH_TRIGGERED (CRITICAL 级别)
            event_type = (
                RiskEventType.KILL_SWITCH_TRIGGERED
                if event.level == DQCLevel.CRITICAL
                else RiskEventType.LIQUIDITY_BREACH
            )
            severity = RiskSeverity(self.level.to_risk_severity) if False else RiskSeverity(
                "critical" if event.level in (DQCLevel.ERROR, DQCLevel.CRITICAL)
                else ("warn" if event.level == DQCLevel.WARN else "info")
            )

            risk_event = RiskEvent(
                event_type=event_type,
                source="dqc",
                severity=severity,
                payload=event.to_risk_event_payload(),
                symbol=event.symbol,
            )
            bus.publish(risk_event)
        except ImportError as e:
            logger.debug("RiskBus 不可用, 跳过事件发布: %s", e)

    def _write_audit_log(self, event: DQCEvent) -> None:
        """写入 DQC 审计日志 (JSONL 格式)."""
        import json

        from utils.path_config import get_reports_dir

        audit_dir = get_reports_dir() / "dqc" / event.timestamp[:10].replace("-", "")
        audit_dir.mkdir(parents=True, exist_ok=True)
        audit_file = audit_dir / "dqc_events.jsonl"

        with open(audit_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def _log_block(self, target_date: date, blocking: list[DQCEvent]) -> None:
        """记录阻断决策到日志."""
        logger.error(
            "DQC P2 阻断因子计算: target_date=%s, blocking_events=%d",
            target_date, len(blocking),
        )
        for e in blocking:
            logger.error("  - [%s] %s", e.metric_id, e.message)

    def _is_gate_enabled(self) -> bool:
        """检查 Feature Flag 是否启用阻断模式."""
        try:
            from utils.infra.feature_flags import is_enabled
            return is_enabled(FLAG_USE_P2_GATE)
        except (ImportError, RuntimeError, ValueError) as e:
            logger.debug("Feature Flag 检查失败, 默认不阻断: %s", e)
            return False


# ============================================================
# 模块级便捷函数
# ============================================================
def run_p2_gate(
    target_date: date,
    symbols: list[str],
    df: Optional[pd.DataFrame] = None,
    data_arrival_time: Optional[datetime] = None,
    cache_path: Optional[Path] = None,
) -> tuple[bool, list[DQCEvent]]:
    """执行 P2 检查 (便捷入口).

    Args:
        target_date: 目标交易日
        symbols: 预期标的池
        df: 待检查的 DataFrame (None 时从 cache_path 加载)
        data_arrival_time: 数据实际到达时间
        cache_path: 缓存文件路径

    Returns:
        tuple[是否通过, 事件列表]
    """
    gate = P2CacheQualityGate()
    return gate.run(target_date, symbols, df, data_arrival_time, cache_path)
