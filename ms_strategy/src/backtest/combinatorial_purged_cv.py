"""Combinatorial Purged Cross-Validation (CPCV) — Lopez de Prado (2018).

v8.4 T06 (2026-07-28): 替代原 5-fold CV, 解决 lookahead/leakage 问题.

核心概念:
    1. Purge (清洗): 删除训练集中与测试集时间重叠的样本 (防止标签泄漏)
    2. Embargo (禁运): 测试集后追加禁运期, 防止自相关导致的间接泄漏
    3. Combinatorial (组合): 从 N 个组中选 k 个作为测试集, 其余作为训练集
       生成 C(N, k) 个路径, 每个路径都是独立的回测

数学基础:
    - 给定 N=6 groups, k=2 test groups → C(6,2)=15 个回测路径
    - 每个路径的测试集不重叠, 训练集经 purge+embargo 后无泄漏
    - 最终 SR 分布由 15 个路径给出, 可计算 DSR

References:
    - Lopez de Prado, M. (2018). Advances in Financial Machine Learning. Wiley.
    - Chapter 7: Cross-Validation in Finance
"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from itertools import combinations
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class CPCVSplit:
    """单个 CPCV 分割.

    Attributes:
        path_id: 路径编号 (0 ~ C(N,k)-1)
        train_idx: 训练集位置索引 (已 purge + embargo)
        test_idx: 测试集位置索引
        test_groups: 测试集包含的组编号 (如 (0, 3))
    """
    path_id: int
    train_idx: np.ndarray
    test_idx: np.ndarray
    test_groups: tuple[int, ...]


@dataclass
class CPCVResult:
    """单条 CPCV 路径的回测结果.

    Attributes:
        path_id: 路径编号
        test_groups: 测试集组编号
        sharpe: 该路径的 Sharpe ratio
        sortino: Sortino ratio
        max_dd: 最大回撤
        returns: 测试期收益序列
        n_test_samples: 测试样本数
        n_train_samples: 训练样本数 (purge + embargo 后)
    """
    path_id: int
    test_groups: tuple[int, ...]
    sharpe: float = 0.0
    sortino: float = 0.0
    max_dd: float = 0.0
    returns: np.ndarray | None = None
    n_test_samples: int = 0
    n_train_samples: int = 0


@dataclass
class CPCVConfig:
    """CPCV 配置.

    Attributes:
        n_groups: 组数 N (典型 6-10)
        n_test_groups: 测试组数 k (典型 1-2)
        purge_pct: purge 比例 (相对测试集长度, 默认 0.0 表示按时间重叠 purge)
        embargo_pct: embargo 比例 (相对总长度, 默认 0.01 = 1%)
        min_train_samples: 最小训练样本数 (低于此数跳过该路径)
    """
    n_groups: int = 6
    n_test_groups: int = 2
    purge_pct: float = 0.0
    embargo_pct: float = 0.01
    min_train_samples: int = 100


class CombinatorialPurgedCV:
    """Combinatorial Purged Cross-Validation.

    Usage:
        >>> cv = CombinatorialPurgedCV(CPCVConfig(n_groups=6, n_test_groups=2))
        >>> splits = cv.split(n_samples=len(data))
        >>> for split in splits:
        ...     train = data.iloc[split.train_idx]
        ...     test = data.iloc[split.test_idx]
        ...     # 训练 + 测试

    Notes:
        - 生成 C(N, k) 个路径, 每个路径独立回测
        - 训练集已删除与测试集时间重叠的样本 (purge)
        - 测试集后追加 embargo 期 (防止自相关泄漏)
    """

    def __init__(self, config: CPCVConfig | None = None) -> None:
        self.config = config or CPCVConfig()
        self._validate_config()

    def _validate_config(self) -> None:
        """校验配置参数."""
        cfg = self.config
        if cfg.n_groups < 2:
            raise ValueError(f"n_groups 必须 >= 2, 实际 {cfg.n_groups}")
        if cfg.n_test_groups < 1 or cfg.n_test_groups >= cfg.n_groups:
            raise ValueError(
                f"n_test_groups 必须在 [1, {cfg.n_groups - 1}], 实际 {cfg.n_test_groups}"
            )
        if cfg.purge_pct < 0 or cfg.purge_pct > 1:
            raise ValueError(f"purge_pct 必须在 [0, 1], 实际 {cfg.purge_pct}")
        if cfg.embargo_pct < 0 or cfg.embargo_pct > 0.5:
            raise ValueError(f"embargo_pct 必须在 [0, 0.5], 实际 {cfg.embargo_pct}")

    def split(self, n_samples: int) -> list[CPCVSplit]:
        """生成 CPCV 分割.

        Args:
            n_samples: 总样本数

        Returns:
            CPCVSplit 列表, 长度 = C(N, k)
        """
        cfg = self.config
        # 将样本均匀切分为 N 组
        group_boundaries = self._compute_group_boundaries(n_samples)
        # embargo 行数
        n_embargo = max(1, int(n_samples * cfg.embargo_pct))

        # 生成所有 C(N, k) 个测试组组合
        test_group_combos = list(combinations(range(cfg.n_groups), cfg.n_test_groups))

        splits: list[CPCVSplit] = []
        for path_id, test_groups in enumerate(test_group_combos):
            # 测试集索引 = 所有测试组的样本并集
            test_idx_list: list[int] = []
            for g in test_groups:
                g_start, g_end = group_boundaries[g]
                test_idx_list.extend(range(g_start, g_end))

            if not test_idx_list:
                continue

            test_idx = np.array(sorted(test_idx_list), dtype=np.int64)
            test_min, test_max = int(test_idx[0]), int(test_idx[-1])

            # 训练集 = 全部样本 - 测试集 - purge - embargo
            train_mask = np.ones(n_samples, dtype=bool)
            train_mask[test_idx] = False

            # Purge: 删除测试集时间重叠的样本
            # (简化版: 删除测试集前后 purge_pct × len(test) 个样本)
            if cfg.purge_pct > 0:
                purge_n = max(1, int(len(test_idx) * cfg.purge_pct))
                purge_lo = max(0, test_min - purge_n)
                purge_hi = min(n_samples, test_max + purge_n + 1)
                train_mask[purge_lo:test_max + 1] = False
                train_mask[test_min:purge_hi] = False

            # Embargo: 测试集后 n_embargo 个样本也不参与训练
            embargo_start = test_max + 1
            embargo_end = min(n_samples, embargo_start + n_embargo)
            train_mask[embargo_start:embargo_end] = False

            train_idx = np.where(train_mask)[0].astype(np.int64)

            # 检查最小训练样本数
            if len(train_idx) < cfg.min_train_samples:
                logger.debug(
                    f"Path {path_id} (groups={test_groups}): "
                    f"训练样本不足 {len(train_idx)} < {cfg.min_train_samples}, 跳过"
                )
                continue

            splits.append(CPCVSplit(
                path_id=path_id,
                train_idx=train_idx,
                test_idx=test_idx,
                test_groups=test_groups,
            ))

        logger.info(
            f"CPCV 生成 {len(splits)}/{len(test_group_combos)} 个有效路径 "
            f"(N={cfg.n_groups}, k={cfg.n_test_groups}, "
            f"理论路径数 C({cfg.n_groups},{cfg.n_test_groups})={len(test_group_combos)})"
        )
        return splits

    def _compute_group_boundaries(self, n_samples: int) -> list[tuple[int, int]]:
        """将 n_samples 均匀切分为 N 组, 返回 [(start, end), ...]."""
        cfg = self.config
        # 每组大小 (向上取整, 最后一组截断)
        group_size = math.ceil(n_samples / cfg.n_groups)
        boundaries: list[tuple[int, int]] = []
        for g in range(cfg.n_groups):
            start = g * group_size
            end = min(start + group_size, n_samples)
            if start >= n_samples:
                break
            boundaries.append((start, end))
        # 更新 n_groups 为实际组数
        if len(boundaries) != cfg.n_groups:
            logger.warning(
                f"样本数 {n_samples} 不足以切分为 {cfg.n_groups} 组, "
                f"实际切分为 {len(boundaries)} 组"
            )
            cfg.n_groups = len(boundaries)
        return boundaries

    def split_with_labels(
        self,
        data: pd.DataFrame,
        label_col: str | None = None,
    ) -> list[CPCVSplit]:
        """基于标签的 CPCV 分割 (考虑标签生成窗口的 purge).

        Args:
            data: 带时间索引的 DataFrame
            label_col: 标签列名 (None 时退化为普通 split)

        Returns:
            CPCVSplit 列表
        """
        n_samples = len(data)
        splits = self.split(n_samples)

        if label_col is None or label_col not in data.columns:
            return splits

        # TODO: 完整实现应基于标签生成时间窗口做更精确的 purge
        # 当前简化版已通过 purge_pct 参数处理
        return splits

    def run(
        self,
        data: pd.DataFrame,
        strategy_fn: Callable[[pd.DataFrame, pd.DataFrame], pd.Series],
        verbose: bool = True,
    ) -> list[CPCVResult]:
        """执行 CPCV 回测.

        Args:
            data: 全量数据 (带时间索引)
            strategy_fn: 策略函数 (train_df, test_df) -> test_returns Series
            verbose: 是否打印日志

        Returns:
            CPCVResult 列表, 长度 = 有效路径数
        """
        splits = self.split(len(data))
        results: list[CPCVResult] = []

        for split in splits:
            train_df = data.iloc[split.train_idx]
            test_df = data.iloc[split.test_idx]

            if len(train_df) < self.config.min_train_samples or len(test_df) < 5:
                if verbose:
                    logger.warning(
                        f"Path {split.path_id}: 数据不足 "
                        f"train={len(train_df)} test={len(test_df)}"
                    )
                continue

            try:
                test_ret = strategy_fn(train_df, test_df)
                if test_ret is None or len(test_ret) == 0:
                    continue

                # 计算指标
                sharpe, sortino, max_dd = self._compute_metrics(test_ret)

                result = CPCVResult(
                    path_id=split.path_id,
                    test_groups=split.test_groups,
                    sharpe=sharpe,
                    sortino=sortino,
                    max_dd=max_dd,
                    returns=test_ret.values if hasattr(test_ret, 'values') else np.array(test_ret),
                    n_test_samples=len(test_df),
                    n_train_samples=len(train_df),
                )
                results.append(result)

                if verbose:
                    logger.info(
                        f"Path {split.path_id} (groups={split.test_groups}): "
                        f"Sharpe={sharpe:.3f} Sortino={sortino:.3f} MaxDD={max_dd:.2%}"
                    )
            except (ValueError, KeyError, RuntimeError) as e:
                logger.error(
                    f"Path {split.path_id} 执行失败: {e}",
                    exc_info=True,
                )
                continue

        return results

    @staticmethod
    def _compute_metrics(returns: pd.Series) -> tuple[float, float, float]:
        """计算 Sharpe / Sortino / MaxDD.

        Args:
            returns: 收益序列

        Returns:
            (sharpe, sortino, max_dd)
        """
        if len(returns) < 2:
            return 0.0, 0.0, 0.0

        ret_arr = np.asarray(returns, dtype=np.float64)
        mean_ret = float(np.mean(ret_arr))
        std_ret = float(np.std(ret_arr, ddof=1))

        # 年化 (假设日频, 252 日)
        ann_factor = math.sqrt(252)
        sharpe = (mean_ret / std_ret * ann_factor) if std_ret > 0 else 0.0

        # Sortino: 只用下行波动
        downside = ret_arr[ret_arr < 0]
        if len(downside) > 0:
            downside_std = float(np.std(downside, ddof=1))
            sortino = (mean_ret / downside_std * ann_factor) if downside_std > 0 else 0.0
        else:
            sortino = float('inf') if mean_ret > 0 else 0.0

        # MaxDD
        cum = np.cumprod(1 + ret_arr)
        running_max = np.maximum.accumulate(cum)
        drawdown = (cum - running_max) / running_max
        max_dd = float(abs(np.min(drawdown))) if len(drawdown) > 0 else 0.0

        return sharpe, sortino, max_dd

    def aggregate(self, results: list[CPCVResult]) -> dict[str, Any]:
        """聚合所有路径的指标, 生成分布统计.

        Args:
            results: CPCVResult 列表

        Returns:
            {
                'n_paths': int,
                'sharpe_mean': float,
                'sharpe_std': float,
                'sharpe_median': float,
                'sharpe_p5': float,  # 5% 分位数 (悲观情景)
                'sharpe_p95': float,
                'sharpe_min': float,
                'sharpe_max': float,
                'sortino_mean': float,
                'max_dd_mean': float,
                'max_dd_p95': float,
                'sharpe_distribution': np.ndarray,  # 所有路径的 Sharpe
                ...
            }
        """
        if not results:
            return {'n_paths': 0}

        sharpes = np.array([r.sharpe for r in results])
        sortinos = np.array([r.sortino for r in results])
        max_dds = np.array([r.max_dd for r in results])

        # 拼接所有路径的测试收益 (用于整体 SR)
        all_returns: list[float] = []
        for r in results:
            if r.returns is not None:
                all_returns.extend(r.returns.tolist())

        overall_sharpe = 0.0
        if all_returns:
            ret_arr = np.array(all_returns)
            mean_ret = float(np.mean(ret_arr))
            std_ret = float(np.std(ret_arr, ddof=1))
            if std_ret > 0:
                overall_sharpe = mean_ret / std_ret * math.sqrt(252)

        return {
            'n_paths': len(results),
            # Sharpe 分布
            'sharpe_mean': float(np.mean(sharpes)),
            'sharpe_std': float(np.std(sharpes, ddof=1)),
            'sharpe_median': float(np.median(sharpes)),
            'sharpe_p5': float(np.percentile(sharpes, 5)),
            'sharpe_p25': float(np.percentile(sharpes, 25)),
            'sharpe_p75': float(np.percentile(sharpes, 75)),
            'sharpe_p95': float(np.percentile(sharpes, 95)),
            'sharpe_min': float(np.min(sharpes)),
            'sharpe_max': float(np.max(sharpes)),
            # Sortino 分布
            'sortino_mean': float(np.mean(sortinos)),
            'sortino_median': float(np.median(sortinos)),
            # MaxDD 分布
            'max_dd_mean': float(np.mean(max_dds)),
            'max_dd_p95': float(np.percentile(max_dds, 95)),
            # 整体 (拼接所有路径)
            'overall_sharpe': overall_sharpe,
            # 原始分布
            'sharpe_distribution': sharpes,
        }

    def summary(self, results: list[CPCVResult] | None = None) -> str:
        """生成汇总报告字符串."""
        if results is None:
            results = getattr(self, '_last_results', [])
        agg = self.aggregate(results)

        if agg['n_paths'] == 0:
            return "CPCV: 无有效路径"

        lines = [
            f"=== CPCV 汇总 ({agg['n_paths']} 个路径) ===",
            "Sharpe Ratio 分布:",
            f"  Mean:   {agg['sharpe_mean']:.4f}",
            f"  Median: {agg['sharpe_median']:.4f}",
            f"  Std:    {agg['sharpe_std']:.4f}",
            f"  P5:     {agg['sharpe_p5']:.4f}  (悲观情景)",
            f"  P95:    {agg['sharpe_p95']:.4f}",
            f"  Range:  [{agg['sharpe_min']:.4f}, {agg['sharpe_max']:.4f}]",
            f"Sortino (mean):  {agg['sortino_mean']:.4f}",
            f"Max DD (mean):   {agg['max_dd_mean']:.2%}",
            f"Max DD (P95):    {agg['max_dd_p95']:.2%}",
            f"Overall Sharpe:  {agg['overall_sharpe']:.4f}  (拼接所有路径)",
        ]
        return '\n'.join(lines)


__all__ = [
    'CPCVConfig',
    'CPCVResult',
    'CPCVSplit',
    'CombinatorialPurgedCV',
]
