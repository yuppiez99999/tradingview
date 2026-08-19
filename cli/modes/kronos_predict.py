"""
Kronos 金融 K 线预测模式 — v5.10 集成
"""

import json
import os

from core.context import BASE_DIR


def run_kronos_predict_mode(args):
    """Kronos 金融 K 线预测模式"""
    # 延迟导入，避免在无 Kronos 环境启动时报错
    try:
        from utils.kronos_predictor import (
            KronosPredictor,
            fetch_a_stock_data,
            run_batch_prediction,
        )
    except ImportError as e:
        print("\n❌ Kronos 预测模块不可用")
        print(f"   原因: {e}")
        print("   请确认 utils/kronos_predictor.py 与依赖可正常导入")
        return None

    pred_len = getattr(args, 'kronos_pred_len', 24)
    device = getattr(args, 'kronos_device', 'cpu')
    code = getattr(args, 'kronos_code', None)
    name = getattr(args, 'kronos_name', None)
    batch = getattr(args, 'kronos_batch', None)
    output = getattr(args, 'kronos_output', None)

    print("\n" + "=" * 70)
    print("  🔮 Kronos 金融 K 线预测")
    print("=" * 70)
    print(f"  预测窗口: T+{pred_len} | 设备: {device}")

    # 初始化预测器
    try:
        predictor = KronosPredictor(device=device, verbose=True)
    except Exception as e:
        print(f"\n❌ Kronos 预测器初始化失败: {e}")
        return None

    # 单股预测
    if code:
        if not name:
            name = code

        print(f"\n📈 单股预测: {name} ({code})")
        try:
            df = fetch_a_stock_data(code, days=120, verbose=True)
        except Exception as e:
            print(f"\n❌ 获取 {code} 数据失败: {e}")
            return None

        try:
            result = predictor.analyze_stock(code, name, df, pred_len=pred_len)
        except Exception as e:
            print(f"\n❌ 预测失败: {e}")
            return None

        results = [result]

    # 批量预测
    elif batch:
        if isinstance(batch, str):
            try:
                batch = json.loads(batch)
            except json.JSONDecoder:
                print("\n❌ --kronos-batch 不是合法 JSON")
                return None

        if not isinstance(batch, list) or not batch:
            print("\n❌ --kronos-batch 需为非空列表")
            return None

        print(f"\n📊 批量预测: {len(batch)} 只标的")
        try:
            results = run_batch_prediction(
                batch,
                pred_len=pred_len,
                device=device,
                verbose=True,
            )
        except Exception as e:
            print(f"\n❌ 批量预测失败: {e}")
            return None
    else:
        print("\n❌ 请提供 --kronos-code 或 --kronos-batch")
        return None

    # 汇总输出
    buy_signals = [r for r in results if r.get('signal') == 'buy']
    sell_signals = [r for r in results if r.get('signal') == 'sell']
    hold_signals = [r for r in results if r.get('signal') == 'hold']

    print("\n" + "=" * 70)
    print("📊 预测汇总")
    print("=" * 70)
    print(f"  🟢 买入: {len(buy_signals)} | 🔴 卖出: {len(sell_signals)} | 🟡 持有: {len(hold_signals)}")

    for label, data in [("买入", buy_signals), ("卖出", sell_signals)]:
        if data:
            emoji = '🟢' if label == '买入' else '🔴'
            print(f"\n{emoji} {label}信号:")
            sort_rev = label == '买入'
            for r in sorted(data, key=lambda x: x.get('return_pct', 0), reverse=sort_rev):
                print(f"  {r.get('code')} {r.get('name', ''):<8} "
                      f"收益={r.get('return_pct', 0)*100:+.2f}% "
                      f"信号={r.get('signal', 'hold').upper()}")

    if hold_signals:
        print("\n🟡 持有信号:")
        for r in sorted(hold_signals, key=lambda x: x.get('return_pct', 0), reverse=True)[:5]:
            print(f"  {r.get('code')} {r.get('name', ''):<8} "
                  f"收益={r.get('return_pct', 0)*100:+.2f}%")

    # 可选保存 JSON
    if output:
        output_path = os.path.join(BASE_DIR, 'reports', output)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 结果已保存: {output_path}")

    print("=" * 70)
    return results
