# 09-11 EOD 复跑执行包与运行时可验证性口径

> 状态：**执行包已交付，运行时终态待生产机回填**（不伪造终态）
> 关联：Issue #13 · `docs/eod_复跑执行包_20260911.md` · `docs/b4_mlops_enable_checklist_20260909.md` · `docs/sprint1_收尾判定材料_20260911.md`
> 证据分级：【R】仓库内可复现 / 【P】生产机运行时产物（`reports/` 不入版本库）

## 为什么需要这份文档

09-10 版 sprint1 材料 §附二留下 3 行**待终态项**，且 09-10 T3 曾以**盘前**口径判 FAIL（时点性）。若每轮都靠"时点猜 PASS/FAIL"，会反复出现：
① 盘前核对当终态结账 ② 环境差异（沙箱无 `.venv` / 无 reports）被误判成回归 ③ 假 PASS（缺产物却记绿）。

本文把三类复跑项的**判定字段、判据、常见的假绿/假红**固定下来。

## 一、三类复跑项与硬判据

### A. B4 首 EOD 应跳 warmup（三条硬证据）

| 证据 | 字段 | 判据 |
|---|---|---|
| A1 EOD 摘要 | `每日报告归档/<date>/eod_workflow_summary_<date>.json` → `phases.phase4_86_b4_shadow` | `skipped == true` 且 `reason == "B4 already enabled, warmup complete"` 且 `flag_invariant == "USE_MLOPS_PIPELINE=True (post-warmup)"` |
| A2 EOD 日志 | `logs/daily_eod_<YYYYMMDD>.log` | 含「B4 已启用 … 跳过」；**不含**「B4 shadow 每日预热 (USE_MLOPS_PIPELINE=False 不变式)」 |
| A3 warmup 状态文件 | `reports/shadow/b4_shadow_status.json` → `last_run` | **不以当日开头**（runner 确实没跑）＋ `consecutive_failures < 3` |

**为什么需要 A3**：只查 A1/A2 是文本证据，可被"日志写了但 runner 其实也跑了"绕过（runner 会因不变式每日 FAIL 并写状态文件）。A3 是**反证**：状态文件未被当日刷新 = runner 真没执行。三证互补，缺一即 MISSING。

【R】该契约已加 AST 护栏：`tests/unit/test_daily_workflow_unit.py::TestB4SkipWarmupContract`
- 4 用例：脚本存在 / 必须从 `system_config.json` 读 flag（禁硬编码）/ 三字段名稳定 / **跳过判定在 runner 调用之前**
- 负向实证：把跳过块移到 runner 之后 → 顺序用例立即红

### B. T3 七项无回归

```powershell
python -X utf8 scripts\t3_post_market_check.py --date <YYYY-MM-DD>   # 落盘 reports\operations\t3_check_<date>.md
```

同时必须跑 `scripts/gate_check_daily.py --date <date>`，否则 **② 天然 FAIL**：

| # | 判据要点 | 依赖产物 |
|---|---|---|
| ① | eod_guard + health_score 存在；数据源类 degradation = 0 | `reports/eod_guard_report_<date>.json`、`reports/health_score/…`、`reports/degradation_log.jsonl` |
| ② | health_score + gate 均存在 | **`reports/gate/gate_daily_<date>.json`（须先跑 gate_check_daily）** |
| ③ | degradation 全为 `config_manager` 类；kill_switch level 0 | 同上 |
| ④ | S12 NAV > 1.0 + 等权权重 + fail-fast 未触发 | `scripts/run_s12_shadow.py --status` |
| ⑤ | **mvsk ≥1 条** 且 **S6 ≥1 条且非全 skeleton**；qlib 当日 0 条 = 预期 | 三个 jsonl |
| ⑥ | 当日 fills 落盘，或"无 fills 且无 trade_plan" | `reports/fills/fills_<date>.jsonl` |
| ⑦ | D11 stable 7/7 + samples n/20；B4 warmup 连败 < 3 | `reports/evolution/phase_b_status.json`、`reports/shadow/b4_shadow_status.json` |

**⑦ 的双向含义**：B4 启用后 `warmup_days` 应**冻结不再 +1** —— 这是 B4 生效的第二个指纹，与 A3 互证。

### C. 三 shadow cron

| # | 判据 |
|---|---|
| C1 | `schtasks /Query /TN <Tag> /V` → Last Result = 0；Last Run 落在 16:30/35/50 ±10 分钟内（错过由 `StartWhenAvailable` 补跑，须在备注中如实标注"补跑"） |
| C2 | mvsk 当日 ≥1 / S6 当日 ≥1 且非全 skeleton / qlib 当日 0 = 预期 |
| C3 | S12 `--status` rc=0 且 NAV / 权重 / 再平衡 / fail-fast 可解析 |
| C4 | `logs/task_logs/<Tag>_*.log` 存在且 python exit code = 0 |

三线 = `S12_Shadow_EOD`(16:30，第三线) + `Shadow30Day_EOD`(16:35) + `GNN_S6_Paper_EOD`(16:50)。

## 二、常见假绿 / 假红（踩坑记录）

- `contains` **假红 · 沙箱环境差异**：`gate_check_daily.py` 硬编码 `.venv/bin/python`（Win 为 `.venv/Scripts/python.exe`）。沙箱无 `.venv` → 三件套 `exit_code=-2`、`all_ok=false`、streak 归 0/21。**这是环境差异，不是门禁回归**。生产机才有意义。
- `contains` **假绿 · 缺产物=通过**：只看总出口码会掩盖"门禁根本没跑"。故 `_eod_verify_911.py` 把缺失一律记 MISSING 并计入 INCOMPLETE，而不是跳过后算绿。
- `contains` **假红 · 时点性 FAIL**：09-10 T3 于 10:11（盘前）跑出 FAIL（eod_guard/health_score/gate/fills 缺失、mvsk/S6 当日 0），全部因当日 EOD 未运行。**T3 只应在当日 EOD 之后跑**；盘前结果不得当日结账。
- `contains` **结论源搞错**：T3 脚本自 2026-09-07 起打印路径 + `T3 PASS/FAIL`，**不再把核对表输出到 stdout**。想拿七项明细必须读 `reports/operations/t3_check_<date>.md`（`_eod_verify_911.py` 的 B 段即按该文件解析）。

- `contains` **工具版本差异冒充回归**：沙箱 `ruff 0.16.1` 全仓 40 errors / F401 等专项 1 处（`tests/test_trendcast_audit.py` 未用 `pytest`），09-10 生产机同专项报 0。`git stash` 屏蔽当轮改动后基线**完全一致** ⇒ 属 Py3.11+ruff0.16.1 与 `.venv`(Py3.14) 的版本差异，非本轮引入。**判据：先 stash 复测基线，再决定是否揽责**。

## 三、交付物

| 文件 | 性质 |
|---|---|
| `scripts/_eod_verify_911.py` | 【R】离线只读核对（A/B/C 三段）；只读、不触网、fail-closed、不冒充 T3 判定主体 |
| `tests/unit/test_daily_workflow_unit.py::TestB4SkipWarmupContract` | 【R】B4 跳过契约 AST 护栏（4 用例 + 负向实证） |
| `docs/eod_复跑执行包_20260911.md` | 执行步骤 + 判据 + 结果记录表 + 回滚触发 |
| `docs/sprint1_收尾判定材料_20260911.md` | Sprint 1 正式判定版（附二待终态行转表位） |
| `docs/b4_mlops_enable_checklist_20260909.md` §五 | 首个 EOD 验证项挂口径与工具 |

## 四、待回填项（生产机）

1. §6.1 三 cron 触发结果（09-10 + 09-11 两行）
2. §6.2 T3 七项（09-10 补跑终态 + 09-11 当日）
3. §6.3 B4 首 EOD 三证 + 09-14 连续 2 EOD 复验

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [EOD 运维经验沉淀：OpenBLAS 内存修复 + U9 端到端验证 + D7 断言增强（2026-08-07）](eod-operations-lessons-20260807.md) (相似度 9%)
- [Shadow 30 天验证 (W7.2.8 + W7.2.9)](shadow-30day-validation.md) (相似度 6%)
- [批次三 item 15：broker.enable 切换门禁 + 模拟/实盘对账任务](audit-p3-broker-gate-reconciliation-20260910.md) (相似度 5%)
- [EOD 计划任务静默失败 + 观察期样本补录（2026-08-19）](eod-scheduled-task-fix-20260819.md) (相似度 5%)
- [完成声明 ≠ 完成：三类状态失真与防复发（2026-08-29）](completion-claim-vs-actual-state-20260829.md) (相似度 5%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
