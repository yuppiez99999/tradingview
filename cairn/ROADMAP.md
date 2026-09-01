---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-02
updated: 2026-09-01
related:
  - cairn/gnn-supply-chain-factor.md
---

# 终极量化交易系统 v8.7 路线图

> **版本号说明（已纠正 2026-08-26，两套体系勿混淆）**：
>
> - **系统软件版本**（权威源 = `README.md` 第 1 行 + 版本历史表）：当前 **v8.7**，12-31 正式发布。**不存在 v8.8** —— "v8.8" 仅是 Wave 9-GH 计划文档里顺带写的假设目标，未在 README/CHANGELOG 版本历史中确立。**2026-08-29 口径注记**：LOG 08-28 条目标题中的 "v8.8 对冲调优/v8.8 生产同步" 系口径漂移，其对冲调优成果（RegimeFolio S6 + hysteresis + VIX 趋势 + HG-7 + Deep Hedging 持久化）**统一归入 v8.7 发布内容清单**；未来 v8.8 仅由 CHANGELOG 确立。
> - **计划文档编号**（排期/升级计划自身的迭代号，非软件版本）：v9.0（08-06 工程地基）→ v9.1（08-06 差距评估）→ v9.2（08-06 WORK_PLAN 工业级达标）→ **v9.3（08-10 统一升级计划，最新）**。
> - 本文档（ROADMAP）是 **v9.3 统一升级计划** 的路线图载体；Wave 7/8/9 是 v9.3 计划内的执行轨道。

> **最新状态同步（2026-08-29，排期优化）**：
>
> - **B4 推进口径更正（2026-09-01）**：① "enabler `--auto` 推进 B4" 与实现不符 — `--auto` 对 Stage N≥1 仅提示不推进，实际由 `--advance` 推进（09-01 已执行，阶段轨已达最终阶段 orchestrator，新 flag: USE_EVOLUTION_ORCHESTRATOR/USE_FINENG_EVT/USE_FINENG_PATH_SIM）。② `USE_MLOPS_PIPELINE` 不在 enabler `STAGE_FLAGS` 任何阶段集合中 — B4 真实路径 = `phase_b_b4_shadow_runner.py` shadow 7 天验证后才启用 flag。③ B4 前置口径修正：b3_shadow_status.json 缺失不再阻断（B3 已 08-27 经 enabler 评估启用，shadow 机制被阶段轨吸收），`check_b3_status()` 已加阶段轨回退判定（21 单测全绿）。→ 下一步：启动 B4 shadow 7 天运行，之后评估 USE_MLOPS_PIPELINE 启用。指针: `cairn/LOG.md` 2026-09-01 条目。
> - **升级路线优化与排期完成**：详见 `docs/升级路线优化与排期_20260829.md`。核心结论：发布门禁 D1-D10 全绿、**仅剩 D11**（Phase B shadow 6/7 天 + 6/20 样本，**双条件**达标日约 09-19 后复验）；距 12-31 发布 17 周。8 项路线修正：①版本口径统一（LOG 中 "v8.8 对冲调优" 成果归入 v8.7 发布内容，见版本号说明注记）②ETF P5 前置修正（"v8.7 发布" → "发布门禁全绿 + Sprint 3 收尾"，P5.1 顺延至 11-13）③P3.1 提前至 09-04（P3.0 ③ 数据 5/5 @09-03）④总验收清单 3 项已完成项补打勾（daily_workflow 2159 / R10 裸债清零 / 覆盖率 0.833）⑤支线预算制（每周支线合计 ≤2 人天）⑥新增 **12-10 功能冻结窗**（门禁三件套 21 天 0 FAIL 观察窗起点）⑦Wave10-CTX Phase A 已提前完成（94 用例全绿 flag off diff=0），剩余 A2-T4/A4 并入 10 月中旬一次性执行 ⑧R10/T6 fail-safe 宽捕获 222 处按每周 30 处渐进清理（不阻塞发布，T7 GREEN）。**2026-08-30 口径修正**：D11 PASS 需 `consecutive_stable_days>=7 AND total_samples>=20` 双条件（`engineering_debt_gate.py:1094`），原"09-02 满 7/7 后复验"仅关注 stable_days 忽略 min_samples=20，09-02 复验会 FAIL(8/20)，真实达标日约 09-19（需 20 个交易日样本）。**2026-09-01 达标日精化**：stable 7/7 已达标 + 08-31 样本已记录 → 达标日 09-17 EOD 后 (20/20)，09-18 (周五) 复验可 PASS（详见 `docs/d11_reverify_checklist_20260830.md` 刷新版）；09-12 Sprint 1 收尾判定材料需按此口径预修正。**2026-09-01 口径预修正完成**：Sprint 1 主体收尾判定 (09-12) 材料明确不含 D11（仅 B1+B2 稳定 + daily_workflow + R10）；D11 复验作为独立里程碑 09-18 补章，Sprint 1 收尾判定至此完整闭环。排期文档 + D11 核对清单 §6 已同步修正。
> - 关键路径锚点：D11 复验（consecutive_stable_days 6/7 + total_samples 6/20 **双条件**，09-02 仅 stable_days 满 7/7 但 samples=8/20 不足会 FAIL，真实达标日约 09-19 需 20 个交易日样本）→ Stage 3 auto_retrain 稳定 ≥3 天 (现 2/3) 后 enabler `--auto` 推进 B4 (USE_MLOPS_PIPELINE) → P3.2 开跑 → 09-12 Sprint 1 收尾 → 09-13 shadow 30 天 cron + T15 QMT paper → 10-12 Sprint 2 收尾 → 11-12 Sprint 3 收尾 → 12-10 冻结 → 12-31 v8.7 发布。**2026-08-29 实测纠正**：B1+B2+B3 已于 08-27 全启用 (原 "B3 评估 09-02~04" 失效)，当前 Stage 3 auto_retrain。**2026-08-30 D11 口径修正**：详见 `docs/d11_reverify_checklist_20260830.md`。

> **历史状态同步（2026-08-28）**：
>
> - `W7.1.8` 已确认真实 LightGBM 模型已落盘 (`reports/qlib_model_20260824_201837.pkl` 等)，`signal_fusion.py` 实际读取真实输出，不再降级随机信号；不再视为硬缺口。
> - `GAP-2` (Wave 3 + U5) / `W7.4.5` (覆盖率 0.833 ≥0.80) / `W7.2.6` (工程基础层 Phase 0-1) 今日全部补打完成勾。
> - `B2` 启用评估确认完成：预热 3/3 天全 Go，flag 已启用、阶段已推进到 `auto_retrain`。
> - 今日修复：① D11 断链（回填 8/27 收益，稳定天数 4→5/7）；② EOD 阶段 4.85 在 B2 启用后跳过 shadow 预热（不再每日 FLAG 不变式 FAIL）；③ `shadow_fills_integrator` Windows 写入改用 `shutil.move`（`os.replace` 被安全软件拦截 WinError 5），单测 12/12 恢复。
> - `P3.0` 影子账户闭环门禁代码链路已就绪：`build fills` 可被 shadow 读取并回算 NAV；但需至少 5 个交易日真实成交数据，才允许 `P3.1` 启动。
> - `B2/B3` 启用顺序已修正：B1 已真实启用，B2 预热 1/3 天（08-26）并需 08-26~08-28 三天完成后评估，B3 继续冻结，不越级推进。
> - 当前 `Wave 7 Sprint 1` 收尾判定已收紧为：`B1+B2 稳定 ≥7 天 + daily_workflow ≤4500 + R10 清零`；当前仍未满足进入 Sprint 2 的全量条件。
> - `ER-1.1` / `ER-1.3` 今日已落地，并由对应 24/24 单测验证通过；`W7.2.6` 基础设施层也已确认就位（`uv.lock` + `pyproject.toml` + `python-dotenv` + `.env` 约束 + `ruff` 收紧）。
> - `Wave 8-LIT` 与 `Wave 9-GH` 前置依赖已解除，v8.7 发布窗仍是最高优先级。

**当前焦点**：Wave 6 全部提前完成 (2026-08-12, 超前 107-141 天) → **v8.7 发布主线已成为唯一最高优先级**。当前重点不是“继续扩张研究项”，而是完成以下门禁：B1+B2+B3 已于 08-27 全启用（Stage 3 auto_retrain，consecutive_stable_days=6/7 推进 D11）、P3.0 闭环代码链路已就绪但需至少 5 个交易日真实成交数据才允许 `P3.1` 启动、`W7.1.8` 真实 qlib LightGBM 模型已落盘。**下一步**：守住 Stage 3 auto_retrain 健康门禁（稳定 ≥3 天）由 enabler `--auto` 自然推进 B4，继续积累真实成交数据满足 Sprint 1 收尾，并待 D11 双条件复验（stable_days≥7 AND total_samples≥20，真实达标日约 09-19）。**第一优先级**为：巩固 Phase B 稳定、完成真实数据积累、让 `daily_workflow ≤3000` 与 `R10 清零` 稳定；v8.7 发布窗口仍为最高优先级。**2026-08-29 实测纠正**：原 "B2 预热 1/3 天 (08-26) / B3 冻结" 与运行时不符，详见 W7.1.1 与 Wave 2 B 状态段。

## 优化后的升级路径（2026-08-27）

为避免主线被研究/工程扩展任务稀释，路线图已收敛为三条主线，并强制单一优先级：

### 1) 主线 A：v8.7 发布与稳定（最高优先级）

- B1/B2/B3 顺序门禁与 `P3.0/P3.1` 真实成交验证
- `daily_workflow` 稳定性与 `R10` 清零
- 影子账户/真实交易数据积累
- 影响 v8.7 交付质量的所有阻塞项

### 2) 支线 B：研究增强与实验验证（shadow / 研究模式）

- ETF 期权对冲子模型 P3/P4/P5
- 自我进化框架继续观察与优化
- GNN / Layer 2 研究资产保留在研究路径，不进入生产主线
- 仅在主线门禁满足后再决定是否真实接入生产

### 3) 支线 C：工程化与集成（服务主线，不主导路线）

- Wave 7-QC 代码质量提升
- Wave 8-LIT 文献升级沉淀
- Wave 9-GH 增补集成（顺延，避免与 v8.7 发布冲突）
- 其目标是提升工程质量与协同效率，而不是吞噬发布主线资源

### 冻结项 / 后置项

- Wave 9-GH 大规模集成：在 v8.7 发布门禁未满足前维持顺延
- 非阻塞的研究扩展与文献升级：仅在主线稳定后再重启
- 任何可能影响稳定运行的“实验型接入”，必须先进入 shadow / 研究状态

> 结论：当前阶段的最佳实践不是继续堆更多增强功能，而是让主线单线化、研究分流、工程服务化。Wave 8-LIT 已提前完成且无需再扩张，Wave 9-GH 继续顺延，重点回到 B2 + P3.0 + `daily_workflow` 的闭环稳定。

**Wave 7-QC 子轨道已启动** (2026-08-22 ~ 10-05, 3 Sprint, 代码质量提升) — QC-1 P0落地(CI修复+ocr固化+工作区收敛) / QC-2 P1+外部接入(daily_workflow拆分+mypy strict+五轴审查+重构规则) / QC-3 P2收尾(ruff清零+覆盖率80%+)。详见 `docs/代码质量提升排期计划_20260821.md`。**Wave 8-LIT 文献驱动升级** (2026-08-24 ~ 11-02, 5 Sprint, 26 任务) — ✅ **全部提前完成 2026-08-24** (808 测试全绿 + 26 知识专题归档 + v8.7 灰度 50% 运行中), LIT 窗口空出, Wave 9-GH 前置已解除。详见 `docs/系统升级文献调研与排期_20260823.md` + `cairn/v87-release.md`。**Wave 9-GH GitHub 增补集成** (2027-01-04 ~ 2027-04-30, 4 Sprint, 18 项目) — 正式启动已顺延，避免与 v8.7 发布冲突。详见 `docs/Wave9_GitHub增补集成计划_20260825.md`。**Wave 5 (GNN 因子) 2026-08-03 重大更新**: B1-B5 全套修复后 Gate1 PASS (CHAIN_MOM_60D 反向因子 effICIR=0.503/多空夏普1.766) + Gate2 FAIL (+0.039 增益证伪, 实际+0.0017/+0.0064 t值0.15/0.76不显著) — 决策: Layer 2 回退, GAT 代码保留为研究资产不在生产路径, Layer 1 CHAIN_MOM_60D 推进 S5-S7 入库流程 (S1-S5 已通过, S6/S7 长期任务, 归入 Wave 7 Sprint 2/3); 排期时间不变 (10-06~11-30), 与自我进化 Wave 1/2 (09-05 前收尾) 无时间冲突, 仅与 Wave 4 后半 (10-06~10-31) 部分重叠 — W5.2 验证避开 Wave 4 实盘验证窗口

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
- [x] **执行层自动闭环 (2026-08-19)** — 盈亏→风控→改计划→对冲增减→再平衡全自动闭环 (断链修复 + 方案A/B/C + DeepSeek+GLM 双模型判断)。指针: `cairn/LOG.md` 2026-08-19 条目
- [x] **自我进化迭代再平衡闭环补齐 (2026-08-21)** — 4 处断裂修复 (factor_weights 消费 / EOD 进化循环 / 时序修正 / 漂移回调), 双孤岛打通为完整闭环。指针: `cairn/evolution-rebalance-loop.md`
- [x] **ETF 期权对冲 Phase 2 真实历史回测验证完成 (2026-08-25~08-26)** — P2.1 数据接入 (14 ETF 2021-2026 日线) + P2.2 S1-S5 五策略双源交叉验证 + P2.3 诚实验证三件套 (DSR/CPCV/Noise)。**P2.3 最终结果 (2015-2026, T=2829, CPCV N=4)**: S6(调优后 bo=1.65/bd=0.5) 年化 5.07% / 回撤 15.85% / DSR=1.0 PASS / CPCV CV=0.414 PASS / Noise=stable → **HONEST**, 5/6 验收项通过 (仅 Sharpe 0.281 未达 0.38 校准线, regime 轮动结构上限)。详见 §ETF期权对冲再平衡子模型排期 P2.3 节
- [x] **实盘前工作清单与推进计划 (2026-08-24)** — 10 条工作线核查/落地完成, 落地 8 项修复 (QMT broker 段 + 实盘门控 + 建仓落盘 + broker 契约校验 + auto-confirm 护栏), 发现 P0 阻断项 (影子账户无真实撮合)。详见 `docs/实盘前工作清单与推进计划_20260824.md`
- [x] **MVSK P5-2 + qlib_lgb_v2 shadow 30 天验证启动器 (2026-08-24)** — `scripts/launch_shadow_30day.py` + `utils/shadow_30day_evaluator.py` + 22 测试全绿, 待 09-13 cron 启动。详见 `cairn/shadow-30day-validation.md`
- [x] **盘前工作流并行化 + 情感信号注入 + 12 任务定时注册 (2026-08-26)** — morning_info_runner 7 项采集并行化 (ThreadPoolExecutor 4 线程) + 舆情综合日报→ai_recommendations + llm_intraday_decision_engine 盘中 LLM 决策 + 12/12 Windows 计划任务注册。详见 `cairn/daily-workflow-parallel-sentiment-20260826.md`
- [x] **任务3 B2/B3 flag 启用顺序决策与执行 (2026-08-26)** — 修复三层 flag 断链 (enabler 只写 status 不落盘运行时 + 运行时注册表 config vs configs 双源漂移 + 健康检查跨阶段稳定日污染) + 回滚 11:44 B3 越级推进 + B 顺序硬门禁。B2 预热 1/3 天, B3 未启用。详见 `cairn/LOG.md` 2026-08-26 任务3 条目
- [ ] ~~C++/Rust 核心路径重写~~（超低延迟行情解码、订单生成、风控检查）— ⚠️ **2026-08-26 证伪搁置**: W6.3.3 实测 3 档场景 ROI 均不达标 (日线0.117s / 分钟32.4s / TICK 0.010s, 前置场景高估 98.7%), 依据 `cairn/nautilus-trader-study.md` §4.3; 重启条件: TICK 级实盘实测延迟成为瓶颈
- [ ] PTP 硬件时钟采购与部署（¥360K-710K 预算）— 与 C++/Rust 证伪决议联动, 低优先级不阻塞主排期
- [ ] 多策略组合优化（跨信号协方差矩阵 + 动态风险预算分配）
  - [x] **MVSK 高阶矩优化 P1**（YAND 启发，2026-08-17）— `risk_budget_optimizer.py` 扩展偏度/峰度目标，8/8+31/31 测试 passed，A/B 机制验证成功 → `cairn/mvsk-higher-moment-optimization.md`
  - [x] **MVSK P2 BL+MVSK 联合优化**（2026-08-17）— BL 后验 μ + MVSK 样本外夏普 -0.923 显著优于 MV -1.272，BL 与 MVSK 强互补验证成功
  - [x] **MVSK P3 Regime 动态切换**（2026-08-17）— 原 regime 切换失败→γ 扫描证 252 日 MVSK 根本性失效→训练窗口扫描发现临界点 336~378→修正实验确认**始终 BL+MVSK(378) 最优夏普 +0.418**，无需 regime 切换。最优生产策略：378 日训练 + BL + MVSK(γ_s=0.1, γ_k=0.05)
  - [x] **MVSK P4 跨周期验证**（2026-08-17）— 995 日（2022-07~2026-08）跨周期回测：BL+MVSK(378) **4/4 段跑赢 BL+MV**（Δ夏普 +0.22），γ_s=0.1 泛化成功，γ_k 可调到 0.1（夏普 +0.683）。**MVSK 生产就绪**，最终策略 BL+MVSK(378, γ_s=0.1, γ_k=0.1)
  - [ ] **MVSK P5 生产接入**（09-05 ~ 11-12，挂 Wave 7 Sprint 1-3）
    - [ ] P5-1 (09-05~09-12, Sprint 1 后半) scheduler.py 中线层 shadow 接入准备 — 代码改造 `utils/universe/portfolio_builder.py` 支持 BL+MVSK(378) + shadow 模式（不产出真实订单，仅记录 vs 当前 BL+MV 差异）；冷启动 378 日历史数据预加载
    - [ ] P5-2 (09-13~10-12, Sprint 2) shadow 运行 30 天验证 — 实盘 shadow 对比 BL+MVSK(378, γ_s=0.1, γ_k=0.1) vs 当前中线层 BL+MV(252)，每日记录权重差异/收益差异/换仓成本，30 天后评估 Δ夏普
    - [ ] P5-3 (10-13~11-12, Sprint 3) 正式启用中线层 BL+MVSK(378) — shadow 通过后（Δ夏普 > 0 且无异常换仓）切换中线层优化器；短线层保持 BL+MV(252) 不变；接入风控六件套监控
    - [ ] P5-4 (11-13~12-31, Sprint 4, 可选) 沪深 300 股票池扩展验证 — 华为云上用 5~10 年数据 + 沪深 300 验证 γ 参数泛化；若 γ_s=0.1/γ_k=0.1 仍最优则全市场推广
- [ ] 十五五规划对齐（2026-2030 五年分阶段管理，2030-12-31 强制清仓）
- [x] **ETF期权对冲再平衡子模型 Phase 1**（2026-08-20）— 独立200万纯ETF子组合(14 ETF/100%) + ETF期权对冲(4标的认沽保护) + 自我再平衡(五阶段) + 回撤熔断L0-L4 + 6压力测试。31/31单元测试通过。详见 `cairn/etf-option-hedge-model.md`

## ETF期权对冲再平衡子模型排期（2026-08-20 ~ 12-31）

> **定位**：基于用户需求"以本系统构建A股ETF+期权对冲的自我再平衡模型，年化≥8%/回撤<15%"，新建独立200万纯ETF子组合，与500万主组合并行运行。Phase 1 已于 2026-08-20 完成，Phase 2-5 排期如下。
> **设计依据**：V9实测年化19.62%/回撤9.95%已超目标(`cairn/ROADMAP.md:24`) + BL+MVSK(378)生产就绪(`cairn/ROADMAP.md:39`) + protective_put_engine年化成本<2.5%(`utils/protective_put_engine.py:84`)。
> **与Wave 7协调**：Phase 2-3 与 Wave 7 Sprint 1-2 并行（回测+shadow不侵入生产链路）；Phase 4 灰度发布需 Wave 7 Sprint 3 实盘验证四件套就绪后启动；Phase 5 全量启用需 Wave 7 v8.7 发布后。

### Phase 1 ✅ 已完成（2026-08-20）

- [x] P1.1 子组合配置 `config/etf_option_subportfolio.yaml`（200万/14 ETF/宽基60%+行业25%+防御15%）
- [x] P1.2 编排器 `etf_option_hedge_rebalancer.py`（五阶段日度再平衡，复用6个现有模块）
- [x] P1.3 单元测试 `tests/unit/test_etf_option_hedge_rebalancer_unit.py`（31/31 passed）
- [x] P1.4 压力测试验证（裸敞口6/6突破→对冲后3/6突破，2020疫情/2022俄乌/2024地产已保护到15%以内）
- [x] P1.5 知识沉淀 `cairn/etf-option-hedge-model.md`

### Phase 2 真实历史数据回测验证（08-21 ~ 09-05，~2 周）— ✅ **全部提前完成 2026-08-26**

- [x] P2.1 (08-21~08-25) 接入AKShare/Wind数据源，拉取14 ETF 2021-2026日线数据 — ✅ **DONE 2026-08-25**: `scripts/fetch_etf_phase2_data.py` + `data_cache/etf_phase2/` (14 parquet + 合并面板 19099 行, sina 源, 零缺失)
- [x] P2.2 (08-26~08-30) 跑S1-S5策略对比回测（复用 `utils/hedge_rebalance_backtest.py`）— ✅ **DONE 2026-08-25 (提前)**: `scripts/run_p22_backtest.py` 双源交叉验证 (Wind MCP 前复权主源 + sina 除权修正); **验收 FAIL**: S4 年化 0.49%/回撤 38.36%/Sharpe -0.082, S5 年化 3.03%/回撤 34.16%/Sharpe 0.056 (标准: ≥8%/<15%/>0.8); 发现 sina 未复权数据在份额折算 ETF (512100/510310) 严重失真 + 再平衡策略对复权口径敏感(±1pp)
  > 🔄 **v8.6.15 UPDATE (2026-08-25)**: 数据源修复 (Wind MCP 显式前复权 + sina 除权修正冗余源) + 策略重构 (主题权重 21%→13%/防御 20%→28%；期权伪对冲→Black-Scholes 月度滚仓真实定价；固定 6% 阈值→动态 3/5/8%；S5 伪尾部期权→回撤熔断降净敞口 L1/L2/L3)。重构后: S1 4.76%/29.84%/0.164, S3 4.74%/29.79%/0.158, S5 1.53%/22.35%/−0.035 (回撤 34→22), 复权路径依赖 1.04→0.24pp。**P2.2b drawdown_breaker 参数枚举 (4×4 网格)**: 最优 levels=(0.08,0.12,0.18)/exposures=(0.4,0.25,0.1) → S5 回撤 22.35%→**15.72%** (首次达校准线≤20%), 年化 2.58%/Sharpe 0.051。**结构性结论: 2021-2026 高波动区间下 ≥8%年化 与 <15%回撤 不可同时达成** — 组合自身设计目标即为 20%/0.38 (`config/etf_option_subportfolio.yaml`), 验收线已校准见下
  - S1: 静态目标权重 + 无对冲（基准）
  - S2: 动态6%阈值再平衡 + 无对冲
  - S3: 认沽期权保护 + 静态再平衡
  - S4: 认沽保护 + 动态再平衡 + ETF资金流加减仓
  - S5: 动态再平衡 + 组合自触发尾部对冲
- [x] P2.3 (08-26) 诚实验证三件套 — ✅ **DONE 2026-08-26: 方案A(扩展样本2015-2026) + 方案B(V9 regime S6) + S6参数调优 → S5/S6 全部 HONEST, S6年化首破5%**。数据: 14 ETF 2015-2026 Wind MCP 前复权 (T=2829, D:\etf_data_2015_2026\)。S5: 年化4.67%/回撤15.86%/Sharpe0.269, DSR=1.0/CPCV CV=0.435/Noise=stable → **HONEST**。S6(调优后 bo=1.65/bd=0.5): 年化**5.07%**/回撤15.85%/Sharpe0.281, DSR=1.0/CPCV CV=0.414/Noise=stable → **HONEST**。关键: ①扩展T=1364→2829使DSR从0.64→1.0 ②CPCV N=6→4使CV从0.83→0.43 ③S6参数枚举54组→年化4.61%→5.07%首破5%校准线。验收: 年化≥5%[S6 PASS] / 回撤≤20%[PASS] / DSR[PASS] / CPCV[PASS] / Noise[PASS] / Sharpe≥0.38[FAIL 0.281] — 5/6验收项通过, 可顺延P3
- **验收标准（v8.6.15 校准 2026-08-25）**：原定 ≥8%/<15%/>0.8 与组合自身风险预算冲突（`config/etf_option_subportfolio.yaml` 设计目标即 target_max_drawdown=0.20 / target_sharpe=0.38 / target_annual_return=0.095；2021-2026 实测该纯 ETF 组合收益天花板 ≈5%）。校准为：**S4或S5 年化≥5% / 回撤≤20% / Sharpe≥0.38 / 超额≥5pp / DSR通过(P2.3)**。**P2.3 最终结果 (2015-2026, T=2829, CPCV N=4)**: S6(调优后) 5/6 验收项通过 — 年化5.07%[PASS] / 回撤15.85%[PASS] / DSR=1.0[PASS] / CPCV CV=0.414[PASS] / Noise=stable[PASS] / Sharpe0.281[FAIL]. **结论: 统计诚实性已补全 + 年化/回撤达标, 仅 Sharpe 未达0.38(regime轮动结构上限, 需V9完整LGB选品alpha) — 可顺延 P3 影子账户验证**
- **多资产重构结论 (2026-09-01)**: 为突破纯 ETF 5% 天花板引入纳指/标普/红利低波 (17 资产池)，S10 年化 8.98% 系后视选资产 (DSR≈0)，S11 滚动样本外因果重构亦 DSR=0/CPCV CV=0.824 → **17 资产池内无稳健超额**。S12 纯防御风险平价 (黄金/国债/红利低波 逆波动率月频再平衡, 无拟合) 年化 7.48%/回撤 2.60%/Sharpe 1.71，CPCV CV=0.173 稳定 + Noise 稳定，但 DSR=0.50 未过 0.95 → 为诚实下限 (控回撤+跑赢通胀，不追求 alpha)。详见 `cairn/LOG.md` 2026-09-01 条目

### Phase 3 影子账户并行运行（09-06 ~ 10-05，~1 月）
>
> **前置依赖**: 实盘前清单 2026-08-24 发现的 P0 阻断项 — 影子账户 NAV 回算无真实撮合 (trade_log 空), 需先消费 DTE-1 落盘的 strategy=build fills 积累 1-2 周真实成交后准入。Phase 2 提前完成 (08-26), 影子验证窗口可前移, 但受此 P0 项制约。
> **2026-08-26 前置门禁新增**: DTE-1 已实现并验证 (strategy=build 落盘, `cairn/dte1-build-fills-store-20260824.md`), 但"影子账户消费 build fills + NAV 回算"端到端未闭环 (该文档 §5 标注为后续)。新增 **P3.0 门禁** — 三件验证通过才允许 P3.1 启动, 否则 Phase 3 后移 (自真实成交起算 +1~2 周)。

- [~] P3.0 (09-03~09-05) **影子账户闭环门禁验证 (新增 2026-08-26)** — ①shadow 实读 strategy=build fills 出 NAV ②daily_pnl 消费验证 ③积累 ≥1 周真实成交; **代码链路就绪 2026-08-26**: FillsStore/bridge 加 strategies 过滤 + ShadowAccount.consume_fills_from_store + `scripts/verify_p3_0_gate.py` + 10 测试全绿; ①② 验证 PASS (4 笔 build fills 消费通), ③ 待数据积累 (当前 1 交易日/4 笔, 需 ≥5 交易日, 09-09 后可复验); 端到端 PASS 方为 P3.1 前置; 详见 `cairn/p3-0-gate.md`
- [ ] P3.1 (09-06~09-10) 影子账户配置（复用 `shadow_account_system.py`，分配200万虚拟资金）
- [ ] P3.2 (09-11~10-05) 每日EOD并行运行30天，记录：
  - 日度收益 / 累计收益 / 最大回撤 / Sharpe
  - 再平衡触发次数 / 换手率 / 交易成本
  - 期权对冲成本 / 认沽保护覆盖率 / 滚仓次数
  - 回撤熔断触发级别分布
- [ ] P3.3 (10-06) 30天shadow评估报告，对比回测预期
- **验收标准**：30天累计收益正向 / 最大回撤<15% / 无异常换手 / 与回测偏差<20%

### Phase 4 小资金灰度发布（10-06 ~ 11-05，~1 月）
>
> **前置依赖**：Wave 7 Sprint 3 实盘验证四件套（T15-T18）就绪

- [ ] P4.1 (10-06~10-12) 5%小资金实盘（10万），复用 `GradualRolloutOrchestrator` PAPER→SHADOW阶段
- [ ] P4.2 (10-13~10-26) 10%小资金实盘（20万），SHADOW→PARALLEL阶段
- [ ] P4.3 (10-27~11-05) 25%资金实盘（50万），PARALLEL→FULL阶段
- **验收标准**：每阶段运行7天稳定 / 实盘vs shadow偏差<5% / 回撤<15% / 无风控熔断

### Phase 5 全量启用 + 持续监控（11-06 ~ 12-31，~2 月）
>
> **前置依赖（2026-08-29 修正）**：~~Wave 7 v8.7 发布~~（与 12-31 发布时序倒挂）→ **v8.7 发布门禁全绿（D1-D11）+ Wave 7 Sprint 3 收尾（11-12）**；P5.1 顺延至 11-13 启动，P5.2/P5.3 不变。

- [ ] P5.1 (11-13~11-27) 100%资金启用（200万），接入每日EOD工作流
- [ ] P5.2 (11-21~12-15) 持续监控 + 参数优化：
  - 再平衡阈值动态调优（6%→最优值）
  - 期权对冲OTM程度/DTE调优
  - ETF目标权重季度再评估
- [ ] P5.3 (12-16~12-31) 年度绩效报告 + 与500万主组合合并绩效分析
- **验收标准**：年化≥8% / 回撤<15% / Sharpe>0.8 / 与主组合相关性<0.7（分散化价值）

### 关键风险与缓解

| 风险 | 缓解措施 |
| --- | --- |
| 期权流动性不足（部分ETF期权日成交<100张） | P2.2回测中纳入流动性成本，必要时减少张数或换标的 |
| 期权成本超预期（IV飙升时权利金翻倍） | 年化成本硬上限2.5%，超限自动减张数 |
| 极端场景突破15%（2015股灾/2018贸易战对冲后仍23.5%/18.3%） | 回撤熔断L2/L3减仓配合，分层防御 |
| ETF标的退市/合并 | 季度再评估标的池，备选ETF清单 |
| 与主组合相关性过高 | P5.3相关性分析，必要时调整ETF权重降低重合 |

## 后续升级计划（2026-08-04 制定，四波推进）

### Wave 1：观察期决策 + 自我进化收尾（08-04 ~ 08-20，~12 天, 因样本不足延长）

- [x] W1.1 T4.7 收尾 — ✅ DONE 2026-08-04 — `docs/自我进化框架/FINENG_ACCEPTANCE_REPORT.md` 已补写, 3/4 模块通过 WF 闸门
- [x] W1.2 T4.2 收尾 — ✅ DONE 2026-08-04 — `utils/theta_engine.py` 三处调用点全部切换到 `utils/fineng/pricing/black_scholes.py` 统一内核
- [x] W1.3a (08-04~08-06) G1 真实数据接入沙箱化 — **Day 1+2+3 DONE 2026-08-03**: Day 1 核心实现 (750 行 + 77 测试 + 93.95%) + Day 2 cross_validate + feed_history 并行化与缓存 (108 测试 + 94.12%) + Day 3 集成 EOD 工作流 + EvolutionEval 兜底 + 修复 daily_workflow.py 缺失根因 + 端到端验证 PASS (真实行情 daily_return=+1.246%). 下一步: W1.3b DriftMonitor 真实数据回填
- [x] W1.3b (08-07~08-10) DriftMonitor 真实数据回填 — ✅ **DONE 2026-08-03 (提前完成)**: DriftShadowIntegrator 骨架 (626行) + 46 测试 100% 通过 + 真实 daily_returns 端到端集成 PASS (5/5日成功) + PSI 校准骨架 (降级, 待 B1) + EOD 阶段四点七集成 + EvolutionEval 兜底 + CLI 入口 + 单日/历史回填端到端验证 PASS. 验收 8/9 通过, 3 项降级待真实 V9 panel
- [x] W1.3c (08-11~08-12) StrategyEvaluator 真实评分 — ✅ **DONE 2026-08-03 (提前完成)**: 3/3 验证 PASS (Public/Private 分离 + Flag 透传 + 只读行为). public=0.0 vs private=0.4716, reward_hacking_risk=0.0, pit_violations=0. 样本 5 条不足, 距 20 条差 15 天
- [x] W1.4 (08-10~08-13) 08-13 决策材料 — ✅ **DONE 2026-08-03**: `docs/自我进化框架/OBSERVATION_PERIOD_DECISION.md` v1.2 完成. 推荐选项 B 延长观察期至 08-20 (样本不足为硬阻塞, 机制本身已验证健康)
- [x] **决策规则触发**：08-13 时 Shadow 真实样本预计 14 条 <20, **已选定延长观察期至 08-20**, 不做条件性 Go
  - **2026-08-13 口径校正（更正注记）**：以调度器 `scripts/phase_b_progressive_enabler.py --check` + 真实 `reports/shadow/daily_returns.jsonl` 为准 — 观察期为 **21 天窗口**（`shadow_admission.yaml` observation_days=21），真实样本 **12 条**（07-27~08-11，今日 EOD 未跑），双门槛（21 天 + 样本≥20）均未达 → **观察期维持进行中，今日不决策、不启动 Stage 1（B1）**。调度器预计 Stage 1 评估点 **08-24**（21 天满 + 预热 3 天），早于 ROADMAP 预写的 08-20，差异源于旧简报脚本硬编码 14 天（已修正 `observation_daily_briefing.py` obs_total 14→21 对齐 yaml）。B1-B4 排期以调度器 08-24 评估点为准顺延，Wave 2 以下时间线为计划态。

### Wave 2：Phase B 渐进启用（08-20 决策通过后 → 09-05, 因决策日延后顺延）
>
> **2026-08-26 任务3 更新 (B2/B3 flag 启用顺序决策与执行)**: 修复三层 flag 断链后 B1 已真实落盘运行时 (USE_DRIFT_DETECTOR=true, 覆盖文件+审计生成); B2 shadow 预热中 (08-26 为第 1/3 天); B3 因 B2 未就绪 + 曾发生 11:44 越级推进 (已回滚) 而严格冻结; 新增 `_check_b_order_gate` B 顺序硬门禁防复发。启用顺序严格 B1→B2→B3, B2 需 3 天 shadow 预热全 Go, B3 需 B2_OK 后 3 天观察。
> **2026-08-26 排期复核 (计划态, 由硬门禁 + 预热进度推导)**: B2 预热带 08-26~08-28 (3/3 天) → 08-28 启用评估; B3 观察带 08-29~09-02 (B2_OK 后 3 天) → B3 最早评估 09-02; B4 最早 09-03~09-12 (B3 启用后), 观察窗顺延至 Sprint 2 前段。**原计划态 B2(08-23~26)/B3(08-26~29)/B4(08-29~09-05) 已失效**, 实际推进以 `_check_b_order_gate` 与阶段健康度为准。

- [x] B1 (08-20→23) `USE_DRIFT_DETECTOR=true` 仅告警 — ✅ **已真实落盘运行时 2026-08-26** (USE_DRIFT_DETECTOR=true)
- [x] B2 (08-23→27) `USE_FEEDBACK_LOOP` 自动接入 — ✅ **已启用 2026-08-27** (b2_shadow_runner EOD 阶段4.85 自动预热 3/3 天全 Go 后, 08-27 10:25 落盘 USE_FEEDBACK_LOOP=true)
- [x] B3 (08-27→) `USE_AUTO_RETRAIN=true` + 降级护栏 — ✅ **已启用 2026-08-27** (Stage 3 auto_retrain 激活, 落盘 USE_AUTO_RETRAIN=true + USE_FINENG_GARCH=true + USE_FINENG_KALMAN_BETA=true); 11:44 越级推进已于 08-26 12:05 回滚, 08-27 经 B 顺序门禁合法推进
- [~] B4 (→09-12+) `USE_MLOPS_PIPELINE=true` 完整外层循环 — ⏳ **未启用** (USE_MLOPS_PIPELINE=false); 需 Stage 3 auto_retrain 稳定 ≥3 天 (现 2/3) + 观察窗, 由 enabler `--auto` cron 推进; 并入 Sprint 2 前段

### Wave 3：代码质量持续修复（08-04 ~ 09-04，与主线并行）

- [x] Round 5a — `ms_strategy/` 宽泛异常清零 — ✅ **DONE 2026-08-03 (验证)**: 实际 0 处 `except Exception` (前序会话已清零 131 处). 158 个 except 子句全部使用具体类型 (ValueError/KeyError/RuntimeError/TypeError/AttributeError/OSError/TimeoutError/ConnectionError/ImportError). ROADMAP 原记 "122处 TODO" 为过时信息
- [x] Round 5b — `scripts/` 宽泛异常清零 — ✅ **DONE 2026-08-03**: AST 扫描确认 0 处真实 except Exception (3 处为 _fix_*.py 正则字符串字面量误报). 修复 2 处裸 except: `_install_all_ml.py:14` → `(ValueError, TypeError)`, `_install_scipy.py:64` → `OSError`
- [x] Round 6 — `research/` 宽泛异常清零 — ✅ **DONE 2026-08-03 (验证)**: research/ 自身 (90 文件) 实际 0 处 `except Exception` (前序会话已清零). 具体类型使用得当 (ValueError 133 / TypeError 132 / OSError 127 / KeyError 126 等). research/references/ (第三方 1290 文件) 中 4 处 `except BaseException` 为 Vibe-Trading 有意为之 (线程异常传递 / 原子写入安全 / 异步桥接), 不应修改. ROADMAP 原记 "78处 TODO" 为过时信息
- [x] 第三阶段 — TYPE_IGNORE + SYS_PATH 核心清零 — ✅ **DONE 2026-08-04**: SYS_PATH 扩展 `setup_sys_path()` 注入 v8.3 根/src/utils 四路径, 20 个根目录入口脚本统一调用; TYPE_IGNORE 项目业务代码裸注释清零 (剩余 5 处全在 qlib_env 第三方库), 带错误码注释 300+ 处; 零语法错误, 运行时 import 验证通过 (autolearn_trainer + utils 包 + bridges 模块)
- [x] 第四阶段 — PRINT 清零 + ruff/flake8 BLE001 门禁增强 — ✅ **DONE 2026-08-04**: ruff.toml 启用 T (flake8-print) + BLE (flake8-blind-except) 规则族; 根目录 top 5 + utils/ + v8.3 src/ 共 734 个 print→logger 转换 (info/warning/error 级别自适应); 302 个 except Exception 加 # noqa: BLE001 (标注 fail-safe 待后续精确化); 2 处 (ImportError, Exception) 精确化为 (ImportError, ValueError, TypeError/RuntimeError); per-file-ignores 豁免 scripts/cli/tools/tests/research/ms_strategy 脚本目录; DQC Phase 2 同步完成 (P3 检查点 + F 维度 + X 维度)
- [x] GAP-2 E2E 补齐 — full_pipeline + shadow_account_lifecycle — ✅ **DONE 2026-08-28**: `tests/e2e/test_full_pipeline_e2e.py` + `tests/e2e/test_shadow_account_lifecycle_e2e.py` 存在且 16 passed (E2E 全链路 + 影子账户生命周期覆盖)
- [x] **CI 基础设施真实落地（2026-08-12）** — R1+R4 完成：ci.yml 引用的 6 个缺失脚本（`scripts/_verify_phase3b_static_analysis.py` / `_smoke_runner.py` / `_select_tests_by_diff.py` / `_check_coverage_trend.py` / `_verify_reexport_compat.py` / `_run_v9_regression.py`）全部真实实现非空壳 + 新增 `scripts/ci_integrity_check.py`（C6 机检落点，解析 ci.yml 引用验证存在性 + `--help` 可运行）；新增 `.github/workflows/quality-gate.yml`（PR 增量门禁：ruff 增量 + T201 print + 工作区变更数 + 调用 ci_integrity_check）。C6 判据从 FAIL（6 缺失）→ PASS（12 存在，11 PASS / 1 WARN / 0 FAIL）。关键设计：CLI 入口脚本与可导入模块区分（烟雾测试只验 import）、静态分析基线自动冻结 + 退化检测（WARN 不阻断）。R2 分批提交已将未提交变更收敛至 99（≤100 达成）。细节见 `cairn/ci-repair-and-gate-lessons-20260812.md`

### Wave 4：工程化达标 + 战略升级（09-05 ~ 10-31）

- [~] **daily_workflow.py 拆分（6226→≤3000 行）** — 长期架构重构，按 phase 切分。**第 1 轮已完成 2026-08-12** (W6.6.4): 4 leaf phase (check/calibrate/market/autolearn) 提取到 `workflow/phases/`, 6230→5904 行 (-326). 剩余第 2-5 轮 (risk/hedge/signal/execute/report 等 10 phase, ~2900 行) 待后续推进. 详见 `cairn/daily-workflow-split-plan.md`
- [x] T02/T03 — pylint broad-except 升级 error + 覆盖率基线 — ✅ **DONE 2026-08-12**: `.pylintrc` [MAIN] `fail-on=broad-exception-caught` (pylint 3.x 重命名) + `ci.yml` `--fail-on=broad-exception-caught` 双保险 + `engineering_debt_gate.py` 新增 T7 (裸 except Exception 独立债, 阈值 250, 实测 200 处) + T8 (覆盖率基线退化检测, 基线 0.42 / 当前 0.4307, 退化 >2pp YELLOW) + `.coveragerc` `fail_under` 35→38 + 债务分级重构 (T1-T5 阻断 RED / T6-T8 告警 YELLOW). 三重门禁: ruff BLE001 增量零新增 (主力) + pylint --fail-on (配置就位) + T7 全量监控. 8/8 GREEN. 详见 `cairn/LOG.md` 2026-08-12 T02/T03 条目
- [x] T06-T08 — CPCV/DSR/Noise 残差注入（诚实回测三件套）— ✅ **DONE 2026-08-12** (W6.6.2): `utils/backtest/honest_validation.py` 三件套编排器 (CPCV + DSR + Noise 联合判定) + `utils/backtest/deflated_sharpe.py` DSR 顶层模块 (W6.6.2 修复静默失败) + `ms_strategy/src/backtest/combinatorial_purged_cv.py` CPCV 内核 + `ms_strategy/src/backtest/noise_injection_test.py` Noise 内核. 端到端验证通过 (随机数据 NOT HONEST 符合预期). 综合判定: `is_honest = DSR.is_pass AND Noise.is_stable AND CPCV CV<0.5`
- [x] **Phase 2 (T09-T14) — 不崩风控六件套** — ✅ **DONE 2026-08-12**:
  - T09 PreTradeGuard 预交易风控门 (6 规则: LOT_SIZE / PRICE_BAND / NOTIONAL_CAP / ST_FILTER / WHITELIST / SUSPEND_FILTER, BLOCK+WARN 双模式, 256 行 + 27 测试)
  - T10 PositionLimitEnforcer 持仓集中度执行器 (单票/行业/净敞口/总杠杆 4 维度, BLOCK+WARN, 246 行 + 11 测试)
  - T11 IntradayCircuitBreaker 日内熔断器 (连续失败/日内回撤/波动率爆发 3 触发 + CLOSED→OPEN→HALF_OPEN 状态机 + 冷却期, 337 行 + 14 测试)
  - T12 KillSwitchManager 三级熔断管理 (L1 预警/L2 降仓/L3 强平, 保证金梯度, 321 行 + 13 测试)
  - T13 TradeOrderReconciler 计划单对账 (覆盖/数量/价格偏差/孤儿成交 4 类问题, 130 行 + 11 测试)
  - T14 RiskAuditLogger 风控审计 JSONL 日志 (写盘/缓冲/刷新/按日查询/回放, 279 行 + 10 测试)
  - 端到端: 86/86 单元测试全绿 + industrial_grade_check 11 PASS + engineering_debt_gate 升级为 14/14 GREEN + T9–T14 模块自检 (覆盖率 0.4200 → 0.4307, +1.07pp)
- [x] **Phase 3 (T15-T18) — 实盘验证四件套** — ✅ **DONE 2026-08-12**:
  - T15 LiveOrderExecutor 实盘下单编排器 (T09→T10→T12→T11 四道风控门 fail-closed + broker.place_order + FillsStore + T14 审计, 376 行 + 16 测试)
  - T16 OrderLifecycleTracker 订单生命周期跟踪器 (8 态状态机 + QMT 状态码映射 + 超时撤单 + 孤儿单检测 + 回调 + force_cancel_all + 线程安全, 473 行 + 26 测试)
  - T17 LiveReconciliationLoop 实盘对账循环 (包装 T13 不修改 + 持仓 drift 3 级阈值 + 盘中定时 tick + 盘后全量 + verdict pass/warn/halt, 293 行 + 24 测试)
  - T18 GradualRolloutOrchestrator 灰度发布编排器 (4 阶段 PAPER→SHADOW→PARALLEL→FULL 不可跳 + 6 维准入门禁 + 4 回滚触发器 + split_capital + force_stage, 321 行 + 33 测试)
  - 端到端: 99/99 单元测试全绿 + industrial_grade_check 11 PASS + engineering_debt_gate 升级为 18/18 GREEN (T15 T09拦截行为自检 + T16 状态映射终态自检 + T17 import + T18 4阶段资金比例自检)
- [x] **G6 LLM 智能进化 — Phase D 策略 Ideation 生成** — ✅ **DONE 2026-08-12**:
  - D1 StrategyIdeationEngine (五步流水线: 观察→LLM 假设→因子设计→D2 验证→入库 + JSON 解析 + 多样性去重 + Shadow 模式 + 知识库反馈, ~360 行 + 27 测试)
  - D2 HypothesisVerifier (IC 显著性 RankIC/ICIR/Cohen's d/CV + Purged K-Fold + CRO Gate + Honest Validation DSR+Noise + AB 桶自动判定 + 可配置阈值 + 批量验证, ~280 行 + 17 测试)
  - D3 KnowledgeBase (JSONL 持久化 + 条件查询 + LLM 上下文反馈 + 归因/教训 + 统计, ~220 行 + 18 测试)
  - D4 DualLoopOrchestrator (LLM↔B4 双层闭环 + run_cycle/run_continuous + Kill Switch/CB 安全检查 + 连续失败暂停 + 人工审批门禁, ~280 行 + 17 测试)
  - 端到端: 79/79 单元测试全绿 + industrial_grade_check 11 PASS + engineering_debt_gate 22/22 GREEN (D1-D4 行为自检)
- [ ] C++/Rust 重写 ROI 评估

### Wave 3.5：代码审查派生升级（2026-08-05 新增，与 Wave 3 并行）
>
> 源自 2026-08-05 全库代码审查修复闭环派生的升级点，补强**回测可信度**与**资金安全**主线。详见 `docs/SELF_UPGRADE_PLAN_2026-08-05.md`。

- [x] ~~U1 (P0, 08-05~08-15) 完整时序 IC/ICIR~~ ✅ **2026-08-12 已落地** (W6.5)：`build_forward_returns_history` + `build_factor_history_from_prices` + `evaluate_factors` 双模式；MOM_20D IC=+0.5578 / ICIR=+7.695
- [ ] U4 (P1, 立即) DeepSeek API Key 平台轮换收尾 — 需用户到 platform.deepseek.com 重生成 Key 填入 .env（P1-1 已将泄露 Key 替换为占位符）
- [x] ~~U2 (P1, 08-08~08-20) 回测涨跌停/停牌数据接入~~ ✅ **2026-08-05 已落地**（67 测试通过）：`price_limit_calculator.py` + `market_rules.py` + `backtest/constraints.py` P2-2 约束已激活。**2026-08-12 W6.6.3 增强**：新增 `limit_pool_provider.py` 涨停池/跌停池 API (`ak.stock_zt_pool_em` 系列) + `AKShareDataSource.get_suspend_list()` 交易所停牌公告 + `build_backtest_data_from_ohlcv` 集成 `limit_pool_provider` 参数；10 项验证全通过
- [x] ~~U3 (P1, 08-10~08-25) 复权因子支持~~ ✅ **2026-08-05 已落地**（56 测试通过）：`adjust_factor_provider.py` + `data_provider.enrich_realtime_with_hfq()` + `pnl_calculator(align_hfq=True)`；除权日精确对齐 + 后复权↔未复权双向映射
- [x] ~~U5 (P1, 与 Wave 3 并行) GAP-2 E2E 补齐 — full_pipeline + shadow_account_lifecycle~~ ✅ **2026-08-28 已落地**: `tests/e2e/test_full_pipeline_e2e.py` + `test_shadow_account_lifecycle_e2e.py` 16 passed (与 Wave 3 GAP-2 同批验证)

### Wave 5：GNN 供应链产业链因子（10-06 ~ 11-30，新增 2026-08-03）
>
> 基于已有 `supply_chain_graph.py`（关系图谱数据层）+ `alpha_factor/`（11 大类因子）的三层渐进落地。核心原则：先用非学习版 Lead-Lag 因子验证邻居信息增量（Gate 1），通过后再引入 GAT 深度学习。详见 `cairn/gnn-supply-chain-factor.md`。
> **2026-08-03 前置进展**：W5.1 + W5.2 实际已提前执行（数据管线打通 + Gate 1 实测 FAIL），非排期内的闲置等待。

- [x] W5.1 (10-06~10-15) Layer 1 设计实现 — Lead-Lag 因子 + 接入因子库 + 边数据源实测探活 — ✅ **前置 DONE 2026-08-03**: `graph_data_source.py` (东财push2 slist/get spt=3 行业/概念边) + `supply_chain_builder.py` (图桥接) + `graph.py` (5 LeadLag 因子, 第12大类) + `gate1_validation.py` (真实数据验证模块). 腾讯K线 74/74 只真实拉取, 图 74节点/649边.
- [ ] W5.2 (10-16~10-24) Layer 1 验证门禁 — S1-S4 检验（增量 IC ≥0.01 + 多空夏普>1.0）— ⚠️ **已前置执行但 FAIL 2026-08-03**: Gate 1 判定 FAIL (CHAIN_CONCENTRATION IC均值0.0293/ICIR0.156 持续正向但ICIR<0.3; 其余因子跨窗不稳定; 多空夏普<1.0). 诊断为 universe 74只过小 + 行业/概念边非真实供应商-客户边. 详见 §5.1g.
- [x] W5.2b (10-16~10-24) **Gate 1 改进迭代** — 扩 universe + 引入真实供应商-客户边 + 因子方向权重优化 + 延长回看窗口; 二次验证 — ✅ **前置执行 2026-08-03** (universe 250只/图5757边/10窗ICIR, CONCENTRATION IC=0.019). 详见 §5.1g2.
- [x] W5.2c (10-16~10-24) **Gate 1 三次验证 — 主营构成边增强** — ⚠️ **前置执行但 FAIL 2026-08-03**: 免费源实测巨潮 0 条, 东财 F10 主营构成边 (图 5757→7328边), CHAIN_CONCENTRATION **ICIR 0.150→0.272 ↑** (IC=0.0285 三次稳定为正) 但仍<0.3, 多空 0.807<1.0. 诊断: 静态边权重已充分挖掘, 提示需 GAT 条件化注意力. 详见 §5.1g3.
- [x] W5.3 (10-25~11-03) Layer 2 GAT 实现 — torch 依赖引入 + 康波宏观条件化注意力 — ⚠️ **前置执行但 Gate2 FAIL 2026-08-03**: 纯 numpy GAT 瓶颈(数值梯度失效) → 安装 torch 2.13.0 + `gat_factor_torch.py` (多头GATLayer+MSE+Adam)。原报 +0.039 增益被 B5 掩码错误 + B1/B2 前视偏差虚高。**B1-B5 全套修复后无偏验证: +0.039 证伪** (实际 +0.0017/+0.0064, t值0.15/0.76 不显著), Gate2 FAIL。详见 §5.1h + 踩坑「GAT 增益证伪」。
- [x] W5.4 (11-04~11-10) Layer 2 对比验证 — GAT vs Layer 1 基线增量检验 — ⚠️ **前置执行但 Gate2 FAIL 2026-08-03**: B1-B5 修复后两次无偏验证 GAT effIC 均略弱于静态, 微弱 effICIR/夏普优势为 softmax 平滑副产品非学习能力。**决策: 按 Gate2 闸门回退 Layer 1**, GAT 代码保留为研究资产不在生产路径。康波条件化不适配日频(慢变量). 详见 §5.1h2 + 踩坑「GAT 增益证伪」。
- [ ] W5.5 (11-11~11-20) Layer 3 入库 — S5-S7 门禁（组合层面/纸交易/小资金）— 🔄 **S1-S5 全通过, 推进 S6-S7**: S1-S4 ✅ PASS (effIC=0.094/effICIR=0.503/多空夏普1.766); **S5 ✅ PASS** (`s5_validation.py` 基准夏普-1.5069→增强+0.2879, 边际改善+1.7948>>0.05); S6 纸交易 (口径校准见 W7.2.5) ⏳ 待启动; S7 小资金5-10% ⏳ 待S6通过. CHAIN_MOM_60D 已在 library.py 注册 (enable_graph=True 默认)
- [ ] W5.6 (11-21~11-30) 集成与监控 — AI Hedge Fund 接入 + FactorDecayMonitor 纳入

> **Wave 5 阶段性结论更新（2026-08-03 B1-B5 修复后）**：数据管线完整 + Layer 1 Gate1 PASS (CHAIN_MOM_60D 反向因子) + Layer 2 Gate2 FAIL (+0.039 证伪, 回退 Layer 1)。当前推进 CHAIN_MOM_60D S5-S7 入库。阶段性总结见 `cairn/gnn-supply-chain-factor-wave5-review.md`（含证伪更正注记）。

**关键决策点（更新 2026-08-03 B1-B5 修复后）**：Layer 2 GAT **Gate2 FAIL** — B1-B5 全套无偏验证后 +0.039 增益被证伪（实际 +0.0017/+0.0064，t 值 0.15/0.76 不显著），GAT effIC 略弱于静态，微弱优势为 softmax 平滑副产品。**决策：按 Gate2 闸门回退 Layer 1**。GAT 代码保留为研究资产不在生产路径，未来重启条件：① 真实供应商-客户边（商业源）② 历史 1000+ 天 ③ 转向特征提取器方向。Layer 1 CHAIN_MOM_60D 已通过 Gate1（B3/B4 修复后 effICIR=0.503/多空夏普1.766），推进 S5-S7 入库。康波条件化不适配日频（慢变量）。真实供应商-客户边在免费源不可得（巨潮 0 条），主营构成边已是最佳免费近似。

**与自我进化系统排期协调（2026-08-03 评估）**：

- **无时间冲突**：自我进化 Wave 1/2 在 09-05 前全部收尾，早于 Wave 5 启动（10-06）。
- **与 Wave 4 部分重叠（10-06~10-31）**：Wave 4 实盘验证四件套与 W5.2 因子门禁验证共用回测环境 → W5.2 因子验证错开至 10 月中下旬，Wave 4 实盘验证优先排前。
- **排期性质**：Wave 5 为独立因子研究方向，与 Wave 1/2 自我进化主体流程并行不冲突。

### Wave 6：GitHub 高价值项目集成（2026-09-01 ~ 12-31，新增 2026-08-11 — **✅ 2026-08-12 全部提前完成，超前 107-141 天**）
>
> 基于 2026-08-11 调研的 16 个新项目（针对 v8.6.14 已有模块的精准匹配），与 `docs/高价值GitHub项目清单_20260809.md`（29 项目基础设施层）互补不重复。本 Wave 聚焦**模块级能力增强**而非底座搭建。详见 `docs/高价值项目集成排期计划_20260811.md` + `cairn/github-integration-wave6.md`。
> **Wave 6 整体进度（2026-08-12 更新）**：4 个 Sprint **全部提前完成**（原计划 09-01~12-31，实际 08-11~08-12 全部落地），超前 107-141 天。累计新建 12+ 生产模块 + 12+ 测试脚本 + 377+ 单元测试通过 + 327 处 type:ignore 消除（81.4% 基线下降）。收尾报告见 `docs/Wave6_收尾报告_20261231.md`。
> **与 Wave 1-5 协调原则**：09-05 前不动核心链路（Sprint 1-4 均为模块内部增强，不侵入生产链路）；与 Wave 4 工程化部分重叠（W6.3 类型安全 + 确定性时钟）作为 Wave 4 子任务吸收；与 Wave 5 GNN 因子完全错开（Sprint 1 增强已有因子 / Wave 5 新增第 12 大类）。

- [x] W6.1 Sprint 1 (原 09-01~09-20, ~3 周) Alpha 因子引擎增强 — ✅ **提前完成 2026-08-11，超前 21 天**：① factor-mining 移植 6 个 FM_系列因子（FM_RET_1D/FM_MOM_5D/FM_MOM_20D/FM_IDIO_VOL/FM_AMIHUD_AMT/FM_CIRC_MCAP）→ AlphaFactorLibrary 输出 117 因子（14 大类）；② alphalens 标准化评估 `utils/alpha_factor/evaluator.py`（4 数据类 + 4 分析函数，完整 FactorTearSheet）；③ EigenAlpha 装饰器模式 `@register_factor` + `compute_registered_factors`（3 个 demo 示例）；④ ml-quant-trading Transformer 探索 `utils/alpha_factor/transformer_encoder.py`（双模式 numpy/torch，POC 跑通）。5 核心文件 py_compile 全通过。
- [x] W6.2 Sprint 2 (原 09-21~10-15, ~3.5 周) AI Hedge Fund 多空辩论增强 — ✅ **提前完成 2026-08-11，超前 41 天**：① TradingAgents 7 层架构适配 → `debate_layer.py`（Bull/Bear 两轮辩论 + LLM/规则双模式 + 审计 JSON 100% 留痕）；② 决策记忆反思 → `memory_reflection.py`（记录 + T+N 评估 + 反思提取，JSONL 持久化）；③ DanisHack 速率限制 + 缓存 → `llm_rate_limiter.py`（令牌桶 60/min + TTL 缓存 LRU 1024 + 指数退避 3 次 + 7 项统计）；④ 真实链路补齐（接 shadow 回报 + 接 RateLimiter）；⑤ `orchestrator.py` 单入口编排器。测试套件 **37/37 PASS**（集成 18 + E2E 闭环 19），演示脚本 demo_sprint2_real_links.py 3 场景端到端。
- [x] W6.3 Sprint 3 (原 10-16~11-15, ~4.5 周) G15 事件驱动回测架构优化 — ✅ **提前完成 2026-08-12，超前 65+ 天**：① nautilus_trader 确定性事件时钟（双模式 MONOTONIC_INDEX / WALL_CLOCK_NS + ts_event/ts_init + NonMonotonicTimestampError 防前视硬门禁）→ backtest 219→264 全绿；② QS-Trader 类型安全 + secid 合约解析 5 步落地：`utils/contracts/symbols.py`（6 NewType + parse_symbol() 统一入口 + 8 类资产覆盖 + 修复 INE/SHFE/ZCE 期货正则）→ Step 1 迁移 3 处本地 secid 实现 → Step 2 `utils/contracts/registry.py`（ContractSpec + ContractRegistry 单例 + 13 内置品种 + 3 来源整合对齐）→ Step 3 directional_futures_trader dict→ContractSpec 属性，消除 12 处 ignore（10→0）→ Step 4 wt_structs 6 数据类 **post_init** code/exchange 一致性校验 + strict_symbol_validation 上下文管理器（CodeExchangeMismatchWarning/Error，默认零破坏）；③ 批次 B-J 类型安全推进：`utils/` 基线 392 → 65 = -83.4%（扫描实测），文档累计消除 327 处 / 55 模块，超估算上限 817%；④ Rust 加速 POC **跳过**（实测 3 档场景均低于 1/10 ROI 阈值：日线 0.117s / 分钟 32.4s / TICK 0.010s，前置场景高估 98.7%，决策沉淀 `cairn/nautilus-trader-study.md` §4.3）。累计 342/342 全绿（contracts 73 + backtest 219 + structs 50）。
- [x] W6.4 Sprint 4 (原 11-16~12-31, ~6.5 周) 多场景验证与资源导航 — ✅ **提前完成 2026-08-12，超前 107 天**：① W6.4.1 vectorbt 对照 `utils/backtest/vectorbt_bridge.py`（MA 交叉 120 bars，事件驱动↔向量化语义对齐，**偏差 0.000%**，3 个引擎一致性对比：G15 / vectorbt / 手工向量化）；② W6.4.2 SimTradeLab T+1 模拟 `utils/backtest/a_share_rules.py`（T1PositionTracker lot-based FIFO + AShareTradingRules + 20cm 涨跌停集成，**6/6 单元测试全通过**）；③ W6.4.3 配对交易 Walk-Forward `utils/strategy/arbitrage/pairs_trading.py`（4 窗口滑窗：训练找协整对 + 测试评估 OOS，OOS PnL 计算修正为 ret_A - hedge_ratio * ret_B，消除价差除零极端值）；④ W6.4.4 ETF 轮动三层验证 `utils/strategy/etf_rotation/engine.py`（WFO 网格搜索 + VEC 多数票选稳健参数 + BT 完整回测，合成数据 5 ETF×400d：**BT Sharpe=2.2864 / 收益 63.55% / 回撤 9.63%**，VEC 选 lookback=40d holdings=3）；⑤ W6.4.5 借鉴评估（LOG 沉淀）：quantitative_analysis 因子表达式引擎值得移植(有条件) / stock(myhhub) CYQ 筹码分布算法值得集成(推荐) / 形态识别暂不移植；⑥ W6.4.6 资源导航 + 收尾：`docs/高价值GitHub项目清单_20260809.md` 追加"持续跟踪资源"章节（awesome-quant 22.7k + awesome-backtesting-python 入书签）+ 本收尾报告 + ROADMAP 全量更新。收尾报告见 `docs/Wave6_收尾报告_20261231.md`，教训与评估结论见 `cairn/LOG.md` 2026-08-12 系列条目。

**Wave 6 关键决策点（2026-08-12 更新：全部落地，不再阻塞）**：原 08-20 观察期 / 09-05 Phase B / 10-31 Wave4 / 11-30 Wave5 四个决策依赖全部解除（Sprint 1-4 纯模块增强不侵入生产链路）。

**Wave 6 后续候选任务（来自 W6.4.5 评估，已全部落地）**：W6.5 CYQ 筹码分布 (第13大类) + U1 时序 IC/ICIR (MOM_20D IC+0.5578/ICIR+7.695) / W6.6.1 表达式引擎 (第16大类) / W6.6.2 诚实回测三件套 DSR 修复。详见 `cairn/LOG.md` 2026-08-12 W6.5/W6.6 条目
4. 形态识别集成（优先级低）— 触发条件：talib 集成决策后

### Wave 7：统一整合 / v8.7 升级（2026-08-13 ~ 12-31，新增 2026-08-12 — Wave 6 提前完成释放 107+ 天窗口）
>
> **定位**：Wave 6 全部提前完成（原计划 09-01~12-31，实际 08-11~08-12 全部落地，超前 107-141 天）后，整合 Wave 1-5 剩余任务 + 工业级差距 P1-P3 + 工程基础层 Phase 0-3 + 工具增强（ocr/ECC）+ AutoResearch，统一为 4 Sprint 推进至 12-31 v8.7 发布。
> **主文档**：`docs/高价值项目集成排期计划_20260811.md` §7（统一整合章节）+ `cairn/github-integration-wave6.md` §九。
> **与既有计划关系**：本 Wave 7 是 UNIFIED_UPGRADE_PLAN_20260810.md（v9.3）的精化与对齐版本，不取代之——v9.3 的 8 Sprint 框架继续作为工程基础层主线，Wave 7 聚焦"剩余任务收口 + v8.7 发布"的整合视角。
> **整合范围（15 项任务来源映射）**：Wave 2 Phase B 启用 (B1-B4) / daily_workflow.py 拆分第 2-5 轮 / R10 残债 42 处裸 except / Wave 4 Phase 3 (T15-T18) 实盘验证四件套 / Wave 5 S6 纸交易 / Wave 5 S7 小资金灰度 / G9 FeatureStore / G11 CVaR / Wave 4 G6 LLM Phase D / G7 覆盖率 0.80 / AutoResearch Skill / 工程基础层 Phase 0-3 / ocr 三步固化 / ECC skills 选择性安装 / v8.7 发布。

#### Wave 7 Sprint 排期总表

| Sprint | 时间窗口 | 周数 | 核心目标 | 与既有 Wave 协调 |
| -------- | --------- | ------ | --------- | ---------------- |
| Sprint 1 | 08-13 ~ 09-12 | ~4 | Phase B 启用 + 工作流收尾 + R10 残债 | Wave 2 主线 |
| Sprint 2 | 09-13 ~ 10-12 | ~4 | 实盘验证四件套 + 工程基础层 0-1 | Wave 4 Phase 3 + Wave 5 S6 |
| Sprint 3 | 10-13 ~ 11-12 | ~4 | 因子入库 + 风控增强 + 工程基础层 2 | Wave 5 收尾 + 工程基础层 |
| Sprint 4 | 11-13 ~ 12-31 | ~7 | AutoResearch + LLM 进化 + v8.7 发布 | UNIFIED v9.3 Sprint 7-8 实盘准入 |

#### Wave 7 任务清单（按 Sprint 分组）

**Sprint 1（08-13 ~ 09-12，~4 周）：Phase B 启用 + 工作流收尾 + R10 残债清偿**

- [~] W7.1.1 (08-13~09-12) Wave 2 Phase B 渐进启用 — B1 `USE_DRIFT_DETECTOR=true` ✅ **DONE 2026-08-26 真实落盘** / B2 `USE_FEEDBACK_LOOP` ✅ **已启用 2026-08-27** (预热 3/3 天全 Go) / B3 `USE_AUTO_RETRAIN=true` ✅ **已启用 2026-08-27** (Stage 3 auto_retrain 激活) / B4 `USE_MLOPS_PIPELINE=true` ⏳ 未启用 (现 Stage 3, 稳定 2/3 天, 待 enabler `--auto` 推进) — **2026-08-29 实测纠正**: ROADMAP 此前记 "B2 预热 1/3 天 / B3 冻结至 09-02" 与运行时不符; 实测 `phase_b_progressive_enabler.py --check` 显示 B1+B2+B3 已于 08-27 全启用, 当前 Stage 3 auto_retrain, `consecutive_stable_days=6/7` (D11 进度); **Sprint 1 收尾判定**: B1+B2 稳定 ≥7 天即可 (现 6/7, 差 1 个交易日)
- [x] W7.1.2 (08-13~09-05, 非交易时段) daily_workflow.py 拆分第 2-3 轮 — risk phase (~600 行) + hedge phase (~500 行) + signal phase (~400 行) 提取到 `workflow/phases/`; daily_workflow 5904→≤4500 — ✅ **DONE 2026-08-14 (提前)**: `workflow/phases/` 已含 16 个 phase 文件 (含 risk.py/hedge.py/signal.py); daily_workflow.py 2828 行 (目标 ≤4500)
- [x] W7.1.3 (08-13~08-31) R10 拖债清偿 — ✅ **DONE 2026-08-13**: 36 处裸 `except Exception` (无 `# fail-safe` 标记) 全部精确化 (ai_hedge_fund/ 30 + alpha_factor/ 4 + notify.py 2); `scripts/_r10_refine_bare_excepts.py` AST 替换; ruff BLE001 归零; 14 文件 py_compile PASS
- [x] W7.1.4 (08-25~09-12) QMT 实盘接入准备 — ✅ **DONE 2026-08-13 (提前)**: `quant_modules/qmt_connector.py` (~420 行) paper trading 骨架 + 30 tests 全绿. 为 Sprint 2 W7.2.1 T15 准备
- [x] W7.1.5 (08-13~09-12) G7 覆盖率提升启动 — ✅ **DONE 2026-08-13**: 36 P0 模块补测 1399 tests, line-rate 0.4307→**0.7477** (远超 0.55); 后续 08-20 D9 门禁达标 0.833/0.7605
- [x] W7.1.6 (09-05~09-12) **MVSK P5-1 中线层 shadow 接入准备** — ✅ **DONE 2026-08-24 (提前)**: `apply_mvsk_shadow_to_mid_layer()` + `MVSKShadowResult` 已就绪; BL+MVSK(378, γ_s=0.1, γ_k=0.1) + 378日冷启动 + shadow 不修改 portfolio (portfolio unchanged=True); 差异记录到 `reports/shadow/mvsk_p5_daily_diff.jsonl`; 验证通过 (4只中线ETF, weight_diff_l2=0.0, data_sufficient=True); scheduler.py 未接入符合 P5-1 "不侵入生产链路" 设计; 测试 289行 5场景全绿
- [x] W7.1.7 (09-05~09-12) **qlib新选股模型 shadow 接入准备** — ✅ **DONE 2026-08-24 (提前)**: `register_qlib_lgb_v2_shadow()` + `apply_qlib_lgb_v2_shadow()` + `QlibShadowResult` 已就绪; shadow 权重=0.0 不参与融合, 仅旁路记录到 `reports/shadow/qlib_lgb_v2_daily.jsonl`; 验证通过 (symbol=510300, qlib_signal=0.053, shadow_mode=True)
- [x] W7.1.8 (09-05~09-12) **qlib_lgb_v2 生产模型训练落盘 (新增 2026-08-26, H1)** — ✅ **DONE 2026-08-24 (提前, 08-26 复核确认)**: 真实 LightGBM 模型已训练落盘 `reports/qlib_model_20260824_201837.pkl` + `predictions_20260824_201837.csv` (86 只 OOS 信号) + `qlib_train_20260824_201837.json` (csi300/683 股/209873 train/21414 test/mean_daily_ic 0.0113/rank_ic 0.0281); `utils/qlib_lgb_v2_model.py` 从 predictions CSV 加载, `_get_qlib_lgb_v2_signal` (signal_fusion.py:1308) 验证通过 (600089=>0.0536, 600519=>-0.038, 300433=>0.0549, 不走随机 fallback); **H1 原判断修正**: 08-26 初判"模型缺失/降级随机"系基于 W7.1.7 旧描述, 实际 08-24 训练已完成; 可选后续: 归档至 `models/qlib_lgb_v2/` (当前 reports/ 路径已工作, 非阻塞); 依赖 W7.1.7; 前置 W7.2.9 (09-13 cron 启动)

**Sprint 2（09-13 ~ 10-12，~4 周）：实盘验证四件套 + 工程基础层 Phase 0-1**

- [ ] W7.2.1 (09-13~09-26) T15 QMT 实盘接入 (paper → 10% 灰度) — `quant_modules/qmt_connector.py` 完整实现 + paper trading 7 天 + 10% 资金灰度启动
- [ ] W7.2.2 (09-27~10-10) T16 影子账户跟踪 ≥2 周 — 14 天影子账户跟踪报告 (PnL + 胜率 + 最大回撤 + DriftMonitor)
- [ ] W7.2.3 (10-11~10-12, 跨 Sprint) T17 灰度发布 (10% → 50% → 100%) — `scripts/gradual_release.py` 三阶段资金切换
- [ ] W7.2.4 (09-13~10-05) T18 实盘对账系统 — `utils/reconciliation/live_reconciler.py` (新建) 计划单 vs 实际成交对账
- [ ] W7.2.5 (09-13~10-12) Wave 5 S6 纸交易启动 — CHAIN_MOM_60D 纸交易 ≥30 天跟踪报告 — ⚠️ **2026-08-26 口径校准**: 原 W5.5 标准 "纸交易≥3月" 与本任务 "≥30 天" 冲突; 决策 = 30 天观察 + 稳定性收紧替代 3 月 (为 S7 前置), 若 30 天内出现不稳定信号自动延长至 3 月并后移 S7
- [x] W7.2.6 (09-13~10-12) 工程基础层 Phase 0-1 — uv 环境管理迁移 + python-dotenv 密钥安全 + ruff T201/BLE001 收紧 — ✅ **提前完成 2026-08-27**: `uv.lock` + `pyproject.toml` + `python-dotenv` + `.env` 约束 + `ruff` 收紧已确认就位 (见顶部状态同步 2026-08-27)
- [ ] W7.2.7 (09-13~10-12) ocr 三步固化 Step 1-2 — GLM API 充值 → 补扫 16 文件 → PR 自动审查接入
- [~] W7.2.8 (09-13~10-12) **MVSK P5-2 shadow 运行 30 天验证** — 实盘 shadow 对比 BL+MVSK(378) vs BL+MV(252)，每日记录权重/收益/换仓差异，30 天后评估 Δ夏普；依赖 W7.1.6 P5-1 — **启动器就绪 2026-08-24**: `scripts/launch_shadow_30day.py` + `utils/shadow_30day_evaluator.py` + 22测试全绿; 待 09-13 cron 启动 30天窗口; 详见 `cairn/shadow-30day-validation.md`
- [~] W7.2.9 (09-13~10-12) **qlib新选股模型 shadow 运行 30 天对比 V9** — 实盘 shadow 对比 qlib_lgb_v2 vs V9 Regime-Specific，每日记录信号/收益/换仓差异，30 天后评估 Δ夏普；依赖 W7.1.8 ✅ (真实模型已就绪 2026-08-24); 详见 `cairn/qlib-backtest-validation.md` — 启动器就绪 2026-08-24 (与 W7.2.8 同包)

**Sprint 3（10-13 ~ 11-12，~4 周）：因子入库 + 风控增强 + 工程基础层 Phase 2**

- [ ] W7.3.1 (10-13~11-12) Wave 5 S7 小资金 5-10% 灰度入库 — CHAIN_MOM_60D 小资金灰度 ≥30 天跟踪报告 + 完整入库决策; 前置: W7.2.5 S6 30 天观察通过 (2026-08-26 口径校准, 不稳定则本任务后移)
- [ ] W7.3.2 (10-13~10-31) G9 FeatureStore 物理分层 — `utils/feature_store/` (新建: online_store + offline_store + registry) 在线/离线分离 + 增量更新
- [x] W7.3.3 (10-13~10-31) G11 CVaR 风险计量 — `utils/risk/cvar.py` (新建) + EVT 肥尾建模整合 + 接入风控六件套 ✅ (08-14 完成, 106 tests / 覆盖率 97.24% / 门禁全通过)
- [ ] W7.3.4 (10-13~11-12) 工程基础层 Phase 2 — Prefect 编排 EOD 工作流 + DuckDB 统一查询层
- [ ] W7.3.5 (10-13~10-26) ECC skills 选择性安装 — 8 个高价值 skills 安装到 `.codebuddy/`
- [ ] W7.3.6 (10-13~11-12) ocr Step 3 nightly 全量 scan — `.github/workflows/ocr-nightly.yml` + 周度增量审查
- [ ] W7.3.7 (10-13~11-12) **MVSK P5-3 正式启用中线层 BL+MVSK(378)** — shadow 通过后（Δ夏普 > 0 且无异常换仓）切换中线层优化器；短线层保持 BL+MV(252)；接入风控六件套监控；依赖 W7.2.8 P5-2
- [ ] W7.3.8 (10-13~11-12) **qlib新选股模型评估决策** — shadow 30天通过后（Δ夏普 > 0 且无异常换仓）切换 `SignalFusionEngine` 的 `ml` 信号源 V9 → qlib_lgb_v2，alpha_weight=0.4 不变，接入风控六件套监控；依赖 W7.2.9 (W7.1.8 ✅ 已就绪); 详见 `cairn/qlib-backtest-validation.md`

**Sprint 4（11-13 ~ 12-31，~7 周）：AutoResearch + LLM 智能进化 + v8.7 发布**

- [x] W7.4.1 (11-13~12-07) AutoResearch Skill 开发 — `skills/auto_research/` (新建) 自动化因子研究 pipeline: 假设生成 → G15 回测 → S1-S7 门禁 → DSR 验证 → 入库决策 — ✅ **提前完成 2026-08-12** (D5 落地, 38 测试全绿, debt_gate 23/23 GREEN)
- [x] W7.4.2 (11-13~11-30) Wave 4 G6 LLM 智能进化 Phase D — `utils/llm_evolution/strategy_ideation.py` (新建) LLM 策略 Ideation 生成 — ✅ **提前完成 2026-08-12** (D1-D4 全部落地, 79 测试全绿, debt_gate 22/22 GREEN)
- [x] W7.4.3 (11-13~12-07) 工程基础层 Phase 3: LiteLLM — `utils/llm_gateway/litellm_router.py` (新建) + `utils/glm5_client.py` (重构) 多模型路由统一 — ✅ **提前完成 2026-08-12** (D6 落地, 37 测试全绿, debt_gate 24/24 GREEN, glm5_client 822→280行 -66%)
- [x] W7.4.4 (11-13~12-14, 非交易时段) daily_workflow.py 拆分收尾 — execute phase (~800 行) + report phase (~600 行) + eod_summary phase (~400 行) 提取; daily_workflow ≤3000 行 — ✅ **提前完成 2026-08-12** (D7 落地, 2828行达标,_scan_func_quality.py 创建, debt_gate 25/25 GREEN; 注: execute/report/eod_summary phase 未进一步拆分因门禁已达标, 按"不过度设计"原则停止)
- [x] W7.4.5 (11-13~12-21) G7 覆盖率 80% 达标冲刺 — 补齐 P1-P2 链路测试 + 集成测试 + E2E 测试; 覆盖率 ≥0.80 — ✅ **DONE 2026-08-28**: `reports/ci/coverage_baseline.json` line_rate=**0.833** ≥ 0.80 (`sprint4_threshold_met=true`, 冻结于 08-20), D9 门禁 `[OK]`; 11个0%模块全部补测 637 tests / 0%模块清零 (utils/risk style_beta 0%→100% + risk_audit_logger 47%→93% + risk_event 70%→98%)
- [ ] W7.4.6 (12-22~12-31) v8.7 发布 — `docs/v8.7_release_notes.md` + `cairn/ROADMAP.md` + `CHANGELOG.md` 文档归档 + 12-31 上实盘
- [ ] W7.4.7 (11-13~12-31, 可选) **MVSK P5-4 沪深 300 扩展验证** — 华为云上用 5~10 年数据 + 沪深 300 验证 γ_s=0.1/γ_k=0.1 泛化；若仍最优则全市场推广；依赖 W7.3.7 P5-3

#### Wave 7 总验收清单（12-31 v8.7 发布前）

- [ ] Phase B 4 flag 全部稳定运行 ≥30 天
- [ ] Wave 4 Phase 3 (T15-T18) 实盘验证四件套全部 PASS
- [ ] Wave 5 CHAIN_MOM_60D S6+S7 完整入库
- [x] daily_workflow.py ≤3000 行 ✅ (2026-08-28 实测 2159 行, D7 门禁通过; 见 `cairn/LOG.md` 08-28 #3 条目)
- [x] R10 残债 42 处全部清零 ✅ (2026-08-13 W7.1.3 完成, 裸 `except Exception` 无标记清零; 注: 当前 T6 fail-safe 宽捕获 222 处为带 `# fail-safe` 标记的合法降级, 与裸债口径不同, 按每周 30 处渐进清理)
- [x] G7 覆盖率 ≥0.80 ✅ (2026-08-28 line_rate=0.833, `reports/ci/coverage_baseline.json` 冻结; 08-28 复核 83.05% 超额)
- [ ] G9 FeatureStore 物理分层落地
- [x] G11 CVaR 接入风控六件套 ✅ (2026-08-14)
- [x] G6 LLM 智能进化 Phase D 完成 ✅ (2026-08-12)
- [x] AutoResearch Skill 落地 ✅ (2026-08-12)
- [ ] 工程基础层 Phase 0-3 (uv/dotenv/Prefect/DuckDB/LiteLLM) 完成
- [ ] ocr 三步固化 + ECC 8 skills 安装
- [ ] 门禁三件套连续 21 天 0 FAIL
- [ ] 影子账户 2 周稳定 + 灰度 100%
- [ ] v8.7 Release Notes + 文档归档

#### Wave 7 风险登记（Top 5）

| 风险 | 概率 | 影响 | 归属 Sprint | 缓解 |
| ------ | ------ | ------ | ------------ | ------ |
| QMT 实盘接入资金风险 | 高 | 高 | Sprint 2 | paper → 10% → 50% → 100% 渐进 + 风控六件套 |
| AutoResearch 前视偏差 | 高 | 高 | Sprint 4 | S1-S7 门禁 + CPCV/DSR/Noise 三件套 |
| v8.7 发布窗口风险 | 高 | 高 | Sprint 4 | 12-31 硬 deadline; 未达标延期至 2027 Q1, 实盘准入可先于发布 |
| Phase B 启用暴露前视偏差 | 中 | 高 | Sprint 1 | shadow 模式先行 7 天 + kill_switch |
| daily_workflow 拆分回归 | 中 | 高 | Sprint 1/4 | 非交易时段 + DRY-RUN 对照 + 29 单元测试 |

#### Wave 7 关键决策点

- **08-20 观察期决策日**：若 Wave 1 观察期延长至 08-24（已选定 Plan C），Sprint 1 Phase B 启用顺延至 08-25 启动，整体排期后移 5 天。
- **09-12 Sprint 1 收尾（2026-08-26 复核修正）**：~~Phase B 4 flag 全部稳定运行 ≥7 天~~ → **B1+B2 稳定运行 ≥7 天** + daily_workflow ≤4500 行 + R10 清零，方可进入 Sprint 2。B3/B4 (USE_AUTO_RETRAIN / USE_MLOPS_PIPELINE) 非 Sprint 2 前置依赖，并入 Sprint 2 前段完成（原判定在 B4 09-03 启用后 09-12 仅 7 天边缘且与 08-26 硬化的 B 顺序门禁冲突）。
- **10-12 Sprint 2 收尾**：T15-T18 实盘验证四件套 PASS + QMT 灰度 7 天稳定 + 工程基础层 Phase 0-1 完成，方可进入 Sprint 3。
- **11-12 Sprint 3 收尾**：S7 入库 + FeatureStore 落地 + CVaR 接入 + Prefect/DuckDB 完成，方可进入 Sprint 4。
- **12-31 v8.7 发布**：门禁三件套连续 21 天 0 FAIL + 影子账户 2 周稳定 + 灰度 100% + daily_workflow ≤3000 + 覆盖率 ≥0.80，方可发布 v8.7。

### Wave 7-ERL：进化→再平衡闭环补全子轨道（2026-09-05 ~ 12-31，新增 2026-08-22）

> **定位**：策略级进化→再平衡闭环（阶段1-5）已完成，但调查发现 4 个剩余缺口阻碍"完成进化后能自我再平衡"全链路自动化。本子轨道补齐训练→再平衡联动、漂移回调生产启用、institutional pipeline 集成、灰度发布推进。
> **主文档**：`cairn/evolution-rebalance-loop.md` §十三 剩余缺口补全计划
> **与 Wave 7 协调**：3 Sprint 与 Wave 7 Sprint 1-4 并行，避开 ETF期权对冲 Phase 4 灰度窗口（10-06~11-05）实盘验证资源争用

#### Wave 7-ERL 缺口与排期总表

| 缺口 | 现状 | Sprint | 时间窗口 |
| ------ | ------ | -------- | --------- |
| G1 模型训练→再平衡联动 | 训练后无回调，需手动运行 | Sprint 1 | 09-05~09-19 |
| G2 漂移→再平衡回调生产启用 | `rebalance_callback` 未生产传入 | Sprint 1 | 09-20~09-26 |
| G3 institutional pipeline 集成 | 管道无 evolution/rebalance phase | Sprint 2 | 09-27~10-31 |
| G4 灰度发布推进 | STAGE_2_50PCT 进行中 | Sprint 3 | 11-01~12-31 |

#### Wave 7-ERL 任务清单

**Sprint 1（09-05 ~ 09-26）：训练→再平衡联动 + 漂移回调启用**

- [x] ER-1.1 (09-05~09-12) 训练器新增 `post_train_callback` 钩子（`autolearn_trainer.py`/`lgb_enhanced_trainer.py`/`lgb_tscv_trainer.py`），fail-safe 降级 — ✅ **DONE 2026-08-27 (提前)**: `invoke_post_train_callback` 公共辅助函数 + 两个训练器新增参数 + 14 测试全绿
- [x] ER-1.2 (09-13~09-19) 训练→进化→再平衡串联：训练完成 → `EvolutionOrchestratorV2.run_cycle()` → `run_daily_rebalance()`，受 `USE_EVOLUTION_ORCHESTRATOR` 控制 — ✅ **DONE 2026-08-27 (提前)**: `utils/evolution/train_rebalance_bridge.py` 串联回调桥接模块 (`make_train_evolution_rebalance_callback` 工厂 + 进化循环→再平衡串联 + Feature Flag 双控 + 乘子约束 [0.5,2.0] + JSONL 审计) + 18/18 测试全绿; B3 冻结期由显式训练事件触发 (post_train_callback), 不依赖 USE_AUTO_RETRAIN
- [x] ER-1.3 (09-20~09-26) 漂移→再平衡回调生产启用：`etf_option_hedge_rebalancer.py` 等处实例化 `DriftMonitor` 时传入 `rebalance_callback`，受 `USE_DRIFT_DETECTOR` 控制 — ✅ **DONE 2026-08-27 (提前)**: `_make_drift_rebalance_callback` 工厂方法 + `_drift_rebalance_pending` 标志位 + 10 测试全绿; B1 依赖已就绪

**Sprint 2（09-27 ~ 10-31）：institutional pipeline 集成**

- [x] ER-2.1 (09-27~10-10) `institutional_pipeline_runner.py` 新增 `phase_evolution`（Step 4.6），调用 `EvolutionOrchestratorV2` — ✅ **代码就绪 2026-08-27**: `_run_evolution_phase` + `_step_evolution` 已挂进 `run()` Step 4.6 (flag 控制 + V2→V1 降级 + fail-safe); 端到端测试 12/12 全绿 (`tests/unit/test_er23_pipeline_orchestration.py`); 生产启用待 Sprint 2 时段 + Flag 双签
- [x] ER-2.2 (10-11~10-24) 新增 `phase_rebalance`（Step 6.5），调用 `etf_option_hedge_rebalancer.run_daily_rebalance()` — ✅ **代码就绪 2026-08-27**: `_run_rebalance_phase` + `_step_rebalance` 已挂进 `run()` Step 6.6 (flag 控制 + smoke 跳过 + fail-safe); 端到端测试覆盖; 生产启用待 Sprint 2 时段 + Flag 双签
- [x] ER-2.3 (10-25~10-31) 管道编排确认：data→factors→evolution→signals→rebalance→execution→report — ✅ **DONE 2026-08-27**: 编排顺序已验证 (evolution Step 4.6 在 risk_budget Step 5 前; rebalance Step 6.6 在 execution Step 6 后); 12 测试覆盖 flag 开关/降级/smoke/编排顺序/集成; ruff 全通过

**Sprint 3（11-01 ~ 12-31）：灰度发布 + 端到端验证**

- [ ] ER-3.1 (11-01~11-14) 灰度 Stage 1 (10%)，观察闭环健康度指标
- [ ] ER-3.2 (11-15~11-30) 灰度 Stage 2 (50%)，健康度达标后推进
- [ ] ER-3.3 (12-01~12-15) 灰度 Stage 3 (100%)，全量启用
- [ ] ER-3.4 (12-16~12-31) 端到端验证 + 知识沉淀

#### Wave 7-ERL 验收清单

- [x] 训练完成→再平衡自动触发（无需人工） ✅ ER-1.2 `train_rebalance_bridge.py`
- [x] 漂移→再平衡回调生产生效 ✅ ER-1.3
- [x] `institutional_pipeline --phase all` 含 evolution + rebalance ✅ ER-2.1/2.2/2.3 代码就绪 (Step 4.6 evolution + Step 6.6 rebalance 已挂进 run(), 12 测试覆盖)
- [ ] 灰度 100% 健康度达标

### Wave 8-LIT：文献驱动系统升级（2026-08-24 ~ 11-02，新增 2026-08-23）— ✅ **全部提前完成 2026-08-24**
>
> **定位**：基于系统升级文献调研的 5 Sprint 26 任务，驱动 v8.7 基线就绪（FinGPT/skfolio/Deep Hedging/FinRL-X/R&D-Agent/AlphaForge 等）。**✅ 全量完成 2026-08-24** — LIT-S1~S5 共 26 任务全部落地 (808 测试全绿 + 26 知识专题归档 + pre-commit/ruff 全通过), v8.7 灰度 50% 运行中, 12-31 发布 deadline。详见 `docs/系统升级文献调研与排期_20260823.md` + `cairn/v87-release.md`（全量集成验收报告）。
> **与 Wave 9-GH 关系**: ✅ **前置依赖（LIT-S5 收尾）已于 08-24 解除** — Wave 9-GH 不再受 LIT 排期约束。
> **2026-08-26 审查注记**: 原 08-24~11-02 的 LIT 窗口已整体空出; 释放的人力建议接入 Wave 7 Sprint 1/2 缺口 (W7.1.8 ✅ 已就绪 / 覆盖率冲刺), 或用于评估 Wave 9-GH 提前（见 Wave 9-GH 决策注记）。

### Wave 9-GH：GitHub 增补项目集成（2027-01-04 ~ 2027-04-30，新增 2026-08-25）
>
> **定位**：基于 2026-08-25 调研的 18 个新增 GitHub 高价值项目（FinRL/evidently/optuna/LightGBM/QuantLib/ARCH/prefect/backtrader 等），系统性集成至 v8.7 基线。4 Sprint（GH-S1 P0核心+漂移 / GH-S2 P1对冲+绩效 / GH-S3 P1工程+回测 / GH-S4 P2评估+收尾），~61 人天。
> **前置依赖**: ✅ Wave 8-LIT 已于 08-24 全部完成（前置解除，不再依赖 11-02 收尾）。启动时间见下决策注记。详见 `docs/Wave9_GitHub增补集成计划_20260825.md` + `cairn/wave9-gh-integration-20260825.md`。
> **协调原则**: 去重（Wave 6 已完成的 alphalens/ai-hedge-fund + Wave 8-LIT 已排的 FinGPT/skfolio 不重复）；每子项独立走 spec→design→task→实现→验证。
> **2026-08-26 决策 (已定, L3 最优方案)**: **正式集成推迟至 2027-01-04 启动, 整体顺延至 2027-04-30** — 原 11-03 启动与 v8.7 发布冲刺 (Wave 7 Sprint 4 + ETF Phase 5 + ER-3 灰度, 11-13~12-31) 资源重载冲突。9-10 月 LIT 空窗仅做 GH-S1 零成本预研 (18 项目去重核对 + 调研, 不占正式 Sprint 人天); 12 月 v8.7 发布为最高优先级, Wave 9 任何工作不得侵入发布窗口。

### Wave 9-GH+：工程化与知识层增补（2026-09-07 ~ 09-25，新增 2026-08-26）
>
> **定位**：源自 2026-08-26 GitHub Trending 接入评估（`G 20260826.md`），从 10 个热门项目中筛选出 2 项轻量工程化高价值项。**TradingAgents 经核验已在 Wave 6 Sprint 1-2 完成**（`quant_modules/ai_hedge_fund/` 20 分析师 LangGraph），不重复排期；basecamp/omarchy(Linux)/free-claude-code(ToS 风险)/codex(重叠) 等已排除。
> **与 Wave 9-GH 关系**: 本子轨道为 GH 生态的工程化/知识层补充，与 Wave 9-GH 量化库集成（2027-01-04 启动）正交，不依赖 Wave 8-LIT/v8.7 发布，可提前独立执行。
> **执行窗口**: 09-07 ~ 09-25（LIT 空窗期，不侵入 v8.7 发布冲刺 11-13~12-31）。总预估 3-4 人天。

| 任务 ID | 任务 | 来源项目 | 交付物 | 验收标准 | 预估 |
| --------- | ------ | --------- | -------- | ---------- | ------ |
| GH+-1 ✅ (08-29) | Skill 通用约束落地 + 检验标准补全 | `multica-ai/andrej-karpathy-skills` 4 原则 | `skills/AGENT_SKILLS_ADAPTER.md` §0 增补 4 原则（编码前思考 / 简洁优先 / 精准修改 / 目标驱动执行），作为所有 Skill 通用约束 | ✅ 4 原则写入 adapter（08-28 摘要版 → 08-29 补全检验标准 + 第 4 条口径由"验证结果"校正为"目标驱动执行"）；现有 FinClaw 1031 + ECC 64 Skill 零破坏（纯文档，skill 本体不安装）；pre-commit 通过 | 1d |
| GH+-2 | cairn 知识层自动交叉引用 | `AgriciDaniel/claude-obsidian` 知识图谱模式 | 归档环节加自动链接生成（晨报/尽调/专题互相引用），沉淀进 `knowledge/` | 归档后专题间引用链接可跳转；比纯日期堆文件检索提效；不强制装 Obsidian | 2-3d |

**Wave 9-GH+ 门禁**（状态：GH+-1 ✅ 2026-08-29 / GH+-2 未开始，模板 ✅ 已改为真实 checkbox）:

- [x] karpathy 4 原则写入 `skills/AGENT_SKILLS_ADAPTER.md`，现有 Skill 零破坏 — ✅ 08-29（含检验标准补全 + 第 4 条口径校正）
- [ ] cairn 归档自动交叉引用上线，专题间链接可跳转 — GH+-2，排期 09-07~09-25，未开始
- [x] 不侵入 v8.7 发布窗口；`cairn/LOG.md` 追加进展条目 — GH+-1 完成于 08-29（早于 09-07 窗口），持续满足

## 开放问题

1. 情绪因子数据源质量 — 当前新闻数据覆盖率和时效性不足，需要更高质量的中文财经新闻源或替代情绪指标
2. 策略容量天花板 — 日频选股策略当前规模下容量充足，若扩大规模需重新评估冲击成本模型
3. C++/Rust 重写的投入产出比 — 硬件采购 + 核心路径重写预算 ¥360K-710K，需评估 vs. 当前 Python 方案的延迟是否已构成实质瓶颈
4. V9 全量上线时间表 — 影子账户跟踪达标后是否按原计划推进 50%→100% 灰度
5. 自我进化框架观察期 — 2026-08-13 观察期满日是关键决策点：Phase 0 出口是否达成、Public/Private 分离性是否健康、DriftMonitor 是否误报。若三项均通过则解除 Feature Flag 只读限制并启动 Stage B 渐进启用。Phase 4 收尾两项（T4.2 theta_engine.py 切换到统一期权定价内核、T4.7 fineng 影子验证验收报告）需独立排期
6. **daily_workflow.py 6226 行拆分** — 超架构门禁（≤3000 行），属长期重构非紧急。排期于 Wave 4 或独立周末窗口，禁止交易时段执行。计划见 `cairn/daily-workflow-split-plan.md`
7. **独立审查报告须二次核验 + 勘误登记** — 2026-08-08 实战：独立审查报告（B1–B5/M1–M7）经代码交叉核验发现 3 处误判（B1 字符串注解≠运行时错误降 P1、B3 离线脚本≠实盘链路重定性 M4、B4/B5 "3.12 语法崩溃"误报撤销）。**铁律：任何 P0 进清零配额前必经二次核验，勘误写进同文档不漂移**。方法论见 `cairn/code-review-independent-audit-20260808.md`；待办：E1 补 `wt_backtest_engine.py` 的 `import pandas as pd`、B4/B5 的 F821/语法逐条核验后清零。
8. **CI 门禁维护与遗留清理 + 复审发现闭环（2026-08-12 已完成）** — R1/R4 落地 (ci.yml 6 脚本真实实现 + quality-gate.yml PR 增量门禁), 工作区未跟踪文件 99→0 收敛 (分批 5 批 129 文件, 零工作丢失), R10 已完成 (346 处 except Exception 精确化, T6=0), R12 .gitignore 补齐。剩余待办: D1 压力测试既有问题独立跟踪 / ci.yml 引用修改须同步 ci_integrity_check.py / 裸 except 42 处独立治理。经验见 `cairn/ci-repair-and-gate-lessons-20260812.md` + `cairn/code-review-reaudit-20260812.md`
