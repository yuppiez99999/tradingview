# 跨线合并与门禁集成 playbook（2026-09-11 实战沉淀）

> **适用**：`cnb`（云端 NPC 自动开发线）↔ 本地 `main` 的定期合并；以及任何「入站改动 + 未提交 WIP 共存」的合并。
> **首次成文依据**：merge `f7c8fca3`（同步 PR#14/#16/#17，入站 47 文件 / 10 提交 / 1 冲突 / 17 处 DTZ005 拦截）。

---

## 0. 硬约束（先读）

- 合并前必须算 **脏文件 ∩ 入站改动**；有交集就先处置（§1），否则「局部干净地」丢掉未提交工作。
- 绝不 `git clean -fd`；**绝不用 `--no-verify` / `SKIP_DTZ_CHECK=1` 跳过 pre-commit**（拦下来的是真债）。
- 仓库有**自动提交**在跑 → 合并期间不做破坏性试验；不擅自 `push --force`。

## 1. 合并前三件套

```bash
git fetch <remote> main
git rev-list --left-right --count HEAD...<remote>/main        # 左=本地领先, 右=本地落后
git status --porcelain                                        # 脏文件集
git diff --name-only HEAD...<remote>/main                     # 入站改动集
# 求交集 → 交集非空即须备份+stash
```

交集非空时的处置（**只动这几个文件，不碰其它 WIP**）：

```powershell
Copy-Item <file1>,<file2> -Destination "$env:TEMP\premerge_backup_<date>"   # ① 先备份
git stash push -m "pre-merge WIP (<用途>)" -- <file1> <file2>               # ② 按路径 stash
```

> 本次实例：3 个文件（`daily_trade_executor.py` / `executor/premarket.py` / `tests/unit/test_daily_trade_executor_unit.py`）
> 的 09-10 WIP 与入站改动相撞 —— 备份 + 路径级 stash 后 merge，再 `git stash pop` **干净恢复**（两侧改的是不同区域）。

## 2. 冲突解决：append-only 日志要「取并集」

- 高发区 = **`cairn/LOG.md`**：两侧都在文件**顶部**新增条目 → 冲突。
  正解 = **并集**（只删 3 个标记行，两侧条目全留）；**不要**二选一（会静默丢另一条线的记录）。
- 1MB / 7000 行级文件**不要手工编辑**：用脚本按行扫描 —— 进入 `<<<<<<<` 后丢弃标记行、保留普通行、`>>>>>>>` 退出；
  写回 UTF-8（`io.open(..., encoding="utf-8", newline="")` 保持换行风格）。
- 收口判据：`git diff --name-only --diff-filter=U` 为空 → `git add <冲突文件>` → `git commit --no-edit`。

## 3. merge 提交会被 pre-commit 拦（这是设计，别绕）

| 门 | 行为 | merge 场景下的正确处置 |
|---|---|---|
| **DTZ005** | 把**暂存 `.py` 整体**交给 ruff ⇒ 入站代码里的存量裸 `datetime.now()` 直接卡住 merge commit | 按仓库口径改 `now_bj()`（业务/状态/审计时间戳**语义等价**：本机 CN 时区，旧 `now()` 返回的就是北京墙上时间）。**不要**在 merge 提交里顺手把审计时间戳改成 `utc_iso()`（静默 −8h + tz 后缀，跨线语义变更应由对线自己决定） |
| **mypy 基线** | 只对暂存 `utils/**.py` 比对总数 vs 基线 | 入站带类型债会在此暴露；本次 `317 = 317, +0` ⇒ CNB 侧无新增类型债 |
| **NaN 守卫 / P0 自检** | 暂存文件扫描 | 通常直接通过；失败即真问题 |

**批量插 import 的正确姿势**：用 **`ast` 解析顶层 `Import/ImportFrom` 取 `end_lineno`**，在其后插入。
按「前 N 行最后一条 import」定位会撞上 `from X import (` **多行导入的开头**，把 import 劈成两半 → `invalid-syntax: Expected one or more symbol names after import`。
修复两步：①先删掉错插行（文件恢复可解析）→ ②再 `ast` 定位插入。改完用 `ruff check --select F401,I001 --fix` 清失效导入与排序。

## 4. 合并后：恢复 WIP + 自检

1. `git stash pop`：干净恢复即完成（stash 自动 drop）；**若冲突 → 不 pop 到冲突态**：保留 stash（不 drop），把文件恢复成合并版，交人工裁决（另可对照 `%TEMP%\premerge_backup_<date>\`）。
2. `py_compile` 恢复的 WIP 文件（确认与入站代码组合后仍可解析）。
3. 定向自检：配置门禁（`validate_configs`）+ 受影响域单测 + 本次主线的回归测试。
4. **数据类改动的等价性自证**：加"关闭新机制的对照组"，要求与旧口径数值重合（本次 A/B 两组差 ≤0.0005pp）。

## 5. 推送与同步

```bash
git -c http.version=HTTP/1.1 push origin main     # GitHub 直连/代理常 "unexpected eof"; HTTP/1.1 稳定
git push cnb main                                  # 若被拒 (fetch first) → 回 §1 再合, 不要强推
git rev-list --left-right --count HEAD...origin/main   # 建议收口 0/0
```

推前对 `<remote>/main..HEAD` 扫明文密钥：`sk-/ak_/api_key/password/BEGIN (RSA|OPENSSH|PRIVATE) KEY`。
**注意**：remote URL 里内嵌 token（如 `cnb`）只存在于 `.git/config`，**不得入库**。

## 6. 本次复现命令

```bash
git fetch cnb main && git rev-list --left-right --count HEAD...cnb/main
Copy-Item <3 文件> -Destination "$env:TEMP\premerge_backup_20260911"
git stash push -m "pre-merge WIP (09-10 premarket 拆分/trade_plan 缺失处理)" -- <3 文件>
git merge cnb/main -m "merge(cnb): 同步云端 NPC 自动开发线 PR#14/#16/#17"
# 冲突: cairn/LOG.md → 取并集(脚本删标记行) → git add → ruff DTZ005 清零 17 处 → git commit --no-edit
git stash pop
git -c http.version=HTTP/1.1 push origin main && git -c http.version=HTTP/1.1 push cnb main
```

## 7. 相关文档

- `cairn/ROADMAP.md`（口径与决策登记唯一事实源）
- `cairn/timezone-convention-20260907.md`（DTZ005 / `now_bj` / `utc_iso` 口径）
- `cairn/etf-option-hedge-model.md` §v9.1（本轮主线的实证与教训）
