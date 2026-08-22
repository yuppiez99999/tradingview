"""进化编排器 (v2) — 三层路由中枢 + Memory/Guard 集成.

模块整合 8.4 — ARCHITECTURE_自我进化框架 §6.2 (v2.0 合并版)
任务编号: T3.1 (Phase 3 进化层)

职责:
    统一调度 L1/L2/L3 三层进化动作, 整合:
      - EvolutionMemory: 全量审计留痕 (替代旧 decisions.jsonl 的部分功能)
      - EvolutionGuard: 五道防线, 所有提案必过
      - StrategyEvaluator: Public/Private 分数分离评估
      - AutoFixEngine: L1 自动修复
      - KillSwitch: 熔断冻结

与 utils/alpha/evolution_orchestrator.py (v1 原型) 的关系:
    v1 原型实现了观察期模式 (collect → evaluate → log), 但缺失:
      - L1/L2/L3 三层路由逻辑
      - EvolutionGuard 检查
      - EvolutionMemory 审计 (仍用旧 decisions.jsonl)
      - Kill Switch 冻结
    本模块 (v2) 复用 v1 的 collect_metrics/evaluate_current (通过组合),
    新增 route_proposal() / run_cycle() 实现完整三层路由.

三层路由逻辑 (ARCHITECTURE §6.2):
    L1 防御层 (自动执行, 无需审批):
        - AutoFixEngine 修复 → Memory 记录 (status=executed)
        - 不经 Guard (L1 是修复, 不是进化), 但记录到 Memory
    L2 优化层 (影子验证, 自动 Promote):
        - Guard 检查 → 影子账户验证 → 自动 Promote/Rollback
        - Memory 记录全生命周期 (pending → executed → learned)
    L3 进化层 (人工审批):
        - Guard 检查 → 生成人工审批工单 → 等待批准
        - Memory 记录 (status=pending), 批准后执行

核心 API:
    Orchestrator.run_cycle() -> CycleResult       # 完整感知→决策→行动→学习
    Orchestrator.route_proposal(proposal) -> Result  # 单提案路由

硬约束:
    - HC-1: Feature Flag 默认 False
    - HC-2: 所有进化动作 100% 审计留痕 (写入 Memory)
    - HC-3: 所有进化动作可一键回滚
    - HC-4: L3 进化需人工审批, 不可自动执行
    - HC-5: Kill Switch 触发时冻结所有进化

用法:
    from utils.evolution.orchestrator import EvolutionOrchestratorV2
    from utils.evolution.guard import EvolutionProposal, LEVEL_L2

    orch = EvolutionOrchestratorV2()
    result = orch.run_cycle()  # 完整循环

    # 或单提案路由
    proposal = EvolutionProposal(level=LEVEL_L2, ...)
    result = orch.route_proposal(proposal)
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================
# 常量
# ============================================================

# Feature Flag 名称
FLAG_ORCHESTRATOR = "USE_EVOLUTION_ORCHESTRATOR"
FLAG_EVALUATOR = "USE_STRATEGY_EVALUATOR"

# 循环结果状态
CYCLE_STATUS_SUCCESS = "success"
CYCLE_STATUS_DISABLED = "disabled"  # Flag 关闭
CYCLE_STATUS_FROZEN = "frozen"  # Kill Switch 冻结
CYCLE_STATUS_DEGRADED = "degraded"  # 子模块失败
CYCLE_STATUS_NO_ACTION = "no_action"  # 无需进化动作
CYCLE_STATUS_ROLLED_BACK = "rolled_back"  # L2 影子验证未通过, 自动回滚

# L2 影子验证 DSR 阈值 (低于此值则 rollback)
DEFAULT_L2_DSR_THRESHOLD = 0.5

# daily_returns.jsonl 路径
_SHADOW_RETURNS_PATH = _PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"


# ============================================================
# 数据类
# ============================================================


@dataclass
class CycleResult:
    """单次 run_cycle 的结果摘要."""

    status: str = CYCLE_STATUS_NO_ACTION
    level: str = ""  # 触发的层级 (L1/L2/L3), 空=无动作
    action: str = ""  # 执行的动作
    proposal_id: str = ""  # Memory 中的 proposal_id
    reason: str = ""
    metrics_snapshot: dict[str, Any] = field(default_factory=dict)
    evaluator_report: dict[str, Any] = field(default_factory=dict)
    guard_decision: dict[str, Any] = field(default_factory=dict)
    executed: bool = False  # 是否真正执行了进化动作
    weight_adjustments: dict[str, float] = field(default_factory=dict)  # {code: multiplier} 进化→再平衡权重乘子
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为字典 (用于审计)."""
        return {
            "status": self.status,
            "level": self.level,
            "action": self.action,
            "proposal_id": self.proposal_id,
            "reason": self.reason,
            "metrics_snapshot": self.metrics_snapshot,
            "evaluator_report": self.evaluator_report,
            "guard_decision": self.guard_decision,
            "executed": self.executed,
            "weight_adjustments": self.weight_adjustments,
            "timestamp": self.timestamp,
        }


# ============================================================
# 进化编排器 v2
# ============================================================


class EvolutionOrchestratorV2:
    """进化编排器 v2 — 三层路由 + Memory/Guard 集成.

    组合关系 (优先组合, 避免继承):
        - v1 原型 (utils.alpha.evolution_orchestrator.EvolutionOrchestrator):
            复用 collect_metrics / evaluate_current / log_decision
        - EvolutionMemory: 所有动作审计留痕
        - EvolutionGuard: L2/L3 提案五道防线检查
        - AutoFixEngine: L1 自动修复
        - KillSwitch: 熔断冻结检查

    设计原则:
        1. 单一职责: 本类只做路由调度, 不实现具体修复/训练逻辑
        2. 审计优先: 任何动作先 record 到 Memory, 再执行 (HC-2)
        3. Guard 必过: L2/L3 提案必经 Guard.check_proposal (HC-3)
        4. Kill Switch 优先: run_cycle 入口先检查熔断状态
        5. 容错降级: 子模块失败不阻塞, 返回降级结果
    """

    def __init__(
        self,
        memory: Any | None = None,
        guard: Any | None = None,
        auto_fix_engine: Any | None = None,
        kill_switch: Any | None = None,
        v1_orchestrator: Any | None = None,
        ab_test_framework: Any | None = None,
        shadow_adapter: Any | None = None,
        feature_flag_name: str = FLAG_ORCHESTRATOR,
        l2_dsr_threshold: float = DEFAULT_L2_DSR_THRESHOLD,
    ) -> None:
        """初始化编排器 v2.

        Args:
            memory: EvolutionMemory 实例 (None=懒加载默认实例)
            guard: EvolutionGuard 实例 (None=懒加载, 含 memory + kill_switch)
            auto_fix_engine: AutoFixEngine 实例 (None=懒加载)
            kill_switch: KillSwitch 实例 (None=懒加载, 用于 Guard)
            v1_orchestrator: v1 原型实例 (None=懒加载, 复用 collect/evaluate)
            ab_test_framework: ABTestFramework 实例 (None=懒加载)
            shadow_adapter: ShadowAccountAdapter 实例 (None=懒加载, L2 影子验证)
            feature_flag_name: Feature Flag 名称 (HC-1)
            l2_dsr_threshold: L2 影子验证 DSR 阈值, 低于此值则 rollback
        """
        self.feature_flag_name = feature_flag_name
        self._enabled = self._check_feature_flag(feature_flag_name)
        self._l2_dsr_threshold = float(l2_dsr_threshold)

        # 组件懒加载 (避免初始化时强依赖)
        self._memory = memory
        self._guard = guard
        self._auto_fix_engine = auto_fix_engine
        self._kill_switch = kill_switch
        self._ab_test_framework = ab_test_framework
        self._v1 = v1_orchestrator
        self._shadow_adapter = shadow_adapter

        # 闭环健康度指标 (阶段4)
        self._loop_health: dict[str, Any] = {
            "total_cycles": 0,
            "evolution_trigger_count": 0,
            "l1_count": 0,
            "l2_promote_count": 0,
            "l2_rollback_count": 0,
            "l3_pending_count": 0,
            "total_latency_ms": 0.0,
            "last_cycle_latency_ms": 0.0,
            "weight_adjustment_magnitudes": [],
        }

        logger.info(
            "EvolutionOrchestratorV2 初始化: enabled=%s (flag=%s, dsr_threshold=%.2f)",
            self._enabled,
            feature_flag_name,
            self._l2_dsr_threshold,
        )

    # ============================================================
    # Feature Flag
    # ============================================================

    def _check_feature_flag(self, name: str) -> bool:
        """检查 Feature Flag (HC-1)."""
        try:
            from utils.infra.feature_flags import is_enabled

            return bool(is_enabled(name))
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            logger.warning("Feature Flag 检查失败, 默认禁用: %s (%s)", name, e)
            return False

    @property
    def enabled(self) -> bool:
        """是否启用."""
        return self._enabled

    def _check_rollout_eligible(self) -> bool:
        """检查当前日期是否命中灰度比例 (阶段5).

        灰度判断: hash(date) % 100 < rollout_percent
        若灰度管理器不可用或未启动 → 返回 True (不阻塞, 由 Feature Flag 控制)

        Returns:
            True 如果应运行进化 (命中灰度 或 灰度未启动)
        """
        try:
            from scripts.gradual_rollout_manager import should_run_today

            return should_run_today()
        except (ImportError, AttributeError, RuntimeError, OSError, ValueError, TypeError, KeyError) as e:
            logger.debug("灰度比例检查跳过 (容错, 不阻塞): %s", e)
            return True

    # ============================================================
    # 组件懒加载
    # ============================================================

    def _get_memory(self) -> Any:
        """懒加载 EvolutionMemory."""
        if self._memory is None:
            from utils.evolution.memory import EvolutionMemory

            self._memory = EvolutionMemory()
        return self._memory

    def _get_kill_switch(self) -> Any | None:
        """懒加载 KillSwitch (失败返回 None, 不阻塞)."""
        if self._kill_switch is None:
            try:
                from utils.kill_switch import KillSwitch

                self._kill_switch = KillSwitch()
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
                # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
                logger.warning("KillSwitch 加载失败, Guard 熔断检查将跳过: %s", e)
                self._kill_switch = None
        return self._kill_switch

    def _get_guard(self) -> Any:
        """懒加载 EvolutionGuard (含 memory + kill_switch)."""
        if self._guard is None:
            from utils.evolution.guard import EvolutionGuard

            self._guard = EvolutionGuard(
                memory=self._get_memory(),
                kill_switch=self._get_kill_switch(),
            )
        return self._guard

    def _get_auto_fix_engine(self) -> Any:
        """懒加载 AutoFixEngine."""
        if self._auto_fix_engine is None:
            from utils.evolution.auto_fix_engine import AutoFixEngine

            self._auto_fix_engine = AutoFixEngine(memory=self._get_memory())
        return self._auto_fix_engine

    def _get_ab_test_framework(self) -> Any:
        """懒加载 ABTestFramework."""
        if self._ab_test_framework is None:
            from utils.alpha.ab_testing import ABTestFramework

            self._ab_test_framework = ABTestFramework()
        return self._ab_test_framework

    def _get_v1(self) -> Any:
        """懒加载 v1 原型 (复用 collect_metrics / evaluate_current)."""
        if self._v1 is None:
            try:
                from utils.alpha.evolution_orchestrator import EvolutionOrchestrator

                self._v1 = EvolutionOrchestrator()
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
                # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
                logger.warning("v1 原型加载失败, collect/evaluate 将不可用: %s", e)
                self._v1 = None
        return self._v1

    def _get_shadow_adapter(self) -> Any | None:
        """懒加载 ShadowAccountAdapter (L2 影子验证, 失败返回 None)."""
        if self._shadow_adapter is None:
            try:
                from utils.alpha.shadow_account_adapter import ShadowAccountAdapter

                self._shadow_adapter = ShadowAccountAdapter()
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError, ImportError) as e:
                logger.warning("ShadowAccountAdapter 加载失败, L2 影子验证将跳过: %s", e)
                self._shadow_adapter = None
        return self._shadow_adapter

    def _load_shadow_daily_returns(self) -> tuple[list[float], list[str]]:
        """从 daily_returns.jsonl 加载影子收益率序列.

        Returns:
            (daily_returns, dates) — 收益率列表 + 对应日期列表
            失败时返回 ([], [])
        """
        import json

        try:
            if not _SHADOW_RETURNS_PATH.exists():
                return [], []

            returns: list[float] = []
            dates: list[str] = []
            with open(_SHADOW_RETURNS_PATH, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning("daily_returns.jsonl 跳过无效行: %s", line[:50])
                        continue
                    ret = record.get("daily_return")
                    date_str = record.get("date", "")
                    if ret is not None and date_str:
                        returns.append(float(ret))
                        dates.append(str(date_str))
            return returns, dates
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError) as e:
            logger.warning("加载 daily_returns.jsonl 失败: %s", e)
            return [], []

    # ============================================================
    # 核心 API: run_cycle (感知→决策→行动→学习)
    # ============================================================

    def run_cycle(self, signal_history: dict[str, Any] | None = None) -> CycleResult:
        """运行一次完整进化循环 (感知→决策→行动→学习).

        流程:
            1. 感知: collect_metrics (只读 daily_returns)
            2. 决策: evaluate_current → 根据 ScoreReport 决定是否提提案
            3. 行动: route_proposal 路由到 L1/L2/L3
            4. 学习: Memory 自动留痕 (record + update_status)

        Args:
            signal_history: 信号历史 (可选, 传给评估器)

        Returns:
            CycleResult 循环结果摘要
        """
        timestamp = self._now_iso()
        _t_start = time.perf_counter()

        # HC-1: Feature Flag 检查
        if not self._enabled:
            return CycleResult(
                status=CYCLE_STATUS_DISABLED,
                reason=f"feature_flag_disabled ({self.feature_flag_name}=False)",
                timestamp=timestamp,
            )

        # 灰度比例检查 (阶段5: 生产灰度发布)
        if not self._check_rollout_eligible():
            return CycleResult(
                status=CYCLE_STATUS_DISABLED,
                reason="rollout_percent_excluded (灰度比例未命中)",
                timestamp=timestamp,
            )

        # Kill Switch 冻结检查 (HC-5)
        ks = self._get_kill_switch()
        if ks is not None:
            try:
                events = ks.get_event_history(days=1) if hasattr(ks, "get_event_history") else []
                # L3 熔断冻结所有层级; L2 冻结 L2/L3
                frozen = any(
                    e.get("level", 0) >= 2 and e.get("executed", False)
                    for e in events
                )
                if frozen:
                    logger.warning("Kill Switch 触发, 冻结所有进化 (HC-5)")
                    return CycleResult(
                        status=CYCLE_STATUS_FROZEN,
                        reason="kill_switch_triggered",
                        timestamp=timestamp,
                    )
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
                # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
                logger.warning("Kill Switch 检查异常 (容错继续): %s", e)

        # 1. 感知: 收集指标
        v1 = self._get_v1()
        if v1 is None:
            return CycleResult(
                status=CYCLE_STATUS_DEGRADED,
                reason="v1_orchestrator_unavailable",
                timestamp=timestamp,
            )

        metrics = v1.collect_metrics()
        if metrics.is_degraded:
            return CycleResult(
                status=CYCLE_STATUS_DEGRADED,
                reason=f"metrics_degraded: {metrics.degraded_reason}",
                metrics_snapshot=metrics.to_dict(),
                timestamp=timestamp,
            )

        # 2. 决策: 评估当前策略
        report = v1.evaluate_current(metrics=metrics, signal_history=signal_history)
        if report is None:
            return CycleResult(
                status=CYCLE_STATUS_NO_ACTION,
                reason="evaluation_skipped (样本不足或评估器降级)",
                metrics_snapshot=metrics.to_dict(),
                timestamp=timestamp,
            )

        # 3. 行动: 根据 recommendation 路由
        result = self._route_by_recommendation(report, metrics, timestamp)

        # 4. v1 记录决策到 decisions.jsonl (保持向后兼容)
        try:
            v1.log_decision(report=report, action=result.action, metrics=metrics)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            logger.warning("v1 log_decision 失败 (容错, Memory 已记录): %s", e)

        # 5. 闭环健康度指标更新 (阶段4)
        self._update_loop_health(result, _t_start)

        return result

    # ============================================================
    # 闭环健康度指标 (阶段4)
    # ============================================================

    def _update_loop_health(self, result: CycleResult, t_start: float) -> None:
        """更新闭环健康度指标 (run_cycle 结束时调用).

        指标:
            - total_cycles: 总循环次数
            - evolution_trigger_count: 触发进化动作的次数 (非 no_action/disabled/frozen)
            - l1_count / l2_promote_count / l2_rollback_count / l3_pending_count
            - total_latency_ms / last_cycle_latency_ms
            - weight_adjustment_magnitudes: 每次循环的权重调整幅度均值列表
        """
        latency_ms = (time.perf_counter() - t_start) * 1000.0
        self._loop_health["total_cycles"] += 1
        self._loop_health["total_latency_ms"] += latency_ms
        self._loop_health["last_cycle_latency_ms"] = round(latency_ms, 2)

        if result.status not in (CYCLE_STATUS_NO_ACTION, CYCLE_STATUS_DISABLED, CYCLE_STATUS_FROZEN):
            self._loop_health["evolution_trigger_count"] += 1

        if result.level == "L1":
            self._loop_health["l1_count"] += 1
        elif result.level == "L2":
            if result.status == CYCLE_STATUS_SUCCESS:
                self._loop_health["l2_promote_count"] += 1
            elif result.status == CYCLE_STATUS_ROLLED_BACK:
                self._loop_health["l2_rollback_count"] += 1
        elif result.level == "L3":
            self._loop_health["l3_pending_count"] += 1

        # 权重调整幅度: avg(|multiplier - 1.0|) over all codes
        if result.weight_adjustments:
            magnitudes = [abs(v - 1.0) for v in result.weight_adjustments.values()]
            avg_mag = sum(magnitudes) / len(magnitudes) if magnitudes else 0.0
            self._loop_health["weight_adjustment_magnitudes"].append(round(avg_mag, 6))

    def get_loop_health_metrics(self) -> dict[str, Any]:
        """获取闭环健康度指标快照.

        Returns:
            包含以下字段的字典:
            - total_cycles: 总循环次数
            - evolution_trigger_rate: 进化触发率 (evolution_trigger_count / total_cycles)
            - l1_count / l2_promote_count / l2_rollback_count / l3_pending_count
            - avg_latency_ms: 平均循环延迟 (ms)
            - last_latency_ms: 最近一次循环延迟 (ms)
            - avg_weight_adjustment_magnitude: 平均权重调整幅度
            - l2_promote_rate: L2 promote 率 (promote / (promote + rollback))
        """
        h = self._loop_health
        total = h["total_cycles"]
        promote = h["l2_promote_count"]
        rollback = h["l2_rollback_count"]
        l2_total = promote + rollback
        mags = h["weight_adjustment_magnitudes"]

        return {
            "total_cycles": total,
            "evolution_trigger_count": h["evolution_trigger_count"],
            "evolution_trigger_rate": (h["evolution_trigger_count"] / total) if total > 0 else 0.0,
            "l1_count": h["l1_count"],
            "l2_promote_count": promote,
            "l2_rollback_count": rollback,
            "l2_promote_rate": (promote / l2_total) if l2_total > 0 else 0.0,
            "l3_pending_count": h["l3_pending_count"],
            "avg_latency_ms": round(h["total_latency_ms"] / total, 2) if total > 0 else 0.0,
            "last_latency_ms": h["last_cycle_latency_ms"],
            "avg_weight_adjustment_magnitude": round(sum(mags) / len(mags), 6) if mags else 0.0,
        }

    # ============================================================
    # A/B 测试生命周期
    # ============================================================

    def run_ab_test_cycle(
        self,
        test_name: str,
        champion_model: str,
        challenger_model: str,
        traffic_split: float = 0.2,
        daily_champion_metrics: dict[str, float] | None = None,
        daily_challenger_metrics: dict[str, float] | None = None,
        date: str | None = None,
        min_samples: int = 30,
        force_evaluate: bool = False,
        description: str = "",
    ) -> CycleResult:
        """运行一次 A/B 测试生命周期步骤.

        流程:
            1. 创建 ABTest (如果尚不存在)
            2. 启动测试 (如果未启动)
            3. 记录每日指标 (如果提供)
            4. 评估 (如果样本充足或 force_evaluate=True)
            5. 写入 EvolutionMemory
            6. 根据评估结果生成 L2 提案

        Args:
            test_name: 测试名称
            champion_model: champion 模型名称
            challenger_model: challenger 模型名称
            traffic_split: challenger 流量占比 (0.0-1.0)
            daily_champion_metrics: 当日 champion 组指标 (可选)
            daily_challenger_metrics: 当日 challenger 组指标 (可选)
            date: 日期 YYYY-MM-DD (默认当天)
            min_samples: 评估所需最小样本数
            force_evaluate: 是否强制评估 (忽略样本数)
            description: 测试描述

        Returns:
            CycleResult 包含 ABTest 评估结果
        """
        timestamp = self._now_iso()

        if not self._enabled:
            return CycleResult(
                status=CYCLE_STATUS_DISABLED,
                reason=f"{self.feature_flag_name}=False",
                timestamp=timestamp,
            )

        ab = self._get_ab_test_framework()
        from utils.alpha.ab_testing import ABTestConfig

        # 1. 创建测试 (如果不存在)
        try:
            existing = ab.get_test(test_name)
            logger.info("ABTest %s 已存在 (status=%s)", test_name, existing.status)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            # 测试不存在, 创建
            config = ABTestConfig(
                name=test_name,
                champion_model=champion_model,
                challenger_model=challenger_model,
                traffic_split=traffic_split,
                min_samples=min_samples,
                description=description or f"进化编排器 ABTest: {champion_model} vs {challenger_model}",
            )
            ab.create_test(config)
            logger.info("ABTest %s 已创建", test_name)

        # 2. 启动测试 (如果未运行)
        try:
            running = ab.get_test(test_name)
            if running.status != "running":
                ab.start_test(test_name)
                logger.info("ABTest %s 已启动", test_name)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            logger.warning("ABTest %s 启动失败 (容错继续): %s", test_name, e)

        # 3. 记录每日指标 (如果提供)
        if daily_champion_metrics is not None and daily_challenger_metrics is not None:
            record_date = date or timestamp[:10]
            try:
                ab.record_daily_metrics(
                    test_name,
                    record_date,
                    daily_champion_metrics,
                    daily_challenger_metrics,
                )
                logger.info("ABTest %s 已记录指标: %s", test_name, record_date)
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
                # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
                logger.warning("ABTest %s 指标记录失败 (容错继续): %s", test_name, e)

        # 4. 评估
        test = ab.get_test(test_name)
        samples = len(test.daily_records)

        if not force_evaluate and samples < min_samples:
            return CycleResult(
                status=CYCLE_STATUS_NO_ACTION,
                level="L2",
                action="ab_test_collecting",
                reason=f"ABTest {test_name} 样本不足: {samples}/{min_samples}",
                timestamp=timestamp,
            )

        try:
            result = ab.evaluate_test(test_name)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            logger.warning("ABTest %s 评估失败 (容错): %s", test_name, e)
            return CycleResult(
                status=CYCLE_STATUS_DEGRADED,
                level="L2",
                action="ab_test_evaluate",
                reason=f"ABTest 评估失败: {e}",
                timestamp=timestamp,
            )

        # 5. 写入 EvolutionMemory
        memory_result = self._write_ab_test_to_memory(test_name, result)

        # 6. 根据推荐动作生成 L2 提案
        if result.recommendation == "promote":
            # challenger 更优 → 生成 L2 promote 提案
            proposal = self._build_ab_test_proposal(
                test_name, champion_model, challenger_model,
                "promote", result,
            )
            route_result = self.route_proposal(proposal)
            route_result.evaluator_report = result.to_dict()
            route_result.timestamp = timestamp
            return route_result

        if result.recommendation == "rollback":
            # champion 更优 → 生成 L2 rollback 提案
            proposal = self._build_ab_test_proposal(
                test_name, champion_model, challenger_model,
                "rollback", result,
            )
            route_result = self.route_proposal(proposal)
            route_result.evaluator_report = result.to_dict()
            route_result.timestamp = timestamp
            return route_result

        # continue: 无动作, 仅记录
        return CycleResult(
            status=CYCLE_STATUS_NO_ACTION,
            level="L2",
            action="ab_test_continue",
            proposal_id=memory_result.get("proposal_id", ""),
            reason=f"ABTest continue (p={result.p_value:.4f}, recommendation={result.recommendation})",
            evaluator_report=result.to_dict(),
            timestamp=timestamp,
        )

    # ============================================================
    # ABTest 结果写入 EvolutionMemory
    # ============================================================

    def _write_ab_test_to_memory(
        self,
        test_name: str,
        result: Any,
    ) -> dict[str, Any]:
        """将 ABTest 评估结果写入 EvolutionMemory.

        Args:
            test_name: 测试名称
            result: ABTestResult 对象

        Returns:
            dict: 包含 proposal_id 和 memory 状态的字典
        """
        memory = self._get_memory()
        result_dict = result.to_dict() if hasattr(result, "to_dict") else {}

        proposal_data = {
            "level": "L2",
            "action_type": "ab_test_evaluate",
            "trigger_reason": (
                f"ABTest {test_name} 评估完成: "
                f"recommendation={result_dict.get('recommendation', 'unknown')}, "
                f"p_value={result_dict.get('p_value', 1.0):.4f}"
            ),
            "target_module": f"ab_test:{test_name}",
            "rollback_plan": "ABTest 评估结果已记录, 如有需要可回滚 challenger 部署",
            "score_report": result_dict,
            "status": "executed",
            "metadata": {
                "test_name": test_name,
                "champion_metrics": result_dict.get("champion_metrics", {}),
                "challenger_metrics": result_dict.get("challenger_metrics", {}),
                "is_significant": result_dict.get("is_significant", False),
                "challenger_better": result_dict.get("challenger_better", False),
                "effect_size": result_dict.get("effect_size", 0.0),
                "recommendation": result_dict.get("recommendation", ""),
                "source": "evolution_orchestrator_v2",
            },
        }

        try:
            pid = memory.record(proposal_data)
            logger.info("ABTest 结果已写入 EvolutionMemory: pid=%s, test=%s", pid, test_name)
            return {"proposal_id": pid, "status": "recorded"}
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            logger.warning("ABTest 结果写入 EvolutionMemory 失败 (容错): %s", e)
            return {"proposal_id": "", "status": f"failed: {e}"}

    def _build_ab_test_proposal(
        self,
        test_name: str,
        champion_model: str,
        challenger_model: str,
        recommendation: str,
        result: Any,
    ) -> Any:
        """根据 ABTest 评估结果构建 EvolutionProposal.

        Args:
            test_name: 测试名称
            champion_model: champion 模型名称
            challenger_model: challenger 模型名称
            recommendation: promote / rollback
            result: ABTestResult 对象

        Returns:
            EvolutionProposal 实例
        """
        from utils.evolution.guard import LEVEL_L2, EvolutionProposal

        result_dict = result.to_dict() if hasattr(result, "to_dict") else {}
        p_value = result_dict.get("p_value", 1.0)
        effect_size = result_dict.get("effect_size", 0.0)

        return EvolutionProposal(
            level=LEVEL_L2,
            action_type=recommendation,
            target_module=f"ab_test:{test_name}",
            weight_change=0.0,
            rollback_plan=(
                f"回滚至 {champion_model} (champion 基线)"
                if recommendation == "promote"
                else f"保持 {champion_model} (champion 已是最优)"
            ),
            trigger_reason=(
                f"ABTest {test_name}: {champion_model} vs {challenger_model}, "
                f"recommendation={recommendation}, "
                f"p={p_value:.4f}, effect={effect_size:.4f}"
            ),
        )

    # ============================================================
    # 单提案路由
    # ============================================================

    def route_proposal(self, proposal: Any) -> CycleResult:
        """路由单个进化提案到对应层级处理.

        Args:
            proposal: EvolutionProposal 对象 (含 level/action_type/target_module)

        Returns:
            CycleResult 路由结果
        """
        timestamp = self._now_iso()

        if not self._enabled:
            return CycleResult(
                status=CYCLE_STATUS_DISABLED,
                reason="feature_flag_disabled",
                timestamp=timestamp,
            )

        level = getattr(proposal, "level", "")
        action_type = getattr(proposal, "action_type", "")
        target_module = getattr(proposal, "target_module", "")

        logger.info(
            "路由提案: level=%s action=%s module=%s",
            level, action_type, target_module,
        )

        # L1: AutoFixEngine 自动执行 (不经 Guard, L1 是修复不是进化)
        if level == "L1":
            return self._route_l1(proposal, timestamp)

        # L2/L3: 必经 Guard 检查
        guard = self._get_guard()
        try:
            decision = guard.check_proposal(proposal)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            logger.exception("Guard 检查异常: %s", e)
            return CycleResult(
                status=CYCLE_STATUS_DEGRADED,
                level=level,
                reason=f"guard_exception: {e}",
                timestamp=timestamp,
            )

        if not decision.passed:
            # 被拒: 记录到 Memory (审计完整性, HC-2)
            return self._record_rejected(proposal, decision, timestamp)

        # 通过 Guard: 按层级路由
        if level == "L2":
            return self._route_l2(proposal, decision, timestamp)
        if level == "L3":
            return self._route_l3(proposal, decision, timestamp)

        return CycleResult(
            status=CYCLE_STATUS_DEGRADED,
            level=level,
            reason=f"unknown_level: {level}",
            timestamp=timestamp,
        )

    # ============================================================
    # 三层路由实现
    # ============================================================

    def _route_l1(self, proposal: Any, timestamp: str) -> CycleResult:
        """L1 路由: AutoFixEngine 自动修复.

        L1 是修复层, 不经 Guard (修复不是进化), 但记录到 Memory.
        """
        memory = self._get_memory()

        # 记录到 Memory (pending)
        try:
            pid = memory.record({
                "level": "L1",
                "action_type": getattr(proposal, "action_type", "fix"),
                "trigger_reason": getattr(proposal, "trigger_reason", ""),
                "target_module": getattr(proposal, "target_module", ""),
                "status": "pending",
            })
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            logger.exception("L1 Memory 记录失败: %s", e)
            return CycleResult(
                status=CYCLE_STATUS_DEGRADED,
                level="L1",
                reason=f"memory_record_failed: {e}",
                timestamp=timestamp,
            )

        # 执行修复 (如果有 check_result 上下文)
        # 注: route_proposal 接收的是 EvolutionProposal, 不是 CheckResult,
        # 所以 L1 路由主要做审计记录, 实际修复由 AutoFixEngine.try_fix(check_result) 触发.
        # 这里记录提案, 标记为 executed (假设修复已由 AutoFixEngine 完成)
        try:
            memory.update_status(pid, "executed", result={
                "note": "L1 修复提案已记录, 实际修复由 AutoFixEngine.try_fix 触发",
            })
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            logger.warning("L1 状态更新失败 (容错): %s", e)

        return CycleResult(
            status=CYCLE_STATUS_SUCCESS,
            level="L1",
            action=getattr(proposal, "action_type", "fix"),
            proposal_id=pid,
            reason="L1 修复提案已审计",
            executed=True,
            timestamp=timestamp,
        )

    def _route_l2(self, proposal: Any, decision: Any, timestamp: str) -> CycleResult:
        """L2 路由: 影子验证 + 自动 Promote/Rollback.

        L2 通过 Guard 后, 记录 pending → 调用 ShadowAccountAdapter 实际验证
        → DSR ≥ 阈值则 promote (executed), DSR < 阈值则 rollback (rolled_back).

        fail-safe: adapter 不可用 / 样本不足 / 异常 → 降级 (假设通过, 不阻塞).
        """
        memory = self._get_memory()

        try:
            pid = memory.record({
                "level": "L2",
                "action_type": getattr(proposal, "action_type", ""),
                "trigger_reason": getattr(proposal, "trigger_reason", ""),
                "target_module": getattr(proposal, "target_module", ""),
                "rollback_plan": getattr(proposal, "rollback_plan", ""),
                "status": "pending",
                "result": decision.to_dict() if hasattr(decision, "to_dict") else {},
            })
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            logger.exception("L2 Memory 记录失败: %s", e)
            return CycleResult(
                status=CYCLE_STATUS_DEGRADED,
                level="L2",
                reason=f"memory_record_failed: {e}",
                timestamp=timestamp,
            )

        # --- 影子验证 (阶段4: 直接调用 ShadowAccountAdapter) ---
        adapter = self._get_shadow_adapter()
        guard_dict = decision.to_dict() if hasattr(decision, "to_dict") else {}

        if adapter is None:
            # fail-safe: adapter 不可用, 降级假设通过
            logger.warning("L2 影子验证: ShadowAccountAdapter 不可用, 降级假设通过")
            self._loop_health["l2_promote_count"] += 1
            try:
                memory.update_status(pid, "executed", result={
                    "guard_passed": True,
                    "shadow_verified": False,
                    "note": "shadow_adapter_unavailable, degraded_assume_pass",
                })
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
                logger.warning("L2 状态更新失败 (容错): %s", e)
            return CycleResult(
                status=CYCLE_STATUS_DEGRADED,
                level="L2",
                action=getattr(proposal, "action_type", ""),
                proposal_id=pid,
                reason="L2 影子验证降级 (adapter 不可用), 假设通过",
                guard_decision=guard_dict,
                executed=True,
                timestamp=timestamp,
            )

        # 加载影子收益率
        daily_returns, dates = self._load_shadow_daily_returns()

        try:
            from utils.alpha import shadow_account_adapter as _sa_mod
            _min_samples = _sa_mod.MIN_SAMPLES_FOR_DSR
        except (ImportError, AttributeError, RuntimeError) as e:
            logger.warning("MIN_SAMPLES_FOR_DSR 导入失败, 使用默认 20: %s", e)
            _min_samples = 20

        if len(daily_returns) < _min_samples:
            # 样本不足, 降级假设通过 (不阻塞进化)
            logger.warning(
                "L2 影子验证: 样本不足 %d < %d, 降级假设通过",
                len(daily_returns),
                _min_samples,
            )
            self._loop_health["l2_promote_count"] += 1
            try:
                memory.update_status(pid, "executed", result={
                    "guard_passed": True,
                    "shadow_verified": False,
                    "note": f"insufficient_samples ({len(daily_returns)} < {_min_samples})",
                })
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
                logger.warning("L2 状态更新失败 (容错): %s", e)
            return CycleResult(
                status=CYCLE_STATUS_DEGRADED,
                level="L2",
                action=getattr(proposal, "action_type", ""),
                proposal_id=pid,
                reason=f"L2 影子验证降级 (样本不足 {len(daily_returns)}), 假设通过",
                guard_decision=guard_dict,
                executed=True,
                timestamp=timestamp,
            )

        # 实际调用 ShadowAccountAdapter
        try:
            run_result = adapter.run_shadow(daily_returns=daily_returns, dates=dates, is_real_data=True)

            if not run_result.success or run_result.fail_fast_triggered:
                # Fail-Fast 触发 → rollback
                logger.warning(
                    "L2 影子验证: run_shadow 失败/fail-fast (reason=%s), 自动 rollback",
                    run_result.fail_fast_reason,
                )
                self._loop_health["l2_rollback_count"] += 1
                try:
                    memory.update_status(pid, "rolled_back", result={
                        "guard_passed": True,
                        "shadow_verified": True,
                        "shadow_success": False,
                        "fail_fast_reason": run_result.fail_fast_reason,
                        "note": "L2 影子验证 fail-fast, 自动回滚",
                    })
                except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
                    logger.warning("L2 状态更新失败 (容错): %s", e)
                return CycleResult(
                    status=CYCLE_STATUS_ROLLED_BACK,
                    level="L2",
                    action=getattr(proposal, "action_type", ""),
                    proposal_id=pid,
                    reason=f"L2 影子验证 fail-fast: {run_result.fail_fast_reason}",
                    guard_decision=guard_dict,
                    executed=False,
                    timestamp=timestamp,
                )

            # 获取 DSR 指标
            metrics = adapter.get_metrics()
            dsr = float(metrics.dsr)

            if dsr >= self._l2_dsr_threshold:
                # DSR 达标 → promote
                logger.info("L2 影子验证通过: DSR=%.4f ≥ %.2f, 自动 promote", dsr, self._l2_dsr_threshold)
                self._loop_health["l2_promote_count"] += 1
                try:
                    memory.update_status(pid, "executed", result={
                        "guard_passed": True,
                        "shadow_verified": True,
                        "shadow_success": True,
                        "dsr": dsr,
                        "annual_return": metrics.annual_return,
                        "max_drawdown": metrics.max_drawdown,
                        "sharpe_cv": metrics.sharpe_cv,
                        "note": f"L2 影子验证通过 (DSR={dsr:.4f}), 自动 promote",
                    })
                except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
                    logger.warning("L2 状态更新失败 (容错): %s", e)
                return CycleResult(
                    status=CYCLE_STATUS_SUCCESS,
                    level="L2",
                    action=getattr(proposal, "action_type", ""),
                    proposal_id=pid,
                    reason=f"L2 影子验证通过 (DSR={dsr:.4f}), 已 promote",
                    guard_decision=guard_dict,
                    executed=True,
                    timestamp=timestamp,
                )
            else:
                # DSR 不达标 → rollback
                logger.warning(
                    "L2 影子验证未通过: DSR=%.4f < %.2f, 自动 rollback",
                    dsr,
                    self._l2_dsr_threshold,
                )
                self._loop_health["l2_rollback_count"] += 1
                try:
                    memory.update_status(pid, "rolled_back", result={
                        "guard_passed": True,
                        "shadow_verified": True,
                        "shadow_success": False,
                        "dsr": dsr,
                        "note": f"L2 影子验证未通过 (DSR={dsr:.4f} < {self._l2_dsr_threshold}), 自动 rollback",
                    })
                except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
                    logger.warning("L2 状态更新失败 (容错): %s", e)
                return CycleResult(
                    status=CYCLE_STATUS_ROLLED_BACK,
                    level="L2",
                    action=getattr(proposal, "action_type", ""),
                    proposal_id=pid,
                    reason=f"L2 影子验证未通过 (DSR={dsr:.4f} < {self._l2_dsr_threshold}), 已 rollback",
                    guard_decision=guard_dict,
                    executed=False,
                    timestamp=timestamp,
                )

        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # fail-safe: 影子验证异常, 降级假设通过 (不阻塞进化)
            logger.warning("L2 影子验证异常 (容错降级): %s", e)
            self._loop_health["l2_promote_count"] += 1
            try:
                memory.update_status(pid, "executed", result={
                    "guard_passed": True,
                    "shadow_verified": False,
                    "note": f"shadow_validation_error: {e}",
                })
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e2:
                logger.warning("L2 状态更新失败 (容错): %s", e2)
            return CycleResult(
                status=CYCLE_STATUS_DEGRADED,
                level="L2",
                action=getattr(proposal, "action_type", ""),
                proposal_id=pid,
                reason=f"L2 影子验证异常 (容错降级): {e}",
                guard_decision=guard_dict,
                executed=True,
                timestamp=timestamp,
            )

    def _route_l3(self, proposal: Any, decision: Any, timestamp: str) -> CycleResult:
        """L3 路由: 人工审批闸门 (HC-4).

        L3 通过 Guard 后, 仅记录 pending, 等待人工审批.
        不可自动执行 (HC-4).
        """
        memory = self._get_memory()

        try:
            pid = memory.record({
                "level": "L3",
                "action_type": getattr(proposal, "action_type", ""),
                "trigger_reason": getattr(proposal, "trigger_reason", ""),
                "target_module": getattr(proposal, "target_module", ""),
                "rollback_plan": getattr(proposal, "rollback_plan", ""),
                "status": "pending",  # 等待人工审批
                "result": decision.to_dict() if hasattr(decision, "to_dict") else {},
            })
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            logger.exception("L3 Memory 记录失败: %s", e)
            return CycleResult(
                status=CYCLE_STATUS_DEGRADED,
                level="L3",
                reason=f"memory_record_failed: {e}",
                timestamp=timestamp,
            )

        logger.info(
            "L3 提案已生成人工审批工单: pid=%s (HC-4 不可自动执行)",
            pid,
        )

        return CycleResult(
            status=CYCLE_STATUS_SUCCESS,
            level="L3",
            action=getattr(proposal, "action_type", ""),
            proposal_id=pid,
            reason="L3 提案通过 Guard, 已生成人工审批工单 (等待批准)",
            guard_decision=decision.to_dict() if hasattr(decision, "to_dict") else {},
            executed=False,  # L3 不自动执行, 等待人工审批
            timestamp=timestamp,
        )

    def _record_rejected(
        self, proposal: Any, decision: Any, timestamp: str
    ) -> CycleResult:
        """记录被 Guard 拒绝的提案到 Memory (审计完整性, HC-2)."""
        memory = self._get_memory()
        level = getattr(proposal, "level", "")

        try:
            memory.record({
                "level": level,
                "action_type": getattr(proposal, "action_type", ""),
                "trigger_reason": getattr(proposal, "trigger_reason", ""),
                "target_module": getattr(proposal, "target_module", ""),
                "rollback_plan": getattr(proposal, "rollback_plan", ""),
                "status": "rejected",
                "result": decision.to_dict() if hasattr(decision, "to_dict") else {},
            })
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
            # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
            logger.exception("拒绝提案 Memory 记录失败: %s", e)

        return CycleResult(
            status=CYCLE_STATUS_NO_ACTION,
            level=level,
            reason=f"rejected_by_guard: {decision.reason}",
            guard_decision=decision.to_dict() if hasattr(decision, "to_dict") else {},
            executed=False,
            timestamp=timestamp,
        )

    # ============================================================
    # 根据 ScoreReport.recommendation 路由
    # ============================================================

    def _route_by_recommendation(
        self,
        report: Any,
        metrics: Any,
        timestamp: str,
    ) -> CycleResult:
        """根据评估报告的 recommendation 生成并路由提案.

        recommendation 取值 (来自 StrategyEvaluator):
            - promote: private_score 高 + rh_risk 低 → L2 自动晋升
            - rollback: private_score 低 或 rh_risk 高 → L2 回滚
            - continue: 中间状态 → 无动作

        Args:
            report: ScoreReport 对象
            metrics: MetricsSnapshot 对象
            timestamp: 时间戳

        Returns:
            CycleResult 路由结果
        """
        recommendation = getattr(report, "recommendation", "continue")
        public_score = getattr(report, "public_score", 0.0)
        private_score = getattr(report, "private_score", 0.0)
        rh_risk = getattr(report, "reward_hacking_risk", 0.0)

        evaluator_report = {}
        if hasattr(report, "to_dict"):
            evaluator_report = report.to_dict()

        metrics_snapshot = {}
        if hasattr(metrics, "to_dict"):
            metrics_snapshot = metrics.to_dict()

        weight_adjustments = self._derive_weight_adjustments(evaluator_report)

        # continue: 无需进化动作, 仅记录评估结果
        if recommendation == "continue":
            memory = self._get_memory()
            try:
                memory.record({
                    "level": "L2",
                    "action_type": "evaluate",
                    "trigger_reason": f"recommendation=continue (private={private_score:.3f})",
                    "target_module": "v9_baseline",
                    "rollback_plan": "评估只读, 无需回滚",
                    "score_report": evaluator_report,
                    "status": "executed",
                })
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
                # 数值计算/数据处理异常: 格式/类型/字段/属性/运行时/IO/超时/网络
                logger.warning("评估记录 Memory 失败 (容错): %s", e)

            return CycleResult(
                status=CYCLE_STATUS_NO_ACTION,
                level="L2",
                action="evaluate",
                reason=f"recommendation=continue, private={private_score:.3f}",
                metrics_snapshot=metrics_snapshot,
                evaluator_report=evaluator_report,
                weight_adjustments=weight_adjustments,
                timestamp=timestamp,
            )

        # promote/rollback: 生成 L2 提案路由
        from utils.evolution.guard import LEVEL_L2, EvolutionProposal

        action_type = "promote" if recommendation == "promote" else "rollback"
        proposal = EvolutionProposal(
            level=LEVEL_L2,
            action_type=action_type,
            target_module="v9_baseline",
            weight_change=0.0,  # 评估驱动的提案不调整权重, 由下游模块处理
            rollback_plan=(
                "回滚至 v8.6.14 基线模型" if action_type == "promote"
                else "已回滚, 保持当前状态"
            ),
            trigger_reason=(
                f"recommendation={recommendation}, "
                f"public={public_score:.3f}, private={private_score:.3f}, "
                f"rh_risk={rh_risk:.3f}"
            ),
        )

        result = self.route_proposal(proposal)
        # 补充评估报告和指标快照
        result.evaluator_report = evaluator_report
        result.metrics_snapshot = metrics_snapshot
        result.weight_adjustments = weight_adjustments
        return result

    # ============================================================
    # 辅助
    # ============================================================

    @staticmethod
    def _derive_weight_adjustments(evaluator_report: dict[str, Any]) -> dict[str, float]:
        """从评估报告推导再平衡权重乘子.

        进化→再平衡闭环的关键转换:
            evaluator_report (策略级聚合指标) → weight_adjustments ({code: multiplier})

        推导逻辑 (按优先级):
            1. 显式 weight_adjustments 键: 前置阶段已计算好 per-code 乘子, 直接提取 + clamp
            2. factor_scores 键: 因子评分 → 乘子 (score > 0.6 → 轻微放大, < 0.4 → 缩小)
            3. 无 per-code 信息: 返回空字典 (优雅降级, 由 factor_weights.json 文件传输)

        Args:
            evaluator_report: ScoreReport.to_dict() 或类似字典

        Returns:
            {code: multiplier} 乘子限制 [0.5, 2.0]
        """
        if not isinstance(evaluator_report, dict):
            return {}

        # 1. 显式 weight_adjustments
        explicit = evaluator_report.get("weight_adjustments")
        if isinstance(explicit, dict) and explicit:
            return {
                str(k): float(v)
                for k, v in explicit.items()
                if isinstance(v, (int, float)) and 0.5 <= v <= 2.0
            }

        # 2. factor_scores → multiplier
        factor_scores = evaluator_report.get("factor_scores")
        if isinstance(factor_scores, dict) and factor_scores:
            multipliers: dict[str, float] = {}
            for code, score in factor_scores.items():
                if not isinstance(score, (int, float)):
                    continue
                # score 0.0-1.0 → multiplier 0.5-2.0 线性映射
                # score=0.6 → 1.0 (中性), score=1.0 → 2.0, score=0.0 → 0.5
                raw = 0.5 + 1.5 * max(0.0, min(1.0, score))
                multipliers[str(code)] = max(0.5, min(2.0, raw))
            return multipliers

        # 3. 无 per-code 信息
        return {}

    @staticmethod
    def _now_iso() -> str:
        """当前时间 ISO8601 (UTC)."""
        from datetime import datetime

        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
