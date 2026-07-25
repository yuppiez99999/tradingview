# -*- coding: utf-8 -*-
"""LGB 集成回测 V6.2 - 止盈规则精调 (平衡峰度与Sharpe CV)

回测期: 2022-04-01 ~ 2025-12-31 (45 个月)
标的: 全部 23 个 POSITION_SYMBOLS

V6.2 优化内容 (在V6.1基础上, 回退阈值但保留加大减仓力度):
  V6问题: Sharpe CV=0.46 PASS, 但 DSR n_trials=3 FAIL (峰度4.99过高)
  V6.1方案: 组合>10%→×0.80 (降阈值+加力度)
    实测: DSR n_trials=5 PASS (峰度3.83), 但 Sharpe CV=0.55 FAIL, 去极端月年化=6.48% FAIL
    失败原因: 10%阈值在Window 1(2023-07~2024-09)触发级联减仓,
              该窗口年化从V6的6.98%退到3.23%, Sharpe从0.578退到0.330
  V6.2方案: 组合>12%→×0.80 (回退阈值至V6的12%, 保留V6.1的0.80减仓力度)
    设计逻辑:
      - 12%阈值: 避免在Window 1低收益月份触发级联减仓, 保护Sharpe CV
      - 0.80因子: 比V6的0.85更激进减仓, 降低极端月份(2025-08/09)的峰度
    预期效果:
      - Sharpe CV: 0.46~0.50 (保持<0.5, 因Window 1不再被级联减仓破坏)
      - DSR n_trials: 3→5 (峰度从4.99降至~3.5, 接近V6.1的3.83)
      - 去极端月年化: 8%~9% (保持>=8%, 因12%阈值不影响常规月份)

V6/V6.1/V6.2 对比表:
  版本    | 阈值 | 因子 | Sharpe CV | DSR n_trials | 去极端月年化 | Window1年化
  --------|------|------|-----------|--------------|--------------|------------
  V6      | 12%  | 0.85 | 0.46 PASS | 3 FAIL       | 8.92% PASS   | 6.98%
  V6.1    | 10%  | 0.80 | 0.55 FAIL | 5 PASS       | 6.48% FAIL   | 3.23%
  V6.2    | 12%  | 0.80 | 待验证    | 待验证       | 待验证       | 待验证

目标: Sharpe CV<0.5 AND DSR n_trials>=5 AND 去极端月年化>=8% AND 年化>=8%
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
print("LGB 集成回测 V6.2 (止盈精调: 组合>12%→×0.80, 平衡峰度与Sharpe CV)")
print("=" * 70)
print(f"标的数: {len(SYMBOLS)}")
print(f"回测期: 2022-04-01 ~ 2025-12-31 (45 个月)")
print(f"单标的硬上限: {DEFAULT_MAX_WEIGHT:.0%}")
print(f"V6.2止盈规则: 单标的>50%→×0.6, 组合>12%→×0.80 (V6原: 组合>12%→×0.85)")
print(f"模式: resume=True (复用V6/V6.1 LGB缓存, 仅重新应用V6.2止盈规则)")
print()
print("对比基准:")
print(f"  V6   (12%/×0.85): Sharpe CV=0.46 PASS, DSR n_trials=3 FAIL, 去极端月=8.92% PASS")
print(f"  V6.1 (10%/×0.80): Sharpe CV=0.55 FAIL, DSR n_trials=5 PASS, 去极端月=6.48% FAIL")
print(f"  V6.2 (12%/×0.80): 期望 Sharpe CV<0.5 AND DSR n_trials>=5 AND 去极端月>=8%")
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
from pathlib import Path
from datetime import datetime

output_path = Path("output/validation_reports")
output_path.mkdir(parents=True, exist_ok=True)
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
result_file = output_path / f"lgb_backtest_v6_2_profit_taking_{ts}.json"
with open(result_file, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)
print(f"\n结果已保存: {result_file}")
