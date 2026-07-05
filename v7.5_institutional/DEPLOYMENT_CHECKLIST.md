# 部署确认清单 — v7.5 7/6 自动建仓

**部署日期**：2026-07-06  
**版本**：v7.5.1  
**状态**：已就绪  

---

## 1. 交易计划文件

| 文件 | 路径 | 状态 |
|------|------|------|
| JSON 计划 | `v7.5_institutional/trade_plans/trade_plan_20260706.json` | ✅ 已生成 |
| Markdown 计划 | `v7.5_institutional/trade_plans/trade_plan_20260706.md` | ✅ 已生成 |
| 执行窗口 | 09:30-10:30 | ✅ 已配置 |
| 首日订单 | 10 笔 | ✅ 已配置 |

**计划摘要**：
- 起始日：2026-07-06（周一）
- 建仓周期：5 个交易日（7/6 — 7/10）
- 首日金额：¥1,091,964
- 标的：510300 / 510500 / 512100 / 515180 / 600036 / 600900 / 601088 / 518880 / 588000 / 159915

---

## 2. 每日工作流验证

| 验证项 | 结果 | 说明 |
|--------|------|------|
| `daily_workflow.py --date 2026-07-06 --dry-run` | ✅ PASS | Phase 1-7 全部通过 |
| 交易计划加载 | ✅ PASS | `trade_plan_20260706.json` 读取成功 |
| Phase 信号生成 | ✅ PASS | 10 笔订单生成 |
| Qlib 信号注入 | ✅ PASS | 加仓 3 / 减仓 1 / 跳过 4 |
| 报告输出 | ✅ PASS | `每日报告归档/2026/07/06/v75_daily_workflow_20260706.md` |

---

## 3. Windows 任务计划

| 任务 | 时间 | 下次运行 | 状态 |
|------|------|----------|------|
| `v75_PreMarket` | 每交易日 07:00 | 2026-07-06 07:00 | ✅ 已注册 |
| `v75_PostMarket` | 每交易日 15:30 | 2026-07-06 15:30 | ✅ 已注册 |

**触发条件**：每周一至周五  
**执行脚本**：`run_all_modules.bat pre` / `run_all_modules.bat post`  
**工作目录**：`e:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional`

---

## 4. 文档更新

| 文件 | 更新内容 | 状态 |
|------|----------|------|
| `README.md` | 7/6 自动建仓计划、首日执行摘要、建仓标的表、自动任务说明 | ✅ 已更新并推送 |
| `CHANGELOG.md` | v7.5.1 条目：7/6 自动建仓与全自动运行 | ✅ 已更新并推送 |
| `trade_plan_20260706.md` | 阅读版交易计划 | ✅ 已生成 |

---

## 5. GitHub 同步

| 仓库 | 分支 | 提交 | 状态 |
|------|------|------|------|
| https://github.com/yuppiez99999/zhunbeibanjia.git | main | `89e3504` | ✅ 已推送 |
| https://github.com/yuppiez99999/zhunbeibanjia.git | main | `f63ea10` | ✅ 已推送 |

---

## 6. 实盘预期（基于蒙特卡洛模拟）

| 指标 | 数值 |
|------|------|
| 年化收益 P50 | 20.2% |
| 年化波动 P50 | 14.1% |
| Sharpe P50 | 1.294 |
| 最大回撤 P5 | -17.8% |
| 最大回撤 P50 | -10.1% |

---

## 7. 下一步操作

### 7.1 盘前检查（每日 06:30）
```powershell
# 1. 查看任务状态
schtasks.exe /query /tn 'v75_PreMarket' /fo LIST

# 2. 查看前一交易日报告
notepad "e:\各种PY程序\28-终极量化交易系统7.1\每日报告归档\2026\07\05\v75_daily_workflow_20260705.md"
```

### 7.2 盘后确认（每日 15:45）
```powershell
# 1. 查看当日执行报告
notepad "e:\各种PY程序\28-终极量化交易系统7.1\每日报告归档\2026\07\06\v75_daily_workflow_20260706.md"

# 2. 查看黑天鹅测试结果
notepad "e:\各种PY程序\28-终极量化交易系统7.1\每日报告归档\2026\07\06\v75_black_swan_20260706.md"
```

### 7.3 手动触发（如需立即执行）
```powershell
# 立即运行盘前批次
Start-ScheduledTask -TaskName 'v75_PreMarket'

# 立即运行盘后批次
Start-ScheduledTask -TaskName 'v75_PostMarket'
```

### 7.4 任务管理
```powershell
# 禁用任务
Disable-ScheduledTask -TaskName 'v75_PreMarket'
Disable-ScheduledTask -TaskName 'v75_PostMarket'

# 启用任务
Enable-ScheduledTask -TaskName 'v75_PreMarket'
Enable-ScheduledTask -TaskName 'v75_PostMarket'

# 卸载任务
schtasks.exe /delete /tn 'v75_PreMarket' /f
schtasks.exe /delete /tn 'v75_PostMarket' /f
```

---

## 8. 联系与支持

- **项目主页**：https://github.com/yuppiez99999/zhunbeibanjia.git
- **交易计划目录**：`v7.5_institutional/trade_plans/`
- **每日报告目录**：`每日报告归档/YYYY/MM/DD/`

---

**部署确认**：所有步骤已完成并通过验证，系统将于 2026-07-06 07:00 开始自动运行。
