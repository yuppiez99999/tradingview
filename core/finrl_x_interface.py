"""
FinRL-X 权重中心接口架构
========================

文献依据: #49 FinRL-X (PAKDD 2026)
任务: LIT-4.1 FinRL-X 权重中心接口架构

核心设计
--------
1. WeightCenter: 权重中心 — 统一回测/实盘接口
2. StrategyPipeline: 可组合策略管线 — 数据→特征→信号→权重
3. BacktestLiveConsistency: 回测=实盘一致性验证
4. FinRLXInterface: 集成接口 — 旧管道兼容

关键特性
--------
- 回测=实盘一致性 (weight center 作为单一真相源)
- 可组合策略管线 (modular strategy pipeline)
- 旧管道兼容 (legacy adapter)

使用示例
--------
    from core.finrl_x_interface import FinRLXInterface, StrategyNode

    pipeline = StrategyPipeline()
    pipeline.add(StrategyNode("data"))
    pipeline.add(StrategyNode("signal"))
    weights = pipeline.execute(data0)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np

logger = logging.getLogger("finrl_x_interface")


# ============================================================
# 枚举
# ============================================================

class WeightSource(str, Enum):
    """权重来源。"""
    BACKTEST = "backtest"
    LIVE = "live"
    PAPER = "paper"


class PipelineStage(str, Enum):
    """管线阶段。"""
    DATA = "data"
    FEATURE = "feature"
    SIGNAL = "signal"
    WEIGHT = "weight"
    EXECUTION = "execution"


# ============================================================
# 权重中心
# ============================================================

@dataclass
class WeightRecord:
    """权重记录。

    Attributes:
        weights: 权重向量
        source: 来源 (backtest/live/paper)
        timestamp: 时间戳
        metadata: 元数据
    """
    weights: np.ndarray
    source: WeightSource
    timestamp: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class WeightCenter:
    """权重中心 — 统一回测/实盘接口.

    作为回测和实盘的单一真相源 (single source of truth),
    确保回测=实盘一致性。
    """

    def __init__(self) -> None:
        self._records: list[WeightRecord] = []
        self._latest: dict[WeightSource, WeightRecord | None] = {
            WeightSource.BACKTEST: None,
            WeightSource.LIVE: None,
            WeightSource.PAPER: None,
        }

    def submit(
        self,
        weights: np.ndarray,
        source: WeightSource,
        timestamp: str = "",
        **metadata: Any,
    ) -> WeightRecord:
        """提交权重。"""
        record = WeightRecord(
            weights=weights.copy(),
            source=source,
            timestamp=timestamp,
            metadata=metadata,
        )
        self._records.append(record)
        self._latest[source] = record
        logger.debug("权重提交: source=%s, n=%d", source.value, len(weights))
        return record

    def get_latest(self, source: WeightSource) -> WeightRecord | None:
        """获取最新权重。"""
        return self._latest[source]

    def check_consistency(
        self,
        threshold: float = 0.01,
    ) -> dict[str, Any]:
        """检查回测=实盘一致性.

        Args:
            threshold: 权重差异阈值
        Returns:
            一致性报告
        """
        bt = self._latest[WeightSource.BACKTEST]
        live = self._latest[WeightSource.LIVE]

        if bt is None or live is None:
            return {"consistent": False, "reason": "missing weights"}

        if len(bt.weights) != len(live.weights):
            return {"consistent": False, "reason": "dimension mismatch"}

        diff = np.abs(bt.weights - live.weights)
        max_diff = float(np.max(diff))
        mean_diff = float(np.mean(diff))
        consistent = max_diff < threshold

        return {
            "consistent": consistent,
            "max_diff": max_diff,
            "mean_diff": mean_diff,
            "threshold": threshold,
            "backtest_norm": float(np.linalg.norm(bt.weights)),
            "live_norm": float(np.linalg.norm(live.weights)),
        }

    def get_history(self, source: WeightSource | None = None) -> list[WeightRecord]:
        """获取历史记录。"""
        if source is None:
            return list(self._records)
        return [r for r in self._records if r.source == source]


# ============================================================
# 策略管线
# ============================================================

@dataclass
class StrategyNode:
    """策略管线节点.

    Attributes:
        name: 节点名称
        stage: 管线阶段
        func: 处理函数
        enabled: 是否启用
    """
    name: str
    stage: PipelineStage = PipelineStage.SIGNAL
    func: Callable[..., Any] | None = None
    enabled: bool = True


class StrategyPipeline:
    """可组合策略管线.

    数据流: 数据 → 特征 → 信号 → 权重 → 执行
    每个节点可插拔/可组合。
    """

    def __init__(self) -> None:
        self._nodes: list[StrategyNode] = []
        self._context: dict[str, Any] = {}

    def add(self, node: StrategyNode) -> StrategyPipeline:
        """添加节点。"""
        self._nodes.append(node)
        return self

    def remove(self, name: str) -> bool:
        """移除节点。"""
        for i, node in enumerate(self._nodes):
            if node.name == name:
                self._nodes.pop(i)
                return True
        return False

    def execute(self, data: Any) -> Any:
        """执行管线。"""
        result = data
        for node in self._nodes:
            if not node.enabled:
                continue
            if node.func is not None:
                result = node.func(result, self._context)
            self._context[node.name] = result
            logger.debug("管线节点: %s (stage=%s)", node.name, node.stage.value)
        return result

    def get_stages(self) -> list[PipelineStage]:
        """获取阶段列表。"""
        return [node.stage for node in self._nodes if node.enabled]

    def validate(self) -> dict[str, Any]:
        """验证管线完整性。"""
        stages = self.get_stages()
        expected_order = [
            PipelineStage.DATA,
            PipelineStage.FEATURE,
            PipelineStage.SIGNAL,
            PipelineStage.WEIGHT,
        ]
        has_required = all(s in stages for s in expected_order)
        return {
            "valid": has_required,
            "n_nodes": len(self._nodes),
            "stages": [s.value for s in stages],
            "has_data": PipelineStage.DATA in stages,
            "has_signal": PipelineStage.SIGNAL in stages,
            "has_weight": PipelineStage.WEIGHT in stages,
        }


# ============================================================
# 回测=实盘一致性验证
# ============================================================

class BacktestLiveConsistency:
    """回测=实盘一致性验证器。"""

    def __init__(self, weight_center: WeightCenter) -> None:
        self.weight_center = weight_center
        self._reports: list[dict[str, Any]] = []

    def verify_weights(self, threshold: float = 0.01) -> dict[str, Any]:
        """验证权重一致性。"""
        report = self.weight_center.check_consistency(threshold)
        self._reports.append(report)
        return report

    def verify_data(
        self,
        backtest_data: np.ndarray,
        live_data: np.ndarray,
    ) -> dict[str, Any]:
        """验证数据一致性。"""
        if backtest_data.shape != live_data.shape:
            return {"consistent": False, "reason": "shape mismatch"}

        diff = np.abs(backtest_data - live_data)
        return {
            "consistent": float(np.max(diff)) < 1e-6,
            "max_diff": float(np.max(diff)),
            "mean_diff": float(np.mean(diff)),
        }

    def verify_execution(
        self,
        backtest_orders: list[dict],
        live_orders: list[dict],
    ) -> dict[str, Any]:
        """验证执行一致性。"""
        if len(backtest_orders) != len(live_orders):
            return {"consistent": False, "reason": "order count mismatch"}

        mismatches = 0
        for bt, live in zip(backtest_orders, live_orders, strict=True):
            if bt.get("symbol") != live.get("symbol"):
                mismatches += 1
            elif abs(bt.get("quantity", 0) - live.get("quantity", 0)) > 1e-6:
                mismatches += 1

        return {
            "consistent": mismatches == 0,
            "n_orders": len(backtest_orders),
            "mismatches": mismatches,
        }

    def get_reports(self) -> list[dict[str, Any]]:
        return list(self._reports)


# ============================================================
# FinRL-X 集成接口
# ============================================================

class FinRLXInterface:
    """FinRL-X 集成接口 — 旧管道兼容.

    使用示例:
        interface = FinRLXInterface()
        interface.submit_backtest_weights(weights)
        interface.submit_live_weights(weights)
        report = interface.verify_consistency()
    """

    def __init__(self) -> None:
        self.weight_center = WeightCenter()
        self.consistency = BacktestLiveConsistency(self.weight_center)
        self.pipeline = StrategyPipeline()

    def submit_backtest_weights(
        self, weights: np.ndarray, timestamp: str = "", **meta: Any,
    ) -> WeightRecord:
        """提交回测权重。"""
        return self.weight_center.submit(
            weights, WeightSource.BACKTEST, timestamp, **meta
        )

    def submit_live_weights(
        self, weights: np.ndarray, timestamp: str = "", **meta: Any,
    ) -> WeightRecord:
        """提交实盘权重。"""
        return self.weight_center.submit(
            weights, WeightSource.LIVE, timestamp, **meta
        )

    def verify_consistency(self, threshold: float = 0.01) -> dict[str, Any]:
        """验证一致性。"""
        return self.consistency.verify_weights(threshold)

    def build_pipeline(self, nodes: list[StrategyNode]) -> StrategyPipeline:
        """构建管线。"""
        self.pipeline = StrategyPipeline()
        for node in nodes:
            self.pipeline.add(node)
        return self.pipeline

    def run_pipeline(self, data: Any) -> Any:
        """运行管线。"""
        return self.pipeline.execute(data)

    def get_status(self) -> dict[str, Any]:
        """获取状态。"""
        return {
            "weight_history": len(self.weight_center.get_history()),
            "pipeline_valid": self.pipeline.validate()["valid"],
            "consistency_reports": len(self.consistency.get_reports()),
        }


# ============================================================
# 旧管道兼容适配器
# ============================================================

class LegacyAdapter:
    """旧管道兼容适配器.

    将旧管道接口 (get_weights/allocate) 适配到 FinRL-X。
    """

    def __init__(self, interface: FinRLXInterface) -> None:
        self.interface = interface

    def get_weights(self, source: str = "backtest") -> np.ndarray | None:
        """旧接口: get_weights。"""
        src = WeightSource(source) if source in [s.value for s in WeightSource] else WeightSource.BACKTEST
        record = self.interface.weight_center.get_latest(src)
        return record.weights if record is not None else None

    def allocate(self, returns: np.ndarray) -> np.ndarray:
        """旧接口: allocate (简单等权)。"""
        n = returns.shape[1]
        weights = np.ones(n) / n
        self.interface.submit_backtest_weights(weights)
        return weights


# ============================================================
# CLI 入口
# ============================================================

def main() -> None:
    """CLI 入口: 演示 FinRL-X 权重中心接口。"""
    print("=" * 60)
    print("FinRL-X 权重中心接口架构")
    print("文献: #49 FinRL-X PAKDD 2026")
    print("=" * 60)

    interface = FinRLXInterface()

    rng = np.random.default_rng(42)
    bt_weights = np.array([0.3, 0.25, 0.2, 0.15, 0.1])
    live_weights = bt_weights + rng.standard_normal(5) * 0.001
    live_weights = np.maximum(live_weights, 0)
    live_weights = live_weights / live_weights.sum()

    print("\n--- 权重提交 ---")
    interface.submit_backtest_weights(bt_weights, timestamp="2026-08-23")
    interface.submit_live_weights(live_weights, timestamp="2026-08-23")
    print(f"  回测权重: {bt_weights}")
    print(f"  实盘权重: {live_weights}")

    print("\n--- 一致性验证 ---")
    report = interface.verify_consistency(threshold=0.01)
    print(f"  一致: {report['consistent']}")
    print(f"  最大差异: {report['max_diff']:.6f}")
    print(f"  平均差异: {report['mean_diff']:.6f}")

    print("\n--- 策略管线 ---")
    def data_fn(data: Any, ctx: dict) -> Any:
        return {"data": data}

    def feature_fn(data: Any, ctx: dict) -> Any:
        return {"features": np.mean(data["data"])}

    def signal_fn(data: Any, ctx: dict) -> Any:
        return {"signal": data["features"] * 2}

    def weight_fn(data: Any, ctx: dict) -> Any:
        return np.array([0.4, 0.3, 0.2, 0.1])

    pipeline = interface.build_pipeline([
        StrategyNode("data", PipelineStage.DATA, data_fn),
        StrategyNode("feature", PipelineStage.FEATURE, feature_fn),
        StrategyNode("signal", PipelineStage.SIGNAL, signal_fn),
        StrategyNode("weight", PipelineStage.WEIGHT, weight_fn),
    ])

    result = interface.run_pipeline(np.random.randn(100, 4))
    validation = pipeline.validate()
    print(f"  管线有效: {validation['valid']}")
    print(f"  阶段: {validation['stages']}")
    print(f"  结果: {result}")

    print("\n--- 旧管道兼容 ---")
    adapter = LegacyAdapter(interface)
    legacy_weights = adapter.get_weights("backtest")
    print(f"  旧接口权重: {legacy_weights}")

    print("\n--- 状态 ---")
    status = interface.get_status()
    print(f"  {status}")


if __name__ == "__main__":
    main()
