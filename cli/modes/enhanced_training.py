"""
ML增强训练模式 — v5.10 P0-9 重构
"""

import os

from core.context import (
    BASE_DIR,
    ML_ENHANCED_TRAINER_AVAILABLE,
    ProgressIndicator,
    run_enhanced_training,
)


def run_enhanced_training_mode(args):
    """ML增强训练 v2.0 — 四维优化管线"""
    if not ML_ENHANCED_TRAINER_AVAILABLE:
        print("\n❌ 增强训练引擎未安装")
        return None

    horizon = getattr(args, "horizon", 1)
    filter_osc = getattr(args, "filter_oscillation", True)
    use_optuna = getattr(args, "optuna", False)
    n_trials = getattr(args, "trials", 50)
    n_features = getattr(args, "features", 30)
    northbound_path = getattr(args, "northbound", None) or None

    print("\n" + "=" * 70)
    print("  🧠 ML 增强训练引擎 v2.0 — 四维优化管线")
    print("=" * 70)
    print(f"  预测窗口: T+{horizon} | 过滤震荡: {filter_osc} | Optuna: {use_optuna}")
    print(f"  特征数: {n_features} | 样本加权: 时间衰减+波动率")
    print("  增强特征: 行业RS+市场宽度+北向+PE/PB/ROE")
    print("-" * 70)

    progress = ProgressIndicator("增强训练", 5)
    progress.update(1, "加载与特征工程...")
    data_dir = os.path.join(BASE_DIR, "data", "cache")
    model_dir = os.path.join(BASE_DIR, "models")

    result = run_enhanced_training(
        data_dir=data_dir,
        model_dir=model_dir,
        prediction_horizon=horizon,
        filter_oscillation=filter_osc,
        use_optuna=use_optuna,
        n_trials=n_trials,
        n_features=n_features,
        northbound_path=northbound_path,
    )

    if "error" in result:
        print(f"\n❌ 训练失败: {result['error']}")
        return None

    progress.update(3, "训练...")
    progress.update(4, "保存...")
    progress.update(5, "完成")
    progress.complete("✅ 增强训练完成")

    print(
        f"\n📊 最佳: {result['best_model']} | F1={result['best_f1']:.4f} "
        f"| AUC={result['best_auc']:.4f} | 样本={result['n_samples']}"
    )
    for name, m in result["results"].items():
        print(f"  {name:<25} F1={m['f1']:.4f}  AUC={m['auc']:.4f}")
    print("\n💡 python v5.9.py --ml-enhanced  # 使用新模型预测")
    print("=" * 70)
    return result
