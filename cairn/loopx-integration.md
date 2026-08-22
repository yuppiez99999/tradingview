# loopx 集成方案 (Long-horizon Agent Control Plane)

> 调研日期: 2026-08-21 (W34, 旧 Phase 1)
> 来源: [huangruiteng/loopx](https://github.com/huangruiteng/loopx) — 5k stars, Apache-2.0
> 状态: **调研完成, 集成方案已设计, 待 W34-W35 POC**

---

## 一、项目背景

LoopX 是开源、provider 中立、有状态的长跑 Agent 控制平面:
- 运行在任意 agent harness 之上 (Codex App, Claude Code, Cursor, dsh)
- 提供长跑状态、语义决策、治理、恢复、人机协作
- Objectives / gates / todos / evidence / quota / handoffs 跨 turn 稳定
- Python 3.11+, 零运行时依赖 (仅标准库)
- `pip install loopx` 即可用

**与 v8.6 契合点**: `live_scheduler.py` 长跑调度可增强 — loopx 的"持久目标 + 配额感知自动唤醒 + 可验证交接"提升长跑健壮性。

---

## 二、核心架构

```
objective / issue / project
   │
   ▼
LoopX state: objective + gates + todos + scope + evidence + quota
   │
   ├─ human judgment needed? ── yes ─▶ ask a concrete question and wait
   │
   ├─ safe fallback available? ──────▶ run one bounded agent slice
   │
   ▼
Codex / Claude Code / Cursor / shell agent executes one turn
   │
   ▼
write evidence + handoff + next todo ─▶ quota decides the next tick
```

**四个边界**:
- **Kernel**: 持有 durable goal/todo/gate/evidence/quota/recovery/scheduling 真相
- **Capability**: provider 中立的稳定契约
- **Provider**: 调用外部系统返回有界观察
- **Extension**: 可选 provider 的生命周期管理

---

## 三、核心 tick (与 live_scheduler.py 集成点)

```bash
loopx quota should-run      # 应该行动吗? (配额感知)
loopx todo claim            # 谁拥有这个切片?
loopx todo update           # 发生了什么?
loopx refresh-state         # 下一 turn 应看到什么?
loopx quota spend-slot      # 记账完成的验证切片
```

**集成思路**: `live_scheduler.py` 的简单定时唤醒 → loopx `quota should-run` 配额感知唤醒。

---

## 四、集成方案 (W34-W35)

### W34: 调研 + POC (本周)

- [x] 调研 loopx 状态内核 API (本文档)
- [ ] 在分支做最小可运行 POC
  - `pip install loopx`
  - `loopx connect` (项目根)
  - `loopx doctor` 确认通过
  - `loopx start-goal --guided --project . --goal-text "每日 EOD 管道"`
  - 验证 `loopx status` 显示当前 objective + next todo

### W35: shadow 运行

- [ ] loopx 状态内核与现有 `live_scheduler.py` 并行
- [ ] 对比断点续跑 / 状态一致性
- [ ] **不影响实盘下单** (loopx 仅治理调度时序, 不触碰交易逻辑)
- [ ] 验收: 连续 7 天长跑无状态丢失; 手动 kill 后能续跑; 交易调度时序零偏移

---

## 五、集成点 (live_scheduler.py)

```python
# 现有: live_scheduler.py 简单定时
while True:
    run_daily_workflow()
    sleep(seconds_until_next_tick)

# 集成后: loopx 配额感知
import subprocess

def should_run() -> bool:
    """loopx quota should-run 决定是否行动."""
    result = subprocess.run(
        ["loopx", "quota", "should-run", "--goal-id", "daily-eod"],
        capture_output=True, text=True,
    )
    return "yes" in result.stdout.lower()

while True:
    if should_run():
        subprocess.run(["loopx", "todo", "claim"])
        run_daily_workflow()
        subprocess.run(["loopx", "todo", "update", "--status", "done"])
        subprocess.run(["loopx", "quota", "spend-slot"])
    subprocess.run(["loopx", "refresh-state"])
    sleep(seconds_until_next_tick)
```

**Feature Flag**: `LOOPX_INTEGRATED=0` 默认关闭, shadow 验证后开启。

---

## 六、验收标准

- [ ] `loopx doctor` 通过
- [ ] `.loopx/registry.json` + 活跃 goal state 投影存在
- [ ] `loopx status` 显示当前 objective + user gate + next todo
- [ ] 连续 7 天长跑无状态丢失
- [ ] 手动 kill 后能续跑 (durable state)
- [ ] 交易调度时序零偏移 (不影响实盘)
- [ ] loopx 配额机制不拖慢交易调度实时性

---

## 七、回滚

`LOOPX_INTEGRATED=0` 即切回原 `live_scheduler.py`, 零影响。

---

## 八、风险

| 风险 | 缓解 |
|---|---|
| loopx 配额机制拖慢实时调度 | shadow 运行隔离验证 + feature flag |
| loopx Python 3.11+ 要求 | 确认运行环境 Python 版本 |
| loopx 状态文件污染项目 | `.loopx/` 加入 .gitignore |
| loopx 与现有 cairn 知识层冲突 | loopx 治理调度, cairn 治理知识, 职责分离 |

---

## 九、配套

- loopx 文档: https://huangruiteng.github.io/loopx/docs/
- loopx 中文 README: https://github.com/huangruiteng/loopx/blob/main/README.zh-CN.md
- 用户手册: https://my.feishu.cn/wiki/CaL5wMk9ui17ngkWzeUcMlAYnZg

---

*调研完成时间: 2026-08-21*
*下一步: W34 POC (pip install loopx + loopx connect)*