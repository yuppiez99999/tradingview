---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-02
updated: 2026-08-02
contains: risk-guard-system, kill-switch, vega-monitor, liquidity-monitor, evt-tail-risk, multi-layer-defense
related:
  - cairn/backtest-standards.md
---

# 风控架构

> 记录本项目的多层风控体系：四 Guard 联动、Kill Switch、Vega/流动性/EVT 监控、配置管理与一致性保障。对应 CHANGELOG v8.3.0、v8.5、v8.6.14。

## 一、四 Guard 联动强制执行系统

风控核心是 `utils/risk_guard_integrator.py` 实现的四 Guard 联动机制，每日 EOD 按优先级顺序强制执行、不可跳过。每个 Guard 的输出作为下一个 Guard 的输入约束。

### Guard 1 — 回撤守卫（Drawdown Guard）

监控组合最大回撤与日内回撤，分三级响应：

- **Level 0（正常）**：回撤 <10%，正常运作，无限制
- **Level 1（预警）**：回撤 10-15%，新开仓位减半，已有仓位不做强制减仓
- **Level 2（强制减仓）**：回撤 15-20%，强制削减权益净敞口至 50%，暂停追加新仓位
- **Level 3（熔断）**：回撤 >20%，清仓所有非对冲仓位，仅保留核心 ETF 底仓，触发全系统 Kill Switch 审查

### Guard 2 — 波动率守卫（Volatility Guard）

基于 AQR/Man Group 风格的波动率目标控制（`utils/vol_target_controller.py`）：

- 目标年化波动率：12%
- 计算方法：已实现波动率（滚动 60 日） vs 目标波动率的比率
- vol_scale < 1.0 时按比例缩仓，vol_scale > 1.0 时维持 1.0（不主动加杠杆）
- 触发阈值：当实际波动率超过目标 20%（vol_scale < 0.83）时强制执行缩仓

### Guard 3 — 对冲守卫（Hedge Guard）

通过 `utils/hedge_execution_engine.py` 将 Beta 对冲信号转化为实际订单：

- 计算持仓加权 Beta（基于 120 日滚动窗口）
- IF 期货对冲：1 手 = ¥1,241,520 名义价值，Beta 暴露从 1.052 降至 0.30
- 动态对冲：回撤每加深 5%，自动追加 1 手 IF 空单（回撤加码联动）

### Guard 4 — 认沽守卫（Protective Put Guard）

通过 `utils/protective_put_engine.py` 自动管理认沽保护：

- PUT 预算：56 万元（初始），已扩大至 77.8 万元（v8.4 优化后）
- 策略：OTM 5% 虚值 Put，覆盖 6 大指数 ETF（510050/588080/159915/510300/510500/512100）
- 到期前 5 天自动滚仓至下一月合约
- 去重保护（v8.3.1）：`UNDERLYING_CODE_MAP` 三层代码提取策略，认沽引擎为权威来源，对冲引擎重复 PUT 自动剔除

## 二、Kill Switch 熔断机制

`utils/kill_switch.py` 实现全系统紧急断电，触发条件包括：

- 组合日亏损超过预设阈值（可配置，默认 -5%）
- 连续 5 笔交易亏损自动暂停 10 分钟
- 撤单率 >50% 触发警告，>80% 强制暂停
- 保证金占用超过净值 80%
- 任意单个 Guard 触发 Level 3 时，Kill Switch 自动审查

生命周期：工作流 `run()` 启动时 `arm()` 武装，每个阶段后 `check()` 检查，触发即终止整个工作流。v8.5 修复了导入但从未 arm/check 的 P0 Bug。

## 三、v8.5 增强监控模块

### VegaMonitor（`utils/vega_monitor.py`）

期权组合的波动率暴露监控。Vega 表示隐含波动率变化 1% 对组合价值的影响。监控阈值：组合 Vega 不超过净值的 1%-2%（对应 1% 波动率变化）。超限自动告警，触发对冲调整建议。

### LiquidityMonitor（`utils/liquidity_monitor.py`）

实时监控市场流动性状况以评估执行可行性：

- 盘口深度评分：买一 + 卖一量 vs 历史均值
- 流动性枯竭检测：盘口深度骤降 >70% 自动撤单
- 执行可行性检查：组合仓位 vs 日均成交额的比例检查（不超过 1%-5%）

### EVTTailRisk（`utils/evt_tail_risk.py`）

极值理论（Extreme Value Theory）尾部风险建模：

- 广义帕累托分布（GPD）拟合尾部极端损失分布
- 99% VaR 和 CVaR 估计，与历史模拟法交叉验证
- 需要 ≥120 天 Shadow 账户数据后自动激活（当前处于只读对照模式）

### FactorDecayMonitor（`utils/factor_decay_monitor.py`）

因子 Alpha 衰减检测：

- 滚动窗口 IC 序列趋势分析
- 连续 3 个月 IC 均值 <0.02 或趋势性衰减（线性回归斜率 p<0.05）触发预警
- 与因子退役标准联动

## 四、多层防御架构总览

```
第一层：事前风控（订单生成后，微秒级）
├── 单笔订单金额上限检查
├── 净头寸 Delta/Gamma 上限检查
├── 保证金占用 ≤ 净值 80%
├── 日内累计成交额上限
└── 撤单率监控（>50% 警告，>80% 暂停）

第二层：事中风控（毫秒级实时监控）
├── PnL 偏离预期路径检测（>3σ 预警）
├── 市场异常波动检测（5 分钟涨跌 >2% 削减 50% 仓位）
├── 流动性枯竭检测（盘口深度 >70% 骤降 → 自动撤单）
└── 跨品种相关性崩溃检测

第三层：事后风控（日频复盘）
├── Brinson 归因分析（Alpha/Beta/行业/风格分解）
├── Implementation Shortfall 每笔归档
├── 协方差矩阵周度更新（Shrinkage Estimator 修正）
└── 四 Guard EOD 强制执行

第四层：极端情景风控（每日自动运行）
├── 历史十大极端行情压力测试（2008/2015/2016/2020/2024）
├── 任一情景损失 >20% 触发减仓
├── 蒙特卡洛 2000 路径 99% CVaR
└── EVT GPD 尾部 VaR
```

## 五、配置管理 — 单一事实源

`utils/master_config_manager.py`（v8.3.0）解决三份配置文件的漂移问题。启动时对 `system_config.json`、`configs/portfolio.yaml`、`config/positions.json` 做一致性校验，不一致时拒绝启动并输出差异报告。v8.4 CHANGELOG 记录了一个实际案例：`v8.3_institutional/config/portfolio.yaml` 中 stock_etf_capital 从 3M 修正为 4M、hedge_capital 从 2M 修正为 1M 以与权威版保持一致。

## 六、对冲执行引擎

`utils/hedge_execution_engine.py` 承担对冲信号到实际订单的转化：

- 组合加权 Beta 计算（120 日滚动窗口，`positions.json` 驱动）
- 订单去重按（类型、标的、动作）键合并
- 执行时机管理：期权 09:30-10:00、期货 10:30-11:00
- 对冲账户保留 30% 资金作为动态调整空间
- 回撤加码联动：回撤每加深 5% 自动追加 1 手 IF 空单

## 七、后续方向

- 三大 Guard 覆盖率提升至 80%+ 单元测试
- Shadow Account 14 天观测期届满后激活 Kill Switch callback 注册
- EVT/GARCH/Kalman 从只读对照升级为生产接入
- 风控参数的贝叶斯自适应调整替代静态阈值
- **MVSK 高阶矩组合优化**（YAND 启发）：P1 已在 `risk_budget_optimizer.py` 落地（偏度/峰度进优化目标，不建张量），P2/P3 待启动 → `cairn/mvsk-higher-moment-optimization.md`
