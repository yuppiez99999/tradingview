---
title: P1 前置清障 — 市场中性回测数据管道就绪度报告
date: 2026-09-09
status: done
phase: P1 前置（09-19 启动前清障）
related:
  - cairn/hedge-fund-style-shadow-plan-20260907.md (T1.1)
  - utils/quant_neutral_runner.py
  - utils/akshare_futures.py
  - utils/universe/stock_universe.py
---

# P1 前置清障：市场中性回测数据管道就绪度

> **结论**：计算器层 100% 就绪，回测骨架 90% 可复用，**数据层仅 ETF 侧就绪** — 个股/期货/基本面三条数据管道需新建。P1 T1.1 建议采用"量价因子轻量版"先跑通，完整 7 因子版并行补数据。

---

## 1. 就绪度总览

| 层 | 组件 | 状态 | 说明 |
|----|------|------|------|
| 计算器 | `quant_neutral_runner.run_monthly_rebalance()` | ✅ 就绪 | 纯计算器，7 因子截面打分 + IC 对冲 + 风控，全部输入外部传入 |
| 计算器 | `ic_hedge_calculator.calculate()` | ✅ 就绪 | 纯算术（合约规格硬编码），基差由外部计算传入 |
| 骨架 | `hedge_rebalance_backtest.BacktestDataLoader` | ✅ 可复用 | 缓存+baostock 下载逻辑现成；三指数现货加载现成（:341-376） |
| 骨架 | `wt_backtest_engine.BacktestEngine` | ✅ 可复用 | 逐日事件引擎，T+1/佣金/印花税/冲击成本（ADV 分层）— 与月度换手 150% 撮合建模匹配 |
| 骨架 | `ms_strategy/src/backtest/` | ✅ 可复用 | WalkForward / CPCV / DSR / CostModel / 噪声注入 |
| 数据 | ETF 日线 2021-2026 | ✅ 就绪 | `data/etf_option_backtest/` 14+17 只 qfq parquet（2015 起可选） |
| 数据 | 个股日线 2021-2026 | ❌ 缺失 | `data/cache/klines/` 791 只仅 300 日（2025-05 起）；`kline_*_daily.parquet` 0 个 |
| 数据 | IC 期货日线 | ❌ 缺失 | 无任何 CFFEX 数据文件；`get_futures_daily` 接口在但 IC 未实测 |
| 数据 | 中证500 现货 | ❌ 缺失 | baostock `sh.000905` 逻辑现成（hedge_rebalance_backtest:341），缓存未建 |
| 数据 | ROE/营收增速/PE 分位历史 | ❌ 缺失 | `feature_store/` 仅单日占位（roe=0.0 疑似空数据）；AKShare 财务接口仅"最新值" |
| 数据 | 历史时点股票池 | ❌ 缺失 | `data/universe/` 不存在；`survivorship_free_universe.py` 纯框架未灌数据 |
| 管道 | 因子截面生成 | ❌ 缺失 | 生产 phase 硬编码 15 只持仓 + 占位因子值（quant_neutral.py:182-202） |

## 2. 关键数据需求（已确认精确结构）

**candidate_universe 每只股票只需 7 个数据列**（`_map_factor_to_column` 实际映射，docstring 中 cashflow_ratio/profit_growth/pb_percentile 未参与打分）：

| 因子 | 权重 | 数据列 | 类型 |
|------|------|--------|------|
| momentum | 20% | `returns_20d` | 日线 |
| reversal | 15% | `returns_5d` | 日线 |
| volatility | 15% | `volatility_60d` | 日线 |
| liquidity | 10% | `avg_turnover_amount` | 日线 |
| earnings_quality | 15% | `roe` | **季度财务** |
| growth | 15% | `revenue_growth` | **季度财务** |
| valuation | 10% | `pe_percentile` | **估值历史** |

**IC 侧只需 2 个标量/月**：`ic_price`（点位）+ `basis`（=(现货-期货)/现货，>1.5% 触发减仓 30%）。

## 3. 缺口清单与补数据方案（按优先级）

| # | 缺口 | 建议来源（项目内已有能力） | 工作量 |
|---|------|--------------------------|--------|
| 1 | 个股日线 800 只×2021-2026 | AKShare `stock_zh_a_hist(hfq)`（akshare_data_source:248-329 现成） | 批量脚本 ~1 天（限速 ~2h 跑完） |
| 2 | 中证500 现货日线 | baostock sh.000905（hedge_rebalance_backtest:341 现成） | 10 行脚本 |
| 3 | IC 期货主力日线 | `futures_main_sina("IC0")`（akshare_futures:411 现成，需实测） | 0.5 天含验证 |
| 4 | IC 基差序列 | #2 ÷ #3 自算 | 10 行脚本 |
| 5 | ROE + 营收增速季度历史 | AKShare `stock_financial_report_sina`（:381-397 已封装）；⚠️ 需 point-in-time 对齐（财报发布滞后）防前视偏差 | 2-3 天（最复杂） |
| 6 | PE 历史分位 | 日线+财务自算 或 AKShare 估值接口（需新接） | 1-2 天 |
| 7 | 历史时点股票池快照 | `survivorship_free_universe.py` 框架灌数据（月度快照 2021-2026） | 1 天 |
| 8 | 因子截面生成管道 | 新写：stock_universe.get_universe() + #1 数据 → 7 列 dict | 1 天 |
| 9 | 个股 beta 序列 | #1 日线对指数 60 日回归自算 | 0.5 天 |

**合计完整版 ~7-10 人天**；轻量版（#1-#4 + #8 量价部分）~2-3 人天。

## 4. P1 T1.1 两档执行方案（建议）

### 轻量版（推荐先跑，09-19 即可启动）

- **因子**：仅 4 个量价因子（momentum 33% / reversal 25% / volatility 25% / liquidity 17%，权重重归一化）
- **数据**：#1-#4 + #8（量价部分）— 2-3 天补齐
- **股票池**：hs300_zz500 合并池缩样（300 只，按日均成交额降序）— 控制拉取量与回测速度
- **对冲**：IC 主力合约月度换仓，基差用 #4 真实序列
- **验收**：方案文档 §4.1 标准（年化≥8%/回撤≤8%/夏普≥1.0/beta≤0.1）
- **局限**：与完整版因子构成不同，结论外推需注明"量价因子版"

### 完整版（并行补数据，10 月中前补齐）

- 轻量版跑通后叠加 #5-#7（财务/估值/幸存者池）
- **前视偏差防护**：财务数据按"财报公告日后第二个交易日可用"对齐（point-in-time），CPCV 交叉验证
- 完整版结果取代轻量版作为 T1.1 最终决策材料

## 5. 现成资产直用清单（P1 开发时直接引用）

| 组件 | 路径:行号 |
|------|----------|
| 月度调仓决策引擎 | `utils/quant_neutral_runner.py:450` `run_monthly_rebalance()` |
| IC 对冲计算 | `utils/ic_hedge_calculator.py:106` `calculate()` |
| ETF 数据拉取模板 | `data/etf_option_backtest/fetch_etf_data.py`（Wind MCP→AKShare 降级 + parquet 落盘模式，照抄改造为个股版） |
| 个股日线 AKShare 封装 | `utils/akshare_data_source.py:248-329` |
| 期货日线封装 | `utils/akshare_futures.py:411-435` `get_futures_daily()` |
| 指数现货 baostock | `utils/hedge_rebalance_backtest.py:341-376` |
| 逐日撮合引擎 | `utils/wt_backtest_engine.py:110` `BacktestEngine.run()` |
| 绩效/DSR/CPCV | `ms_strategy/src/backtest/metrics.py` + `combinatorial_purged_cv.py` |
| 股票池 | `utils/universe/stock_universe.py:148` `get_universe("hs300_zz500")` |

## 6. 风险提示

1. **AKShare 限速**：800 只×5.5 年日线拉取需限速（~0.5s/次），批量脚本须断点续传（parquet 增量落盘）
2. **前视偏差**：PE 分位/财务因子必须 point-in-time 对齐 — 轻量版量价因子天然规避此风险
3. **幸存者偏差**：轻量版用"当前 hs300_zz500 成分回测 2021-2026"存在幸存者偏差 — 结论标注局限，完整版用快照池修正
4. **IC 基差回测口径**：2015 股灾后中金所对 IC 有长期贴水，基差>1.5% 减仓规则在回测中会频繁触发 — 这是真实成本，不是 bug
5. **tushare 不可用**：TS_TOKEN 未设置，与本需求无关（主链不依赖）