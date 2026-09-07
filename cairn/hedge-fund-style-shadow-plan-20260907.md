---
title: 对冲基金模式 shadow 验证方案
date: 2026-09-07
status: draft
related:
  - cairn/ROADMAP.md (CURRENT STATE + 2027 PLAN v8.7.1)
  - utils/quant_neutral_runner.py
  - utils/etf_option_combo/
  - utils/stat_arb/pairs_trading.py
  - utils/smart_beta_engine.py
---

# 对冲基金模式 shadow 验证方案

> **目标**：系统盈亏与涨跌方向解耦，靠波动/价差/对冲获利。
> **约束**：冻结窗 09-19~12-10 内只 shadow（不触生产代码），产出决策材料供 2027-01 v8.7.1 集成。
> **资金载体**：期货 100 万账户（已定位对冲/套利载体，杠杆 ≤2 倍）+ 证券 200 万灰度。

---

## 1. 已有策略审计

### 1.1 市场中性（已实现，未集成）

| 属性 | 值 |
|------|-----|
| 文件 | `utils/quant_neutral_runner.py` (890 行, v1.0) |
| 策略 | 多空市场中性 (long_short_market_neutral) |
| 目标 beta | 0.05，最大净敞口 0.10 |
| 调仓 | 月度，换手率 150%/月 |
| 做多 | 25 只因子 top 20% 股票 |
| 做空 | IC 期货对冲，最多 3 张合约 |
| 因子 | 7 因子（momentum 20% / reversal 15% / volatility 15% / liquidity 10% / earnings_quality 15% / growth 15% / valuation 10%） |
| 止损 | 月度回撤 > 95% 分位 → 仓位减半；连续 3 月 → 暂停 1 月；IC 基差 > 1.5% → 减仓 30% |
| 目标 | 最大回撤 8%，夏普 1.2 |
| 集成路径 | `daily_workflow.py phase_quant_neutral() → run_monthly_rebalance()` |

### 1.2 期权卖方组合（已实现，未集成）

| 属性 | 值 |
|------|-----|
| 目录 | `utils/etf_option_combo/` (12 文件) |
| 编排器 | `ComboOrchestrator` — 策略路由 → 并行生成 → 订单合并 → 滚仓 → 状态持久化 |
| 策略 | covered_call / collar / cash_secured_put / vertical_spread / calendar_spread |
| 回测 | `ComboBacktest` — 逐日驱动（BS 定价 → 信号 → 建仓 → Greeks → 滚仓 → 成本 → 绩效） |
| 风控 | `ComboRiskManager` |
| IV 自适应 | `iv_adaptive.py` — IV Rank 分层选策略 |
| 配置 | `config/etf_option_combo.yaml` |

### 1.3 统计套利/配对交易（已实现，未集成）

| 属性 | 值 |
|------|-----|
| 文件 | `utils/stat_arb/pairs_trading.py` (225 行, 零依赖) |
| 协整 | `cointegration.py` — Engle-Granger 检验 |
| 信号 | Z-score：z > entry_z 做空价差，z < -entry_z 做多价差，\|z\| < exit_z 平仓 |
| 半衰期 | [1, 60] 天过滤 |
| 备选 | `utils/strategy_lib/pairs_trading.py` (依赖 statsmodels) + `utils/strategy/arbitrage/pairs_trading.py` (Walk-Forward) |

### 1.4 多空组合构建（已实现）

| 属性 | 值 |
|------|-----|
| 文件 | `utils/smart_beta_engine.py:335` |
| 方法 | `build_long_short_portfolio(market_neutral=True)` — 多空等市值构建 |

### 1.5 缺口

| 缺口 | 说明 | 优先级 |
|------|------|--------|
| 波动率交易引擎 | IV 均值回归 / gamma scalping — 不存在 | P1 |
| delta neutral 框架 | 仅有 beta_neutral 用于期货比率分配 — 无独立框架 | P2 |
| 市场中性回测 | quant_neutral_runner 无独立回测 — 需新建 | P1 |
| 配对交易回测 | pairs_trading 有信号无回测 — 需新建 | P2 |
| 主流程集成 | 全部未集成 — 等 v8.7.1 窗口 | P1 |

---

## 2. Shadow 验证方案（冻结窗 09-19 ~ 12-10）

### Phase 1: 策略回测审计（09-19 ~ 10-12，shadow）

**目标**：对已有策略跑 2021-2026 回测，产出绩效报告 + 容量分析 + 相关性矩阵。

| 任务 | 输入 | 输出 | 状态 |
|------|------|------|------|
| T1.1 市场中性回测 | quant_neutral_runner + 2021-2026 日线 | `reports/shadow/hedge_fund/market_neutral_backtest.json` | 待开发 |
| T1.2 期权卖方回测 | ComboBacktest + 510050/510300 期权链 | `reports/shadow/hedge_fund/option_selling_backtest.json` | 已有引擎，待配置运行 |
| T1.3 配对交易回测 | pairs_trading + 协整对筛选 | `reports/shadow/hedge_fund/pairs_trading_backtest.json` | 待开发 |
| T1.4 策略相关性矩阵 | T1.1-T1.3 日收益序列 | `reports/shadow/hedge_fund/correlation_matrix.json` | 待开发 |

**验收标准**：
- 市场中性：年化 ≥ 8%，回撤 ≤ 8%，夏普 ≥ 1.0，beta ≤ 0.1
- 期权卖方：年化 ≥ 6%，回撤 ≤ 5%，胜率 ≥ 70%
- 配对交易：年化 ≥ 5%，回撤 ≤ 4%，半衰期 [5, 30] 天
- 策略间相关性 < 0.5（可组合）

### Phase 2: 波动率交易 POC（09-19 ~ 10-26，shadow 代码）

**目标**：补齐波动率交易引擎（冻结窗内可写 shadow 代码，不触生产）。

| 模块 | 功能 | 文件 |
|------|------|------|
| IV 均值回归 | IV Rank > 80 卖期权，IV Rank < 20 买回 | `utils/vol_trading/iv_mean_reversion.py` (新建) |
| gamma scalping | delta 中性 + 定期再平衡收割 gamma | `utils/vol_trading/gamma_scalp.py` (新建) |
| delta neutral 框架 | 组合层 delta → 0 的自动调仓框架 | `utils/vol_trading/delta_neutral.py` (新建) |
| 波动率回测 | 逐日驱动 + IV 路径模拟 | `utils/vol_trading/vol_backtest.py` (新建) |

**设计要点**：
- IV 数据源：期权链隐含波动率（复用 `OptionChainFetcher`）
- delta 对冲工具：ETF 期权 + 50ETF/300ETF 现货
- gamma scalp 频率：日度再平衡
- 风控：单策略保证金 ≤ 15% 总资产，IV 突变 > 3σ → 减仓

### Phase 3: 方向中性组合构建（10-13 ~ 11-10，shadow）

**目标**：设计资金分配 + 组合层风控参数。

```yaml
# config/hedge_fund_style.yaml (新建, shadow)
capital_allocation:
  total: 3_000_000          # 证券 200 万 + 期货 100 万
  market_neutral: 0.40      # 120 万 — 多空市场中性
  option_selling: 0.30      # 90 万 — covered_call + cash_secured_put
  pairs_trading: 0.20       # 60 万 — 协整套利
  volatility_trading: 0.10  # 30 万 — IV 均值回归 + gamma scalp

risk_control:
  portfolio_beta_target: 0.05     # 组合层 beta ≤ 0.1
  portfolio_delta_target: 0.0     # 组合层 delta → 0
  max_drawdown: 0.10              # 组合最大回撤 10%
  max_leverage: 2.0               # 杠杆 ≤ 2 倍
  max_correlation: 0.5            # 子策略间相关性 < 0.5
  monthly_rebalance: true         # 月度调仓
  daily_risk_check: true          # 日度风控检查

regime_adaptive:
  high_vol:                       # 高波动环境
    option_selling_weight: 0.35   # 增加期权卖方
    vol_trading_weight: 0.15      # 增加波动率交易
  low_vol:                        # 低波动环境
    market_neutral_weight: 0.50   # 增加市场中性
    pairs_trading_weight: 0.25    # 增加配对交易
```

### Phase 4: Shadow 联合运行（11-11 ~ 12-10，shadow）

**目标**：联合 shadow 运行所有策略，逐日绩效追踪。

| 任务 | 说明 |
|------|------|
| T4.1 shadow runner | 新建 `scripts/hedge_fund_shadow_runner.py` — 日度 EOD 调度 |
| T4.2 绩效追踪 | 逐日 NAV + 各子策略贡献分解 + 与主组合相关性 |
| T4.3 风控监控 | 组合层 beta/delta/gamma 实时监控 + 回撤预警 |
| T4.4 cron 注册 | EOD 16:40 触发（与现有 shadow cron 并行） |

**输出**：
- `reports/shadow/hedge_fund/daily/{date}.json` — 逐日绩效
- `reports/shadow/hedge_fund/summary_30day.json` — 30 天汇总
- `reports/shadow/hedge_fund/decision_material.json` — 集成决策材料

### Phase 5: 集成规划（2027-01 v8.7.1 窗口）

| 任务 | 说明 |
|------|------|
| T5.1 生产集成 | 市场中性 + 期权卖方 + 配对交易 + 波动率交易 → 主流程 |
| T5.2 回测验证 | 联合回测 2021-2026 + Walk-Forward |
| T5.3 灰度上线 | 10 万 → 50 万 → 100 万 → 300 万 |
| T5.4 持续监控 | 日度绩效 + 月度归因 + 季度调参 |

---

## 3. 时间线

```
09-19 ~ 10-12  Phase 1: 策略回测审计 (shadow)
09-19 ~ 10-26  Phase 2: 波动率交易 POC (shadow 代码)
10-13 ~ 11-10  Phase 3: 方向中性组合构建 (shadow)
11-11 ~ 12-10  Phase 4: Shadow 联合运行
─────────────  12-10 功能冻结 / 12-31 v8.7 发布 ─────────────
01-02 ~ 01-09  生产切换窗 (四项独立 Go/No-Go)
01-10 ~ 03-31  Phase 5: v8.7.1 集成 (正式)
```

---

## 4. 验收标准

### 4.1 Shadow 验收（12-10 前）

| 指标 | 目标 | 口径 |
|------|------|------|
| 组合年化收益 | ≥ 8% | shadow 30 天滚动 |
| 组合最大回撤 | ≤ 10% | shadow 全周期 |
| 组合 beta | ≤ 0.1 | 相对沪深 300 |
| 月度胜率 | ≥ 70% | shadow 月度 |
| 子策略相关性 | < 0.5 | 两两相关 |
| 与主组合相关性 | < 0.3 | 方向解耦验证 |

### 4.2 生产验收（v8.7.1 灰度）

| 指标 | 目标 | 口径 |
|------|------|------|
| 实盘年化 | 8% ~ 18% | 真实资金灰度 |
| 实盘回撤 | ≤ 10% | 真实资金 |
| 实盘 beta | ≤ 0.1 | 真实资金 |
| 月度胜率 | ≥ 70% | 12 个月中 8-9 月正 |

---

## 5. 风险与缓解

| 风险 | 缓解 |
|------|------|
| IC 期货基差风险 | 基差 > 1.5% → 减仓 30% + 转 ETF 对冲 |
| 期权流动性不足 | 限 50ETF/300ETF 主力合约 + 滑点 ≤ 0.5% |
| 协整对失效 | 半衰期监控 + 动态剔除 + 月度重筛 |
| IV 突变 | IV > 3σ → 减仓 + Kill Switch |
| 杠杆超限 | 组合层杠杆 ≤ 2 + 日度监控 |
| 与主组合相关性过高 | 相关性 > 0.3 → 降权重 + 归因分析 |

---

## 6. 决策材料产出清单（供 2027-01 集成评审）

1. `reports/shadow/hedge_fund/market_neutral_backtest.json` — 市场中性回测
2. `reports/shadow/hedge_fund/option_selling_backtest.json` — 期权卖方回测
3. `reports/shadow/hedge_fund/pairs_trading_backtest.json` — 配对交易回测
4. `reports/shadow/hedge_fund/vol_trading_backtest.json` — 波动率交易回测
5. `reports/shadow/hedge_fund/correlation_matrix.json` — 策略相关性矩阵
6. `reports/shadow/hedge_fund/summary_30day.json` — 30 天联合 shadow 汇总
7. `reports/shadow/hedge_fund/decision_material.json` — 集成决策材料（Go/No-Go 输入）
8. `config/hedge_fund_style.yaml` — 方向中性组合配置