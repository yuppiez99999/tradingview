---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-09-03
updated: 2026-09-03
contains: github-trending, wave13, wave12-dedup, star-baseline, deprecation-list, g1-qmt-conflict
related:
  - docs/github_integration_plan_wave12_20260830.md
  - cairn/github-trending-wave11-20260829.md
  - cairn/github-integration-wave6.md
  - cairn/rd-agent-quant.md
  - cairn/nautilus-trader-study.md
  - cairn/ROADMAP.md
---

# Wave 13：GitHub 项目实时快照校正与去重裁决（2026-09-03）

> 2026-09-03 用 GitHub Search API 拉取 33 个项目**实时 star / 最近 push** → 与 Wave 12 排期去重 → 冲突裁决。
> **本文档不是新代码轨道**，而是给既有 Wave 12-B 提供事实基线（真实星标 + 维护活跃度）与优先级重排依据，并产出零成本立即生效的**劝退清单**。
> 排期载体仍是 `docs/github_integration_plan_wave12_20260830.md`（本次在其 §9 追加校正章节）。

## 一、与前几波的口径差异

| 波次 | 数据源 | 指标 | 局限 |
|------|--------|------|------|
| Wave 9 / 11（08-19 / 08-29） | GitHub Trending weekly | **周增 star** | 热度≠成熟度和维护度，热榜新项目占比高 |
| Wave 12（08-30） | `GitHub高价值项目主表`（123 项目） | 领域覆盖 | 无真实星标与维护状态，未做停更判定 |
| **Wave 13（本次）** | **GitHub Search API 实时查询** | **累计 star + 最近 push_at** | 项目数少（33），非全量扫描 |

**本次新增的、前几波都没有的判据 = 维护活跃度（`pushed_at`）**。它直接产出劝退清单，是零成本但立即生效的决策资产。

## 二、实时事实基线（2026-09-03 拉取）

### 2.1 量化框架 / 回测

| 项目 | Star | 语言 | 最近 push | 维护状态 | 本地现状 |
|------|------|------|-----------|---------|---------|
| [freqtrade/freqtrade](https://github.com/freqtrade/freqtrade) | 53,972 | Python | 2026-09-03 | 活跃 | — |
| [microsoft/qlib](https://github.com/microsoft/qlib) | 48,226 | Python | 2026-09-02 | 活跃 | **本地已有 `qlib/`** |
| [vnpy/vnpy](https://github.com/vnpy/vnpy) | 45,078 | Python | 2026-09-01 | 活跃 | 已在 12-B3（03-08） |
| [mementum/backtrader](https://github.com/mementum/backtrader) | 23,126 | Python | **2024-08-19** | ⚠️ 停更 ~2 年 | — |
| [QuantConnect/Lean](https://github.com/QuantConnect/Lean) | 21,466 | C# | 2026-09-02 | 活跃 | — |
| [quantopian/zipline](https://github.com/quantopian/zipline) | 20,078 | Python | **2024-02-13** | ⚠️ 停更 2.5 年 | — |
| [bbfamily/abu](https://github.com/bbfamily/abu) | 18,535 | Python | 2026-01-24 | ⚠️ 半年未更 | — |
| [polakowo/vectorbt](https://github.com/polakowo/vectorbt) | 8,972 | Python | 2026-08-02 | 活跃 | 已在 12-B2（02-09） |
| [ricequant/rqalpha](https://github.com/ricequant/rqalpha) | 6,743 | Python | 2026-09-02 | 活跃 | — |
| [fasiondog/hikyuu](https://github.com/fasiondog/hikyuu) | 3,485 | C++ | 2026-09-02 | 活跃 | — |
| [akfamily/akquant](https://github.com/akfamily/akquant) | 2,250 | Python/Rust | 2026-09-03 | 活跃 | — |
| [wondertrader/wtpy](https://github.com/wondertrader/wtpy) | 1,487 | Python | **2025-08-06** | ⚠️ 停更 1 年 | — |

### 2.2 AI + 金融 Agent

| 项目 | Star | 语言 | 最近 push | 维护状态 | 本地现状 |
|------|------|------|-----------|---------|---------|
| [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) | 102,381 | Python | 2026-09-01 | 活跃 | **本地已有 `TradingAgents/`** |
| [OpenBB-finance/OpenBB](https://github.com/OpenBB-finance/OpenBB) | 72,637 | Python | 2026-07-30 | 活跃 | 已在 12-B2（02-02） |
| [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) | 63,209 | Python | 2026-08-07 | 活跃 | **本地已有 `ai-hedge-fund/`** |
| [AI4Finance-Foundation/FinGPT](https://github.com/AI4Finance-Foundation/FinGPT) | 21,205 | Notebook | 2026-09-03 | 活跃 | Wave 8-LIT 已排 |
| [AI4Finance-Foundation/FinRL](https://github.com/AI4Finance-Foundation/FinRL) | 16,203 | Notebook | 2026-07-13 | 活跃 | Wave 9-GH 已排 |
| [microsoft/RD-Agent](https://github.com/microsoft/RD-Agent) | 14,442 | Python | 2026-09-02 | 活跃 | 已在 12-B3（02-23） |
| [OpenByteInc/QuantDinger](https://github.com/OpenByteInc/QuantDinger) | 11,309 | Python | 2026-09-03 | 活跃（2025-12 新建） | — |
| [The-Swarm-Corporation/AutoHedge](https://github.com/The-Swarm-Corporation/AutoHedge) | 4,339 | Python | 2026-05-11 | 一般 | — |
| [0xemmkty/QuantMuse](https://github.com/0xemmkty/QuantMuse) | 2,907 | Python | **2025-07-29** | ⚠️ 停更 1 年+ | — |

### 2.3 数据源与数据工程

| 项目 | Star | 语言 | 最近 push | 维护状态 | 本地现状 |
|------|------|------|-----------|---------|---------|
| [duckdb/duckdb](https://github.com/duckdb/duckdb) | 40,957 | C++ | 2026-09-03 | 活跃 | 已在 12-B2（01-26） |
| [pola-rs/polars](https://github.com/pola-rs/polars) | 39,616 | Rust | 2026-09-03 | 活跃 | 已在 12-B2（01-26，与 DuckDB 同期） |
| [akfamily/akshare](https://github.com/akfamily/akshare) | 22,388 | Python | 2026-09-02 | 活跃 | **已在用（数据源 P3）** |
| [waditu/tushare](https://github.com/waditu/tushare) | 15,383 | Python | **2024-03-13** | ⚠️ 停更 2.5 年，758 open issue | — |

### 2.4 通用 Agent 工程（可迁移到编排层）

| 项目 | Star | 语言 | 最近 push | 备注 |
|------|------|------|-----------|------|
| [obra/superpowers](https://github.com/obra/superpowers) | 281,079 | Shell | 2026-08-31 | Agentic skills 框架 |
| [langchain-ai/langchain](https://github.com/langchain-ai/langchain) | 145,566 | Python | 2026-09-03 | — |
| [FoundationAgents/MetaGPT](https://github.com/FoundationAgents/MetaGPT) | 70,189 | Python | 2026-01-21 | — |
| [microsoft/autogen](https://github.com/microsoft/autogen) | 60,785 | Python | 2026-04-15 | 1031 open issue |
| [crewAIInc/crewAI](https://github.com/crewAIInc/crewAI) | 58,038 | Python | 2026-09-03 | — |
| [HKUDS/nanobot](https://github.com/HKUDS/nanobot) | 47,678 | Python | 2026-09-03 | 2026 新晋 |
| [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) | 40,982 | Python | 2026-09-03 | **AI Hedge Fund 已在用** |
| [openai/openai-agents-python](https://github.com/openai/openai-agents-python) | 29,168 | Python | 2026-09-02 | — |
| [deepset-ai/haystack](https://github.com/deepset-ai/haystack) | 26,401 | Python | 2026-09-03 | — |

### 2.5 策略研究资料

| 项目 | Star | 最近 push | 用途 |
|------|------|-----------|------|
| [paperswithbacktest/awesome-systematic-trading](https://github.com/paperswithbacktest/awesome-systematic-trading) | 14,124 | 2026-09-03 | 系统化交易 Awesome 清单 |
| [je-suis-tm/quant-trading](https://github.com/je-suis-tm/quant-trading) | 10,669 | 2026-06-20 | 20+ 经典策略从零实现，可对拍自研因子 |
| [cybergeekgyan/Quant-Developers-Resources](https://github.com/cybergeekgyan/Quant-Developers-Resources) | 3,748 | 2026-08-15 | 量化知识图谱 |
| [Barca0412/Introduction-to-Quantitative-Finance](https://github.com/Barca0412/Introduction-to-Quantitative-Finance) | 1,722 | 2026-09-02 | 中文 AI+金融教程 |

## 三、与 Wave 12 的去重裁决（本文档核心）

### 3.1 本次 Top-5 推荐 vs Wave 12-B 排期 —— 4/5 已排，无需新建轨道

| 本次推荐 | 对应 Wave 12-B 条目 | 裁决 |
|---------|-------------------|------|
| vnpy Gateway 抽象 → 解 G1 QMT | #6 vnpy 实盘交易适配（03-08~03-14，5 人天） | **保留 12-B3 排期**；但 G1 冲突需单独裁决，见 §4 |
| RD-Agent → 解 B3 auto_retrain | #7 RD-Agent 因子自动发现（02-23~03-07，8 人天） | **保留 12-B3**；补一条：其量化场景 RD-Agent(Q) 亦可对标 B3，非仅因子发现 |
| DuckDB + Polars → G9 数据分层 + 治 OpenBLAS 内存爆 | #10 DuckDB 批量分析（01-26~02-01，2 人天） | **12-B2 补挂 Polars**（原条目只写 DuckDB，漏了 Polars）；合并人天 2.0→3.0 |
| vectorbt → DSR/参数敏感性体检 | #11 vectorbt 向量化回测（02-09~02-15，3 人天） | **保留 12-B2**；验收标准补充"用于复核 MVSK/qlib 的 DSR 与参数敏感性结论" |
| LangGraph checkpoint / human-in-the-loop | 无 | **不新增依赖**——LangGraph 已在用，属"用足既有能力"而非引入新项目，见 §5 |

**结论**：本次调研**不产生 Wave 13 代码子轨道**。33 个项目中 12 项本地已有或前波已排，5 项劝退，16 项登记为 2027 候选池。

### 3.2 本地已存在、禁止重复引入（4 项）

| 目录 | 对应项目 | 说明 |
|------|---------|------|
| `qlib/` | microsoft/qlib (48,226) | Wave 6 Sprint 3 / W7.1.7-1.8 已集成 |
| `TradingAgents/` | TauricResearch/TradingAgents (102,381) | Wave 6 Sprint 2 已集成 `quant_modules/ai_hedge_fund/` |
| `ai-hedge-fund/` | virattt/ai-hedge-fund (63,209) | 同上 |
| `TencentDB-Agent-Memory/` | TencentCloud/TencentDB-Agent-Memory | 独立第三方项目，非本系统集成目标 |

## 四、冲突裁决：G1 QMT vs 「2027 前不引入新 GitHub 项目」

### 4.1 冲突陈述

- **09-02 决策 2**（`cairn/ROADMAP.md` §稳定观察期与运营收敛决策）：Wave 11-A 推迟 2027，执行"**2027 前不引入新 GitHub 项目 / 新框架 / 新模型**"。
- **G1 QMT 真实下单**是 12-31 上实盘的 P0 阻塞项（记忆 ID 23032726：`broker_factory.py` + `system_config.json` broker 段已就绪，但 `xtquant` 未安装、真实下单仍挂 `dry_run`）。
- vnpy（45,078 star，活跃）本是最成熟的 A股/CTP Gateway 抽象参照。

### 4.2 裁决（本次拍板，采用"引设计不引依赖"）

| 维度 | 结论 |
|------|------|
| **是否 pip install vnpy** | **否**。违反 09-02 决策 2，且 vnpy 自带完整交易生态会与 `automated_execution_system.py` / `OrderRouter` / `broker_factory.py` 三层既有结构冲突，替换成本高 |
| **是否参考其设计** | **是**。仅提取 `Gateway` 抽象层设计（连接/订阅/下单/撤单/回报回调 + 订单状态机 + 合约信息缓存），写成设计对照笔记落 `cairn/`，用于校验 `broker_factory.py` 的四重门控是否缺状态机与回报回调 |
| **G1 真实路径不变** | 仍走 `quant_modules/qmt_connector.py` + `utils/execution/broker_factory.py` + `xtquant`（待装），Phase 4 前保持 `dry_run` |
| **12-B3 vnpy 条目处理** | 保留但**降级为"设计对照"**，人天 5.0 → 1.0（只产出对照笔记，不做 CTP 适配层）；释放 4 人天 |

**理由**：G1 的真实阻塞是 `xtquant` 未安装 + 账号未配（环境与运营问题），**不是缺 Gateway 抽象**（`broker_factory.py` 已实现四重门控）。引入 vnpy 属于用代码方案解决环境问题，是典型的解决错层。

## 五、LangGraph 一条不进 12-B 的理由

LangGraph（40,982）的 checkpoint + human-in-the-loop 是本次"立即可用"推荐，但**它已在用**（`quant_modules/ai_hedge_fund/` 20 分析师 LangGraph 编排）。因此：

- 不属"引入新项目"，无需进 12-B，也不违反 09-02 决策 2
- 应作为**既有能力用足项**登记：`ai_coordinator.py` 的人工 confirm 链路可改用 LangGraph 原生 checkpoint / interrupt，替代自研状态持久化
- 归入 2027 v8.7.1 或 Wave 12-B 期间顺手做，人天不计入 Wave 12 预算

## 六、劝退清单（零成本、立即生效）

> 判据：`pushed_at` 距今 > 12 个月，或 open issue 数量级异常。以下项目**禁止作为生产依赖新增**，仅可读源码参考。

| 项目 | Star | 最近 push | 停更时长 | 附加风险 |
|------|------|-----------|---------|---------|
| zipline | 20,078 | 2024-02-13 | 2.5 年 | 原 Quantopian 已关停 |
| backtrader | 23,126 | 2024-08-19 | 2 年 | — |
| tushare | 15,383 | 2024-03-13 | 2.5 年 | **758 open issue**，积分制 Token |
| wtpy | 1,487 | 2025-08-06 | 1 年 | — |
| QuantMuse | 2,907 | 2025-07-29 | 1 年+ | — |
| abu | 18,535 | 2026-01-24 | 7 个月 | 边缘观察，暂不新增依赖 |

**与 Wave 12 §1.3 的关系**：Wave 12 已列"维护停滞 12 项"但未点名，本节补齐具体名单与判据，作为 `docs/高价值GitHub项目清单_20260809.md` 与 Wave 12-B 立项前的**准入前置检查表**。

## 七、2027 候选池（本次新增，未进 12-B）

> 性质：登记观察，需独立走 spec→design→task→实现→验证，不自动进入任何 Sprint。

| 项目 | Star | 候选接入点 | 前置条件 |
|------|------|-----------|---------|
| freqtrade | 53,972 | dry-run ↔ live 双模切换机制参照 | 12-31 实盘灰度前设计评审 |
| QuantConnect/Lean | 21,466 | 逐档撮合 / 订单簿建模（G10 参照） | G10 重启（TICK 延迟成瓶颈） |
| akfamily/akquant | 2,250 | Rust 内核 + Python 接口的 G10 捷径 | 与 `nautilus-trader-study.md` 结论对拍（该研究已证伪 Rust 加速 ROI） |
| fasiondog/hikyuu | 3,485 | C++ 内核国产化替代参照 | 同上 |
| FinGPT | 21,205 | 金融垂类情感（补舆情维度） | Wave 8-LIT 已排，核对后再定 |
| QuantDinger | 11,309 | 多租户 + 计费骨架（若产品化对外） | 产品化决策明确后 |
| AutoHedge | 4,339 | 与 `HedgeEngine` 同赛道对照 | — |
| openai-agents-python | 29,168 | `ai_coordinator.py` handoff/guardrail 对标 | — |
| nanobot | 47,678 | 对话式运维入口 | 运营中心 Dashboard 二期 |
| stumpy 等（Wave 12-A 已完成） | — | — | — |

**akquant / hikyuu 特别注记**：二者均为"C++ 内核 + Python 接口"，正是 G10 差距项。但 `cairn/nautilus-trader-study.md` §4.3 已实测**三档场景 ROI 均低于 1/10 阈值**（日线 0.117s / 分钟 32.4s / TICK 0.010s，前置场景高估 98.7%），G10 于 08-26 已证伪搁置。故二者**只登记不排期**，重启条件不变：TICK 级实盘实测延迟成为瓶颈。

## 八、决策记录

1. **为何不新建 Wave 13 代码轨道**：本次 Top-5 中 4 项已落在 Wave 12-B，新建轨道会造成同一项目两处排期、人天重复计算（历史教训见 `cairn/completion-claim-vs-actual-state-20260829.md` 的"状态声明与实物分离"失真）。
2. **为何用 `pushed_at` 而非 star 做主判据**：star 是累计量，对 2012-2017 年的老项目天然有利（zipline 20k / backtrader 23k 看着很高实则已停更）。维护活跃度才是生产依赖的准入硬条件。
3. **为何 G1 走"引设计不引依赖"**：G1 的真实阻塞是环境（xtquant 未装）+ 运营（账号未配），不是抽象层缺失；且 09-02 决策 2 是用户拍板约束，须遵守。设计对照笔记的成本（1 人天）只有原 CTP 适配（5 人天）的 1/5，且零环境风险。
4. **为何劝退清单算"交付物"**：它不产生代码，但直接阻止未来重复踩坑（如 Wave 12 §1.3 "维护停滞 12 项"未点名，立项时仍可能误选），符合"零成本立即生效"的支线价值标准。

## 九、后续动作

- **立即（无成本）**：劝退清单作为 GitHub 项目准入前置检查表；本地已存在 4 项标注到 Wave 12 §1.3 "已有替代"
- **12-B2 启动时（2027-01-26）**：DuckDB 条目补挂 Polars，人天 2.0→3.0；vectorbt 验收补 DSR 复核口径
- **12-B3 启动时（2027-02-23）**：vnpy 条目按 §4.2 降级为设计对照，人天 5.0→1.0
- **12-B 全部收尾（2027-03-21）**：`cairn/LOG.md` 追加验收记录 + 本文档状态更新

## 十、关联文件

| 文件 | 职责 |
|------|------|
| `docs/github_integration_plan_wave12_20260830.md` | 排期正文（本次在其 §9 追加 09-03 校正章节） |
| `cairn/github-trending-wave11-20260829.md` | Wave 11 决策沉淀（周增 star 口径，前置参考） |
| `cairn/rd-agent-quant.md` | RD-Agent 量化场景既有沉淀 |
| `cairn/nautilus-trader-study.md` | G10 C++/Rust 证伪依据（akquant/hikyuu 不排期的依据） |
| `cairn/ROADMAP.md` §GitHub 集成 Wave 12/13 收敛 | 结论事实源 |
| `docs/排期计划总览_20260826.md` | Wave 注册表（补 Wave 12/13 两行） |
