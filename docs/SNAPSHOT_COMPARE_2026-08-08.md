# 二次审查 · 质量快照对比（2026-08-08 vs 2026-08-07 基线）

> 数据来源：`python scripts/quality_snapshot.py` + `git status --short` 实测。
> 原则：取数以磁盘/ git 现实为准，不与索引幽灵条目纠缠。

## 一句话结论
**头号阻断项（git 游离变更）已解除，但 P0 生产链路代码指标零变化。**
治理重心应从「git 纪律收敛」切换到「P0 可观测性 + 巨文件拆分」。

---

## 1. 头号阻断项：已解除 ✅
| 指标 | 8-7 基线 | 8-8 现状 | 变化 |
|---|---|---|---|
| 未提交变更合计 | 1442 | **9** | **−99.4%** |
| ├ 已删除未提交 | 710 | 0 | 已清 |
| ├ 已修改未提交 | 392 | 9 | 收敛 |
| └ 未跟踪 | 340 | 0 | 已清 |

`git log` 可见 3 次「补交」提交（githooks / 根级脚本 / ms_strategy 源码），属人为、真实收敛。
**意义**：8-7 标定「审查前提失效」不再成立，现在可基于干净工作区做增量审查。

## 2. P0 生产交易路径：零变化 ⚠️
| 指标 | 8-7 | 8-8 | 门禁目标(阶段1) | 状态 |
|---|---|---|---|---|
| P0 print() | 90 | 90 | ≤60 | FAIL（未动）|
| P0 静默异常 | 14 | 14 | ≤6 | FAIL（未动）|
| P0 exception() | 9 | 9 | — | 观察 |
| daily_workflow.py 行数 | 6226 | 6226 | ≤3000 | FAIL（未动）|
| Decimal 使用文件 | 1 | 1 | — | 观察 |

→ git 收敛了，但 P0 代码本身一行没改。90 处 `print()` 仍不进日志，故障不可追溯。

## 3. 分区日志规范（核心治理指标）
| 分区 | 文件 | print() | 密度 | 8-7 文件数 | 变化 |
|---|---|---|---|---|---|
| utils/ | 328 | 55 | 0.2 | 323 | +5 文件，print 持平 |
| tests/ | 158 | 533 | 3.4 | — | — |
| scripts/ | 110 | 1297 | 11.8 | — | — |
| 根目录(P0) | 53 | 456 | 8.6 | 53 | print 461→456 (−5) |
| research/ | 30 | 343 | 11.4 | — | — |
| ui/ | 29 | 0 | 0.0 | — | — |
| tools/ | 16 | 251 | 15.7 | — | — |
| ai_decision/ | 15 | 2 | 0.1 | — | — |
| v8.3_institutional/ | 5 | 65 | 13.0 | — | exception()=0（零堆栈）|

业务 py 文件总数：**942 → 949（+7，自然增长）**。
utils/ 仍是唯一有 CI 门禁的基准区，密度 0.2 稳定。

## 4. 阶段1 门禁达成：0/5 → 1/5
| 门禁 | 数值 | 目标 | 结果 |
|---|---|---|---|
| 未提交变更总数 | 9 | ≤100 | **PASS**（8-7 为 1442，FAIL）|
| git 中 qlib_env 文件 | 13 | ≤0 | FAIL |
| P0 区 print() | 90 | ≤60 | FAIL |
| P0 区静默异常 | 14 | ≤6 | FAIL |
| 单文件最大行数 | 6226 | ≤3000 | FAIL |

唯一移动的门禁是「未提交变更总数」，从 FAIL(1442)→PASS(9)，其余 4 项全未动。

## 5. 新发现 / 需修正点
- 🟡 **git 误提交 `qlib_env/` 虚拟环境**：13 个文件（激活脚本 + pip.exe/wheel.exe）。
  应 `git rm --cached -r qlib_env` 并加入 `.gitignore`。8-7 已存在（报 13），
  当时被 1442 淹没，现在成显眼 FAIL。剩余 9 处未提交中有 4 个就是 `qlib_env/Scripts/*.exe` 更新。
- 💭 **快照脚本标签 bug**：`quality_snapshot.py` 中
  `git_pollution = len(git_ls("qlib_env"))`，但输出标签写「git 中 venv/二进制」。
  实际只扫 `qlib_env/`，不扫通用 `venv/`。建议改标签为「git 中 qlib_env/ 文件」以免误导。
- 💭 剩余 9 处未提交明细：
  - `.agents/skills/{intraday_abnormal_move_alert,policy_headline_interpreter,position_sizing_decision}_skill/SKILL.md`（3，技能文档）
  - 子模块指针漂移：`_archive/research_references_quarantine/references/Vibe-Trading`、`ms_strategy`（2）
  - `qlib_env/Scripts/{pip,pip3.8,pip3,wheel}.exe`（4，venv 二进制）

## 6. 下一步建议（按优先级）
1. 🔴 把 `qlib_env/` 移出 git 跟踪 + .gitignore（顺手清掉剩余 9 处里的 4 个 exe）。
2. 🟡 启动 P0 路径 print→logger 替换（90 处，按 CODE_REVIEW_STANDARD §2.1；CLI 输出行尾 `# allow-print`）。
3. 🟡 给 `daily_workflow.py` 零堆栈问题补 `logger.exception`（14 静默异常 + 9 exception）。
4. 💭 排期拆分 `v8.3_institutional/daily_workflow.py`（6226 行 → ≤3000）。
5. 💭 修正快照脚本标签 bug。
