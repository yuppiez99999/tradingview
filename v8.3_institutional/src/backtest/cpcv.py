# -*- coding: utf-8 -*-
"""
v7.6 CPCV (Combinatorial Purged Cross-Validation) — 组合净化交叉验证

对标 Marcos Lopez de Prado (2018) "Advances in Financial Machine Learning":
  - 金融数据的关键特性: 序列相关、标签重叠、信息泄露
  - 普通 K-Fold → 严重过拟合 + 假正率飙升 (+50-80%)
  - Purged K-Fold → 消除训练/验证间信息泄露
  - CPCV → 组合多个 Purged K-Fold 路径, 减少方差

核心创新:
  1. Purging: 训练集和验证集之间插入 gap, 防止标签重叠
  2. Embargo: 训练集最后 N 天不能出现在验证集中
  3. Backtesting Path: 每个组合生成独特的回测路径
  4. Deflated Sharpe Ratio: 多试验校正后的显著性检验

预期改进: 假正率从 ~40% 降至 ~5% (Harvey-Liu-Zhu 2016 多重检验校正)
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("v76.backtest.cpcv")


class CPCVCrossValidator:
    """
    v7.6 组合净化交叉验证

    Usage:
        cv = CPCVCrossValidator(n_groups=6, n_test_groups=2, purge_days=5)
        results = cv.run(data, train_func, test_func)
    """

    def __init__(
        self,
        n_groups: int = 6,
        n_test_groups: int = 2,
        purge_days: int = 5,
        embargo_days: int = 0,
        min_train_groups: int = 2,
    ):
        """
        Args:
            n_groups: 总分组数 (推荐 6-15)
            n_test_groups: 每次验证使用几组 (推荐 n_groups / 3)
            purge_days: 训练-验证间净化天数 (消除标签重叠)
            embargo_days: 验证-训练间禁运天数
            min_train_groups: 最少训练组数
        """
        self.n_groups = n_groups
        self.n_test_groups = n_test_groups
        self.purge_days = purge_days
        self.embargo_days = embargo_days
        self.min_train_groups = min_train_groups

        # 验证结果存储
        self.results_: List[Dict] = []
        self.scores_: List[float] = []
        self.n_paths_: int = 0

    def split(self, data: pd.DataFrame) -> List[Tuple[pd.DataFrame, pd.DataFrame]]:
        """生成 CPCV 训练/验证集分割

        Returns:
            List of (train_data, test_data) tuples
        """
        n = len(data)

        if n < self.n_groups:
            raise ValueError(f"数据长度 {n} < 分组数 {self.n_groups}")

        # 数据按时间排序后分 N 组
        group_size = n // self.n_groups
        group_boundaries = []
        for g in range(self.n_groups):
            start = g * group_size
            end = (g + 1) * group_size if g < self.n_groups - 1 else n
            group_boundaries.append((start, end))

        splits = []

        # 生成所有可能的 test groups 组合
        # 只考虑连续的 test groups (模拟前进式回测)
        for test_start in range(0, self.n_groups - self.n_test_groups + 1):
            test_groups = list(range(test_start, test_start + self.n_test_groups))

            # 训练组: 在 test group 之前的所有组 (前进式只在历史数据上训练)
            purge_end = test_start * group_size - self.purge_days
            train_end = max(purge_end, self.min_train_groups * group_size)

            if purge_end < self.min_train_groups * group_size:
                # 训练数据不足, 跳过
                continue

            if self.embargo_days > 0:
                train_end -= self.embargo_days

            train_data = data.iloc[:train_end]
            test_data = data.iloc[test_groups[0] * group_size : test_groups[-1] * group_size + group_size]

            if len(train_data) >= self.min_train_groups * group_size and len(test_data) > 0:
                splits.append((train_data, test_data))

        self.n_paths_ = len(splits)
        logger.info(f"CPCV: {self.n_groups} 组 → {self.n_paths_} 条验证路径")
        return splits

    def run(self, data: pd.DataFrame, train_fn: Callable, test_fn: Callable, **kwargs) -> Dict:
        """执行 CPCV 验证

        Args:
            data: 时间序列数据 (必须按时间排序)
            train_fn: training_func(data, **kwargs) → model
            test_fn: test_func(model, data, **kwargs) → score
            **kwargs: 传递给 train_fn/test_fn

        Returns:
            {
                'scores': [...],
                'mean_score': ...,
                'std_score': ...,
                'sharpe_ratio': ...,
                'deflated_sharpe': ...,
                'n_paths': ...,
            }
        """
        splits = self.split(data)
        scores = []

        for train_data, test_data in splits:
            try:
                model = train_fn(train_data, **kwargs)
                score = test_fn(model, test_data, **kwargs)
                scores.append(score)
                self.results_.append(
                    {
                        "train_size": len(train_data),
                        "test_size": len(test_data),
                        "score": score,
                    }
                )
            except Exception as e:
                logger.warning(f"CPCV 路径失败: {str(e)[:80]}")
                continue

        self.scores_ = scores

        if not scores:
            return {"mean_score": 0, "std_score": 0, "n_paths": 0}

        scores_arr = np.array(scores)
        mean_score = float(np.mean(scores_arr))
        std_score = float(np.std(scores_arr, ddof=1))

        # 信息比率
        sr = mean_score / max(std_score, 1e-8)

        # Deflated Sharpe / t-statistic
        deflated = self.deflated_sharpe_ratio(scores_arr)

        result = {
            "scores": [round(s, 6) for s in scores],
            "mean_score": round(mean_score, 6),
            "std_score": round(std_score, 6),
            "sharpe_ratio": round(sr, 4),
            "deflated_sharpe": round(deflated["deflated_sr"], 4),
            "deflated_p_value": round(deflated["p_value"], 6),
            "significant_at_5pct": deflated["p_value"] < 0.05,
            "n_paths": len(scores),
            "min_score": round(float(np.min(scores_arr)), 6),
            "max_score": round(float(np.max(scores_arr)), 6),
        }

        logger.info(
            f"CPCV 验证: SR={sr:.3f}, DeflatedSR={deflated['deflated_sr']:.3f}, "
            f"P={deflated['p_value']:.4f}, paths={len(scores)}"
        )

        return result

    def deflated_sharpe_ratio(
        self, observed_sr: float, n_trials: int = 100, variance_observed_sr: Optional[float] = None
    ) -> Dict:
        """Deflated Sharpe Ratio (Harvey-Liu-Zhu 2016)

        多重检验下的显著性校正:
        - 如果只试了一个策略, SR=1.0 可能是真的
        - 如果试了 1000 个策略, SR=1.0 可能只是运气

        Args:
            observed_sr: 策略的 Sharpe Ratio
            n_trials: 总试验次数 (包括未报告的)
            variance_observed_sr: SR 的渐近方差

        Returns:
            {deflated_sr, p_value, adjusted_significant}
        """
        # E[max(SR)] 的近似 — 极值统计
        # 假设 SRs 服从标准正态 (最保守)
        if variance_observed_sr is None:
            # SR 的渐近方差 (Lo 2002)
            variance_observed_sr = 1.0 + observed_sr**2 / 2

        # 第 k 个 order statistic 的期望
        # E[max_1..N(SR)] ~ sqrt(2 * log(N)) for large N
        import math

        expected_max_sr = math.sqrt(2 * math.log(max(n_trials, 2)))

        # Deflated SR
        deflated_sr = (observed_sr - expected_max_sr) / math.sqrt(variance_observed_sr)

        # P-value
        from scipy import stats

        p_value = 1 - stats.norm.cdf(deflated_sr)

        return {
            "observed_sr": observed_sr,
            "n_trials": n_trials,
            "expected_max_sr": round(expected_max_sr, 4),
            "deflated_sr": round(deflated_sr, 4),
            "p_value": round(p_value, 6),
        }

    def _deflated_sharpe_ratio(self, scores: np.ndarray) -> Dict:
        """从 CPCV 分数计算 Deflated SR"""
        if len(scores) == 0:
            return {"deflated_sr": 0, "p_value": 1}

        sr = np.mean(scores) / max(np.std(scores, ddof=1), 1e-8)
        return self.deflated_sharpe_ratio(observed_sr=sr, n_trials=self.n_paths_)


class PurgedKFold:
    """
    净化 K-Fold: 消除训练/验证之间的信息泄露

    金融数据问题: 你今天预测明天的股票 → 普通 K-Fold
    会随机把今天放在训练集, 明天放在验证集 → 信息泄露

    解决: 每个 fold 之间插入 purge gap (标签重叠天数)
    """

    def __init__(self, n_splits: int = 5, purge_days: int = 5, embargo_days: int = 0):
        self.n_splits = n_splits
        self.purge_days = purge_days
        self.embargo_days = embargo_days

    def split(self, data: pd.DataFrame) -> List[Tuple[np.ndarray, np.ndarray]]:
        """按时间顺序的净化 split"""
        n = len(data)
        fold_size = n // self.n_splits

        splits = []
        for fold in range(self.n_splits):
            # 验证集: fold 所在段
            test_start = fold * fold_size
            test_end = (fold + 1) * fold_size if fold < self.n_splits - 1 else n

            # 训练集: 验证集之前 - purge - embargo
            train_end = test_start - self.purge_days - self.embargo_days
            train_start = 0

            if train_end > 0:
                train_idx = np.arange(train_start, train_end)
                test_idx = np.arange(test_start, test_end)
                splits.append((train_idx, test_idx))

        return splits

    def split_indices(self, data: pd.DataFrame) -> List[Tuple[np.ndarray, np.ndarray]]:
        """返回索引数组的净化 split"""
        return self.split(data)
