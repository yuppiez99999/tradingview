"""LGB 集成回测 V6 - Alpha信号质量提升 (均值回归特征 + 5日前向收益标签)

回测期: 2022-04-01 ~ 2025-12-31 (45 个月)
标的: 全部 23 个 POSITION_SYMBOLS

V6 优化内容 (在V5.1基础上, 真正提升Alpha信号质量):
  1. 标签改进: 次日收益率 → 5日前向收益
     - 动机: 次日收益率在震荡市噪声过大, IC极低
     - 5日horizon与月度调仓周期更匹配, 均值回归效应更显著
  2. 新增9个均值回归特征:
     - rsi_oversold/rsi_overbought: RSI12极端值信号
     - price_zscore_20: 价格偏离20日均值的Z-score
     - reversal_5d/reversal_10d: 短期反转因子
     - boll_oversold/boll_overbought: 布林带极端位置
     - volume_surge: 成交量异常放大
     - vol_compression: 波动率压缩
  3. 保留V5.1动量反转仓位调整 (bear/rebound regime下, 超跌加仓, 超涨减仓)

动机: Window 1 (2023-07~2024-09) 年化仅2.47%, Sharpe 0.28,
      是WF Sharpe CV=0.67无法达到<0.5目标的根本原因。
      V5.1只是仓位调整, 未真正提升Alpha信号质量。
      V6从模型层面改进: 更好的标签 + 更全面的特征。

V5.1基准: 年化15.57%, 回撤9.60%, Sharpe 1.176, WF Sharpe CV=0.67, DSR n_trials<=8
目标: WF Sharpe CV < 0.5, 去极端月年化 >= 8%, DSR n_trials >= 10
"""
import logging
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

from research.backtest_runner import run_backtest
from utils.risk_constraints import DEFAULT_MAX_WEIGHT

SYMBOLS = [
    "588000", "688041", "002371", "688981", "300308",
    "000425", "601088", "600276", "600900", "515180",
    "600036", "518880", "300274", "603019", "600089",
    "688017", "600219", "600019", "000680", "000333",
    "000408", "000975", "002422",
]

print("=" * 70)
print("LGB 集成回测 V6 (Alpha信号质量提升: 均值回归特征 + 5日前向收益标签)")
print("=" * 70)
print(f"标的数: {len(SYMBOLS)}")
print("回测期: 2022-04-01 ~ 2025-12-31 (45 个月)")
print(f"单标的硬上限: {DEFAULT_MAX_WEIGHT:.0%}")
print("V6核心改进:")
print("  1. 标签: 次日收益率 → 5日前向收益 (与月度调仓匹配)")
print("  2. 新增9个均值回归特征 (RSI极端值/Z-score/反转因子/布林带/量价)")
print("  3. 保留V5.1动量反转仓位调整")
print("模式: resume=True (复用缓存, V6特征+标签改进)")
print()

t0 = time.time()
result = run_backtest(
    symbols=SYMBOLS,
    start="2022-04-01",
    end="2025-12-31",
    resume=True,
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

import json
from datetime import datetime
from pathlib import Path

output_path = Path("output/validation_reports")
output_path.mkdir(parents=True, exist_ok=True)
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
result_file = output_path / f"lgb_backtest_v6_alpha_quality_{ts}.json"
with open(result_file, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)
print(f"\n结果已保存: {result_file}")
