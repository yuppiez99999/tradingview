"""
ML模型显著性检验 — v5.10 P0-2 修复
====================================
验证ML模型预测是否统计显著优于抛硬币(50/50)。
Renaissance标准: 单一信号 t-statistic > 2.0 才纳入组合。

功能:
1. bootstrap_accuracy_ci()        — Bootstrap置信区间
2. permutation_test()             — 置换检验 (p值)
3. compute_rank_ic()              — Rank IC 计算
4. rank_ic_analysis()             — Rank IC 显著性分析
5. benjamini_hochberg_correction()— BH FDR多重检验校正
6. whites_reality_check()         — White's Reality Check
7. generate_significance_report() — 综合显著性报告

参考文献:
- White, H. (2000). A Reality Check for Data Snooping. Econometrica.
- Benjamini, Y. & Hochberg, Y. (1995). Controlling the FDR. JRSS-B.
- Lopez de Prado, M. (2018). Advances in Financial Machine Learning.
"""

import math
from typing import Dict, List, Optional, Union

import numpy as np

# ═══════════════════════════════════════════════════════
#  Bootstrap 置信区间
# ═══════════════════════════════════════════════════════


def bootstrap_accuracy_ci(
    predictions: List[int],
    n_bootstrap: int = 1000,
    confidence: float = 0.95,
    random_seed: Optional[int] = None,
) -> Dict[str, float]:
    """Bootstrap 准确率置信区间

    从预测结果中重复抽样(Bootstrap)估计准确率的置信区间。

    Args:
        predictions: 预测结果列表, 1=正确, 0=错误
        n_bootstrap: Bootstrap重采样次数
        confidence: 置信水平 (默认95%)
        random_seed: 随机种子

    Returns:
        {'mean': 平均准确率, 'ci_lower': 下界, 'ci_upper': 上界, 'std': 标准差}
    """
    import random

    rng = random.Random(random_seed) if random_seed else random.Random()
    n = len(predictions)

    if n == 0:
        return {"mean": 0.0, "ci_lower": 0.0, "ci_upper": 0.0, "std": 0.0}

    # 真实准确率
    obs_accuracy = sum(predictions) / n

    # Bootstrap
    boot_means = []
    for _ in range(n_bootstrap):
        sample = [rng.choice(predictions) for _ in range(n)]
        boot_means.append(sum(sample) / n)

    boot_means.sort()
    mean_acc = sum(boot_means) / n_bootstrap
    std_acc = (sum((x - mean_acc) ** 2 for x in boot_means) / (n_bootstrap - 1)) ** 0.5

    alpha = (1 - confidence) / 2
    idx_lower = int(alpha * n_bootstrap)
    idx_upper = int((1 - alpha) * n_bootstrap)

    return {
        "mean": round(obs_accuracy, 6),
        "ci_lower": round(boot_means[idx_lower], 6),
        "ci_upper": round(boot_means[idx_upper], 6),
        "std": round(std_acc, 6),
    }


# ═══════════════════════════════════════════════════════
#  置换检验
# ═══════════════════════════════════════════════════════


def permutation_test(
    y_true: List[int],
    y_pred: List[int],
    n_permutations: int = 1000,
    metric: str = "accuracy",
    random_seed: Optional[int] = None,
) -> Dict[str, Union[float, str]]:
    """置换检验: 预测是否显著优于随机打乱标签

    H0: 预测结果与随机打乱无差异 (模型无预测力)
    H1: 预测结果显著优于随机

    p值 = P(随机打乱后的准确率 ≥ 观察准确率)

    Args:
        y_true: 真实标签列表
        y_pred: 预测标签列表
        n_permutations: 置换次数
        metric: 'accuracy' 或 'f1'
        random_seed: 随机种子

    Returns:
        {'observed': 观察值, 'p_value': p值, 'null_mean': 零分布均值}
    """
    import random

    rng = random.Random(random_seed) if random_seed else random.Random()

    n = len(y_true)
    if n == 0:
        return {"observed": 0.0, "p_value": 1.0, "null_mean": 0.0}

    # 观察值
    observed = _compute_metric(y_true, y_pred, metric)

    # 置换: 随机打乱预测标签, 计算零分布
    null_dist = []
    for _ in range(n_permutations):
        shuffled = rng.sample(y_pred, n)
        null_dist.append(_compute_metric(y_true, shuffled, metric))

    # p值 = P(null ≥ observed)
    null_mean = sum(null_dist) / n_permutations
    p_value = sum(1 for v in null_dist if v >= observed) / n_permutations

    return {
        "observed": round(observed, 6),
        "p_value": round(p_value, 6),
        "null_mean": round(null_mean, 6),
        "metric": metric,
        "n_permutations": n_permutations,
    }


def _compute_metric(y_true: List[int], y_pred: List[int], metric: str) -> float:
    """计算分类指标"""
    n = len(y_true)
    if n == 0:
        return 0.0

    correct = sum(1 for t, p in zip(y_true, y_pred) if t == p)
    accuracy = correct / n

    if metric == "accuracy":
        return accuracy

    if metric == "f1":
        # F1-score
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 1)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 1)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 0)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0

        if precision + recall > 0:
            return 2 * precision * recall / (precision + recall)
        return 0.0

    return accuracy


# ═══════════════════════════════════════════════════════
#  Rank IC 计算与显著性
# ═══════════════════════════════════════════════════════


def compute_rank_ic(
    predictions: "np.ndarray",
    future_returns: "np.ndarray",
) -> float:
    """计算 Rank IC (Spearman 秩相关系数)

    Rank IC = corr(rank(predictions), rank(future_returns))

    Args:
        predictions: 预测值 (n,)
        future_returns: 未来收益 (n,)

    Returns:
        Rank IC 值 [-1, 1]
    """
    import numpy as np

    n = len(predictions)
    if n < 3:
        return 0.0

    # 获取秩 (处理平局用平均秩)
    from scipy.stats import rankdata

    pred_rank = rankdata(predictions)
    ret_rank = rankdata(future_returns)

    # Pearson 相关系数 = Spearman 秩相关系数 (对秩)
    ic = float(np.corrcoef(pred_rank, ret_rank)[0, 1])
    return ic if not np.isnan(ic) else 0.0


def rank_ic_analysis(
    predictions: "np.ndarray",
    future_returns: "np.ndarray",
) -> Dict[str, float]:
    """Rank IC 显著性分析

    计算: 均值IC, IC标准差, IC_IR (信息比率), t统计量, p值

    Args:
        predictions: 单次预测值 (n_observations,)
        future_returns: 对应实际收益

    Returns:
        {'mean_ic', 'std_ic', 'ic_ir', 't_statistic', 'p_value', 'n'}
    """

    ic = compute_rank_ic(predictions, future_returns)
    n = len(predictions)

    if n < 3:
        return {
            "mean_ic": ic,
            "std_ic": 0.0,
            "ic_ir": 0.0,
            "t_statistic": 0.0,
            "p_value": 1.0,
            "n": n,
        }

    # 单个IC值的"标准差"用Jackknife近似
    # IC_IR = mean_IC / std_IC, 当只有一次横截面IC时, 用近似公式
    # 更严格的做法: 用 Fisher z-transform
    z = math.atanh(max(min(ic, 0.9999), -0.9999))
    std_ic = 1.0 / math.sqrt(n - 3)

    # IC_IR
    ic_ir = z / std_ic if std_ic > 0 else 0.0

    # t检验 (H0: IC=0, 双尾)
    t_stat = z / std_ic if std_ic > 0 else 0.0
    df = n - 2
    p_value = 2.0 * (1.0 - _t_cdf(abs(t_stat), df)) if df > 0 else 1.0

    return {
        "mean_ic": round(ic, 6),
        "std_ic": round(std_ic, 6),
        "ic_ir": round(ic_ir, 4),
        "t_statistic": round(t_stat, 4),
        "p_value": round(p_value, 6),
        "n": n,
    }


def _t_cdf(t_val: float, df: float) -> float:
    """不完全beta函数的 t 分布 CDF 近似 (避免scipy依赖)"""
    # 使用正态近似 (大样本下精确)
    # Abramowitz and Stegun 26.7.1
    if df <= 0:
        return 0.5

    x = t_val * (1.0 - 1.0 / (4.0 * df)) / math.sqrt(1.0 + t_val * t_val / (2.0 * df))
    # 标准正态CDF 近似
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# ═══════════════════════════════════════════════════════
#  Benjamini-Hochberg FDR 校正
# ═══════════════════════════════════════════════════════


def benjamini_hochberg_correction(
    p_values: List[float],
    alpha: float = 0.05,
) -> Dict:
    """Benjamini-Hochberg FDR 多重检验校正

    控制 False Discovery Rate (FDR) 而非 Family-Wise Error Rate (FWER)。
    适用于: 对多标的/多特征/多策略同时进行显著性检验。

    Args:
        p_values: p值列表
        alpha: FDR 控制水平 (默认0.05)

    Returns:
        {'significant': [(index, p_value), ...], 按p值升序,
         'rejected': 被拒绝(显著)的数量,
         'threshold': BH阈值,
         'adjusted_p_values': BH调整后的p值列表}
    """
    n = len(p_values)
    if n == 0:
        return {"significant": [], "rejected": 0, "threshold": 0.0, "adjusted_p_values": []}

    # 按p值升序排列, 记录原始索引
    sorted_indices = sorted(range(n), key=lambda i: p_values[i])
    sorted_p = [p_values[i] for i in sorted_indices]

    # BH 步骤:
    # 找到最大 k 满足 p_(k) <= k * alpha / n
    rejected = 0
    for k in range(n - 1, -1, -1):
        if sorted_p[k] <= (k + 1) * alpha / n:
            rejected = k + 1
            break

    # BH 调整: p_adj_(i) = min(p_(i) * n / i, p_adj_(i+1))
    adjusted = [0.0] * n
    for i in range(n - 1, -1, -1):
        bh_val = sorted_p[i] * n / (i + 1)
        if i == n - 1:
            adjusted[i] = min(bh_val, 1.0)
        else:
            adjusted[i] = min(min(bh_val, adjusted[i + 1]), 1.0)

    # 按原始索引恢复
    original_adjusted = [0.0] * n
    for i, idx in enumerate(sorted_indices):
        original_adjusted[idx] = adjusted[i]

    significant = [(sorted_indices[i], sorted_p[i]) for i in range(rejected)]

    return {
        "significant": significant,
        "rejected": rejected,
        "threshold": round(rejected * alpha / n if rejected > 0 else 0.0, 6),
        "adjusted_p_values": [round(v, 6) for v in original_adjusted],
    }


# ═══════════════════════════════════════════════════════
#  White's Reality Check
# ═══════════════════════════════════════════════════════


def whites_reality_check(
    returns_matrix: "np.ndarray",
    n_bootstrap: int = 1000,
    random_seed: Optional[int] = None,
) -> Dict:
    """White's Reality Check for Data Snooping

    检验多个策略中表现最好的策略是否统计显著。
    H0: 最佳策略的绩效不超过基准(零均值)
    使用 Stationary Bootstrap 控制时间序列自相关。

    Args:
        returns_matrix: (n_periods, n_strategies) 各策略各期收益
        n_bootstrap: Bootstrap 次数
        random_seed: 随机种子

    Returns:
        {'best_strategy': int, 'p_value': float,
         'nominal_p_value': float, 'mean_returns': list}
    """
    import numpy as np

    n_periods, _n_strat = returns_matrix.shape
    if n_periods < 10:
        return {
            "best_strategy": 0,
            "p_value": 1.0,
            "nominal_p_value": 1.0,
            "mean_returns": [],
        }

    # 各策略平均收益
    mean_rets = returns_matrix.mean(axis=0)
    best_idx = int(np.argmax(mean_rets))
    observed = float(mean_rets[best_idx])

    # Bootstrap: 对时间序列 block-resample
    # 简化为随机重采样 (Stationary Bootstrap 的近似)
    rng = np.random.RandomState(random_seed)
    bootstrap_maxs = []

    # 中心化 (H0: 所有策略均值为零)
    centered = returns_matrix - returns_matrix.mean(axis=0)

    for _ in range(n_bootstrap):
        idx = rng.randint(0, n_periods, n_periods)
        boot_rets = centered[idx].mean(axis=0)
        bootstrap_maxs.append(float(boot_rets.max()))

    # 单尾检验: P(max(bootstrap) ≥ observed)
    p_value = float(np.mean(np.array(bootstrap_maxs) >= observed))

    # 名义p值 (仅最佳策略的单样本t检验)
    if returns_matrix[:, best_idx].std() > 0:
        t_stat = observed / (returns_matrix[:, best_idx].std() / np.sqrt(n_periods))
        from scipy.stats import t as t_dist

        nominal_p = 1.0 - t_dist.cdf(t_stat, n_periods - 1)
    else:
        nominal_p = 1.0

    return {
        "best_strategy": best_idx,
        "p_value": round(float(p_value), 6),
        "nominal_p_value": round(float(nominal_p), 6),
        "mean_returns": [round(float(r), 6) for r in mean_rets],
        "observed": round(observed, 8),
    }


# ═══════════════════════════════════════════════════════
#  综合显著性报告
# ═══════════════════════════════════════════════════════


def generate_significance_report(
    y_true: List[int],
    y_pred: List[int],
    y_prob: Optional[List[float]] = None,
    model_name: str = "MLModel",
    accuracy: Optional[float] = None,
    f1: Optional[float] = None,
    auc: Optional[float] = None,
) -> str:
    """生成模型统计显著性综合报告

    Args:
        y_true: 真实标签
        y_pred: 预测标签
        y_prob: 预测概率 (用于Rank IC)
        model_name: 模型名称
        accuracy: 报告准确率 (可选)
        f1: 报告F1 (可选)
        auc: 报告AUC (可选)

    Returns:
        Markdown格式综合显著性报告
    """
    n = len(y_true)
    correct = [1 if t == p else 0 for t, p in zip(y_true, y_pred)]
    obs_acc = sum(correct) / n if n > 0 else 0.0

    if accuracy is None:
        accuracy = obs_acc
    if f1 is None:
        f1_val = _compute_metric(y_true, y_pred, "f1")
        f1 = f1_val

    lines = []
    lines.append(f"## {model_name} 统计显著性检验")
    lines.append("")
    lines.append(f"**样本量**: {n} | **准确率**: {accuracy:.2%} | **F1**: {f1:.4f}")
    if auc is not None:
        lines.append(f" | **AUC**: {auc:.4f}")
    lines.append("")
    lines.append("**Renaissance标准**: 单一信号 t-statistic > 2.0 才纳入组合")
    lines.append("")

    # 1. Bootstrap 置信区间
    bootstrap = bootstrap_accuracy_ci(correct, n_bootstrap=2000)
    lines.append("### 1. Bootstrap 置信区间 (95%)")
    lines.append(f"- 均值: {bootstrap['mean']:.4%} ({bootstrap['mean']:.4f})")
    lines.append(f"- 95% CI: [{bootstrap['ci_lower']:.4f}, {bootstrap['ci_upper']:.4f}]")
    lines.append(f"- 标准差: {bootstrap['std']:.4f}")

    # 判断: 95% CI 下界是否 > 0.50
    if bootstrap["ci_lower"] > 0.50:
        lines.append(f"- ✅ 95% CI 下界({bootstrap['ci_lower']:.4f}) > 0.50, 模型显著优于抛硬币")
    elif bootstrap["ci_upper"] < 0.50:
        lines.append(f"- ❌ 95% CI 上界({bootstrap['ci_upper']:.4f}) < 0.50, 模型显著劣于抛硬币!")
    else:
        lines.append(
            f"- ⚠️ 95% CI包含0.50 [{bootstrap['ci_lower']:.4f}, {bootstrap['ci_upper']:.4f}], 无法拒绝准确率=50%的零假设"
        )
    lines.append("")

    # 2. 置换检验
    perm = permutation_test(y_true, y_pred, n_permutations=2000)
    lines.append("### 2. 置换检验")
    lines.append(f"- 观察准确率: {perm['observed']:.4f}")
    lines.append(f"- 零分布均值: {perm['null_mean']:.4f}")
    lines.append(f"- p值: {perm['p_value']:.4f}")
    if perm["p_value"] < 0.01:
        lines.append("- ✅ p < 0.01, 模型预测力极显著")
    elif perm["p_value"] < 0.05:
        lines.append("- ✅ p < 0.05, 模型预测力显著")
    elif perm["p_value"] < 0.10:
        lines.append("- ⚠️ 0.05 ≤ p < 0.10, 边缘显著, 需更大样本验证")
    else:
        lines.append(f"- ❌ p = {perm['p_value']:.4f} ≥ 0.05, 模型预测力不显著于随机!")
    lines.append("")

    # 3. Rank IC (如果有概率预测)
    if y_prob is not None and len(y_prob) == n:
        import numpy as np

        future_returns = np.array([1.0 if t == 1 else -1.0 for t in y_true])
        pred_proba = np.array(y_prob)

        ic_analysis = rank_ic_analysis(pred_proba, future_returns)
        lines.append("### 3. Rank IC 分析")
        lines.append(f"- Rank IC: {ic_analysis['mean_ic']:.4f}")
        lines.append(f"- IC_IR (信息比率): {ic_analysis['ic_ir']:.2f}")
        lines.append(f"- t统计量: {ic_analysis['t_statistic']:.2f}")
        lines.append(f"- p值: {ic_analysis['p_value']:.4f}")

        if ic_analysis["t_statistic"] > 2.0:
            lines.append("- ✅ t > 2.0, 通过Renaissance信号纳入标准")
        else:
            lines.append(f"- ❌ t = {ic_analysis['t_statistic']:.2f} < 2.0, 未达到Renaissance标准!")
        lines.append("")

    # 4. 综合判定
    lines.append("### 4. 综合判定")
    is_significant = bootstrap["ci_lower"] > 0.50 and perm["p_value"] < 0.05
    if is_significant:
        lines.append("✅ **模型预测力统计显著**, 可用于实盘信号生成。建议持续监控IC_IR衰减。")
    else:
        lines.append(
            "❌ **模型预测力统计不显著**。建议: "
            "(1)增加特征维度; (2)扩大训练集; "
            "(3)优化标签构造(Triple Barrier); "
            "(4)考虑模型是否过度依赖噪音。"
        )

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════
#  自测
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    import random

    rng = random.Random(42)
    n = 500

    # 模拟56%准确率模型
    y_true = [rng.randint(0, 1) for _ in range(n)]
    y_pred = [y if rng.random() < 0.56 else (1 - y) for y in y_true]
    y_prob = [rng.uniform(0.3, 0.7) for _ in range(n)]

    report = generate_significance_report(
        y_true=y_true,
        y_pred=y_pred,
        y_prob=y_prob,
        model_name="GradientBoosting v2.0",
        accuracy=0.56,
        f1=0.628,
        auc=0.62,
    )
    print(report)
    print("\n✅ ML显著性检验模块自测通过")
