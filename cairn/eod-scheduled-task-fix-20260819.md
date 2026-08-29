---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-19
updated: 2026-08-19
contains: scheduled-task-pitfall, python-path-hardcode, eod-silent-failure, wind-mcp-backfill, observation-sample-gap, ocr-secrets-config
related:
  - cairn/LOG.md
  - cairn/test-health-20260819.md
  - docs/0824前最优方案_20260814.md
  - 15_每日工作流/run_daily_eod_workflow.py
---

# EOD 计划任务静默失败 + 观察期样本补录（2026-08-19）

> 记录 EOD 计划任务因 Python 路径硬编码不存在导致静默失败的根因与修复，以及观察期样本缺口用 Wind MCP 补录的方法。**核心教训：计划任务的 Python 路径必须用 `where.exe python` 验证存在性，不能硬编码可能不存在的路径。**

## 一、问题现象

08-19 EOD 数据缺失，`daily_returns.jsonl` 最新记录停留在 08-18。排查发现：

| 检查项 | 结果 |
|--------|------|
| `daily_returns.jsonl` 08-19 记录 | ❌ 缺失 |
| 15:00 收盘后 EOD 运行 | ❌ 未运行 |
| 08-19 EOD 日志 | ⚠️ 有 9 次运行（09:20~14:31，均为收盘前手动触发） |
| 阶段四点五（收益写入） | ❌ 每次都失败（"阶段四点五未成功写入数据"） |
| 计划任务 `v84_PostMarket` 上次结果 | **-2147024894 = 0x80070002 = ERROR_FILE_NOT_FOUND** |

## 二、根因：Python 路径硬编码不存在

计划任务 `v84_PostMarket` 配置的 Python 路径：
```
C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe  ← 不存在
```

实际存在的 Python 3.11 路径：
```
C:\Users\Administrator\py311\python.exe  ← 存在（Python 3.11.9）
E:\Python38\python.exe                   ← 存在（Python 3.11.9，目录名历史遗留）
```

**受影响的 3 个任务**：

| 任务 | 用途 | 排期 | 修复前路径 | 修复后路径 |
|------|------|------|-----------|-----------|
| `v84_PostMarket` | EOD 工作流 | 周一~五 15:30 | ❌ 不存在 | ✅ `C:\Users\Administrator\py311\python.exe` |
| `v84_DailyPnlReport` | PnL 报告 | 每日 16:00 | ❌ 不存在 | ✅ 同上 |
| `v84_ShadowAdmissionDaily` | 观察期 DSR | 每日 16:15 | ❌ 不存在 | ✅ 同上 |

**为何之前有 EOD 日志**：09:20~14:31 的运行是手动触发的（非计划任务），且在收盘前跑，行情未收盘 → feeder 无法获取当日收益 → 阶段四点五未写入 → 降级重建历史状态。计划任务 15:30 的自动运行因路径不存在启动即失败，**无日志产出**（静默失败）。

## 三、修复方法

```powershell
# 1. 导出任务 XML
schtasks /query /tn "v84_PostMarket" /xml > "$env:TEMP\v84_postmarket.xml"

# 2. 替换 Python 路径
$content = Get-Content "$env:TEMP\v84_postmarket.xml" -Raw
$content = $content -replace [regex]::Escape("C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"), "C:\Users\Administrator\py311\python.exe"
Set-Content "$env:TEMP\v84_postmarket.xml" -Value $content -Encoding Unicode

# 3. 重新注册
schtasks /delete /tn "v84_PostMarket" /f
schtasks /create /tn "v84_PostMarket" /xml "$env:TEMP\v84_postmarket.xml"
```

**验证**：`schtasks /query /tn "v84_PostMarket" /fo LIST /v` 确认"要运行的任务"路径存在 + "下次运行时间"正确。

## 四、观察期样本补录

修复 EOD 后发现观察期样本缺口：

| 指标 | 补录前 | 补录后 |
|------|--------|--------|
| 缺失日期 | 07-23、07-24（观察期前 2 个交易日） | ✅ 已补录 |
| 样本数 | 18/20 | **20/20 (OK)** |
| 观察期天数 | 18/21 (85.7%) | **20/21 (95.2%)** |
| 预计达标 | 08-24 | **08-20**（明天 1 天即满 21 天） |

**补录方法**（Wind MCP 回填历史日期）：
```bash
python -X utf8 scripts/shadow_real_data_feeder.py --date 2026-07-23  # -0.1206%
python -X utf8 scripts/shadow_real_data_feeder.py --date 2026-07-24  # -1.0584%
```

补录后需：
1. **排序去重** `daily_returns.jsonl`（追加记录在末尾，日期乱序）
2. **重建状态** `python -X utf8 scripts/rebuild_shadow_state_from_returns.py`
3. **刷新进度** `python -X utf8 scripts/observation_tracker.py`

## 五、教训（contains: scheduled-task-pitfall, python-path-hardcode）

1. **计划任务的 Python 路径必须验证存在性**：用 `Test-Path` 或 `where.exe python` 确认，不能假设路径。SYSTEM 用户运行的计划任务不继承用户 PATH，必须用绝对路径。
2. **计划任务静默失败无日志**：`ERROR_FILE_NOT_FOUND` 在任务计划程序里只显示一个错误码，不产出应用日志。需定期检查 `schtasks /query` 的"上次结果"字段（0 = 成功）。
3. **EOD 必须在收盘后运行**：收盘前运行时行情未定型，feeder 无法获取当日收益 → 阶段四点五失败 → 降级重建。计划任务时间应设为 15:30（收盘后 30 分钟）而非更早。
4. **观察期样本补录用 feeder --date**：`shadow_real_data_feeder.py` 支持任意历史日期，Wind MCP 数据源可回填。补录后必须排序去重 + 重建状态 + 刷新进度三步。
5. **批量检查所有任务的 Python 路径**：一个任务路径坏可能是迁移遗留，多个任务同路径坏是系统性问题。用脚本批量 `Test-Path` 检查所有任务的 `<Command>` 路径。

## 六、关联文件

| 文件 | 职责 |
|------|------|
| `15_每日工作流/run_daily_eod_workflow.py` | EOD 工作流主入口（阶段四点五 feeder + 阶段四点五B 状态同步） |
| `scripts/shadow_real_data_feeder.py` | Shadow 真实数据注入（支持 `--date` 补录历史） |
| `scripts/rebuild_shadow_state_from_returns.py` | 从 daily_returns.jsonl 重建 shadow_state.json |
| `scripts/observation_tracker.py` | 观察期进度快照生成 |
| `reports/shadow/daily_returns.jsonl` | Shadow 日报收益（20 条，07-23~08-19） |
| `reports/evolution/observation_progress.json` | 观察期进度（20/21 天，20/20 样本） |
| `15_每日工作流/setup_eod_scheduled_task.ps1` | EOD 计划任务注册脚本（需更新 Python 路径） |

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [08-21 EOD 不失败保障方案（2026-08-20）](eod-fallback-guarantee-20260820.md) (相似度 25%)
- [EOD 运维经验沉淀：OpenBLAS 内存修复 + U9 端到端验证 + D7 断言增强（2026-08-07）](eod-operations-lessons-20260807.md) (相似度 18%)
- [盘前工作流并行化 + 情感信号注入 + 12任务定时注册](daily-workflow-parallel-sentiment-20260826.md) (相似度 14%)
- [观察期配置脱节修复 — 2026-08-09](observation-period-config-drift-20260809.md) (相似度 12%)
- [Shadow 数据质量闭环设计](shadow-data-quality-loop.md) (相似度 12%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
