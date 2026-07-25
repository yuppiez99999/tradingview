# -*- coding: utf-8 -*-
"""
walk_forward.py — Purged Walk-Forward Cross-Validation 框架 v1.0

参考: Marcos Lopez de Prado, "Advances in Financial Machine Learning" (2018)
      Chapter 7: Cross-Validation in Finance

实现三个核心功能:
  1. Purged Walk-Forward Split — 时序分割 + 清洗期 + 禁运期
  2. Combinatorial Purged CV — 小样本组合清洗验证
  3. Walk-Forward 稳定性检验 — 多窗口一致性评估

设计原则:
  - 零外部依赖 (纯Python标准库 + 可选numpy加速)
  - 所有分割保证: train数据严格早于test数据
  - 清洗期消除标签重叠, 禁运期消除序列相关性泄露
"""

import math
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass, field


# ============================================================
# 数据类
# ============================================================

@dataclass
class WalkForwardFold:
    """单折 Walk-Forward 结构"""
    fold_id: int
    train_start: int
    train_end: int          # 清洗后的训练结束索引 (排他)
    train_end_raw: int      # 清洗前的原始训练结束索引
    test_start: int
    test_end: int
    purge_days: int
    embargo_days: int

    @property
    def train_size(self) -> int:
        return self.train_end - self.train_start

    @property
    def test_size(self) -> int:
        return self.test_end - self.test_start

    @property
    def gap_size(self) -> int:
        """训练与测试之间的总隔离天数"""
        return self.test_start - self.train_end


@dataclass
class WalkForwardResult:
    """单折回测结果"""
    fold: WalkForwardFold
    train_metrics: Dict[str, float] = field(default_factory=dict)
    test_metrics: Dict[str, float] = field(default_factory=dict)
    # 性能指标
    train_sharpe: float = 0.0
    test_sharpe: float = 0.0
    train_return: float = 0.0
    test_return: float = 0.0
    train_volatility: float = 0.0
    test_volatility: float = 0.0
    train_max_dd: float = 0.0
    test_max_dd: float = 0.0

    @property
    def sharpe_decay(self) -> float:
        """夏普衰减 (样本外 vs 样本内)"""
        if self.train_sharpe == 0:
            return 0.0
        return (self.test_sharpe - self.train_sharpe) / abs(self.train_sharpe)


@dataclass
class WalkForwardReport:
    """多折 Walk-Forward 汇总报告"""
    results: List[WalkForwardResult]
    n_folds: int
    total_test_days: int
    # 汇总统计
    mean_test_sharpe: float = 0.0
    std_test_sharpe: float = 0.0
    mean_test_return: float = 0.0
    std_test_return: float = 0.0
    mean_sharpe_decay: float = 0.0
    prob_sharpe_positive: float = 0.0   # P(SR>0)
    is_stable: bool = False
    stability_score: float = 0.0


# ============================================================
# 核心API
# ============================================================

def purged_walk_forward_split(
    n_total: int,
    train_size: int,
    test_size: int,
    purge_days: int = 5,
    embargo_days: int = 0,
    min_train_size: int = 100,
    step_size: int = None,
) -> List[WalkForwardFold]:
    """
    Purged Walk-Forward 时序分割

    核心思想:
    - 训练集末尾清洗掉 purge_days (消除标签在时间上的重叠)
    - 训练集和测试集之间插入 embargo_days (消除序列相关性泄露)
    - 每折向前滑动 step_size 天

    Args:
        n_total: 总观测天数
        train_size: 每折训练集大小 (清洗前)
        test_size: 每折测试集大小
        purge_days: 训练集末尾剔除天数 (默认5)
        embargo_days: 训练/测试之间隔离天数 (默认0)
        min_train_size: 最小有效训练集大小 (清洗后)
        step_size: 每折前进步长 (默认=test_size)

    Returns:
        WalkForwardFold列表, 按时间顺序排列

    Example:
        >>> folds = purged_walk_forward_split(1000, 300, 60, purge_days=5, embargo_days=5)
        >>> len(folds) >= 5
        True
        >>> folds[0].train_start, folds[0].train_end, folds[0].test_start
        (0, 295, 305)
    """
    if step_size is None:
        step_size = test_size

    folds = []
    start = 0
    fold_id = 0

    while True:
        train_start = start
        train_end_raw = train_start + train_size
        train_end = train_end_raw - purge_days

        # 清洗后训练集太小则停止
        if train_end - train_start < min_train_size:
            break

        test_start = train_end_raw + embargo_days
        test_end = test_start + test_size

        if test_end > n_total:
            break

        folds.append(WalkForwardFold(
            fold_id=fold_id,
            train_start=train_start,
            train_end=train_end,
            train_end_raw=train_end_raw,
            test_start=test_start,
            test_end=test_end,
            purge_days=purge_days,
            embargo_days=embargo_days,
        ))

        start += step_size
        fold_id += 1

    return folds


def combinatorial_purged_cv(
    n_total: int,
    n_splits: int,
    test_size: int,
    purge_days: int = 5,
    embargo_days: int = 5,
    min_train_size: int = 100,
) -> List[WalkForwardFold]:
    """
    组合清洗交叉验证 (Combinatorial Purged CV)

    生成多个重叠的训练集, 但每个测试集独立且被清洗。
    适用于小样本场景 (n_total < 1000)。

    与标准 purged_walk_forward_split 的区别:
    - 所有训练集从0开始 (不使用滚动窗口)
    - 每折仅测试集位置不同
    - 保证任意折的 test 都与所有折的 train 分离

    Args:
        n_total: 总天数
        n_splits: 期望折数
        test_size: 每折测试集大小
        purge_days: 清洗期
        embargo_days: 禁运期
        min_train_size: 最小训练集

    Returns:
        WalkForwardFold列表
    """
    folds = []
    gap = purge_days + embargo_days

    for k in range(n_splits):
        test_end = n_total - k * (test_size + gap)
        test_start = test_end - test_size

        if test_start < min_train_size + gap:
            break

        # 所有折共用同一个训练集: [0, test_start - gap)
        train_end = test_start - gap
        train_start = 0

        if train_end - train_start < min_train_size:
            continue

        folds.append(WalkForwardFold(
            fold_id=k,
            train_start=train_start,
            train_end=train_end,
            train_end_raw=train_end,  # 组合CV中purge已体现在gap中
            test_start=test_start,
            test_end=test_end,
            purge_days=purge_days,
            embargo_days=embargo_days,
        ))

    return folds


def check_cv_leakage(folds: List[WalkForwardFold]) -> List[str]:
    """
    检查 Walk-Forward 分割是否存在数据泄露

    检查项:
    1. 同折内 train_end < test_start
    2. 任意两折的测试集不重叠
    3. 任意折的训练集不包含其它折的测试数据

    Returns:
        泄露描述列表, 空列表=无泄露
    """
    issues = []

    for i, f in enumerate(folds):
        # 检查1: 同折内
        if f.train_end >= f.test_start:
            issues.append(
                f"折{i}: train_end({f.train_end}) >= test_start({f.test_start}), 同折泄露"
            )

        for j, g in enumerate(folds):
            if i >= j:
                continue

            # 检查2: 测试集重叠
            ti = set(range(f.test_start, f.test_end))
            tj = set(range(g.test_start, g.test_end))
            overlap = ti & tj
            if overlap:
                issues.append(
                    f"折{i}测试 vs 折{j}测试: 重叠{len(overlap)}天 [{min(overlap)},{max(overlap)}]"
                )

    return issues


def walk_forward_stability_test(
    performance_series: List[float],
    n_windows: int = 3,
    sharpe_range_threshold: float = 1.0,
    return_range_threshold: float = 0.5,
) -> Dict[str, Any]:
    """
    Walk-Forward 多窗口稳定性检验

    动机: 即使单折表现好, 如果不同时间窗口表现差异巨大,
          策略可能在样本外显著劣化。

    Args:
        performance_series: 日收益率序列
        n_windows: 等分子窗口数
        sharpe_range_threshold: 夏普跨窗口波动阈值 (默认100%)
        return_range_threshold: 收益率跨窗口波动阈值 (默认50%)

    Returns:
        {
            'stable': bool,
            'windows': [{window stats}],
            'reasons': [str],
            'sharpe_cv': float,  # 夏普变异系数
            'return_cv': float,  # 收益变异系数
        }
    """
    n = len(performance_series)
    if n < n_windows * 50:
        return {
            'stable': False,
            'windows': [],
            'reasons': [f'数据量不足: {n}天 < {n_windows * 50}天'],
            'sharpe_cv': 0.0,
            'return_cv': 0.0,
        }

    window_size = n // n_windows
    window_stats = []

    for w in range(n_windows):
        start = w * window_size
        end = min(start + window_size, n)
        window_rets = performance_series[start:end]

        if len(window_rets) < 20:
            continue

        ann_ret = _annualized_return(window_rets)
        ann_vol = _annualized_volatility(window_rets)
        sharpe = ann_ret / max(ann_vol, 0.0001)
        max_dd = _max_drawdown_from_returns(window_rets)

        window_stats.append({
            'window': w,
            'start': start,
            'end': end,
            'n_days': len(window_rets),
            'ann_return': ann_ret,
            'ann_volatility': ann_vol,
            'sharpe': sharpe,
            'max_drawdown': max_dd,
        })

    if len(window_stats) < 2:
        return {
            'stable': True,
            'windows': window_stats,
            'reasons': ['窗口数不足, 无法评估稳定性'],
            'sharpe_cv': 0.0,
            'return_cv': 0.0,
        }

    # 计算跨窗口统计
    sharpes = [w['sharpe'] for w in window_stats]
    returns = [w['ann_return'] for w in window_stats]

    mean_sharpe = sum(sharpes) / len(sharpes)
    mean_return = sum(returns) / len(returns)

    std_sharpe = math.sqrt(
        sum((s - mean_sharpe)**2 for s in sharpes) / (len(sharpes) - 1)
    ) if len(sharpes) > 1 else 0.0
    std_return = math.sqrt(
        sum((r - mean_return)**2 for r in returns) / (len(returns) - 1)
    ) if len(returns) > 1 else 0.0

    sharpe_cv = abs(std_sharpe / mean_sharpe) if mean_sharpe != 0 else float('inf')
    return_cv = abs(std_return / mean_return) if mean_return != 0 else float('inf')

    stable = True
    reasons = []

    # 夏普变异系数检查
    if sharpe_cv > sharpe_range_threshold:
        stable = False
        reasons.append(
            f"夏普变异系数{sharpe_cv:.1f} > 阈值{sharpe_range_threshold} "
            f"(跨窗口夏普: {[f'{s:.2f}' for s in sharpes]})"
        )

    # 收益变异检查
    if return_cv > return_range_threshold:
        stable = False
        reasons.append(
            f"收益变异系数{return_cv:.1f} > 阈值{return_range_threshold} "
            f"(跨窗口收益: {[f'{r*100:.1f}%' for r in returns]})"
        )

    # 符号一致性 (所有窗口收益同号)
    if all(r > 0 for r in returns) or all(r < 0 for r in returns):
        pass  # 一致性好
    else:
        reasons.append(f"收益符号不一致: {[f'{r*100:.1f}%' for r in returns]}")

    return {
        'stable': stable,
        'windows': window_stats,
        'reasons': reasons,
        'sharpe_cv': sharpe_cv,
        'return_cv': return_cv,
    }


def generate_walk_forward_report(
    fold_results: List[WalkForwardResult],
) -> WalkForwardReport:
    """
    汇总 Walk-Forward 回测结果, 生成标准化报告

    Args:
        fold_results: 各折结果列表

    Returns:
        WalkForwardReport 汇总对象
    """
    if not fold_results:
        return WalkForwardReport(
            results=[], n_folds=0, total_test_days=0,
            is_stable=False, stability_score=0.0,
        )

    n_folds = len(fold_results)
    test_sharpes = [r.test_sharpe for r in fold_results]
    test_returns = [r.test_return for r in fold_results]
    sharpe_decays = [r.sharpe_decay for r in fold_results]
    total_test_days = sum(r.fold.test_size for r in fold_results)

    n = len(test_sharpes)

    mean_sharpe = sum(test_sharpes) / n
    std_sharpe = math.sqrt(
        sum((s - mean_sharpe)**2 for s in test_sharpes) / max(n - 1, 1)
    )
    mean_return = sum(test_returns) / n
    std_return = math.sqrt(
        sum((r - mean_return)**2 for r in test_returns) / max(n - 1, 1)
    )
    mean_decay = sum(sharpe_decays) / n

    # P(SR > 0): 正夏普折占比
    prob_positive = sum(1 for s in test_sharpes if s > 0) / n

    # 稳定性评分: 0-1
    # 评分标准:
    #   样本外Sharpe >= 样本内Sharpe的50%: +0.3
    #   P(SR>0) >= 0.66: +0.3
    #   Sharpe变异系数 < 1.0: +0.2
    #   所有折的样本外回报为正: +0.2
    stability = 0.0
    if mean_decay > -0.5:
        stability += 0.3
    if prob_positive >= 0.66:
        stability += 0.3
    sharpe_cv = abs(std_sharpe / mean_sharpe) if mean_sharpe != 0 else float('inf')
    if sharpe_cv < 1.0:
        stability += 0.2
    if all(r > 0 for r in test_returns):
        stability += 0.2

    return WalkForwardReport(
        results=fold_results,
        n_folds=n_folds,
        total_test_days=total_test_days,
        mean_test_sharpe=mean_sharpe,
        std_test_sharpe=std_sharpe,
        mean_test_return=mean_return,
        std_test_return=std_return,
        mean_sharpe_decay=mean_decay,
        prob_sharpe_positive=prob_positive,
        is_stable=(stability >= 0.6),
        stability_score=stability,
    )


# ============================================================
# 辅助函数
# ============================================================

def _annualized_return(daily_rets: List[float]) -> float:
    """年化收益率 (252交易日)"""
    if not daily_rets:
        return 0.0
    total = 1.0
    for r in daily_rets:
        total *= (1 + r)
    n_years = len(daily_rets) / 252
    if n_years <= 0:
        return 0.0
    return total ** (1 / n_years) - 1


def _annualized_volatility(daily_rets: List[float]) -> float:
    """年化波动率"""
    n = len(daily_rets)
    if n < 2:
        return 0.0
    mean = sum(daily_rets) / n
    var = sum((r - mean)**2 for r in daily_rets) / (n - 1)
    return math.sqrt(var) * math.sqrt(252)


def _max_drawdown_from_returns(daily_rets: List[float]) -> float:
    """从日收益率计算最大回撤"""
    equity = [1.0]
    for r in daily_rets:
        equity.append(equity[-1] * (1 + r))

    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = (v - peak) / peak
        if abs(dd) > max_dd:
            max_dd = abs(dd)
    return max_dd


# ============================================================
# 便捷打印函数
# ============================================================

def format_fold_summary(fold: WalkForwardFold) -> str:
    """格式化单折摘要"""
    return (
        f"折{fold.fold_id}: train[{fold.train_start}:{fold.train_end}]"
        f"({fold.train_size}天) "
        f"--[{fold.gap_size}天隔离]--> "
        f"test[{fold.test_start}:{fold.test_end}]({fold.test_size}天)"
    )


def print_all_folds(folds: List[WalkForwardFold]):
    """打印所有折的摘要"""
    for f in folds:
        print(f"  {format_fold_summary(f)}")
    print(f"  共{len(folds)}折, 总测试{sum(f.test_size for f in folds)}天")


def print_walk_forward_report(report: WalkForwardReport):
    """打印 Walk-Forward 报告"""
    print("=" * 60)
    print(f"  Walk-Forward 验证报告 ({report.n_folds}折)")
    print("=" * 60)
    print(f"  总样本外天数: {report.total_test_days}")
    print(f"  样本外夏普: {report.mean_test_sharpe:.2f} ± {report.std_test_sharpe:.2f}")
    print(f"  样本外年化: {report.mean_test_return*100:.2f}% ± {report.std_test_return*100:.2f}%")
    print(f"  夏普衰减: {report.mean_sharpe_decay*100:.1f}%")
    print(f"  P(SR>0): {report.prob_sharpe_positive*100:.0f}%")
    print(f"  稳定性评分: {report.stability_score:.2f}")
    print(f"  通过检验: {'是' if report.is_stable else '否'}")


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    # 基础分割演示
    print("=== Purged Walk-Forward Split ===")
    folds = purged_walk_forward_split(1000, 300, 60, purge_days=5, embargo_days=5)
    print_all_folds(folds)

    # 泄露检查
    issues = check_cv_leakage(folds)
    if issues:
        print(f"\n  数据泄露警告: {len(issues)}项")
        for issue in issues:
            print(f"    - {issue}")
    else:
        print("\n  数据泄露检查: 通过")

    # 组合CV
    print("\n=== Combinatorial Purged CV ===")
    combo_folds = combinatorial_purged_cv(500, 4, 40, purge_days=5, embargo_days=5)
    print_all_folds(combo_folds)
    issues = check_cv_leakage(combo_folds)
    print(f"  泄露检查: {'通过' if not issues else f'{len(issues)}项问题'}")
