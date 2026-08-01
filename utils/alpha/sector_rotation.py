"""行业轮动信号生成器 — 终极量化交易系统 8.4 (T4.4).

模块整合 8.4 — ARCHITECTURE §2.4
任务: T4.4

设计原则:
    1. 三维度信号融合: momentum (动量) + flow (资金流) + valuation (估值)
    2. 与 MacroIndicatorManager 集成: 根据 regime 调整信号权重 (HC-3 risk_managed)
    3. 支持 24 个申万一级行业
    4. Feature Flag 透传 (HC-1): USE_SECTOR_ROTATION=False 时降级为等权模式
    5. ConfigManager 4 级优先级解析 (HC-5)

API:
    from utils.alpha.sector_rotation import SectorRotation, generate_signals

    # 推荐: 使用快捷函数
    signals = generate_signals(sector_returns={"801010": [0.01, ...], ...})

    # 或使用类 (需要自定义配置时)
    sr = SectorRotation()
    signals = sr.generate_signals(
        sector_returns={"801010": [0.01, -0.005, ...], ...},
        sector_flows={"801010": 0.005, ...},  # 可选
        sector_valuations={"801010": 0.3, ...},  # 可选
        regime="bull",  # 可选, 来自 MacroIndicatorManager
    )

硬约束:
    - HC-1: flag=False 时降级为等权模式 (所有行业 score=1.0)
    - HC-3: risk_managed=True (regime 调整权重)
    - HC-5: 配置走 ConfigManager 4 级优先级
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

from utils.config_manager import get_config

logger = logging.getLogger("sector_rotation")


# ============================================================
# 常量
# ============================================================

# 默认信号权重
DEFAULT_SIGNAL_WEIGHTS: dict[str, float] = {
    "momentum": 0.5,
    "flow": 0.3,
    "valuation": 0.2,
}

# 默认信号阈值
DEFAULT_THRESHOLDS: dict[str, float] = {
    "momentum_strong": 3.0,
    "momentum_weak": -3.0,
    "flow_in_strong": 1.0,
    "flow_out_strong": -1.0,
    "valuation_cheap": 0.3,
    "valuation_expensive": 0.7,
}

# 默认 Regime 调整 (与 macro_indicator.regime_adjustments 联动)
DEFAULT_REGIME_ADJUSTMENTS: dict[str, dict[str, float]] = {
    "bull": {"momentum_boost": 1.2, "flow_boost": 1.0, "valuation_boost": 0.8},
    "bear": {"momentum_boost": 0.6, "flow_boost": 0.8, "valuation_boost": 1.5},
    "choppy": {"momentum_boost": 0.8, "flow_boost": 1.2, "valuation_boost": 1.2},
    "rebound": {"momentum_boost": 1.1, "flow_boost": 1.1, "valuation_boost": 1.0},
    "default": {"momentum_boost": 1.0, "flow_boost": 1.0, "valuation_boost": 1.0},
}

# 默认 Top N / Bottom N
DEFAULT_TOP_N = 3
DEFAULT_BOTTOM_N = 3
DEFAULT_LOOKBACK_DAYS = 20
DEFAULT_MIN_SAMPLES = 10

# Feature Flag 名称
FLAG_NAME = "USE_SECTOR_ROTATION"

# 配置文件名 (走 ConfigManager 4 级优先级)
DEFAULT_CONFIG_NAME = "sector_rotation"


# ============================================================
# 异常定义
# ============================================================


class SectorRotationError(Exception):
    """SectorRotation 基础异常."""


class InsufficientSectorsError(SectorRotationError):
    """行业样本不足."""


class InvalidSignalError(SectorRotationError):
    """无效的信号维度."""


# ============================================================
# 数据类
# ============================================================


@dataclass
class SectorSignal:
    """单行业信号."""

    code: str = ""
    name: str = ""
    # 原始信号
    momentum: float = 0.0  # 涨跌幅 %
    flow: float = 0.0  # 资金净流入 %
    valuation: float = 0.5  # 估值分位数 (0-1)
    # 评分 (0-100, 越高越看好)
    momentum_score: float = 50.0
    flow_score: float = 50.0
    valuation_score: float = 50.0
    composite_score: float = 50.0
    # 信号标签
    label: str = "NEUTRAL"  # STRONG_BUY / BUY / NEUTRAL / SELL / STRONG_SELL
    rank: int = 0  # 综合排名 (1=最佳)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RotationResult:
    """行业轮动结果."""

    signals: list[SectorSignal] = field(default_factory=list)
    top_sectors: list[str] = field(default_factory=list)  # 推荐行业 code 列表
    bottom_sectors: list[str] = field(default_factory=list)  # 规避行业 code 列表
    regime: str = "unknown"
    regime_adjusted: bool = False
    n_sectors: int = 0
    timestamp: str = ""
    status: str = "ok"  # ok / feature_flag_disabled / insufficient_data

    def to_dict(self) -> dict[str, Any]:
        return {
            "signals": [s.to_dict() for s in self.signals],
            "top_sectors": self.top_sectors,
            "bottom_sectors": self.bottom_sectors,
            "regime": self.regime,
            "regime_adjusted": self.regime_adjusted,
            "n_sectors": self.n_sectors,
            "timestamp": self.timestamp,
            "status": self.status,
        }


# ============================================================
# 信号评分函数
# ============================================================


def compute_momentum_score(
    returns: Sequence[float],
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    thresholds: dict[str, float] | None = None,
) -> float:
    """计算动量评分 (0-100).

    Args:
        returns: 日收益率序列
        lookback_days: 回看天数
        thresholds: 阈值 (momentum_strong / momentum_weak)

    Returns:
        动量评分 (0-100, 越高越强势)
    """
    th = thresholds or DEFAULT_THRESHOLDS
    rets = list(returns[-lookback_days:]) if len(returns) > lookback_days else list(returns)
    if not rets:
        return 50.0

    # 累计涨跌幅
    cumulative = 1.0
    for r in rets:
        cumulative *= 1.0 + r
    pct = (cumulative - 1.0) * 100.0  # 转为 %

    # 线性映射到评分
    strong = th.get("momentum_strong", 3.0)
    weak = th.get("momentum_weak", -3.0)

    if pct >= strong:
        return 100.0
    if pct <= weak:
        return 0.0
    # 线性插值: weak -> 0, 0 -> 50, strong -> 100
    if pct >= 0:
        return 50.0 + (pct / strong) * 50.0
    return 50.0 + (pct / abs(weak)) * 50.0


def compute_flow_score(
    flow_pct: float,
    thresholds: dict[str, float] | None = None,
) -> float:
    """计算资金流评分 (0-100).

    Args:
        flow_pct: 净流入占市值百分比 (%)
        thresholds: 阈值 (flow_in_strong / flow_out_strong)

    Returns:
        资金流评分 (0-100, 越高越强势)
    """
    th = thresholds or DEFAULT_THRESHOLDS
    in_strong = th.get("flow_in_strong", 1.0)
    out_strong = th.get("flow_out_strong", -1.0)

    if flow_pct >= in_strong:
        return 100.0
    if flow_pct <= out_strong:
        return 0.0
    if flow_pct >= 0:
        return 50.0 + (flow_pct / in_strong) * 50.0
    return 50.0 + (flow_pct / abs(out_strong)) * 50.0


def compute_valuation_score(
    quantile: float,
    thresholds: dict[str, float] | None = None,
) -> float:
    """计算估值评分 (0-100).

    Args:
        quantile: 估值分位数 (0-1, 0=最低估, 1=最高估)
        thresholds: 阈值 (valuation_cheap / valuation_expensive)

    Returns:
        估值评分 (0-100, 越高越低估=越看好)
    """
    th = thresholds or DEFAULT_THRESHOLDS
    cheap = th.get("valuation_cheap", 0.3)
    expensive = th.get("valuation_expensive", 0.7)

    q = max(0.0, min(1.0, quantile))
    if q <= cheap:
        return 100.0
    if q >= expensive:
        return 0.0
    # 线性插值: expensive -> 0, cheap -> 100
    return 100.0 - (q - cheap) / (expensive - cheap) * 100.0


def classify_signal_label(score: float) -> str:
    """根据综合评分分类信号标签.

    Args:
        score: 综合评分 (0-100)

    Returns:
        标签: STRONG_BUY / BUY / NEUTRAL / SELL / STRONG_SELL
    """
    if score >= 80:
        return "STRONG_BUY"
    if score >= 60:
        return "BUY"
    if score >= 40:
        return "NEUTRAL"
    if score >= 20:
        return "SELL"
    return "STRONG_SELL"


# ============================================================
# 主类: SectorRotation
# ============================================================


class SectorRotation:
    """行业轮动信号生成器.

    功能:
        1. 三维度信号融合 (momentum + flow + valuation)
        2. 根据 regime 调整信号权重 (HC-3 risk_managed)
        3. 输出 Top N / Bottom N 行业推荐
        4. Feature Flag 透传 (HC-1): 关闭时降级为等权模式

    集成 MacroIndicatorManager:
        regime = macro_mgr.get_regime().label
        signals = sr.generate_signals(..., regime=regime)
    """

    def __init__(
        self,
        config_name: str = DEFAULT_CONFIG_NAME,
        feature_flag_name: str = FLAG_NAME,
        config: dict[str, Any] | None = None,
    ) -> None:
        """初始化行业轮动信号生成器.

        Args:
            config_name: ConfigManager 配置名
            feature_flag_name: Feature Flag 名称
            config: 显式配置 (优先于 ConfigManager)
        """
        self._feature_flag_name = feature_flag_name
        self._config = config or self._load_config(config_name)
        self._settings = self._config.get("settings", {}) or {}
        self._sectors_cfg = self._config.get("sectors", []) or []
        self._signal_weights = dict(DEFAULT_SIGNAL_WEIGHTS)
        self._signal_weights.update(self._config.get("signal_weights", {}) or {})
        self._thresholds = dict(DEFAULT_THRESHOLDS)
        self._thresholds.update(self._config.get("thresholds", {}) or {})
        self._regime_adjustments = dict(DEFAULT_REGIME_ADJUSTMENTS)
        self._regime_adjustments.update(self._config.get("regime_adjustments", {}) or {})

        # 参数
        self._lookback_days = int(self._settings.get("lookback_days", DEFAULT_LOOKBACK_DAYS))
        self._top_n = int(self._settings.get("top_n", DEFAULT_TOP_N))
        self._bottom_n = int(self._settings.get("bottom_n", DEFAULT_BOTTOM_N))
        self._min_samples = int(self._settings.get("min_samples", DEFAULT_MIN_SAMPLES))

        # 构建行业代码 -> 名称映射
        self._sector_names: dict[str, str] = {}
        for s in self._sectors_cfg:
            if isinstance(s, dict):
                self._sector_names[s.get("code", "")] = s.get("name", "")

    def _load_config(self, config_name: str) -> dict[str, Any]:
        """加载配置 (走 ConfigManager 4 级优先级, HC-5)."""
        try:
            cfg = get_config(config_name, default={}) or {}
            if not cfg:
                logger.warning("配置未找到: %s, 使用默认配置", config_name)
            return cfg
        except Exception as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            logger.warning("配置加载失败: %s (%s), 使用默认配置", config_name, e)
            return {}

    def _is_enabled(self) -> bool:
        """检查 Feature Flag 是否启用 (HC-1)."""
        try:
            from utils.infra.feature_flags import is_enabled

            return bool(is_enabled(self._feature_flag_name))
        except Exception:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            return False

    def _get_regime_adjustment(self, regime: str) -> dict[str, float]:
        """获取 regime 调整因子."""
        return self._regime_adjustments.get(
            regime,
            self._regime_adjustments.get("default", {"momentum_boost": 1.0, "flow_boost": 1.0, "valuation_boost": 1.0}),
        )

    def generate_signals(
        self,
        sector_returns: dict[str, Sequence[float]] | None = None,
        sector_flows: dict[str, float] | None = None,
        sector_valuations: dict[str, float] | None = None,
        regime: str = "unknown",
    ) -> RotationResult:
        """生成行业轮动信号.

        Args:
            sector_returns: {行业代码: 日收益率序列}
            sector_flows: {行业代码: 净流入占市值 %}
            sector_valuations: {行业代码: 估值分位数 (0-1)}
            regime: 当前 regime (来自 MacroIndicatorManager.get_regime().label)

        Returns:
            RotationResult 含所有行业信号 + Top/Bottom 推荐
        """
        # HC-1 透传: Feature Flag 关闭时降级为等权模式
        if not self._is_enabled():
            return self._generate_equal_weight(
                sector_returns=sector_returns,
                regime=regime,
                status="feature_flag_disabled",
            )

        # 收集所有行业代码
        codes: set = set()
        if sector_returns:
            codes.update(sector_returns.keys())
        if sector_flows:
            codes.update(sector_flows.keys())
        if sector_valuations:
            codes.update(sector_valuations.keys())

        if not codes:
            return RotationResult(regime=regime, status="no_data")

        if len(codes) < self._min_samples:
            return self._generate_equal_weight(
                sector_returns=sector_returns,
                regime=regime,
                status="insufficient_data",
            )

        # 获取 regime 调整
        adj = self._get_regime_adjustment(regime)
        regime_adjusted = regime != "unknown"

        # 调整后的权重
        w_mom = self._signal_weights.get("momentum", 0.5) * adj.get("momentum_boost", 1.0)
        w_flow = self._signal_weights.get("flow", 0.3) * adj.get("flow_boost", 1.0)
        w_val = self._signal_weights.get("valuation", 0.2) * adj.get("valuation_boost", 1.0)
        total_w = w_mom + w_flow + w_val
        if total_w > 0:
            w_mom /= total_w
            w_flow /= total_w
            w_val /= total_w

        # 计算每个行业的信号
        signals: list[SectorSignal] = []
        for code in sorted(codes):
            name = self._sector_names.get(code, code)
            rets = (sector_returns or {}).get(code, [])
            flow = (sector_flows or {}).get(code, 0.0)
            val = (sector_valuations or {}).get(code, 0.5)

            mom_score = compute_momentum_score(rets, self._lookback_days, self._thresholds) if rets else 50.0
            flow_score = compute_flow_score(flow, self._thresholds)
            val_score = compute_valuation_score(val, self._thresholds)

            composite = w_mom * mom_score + w_flow * flow_score + w_val * val_score
            label = classify_signal_label(composite)

            signals.append(
                SectorSignal(
                    code=code,
                    name=name,
                    momentum=sum(rets) * 100.0 if rets else 0.0,  # 累计涨跌幅 %
                    flow=flow,
                    valuation=val,
                    momentum_score=mom_score,
                    flow_score=flow_score,
                    valuation_score=val_score,
                    composite_score=composite,
                    label=label,
                )
            )

        # 按综合评分降序排名
        signals.sort(key=lambda s: s.composite_score, reverse=True)
        for i, s in enumerate(signals, 1):
            s.rank = i

        # Top N / Bottom N
        top_codes = [s.code for s in signals[: self._top_n]]
        bottom_codes = [s.code for s in signals[-self._bottom_n :] if self._bottom_n > 0]

        return RotationResult(
            signals=signals,
            top_sectors=top_codes,
            bottom_sectors=bottom_codes,
            regime=regime,
            regime_adjusted=regime_adjusted,
            n_sectors=len(signals),
            status="ok",
        )

    def _generate_equal_weight(
        self,
        sector_returns: dict[str, Sequence[float]] | None,
        regime: str,
        status: str,
    ) -> RotationResult:
        """生成等权信号 (Feature Flag 关闭时降级模式, HC-1)."""
        codes = list((sector_returns or {}).keys())
        signals = [
            SectorSignal(
                code=c,
                name=self._sector_names.get(c, c),
                composite_score=50.0,
                label="NEUTRAL",
                rank=i + 1,
            )
            for i, c in enumerate(sorted(codes))
        ]
        return RotationResult(
            signals=signals,
            top_sectors=[s.code for s in signals[: self._top_n]],
            bottom_sectors=[s.code for s in signals[-self._bottom_n :]] if self._bottom_n > 0 else [],
            regime=regime,
            regime_adjusted=False,
            n_sectors=len(signals),
            status=status,
        )

    # ===== 便捷接口 =====

    def get_top_sectors(
        self,
        sector_returns: dict[str, Sequence[float]] | None = None,
        regime: str = "unknown",
    ) -> list[str]:
        """便捷函数: 获取 Top N 行业代码."""
        result = self.generate_signals(sector_returns=sector_returns, regime=regime)
        return result.top_sectors

    def get_bottom_sectors(
        self,
        sector_returns: dict[str, Sequence[float]] | None = None,
        regime: str = "unknown",
    ) -> list[str]:
        """便捷函数: 获取 Bottom N 行业代码."""
        result = self.generate_signals(sector_returns=sector_returns, regime=regime)
        return result.bottom_sectors


# ============================================================
# 模块级便捷函数
# ============================================================


def generate_signals(
    sector_returns: dict[str, Sequence[float]] | None = None,
    sector_flows: dict[str, float] | None = None,
    sector_valuations: dict[str, float] | None = None,
    regime: str = "unknown",
    config_name: str = DEFAULT_CONFIG_NAME,
) -> RotationResult:
    """便捷函数: 一次性生成行业轮动信号."""
    sr = SectorRotation(config_name=config_name)
    return sr.generate_signals(
        sector_returns=sector_returns,
        sector_flows=sector_flows,
        sector_valuations=sector_valuations,
        regime=regime,
    )


def is_sector_rotation_enabled() -> bool:
    """便捷函数: 检查 Feature Flag 是否启用."""
    try:
        from utils.infra.feature_flags import is_enabled

        return bool(is_enabled(FLAG_NAME))
    except Exception:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
        return False


__all__ = [
    "DEFAULT_BOTTOM_N",
    "DEFAULT_CONFIG_NAME",
    "DEFAULT_LOOKBACK_DAYS",
    "DEFAULT_MIN_SAMPLES",
    "DEFAULT_REGIME_ADJUSTMENTS",
    # 常量
    "DEFAULT_SIGNAL_WEIGHTS",
    "DEFAULT_THRESHOLDS",
    "DEFAULT_TOP_N",
    "FLAG_NAME",
    "InsufficientSectorsError",
    "InvalidSignalError",
    "RotationResult",
    # 主类
    "SectorRotation",
    # 异常
    "SectorRotationError",
    # 数据类
    "SectorSignal",
    "classify_signal_label",
    "compute_flow_score",
    # 评分函数
    "compute_momentum_score",
    "compute_valuation_score",
    # 便捷函数
    "generate_signals",
    "is_sector_rotation_enabled",
]
