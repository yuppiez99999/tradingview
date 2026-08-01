"""策略层健康度采集器 — 三层面自我进化 Stage 1.

模块整合 8.4 — ARCHITECTURE_三层面进化 §第1阶段
复用: EvolutionOrchestrator (只读状态) + decisions.jsonl (历史决策) + StrategyEvaluator

设计原则:
    1. Feature Flag (HC-1): USE_STRATEGY_HEALTH_LAYER 默认 False
    2. 配置走 ConfigManager (HC-5): get_config("evolution").strategy_health
    3. 只读历史 (HC-4): 读取 decisions.jsonl, 不触发新评估
    4. 容错降级: 历史不足时降级, 不阻塞
    5. 不可变: 返回 LayerScore(frozen=True)

子指标 (各 0.0-1.0, 权重走 evolution.yaml):
    private_score (0.40): 来自最近 decisions.jsonl 的 private_score
    public_score (0.20): 来自最近 decisions.jsonl 的 public_score
    anti_cheat (0.20): 1 - reward_hacking_risk
    drift_health (0.10): 漂移健康度 (第2阶段接入 DriftMonitor, 暂降级)
    observation_progress (0.10): observation_day / observation_total

用法:
    from utils.alpha.layers.strategy_health import StrategyHealthLayer
    layer = StrategyHealthLayer()
    score = layer.collect()
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.alpha.health_metrics import LayerScore

# ============================================================
# 常量
# ============================================================

DEFAULT_WEIGHTS: dict[str, float] = {
    "private_score": 0.40,
    "public_score": 0.20,
    "anti_cheat": 0.20,
    "drift_health": 0.10,
    "observation_progress": 0.10,
}

# 默认决策日志路径 (与 EvolutionOrchestrator 一致)
DEFAULT_DECISIONS_PATH = _PROJECT_ROOT / "reports" / "evolution" / "decisions.jsonl"

# 观察期总天数 (HC-4)
DEFAULT_OBSERVATION_TOTAL = 14


class StrategyHealthLayer:
    """策略层健康度采集器 (只读历史, 不触发评估).

    Feature Flag: USE_STRATEGY_HEALTH_LAYER (默认 False, HC-1)
    配置: evolution.yaml → strategy_health (HC-5)
    """

    FLAG_NAME = "USE_STRATEGY_HEALTH_LAYER"

    def __init__(
        self,
        weights: dict[str, float] | None = None,
        decisions_path: Path | None = None,
        feature_flag_name: str = FLAG_NAME,
    ) -> None:
        self.feature_flag_name = feature_flag_name
        self._enabled = self._check_feature_flag(feature_flag_name)
        self.weights = weights if weights is not None else self._load_weights()
        self.decisions_path = Path(decisions_path) if decisions_path else DEFAULT_DECISIONS_PATH

        logger.info(
            "StrategyHealthLayer 初始化: enabled=%s (flag=%s)",
            self._enabled, feature_flag_name,
        )

    # ============================================================
    # Feature Flag (HC-1)
    # ============================================================
    @staticmethod
    def _check_feature_flag(flag_name: str) -> bool:
        try:
            from utils.infra.feature_flags import is_enabled
            return bool(is_enabled(flag_name))
        except Exception as e:
            logger.warning("Feature Flag 检查失败 (降级 False): %s — %s", flag_name, e)
            return False

    # ============================================================
    # 配置加载 (HC-5)
    # ============================================================
    def _load_weights(self) -> dict[str, float]:
        try:
            from utils.config_manager import get_config
            cfg = get_config("evolution") or {}
            w = (cfg.get("strategy_health", {}) or {}).get("weights", {})
            if w:
                return {k: float(w.get(k, DEFAULT_WEIGHTS.get(k, 0.0))) for k in DEFAULT_WEIGHTS}
        except Exception as e:
            logger.warning("StrategyHealth 权重加载失败, 用默认值: %s", e)
        return dict(DEFAULT_WEIGHTS)

    # ============================================================
    # 核心: 采集策略层健康度
    # ============================================================
    def collect(self) -> LayerScore:
        """采集策略层指标 (只读历史). 失败时返回降级 LayerScore."""
        now = datetime.now(timezone.utc).isoformat()

        if not self._enabled:
            return LayerScore(
                layer="strategy", score=0.0, is_degraded=True,
                degraded_reason="FEATURE_FLAG_DISABLED", collected_at=now,
            )

        # 读取最近决策记录 (只读, HC-4)
        latest_decision = self._read_latest_decision()
        observation_day, observation_total = self._get_observation_progress()

        # 子指标
        private_score = float(latest_decision.get("private_score", 0.0)) if latest_decision else 0.0
        public_score = float(latest_decision.get("public_score", 0.0)) if latest_decision else 0.0
        rh_risk = float(latest_decision.get("reward_hacking_risk", 0.0)) if latest_decision else 0.0
        anti_cheat = max(0.0, 1.0 - rh_risk)
        drift_health = self._get_drift_health()  # 第2阶段接入 DriftMonitor, 暂降级
        observation_progress = (
            observation_day / observation_total if observation_total > 0 else 0.0
        )
        observation_progress = max(0.0, min(1.0, observation_progress))

        sub_metrics = {
            "private_score": private_score,
            "public_score": public_score,
            "anti_cheat": anti_cheat,
            "drift_health": drift_health,
            "observation_progress": observation_progress,
        }

        # 加权求和
        score = (
            self.weights.get("private_score", 0.40) * private_score
            + self.weights.get("public_score", 0.20) * public_score
            + self.weights.get("anti_cheat", 0.20) * anti_cheat
            + self.weights.get("drift_health", 0.10) * drift_health
            + self.weights.get("observation_progress", 0.10) * observation_progress
        )
        score = max(0.0, min(1.0, score))

        # 无历史决策时标记降级 (但仍返回评分)
        is_degraded = latest_decision is None
        degraded_reason = "NO_DECISIONS_HISTORY" if is_degraded else ""

        return LayerScore(
            layer="strategy", score=score, sub_metrics=sub_metrics,
            is_degraded=is_degraded, degraded_reason=degraded_reason,
            collected_at=now,
        )

    # ============================================================
    # 子指标采集
    # ============================================================
    def _read_latest_decision(self) -> dict[str, Any] | None:
        """读取 decisions.jsonl 最新一条记录 (只读)."""
        try:
            if not self.decisions_path.exists():
                return None
            lines = self.decisions_path.read_text(encoding="utf-8").strip().splitlines()
            if not lines:
                return None
            # 从末尾找第一条有效 JSON
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    return json.loads(line)
                except Exception:
                    continue
            return None
        except Exception as e:
            logger.warning("读取 decisions.jsonl 失败: %s", e)
            return None

    def _get_observation_progress(self) -> tuple:
        """获取观察期进度 (observation_day, observation_total).

        复用 EvolutionOrchestrator.get_status() (只读, 不触发评估).
        """
        try:
            from utils.alpha.evolution_orchestrator import EvolutionOrchestrator
            orch = EvolutionOrchestrator()
            status = orch.get_status()
            day = getattr(status, "observation_day", 0)
            total = getattr(status, "observation_total", DEFAULT_OBSERVATION_TOTAL)
            return int(day), int(total)
        except Exception as e:
            logger.warning("获取观察期进度失败 (用默认值): %s", e)
            return 0, DEFAULT_OBSERVATION_TOTAL

    @staticmethod
    def _get_drift_health() -> float:
        """漂移健康度 (第2阶段接入 DriftMonitor, 暂返回中性值 0.5).

        TODO Stage 2: 复用 DriftMonitor.get_recent_alerts() 计算
        drift_health = 1 - drift_severity
        """
        return 0.5
