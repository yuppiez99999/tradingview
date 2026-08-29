"""
ML增强预测模式 — v5.10 P0-9 重构
"""

import glob
import os

import pandas as pd

from cli.modes.ml_signal import run_ml_signal_mode
from core.context import (
    BASE_DIR,
    ML_ENHANCED_PREDICTOR_AVAILABLE,
    EnhancedPredictor,
    ProgressIndicator,
)
from utils.cli_helpers import get_stock_name


def run_enhanced_prediction_mode(args):
    """ML增强预测 v2.0"""
    if not ML_ENHANCED_PREDICTOR_AVAILABLE:
        print("\n❌ 增强预测器不可用")
        return None

    print("\n" + "=" * 70)
    print("  📈 ML 增强预测 v2.0")
    print("=" * 70)

    model_dir = os.path.join(BASE_DIR, "models")
    data_dir = os.path.join(BASE_DIR, "data", "cache")
    threshold = getattr(args, "threshold", 0.55)

    progress = ProgressIndicator("增强预测", 4)
    progress.update(1, "加载模型...")
    predictor = EnhancedPredictor(model_dir=model_dir, weight_method="f1_weighted")

    if not predictor.auto_discover_and_load(prefer_enhanced=True):
        print("  ⚠️ 未找到增强模型，回退标准预测")
        return run_ml_signal_mode(args)

    info = predictor.get_model_info()
    print(
        f"\n  T+{info.get('horizon',1)} | 过滤震荡={info.get('filter_oscillation',True)} "
        f"| 模型数={info.get('model_count',0)} | F1={info.get('f1',0):.4f}"
    )

    progress.update(2, "加载K线...")
    kline_dict = {}
    for f in glob.glob(os.path.join(data_dir, "kline_*.parquet")):
        code = os.path.basename(f).replace("kline_", "").replace("_daily.parquet", "")
        try:
            kline_dict[code] = pd.read_parquet(f)
        except Exception:
            continue

    if not kline_dict:
        print("  ⚠️ 无K线数据")
        return None

    progress.update(3, "预测...")
    signals = predictor.generate_trading_signals(kline_dict, threshold=threshold)
    progress.complete("✅ 增强预测完成")

    print(f"\n📊 信号分布 (T+{predictor.prediction_horizon}):")
    print(
        f"  🟢 买入: {len(signals['buy'])} | 🔴 卖出: {len(signals['sell'])} | 🟡 震荡/持有: {len(signals['hold'])}"
    )

    for label, data in [("买入", signals["buy"]), ("卖出", signals["sell"])]:
        if data:
            emoji = "🟢" if label == "买入" else "🔴"
            print(f"\n{emoji} {label}信号:")
            sort_rev = label == "买入"
            for s in sorted(data, key=lambda x: x["probability"], reverse=sort_rev):
                name = get_stock_name(s["code"])
                print(
                    f"  {s['code']} {name:<8} 概率={s['probability']:.2%} "
                    f"置信={s['confidence']:.2%} 强度={s['strength']}"
                )

    if signals["hold"]:
        print("\n🟡 震荡/持有 (建议观望):")
        for s in sorted(signals["hold"], key=lambda x: x["probability"], reverse=True)[
            :5
        ]:
            name = get_stock_name(s["code"])
            print(f"  {s['code']} {name:<8} 概率={s['probability']:.2%}")

    print(
        f"\n💡 四维优化: 三分类标签 | T+{predictor.prediction_horizon}窗口 | 增强特征(行业+北向+基本面) | 样本加权"
    )
    print("=" * 70)
    return signals
