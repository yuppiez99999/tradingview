---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-11
updated: 2026-08-12
contains: github-integration-strategy, wave6-schedule, sprint-breakdown, wave-coordination, wave7-unified-integration
related:
  - cairn/ROADMAP.md
  - cairn/alpha-factor-system.md
  - cairn/backtest-standards.md
  - cairn/architecture-map.md
  - docs/高价值项目集成排期计划_20260811.md
  - docs/UNIFIED_UPGRADE_PLAN_20260810.md
---

# GitHub 高价值项目集成策略（Wave 6）

> 记录 2026-08-11 调研的 16 个新项目的集成策略、Sprint 排期决策、与现有 Wave 1-5 的协调关系。配套主计划：`docs/高价值项目集成排期计划_20260811.md`。
> 与 `docs/高价值GitHub项目清单_20260809.md`（29 项目基础设施层）互补不重复 — 本文档聚焦 v8.6.14 已有模块的**能力增强**，08-09 文档聚焦**底座搭建**。

## 一、集成策略概述

### 1.1 定位

Wave 6 是 v8.6.14 → v8.7 演进的**能力增强波次**，不引入新的基础设施依赖（vnpy/duckdb/openbb 等由 08-09 计划 Phase A/B/C 负责），而是借鉴 16 个新项目的优秀设计模式，优化已有模块：

| 已有模块 | 借鉴项目 | 增强目标 |
|----------|----------|----------|
| Alpha 因子引擎（11 大类+GTJA191） | factor-mining + alphalens + EigenAlpha | 因子补全 + 评估标准化 + 装饰器注册 |
| AI Hedge Fund（20 分析师） | TradingAgents + virattt/DanisHack | 多空对抗辩论 + 决策记忆反思 |
| G15 事件驱动回测（199 测试） | nautilus_trader + QS-Trader | 确定性事件时钟 + 类型安全 + Rust 加速 POC |
| 回测多场景验证 | vectorbt + SimTradeLab + StatisticalArbitrageEngine + etf-rotation-strategy | 向量化对照 + T+1 模拟 + 配对交易 + ETF 轮动三层验证 |

### 1.2 与 08-09 计划的边界

| 维度 | 08-09 计划（Phase A/B/C） | Wave 6（本计划） |
|------|---------------------------|------------------|
| 项目范围 | 29 个基础设施项目 | 16 个模块精准匹配项目 |
| 接入性质 | 底座搭建（数据/执行/Agent 编排） | 能力增强（已有模块优化） |
| 时间窗口 | 2026-08-09 起，~12 周 | 2026-09-01 ~ 12-31，4 个 Sprint |
| 优先级 | P0（vnpy/duckdb）→ P1（qlib/openbb/情感）→ P2（langchain/freqtrade） | Sprint 1（因子）→ Sprint 2（AI）→ Sprint 3（回测）→ Sprint 4（验证） |
| 协调关系 | Phase A 与 Wave 1-2 并行；Phase B/C 与 Wave 4/5 重叠 | Sprint 1-2 与 Wave 2/4 协调；Sprint 3 与 Wave 5 并行；Sprint 4 收尾 |

**结论**：两批项目互补不重复，协同推进系统从 v8.6.14 → v8.7。

## 二、Sprint 排期决策

### 2.1 排期原则

1. **不干扰核心链路**：09-05 前不动 AI 决策链（Phase B 渐进启用期间）；10-31 前不动实盘执行链路（Wave 4 实盘验证四件套期间）。
2. **与 Wave 4/5 协调**：Wave 4/5 优先，Wave 6 顺延；因子类项目（Sprint 1）错开至 Wave 5 GNN 因子收尾后（11-30）启动因子类增强，但 Sprint 1 启动于 09-01 是因为 Sprint 1 的因子增强与 Wave 5 的 GNN 因子是不同维度（Sprint 1 增强 11 大类已有因子，Wave 5 新增第 12 大类 GNN 因子），不冲突。
3. **串行验收**：4 个 Sprint 严格串行，每 Sprint 独立验收再进下一阶段，避免多项目并行回归。
4. **shadow 优先**：Sprint 2 的 AI 辩论层在 shadow 模式先行 10 天；Sprint 4 的策略类项目影子账户跟踪 ≥3 个月再考虑上线。

### 2.2 关键决策点

- **08-20 观察期决策日**：若 Wave 1 观察期延长至 08-25（Plan C），Sprint 1 启动顺延至 09-05，整体排期后移 4 天。
- **09-05 Phase B 全量启用**：若 Phase B 全量启用后 AI 决策链稳定运行 7 天，Sprint 2 启动；否则 Sprint 2 顺延至 Phase B 稳定后 7 天。
- **10-31 Wave 4 收尾**：若 Wave 4 实盘验证四件套未完成，Sprint 3 顺延至 Wave 4 完成后启动。
- **11-30 Wave 5 收尾**：若 Wave 5 GNN 因子 S6/S7 入库未完成，Sprint 4 的因子类验证项目顺延。

### 2.3 Sprint 排期总表

| Sprint | 时间窗口 | 项目数 | 核心目标 | 与 Wave 协调 |
|--------|----------|--------|----------|--------------|
| Sprint 1 | 09-01 ~ 09-20（~3 周） | 4 | Alpha 因子引擎增强 | 与 Wave 4 因子库增强子任务吸收 |
| Sprint 2 | 09-21 ~ 10-15（~3.5 周） | 3 | AI Hedge Fund 多空辩论增强 | 前置：Phase B 全量启用稳定 7 天；作为 Wave 4 G6 前置 |
| Sprint 3 | 10-16 ~ 11-15（~4.5 周） | 3 | G15 事件驱动回测架构优化 | 与 Wave 5 GNN 因子并行不冲突；与 Wave 4 Rust 重写评估协调 |
| Sprint 4 | 11-16 ~ 12-31（~6.5 周） | 6 | 多场景验证与资源导航 | 与 Wave 5 收尾无冲突；Wave 6 收尾 |

## 三、项目分类映射

### 3.1 Sprint 1：Alpha 因子引擎增强

| 项目 | 接入点 | 借鉴点 | 验证标准 |
|------|--------|--------|----------|
| factor-mining | `utils/alpha_factor/momentum.py` / `reversal.py`（补全） | A 股 14 因子实现（动量/反转/波动/流动性/规模/估值） | S1-S3 门禁通过（IC>0.03/ICIR>0.5/多空夏普>1.0） |
| alphalens | `utils/alpha_factor/evaluator.py`（新建） | IC/ICIR/分层收益/换手率/Tear Sheet 标准化 | 与现有 `calc_ic` 结果一致（偏差 <5%） |
| EigenAlpha | `utils/alpha_factor/library.py`（重构） | 装饰器系统自动计算 + 因子正交化 + Alpha 分解 | 装饰器注册的因子与旧方式行为一致 |
| ml-quant-trading | `utils/alpha_factor/transformer.py`（POC） | PyTorch Transformer 因子编码（探索性） | POC 可行性评估，不强制上线 |

### 3.2 Sprint 2：AI Hedge Fund 多空辩论增强

| 项目 | 接入点 | 借鉴点 | 验证标准 |
|------|--------|--------|----------|
| TradingAgents | `utils/ai_hedge_fund/debate_layer.py`（新建） | 7 层架构 + 多空对抗辩论 + 决策记忆反思 | shadow 模式 ≥10 天，辩论 100% 留痕 |
| virattt/ai-hedge-fund | `utils/ai_hedge_fund/agents/`（增强） | Agent 角色定义模板（巴菲特/芒格等哲学具象化） | 角色定义与现有 20 分析师协调 |
| DanisHack/ai-hedge-fund | `utils/ai_coordinator.py`（增强） | LLM 调用频率控制 + API 容错机制 | 无 429 限流 |

### 3.3 Sprint 3：G15 事件驱动回测架构优化

| 项目 | 接入点 | 借鉴点 | 验证标准 |
|------|--------|--------|----------|
| nautilus_trader | `utils/backtest/event_driven_engine.py`（优化） | 确定性事件时钟 + Rust 加速热点路径 + research-to-live 无缝迁移 | 199 单元测试全绿；回测结果与优化前一致 |
| QS-Trader | `utils/backtest/`（类型安全强化） | 类型注解设计 + secid 感知合约解析 | mypy 类型错误数下降 ≥30% |
| Event_Driven_Backtesting_Framework | 架构参考 | 5 层事件流水线设计（已部分实现于 G15） | 流水线设计与 G15 现有实现对齐 |

### 3.4 Sprint 4：多场景验证与资源导航

| 项目 | 接入点 | 借鉴点 | 验证标准 |
|------|--------|--------|----------|
| vectorbt | `utils/backtest/vectorbt_bridge.py`（新建） | 向量化回测对照 G15 | 简单策略结果偏差 <5% |
| SimTradeLab | `utils/backtest/a_share_rules.py`（新建） | A 股 T+1 交易限制模拟 | 10 个交易日回测无虚假成交 |
| StatisticalArbitrageEngine | `utils/strategy/arbitrage/pairs_trading.py`（新建） | 协整配对筛选 + Walk-Forward 样本外验证 | OOS Sharpe ≥1.0 |
| etf-rotation-strategy | `utils/strategy/etf_rotation/`（新建） | WFO→VEC→BT 三层验证引擎 | 历史数据跑通，输出可审计报告 |
| quantitative_analysis | 评估借鉴 | 自定义因子表达式引擎（白名单校验） | 评估报告，不强制移植 |
| stock (myhhub) | 评估借鉴 | 筹码分布算法 + 形态识别 | 评估报告，不强制移植 |
| awesome-quant | `docs/` 必备书签 | 300+ 开源项目分类索引 | 加入文档导航 |
| awesome-backtesting-python | `docs/` 必备书签 | 2026 年框架对比 | 加入文档导航 |

## 四、与现有 Wave 1-5 的协调关系

### 4.1 时间轴

```
时间轴  08-04 ─── 08-20 ─── 09-05 ─── 10-01 ─── 10-31 ─── 11-30 ─── 12-31
        │        │        │        │        │        │        │
Wave 1  ├─观察期决策─────┤        │        │        │        │
Wave 2  │        ├──Phase B 渐进──┤        │        │        │
Wave 3  ├──代码质量修复────────────┤        │        │        │
Wave 4  │        │        ├──工程化达标+战略升级──────────┤        │
Wave 5  │        │        │        ├──GNN 因子──────────┤        │
Wave 6  │        │        ├──Sprint1─┤──Sprint2─┤──Sprint3──┤──Sprint4────────┤
```

### 4.2 冲突分析与缓解

| Wave | 重叠期 | 冲突点 | 缓解措施 |
|------|--------|--------|----------|
| Wave 1 | 无 | 无 | Sprint 1 启动于 09-01，观察期已结束 |
| Wave 2 | 09-01~09-05（4 天） | Sprint 1 启动期与 Phase B 收尾重叠 | Sprint 1 仅做因子库增强，不动 AI 决策链 |
| Wave 3 | 09-01~09-04（4 天） | Sprint 1 与代码质量修复并行 | Wave 3 是异常处理/类型注解，Wave 6 是新增模块，不冲突 |
| Wave 4 | 09-05~10-31（8 周） | Sprint 1/2/3 与 Wave 4 重叠 | 作为 Wave 4 子任务吸收：Sprint 1 归入因子库增强，Sprint 2 归入 G6 LLM 智能进化前置，Sprint 3 归入 C++/Rust 重写评估 |
| Wave 5 | 10-06~11-30（8 周） | Sprint 3 与 Wave 5 部分重叠 | Sprint 3 的 nautilus_trader 与 Wave 5 的 GAT 不冲突（不同模块）；Sprint 4 的因子类项目错开至 11-30 后 |

## 五、关键决策点

### 5.1 08-20 观察期决策日

- **场景 A（Plan A，按期 Go）**：08-20 观察期满 21 天，Phase B 全量启用 → Sprint 1 按期 09-01 启动。
- **场景 B（Plan B，延长至 08-25）**：08-20 决策延长 5 天 → Sprint 1 顺延至 09-05 启动，整体排期后移 4 天。
- **场景 C（Plan C，10% 资金灰度）**：08-25 决策灰度 → Sprint 1 按期 09-01 启动（因子增强与实盘灰度无关），Sprint 2 前置条件改为"灰度运行稳定 7 天"。

### 5.2 09-05 Phase B 全量启用

- **稳定运行 7 天**：Sprint 2 按期 09-21 启动。
- **未稳定**：Sprint 2 顺延至 Phase B 稳定后 7 天；Sprint 3/4 整体后移。

### 5.3 10-31 Wave 4 收尾

- **实盘验证四件套完成**：Sprint 3 按期 10-16 启动。
- **未完成**：Sprint 3 顺延至 Wave 4 完成后启动；Sprint 4 整体后移。

### 5.4 11-30 Wave 5 收尾

- **GNN 因子 S6/S7 入库完成**：Sprint 4 按期 11-16 启动。
- **未完成**：Sprint 4 的因子类验证项目（W6.4.5）顺延；其他项目按期启动。

## 六、风险登记

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| Sprint 1 因子移植引入前视偏差 | 中 | 高 | 严格遵循 `cairn/backtest-standards.md` §二；S1-S3 门禁拦截 |
| Sprint 2 多 Agent 辩论失控 | 中 | 高 | shadow 模式先行 10 天；`kill_switch`/`fat_finger` 兜底 |
| Sprint 3 架构改动引入回归 | 低 | 高 | 199 单元测试全绿为硬门禁；Rust 加速为可选 POC |
| Sprint 4 策略过拟合 | 中 | 中 | Walk-Forward 样本外验证 + 影子账户 ≥3 个月 |
| LLM 凭证额度耗尽（Sprint 2） | 中 | 中 | 多模型路由降级；借鉴 `cairn/code-review-agent-fallback-20260810.md` |
| 与 Wave 4/5 时间冲突 | 中 | 中 | §4.2 缓解措施；Wave 4/5 优先，Wave 6 顺延 |

## 七、验收标准（每 Sprint 通用）

1. **代码质量门禁**：`.ruff` / `mypy` / `pre-commit`（`forbid-p0-risk`）全绿；`code-review-graph` MCP 复查无 P0/P1 问题。
2. **测试覆盖**：新增模块单元测试覆盖率 ≥80%；现有模块测试无回归（199 单元测试全绿）。
3. **文档沉淀**：每个 Sprint 结束后在 `cairn/LOG.md` 顶部追加一条记录（摘要 + 指针）；结论沉淀为 `cairn/<topic>.md` 知识专题文档。
4. **影子验证**（涉及策略类项目）：shadow 模式先行 ≥10 个交易日（Sprint 2）或 ≥3 个月（Sprint 4 策略类），不影响实盘。
5. **与 Wave 协调**：每 Sprint 启动前确认与 Wave 1-5 的协调关系无冲突；若有冲突则顺延。

## 八、后续方向

> **2026-08-12 更新**：Wave 6 全部提前完成后（原计划 09-01~12-31，实际 08-11~08-12 全部落地，超前 107-141 天），Wave 7 已从"规划中"转为"已设计并启动"，详见 §九。

- **Wave 7（2026-08-13 ~ 12-31，已设计）**：基于 Wave 6 提前完成释放的 107+ 天窗口，整合 Wave 1-5 剩余任务 + 工业级差距 + 工程基础层 + AutoResearch，目标 12-31 v8.7 发布。详见 §九 + `docs/高价值项目集成排期计划_20260811.md` §7。
- **v8.7 发布**：Wave 7 Sprint 4 收尾（12-22~12-31），系统从 v8.6.14 升级到 v8.7，发布说明涵盖 Wave 4/5/6/7 的所有增强。前置条件：门禁三件套连续 21 天 0 FAIL + 影子账户 2 周稳定 + 灰度 100%。
- **Wave 8（2027 Q1，远期）**：C++/Rust 核心路径重写 ROI 评估 + 多策略组合优化 + 十五五规划对齐 + 新一轮 GitHub 项目调研（重点：Rust 生态成熟项目、AI Agent 新框架、A 股市场特有项目）。
- **持续跟踪**：awesome-quant 和 awesome-backtesting-python 作为必备书签，每月扫描一次新项目，符合 v8.7 方向的纳入 Wave 8 候选清单。

---

## 九、Wave 7 统一整合（2026-08-12 新增）

> **定位**：Wave 6 提前完成后，整合 Wave 1-5 剩余任务 + 工业级差距 P1-P3 + 工程基础层 Phase 0-3 + 工具增强（ocr/ECC）+ AutoResearch，统一为 4 Sprint 推进至 12-31 v8.7 发布。
> **主文档**：`docs/高价值项目集成排期计划_20260811.md` §7（统一整合章节）。
> **与既有计划关系**：本 Wave 7 是 UNIFIED_UPGRADE_PLAN_20260810.md（v9.3）的精化与对齐版本，不取代之——v9.3 的 8 Sprint 框架继续作为工程基础层主线，Wave 7 聚焦"剩余任务收口 + v8.7 发布"的整合视角。

### 9.1 Wave 7 整合范围（来源映射）

| 任务 | 来源 | 当前状态 | Wave 7 归属 |
|------|------|---------|------------|
| Wave 2 Phase B 启用 (B1-B4) | ROADMAP Wave 2 | 4 flag 全部待启用 | Sprint 1 |
| daily_workflow.py 拆分第 2-5 轮 | ROADMAP Wave 4 / 工业级差距 P3 | 第 1 轮已完成 (6230→5904), 剩余 4 轮 | Sprint 1/4 |
| R10 残债: 42 处裸 except Exception | 代码审查复审 R10 | 已精确化 (T6 GREEN), 42 处独立债待治 | Sprint 1 |
| Wave 4 Phase 3 (T15-T18) 实盘验证四件套 | ROADMAP Wave 4 | 全部待启动 | Sprint 2 |
| Wave 5 S6 纸交易 | ROADMAP Wave 5 | S5 PASS, S6 待启动 | Sprint 2 |
| Wave 5 S7 小资金 5-10% 灰度 | ROADMAP Wave 5 | 待 S6 通过 | Sprint 3 |
| G9 FeatureStore 物理分层 | OPTIMAL_PLAN G9 | 待启动 | Sprint 3 |
| G11 CVaR 风险计量 | OPTIMAL_PLAN G11 | 待启动 | Sprint 3 |
| Wave 4 G6 LLM 智能进化 Phase D | ROADMAP Wave 4 | 待启动 | Sprint 4 |
| G7 测试覆盖率 80% | OPTIMAL_PLAN G7 | 0.4307, 待提升 | Sprint 1/4 |
| AutoResearch Skill | OPTIMAL_PLAN | 待启动 | Sprint 4 |
| 工程基础层 Phase 0-3 | UNIFIED_UPGRADE_PLAN v9.3 | 待启动 | Sprint 1-4 (穿插) |
| ocr 三步固化 | UNIFIED_UPGRADE_PLAN v9.3 | Step 0 (已用), Step 1-3 待落地 | Sprint 1-3 |
| ECC skills 选择性安装 | UNIFIED_UPGRADE_PLAN v9.3 | 待启动 | Sprint 3 |
| v8.7 发布 | ROADMAP 后续方向 | 待 12-31 | Sprint 4 收尾 |

### 9.2 Wave 7 Sprint 排期总表

| Sprint | 时间窗口 | 周数 | 核心目标 | 与既有 Wave 协调 |
|--------|---------|------|---------|----------------|
| Sprint 1 | 08-13 ~ 09-12 | ~4 | Phase B 启用 + 工作流收尾 + R10 残债 | Wave 2 主线 |
| Sprint 2 | 09-13 ~ 10-12 | ~4 | 实盘验证四件套 + 工程基础层 0-1 | Wave 4 Phase 3 + Wave 5 S6 |
| Sprint 3 | 10-13 ~ 11-12 | ~4 | 因子入库 + 风控增强 + 工程基础层 2 | Wave 5 收尾 + 工程基础层 |
| Sprint 4 | 11-13 ~ 12-31 | ~7 | AutoResearch + LLM 进化 + v8.7 发布 | UNIFIED v9.3 Sprint 7-8 实盘准入 |

### 9.3 Wave 7 关键决策点

- **08-20 观察期决策日**：若 Wave 1 观察期延长至 08-24（已选定 Plan C），Sprint 1 Phase B 启用顺延至 08-25 启动，整体排期后移 5 天。
- **09-12 Sprint 1 收尾**：Phase B 4 flag 全部稳定运行 ≥7 天 + daily_workflow ≤4500 行 + R10 清零，方可进入 Sprint 2。
- **10-12 Sprint 2 收尾**：T15-T18 实盘验证四件套 PASS + QMT 灰度 7 天稳定 + 工程基础层 Phase 0-1 完成，方可进入 Sprint 3。
- **11-12 Sprint 3 收尾**：S7 入库 + FeatureStore 落地 + CVaR 接入 + Prefect/DuckDB 完成，方可进入 Sprint 4。
- **12-31 v8.7 发布**：门禁三件套连续 21 天 0 FAIL + 影子账户 2 周稳定 + 灰度 100% + daily_workflow ≤3000 + 覆盖率 ≥0.80，方可发布 v8.7。

### 9.4 Wave 7 总验收清单（12-31 v8.7 发布前）

- [ ] Phase B 4 flag 全部稳定运行 ≥30 天
- [ ] Wave 4 Phase 3 (T15-T18) 实盘验证四件套全部 PASS
- [ ] Wave 5 CHAIN_MOM_60D S6+S7 完整入库
- [ ] daily_workflow.py ≤3000 行
- [ ] R10 残债 42 处全部清零
- [ ] G7 覆盖率 ≥0.80
- [ ] G9 FeatureStore 物理分层落地
- [ ] G11 CVaR 接入风控六件套
- [ ] G6 LLM 智能进化 Phase D 完成
- [ ] AutoResearch Skill 落地
- [ ] 工程基础层 Phase 0-3 (uv/dotenv/Prefect/DuckDB/LiteLLM) 完成
- [ ] ocr 三步固化 + ECC 8 skills 安装
- [ ] 门禁三件套连续 21 天 0 FAIL
- [ ] 影子账户 2 周稳定 + 灰度 100%
- [ ] v8.7 Release Notes + 文档归档

### 9.5 Wave 7 风险登记（Top 5）

| 风险 | 概率 | 影响 | 归属 Sprint | 缓解 |
|------|------|------|------------|------|
| QMT 实盘接入资金风险 | 高 | 高 | Sprint 2 | paper → 10% → 50% → 100% 渐进 + 风控六件套 |
| AutoResearch 前视偏差 | 高 | 高 | Sprint 4 | S1-S7 门禁 + CPCV/DSR/Noise 三件套 |
| v8.7 发布窗口风险 | 高 | 高 | Sprint 4 | 12-31 硬 deadline; 未达标延期至 2027 Q1, 实盘准入可先于发布 |
| Phase B 启用暴露前视偏差 | 中 | 高 | Sprint 1 | shadow 模式先行 7 天 + kill_switch |
| daily_workflow 拆分回归 | 中 | 高 | Sprint 1/4 | 非交易时段 + DRY-RUN 对照 + 29 单元测试 |
