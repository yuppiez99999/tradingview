# -*- coding: utf-8 -*-
"""
Purged K-Fold 时序交叉验证 — v8.3.2 过拟合防护升级

Claude Audit 2026-07-22 改进项 #4 — 过拟合防护 (评级 B- → A)

问题:
    标准 TimeSeriesSplit 在金融时序中存在标签重叠泄漏:
    若预测目标是未来 N 日收益率, 则训练集末尾的样本标签
    可能与测试集开头样本的标签共享了未来信息。

    De Prado (2018) "Advances in Financial ML" 提出:
    - Purge: 在 train/test 分割点前后各留出标签窗口长度的样本
    - Embargo: 额外在 purge 后再留出一个安全间隔

用法:
    from utils.purged_kfold import purged_timeseries_split, validate_embargo

    for train_idx, test_idx in purged_timeseries_split(n_samples, n_splits=5, embargo_pct=0.01):
        X_train, X_test = X[train_idx], X[test_idx]
"""

from __future__ import annotations

import logging
from typing import Generator, List, Tuple

import numpy as np

logger = logging.getLogger("purged_kfold")


def purged_timeseries_split(
    n_samples: int,
    n_splits: int = 5,
    embargo_pct: float = 0.01,
    min_train_pct: float = 0.3,
) -> Generator[Tuple[np.ndarray, np.ndarray], None, None]:
    """Purged 时间序列交叉验证分割器。

    在标准 TimeSeriesSplit 基础上增加 embargo (禁运期),
    避免训练集尾部标签与测试集头部标签共享未来信息。

    示意图:
        [---- Train ----] |e| [-- Test --] |e| [-- Test --]
                          purge             embargo

    Args:
        n_samples: 总样本数
        n_splits: 折数 (默认5)
        embargo_pct: 禁运比例 (占总样本的百分比, 默认1%)
        min_train_pct: 最小训练集比例 (默认30%)

    Yields:
        (train_indices, test_indices) — numpy 整数数组

    Ref:
        Lopez de Prado, M. (2018). Advances in Financial Machine Learning.
        Chapter 7: Cross-Validation in Finance.
    """
    if n_splits < 2:
        raise ValueError(f"n_splits 必须 >= 2, 当前值 {n_splits}")
    if n_samples < 30:
        logger.warning("样本量 %d 过小, Purged KFold 可能不稳定", n_samples)
        # 回退到最简单的 80/20 分割
        split_point = max(int(n_samples * 0.8), 10)
        yield np.arange(split_point), np.arange(split_point, n_samples)
        return

    embargo_size = max(1, int(n_samples * embargo_pct))
    min_train = int(n_samples * min_train_pct)
    test_size = (n_samples - min_train) // n_splits

    if test_size < 5:
        logger.warning("测试集太小 (%d), 减少折数", test_size)
        n_splits = max(2, (n_samples - min_train) // 10)
        test_size = (n_samples - min_train) // n_splits

    indices = np.arange(n_samples)

    for i in range(n_splits):
        # 训练集: [0, train_end)
        train_end = min_train + i * test_size
        # 测试集: [test_start, test_end)
        test_start = train_end + embargo_size
        test_end = min(test_start + test_size, n_samples)

        if test_end <= test_start or train_end < 10:
            continue

        train_idx = indices[:train_end]
        test_idx = indices[test_start:test_end]

        # 额外 purge: 训练集尾部剔除 embargo 大小的样本
        if len(train_idx) > embargo_size:
            train_idx = train_idx[:-embargo_size]

        yield train_idx, test_idx


def purged_kfold_generator(
    n_samples: int,
    n_splits: int = 5,
    embargo_pct: float = 0.01,
    purge_pct: float = 0.01,
    min_train_pct: float = 0.3,
) -> List[Tuple[np.ndarray, np.ndarray]]:
    """一次性生成所有 Purged KFold 分割 (便于检查)。

    Args:
        purge_pct: 每个分割点两侧剔除的比例 (De Prado 建议 1%)

    Returns:
        [(train_idx, test_idx), ...]
    """
    folds = []
    embargo_size = max(1, int(n_samples * embargo_pct))
    purge_size = max(1, int(n_samples * purge_pct))
    min_train = int(n_samples * min_train_pct)
    test_size = (n_samples - min_train) // n_splits

    if test_size < 5:
        n_splits = max(2, (n_samples - min_train) // 10)
        test_size = (n_samples - min_train) // n_splits

    indices = np.arange(n_samples)

    for i in range(n_splits):
        train_end = min_train + i * test_size
        test_start = train_end + embargo_size + purge_size
        test_end = min(test_start + test_size, n_samples)

        if test_end <= test_start or train_end < purge_size:
            continue

        train_idx = indices[: train_end - purge_size]
        test_idx = indices[test_start:test_end]
        folds.append((train_idx, test_idx))

    return folds


def validate_embargo(
    train_idx: np.ndarray,
    test_idx: np.ndarray,
    min_gap: int = 1,
) -> Tuple[bool, str]:
    """验证训练集和测试集之间是否存在足够的安全间隔。

    Args:
        train_idx: 训练集索引
        test_idx: 测试集索引
        min_gap: 最小间隔 (样本数)

    Returns:
        (是否通过验证, 说明)
    """
    if len(train_idx) == 0 or len(test_idx) == 0:
        return False, "训练集或测试集为空"

    train_max = train_idx.max()
    test_min = test_idx.min()
    gap = test_min - train_max

    if gap < min_gap:
        return False, (
            f"间隔不足: train_max={train_max}, test_min={test_min}, gap={gap} < min_gap={min_gap} — 存在标签泄漏风险"
        )

    return True, f"安全间隔: gap={gap} >= min_gap={min_gap}"


def overfitting_diagnosis(
    fold_metrics: List[dict],
    metric_keys: Tuple[str, ...] = ("r2", "ic", "sharpe"),
) -> dict:
    """过拟合诊断 — 基于 Purged KFold 各折指标分析。

    诊断规则:
        1. 前半折均值 vs 后半折均值: 若后半 < 前半*0.6 → 信号衰减
        2. 各折标准差 / 均值: 若 CV > 1.0 → 不稳定
        3. 最佳折 vs 最差折: 差异 > 3x → 可能的过拟合

    Args:
        fold_metrics: [{"r2": ..., "ic": ..., "sharpe": ...}, ...]
        metric_keys: 要分析的指标

    Returns:
        诊断结果字典
    """
    if len(fold_metrics) < 2:
        return {"status": "INSUFFICIENT_DATA", "message": "至少需要 2 折"}

    diagnoses = {}
    n = len(fold_metrics)

    for key in metric_keys:
        values = [f.get(key, 0) for f in fold_metrics]
        if not values:
            continue

        mean_val = np.mean(values)
        std_val = np.std(values)
        cv = abs(std_val / (mean_val + 1e-9))

        mid = n // 2
        first_half_mean = np.mean(values[:mid])
        second_half_mean = np.mean(values[mid:])

        best = max(values)
        worst = min(values)
        spread_ratio = abs(best) / (abs(worst) + 1e-9)

        issues = []
        if second_half_mean < first_half_mean * 0.6 and first_half_mean > 0:
            issues.append(f"后半折衰减: {first_half_mean:.4f} → {second_half_mean:.4f}")
        if cv > 1.0:
            issues.append(f"高变异系数 CV={cv:.2f}")
        if spread_ratio > 3.0:
            issues.append(f"极值偏离: best={best:.4f}, worst={worst:.4f}")

        diagnoses[key] = {
            "mean": round(mean_val, 4),
            "std": round(std_val, 4),
            "cv": round(cv, 4),
            "first_half_mean": round(first_half_mean, 4),
            "second_half_mean": round(second_half_mean, 4),
            "spread_ratio": round(spread_ratio, 2),
            "issues": issues,
            "pass": len(issues) == 0,
        }

    all_issues = [i for d in diagnoses.values() for i in d.get("issues", [])]
    overall_pass = len(all_issues) == 0

    return {
        "overall_pass": overall_pass,
        "summary": "PASS - 无过拟合迹象" if overall_pass else "WARN - 检测到过拟合风险",
        "metrics": diagnoses,
        "total_issues": len(all_issues),
    }
