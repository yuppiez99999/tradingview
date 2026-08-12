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
