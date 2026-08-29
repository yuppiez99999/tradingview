# Spec — CTX-A1 EventStore append-only 事件日志

> **状态**: spec-requirement + spec-design 完成，待评审 → spec-task 待排期执行
> **生成日期**: 2026-08-28（提前完成 `docs/Wave10_经验上下文层集成计划_20260828.md` §9 下一步第 1 项，原排期 09-07）
> **上游设计**: `cairn/experience-context-layer.md` §三
> **本文档依据**: 全部基于 2026-08-28 实测代码与数据采样，非假设

---

## 1. spec-requirement（需求）

### 1.1 问题

`EvolutionOrchestrator.log_decision`（`utils/alpha/evolution_orchestrator.py:452`）已将决策写入 `reports/evolution/decisions.jsonl`，但该文件是**平面 JSONL 追加日志**：无事件类型体系、无全序保证语义、无回放 API、无纠错补偿机制、不可按 as_of 重建状态。ECL L1 层需要一个 append-only 事件源（EventStore）作为唯一可信历史。

### 1.2 现有资产实测盘点（2026-08-28 采样）

| 资产 | 实测格式 | 与 EventStore 的关系 |
|------|---------|---------------------|
| `reports/evolution/decisions.jsonl` | `DecisionRecord.to_dict()`：`timestamp/action/public_score/private_score/reward_hacking_risk/recommendation/sample_count/observation_day/reason/metrics_snapshot/evaluator_report`。**两类记录混存**：① 完整策略评估（含 public/private_metrics/dsr/overfit_score）② `evaluator_report.vol_regime_suggestion`（含 regime/vix/realized_vol/current_drawdown/ suggested_weights/multipliers/deltas/constraints_applied） | **主数据源**。sink 直写 + 历史回填 |
| `reports/evolution/memory.jsonl` | 进化提案记忆：`proposal_id(EVO-*)/level/action_type/status/learned/rollback_plan` | **边界清晰不合并**：memory 记"提案与执行状态"，EventStore 记"事件流"。经验条目（L2）可通过 `event_id ↔ proposal_id` 关联 |
| `reports/evolution/observation_alert_*.json` | 漂移/观察告警，按日一份 | 回填导入 → `event_type=drift_alert` |
| `reports/evolution/vol_regime_weights_*.json` | 与 decisions.jsonl 中 vol_regime_suggestion **内容重复**（含 report_path 回指） | **回填忽略**（去重规则：以 decisions.jsonl 为准，独立 JSON 仅作旁证） |
| `utils/evolution/orchestrator.py:141` `EvolutionOrchestratorV2` | 第二代编排器 | sink 注册制需同时适配两代编排器 |

### 1.3 需求清单

- R1: append-only 事件表，无 UPDATE/DELETE 代码路径；纠错 = compensation 事件
- R2: 单调递增 id 全序；`replay(as_of)` 可重建任意时点状态视图
- R3: 单条写入 <10ms（EOD 单进程场景）
- R4: sink 挂点不改变 `log_decision` 既有签名与 JSONL 落盘行为；sink 失败**必须**静默降级（与现有 except 返回 False 风格一致），不阻塞主流程
- R5: 三 flag（USE_ECL_EVENT_LOG / USE_ECL_EXPERIENCE / USE_ECL_RETRIEVAL）默认 False；注册方式遵循项目现行机制（`flag_overrides` + `flag_audit` 审计落盘，参照 B1 真实落盘先例 `cairn/ROADMAP.md` 08-26 条目），**不新建 yaml**
- R6: 回填 `decisions.jsonl` + `observation_alert_*.json` 全量历史，来源标记 schema_version 区分
- R7: 新模块 `utils/infra/ecl/event_store.py` ≤250 行（AGENTS.md §5.3），单测同步

### 1.4 非目标（YAGNI 划界）

- 不做 L2 经验提炼 / L3 检索（CTX-A2 范围）
- 不做 order/rebalance 事件类型（CTX-B4 ECL v2 范围，本期仅 decision/drift_alert/regime_shift_suggestion/strategy_eval/noop 五类）
- 不做并发写支持（EOD 单进程 + WAL 足够）
- 不迁移 memory.jsonl / knowledge_base.jsonl

---

## 2. spec-design（设计）

### 2.1 模块与 API

`utils/infra/ecl/event_store.py`（新建包 `utils/infra/ecl/`）：

```python
class EventStore:
    def __init__(self, db_path: Path) -> None: ...          # PRAGMA journal_mode=WAL, busy_timeout=5000
    def append(self, event_type: str, subject: str, payload: dict, ts: str | None = None) -> int: ...
        # 仅 INSERT；返回 event_id；ts 缺省取调用方传入（sink 场景必须传 log_decision 的原始 timestamp，保回填与直写时序一致）
    def replay(self, as_of: str | None = None, event_type: str | None = None, subject: str | None = None) -> list[Event]: ...
        # as_of 含边界（<=）；compensation 事件在回放视图中按 payload.corrected_event_id 覆盖
    def append_compensation(self, corrected_event_id: int, reason: str) -> int: ...  # event_type="correction"
    def count(self) -> int: ...
```

`utils/infra/ecl/sinks.py`：

```python
class DecisionSink(Protocol):
    def write(self, record: dict[str, Any]) -> bool: ...   # 失败返回 False，禁止抛出

class EclEventSink:  # DecisionSink 实现
    # record → EventStore 映射规则见 2.3

def register_sink(sink: DecisionSink) -> None: ...          # 全局注册表，log_decision 末尾遍历调用
```

### 2.2 sink 挂点（零签名变更）

`log_decision` 现有写 JSONL 成功后（`evolution_orchestrator.py:513-518` 之后、`return True` 之前）追加：

```python
for sink in iter_registered_sinks():
    if not sink.write(record.to_dict()):
        logger.warning("ECL sink 写入失败(已降级, 不影响主流程)")
```

- flag 检查在 `EclEventSink.write` 内部（`is_enabled("USE_ECL_EVENT_LOG")`），关闭时直接 return True（noop），保证注册常驻、开关即时生效
- `EvolutionOrchestratorV2` 同点位适配（注册制天然支持）

### 2.3 事件映射规则（回填与直写共用，单一实现）

| 源记录特征 | event_type | subject | market_context 快照提取 |
|-----------|-----------|---------|------------------------|
| `evaluator_report.vol_regime_suggestion` 存在 | `regime_shift_suggestion` | `portfolio` | `indicators.{vix, realized_vol, current_drawdown}` + `regime.{label, confidence}` |
| 有 `public_score/private_score` 且 `sample_count>0` | `strategy_eval` | `v9_baseline` | null（回填时与同日相邻 regime 记录 join 补全，见 2.4） |
| 其余（`action=evaluate_only` 空载） | `noop` | `orchestrator` | null |

~~`observation_alert_*.json` → `event_type=drift_alert`，subject 取告警对象。~~
**2026-08-29 实证修正**：全量 5 份 `observation_alert_*.json`（08-20/25/26/27/28）实测均为 `type=observation_data_missing`（由 `run_daily_eod_workflow.py:726 _alert_observation_missing` 写出的**观察期数据断档告警**，非漂移告警）。修正映射：`observation_alert_*.json → event_type=data_gap_alert`，subject=feeder。真正的漂移数据在 `reports/drift/`（`integration_{date}.json` 轻量日摘要 + `drift_report_{date}.json` 特征级明细 155KB 大文件）；漂移告警事件（`drift_alert`）的回填来源**追加为 `integration_*.json`**（10 份），`drift_report_*.json` 明细不导入 EventStore（A2 §1.2 划界，仅 A4 按需读）。

### 2.4 回填设计（`scripts/backfill_ecl_events.py`）

- 逐行读 `decisions.jsonl`（已确认 UTF-8 JSONL，全量 ~8-21 起多轮写入），按 2.3 映射写入，`schema_version=1`，ts 用原始 timestamp **不重排**（保留真实时序，id 序 = 文件行序）
- 同日 strategy_eval 的 market_context：向前查最近一条 regime_shift_suggestion 的 indicators（≤24h 有效），无则 null——**回填只 join 已落盘数据，不引入外部行情**（防前视）
- 幂等：以 `(ts, event_type, subject)` 唯一索引去重，脚本可重跑
- observation_alert 按文件日期导入；vol_regime_weights_*.json 按 1.2 去重规则跳过

### 2.5 flag 注册

沿用现行机制：`flag_overrides`（ConfigManager 4 级优先级内）+ `flag_audit` 审计；`FeatureFlags.is_enabled("USE_ECL_EVENT_LOG")` 默认 False。三 flag 定义（description/owner=dual_signature/enable_requires）追加到 flag 注册源，启动时 `list_flags()` 可见。

> **2026-08-29 实证修正**：flag 注册源 = **项目根 `config/feature_flags.yaml`**（214 行，ConfigManager 实际解析路径，含 Phase B flag 08-26 补注册先例及其 "configs/ 复数注册不生效" 教训注释 :150-153）。此前记忆 "feature_flags.yaml 不存在" 系指 `v8.3_institutional/config/` 下不存在——**新 flag 必须写 config/（单数）**。三 flag 的 yaml 定义格式与启用顺序设计见 `spec_CTX-A3.md` §2.1。

---

## 3. spec-task（实现分解，共 3 人天）

| # | 任务 | 产出 | 验收 | 工期 | 依赖 |
|---|------|------|------|------|------|
| T1 | 表结构 + pragma + 唯一索引 | `ecl_events` DDL + WAL 初始化 | 幂等重跑不重复；UPDATE/DELETE 无代码路径（代码审查项） | 0.5d | — |
| T2 | append/replay/compensation API + 单测 | `event_store.py` + `tests/infra/ecl/test_event_store.py`（≥15 用例：不可变、全序、as_of 边界、compensation 覆盖、WAL、单条<10ms 基准） | 单测全绿；写入延迟断言通过 | 1d | T1 |
| T3 | sink 协议 + log_decision 挂点 + flag 注册 | `sinks.py` + 编排器两代挂点 + flag 三项注册 | E2E：flag off/on 决策输出 diff=0；sink 抛异常时主流程返回 True；flag_audit 生成 | 1d | T2 |
| T4 | 回填脚本（提前做，原属 CTX-A4） | `scripts/backfill_ecl_events.py` | 全量回填成功 + 重跑零新增 + 抽样 10 条人工核对 | 0.5d | T2 |

> T4 前置到 A1（回填依赖映射规则，同一实现者一次做对更省）；CTX-A4 保留"增益验证"部分不变。

---

## 4. 验收标准（量化）

1. `git grep -iE "UPDATE|DELETE" utils/infra/ecl/` 仅命中注释/测试（不可变性代码级保证）
2. replay(as_of=任意历史 ts) 输出与逐日快照一致（测试构造）
3. sink 开启后 `decisions.jsonl` 与 `ecl_events` 双写一致率 100%（E2E 对账）
4. flag 全 off 时 E2E 决策输出与基线 diff=0
5. 回填后 `count()` = decisions.jsonl **去重后唯一 (ts, event_type, subject) 数** + observation_alert 文件数（dry-run 实证同秒存在内容相同的重复行，去重是期望行为，见 §6）

## 5. 风险

| 风险 | 缓解 |
|------|------|
| decisions.jsonl 存在重复写入（采样已见同秒多条） | 幂等唯一索引 (ts, event_type, subject) + 回填对账 |
| 编排器两代挂点遗漏 | 注册表集中制 + 两代各一条 E2E |
| 回填 join 补全 market_context 误伤时序 | 仅用 ≤24h 前向已落盘数据，不用外部行情 |

## 6. 评审结论

- [x] **评审点① 已验证通过（2026-08-28 dry-run，全量 106 行）**：
  - `total=106, parseFail=0`；时间范围 2026-08-21 ~ 08-27（7 天）
  - 映射分布：`strategy_eval=65` + `regime_shift_suggestion=41`，**noop=0**（兜底分支未被触发，无遗漏形态）
  - `evaluator_report` 顶层键全景：评估类 65 条固定 12 键 + regime 类 41 条仅多 `report_path`（回填忽略键）——**无未知 extra_payload 形态**
  - regime indicators（vix/realized_vol/current_drawdown）41/41 全覆盖；regime label 全部 `bull`（7 天窗口单一 regime）
- [x] **dry-run 引出的两处 spec 修正（已并入本文档）**：
  1. **发现 `action=evaluate` 24 条**（≠ evaluate_only，来自另一写入入口，与 memory.jsonl 的 EVO proposal 同源）。它们全部被形态规则正确分类（不依赖 action 字段）——**验证"按 evaluator_report 形态映射优于按 action 映射"的设计选择**；2.3 映射规则不变
  2. **对账公式修正**（§4 第 5 条）：真实数据存在**同秒多条内容相同**的重复写入（采样实证：EVO-20260821-001/002/003 三条完全一致）。原公式"count = 有效行数 + alert 数"不成立，修正为：`count() = 去重后唯一 (ts, event_type, subject) 数 + observation_alert 数`——同秒重复**去重是期望行为**，幂等唯一索引设计正确
- [x] **评审点② compensation 语义**：已确认——本设计取**视图级**（物理行永不删，回放时按 `corrected_event_id` 覆盖）
- [x] **评审点③ 映射与注册源实证核验（2026-08-29 探查，两处修正已并入本文档）**：
  1. `observation_alert_*.json` 实测为**观察期数据断档告警**（type 固定 `observation_data_missing`，5/5 份一致）→ §2.3 映射修正为 `data_gap_alert`；真正漂移数据在 `reports/drift/`（integration 摘要 10 份可回填为 `drift_alert`；drift_report 明细 155KB 不导入）
  2. flag 注册源实证 = **项目根 `config/feature_flags.yaml`**（非 v8.3_institutional/config/；"configs 复数注册不生效" 教训注释在案）→ §2.5 已修正
- **附加观察（影响 CTX-A4）**：回填窗口仅 7 天且 regime 单一（全 bull），经验检索增益验证样本不足的既有风险被实证——A4 启动时需按风险表预案延后或引入回测日志模拟回填
- 评审通过 → 按计划 09-07 启动 T1-T3
