# Spec — CTX-A3 EOD 旁路接线（三 flag 注册 + 阶段 4.95 + diff=0 断言）

> **状态**: spec-requirement + spec-design 完成，待评审 → spec-task 待排期执行
> **生成日期**: 2026-08-29（提前完成 Wave10 计划 §3 CTX-A3 的 spec 设计，原排期 09-07 后）
> **上游设计**: `cairn/experience-context-layer.md` §三 3.3（读写时序）+ §五.4（只读先行）
> **依赖**: CTX-A1（EventStore + sinks）+ CTX-A2（ExperienceStore + 检索）
> **本文档依据**: 全部基于 2026-08-29 实测代码探查（`15_每日工作流/run_daily_eod_workflow.py` 1680 行全读 + `config/feature_flags.yaml` 214 行全读），非假设

---

## 1. spec-requirement（需求）

### 1.1 问题

A1 交付 EventStore + sink 协议（`log_decision` 内直写）；A2 交付 ExperienceStore + 相似检索。两者均未接入生产 EOD 工作流——本任务补齐三件事：

1. 三 flag（USE_ECL_EVENT_LOG / USE_ECL_EXPERIENCE / USE_ECL_RETRIEVAL）注册进现行 FeatureFlags 体系
2. EOD 工作流新增 ECL 旁路阶段（fail-safe）
3. diff=0 E2E 断言（flag 全开 vs 全关，生产决策输出逐字节一致）

### 1.2 现有资产实测盘点（2026-08-29 探查）

| 事实 | 实证位置 | 对本设计的意义 |
|------|---------|--------------|
| **flag 唯一注册表 = 项目根 `config/feature_flags.yaml`**（214 行；ConfigManager 优先级解析到此处） | 文件内 Phase B flag 注释（:150-153）明确记载教训："此前仅 configs/（复数）注册，ConfigManager 实际解析到 config/（单数）——运行时 is_enabled() 报 Flag not registered 永远返回 False" | 三 flag **必须**追加到 `config/feature_flags.yaml`；A1 spec §2.5 "注册源"表述据此实证明确（此前记忆中 "feature_flags.yaml 不存在" 实为 `v8.3_institutional/config/` 下不存在，项目根的存在且权威） |
| flag 定义格式 | 同文件：`description / default / requires_dual_sign / rollback_seconds / fallback / critical_path` | 三 flag 对齐此格式 |
| flag 启用走 `enable()` 官方 API（双签 + flag_overrides 落盘 + flag_audit 审计） | `scripts/phase_b_progressive_enabler.py:731 _apply_flags_to_runtime`（B1/B2 真实启用先例） | A3 不另造启用机制，evalu/审计走现有 API |
| EOD 阶段编号体系 | `run_daily_eod_workflow.py` main()：0 → 1 → 2 → 3 → 4 → 4.5 → 4.5b(+4.5b+1) → 4.7 → 4.8 → 4.85 → 4.55 → 4.6 → 4.9 → 5 → 6 | ECL 旁路插位 = **阶段 4.95**（4.9 进化编排之后、阶段 5 归档之前） |
| 阶段 4.9 实现模式（本任务样板） | `run_phase4_9_evolution_cycle`（:1160-1208）：直接 import + enabled 检查 + try/except fail-safe + eod_summary 记录 | A3 旁路完全复刻此模式 |
| `observation_alert_*.json` 真实语义 | `_alert_observation_missing`（:726-797）写出，type 固定 `observation_data_missing`（feeder 失败/未写入），**非漂移告警** | A1 spec §2.3 映射修正：`data_gap_alert`；真正漂移数据在 `reports/drift/`（integration 摘要 + drift_report 明细） |
| 阶段 5 归档的 REPORT_PATTERNS | :120-129（8 类文件名模式，不含 reports/ecl/） | ECL 检索报告不被归档移动，保持 `reports/ecl/` 单一位置 |

### 1.3 需求清单

- R1: 三 flag 注册到 `config/feature_flags.yaml`，全部 `default: false`、`requires_dual_sign: true`、`critical_path: false`
- R2: EOD 新增阶段 4.95 `run_phase4_95_ecl_bypass`：对账兜底 + 经验提炼 + 检索记录（见 2.3）
- R3: CLI 参数 `--skip-ecl`（对齐 `--skip-shadow` / `--skip-feedback-loop` 命名惯例）
- R4: fail-safe：旁路任何异常不增加 `fail_count`、不改变 EOD 退出码、不阻塞后续阶段
- R5: diff=0 E2E：三 flag 全开 vs 全关，当日 `decisions.jsonl` 新增行 + 次日 trade_plan 输出**逐字节一致**
- R6: sink 常驻注册（A1 已定）；阶段 4.95 做**对账兜底**——当日 decisions.jsonl 行数 vs `ecl_events` 当日事件数，差额行回补（防 sink 静默失败丢事件）

### 1.4 非目标（YAGNI 划界）

- 不做盘前/决策前检索注入（Phase B CTX-B5 评估，且受 A4 YAGNI 门禁约束）
- 不改 `log_decision` 签名与 JSONL 落盘行为（A1 已定）
- 不动 phase_b 推进逻辑 / stage 编排 / kill_switch 任何现有机制
- 不把 ECL 报告加进 REPORT_PATTERNS（见 1.2 末行）

---

## 2. spec-design（设计）

### 2.1 三 flag 注册（`config/feature_flags.yaml` 追加）

```yaml
  # ============================================================
  # Wave10-CTX 经验上下文层 ECL (2026-09, 只读旁路)
  # 参考: cairn/experience-context-layer.md + docs/Wave10_经验上下文层集成计划_20260828.md
  # 铁律: 全部旁路只读, 开启后生产决策输出必须与关闭时逐字节一致 (diff=0 E2E)
  # 启用顺序: EVENT_LOG → EXPERIENCE → RETRIEVAL (后者依赖前者数据积累)
  # ============================================================
  USE_ECL_EVENT_LOG:
    description: "ECL 事件日志 — log_decision 旁路双写 EventStore (append-only), sink 失败静默降级"
    default: false
    requires_dual_sign: true
    rollback_seconds: 60
    fallback: "jsonl_only"
    critical_path: false

  USE_ECL_EXPERIENCE:
    description: "ECL 经验提炼 — EOD 阶段4.95 从当日事件生成经验条目 (场景指纹+延迟outcome)"
    default: false
    requires_dual_sign: true
    rollback_seconds: 60
    fallback: "skip_derive"
    critical_path: false

  USE_ECL_RETRIEVAL:
    description: "ECL 相似检索 — EOD 阶段4.95 输出相似历史场景报告到 reports/ecl/ (仅记录不注入)"
    default: false
    requires_dual_sign: true
    rollback_seconds: 60
    fallback: "skip_retrieval"
    critical_path: false
```

### 2.2 与 A1 sink 的职责分工（避免重复写入）

| 写入路径 | 时机 | flag | 内容 |
|---------|------|------|------|
| A1 sink（log_decision 内） | 决策发生时（实时） | USE_ECL_EVENT_LOG | 决策事件 → `ecl_events` |
| A3 阶段 4.95 ①对账兜底 | EOD 末尾 | USE_ECL_EVENT_LOG | decisions.jsonl 当日行 vs ecl_events 当日事件对账，**差额行回补**（复用 A1 回填映射规则） |
| A3 阶段 4.95 ②经验提炼 | EOD 末尾 | USE_ECL_EXPERIENCE | `ExperienceStore.derive_from_events(as_of=当日)` |
| A3 阶段 4.95 ③检索记录 | EOD 末尾 | USE_ECL_RETRIEVAL | `query_similar(最新 regime context, k=5)` → `reports/ecl/retrieval_{date}.json` |

### 2.3 EOD 阶段 4.95 实现（复刻阶段 4.9 模式）

```python
def run_phase4_95_ecl_bypass(report_date: str, eod_summary: dict, args) -> bool:
    """阶段四点九五: ECL 旁路 (对账兜底 + 经验提炼 + 检索记录, Wave10-CTX)

    在进化编排 (4.9) 之后、归档 (5) 之前执行。三 flag 独立控制三步,
    全部 fail-open: 任何异常不增加 fail_count (旁路性质, 非 EOD 核心阶段)。
    """
    if getattr(args, "skip_ecl", False):
        eod_summary["phases"]["phase4_95_ecl_bypass"] = {"skipped": True}
        return True
    try:
        from utils.infra.ecl.bypass import run_ecl_bypass  # 薄编排层
        result = run_ecl_bypass(report_date=report_date)
        eod_summary["phases"]["phase4_95_ecl_bypass"] = result  # 各步 success/counts
        return True
    except Exception as e:  # noqa: BLE001 — fail-open 旁路
        log(f"[WARN] ECL 旁路异常(不影响主流程): {e}", "WARN")
        eod_summary["phases"]["phase4_95_ecl_bypass"] = {"success": False, "error": str(e)}
        return True
```

- 编排细节（flag 检查/调用顺序）收进 `utils/infra/ecl/bypass.py`（~100 行），**EOD 脚本本体只增两处**：CLI 参数 + main() 插一段调用（改动最小化，1680 行生产脚本不膨胀）
- main() 插入位置：`run_phase4_9_evolution_cycle(...)` 之后、`run_phase5_archive(...)` 之前

### 2.4 diff=0 E2E 断言

**测试级**（`tests/e2e/test_ecl_bypass_diff0.py`）：

- 构造同一决策输入，三 flag 全 off → 基线 `decisions.jsonl` 行 + trade_plan 字段快照
- 三 flag 全 on → 同输入重跑，逐字节对比上述产物
- 断言 sink/旁路写库写报告**不影响**决策产物（ECL 只新增自己的库与 `reports/ecl/` 文件）

**生产干跑级**（B4 完成后首次启用前）：

- 挑一个非交易时段，`--skip-ecl` 跑一轮基线 → 开 flag 只跑阶段 4.95（或 EOD 全流程）→ 对比当日 EOD summary 的核心 phases 结果一致 + decisions.jsonl 无新增行

### 2.5 CLI 参数

`--skip-ecl`：跳过阶段 4.95（调试/紧急排查用；默认执行——三 flag 全 False 时旁路内部 noop，秒级返回）

---

## 3. spec-task（实现分解，共 2 人天）

| # | 任务 | 产出 | 验收 | 工期 | 依赖 |
|---|------|------|------|------|------|
| T1 | 三 flag 注册 + 回归测试 | `config/feature_flags.yaml` 追加 + `tests/unit/test_ecl_flags.py` | `list_flags()` 三 flag 可见且 `current_value=False`；现有 flag 回归测试（`test_existing_flags_not_broken` 类）不破坏；yaml 语法校验过 | 0.5d | A1-T3 |
| T2 | bypass.py + 阶段 4.95 接线 + CLI | `utils/infra/ecl/bypass.py` + EOD 脚本两处改动 + 单测 | ①flag 全 off 时 EOD 行为与现状完全一致（代码只增不改）②旁路抛异常 → EOD 退出码与 summary 正常 ③对账兜底：人为删 2 条事件后重跑 → 回补 2 条 | 1d | T1 + A2-T2 |
| T3 | diff=0 E2E 测试 | `tests/e2e/test_ecl_bypass_diff0.py` | flag on/off 决策输出逐字节一致断言通过 | 0.5d | T2 |

> 生产首次启用（三 flag 逐个 enable，走双签 + flag_audit）安排在 **B4（09-20）完成后**——Wave10 计划铁律：B4 完成前仅离线开发，不碰运行中系统。

---

## 4. 验收标准（量化）

1. flag 全 off：EOD 全流程输出（summary/phases/退出码/decisions.jsonl/trade_plan）与改动前完全一致
2. flag 全 on：`ecl_events` 当日事件数 = decisions.jsonl 当日行数（对账 100%）；经验条目与 retrieval 报告产出；**决策产物 diff=0**
3. 旁路任意异常（含 import 失败/磁盘满）→ EOD 正常完成，`fail_count` 不变
4. flag 启用走 `enable()` 双签 → `reports/flag_audit/{FLAG}.jsonl` 生成
5. pre-commit + CI 全过；EOD 脚本净增 ≤60 行（CLI + 调用 + docstring）

---

## 5. 风险

| 风险 | 缓解 |
|------|------|
| EOD 脚本是生产链路（每日 15:30 自动运行） | 改动仅两处 + fail-open 全包；上线前 `--dry-run` 验证；首周人工核对 eod_summary |
| 阶段 4.95 与阶段 5 归档的顺序交互 | 4.95 只写 `reports/ecl/`（不在 REPORT_PATTERNS），不产当日归档文件，零干扰（已实证 1.2） |
| flag 提前启用干扰 B3/B4 观察 | spec 明确：09-20 B4 完成前不启用；flag 注册（默认 False）本身零行为变更 |
| 对账兜底与 A1 回填映射规则漂移 | 兜底回补复用 A1 `backfill_ecl_events.py` 同一映射函数（单一实现） |

---

## 6. 评审结论（待评审）

- [x] **评审点① 阶段编号/插位**：已确认——4.95（4.9 之后、归档之前）——备选方案为并入阶段 6 审核之后（更靠后、更安全但当日检索报告产出滞后），本设计取前者（经验提炼紧贴事件产生，时序更自然）
- [x] **评审点② 检索报告位置**：已确认——`reports/ecl/retrieval_{date}.json` 不进归档（单一位置便于 A4 批量分析）
- 实证注记：`config/feature_flags.yaml` 的 configs/config 双源教训（:150-153 注释）是本 spec flag 落点的直接依据——**新增 flag 必须写 config/（单数）**
- A1 spec 两处实证修正已同步（§2.3 observation_alert → `data_gap_alert`；§2.5 注册源明确为 `config/feature_flags.yaml`），见 A1 spec §6 评审点③
- 评审通过 → 按 Wave10 计划 09-07 后启动 T1-T3（B4 完成前仅离线开发）
