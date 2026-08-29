# Spec — CTX-A2 ExperienceStore 经验库 + 嵌入降级链 + 相似检索

> **状态**: spec-requirement + spec-design 完成，待评审 → spec-task 待排期执行
> **生成日期**: 2026-08-29（提前完成 Wave10 计划 §3 CTX-A2 的 spec 设计，原排期 09-07 后）
> **上游设计**: `cairn/experience-context-layer.md` §三 3.1（L2/L3 层）+ §五.2（嵌入降级链决策）
> **依赖**: CTX-A1（EventStore API + sinks，spec 见同目录 `spec_CTX-A1.md`）
> **本文档依据**: 全部基于 2026-08-28 A1 dry-run + 2026-08-29 原料探查实测，非假设

---

## 1. spec-requirement（需求）

### 1.1 问题

A1 的 EventStore 解决了"事件不可变、可回放"，但**事件 ≠ 经验**：

- 事件是原始流水，无场景归一化（vix=21.3 与 vix=25.7 的两次 regime 建议无法关联为"同类场景"）
- 无 outcome（决策发生后 T+5/T+20 的组合结果未回填）
- 无检索能力（"这个场景以前出现过吗"无法查询）

L2 ExperienceStore 负责补齐：**场景指纹归一化 + 经验条目沉淀 + outcome 延迟回填 + Top-K 相似检索**（OpenViking 范式的本地轻量实现）。

### 1.2 现有资产实测盘点（2026-08-29 探查）

| 资产 | 实测格式 | 与 ExperienceStore 的关系 |
|------|---------|--------------------------|
| EventStore `ecl_events`（A1 交付） | `regime_shift_suggestion` 事件含 `indicators.{vix, realized_vol, current_drawdown}` + `regime.{label, confidence}`（A1 dry-run 实证 41/41 全覆盖） | **主原料**：场景指纹 + decision_summary 全部取自事件 |
| `reports/shadow/daily_returns.jsonl` | `{date, daily_return, ...}` 逐交易日 | **outcome 回填唯一来源**（只读，不引外部行情） |
| `reports/drift/integration_{date}.json` | 轻量日摘要（08-28 实测 28 行）：`daily_return / drift_reports_count / delayed_metrics{ic, rank_ic, ic_ir, observation_rate} / ic_degradation / alerts[] / symbols_updated` | 漂移日场景附注 + lesson_tags 规则原料 |
| `reports/drift/drift_report_{date}.json` | 特征级明细大文件（08-28 实测 **155KB / 5102 行**，每条 `feature_name/psi/severity/baseline_mean/current_mean`） | **不导入 L1/L2 主表**（体积与粒度不符）；仅 A4 增益验证诊断时按需读 |
| 环境依赖（`pyproject.toml`） | 生产依赖**无** sentence-transformers、无 torch（torch 仅 timesfm 可选 extras）；有 numpy / scikit-learn / scipy | 嵌入降级链必要：**当前环境预期落 FTS5 或 hash 档**，运行时探测为准 |

### 1.3 需求清单

- R1: 场景指纹 `scenario_fp = regime_label|vix_bucket|rv_bucket|dd_bucket`，桶边界常量单一实现
- R2: 经验条目仅从 `regime_shift_suggestion` + `strategy_eval` 两类事件生成；`data_gap_alert` / `noop` 不生成（数据断档与空载无经验价值）
- R3: outcome 延迟回填：双 horizon（T+5 / T+20 组合累计收益，从 `daily_returns.jsonl` 取），仅回填已实现样本，未到期保持 NULL
- R4: 嵌入降级链三档**运行时一次性探测**：sentence-transformers → sqlite FTS5 → hashed n-gram（numpy 256 维），探测结果写 `ecl_meta` 表供 A4 报告
- R5: `query_similar(context, k)` Top-K 相似场景 <500ms
- R6: 检索结果仅写 `reports/ecl/retrieval_{date}.json`（供人工/后续评估），**不进决策链**
- R7: 新模块 200-400 行约束（AGENTS.md §5.3）→ 拆两个文件：`experience_store.py`（~300 行）+ `embeddings.py`（~150 行）

### 1.4 非目标（YAGNI 划界）

- 不做经验注入决策链（Phase B CTX-B5，且受 A4 YAGNI 门禁约束）
- 不做 LLM 自动教训提炼（Phase A 的 lesson_tags 仅规则标签；LLM 提炼推迟到有回填数据后评估）
- 不做多模态嵌入（CTX-B2 WeMM 原型，research/ 范围）
- 不迁移/导入 `drift_report_*.json` 特征明细（见 1.2）

---

## 2. spec-design（设计）

### 2.1 模块与 API

`utils/infra/ecl/embeddings.py`（新建，~150 行）：

```python
def detect_embedding_backend() -> str:
    # 返回 "sentence_transformers" | "fts5" | "hash"，探测顺序固定，结果缓存在模块级

def embed(text: str) -> list[float] | None:
    # st 档: 真向量; hash 档: n-gram hash 到 256 维; fts5 档: None (调用方走 SQL 全文检索)

def cosine_sim(a: list[float], b: list[float]) -> float: ...
```

`utils/infra/ecl/experience_store.py`（新建，~300 行）：

```python
class ExperienceStore:
    def __init__(self, db_path: Path, event_store: EventStore) -> None: ...
    def derive_from_events(self, as_of: str) -> int:
        # 当日(as_of)事件 → 经验条目; 幂等: event_id 唯一索引, 可重跑; 返回新增条数
    def backfill_outcomes(self, returns_jsonl: Path, horizons: tuple = (5, 20)) -> int:
        # 仅回填 ts + horizon <= 今日 且 outcome 为 NULL 的条目; 返回回填条数
    def query_similar(self, context: dict, k: int = 5) -> list[dict]: ...
    def stats(self) -> dict: ...  # 条目数/outcome 回填率/embedding 档位/scenario_fp 分布
```

### 2.2 场景指纹（分桶标准，单一实现常量）

```python
VIX_BUCKETS = [(20.0, "low"), (30.0, "normal")]   # <20 low, [20,30) normal, >=30 high
RV_BUCKETS  = [(0.15, "low"), (0.30, "normal")]   # realized_vol
DD_BUCKETS  = [(0.05, "shallow"), (0.10, "mid")]  # current_drawdown, >=0.10 deep

scenario_fp = f"{regime_label}|vix_{vb}|rv_{rvb}|dd_{ddb}"
```

- 嵌入输入文本 = `"{regime_label} regime, VIX {bucket}, realized vol {bucket}, drawdown {bucket}. {decision_summary}"`
- 分桶边界即"经验可比性"的定义：同桶 = 可比场景。**桶越粗样本越多、增益验证越可行**（当前回填窗口仅 7 天 + 单一 bull regime，A1 dry-run 已实证样本不足风险）

### 2.3 经验条目生成规则（derive_from_events）

| 源事件 | decision_summary | lesson_tags（Phase A 规则标签） |
|--------|-----------------|------------------------------|
| `regime_shift_suggestion` | suggested_weights 增量摘要 + multipliers + constraints_applied | `[]`（Phase A 不预判教训，留 A4 回填数据后分析） |
| `strategy_eval` | recommendation + public/private score + reason | 简单规则：score<0 → `["score_negative"]`；reward_hacking_risk>0.5 → `["reward_hacking_risk"]` |

- 幂等：`(event_id)` 唯一索引；`derive` 重跑零新增
- 事件缺 indicators（如回填时 join 失败的 strategy_eval）→ `scenario_fp = None`，仍入库但检索一级过滤排除

### 2.4 outcome 延迟回填（backfill_outcomes）

- 表结构：`outcome_5d REAL` + `outcome_20d REAL` 两列（扩展 A1 设计的单 `outcome` 列为双 horizon——T+5 验证短周期一致性，T+20 验证月度级别）
- 取值：事件日之后第 N 个**交易日**的组合累计收益（`daily_returns.jsonl` 按 date 排序的序列）
- 只回填 `ts + N 交易日 <= 今日` 且当前值为 NULL 的条目——**不可回填的保持 NULL，绝不前视**
- 数据断档日（如 observation_alert 记录的缺失日）如实跳过，交易日序列按 jsonl 实际存在的 date 计算

### 2.5 相似检索（query_similar）

- 输入 `context`: `{vix, realized_vol, current_drawdown, regime_label}`（调用方从最新 regime 事件提取；A3 EOD 旁路传入）
- **一级过滤**：`scenario_fp` 完全匹配（SQL，命中桶内全部条目）
- **二级排序**（桶内条目 >K 时）：嵌入余弦（st/hash 档）或 FTS5 rank
- 输出 Top-K dict：`{scenario_fp, ts, decision_summary, outcome_5d, outcome_20d, lesson_tags}`
- 性能保证：样本 <1000 时桶内暴力扫描足够（sqlite 索引 + K≤10）；<500ms 断言写进单测基准

### 2.6 嵌入降级链探测（运行时一次性）

1. `import sentence_transformers` + 本地模型缓存命中 → 档 1（当前环境预期不可用，见 1.2）
2. `CREATE VIRTUAL TABLE ... USING fts5` 探测 → 档 2（Python 官方 Windows 构建通常带 FTS5，以实测为准）
3. 兜底 → 档 3 hashed n-gram（纯 numpy，任何环境可跑）

探测结果写 `ecl_meta` 表 + logger.info，供 A4 检索质量报告引用（"增益结论基于 X 档嵌入"）。

### 2.7 表结构（L2）

```sql
CREATE TABLE IF NOT EXISTS ecl_experiences (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id INTEGER NOT NULL UNIQUE REFERENCES ecl_events(id),  -- 溯源 + 幂等
  scenario_fp TEXT,              -- NULL = 指纹缺原料(检索一级过滤排除)
  decision_summary TEXT NOT NULL,
  lesson_tags TEXT,              -- JSON 数组
  outcome_5d REAL,               -- T+5 组合累计收益(延迟回填)
  outcome_20d REAL,              -- T+20 组合累计收益(延迟回填)
  embedding BLOB,                -- hash/st 档向量; fts5 档 NULL
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ecl_exp_fp ON ecl_experiences(scenario_fp);
```

（与 `cairn/experience-context-layer.md` §3.2 相比：单 outcome 列 → 双 horizon 列；lesson_tags 保留；embedding 同设计）

---

## 3. spec-task（实现分解，共 4 人天）

| # | 任务 | 产出 | 验收 | 工期 | 依赖 |
|---|------|------|------|------|------|
| T1 | embeddings.py 三档降级链 | `utils/infra/ecl/embeddings.py` + 单测 | 三档各自单测绿（monkeypatch 探测）；当前环境实际档位记录进 spec 评审结论 | 1d | A1-T2 |
| T2 | experience_store 表 + derive/backfill API | DDL + 两个 API + 单测 ≥12 用例（幂等/分桶边界/NULL 不前视/断档跳过） | 全绿；derive 重跑零新增 | 1.5d | T1 |
| T3 | query_similar + 检索报告输出 | 检索 API + `reports/ecl/retrieval_{date}.json` 写出 | Top-K <500ms 基准断言；输出含 outcome 字段 | 1d | T2 |
| T4 | 全历史回填联跑 | derive(全历史) + backfill_outcomes | 条目数 = 可生成事件数（对账）；outcome 回填率写入 stats | 0.5d | T2 + A1-T4 |

---

## 4. 验收标准（量化）

1. 幂等：`derive_from_events` / `backfill_outcomes` 重跑零新增/零改写
2. 分桶边界值测试全过（20.0 / 30.0 / 0.15 / 0.30 / 0.05 / 0.10 的左右邻域）
3. 降级链三档可独立测试（不依赖实际安装状态）
4. 检索延迟 <500ms（1000 条合成样本基准）
5. 零行为变更：A2 模块不被任何生产代码 import（仅 CTX-A3 旁路在 flag 内引入）

---

## 5. 风险

| 风险 | 缓解 |
|------|------|
| 样本严重不足（回填窗口仅 7 天 + 单一 bull regime，A1 dry-run 实证） | ① 分桶取粗（3×3×3 桶）保桶内样本；② A4 有明确 YAGNI 门禁（<55% 即降级为审计工具），不硬上 |
| FTS5 编译缺失（Python 构建差异） | 降级链 hash 档保底，任何环境可跑；探测结果进 ecl_meta 可追溯 |
| outcome 回填错位（交易日 vs 自然日） | 以 daily_returns.jsonl 实际存在日期为交易日序列，断档如实跳过 |
| 双列 outcome 与 A1 设计漂移 | 本 spec §2.4 显式扩展并注明理由，A1 设计文档不回改（以本 spec 为实现准绳） |

---

## 6. 评审结论（待评审）

- [x] **评审点① 分桶标准**：已确认——§2.2 边界值（VIX 20/30、RV 0.15/0.30、DD 0.05/0.10）取恰当——样本不足期偏粗桶更有利增益验证
- [x] **评审点② outcome 双 horizon**：已确认——单列（A1 原设计）vs 双列 5d/20d（本设计）——本设计取双列（短/长周期一致性分离验证）
- 实证注记：当前环境（pyproject.toml 无 sentence-transformers）嵌入预期落 **FTS5 或 hash 档**；若 A4 增益验证需语义检索，届时再评估安装 sentence-transformers（~500MB）的成本
- 评审通过 → 按 Wave10 计划 09-07 后启动 T1-T4
