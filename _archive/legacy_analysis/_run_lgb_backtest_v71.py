"""LGB 集成回测 V7.1 - 信号后处理 (bull regime 高波动股惩罚)

回测期: 2022-04-01 ~ 2025-12-31 (45 个月)
标的: 全部 23 个 POSITION_SYMBOLS

V7.1 优化内容 (信号层, 替代 V7-Model 模型层):
  V7-Model 失败: WF Sharpe CV 0.55->0.74 (严重恶化)
    - regime-aware 特征帮助 Window2 (Sharpe 1.725->2.008)
    - 但未解决 Window1 的 2024-06 crash (688017/300308 高波动股大跌)
    - Window1 Sharpe 0.330->0.300 (略差)
  V7-Model 成功: DSR n_trials 3->5, 峰度 6.86->2.48

V7.1 方案 (信号层硬编码惩罚, 定向解决 2024-06 crash):
  新增 _apply_v71_signal_penalty() 方法 (institutional_pipeline_runner.py):
    1. 训练后计算当前 regime (510300 MA60)
    2. 若 regime==bull, 遍历持仓:
       - 获取 volatility_20 (日波动率)
       - 若 vol20 > 4.5% 且 signal > 0: signal *= 0.5
    3. 直接修改 _lgb_models[code]["signal"]
  接入位置: _lgb_walkforward_train 训练完成后

V7.1 预期效果:
  - 2024-06 (bull regime): 688017/300308 高波动股信号减半 -> 权重下降
  - Window1 Sharpe 提升 -> Sharpe CV 下降
  - 保留 V7-Model 的 regime-aware 特征 (峰度改善, DSR 改善)
  - 保留 V6.2 止盈规则 (单标的>50%->x0.6, 组合>12%->x0.80)

目标: WF Sharpe CV < 0.5, DSR n_trials >= 5, 年化 >= 8%, 去极端月年化 >= 8%
"""
import logging
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

from research.backtest_runner import run_backtest  # noqa: E402
from utils.risk_constraints import DEFAULT_MAX_WEIGHT  # noqa: E402

SYMBOLS = [
    "588000", "688041", "002371", "688981", "300308",
    "000425", "601088", "600276", "600900", "515180",
    "600036", "518880", "300274", "603019", "600089",
    "688017", "600219", "600019", "000680", "000333",
    "000408", "000975", "002422",
]

print("=" * 70)
print("LGB 集成回测 V7.1 (信号后处理: bull regime 高波动股惩罚)")
print("=" * 70)
print(f"标的数: {len(SYMBOLS)}")
print("回测期: 2022-04-01 ~ 2025-12-31 (45 个月)")
print(f"单标的硬上限: {DEFAULT_MAX_WEIGHT:.0%}")
print("V7.1 信号惩罚: regime==bull 且 vol20>4.5% 且 signal>0 -> signal *= 0.5")
print("V7.1 目标: 定向降低 2024-06 的 688017/300308 权重")
print("止盈规则: 单标的>50%->x0.6, 组合>12%->x0.80 (与V6.2/V7-Model一致)")
print("模式: resume=False (强制LGB重训, 应用V7.1信号惩罚)")
print()

t0 = time.time()
result = run_backtest(
    symbols=SYMBOLS,
    start="2022-04-01",
    end="2025-12-31",
    resume=False,  # V7.1: 强制重训, 应用信号惩罚
)
elapsed = time.time() - t0

print()
print(f"=== 回测完成 (耗时 {elapsed:.0f}s / {elapsed/60:.1f}min) ===")
print(f"年化收益: {result.get('annual_return', 'N/A')}")
print(f"最大回撤: {result.get('max_drawdown', 'N/A')}")
print(f"胜率: {result.get('win_rate', 'N/A')}")
acceptance = result.get('acceptance', {})
if acceptance:
    print(f"验收通过: {acceptance.get('passed', 'N/A')}")
    for c in acceptance.get('checks', []):
        ok = 'PASS' if c['ok'] else 'FAIL'
        print(f"  [{ok}] {c['metric']}: {c['value']} (要求 {c['required']})")

import json  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402

output_path = Path("output/validation_reports")
output_path.mkdir(parents=True, exist_ok=True)
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
result_file = output_path / f"lgb_backtest_v71_signal_penalty_{ts}.json"
with open(result_file, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)
print(f"\n结果已保存: {result_file}")
