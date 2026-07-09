$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At '15:30'
$action = New-ScheduledTaskAction -Execute 'E:\各种PY程序\28-终极量化交易系统7.1\run_daily_report.bat'
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName 'v75_DailyPnlReport' -Trigger $trigger -Action $action -Settings $settings -Description '收盘盈亏报告自动生成 (15:30)' -Force