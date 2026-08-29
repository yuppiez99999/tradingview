---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-28
updated: 2026-08-28
contains: experience-context-layer, event-store, append-only-journal, experience-retrieval, github-wave10, openviking-pattern, maka-event-sourcing
related:
  - cairn/self-evolution-framework.md
  - cairn/evolution-rebalance-loop.md
  - cairn/architecture-map.md
  - docs/Wave10_经验上下文层集成计划_20260828.md
---

# 经验上下文层（ECL, Experience Context Layer）设计方案

> 记录"自我进化框架"缺失的经验记忆层设计：append-only 决策事件日志 + 跨交易日经验检索。来源于 2026-08-28 GitHub 周热门项目筛选（5 个相关项目：OpenViking / maka / claude-plugins-community / WeMM-Embedding / openhuman），排期见 `docs/Wave10_经验上下文层集成计划_20260828.md`。

## 一、背景与动机

`cairn/self-evolution-framework.md` 已实现"检测→诊断→进化→验证"闭环（DriftMonitor → StrategyEvaluator → AutoRetrainScheduler → FeedbackLoop，24/29 任务完成），但存在一个结构性缺口：**系统每次启动都是"空白记忆"**。

当前闭环只回答"现在是否漂移、要不要重训"，无法回答：

1. **"这个场景以前出现过吗？"** — 3 年前类似的 VIX 飙升 + 科技回撤场景，当时的调仓决策对错与否，系统不复记忆
2. **"为什么当时这么决策？"** — `EvolutionOrchestrator.log_decision` 已有审计链（`utils/alpha/evolution_orchestrator.py:452`），但写入的是分散 JSON，无统一事件源、不可回放
3. **"哪些教训已沉淀？"** — 回撤归因、因子失效结论散落在 `reports/` 与 cairn 文档，机器不可检索

这就是"自我进化"与"自我进化**且积累经验**"的差距。2026-08-28 GitHub 周热门筛选出的 5 个项目恰好命中此缺口。

## 二、上游项目取舍决策

| 项目 | 取舍 | 理由 |
|------|------|------|
| **volcengine/OpenViking**（Agent 自进化上下文数据库，⭐+3,078） | **借鉴架构，不引入依赖** | 其"记忆 + RAG + Skills 统一上下文"分层范式是 ECL 的直接蓝本；但属火山云生态，直接嵌入有云绑定风险，本地 sqlite 自研轻量版 |
| **apache/maka**（本地 Agent 工作区，append-only 事件日志，Apache 孵化） | **借鉴范式（EventStore 核心）** | append-only 事件溯源 = 决策账本不可篡改 + 任意时点状态可回放，对复盘/归因/合规价值极高；Apache 背书协议稳定，范式成熟 |
| **anthropics/claude-plugins-community**（官方插件规范） | **借鉴契约（Phase B）** | "能力即插件"接口范式用于 factor_discovery / ml_enhanced_trainer 插件化改造设计；不依赖其运行时 |
| **Tencent/WeMM-Embedding**（通用多模态嵌入，本周新发） | **原型验证（Phase B，探索性）** | 新闻/研报图表 → 向量 → 相似历史行情检索 → 另类因子原型；新项目生态未成熟，不上生产 |
| **tinyhumansai/openhuman**（Rust 个人 AI，多 Agent 编排） | **仅架构评估（Phase B）** | 风控/因子/执行三 Agent 拆分编排思想可参考；Rust 异构集成成本高，不接入 |

**决策原则**：五个项目全部"借思想不引代码"（除 WeMM 仅作原型依赖）。ECL 用纯本地栈实现（sqlite + 标准库），零云依赖、零异构依赖，符合系统本地优先现状。

## 三、ECL 架构设计

### 3.1 三层结构

```
┌─────────────────────────────────────────────────────────┐
│                     EOD 工作流（生产链路）                  │
│   daily_workflow → EvolutionOrchestrator.run_observation │
└──────────────┬───────────────────────────▲──────────────┘
               │ 写入(flag 控制)             │ 读取(决策前注入)
┌──────────────▼───────────────────────────┴──────────────┐
│                 L3 RetrievalService 检索层                │
│   query_similar_scenarios(context, k) → Top-K 经验条目    │
│   （Phase A 只记录不注入；Phase B 评估后决定是否进决策链）      │
├─────────────────────────────────────────────────────────┤
│              L2 ExperienceStore 经验库（OpenViking 范式）   │
│   结构化经验条目: 场景指纹 + 决策 + 结果 + 教训标签           │
│   向量列(sqlite-vec 或降级 hash 向量) + FTS5 全文索引        │
├─────────────────────────────────────────────────────────┤
│              L1 EventStore 事件日志（maka 范式）            │
│   append-only: decision/order/rebalance/drift/eval 事件   │
│   单表 sqlite, 仅 INSERT, 重放 API: replay(as_of=date)     │
└─────────────────────────────────────────────────────────┘
```

### 3.2 数据模型

**L1 EventStore**（表 `ecl_events`）：

```sql
CREATE TABLE ecl_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,   -- 单调递增 = 全序
  ts TEXT NOT NULL,                        -- ISO8601 事件时间
  event_type TEXT NOT NULL,                -- decision|order|rebalance|drift_alert|strategy_eval|regime_shift
  subject TEXT NOT NULL,                   -- 作用对象: strategy_id / symbol / portfolio
  payload_json TEXT NOT NULL,              -- 事件体(含当时的 market_context 快照)
  schema_version INTEGER NOT NULL DEFAULT 1
);
-- 无 UPDATE/DELETE 权限路径；纠正 = 追加 compensation 事件
```

**L2 ExperienceStore**（表 `ecl_experiences`）：

```sql
CREATE TABLE ecl_experiences (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id INTEGER NOT NULL REFERENCES ecl_events(id),  -- 溯源到原始事件
  scenario_fp TEXT NOT NULL,   -- 场景指纹: regime|vix_bucket|dd_bucket|style_drift 的拼接键
  decision_summary TEXT NOT NULL,
  outcome REAL,                -- T+5/T+20 已实现结果(延迟回填)
  lesson_tags TEXT,            -- JSON 数组: ["过度对冲","反弹踏空","止损有效"...]
  embedding BLOB,              -- 场景描述向量(降级方案: hashed n-gram)
  created_at TEXT NOT NULL
);
```

### 3.3 读写时序（Phase A 只读模式）

```
EOD 收盘后:
  1. run_observation_cycle 照常执行（不动）
  2. log_decision 原有 JSON 落盘照常（不动）
  3. [flag: USE_ECL_EVENT_LOG] 同步事件 → EventStore（新增旁路写入，失败不阻塞主流程）
  4. [flag: USE_ECL_EXPERIENCE] 从当日事件提炼经验条目 → ExperienceStore（含场景指纹）

次日开盘前（Phase A 仅记录，不注入）:
  5. [flag: USE_ECL_RETRIEVAL] query_similar_scenarios(今日 market_context, k=5)
     → 结果写入 reports/ecl/retrieval_YYYY-MM-DD.json（供人工/后续评估，不进决策链）
```

**零行为变更保证**：三个 flag 全部默认 False；任一 flag 开启，生产决策输出必须与关闭时逐字节一致（E2E 断言）。

### 3.4 与现有审计链的关系（不新建平行账本）

`EvolutionOrchestrator.log_decision` 是既有唯一决策审计入口。ECL 不绕开它，而是：

- **EventStore 作为其第二落盘目标**（sink 模式）：`log_decision` 内部追加一个可选 sink 调用，将同一 payload 写入 `ecl_events`
- `utils/evolution/orchestrator.py:141` 的 `EvolutionOrchestratorV2` 同步适配（sink 列表注册制）
- 历史 `reports/evolution/` JSON 通过一次性回填脚本导入 EventStore（`scripts/backfill_ecl_events.py`），schema_version 标记回填来源

## 四、与自我进化闭环的整合点

对应 `cairn/self-evolution-framework.md` §10.1 闭环四阶段：

| 阶段 | ECL 角色 | 时机 |
|------|---------|------|
| **检测**（DriftMonitor） | 漂移告警时自动检索历史相似漂移场景及当时处置结果，附在告警 payload（"上次类似漂移重训后 IC 恢复用了 5 天"） | Phase B（A 阶段先只记录） |
| **诊断**（StrategyEvaluator） | 经验条目的 outcome 回填为诊断提供跨周期样本 | Phase A 数据积累即开始回填 |
| **进化**（AutoRetrainScheduler） | Phase B 评估：经验教训标签作为重训后验证的对照清单 | Phase B |
| **验证**（FeedbackLoop） | replay(as_of) 提供决策溯源链，支撑闸门审计 | Phase A（A1 交付即具备） |

## 五、关键设计决策记录

1. **sqlite 而非 postgres/向量库**：EOD 单进程写入、日频低并发，sqlite WAL 模式足够；零运维、零部署，与系统本地优先一致。规模上限估算：日频下 10 年 < 100 万事件，sqlite 游刃有余。
2. **嵌入降级链**：sentence-transformers（若已装）→ sqlite FTS5 全文检索 → hashed n-gram 向量。三档运行时自动探测，保证任何环境可跑。
3. **append-only 纠错方式**：不提供 UPDATE/DELETE API；错误用 compensation 事件（event_type=correction, payload 指向被纠正 event_id）表达，保持审计完整性（maka 范式核心）。
4. **只读先行**：Phase A 全部旁路 + flag 控制 + 输出 diff=0 断言，把"经验记忆"先变成可积累的数据资产，注入决策链的决策推迟到 Phase B 有回填验证数据之后。
5. **YAGNI 门禁**：A4 回填验证若显示相似场景检索无统计增益（方向一致率 < 55%），Phase B 的注入类任务全部取消，ECL 降级为纯审计回放工具归档——此为明确退出条件。

## 六、后续演进（Phase B，2027-05 起）

1. **插件化契约**（借鉴 claude-plugins）：`factor_discovery` / `ml_enhanced_trainer` 插件接口设计文档 + 1 个示范插件
2. **多模态另类因子原型**（WeMM-Embedding）：新闻/图表 → 向量 → 相似历史行情检索 → 事件因子 prototype（research/ 下实验，不入生产）
3. **多 Agent 编排评估**（openhuman/maka 对标）：风控/因子/执行三 Agent 拆分设计文档（评估不接入）
4. **ECL v2**：若 A 阶段验证通过，replay 升级为完整决策溯源链（覆盖 order/rebalance 全事件类型）

## 七、指针

- 排期：`docs/Wave10_经验上下文层集成计划_20260828.md`
- 审计链接线点：`utils/alpha/evolution_orchestrator.py:452`（log_decision）、`:536`（run_observation_cycle）
- 上游筛选依据：2026-08-28 GitHub 周热门（本周 Trending 5 个相关项目，§二）
- 理论关联：`cairn/self-evolution-framework.md` §八（控制论映射——ECL 即"可观察性分解"的历史维度扩展）

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [自我进化框架](self-evolution-framework.md) (相似度 14%)
- [Wave 9：GitHub 热榜项目集成决策沉淀](github-trending-wave9-20260819.md) (相似度 10%)
- [自我进化迭代再平衡闭环（Evolution-Rebalance Loop）](evolution-rebalance-loop.md) (相似度 8%)
- [docs/1 八项目集成 — Sprint A 知识专题](docs1-integration-sprint-a-20260817.md) (相似度 8%)
- [Shadow 数据质量闭环设计](shadow-data-quality-loop.md) (相似度 7%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
