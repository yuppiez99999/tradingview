$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$TaskName = "v75_DailyWorkflow"
$TriggerTime = "07:00"
$Python = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
$Script = Join-Path $ScriptDir "daily_workflow.py"

Write-Host "Register v75_DailyWorkflow..."

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Task exists, removing..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
}

$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $TriggerTime
$action = New-ScheduledTaskAction -Execute $Python -Argument "`"$Script`" --date `"%DATE%`""
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Trigger $trigger -Action $action -Settings $settings -Principal $principal -Description "v7.5 daily workflow" -Force | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Write-Host "OK: $TaskName state=$($task.State) next=$($task.Triggers[0].StartBoundary)"
} else {
    Write-Host "ERROR: register failed"
    exit 1
}
