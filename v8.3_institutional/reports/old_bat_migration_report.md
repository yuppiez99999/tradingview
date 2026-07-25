# 根目录旧BAT脚本迁移报告

**生成时间**: 2026-07-24  
**任务编号**: P2 - 旧脚本清理  
**状态**: 待执行物理删除

## 1. 根目录旧BAT脚本清单 (共10个)

| # | 脚本名称 | 大小 | 最后修改时间 | 迁移目标路径 |
|---|---------|------|-------------|-------------|
| 1 | `install_daily_hedge_task.bat` | 1,133 bytes | 2026-07-10 | `v8.3_institutional/scripts/` |
| 2 | `pack_cloud.bat` | 1,301 bytes | 2026-07-09 | `v8.3_institutional/scripts/` |
| 3 | `run_daily_build_hedge.bat` | 1,650 bytes | 2026-07-14 | `v8.3_institutional/scripts/` |
| 4 | `run_daily_report.bat` | 166 bytes | 2026-07-10 | `v8.3_institutional/scripts/` |
| 5 | `run_hn_daily.bat` | 101 bytes | 2026-07-09 | `v8.3_institutional/scripts/` |
| 6 | `run_intraday_decision.bat` | 163 bytes | 2026-07-21 | `v8.3_institutional/scripts/` |
| 7 | `run_pre_market.bat` | 620 bytes | 2026-07-10 | `v8.3_institutional/scripts/` |
| 8 | `run_start_live.bat` | 1,043 bytes | 2026-07-11 | `v8.3_institutional/scripts/` |
| 9 | `run_stop_live.bat` | 446 bytes | 2026-07-11 | `v8.3_institutional/scripts/` |
| 10 | `simulate_trading_plan.bat` | 5,737 bytes | 2026-07-04 | `v8.3_institutional/scripts/` |

**额外脚本** (非业务脚本，保留在根目录):
- `start_ollama_cpu.bat` (146 bytes) - AI工具脚本，不属于量化交易系统

## 2. 迁移策略

### 2.1 已迁移到v8.3_institutional/scripts/的脚本

根据git status显示，以下脚本已在`_archive_old_scripts`目录中:
- `build_and_run_all.bat`
- `check_system.bat`
- `daily_build_and_hedge.bat`
- `daily_hedge_update.bat`
- `daily_trade_executor.bat`
- `generate_daily_report.bat`
- `hedge_execution_orders.bat`
- `hedge_quantity_calculator.bat`
- `hn_daily_report.bat`
- `institutional_pipeline_runner.bat`
- `lgb_enhanced_trainer.bat`
- `lgb_tscv_trainer.bat`
- `quick_start.bat`
- `run_all_modules.bat`
- `run_daily.bat`
- `setup_virtual_env.bat`
- `start_system.bat`
- `test_all_modules.bat`
- `test_core_modules.bat`
- `test_quick.bat`

### 2.2 待迁移的脚本 (本次任务)

以下10个脚本需要从根目录迁移到`v8.3_institutional/scripts/`:

1. `install_daily_hedge_task.bat` → `v8.3_institutional/scripts/install_daily_hedge_task.bat`
2. `pack_cloud.bat` → `v8.3_institutional/scripts/pack_cloud.bat`
3. `run_daily_build_hedge.bat` → `v8.3_institutional/scripts/run_daily_build_hedge.bat`
4. `run_daily_report.bat` → `v8.3_institutional/scripts/run_daily_report.bat`
5. `run_hn_daily.bat` → `v8.3_institutional/scripts/run_hn_daily.bat`
6. `run_intraday_decision.bat` → `v8.3_institutional/scripts/run_intraday_decision.bat`
7. `run_pre_market.bat` → `v8.3_institutional/scripts/run_pre_market.bat`
8. `run_start_live.bat` → `v8.3_institutional/scripts/run_start_live.bat`
9. `run_stop_live.bat` → `v8.3_institutional/scripts/run_stop_live.bat`
10. `simulate_trading_plan.bat` → `v8.3_institutional/scripts/simulate_trading_plan.bat`

## 3. 文档更新计划

### 3.1 README.md 需要更新的引用

当前README.md第355-356行引用了旧路径:
```
│   ├── run_all_modules.bat            # 一键启动全部模块
│   ├── run_daily.bat                  # 每日运行入口
```

需要更新为:
```
│   scripts/
│   ├── run_all_modules.bat            # 一键启动全部模块
│   ├── run_daily.bat                  # 每日运行入口
│   ├── install_daily_hedge_task.bat   # 安装每日对冲任务
│   ├── pack_cloud.bat                 # 云打包脚本
│   ├── run_daily_build_hedge.bat      # 每日构建对冲脚本
│   ├── run_daily_report.bat           # 每日报告生成脚本
│   ├── run_hn_daily.bat               # HN每日运行脚本
│   ├── run_intraday_decision.bat      # 日内决策脚本
│   ├── run_pre_market.bat             # 盘前准备脚本
│   ├── run_start_live.bat             # 启动实盘脚本
│   ├── run_stop_live.bat              # 停止实盘脚本
│   └── simulate_trading_plan.bat      # 模拟交易计划脚本
```

### 3.2 USER_GUIDE.md 检查

USER_GUIDE.md中未找到对旧bat脚本的直接引用，无需更新。

## 4. 执行时间表

### 4.1 过渡期 (2026-07-24 至 2026-07-31)

- [x] 创建本迁移报告
- [x] 更新README.md中的路径引用
- [ ] 将10个旧脚本复制到`v8.3_institutional/scripts/`
- [ ] 验证所有脚本在新位置可正常运行
- [ ] 通知团队成员路径变更

### 4.2 物理删除 (2026-08-01及之后)

过渡期结束后，执行物理删除:
```bash
cd "e:\各种PY程序\28-终极量化交易系统8.4"
del install_daily_hedge_task.bat
del pack_cloud.bat
del run_daily_build_hedge.bat
del run_daily_report.bat
del run_hn_daily.bat
del run_intraday_decision.bat
del run_pre_market.bat
del run_start_live.bat
del run_stop_live.bat
del simulate_trading_plan.bat
```

## 5. 风险评估

### 5.1 潜在风险

| 风险项 | 影响等级 | 缓解措施 |
|--------|---------|---------|
| 脚本路径变更导致用户混淆 | 中 | 提前通知+保留符号链接(可选) |
| 外部系统依赖旧路径 | 低 | 检查是否有CI/CD依赖根目录脚本 |
| 脚本内部硬编码路径 | 高 | 逐一检查10个脚本内容，确保使用相对路径 |

### 5.2 回滚方案

如需回滚，可从`_archive_old_scripts`目录恢复脚本到根目录。

## 6. 验证清单

物理删除前必须完成:
- [ ] 所有10个脚本已复制到`v8.3_institutional/scripts/`
- [ ] 每个脚本在新位置测试通过
- [ ] README.md已更新
- [ ] USER_GUIDE.md已检查(无引用)
- [ ] 团队成员已通知
- [ ] CI/CD系统已更新(如有)

## 7. 后续任务

- [ ] 检查脚本内部是否有硬编码的根目录路径
- [ ] 创建符号链接(可选，用于向后兼容)
- [ ] 更新CHANGELOG.md记录此次变更

---

**报告生成者**: Agnes-2.0-Flash  
**审核状态**: 待审核  
**下次审查日期**: 2026-07-31
