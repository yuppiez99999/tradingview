"""LGB 集成回测 V7-Model - Regime-Aware 特征工程 (模型层优化)

回测期: 2022-04-01 ~ 2025-12-31 (45 个月)
标的: 全部 23 个 POSITION_SYMBOLS

V7-Model 优化内容 (模型层, 替代 V6/V6.2/V7-风控):
  V6.2 基线 (修复bug后): 年化14.35%, Sharpe1.092, WF Sharpe CV=0.55 (未达<0.5)
  V6.2 失败根因: Window 1 bull regime 平均 -1.92%
    2024-06-03 (bull regime): 688017 权重10%但跌34.82%, 300308 权重8%但跌17.34%
    LGB 给高波动股高权重但信号在 bull regime 失效, 满仓无个股级保护
  V7-风控 (止损/波动率调整) 已证明无效: 被动风控无法解决 Alpha 信号质量问题

V7-Model 方案 (从模型层让 LGB 学习 regime 风险):
  新增 add_regime_aware_features() 函数 (lgb_enhanced_trainer.py):
    - 4 个 regime dummy: market_regime_bull/bear/choppy/rebound (基于510300 MA60)
    - 2 个大盘指标: market_vol_20, market_mom_20
    - 2 个交互特征: vol20_x_bull, mom20_x_bull
      核心交互让 LGB 学习 "bull × 高波动 → 低未来收益" 的模式
  接入位置: institutional_pipeline_runner._build_lgb_feature_dict Step 2.6
  缓存清理: output/institutional_pipeline/ 已备份至 v6_2_backup 并清空

V7-Model 预期效果:
  - bull regime 下高波动股 (如688017/300308) 预测分降低 → 权重自然下降
  - Window 1 bull regime 收益改善 → Sharpe CV 下降至 <0.5
  - 模型层优化不依赖事后风控, 保留 V6.2 止盈规则 (单标的>50%→×0.6, 组合>12%→×0.80)

目标: WF Sharpe CV < 0.5, DSR n_trials >= 5, 年化 >= 8%, 去极端月年化 >= 8%
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
print("LGB 集成回测 V7-Model (Regime-Aware 特征工程, 模型层优化)")
print("=" * 70)
print(f"标的数: {len(SYMBOLS)}")
print("回测期: 2022-04-01 ~ 2025-12-31 (45 个月)")
print(f"单标的硬上限: {DEFAULT_MAX_WEIGHT:.0%}")
print("V7-Model 新增特征: 8 个 regime-aware (4 dummy + 2 大盘指标 + 2 交互项)")
print("V7-Model 核心交互: vol20_x_bull, mom20_x_bull")
print("止盈规则: 单标的>50%→×0.6, 组合>12%→×0.80 (与V6.2一致)")
print("模式: resume=False (强制LGB重训, 应用新特征)")
print()

t0 = time.time()
result = run_backtest(
    symbols=SYMBOLS,
    start="2022-04-01",
    end="2025-12-31",
    resume=False,  # V7: 强制重训, 应用 regime-aware 特征
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
result_file = output_path / f"lgb_backtest_v7_model_regime_aware_{ts}.json"
with open(result_file, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)
print(f"\n结果已保存: {result_file}")
