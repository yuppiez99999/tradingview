---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-19
updated: 2026-08-19
contains: recommended-reading, book-mapping, theory-coverage, enhancement-backlog
related:
  - cairn/self-evolution-framework.md
  - cairn/ROADMAP.md
  - cairn/test-health-20260819.md
---

# 推荐书目：对系统有工程指导意义的中外书籍

> 基于 28 系统 v8.6.14 核心领域（A 股量化、多因子+LightGBM、回测/过拟合防护、自我进化、风控、对冲）筛选的书籍清单。每本书标注与系统现有模块的映射关系和覆盖状态。**结论：系统已覆盖这些书籍的核心方法，书籍中的更多技术属于增强方向而非缺口，不影响现有 ROADMAP 排期。**

## 一、覆盖状态定义

| 标记 | 含义 |
|------|------|
| ✅ 已覆盖 | 系统已实现该书核心方法 |
| 🔄 部分覆盖 | 系统实现了部分，书中还有更多可借鉴 |
| ⬜ 未覆盖 | 系统尚未实现，但当前非紧急缺口 |
| 📖 理论依据 | 提供理论指导而非具体实现 |

## 二、金融机器学习 / Alpha 研究

| 书名 | 作者 | 与系统映射 | 覆盖状态 | 说明 |
|------|------|-----------|---------|------|
| Advances in Financial Machine Learning | López de Prado | Purged K-Fold / DSR / CPCV / Noise Injection | 🔄 部分覆盖 | 系统已实现 Purged K-Fold + DSR + CPCV + Noise（W6.6.2 T06-T08 DONE）；**未覆盖**：Triple-Barrier Labeling、Meta-Labeling、MDI/MDA 特征重要性、Combinatorial Purged CV 的更多变体 |
| Machine Learning for Asset Managers | López de Prado | 聚类聚类 / 特征重要性 / 回测过拟合量化 | 🔄 部分覆盖 | 系统有 DriftMonitor + StrategyEvaluator；**未覆盖**：层次聚类 Dendrogram、MDI（Mean Decrease Impurity）、MDA（Mean Decrease Accuracy） |
| Finding Alphas | Igor Tulchinsky (WorldQuant) | Alpha 构造方法论 → AutoFactorFactory | 🔄 部分覆盖 | 系统有 AutoFactorFactory（Phase 3 DONE）+ Expression Engine（W6.6.1）；**未覆盖**：WorldQuant 的 Alpha 表达式 DSL 更丰富、Alpha 组合的正交化流程 |
| The Elements of Statistical Learning | Hastie/Tibshirani/Friedman | 统计学习理论基石 → LightGBM 调参 | 📖 理论依据 | 指导 LightGBM 正则化/早停/学习率调参的理论基础 |

## 三、多因子 / 投资组合

| 书名 | 作者 | 与系统映射 | 覆盖状态 | 说明 |
|------|------|-----------|---------|------|
| Active Portfolio Management | Grinold & Kahn | IR 基本定律、Alpha 覆盖/转移/时机 | 🔄 部分覆盖 | 系统有 StrategyEvaluator composite_score + IC/ICIR 评估（U1 DONE）；**未覆盖**：IR 基本定律的"覆盖/转移/时机"三维分解、Active Risk 预算 |
| 因子投资：方法与实践 | 石川/刘源/刀熊（中文） | A 股因子构建/IC/IR/中性化 | 🔄 部分覆盖 | 系统有 20 个 GTJA191 因子 + 14 大类 + IC/ICIR 评估；**未覆盖**：因子正交化、Barra 风险模型全流程、因子衰减的系统性监控 |
| 机器学习与资产定价 | 石川（中文） | ML 因子 → LightGBM 增强训练 | 🔄 部分覆盖 | 系统有 LightGBM 增强训练器 + V9 Regime-Specific 双模型；**未覆盖**：更多 ML 因子构造方法（如 XGBoost 因子、Transformer 因子的系统性评估） |
| Robust Portfolio Optimization | Fabozzi/Kolm/Pachamanova | 鲁棒优化 → BL + MVSK | 🔄 部分覆盖 | 系统有 BL+MVSK(378) 联合优化（P4 DONE，生产就绪）；**未覆盖**：更丰富的鲁棒优化框架（Worst-Case Optimization、Distributionally Robust） |

## 四、控制论 / 系统科学

| 书名 | 作者 | 与系统映射 | 覆盖状态 | 说明 |
|------|------|-----------|---------|------|
| Cybernetics | Norbert Wiener | 控制论创始 → 负反馈调节 | 📖 理论依据 | 系统自我进化框架 §八已沉淀控制论映射；Wiener 的原始理论是负反馈调节的数学基础 |
| Design for a Brain / Introduction to Cybernetics | W. Ross Ashby | 超稳定系统 / 稳态切换 | 📖 理论依据 | 系统有 VolRegimeWeighter 四档 Regime（bull/neutral/bear/crisis）；Ashby 的超稳定系统理论可指导 §八.3"Lyapunov 稳定性度量"实现 |
| Thinking in Systems | Donella Meadows | 系统杠杆点 / 反馈回路 | 📖 理论依据 | 指导自我进化闭环设计；12 个杠杆点可对照检查框架的进化效率 |
| 复杂 | 梅拉妮·米歇尔（中文） | 复杂适应系统 / 涌现 | 📖 理论依据 | 策略进化宏观行为的理论框架；CA（Cellular Automata）思想可指导因子空间的探索 |

## 五、回测 / 策略评估

| 书名 | 作者 | 与系统映射 | 覆盖状态 | 说明 |
|------|------|-----------|---------|------|
| The Evaluation and Optimization of Trading Strategies | Robert Pardo | Walk-Forward / 优化框架 | ✅ 已覆盖 | 系统有 FeedbackLoop 四道闸门（Walk-Forward + DSR + 压力场景 + CRO Gate）+ W6.4.4 ETF 轮动 WFO 网格搜索 |
| Advances in Active Portfolio Management | Sorensen/Chen/Li/Hua | 最新 IR / Alpha 实践 | ⬜ 未覆盖 | 可作为 StrategyEvaluator 迭代的参考，但当前 composite_score 已满足需求 |

## 六、风险 / 衍生品

| 书名 | 作者 | 与系统映射 | 覆盖状态 | 说明 |
|------|------|-----------|---------|------|
| Quantitative Risk Management | McNeil/Frey/Embrechts | 极值理论 / Copula / VaR | 🔄 部分覆盖 | 系统有 CVaR + EVT 肥尾建模（W7.3.3 G11 DONE）；**未覆盖**：Copula 多变量依赖建模、EVT 的 POT（Peaks Over Threshold）完整流程 |
| Dynamic Hedging | Nassim Taleb | 动态对冲 / Greeks / 尾部风险 | 🔄 部分覆盖 | 系统有 hedge_engine + Greeks 监控面板 + Black-Scholes 内核（T4.2 DONE）；**未覆盖**：Taleb 的非线性 Greeks（Vanna/Volga）、尾部风险对冲的更多策略 |
| Options, Futures, and Other Derivatives | John Hull | 衍生品定价 → Black-Scholes | ✅ 已覆盖 | 系统有 `utils/fineng/pricing/black_scholes.py` 统一内核（T4.2 DONE） |

## 七、A 股特色 / 行为金融

| 书名 | 作者 | 与系统映射 | 覆盖状态 | 说明 |
|------|------|-----------|---------|------|
| 量化投资—以 Python 为工具 | 蔡立耑（中文） | A 股量化 Python 实现 | ✅ 已覆盖 | 系统数据采集/回测框架已远超该书范围 |
| 行为金融学 | 饶育蕾/汪丁丁（中文） | 市场异象 / 情绪因子 | 🔄 部分覆盖 | 系统有情绪因子 v4.3 + ETF 资金流监控 + 舆情信号；**未覆盖**：更多行为异象的系统性因子化（如处置效应、锚定效应） |

## 八、已沉淀书籍

| 书名 | 沉淀位置 | 沉淀日期 |
|------|---------|---------|
| 控制论与科学方法论（金观涛） | `cairn/self-evolution-framework.md` §八 | 2026-08-19 |

## 九、后续可增强方向（不影响现有 ROADMAP 排期）

> 以下方向来自书籍中系统**未覆盖**的方法，标注为后续增强候选，**不新增到现有 Wave 7/9 排期**（已满 08-13~12-31）。待 v8.7 发布后或 Wave 9 收尾后评估优先级。

| 方向 | 来源书籍 | 系统映射模块 | 优先级 | 触发条件 |
|------|---------|-------------|--------|---------|
| Triple-Barrier Labeling | López de Prado | AutoResearch Skill / 因子验证 | 中 | v8.7 发布后，AutoResearch 需要更丰富的标签方案 |
| Meta-Labeling（二级分类器） | López de Prado | SignalFusion / 策略组合 | 中 | 信号融合层需要减少假阳性 |
| MDI/MDA 特征重要性 | López de Prado | LightGBM 训练器 | 低 | 当前 LightGBM 自带 feature_importance 已满足 |
| IR 三维分解（覆盖/转移/时机） | Grinold & Kahn | StrategyEvaluator | 中 | 策略评估需要更精细的归因 |
| 因子正交化流程 | 石川 | AlphaFactorLibrary | 中 | 因子库扩展到 50+ 时需要降维 |
| Barra 风险模型全流程 | 石川 | risk_budget_optimizer | 低 | 当前 CVaR + EVT 已覆盖尾部风险 |
| Copula 多变量依赖 | McNeil | risk/cvar.py | 低 | 多资产联合尾部风险建模需要时 |
| Lyapunov 稳定性度量 | Ashby | self-evolution-framework §八.3 | 中 | 自我进化框架 Phase 1-2 启用时 |
| 非线性 Greeks（Vanna/Volga） | Taleb | hedge_engine / Greeks 监控 | 低 | 期权对冲精度需要提升时 |
| 行为异象因子化 | 饶育蕾 | alpha_factor / 情绪因子 | 低 | 情绪因子 v5.0 迭代时 |

## 十、与系统计划的关系

**结论：书籍推荐不需要修改现有 ROADMAP 排期。**

原因：
1. **核心方法已覆盖**：Purged K-Fold / DSR / CPCV / Noise / CVaR / EVT / Walk-Forward / Black-Scholes / IR / IC/ICIR 均已实现
2. **未覆盖部分属增强而非缺口**：Triple-Barrier Labeling、Meta-Labeling、IR 分解等是现有方法的扩展，不影响系统正确性
3. **理论依据已沉淀**：控制论映射在 `self-evolution-framework.md` §八，后续方向在 §八.3
4. **排期已满**：Wave 7（08-13~12-31）+ Wave 9（09-02~12-15）已覆盖 12-31 v8.7 发布前全部窗口，不宜新增大任务
5. **增强方向归入后续候选**：§九 的 10 个增强方向标注触发条件，待 v8.7 发布后或 Wave 9 收尾后评估

**唯一需关注的计划交叉点**：
- self-evolution-framework.md §八.3 "Lyapunov 稳定性度量" — 已在自我进化框架中记录为后续方向，Ashby 的理论可指导实现，但**不改变现有 Phase 0-4 排期**（08-24 观察期决策 → Phase B 启用 → ... 的流程不变）

## 十一、关联文件

| 文件 | 职责 |
|------|------|
| `cairn/self-evolution-framework.md` §八 | 控制论概念映射（已沉淀） |
| `cairn/ROADMAP.md` | 系统路线图（Wave 1-7 + Wave 9） |
| `cairn/recommended-reading-20260819.md` | 本文档 — 推荐书目与覆盖状态 |

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [经典理论覆盖度审计 — 2026-08-19](classic-theory-coverage-20260819.md) (相似度 21%)
- [自我进化框架](self-evolution-framework.md) (相似度 19%)
- [哲学与交易系统：哲学思想对量化交易的工程指导](philosophy-trading-mapping-20260819.md) (相似度 15%)
- [情绪因子演化：v4.0 → v4.3](sentiment-factor-evolution.md) (相似度 9%)
- [P0 经典理论四件套实现 — 2026-08-23](p0-classic-theory-impl-20260823.md) (相似度 9%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
