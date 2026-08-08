"""多因子 IC 加权融合信号生成器 — 终极量化交易系统 8.4 (T2.3).

模块整合 8.4 — ARCHITECTURE §2.3 / ADR-005
任务: T2.3

设计原则:
    1. IC 加权 (lookback=10 天, HC-7): 滚动 IC_IR 作为动态权重, 符号自适应
    2. 反向信号因子处理 (HC-6): |IC_IR|>=0.3 的负 IC_IR 因子反向使用
    3. 与 PipelineOrchestrator 集成: 不破坏 ic_weighted_enabled=True 默认
    4. Feature Flag 透传 (HC-1): USE_MULTI_FACTOR_SIGNAL=False 时降级到等权模式
    5. ConfigManager 4 级优先级解析 (HC-5)
    6. Phase 3 集成 (HC-8): Vibe 因子正交化过滤, flag=USE_VIBE_FACTOR_INJECTION

方法学 (复用 PipelineOrchestrator._combine_factors_ic_weighted):
    - 每个时间点 t: weight_i = IC_IR_i / Σ|IC_IR_j| (保留符号)
    - IC_IR 为负 → 权重为负 (反向使用因子)
    - IC_IR 接近 0 → 权重接近 0 (自动降低弱信号因子权重)
    - combined[t] = Σ weight_i * rank(factor_i[t])

API:
    from utils.alpha.multi_factor_signal import MultiFactorSignal, combine_factors

    # 推荐: 使用快捷函数
    combined, weights = combine_factors(factor_history, forward_returns, ["F_A", "F_B"])

    # 或使用类 (需要自定义配置时)
    mfs = MultiFactorSignal(lookback=10)
    combined, weights = mfs.combine_factors_ic_weighted(
        factor_history=factor_history,
        forward_returns=forward_returns,
        factor_names=["F_A", "F_B"],
    )

硬约束:
    - HC-1: flag=False 时降级到等权模式, 行为完全等价 (不破坏 V9 基线)
    - HC-5: 配置走 ConfigManager 4 级优先级
    - HC-6: 反向信号因子处理 (|IC_IR|>=0.3 的负 IC_IR 反向使用)
    - HC-7: IC 加权 lookback=10 天 (对齐 v6.9 优化)
    - HC-8: Vibe 因子正交化 (flag=USE_VIBE_FACTOR_INJECTION, 默认关闭)
            flag 关闭或异常时降级为不过滤, 保持原行为
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Sequence

from utils.config_manager import get_config
from utils.infra.feature_flags import is_enabled

logger = logging.getLogger("multi_factor_signal")


# ============================================================
# 常量 (对齐 PipelineOrchestrator)
# ============================================================

# HC-7: IC 加权滚动 IC_IR 回看窗口 (天) - v6.9 优化自 20
DEFAULT_LOOKBACK = 10

# HC-6: 反向信号因子检测阈值 - |IC_IR| >= 此值且为负时反向使用
INVERTED_SIGNAL_IC_IR_THRESHOLD = 0.3

# 滚动 IC 计算的最小样本数
MIN_IC_SAMPLES = 5

# IC 标准差下限 (避免除零)
IC_STD_FLOOR = 1e-12

# 权重绝对值之和的下限 (两个因子都接近 0 时用等权兜底)
WEIGHT_ABS_SUM_FLOOR = 1e-6

# Feature Flag 名称
FLAG_NAME = "USE_MULTI_FACTOR_SIGNAL"

# ============================================================
# Phase 3: Vibe 因子正交化常量 (HC-8)
# ============================================================
# 正交化 flag (与 factor_orthogonalizer.py 共用)
ORTHO_FLAG_NAME = "USE_VIBE_FACTOR_INJECTION"

# 默认正交性阈值 (来自 feature_flags.yaml: 正交性 corr < 0.7)
ORTHO_DEFAULT_THRESHOLD = 0.7

# 正交化所需最小样本数 (少于此值的因子保留原状, 不参与正交化)
ORTHO_MIN_SAMPLES = 20


# ============================================================
# 异常定义
# ============================================================


class MultiFactorSignalError(Exception):
    """MultiFactorSignal 基础异常."""


class InsufficientSamplesError(MultiFactorSignalError):
    """样本不足, 无法计算 IC_IR."""


# ============================================================
# 数据类
# ============================================================


@dataclass
class FactorICMetrics:
    """单因子 IC 指标快照.

    Attributes:
        factor_name: 因子名称
        ic_mean: IC 均值
        ic_std: IC 标准差
        ic_ir: IC_IR = ic_mean / ic_std
        is_inverted: 是否为反向信号因子 (HC-6: IC_IR<=-threshold)
        effective_ic_ir: 反向后的有效 IC_IR (反向时取绝对值, 否则原值)
        n_samples: 样本数
    """

    factor_name: str
    ic_mean: float
    ic_std: float
    ic_ir: float
    is_inverted: bool
    effective_ic_ir: float
    n_samples: int

    def to_dict(self) -> dict[str, Any]:
        """转为字典."""
        return {
            "factor_name": self.factor_name,
            "ic_mean": round(self.ic_mean, 4),
            "ic_std": round(self.ic_std, 4),
            "ic_ir": round(self.ic_ir, 4),
            "is_inverted": self.is_inverted,
            "effective_ic_ir": round(self.effective_ic_ir, 4),
            "n_samples": self.n_samples,
        }


@dataclass
class CombinationResult:
    """IC 加权组合结果.

    Attributes:
        combined_history: 融合后的因子值序列 [{symbol: value}, ...]
        weights_history: 每日权重历史 [{factor_name: weight, ...}, ...]
        factor_metrics: 各因子全局 IC 指标 {factor_name: FactorICMetrics}
        lookback: 实际使用的 lookback
        n_days: 有效天数
        mode: 实际运行模式 ("ic_weighted" / "equal_weight")
    """

    combined_history: list[dict[str, float]]
    weights_history: list[dict[str, float]]
    factor_metrics: dict[str, FactorICMetrics]
    lookback: int
    n_days: int
    mode: str

    def to_dict(self) -> dict[str, Any]:
        """转为字典 (用于审计日志)."""
        return {
            "lookback": self.lookback,
            "n_days": self.n_days,
            "mode": self.mode,
            "factor_metrics": {k: v.to_dict() for k, v in self.factor_metrics.items()},
            "n_combined": len(self.combined_history),
        }


# ============================================================
# MultiFactorSignal 主类
# ============================================================


class MultiFactorSignal:
    """多因子 IC 加权融合信号生成器.

    Feature Flag:
        USE_MULTI_FACTOR_SIGNAL=False (默认): 等权模式 (HC-1 透传)
        USE_MULTI_FACTOR_SIGNAL=True: IC 加权模式

    Usage:
        >>> mfs = MultiFactorSignal(lookback=10)
        >>> combined, weights = mfs.combine_factors_ic_weighted(
        ...     factor_history={"F_A": [{...}, ...], "F_B": [{...}, ...]},
        ...     forward_returns=[{...}, ...],
        ...     factor_names=["F_A", "F_B"],
        ... )
        >>> metrics = mfs.compute_factor_ic_metrics(
        ...     factor_history={"F_A": [...]},
        ...     forward_returns=[...],
        ...     factor_name="F_A",
        ... )
        >>> if metrics.is_inverted:
        ...     logger.info(f"反向信号因子: {metrics.factor_name}")
    """

    def __init__(
        self,
        lookback: int = DEFAULT_LOOKBACK,
        inverted_threshold: float = INVERTED_SIGNAL_IC_IR_THRESHOLD,
        feature_flag_name: str = FLAG_NAME,
        config_name: str = "multi_factor_signal",
    ) -> None:
        """初始化.

        Args:
            lookback: 滚动 IC_IR 回看窗口 (天, 默认 10, HC-7)
            inverted_threshold: 反向信号检测阈值 (|IC_IR|>=此值且为负时反向, HC-6)
            feature_flag_name: Feature Flag 名称
            config_name: ConfigManager 配置名
        """
        self.lookback = int(lookback)
        self.inverted_threshold = float(inverted_threshold)
        self.feature_flag_name = feature_flag_name
        self.config_name = config_name

        # 从 ConfigManager 加载覆盖配置 (HC-5)
        self._load_config()

    def _load_config(self) -> None:
        """从 ConfigManager 加载 multi_factor_signal.yaml (4 级优先级, HC-5).

        配置项 (可选):
            lookback: int
            inverted_threshold: float
            feature_flag_name: str
        """
        try:
            cfg = get_config(self.config_name, default={}) or {}
            if cfg:
                # 仅在显式配置时覆盖构造参数
                if "lookback" in cfg:
                    self.lookback = int(cfg["lookback"])
                if "inverted_threshold" in cfg:
                    self.inverted_threshold = float(cfg["inverted_threshold"])
                if "feature_flag_name" in cfg:
                    self.feature_flag_name = str(cfg["feature_flag_name"])
                logger.debug(
                    "MultiFactorSignal 配置加载: lookback=%d, threshold=%.2f, flag=%s",
                    self.lookback,
                    self.inverted_threshold,
                    self.feature_flag_name,
                )
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001  # P2 模块 fail-safe, 待后续精确化
            logger.warning("MultiFactorSignal 配置加载失败, 使用默认值: %s", e)

    # ============================================================
    # 公开 API
    # ============================================================

    def combine_factors_ic_weighted(
        self,
        factor_history: dict[str, list[dict[str, float]]],
        forward_returns: list[dict[str, float]],
        factor_names: Sequence[str],
        lookback: int | None = None,
    ) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
        """IC 加权融合多个因子 (HC-7 lookback=10).

        Feature Flag 透传 (HC-1):
            - USE_MULTI_FACTOR_SIGNAL=False: 等权模式 (所有因子 weight=1/N)
            - USE_MULTI_FACTOR_SIGNAL=True: IC 加权模式

        Phase 3 集成 (HC-8):
            - USE_VIBE_FACTOR_INJECTION=True 时, 先正交化过滤高相关冗余因子
            - 失败安全: flag 关闭/异常时使用原 factor_names

        Args:
            factor_history: {factor_name: [day_0_values, day_1_values, ...]}
                            每个 day_i_values = {symbol: factor_value}
            forward_returns: 日频 forward return 序列 [{symbol: fwd_return}, ...]
            factor_names: 参与融合的因子名称列表
            lookback: 滚动 IC_IR 回看窗口 (None 用实例默认值)

        Returns:
            (combined_history, weights_history)
            - combined_history: [{symbol: combined_value}, ...]
            - weights_history: [{factor_name: weight, ...}, ...]
        """
        lb = lookback if lookback is not None else self.lookback
        flag_enabled = is_enabled(self.feature_flag_name)

        # 校验输入
        n_factors = len(factor_names)
        if n_factors == 0:
            return [], []

        # 对齐时间序列长度
        n = self._aligned_length(factor_history, forward_returns, factor_names)
        if n == 0:
            return [], []

        # ============================================================
        # Phase 3: Vibe 因子正交化过滤 (HC-8, flag-gated)
        # 在 IC 加权前过滤高相关冗余因子, 提升组合信号的信息有效性
        # 失败安全: flag 关闭/异常时使用原 factor_names (HC-1 透传)
        # ============================================================
        filtered_names, ortho_report = self.filter_orthogonal_factors(factor_history, factor_names)
        # 仅在过滤成功且结果非空时使用过滤结果
        if ortho_report is not None and filtered_names:
            factor_names = filtered_names
            n_factors = len(factor_names)

        if flag_enabled:
            return self._combine_ic_weighted(
                factor_history,
                forward_returns,
                factor_names,
                lb,
                n,
            )
        # HC-1 透传: 等权模式
        return self._combine_equal_weight(factor_history, factor_names, n)

    def compute_factor_ic_metrics(
        self,
        factor_history: dict[str, list[dict[str, float]]],
        forward_returns: list[dict[str, float]],
        factor_name: str,
    ) -> FactorICMetrics:
        """计算单因子全局 IC 指标 (用于反向信号检测, HC-6).

        Args:
            factor_history: {factor_name: [day_0_values, ...]}
            forward_returns: 日频 forward return 序列
            factor_name: 因子名称

        Returns:
            FactorICMetrics 数据类

        Raises:
            InsufficientSamplesError: 样本不足 (n < MIN_IC_SAMPLES)
        """
        if factor_name not in factor_history:
            raise InsufficientSamplesError(f"因子不存在: {factor_name} (available: {list(factor_history.keys())})")

        hist = factor_history[factor_name]
        fwd = forward_returns or []
        n = min(len(hist), len(fwd))
        if n < MIN_IC_SAMPLES:
            raise InsufficientSamplesError(f"样本不足: n={n} < MIN_IC_SAMPLES={MIN_IC_SAMPLES}")

        ic_series = self.compute_rolling_ic_series(hist[:n], fwd[:n])
        valid_ic = [v for v in ic_series if math.isfinite(v)]
        if len(valid_ic) < MIN_IC_SAMPLES:
            raise InsufficientSamplesError(f"有效 IC 样本不足: n={len(valid_ic)} < {MIN_IC_SAMPLES}")

        ic_mean = sum(valid_ic) / len(valid_ic)
        var = sum((v - ic_mean) ** 2 for v in valid_ic) / max(len(valid_ic) - 1, 1)
        ic_std = math.sqrt(var) if var > 0 else IC_STD_FLOOR
        ic_ir = ic_mean / ic_std if ic_std > IC_STD_FLOOR else 0.0

        # HC-6: 反向信号检测
        is_inverted = ic_ir <= -self.inverted_threshold
        effective_ic_ir = abs(ic_ir) if is_inverted else ic_ir

        return FactorICMetrics(
            factor_name=factor_name,
            ic_mean=ic_mean,
            ic_std=ic_std,
            ic_ir=ic_ir,
            is_inverted=is_inverted,
            effective_ic_ir=effective_ic_ir,
            n_samples=len(valid_ic),
        )

    def detect_inverted_factors(
        self,
        factor_history: dict[str, list[dict[str, float]]],
        forward_returns: list[dict[str, float]],
        factor_names: Sequence[str],
    ) -> dict[str, FactorICMetrics]:
        """批量检测反向信号因子 (HC-6).

        Args:
            factor_history: 因子历史数据
            forward_returns: forward return 序列
            factor_names: 待检测的因子名称列表

        Returns:
            {factor_name: FactorICMetrics} (仅包含成功计算的因子)
        """
        results: dict[str, FactorICMetrics] = {}
        for name in factor_names:
            try:
                metrics = self.compute_factor_ic_metrics(
                    factor_history,
                    forward_returns,
                    name,
                )
                results[name] = metrics
                if metrics.is_inverted:
                    logger.info(
                        "[MultiFactorSignal] 反向信号因子: %s | IC_IR=%.4f -> +%.4f",
                        name,
                        metrics.ic_ir,
                        metrics.effective_ic_ir,
                    )
            except InsufficientSamplesError as e:
                logger.warning("[MultiFactorSignal] %s IC 计算跳过: %s", name, e)
        return results

    def generate_signal(
        self,
        factor_values: dict[str, dict[str, float]],
        factor_weights: dict[str, float] | None = None,
    ) -> dict[str, float]:
        """生成多因子融合信号 (单日快照, 不含时序).

        Args:
            factor_values: {factor_name: {symbol: value}}
            factor_weights: {factor_name: weight} (None 时等权)

        Returns:
            {symbol: combined_signal}
        """
        if not factor_values:
            return {}

        factor_names = list(factor_values.keys())
        n_factors = len(factor_names)
        if n_factors == 0:
            return {}

        # 默认等权
        if factor_weights is None:
            factor_weights = {name: 1.0 / n_factors for name in factor_names}

        # rank 标准化
        ranked = {name: self.cross_sectional_rank(values) for name, values in factor_values.items()}

        # 取所有标的的并集
        all_symbols: set = set()
        for vals in factor_values.values():
            all_symbols.update(vals.keys())

        # 加权融合
        combined: dict[str, float] = {}
        for sym in all_symbols:
            total = 0.0
            for name in factor_names:
                val = ranked[name].get(sym, 0.5)
                total += factor_weights.get(name, 0.0) * val
            combined[sym] = total

        return combined

    # ============================================================
    # Phase 3: Vibe 因子正交化过滤 (HC-8)
    # ============================================================

    def filter_orthogonal_factors(
        self,
        factor_history: dict[str, list[dict[str, float]]],
        factor_names: Sequence[str],
        threshold: float = ORTHO_DEFAULT_THRESHOLD,
    ) -> tuple[list[str], Any | None]:
        """过滤高相关冗余因子 (Phase 3 集成, HC-8).

        HC-1: flag 检查由 orthogonalize_factors 内部完成 (单一职责, 避免双重检查).
        HC-8: 仅对样本充足的因子进行正交化; 样本不足的因子保留原状.
        失败安全: flag 关闭/异常时返回原 factor_names (不阻断主流程).

        Args:
            factor_history: {factor_name: [day_0_values, ...]}
            factor_names: 待过滤的因子名称列表
            threshold: 相关系数阈值 (默认 0.7, 来自 feature_flags.yaml)

        Returns:
            (filtered_factor_names, report)
            - filtered_factor_names: 正交化后保留的因子名列表
              (flag 关闭/异常时返回原 factor_names 的拷贝)
            - report: OrthogonalizationReport 或 None (flag 关闭/异常时)
        """
        # 懒导入 orthogonalizer (避免模块级依赖, 保持 pandas-free 特性)
        try:
            from utils.alpha.factor_orthogonalizer import orthogonalize_factors
        except ImportError as e:
            logger.warning(
                "[MultiFactorSignal] factor_orthogonalizer 不可用, 跳过正交化: %s",
                e,
            )
            return list(factor_names), None

        # 构建因子时间序列 (仅样本充足的因子参与正交化)
        factor_series, evaluable_names, insufficient_names = self._build_factor_series(factor_history, factor_names)

        # 无可评估因子, 直接返回原列表
        if not evaluable_names:
            logger.debug(
                "[MultiFactorSignal] 无样本充足的因子可正交化 (min=%d), 跳过",
                ORTHO_MIN_SAMPLES,
            )
            return list(factor_names), None

        # 调用正交化 (orthogonalizer 内部检查 USE_VIBE_FACTOR_INJECTION flag, HC-1)
        try:
            selected, report = orthogonalize_factors(factor_series, threshold=threshold)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e: # noqa: BLE001  # P2 fail-safe, 不阻断主流程
            logger.warning(
                "[MultiFactorSignal] 正交化异常, 降级为原因子列表: %s",
                e,
            )
            return list(factor_names), None

        # ============================================================
        # 处理 orthogonalize_factors 的返回状态
        # ============================================================
        # flag 关闭: status='disabled', selected=[]
        # 异常: status='error', selected=[]
        # 全部丢弃: status='success', selected=[]
        # 以上三种情况均降级为原列表
        if not selected:
            if report.status == "disabled":
                logger.debug(
                    "[MultiFactorSignal] 正交化 flag=%s 关闭, 跳过过滤",
                    ORTHO_FLAG_NAME,
                )
                return list(factor_names), None
            if report.status == "error":
                logger.warning(
                    "[MultiFactorSignal] 正交化失败, 降级为原因子列表: %s",
                    report.error,
                )
                return list(factor_names), None
            # success 但 0 选中 (极端情况, 所有因子被低方差丢弃)
            logger.warning("[MultiFactorSignal] 正交化后 0 因子被选中, 降级为原因子列表")
            return list(factor_names), report

        # 合并: 正交化选中的 + 样本不足保留的
        # 保持原始顺序 (降低权重历史对齐的复杂度)
        selected_set = set(selected)
        insufficient_set = set(insufficient_names)
        final_names = [f for f in factor_names if f in selected_set or f in insufficient_set]

        # 日志记录过滤结果
        n_dropped = len(factor_names) - len(final_names)
        if n_dropped > 0:
            dropped = set(factor_names) - set(final_names)
            logger.info(
                "[MultiFactorSignal] 正交化过滤: %d -> %d 因子 (threshold=%.2f, dropped=%s, insufficient=%d)",
                len(factor_names),
                len(final_names),
                threshold,
                sorted(dropped),
                len(insufficient_names),
            )

        return final_names, report

    @staticmethod
    def _build_factor_series(
        factor_history: dict[str, list[dict[str, float]]],
        factor_names: Sequence[str],
    ) -> tuple[dict[str, Any], list[str], list[str]]:
        """将多因子历史转为 {factor_name: pd.Series} 时间序列.

        转换逻辑: 对每个因子, 计算每日 cross-sectional mean 作为该日代表值,
        构建一条时间序列. 用于计算因子间相关性 (正交化过滤输入).

        HC-8: 样本不足 (< ORTHO_MIN_SAMPLES) 的因子不参与正交化, 保留原状.

        Args:
            factor_history: {factor_name: [day_0_values, ...]}
            factor_names: 因子名称列表

        Returns:
            (series_dict, evaluable_names, insufficient_names)
            - series_dict: {factor_name: pd.Series} (仅样本充足的因子)
            - evaluable_names: 样本充足的因子名列表 (参与正交化)
            - insufficient_names: 样本不足的因子名列表 (保留原状)
        """
        try:
            import pandas as pd  # 局部导入, 保持模块级 pandas-free 特性
        except ImportError as e:
            logger.warning(
                "[MultiFactorSignal] pandas 不可用, 跳过正交化: %s",
                e,
            )
            return {}, [], list(factor_names)

        series_dict: dict[str, Any] = {}
        evaluable_names: list[str] = []
        insufficient_names: list[str] = []

        for name in factor_names:
            if name not in factor_history:
                insufficient_names.append(name)
                continue

            daily_values = factor_history[name]
            means: list[float] = []
            for day_vals in daily_values:
                if not day_vals:
                    means.append(float("nan"))
                    continue
                valid = [v for v in day_vals.values() if isinstance(v, (int, float)) and not math.isnan(v)]
                means.append(sum(valid) / len(valid) if valid else float("nan"))

            if len(means) >= ORTHO_MIN_SAMPLES:
                series_dict[name] = pd.Series(means, name=name)
                evaluable_names.append(name)
            else:
                insufficient_names.append(name)

        return series_dict, evaluable_names, insufficient_names

    # ============================================================
    # IC 计算 (独立实现, 不依赖 pandas/numpy, 便于测试)
    # ============================================================

    def compute_rolling_ic_series(
        self,
        factor_history: list[dict[str, float]],
        forward_returns: list[dict[str, float]],
    ) -> list[float]:
        """计算因子滚动 IC 序列 (Spearman 秩相关).

        每日 IC = spearman_corr(factor_values, forward_returns).

        Args:
            factor_history: 因子日频值序列
            forward_returns: forward return 日频序列

        Returns:
            IC 序列 (长度 = min(len(factor_history), len(forward_returns)))
        """
        n = min(len(factor_history), len(forward_returns))
        ic_series: list[float] = []
        for t in range(n):
            ic = self._spearman_ic(factor_history[t], forward_returns[t])
            ic_series.append(ic)
        return ic_series

    def compute_rolling_ic_ir_at_t(
        self,
        ic_series: list[float],
        t: int,
        lookback: int | None = None,
    ) -> float:
        """计算时间点 t 的滚动 IC_IR.

        用过去 lookback 天的 IC 序列计算 IC_IR = mean(IC) / std(IC).
        样本不足时返回 0 (中性权重, 避免误判).

        Args:
            ic_series: IC 序列
            t: 时间点索引
            lookback: 回看窗口 (None 用实例默认值)

        Returns:
            IC_IR 值 (样本不足或标准差为 0 时返回 0.0)
        """
        lb = lookback if lookback is not None else self.lookback
        if t < lb:
            return 0.0
        window = ic_series[t - lb : t]
        valid = [v for v in window if math.isfinite(v)]
        if len(valid) < MIN_IC_SAMPLES:
            return 0.0
        mean = sum(valid) / len(valid)
        var = sum((v - mean) ** 2 for v in valid) / max(len(valid) - 1, 1)
        std = math.sqrt(var) if var > 0 else 0.0
        if std < IC_STD_FLOOR:
            return 0.0
        return mean / std

    # ============================================================
    # 静态工具方法
    # ============================================================

    @staticmethod
    def cross_sectional_rank(values: dict[str, float]) -> dict[str, float]:
        """cross-sectional rank 标准化到 [0, 1].

        rank 适合 Spearman IC 和 IC 加权组合 (避免大量级因子主导权重).

        Args:
            values: {symbol: raw_value}

        Returns:
            {symbol: rank_in_[0,1]}
        """
        valid = {s: v for s, v in values.items() if isinstance(v, (int, float)) and math.isfinite(v)}
        if len(valid) < 2:
            return {s: 0.5 for s in values}

        sorted_syms = sorted(valid.keys(), key=lambda s: valid[s])
        n = len(sorted_syms)
        # rank 归一化到 [0, 1] (最小值=0, 最大值=1)
        ranks = {s: i / (n - 1) for i, s in enumerate(sorted_syms)}
        return {s: ranks.get(s, 0.5) for s in values}

    # ============================================================
    # 内部实现
    # ============================================================

    def _combine_ic_weighted(
        self,
        factor_history: dict[str, list[dict[str, float]]],
        forward_returns: list[dict[str, float]],
        factor_names: Sequence[str],
        lookback: int,
        n_days: int,
    ) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
        """IC 加权融合核心实现 (复用 PipelineOrchestrator 算法)."""
        # 预计算每个因子的 IC 序列
        ic_series_by_factor: dict[str, list[float]] = {}
        for name in factor_names:
            ic_series_by_factor[name] = self.compute_rolling_ic_series(
                factor_history[name][:n_days],
                forward_returns[:n_days],
            )

        combined: list[dict[str, float]] = []
        weights_history: list[dict[str, float]] = []

        for t in range(n_days):
            # 计算每个因子在时间 t 的滚动 IC_IR
            ic_irs = {
                name: self.compute_rolling_ic_ir_at_t(
                    ic_series_by_factor[name],
                    t,
                    lookback,
                )
                for name in factor_names
            }

            # 权重 = IC_IR / Σ|IC_IR| (保留符号)
            weights = self._normalize_ic_weights(ic_irs)

            # 每个因子 rank 标准化
            ranked = {name: self.cross_sectional_rank(factor_history[name][t]) for name in factor_names}

            # 取共同标的
            common_syms = self._common_symbols(ranked)

            # 加权融合
            combined_day = {
                s: sum(weights[name] * ranked[name].get(s, 0.5) for name in factor_names) for s in common_syms
            }
            combined.append(combined_day)

            # 记录权重历史
            w_record = dict(weights)
            w_record.update({f"ic_ir_{name}": ic_irs[name] for name in factor_names})
            weights_history.append(w_record)

        return combined, weights_history

    def _combine_equal_weight(
        self,
        factor_history: dict[str, list[dict[str, float]]],
        factor_names: Sequence[str],
        n_days: int,
    ) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
        """等权模式 (HC-1 透传, flag=False 时使用)."""
        n_factors = len(factor_names)
        equal_weight = 1.0 / n_factors if n_factors > 0 else 0.0

        combined: list[dict[str, float]] = []
        weights_history: list[dict[str, float]] = []

        for t in range(n_days):
            ranked = {name: self.cross_sectional_rank(factor_history[name][t]) for name in factor_names}
            common_syms = self._common_symbols(ranked)

            combined_day = {
                s: sum(equal_weight * ranked[name].get(s, 0.5) for name in factor_names) for s in common_syms
            }
            combined.append(combined_day)

            w_record: dict[str, Any] = {name: equal_weight for name in factor_names}
            w_record["mode"] = "equal_weight"
            weights_history.append(w_record)

        return combined, weights_history

    def _normalize_ic_weights(self, ic_irs: dict[str, float]) -> dict[str, float]:
        """归一化 IC_IR 为权重 (保留符号, 符号自适应).

        weight_i = IC_IR_i / Σ|IC_IR_j|

        当所有 IC_IR 都接近 0 时用等权兜底 (避免除零).
        """
        abs_sum = sum(abs(v) for v in ic_irs.values())
        if abs_sum < WEIGHT_ABS_SUM_FLOOR:
            # 等权兜底
            n = len(ic_irs)
            return {name: 1.0 / n if n > 0 else 0.0 for name in ic_irs}
        return {name: v / abs_sum for name, v in ic_irs.items()}

    @staticmethod
    def _common_symbols(ranked: dict[str, dict[str, float]]) -> set:
        """取所有因子 rank 字典的标的交集."""
        if not ranked:
            return set()
        sets = [set(v.keys()) for v in ranked.values()]
        common = sets[0]
        for s in sets[1:]:
            common = common & s
        return common

    @staticmethod
    def _aligned_length(
        factor_history: dict[str, list[dict[str, float]]],
        forward_returns: list[dict[str, float]],
        factor_names: Sequence[str],
    ) -> int:
        """计算对齐后的时间序列长度."""
        lengths = [len(forward_returns)]
        for name in factor_names:
            if name not in factor_history:
                logger.warning("[MultiFactorSignal] 因子不存在: %s", name)
                return 0
            lengths.append(len(factor_history[name]))
        return min(lengths) if lengths else 0

    @staticmethod
    def _spearman_ic(
        factor_values: dict[str, float],
        forward_returns: dict[str, float],
    ) -> float:
        """计算单日 Spearman 秩相关 IC.

        Args:
            factor_values: {symbol: factor_value}
            forward_returns: {symbol: forward_return}

        Returns:
            Spearman 秩相关系数, 样本不足或异常时返回 float('nan')
            (nan 语义: 无法计算; 下游用 math.isfinite 过滤)
        """
        common = set(factor_values.keys()) & set(forward_returns.keys())
        pairs = []
        for sym in common:
            fv = factor_values.get(sym)
            fr = forward_returns.get(sym)
            if (
                isinstance(fv, (int, float))
                and isinstance(fr, (int, float))
                and math.isfinite(fv)
                and math.isfinite(fr)
            ):
                pairs.append((fv, fr))

        if len(pairs) < MIN_IC_SAMPLES:
            return float("nan")

        # 计算 rank
        sorted_by_fv = sorted(range(len(pairs)), key=lambda i: pairs[i][0])
        sorted_by_fr = sorted(range(len(pairs)), key=lambda i: pairs[i][1])
        rank_fv = [0.0] * len(pairs)
        rank_fr = [0.0] * len(pairs)
        for rank, idx in enumerate(sorted_by_fv):
            rank_fv[idx] = rank
        for rank, idx in enumerate(sorted_by_fr):
            rank_fr[idx] = rank

        # Pearson 相关系数 (在 rank 上)
        n = len(pairs)
        mean_fv = sum(rank_fv) / n
        mean_fr = sum(rank_fr) / n
        num = sum((rank_fv[i] - mean_fv) * (rank_fr[i] - mean_fr) for i in range(n))
        var_fv = sum((r - mean_fv) ** 2 for r in rank_fv)
        var_fr = sum((r - mean_fr) ** 2 for r in rank_fr)
        denom = math.sqrt(var_fv * var_fr) if var_fv > 0 and var_fr > 0 else 0.0

        if denom < IC_STD_FLOOR:
            return float("nan")  # 方差为 0 无法计算相关系数
        return num / denom


# ============================================================
# 模块级便捷函数
# ============================================================


def combine_factors(
    factor_history: dict[str, list[dict[str, float]]],
    forward_returns: list[dict[str, float]],
    factor_names: Sequence[str],
    lookback: int = DEFAULT_LOOKBACK,
) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
    """快捷函数: IC 加权融合多个因子.

    Args:
        factor_history: {factor_name: [day_0_values, ...]}
        forward_returns: 日频 forward return 序列
        factor_names: 参与融合的因子名称列表
        lookback: 滚动 IC_IR 回看窗口 (默认 10 天, HC-7)

    Returns:
        (combined_history, weights_history)
    """
    mfs = MultiFactorSignal(lookback=lookback)
    return mfs.combine_factors_ic_weighted(
        factor_history=factor_history,
        forward_returns=forward_returns,
        factor_names=factor_names,
    )


def detect_inverted_factors(
    factor_history: dict[str, list[dict[str, float]]],
    forward_returns: list[dict[str, float]],
    factor_names: Sequence[str],
    threshold: float = INVERTED_SIGNAL_IC_IR_THRESHOLD,
) -> dict[str, FactorICMetrics]:
    """快捷函数: 批量检测反向信号因子 (HC-6).

    Args:
        factor_history: 因子历史数据
        forward_returns: forward return 序列
        factor_names: 待检测的因子名称列表
        threshold: 反向信号检测阈值 (默认 0.3)

    Returns:
        {factor_name: FactorICMetrics} (仅包含成功计算的因子)
    """
    mfs = MultiFactorSignal(inverted_threshold=threshold)
    return mfs.detect_inverted_factors(
        factor_history=factor_history,
        forward_returns=forward_returns,
        factor_names=factor_names,
    )
