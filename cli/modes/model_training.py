"""
统一模型训练入口 — v5.10 P0-9 重构
========================================
整合所有训练管线:
  - ML分类器: Optuna贝叶斯优化 + Triple Barrier标签
  - ML集成: model_train/signal_composer.py (三源信号合成)
  - 情感分析: model_train/finbert_sentiment.py (FinBERT)
  - DL时序: 16_金融市场预测模型/patchtst_trainer.py (PatchTST, 需GPU)
"""


def run_model_training(args):
    """统一模型训练入口 (v5.7 Phase 2 增强)

    当前通过 --train-enhanced 使用增强训练引擎。
    未来将在此统一入口整合所有训练管线。
    """
    print("\n🧠 统一模型训练模式")
    print("=" * 70)

    # 检测增强训练引擎是否可用
    try:
        from core.context import ML_ENHANCED_TRAINER_AVAILABLE

        if ML_ENHANCED_TRAINER_AVAILABLE:
            print("  💡 建议使用增强训练引擎:")
            print("    python v5.10.py --train-enhanced             # T+1 基础训练")
            print("    python v5.10.py --train-enhanced --horizon 5  # T+5 中期预测")
            print(
                "    python v5.10.py --train-enhanced --horizon 10 --optuna  # T+10+贝叶斯优化"
            )
            print(
                "    python v5.10.py --train-enhanced --optuna --trials 100  # 100次Optuna试验"
            )
        else:
            print("  ❌ 增强训练引擎未安装")
            print("     pip install scikit-learn lightgbm optuna")
    except ImportError:
        print("  ❌ 增强训练引擎不可用")
        print("     pip install scikit-learn lightgbm optuna")

    print("\n📋 经典时序预测训练 (Transformer):")
    print("  python v5.10.py --train-model  # (功能开发中)")

    print("\n" + "=" * 70)
