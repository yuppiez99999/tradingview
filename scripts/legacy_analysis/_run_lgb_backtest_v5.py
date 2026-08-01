# -*- coding: utf-8 -*-
"""LGB 集成回测 V5 - 防御性权重调整

回测期: 2022-04-01 ~ 2025-12-31 (45 个月)
标的: 全部 23 个 POSITION_SYMBOLS

V5 优化内容 (在V4.1基础上):
  防御性权重调整 (bear/rebound regime下):
    - 根据标的20日波动率调整权重
    - 高波动(>1.5倍中位数)→×0.5 (降低688041/588000/300308等高波动标的权重)
    - 低波动(<0.7倍中位数)→×1.2 (增加600900/601088等防御性标的权重)
    - 归一化保持总仓位不变

动机: 窗口1(2023-07~2024-09)年化-2.11%, bear regime下LGB信号失效
      高波动标的胜率仅28.6%, 防御性标的胜率64-78%但权重不足

V4.1基准: 年化15.71%, 回撤9.27%, Sharpe 1.191, WF Sharpe CV=0.66, DSR n_trials<=8
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
print("LGB 集成回测 V5 (防御性权重调整)")
print("=" * 70)
print(f"标的数: {len(SYMBOLS)}")
print("回测期: 2022-04-01 ~ 2025-12-31 (45 个月)")
print(f"单标的硬上限: {DEFAULT_MAX_WEIGHT:.0%}")
print("止盈规则: 单标的>50%→×0.6, 组合>12%→×0.85")
print("防御性调整: bear/rebound regime下, 高波动>1.5x中位数→×0.5, 低波动<0.7x→×1.2")
print("模式: resume=True (复用V2缓存, 10%截断+归一化+止盈+防御性调整)")
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
result_file = output_path / f"lgb_backtest_v5_defensive_{ts}.json"
with open(result_file, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)
print(f"\n结果已保存: {result_file}")
