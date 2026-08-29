---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-11
contains: wave6-prep, sprint1-design, decorator-pattern, factor-transplant
related:
  - cairn/github-integration-wave6.md
  - cairn/alpha-factor-system.md
---

# Wave 6 启动前置研究笔记（2026-08-11）

> 记录 Wave 6 四个 Sprint 的前置技术调研结论、现有模块摸底结果、设计决策与映射方案。
> 配套文件：`docs/高价值项目集成排期计划_20260811.md`

## 一、现有系统结构摸底结论

### 1.1 utils/alpha_factor/（Sprint 1 接入点）

| 模块 | 职责 | 现状 |
|------|------|------|
| `base.py` | 因子数据结构 + 预处理（去极值/中性化/标准化） | FactorValue 数据类 + winsorize/standardize/neutralize 函数完备 |
| `library.py` | 12 大类因子聚合入口 | AlphaFactorLibrary.compute_all() 统一调度，调用各分类 compute_xxx_factors 函数 |
| `price_volume.py` | 动量/低波/规模/流动性 4 大类 33 因子 | MOM/VOL/SIZE/LIQ 前缀命名，正交化解耦设计已建立 |
| `technical.py` | GTJA191 量价因子（精选 20 个） | 双实现回退：vibe adapter → ms_strategy 纯 Python |
| `fundamental.py` | 估值/成长/质量/杠杆/营运 5 大类 50 因子 | 财务数据驱动，依赖 fundamentals dict |
| `graph.py` | Lead-Lag 图因子（GNN Layer 1） | 5 个因子，CHAIN_MOM_60D 通过 S1-S5 门禁 |

### 1.2 quant_modules/ai_hedge_fund/（Sprint 2 接入点）

现有架构：LangGraph 工作流 + 20 位分析师 Agent（巴菲特/芒格/达利欧等人物具象化）。
编排流程：start → 分析师并行 → 风控经理 → 组合经理 → 对冲分析师 → END

接入点映射：
- `orchestrator.py` create_workflow() — 在"分析师并行"后插入**多空辩论层**
- `agents/*.py` — Bull Agent / Bear Agent 新增辩论角色
- `graph/state.py` — 扩展 AgentState 支持辩论历史

### 1.3 utils/backtest/（Sprint 3 接入点）

G15 事件驱动引擎 8 模块：
- event_driven_engine.py（主引擎，事件循环 next-event 模型）
- matching_engine.py（撮合引擎）
- latency_model.py（延迟模型，FixedLatency）
- order_queue.py（订单队列）
- adapters.py（Strategy 适配）
- constraints.py（约束规则）
- result_converter.py（结果转换）

借鉴 nautilus_trader 切入点：
1. 确定性事件时钟（当前 T+1 撮合已是 next-event，但缺乏纳秒级时间戳）
2. LatencyModel 扩充实战场景（网络延迟+滑点模型）
3. 撮合引擎冷热路径分离（热点用 Rust 重写评估 ROI）

## 二、Sprint 1 技术方案（Alpha 因子引擎增强）

### 2.1 W6.1.1 factor-mining 14 因子移植方案

factor-mining 14 因子 → 映射到现有 `price_volume.py` + `fundamental.py` 分类：

| factor-mining 因子 | 类别 | 映射方式 |
|---------------------|------|----------|
| ret_1d | 动量 | 已有 MOM_REVERSAL_5D 对应 1 日收益，直接复用 |
| momentum_5d | 动量 | 已有 MOM_20D 的 5 日窗口变种 |
| momentum_20d | 动量 | 已有 MOM_20D |
| momentum_12m_1m | 动量 | 已有 MOM_12_1M（12-1月剔除最近一月） |
| reversal_5d | 反转 | 已有 MOM_REVERSAL_5D |
| reversal_20d | 反转 | 已有 MOM_REVERSAL_20D（时序分位数版本） |
| volatility_20d | 波动 | 已有 VOL_20D |
| idiosyncratic_vol | 波动 | 已有 VOL_IDIO（特质波动） |
| amihud_illiq | 流动性 | 已有 LIQ_AMIHUD |
| turnover_rate | 流动性 | 已有 LIQ_TURNOVER（需确认，无则新增） |
| log_market_cap | 规模 | 已有 SIZE_LOG_MCAP |
| circulating_market_cap | 规模 | SIZE_CIRC_MCAP（需新增，区别 LOG_MCAP） |
| ep (EP 倒数) | 估值 | fundamental.py 已有 EP (earning/price) |
| bp (BP 倒数) | 估值 | fundamental.py 已有 BP (book/price) |

**差异点处理（新增因子）**：
1. `FM_RET_1D` — factor-mining 的 ret_1d 是纯 1 日收益率（`p[t]/p[t-1] - 1`），区别于 MOM_REVERSAL_5D（负数表示反转）
2. `FM_IDIO_VOL` — factor-mining 的特质波动率计算方式（对市场回归残差）与现有 VOL_IDIO（基准回归）不同
3. `FM_AMIHUD` — Amihud 非流动性 = mean(|ret| / amount)，非 LIQ_AMIHUD（= |ret| / volume）
4. `FM_CIRC_MCAP` — 流通市值对数（区别于总市值 LOG_MCAP）

### 2.2 W6.1.2 alphalens 评估标准化方案

现有 `base.py` 中 evaluate_factors() 计算 IC （当前为单点 forward return），alphalens 增强点：

1. **时序 IC 序列**：不只是单点 IC，而是按历史交易日逐日计算 IC，统计 IC 均值/标准差
2. **分层收益分析**：将股票按因子值分 5 档，计算每档平均收益，验证单调性
3. **换手率分析**：因子组合的每期换手率，评估交易成本
4. **Tear Sheet**：HTML 报告输出（与 factor-mining 的 report.py 整合）

### 2.3 W6.1.3 EigenAlpha 装饰器方案

参考 EigenAlpha strategy.py 的核心实现：

```python
# 装饰器系统伪代码
_FACTOR_REGISTRY: dict[str, callable] = {}

def register_factor(category: str):
    def deco(fn):
        sig inspect(fn)  # 记录参数名
        _FACTOR_REGISTRY[fn.__name__] = (fn, category)
        return fn
    return deco

# 然后 compute_all 时根据 registry 自动调用
```

现有 library.py 的重构路径：
1. 在 `base.py` 新增 `@register_factor(category="momentum")` 装饰器
2. 旧的 compute_xxx_factors 函数保持不变，装饰器可选启用
3. 双重注册模式：装饰器注册的因子 + 旧方式注册的因子统一汇总
4. 向后兼容：所有旧调用点不改，AlphaFactorLibrary 行为一致

### 2.4 W6.1.4 ml-quant-trading Transformer POC

探索性项目，评估后决定是否正式立项：
- 数据维度：OHLCV × 60 天 → Transformer Encoder → 因子向量
- 可行性前提：PyTorch 已在 Wave 5 GNN 中引入（gat_factor_torch.py），依赖无新增
- 输出文件：`utils/alpha_factor/transformer.py`（POC 阶段只读不写生产数据）

## 三、Sprint 2-4 技术映射速览

### 3.1 Sprint 2：AI Hedge Fund 多空辩论层

现有架构（LangGraph）：
- 分析师层 → 风控经理 → 组合经理 → END（对冲分析师作为可选）

TradingAgents 增强方向：
1. 新增 Bull/Bear Debate Layer（在分析师并行后、风控前插入）
2. 多轮结构化辩论：Bull 提出做多证据链 → Bear 反驳 → Bull 再反驳 → Bear 总结
3. 决策记忆反思（memory_reflection.py）：历史决策 PnL 对比后修正推理偏差

### 3.2 Sprint 3：G15 确定性事件时钟

当前事件模型：T 提交订单 → T+1 撮合（FixedLatency(0)）
借鉴 nautilus_trader：
1. 事件时间戳统一纳秒级（避免浮点数）
2. 引入确定性事件时钟：事件ID单调递增，所有组件共享同一个时间源
3. 事件顺序保证：MarketData → Strategy → Portfolio → Execution → Fill，无乱序

### 3.3 Sprint 4：多场景验证

- vectorbt 向量化回测对照 G15 事件驱动（简单策略偏差 <5%）
- A 股 T+1 规则：SimTradeLab 的持仓限制模拟
- StatisticalArbitrageEngine 的协整配对筛选 + Walk-Forward

## 四、与现有门禁系统的衔接

所有新增代码须通过：
- `.ruff`（T01 格式）
- `mypy`（类型安全，Sprint 3 强化）
- `pre-commit forbid-p0-risk`
- 现有 199 单元测试全绿

## 五、前置准备清单

- [x] factor-mining 因子清单与现有因子映射表完成
- [x] EigenAlpha 装饰器模式样本获取（strategy.py @register_factor）
- [x] G15 回测引擎结构（event_driven_engine.py / matching_engine.py）摸底
- [x] AI Hedge Fund LangGraph 架构摸底
- [ ] 下一步：Sprint 1 因子移植与装饰器实现

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [GitHub 高价值项目集成策略（Wave 6）](github-integration-wave6.md) (相似度 22%)
- [nautilus_trader 架构研究报告 — Wave 6 Sprint 3 W6.3.1](nautilus-trader-study.md) (相似度 20%)
- [Alpha 因子体系](alpha-factor-system.md) (相似度 16%)
- [GNN 供应链产业链因子落地设计](gnn-supply-chain-factor.md) (相似度 10%)
- [P0 经典理论四件套实现 — 2026-08-23](p0-classic-theory-impl-20260823.md) (相似度 9%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
