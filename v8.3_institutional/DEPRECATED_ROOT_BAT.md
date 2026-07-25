# 根目录旧版 bat 脚本废弃清单

## 废弃日期: 2026-07-24 (v8.5 最终交付)

## 废弃策略

以下根目录 bat 脚本已全部迁移至 `v8.3_institutional/v8.5_start.bat`,原脚本标记为废弃:

| 旧脚本路径 | 新脚本路径 | 说明 |
|-----------|-----------|------|
| `run_pre_market.bat` | `v8.5_start.bat` → 选项 1 | 盘前检查 |
| `run_daily_build_hedge.bat` | `v8.5_start.bat` → 选项 2 | 每日建仓+对冲 |
| `run_intraday_decision.bat` | `v8.5_start.bat` → 选项 3 | 日内决策 |
| `run_daily_report.bat` | `v8.5_start.bat` → 选项 4 | 生成日报 |
| `run_hn_daily.bat` | `v8.5_start.bat` → 选项 5 | HN 日报 |
| `run_start_live.bat` | `v8.5_start.bat` → 选项 6 | 启动监控 |
| `run_stop_live.bat` | `v8.5_start.bat` → 选项 7 | 停止监控 |
| `simulate_trading_plan.bat` | `v8.5_start.bat` → 选项 8 | 模拟交易 |
| `install_daily_hedge_task.bat` | `v8.5_start.bat` → 选项 9 | 安装定时任务 |
| `pack_cloud.bat` | `v8.5_start.bat` → 选项 0 | 打包云端 |

## 保留脚本

以下脚本不属于工作流,予以保留:
- `start_ollama_cpu.bat` - Ollama 本地模型启动器 (独立工具)

## 迁移指南

### 旧方式 (已废弃)
```batch
cd e:\各种PY程序\28-终极量化交易系统8.4
run_pre_market.bat
```

### 新方式 (推荐)
```batch
cd e:\各种PY程序\28-终极量化交易系统8.4\v8.3_institutional
v8.5_start.bat
# 选择菜单选项 1
```

### 一键完整工作流
```batch
cd e:\各种PY程序\28-ultimate-quant-system\v8.3_institutional
quick_start.bat
```

## 自动化任务更新

如果使用 Windows 任务计划程序,需要更新脚本路径:

```powershell
# 旧任务删除
schtasks /delete /tn "DailyHedgeUpdate" /f

# 新任务创建 (指向 v8.3_institutional)
schtasks /create /tn "DailyHedgeUpdate" /tr "\"C:\Program Files\Python38\python.exe\" \"e:\各种PY程序\28-终极量化交易系统8.4\v8.3_institutional\daily_hedge_update.py\"" /sc weekly /d MON,TUE,WED,THU,FRI /st 09:15 /ru "%USERNAME%" /np /f
```

## 影响范围

- **用户可见**: 所有手动运行的 bat 脚本需要切换到 `v8.3_institutional\v8.5_start.bat`
- **自动化任务**: 需要更新 Windows 任务计划程序中的脚本路径
- **文档引用**: `README.md` 和 `USER_GUIDE.md` 中的 bat 路径需要更新

## 后续计划

1. [x] 创建 `v8.5_start.bat` 统一入口
2. [x] 创建 `quick_start.bat` 一键工作流
3. [x] 创建 `run_tests.bat` 测试运行器
4. [ ] 更新 `README.md` 和 `USER_GUIDE.md` 中的路径引用
5. [ ] 根目录旧 bat 脚本物理删除 (建议保留 1 周作为过渡)
