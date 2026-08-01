# -*- coding: utf-8 -*-
"""LGB 集成后完整回测验证 (扩展周期版)

回测期: 2022-04-01 ~ 2025-12-31 (45 个月, 满足 DSR 统计显著性要求)
标的: 全部 23 个 POSITION_SYMBOLS (均有 LGB 模型)
模式: resume=False (MACD warm-up NaN 修复后, 旧缓存失效, 完全重新运行)

训练数据说明:
  - 5 年 _base.parquet 覆盖 2021-04 ~ 2026-07 (1260 天)
  - 最早回测日 2022-04-01: 训练数据 2021-04 ~ 2022-03 (~240 天)
    减去 MACD warm-up (35 天) 后 ~205 天, 满足 min_samples=150
  - 所有回测日均有充足训练数据, 无需降门槛
"""
import logging
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

from research.backtest_runner import run_backtest

# 使用全部有 LGB 模型的标的（POSITION_SYMBOLS 的代码部分）
SYMBOLS = [
    "588000",  # 科创50ETF华夏
    "688041",  # 海光信息
    "002371",  # 北方华创
    "688981",  # 中芯国际
    "300308",  # 中际旭创
    "000425",  # 徐工机械
    "601088",  # 中国神华
    "600276",  # 恒瑞医药
    "600900",  # 长江电力
    "515180",  # 易方达红利ETF
    "600036",  # 招商银行
    "518880",  # 黄金ETF华安
    "300274",  # 阳光电源
    "603019",  # 中科曙光
    "600089",  # 特变电工
    "688017",  # 绿的谐波
    "600219",  # 南山铝业
    "600019",  # 宝钢股份
    "000680",  # 山推股份
    "000333",  # 美的集团
    "000408",  # 藏格矿业
    "000975",  # 山金国际
    "002422",  # 科伦药业
]

print("=== LGB 集成回测 (扩展周期) ===")
print(f"标的数: {len(SYMBOLS)}")
print("回测期: 2022-04-01 ~ 2025-12-31 (45 个月)")
print("模式: resume=False (MACD 修复后完全重新运行)")
print()

t0 = time.time()
result = run_backtest(
    symbols=SYMBOLS,
    start="2022-04-01",
    end="2025-12-31",
    resume=False,  # MACD warm-up NaN 修复后旧缓存失效, 完全重新运行
)
elapsed = time.time() - t0

print()
print(f"=== 回测完成 (耗时 {elapsed:.0f}s / {elapsed/60:.1f}min) ===")
print(f"状态: {result.get('status', 'N/A')}")

# 打印关键指标（run_backtest 返回顶层键）
print(f"年化收益: {result.get('annual_return', 'N/A')}")
print(f"最大回撤: {result.get('max_drawdown', 'N/A')}")
print(f"胜率: {result.get('win_rate', 'N/A')}")
acceptance = result.get('acceptance', {})
if acceptance:
    print(f"验收通过: {acceptance.get('passed', 'N/A')}")
    for c in acceptance.get('checks', []):
        print(f"  {c['metric']}: {c['value']} (要求 {c['required']}) {'✅' if c['ok'] else '❌'}")

# 保存结果
import json
from pathlib import Path
from datetime import datetime

output_path = Path("output/validation_reports")
output_path.mkdir(parents=True, exist_ok=True)
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
result_file = output_path / f"lgb_backtest_result_{ts}.json"
with open(result_file, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)
print(f"\n结果已保存: {result_file}")
