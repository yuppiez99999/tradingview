"""
实时监控模式 — v5.10 P0-9 重构
"""

from core.context import (
    ProgressIndicator,
    auto_trading,
)
from utils.cli_helpers import get_ml_signal_section, get_stock_name


def run_live_monitoring(args):
    """实时监控模式 - 盘中实时行情监控 + 自动再平衡 + ML信号"""
    print("\n🚀 启动实时监控模式")
    print("=" * 70)

    progress = ProgressIndicator("初始化系统", 6)

    progress.update(1, "扫描ML预测信号...")
    ml_result = get_ml_signal_section(return_raw=True)
    if ml_result:
        ml_section, result = ml_result
        if 'signals' in result:
            sig = result['signals']
            buy_n = len(sig.get('buy', []))
            sell_n = len(sig.get('sell', []))
            hold_n = len(sig.get('hold', []))
            model_name = result.get('model_info', {}).get('best_model', '?')
            model_acc = result.get('model_info', {}).get('accuracy', 0)
            print(f"\n  [ML信号] {model_name} (Acc={model_acc:.1%}) "
                  f"买入:{buy_n} 卖出:{sell_n} 持有:{hold_n}")
            for s in sig.get('buy', [])[:5]:
                name = get_stock_name(s['code'])
                print(f"    买入 {s['code']} {name:8s} 概率:{s['probability']:.1%}")
    else:
        print("  ⚠️ ML信号不可用")

    progress.update(2, "加载交易系统...")
    AutoTradingSystem = auto_trading.get('AutoTradingSystem')

    if AutoTradingSystem:
        progress.update(3, "创建交易实例...")
        system = AutoTradingSystem()

        progress.update(4, "连接数据源...")

        progress.update(5, "启动监控循环...")
        system.run()
        progress.complete("监控结束")
    else:
        progress.complete("❌ 自动交易系统模块不可用")
