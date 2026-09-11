# specs/ — SDD 规格目录（spec-kit 集成）

> 方案：`cairn/spec-kit-sdd-integration-20260911.md` · 宪法：`.specify/memory/constitution.md` · 门禁：`scripts/speckit_converge_gate.py`

## 命名与结构

```
specs/<ROADMAP-TaskID>-<slug>/
├── spec.md                  # 需求 + 用户故事 + 验收判据表（模板 .specify/templates/spec-template.md）
├── plan.md                  # 技术计划（模板 .../plan-template.md）
├── tasks.md                 # 任务清单（模板 .../tasks-template.md；含 测试范围 声明行）
└── implementation-notes.md   # converge 产出（门禁输出摘录）
```

- 目录名必须带 ROADMAP TaskID 前缀（如 `G1-qmt-live-order-wiring`）；无 TaskID 不合规范。
- 生成新 feature 目录可用：`.specify/scripts/powershell/create-new-feature.ps1`（或手工按模板建）。

## 铁律（违者评审打回）

1. spec 只**引用** `cairn/ROADMAP.md` §CURRENT STATE 的条目号，禁止复制资金/绩效/窗口数值。
2. `[fix]` 任务必须附"修复前会失败"的回归测试。
3. 勾选任务须带三要素：文件+日期 / 实证值 / 复现命令。
4. converge 完成的唯一定义：`speckit_converge_gate.py` exit 0（四门禁全绿）。
5. **spec 生成权在本端**（Claude Code / CodeBuddy 交互会话）；云端 NPC 线只读、只产出实现。
6. `tasks.md` 复选框在三端同步冲突时取并集（勾选 OR）；`spec.md`/`plan.md` 冲突则同步器挂起人工裁决。

## 使用流程（端到端）

```
1) /speckit.specify "<需求>"      → specs/<TaskID>-<slug>/spec.md（Roadmap Ref 必填 + 验收判据表）
2) /speckit.plan                  → plan.md / research.md / data-model.md / quickstart.md
3) /speckit.tasks                 → tasks.md（**测试范围** 声明行 = converge G2 入参）
4) /speckit.implement             → 逐任务实现；收尾自动触发 after_implement 强制钩子
      └─ 钩子跑 speckit.gate → scripts/speckit_converge_gate.py（四门禁；失败短路 RC=1）
5) /speckit.converge              → 先跑 before_converge 强制钩子重确认，再按 spec/plan/tasks 收敛（只追加 Convergence 段）
6) 全绿后：勾任务（三要素）+ 更新 ROADMAP 状态 + cairn/LOG.md + 三端同步
```

**直接手工跑门禁**（不经代理，用于自检）：

```powershell
.venv/Scripts/python.exe scripts/speckit_converge_gate.py `
  --files scripts/xxx.py tests/unit/test_xxx.py `
  --pytest-args "tests/unit/test_xxx.py -q"
```

门禁接线位置 = `.specify/extensions.yml`（`after_implement` + `before_converge`，均 `optional: false`）。
**勿删该文件**：命令文件检测到它缺失或不可解析时会明确报告"未检查任何钩子"，等于门禁空转。
