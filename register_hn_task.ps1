# ============================================================
# Hacker News 每日热帖讨论榜 - Windows 任务计划程序注册脚本
# ============================================================
# 功能:
#   1. 注册 "HN_DailyTop" 任务, 每个交易日 07:00 自动执行
#   2. 周末自动跳过
#   3. 任务以当前用户身份运行, 不需要登录即可触发
#
# 用法 (以管理员身份运行 PowerShell):
#   cd e:\各种PY程序\28-终极量化交易系统7.1
#   .\register_hn_task.ps1              # 注册任务
#   .\register_hn_task.ps1 -Uninstall   # 卸载任务
#   .\register_hn_task.ps1 -Test        # 立即测试执行一次
# ============================================================

param(
    [switch]$Uninstall,
    [switch]$Test,
    [string]$TaskName = "HN_DailyTop",
    [string]$TriggerTime = "07:00"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BatchFile = Join-Path $ScriptDir "run_hn_daily.bat"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  HN Daily Top - Task Scheduler Registration" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Task Name: $TaskName"
Write-Host "Trigger:   Daily @ $TriggerTime (Mon-Fri)"
Write-Host "Script:    $BatchFile"
Write-Host ""

# 卸载模式
if ($Uninstall) {
    Write-Host "[Uninstall] 删除任务 $TaskName ..." -ForegroundColor Yellow
    try {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
        Write-Host "[OK] 任务已删除" -ForegroundColor Green
    } catch {
        Write-Host "[SKIP] 任务不存在, 无需删除" -ForegroundColor Yellow
    }
    exit 0
}

# 检查批处理文件
if (-not (Test-Path $BatchFile)) {
    Write-Host "[ERROR] 找不到批处理文件: $BatchFile" -ForegroundColor Red
    exit 1
}

# 测试模式
if ($Test) {
    Write-Host "[Test] 立即执行一次测试..." -ForegroundColor Yellow
    Start-Process -FilePath $BatchFile -Wait -NoNewWindow
    exit 0
}

# 检查是否已存在
$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Write-Host "[WARN] 任务已存在, 将先删除再重新创建" -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# 创建任务
Write-Host "[1/4] 创建触发器 (每周一至五 $TriggerTime)..." -ForegroundColor Green
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $TriggerTime

Write-Host "[2/4] 创建动作 (启动批处理)..." -ForegroundColor Green
$action = New-ScheduledTaskAction -Execute $BatchFile -WorkingDirectory $ScriptDir

Write-Host "[3/4] 创建设置..." -ForegroundColor Green
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

Write-Host "[4/4] 注册任务..." -ForegroundColor Green
Register-ScheduledTask `
    -TaskName $TaskName `
    -Trigger $trigger `
    -Action $action `
    -Settings $settings `
    -Principal $principal `
    -Description "Hacker News 每日热帖讨论榜 - 每个交易日 07:00 自动抓取并生成报告" `
    -Force | Out-Null

# 验证
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "  [OK] 任务注册成功!" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "TaskName:    $($task.TaskName)"
    Write-Host "State:       $($task.State)"
    Write-Host "NextRunTime: $($task.Triggers[0].StartBoundary)"
    Write-Host ""
    Write-Host "管理命令:" -ForegroundColor Cyan
    Write-Host "  查看:    Get-ScheduledTask -TaskName '$TaskName'"
    Write-Host "  立即运行: Start-ScheduledTask -TaskName '$TaskName'"
    Write-Host "  禁用:    Disable-ScheduledTask -TaskName '$TaskName'"
    Write-Host "  启用:    Enable-ScheduledTask -TaskName '$TaskName'"
    Write-Host "  卸载:    .\register_hn_task.ps1 -Uninstall"
    Write-Host "  测试:    .\register_hn_task.ps1 -Test"
} else {
    Write-Host "[ERROR] 任务注册失败" -ForegroundColor Red
    exit 1
}
