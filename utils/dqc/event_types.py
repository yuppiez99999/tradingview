"""DQC 事件类型定义 — DQC Phase 1.

设计原则:
    1. dataclass + frozen=True — 事件不可变, 防止订阅者篡改 (与 RiskEvent 一致)
    2. 与 RiskEvent 协议兼容 — 可转换为 RiskEvent 发布到 RiskBus
    3. 严重级别分级 (INFO/WARN/ERROR/CRITICAL) — 比 RiskSeverity 多一级 ERROR
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("dqc.event")


# ============================================================
# 严重级别 (4 级, 比 RiskSeverity 多 ERROR 级)
# ============================================================
class DQCLevel(str, Enum):
    """DQC 严重级别 — 4 级.

    级别递增:
        INFO < WARN < ERROR < CRITICAL

    动作:
        INFO      — 仅日志记录
        WARN     — 日志 + 通知 + 因子降权 50%
        ERROR    — 日志 + 加急通知 + 阻断下游 + 降权到 0%
        CRITICAL — 上述全部 + KillSwitch 触发 + Shadow 暂停
    """

    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    CRITICAL = "critical"

    @property
    def is_blocking(self) -> bool:
        """是否阻断下游流程."""
        return self in (DQCLevel.ERROR, DQCLevel.CRITICAL)

    @property
    def to_risk_severity(self) -> str:
        """映射到 RiskSeverity (用于发布到 RiskBus)."""
        mapping = {
            DQCLevel.INFO: "info",
            DQCLevel.WARN: "warn",
            DQCLevel.ERROR: "critical",  # ERROR 视为 RiskBus 的 critical
            DQCLevel.CRITICAL: "critical",
        }
        return mapping[self]


# ============================================================
# 检查点枚举 (5 个)
# ============================================================
class DQCCheckpoint(str, Enum):
    """DQC 检查点 — 数据流 5 道防线."""

    P1_SOURCE = "P1"  # 源头完整性 (数据源 → 缓存)
    P2_CACHE = "P2"   # 缓存质量 (因子计算前)
    P3_FACTOR = "P3"  # 因子质量 (训练样本生成前)
    P4_SAMPLE = "P4"  # 样本质量 (模型训练前)
    P5_PREDICTION = "P5"  # 预测质量 (下单前)


# ============================================================
# 指标枚举 (六维, 共 30+ 指标)
# ============================================================
class DQCMetric(str, Enum):
    """DQC 指标 ID — 六维分类."""

    # 维度 C: 完整性 (Completeness) — P1/P2
    C01_SYMBOL_COVERAGE = "C-01"        # 标的覆盖率
    C02_TRADING_DAY_COVERAGE = "C-02"   # 交易日覆盖率
    C03_FIELD_MISSING_RATE = "C-03"     # 字段缺失率
    C04_TIMESTAMP_CONTINUITY = "C-04"  # 时间戳连续性
    C05_OHLCV_COMPLETENESS = "C-05"    # OHLCV 完整性
    C06_ADJFACTOR_COMPLETENESS = "C-06"  # 复权因子完整性

    # 维度 T: 时效性 (Timeliness) — P1
    T01_DATA_LATENCY = "T-01"          # 数据延迟
    T02_LATEST_DATE = "T-02"           # 最新数据日期
    T03_EOD_ARRIVAL = "T-03"           # EOD 到位时间
    T04_FACTOR_COMPUTE_TIME = "T-04"   # 因子计算耗时
    T05_TRAIN_DATA_READY = "T-05"      # 训练数据就绪

    # 维度 X: 一致性 (Consistency) — P2 (跨源校验)
    X01_CROSS_SOURCE_PRICE = "X-01"    # 跨源价格偏差
    X02_CROSS_SOURCE_VOL = "X-02"      # 跨源成交量偏差
    X03_HISTORY_INVARIANCE = "X-03"    # 历史值不变性
    X04_FACTOR_REPRODUCIBILITY = "X-04"  # 因子值可复现性
    X05_INDEX_CONSISTENCY = "X-05"     # 指数成分股一致

    # 维度 A: 准确性 (Accuracy) — P2 (业务规则)
    A01_PRICE_CHANGE_LIMIT = "A-01"    # 涨跌幅边界
    A02_OHLC_RELATION = "A-02"         # OHLC 关系
    A03_VOLUME_NON_NEGATIVE = "A-03"   # 成交量非负
    A04_MARKET_CAP_CONSISTENCY = "A-04"  # 市值一致性
    A05_PRICE_JUMP = "A-05"            # 价格异常跳变
    A06_ZERO_PRICE = "A-06"            # 零价格检测

    # 维度 U: 唯一性 (Uniqueness) — P2/P3
    U01_PRIMARY_KEY_DEDUP = "U-01"     # 主键去重
    U02_FACTOR_DEDUP = "U-02"          # 因子重复计算
    U03_SYMBOL_CODE_FORMAT = "U-03"    # 标的代码规范

    # 维度 F: 分布稳定性 (Distribution Drift) — P3/P4/P5
    # (委托 DriftMonitor 实现, 这里仅做事件归档)
    F01_FACTOR_PSI = "F-01"            # 因子 PSI
    F02_MEAN_DRIFT = "F-02"            # 均值漂移
    F03_VARIANCE_DRIFT = "F-03"        # 方差漂移
    F04_EXTREME_FREQ = "F-04"          # 极值频率
    F05_LABEL_DIST_DRIFT = "F-05"      # 标签分布漂移
    F06_PRED_DIST_DRIFT = "F-06"       # 预测分布漂移
    F07_IC_DECAY = "F-07"              # IC 衰减 (已实现)


# ============================================================
# 事件载体
# ============================================================
@dataclass(frozen=True)
class DQCEvent:
    """DQC 事件 (不可变).

    与 RiskEvent 协议兼容, 可转换为 RiskEvent 发布到 RiskBus.

    Attributes:
        metric_id: 指标 ID (如 "C-01", "A-02")
        level: 严重级别 (INFO/WARN/ERROR/CRITICAL)
        checkpoint: 检查点 (P1/P2/P3/P4/P5)
        symbol: 涉及标的 (None 表示全市场/组合级)
        value: 实际值
        threshold: 阈值
        message: 人类可读消息
        timestamp: ISO 格式时间戳 (自动填充)
        context: 附加上下文 (如 {"field": "close", "expected": 100, "actual": 99})
    """

    metric_id: str
    level: DQCLevel
    checkpoint: DQCCheckpoint
    value: float
    threshold: float
    message: str
    symbol: Optional[str] = None
    timestamp: str = ""
    context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验 + 自动填充时间戳."""
        if not self.timestamp:
            object.__setattr__(self, "timestamp", self._now_iso())
        if not isinstance(self.level, DQCLevel):
            raise TypeError(f"level must be DQCLevel, got {type(self.level)}")
        if not isinstance(self.checkpoint, DQCCheckpoint):
            raise TypeError(f"checkpoint must be DQCCheckpoint, got {type(self.checkpoint)}")

    @staticmethod
    def _now_iso() -> str:
        """当前时间 ISO 格式 (本地时区, 与 RiskEvent 一致使用 UTC)."""
        return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S") + "Z"

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典 (用于日志/审计)."""
        return {
            "metric_id": self.metric_id,
            "level": self.level.value,
            "checkpoint": self.checkpoint.value,
            "symbol": self.symbol,
            "value": self.value,
            "threshold": self.threshold,
            "message": self.message,
            "timestamp": self.timestamp,
            "context": dict(self.context),
        }

    def to_risk_event_payload(self) -> dict[str, Any]:
        """转换为 RiskEvent.payload (用于发布到 RiskBus)."""
        return {
            "dqc_metric": self.metric_id,
            "dqc_level": self.level.value,
            "dqc_checkpoint": self.checkpoint.value,
            "value": self.value,
            "threshold": self.threshold,
            "message": self.message,
            "context": dict(self.context),
        }


# ============================================================
# 便捷构造函数
# ============================================================
def make_event(
    metric_id: str,
    level: DQCLevel,
    checkpoint: DQCCheckpoint,
    value: float,
    threshold: float,
    message: str,
    symbol: Optional[str] = None,
    **context: Any,
) -> DQCEvent:
    """便捷构造 DQCEvent.

    Args:
        metric_id: 指标 ID (如 "C-01")
        level: 严重级别
        checkpoint: 检查点
        value: 实际值
        threshold: 阈值
        message: 消息
        symbol: 涉及标的 (None=全市场)
        **context: 附加上下文

    Returns:
        DQCEvent 实例
    """
    return DQCEvent(
        metric_id=metric_id,
        level=level,
        checkpoint=checkpoint,
        value=value,
        threshold=threshold,
        message=message,
        symbol=symbol,
        context=context,
    )
