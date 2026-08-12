# Cairn 知识体系总览与沉淀

> **生成时间**: 2026-08-05
> **知识库**: `e:\各种PY程序\28-终极量化交易系统8.4\cairn\` (26 个文件)
> **系统版本**: v8.6.14
> **目的**: 对 cairn 知识库进行系统性梳理、分类沉淀，形成可检索的知识索引，便于后续开发快速定位和复用已有经验。

---

## 一、知识库全景

cairn 目录是项目的显式知识沉淀库，遵循"结论走 cairn，过程走 claude-mem"的协调原则。共 26 个文件，覆盖 7 大知识域：

| 知识域 | 文件数 | 核心文件 |
|--------|--------|----------|
| 架构与导航 | 3 | architecture-map.md, code-review-graph-guide.md, ROADMAP.md |
| 因子与信号 | 4 | alpha-factor-system.md, sentiment-factor-evolution.md, gnn-supply-chain-factor.md, factor-discovery-loop-engineering.md |
| 回测与数据 | 3 | backtest-standards.md, data-pipeline.md, returns-calibration-standards.md |
| 风控与交易 | 2 | risk-architecture.md, shadow-data-quality-loop.md |
| 模型与进化 | 2 | model-training.md, self-evolution-framework.md |
| 代码质量 | 9 | exception-handling-standards.md, refactoring-standards.md, code-quality-wave3.md, code-quality-review-open-code-review.md, code-review-glm45-llm-scan.md, code-review-agent-fallback-20260810.md, code-review-lessons-v8.4.md, SYSTEM_QUALITY_SCAN_20260803.md, code-review-quality-gate-lessons-20260811.md |
| 工程实践 | 4 | bug_fix_tracker.md, dev-workflow-automation.md, llm-output-quality-standards.md, live-trading-admission-criteria-20260811.md |
| 进展日志 | 2 | LOG.md, gnn-supply-chain-factor-wave5-review.md |
| 其他 | 1 | Cited.md |

---

## 二、架构与导航

### 2.1 三层架构（architecture-map.md）

系统采用严格单向依赖的四层级架构：

```
级 0: utils/ (60+ 模块，无项目内依赖)
  ↑
级 1: lgb_trainer/ ms_strategy/ ai_decision/ realtime_monitor/
  ↑
级 2: reporting/ (依赖 utils + lgb_trainer)
  ↑
级 3: 15_每日工作流/ (编排所有模块)
  ↑
级 4: 顶层入口脚本 (daily_trade_executor, institutional_pipeline_runner, live_scheduler)
```

核心入口点包括：每日交易执行（daily_trade_executor.py）、机构六阶段闭环（institutional_pipeline_runner.py）、盘中并发调度（live_scheduler.py）、晨间/EOD 工作流。

### 2.2 数据流（architecture-map.md + data-pipeline.md）

端到端数据流：外部数据源 → L0 多源采集（五级降级链）→ L1 清洗验证（DataGate 硬门控）→ L2 Alpha 信号（三级降级）→ SignalFusion 10 层融合 → AI 辩论共识 → 订单生成 → L4 执行（TWAP/VWAP）→ L3 回测验证 → L5 风控监控 → 收盘报告。

关键原则："坏数据不交易"——DataGate 在质量评分 <80 时硬阻断整条流水线。

### 2.3 Code-Review-Graph MCP（code-review-graph-guide.md）

项目有知识图谱工具，代码探索应优先使用 graph MCP 而非 Grep/Glob。核心工具包括 detect_changes_tool（变更审查）、get_impact_radius_tool（影响半径）、query_graph_tool（调用链追踪）、semantic_search_nodes_tool（语义搜索）。

---

## 三、因子与信号体系

### 3.1 Alpha 因子体系（alpha-factor-system.md）

11 大类因子体系位于 `utils/alpha_factor/`，GTJA191 精选 20 个技术因子。三级共线解决方案：类内正交化（Level 1）→ 公式重定义（Level 2）→ 跨类正交化后处理（Level 3），12 对完全共线对（|ρ|>0.99）全部消除至 |ρ|<0.65。

因子入库 7 阶段门禁（S1-S7）：假设 → 数据可行性 → 单因子检验（Rank IC >0.03, ICIR >0.5）→ 正交化增量（IC ≥0.01）→ 组合层面（夏普改善 >0.05）→ 纸交易 ≥3 月 → 小资金 5-10% ≥3 月。退役标准：连续 6 月 ICIR <0.2 或连续 3 月多空夏普 <0。

### 3.2 情绪因子演化（sentiment-factor-evolution.md）

v4.0→v4.3 四阶段演化：分级加权词典（v4.0）→ 回看周期 30→250 天（v4.1）→ NaN 原生处理 + 全市场聚合指数（v4.2）→ 移除保护机制正常参与特征选择（v4.3）。核心技术决策包括：多词短语优先匹配、对数衰减防堆砌、-999 哨兵值替代合成填充。

### 3.3 GNN 供应链因子（gnn-supply-chain-factor.md + wave5-review.md）

三层渐进路径：Layer 1 Lead-Lag 因子（非学习版）→ Layer 2 GAT 注意力网络 → Layer 3 入库。关键结论：Layer 1 CHAIN_MOM_60D 反向因子通过 Gate1（effICIR=0.503, 多空夏普 1.766），Layer 2 GAT Gate2 FAIL（+0.039 增益被 B1-B5 修复后证伪，实际 +0.0017/+0.0064，t 值不显著）。

重要教训（GAT 增益证伪）：原报告结论建立在 B1/B2 前视偏差 + B5 注意力掩码错误的有偏评估上。B1-B5 全套修复后用无偏框架重测，增益缩水 6-23 倍。GAT 代码保留为研究资产，不在生产路径。

### 3.4 因子发现 Loop Engineering（factor-discovery-loop-engineering.md）

设计阶段方案，目标从"人工设计因子"升级为"LLM 引导 + 演化搜索 + 机器验证"闭环。参考中金研究框架：581 轮迭代测试 16,939 候选，保留 69 因子，Top 5 夏普 3.14。核心组件：ExpressionTree 表达式树 + 五维演化（变异/交叉/扰动/随机/LLM）+ FSA 频繁子树规避 + 11 项联合过滤 + CheckpointManager 原子写入。

约束：LLM 资源隔离（盘后运行）、V9 基线保护、08-20 前不引入新代码、双签 Feature Flag。

---

## 四、回测与数据标准

### 4.1 回测标准（backtest-standards.md）

核心原则："回测的目标不是找到最高收益的参数，而是无偏估计策略在实盘中的真实表现。"

前视偏差防范清单：财报按实际披露日（非报告期截止日）、滚动标准化（非全样本）、至少滞后一期调仓。幸存者偏差：逐日成分股快照 + 退市股保留到最后交易日。停牌/涨跌停：停牌冻结、涨跌停禁成交。

Purged K-Fold（v8.5）：训练/验证折叠间设 purge gap 消除时间序列泄漏。影子账户三阶段：10% → 50% → 100%，偏差 <30% 才上线。Almgren-Chriss 冲击成本：永久冲击 + 临时冲击 + 闭式最优轨迹。Walk-Forward：滚动训练/测试窗口，样本外仅用一次。

回测报告 8 项必查清单：样本外占比 ≥30%、前后段 CAGR 对比、月度 IC 序列、参数敏感性、净收益扣成本、分段分析、容量评估、影子偏离度。

### 4.2 数据管道（data-pipeline.md）

六阶段状态机：IDLE → DATA_CLEANING → ALPHA_GENERATION → BACKTEST_GATE → EXECUTION → RISK_MONITOR → COMPLETED。每阶段 fail-closed 回退。

五级降级链：Wind MCP → iFinD → TDX → AKShare → 新浪。全部失败抛 RuntimeError，不返回假数据。CrossSourceValidator 多源交叉校验：偏差 >2% 标记不一致。

L1 清洗四步：多源验证 → 异常检测（Z-score 3.0 + IQR 1.5x + MAD 3.0）→ 缺失填充 → DataGate 门控。质量评分 0-100，<80 硬阻断。

### 4.3 年化收益校准（returns-calibration-standards.md）

核心参数：MAX_ANNUALIZED=+200%、MIN_ANNUALIZED=-99%、BAYESIAN_PRIOR=15%、SHRINK_THRESHOLD=±50%、SAMPLE_PERIOD_THRESHOLD=2 年。

短周期贝叶斯收缩：样本期 <2 年且 |年化|>50% 时触发，向 A 股长期均值 15% 回归。收缩强度 = max(0, min(0.7, 1-years/2.0))。触发背景：300308 中际旭创 +483.6% 年化异常暴露。

---

## 五、风控与交易架构

### 5.1 风控架构（risk-architecture.md）

四 Guard 联动（EOD 强制执行、不可跳过）：

Guard 1 回撤守卫：L0 正常(<10%) → L1 预警(10-15%) → L2 减仓(15-20%) → L3 熔断(>20%)。
Guard 2 波动率守卫：目标年化 12%，vol_scale <0.83 强制缩仓。
Guard 3 对冲守卫：120 日滚动 Beta，IF 期货对冲，回撤每加深 5% 追加 1 手空单。
Guard 4 认沽守卫：OTM 5% Put 覆盖 6 大 ETF，到期前 5 天滚仓。

Kill Switch 熔断：日亏损 >5%、连续 5 笔亏损暂停 10 分钟、撤单率 >80% 暂停、保证金 >80%、任一 Guard L3 触发审查。

v8.5 增强模块：VegaMonitor（期权波动率暴露）、LiquidityMonitor（盘口深度/枯竭检测）、EVTTailRisk（GPD 尾部 VaR，需 ≥120 天 Shadow 数据）、FactorDecayMonitor（IC 衰减检测）。

四层防御：事前（微秒级订单检查）→ 事中（毫秒级实时监控）→ 事后（日频归因复盘）→ 极端情景（历史十大压力测试 + 蒙特卡洛 2000 路径 99% CVaR）。

### 5.2 Shadow 数据质量闭环（shadow-data-quality-loop.md）

三脚本协同：clean_shadow_returns.py（数据清洗，标记 real/backtest/fixed/missing）→ integrate_cleaned_to_drift.py（漂移告警，仅用 real 记录）→ observation_watchdog.py（14 天双重门槛看门狗）。

核心问题：回测回填数据污染（6 条中仅 1 条真实）+ 小样本 PSI 误判（3+3 采样 PSI=4.37 是统计噪音）。解决方案：数据清洗标记 4 类质量 + 14 天双重门槛 + 幂等告警（同日替换非追加）+ 断档检测（连续 ≥2 交易日无新数据则告警）。

---

## 六、模型训练与自我进化

### 6.1 模型训练（model-training.md）

六阶段训练管道：OHLCV 加载（12h parquet 缓存）→ 特征工程（35+ 技术因子 + 扩展特征 + 截面因子 + 新闻情绪）→ Purged K-Fold TSCV + 特征选择 → LightGBM 训练（GPU→CPU 回退，200 轮早停）→ 持久化 + 信号生成 → 报告。

V9 Regime-Specific 双模型：bull_model（仅 bull 样本）+ non_bull_model（bear+choppy+rebound）+ full_model（fallback）。四态 Regime 基于 510300 MA60 + 斜率。绩效：年化 19.62%、回撤 9.95%、Sharpe 1.315。

退役机制：连续 5 日 IC<0.02 减半 → 连续 10 日 IC<0 禁用 → 连续 20 日 IC<0 强制退役。重训触发：定时（7 天）+ 漂移触发 + 手动。MLOps 管道：Train → Register → Drift Monitor → A/B Test（20% 流量 14 天）→ Promote/Rollback。

### 6.2 自我进化框架（self-evolution-framework.md）

核心闭环：每日数据 → DriftMonitor 漂移检测 → StrategyEvaluator 评分 → AutoRetrainScheduler 重训 → FeedbackLoop 回测验证 → Feature Flag 灰度 → Shadow 验证 → 全量上线。

Phase 进度：Phase 0 观察期（运行中，24/29 任务 83% 完成）→ Phase 1 DriftMonitor（核心完成）→ Phase 2 AutoRetrain（部分）→ Phase 3 AutoFactorFactory（完成）→ Phase 4 FeedbackLoop + 金融工程内核（部分）→ Phase 4.5 工程加固（进行中）。

Feature Flag 三层保护：Layer 1 代码开关（默认 False）→ Layer 2 只读对照模式 → Layer 3 灰度发布（10%→50%→100%）。

08-20 关键决策日三项评估：Shadow 样本 ≥20 条（当前 8 条不足）、VolRegime Regime 识别可靠、Public/Private 分离健康。

VolRegimeWeighter：bull/neutral/bear/crisis 四档（VIX <20/20-30/30-40/≥40），输出 8 类风格权重建议。Phase 0 只读模式，306 周期 bull 稳定。

---

## 七、代码质量体系

### 7.1 异常处理规约（exception-handling-standards.md）

核心原则：禁止裸 `except Exception`，按调用场景选择具体异常类型，每处附注释说明。fail-safe 行为必须保留（原有返回值/日志/熔断反馈不变）。

7 种场景的异常类型选择：配置加载（ImportError/OSError/ValueError/TypeError/AttributeError/RuntimeError）、JSONL 加载（行级 JSONDecodeError 单独 continue + 文件级错误外层捕获）、LLM Provider（RuntimeError/ConnectionError/TimeoutError/ValueError/KeyError）、importlib（加 SyntaxError）、外部插件（最宽集合但单条失败不影响其他）、pandas/numpy（ValueError/KeyError 为主）、Loader Protocol。

门禁：ruff BLE001 检测裸 except Exception，已修复的 29 处用 # noqa: BLE001 标注。

### 7.2 重构规约（refactoring-standards.md）

核心原则：零行为变更是底线（重构前后 bit-for-bit 一致）、不在交易时间做、优先选择结构清晰的函数。

决策树：同构分支（做同一件事的不同变体，输出结构一致）→ 表驱动化（配置表 + 查表）；异构检查（独立检查块，输出结构不同）→ 提取 helper。

反模式：强行对异构检查链表驱动化（改变 checks dict key 结构 = behavior change）、表驱动化时丢失默认值处理（`adv or 1_000_000`）、提取 helper 时改变 mutable state 顺序、无测试覆盖就重构。

三轴问题阈值：函数 >80 行、CC >15、参数 >5。Strong（三项超标）已于 2026-08-03 清零。

### 7.3 代码审查经验（code-review-lessons-v8.4.md）

Top 3 教训：

1. 回测前视偏差是"静默杀手"——不崩溃、不报错、甚至看起来结果很好，但回测虚高、实盘无法复现。铁律：决策时点 T 只用 ≤T 已披露数据、财务按披露日、因子值与目标收益严格错开、无法保证 point-in-time 时 fail-closed。

2. fail-open 熔断 = 宣称有保护实际没保护——风控只记日志不执行。铁律：风控返回决策必须被调用方消费、失败默认 fail-closed、两套引擎熔断行为一致。

3. 硬编码凭证/过期合约是"定时炸弹"——不报错但 git 提交或实盘调用就出事。铁律：凭证永不硬编码、合约到期日校验、fallback 数据带时间戳。

修复中的深层发现：价格数据缺日期轴（回测管道设计时就应自带时间轴）、财报披露日规则（Q4 年报 ≤次年4/30，很多人误认为已可用）、因子 IC 依赖数据形态而非公式、合约月份码解析坑（YYMM 非 YYYYMM，期权短码 YMM 需 as_of 推断，子串污染需先长后短）、qfq vs hfq 取舍（历史用 hfq 可复现，实时用未复权，绝不混用）。

### 7.4 Wave 3 代码质量（code-quality-wave3.md + bug_fix_tracker.md + SYSTEM_QUALITY_SCAN）

修复进度：878 处宽泛异常清零（ai_decision 29 + utils 849）、ms_strategy 131 处清零、scripts 105 处 + research 78 处（Round 5b 待处理）、TYPE_IGNORE + SYS_PATH 核心清零、PRINT 清零 + ruff BLE001 门禁。

系统扫描结果：P0 安全风险 4 处（1 真实 pickle.load 已三重保护 + 3 误报）、P1 代码质量 551 处、P2 代码风格 1642 处、超长函数 74 处、高复杂度 42 处。总体：无未受控 P0 风险。

### 7.5 LLM 驱动审查（code-review-glm45-llm-scan.md）
- GLM 4.5-air 全量撒网 + 二次过滤方法论；模型名须精确匹配资源包（`glm-4.5-air` 非 `glm-4.5`）；扫描须 `--exclude` 非代码文件；严重度 ≠ 真缺陷，critical 100% 现场验证。

### 7.6 Agent 直接审查兜底（code-review-agent-fallback-20260810.md）
- **触发**: 所有外部 LLM 凭证额度归零（GLM 429 / DeepSeek 402）时，ocr 整批失败 → 由 Agent 会话内直接读源码审查，零额度依赖。
- **流程**: 定位批次清单 → 大文件分块读 + search_content 定位核心区 → 静态审查（执行闭环/异常保护/废弃 API/键匹配/原子写）→ 交叉验证（读 positions.json 确认键格式）→ 产出 Markdown 缺陷表。
- **对比**: LLM 扫描覆盖全、误报高(需二次过滤)；Agent 审查零额度、误报低、需人工定位核心区。两者互补：有额度用 LLM 撒网，无额度用 Agent 兜底。
- **核心教训**: 额度是 LLM 审查硬阻塞，先 `ocr llm test` 验额度；缺陷严重度须读源码交叉验证；废弃 API（如 `datetime.utcnow()`）跨文件通病应一次性替换 + ruff 规则防回归。

### 7.7 open-code-review 审查（code-quality-review-open-code-review.md）

6 个已确认缺陷：EOD 工作流 log() 参数错误（TypeError 掩盖真实错误）、5 个 _mock_* 死代码、缓存 TTL .seconds 回绕、主营构成缓存类型不匹配、data_layer 降级链重复委托（保留不改）、RLock 包裹网络 IO（已修复锁收窄）。

审查方法论：先审交易执行（错一个直接亏钱）→ 再审风控熔断（fail-open 最隐蔽）→ 再审回测数据（前视/幸存者偏差静默失真）→ 最后审基础层（崩溃/泄露）。优先精确而非召回，关键 bug 必须亲自读源码验证。

---

## 八、工程实践

### 8.1 开发工作流自动化（dev-workflow-automation.md）

Claude Code Hooks 四个事件：PreToolUse（拦截 P0 安全 + P1 静默吞异常）、PostToolUse（双保险复查）、SessionStart（自动加载 LOG.md 最近 3 条 + ROADMAP 焦点）、Stop（校验代码修改后是否更新 LOG.md）。

cairn 与 claude-mem 协调：结论走 cairn（决策/架构/踩坑），过程走 claude-mem（交互过程/临时上下文）。cairn 是单一事实源。

### 8.2 LLM 输出质量（llm-output-quality-standards.md）

核心原则：LLM 输出是下游程序的输入数据（不是给人看的报告），必须可被程序解析，禁止描述性输出。

质量分层防御：Prompt 层（第 1 道，反描述化禁令前置 + 关键词约束）→ 输出解析层（第 2 道，正则匹配 + 关键词检测 + 空结果降级）。触发背景：ai_recommendations 字段仅存 6 条描述性文字，操作建议被硬截断丢弃。

### 8.3 Bug 修复追踪（bug_fix_tracker.md）

v2.3 进度：Round 1-5a 全部完成（EVAL/EXEC 清除 → 真实除零 7→0 → ai_decision 29→0 → utils 849→0 → ms_strategy 131→0）。Round 5b（scripts 105 + research 78）+ Phase 3（TYPE_IGNORE + SYS_PATH）+ Phase 4（PRINT 清零 + 门禁增强）后续推进。

---

## 九、关键踩坑记录索引

| 踩坑 | 文件 | 核心教训 |
|------|------|----------|
| GAT 增益证伪 | gnn-supply-chain-factor.md | 前视偏差 + 掩码错误导致增益虚高 6-23 倍，无偏验证后 Gate2 FAIL |
| 前视偏差隐藏形态 | code-review-lessons-v8.4.md | 价格数据缺日期轴、财报披露日规则、IC 依赖数据形态 |
| fail-open 熔断 | code-review-lessons-v8.4.md | 风控只记日志不执行 = 宣称有保护实际没保护 |
| 合约月份码解析 | code-review-lessons-v8.4.md | YYMM 非 YYYYMM，期权短码 YMM 需 as_of 推断，子串污染先长后短 |
| qfq vs hfq 混用 | code-review-lessons-v8.4.md | 历史用 hfq 可复现，实时用未复权，同一计算绝不混用 |
| 空 handler 假成功 | code-review-lessons-v8.4.md | 废弃功能必须显式抛错，非静默返回成功 |
| Shadow 数据污染 | shadow-data-quality-loop.md | 回测回填数据导致漂移告警失真，小样本 PSI 误判 |
| PowerShell $_ 转义 | exception-handling-standards.md | 改用 Python 脚本替代 PowerShell 管道 |
| Python 3.8 不支持 PEP 585 | refactoring-standards.md | 用 typing.List 替代 list[str] 或 from __future__ import annotations |
| 扫描器误报率极高 | refactoring-standards.md | 除零扫描误报 99.3%，扫描器做粗筛人工做精核 |
| 缓存 TTL .seconds 回绕 | code-quality-review-open-code-review.md | 跨天回绕导致过期缓存误命中，用 .total_seconds() |
| RLock 包裹网络 IO | code-quality-review-open-code-review.md | 锁收窄，仅在写共享状态时加锁 |

---

## 十、ROADMAP 关键节点

| 里程碑 | 日期 | 状态 | 说明 |
|--------|------|------|------|
| v8.6.14 | 08-05 | 已完成 | 因子库 GTJA191 对标 + 代码质量加固 |
| 代码审查修复闭环 | 08-05 | 已完成 | 25 项问题 P0/P1/P2/LOW 全部修复 |
| U1-U5 升级 | 08-05 | 已完成 | 时序 IC + 涨跌停 + 复权 + E2E（160 测试） |
| VolRegime 实盘集成 | 08-05 | 已完成 | 306 周期 bull 稳定 |
| 08-20 决策日 | 08-20 | 待决策 | Shadow 样本 ≥20 + VolRegime Phase 1 评估 + Public/Private 分离 |
| Phase B 渐进启用 | 08-20→09-05 | 未启动 | B1 DriftMonitor → B2 FeedbackLoop → B3 AutoRetrain → B4 MLOps |
| 诚实回测三件套 | 09-05→10-31 | 未启动 | CPCV + DSR + Noise |
| GNN S5-S7 | 10-06→11-30 | S5 通过 | CHAIN_MOM_60D 入库流程 |
| 因子发现 Loop MVP | 08-20 后 | 未启动 | ≥100 候选 ≥1 因子入库 |
| C++/Rust 重写 | 待立项 | 未启动 | 超低延迟行情解码/订单生成/风控 |
| PTP 硬件时钟 | 待采购 | 未启动 | ¥360K-710K |
| 十五五对齐 | 2026-12-31 | 未启动 | 第一阶段检查 |

---

## 十一、知识复用指南

### 11.1 新开发者快速入门路径

1. 读 `architecture-map.md` 理解系统骨架和依赖关系
2. 读 `data-pipeline.md` 理解数据如何流动
3. 读 `risk-architecture.md` 理解风控四层防御
4. 读 `self-evolution-framework.md` 理解当前 Phase 0 观察期
5. 读 `ROADMAP.md` 理解后续升级方向
6. 用 code-review-graph MCP 探索具体代码

### 11.2 常见任务的知识索引

| 任务 | 首选文档 | 补充文档 |
|------|----------|----------|
| 新增因子 | alpha-factor-system.md | backtest-standards.md, factor-discovery-loop-engineering.md |
| 修改风控逻辑 | risk-architecture.md | code-review-lessons-v8.4.md |
| 修改回测代码 | backtest-standards.md | code-review-lessons-v8.4.md, returns-calibration-standards.md |
| 修改数据管道 | data-pipeline.md | shadow-data-quality-loop.md |
| 修改模型训练 | model-training.md | self-evolution-framework.md |
| 代码重构 | refactoring-standards.md | exception-handling-standards.md |
| 代码审查 | code-review-lessons-v8.4.md | code-quality-review-open-code-review.md |
| 修复异常处理 | exception-handling-standards.md | bug_fix_tracker.md |
| LLM 集成 | llm-output-quality-standards.md | model-training.md |
| 新增 Feature Flag | self-evolution-framework.md | dev-workflow-automation.md |

### 11.3 知识更新规则

遵循 dev-workflow-automation.md 的协调原则：

结论走 cairn：决策结论/架构选择/踩坑教训 → 写入对应专题文档 + LOG.md 追加变更指针。
过程走 claude-mem：上次修到哪/临时调试思路/会话级偏好 → 自动留存。
原位更新：cairn 文档的内容直接在原文件更新，不新建重复文件。
LOG.md 追加：每次实质性推进后在 LOG.md 顶部追加重构条目（含降幅/测试结果/模式）。

---

## 十二、文档完整性评估

| 文档 | 完整性 | 时效性 | 建议更新 |
|------|--------|--------|----------|
| architecture-map.md | 完整 | 08-02 | 补充 VolRegimeWeighter 模块 |
| alpha-factor-system.md | 完整 | 08-02 | 补充 20 个 GTJA191 因子 IC 验证结果 |
| backtest-standards.md | 完整 | 08-02 | 补充 U6 诚实回测三件套进展 |
| risk-architecture.md | 完整 | 08-02 | 补充期权对冲实盘验证结果 |
| self-evolution-framework.md | 完整 | 08-05 | 最新，持续更新观察期进度 |
| data-pipeline.md | 完整 | 08-02 | 补充 U2 涨跌停/U3 复权因子接入 |
| model-training.md | 完整 | 08-02 | 补充 V9 全量上线时间表 |
| code-review-lessons-v8.4.md | 完整 | 08-05 | 最新，审查修复闭环已完成 |
| exception-handling-standards.md | 完整 | 08-03 | 补充 utils/ 849 处清零结果 |
| refactoring-standards.md | 完整 | 08-03 | 无需更新 |
| ROADMAP.md | 完整 | 08-05 | 最新，Wave 3.5 U1-U7 已追加 |
| LOG.md | 完整 | 08-05 | 持续追加 |

---

## 修订记录

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-08-05 | v1.0 | 初版：系统性梳理 cairn 26 个知识文件，形成 12 章知识沉淀总览 |
