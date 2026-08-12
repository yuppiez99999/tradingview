# 代码审查流程 v1.0

> 配套文档：`docs/CODE_REVIEW_STANDARD.md`（审查标准与检查清单）
> 制定日期：2026-08-08

---

## 0. 设计原则

这份流程针对**单人/小团队 + 真金白银实盘**的实际情况设计，不照搬大厂那套。

三条约束决定了流程形态：

1. **不能停工整改** —— 系统每天要跑，¥53 万在场内，不存在"暂停两周重构"的选项
2. **门禁必须能过** —— 定太高会被 `--no-verify` 绕过，那还不如不定
3. **存量与增量分治** —— 存量 942 个文件慢慢治，增量代码从第一天起就守规矩

核心思路：**增量卡死，存量渐进。** 新代码不许再欠债，旧债按优先级还。

---

## ⚠️ 前置条件：先把工作区收敛回版本控制

**在执行本文任何流程之前，必须先解决这个问题。**

实测当前工作区状态：

```
已删除未提交: 710    已修改未提交: 392    未跟踪: 340
未提交变更合计: 1442
```

代码审查的物理前提是**存在一个可审查的变更单元**。1442 个变更游离在 git 之外意味着：

- PR 的 diff 反映不了真实改动 —— 审查对象本身就是失真的
- 出问题无法定位是哪次变更引入的
- 回滚没有可靠锚点 —— 实盘系统出事时这是致命的
- 任何门禁都可以被"不提交"轻易绕过

**收敛步骤**（建议半天完成）：

```bash
# 1. 先看清楚都是些什么
git status --porcelain > /tmp/wt_status.txt
git status --porcelain | grep "^ D" | head -30   # 已删除的是哪些

# 2. 已删除的多在 v8.3_institutional/_archive_dead_code/ —— 确认是有意归档后
git add -A v8.3_institutional/_archive_dead_code/
git commit -m "chore: 归档 v8.3 死代码 (已确认无引用)"

# 3. 未跟踪文件分流: 该提交的提交, 该忽略的进 .gitignore
git status --porcelain | grep "^??" | awk '{print $2}'

# 4. 已修改的按模块分批提交, 不要一次 git add -A
```

> 第 4 步很重要：392 个已修改文件如果一次性提交，会产生一个无法审查的巨型 commit，
> 等于把问题从"没提交"变成"提交了但审不了"。**按模块分 5-10 次提交。**

收敛完成后运行 `python scripts/quality_snapshot.py`，确认"未提交变更总数"降到 100 以下，
再继续下面的流程。

---

## 1. 分支策略

保持轻量，够用就行。

```
main          ← 生产分支，只接受 PR 合入，禁止直推
  ├── feat/xxx    功能开发
  ├── fix/xxx     缺陷修复
  ├── refactor/xxx 重构
  └── hotfix/xxx  紧急修复（可走快速通道，见 §5）
```

**规则**：

- `main` 分支设为 protected，禁止 force push
- 分支从 `main` 切出，合并前 rebase 或 merge 最新 `main`
- 分支存活 ≤ 5 天，超期拆分——长分支必然导致大 PR，大 PR 必然审查失效

---

## 2. PR 规范

### 2.1 体量约束

| 变更行数 | 处置 |
|---|---|
| ≤ 200 行 | ✅ 理想区间 |
| 200–400 行 | ⚠️ 可接受，需在描述中说明为何无法拆分 |
| > 400 行 | 🔴 要求拆分，纯格式化/重命名/文件移动除外 |

理由很实际：超过 400 行，审查者的缺陷检出率会断崖下跌。
大 PR 得到的往往是"LGTM"，等于没审。

### 2.2 PR 描述必填项

见 `.github/pull_request_template.md`，核心四项：

1. **改了什么** —— 一句话
2. **为什么改** —— 关联的问题/需求
3. **影响面** —— 是否触及 P0 交易路径、是否改变策略行为
4. **怎么验证** —— 测试方式、回测结果（若涉及策略）

**涉及策略逻辑变更的 PR，必须附回测对比数据。** 改了信号计算却不给回测，
等于让审查者凭想象判断这次改动是赚是亏。

### 2.3 自查（提 PR 前）

```bash
# 1. 静态扫描（只扫改动文件）
.venv/Scripts/ruff.exe check $(git diff --name-only main...HEAD -- "*.py" | tr '\n' ' ')

# 2. P0 文件额外查类型
python -m mypy --config-file mypy.ini <改动的P0文件>

# 3. 跑相关测试
python -m pytest tests/unit -m "not slow" -q
```

---

## 3. CI 门禁演进

### 3.1 当前状态（已核实）

CI 有 7 个 job，基础不错，但覆盖范围有缺口：

```yaml
# ci.yml:98  —— mypy 只扫 utils/
python -m mypy --config-file mypy.ini utils/

# ci.yml:104 —— pylint 只扫 utils/ 的 4 个子目录
python -m pylint --rcfile=.pylintrc utils/infra/ utils/risk/ utils/execution/ utils/data/

# ci.yml:206 —— 覆盖率门禁
python -m pytest ... --cov=utils --cov=v8.3_institutional/src --cov-fail-under=60
```

**缺口**：根目录 53 个生产交易文件 + `v8.3_institutional/`（`src/` 之外）
**不在任何静态门禁范围内**。这正是 `print()` 密度相差 43 倍（`utils/` 0.2 vs 根目录 8.7）
的直接成因。

### 3.2 演进路线

#### 阶段 1（2 周内）—— 止血，不设高门槛

目标：**把新债堵住 + 清掉零成本的存量问题**

- [ ] **收敛工作区**（见前置条件，1442 → ≤100）
- [ ] **修 2 个 py38 语法错误**
      `apply_ocr_fixes.py:150`、`scripts/_pip_noproxy.py:39`（f-string 用了 3.12 语法）
- [ ] **清理 git 中的 13 个 venv 二进制**
      ```bash
      git rm -r --cached qlib_env
      echo "qlib_env/" >> .gitignore
      git commit -m "chore: 移除误提交的虚拟环境二进制"
      ```
- [ ] **增量门禁上线**：CI 新增 job，只扫本次 PR 改动的文件
      ```yaml
      - name: 增量静态检查
        run: |
          FILES=$(git diff --name-only origin/main...HEAD -- "*.py" \
                  | grep -v "^_archive/\|^qlib\|^external/" | tr '\n' ' ')
          if [ -n "$FILES" ]; then
            python -m ruff check $FILES --select F,E9,BLE001,T201
          fi
      ```
      > `T201` 就是 `print` 检查。增量门禁的好处：**存量 print（仅根目录生产区就 461 处）不阻塞，
      > 但新写的 print 一个都进不来**。这是"增量卡死"的技术实现。
- [ ] **P0 文件纳入 mypy**（先允许 warning，只看不拦）

#### 阶段 2（1 个月内）—— 补可观测性

目标：**让故障可追溯**——这是实盘系统的生命线

- [ ] `v8.3_institutional/` 异常处理改造：`.exception()` 从 0 提到 ≥60
- [ ] P0 区 `print()` 从 90 降到 ≤30
- [ ] 建立**可信的**覆盖率基线（当前 `coverage.xml` 是 08-06 本地快照，
      25 个包只有 3 个有数据，不能作为基线）
- [ ] P0 区 mypy 转为阻断
- [ ] pylint 扫描范围扩到 `utils/` 全量 + P0 根目录文件

#### 阶段 3（1 季度内）—— 提标准

- [ ] P0 覆盖率 ≥70%，全仓 ≥60%
- [ ] mypy strict 模式（P0 区）
- [ ] 拆分 `daily_workflow.py`（6226 行 → ≤1500 行）
- [ ] `Decimal` 迁移（累加型金额优先）

### 3.3 pre-commit 本地门禁

本地拦截比 CI 便宜——CI 跑一次几分钟，本地几秒。

```yaml
# .pre-commit-config.yaml 建议增加
- repo: local
  hooks:
    - id: no-print-in-p0
      name: P0 交易文件禁用 print
      entry: python scripts/check_no_print_p0.py
      language: system
      files: ^(alpha_hedge_engine|build_plan_executor|daily_build_and_hedge|daily_trade_executor|hedge_execution_orders|hedge_quantity_calculator|institutional_pipeline_runner|live_scheduler|rebalance_execution_orders|run_daily_eod|signal_monitor|signal_post_processing|stop_loss_monitor|today_hedge_decision|vol_adjusted_stop_loss)\.py$
```

---

## 4. 审查执行流程

### 4.1 标准流程

```
提 PR → 自动门禁 → 人工审查 → 修改 → 复审 → 合并
         (CI)      (清单核对)         (确认)
```

### 4.2 完成定义（DoD）——「验证过」而非「改过了」

> 来源：`CODE_REVIEW_AUDIT_2026-08-09B.md` §4.1 复盘。连续三轮审查的根因是
> **修复完成的判定标准是"代码改了"，而不是"验证过它按预期工作"**。
> 以下两条为强制 DoD，缺一则 PR 不得合并。

#### 4.2.1 修复类变更 DoD（强制）

每项代码变更必须满足以下条件才能标记为"完成"：

- [ ] 变更已提交，commit message 带 `[Gx]` 或 `[Ux]` 标记
- [ ] 变更文件已通过 `ruff check` 和 `mypy`
- [ ] 无新增硬编码路径/凭证/行情
- [ ] **修复前会失败的回归测试已写**（回滚必红 / 修复必绿）
- [ ] 回归测试已跑且 PASS（修复必绿）
- [ ] 测试断言符合引擎真实行为（先读引擎逻辑再写断言）
- [ ] `industrial_grade_check.py` 跑完，无新增 FAIL
- [ ] `assert_data_validity.py` 跑完，无新增 FAIL
- [ ] `engineering_debt_gate.py` 跑完，无新增 RED
- [ ] **门禁类变更已做 CI 同格式负向测试**（如正斜杠路径 vs Windows 反斜杠）
- [ ] 若新增/改动 Python 文件，已重跑 `ruff_baseline_gen.py`
- [ ] 若新增测试，已重跑 coverage 并确认基线无漂移
- [ ] 若修复了新的 bug 模式，已更新 `cairn/code-review-lessons-v8.4.md` 对应条目
- [ ] 若涉及数据链路/执行链，已更新 `cairn/data-integrity-fix-lessons-*.md`

#### 4.2.2 审查会话 DoD（强制）

每次代码审查（含 AI 审查）必须满足：

- [ ] 确定审查范围（文件列表）和重点（按资金风险排序：交易执行 > 风控 > 回测 > 基础层）
- [ ] 确认审查工具版本（ocr / ruff / bandit）与 CI 一致
- [ ] 关键 bug 亲自读源码验证，不只信子代理输出
- [ ] 区分"模块本身质量好"与"调用链是软控制"
- [ ] 记录每条缺陷的**真实影响**（资金损失 / 数据失真 / 崩溃 / 沉默失败）
- [ ] 缺陷分级（CRITICAL / HIGH / MEDIUM / LOW）且可溯源
- [ ] 每条缺陷附带**修复验证方法**（命令 + 预期结果）
- [ ] 区分"已修复"与"需修复"——已修复的须有回归测试证明
- [ ] 更新 `cairn/code-review-lessons-v8.4.md` 的 bug 模式清单

#### 4.2.3 计划模板 DoD 强制列

计划文档中每项任务必须包含：

| 任务 | 负责人 | 验证命令 | 预期结果 | 回滚条件 |
|------|--------|----------|----------|----------|
| 修复 M-1 | xxx | `pytest tests/test_m1.py -v` | 3 个回归测试 PASS | 门禁 FAIL 立即回滚 |
| 接入 QMT | xxx | `python scripts/industrial_grade_check.py` | C1 从 WARN 变为 PASS | dry_run 异常立即回滚 |

#### 4.2.4 关键流程铁律（已沉淀到 cairn/）

1. **修复后立即跑门禁+测试**：防止增量引入回归
2. **门禁负向测试用 CI 同格式入参**：确保门禁真的在拦
3. **nightly 全量扫描**：捕获增量门禁漏扫的存量问题
4. **fail-closed 原则贯穿门禁本身**：门禁异常时阻断而非放行
5. **每轮修复后重跑基线生成**：消除 ruff/coverage 基线漂移
6. **事实源唯一原则**：成交/持仓/fills 必须有单一落盘真相源
7. **研究/生产物理隔离**：消除 `import research.*` 等隐蔽依赖

#### 4.2.5 传统 DoD（保留）

- [ ] 所有 HIGH/MEDIUM 缺陷已修复或明确延期
- [ ] 回归测试通过
- [ ] 门禁三件套无新增 FAIL
- [ ] 审查报告归档

**审查者动线**（用项目已有工具，别手撸 grep）：

1. `detect_changes_tool` —— 拿变更风险评分
2. `get_impact_radius_tool` —— 看影响半径，判断是否触及 P0
3. 对高风险函数查 `tests_for` —— 确认测试覆盖
4. 按 `CODE_REVIEW_STANDARD.md` 清单逐项核对
5. 出具分级意见（🔴/🟡/💭/✅）

### 4.2 审查强度分级

| 变更触及 | 审查者 | 必须核对 |
|---|---|---|
| P0 交易路径 | 2 人（其中 1 人须熟悉交易逻辑） | 全部 🔴 项 + §3 量化专项清单 |
| P1 核心业务 | 1 人 | 全部 🔴 项 |
| P2/P3 | 1 人（可异步） | 🔴 项抽查 |
| 纯文档/注释 | 免审 | — |

> 单人开发场景下，"2 人审查"可用 **AI 审查 + 本人隔日复审** 替代。
> 隔一天再看自己的代码，检出率会明显提高——这不是玄学，是认知偏差消退。

### 4.3 时效

- 审查响应：24 小时内
- 修改后复审：12 小时内
- 超时未审：P2/P3 可自动合并，P0/P1 不可

---

## 5. 紧急修复通道

实盘系统必须有快速通道，否则流程会在最需要它的时候被绕过。

**触发条件**（满足其一）：
- 交易日盘中，系统故障影响下单
- 风控失效，存在超预期敞口
- 数据错误导致信号明显异常

**流程**：

1. `hotfix/xxx` 分支，**允许跳过非必要门禁**（保留 ruff `F` 类检查）
2. 合并后 **24 小时内**必须补：PR 描述、测试、事后复盘
3. 每个 hotfix 必须回答：**为什么常规流程没能拦住它？**
   答案会转化为新的检查项——这是流程自我进化的唯一途径

**滥用防护**：hotfix 每月 >3 次，说明常规流程有系统性问题，需专项复盘。

---

## 6. 存量债务治理

存量不能靠"哪天有空集中整改"，那天永远不会来。

### 6.1 童子军规则

**碰到哪里，顺手清理哪里。** 修改一个文件时：

- 该文件的 `print()` → 改 `logger`
- 该文件的静默 `except` → 加 `logger.exception`
- 该函数缺类型注解 → 补上

单次成本很低，累积效果显著。且不需要额外排期。

### 6.2 专项清理排期

| 优先级 | 事项 | 工作量 | 何时 |
|:---:|---|---|---|
| P0 | 2 个 py38 语法错误 | 0.5h | 本周 |
| P0 | 13 个 git 二进制清理 | 0.5h | 本周 |
| P1 | P0 区 `print()` → `logger` | 2d | 2 周内 |
| P1 | `v8.3/` 补 `.exception()` | 2d | 1 月内 |
| P2 | 建立可信覆盖率基线 | 1d | 1 月内 |
| P2 | 拆 `daily_workflow.py` | 5d | 1 季内 |
| P3 | `Decimal` 迁移 | 3d | 1 季内 |

### 6.3 债务不再增长的保证

**增量门禁**（阶段 1）是关键机制：
存量 `print()`（仅根目录生产区就 461 处）不阻塞任何人，但新增 1 个都进不来。
这样存量是有限的、递减的，而不是一边治一边涨。

---

## 7. 度量与复盘

### 7.1 每周看板

```bash
# 一键生成质量快照
python scripts/quality_snapshot.py   # 阶段1 待建
```

跟踪 4 个数：

| 指标 | 口径 |
|---|---|
| P0 区 `print()` 数 | 应单调递减 |
| `v8.3/` `.exception()` 数 | 应单调递增 |
| 增量门禁拦截次数 | 反映门禁是否在起作用 |
| hotfix 次数 | >3/月 需专项复盘 |

### 7.2 阶段复盘

每阶段结束复盘三个问题：

1. 门禁指标是否达成？未达成的**真实阻碍**是什么？
2. 有没有出现绕过流程的情况？为什么绕？
3. 哪些检查项从没抓到过问题？—— **抓不到问题的检查项应该删掉**，
   它们只增加成本不产生价值

---

## 8. 落地检查表

从今天开始，按序执行：

**本周**
- [ ] 修 `apply_ocr_fixes.py:150` + `scripts/_pip_noproxy.py:39` 语法错误
- [ ] `git rm -r --cached qlib_env` + 更新 `.gitignore`
- [ ] `main` 分支设 protected
- [ ] 落地 `.github/pull_request_template.md`

**2 周内**
- [ ] CI 增加增量静态检查 job（含 `T201` print 检查）
- [ ] P0 文件纳入 mypy（warning 模式）
- [ ] pre-commit 增加 P0 print 检查

**1 月内**
- [ ] `v8.3/` 异常处理改造
- [ ] P0 区 print 清理
- [ ] 建立可信覆盖率基线
- [ ] 首次阶段复盘

---

**版本** v1.0 ｜ **制定** 2026-08-08 ｜ **下次复审** 阶段1 结束后
