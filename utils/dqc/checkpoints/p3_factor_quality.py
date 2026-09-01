"""检查点 P3: 因子质量门禁 — DQC Phase 2 核心.

位置: 在训练样本生成前执行 (因子计算后, 模型训练前)
职责: 检查因子分布稳定性 (F 维度) + 因子重复 (U-02) + 可复现性 (X-04)
阻断: ERROR 级别阻断训练样本生成 (Feature Flag USE_DQC_P3_GATE 控制)

接入方式:
    >>> from utils.dqc import run_p3_gate
    >>> passed, events = run_p3_gate(
    ...     target_date=date(2026, 8, 4),
    ...     factor_df=current_factors,
    ...     baseline_df=baseline_factors,
    ...     factor_cols=["MOM_5D", "VOL_20D"],
    ... )
    >>> if not passed:
    ...     # 阻断训练样本生成, 触发降级 (使用昨日样本 / 跳过重训练)
    ...     logger.error("DQC P3 阻断, 跳过训练样本生成")

Feature Flag:
    USE_DQC_P3_GATE (默认 False, critical_path: false)
    - True: ERROR 阻断下游流程
    - False: 仅日志观察, 不阻断 (Phase 2 观察模式)

硬约束:
    - HC-DQC4: DQC 自身失败 fail-safe (不阻塞生产, 降级为通过)
    - 复用 DriftMonitor.compute_psi() (不重复造轮子)
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from utils.dqc.event_types import DQCCheckpoint, DQCEvent, DQCLevel, make_event
from utils.dqc.metrics import check_distribution_drift

logger = logging.getLogger("dqc.p3_gate")

# Feature Flag 名称
FLAG_USE_P3_GATE = "USE_DQC_P3_GATE"

# 因子 NaN 比例阈值 (X-04 可复现性, NaN 多表示计算不稳定)
FACTOR_NAN_THRESHOLD_WARN = 0.05  # 5%
FACTOR_NAN_THRESHOLD_ERROR = 0.20  # 20%


class P3FactorQualityGate:
    """P3 检查点: 因子质量门禁.

    执行顺序:
        1. F 维度 (F-01~F-04): 分布稳定性 (PSI / 均值 / 方差 / 极值)
        2. U-02: 因子重复计算 (同标的同日期重复行)
        3. X-04: 因子可复现性 (NaN 比例 + 数值稳定性)

    阻断规则:
        - 任何 ERROR/CRITICAL 级别事件 → 阻断
        - Feature Flag USE_DQC_P3_GATE=False 时 → 仅日志不阻断
    """

    BLOCKING_LEVELS = {DQCLevel.ERROR, DQCLevel.CRITICAL}

    def __init__(self) -> None:
        """初始化 P3 门禁."""
        self._checkpoint = DQCCheckpoint.P3_FACTOR
        self._published: list[DQCEvent] = []

    def run(
        self,
        target_date: date,
        factor_df: pd.DataFrame,
        baseline_df: pd.DataFrame,
        factor_cols: list[str],
        symbols: list[str] | None = None,
    ) -> tuple[bool, list[DQCEvent]]:
        """执行 P3 检查.

        Args:
            target_date: 目标交易日
            factor_df: 当前因子 DataFrame (含 symbol/date + factor_cols)
            baseline_df: 基线因子 DataFrame (T-N ~ T-1 的因子值, 用于漂移对比)
            factor_cols: 待检查的因子列名
            symbols: 预期标的池 (可选, 用于 U-02 范围校验)

        Returns:
            tuple[是否通过, 事件列表]
            - 是否通过: True=可继续生成训练样本, False=应阻断
            - 事件列表: 所有 DQC 事件 (含 INFO/WARN/ERROR/CRITICAL)
        """
        self._published = []
        logger.info(
            "DQC P3 开始检查: target_date=%s, factor_cols=%d, rows=%d",
            target_date,
            len(factor_cols),
            len(factor_df),
        )

        # 1. 输入校验
        if factor_df.empty:
            event = make_event(
                metric_id="F-01",
                level=DQCLevel.ERROR,
                checkpoint=self._checkpoint,
                value=0.0,
                threshold=1.0,
                message="P3 检查失败: factor_df 为空",
                target_date=str(target_date),
            )
            self._publish(event)
            return False, self._published

        if not factor_cols:
            event = make_event(
                metric_id="F-01",
                level=DQCLevel.WARN,
                checkpoint=self._checkpoint,
                value=0.0,
                threshold=1.0,
                message="P3 检查跳过: factor_cols 为空",
                target_date=str(target_date),
            )
            self._publish(event)
            return True, self._published

        # 2. 六维检查
        try:
            all_events: list[DQCEvent] = []

            # F 维度: 分布稳定性 (委托 distribution.py, 内部调用 compute_psi)
            events_f = check_distribution_drift(
                baseline_df=baseline_df,
                current_df=factor_df,
                factor_cols=factor_cols,
                checkpoint=self._checkpoint,
            )
            all_events.extend(events_f)

            # U-02: 因子重复计算
            events_u02 = self._check_u02_factor_dedup(factor_df, factor_cols)
            all_events.extend(events_u02)

            # X-04: 因子可复现性 (NaN 比例)
            events_x04 = self._check_x04_factor_reproducibility(factor_df, factor_cols)
            all_events.extend(events_x04)

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError) as e:
            # HC-DQC4: DQC 自身失败 fail-safe (不阻塞生产, 降级为通过)
            logger.exception("DQC P3 检查异常, 降级为通过 (fail-safe): %s", e)
            event = make_event(
                metric_id="F-01",
                level=DQCLevel.WARN,
                checkpoint=self._checkpoint,
                value=0.0,
                threshold=1.0,
                message=f"DQC P3 自身异常, 降级为通过: {e}",
                exception=str(e),
                exception_type=type(e).__name__,
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
                "DQC P3 发现 %d 个阻断级问题, 但 Feature Flag %s=False, 观察模式不阻断",
                len(blocking),
                FLAG_USE_P3_GATE,
            )
            return True, self._published

        passed = len(blocking) == 0
        if not passed:
            self._log_block(target_date, blocking)

        logger.info(
            "DQC P3 完成: passed=%s, events=%d, blocking=%d",
            passed,
            len(all_events),
            len(blocking),
        )
        return passed, self._published

    # ============================================================
    # U-02: 因子重复计算
    # ============================================================
    def _check_u02_factor_dedup(
        self,
        factor_df: pd.DataFrame,
        factor_cols: list[str],
    ) -> list[DQCEvent]:
        """U-02: 因子重复计算 — 同一 (symbol, date) 下不应有重复行.

        阈值: 0 重复
        """
        events: list[DQCEvent] = []

        symbol_col = (
            "symbol"
            if "symbol" in factor_df.columns
            else ("code" if "code" in factor_df.columns else None)
        )
        date_col = (
            "date"
            if "date" in factor_df.columns
            else ("datetime" if "datetime" in factor_df.columns else None)
        )
        if symbol_col is None or date_col is None:
            return events

        duplicates = factor_df.duplicated(subset=[symbol_col, date_col], keep=False)
        dup_count = int(duplicates.sum())

        if dup_count > 0:
            events.append(
                make_event(
                    metric_id="U-02",
                    level=DQCLevel.ERROR,
                    checkpoint=self._checkpoint,
                    value=float(dup_count),
                    threshold=0.0,
                    message=f"因子重复计算: (symbol, date) 重复 {dup_count} 行",
                    violation_count=dup_count,
                    factor_cols=factor_cols,
                )
            )

        return events

    # ============================================================
    # X-04: 因子可复现性 (NaN 比例 + 数值稳定性)
    # ============================================================
    def _check_x04_factor_reproducibility(
        self,
        factor_df: pd.DataFrame,
        factor_cols: list[str],
    ) -> list[DQCEvent]:
        """X-04: 因子可复现性 — NaN 比例过高表示计算不稳定.

        阈值:
            NaN > 20%: ERROR (因子不可用)
            NaN > 5%:  WARN (因子质量下降)
            NaN <= 5%: 通过
        """
        events: list[DQCEvent] = []
        if factor_df.empty:
            return events

        total_rows = len(factor_df)
        if total_rows == 0:
            return events

        for col in factor_cols:
            if col not in factor_df.columns:
                continue
            nan_count = int(factor_df[col].isna().sum())
            if nan_count == 0:
                continue
            nan_rate = nan_count / total_rows

            if nan_rate > FACTOR_NAN_THRESHOLD_ERROR:
                level = DQCLevel.ERROR
            elif nan_rate > FACTOR_NAN_THRESHOLD_WARN:
                level = DQCLevel.WARN
            else:
                continue

            events.append(
                make_event(
                    metric_id="X-04",
                    level=level,
                    checkpoint=self._checkpoint,
                    value=nan_rate,
                    threshold=FACTOR_NAN_THRESHOLD_WARN,
                    message=f"因子 {col} NaN 比例 {nan_rate:.1%} (计算不稳定, 可复现性差)",
                    factor=col,
                    nan_count=nan_count,
                    nan_rate=nan_rate,
                    total_rows=total_rows,
                )
            )

        return events

    # ============================================================
    # 内部方法 (复用 P2 模式)
    # ============================================================
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
            event_type = (
                RiskEventType.KILL_SWITCH_TRIGGERED
                if event.level == DQCLevel.CRITICAL
                else RiskEventType.LIQUIDITY_BREACH
            )
            severity = RiskSeverity(
                "critical"
                if event.level in (DQCLevel.ERROR, DQCLevel.CRITICAL)
                else ("warn" if event.level == DQCLevel.WARN else "info")
            )

            risk_event = RiskEvent(
                event_type=event_type,
                source="dqc.p3",
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
            "DQC P3 阻断训练样本生成: target_date=%s, blocking_events=%d",
            target_date,
            len(blocking),
        )
        for e in blocking:
            logger.error("  - [%s] %s", e.metric_id, e.message)

    def _is_gate_enabled(self) -> bool:
        """检查 Feature Flag 是否启用阻断模式."""
        try:
            from utils.infra.feature_flags import is_enabled

            return is_enabled(FLAG_USE_P3_GATE)
        except (ImportError, RuntimeError, ValueError) as e:
            logger.debug("Feature Flag 检查失败, 默认不阻断: %s", e)
            return False


# ============================================================
# 模块级便捷函数
# ============================================================
def run_p3_gate(
    target_date: date,
    factor_df: pd.DataFrame,
    baseline_df: pd.DataFrame,
    factor_cols: list[str],
    symbols: list[str] | None = None,
) -> tuple[bool, list[DQCEvent]]:
    """执行 P3 检查 (便捷入口).

    Args:
        target_date: 目标交易日
        factor_df: 当前因子 DataFrame
        baseline_df: 基线因子 DataFrame (用于漂移对比)
        factor_cols: 待检查的因子列名
        symbols: 预期标的池 (可选)

    Returns:
        tuple[是否通过, 事件列表]
    """
    gate = P3FactorQualityGate()
    return gate.run(target_date, factor_df, baseline_df, factor_cols, symbols)
