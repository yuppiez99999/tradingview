# -*- coding: utf-8 -*-
"""LGB 集成回测 V2 - 三层过滤+回撤熔断+板块约束修复

回测期: 2022-04-01 ~ 2025-12-31 (45 个月)
标的: 全部 23 个 POSITION_SYMBOLS

V2 优化内容:
  1. 三层市场状态过滤 (MA60 + 20日波动率 + 20日动量)
     - 解决 2022-07/2024-12 MA60滞后导致熊市满仓问题
  2. 路径依赖回撤熔断器
     - dd>5% → 下月减仓50%, dd>10% → 下月减仓70%
     - 捕捉崩盘后的连锁亏损
  3. 板块集中度硬约束修复
     - 补全 sector_map 缺失的 11 个标的 (688981/000425/600089等)
     - 二次 enforce_hard_constraints 校验缓存权重
     - 科技板块 ≤25% (项目硬约束)

模式: resume=False (代码变更后需完全重新运行)
"""
import logging
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

from research.backtest_runner import run_backtest

SYMBOLS = [
    "588000", "688041", "002371", "688981", "300308",
    "000425", "601088", "600276", "600900", "515180",
    "600036", "518880", "300274", "603019", "600089",
    "688017", "600219", "600019", "000680", "000333",
    "000408", "000975", "002422",
]

print("=" * 70)
print("LGB 集成回测 V2 (三层过滤+回撤熔断+板块约束修复)")
print("=" * 70)
print(f"标的数: {len(SYMBOLS)}")
print(f"回测期: 2022-04-01 ~ 2025-12-31 (45 个月)")
print(f"模式: resume=False (V2 代码变更后完全重新运行)")
print()

t0 = time.time()
result = run_backtest(
    symbols=SYMBOLS,
    start="2022-04-01",
    end="2025-12-31",
    resume=False,
)
elapsed = time.time() - t0

print()
print(f"=== 回测完成 (耗时 {elapsed:.0f}s / {elapsed/60:.1f}min) ===")
print(f"状态: {result.get('status', 'N/A')}")
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
from pathlib import Path
from datetime import datetime

output_path = Path("output/validation_reports")
output_path.mkdir(parents=True, exist_ok=True)
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
result_file = output_path / f"lgb_backtest_v2_optimized_{ts}.json"
with open(result_file, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)
print(f"\n结果已保存: {result_file}")
