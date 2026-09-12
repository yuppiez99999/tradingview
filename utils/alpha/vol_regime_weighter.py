"""
波动率 Regime 权重建议器 (Volatility Regime Weighter)
====================================================

自我进化框架的核心组件之一，根据市场波动率 Regime (bull/neutral/bear/crisis)
动态调整 8 类风格大类的权重建议。

设计原则:
    1. HC-1 透传: USE_VOL_REGIME_WEIGHTER=False 时返回 WeightSuggestion.noop()
    2. HC-4 观察期: Phase 0 不修改 portfolio.yaml, 仅输出 reports/evolution/
    3. HC-5 配置: 走 ConfigManager 4 级优先级 (configs/vol_regime_weighter.yaml)
    4. 复用 VolTargetController.calc_realized_vol, 不重写 EWMA 逻辑
    5. 复用 EvolutionOrchestrator.log_decision, 不重复造审计链
    6. 容错降级: VIX 缺失时回退到 realized_vol 单指标分类

Regime 四档对齐 portfolio.yaml 的 dynamic_hedge_policy:
    - bull    (VIX < 20):     进攻类加仓, 防御类减仓
    - neutral (20 ≤ VIX < 30): 中性, 倍数全 1.0
    - bear    (30 ≤ VIX < 40): 进攻类减仓, 防御类加仓
    - crisis  (VIX ≥ 40):     大幅减仓进攻, 现金为王

主入口:
    weighter = VolRegimeWeighter()
    result = weighter.run_cycle(
        portfolio_snapshot={"科技": 0.235, "现金": 0.05, ...},
        vix_value=32.5,
        daily_returns=[0.01, -0.02, ...],
        orchestrator=evolution_orchestrator,  # 可选
    )
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from utils.datetime_utils import now_bj

logger = logging.getLogger(__name__)

# ============================================================
# 常量定义
# ============================================================

# 8 类风格大类 (与 configs/account_structure.yaml 的 style 字段对齐)
STYLE_CATEGORIES: list[str] = [
    "科技",
    "新能源",
    "医药",
    "金融",
    "宽基",
    "资源",
    "防御",
    "现金",
]

# 4×8 默认权重调整矩阵 (倍数 = suggested / current)
# 设计依据:
#   - 进攻类 (科技/新能源/医药): 高波动减仓, 低波动加仓
#   - 金融类 (金融/宽基): 随波动率线性调整, 幅度小
#   - 资源类: 黄金属性偏防御, 铜矿偏进攻, 整体中性偏防御
#   - 防御类 (防御/现金): 高波动加仓, 低波动减仓
#   - 现金: 所有档位的最终吸收者, 调整后差额归现金
DEFAULT_WEIGHT_MATRIX: dict[str, dict[str, float]] = {
    "bull": {
        "科技": 1.20,
        "新能源": 1.15,
        "医药": 1.10,
        "金融": 1.05,
        "宽基": 1.05,
        "资源": 1.00,
        "防御": 0.90,
        "现金": 0.50,
    },
    "neutral": {
        "科技": 1.00,
        "新能源": 1.00,
        "医药": 1.00,
        "金融": 1.00,
        "宽基": 1.00,
        "资源": 1.00,
        "防御": 1.00,
        "现金": 1.00,
    },
    "bear": {
        "科技": 0.60,
        "新能源": 0.65,
        "医药": 0.80,
        "金融": 0.90,
        "宽基": 0.95,
        "资源": 1.10,
        "防御": 1.30,
        "现金": 2.00,
    },
    "crisis": {
        "科技": 0.30,
        "新能源": 0.35,
        "医药": 0.60,
        "金融": 0.70,
        "宽基": 0.85,
        "资源": 1.20,
        "防御": 1.50,
        "现金": 3.00,
    },
}

# Regime 阈值 (VIX 优先, realized_vol 备选)
VIX_THRESHOLDS: dict[str, float] = {"bull": 20.0, "neutral": 30.0, "bear": 40.0}
RV_THRESHOLDS: dict[str, float] = {"bull": 0.15, "neutral": 0.25, "bear": 0.40}

# 对齐 portfolio.yaml dynamic_hedge_policy 的四档 hedge_ratio
ALIGNED_HEDGE_RATIOS: dict[str, float] = {
    "bull": 0.20,
    "neutral": 0.40,
    "bear": 0.75,
    "crisis": 0.90,
}

# Regime 标签 (与 portfolio.yaml dynamic_hedge_policy 的 key 对齐)
REGIME_BULL = "bull"
REGIME_NEUTRAL = "neutral"
REGIME_BEAR = "bear"
REGIME_CRISIS = "crisis"
REGIME_ORDER: list[str] = [REGIME_BULL, REGIME_NEUTRAL, REGIME_BEAR, REGIME_CRISIS]

# 默认约束 (对齐 configs/account_structure.yaml risk_parameters)
DEFAULT_MAX_SINGLE_POSITION = 0.08
DEFAULT_MAX_SECTOR_EXPOSURE = 0.30
DEFAULT_CASH_FLOOR = 0.05
DEFAULT_MIN_WEIGHT = 0.01

# 默认报告输出目录
DEFAULT_REPORTS_DIR = Path("reports/evolution")

# 配置文件默认路径
DEFAULT_CONFIG_PATH = Path("configs/vol_regime_weighter.yaml")


# ============================================================
# 数据类
# ============================================================


@dataclass
class VolRegime:
    """波动率 Regime 识别结果.

    Attributes:
        label: Regime 标签 ("bull"/"neutral"/"bear"/"crisis")
        confidence: 置信度 0.0-1.0
        source: 识别来源 ("vix"/"realized_vol"/"fallback")
        indicators: 原始指标字典
        aligned_hedge_ratio: 对齐 portfolio.yaml dynamic_hedge_policy 的 hedge_ratio
        hedge_policy_key: 对应 portfolio.yaml 的 hedge policy key (bull_market 等)
        consistency_check: VIX 与 RV 一致性检查结果
    """

    label: str
    confidence: float
    source: str
    indicators: dict[str, float] = field(default_factory=dict)
    aligned_hedge_ratio: float = 0.40
    hedge_policy_key: str = "neutral_market"
    consistency_check: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "confidence": round(self.confidence, 4),
            "source": self.source,
            "indicators": {
                k: round(v, 4) if isinstance(v, float) else v
                for k, v in self.indicators.items()
            },
            "aligned_hedge_ratio": self.aligned_hedge_ratio,
            "hedge_policy_key": self.hedge_policy_key,
            "consistency_check": self.consistency_check,
        }


@dataclass
class WeightSuggestion:
    """权重建议 (Phase 0: 不直接生效, 仅作建议).

    Attributes:
        timestamp: 生成时间 (ISO 格式)
        regime: VolRegime 对象
        current_weights: 当前权重 (style → weight)
        suggested_weights: 建议权重 (style → weight, 经约束调整)
        multipliers: 调整倍数 (style → multiplier)
        deltas: 权重变化 (style → delta = suggested - current)
        constraints_applied: 应用的约束说明列表
        confidence: 综合置信度 0.0-1.0
        trigger_reason: 触发理由 (人类可读)
        observation_phase: 是否处于 Phase 0 观察期
        degraded: 是否降级
        degraded_reason: 降级原因
    """

    timestamp: str
    regime: VolRegime
    current_weights: dict[str, float] = field(default_factory=dict)
    suggested_weights: dict[str, float] = field(default_factory=dict)
    multipliers: dict[str, float] = field(default_factory=dict)
    deltas: dict[str, float] = field(default_factory=dict)
    constraints_applied: list[str] = field(default_factory=list)
    confidence: float = 0.0
    trigger_reason: str = ""
    observation_phase: bool = True
    degraded: bool = False
    degraded_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_version": "1.0",
            "generated_at": self.timestamp,
            "observation_phase": self.observation_phase,
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
            "regime": self.regime.to_dict(),
            "current_weights": {
                k: round(v, 4) for k, v in self.current_weights.items()
            },
            "suggested_weights": {
                k: round(v, 4) for k, v in self.suggested_weights.items()
            },
            "multipliers": {k: round(v, 4) for k, v in self.multipliers.items()},
            "deltas": {k: round(v, 4) for k, v in self.deltas.items()},
            "constraints_applied": self.constraints_applied,
            "trigger_reason": self.trigger_reason,
            "confidence": round(self.confidence, 4),
            "audit": {
                "portfolio_yaml_untouched": True,  # Phase 0 强制
            },
            "next_steps": {
                "phase_0_action": "review_only",
                "phase_1_trigger": "user_approval_after_observation_period",
                "phase_1_action": "apply_to_portfolio_yaml_with_dual_signature",
            },
        }

    @classmethod
    def noop(cls, reason: str = "feature_flag_disabled") -> WeightSuggestion:
        """Flag 关闭时的降级返回 (HC-1)."""
        return cls(
            timestamp=now_bj().isoformat(),
            regime=VolRegime(
                label=REGIME_NEUTRAL,
                confidence=0.0,
                source="noop",
                aligned_hedge_ratio=ALIGNED_HEDGE_RATIOS[REGIME_NEUTRAL],
                hedge_policy_key="neutral_market",
            ),
            degraded=True,
            degraded_reason=reason,
            trigger_reason=f"模块禁用: {reason}",
        )


# ============================================================
# 核心类
# ============================================================


class VolRegimeWeighter:
    """波动率 Regime 权重建议器 (Phase 0: 只读建议模式).

    用法:
        weighter = VolRegimeWeighter()
        result = weighter.run_cycle(
            portfolio_snapshot={"科技": 0.235, "现金": 0.05, ...},
            vix_value=32.5,
            daily_returns=[0.01, -0.02, ...],
        )
        # result["report_path"] 指向 reports/evolution/vol_regime_weights_YYYY-MM-DD.json
    """

    def __init__(
        self,
        feature_flag_name: str = "USE_VOL_REGIME_WEIGHTER",
        vol_controller: Any = None,
        reports_dir: Path | None = None,
        config_path: Path | None = None,
    ) -> None:
        """初始化权重建议器.

        Args:
            feature_flag_name: Feature Flag 名称 (HC-1)
            vol_controller: VolTargetController 实例 (依赖注入, 测试可替换)
            reports_dir: 报告输出目录 (None=默认 reports/evolution/)
            config_path: 配置文件路径 (None=默认 configs/vol_regime_weighter.yaml)
        """
        self.feature_flag_name = feature_flag_name
        self._enabled = self._check_flag(feature_flag_name)

        # 依赖注入 VolTargetController (复用 calc_realized_vol)
        self._vol_controller = vol_controller
        if self._vol_controller is None:
            self._vol_controller = self._init_vol_controller()

        # 报告输出目录
        self.reports_dir = Path(reports_dir) if reports_dir else DEFAULT_REPORTS_DIR

        # 加载配置 (HC-5: 4 级优先级)
        self.config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self._config = self._load_config()

        # 矩阵 (配置可覆盖默认)
        self.weight_matrix = self._merge_matrix()

        logger.info(
            "VolRegimeWeighter 初始化: enabled=%s (flag=%s), reports_dir=%s",
            self._enabled,
            feature_flag_name,
            self.reports_dir,
        )

    # ============================================================
    # Feature Flag 与配置
    # ============================================================

    def _check_flag(self, name: str) -> bool:
        """检查 Feature Flag (HC-1)."""
        try:
            from utils.infra.feature_flags import is_enabled

            return bool(is_enabled(name))
        except (ImportError, AttributeError) as e:
            logger.warning("Feature Flag 检查失败, 默认禁用: %s (%s)", name, e)
            return False

    def _init_vol_controller(self) -> Any:
        """初始化 VolTargetController (复用, 不重写)."""
        try:
            from utils.vol_target_controller import VolTargetController

            return VolTargetController()
        except (ImportError, AttributeError) as e:
            logger.warning("VolTargetController 初始化失败, 将降级: %s", e)
            return None

    def _load_config(self) -> dict[str, Any]:
        """加载配置文件 (HC-5 4 级优先级, 这里简化为直接读取)."""
        try:
            if self.config_path.exists():
                with self.config_path.open("r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                logger.debug("配置加载成功: %s", self.config_path)
                return cfg
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            logger.warning("配置加载失败, 使用默认值: %s (%s)", self.config_path, e)
        return {}

    def _merge_matrix(self) -> dict[str, dict[str, float]]:
        """合并默认矩阵与配置覆写."""
        merged = {
            regime: dict(styles) for regime, styles in DEFAULT_WEIGHT_MATRIX.items()
        }
        override = self._config.get("weight_matrix", {}) or {}
        for regime, styles in override.items():
            if regime in merged:
                merged[regime].update(styles)
        return merged

    @property
    def enabled(self) -> bool:
        """是否启用."""
        return self._enabled

    # ============================================================
    # 感知层 (Sense)
    # ============================================================

    def sense_regime(
        self,
        vix_value: float | None = None,
        daily_returns: list[float] | None = None,
        psi_value: float | None = None,
        current_drawdown: float | None = None,
    ) -> VolRegime:
        """识别当前波动率 Regime.

        优先级:
            1. VIX (若提供): <20=bull, 20-30=neutral, 30-40=bear, >40=crisis
            2. realized_vol (VIX 缺失时, 复用 VolTargetController)
            3. 都缺失: neutral + confidence=0.3

        Args:
            vix_value: VIX 数值 (如 32.5)
            daily_returns: 日收益率序列 (用于计算 realized_vol)
            psi_value: PSI 值 (辅助修正, 可选)
            current_drawdown: 当前回撤 (辅助修正, 可选)

        Returns:
            VolRegime 对象
        """
        indicators: dict[str, float] = {}
        vix_classification: str | None = None
        rv_classification: str | None = None
        source = "fallback"

        # Step 1: 主指标分类
        if vix_value is not None:
            indicators["vix"] = float(vix_value)
            vix_classification = self._classify_by_vix(vix_value)
            source = "vix"

        # 计算 realized_vol (复用 VolTargetController)
        realized_vol: float | None = None
        if daily_returns is not None and len(daily_returns) > 0:
            if self._vol_controller is not None:
                try:
                    realized_vol = float(
                        self._vol_controller.calc_realized_vol(daily_returns)
                    )
                    indicators["realized_vol"] = realized_vol
                    rv_classification = self._classify_by_rv(realized_vol)
                    if source == "fallback":
                        source = "realized_vol"
                except (ValueError, KeyError, AttributeError, OSError) as e:
                    logger.warning("calc_realized_vol 失败: %s", e)

        # 辅助指标
        if psi_value is not None:
            indicators["psi"] = float(psi_value)
        if current_drawdown is not None:
            indicators["current_drawdown"] = float(current_drawdown)

        # 各分支一致性字典结构异构 (bool/str/float 混合), 先声明宽类型再赋值
        consistency: dict[str, Any]
        # Step 2: 决定主分类
        if vix_classification and rv_classification:
            # 一致性校验: 不一致时取更保守档
            if vix_classification == rv_classification:
                label = vix_classification
                confidence = 0.85
                consistency = {"consistent": True, "confidence_adjustment": 0.0}
            else:
                # 取更保守 (regime 顺序更靠后)
                label = self._more_conservative(vix_classification, rv_classification)
                confidence = 0.50
                consistency = {
                    "consistent": False,
                    "vix_classification": vix_classification,
                    "realized_vol_classification": rv_classification,
                    "chosen": label,
                    "confidence_adjustment": -0.35,
                }
        elif vix_classification:
            label = vix_classification
            confidence = 0.75
            consistency = {"consistent": "no_rv_data", "confidence_adjustment": 0.0}
        elif rv_classification:
            label = rv_classification
            confidence = 0.65
            consistency = {"consistent": "no_vix_data", "confidence_adjustment": 0.0}
        else:
            # 都缺失: 默认 neutral + 低 confidence
            label = REGIME_NEUTRAL
            confidence = 0.30
            consistency = {"consistent": "no_data", "confidence_adjustment": 0.0}

        # Step 3: 辅助修正
        if psi_value is not None and psi_value > 0.25:
            confidence = max(0.0, confidence - 0.20)

        if current_drawdown is not None:
            if current_drawdown > 0.12:
                # 回撤 > 12%: 至少 bear
                label = self._more_conservative(label, REGIME_BEAR)
            elif current_drawdown > 0.05:
                # 回撤 > 5%: 至少 neutral
                label = self._more_conservative(label, REGIME_NEUTRAL)

        # Step 4: 对齐 dynamic_hedge_policy
        aligned_ratio = ALIGNED_HEDGE_RATIOS.get(label, 0.40)
        hedge_key = self._regime_to_hedge_key(label)

        return VolRegime(
            label=label,
            confidence=round(confidence, 4),
            source=source,
            indicators=indicators,
            aligned_hedge_ratio=aligned_ratio,
            hedge_policy_key=hedge_key,
            consistency_check=consistency,
        )

    def _classify_by_vix(self, vix: float) -> str:
        """VIX 分类."""
        if vix < VIX_THRESHOLDS["bull"]:
            return REGIME_BULL
        if vix < VIX_THRESHOLDS["neutral"]:
            return REGIME_NEUTRAL
        if vix < VIX_THRESHOLDS["bear"]:
            return REGIME_BEAR
        return REGIME_CRISIS

    def _classify_by_rv(self, rv: float) -> str:
        """realized_vol 分类."""
        if rv < RV_THRESHOLDS["bull"]:
            return REGIME_BULL
        if rv < RV_THRESHOLDS["neutral"]:
            return REGIME_NEUTRAL
        if rv < RV_THRESHOLDS["bear"]:
            return REGIME_BEAR
        return REGIME_CRISIS

    @staticmethod
    def _more_conservative(a: str, b: str) -> str:
        """取更保守的 regime (顺序更靠后)."""
        return a if REGIME_ORDER.index(a) >= REGIME_ORDER.index(b) else b

    @staticmethod
    def _regime_to_hedge_key(regime: str) -> str:
        """regime 标签 → portfolio.yaml dynamic_hedge_policy 的 key."""
        return {
            REGIME_BULL: "bull_market",
            REGIME_NEUTRAL: "neutral_market",
            REGIME_BEAR: "bear_market",
            REGIME_CRISIS: "crisis_mode",
        }.get(regime, "neutral_market")

    # ============================================================
    # 决策层 (Decide)
    # ============================================================

    def compute_weights(
        self,
        current_weights: dict[str, float],
        regime: VolRegime | None = None,
        vix_value: float | None = None,
        daily_returns: list[float] | None = None,
        psi_value: float | None = None,
        current_drawdown: float | None = None,
    ) -> WeightSuggestion:
        """根据 regime 计算 8 类风格的建议权重.

        Phase 0: 仅生成建议, 不修改 portfolio.yaml.

        Args:
            current_weights: 当前权重 (style → weight, 如 {"科技": 0.235, ...})
            regime: 已识别的 VolRegime (None=自动识别)
            vix_value: VIX 值 (regime=None 时使用)
            daily_returns: 日收益率序列 (regime=None 时使用)
            psi_value: PSI 值 (可选)
            current_drawdown: 当前回撤 (可选)

        Returns:
            WeightSuggestion 对象
        """
        timestamp = now_bj().isoformat()

        # Flag 检查 (HC-1)
        if not self._enabled:
            return WeightSuggestion.noop(
                reason=f"feature_flag_disabled ({self.feature_flag_name}=False)"
            )

        # Regime 识别
        if regime is None:
            regime = self.sense_regime(
                vix_value=vix_value,
                daily_returns=daily_returns,
                psi_value=psi_value,
                current_drawdown=current_drawdown,
            )

        # 应用 4×8 矩阵
        multipliers = self.weight_matrix.get(
            regime.label, self.weight_matrix[REGIME_NEUTRAL]
        )
        raw_suggested: dict[str, float] = {}
        applied_multipliers: dict[str, float] = {}

        for style, current_w in current_weights.items():
            if style in multipliers:
                m = multipliers[style]
            else:
                # 不在 8 类内的 (如 "制造" "顺周期" "其他") 按 neutral 倍数 1.0
                m = 1.0
            applied_multipliers[style] = m
            raw_suggested[style] = current_w * m

        # 约束执行
        constraints_cfg = self._config.get("constraints", {}) or {}
        max_single = float(
            constraints_cfg.get("max_single_position", DEFAULT_MAX_SINGLE_POSITION)
        )
        max_sector = float(
            constraints_cfg.get("max_sector_exposure", DEFAULT_MAX_SECTOR_EXPOSURE)
        )
        cash_floor = float(constraints_cfg.get("cash_floor", DEFAULT_CASH_FLOOR))

        final_suggested, constraints_applied = self.enforce_constraints(
            raw_suggested,
            max_single_position=max_single,
            max_sector_exposure=max_sector,
            cash_floor=cash_floor,
        )

        # 计算 deltas
        deltas = {
            style: final_suggested.get(style, 0.0) - current_weights.get(style, 0.0)
            for style in current_weights
        }

        # 触发理由
        trigger = self._build_trigger_reason(regime, applied_multipliers)

        return WeightSuggestion(
            timestamp=timestamp,
            regime=regime,
            current_weights=dict(current_weights),
            suggested_weights=final_suggested,
            multipliers=applied_multipliers,
            deltas=deltas,
            constraints_applied=constraints_applied,
            confidence=regime.confidence,
            trigger_reason=trigger,
            observation_phase=True,
        )

    def _build_trigger_reason(
        self, regime: VolRegime, multipliers: dict[str, float]
    ) -> str:
        """构建人类可读的触发理由."""
        ind = regime.indicators
        ind_str = ", ".join(f"{k}={v}" for k, v in ind.items()) if ind else "无指标"

        # 找出调整最大的三类
        sorted_styles = sorted(
            multipliers.items(), key=lambda x: abs(x[1] - 1.0), reverse=True
        )
        top3 = sorted_styles[:3]
        top3_str = ", ".join(f"{s}×{m:.2f}" for s, m in top3)

        return (
            f"{regime.source}={ind_str} → {regime.label} regime "
            f"(confidence={regime.confidence:.2f}), 主要调整: {top3_str}"
        )

    # ============================================================
    # 约束层 (硬执行)
    # ============================================================

    def enforce_constraints(
        self,
        suggested_weights: dict[str, float],
        max_single_position: float = DEFAULT_MAX_SINGLE_POSITION,
        max_sector_exposure: float = DEFAULT_MAX_SECTOR_EXPOSURE,
        cash_floor: float = DEFAULT_CASH_FLOOR,
    ) -> tuple[dict[str, float], list[str]]:
        """强制约束: 单标的≤max_single, 单一风格≤max_sector, 现金≥cash_floor, 总和=1.0.

        Args:
            suggested_weights: 原始建议权重 (style → weight)
            max_single_position: 单标的权重上限 (默认 0.08)
            max_sector_exposure: 单一风格权重上限 (默认 0.30)
            cash_floor: 现金下限 (默认 0.05)

        Returns:
            (调整后权重, 调整说明列表)
        """
        constraints_applied: list[str] = []
        weights = dict(suggested_weights)

        # 1. 负值清洗 (输入防御, 提前到最先 — 后续缩放/归一不引入新负值)
        for style, w in list(weights.items()):
            if w < 0:
                constraints_applied.append(
                    f"constraint.negative_protection: {style} {w:.4f} < 0 → 归零"
                )
                weights[style] = 0.0

        # 2. 单一风格上限 (max_sector_exposure)
        # 注意: 这里 style 级别的权重, 单标的约束在标的级才生效
        # 但由于本模块按风格大类输出, max_single_position 作为风格级冗余保护
        for style, w in list(weights.items()):
            cap = min(
                max_single_position * 4, max_sector_exposure
            )  # 风格级: 单标的的 4 倍或 sector 上限
            if w > max_sector_exposure:
                constraints_applied.append(
                    f"constraint.max_sector_exposure: {style} {w:.4f} > {max_sector_exposure} → 裁剪"
                )
                weights[style] = max_sector_exposure
            elif w > cap:
                constraints_applied.append(
                    f"constraint.max_single_position×4: {style} {w:.4f} > {cap:.4f} → 裁剪"
                )
                weights[style] = cap

        # 3. 现金下限 (SC-15 修复: 赤字从非现金等比扣减, 不凭空注权)
        cash_weight = weights.get("现金", 0.0)
        if cash_weight < cash_floor:
            shortfall = cash_floor - cash_weight
            non_cash = {k: v for k, v in weights.items() if k != "现金"}
            non_cash_total = sum(non_cash.values())
            if non_cash_total > 1e-12 and non_cash_total >= shortfall:
                scale = (non_cash_total - shortfall) / non_cash_total
                for k in non_cash:
                    weights[k] = weights[k] * scale
                constraints_applied.append(
                    f"constraint.cash_floor: 现金 {cash_weight:.4f} < {cash_floor}"
                    f" → 抬升至 {cash_floor}, 非现金等比×{scale:.4f}"
                )
            else:
                constraints_applied.append(
                    f"constraint.cash_floor.INFEASIBLE: 现金 {cash_weight:.4f} <"
                    f" {cash_floor} 且非现金总量 {non_cash_total:.4f} 不足以等比补足"
                    " → 强制抬升, 交由归一步骤收紧"
                )
            weights["现金"] = cash_floor

        # 4. 总和归一 (SC-15 修复: 盈余归现金 / 超额先扣现金至下限再等比扣非现金)
        total = sum(weights.values())
        if abs(total - 1.0) > 1e-6:
            cash_weight = weights.get("现金", 0.0)
            if total < 1.0:
                weights["现金"] = cash_weight + (1.0 - total)
                constraints_applied.append(
                    f"constraint.sum_to_one: 总和={total:.4f},"
                    f" 盈余 {1.0 - total:+.4f} 归现金"
                )
            else:
                deficit = total - 1.0
                cash_absorbable = max(0.0, cash_weight - cash_floor)
                from_cash = min(deficit, cash_absorbable)
                weights["现金"] = cash_weight - from_cash
                remaining = deficit - from_cash
                if remaining > 1e-12:
                    non_cash = {k: v for k, v in weights.items() if k != "现金"}
                    non_cash_total = sum(non_cash.values())
                    if non_cash_total > 1e-12:
                        scale = max(0.0, (non_cash_total - remaining) / non_cash_total)
                        for k in non_cash:
                            weights[k] = weights[k] * scale
                        constraints_applied.append(
                            f"constraint.sum_to_one: 总和={total:.4f}, 超额"
                            f" {deficit:.4f} = 现金吸收 {from_cash:.4f} +"
                            f" 非现金等比×{scale:.4f}"
                        )
                    else:
                        constraints_applied.append(
                            f"constraint.sum_to_one.INFEASIBLE: 总和={total:.4f}"
                            f" 超额 {deficit:.4f} 且无非现金可扣"
                        )
                else:
                    constraints_applied.append(
                        f"constraint.sum_to_one: 总和={total:.4f}, 超额"
                        f" {deficit:.4f} 由现金下限以上部分吸收"
                    )

        # 5. 最终校验 (SC-15 修复: 不再只记日志 — 违反时显式告警 + 兜底修正)
        final_total = sum(weights.values())
        final_cash = weights.get("现金", 0.0)
        has_negative = any(w < -1e-9 for w in weights.values())
        if abs(final_total - 1.0) <= 1e-6 and final_cash >= cash_floor - 1e-6 and not has_negative:
            constraints_applied.append(
                f"constraint.final_check: PASS 总和={final_total:.6f},"
                f" 现金={final_cash:.4f} (floor={cash_floor})"
            )
        else:
            constraints_applied.append(
                f"constraint.final_check.FAIL: 总和={final_total:.6f},"
                f" 现金={final_cash:.4f}, 负值={has_negative} → 兜底修正"
            )
            logger.warning(
                "enforce_constraints 终检未收敛: 总和=%.6f 现金=%.4f 负值=%s,"
                " 输入=%s → 执行兜底修正",
                final_total,
                final_cash,
                has_negative,
                suggested_weights,
            )
            for style, w in list(weights.items()):
                if w < 0:
                    weights[style] = 0.0
            non_cash = {k: v for k, v in weights.items() if k != "现金"}
            non_cash_total = sum(non_cash.values())
            target_non_cash = max(0.0, 1.0 - cash_floor)
            if non_cash_total > target_non_cash + 1e-12:
                scale = target_non_cash / non_cash_total
                for k in non_cash:
                    weights[k] = weights[k] * scale
            weights["现金"] = 1.0 - sum(v for k, v in weights.items() if k != "现金")
            constraints_applied.append(
                f"constraint.final_fix: 总和={sum(weights.values()):.6f},"
                f" 现金={weights['现金']:.4f}"
            )

        return weights, constraints_applied

    # ============================================================
    # 输出层 (Act, Phase 0: 仅写文件)
    # ============================================================

    def emit_suggestion(
        self,
        suggestion: WeightSuggestion,
        reports_dir: Path | None = None,
    ) -> Path:
        """持久化建议到 reports/evolution/vol_regime_weights_YYYY-MM-DD.json.

        Phase 0: 只写文件, 不调用任何 portfolio.yaml 写入器.

        Args:
            suggestion: WeightSuggestion 对象
            reports_dir: 报告目录 (None=使用实例默认)

        Returns:
            报告文件路径
        """
        out_dir = Path(reports_dir) if reports_dir else self.reports_dir
        out_dir.mkdir(parents=True, exist_ok=True)

        date_str = now_bj().strftime("%Y-%m-%d")
        filename = f"vol_regime_weights_{date_str}.json"
        report_path = out_dir / filename

        report_data = suggestion.to_dict()
        report_data["audit"].update(
            {
                "feature_flag": f"{self.feature_flag_name}={self._enabled}",
                "config_source": str(self.config_path),
                "vol_controller": (
                    type(self._vol_controller).__name__
                    if self._vol_controller
                    else "None"
                ),
            }
        )

        with report_path.open("w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)

        logger.info("权重建议报告已写入: %s", report_path)
        return report_path

    # ============================================================
    # 集成层 (与 EvolutionOrchestrator 对接)
    # ============================================================

    def run_cycle(
        self,
        portfolio_snapshot: dict[str, Any],
        vix_value: float | None = None,
        daily_returns: list[float] | None = None,
        psi_value: float | None = None,
        current_drawdown: float | None = None,
        orchestrator: Any = None,
        reports_dir: Path | None = None,
    ) -> dict[str, Any]:
        """端到端运行一次: sense → compute → enforce → emit → log.

        Args:
            portfolio_snapshot: portfolio.yaml 的 assets 快照 (style → weight, 或完整 assets 列表)
            vix_value: VIX 值
            daily_returns: 日收益率序列
            psi_value: PSI 值 (可选)
            current_drawdown: 当前回撤 (可选)
            orchestrator: EvolutionOrchestrator 实例 (可选, 用于 log_decision)
            reports_dir: 报告目录 (None=使用实例默认)

        Returns:
            结果字典: {status, regime, report_path, decision_logged, confidence}
        """
        # Flag 检查 (HC-1)
        if not self._enabled:
            return {
                "status": "disabled",
                "reason": f"feature_flag_disabled ({self.feature_flag_name}=False)",
                "suggestion": WeightSuggestion.noop().to_dict(),
            }

        # 解析 portfolio_snapshot 为 style → weight
        current_weights = self._parse_portfolio_snapshot(portfolio_snapshot)

        # 计算
        suggestion = self.compute_weights(
            current_weights=current_weights,
            vix_value=vix_value,
            daily_returns=daily_returns,
            psi_value=psi_value,
            current_drawdown=current_drawdown,
        )

        # 输出报告
        report_path = self.emit_suggestion(suggestion, reports_dir=reports_dir)

        # 写入 decisions.jsonl (复用 EvolutionOrchestrator 审计链)
        decision_logged = False
        if orchestrator is not None:
            try:
                decision_logged = orchestrator.log_decision(
                    report=None,
                    action="evaluate_only",
                    reason=f"vol_regime_suggestion: regime={suggestion.regime.label}, "
                    f"confidence={suggestion.confidence:.2f}",
                    metrics=None,
                    extra_payload={
                        "vol_regime_suggestion": suggestion.to_dict(),
                        "report_path": str(report_path),
                    },
                )
            except TypeError:
                # 兼容: 旧版 log_decision 不支持 extra_payload
                decision_logged = orchestrator.log_decision(
                    report=None,
                    action="evaluate_only",
                    reason=f"vol_regime_suggestion: regime={suggestion.regime.label}",
                    metrics=None,
                )
            except (ValueError, KeyError, AttributeError, OSError) as e:
                logger.warning("log_decision 失败, 不阻塞: %s", e)

        return {
            "status": "ok",
            "regime": suggestion.regime.label,
            "confidence": suggestion.confidence,
            "report_path": str(report_path),
            "decision_logged": decision_logged,
            "suggested_weights": suggestion.suggested_weights,
        }

    def _parse_portfolio_snapshot(self, snapshot: dict[str, Any]) -> dict[str, float]:
        """解析 portfolio 快照为 style → weight 字典.

        支持两种输入:
            1. {style: weight} (如 {"科技": 0.235, ...})
            2. {assets: [{style: "科技", weight: 0.045}, ...]} (portfolio.yaml 结构)
        """
        # 情况 1: 直接是 style → weight
        if all(isinstance(v, (int, float)) for v in snapshot.values()):
            return {k: float(v) for k, v in snapshot.items()}

        # 情况 2: portfolio.yaml 结构
        assets = snapshot.get("assets", [])
        if not isinstance(assets, list):
            logger.warning("portfolio_snapshot 格式无法识别: %s", type(assets))
            return {}

        # 按 style 聚合
        style_weights: dict[str, float] = dict.fromkeys(STYLE_CATEGORIES, 0.0)
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            style = asset.get("style", "")
            weight = float(asset.get("weight", 0.0))
            if style in style_weights:
                style_weights[style] += weight
            else:
                # 不在 8 类内的 (如 "制造" "顺周期") 归入 "其他", 单独存储
                style_weights.setdefault(style, 0.0)
                style_weights[style] += weight

        return style_weights

    # ============================================================
    # 扩展点 (Phase 1+ 预留, Phase 0 不实现)
    # ============================================================

    def apply_to_portfolio(
        self,
        yaml_path: Path,
        dual_signature: tuple[str, str],
    ) -> bool:
        """Phase 1: 双签后写入 portfolio.yaml (Phase 0 不实现).

        Args:
            yaml_path: portfolio.yaml 路径
            dual_signature: (发起人, 风控负责人) 双签

        Returns:
            是否成功

        Raises:
            NotImplementedError: Phase 0 内调用此方法
        """
        raise NotImplementedError(
            "apply_to_portfolio 是 Phase 1 扩展点, Phase 0 观察期内不可调用. "
            "请等待观察期结束后通过双签启用."
        )

    def backtest(
        self,
        historical_returns: list[float],
        historical_vix: list[float],
    ) -> dict[str, Any]:
        """Phase 2: 接入 fast_backtest.py 做历史回测 (Phase 0 不实现).

        Args:
            historical_returns: 历史日收益率序列
            historical_vix: 历史 VIX 序列

        Returns:
            回测结果

        Raises:
            NotImplementedError: Phase 0/1 内调用此方法
        """
        raise NotImplementedError("backtest 是 Phase 2 扩展点, 当前不可用.")


# ============================================================
# 便捷函数
# ============================================================


def classify_regime_by_vol(
    vix: float | None = None,
    realized_vol: float | None = None,
    psi: float | None = None,
    drawdown: float | None = None,
) -> VolRegime:
    """便捷函数: 直接分类 regime (无需实例化 VolRegimeWeighter).

    Args:
        vix: VIX 值 (优先)
        realized_vol: 已实现波动率 (备选)
        psi: PSI 值 (辅助)
        drawdown: 当前回撤 (辅助)

    Returns:
        VolRegime 对象

    Example:
        >>> regime = classify_regime_by_vol(vix=32.5)
        >>> print(regime.label, regime.confidence)
        bear 0.75
    """
    weighter = VolRegimeWeighter()
    # 若 daily_returns 为 None, vol_controller 不会计算 RV
    # 这里直接构造一个临时 daily_returns 列表用于触发 RV 计算
    daily_returns = None
    if realized_vol is not None and realized_vol > 0:
        # 反推一个等价日波动率 (仅用于触发分类, 不影响结果)
        # realized_vol (年化) / sqrt(252) = 日波动率
        import math

        daily_vol = realized_vol / math.sqrt(252)
        # 构造 20 个等价日收益 (均值为 0, 标准差为 daily_vol)
        # 注意: 这里只是为了让 vol_controller 计算出接近的 realized_vol
        # 实际使用时应直接传入真实 daily_returns
        daily_returns = [daily_vol, -daily_vol] * 10

    return weighter.sense_regime(
        vix_value=vix,
        daily_returns=daily_returns,
        psi_value=psi,
        current_drawdown=drawdown,
    )


# ============================================================
# CLI 入口
# ============================================================


def _cli_main() -> int:
    """CLI 入口: python -m utils.alpha.vol_regime_weighter [--vix 32.5]"""
    import argparse

    parser = argparse.ArgumentParser(description="波动率 Regime 权重建议器")
    parser.add_argument("--vix", type=float, default=None, help="VIX 值")
    parser.add_argument("--reports-dir", type=str, default=None, help="报告输出目录")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    weighter = VolRegimeWeighter(
        reports_dir=Path(args.reports_dir) if args.reports_dir else None
    )

    if not weighter.enabled:
        print(f"❌ Feature Flag {weighter.feature_flag_name}=False, 模块禁用")
        print(
            "   启用方法: 在 reports/flag_overrides/USE_VOL_REGIME_WEIGHTER.json 写入"
        )
        print('            {"enabled": true, "signer": "your_name"}')
        return 1

    # 加载 portfolio.yaml 当前权重
    portfolio_path = Path("configs/account_structure.yaml")
    if not portfolio_path.exists():
        print(f"❌ 找不到 {portfolio_path}")
        return 1

    with portfolio_path.open("r", encoding="utf-8") as f:
        portfolio_data = yaml.safe_load(f) or {}

    result = weighter.run_cycle(
        portfolio_snapshot=portfolio_data,
        vix_value=args.vix,
    )

    print("\n" + "=" * 60)
    print("📊 波动率 Regime 权重建议报告")
    print("=" * 60)
    print(f"状态: {result['status']}")
    if result["status"] == "ok":
        print(f"Regime: {result['regime']}")
        print(f"置信度: {result['confidence']:.2f}")
        print(f"报告路径: {result['report_path']}")
        print(f"决策已记录: {result['decision_logged']}")
        print("\n建议权重:")
        for style, w in result.get("suggested_weights", {}).items():
            print(f"  {style}: {w:.4f}")
    else:
        print(f"原因: {result.get('reason', '未知')}")

    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_cli_main())


__all__ = [
    "VolRegimeWeighter",
    "VolRegime",
    "WeightSuggestion",
    "classify_regime_by_vol",
    "DEFAULT_WEIGHT_MATRIX",
    "STYLE_CATEGORIES",
    "REGIME_BULL",
    "REGIME_NEUTRAL",
    "REGIME_BEAR",
    "REGIME_CRISIS",
]
