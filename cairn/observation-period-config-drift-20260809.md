# 观察期配置脱节修复 — 2026-08-09

> 问题: 影子账户 14 天观察期的 PM 决策 (yaml=21 天) 从未生效, 所有代码硬编码 14 天。
> 修复: 以 `shadow_admission.yaml` 为单事实源, 消除全部硬编码 14。

---

## 1. 问题根因

`v8.3_institutional/config/shadow_admission.yaml` 的 PM 决策:
```yaml
settings:
  observation_days: 21        # 原 14 → 21 (PM 双管齐下方案)
  min_observation_days: 21
admission_criteria:
  min_samples_for_dsr: 20
```

但**所有代码都硬编码 14 天**, 且**不读 yaml**:
- `scripts/observation_tracker.py` L41: `OBSERVATION_DAYS = 14` (纯硬编码)
- `scripts/phase_b_progressive_enabler.py` L70: `observation_days_required: int = 14` (硬编码)
- `scripts/run_evolution_eval.py` L129-131: `observation_total: 14` (硬编码)
- `scripts/shadow_admission_launcher.py` L66: `DEFAULT_OBSERVATION_DAYS = 14` (回退默认)
- `scripts/observation_watchdog.py` L88: `DEFAULT_REQUIRED_DAYS = 14` (回退默认)
- `reports/evolution/status.json` / `phase_b_status.json`: 缓存值 14 (陈旧)

**后果**:
- 观察期预计达标日被算成 **2026-08-13** (按 14 天), 但 PM 真实意图是 **21 天 → 2026-08-20+**。
- 阶段 B 会在 14 天就误判可启用, 提前 7 天放开进化闭环 (违反 PM 决策)。
- `observation_tracker.py` 的 `MIN_SAMPLES=20` 与 `shadow_account_adapter.MIN_SAMPLES_FOR_DSR=15` 口径分裂 (前者更严, 职责不同, 保留)。

**关键陷阱**: yaml 字段在 `settings` 嵌套层, 非顶层。初次修复误读 `_CFG.get("observation_days")` 返回 None 回退 14, 必须 `_CFG["settings"]["observation_days"]`。

---

## 2. 修复清单 (6 处)

| 文件 | 改动 | 验证 |
|------|------|------|
| `scripts/observation_tracker.py` | 加 `_load_shadow_admission()` 读 `settings.observation_days` (回退 14), `OBSERVATION_DAYS`/`MIN_SAMPLES` 改为 yaml 驱动 | ✅ 跑出 `required_days:21` |
| `scripts/phase_b_progressive_enabler.py` | 加 `_load_observation_days_required()` 读 yaml, dataclass 默认值改 `OBSERVATION_DAYS_REQUIRED` | ✅ `--check` 显示 `10/21` |
| `scripts/run_evolution_eval.py` | 加 `OBSERVATION_DAYS` 常量, 替换 L129-131 硬编码 + L353 日志 | ✅ lint 0 |
| `scripts/shadow_admission_launcher.py` | `DEFAULT_OBSERVATION_DAYS = 14 → 21` | ✅ |
| `scripts/observation_watchdog.py` | `DEFAULT_REQUIRED_DAYS = 14 → 21` | ✅ `--dry-run` 显示 `10/21` |
| `reports/evolution/status.json` | 缓存 `observation_total:14→21`, `next_action`/`blockers` 文本同步 | ✅ |
| `reports/evolution/phase_b_status.json` | `observation_days_required:14→21` | ✅ |

---

## 3. 验证快照 (2026-08-09 06:32)

```
observation_tracker.py:   required_days=21, days_completed=10, days_remaining=11, estimated=2026-08-24
phase_b_progressive_enabler.py --check: 观察期 10/21 天, 预计 Stage1=2026-08-20
observation_watchdog.py --dry-run:      days=10/21, GATE-A FAIL, 预计达标 2026-08-24
run_evolution_eval.py:   observation_total=21 (lint 0)
全仓硬编码 "= 14" 扫描: 0 命中
```

---

## 4. 经验教训

1. **yaml 嵌套层陷阱**: PM 决策字段在 `settings.` 下, 代码若按顶层键读取会静默回退默认值, 导致配置升级永不生效。读取前必须确认 yaml 实际结构。
2. **单事实源铁律**: 配置值 (观察天数/样本阈值) 只能在 yaml 定义一次, 代码全部 import 读取, 禁止任何 `= 14` 字面量。
3. **状态缓存陈旧**: `status.json` / `phase_b_status.json` 是缓存快照, 代码升级后必须同步刷新, 否则下游读缓存仍用旧口径 (双源分裂的另一种形式)。
4. **grep 全仓验证**: 修复后必须 `search_content` 全仓扫描残留 `= 14` 硬编码, 确认 0 命中才算收尾。
5. **验证要打真实接入点**: 跑 `tracker`/`enabler --check`/`watchdog --dry-run` 三个独立入口, 确认都显示 21 天才算口径统一 (单点验证会漏掉 watchdog 等旁路)。

---

## 5. 第二轮口径分裂 (2026-08-12 复审)

> 用户 2026-08-12 触发"观察期情况"审计, 发现第一轮修复后仍存在两处隐蔽的口径分裂。

### 5.1 数据断档假阳性 (cleaned 文件停滞)

**现象**: watchdog `--dry-run` 报 `断档告警: 6 个交易日无新数据`, GATE-B 仅 7 条 (vs tracker 12 条)。

**根因**: watchdog 读 `daily_returns_cleaned.jsonl` 并按 `quality=="real"` 筛选, 但该 cleaned 文件停留在 2026-08-04 未刷新 (清洗流水线只在历史某次手动跑过, 未纳入 v84_PostMarket 定时任务). raw `daily_returns.jsonl` 已积累到 2026-08-11 共 12 条, 但清洗层未同步。

**修复**: 跑 `python scripts/clean_shadow_returns.py` 手动刷新 → 12 real + 1 missing (08-09 周六). GATE-B 立即恢复 12/21 与 GATE-A 一致。

**经验**: 配置单事实源 (yaml) 只是第一层; **数据单事实源 (raw vs cleaned) 是第二层**, 同样可能分裂. cleaned 文件应纳入定时任务自动刷新, 否则 watchdog 等下游会基于陈旧清洗结果告警。

### 5.2 MIN_SAMPLES_FOR_DSR 硬编码残留

**现象**: yaml `admission_criteria.min_samples_for_dsr=20` (注释 "保留 20 更严格"), 但两个核心 alpha 模块硬编码 15:

| 模块 | 行号 | 原值 | 注释 |
|---|---|---|---|
| `utils/alpha/strategy_evaluator.py` | L85 | `15` | "2026-07-30 PM 决策: 20→15, 配合 21 天观察期" |
| `utils/alpha/shadow_account_adapter.py` | L74 | `15` | 同上 |

**根因**: 第一轮 (2026-08-09) 只修了 `scripts/observation_tracker.py` 等脚本层, 未扫到 `utils/alpha/` 业务核心层. PM 决策原文 "20→15" 在代码中被字面量保留, 与 yaml 后续 "保留 20 更严格" 的反悔决策冲突.

**修复 (路径 A: 代码改读 yaml=20)**:
- 两个模块新增 `_load_min_samples_for_dsr(default=20)` 内嵌 yaml reader (失败安全降级)
- `tests/e2e/conftest.py::sample_daily_returns_14d` fixture 15→20 天
- `tests/e2e/test_shadow_account_lifecycle_e2e.py` 注释/断言/边界测试同步更新
- 9 个相关测试 402 passed/9 failed (全部 pre-existing, 经 git stash 基线对比确认)

**经验**:
1. **yaml 单事实源扫描要扫全栈**: 不能只扫 `scripts/`, 必须扫 `utils/`/`v8.3_institutional/src/` 等业务核心层. `MIN_SAMPLES_FOR_DSR = \d+` 这种正则全仓扫描一次, 才能定位所有硬编码。
2. **PM 决策历史回滚**: yaml 注释保留 "20→15" 历史轨迹, 但 yaml 实际值推翻回 20 "更严格". 代码注释必须同步更新, 否则后人读代码会被旧注释误导。
3. **多入口验证铁律**: tracker/watchdog/launcher 三个独立入口都要跑, 单点验证会漏. 本次 launcher 显示 "16/21 天" (日历口径), tracker 显示 "12/21 天" (样本口径), 两者本就不同, 但都需要 ≥21 才达标。

### 5.3 遗留已知问题 (留待后续修复)

1. **status.json 缓存陈旧**: `reports/evolution/status.json` 的 `eval_status.next_action` 仍停留在 "11/21 天, 还需 10 天" (实际 12/21). 不影响 GATE 判定 (判定读 observation_progress.json 而非 status.json), 但人类读 status 会困惑. 留待后续刷新机制修复。
2. **launcher 不支持历史补跑**: `shadow_admission_launcher.py daily` 只能生成今天的报告, 不支持 `--date` 参数补跑历史. 08-10/11 DSR 报告缺失但数据已在 `daily_returns.jsonl` 供 DSR 全量评估, 不影响判定。
3. **cleaned 文件未纳入定时任务**: `clean_shadow_returns.py` 仍是手动脚本, 应纳入 `v84_PostMarket` 任务, 否则下次又会停滞.

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [代码审查 + 修复批次 标准作业流程 (SOP)](code-review-sop.md) (相似度 15%)
- [EOD 计划任务静默失败 + 观察期样本补录（2026-08-19）](eod-scheduled-task-fix-20260819.md) (相似度 12%)
- [EOD 运维经验沉淀：OpenBLAS 内存修复 + U9 端到端验证 + D7 断言增强（2026-08-07）](eod-operations-lessons-20260807.md) (相似度 11%)
- [Shadow 数据质量闭环设计](shadow-data-quality-loop.md) (相似度 11%)
- [2026-08-08 代码审查修复批次经验沉淀](code-review-fix-batch-20260808.md) (相似度 8%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
