"""数据质量监控 (Data Quality Control, DQC) — 模块整合 v8.6.15.

任务: Wave 3 第三阶段延伸 → DQC Phase 1 + Phase 2
责任层: L3 数据管道
依赖: utils.risk.risk_bus / utils.infra.feature_flags / utils.alpha.drift_monitor

设计原则 (对冲基金标准):
    1. 六维指标 × 五检查点 × 四级报警
       - 六维: 完整性/时效性/一致性/准确性/唯一性/分布稳定性
       - 五检查点: P1源头/P2缓存/P3因子/P4样本/P5预测
       - 四级: INFO/WARN/ERROR/CRITICAL
    2. 阻断式门禁 (ERROR/CRITICAL 阻断下游流程)
       - 数据有问题不能跑因子, 否则污染下游 (与私募机构一致)
       - Feature Flag USE_DQC_P2_GATE / USE_DQC_P3_GATE 控制是否阻断 (默认 False 观察模式)
    3. 复用现有基础设施
       - RiskBus: 事件发布
       - DriftMonitor.compute_psi: 维度 F 分布稳定性 (Phase 2 已接入)
       - KillSwitch: CRITICAL 级别联动
       - ShadowAccount: CRITICAL 时暂停推进
       - cairn/LOG: ERROR 级别自动追加
    4. 审计可追溯
       - 所有 DQC 事件写入 reports/dqc/YYYY-MM-DD/ 审计日志
       - 所有阻断决策记录到 cairn/LOG.md

Phase 进度:
    - Phase 1 (DONE): P2 检查点 + 四维指标 (C/T/A/U)
    - Phase 2 (DONE 2026-08-04): P3 检查点 + F 维度 (分布稳定性, 复用 DriftMonitor)
                                 + X 维度 (跨源校验, consistency.py)
    - Phase 3 (TODO): P1/P4/P5 检查点 + 仪表盘

硬约束:
    - HC-DQC1: ERROR 级别必须阻断下游 (Feature Flag 关闭时仅日志不阻断)
    - HC-DQC2: CRITICAL 级别必须联动 KillSwitch (fail-closed)
    - HC-DQC3: 历史数据只标记不修改 (X-03 历史值不变性)
    - HC-DQC4: DQC 自身失败 fail-safe (不阻塞生产, 降级为日志)

Feature Flag:
    - USE_DQC_P2_GATE (默认 False, critical_path: false)
      启用后: P2 检查点 ERROR 阻断因子计算
      关闭时: P2 仅日志观察, 不阻断
    - USE_DQC_P3_GATE (默认 False)
      启用后: P3 检查点 ERROR 阻断训练样本生成
      关闭时: P3 仅日志观察, 不阻断
    - USE_DQC_DASHBOARD (默认 False)

Usage:
    >>> from utils.dqc import run_p2_gate, run_p3_gate, DQCEvent, DQCLevel
    >>> # P2: 缓存质量门禁 (因子计算前)
    >>> passed, events = run_p2_gate(target_date, symbols)
    >>> if not passed:
    ...     # 阻断因子计算
    >>> # P3: 因子质量门禁 (训练样本生成前)
    >>> passed, events = run_p3_gate(target_date, factor_df, baseline_df, factor_cols)
    >>> if not passed:
    ...     # 阻断训练样本生成
"""

from __future__ import annotations

from utils.dqc.aggregator import AlertAggregator, get_aggregator
from utils.dqc.checkpoints.p2_cache_quality import P2CacheQualityGate, run_p2_gate
from utils.dqc.checkpoints.p3_factor_quality import P3FactorQualityGate, run_p3_gate
from utils.dqc.event_types import DQCCheckpoint, DQCEvent, DQCLevel, DQCMetric

__all__ = [
    "DQCEvent",
    "DQCLevel",
    "DQCCheckpoint",
    "DQCMetric",
    "AlertAggregator",
    "get_aggregator",
    "P2CacheQualityGate",
    "run_p2_gate",
    "P3FactorQualityGate",
    "run_p3_gate",
]

__version__ = "0.2.0"
