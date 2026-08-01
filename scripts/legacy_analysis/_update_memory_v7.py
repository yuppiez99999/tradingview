from pathlib import Path

p = Path(r"c:\Users\Administrator\.trae-cn\memory\projects\-e---PY---28---------8-4\project_memory.md")
c = p.read_text(encoding="utf-8")

if "V7-Model" in c:
    print("SKIP: V7-Model already in memory")
    raise SystemExit(0)

v7_block = """
## V7-Model 优化方案 (2026-07-25) — 模型层 Regime-Aware 特征工程
### 背景
- V6.2 基线 (修复bug后): 年化14.35%, Sharpe1.092, WF Sharpe CV=0.55 (未达<0.5)
- V6.2 失败根因: Window 1 bull regime 平均 -1.92% (非bear regime)
  - 2024-06-03 (bull regime): 688017权重10%但跌34.82%, 300308权重8%但跌17.34%
  - LGB给高波动股高权重但信号在bull regime失效, 满仓无个股级保护
- V7-风控 (止损/波动率调整) 已证明无效: 被动风控无法解决Alpha信号质量问题

### V7-Model 方案 (从模型层让 LGB 学习 regime 风险)
- 新增 add_regime_aware_features() 函数 (lgb_enhanced_trainer.py)
  - 4 个 regime dummy: market_regime_bull/bear/choppy/rebound (基于510300 MA60)
  - 2 个大盘指标: market_vol_20, market_mom_20
  - 2 个交互特征: vol20_x_bull, mom20_x_bull (核心)
    核心交互让 LGB 学习 "bull × 高波动 → 低未来收益" 的模式
- 接入位置: institutional_pipeline_runner._build_lgb_feature_dict Step 2.6
- 缓存处理: output/institutional_pipeline/ 备份至 v6_2_backup 并清空

### V7-Model 设计原则
1. 全部 regime 信号基于大盘 proxy (510300) 计算, 无前视偏差
2. regime label 在训练样本期间是已知的 (基于历史 MA60), 可用于训练
3. 交互特征让 LGB 自动学习 "bull 下高波动→低收益" 的模式, 而非硬编码

### V7-Model 回测状态 (2026-07-25 07:51 启动)
- 后台任务: job-4642a325d3ee4f529f9f8fb7a819857f
- 脚本: _run_lgb_backtest_v7_model.py (resume=False 强制重训)
- 验证脚本: _run_dsr_walkforward_v7_model.py
- 目标: WF Sharpe CV<0.5, DSR n_trials≥5, 年化≥8%, 去极端月年化≥8%
- Smoke test 已通过: 688017 features 47→55, regime分布合理 (bull 48%, bear 31%)
"""

c = c.rstrip() + v7_block
p.write_text(c, encoding="utf-8")
print("OK: V7-Model 已记录到 project_memory.md")
print("lines:", len(c.splitlines()))
