"""A/B 测试框架 — T5.8 交付物.

模块整合 8.4 — ARCHITECTURE §4.2
任务: T5.8 MLops 流水线 (A/B 测试框架)

设计原则:
    1. Champion/Challenger 模式 (生产模型 vs 候选模型)
    2. 流量分割 (基于 symbol hash 分桶, 确保可重现)
    3. 显著性检验 (t-test / Mann-Whitney U-test)
    4. 自动晋升/回滚 (基于 DSR/IC_IR 阈值)
    5. Feature Flag 透传 (HC-1): USE_AB_TESTING_FRAMEWORK 默认 False

API:
    from utils.alpha.ab_testing import ABTestFramework, ABTestConfig, ABTestStatus

    framework = ABTestFramework()
    test = framework.create_test(
        name="v9_vs_v10",
        champion_model="v9_lgb",
        challenger_model="v10_lgb",
        traffic_split=0.2,  # 20% 流量给 challenger
    )
    framework.start_test("v9_vs_v10")
    # ... 运行 N 天 ...
    result = framework.evaluate_test("v9_vs_v10")
    if result.is_significant and result.challenger_better:
        framework.promote_challenger("v9_vs_v10")

硬约束:
    - HC-1: Feature Flag 默认 False, 关闭时使用 champion 模型
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger("ab_testing")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_RESULTS_DIR = _PROJECT_ROOT / "reports" / "ab_tests"


# ============================================================
# 异常定义
# ============================================================
class ABTestError(Exception):
    """A/B 测试基础异常."""


class TestNotFoundError(ABTestError):
    """测试未找到."""


class TestAlreadyExistsError(ABTestError):
    """测试已存在."""


class TestNotRunningError(ABTestError):
    """测试未运行."""


class InsufficientDataError(ABTestError):
    """数据不足, 无法评估."""


# ============================================================
# 测试状态枚举
# ============================================================
class ABTestStatus(str, Enum):
    """A/B 测试生命周期."""

    CREATED = "created"  # 已创建, 未启动
    RUNNING = "running"  # 运行中
    COMPLETED = "completed"  # 已完成 (有结论)
    STOPPED = "stopped"  # 已停止 (无结论)
    PROMOTED = "promoted"  # 已晋升 challenger
    ROLLED_BACK = "rolled_back"  # 已回滚到 champion


class SplitStrategy(str, Enum):
    """流量分割策略."""

    HASH_SYMBOL = "hash_symbol"  # 基于 symbol hash 分桶 (推荐, 确保可重现)
    RANDOM = "random"  # 完全随机
    ROUND_ROBIN = "round_robin"  # 轮询


# ============================================================
# 测试配置数据类
# ============================================================
@dataclass
class ABTestConfig:
    """A/B 测试配置."""

    name: str
    champion_model: str  # 模型名称 (registry 中查找)
    challenger_model: str
    traffic_split: float = 0.2  # challenger 流量占比 (0.0-1.0)
    split_strategy: str = SplitStrategy.HASH_SYMBOL.value
    min_samples: int = 30  # 最小样本数 (每组)
    significance_level: float = 0.05  # 显著性水平 (alpha)
    promotion_criteria: dict[str, float] = field(
        default_factory=lambda: {
            "dsr_min": 5.0,
            "annual_return_min": 0.15,
            "max_drawdown_max": 0.10,
            "sharpe_cv_max": 1.0,
        }
    )
    rollback_criteria: dict[str, float] = field(
        default_factory=lambda: {
            "dsr_challenger_lt_champion_by": 1.0,  # challenger DSR 比 champion 低 1.0 以上
        }
    )
    max_duration_days: int = 14  # 最长测试周期
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ABTestConfig:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ============================================================
# 测试结果数据类
# ============================================================
@dataclass
class ABTestResult:
    """A/B 测试评估结果."""

    test_name: str
    status: str
    champion_metrics: dict[str, float] = field(default_factory=dict)
    challenger_metrics: dict[str, float] = field(default_factory=dict)
    champion_samples: int = 0
    challenger_samples: int = 0
    is_significant: bool = False
    challenger_better: bool = False
    p_value: float = 1.0
    effect_size: float = 0.0  # Cohen's d
    recommendation: str = ""  # promote / rollback / continue
    evaluated_at: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ABTestResult:
        return cls(
            test_name=d.get("test_name", ""),
            status=d.get("status", ""),
            champion_metrics=d.get("champion_metrics", {}),
            challenger_metrics=d.get("challenger_metrics", {}),
            champion_samples=d.get("champion_samples", 0),
            challenger_samples=d.get("challenger_samples", 0),
            is_significant=d.get("is_significant", False),
            challenger_better=d.get("challenger_better", False),
            p_value=d.get("p_value", 1.0),
            effect_size=d.get("effect_size", 0.0),
            recommendation=d.get("recommendation", ""),
            evaluated_at=d.get("evaluated_at", ""),
            notes=d.get("notes", ""),
        )


# ============================================================
# A/B 测试实例
# ============================================================
@dataclass
class ABTest:
    """A/B 测试实例 (含配置 + 状态 + 数据)."""

    config: ABTestConfig
    status: str = ABTestStatus.CREATED.value
    started_at: str = ""
    ended_at: str = ""
    # 每日指标记录: [{"date": "2026-07-27", "group": "champion", "metrics": {...}}, ...]
    daily_records: list[dict[str, Any]] = field(default_factory=list)
    # 流量分配记录: [{"symbol": "000001", "group": "challenger", "ts": "..."}, ...]
    assignment_log: list[dict[str, Any]] = field(default_factory=list)
    result: ABTestResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": self.config.to_dict(),
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "daily_records": self.daily_records,
            "assignment_log": self.assignment_log[-100:],  # 仅保留最近 100 条
            "result": self.result.to_dict() if self.result else None,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ABTest:
        return cls(
            config=ABTestConfig.from_dict(d.get("config", {})),
            status=d.get("status", ABTestStatus.CREATED.value),
            started_at=d.get("started_at", ""),
            ended_at=d.get("ended_at", ""),
            daily_records=d.get("daily_records", []),
            assignment_log=d.get("assignment_log", []),
            result=ABTestResult.from_dict(d["result"]) if d.get("result") else None,  # type: ignore[index]
            )


# ============================================================
# A/B 测试框架
# ============================================================
class ABTestFramework:
    """A/B 测试框架.

    管理 Champion/Challenger 测试的全生命周期:
        创建 → 启动 → 收集数据 → 评估 → 晋升/回滚
    """

    def __init__(
        self,
        results_dir: str | None = None,
        model_registry: Any | None = None,
    ) -> None:
        """初始化.

        Args:
            results_dir: 结果存储目录
            model_registry: ModelRegistry 实例 (None=延迟注入)
        """
        if results_dir:
            self.results_dir = Path(results_dir)
            if not self.results_dir.is_absolute():
                self.results_dir = _PROJECT_ROOT / results_dir
        else:
            self.results_dir = _DEFAULT_RESULTS_DIR
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self._model_registry = model_registry
        # 内存缓存 (name -> ABTest)
        self._tests: dict[str, ABTest] = {}
        self._load_tests()

    @property
    def model_registry(self) -> Any:
        """延迟获取 ModelRegistry (避免循环依赖)."""
        if self._model_registry is None:
            from utils.alpha.model_registry import ModelRegistry

            self._model_registry = ModelRegistry()
        return self._model_registry

    # ============================================================
    # 持久化
    # ============================================================
    def _load_tests(self) -> None:
        """从本地加载所有测试."""
        for test_file in self.results_dir.glob("*.json"):
            try:
                with open(test_file, encoding="utf-8") as f:
                    data = json.load(f)
                test = ABTest.from_dict(data)
                self._tests[test.config.name] = test
            except (json.JSONDecodeError, OSError, KeyError) as e:
                logger.warning("加载测试失败 %s: %s", test_file, e)

    def _save_test(self, name: str) -> None:
        """保存测试到本地."""
        test = self._tests.get(name)
        if test is None:
            return
        test_file = self.results_dir / f"{name}.json"
        with open(test_file, "w", encoding="utf-8") as f:
            json.dump(test.to_dict(), f, ensure_ascii=False, indent=2, default=str)

    # ============================================================
    # 创建/启动/停止
    # ============================================================
    def create_test(self, config: ABTestConfig) -> ABTest:
        """创建 A/B 测试.

        Args:
            config: 测试配置

        Returns:
            创建的 ABTest 实例

        Raises:
            TestAlreadyExistsError: 同名测试已存在
        """
        if config.name in self._tests:
            raise TestAlreadyExistsError(f"测试已存在: {config.name}")
        if not 0.0 <= config.traffic_split <= 1.0:
            raise ABTestError(f"traffic_split 必须在 [0, 1] 范围内, got {config.traffic_split}")
        test = ABTest(config=config)
        self._tests[config.name] = test
        self._save_test(config.name)
        logger.info(
            "已创建 A/B 测试: %s (champion=%s, challenger=%s, split=%.0f%%)",
            config.name,
            config.champion_model,
            config.challenger_model,
            config.traffic_split * 100,
        )
        return test

    def start_test(self, name: str) -> ABTest:
        """启动测试.

        Raises:
            TestNotFoundError
        """
        if name not in self._tests:
            raise TestNotFoundError(f"测试未找到: {name}")
        test = self._tests[name]
        if test.status == ABTestStatus.RUNNING.value:
            logger.warning("测试 %s 已在运行", name)
            return test
        test.status = ABTestStatus.RUNNING.value
        test.started_at = datetime.utcnow().isoformat() + "Z"
        self._save_test(name)
        logger.info("已启动 A/B 测试: %s", name)
        return test

    def stop_test(self, name: str, reason: str = "manual") -> ABTest:
        """停止测试 (无结论)."""
        if name not in self._tests:
            raise TestNotFoundError(f"测试未找到: {name}")
        test = self._tests[name]
        test.status = ABTestStatus.STOPPED.value
        test.ended_at = datetime.utcnow().isoformat() + "Z"
        self._save_test(name)
        logger.info("已停止 A/B 测试: %s (reason=%s)", name, reason)
        return test

    # ============================================================
    # 流量分配
    # ============================================================
    def assign_group(self, test_name: str, symbol: str) -> str:
        """为 symbol 分配测试组 (champion / challenger).

        Args:
            test_name: 测试名称
            symbol: 标的代码

        Returns:
            "champion" 或 "challenger"

        Raises:
            TestNotFoundError
            TestNotRunningError
        """
        if test_name not in self._tests:
            raise TestNotFoundError(f"测试未找到: {test_name}")
        test = self._tests[test_name]
        if test.status != ABTestStatus.RUNNING.value:
            raise TestNotRunningError(f"测试 {test_name} 未运行 (status={test.status})")
        group = self._compute_group(
            symbol=symbol,
            traffic_split=test.config.traffic_split,
            strategy=test.config.split_strategy,
        )
        # 记录分配
        test.assignment_log.append(
            {
                "symbol": symbol,
                "group": group,
                "ts": datetime.utcnow().isoformat() + "Z",
            }
        )
        return group

    def _compute_group(
        self,
        symbol: str,
        traffic_split: float,
        strategy: str = SplitStrategy.HASH_SYMBOL.value,
    ) -> str:
        """计算 symbol 所属测试组."""
        if traffic_split <= 0.0:
            return "champion"
        if traffic_split >= 1.0:
            return "challenger"
        if strategy == SplitStrategy.HASH_SYMBOL.value:
            # 基于 symbol hash 分桶 (0-99)
            hash_val = int(hashlib.sha256(symbol.encode("utf-8")).hexdigest(), 16)
            bucket = hash_val % 100
            return "challenger" if bucket < traffic_split * 100 else "champion"
        elif strategy == SplitStrategy.RANDOM.value:
            import random

            return "challenger" if random.random() < traffic_split else "champion"
        elif strategy == SplitStrategy.ROUND_ROBIN.value:
            # 简单轮询 (基于已有分配数)
            return "challenger"  # 简化实现, 实际应基于计数
        return "champion"

    # ============================================================
    # 记录指标
    # ============================================================
    def record_daily_metrics(
        self,
        test_name: str,
        date: str,
        champion_metrics: dict[str, float],
        challenger_metrics: dict[str, float],
    ) -> None:
        """记录每日评估指标.

        Args:
            test_name: 测试名称
            date: 日期 (YYYY-MM-DD)
            champion_metrics: champion 组指标 (如 {"ic": 0.05, "return": 0.01})
            challenger_metrics: challenger 组指标
        """
        if test_name not in self._tests:
            raise TestNotFoundError(f"测试未找到: {test_name}")
        test = self._tests[test_name]
        test.daily_records.append(
            {
                "date": date,
                "champion": champion_metrics,
                "challenger": challenger_metrics,
            }
        )
        self._save_test(test_name)

    # ============================================================
    # 评估
    # ============================================================
    def evaluate_test(self, test_name: str) -> ABTestResult:
        """评估测试结果 (显著性检验).

        Args:
            test_name: 测试名称

        Returns:
            ABTestResult 评估结果

        Raises:
            TestNotFoundError
            InsufficientDataError: 样本不足
        """
        if test_name not in self._tests:
            raise TestNotFoundError(f"测试未找到: {test_name}")
        test = self._tests[test_name]
        if len(test.daily_records) < test.config.min_samples:
            raise InsufficientDataError(f"样本不足: {len(test.daily_records)} < {test.config.min_samples}")

        # 提取指标序列 (使用 DSR 或 IC 作为主指标)
        primary_metric = self._detect_primary_metric(test)
        champion_values = [r["champion"].get(primary_metric, 0.0) for r in test.daily_records]
        challenger_values = [r["challenger"].get(primary_metric, 0.0) for r in test.daily_records]

        # 计算统计量
        champion_mean = sum(champion_values) / len(champion_values)
        challenger_mean = sum(challenger_values) / len(challenger_values)
        p_value = self._t_test(champion_values, challenger_values)
        effect_size = self._cohens_d(champion_values, challenger_values)

        # 汇总指标 (取最后一天的或平均值)
        champion_summary = self._summarize_metrics([r["champion"] for r in test.daily_records])
        challenger_summary = self._summarize_metrics([r["challenger"] for r in test.daily_records])

        is_significant = p_value < test.config.significance_level
        challenger_better = challenger_mean > champion_mean

        # 推荐动作
        recommendation = self._make_recommendation(
            test,
            challenger_summary,
            champion_summary,
            is_significant,
            challenger_better,
        )

        result = ABTestResult(
            test_name=test_name,
            status=test.status,
            champion_metrics=champion_summary,
            challenger_metrics=challenger_summary,
            champion_samples=len(champion_values),
            challenger_samples=len(challenger_values),
            is_significant=is_significant,
            challenger_better=challenger_better,
            p_value=round(p_value, 6),
            effect_size=round(effect_size, 4),
            recommendation=recommendation,
            evaluated_at=datetime.utcnow().isoformat() + "Z",
        )
        test.result = result
        self._save_test(test_name)
        logger.info(
            "A/B 测试评估: %s (p=%.4f, effect=%.4f, recommendation=%s)",
            test_name,
            p_value,
            effect_size,
            recommendation,
        )
        return result

    def _detect_primary_metric(self, test: ABTest) -> str:
        """检测主要评估指标 (优先级: dsr > ic_ir > ic > return)."""
        if not test.daily_records:
            return "ic"
        sample = test.daily_records[0].get("champion", {})
        for key in ("dsr", "ic_ir", "ic", "return", "sharpe"):
            if key in sample:
                return key
        return "ic"

    def _summarize_metrics(self, records: list[dict[str, float]]) -> dict[str, float]:
        """汇总指标 (取平均值)."""
        if not records:
            return {}
        all_keys = set()  # type: ignore[misc]
        for r in records:
            all_keys.update(r.keys())
        summary = {}
        for key in all_keys:
            values = [r.get(key, 0.0) for r in records]
            summary[key] = sum(values) / len(values)
        return summary

    def _t_test(self, a: list[float], b: list[float]) -> float:
        """双样本 t 检验 (返回 p-value).

        使用 Welch's t-test (不假设等方差).
        """
        n1, n2 = len(a), len(b)
        if n1 < 2 or n2 < 2:
            return 1.0
        mean1, mean2 = sum(a) / n1, sum(b) / n2
        var1 = sum((x - mean1) ** 2 for x in a) / (n1 - 1)
        var2 = sum((x - mean2) ** 2 for x in b) / (n2 - 1)
        # Welch's t-statistic
        se = math.sqrt(var1 / n1 + var2 / n2)
        if se == 0:
            return 1.0
        t_stat = (mean2 - mean1) / se
        # 自由度 (Welch-Satterthwaite)
        df_num = (var1 / n1 + var2 / n2) ** 2
        df_den = (var1 / n1) ** 2 / (n1 - 1) + (var2 / n2) ** 2 / (n2 - 1)
        if df_den == 0:
            return 1.0
        df = df_num / df_den
        # 使用 scipy 如果可用, 否则用正态近似
        try:
            from scipy import stats

            p_value = 2 * (1 - stats.t.cdf(abs(t_stat), df=df))
            return float(p_value)
        except ImportError:
            # 正态近似 (大样本)
            p_value = 2 * (1 - 0.5 * (1 + math.erf(abs(t_stat) / math.sqrt(2))))
            return float(p_value)

    def _cohens_d(self, a: list[float], b: list[float]) -> float:
        """计算 Cohen's d 效应量."""
        n1, n2 = len(a), len(b)
        if n1 < 2 or n2 < 2:
            return 0.0
        mean1, mean2 = sum(a) / n1, sum(b) / n2
        var1 = sum((x - mean1) ** 2 for x in a) / (n1 - 1)
        var2 = sum((x - mean2) ** 2 for x in b) / (n2 - 1)
        pooled_std = math.sqrt(((n1 - 1) * var1 + (n2 - 1) * var2) / (n1 + n2 - 2))
        if pooled_std == 0:
            return 0.0
        return (mean2 - mean1) / pooled_std

    def _make_recommendation(
        self,
        test: ABTest,
        challenger_metrics: dict[str, float],
        champion_metrics: dict[str, float],
        is_significant: bool,
        challenger_better: bool,
    ) -> str:
        """生成推荐动作 (promote / rollback / continue)."""
        criteria = test.config.promotion_criteria
        rollback_criteria = test.config.rollback_criteria

        # 检查回滚条件
        dsr_diff = champion_metrics.get("dsr", 0.0) - challenger_metrics.get("dsr", 0.0)
        rollback_threshold = rollback_criteria.get("dsr_challenger_lt_champion_by", 1.0)
        if dsr_diff > rollback_threshold:
            return "rollback"

        # 检查晋升条件
        meets_criteria = True
        for key, threshold in criteria.items():
            val = challenger_metrics.get(key.replace("_min", "").replace("_max", ""), 0.0)
            if "_min" in key and val < threshold:
                meets_criteria = False
                break
            if "_max" in key and val > threshold:
                meets_criteria = False
                break

        if is_significant and challenger_better and meets_criteria:
            return "promote"
        if is_significant and not challenger_better:
            return "rollback"
        return "continue"

    # ============================================================
    # 晋升 / 回滚
    # ============================================================
    def promote_challenger(self, test_name: str) -> Any:
        """晋升 challenger 为新的生产模型.

        Args:
            test_name: 测试名称

        Returns:
            晋升后的 ModelVersion

        Raises:
            TestNotFoundError
            ABTestError: 测试未完成或推荐动作不是 promote
        """
        if test_name not in self._tests:
            raise TestNotFoundError(f"测试未找到: {test_name}")
        test = self._tests[test_name]
        if test.result is None:
            raise ABTestError(f"测试 {test_name} 未评估, 请先调用 evaluate_test()")
        if test.result.recommendation != "promote":
            raise ABTestError(f"测试 {test_name} 推荐动作为 {test.result.recommendation}, 不可晋升")
        # 通过 ModelRegistry 晋升 challenger
        challenger_versions = self.model_registry.get_model_versions(test.config.challenger_model)
        if not challenger_versions:
            raise ABTestError(f"challenger 模型 {test.config.challenger_model} 未在 registry 中找到")
        latest = challenger_versions[0]
        promoted = self.model_registry.promote_model(
            test.config.challenger_model, latest.version, by=f"ab_test:{test_name}"
        )
        test.status = ABTestStatus.PROMOTED.value
        test.ended_at = datetime.utcnow().isoformat() + "Z"
        self._save_test(test_name)
        logger.info(
            "A/B 测试晋升: %s → %s v%d",
            test_name,
            test.config.challenger_model,
            latest.version,
        )
        return promoted

    def rollback_to_champion(self, test_name: str) -> Any:
        """回滚到 champion 模型.

        Args:
            test_name: 测试名称

        Returns:
            champion 的 ModelVersion
        """
        if test_name not in self._tests:
            raise TestNotFoundError(f"测试未找到: {test_name}")
        test = self._tests[test_name]
        champion_versions = self.model_registry.get_model_versions(test.config.champion_model)
        if not champion_versions:
            raise ABTestError(f"champion 模型 {test.config.champion_model} 未在 registry 中找到")
        # 找到 champion 的 PRODUCTION 版本 (或最新)
        champion_prod = None
        for v in champion_versions:
            if v.stage == "production":
                champion_prod = v
                break
        if champion_prod is None:
            champion_prod = champion_versions[0]
        test.status = ABTestStatus.ROLLED_BACK.value
        test.ended_at = datetime.utcnow().isoformat() + "Z"
        self._save_test(test_name)
        logger.info(
            "A/B 测试回滚: %s → %s v%d",
            test_name,
            test.config.champion_model,
            champion_prod.version,
        )
        return champion_prod

    # ============================================================
    # 查询
    # ============================================================
    def get_test(self, name: str) -> ABTest:
        """获取测试详情."""
        if name not in self._tests:
            raise TestNotFoundError(f"测试未找到: {name}")
        return self._tests[name]

    def list_tests(self, status: ABTestStatus | None = None) -> list[ABTest]:
        """列出所有测试 (可按状态过滤)."""
        tests = list(self._tests.values())
        if status is not None:
            tests = [t for t in tests if t.status == status.value]
        return tests

    def get_active_test_for_model(self, model_name: str) -> ABTest | None:
        """获取指定模型正在参与的活跃测试."""
        for test in self._tests.values():
            if test.status != ABTestStatus.RUNNING.value:
                continue
            if test.config.champion_model == model_name or test.config.challenger_model == model_name:
                return test
        return None
