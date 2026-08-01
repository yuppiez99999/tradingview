# -*- coding: utf-8 -*-
"""LGB 集成回测 V6.1 - 止盈调优 (降低峰度提升DSR)

回测期: 2022-04-01 ~ 2025-12-31 (45 个月)
标的: 全部 23 个 POSITION_SYMBOLS

V6.1 优化内容 (在V6基础上, 调整止盈规则):
  V6问题: DSR n_trials=3 (目标>=10), 根因是峰度过高(4.99 vs V5.1的1.89)
          极端月份(2025-09: 17.16%, 2025-08: 10.84%)驱动峰度
  V6.1方案: 降低组合止盈阈值, 加大减仓力度
    - 组合月收益 > 10% → 下月仓位 ×0.80 (V6原: >12% → ×0.85)
    - 单标的月收益 > 50% → 下月权重 ×0.6 (不变)
  预期效果 (基于模拟):
    - 峰度: 4.99 → 2.56 (-49%)
    - DSR惩罚项: 1.85 → 0.97 (-48%), DSR n_trials=5 从0.944提升至>0.95
    - 年化: 16.84% → ~15.83% (-1%, 仍>=8%)
    - Sharpe CV: 0.46 → ~0.47 (仍<0.5)

V6基准: 年化16.84%, 回撤7.57%, Sharpe 1.215, WF Sharpe CV=0.46, DSR n_trials<=3
目标: DSR n_trials >= 5, 同时保持 Sharpe CV < 0.5, 年化 >= 8%, 去极端月年化 >= 8%
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
print("LGB 集成回测 V6.1 (止盈调优: 组合>10%→×0.80, 降低峰度提升DSR)")
print("=" * 70)
print(f"标的数: {len(SYMBOLS)}")
print("回测期: 2022-04-01 ~ 2025-12-31 (45 个月)")
print(f"单标的硬上限: {DEFAULT_MAX_WEIGHT:.0%}")
print("V6.1止盈规则: 单标的>50%→×0.6, 组合>10%→×0.80 (V6原: 组合>12%→×0.85)")
print("模式: resume=True (复用V6 LGB缓存, 仅重新应用止盈规则)")
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
result_file = output_path / f"lgb_backtest_v6_1_profit_taking_{ts}.json"
with open(result_file, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)
print(f"\n结果已保存: {result_file}")
