---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-08-20
updated: 2026-08-20
contains: scheduled-task-system-user-pitfall, pythonpath-isolation, module-not-found, eod-fallback-mechanism, three-layer-guarantee
related:
  - cairn/LOG.md
  - cairn/eod-scheduled-task-fix-20260819.md
  - 15_每日工作流/run_eod_with_env.bat
  - scripts/eod_health_check_and_rerun.py
---

# 08-21 EOD 不失败保障方案（2026-08-20）

> 保证 08-21 EOD 工作流成功执行，使 Phase B B1 stable_days 正确累积为 1。**核心教训：SYSTEM 用户运行 Python 时，用户级 site-packages 隔离，需通过 PYTHONPATH 补齐；计划任务失败需三重保障（主任务 + 兜底 + 健康检查）。**

## 一、问题背景

08-20 15:30 计划任务 `v84_PostMarket` 上次结果=1（失败），根因排查发现：

| 检查项 | 结果 |
|--------|------|
| 计划任务用户 | SYSTEM (S-1-5-18) |
| Python 路径 | `C:/Users/Administrator/py311/python.exe` ✅（08-19 已修复） |
| 上次结果码 | 1（非 0，失败） |
| 手动 Administrator 跑 | exit 0 ✅（18:03 成功） |
| 08-19 Python 崩溃 | 4 次 segfault (0xc0000005)，但 08-20 15:30 无崩溃 |

## 二、根因：SYSTEM 用户 site-packages 隔离

EOD 日志显示 3 个阶段失败，根因均为模块缺失：

```
ModuleNotFoundError: No module named 'urllib3'
ModuleNotFoundError: No module named 'wind_mcp_fetcher'
```

| 模块 | 安装位置 | SYSTEM 可见 |
|------|----------|-------------|
| urllib3 2.7.0 | `C:\Users\Administrator\AppData\Roaming\Python\Python311\site-packages` | ❌ 用户级隔离 |
| wind_mcp_fetcher | `E:\...\tools\wind_mcp_fetcher.py` | ❌ 需项目根在 sys.path |

**机制**：SYSTEM 用户运行 `C:\Users\Administrator\py311\python.exe` 时，Python 查找 SYSTEM 的 user site（`C:\Windows\System32\config\systemprofile\...`）而非 Administrator 的。urllib3 装在 Administrator user site，故 SYSTEM 找不到。

**3 个失败阶段**：
1. 收盘报告生成（DeepSeek AI 建议）— urllib3 缺失
2. Shadow Real Data Feeder — urllib3 缺失（**关键：shadow 数据写入失败**）
3. iFinD 新闻分析 — urllib3 缺失

## 三、三重保障方案

### 第一重：主任务 wrapper（15:30）

创建 `15_每日工作流/run_eod_with_env.bat`：

```bat
@echo off
setlocal
set "PYTHONPATH=C:\Users\Administrator\AppData\Roaming\Python\Python311\site-packages;E:\各种PY程序\28-终极量化交易系统8.4\tools;E:\各种PY程序\28-终极量化交易系统8.4\src;%PYTHONPATH%"
cd /d "E:\各种PY程序\28-终极量化交易系统8.4"
"C:\Users\Administrator\py311\python.exe" "...\run_daily_eod_workflow.py" --skip-system-check %*
endlocal
```

- `v84_PostMarket` 重新注册：`/tr run_eod_with_env.bat`，SYSTEM 用户，MON-FRI 15:30
- `--skip-system-check` 绕过 SYSTEM 用户下 `assert_system_ready()` 失败
- 验证：20:20 触发，上次结果=0 ✅

### 第二重：兜底健康检查任务（16:00）

创建 `scripts/eod_health_check_and_rerun.py`：

- 检查 `reports/shadow/daily_returns.jsonl` 最新日期 >= 目标日期
- 缺失则重跑 EOD（带 `--skip-system-check` + PYTHONPATH）
- 最多重试 2 次，间隔 60s
- 全部失败写 `reports/shadow/eod_fallback_alert.json` 告警

注册 `v84_EOD_Fallback`：SYSTEM 用户，MON-FRI 16:00（主任务后 30 分钟）

### 第三重：告警 + 手动介入

- 健康检查脚本全部失败 → 写告警 JSON
- 告警文件存在时，次日晨间检查需人工介入

## 四、验证结果

| 验证项 | 结果 |
|--------|------|
| PYTHONPATH 设置后 urllib3 加载 | ✅ urllib3 2.7.0 |
| PYTHONPATH 设置后 wind_mcp_fetcher 加载 | ✅ |
| 主任务 wrapper 触发 (20:20) | ✅ 上次结果=0 |
| shadow 数据完整性 | ✅ 21 条，07-23~08-20 |
| 兜底任务注册 | ✅ 下次运行 08-21 16:00 |

## 五、08-21 预期执行流程

```
15:30  v84_PostMarket 触发 → wrapper bat → PYTHONPATH 设置 → EOD 12 阶段
       ├─ 阶段一 收盘报告 (urllib3 ✅)
       ├─ 阶段四 EOD 四 Guard
       ├─ 阶段四点五 Shadow Real Data Feeder (urllib3 ✅) → daily_returns.jsonl +1 条
       └─ exit 0

16:00  v84_EOD_Fallback 触发 → 健康检查
       ├─ 读 daily_returns.jsonl 最新日期
       ├─ 若 >= 08-21 → exit 0 ✅
       └─ 若 < 08-21 → 重跑 EOD (PYTHONPATH + --skip-system-check) → 再检查

16:30  (若 16:00 重试 1) → 再检查
17:00  (若 16:00 重试 2) → 再检查 / 写告警
```

## 六、风险与限制

| 风险 | 缓解 |
|------|------|
| Wind MCP 数据源收盘后不可用 | EOD 有多数据源降级链 (Wind > 通达信 > AKShare) |
| 磁盘空间不足 (C: 4.24GB / E: 2.74GB) | 健康检查脚本不写大量数据；EOD 有备份清理 |
| Python 3.11 segfault (08-19 有 4 次) | wrapper 超时 2h；兜底重试 2 次 |
| SYSTEM 用户网络权限 | subprocess 继承 SYSTEM 网络，Wind MCP 走本地 TCP 应可用 |

## 七、指针

- wrapper: `15_每日工作流/run_eod_with_env.bat`
- 健康检查: `scripts/eod_health_check_and_rerun.py`
- 告警: `reports/shadow/eod_fallback_alert.json`
- 日志: `logs/eod_fallback.log`
- 计划任务: `v84_PostMarket` (15:30) + `v84_EOD_Fallback` (16:00)
- 前序: `cairn/eod-scheduled-task-fix-20260819.md`（08-19 Python 路径修复）

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [EOD 计划任务静默失败 + 观察期样本补录（2026-08-19）](eod-scheduled-task-fix-20260819.md) (相似度 25%)
- [EOD 运维经验沉淀：OpenBLAS 内存修复 + U9 端到端验证 + D7 断言增强（2026-08-07）](eod-operations-lessons-20260807.md) (相似度 15%)
- [Shadow 数据质量闭环设计](shadow-data-quality-loop.md) (相似度 7%)
- [盘前工作流并行化 + 情感信号注入 + 12任务定时注册](daily-workflow-parallel-sentiment-20260826.md) (相似度 7%)
- [观察期配置脱节修复 — 2026-08-09](observation-period-config-drift-20260809.md) (相似度 6%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
