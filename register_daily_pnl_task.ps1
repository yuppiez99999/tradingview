# DEPRECATED: 已被 scripts\register_all_tasks_unified.ps1 替代，请勿执行
# 迁移日期: 2026-07-30
# 原因: 收盘 PnL 报告已由 v84_DailyPnlReport（16:00 调用 generate_daily_report.py）承担
# 原脚本还违规使用 PowerShell Register-ScheduledTask cmdlet（约束要求用 COM Schedule.Service）
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At '15:30'
$action = New-ScheduledTaskAction -Execute 'E:\各种PY程序\28-终极量化交易系统8.4\run_daily_report.bat'
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName 'v75_DailyPnlReport' -Trigger $trigger -Action $action -Settings $settings -Description '收盘盈亏报告自动生成 (15:30)' -Force