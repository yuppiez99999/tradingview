# 注册每日早7点早报工作流定时任务
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$BatchFile = Join-Path $ScriptDir "run_daily_morning.bat"
$LogDir = Join-Path $ProjectRoot "logs"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

$TaskName = "V84_DailyMorningWorkflow"
$TaskDesc = "v8.6.14 每日早7点早报工作流"

Write-Host "项目根目录: $ProjectRoot"
Write-Host "批处理文件: $BatchFile"
Write-Host "任务名称: $TaskName"
Write-Host "执行时间: 每日 07:00"

if (-not (Test-Path $BatchFile)) {
    Write-Host "[ERROR] 批处理文件不存在: $BatchFile" -ForegroundColor Red
    exit 1
}

$trigger = New-ScheduledTaskTrigger -Daily -At 07:00
$logFile = Join-Path $LogDir "morning_workflow.log"
$argStr = "/c `"$BatchFile`" >> `"$logFile`" 2>&1"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $argStr -WorkingDirectory $ProjectRoot

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew

try {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "删除旧任务..." -ForegroundColor Yellow
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    }
} catch {}

Write-Host "注册定时任务..." -ForegroundColor Cyan

try {
    Register-ScheduledTask -TaskName $TaskName -Trigger $trigger -Action $action -Settings $settings -Description $TaskDesc -Force -ErrorAction Stop | Out-Null
    Write-Host "[OK] 定时任务注册成功!" -ForegroundColor Green
    $task = Get-ScheduledTask -TaskName $TaskName
    $info = $task | Get-ScheduledTaskInfo
    Write-Host "状态: $($task.State)" -ForegroundColor Green
    Write-Host "下次运行: $($info.NextRunTime)" -ForegroundColor Green
} catch {
    Write-Host "[失败] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "当前量化相关任务:" -ForegroundColor Cyan
Get-ScheduledTask | Where-Object { $_.TaskName -like "*v8*" -or $_.TaskName -like "*morning*" -or $_.TaskName -like "*Daily*" } | ForEach-Object {
    $info = $_ | Get-ScheduledTaskInfo
    Write-Host "  - $($_.TaskName) | $($_.State) | 下次: $($info.NextRunTime)"
}

Write-Host ""
Write-Host "完成!" -ForegroundColor Green
