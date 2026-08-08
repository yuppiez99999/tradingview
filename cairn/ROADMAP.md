---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-02
updated: 2026-08-03
related:
  - cairn/gnn-supply-chain-factor.md
---

# 终极量化交易系统 v8.6.14 路线图

**当前焦点**：Wave 1 自我进化收尾 — T4.2/T4.7 ✅ DONE；W1.3a/b/c (真实数据接入) 全部 ✅ DONE；W1.4 (08-13 决策材料) ✅ DONE — 推荐选项 B 延长观察期至 08-20；Wave 3 Round 5/6 ✅ ALL DONE；Wave 3 第三阶段 (TYPE_IGNORE + SYS_PATH 核心清零) ✅ DONE 2026-08-04；**代码审查修复闭环 ✅ DONE 2026-08-05** (25 项问题 P0/P1/P2/LOW 全部修复, 见 `docs/WORK_REPORT_2026-08-05_代码审查修复闭环.md`)；下一步 DQC Phase 1 启动 或 **本次派生升级点 U1-U7** (见 `docs/SELF_UPGRADE_PLAN_2026-08-05.md`) — 时间窗口 08-04~08-20。**Wave 5 (GNN 因子) 2026-08-03 重大更新**: B1-B5 全套修复后 Gate1 PASS (CHAIN_MOM_60D 反向因子 effICIR=0.503/多空夏普1.766) + Gate2 FAIL (+0.039 增益证伪, 实际+0.0017/+0.0064 t值0.15/0.76不显著) — 决策: Layer 2 回退, GAT 代码保留为研究资产不在生产路径, Layer 1 CHAIN_MOM_60D 推进 S5-S7 入库流程 (S1-S4 已通过, S5 待执行回测, S6/S7 长期任务); 排期时间不变 (10-06~11-30), 与自我进化 Wave 1/2 (09-05 前收尾) 无时间冲突, 仅与 Wave 4 后半 (10-06~10-31) 部分重叠 — W5.2 验证避开 Wave 4 实盘验证窗口

## 里程碑

- [x] v8.6.14 因子库 GTJA191 对标 + 代码质量加固
- [x] **ai_decision/ 异常处理全清零 (2026-08-03)** — 29 处 `except Exception` 全部替换为具体异常类型
- [x] **utils/ 全目录异常处理全清零 (2026-08-03)** — 849 处 `except Exception` / 273 文件全部替换为具体异常类型，零语法错误，fail-safe 行为全部保留。累计清除宽泛异常 878 处 (ai_decision 29 + utils 849)
- [x] v8.6.13 气象因子引擎（7 因子体系、apizero.cn → Open-Meteo 降级链、WeatherAgent 第 6 位专家 11% 权重、14 标的地理坐标映射）
- [x] v8.5 软件层面升级（环境隔离管理器、数据管道、影子账户验证、PTP时间同步、Vega监控、Purged K-Fold、流动性监控、EVT肥尾建模、因子衰减监控）
- [x] 情绪因子 v4.0→v4.3 演化（NaN原生处理 → 全市场聚合 → 移除保护机制，正常参与特征选择）
- [x] LightGBM 增强训练器（真实OHLCV + 自适应重训 + 放宽早停）
- [x] V9 Regime-Specific LGB 双模型（年化 19.62% / 最大回撤 9.95% / Sharpe 1.315，影子账户 10%→50%→100% 灰度发布）
- [x] 自我进化框架（24/29 任务，83% 完成率；Phase 0-4 推进；T0.4/T0.5/T4.2 观察期或收尾中；T4.7 验收报告待补；观察期 2026-07-23 → 08-13）
- [ ] C++/Rust 核心路径重写（超低延迟行情解码、订单生成、风控检查）
- [ ] PTP 硬件时钟采购与部署（¥360K-710K 预算）
- [ ] 多策略组合优化（跨信号协方差矩阵 + 动态风险预算分配）
- [ ] 十五五规划对齐（2026-2030 五年分阶段管理，2030-12-31 强制清仓）

## 后续升级计划（2026-08-04 制定，四波推进）

### Wave 1：观察期决策 + 自我进化收尾（08-04 ~ 08-20，~12 天, 因样本不足延长）
- [x] W1.1 T4.7 收尾 — ✅ DONE 2026-08-04 — `docs/自我进化框架/FINENG_ACCEPTANCE_REPORT.md` 已补写, 3/4 模块通过 WF 闸门
- [x] W1.2 T4.2 收尾 — ✅ DONE 2026-08-04 — `utils/theta_engine.py` 三处调用点全部切换到 `utils/fineng/pricing/black_scholes.py` 统一内核
- [x] W1.3a (08-04~08-06) G1 真实数据接入沙箱化 — **Day 1+2+3 DONE 2026-08-03**: Day 1 核心实现 (750 行 + 77 测试 + 93.95%) + Day 2 cross_validate + feed_history 并行化与缓存 (108 测试 + 94.12%) + Day 3 集成 EOD 工作流 + EvolutionEval 兜底 + 修复 daily_workflow.py 缺失根因 + 端到端验证 PASS (真实行情 daily_return=+1.246%). 下一步: W1.3b DriftMonitor 真实数据回填
- [x] W1.3b (08-07~08-10) DriftMonitor 真实数据回填 — ✅ **DONE 2026-08-03 (提前完成)**: DriftShadowIntegrator 骨架 (626行) + 46 测试 100% 通过 + 真实 daily_returns 端到端集成 PASS (5/5日成功) + PSI 校准骨架 (降级, 待 B1) + EOD 阶段四点七集成 + EvolutionEval 兜底 + CLI 入口 + 单日/历史回填端到端验证 PASS. 验收 8/9 通过, 3 项降级待真实 V9 panel
- [x] W1.3c (08-11~08-12) StrategyEvaluator 真实评分 — ✅ **DONE 2026-08-03 (提前完成)**: 3/3 验证 PASS (Public/Private 分离 + Flag 透传 + 只读行为). public=0.0 vs private=0.4716, reward_hacking_risk=0.0, pit_violations=0. 样本 5 条不足, 距 20 条差 15 天
- [x] W1.4 (08-10~08-13) 08-13 决策材料 — ✅ **DONE 2026-08-03**: `docs/自我进化框架/OBSERVATION_PERIOD_DECISION.md` v1.2 完成. 推荐选项 B 延长观察期至 08-20 (样本不足为硬阻塞, 机制本身已验证健康)
- [x] **决策规则触发**：08-13 时 Shadow 真实样本预计 14 条 <20, **已选定延长观察期至 08-20**, 不做条件性 Go

### Wave 2：Phase B 渐进启用（08-20 决策通过后 → 09-05, 因决策日延后顺延）
- [ ] B1 (08-20→23) `USE_DRIFT_DETECTOR=true` 仅告警
- [ ] B2 (08-23→26) `USE_FEEDBACK_LOOP` 自动接入
- [ ] B3 (08-26→29) `USE_AUTO_RETRAIN=true` + 降级护栏
- [ ] B4 (08-29→09-05) `USE_MLOPS_PIPELINE=true` 完整外层循环

### Wave 3：代码质量持续修复（08-04 ~ 09-04，与主线并行）
- [x] Round 5a — `ms_strategy/` 宽泛异常清零 — ✅ **DONE 2026-08-03 (验证)**: 实际 0 处 `except Exception` (前序会话已清零 131 处). 158 个 except 子句全部使用具体类型 (ValueError/KeyError/RuntimeError/TypeError/AttributeError/OSError/TimeoutError/ConnectionError/ImportError). ROADMAP 原记 "122处 TODO" 为过时信息
- [x] Round 5b — `scripts/` 宽泛异常清零 — ✅ **DONE 2026-08-03**: AST 扫描确认 0 处真实 except Exception (3 处为 _fix_*.py 正则字符串字面量误报). 修复 2 处裸 except: `_install_all_ml.py:14` → `(ValueError, TypeError)`, `_install_scipy.py:64` → `OSError`
- [x] Round 6 — `research/` 宽泛异常清零 — ✅ **DONE 2026-08-03 (验证)**: research/ 自身 (90 文件) 实际 0 处 `except Exception` (前序会话已清零). 具体类型使用得当 (ValueError 133 / TypeError 132 / OSError 127 / KeyError 126 等). research/references/ (第三方 1290 文件) 中 4 处 `except BaseException` 为 Vibe-Trading 有意为之 (线程异常传递 / 原子写入安全 / 异步桥接), 不应修改. ROADMAP 原记 "78处 TODO" 为过时信息
- [x] 第三阶段 — TYPE_IGNORE + SYS_PATH 核心清零 — ✅ **DONE 2026-08-04**: SYS_PATH 扩展 `setup_sys_path()` 注入 v8.3 根/src/utils 四路径, 20 个根目录入口脚本统一调用; TYPE_IGNORE 项目业务代码裸注释清零 (剩余 5 处全在 qlib_env 第三方库), 带错误码注释 300+ 处; 零语法错误, 运行时 import 验证通过 (autolearn_trainer + utils 包 + bridges 模块)
- [x] 第四阶段 — PRINT 清零 + ruff/flake8 BLE001 门禁增强 — ✅ **DONE 2026-08-04**: ruff.toml 启用 T (flake8-print) + BLE (flake8-blind-except) 规则族; 根目录 top 5 + utils/ + v8.3 src/ 共 734 个 print→logger 转换 (info/warning/error 级别自适应); 302 个 except Exception 加 # noqa: BLE001 (标注 fail-safe 待后续精确化); 2 处 (ImportError, Exception) 精确化为 (ImportError, ValueError, TypeError/RuntimeError); per-file-ignores 豁免 scripts/cli/tools/tests/research/ms_strategy 脚本目录; DQC Phase 2 同步完成 (P3 检查点 + F 维度 + X 维度)
- [ ] GAP-2 E2E 补齐 — full_pipeline + shadow_account_lifecycle

### Wave 4：工程化达标 + 战略升级（09-05 ~ 10-31）
- [ ] **daily_workflow.py 拆分（6226→≤3000 行）** — 长期架构重构，按 phase 切分（`DailyWorkflow` 拆为门面 + `workflow/phases/*.py`），零行为变更，仅周末执行。详见 `cairn/daily-workflow-split-plan.md`
- [ ] T02/T03 — pylint broad-except 升级 error + 覆盖率基线
- [ ] T06-T08 — CPCV/DSR/Noise 残差注入（诚实回测三件套）
- [ ] Phase 2 (T09-T14) — 不崩风控六件套
- [ ] Phase 3 (T15-T18) — 实盘验证四件套
- [ ] G6 LLM 智能进化 — Phase D 策略 Ideation 生成
- [ ] C++/Rust 重写 ROI 评估

### Wave 3.5：代码审查派生升级（2026-08-05 新增，与 Wave 3 并行）
> 源自 2026-08-05 全库代码审查修复闭环派生的升级点，补强**回测可信度**与**资金安全**主线。详见 `docs/SELF_UPGRADE_PLAN_2026-08-05.md`。
- [ ] U1 (P0, 08-05~08-15) 完整时序 IC/ICIR — 基于因子历史序列逐日计算，复用 gate1_validation.calc_ic_series；消除单点 forward return 近似
- [ ] U4 (P1, 立即) DeepSeek API Key 平台轮换收尾 — 需用户到 platform.deepseek.com 重生成 Key 填入 .env（P1-1 已将泄露 Key 替换为占位符）
- [ ] U2 (P1, 08-08~08-20) 回测涨跌停/停牌数据接入 — data_provider 下载 limit_up/down/suspended 字段注入 day_data，激活 P2-2 约束
- [ ] U3 (P1, 08-10~08-25) 复权因子支持 — akshare hfq-factor 获取复权因子，建立 hfq历史↔未复权实时映射，除权日精确对齐
- [ ] U5 (P1, 与 Wave 3 并行) GAP-2 E2E 补齐 — full_pipeline + shadow_account_lifecycle

### Wave 5：GNN 供应链产业链因子（10-06 ~ 11-30，新增 2026-08-03）
> 基于已有 `supply_chain_graph.py`（关系图谱数据层）+ `alpha_factor/`（11 大类因子）的三层渐进落地。核心原则：先用非学习版 Lead-Lag 因子验证邻居信息增量（Gate 1），通过后再引入 GAT 深度学习。详见 `cairn/gnn-supply-chain-factor.md`。
> **2026-08-03 前置进展**：W5.1 + W5.2 实际已提前执行（数据管线打通 + Gate 1 实测 FAIL），非排期内的闲置等待。
- [x] W5.1 (10-06~10-15) Layer 1 设计实现 — Lead-Lag 因子 + 接入因子库 + 边数据源实测探活 — ✅ **前置 DONE 2026-08-03**: `graph_data_source.py` (东财push2 slist/get spt=3 行业/概念边) + `supply_chain_builder.py` (图桥接) + `graph.py` (5 LeadLag 因子, 第12大类) + `gate1_validation.py` (真实数据验证模块). 腾讯K线 74/74 只真实拉取, 图 74节点/649边.
- [ ] W5.2 (10-16~10-24) Layer 1 验证门禁 — S1-S4 检验（增量 IC ≥0.01 + 多空夏普>1.0）— ⚠️ **已前置执行但 FAIL 2026-08-03**: Gate 1 判定 FAIL (CHAIN_CONCENTRATION IC均值0.0293/ICIR0.156 持续正向但ICIR<0.3; 其余因子跨窗不稳定; 多空夏普<1.0). 诊断为 universe 74只过小 + 行业/概念边非真实供应商-客户边. 详见 §5.1g.
- [x] W5.2b (10-16~10-24) **Gate 1 改进迭代** — 扩 universe + 引入真实供应商-客户边 + 因子方向权重优化 + 延长回看窗口; 二次验证 — ✅ **前置执行 2026-08-03** (universe 250只/图5757边/10窗ICIR, CONCENTRATION IC=0.019). 详见 §5.1g2.
- [x] W5.2c (10-16~10-24) **Gate 1 三次验证 — 主营构成边增强** — ⚠️ **前置执行但 FAIL 2026-08-03**: 免费源实测巨潮 0 条, 东财 F10 主营构成边 (图 5757→7328边), CHAIN_CONCENTRATION **ICIR 0.150→0.272 ↑** (IC=0.0285 三次稳定为正) 但仍<0.3, 多空 0.807<1.0. 诊断: 静态边权重已充分挖掘, 提示需 GAT 条件化注意力. 详见 §5.1g3.
- [x] W5.3 (10-25~11-03) Layer 2 GAT 实现 — torch 依赖引入 + 康波宏观条件化注意力 — ⚠️ **前置执行但 Gate2 FAIL 2026-08-03**: 纯 numpy GAT 瓶颈(数值梯度失效) → 安装 torch 2.13.0 + `gat_factor_torch.py` (多头GATLayer+MSE+Adam)。原报 +0.039 增益被 B5 掩码错误 + B1/B2 前视偏差虚高。**B1-B5 全套修复后无偏验证: +0.039 证伪** (实际 +0.0017/+0.0064, t值0.15/0.76 不显著), Gate2 FAIL。详见 §5.1h + 踩坑「GAT 增益证伪」。
- [x] W5.4 (11-04~11-10) Layer 2 对比验证 — GAT vs Layer 1 基线增量检验 — ⚠️ **前置执行但 Gate2 FAIL 2026-08-03**: B1-B5 修复后两次无偏验证 GAT effIC 均略弱于静态, 微弱 effICIR/夏普优势为 softmax 平滑副产品非学习能力。**决策: 按 Gate2 闸门回退 Layer 1**, GAT 代码保留为研究资产不在生产路径。康波条件化不适配日频(慢变量). 详见 §5.1h2 + 踩坑「GAT 增益证伪」。
- [ ] W5.5 (11-11~11-20) Layer 3 入库 — S5-S7 门禁（组合层面/纸交易/小资金）— 🔄 **S1-S5 全通过, 推进 S6-S7**: S1-S4 ✅ PASS (effIC=0.094/effICIR=0.503/多空夏普1.766); **S5 ✅ PASS** (`s5_validation.py` 基准夏普-1.5069→增强+0.2879, 边际改善+1.7948>>0.05); S6 纸交易≥3月 ⏳ 待启动; S7 小资金5-10%≥3月 ⏳ 待S6通过. CHAIN_MOM_60D 已在 library.py 注册 (enable_graph=True 默认)
- [ ] W5.6 (11-21~11-30) 集成与监控 — AI Hedge Fund 接入 + FactorDecayMonitor 纳入

> **Wave 5 阶段性结论更新（2026-08-03 B1-B5 修复后）**：数据管线完整 + Layer 1 Gate1 PASS (CHAIN_MOM_60D 反向因子) + Layer 2 Gate2 FAIL (+0.039 证伪, 回退 Layer 1)。当前推进 CHAIN_MOM_60D S5-S7 入库。阶段性总结见 `cairn/gnn-supply-chain-factor-wave5-review.md`（含证伪更正注记）。

**关键决策点（更新 2026-08-03 B1-B5 修复后）**：Layer 2 GAT **Gate2 FAIL** — B1-B5 全套无偏验证后 +0.039 增益被证伪（实际 +0.0017/+0.0064，t 值 0.15/0.76 不显著），GAT effIC 略弱于静态，微弱优势为 softmax 平滑副产品。**决策：按 Gate2 闸门回退 Layer 1**。GAT 代码保留为研究资产不在生产路径，未来重启条件：① 真实供应商-客户边（商业源）② 历史 1000+ 天 ③ 转向特征提取器方向。Layer 1 CHAIN_MOM_60D 已通过 Gate1（B3/B4 修复后 effICIR=0.503/多空夏普1.766），推进 S5-S7 入库。康波条件化不适配日频（慢变量）。真实供应商-客户边在免费源不可得（巨潮 0 条），主营构成边已是最佳免费近似。

**与自我进化系统排期协调（2026-08-03 评估）**：
- **无时间冲突**：自我进化 Wave 1/2 在 09-05 前全部收尾，早于 Wave 5 启动（10-06）。
- **与 Wave 4 部分重叠（10-06~10-31）**：Wave 4 实盘验证四件套与 W5.2 因子门禁验证共用回测环境 → W5.2 因子验证错开至 10 月中下旬，Wave 4 实盘验证优先排前。
- **排期性质**：Wave 5 为独立因子研究方向，与 Wave 1/2 自我进化主体流程并行不冲突。

## 开放问题

1. 情绪因子数据源质量 — 当前新闻数据覆盖率和时效性不足，需要更高质量的中文财经新闻源或替代情绪指标
2. 策略容量天花板 — 日频选股策略当前规模下容量充足，若扩大规模需重新评估冲击成本模型
3. C++/Rust 重写的投入产出比 — 硬件采购 + 核心路径重写预算 ¥360K-710K，需评估 vs. 当前 Python 方案的延迟是否已构成实质瓶颈
4. V9 全量上线时间表 — 影子账户跟踪达标后是否按原计划推进 50%→100% 灰度
5. 自我进化框架观察期 — 2026-08-13 观察期满日是关键决策点：Phase 0 出口是否达成、Public/Private 分离性是否健康、DriftMonitor 是否误报。若三项均通过则解除 Feature Flag 只读限制并启动 Stage B 渐进启用。Phase 4 收尾两项（T4.2 theta_engine.py 切换到统一期权定价内核、T4.7 fineng 影子验证验收报告）需独立排期
6. **daily_workflow.py 6226 行拆分** — 超架构门禁（≤3000 行），属长期重构非紧急。排期于 Wave 4 或独立周末窗口，禁止交易时段执行。计划见 `cairn/daily-workflow-split-plan.md`
7. **独立审查报告须二次核验 + 勘误登记** — 2026-08-08 实战：独立审查报告（B1–B5/M1–M7）经代码交叉核验发现 3 处误判（B1 字符串注解≠运行时错误降 P1、B3 离线脚本≠实盘链路重定性 M4、B4/B5 "3.12 语法崩溃"误报撤销）。**铁律：任何 P0 进清零配额前必经二次核验，勘误写进同文档不漂移**。方法论见 `cairn/code-review-independent-audit-20260808.md`；待办：E1 补 `wt_backtest_engine.py` 的 `import pandas as pd`、B4/B5 的 F821/语法逐条核验后清零。
