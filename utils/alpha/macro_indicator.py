"""宏观经济指标模块 — 终极量化交易系统 8.4 (T4.4).

模块整合 8.4 — ARCHITECTURE §2.4
任务: T4.4

设计原则:
    1. 四类宏观指标接入 (CPI / PMI / M2 / 利率), 通过 DataLayer 复用 P0-P6 降级链
    2. Regime 分类算法迁移自 research/vibe_trading_factor_analysis/validators/regime_conditioner.py
       (bull / bear / choppy / rebound / warmup / unknown / insufficient_samples)
    3. 提供 update() 流式接口 (增量更新), 兼容 research 版 RegimeConditioner.validate() 批量接口
    4. Feature Flag 透传 (HC-1): USE_MACRO_INDICATOR=False 时降级为兼容模式
    5. ConfigManager 4 级优先级解析 (HC-5)
    6. risk_managed=True (HC-3): 根据 regime 输出仓位调整因子和风险预算

API:
    from utils.alpha.macro_indicator import MacroIndicatorManager, RegimeResult

    # 推荐: 使用快捷函数
    regime = classify_regime(benchmark_returns=[0.01, -0.005, ...])

    # 或使用类 (需要自定义配置时)
    mgr = MacroIndicatorManager()
    mgr.update(new_returns=[0.01, -0.005, ...])
    regime = mgr.get_regime()
    # regime = {"label": "bull", "position_factor": 1.0, "risk_budget": 1.2, ...}

    # 获取宏观指标快照
    snapshot = mgr.get_macro_snapshot()
    # snapshot = {"cpi": 2.3, "pmi": 51.2, "m2": 9.5, "rate": 2.8}

硬约束:
    - HC-1: flag=False 时降级为兼容模式 (regime=unknown, position_factor=1.0)
    - HC-3: risk_managed=True (regime 调整仓位)
    - HC-5: 配置走 ConfigManager 4 级优先级
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any, cast

from utils.config_manager import get_config

logger = logging.getLogger("macro_indicator")


# ============================================================
# 常量
# ============================================================

# Regime 标签集合 (兼容 research RegimeConditioner)
REGIME_BULL = "bull"
REGIME_BEAR = "bear"
REGIME_CHOPPY = "choppy"
REGIME_REBOUND = "rebound"
REGIME_WARMUP = "warmup"
REGIME_UNKNOWN = "unknown"
REGIME_INSUFFICIENT = "insufficient_samples"

ALL_REGIMES: list[str] = [
    REGIME_BULL,
    REGIME_BEAR,
    REGIME_CHOPPY,
    REGIME_REBOUND,
    REGIME_WARMUP,
    REGIME_UNKNOWN,
    REGIME_INSUFFICIENT,
]

# 默认参数 (对齐 research RegimeConditioner)
DEFAULT_MA_WINDOW = 60
DEFAULT_CHOPPY_BAND = 0.03
DEFAULT_REBOUND_THRESHOLD = 0.05
DEFAULT_MIN_SAMPLES = 20

# 默认仓位调整因子 (HC-3 risk_managed)
DEFAULT_POSITION_FACTORS: dict[str, float] = {
    REGIME_BULL: 1.0,
    REGIME_BEAR: 0.3,
    REGIME_CHOPPY: 0.5,
    REGIME_REBOUND: 0.7,
    REGIME_WARMUP: 1.0,
    REGIME_UNKNOWN: 1.0,
    REGIME_INSUFFICIENT: 1.0,
}

# 默认风险预算
DEFAULT_RISK_BUDGET: dict[str, float] = {
    REGIME_BULL: 1.2,
    REGIME_BEAR: 0.5,
    REGIME_CHOPPY: 0.8,
    REGIME_REBOUND: 1.0,
    REGIME_WARMUP: 1.0,
    REGIME_UNKNOWN: 1.0,
    REGIME_INSUFFICIENT: 1.0,
}

# Feature Flag 名称
FLAG_NAME = "USE_MACRO_INDICATOR"

# 配置文件名 (走 ConfigManager 4 级优先级)
DEFAULT_CONFIG_NAME = "macro_indicator"


# ============================================================
# 异常定义
# ============================================================


class MacroIndicatorError(Exception):
    """MacroIndicator 基础异常."""


class InsufficientDataError(MacroIndicatorError):
    """数据不足, 无法计算 regime."""


class InvalidRegimeError(MacroIndicatorError):
    """无效的 regime 标签."""


# ============================================================
# 数据类
# ============================================================


@dataclass
class RegimeResult:
    """Regime 分类结果 (兼容 research RegimeConditioner.RegimeResult)."""

    label: str = REGIME_UNKNOWN
    position_factor: float = 1.0
    risk_budget: float = 1.0
    ma_value: float = 0.0
    price_ratio: float = 0.0  # price/MA - 1
    ma_slope: float = 0.0  # MA 斜率
    samples_used: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MacroSnapshot:
    """宏观指标快照 (CPI/PMI/M2/利率)."""

    cpi: float | None = None
    pmi: float | None = None
    m2: float | None = None
    rate: float | None = None
    timestamp: str = ""
    source: str = ""

    # 各指标的分类标签 (low/moderate/high 等)
    cpi_category: str = ""
    pmi_category: str = ""
    m2_category: str = ""
    rate_category: str = ""

    # 综合宏观评分 (0-100, 越高越利好权益)
    composite_score: float = 50.0
    # 状态说明 (feature_flag_disabled / no_data / ok 等)
    status: str = "ok"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ============================================================
# Regime 分类核心算法 (迁移自 research RegimeConditioner)
# ============================================================


def classify_regime(
    benchmark_returns: Sequence[float],
    ma_window: int = DEFAULT_MA_WINDOW,
    choppy_band: float = DEFAULT_CHOPPY_BAND,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> RegimeResult:
    """根据基准收益率序列分类 regime (便捷函数).

    Args:
        benchmark_returns: 基准日收益率序列 (如沪深300/510300)
        ma_window: MA 窗口 (默认 60)
        choppy_band: 震荡区间阈值 (默认 0.03)
        min_samples: 最小样本数 (默认 20)

    Returns:
        RegimeResult 含 label / position_factor / risk_budget / ma_value 等
    """
    rets = list(benchmark_returns)
    n = len(rets)

    if n < min_samples:
        return RegimeResult(
            label=REGIME_INSUFFICIENT,
            samples_used=n,
            reason=f"samples {n} < {min_samples}",
        )

    # 计算累积价格序列
    prices = [1.0]
    for r in rets:
        prices.append(prices[-1] * (1.0 + r))

    # 最近一个时点的 regime
    i = n - 1
    if i < ma_window:
        return RegimeResult(
            label=REGIME_WARMUP,
            samples_used=n,
            reason=f"i={i} < ma_window={ma_window}",
        )

    # 计算 MA 和前一个 MA
    window = prices[i - ma_window + 1 : i + 2]
    ma = sum(window) / len(window)
    cur = prices[i + 1]
    prev_ma = sum(prices[i - ma_window : i + 1]) / (ma_window + 1)

    ratio = cur / ma - 1.0 if ma > 0 else 0.0
    ma_slope = (ma - prev_ma) / prev_ma if prev_ma > 0 else 0.0

    if abs(ratio) < choppy_band:
        label = REGIME_CHOPPY
    elif ratio > 0 and ma_slope > 0:
        label = REGIME_BULL
    elif ratio < 0 and ma_slope < 0:
        label = REGIME_BEAR
    else:
        label = REGIME_REBOUND

    return RegimeResult(
        label=label,
        position_factor=DEFAULT_POSITION_FACTORS.get(label, 1.0),
        risk_budget=DEFAULT_RISK_BUDGET.get(label, 1.0),
        ma_value=ma,
        price_ratio=ratio,
        ma_slope=ma_slope,
        samples_used=n,
        reason=f"ratio={ratio:.4f}, slope={ma_slope:.4f}",
    )


def classify_regimes_batch(
    benchmark_returns: Sequence[float],
    ma_window: int = DEFAULT_MA_WINDOW,
    choppy_band: float = DEFAULT_CHOPPY_BAND,
) -> list[str]:
    """批量分类 regime (对齐 research RegimeConditioner._classify_regimes).

    Args:
        benchmark_returns: 基准日收益率序列
        ma_window: MA 窗口
        choppy_band: 震荡区间阈值

    Returns:
        regime 标签列表 (长度与输入相同)
    """
    rets = list(benchmark_returns)
    n = len(rets)
    regimes: list[str] = [REGIME_UNKNOWN] * n

    prices = [1.0]
    for r in rets:
        prices.append(prices[-1] * (1.0 + r))

    for i in range(n):
        if i < ma_window:
            regimes[i] = REGIME_WARMUP
            continue
        window = prices[i - ma_window + 1 : i + 2]
        ma = sum(window) / len(window)
        cur = prices[i + 1]
        prev_ma = sum(prices[i - ma_window : i + 1]) / (ma_window + 1)
        ratio = cur / ma - 1.0 if ma > 0 else 0.0
        ma_slope = (ma - prev_ma) / prev_ma if prev_ma > 0 else 0.0
        if abs(ratio) < choppy_band:
            regimes[i] = REGIME_CHOPPY
        elif ratio > 0 and ma_slope > 0:
            regimes[i] = REGIME_BULL
        elif ratio < 0 and ma_slope < 0:
            regimes[i] = REGIME_BEAR
        else:
            regimes[i] = REGIME_REBOUND

    return regimes


# ============================================================
# 宏观指标分类
# ============================================================


def classify_cpi(cpi: float, thresholds: dict[str, float] | None = None) -> str:
    """分类 CPI 指标."""
    th = thresholds or {"low": 1.0, "moderate": 3.0, "high": 5.0}
    if cpi < th["low"]:
        return "low"  # 低通胀
    if cpi < th["moderate"]:
        return "moderate"  # 温和通胀
    if cpi < th["high"]:
        return "high"  # 高通胀
    return "hyper"  # 恶性通胀


def classify_pmi(pmi: float, thresholds: dict[str, float] | None = None) -> str:
    """分类 PMI 指标."""
    th = thresholds or {"contraction": 50.0, "neutral": 51.0, "expansion": 52.0}
    if pmi < th["contraction"]:
        return "contraction"  # 衰退
    if pmi < th["neutral"]:
        return "neutral"  # 中性
    if pmi < th["expansion"]:
        return "expansion"  # 扩张
    return "strong_expansion"  # 强扩张


def classify_m2(m2: float, thresholds: dict[str, float] | None = None) -> str:
    """分类 M2 指标."""
    th = thresholds or {"tight": 8.0, "moderate": 10.0, "loose": 12.0}
    if m2 < th["tight"]:
        return "tight"  # 紧缩
    if m2 < th["moderate"]:
        return "moderate"  # 温和
    if m2 < th["loose"]:
        return "loose"  # 宽松
    return "very_loose"  # 非常宽松


def classify_rate(rate: float, thresholds: dict[str, float] | None = None) -> str:
    """分类利率指标."""
    th = thresholds or {"low": 2.5, "moderate": 3.0, "high": 3.5}
    if rate < th["low"]:
        return "low"
    if rate < th["moderate"]:
        return "moderate"
    if rate < th["high"]:
        return "high"
    return "very_high"


def compute_composite_score(
    cpi: float | None = None,
    pmi: float | None = None,
    m2: float | None = None,
    rate: float | None = None,
    weights: dict[str, float] | None = None,
) -> float:
    """计算综合宏观评分 (0-100, 越高越利好权益).

    评分逻辑:
        - CPI: 温和通胀 (1-3%) 最佳, 过高或过低扣分
        - PMI: 扩张 (>50) 加分
        - M2: 宽松 (>10%) 加分
        - 利率: 低利率 (<3%) 加分
    """
    w = weights or {"cpi": 0.25, "pmi": 0.25, "m2": 0.25, "rate": 0.25}
    score = 50.0  # 基准分

    if cpi is not None:
        # 温和通胀最佳
        if 1.0 <= cpi <= 3.0:
            score += w["cpi"] * 20
        elif cpi < 1.0:
            score += w["cpi"] * 5  # 通缩风险
        elif cpi <= 5.0:
            score += w["cpi"] * 0  # 高通胀中性
        else:
            score -= w["cpi"] * 20  # 恶性通胀

    if pmi is not None:
        if pmi >= 52.0:
            score += w["pmi"] * 20
        elif pmi >= 50.0:
            score += w["pmi"] * 10
        else:
            score -= w["pmi"] * 15  # 衰退

    if m2 is not None:
        if m2 >= 10.0:
            score += w["m2"] * 20  # 宽松
        elif m2 >= 8.0:
            score += w["m2"] * 5
        else:
            score -= w["m2"] * 10  # 紧缩

    if rate is not None:
        if rate < 2.5:
            score += w["rate"] * 20  # 低利率
        elif rate < 3.0:
            score += w["rate"] * 10
        elif rate < 3.5:
            score += w["rate"] * 0
        else:
            score -= w["rate"] * 15  # 高利率

    return max(0.0, min(100.0, score))


# ============================================================
# 主类: MacroIndicatorManager
# ============================================================


class MacroIndicatorManager:
    """宏观指标管理器 — 集成指标接入 + Regime 分类.

    功能:
        1. 通过 DataLayer 接入 CPI/PMI/M2/利率 (复用 P0-P6 降级链)
        2. 基于基准收益率分类 regime (bull/bear/choppy/rebound)
        3. 提供 update() 流式接口 (增量更新)
        4. 输出仓位调整因子和风险预算 (HC-3 risk_managed)

    Feature Flag 透传 (HC-1):
        USE_MACRO_INDICATOR=False 时, get_regime() 返回 unknown + position_factor=1.0,
        get_macro_snapshot() 返回空快照, 行为完全等价 (不破坏 V9 基线)
    """

    def __init__(
        self,
        config_name: str = DEFAULT_CONFIG_NAME,
        feature_flag_name: str = FLAG_NAME,
        config: dict[str, Any] | None = None,
    ) -> None:
        """初始化宏观指标管理器.

        Args:
            config_name: ConfigManager 配置名
            feature_flag_name: Feature Flag 名称
            config: 显式配置 (优先于 ConfigManager)
        """
        self._feature_flag_name = feature_flag_name
        self._config = config or self._load_config(config_name)
        self._settings = self._config.get("settings", {}) or {}
        self._indicators_cfg = self._config.get("indicators", {}) or {}
        self._regime_cfg = self._config.get("regime", {}) or {}

        # Regime 参数
        self._ma_window = int(self._settings.get("ma_window", DEFAULT_MA_WINDOW))
        self._choppy_band = float(
            self._settings.get("choppy_band", DEFAULT_CHOPPY_BAND)
        )
        self._rebound_threshold = float(
            self._settings.get("rebound_threshold", DEFAULT_REBOUND_THRESHOLD)
        )
        self._min_samples = int(self._settings.get("min_samples", DEFAULT_MIN_SAMPLES))

        # 仓位因子和风险预算 (从配置覆盖默认值)
        self._position_factors = dict(DEFAULT_POSITION_FACTORS)
        self._position_factors.update(
            self._regime_cfg.get("position_factors", {}) or {}
        )
        self._risk_budget = dict(DEFAULT_RISK_BUDGET)
        self._risk_budget.update(self._regime_cfg.get("risk_budget", {}) or {})

        # 内部状态
        self._benchmark_returns: list[float] = []
        self._last_regime: RegimeResult = RegimeResult()
        self._last_snapshot: MacroSnapshot | None = None

    def _load_config(self, config_name: str) -> dict[str, Any]:
        """加载配置 (走 ConfigManager 4 级优先级, HC-5)."""
        try:
            cfg = get_config(config_name, default={}) or {}
            if not cfg:
                logger.warning("配置未找到: %s, 使用默认配置", config_name)
            return cfg
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            logger.warning("配置加载失败: %s (%s), 使用默认配置", config_name, e)
            return {}

    def _is_enabled(self) -> bool:
        """检查 Feature Flag 是否启用 (HC-1)."""
        try:
            from utils.infra.feature_flags import is_enabled

            return bool(is_enabled(self._feature_flag_name))
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ):  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            return False

    # ===== Regime 分类接口 =====

    def update(self, new_returns: Sequence[float]) -> RegimeResult:
        """流式更新基准收益率并重新分类 regime.

        Args:
            new_returns: 新增的基准日收益率序列

        Returns:
            RegimeResult 含 label / position_factor / risk_budget
        """
        if not self._is_enabled():
            # HC-1 透传: 返回 unknown, 不调整仓位
            self._last_regime = RegimeResult(
                label=REGIME_UNKNOWN,
                reason="feature_flag_disabled",
            )
            return self._last_regime

        self._benchmark_returns.extend(new_returns)
        self._last_regime = classify_regime(
            benchmark_returns=self._benchmark_returns,
            ma_window=self._ma_window,
            choppy_band=self._choppy_band,
            min_samples=self._min_samples,
        )
        # 应用配置中的仓位因子和风险预算
        self._last_regime.position_factor = self._position_factors.get(
            self._last_regime.label, 1.0
        )
        self._last_regime.risk_budget = self._risk_budget.get(
            self._last_regime.label, 1.0
        )
        return self._last_regime

    def get_regime(self) -> RegimeResult:
        """获取当前 regime (最近一次 update 的结果)."""
        return self._last_regime

    def classify_batch(self, benchmark_returns: Sequence[float]) -> list[str]:
        """批量分类 regime (兼容 research RegimeConditioner._classify_regimes).

        Args:
            benchmark_returns: 基准日收益率序列

        Returns:
            regime 标签列表
        """
        if not self._is_enabled():
            return [REGIME_UNKNOWN] * len(benchmark_returns)
        return classify_regimes_batch(
            benchmark_returns=benchmark_returns,
            ma_window=self._ma_window,
            choppy_band=self._choppy_band,
        )

    # ===== 宏观指标接入接口 =====

    def get_macro_snapshot(self) -> MacroSnapshot:
        """获取宏观指标快照 (CPI/PMI/M2/利率).

        数据源走 DataLayer.get_macro_indicators() (P0-P6 降级链).
        Feature Flag 关闭时返回空快照 (HC-1).
        """
        if not self._is_enabled():
            return MacroSnapshot(status="feature_flag_disabled")

        # 尝试通过 DataLayer 获取数据
        data = self._fetch_macro_data()
        if not data:
            logger.warning("宏观数据获取失败, 返回空快照")
            return MacroSnapshot(status="no_data")

        # 解析指标
        cpi = self._extract_indicator(data, "cpi")
        pmi = self._extract_indicator(data, "pmi")
        m2 = self._extract_indicator(data, "m2")
        rate = self._extract_indicator(data, "rate")

        # 分类
        cpi_cat = classify_cpi(cpi) if cpi is not None else ""
        pmi_cat = classify_pmi(pmi) if pmi is not None else ""
        m2_cat = classify_m2(m2) if m2 is not None else ""
        rate_cat = classify_rate(rate) if rate is not None else ""

        # 综合评分
        weights = {
            k: float(v.get("weight", 0.25)) for k, v in self._indicators_cfg.items()
        }
        score = compute_composite_score(cpi, pmi, m2, rate, weights)

        snapshot = MacroSnapshot(
            cpi=cpi,
            pmi=pmi,
            m2=m2,
            rate=rate,
            timestamp="",
            source=data.get("_source", "data_layer"),
            cpi_category=cpi_cat,
            pmi_category=pmi_cat,
            m2_category=m2_cat,
            rate_category=rate_cat,
            composite_score=score,
            status="ok",
        )
        self._last_snapshot = snapshot
        return snapshot

    def _fetch_macro_data(self) -> dict[str, Any]:
        """通过 DataLayer 获取宏观数据 (复用 P0-P6 降级链)."""
        try:
            from utils.data.data_layer import get_macro_indicators

            return get_macro_indicators() or {}
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ) as e:  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            logger.warning("DataLayer 宏观数据获取失败: %s", e)
            return {}

    def _extract_indicator(self, data: dict[str, Any], name: str) -> float | None:
        """从数据字典中提取指标值."""
        cfg = self._indicators_cfg.get(name, {}) or {}
        source_key = cfg.get("source_key", name)
        raw = data.get(source_key)
        if raw is None:
            return None
        try:
            # 兼容字典形式 {"value": 2.3, ...}
            if isinstance(raw, dict):
                return float(cast(float, raw.get("value", raw.get("latest", 0.0))))
            return float(raw)
        except (TypeError, ValueError):
            return None

    # ===== 便捷接口 =====

    def get_position_factor(self) -> float:
        """获取当前 regime 的仓位调整因子 (HC-3 risk_managed)."""
        return self._last_regime.position_factor

    def get_risk_budget(self) -> float:
        """获取当前 regime 的风险预算."""
        return self._last_regime.risk_budget

    def reset(self) -> None:
        """重置内部状态 (测试用)."""
        self._benchmark_returns = []
        self._last_regime = RegimeResult()
        self._last_snapshot = None


# ============================================================
# 模块级便捷函数
# ============================================================


def classify_regime_simple(
    benchmark_returns: Sequence[float],
) -> RegimeResult:
    """便捷函数: 快速分类 regime (使用默认参数)."""
    return classify_regime(benchmark_returns=benchmark_returns)


def get_regime_for_returns(
    benchmark_returns: Sequence[float],
    config_name: str = DEFAULT_CONFIG_NAME,
) -> RegimeResult:
    """便捷函数: 创建管理器并获取 regime (一次性调用)."""
    mgr = MacroIndicatorManager(config_name=config_name)
    return mgr.update(new_returns=benchmark_returns)


def is_macro_indicator_enabled() -> bool:
    """便捷函数: 检查 Feature Flag 是否启用."""
    try:
        from utils.infra.feature_flags import is_enabled

        return bool(is_enabled(FLAG_NAME))
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ):  # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
        return False


__all__ = [
    "ALL_REGIMES",
    "DEFAULT_CHOPPY_BAND",
    "DEFAULT_CONFIG_NAME",
    "DEFAULT_MA_WINDOW",
    "DEFAULT_MIN_SAMPLES",
    "DEFAULT_POSITION_FACTORS",
    "DEFAULT_RISK_BUDGET",
    "FLAG_NAME",
    "REGIME_BEAR",
    # 常量
    "REGIME_BULL",
    "REGIME_CHOPPY",
    "REGIME_INSUFFICIENT",
    "REGIME_REBOUND",
    "REGIME_UNKNOWN",
    "REGIME_WARMUP",
    "InsufficientDataError",
    "InvalidRegimeError",
    # 异常
    "MacroIndicatorError",
    # 主类
    "MacroIndicatorManager",
    "MacroSnapshot",
    # 数据类
    "RegimeResult",
    "classify_cpi",
    "classify_m2",
    "classify_pmi",
    "classify_rate",
    # 核心函数
    "classify_regime",
    # 便捷函数
    "classify_regime_simple",
    "classify_regimes_batch",
    "compute_composite_score",
    "get_regime_for_returns",
    "is_macro_indicator_enabled",
]
