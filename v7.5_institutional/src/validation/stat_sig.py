# -*- coding: utf-8 -*-
"""
统计显著性验证模块 v5.10 — P0-2修复

功能:
  1. Bootstrap置信区间 - 计算模型准确率的95%置信区间
  2. 置换检验 - 随机打乱标签1000次，验证模型是否显著优于随机
  3. Rank IC / IC_IR - 信息系数和IC_IR稳定性评估
  4. t统计量检验 - 检验模型准确率是否显著不同于50%

用法:
  python "量化策略系统 v5.10.py" --ml-significance
"""

import numpy as np
import pandas as pd
import os
import sys
import json
import glob
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import warnings
warnings.filterwarnings('ignore')

try:
    from sklearn.model_selection import cross_val_score, StratifiedKFold
    from sklearn.metrics import accuracy_score, roc_auc_score
    _SKLEARN_AVAILABLE = True
except Exception:
    _SKLEARN_AVAILABLE = False

# ==================== 常量 ====================
ALPHA = 0.05  # 显著性水平
N_BOOTSTRAP = 100  # Bootstrap重采样次数（优化版：100）
N_PERMUTATIONS = 1000  # 置换检验次数（优化版：1000）
N_IC_PERIODS = 12  # IC计算的分段数（月度）


# ==================== 核心函数 ====================

def load_latest_training_metadata(models_dir: str) -> Tuple[Optional[dict], Optional[str]]:
    """加载最新的训练元数据"""
    pattern = os.path.join(models_dir, 'training_metadata_optimized_*.json')
    files = glob.glob(pattern)
    if not files:
        return None, None

    latest = max(files, key=os.path.getmtime)
    with open(latest, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data, os.path.basename(latest)


def load_data_for_validation(data_dir: str = 'data/cache'):
    """加载用于验证的数据"""
    all_data = []
    for file in os.listdir(data_dir):
        if file.startswith('kline_') and file.endswith('.parquet'):
            filepath = os.path.join(data_dir, file)
            try:
                df = pd.read_parquet(filepath)
                code = file.replace('kline_', '').replace('_daily.parquet', '')
                df['code'] = code
                all_data.append(df)
            except Exception:
                pass

    if not all_data:
        return None, None

    combined = pd.concat(all_data, ignore_index=True)
    return combined, len(all_data)


def bootstrap_confidence_interval(
    X: np.ndarray, y: np.ndarray,
    model_class,
    model_params: dict,
    n_bootstrap: int = N_BOOTSTRAP,
    alpha: float = ALPHA,
    test_size: float = 0.2
) -> Dict:
    """
    Bootstrap法计算准确率的置信区间

    返回:
        mean_accuracy: 平均准确率
        std: 标准误差
        ci_lower: 95%置信区间下界
        ci_upper: 95%置信区间上界
        se: 标准误差
        p_value: 准确率>50%的p值
    """
    if not _SKLEARN_AVAILABLE:
        return {'error': 'sklearn not available'}

    from sklearn.model_selection import train_test_split

    n_samples = len(y)
    bootstrap_accuracies = []

    for i in range(n_bootstrap):
        # 有放回抽样
        indices = np.random.choice(n_samples, size=n_samples, replace=True)
        X_boot = X[indices]
        y_boot = y[indices]

        # 划分训练测试集
        try:
            X_train, X_test, y_train, y_test = train_test_split(
                X_boot, y_boot, test_size=test_size, random_state=i, stratify=y_boot
            )
            if len(np.unique(y_test)) < 2:
                continue

            # 训练模型
            model = model_class(**model_params)
            model.fit(X_train, y_train)
            y_pred = model.predict(X_test)
            acc = accuracy_score(y_test, y_pred)
            bootstrap_accuracies.append(acc)
        except Exception:
            continue

    if not bootstrap_accuracies:
        return {'error': 'Bootstrap failed'}

    bootstrap_accuracies = np.array(bootstrap_accuracies)
    mean_acc = np.mean(bootstrap_accuracies)
    std_acc = np.std(bootstrap_accuracies)
    ci_lower = np.percentile(bootstrap_accuracies, 2.5)
    ci_upper = np.percentile(bootstrap_accuracies, 97.5)

    # p值: 准确率>50%的概率
    p_value = np.mean(bootstrap_accuracies > 0.5)

    return {
        'n_bootstrap': len(bootstrap_accuracies),
        'mean_accuracy': mean_acc,
        'std': std_acc,
        'ci_lower': ci_lower,
        'ci_upper': ci_upper,
        'se': std_acc,
        'p_value': p_value,
        'p_value_adj': min(p_value * 2, 1.0),  # Bonferroni校正
    }


def permutation_test(
    X: np.ndarray, y: np.ndarray,
    model_class,
    model_params: dict,
    n_permutations: int = N_PERMUTATIONS,
    test_size: float = 0.2
) -> Dict:
    """
    置换检验: 随机打乱标签，验证模型是否显著优于随机

    返回:
        observed_accuracy: 真实准确率
        permuted_mean: 随机打乱后的平均准确率
        p_value: 真实准确率 > 随机准确率的概率
        significant: 是否统计显著 (p < 0.05)
    """
    if not _SKLEARN_AVAILABLE:
        return {'error': 'sklearn not available'}

    from sklearn.model_selection import train_test_split

    n_samples = len(y)

    # 划分训练测试集
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=42, stratify=y
    )

    if len(np.unique(y_test)) < 2:
        return {'error': 'Insufficient class diversity'}

    # 真实准确率
    model = model_class(**model_params)
    model.fit(X_train, y_train)
    observed_acc = accuracy_score(y_test, model.predict(X_test))

    # 置换准确率
    permuted_accuracies = []
    y_test_permuted = y_test.copy()

    for i in range(n_permutations):
        np.random.seed(i)
        np.random.shuffle(y_test_permuted)

        try:
            model_perm = model_class(**model_params)
            model_perm.fit(X_train, y_train)
            acc_perm = accuracy_score(y_test_permuted, model_perm.predict(X_test))
            permuted_accuracies.append(acc_perm)
        except Exception:
            continue

    if not permuted_accuracies:
        return {'error': 'Permutation test failed'}

    permuted_accuracies = np.array(permuted_accuracies)
    permuted_mean = np.mean(permuted_accuracies)
    permuted_std = np.std(permuted_accuracies)

    # p值: 随机情况下准确率超过真实准确率的概率
    p_value = np.mean(permuted_accuracies >= observed_acc)

    # t统计量
    t_stat = (observed_acc - permuted_mean) / (permuted_std + 1e-10)

    return {
        'n_permutations': len(permuted_accuracies),
        'observed_accuracy': observed_acc,
        'permuted_mean': permuted_mean,
        'permuted_std': permuted_std,
        'p_value': p_value,
        'p_value_adj': min(p_value * n_permutations, 1.0),  # Bonferroni校正
        't_statistic': t_stat,
        'significant': p_value < 0.05,
        'significant_adj': min(p_value * n_permutations, 1.0) < 0.05,
        'edge_over_random': observed_acc - permuted_mean,
    }


def calculate_rank_ic(
    X: np.ndarray, y: np.ndarray,
    model_class,
    model_params: dict,
    n_periods: int = N_IC_PERIODS
) -> Dict:
    """
    计算Rank IC (Information Coefficient) 和 IC_IR

    将数据分成n_periods个时间段，计算每个时间段的IC
    IC = correlation(predicted_probability, actual_return)
    IC_IR = mean(IC) / std(IC)

    注意: 这里用标签(0/1)替代实际收益
    """
    n_samples = len(y)
    period_size = n_samples // n_periods

    if period_size < 30:
        return {'error': f'Too few samples per period ({period_size})'}

    ic_values = []

    for i in range(n_periods):
        start = i * period_size
        end = start + period_size if i < n_periods - 1 else n_samples

        # 训练集: 除了当前时间段的所有数据
        train_indices = list(range(start)) + list(range(end, n_samples))
        test_indices = list(range(start, end))

        if len(train_indices) < 30 or len(test_indices) < 30:
            continue

        X_train, X_test = X[train_indices], X[test_indices]
        y_train, y_test = y[train_indices], y[test_indices]

        if len(np.unique(y_train)) < 2 or len(np.unique(y_test)) < 2:
            continue

        try:
            model = model_class(**model_params)
            model.fit(X_train, y_train)

            # 预测概率
            if hasattr(model, 'predict_proba'):
                proba = model.predict_proba(X_test)[:, 1]
            else:
                proba = model.predict(X_test).astype(float)

            # Rank IC: 预测概率与真实标签的相关系数
            ic = np.corrcoef(proba, y_test)[0, 1]
            if not np.isnan(ic):
                ic_values.append(ic)
        except Exception:
            continue

    if len(ic_values) < 3:
        return {'error': f'Too few valid IC periods ({len(ic_values)})'}

    ic_values = np.array(ic_values)
    ic_mean = np.mean(ic_values)
    ic_std = np.std(ic_values)
    ic_ir = ic_mean / (ic_std + 1e-10) if ic_std > 0 else 0

    # t统计量
    t_stat = ic_mean / (ic_std / np.sqrt(len(ic_values)) + 1e-10)

    return {
        'n_periods': len(ic_values),
        'ic_mean': ic_mean,
        'ic_std': ic_std,
        'ic_ir': ic_ir,
        'ic_values': ic_values.tolist(),
        't_statistic': t_stat,
        'significant': abs(t_stat) > 2.0,  # |t| > 2.0
        'ic_positive_ratio': np.mean(ic_values > 0),
    }


def validate_all_models(
    X: np.ndarray, y: np.ndarray,
    models_info: Dict,
    output_dir: str = 'reports'
) -> Dict:
    """
    对所有模型进行统计显著性验证

    模型信息格式: {'name': {'params': {...}}}
    """
    if not _SKLEARN_AVAILABLE:
        return {'error': 'sklearn not available'}

    from sklearn.ensemble import GradientBoostingClassifier, ExtraTreesClassifier, RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    import xgboost as xgb
    import lightgbm as lgb

    # 模型类映射
    model_classes = {
        'GradientBoosting': GradientBoostingClassifier,
        'ExtraTrees': ExtraTreesClassifier,
        'RandomForest': RandomForestClassifier,
        'LogisticRegression': LogisticRegression,
        'XGBoost_tuned': xgb.XGBClassifier,
        'LightGBM_tuned': lgb.LGBMClassifier,
    }

    results = {}

    for name, info in models_info.items():
        print(f"\n[{name}] 统计验证中...")

        model_class = model_classes.get(name)
        if model_class is None:
            print(f"  [SKIP] 未知模型类型: {name}")
            continue

        params = info.get('params', {})
        # 设置默认随机种子
        if 'random_state' not in params:
            params['random_state'] = 42
        if 'n_estimators' not in params and name in ['GradientBoosting', 'ExtraTrees', 'RandomForest']:
            params['n_estimators'] = 100

        # XGBoost/LightGBM特殊参数
        if name in ['XGBoost_tuned', 'LightGBM_tuned']:
            if 'n_estimators' not in params:
                params['n_estimators'] = 100
            if 'learning_rate' not in params:
                params['learning_rate'] = 0.05
            if 'max_depth' not in params:
                params['max_depth'] = 5
            if name == 'XGBoost_tuned':
                params['eval_metric'] = 'logloss'
            else:
                params['verbose'] = -1

        result = {
            'name': name,
            'reported_accuracy': info.get('accuracy', 0),
            'reported_auc': info.get('auc', 0),
        }

        # 1. Bootstrap置信区间
        try:
            print(f"  Bootstrap (n={N_BOOTSTRAP})...")
            bootstrap = bootstrap_confidence_interval(X, y, model_class, params)
            result['bootstrap'] = bootstrap
        except Exception as e:
            result['bootstrap'] = {'error': str(e)}

        # 2. 置换检验
        try:
            print(f"  置换检验 (n={N_PERMUTATIONS})...")
            permutation = permutation_test(X, y, model_class, params)
            result['permutation'] = permutation
        except Exception as e:
            result['permutation'] = {'error': str(e)}

        # 3. Rank IC
        try:
            print(f"  Rank IC (n={N_IC_PERIODS}期)...")
            rank_ic = calculate_rank_ic(X, y, model_class, params)
            result['rank_ic'] = rank_ic
        except Exception as e:
            result['rank_ic'] = {'error': str(e)}

        results[name] = result
        print(f"  ✅ 完成")

    return results


def generate_significance_report(
    metadata: dict,
    validation_results: Dict,
    n_samples: int,
    n_features: int
) -> str:
    """生成统计显著性验证报告"""

    lines = []
    lines.append("# ML模型统计显著性验证报告 v5.10")
    lines.append("")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**训练时间戳**: {metadata.get('timestamp', 'N/A')}")
    lines.append(f"**最佳模型**: {metadata.get('best_model', 'N/A')}")
    lines.append(f"**验证样本数**: {n_samples}")
    lines.append(f"**特征数**: {n_features}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 显著性标准说明
    lines.append("## 显著性判断标准")
    lines.append("")
    lines.append("| 指标 | 显著标准 | 说明 |")
    lines.append("|------|----------|------|")
    lines.append("| t-statistic | > 2.0 | Renaissance标准: t > 2.0才纳入组合 |")
    lines.append("| p-value | < 0.05 | 95%置信度下显著 |")
    lines.append("| Bootstrap CI | 不包含0.5 | 置信区间不含随机基准 |")
    lines.append("| IC_IR | > 0.5 | 信息比表示稳定性 |")
    lines.append("| Permutation p | < 0.05 | 显著优于随机 |")
    lines.append("")

    # 各模型结果
    lines.append("## 模型显著性验证结果")
    lines.append("")

    for name, result in validation_results.items():
        lines.append(f"### {name}")
        lines.append("")

        reported_acc = result.get('reported_accuracy', 0)
        lines.append(f"**报告准确率**: {reported_acc:.2%}")
        lines.append("")

        # Bootstrap
        bootstrap = result.get('bootstrap', {})
        if 'error' not in bootstrap:
            lines.append("**Bootstrap 95%置信区间**:")
            lines.append(f"- 平均准确率: {bootstrap['mean_accuracy']:.2%}")
            lines.append(f"- 置信区间: [{bootstrap['ci_lower']:.2%}, {bootstrap['ci_upper']:.2%}]")
            lines.append(f"- 标准误差: {bootstrap['se']:.4f}")
            lines.append(f"- p值(>50%): {bootstrap['p_value']:.4f}")
            ci_above_50 = bootstrap['ci_lower'] > 0.5
            lines.append(f"- **CI不含50%**: {'✅ 是' if ci_above_50 else '❌ 否'}")
        else:
            lines.append(f"**Bootstrap**: ❌ {bootstrap['error']}")
        lines.append("")

        # Permutation
        perm = result.get('permutation', {})
        if 'error' not in perm:
            lines.append("**置换检验 (随机打乱标签1000次)**:")
            lines.append(f"- 观测准确率: {perm['observed_accuracy']:.2%}")
            lines.append(f"- 随机平均准确率: {perm['permuted_mean']:.2%}")
            lines.append(f"- 超出随机: {perm['edge_over_random']:+.2%}")
            lines.append(f"- t统计量: {perm['t_statistic']:.2f}")
            lines.append(f"- p值(原始): {perm['p_value']:.4f}")
            lines.append(f"- p值(Bonferroni校正): {perm['p_value_adj']:.4f}")
            lines.append(f"- **统计显著(p<0.05)**: {'✅ 是' if perm['significant'] else '❌ 否'}")
        else:
            lines.append(f"**置换检验**: ❌ {perm['error']}")
        lines.append("")

        # Rank IC
        ic = result.get('rank_ic', {})
        if 'error' not in ic:
            lines.append("**Rank IC / IC_IR**:")
            lines.append(f"- IC均值: {ic['ic_mean']:.4f}")
            lines.append(f"- IC标准差: {ic['ic_std']:.4f}")
            lines.append(f"- IC_IR: {ic['ic_ir']:.4f}")
            lines.append(f"- t统计量: {ic['t_statistic']:.2f}")
            lines.append(f"- IC>0比例: {ic['ic_positive_ratio']:.1%}")
            lines.append(f"- **|t|>2.0 (Renaissance标准)**: {'✅ 是' if ic['significant'] else '❌ 否'}")
            lines.append(f"- **IC_IR>0.5**: {'✅ 是' if ic['ic_ir'] > 0.5 else '❌ 否'}")
        else:
            lines.append(f"**Rank IC**: ❌ {ic['error']}")
        lines.append("")

        lines.append("---")
        lines.append("")

    # 综合评估
    lines.append("## 综合评估")
    lines.append("")
    lines.append("| 模型 | Bootstrap CI | Permutation显著 | IC t>2.0 | IC_IR>0.5 | 综合判断 |")
    lines.append("|------|--------------|----------------|----------|----------|----------|")

    for name, result in validation_results.items():
        bootstrap = result.get('bootstrap', {})
        perm = result.get('permutation', {})
        ic = result.get('rank_ic', {})

        ci_ok = 'error' not in bootstrap and bootstrap.get('ci_lower', 0) > 0.5
        perm_ok = 'error' not in perm and perm.get('significant', False)
        ic_t_ok = 'error' not in ic and ic.get('significant', False)
        ic_ir_ok = 'error' not in ic and ic.get('ic_ir', 0) > 0.5

        passes = sum([ci_ok, perm_ok, ic_t_ok, ic_ir_ok])
        if passes >= 3:
            verdict = "🟢 显著"
        elif passes >= 2:
            verdict = "🟡 边缘"
        else:
            verdict = "🔴 不显著"

        lines.append(f"| {name} | {'✅' if ci_ok else '❌'} | {'✅' if perm_ok else '❌'} | {'✅' if ic_t_ok else '❌'} | {'✅' if ic_ir_ok else '❌'} | {verdict} |")

    lines.append("")
    lines.append("---")
    lines.append("")

    # 结论
    lines.append("## 结论与建议")
    lines.append("")

    significant_models = []
    for name, result in validation_results.items():
        bootstrap = result.get('bootstrap', {})
        perm = result.get('permutation', {})
        ic = result.get('rank_ic', {})
        
        ci_ok = 'error' not in bootstrap and bootstrap.get('ci_lower', 0) > 0.5
        perm_ok = 'error' not in perm and perm.get('significant', False)
        ic_t_ok = 'error' not in ic and ic.get('significant', False)
        ic_ir_ok = 'error' not in ic and ic.get('ic_ir', 0) > 0.5
        
        passes = sum([ci_ok, perm_ok, ic_t_ok, ic_ir_ok])
        if passes >= 3:
            significant_models.append(name)

    if significant_models:
        lines.append(f"🟢 **{len(significant_models)}个模型通过统计显著性验证**: {', '.join(significant_models)}")
        lines.append("")
        lines.append("这些模型的预测能力在统计上显著优于随机猜测，可以纳入交易组合。")
    else:
        lines.append("🔴 **当前模型均未通过严格的统计显著性检验**")
        lines.append("")
        lines.append("建议:")
        lines.append("1. 延长回测时间窗口，增加样本量")
        lines.append("2. 考虑引入更多基本面/宏观特征")
        lines.append("3. 使用更长预测周期(T+5或T+10)替代T+1")
        lines.append("4. 当前建议仅作为辅助参考，不适合作为主要交易依据")

    lines.append("")
    lines.append("---")
    lines.append("*本报告由量化策略系统 v5.10 自动生成*")

    return '\n'.join(lines)


# ==================== CLI入口 ====================

def run_significance_validation(models_dir: str = 'models', data_dir: str = 'data/cache'):
    """运行统计显著性验证"""
    print("\n📊 ML模型统计显著性验证 v5.10")
    print("=" * 70)
    print(f"Bootstrap重采样: {N_BOOTSTRAP}")
    print(f"置换检验次数: {N_PERMUTATIONS}")
    print(f"IC计算期数: {N_IC_PERIODS}")
    print()

    # 1. 加载训练元数据
    print("[1/4] 加载训练元数据...")
    metadata, meta_file = load_latest_training_metadata(models_dir)
    if metadata is None:
        print("❌ 未找到训练元数据文件")
        return None
    print(f"  ✅ {meta_file}")
    print(f"  最佳模型: {metadata.get('best_model', 'N/A')}")

    # 2. 加载数据
    print("\n[2/4] 加载原始数据...")
    df, n_files = load_data_for_validation(data_dir)
    if df is None:
        print("❌ 无法加载数据")
        return None
    print(f"  ✅ {n_files}个文件, {len(df)}条记录")

    # 3. 快速特征工程（与训练时一致）
    print("\n[3/4] 构建特征...")
    X, y = prepare_features_fast(df)
    if X is None:
        print("❌ 特征工程失败")
        return None
    print(f"  ✅ 样本数: {len(y)}, 特征数: {X.shape[1]}")

    # 4. 验证所有模型
    print("\n[4/4] 统计显著性验证...")
    models_info = metadata.get('results', {})
    results = validate_all_models(X, y, models_info)

    # 5. 生成报告
    print("\n[报告] 生成中...")
    n_features = metadata.get('feature_count', 0)
    report = generate_significance_report(metadata, results, len(y), n_features)

    # 保存报告
    output_dir = os.path.join(os.path.dirname(models_dir) if models_dir else '.', 'reports')
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_file = os.path.join(output_dir, f'ml_significance_{timestamp}.md')

    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report)

    print(f"\n✅ 报告已保存: {report_file}")
    print("=" * 70)

    return results, report_file


def prepare_features_fast(df: pd.DataFrame, min_periods: int = 60) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """快速特征工程（与auto_train_optimized.py一致）"""
    try:
        features = []
        labels = []

        for code, group in df.groupby('code'):
            if len(group) < min_periods:
                continue

            if isinstance(group.index, pd.DatetimeIndex):
                group = group.reset_index()
            else:
                group = group.reset_index(drop=True)

            if 'close' not in group.columns:
                continue

            group['close'] = pd.to_numeric(group['close'], errors='coerce')
            group = group.dropna(subset=['close'])

            if len(group) < min_periods:
                continue

            # 基础特征
            group['returns'] = group['close'].pct_change()
            group['ma5'] = group['close'].rolling(5).mean()
            group['ma20'] = group['close'].rolling(20).mean()
            group['ma5_ma20'] = group['ma5'] / group['ma20']
            group['volatility_5'] = group['returns'].rolling(5).std()
            group['volatility_20'] = group['returns'].rolling(20).std()
            group['rsi'] = 50  # 简化RSI

            # 标签
            group['label'] = (group['returns'].shift(-1) > 0).astype(int)

            # 特征列
            feature_cols = ['ma5_ma20', 'volatility_5', 'volatility_20']
            available_cols = [c for c in feature_cols if c in group.columns]

            if len(available_cols) < 2:
                continue

            group_clean = group.dropna(subset=['label'] + available_cols)
            if len(group_clean) < 30:
                continue

            X_group = group_clean[available_cols].values
            y_group = group_clean['label'].values

            features.append(X_group)
            labels.append(y_group)

        if not features:
            return None, None

        X = np.vstack(features)
        y = np.concatenate(labels)

        # 标准化
        X_mean = np.nanmean(X, axis=0)
        X_std = np.nanstd(X, axis=0) + 1e-10
        X = (X - X_mean) / X_std
        X = np.nan_to_num(X, nan=0, posinf=0, neginf=0)

        return X, y

    except Exception as e:
        print(f"  特征工程错误: {e}")
        return None, None


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='ML模型统计显著性验证')
    parser.add_argument('--models-dir', default='models', help='模型目录')
    parser.add_argument('--data-dir', default='data/cache', help='数据目录')
    args = parser.parse_args()

    run_significance_validation(args.models_dir, args.data_dir)
