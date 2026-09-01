# Register LLM Intraday Decision Scheduled Task
# Trading hours: 9:25-15:05 every 15 minutes, every trading day
# Usage: Run as Administrator in PowerShell: .\register_intraday_task.ps1

$ErrorActionPreference = 'Stop'

$taskName = 'Quant_LLM_IntradayDecision'
# P0-2 修复 (2026-09-01): 原硬编码 AppData Python311 路径不存在, 统一改用项目 .venv (3.14.4)
$python = 'E:\各种PY程序\28-终极量化交易系统8.4\.venv\Scripts\python.exe'
$script = 'E:\各种PY程序\28-终极量化交易系统8.4\v8.3_institutional\llm_intraday_decision_engine.py'
$logDir = 'E:\各种PY程序\28-终极量化交易系统8.4\logs'

# Remove old task if exists
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction -Execute $python -Argument "`"$script`" --mode live"

# 每日触发器 + 15 分钟重复（持续 5.5 小时）
$trigger = New-ScheduledTaskTrigger -Daily -At '09:25' -DaysInterval 1
$trigger.Repetition.Interval = "PT15M"
$trigger.Repetition.Duration = "PT5H30M"

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RunOnlyIfNetworkAvailable:$false
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Highest

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'LLM Intraday Decision Engine, runs every 15 minutes during trading hours' | Out-Null

Write-Host "[OK] Task registered: $taskName"
Write-Host "    Script: $script"
Write-Host "    Trigger: Daily at 09:25, every 15 min for 5.5 hours"
Write-Host "    Log: $logDir\intraday_decision.log"
