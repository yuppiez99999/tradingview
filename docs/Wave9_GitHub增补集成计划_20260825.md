# Wave 9 — GitHub 增补项目集成排期计划

> **生成日期**: 2026-08-25
> **排期窗口**: 2026-11-03 ~ 2027-02-28（16 周，4 Sprint）
> **目标**: 基于 2026-08-25 调研的 18 个新增 GitHub 高价值项目，系统性集成至 v8.6，升级至 v8.8
> **上游调研**: 上一轮 GitHub 高价值项目统计（核心引擎/数据源/因子/AI/对冲/工程 6 类）
> **知识沉淀**: `cairn/wave9-gh-integration-20260825.md`
> **与现有计划协调**: 本计划为 Wave 9-GH（GitHub 增补），接 Wave 8-LIT 11-02 收尾后启动，与 Wave 6 Sprint 3-4 并行不冲突

---

## 0. 阅读前提

- 28 系统当前版本 v8.6.14，已具备：11 大类 Alpha 因子 + GTJA191 20 因子 + G15 事件驱动回测 + AI Hedge Fund 20 分析师 + GLM-5 多模型路由 + 五阶段对冲再平衡联动
- 已落地 Wave 1-5 + Wave 6 Sprint 1-2（因子增强 + AI 辩论，08-11 提前完成）；Wave 6 Sprint 3-4 进行中；Wave 7-QC 代码质量；Wave 8-LIT 文献驱动（08-24~11-02）
- **本 Wave 9 与现有 Wave 协调原则**：
  - 去重：已在 Wave 6 Sprint 1-2 完成（alphalens/ai-hedge-fund/EigenAlpha/factor-mining）或 Wave 8-LIT 已排（FinGPT/skfolio/Deep Hedging/FinRL-X/R&D-Agent/AlphaForge）的项目不重复
  - 时间：接 Wave 8-LIT 11-02 收尾后启动，与 Wave 6 Sprint 4（多场景验证）并行不冲突（不同模块）
  - 优先级：无阻塞优先 + 价值递减 + 模块独立性（同 Sprint 内任务可并行）
- 遵循 `AGENTS.md`：结论走 `cairn/`，过程走 claude-mem；改正式文档前先给分析；每个 Sprint 独立验收再进下一阶段
- **铁律**: 每子项独立走 spec-requirement → spec-design → spec-task → 实现 → 验证；所有集成需通过回测验证 + 零行为变更（除明确增强外）

---

## 1. 与已有计划协调关系

| 现有计划 | 本计划关系 | 协调方式 |
|---------|-----------|----------|
| `高价值项目集成排期计划_20260811.md` (Wave 6) | **互补不重复** | Wave 6 Sprint 1-2 已完成（alphalens/ai-hedge-fund），Sprint 3-4 进行中（nautilus_trader/vectorbt）；本计划新增 18 个不同项目 |
| `第三方项目集成总体计划_20260821.md` (S3/S2/A2/A1/S1) | **并行不冲突** | 第三方计划聚焦 `10_第三方项目/` 5 个项目；本计划聚焦 GitHub 公开项目 |
| `系统升级文献调研与排期_20260823.md` (Wave 8-LIT) | **前置依赖** | LIT-S5 11-02 收尾后本计划启动，确保 v8.7 基线就绪 |
| `实盘前工作清单与推进计划_20260824.md` | **无冲突** | 实盘前清单聚焦接线/门控；本计划聚焦能力增强，不动生产链路 |

```
时间轴  09-01 ── 10-31 ── 11-02 ── 12-31 ── 01-31 ── 02-28
        │        │        │        │        │        │
Wave 6  ├──S3 回测──┤──S4 验证──────────┤        │        │
Wave 8  │        ├──LIT-S1~S5──────────┤        │        │
Wave 9  │        │        │        ├──GH-S1──┤──GH-S2──┤──GH-S3──┤──GH-S4──┤
        │        │        │        │        │        │        │
        │        │        │        P0核心   P1对冲   P1工程   P2长期
        │        │        │        +漂移    +绩效    +回测    +评估
```

| Wave | 时间窗口 | 本 Wave 9 关系 |
|------|---------|----------------|
| Wave 6 Sprint 3-4 | 09-01 ~ 12-31 | **并行不冲突** — Wave 6 Sprint 3 优化 `utils/backtest/`，Sprint 4 多场景验证；本计划不同模块 |
| Wave 8-LIT | 08-24 ~ 11-02 | **前置依赖** — LIT-S5 收尾后本计划启动，确保 v8.7 基线 |
| 第三方总体计划 | 08-21 起 | **并行不冲突** — 不同项目集 |

---

## 2. 新增项目清单（18 个，去重后）

### 去重说明

| 已在已有计划覆盖 | 覆盖计划 | 本计划处理 |
|-----------------|----------|-----------|
| alphalens / EigenAlpha / factor-mining / ai-hedge-fund / TradingAgents | Wave 6 Sprint 1-2 已完成 | 不重复 |
| FinGPT / skfolio / Deep Hedging / FinRL-X / R&D-Agent / AlphaForge | Wave 8-LIT 已排 | 不重复 |
| nautilus_trader / vectorbt / QS-Trader | Wave 6 Sprint 3-4 进行中 | 不重复 |
| QLib / vnpy / akshare / pytdx / langgraph / streamlit | 08-09 清单或已使用 | 不重复（已落地） |

### 本计划新增 18 个项目（按 Sprint 分组）

#### Sprint GH-S1：P0 核心引擎 + 模型漂移监控（4 个）

| 项目 | Stars | 中文介绍 | 地址 |
|------|-------|---------|------|
| **FinRL** | 12,000+ | 强化学习量化框架，多智能体组合管理，Meta-Environment | https://github.com/AI4Finance-Foundation/FinRL |
| **evidently** | 6,000+ | ML 模型与数据漂移监控，12 类漂移检测，HTML 报告 | https://github.com/evidentlyai/evidently |
| **optuna** | 10,000+ | 超参优化框架，贝叶斯搜索 + 剪枝，分布式优化 | https://github.com/optuna/optuna |
| **LightGBM** | 17,000+ | 梯度提升框架升级（4.0+），训练速度与精度业界标杆 | https://github.com/microsoft/LightGBM |

#### Sprint GH-S2：P1 对冲 + 绩效分析统一（5 个）

| 项目 | Stars | 中文介绍 | 地址 |
|------|-------|---------|------|
| **QuantLib** | 5,400+ | C++ 期权定价库 Python 绑定，业界标准 | https://github.com/lballabio/QuantLib |
| **pyfolio-reloaded** | 1,500+ | 投资组合绩效与风险分析，Sharpe/Sortino/回撤/贝塔 | https://github.com/stefan-jansen/pyfolio-reloaded |
| **empyrical** | 1,200+ | 风险指标计算库，业界标准实现 | https://github.com/quantopian/empyrical |
| **ARCH** | 1,300+ | 波动率模型库（GARCH/EGARCH/TARCH） | https://github.com/bashtage/arch |
| **py-vollib** | 200+ | 隐含波动率与期权 Greeks 计算库 | https://github.com/vollib/vollib |

#### Sprint GH-S3：P1 工程编排 + 回测增强（5 个）

| 项目 | Stars | 中文介绍 | 地址 |
|------|-------|---------|------|
| **prefect** | 17,000+ | 数据流编排与可观测工作流引擎 | https://github.com/PrefectHQ/prefect |
| **backtrader** | 14,000+ | Python 事件驱动回测框架，支持多资产多策略 | https://github.com/mementum/backtrader |
| **bt** | 2,800+ | 策略组合回测框架，擅长多策略权重分配 | https://github.com/pmorissette/bt |
| **statsmodels** | 10,000+ | 统计建模库（协整/ARIMA/状态空间） | https://github.com/statsmodels/statsmodels |
| **hmmlearn** | 2,800+ | 隐马尔可夫模型，市场状态识别 | https://github.com/hmmlearn/hmmlearn |

#### Sprint GH-S4：P2 长期评估 + 架构参考（4 个 + 收尾）

| 项目 | Stars | 中文介绍 | 地址 |
|------|-------|---------|------|
| **rqalpha** | 5,300+ | 米筐开源量化框架，从数据到实盘完整闭环 | https://github.com/ricequant/rqalpha |
| **zipline-reloaded** | 1,500+ | Quantopian 回测引擎维护版，事件驱动 + Pipeline API | https://github.com/stefan-jansen/zipline-reloaded |
| **zipline-cn** | 300+ | zipline 中国 A 股适配版 | https://github.com/zhangjuewei/zipline-cn |
| **ChatGPT-for-Stock** | 200+ | LLM 股票分析助手，新闻→信号管线 | https://github.com/Yeuoly/ChatGPT-for-Stock |
| **dagster** | 11,000+ | 数据资产编排框架，强类型资产依赖（评估用） | https://github.com/dagster-io/dagster |

---

## 3. 四个 Sprint 详细排期

### Sprint GH-S1：P0 核心引擎 + 模型漂移监控（2026-11-03 ~ 11-30，4 周）

**目标**: FinRL 强化学习决策维度 + evidently 116 模型漂移监控 + optuna 超参优化替换 + LightGBM 4.0 升级

| 任务 ID | 任务 | 交付物 | 验收标准 | 预估工期 | 依赖 | 项目地址 |
|---------|------|--------|----------|----------|------|----------|
| GH-1.1 | FinRL 强化学习决策层集成 | `utils/rl_decision_layer.py` (~300 行) + RL 训练管线 | 20 分析师 + RL 决策维度；回测 Sharpe ≥ 基准；旧接口兼容 | 5d | Wave 6 S2 | https://github.com/AI4Finance-Foundation/FinRL |
| GH-1.2 | evidently 模型漂移监控部署 | `utils/model_drift_monitor.py` + 日报集成 | 116 个 ML 模型漂移检测；触发再训练告警；HTML 报告归档 `reports/drift/` | 3d | 无 | https://github.com/evidentlyai/evidently |
| GH-1.3 | optuna 超参优化替换 | `utils/ml_enhanced_trainer.py` 增强贝叶斯超参 | 训练时间 -30%；IC 提升 ≥ 3%；旧接口保留 | 3d | 无 | https://github.com/optuna/optuna |
| GH-1.4 | LightGBM 4.0+ 升级 | `models/lgb_enhanced/` 版本升级 + 新特性适配 | 训练速度 +20%；零行为变更；单测全过 | 2d | GH-1.3 | https://github.com/microsoft/LightGBM |

**Sprint GH-S1 门禁**:
- ✅ FinRL 集成并通过回测，旧 20 分析师接口兼容
- ✅ evidently 漂移监控上线，116 模型覆盖
- ✅ optuna 替换贝叶斯超参，训练时间 -30%
- ✅ LightGBM 4.0+ 升级，零行为变更
- ✅ 所有改动通过 pre-commit + CI 门禁
- ✅ `cairn/LOG.md` 追加进展条目

---

### Sprint GH-S2：P1 对冲 + 绩效分析统一（2026-12-01 ~ 12-28，4 周）

**目标**: QuantLib 期权 Greeks + pyfolio/empyrical 绩效统一 + ARCH 波动率预测 + py-vollib IV 曲面

| 任务 ID | 任务 | 交付物 | 验收标准 | 预估工期 | 依赖 | 项目地址 |
|---------|------|--------|----------|----------|------|----------|
| GH-2.1 | QuantLib 期权 Greeks 集成 | `utils/option_greeks_ql.py` (~250 行) | Greeks 精度 ±0.001；尾部保护增强；fallback 到 py-vollib | 5d | 无 | https://github.com/lballabio/QuantLib |
| GH-2.2 | pyfolio 绩效分析标准化 | `utils/portfolio_tearsheet.py` | Sharpe/Sortino/回撤/贝塔完整输出；HTML Tear Sheet | 3d | 无 | https://github.com/stefan-jansen/pyfolio-reloaded |
| GH-2.3 | empyrical 风险指标统一 | `utils/hedge_rebalance_backtest.py` 增强指标口径 | 5 策略回测指标一致；业界标准实现 | 2d | GH-2.2 | https://github.com/quantopian/empyrical |
| GH-2.4 | ARCH 波动率模型集成 | `utils/volatility_forecast.py` (~200 行) | GARCH/EGARCH/TARCH；波动率预测 RMSE 优于基准 | 4d | 无 | https://github.com/bashtage/arch |
| GH-2.5 | py-vollib IV 曲面建模 | `utils/hedge_engine.py` IV surface 模块 | IV 拟合误差 < 1%；多对冲工具支持 | 2d | GH-2.1 | https://github.com/vollib/vollib |

**Sprint GH-S2 门禁**:
- ✅ QuantLib Greeks 上线，精度 ±0.001
- ✅ pyfolio + empyrical 绩效分析统一，5 策略指标一致
- ✅ ARCH 波动率预测 RMSE 优于基准
- ✅ py-vollib IV 曲面建模误差 < 1%
- ✅ 旧对冲/优化接口向后兼容

---

### Sprint GH-S3：P1 工程编排 + 回测增强（2027-01-04 ~ 01-31，4 周）

**目标**: prefect 工作流编排 + backtrader/bt 回测增强 + statsmodels 时序建模 + hmmlearn 市场状态识别

| 任务 ID | 任务 | 交付物 | 验收标准 | 预估工期 | 依赖 | 项目地址 |
|---------|------|--------|----------|----------|------|----------|
| GH-3.1 | prefect 工作流编排重构 | `utils/pipeline_orchestrator.py` (~350 行) | 三阶段工作流可观测 + 重试 + SLA；旧管道兼容 | 5d | 无 | https://github.com/PrefectHQ/prefect |
| GH-3.2 | backtrader 回测引擎增强 | `utils/bt_retest_engine.py` (~300 行) | 5 策略回测灵活性增强；多资产多策略支持 | 4d | 无 | https://github.com/mementum/backtrader |
| GH-3.3 | bt 策略组合回测 | `ui/pages/04_投资组合优化.py` 增强 | 5 策略对比增强；组合权重分配逻辑优化 | 3d | GH-3.2 | https://github.com/pmorissette/bt |
| GH-3.4 | statsmodels 时序建模 | `utils/time_series_models.py` (~250 行) | 协整/ARIMA/状态空间；十五五规划时序建模增强 | 3d | 无 | https://github.com/statsmodels/statsmodels |
| GH-3.5 | hmmlearn 市场状态识别 | `utils/regime_hmm.py` (~200 行) | 康波周期阶段切换准确率 ≥ 70%；隐状态可视化 | 3d | 无 | https://github.com/hmmlearn/hmmlearn |

**Sprint GH-S3 门禁**:
- ✅ prefect 编排上线，三阶段工作流可观测
- ✅ backtrader + bt 回测增强，5 策略灵活性提升
- ✅ statsmodels 时序建模增强
- ✅ hmmlearn 市场状态识别准确率 ≥ 70%
- ✅ 旧管道/回测接口向后兼容

---

### Sprint GH-S4：P2 长期评估 + 架构参考 + 收尾（2027-02-01 ~ 02-28，4 周）

**目标**: rqalpha/zipline 架构参考 + ChatGPT-for-Stock 新闻情感 + dagster 选型评估 + v8.8 发布

| 任务 ID | 任务 | 交付物 | 验收标准 | 预估工期 | 依赖 | 项目地址 |
|---------|------|--------|----------|----------|------|----------|
| GH-4.1 | rqalpha 架构参考 | `docs/架构参考/rqalpha_事件循环设计.md` | 不直接接入，参考事件循环架构；输出设计文档 | 2d | 无 | https://github.com/ricequant/rqalpha |
| GH-4.2 | zipline-reloaded Pipeline API 评估 | `docs/架构参考/zipline_pipeline_api_评估.md` | 因子计算表达式 API 评估；选型决策文档 | 2d | 无 | https://github.com/stefan-jansen/zipline-reloaded |
| GH-4.3 | zipline-cn A 股适配参考 | `docs/架构参考/zipline_cn_a股处理.md` | 借鉴 A 股交易日历/停牌处理逻辑 | 2d | 无 | https://github.com/zhangjuewei/zipline-cn |
| GH-4.4 | ChatGPT-for-Stock 新闻情感集成 | `utils/news_sentiment_llm.py` (~200 行) | 新闻情感→信号映射增强；signal_fusion 接入 | 3d | 无 | https://github.com/Yeuoly/ChatGPT-for-Stock |
| GH-4.5 | dagster 选型评估 | `docs/架构参考/prefect_vs_dagster_选型.md` | 与 prefect 对比评估；选型决策（不接入则归档） | 2d | GH-3.1 | https://github.com/dagster-io/dagster |
| GH-4.6 | 全量集成验收 + v8.8 发布 | `CHANGELOG.md` + 版本号更新 | 4 Sprint 全部门禁通过；回测全量绿；文档归档 | 3d | 全部 | — |

**Sprint GH-S4 门禁**:
- ✅ 架构参考文档完成（rqalpha/zipline/dagster）
- ✅ ChatGPT-for-Stock 新闻情感上线
- ✅ 全量回测通过，v8.8 发布
- ✅ `cairn/` 知识专题文档完成沉淀

---

## 4. 资源投入估算

| Sprint | 工时（人天） | 关键模块 | 风险等级 | 价值评级 |
|--------|-------------|----------|----------|----------|
| GH-S1 | ~13 | FinRL + 漂移 + 超参 + LightGBM | 中（RL 训练可能不收敛） | ★★★★★ |
| GH-S2 | ~16 | QuantLib + 绩效 + 波动率 + IV | 中（数值稳定性） | ★★★★☆ |
| GH-S3 | ~18 | prefect + 回测 + 时序 + HMM | 高（管线重构） | ★★★★☆ |
| GH-S4 | ~14 | 架构参考 + 情感 + 收尾 | 低（评估为主） | ★★★☆☆ |
| **合计** | **~61 人天** | **全系统** | — | — |

---

## 5. 全局验收标准

### 5.1 量化指标

| 维度 | 指标 |
|------|------|
| FinRL 决策 | 回测 Sharpe ≥ 基准；RL 训练收敛 |
| 模型漂移 | 116 模型全覆盖；漂移告警触发率可配置 |
| 超参优化 | 训练时间 -30%；IC 提升 ≥ 3% |
| LightGBM | 训练速度 +20%；零行为变更 |
| 期权 Greeks | QuantLib 精度 ±0.001；IV 拟合误差 < 1% |
| 绩效分析 | 5 策略指标一致；业界标准口径 |
| 波动率 | ARCH RMSE 优于基准 |
| 工作流 | 三阶段可观测 + 重试 + SLA |
| 市场状态 | HMM 阶段切换准确率 ≥ 70% |

### 5.2 工程门禁

- 所有改动通过 pre-commit + CI（ruff/mypy/bandit/pytest）
- 覆盖率 ≥ 80%
- P0 文件无裸 print
- 零硬编码绝对路径
- 旧接口向后兼容
- 新增模块 200-400 行（AGENTS.md §5.3）
- 不可变性原则（AGENTS.md §5.1）：新增模块不原地改 df，用 `.assign()`

### 5.3 文档归档

- 每模块集成完成 → `cairn/LOG.md` 追加条目（摘要 + 指针）
- Sprint 完成 → `cairn/wave9-gh-{sprint}.md` 知识专题沉淀
- 全量完成 → `docs/Wave9完成报告_20270228.md`
- 提交规范（AGENTS.md §9）：`feat(integration): GH-1.1 集成 FinRL 强化学习决策层`

---

## 6. 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| FinRL 训练不收敛 | 中 | 高 | 保留旧 20 分析师接口；A/B 并行对比；fallback 到 GLM-5 |
| QuantLib 编译依赖 | 低 | 中 | 用官方预编译 wheel；fallback 到 py-vollib |
| prefect 管线迁移破坏 EOD | 中 | 高 | 渐进式适配；旧管道保留至 GH-S4 验收后；shadow 模式验证 |
| evidently 116 模型扫描耗时 | 中 | 低 | 增量扫描 + 基线缓存；离线全量扫描 |
| ARCH 波动率数值不稳定 | 低 | 中 | 限制模型阶数；fallback 到简单波动率 |
| HMM 隐状态数选择困难 | 中 | 中 | 用 BIC/AIC 自动选择；多组参数对比 |
| 集成膨胀违反 YAGNI | 中 | 中 | 每子项先 spec-requirement 确认真实需求；GH-S4 架构项目仅评估不接入 |
| 与 Wave 8-LIT 延期冲突 | 中 | 高 | LIT-S5 若延期，本计划顺延；保持 11-02 启动节点可调 |

---

## 7. 执行顺序总览

```
Sprint GH-S1（2026-11-03 ~ 11-30，P0 核心）
├── GH-1.2 evidently ──┐
├── GH-1.3 optuna ──────┤── 并行 ──→ GH-1.4 LightGBM ──→ GH-1.1 FinRL
└───────────────────────┘

Sprint GH-S2（2026-12-01 ~ 12-28，P1 对冲+绩效）
├── GH-2.2 pyfolio ──→ GH-2.3 empyrical ──┐
├── GH-2.1 QuantLib ──→ GH-2.5 py-vollib ──┤── 并行 ──→ GH-2.4 ARCH
└───────────────────────────────────────────┘

Sprint GH-S3（2027-01-04 ~ 01-31，P1 工程+回测）
├── GH-3.1 prefect ──┐
├── GH-3.2 backtrader ──→ GH-3.3 bt ──┤── 并行 ──→ GH-3.4 statsmodels ──→ GH-3.5 hmmlearn
└──────────────────────────────────────┘

Sprint GH-S4（2027-02-01 ~ 02-28，P2 评估+收尾）
├── GH-4.1 rqalpha ──┐
├── GH-4.2 zipline ────┤
├── GH-4.3 zipline-cn ─┤── 并行 ──→ GH-4.4 ChatGPT-for-Stock ──→ GH-4.5 dagster ──→ GH-4.6 收尾
└──────────────────────┘
```

---

## 8. 跨阶段约定

- 每子项独立走 spec-requirement → spec-design → spec-task → 实现 → 验证
- 每阶段产出归档到 `docs/集成记录/Wave9/{GH-S1|GH-S2|GH-S3|GH-S4}/`
- 不破坏现有 CI：所有新增先在分支验证，pre-commit 必过
- 不可变性原则（AGENTS.md §5.1）：新增模块不原地改 df，用 `.assign()`
- 文件组织（AGENTS.md §5.3）：新模块 200-400 行，单测同步
- 提交规范（AGENTS.md §9）：`feat(integration): GH-{id} 集成 {项目名}`

---

## 9. 下一步

1. **立即可做**: GH-1.2 evidently 模型漂移监控（零阻塞，价值高）
2. **启动 spec 工作流**: 按 GH-1.2 → GH-1.3 → GH-1.4 → GH-1.1 顺序，每子项走 spec-requirement → spec-design → spec-task
3. **前置确认**: Wave 8-LIT LIT-S5 11-02 收尾进度跟踪；若延期则本计划顺延

---

**生成时间**: 2026-08-25
**版本**: Wave 9-GH（GitHub 增补，18 项目，4 Sprint，16 周，~61 人天）
**目标版本**: v8.6.14 → v8.8
**前置依赖**: Wave 8-LIT 11-02 收尾
**知识沉淀**: `cairn/wave9-gh-integration-20260825.md`
