# Register LLM Intraday Decision Scheduled Task
# Trading hours: 9:25-15:05 every 15 minutes
# Usage: Run as Administrator in PowerShell: .\register_intraday_task.ps1

$ErrorActionPreference = 'Stop'

$taskName = 'Quant_LLM_IntradayDecision'
$python = 'C:\Program Files\Python38\python.exe'
$script = 'E:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional\llm_intraday_decision_engine.py'
$logDir = 'E:\各种PY程序\28-终极量化交易系统7.1\logs'

# Remove old task if exists
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction -Execute $python -Argument "`"$script`" --mode live"
$trigger = New-ScheduledTaskTrigger -Once -At '09:25' -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Hours 5.5)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RunOnlyIfNetworkAvailable:$false
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Highest

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'LLM Intraday Decision Engine, runs every 15 minutes' | Out-Null

Write-Host "[OK] Task registered: $taskName"
Write-Host "    Script: $script"
Write-Host "    Trigger: Every 15 min (9:25-15:05)"
Write-Host "    Log: $logDir\intraday_decision.log"
