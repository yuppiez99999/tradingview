"""
ML模型预测信号模式 — v5.10 P0-9 重构
"""

from core.context import ProgressIndicator
from utils.cli_helpers import get_ml_signal_section, get_stock_name


def run_ml_signal_mode(args):
    """ML模型预测信号模式 - 基于训练好的模型生成涨跌信号"""
    print("\n📈 ML模型预测信号")
    print("=" * 70)

    progress = ProgressIndicator("ML信号扫描", 3)
    progress.update(1, "扫描模型...")

    result_raw = get_ml_signal_section(return_raw=True)
    if not result_raw:
        print("  ⚠️ ML信号不可用 — 请先训练模型: python v5.10.py --train-enhanced")
        progress.complete("❌ 信号不可用")
        return None

    ml_section, result = result_raw
    print(ml_section)

    # 信号摘要
    if "signals" in result:
        sig = result["signals"]
        buy_n = len(sig.get("buy", []))
        sell_n = len(sig.get("sell", []))
        hold_n = len(sig.get("hold", []))
        result.get("model_info", {})
        print(f"\n📊 信号摘要: 买入 {buy_n} | 卖出 {sell_n} | 持有/震荡 {hold_n}")
        if buy_n:
            print("  🟢 买入TOP3:")
            for s in sorted(sig["buy"], key=lambda x: x["probability"], reverse=True)[
                :3
            ]:
                name = get_stock_name(s["code"])
                print(
                    f"    {s['code']} {name:<10} 概率={s['probability']:.2%} 置信={s['confidence']:.2%}"
                )
        if sell_n:
            print("  🔴 卖出TOP3:")
            for s in sorted(sig["sell"], key=lambda x: x["probability"])[:3]:
                name = get_stock_name(s["code"])
                print(
                    f"    {s['code']} {name:<10} 概率={1-s['probability']:.2%} 置信={s['confidence']:.2%}"
                )

    progress.complete("✅ ML信号扫描完成")
    return result
