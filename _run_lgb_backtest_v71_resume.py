# -*- coding: utf-8 -*-
"""LGB 集成回测 V7.1 - 从 2024-03 继续 (resume=True)

V7.1 信号惩罚已验证生效:
  - 2023-02-01 (bull): 688017 signal 0.9834 -> 0.4917
  - 2023-08-01 (bull): 600089 signal 1.0000 -> 0.5000

崩溃原因: LightGBM access violation (600276), 与 V7.1 补丁无关
恢复策略: resume=True 从 2024-03 继续 (前 23 个月已完成)
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
print("LGB 集成回测 V7.1 (resume=True 从 2024-03 继续)")
print("=" * 70)
print(f"标的数: {len(SYMBOLS)}")
print(f"回测期: 2022-04-01 ~ 2025-12-31 (45 个月)")
print(f"模式: resume=True (前 23 个月已完成, 从 2024-03 继续)")

t0 = time.time()
result = run_backtest(
    symbols=SYMBOLS,
    start="2022-04-01",
    end="2025-12-31",
    resume=True,  # V7.1: 从 2024-03 继续
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
from pathlib import Path
from datetime import datetime

output_path = Path("output/validation_reports")
output_path.mkdir(parents=True, exist_ok=True)
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
result_file = output_path / f"lgb_backtest_v71_signal_penalty_{ts}.json"
with open(result_file, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)
print(f"\n结果已保存: {result_file}")
