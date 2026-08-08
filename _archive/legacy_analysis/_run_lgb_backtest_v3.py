"""LGB 集成回测 V3 - 降低单股权重上限 15%→10%

回测期: 2022-04-01 ~ 2025-12-31 (45 个月)
标的: 全部 23 个 POSITION_SYMBOLS

V3 优化内容:
  1. 单标的硬上限 15%→10%
     - 动机: 2025-08 300308 占 15% 权重产生 +84% 月收益, 导致极端月份依赖
     - 降低单股集中度, 减少路径依赖风险
     - 预期: 年化收益略降, 但 Walk-Forward 稳定性提升, 去极端月份后年化提升

模式: resume=True (复用V2缓存权重, 用10%上限截断)
      若效果方向正确, 后续可用 resume=False 精确重跑
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
print("LGB 集成回测 V3 (单股权重上限 15%→10%)")
print("=" * 70)
print(f"标的数: {len(SYMBOLS)}")
print("回测期: 2022-04-01 ~ 2025-12-31 (45 个月)")
print(f"单标的硬上限: {DEFAULT_MAX_WEIGHT:.0%} (V2: 15%)")
print("模式: resume=True (复用V2缓存, 10%截断)")
print()

t0 = time.time()
result = run_backtest(
    symbols=SYMBOLS,
    start="2022-04-01",
    end="2025-12-31",
    resume=True,  # V3: 复用V2缓存, 用10%上限截断
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

import json  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402

output_path = Path("output/validation_reports")
output_path.mkdir(parents=True, exist_ok=True)
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
result_file = output_path / f"lgb_backtest_v3_maxweight10_{ts}.json"
with open(result_file, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)
print(f"\n结果已保存: {result_file}")
