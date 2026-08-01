# ARCHITECTURE — 8.4 工程化达标与 8% 年化路线图

> 创建日期：2026-07-27
> 视角：优秀私募 + 中证500增强（年化 8-12%，夏普 0.6-0.9，最大回撤 ≤ 20%）

## 一、现状基线（2026-07-27 测量）

| 维度 | 现状 | 目标 | 差距 |
|------|------|------|------|
| mypy 错误数 | 560 | 0 | 560 |
| 因子库 alpha 函数 | 1 | ≥ 20 | 19 |
| 测试文件数 | ~75 | +覆盖率 80% | 需测覆盖率 |
| 回测滑点模型 | 固定 2bps | Almgren-Chriss | 需接入 |
| 样本外验证 | 简单切分 | Combinatorial Purged CV | 需重写 |
| DSR 实现 | 简化公式 | bootstrap E[SR_max] | 需重写 |
| KillSwitch callback | 未注册 | 已注册 | 需对接 |
| 三大 Guard | 缺失 | 完整 | 需新建 |
| Shadow Account | Stage 1 设计 | Stage 1 通过 | 需跑 14 天 |

## 二、目标架构（12 个月后）

```
┌─────────────────────────────────────────────────────────┐
│  Phase 3: 50 万小资金实盘 (M7-M12)                        │
│  - live-vs-backtest gap ≤ 3%                             │
│  - 月度 attribution + IC 衰减监控                        │
└─────────────────────────────────────────────────────────┘
                        ▲ 仅在 Phase 2 出口标准满足后启动
┌─────────────────────────────────────────────────────────┐
│  Phase 2: 不崩风控 (M4-M6)                               │
│  - KillSwitch.broker_callback 注册                       │
│  - 三大 Guard (大盘熔断/隔夜跳空/全局撤单)                │
│  - mypy --strict 零错误 + pylint broad-except = error    │
│  - 覆盖率 ≥ 80% + Shadow Account 14 天 DSR ≥ 5           │
└─────────────────────────────────────────────────────────┘
                        ▲ 仅在 Phase 1 出口标准满足后启动
┌─────────────────────────────────────────────────────────┐
│  Phase 1: 诚实回测 (M1-M3)                               │
│  - 因子库 20 个低相关 alpha                              │
│  - Almgren-Chriss 滑点 + Combinatorial Purged CV         │
│  - DSR bootstrap + 1000 次 noise injection               │
│  - 出口: SR ≤ 1.0, 样本外年化 ≥ 沪深300 + 3%             │
└─────────────────────────────────────────────────────────┘
```

## 三、技术方案选型

### 3.1 因子库扩展（T04）

**方案**：补齐 GTJA191 中 20 个低相关因子，不追求 191 全集

**选择**：
- Alpha6 / Alpha12 / Alpha40 / Alpha54 / Alpha101 等（参考文献：国泰君安191因子原文）
- 优先选择：量价类、动量类、波动率类、反转类各 5 个，保证 ρ < 0.7

**为什么不实现 191 全集**：
1. Python 重写 191 因子需要 2-3 个月，ROI 低
2. 完整 191 因子应配合 Qlib Expression Engine 落地（DSL 优于 Python）
3. 20 个低相关因子已能提供足够分散度（Tractability > Completeness）

### 3.2 滑点模型（T05）

**方案**：接入已实现的 `cost_model.Almgren-Chriss`

**选择**：
- 临时冲击：`η * σ * sign(q) * (|q|/V)^(1/2)`
- 永久冲击：`γ * σ * sign(q) * (|q|/V)^(1/2)`
- σ: 日波动率, V: 日成交量, q: 订单量

**为什么不重新实现**：
1. [cost_model.py:56](file:///E:/各种PY程序/28-终极量化交易系统8.4/ms_strategy/src/backtest/cost_model.py#L56) 已实现，仅需接入 SimulatedBroker
2. 重复造轮子是反模式

### 3.3 样本外验证（T06）

**方案**：Combinatorial Purged Cross-Validation (CPCV)

**选择**：
- 参考 López de Prado《Advances in Financial Machine Learning》第 7 章
- N=6 groups, k=2 test groups → 6C2 = 15 个组合回测
- Purge: 剔除 label 重叠的样本
- Embargo: 测试集后附加 embargo 期

**为什么不沿用简单 walk-forward**：
1. 简单切分有信息泄露（label overlap）
2. CPCV 提供回测分布（不是单点估计），可计算 SR 的置信区间
3. SR > 2 但 CPCV 分布下 5% 分位 < 0.5 → 过拟合信号

### 3.4 DSR 改进（T07）

**方案**：bootstrap 估计 E[SR_max]

**选择**：
- 对 N 次试验的 SR 序列做 1000 次 bootstrap
- 每次取 max(SR) → 拟合 GEV 分布
- DSR = SR_observed - E[SR_max]

**为什么不沿用简化公式**：
1. 简化公式 `sqrt(2*log(n))` 假设 SR 独立同分布，不成立
2. 实际 SR 间有相关性（同标的、同时段）
3. bootstrap 不需分布假设，更稳健

### 3.5 风控 Guard（T10-T12）

**方案**：新增三大 Guard，复用现有 circuit_breaker 框架

| Guard | 触发条件 | 动作 |
|-------|---------|------|
| 大盘熔断 | 沪深300 单日 < -4% | 降仓至 30% |
| 隔夜跳空 | 标的跳空 > 3% | 跳空标的降仓 50% |
| 全局撤单 | L3 KillSwitch 触发 | 撤所有未成交订单 |

**为什么复用 circuit_breaker**：
1. [circuit_breaker.py](file:///E:/各种PY程序/28-终极量化交易系统8.4/ms_strategy/src/risk/circuit_breaker.py) 4 级阈值框架已就绪
2. 仅需扩展场景，不重写基础设施

## 四、不做什么（重要）

| 不做的事 | 原因 |
|---------|------|
| 重写 191 因子全集 | ROI 低，应配合 Qlib DSL |
| 自研风险总线 | 现有 circuit_breaker 够用 |
| 对标 Citadel Pod | 叙事与能力错位 |
| 引入新框架 | 现有代码 + 严肃工程化已够 |
| 优化策略 α | 95% 失败是工程问题，先达标工程化 |

## 五、验收标准（Phase Gate）

### Phase 1 出口
- [ ] mypy utils/ 退出码 0
- [ ] 因子库 ≥ 20 个 alpha，相关性矩阵 ρ < 0.7
- [ ] SimulatedBroker 接入 Almgren-Chriss
- [ ] CPCV 实现 + 15 个组合回测
- [ ] DSR bootstrap 实现
- [ ] 1000 次 noise injection: SR 标准差 < 0.3
- [ ] 回测 SR ≤ 1.0（>1.0 视为过拟合）
- [ ] 3 年样本外年化 ≥ 沪深300 + 3%

### Phase 2 出口
- [ ] KillSwitch.broker_callback 注册并测试
- [ ] 三大 Guard 实现 + 极端行情 100 次模拟不崩
- [ ] pylint broad-except = error, 0 error
- [ ] pytest 覆盖率 ≥ 80% (CI 强制)
- [ ] Shadow Account 14 天 DSR ≥ 5

### Phase 3 出口
- [ ] 50 万小资金 3 个月实盘
- [ ] live-vs-backtest gap ≤ 3%
- [ ] 月度 attribution 报告
- [ ] 3 个月实盘年化 ≥ 6%

## 六、风险与应对

| 风险 | 概率 | 影响 | 应对 |
|------|------|------|------|
| mypy 560 错误清零耗时超预期 | 高 | Phase 1 延期 | 用 `# type: ignore` 压制 + 后续消除 |
| 20 因子相关性都 > 0.7 | 中 | α 不足 | 改用 WorldQuant Alpha101 因子 |
| CPCV 实现复杂 | 中 | 延期 | 参考 afml 开源实现 |
| 50 万小资金亏损 > 5% | 中 | 推迟扩规模 | 严格执行 KillSwitch |
| 实盘环境与回测差异大 | 高 | gap > 3% | 优先做 attribution 找原因 |
