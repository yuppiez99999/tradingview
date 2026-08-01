"""统一健康度度量框架 — 三层面自我进化 Stage 1 核心.

模块整合 8.4 — ARCHITECTURE_三层面进化 §第1阶段
基于: ARCHITECTURE_自我进化框架.md (策略层 AIDE² 双层循环)

设计原则:
    1. 不可变性: LayerScore / HealthReport 使用 frozen=True (HC 不可变性原则)
    2. Feature Flag 透传 (HC-1): USE_UNIFIED_HEALTH_METRICS 默认 False
    3. 配置走 ConfigManager (HC-5): get_config("evolution") 4 级优先级
    4. 只读生产数据: 不修改 positions.json / daily_returns.jsonl
    5. 容错降级: 采集器失败不阻塞, 返回 is_degraded=True 的报告

三层面权重 (默认, 走 evolution.yaml):
    H = 0.25·CodeScore + 0.50·StrategyScore + 0.25·OpsScore
    - 降级层排除后重新归一化 (不拉低总分)
    - 全部降级时 overall_score=0 + is_degraded=True

持久化:
    reports/evolution/health_trend.jsonl (追加模式, 每行一个 HealthReport)
    保留最近 90 天历史 (可配置)

用法:
    from utils.alpha.health_metrics import UnifiedHealthMetrics, HealthReport

    metrics = UnifiedHealthMetrics()
    report = metrics.collect_all()
    # report.overall_score       → 综合健康度 (0.0-1.0)
    # report.layer_scores        → 三层各自评分
    # report.trend_vs_yesterday  → vs 昨天变化

硬约束:
    - HC-1: Feature Flag 默认 False, 关闭时返回降级报告
    - HC-4: 只读生产数据, 不动 V9 基线
    - HC-5: 配置走 ConfigManager
"""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
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

# 默认权重 (可被 evolution.yaml 覆盖)
DEFAULT_WEIGHTS: dict[str, float] = {
    "code": 0.25,
    "strategy": 0.50,
    "ops": 0.25,
}

# 默认持久化路径
DEFAULT_HISTORY_PATH = _PROJECT_ROOT / "reports" / "evolution" / "health_trend.jsonl"

# 保留历史天数
DEFAULT_MAX_HISTORY_DAYS = 90

# 权重总和容差 (浮点比较)
WEIGHT_SUM_TOLERANCE = 0.01


# ============================================================
# 数据类 (不可变)
# ============================================================


@dataclass(frozen=True)
class LayerScore:
    """单层面评分 (不可变).

    Attributes:
        layer: 层面名 ("code" / "strategy" / "ops")
        score: 评分 0.0-1.0
        sub_metrics: 子指标明细 (各 0.0-1.0)
        is_degraded: 是否降级 (Flag 关闭 / 采集失败)
        degraded_reason: 降级原因
        collected_at: 采集时间 ISO8601
    """
    layer: str
    score: float
    sub_metrics: dict[str, float] = field(default_factory=dict)
    is_degraded: bool = False
    degraded_reason: str = ""
    collected_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "score": round(self.score, 4),
            "sub_metrics": {k: round(v, 4) for k, v in self.sub_metrics.items()},
            "is_degraded": self.is_degraded,
            "degraded_reason": self.degraded_reason,
            "collected_at": self.collected_at,
        }


@dataclass(frozen=True)
class HealthReport:
    """统一健康度报告 (不可变).

    Attributes:
        overall_score: 加权综合分 (0.0-1.0)
        layer_scores: 三层各自评分 {"code": LayerScore, ...}
        trend_vs_yesterday: vs 昨天变化 (-1.0 to +1.0)
        trend_vs_last_week: vs 上周变化
        is_degraded: 是否整体降级
        degraded_layers: 降级的层面列表
        generated_at: 报告生成时间 ISO8601
        sample_count: 评估样本数
    """
    overall_score: float
    layer_scores: dict[str, LayerScore] = field(default_factory=dict)
    trend_vs_yesterday: float = 0.0
    trend_vs_last_week: float = 0.0
    is_degraded: bool = False
    degraded_layers: list[str] = field(default_factory=list)
    generated_at: str = ""
    sample_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_score": round(self.overall_score, 4),
            "layer_scores": {k: v.to_dict() for k, v in self.layer_scores.items()},
            "trend_vs_yesterday": round(self.trend_vs_yesterday, 4),
            "trend_vs_last_week": round(self.trend_vs_last_week, 4),
            "is_degraded": self.is_degraded,
            "degraded_layers": list(self.degraded_layers),
            "generated_at": self.generated_at,
            "sample_count": self.sample_count,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> HealthReport:
        """从字典重建 HealthReport (用于读取历史)."""
        layer_scores: dict[str, LayerScore] = {}
        for k, v in d.get("layer_scores", {}).items():
            layer_scores[k] = LayerScore(
                layer=v.get("layer", k),
                score=float(v.get("score", 0.0)),
                sub_metrics={sk: float(sv) for sk, sv in v.get("sub_metrics", {}).items()},
                is_degraded=bool(v.get("is_degraded", False)),
                degraded_reason=v.get("degraded_reason", ""),
                collected_at=v.get("collected_at", ""),
            )
        return cls(
            overall_score=float(d.get("overall_score", 0.0)),
            layer_scores=layer_scores,
            trend_vs_yesterday=float(d.get("trend_vs_yesterday", 0.0)),
            trend_vs_last_week=float(d.get("trend_vs_last_week", 0.0)),
            is_degraded=bool(d.get("is_degraded", False)),
            degraded_layers=list(d.get("degraded_layers", [])),
            generated_at=d.get("generated_at", ""),
            sample_count=int(d.get("sample_count", 0)),
        )


# ============================================================
# 统一健康度度量器
# ============================================================


class UnifiedHealthMetrics:
    """统一健康度度量框架.

    Feature Flag: USE_UNIFIED_HEALTH_METRICS (默认 False, HC-1)
    配置: evolution.yaml (走 ConfigManager, HC-5)
    """

    def __init__(
        self,
        weights: dict[str, float] | None = None,
        history_path: Path | None = None,
        feature_flag_name: str = "USE_UNIFIED_HEALTH_METRICS",
    ) -> None:
        """初始化.

        Args:
            weights: 三层权重 {"code":0.25, "strategy":0.50, "ops":0.25}.
                     None=从 evolution.yaml 读取 (HC-5).
            history_path: 历史持久化路径. None=默认 reports/evolution/health_trend.jsonl.
            feature_flag_name: Feature Flag 名称 (HC-1).
        """
        self.feature_flag_name = feature_flag_name
        self._enabled = self._check_feature_flag(feature_flag_name)

        # 加载权重 (HC-5: 优先参数 > ConfigManager > 默认)
        self.weights = weights if weights is not None else self._load_weights()
        self._validate_weights(self.weights)

        # 持久化路径
        self.history_path = Path(history_path) if history_path else DEFAULT_HISTORY_PATH

        # 懒加载采集器 (避免循环依赖)
        self._code_layer: Any | None = None
        self._strategy_layer: Any | None = None
        self._ops_layer: Any | None = None
        self._layers_loaded = False

        logger.info(
            "UnifiedHealthMetrics 初始化: enabled=%s (flag=%s), weights=%s",
            self._enabled, feature_flag_name, self.weights,
        )

    # ============================================================
    # Feature Flag (HC-1)
    # ============================================================
    @staticmethod
    def _check_feature_flag(flag_name: str) -> bool:
        """检查 Feature Flag (HC-1). 失败时降级为 False."""
        try:
            from utils.infra.feature_flags import is_enabled
            return bool(is_enabled(flag_name))
        except Exception as e:
            logger.warning("Feature Flag 检查失败 (降级为 False): %s — %s", flag_name, e)
            return False

    # ============================================================
    # 配置加载 (HC-5: ConfigManager)
    # ============================================================
    def _load_weights(self) -> dict[str, float]:
        """从 evolution.yaml 加载权重 (HC-5). 失败时用默认权重."""
        try:
            from utils.config_manager import get_config
            cfg = get_config("evolution") or {}
            w = (cfg.get("health_metrics", {}) or {}).get("weights", {})
            if w and all(k in w for k in ("code", "strategy", "ops")):
                return {
                    "code": float(w["code"]),
                    "strategy": float(w["strategy"]),
                    "ops": float(w["ops"]),
                }
        except Exception as e:
            logger.warning("从 ConfigManager 加载权重失败, 用默认值: %s", e)
        return dict(DEFAULT_WEIGHTS)

    @staticmethod
    def _validate_weights(weights: dict[str, float]) -> None:
        """校验权重总和 = 1.0 (容差 0.01)."""
        total = sum(weights.get(k, 0.0) for k in ("code", "strategy", "ops"))
        if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
            raise ValueError(
                f"权重总和必须 = 1.0 (容差 {WEIGHT_SUM_TOLERANCE}), 实际 = {total:.4f}"
            )

    # ============================================================
    # 采集器懒加载 (避免循环依赖)
    # ============================================================
    def _load_layers(self) -> None:
        """懒加载三层采集器. 导入失败时为 None (降级)."""
        if self._layers_loaded:
            return
        self._layers_loaded = True
        # 代码层
        try:
            from utils.alpha.layers.code_health import CodeHealthLayer
            self._code_layer = CodeHealthLayer()
        except Exception as e:
            logger.warning("CodeHealthLayer 加载失败 (降级): %s", e)
            self._code_layer = None
        # 策略层
        try:
            from utils.alpha.layers.strategy_health import StrategyHealthLayer
            self._strategy_layer = StrategyHealthLayer()
        except Exception as e:
            logger.warning("StrategyHealthLayer 加载失败 (降级): %s", e)
            self._strategy_layer = None
        # 运维层
        try:
            from utils.alpha.layers.ops_health import OpsHealthLayer
            self._ops_layer = OpsHealthLayer()
        except Exception as e:
            logger.warning("OpsHealthLayer 加载失败 (降级): %s", e)
            self._ops_layer = None

    # ============================================================
    # 核心: 采集三层面并计算统一健康度
    # ============================================================
    def collect_all(self) -> HealthReport:
        """采集三层面指标并计算统一健康度.

        流程:
            1. 检查 Feature Flag (HC-1), 关闭时返回降级报告
            2. 懒加载三层采集器
            3. 并行采集三层面 (各自容错降级)
            4. 排除降级层后重新归一化权重
            5. 加权求和得 overall_score
            6. 与历史对比得趋势
            7. 持久化到 health_trend.jsonl

        Returns:
            HealthReport (不可变)
        """
        now = datetime.now(timezone.utc).isoformat()

        # HC-1: Flag 关闭 → 降级报告
        if not self._enabled:
            return self._degraded_report("FEATURE_FLAG_DISABLED", now)

        # 懒加载采集器
        self._load_layers()

        # 采集三层面 (各自容错)
        layer_scores: dict[str, LayerScore] = {}
        if self._code_layer is not None:
            layer_scores["code"] = self._safe_collect(self._code_layer, "code", now)
        else:
            layer_scores["code"] = LayerScore(
                layer="code", score=0.0, is_degraded=True,
                degraded_reason="CODE_LAYER_UNAVAILABLE", collected_at=now,
            )
        if self._strategy_layer is not None:
            layer_scores["strategy"] = self._safe_collect(self._strategy_layer, "strategy", now)
        else:
            layer_scores["strategy"] = LayerScore(
                layer="strategy", score=0.0, is_degraded=True,
                degraded_reason="STRATEGY_LAYER_UNAVAILABLE", collected_at=now,
            )
        if self._ops_layer is not None:
            layer_scores["ops"] = self._safe_collect(self._ops_layer, "ops", now)
        else:
            layer_scores["ops"] = LayerScore(
                layer="ops", score=0.0, is_degraded=True,
                degraded_reason="OPS_LAYER_UNAVAILABLE", collected_at=now,
            )

        # 计算综合分 (排除降级层后重新归一化)
        overall, is_degraded, degraded_layers = self._compute_overall(layer_scores)

        # 趋势对比
        history = self._read_history(7)
        trend_yesterday = self._calc_trend(overall, history, 1)
        trend_week = self._calc_trend(overall, history, 7)

        report = HealthReport(
            overall_score=overall,
            layer_scores=layer_scores,
            trend_vs_yesterday=trend_yesterday,
            trend_vs_last_week=trend_week,
            is_degraded=is_degraded,
            degraded_layers=degraded_layers,
            generated_at=now,
            sample_count=sum(1 for v in layer_scores.values() if not v.is_degraded),
        )

        # 持久化 (HC-4: 只追加, 不修改历史)
        self._persist(report)

        return report

    def _safe_collect(self, layer_obj: Any, layer_name: str, now: str) -> LayerScore:
        """安全调用采集器, 失败时返回降级 LayerScore."""
        try:
            result = layer_obj.collect()
            # 确保返回的是 LayerScore
            if not isinstance(result, LayerScore):
                return LayerScore(
                    layer=layer_name, score=0.0, is_degraded=True,
                    degraded_reason=f"INVALID_RETURN_TYPE: {type(result).__name__}",
                    collected_at=now,
                )
            return result
        except Exception as e:
            logger.warning("%s 层采集失败 (降级): %s", layer_name, e)
            return LayerScore(
                layer=layer_name, score=0.0, is_degraded=True,
                degraded_reason=f"COLLECT_ERROR: {e}", collected_at=now,
            )

    def _compute_overall(
        self, layer_scores: dict[str, LayerScore]
    ) -> tuple:
        """计算综合分 (排除降级层后重新归一化).

        Returns:
            (overall_score, is_degraded, degraded_layers)
        """
        degraded_layers = [k for k, v in layer_scores.items() if v.is_degraded]
        active_layers = {k: v for k, v in layer_scores.items() if not v.is_degraded}

        if not active_layers:
            # 全部降级
            return 0.0, True, degraded_layers

        # 重新归一化权重 (仅活跃层)
        active_weights = {k: self.weights.get(k, 0.0) for k in active_layers}
        weight_sum = sum(active_weights.values())
        if weight_sum <= 0:
            return 0.0, True, degraded_layers

        overall = sum(
            active_weights[k] * layer_scores[k].score for k in active_layers
        ) / weight_sum

        # 限制在 [0, 1]
        overall = max(0.0, min(1.0, overall))
        is_degraded = len(degraded_layers) > 0
        return overall, is_degraded, degraded_layers

    def _degraded_report(self, reason: str, now: str) -> HealthReport:
        """生成降级报告 (Flag 关闭时)."""
        layer_scores = {
            k: LayerScore(layer=k, score=0.0, is_degraded=True,
                          degraded_reason=reason, collected_at=now)
            for k in ("code", "strategy", "ops")
        }
        return HealthReport(
            overall_score=0.0,
            layer_scores=layer_scores,
            is_degraded=True,
            degraded_layers=["code", "strategy", "ops"],
            generated_at=now,
            sample_count=0,
        )

    # ============================================================
    # 历史持久化与趋势
    # ============================================================
    def _persist(self, report: HealthReport) -> None:
        """持久化到 health_trend.jsonl (追加模式, HC-4 只读历史)."""
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.history_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(report.to_dict(), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("健康度持久化失败 (不影响内存报告): %s", e)

    def _read_history(self, days: int) -> list[HealthReport]:
        """读取最近 N 天历史 (只读). 失败时返回空列表."""
        try:
            if not self.history_path.exists():
                return []
            lines = self.history_path.read_text(encoding="utf-8").strip().splitlines()
            # 取最后 days*2 行 (每天可能有多次采集), 解析有效的
            recent = lines[-(days * 2):] if len(lines) > days * 2 else lines
            reports: list[HealthReport] = []
            for line in reversed(recent):
                line = line.strip()
                if not line:
                    continue
                try:
                    reports.append(HealthReport.from_dict(json.loads(line)))
                except Exception:
                    continue
            return reports
        except Exception as e:
            logger.warning("读取历史失败: %s", e)
            return []

    @staticmethod
    def _calc_trend(
        current_score: float, history: list[HealthReport], target_offset: int
    ) -> float:
        """计算趋势 = current - history[offset]. 不足时返回 0.0."""
        if len(history) <= target_offset:
            return 0.0
        try:
            past = history[target_offset]
            return round(current_score - past.overall_score, 4)
        except Exception:
            return 0.0

    # ============================================================
    # 公共 API
    # ============================================================
    def get_history(self, days: int = 30) -> list[HealthReport]:
        """读取历史健康度 (只读)."""
        return self._read_history(days)

    def compare_baseline(
        self, current: HealthReport, baseline_date: str
    ) -> dict[str, float]:
        """与指定基线日期对比.

        Args:
            current: 当前报告
            baseline_date: 基线日期 YYYY-MM-DD

        Returns:
            {"overall_diff": float, "code_diff": float, ...}
        """
        history = self._read_history(90)
        baseline: HealthReport | None = None
        for h in history:
            if h.generated_at.startswith(baseline_date):
                baseline = h
                break
        if baseline is None:
            return {"error": -1.0, "reason": f"基线 {baseline_date} 不存在"}

        result: dict[str, float] = {
            "overall_diff": round(current.overall_score - baseline.overall_score, 4),
        }
        for layer in ("code", "strategy", "ops"):
            cur_layer = current.layer_scores.get(layer)
            base_layer = baseline.layer_scores.get(layer)
            if cur_layer and base_layer:
                result[f"{layer}_diff"] = round(cur_layer.score - base_layer.score, 4)
        return result

    def get_status(self) -> dict[str, Any]:
        """获取度量器状态 (用于审计)."""
        return {
            "enabled": self._enabled,
            "feature_flag": self.feature_flag_name,
            "weights": dict(self.weights),
            "history_path": str(self.history_path),
            "layers_loaded": self._layers_loaded,
        }
