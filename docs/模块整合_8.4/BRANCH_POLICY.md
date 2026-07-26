# BRANCH_POLICY — 模块整合分支策略（T1.1 交付物）

> 分支隔离策略文档：三层保护框架 Layer 1 落地。
> 创建日期：2026-07-26
> 任务编号：T1.1
> 适用范围：终极量化交易系统 8.4 模块整合全过程

---

## 1. 分支模型

### 1.1 分支类型

| 分支类型 | 命名规则 | 生命周期 | 用途 |
|----------|----------|----------|------|
| 主分支 | `main` | 永久 | 生产基线，受保护，仅接受 PR 合并 |
| 阶段整合分支 | `integration/phase{N}-{name}` | 2-12 周 | 各 Phase 的整合工作分支 |
| 特性分支 | `feature/MIG-{N}-{slug}` | 1-3 天 | 单个任务（T{N}.{M}）的实现分支 |
| 紧急修复分支 | `hotfix/{issue}-{slug}` | <1 天 | 生产紧急修复（绕过整合分支） |

### 1.2 当前活跃分支

- `main` — V9 生产基线 `ad8bc933` (2026-07-26 锁定)
- `integration/phase1-infra` — Phase 1 基础设施整合（基于 `ad8bc933` 创建）

后续将创建：
- `integration/phase2-llm` — Phase 2 AI 决策层
- `integration/phase3-exec-risk` — Phase 3 执行与风控层
- `integration/phase4-signal-macro` — Phase 4 信号与宏观层
- `integration/phase5-polish` — Phase 5 优化与完善

---

## 2. 合并闸门（6 项全部满足）

任何向 `main` 合并的 PR 必须满足以下 6 项闸门：

### 2.1 闸门清单

| 编号 | 闸门 | 验证方式 | 失败处理 |
|------|------|----------|----------|
| G-1 | PR 至少 1 个 reviewer 批准 | GitHub/GitLab PR 审批 | 等待审批 |
| G-2 | CI 全绿：mypy + pylint + pytest unit + pytest integration | CI 自动触发 | 修复后重推 |
| G-3 | V9 基线回归通过：DSR>=5 + 年化>=15% + 回撤<=10% + Sharpe CV<1.0 | `scripts/_run_v9_regression.py` nightly | 立即回滚 |
| G-4 | ConfigManager 漂移检测通过（`get_config_source(name)` 一致性） | `scripts/_verify_config_manager.py` | 修复配置路径 |
| G-5 | 无新增硬编码密钥 | `git secrets --scan` | 移除密钥到 .env |
| G-6 | 双签：阶段负责人 + 风控负责人 | PR 评论中签字 | 等待签字 |

### 2.2 闸门优先级

- **G-3 / G-5**：致命闸门，失败立即阻断合并，不可覆盖
- **G-1 / G-2 / G-4 / G-6**：关键闸门，失败需修复后重推

### 2.3 闸门豁免

仅以下情况可申请豁免（需 CTO 级书面批准）：
- 文档-only PR（仅 .md 修改）— 可豁免 G-2 / G-3
- 测试-only PR（仅 tests/ 修改）— 可豁免 G-3
- 配置-only PR（仅 yaml 修改）— 可豁免 G-3，但需双签

---

## 3. 分支保护规则

### 3.1 main 分支保护

- ❌ 禁止直接 push（仅接受 PR 合并）
- ❌ 禁止 force push
- ✅ 必须通过 6 项闸门
- ✅ 必须保持线性历史（merge commit 不允许，使用 squash/rebase merge）

### 3.2 integration 分支保护

- ✅ 允许直接 push（阶段负责人）
- ✅ 允许 merge commit（便于追溯）
- ❌ 禁止 force push（除非经阶段负责人书面批准）
- ✅ 必须定期 rebase 到 main 最新（每周一次）

### 3.3 feature 分支保护

- ✅ 允许直接 push（任务负责人）
- ✅ 允许 force push（个人工作分支）
- ✅ 合并到 integration 后立即删除

---

## 4. 冲突解决原则

### 4.1 优先级

1. **`utils/config_manager.py`** — 任何修改必须立即 rebase 到所有活跃 integration 分支
2. **`v8.3_institutional/config/feature_flags.yaml`** — 同上
3. **V9 训练/推理路径**（`qlib_v9_train.py` / `lgb_*_trainer.py` / `pipeline_orchestrator.py`）— 一律拒绝修改（除非有 ALIGNMENT §4.1 书面豁免）
4. **`quantitative_system.py`**（如存在）— Phase 5 拆分期间保持只读

### 4.2 冲突解决流程

```
冲突发生
   │
   ├── 涉及 config_manager / feature_flags
   │      └── 立即升级到阶段负责人 + 风控负责人双签
   │
   ├── 涉及 V9 训练/推理路径
   │      └── 一律拒绝，要求修改方调整
   │
   └── 其他模块
          └── 由两位 contributor 协商解决，30 分钟未决升级到阶段负责人
```

---

## 5. V9 基线锁定

### 5.1 当前锁定

- **锁定文件**：[docs/模块整合_8.4/V9_BASELINE_LOCK.txt](file:///e:/各种PY程序/28-终极量化交易系统8.4/docs/模块整合_8.4/V9_BASELINE_LOCK.txt)
- **基线 commit**：`ad8bc933dc375f41ffd94fcec479646b97419636`
- **锁定日期**：2026-07-26
- **基线指标**：DSR=8 / 年化=19.62% / 回撤=9.95% / Sharpe CV=0.88（全部达标）

### 5.2 基线变更流程

1. 提交基线变更申请（更新 V9_BASELINE_LOCK.txt + ALIGNMENT 文档）
2. CTO 级书面批准
3. 重新运行 V9 基线回归测试（T1.8）确认新基线达标
4. 更新所有 integration 分支基点
5. 通知所有整合参与者

---

## 6. 集成模式

### 6.1 feature → integration

```
git checkout integration/phase1-infra
git pull origin integration/phase1-infra
git checkout -b feature/MIG-001-feature-flags
# ... 开发 + 提交 ...
git push origin feature/MIG-001-feature-flags
# 创建 PR: feature/MIG-001-feature-flags → integration/phase1-infra
# 闸门: G-1 + G-2 + G-4 + G-5 (G-3 nightly, G-6 阶段合并时)
```

### 6.2 integration → main（阶段合并）

```
# Phase 完成后
git checkout integration/phase1-infra
git rebase main  # 确保 main 最新
git push origin integration/phase1-infra
# 创建 PR: integration/phase1-infra → main
# 闸门: G-1 + G-2 + G-3 + G-4 + G-5 + G-6 (全部 6 项)
```

### 6.3 hotfix → main（紧急修复）

```
git checkout main
git checkout -b hotfix/issue-123-stop-loss-bug
# ... 修复 + 提交 ...
git push origin hotfix/issue-123-stop-loss-bug
# 创建 PR: hotfix/issue-123-stop-loss-bug → main
# 闸门: G-1 + G-2 + G-3 + G-5 + G-6 (G-4 可豁免)
```

---

## 7. 责任人

| 角色 | 当前担当 | 职责 |
|------|----------|------|
| 阶段负责人 | AI 代为执行（待用户指定） | integration 分支管理 + PR 审批 |
| 风控负责人 | AI 代为执行（待用户指定） | G-6 双签 + 风险升级 |
| CTO 级审批 | 用户 | 基线变更 + 豁免审批 |

> 注：在用户指定人类责任人之前，AI 代为执行所有角色，但 G-6 双签由用户在 PR 审批时履行。

---

## 8. 验收标准（T1.1）

- [x] `integration/phase1-infra` 分支存在且通过 `git log` 可追溯到 main
- [x] 分支保护规则：main 仅接受 PR 合并，需 1 review + CI 全绿（已写入策略文档）
- [x] V9 基线 commit hash 记录到 `docs/模块整合_8.4/V9_BASELINE_LOCK.txt`

---

## 9. 下一步

T1.1 完成后，进入 T1.2 — 创建 utils/ 子目录结构与 re-export 框架。
