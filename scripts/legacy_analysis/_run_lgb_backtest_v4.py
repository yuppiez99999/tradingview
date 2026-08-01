# -*- coding: utf-8 -*-
"""LGB 集成回测 V4.1 - 月度止盈机制 (调优版)

回测期: 2022-04-01 ~ 2025-12-31 (45 个月)
标的: 全部 23 个 POSITION_SYMBOLS

V4.1 调整 (V4止盈触发18次过于激进):
  1. 单标的月收益 > 50% → 该标的下月权重 ×0.6 (原30%→50%, ×0.5→×0.6)
  2. 组合月收益 > 12% → 下月整体仓位 ×0.85 (原10%→12%, ×0.8→×0.85)

V4基准: 年化13.91%, 回撤8.76%, Sharpe 1.132, WF Sharpe CV=0.65, DSR n_trials<=8
V3基准: 年化18.25%, 回撤9.59%, Sharpe 1.236, WF Sharpe CV=0.70, DSR n_trials<=5
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
print("LGB 集成回测 V4.1 (月度止盈-调优版: 阈值50%/12%)")
print("=" * 70)
print(f"标的数: {len(SYMBOLS)}")
print("回测期: 2022-04-01 ~ 2025-12-31 (45 个月)")
print(f"单标的硬上限: {DEFAULT_MAX_WEIGHT:.0%}")
print("止盈规则: 单标的>50%→×0.6, 组合>12%→×0.85")
print("模式: resume=True (复用V2缓存, 10%截断+归一化+止盈)")
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
result_file = output_path / f"lgb_backtest_v4_1_tuned_{ts}.json"
with open(result_file, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)
print(f"\n结果已保存: {result_file}")
