# Copilot 协作指引（仓库级）

本仓库是「终极量化交易系统 v8.6」——A股多因子选股 / 回测 / 风控 / 执行的全栈量化系统。
仓库结构与规则约定见根目录 `AGENTS.md`。

## 进入项目先读
1. `AGENTS.md`（根目录）—— 规则与导航入口，必读。
2. `cairn/ROADMAP.md` —— 路线图与「当前焦点」，最新条目在顶部。
3. 按需读 `cairn/*.md` 知识专题（执行链断链诊断、质量门禁、环境隔离、GBK 编码坑等）。

## 开发铁律（必须遵守）
- **失败友好，禁止静默失败**：fail-safe 必须区分「告警」与「失败」并走 `utils/notify.send_alert`，不得 `except: pass` 吞掉异常。
- **质量门禁**：任何改动须通过 `python engineering_debt_gate.py`、`python assert_data_validity.py`、`python industrial_grade_check.py`，以及 ruff 增量门禁（见 `ruff.toml`）。新增代码不得引入 F821 / 阻断级违规。
- **执行闭环**：信号/订单「生成」与「撮合落盘」必须成对存在；只生成不撮合 = 断链（期权对冲、再平衡都踩过此坑）。诊断「只生成不执行」类缺陷时，向上游看信号、向下游看消费者。
- **环境隔离**：研究代码在 `research/`，生产路径不得直接 `import research.*`。
- **Windows / GBK 编码坑**：print 货币符号用 `RMB/CNY` 而非 `¥`；Python 3.8 泛型下标用 `List[]/Dict[]` 而非 `list[]/dict[]`。
- 提交信息带 `[Ux]/[Gx]/[Px]` 标记（对应 ROADMAP 任务），便于 `sync_upgrade_status.py` 扫描门禁状态。

## 双轨自动开发
- GitHub `zhunbeibanjia`（本仓）= 主开发仓。
- CNB `yuppiez328/tradingview` = NPC 定时接管仓（`.cnb.yml` + `.cnb/settings.yml`，工作日 16:00 UTC）。
- 本仓的 `auto-dev-copilot.yml` 用 cron 把 roadmap issue 指派给 Copilot Agent，实现与 CNB 等价的定时自主接管。

## 接任务时的标准动作
1. 读 `cairn/ROADMAP.md` 顶部，挑一个 pending/in_progress 的 `[Ux]/[Gx]/[Px]` 任务；
2. 用 `tests/` 对应用例自测 + 跑上述质量门禁；
3. 单 PR 聚焦一件事，描述里标任务标记与验证结果；
4. 遇阻塞（缺 `xtquant`、需硬件、跨仓依赖）在 issue 留注记，改挑下一个可行任务，不要卡死。
