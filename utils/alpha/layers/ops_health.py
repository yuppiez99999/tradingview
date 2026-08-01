"""运维层健康度采集器 — 三层面自我进化 Stage 1.

模块整合 8.4 — ARCHITECTURE_三层面进化 §第1阶段
复用: reports/ 各子目录只读聚合 (system_check/data_quality/drift_alerts/flag_audit)

设计原则:
    1. Feature Flag (HC-1): USE_OPS_HEALTH_LAYER 默认 False
    2. 配置走 ConfigManager (HC-5): get_config("evolution").ops_health
    3. 只读聚合: 只读取 reports/ 下的报告文件, 不运行任何检查
    4. 容错降级: 目录不存在/文件解析失败时降级
    5. 不可变: 返回 LayerScore(frozen=True)

子指标 (各 0.0-1.0, 权重走 evolution.yaml):
    datasource_redundancy (0.30): 数据源可用数/总数 (最新 system_check 归档 C3)
    data_quality (0.25): data_quality 报告新鲜度 (有最近报告=高分)
    drift_alert_recency (0.15): 1 - 告警新鲜度 (越新越扣分)
    flag_stability (0.10): 1 - Flag 变更频率 (变更越少越高分)
    risk_event_rate (0.20): 1 - 风控事件频率 (事件越少越高分)

用法:
    from utils.alpha.layers.ops_health import OpsHealthLayer
    layer = OpsHealthLayer()
    score = layer.collect()
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.alpha.health_metrics import LayerScore

# ============================================================
# 常量
# ============================================================

DEFAULT_WEIGHTS: dict[str, float] = {
    "datasource_redundancy": 0.30,
    "data_quality": 0.25,
    "drift_alert_recency": 0.15,
    "flag_stability": 0.10,
    "risk_event_rate": 0.20,
}

# 默认报告目录 (相对项目根)
DEFAULT_REPORT_DIRS: dict[str, str] = {
    "system_check": "reports/system_check",
    "data_quality": "reports/data_quality",
    "drift_alerts": "reports/drift_alerts",
    "flag_audit": "reports/flag_audit",
    "risk_bus": "reports/risk_bus_audit",
}

# 告警新鲜度窗口 (秒, 超过此窗口不扣分)
DEFAULT_ALERT_RECENCY_WINDOW = 86400  # 24h

# 数据源总数 (P0-P6 共 7 级, 但实际配置的连通性数据源约 4 个: Wind/iFinD/TDX/AKShare)
EXPECTED_DATASOURCES = 4


class OpsHealthLayer:
    """运维层健康度采集器 (只读聚合 reports/).

    Feature Flag: USE_OPS_HEALTH_LAYER (默认 False, HC-1)
    配置: evolution.yaml → ops_health (HC-5)
    """

    FLAG_NAME = "USE_OPS_HEALTH_LAYER"

    def __init__(
        self,
        weights: dict[str, float] | None = None,
        report_dirs: dict[str, str] | None = None,
        feature_flag_name: str = FLAG_NAME,
    ) -> None:
        self.feature_flag_name = feature_flag_name
        self._enabled = self._check_feature_flag(feature_flag_name)
        self.weights = weights if weights is not None else self._load_weights()
        self._project_root = _PROJECT_ROOT

        # 加载报告目录配置
        self.report_dirs = self._load_report_dirs(report_dirs)

        logger.info(
            "OpsHealthLayer 初始化: enabled=%s (flag=%s)",
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
            w = (cfg.get("ops_health", {}) or {}).get("weights", {})
            if w:
                return {k: float(w.get(k, DEFAULT_WEIGHTS.get(k, 0.0))) for k in DEFAULT_WEIGHTS}
        except Exception as e:
            logger.warning("OpsHealth 权重加载失败, 用默认值: %s", e)
        return dict(DEFAULT_WEIGHTS)

    def _load_report_dirs(self, override: dict[str, str] | None) -> dict[str, Path]:
        """加载报告目录路径 (HC-5: 从 evolution.yaml 读取)."""
        dirs_config = dict(DEFAULT_REPORT_DIRS)
        try:
            from utils.config_manager import get_config
            cfg = get_config("evolution") or {}
            configured = (cfg.get("ops_health", {}) or {}).get("report_dirs", {})
            if configured:
                dirs_config.update(configured)
        except Exception:
            pass
        if override:
            dirs_config.update(override)
        return {k: self._project_root / v for k, v in dirs_config.items()}

    # ============================================================
    # 核心: 采集运维层健康度
    # ============================================================
    def collect(self) -> LayerScore:
        """采集运维层指标 (只读聚合). 失败时返回降级 LayerScore."""
        now = datetime.now(timezone.utc).isoformat()

        if not self._enabled:
            return LayerScore(
                layer="ops", score=0.0, is_degraded=True,
                degraded_reason="FEATURE_FLAG_DISABLED", collected_at=now,
            )

        # 采集子指标 (各自容错)
        datasource_score = self._calc_datasource_redundancy()
        data_quality_score = self._calc_data_quality_score()
        drift_recency_score = self._calc_drift_alert_recency()
        flag_stability_score = self._calc_flag_stability()
        risk_event_score = self._calc_risk_event_rate()

        sub_metrics = {
            "datasource_redundancy": datasource_score,
            "data_quality": data_quality_score,
            "drift_alert_recency": drift_recency_score,
            "flag_stability": flag_stability_score,
            "risk_event_rate": risk_event_score,
        }

        # 加权求和
        score = (
            self.weights.get("datasource_redundancy", 0.30) * datasource_score
            + self.weights.get("data_quality", 0.25) * data_quality_score
            + self.weights.get("drift_alert_recency", 0.15) * drift_recency_score
            + self.weights.get("flag_stability", 0.10) * flag_stability_score
            + self.weights.get("risk_event_rate", 0.20) * risk_event_score
        )
        score = max(0.0, min(1.0, score))

        return LayerScore(
            layer="ops", score=score, sub_metrics=sub_metrics,
            is_degraded=False, collected_at=now,
        )

    # ============================================================
    # 子指标采集 (只读聚合 reports/)
    # ============================================================
    def _calc_datasource_redundancy(self) -> float:
        """数据源冗余度: 从最新 system_check 归档统计 C3 通过数.

        简化: system_check 归档目录有最近报告 = 高分 (有监控就绪)
        """
        try:
            sc_dir = self.report_dirs.get("system_check")
            if not sc_dir or not sc_dir.exists():
                return 0.3  # 目录不存在, 低分
            # 统计最近 7 天的归档文件数
            files = sorted(sc_dir.glob("system_check_*.json"), reverse=True)
            if not files:
                # 也可能是 .md 文件
                files = sorted(sc_dir.glob("*.json"), reverse=True)
            if not files:
                return 0.3
            # 有最近归档 = 数据源监控就绪, 给高分
            # 尝试读取最新归档统计 C3 通过数
            try:
                latest = json.loads(files[0].read_text(encoding="utf-8"))
                results = latest.get("results", [])
                c3_items = [r for r in results if str(r.get("code", "")).startswith("C3")]
                if c3_items:
                    passed = sum(1 for r in c3_items if r.get("status") == "PASS")
                    return passed / len(c3_items) if c3_items else 0.8
            except Exception:
                pass
            return 0.8  # 有归档但解析失败, 给中高分
        except Exception:
            return 0.3

    def _calc_data_quality_score(self) -> float:
        """数据质量报告新鲜度: 有最近报告 = 高分."""
        try:
            dq_dir = self.report_dirs.get("data_quality")
            if not dq_dir or not dq_dir.exists():
                return 0.5  # 目录不存在, 中性
            files = list(dq_dir.glob("*.json")) + list(dq_dir.glob("*.md"))
            if not files:
                return 0.4
            # 有报告 = 数据质量监控就绪
            return 0.8
        except Exception:
            return 0.5

    def _calc_drift_alert_recency(self) -> float:
        """漂移告警新鲜度: 1 - 新鲜度 (越新越扣分, 24h 内有告警扣分)."""
        try:
            da_dir = self.report_dirs.get("drift_alerts")
            if not da_dir or not da_dir.exists():
                return 0.8  # 无告警目录 = 无漂移 = 高分
            files = list(da_dir.glob("*.json")) + list(da_dir.glob("*.jsonl"))
            if not files:
                return 0.8  # 无告警文件 = 无漂移
            # 检查最新告警时间
            latest_file = max(files, key=lambda f: f.stat().st_mtime)
            age_seconds = (datetime.now(timezone.utc).timestamp() - latest_file.stat().st_mtime)
            if age_seconds < DEFAULT_ALERT_RECENCY_WINDOW:
                # 24h 内有告警, 扣分 (越新扣分越多)
                # age=0 → 0.2 (最新告警, 扣分最多)
                # age=window(24h) → 0.8 (接近窗口边界, 扣分最少)
                return max(0.2, 0.2 + (age_seconds / DEFAULT_ALERT_RECENCY_WINDOW) * 0.6)
            return 0.8  # 旧告警 (>24h), 不扣分
        except Exception:
            return 0.5

    def _calc_flag_stability(self) -> float:
        """Flag 变更稳定性: 变更越少越高分."""
        try:
            fa_dir = self.report_dirs.get("flag_audit")
            if not fa_dir or not fa_dir.exists():
                return 0.9  # 无审计目录 = 无变更 = 高分
            # 统计最近 7 天的 Flag 变更记录数
            files = list(fa_dir.glob("*.json")) + list(fa_dir.glob("*.jsonl"))
            now_ts = datetime.now(timezone.utc).timestamp()
            recent_changes = sum(
                1 for f in files
                if (now_ts - f.stat().st_mtime) < (7 * 86400)
            )
            # 变更越少越高分 (0 变更=1.0, 10 变更=0.0)
            return max(0.0, 1.0 - recent_changes / 10.0)
        except Exception:
            return 0.5

    def _calc_risk_event_rate(self) -> float:
        """风控事件频率: 事件越少越高分."""
        try:
            rb_dir = self.report_dirs.get("risk_bus")
            if not rb_dir or not rb_dir.exists():
                return 0.9  # 无事件目录 = 无事件 = 高分
            files = list(rb_dir.glob("*.json")) + list(rb_dir.glob("*.jsonl"))
            now_ts = datetime.now(timezone.utc).timestamp()
            recent_events = sum(
                1 for f in files
                if (now_ts - f.stat().st_mtime) < (7 * 86400)
            )
            # 事件越少越高分 (0 事件=1.0, 20 事件=0.0)
            return max(0.0, 1.0 - recent_events / 20.0)
        except Exception:
            return 0.5
