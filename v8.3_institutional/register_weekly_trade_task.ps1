# ============================================================
# v7.5 本周自动交易计划 - Windows 任务计划程序注册脚本
# ============================================================
# 功能:
#   1. 注册 "v75_WeeklyTrade" 任务, 每个交易日 09:30 自动执行本周交易计划
#      (加载当日计划 + 模拟盘执行 + 生成报告)
#   2. 注册 "v75_WeeklyTrade_PM" 任务, 每个交易日 14:00 自动执行下午批次
#   3. 周末自动跳过 (脚本内已判断)
#   4. 失败自动重试 3 次, 间隔 5 分钟
#
# 用法 (以管理员身份运行 PowerShell):
#   cd e:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional
#   .\register_weekly_trade_task.ps1                # 注册任务
#   .\register_weekly_trade_task.ps1 -Uninstall     # 卸载任务
#   .\register_weekly_trade_task.ps1 -Test          # 立即测试
# ============================================================

param(
    [switch]$Uninstall,
    [switch]$Test,
    [string]$AMTaskName = "v75_WeeklyTrade",
    [string]$PMTaskName = "v75_WeeklyTrade_PM",
    [string]$NightTaskName = "v75_WeeklyTrade_Night",
    [string]$AMTime = "09:30",
    [string]$PMTime = "14:00",
    [string]$NightTime = "21:00"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = "C:\Program Files\Python38\python.exe"
$ExecutorScript = Join-Path $ScriptDir "weekly_trade_executor.py"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  v7.5 Weekly Trade Plan - Task Scheduler Registration" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "AM Task:   $AMTaskName  @ $AMTime  (Mon-Fri)"
Write-Host "PM Task:   $PMTaskName @ $PMTime (Mon-Fri)"
Write-Host "Night Task: $NightTaskName @ $NightTime (Mon-Thu, 期货夜盘)"
Write-Host "Executor:  $ExecutorScript"
Write-Host ""

# ============================================================
# 卸载模式
# ============================================================
if ($Uninstall) {
    Write-Host "[Uninstall] 删除本周交易任务..." -ForegroundColor Yellow
    foreach ($name in @($AMTaskName, $PMTaskName, $NightTaskName)) {
        try {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction Stop
            Write-Host "  [OK] 已删除: $name" -ForegroundColor Green
        } catch {
            Write-Host "  [SKIP] 不存在: $name" -ForegroundColor Yellow
        }
    }
    exit 0
}

# ============================================================
# 检查执行脚本
# ============================================================
if (-not (Test-Path $ExecutorScript)) {
    Write-Host "[ERROR] 找不到执行脚本: $ExecutorScript" -ForegroundColor Red
    exit 1
}

# ============================================================
# 测试模式
# ============================================================
if ($Test) {
    Write-Host "[Test] 立即执行测试..." -ForegroundColor Yellow
    & $Python $ExecutorScript --dry-run
    exit 0
}

# ============================================================
# 注册单个任务
# ============================================================
function Register-WeeklyTradeTask {
    param(
        [string]$TaskName,
        [string]$TriggerTime,
        [string]$Description,
        [string]$Session
    )

    Write-Host ""
    Write-Host "------ 注册 $TaskName ------" -ForegroundColor Cyan

    $existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existingTask) {
        Write-Host "[WARN] 任务已存在, 将先删除再重新创建" -ForegroundColor Yellow
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }

    if ($Session -eq "night") {
        Write-Host "[1/4] 创建触发器 (每周一至四 $TriggerTime, 期货夜盘)..." -ForegroundColor Green
        $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday -At $TriggerTime
    } else {
        Write-Host "[1/4] 创建触发器 (每周一至五 $TriggerTime)..." -ForegroundColor Green
        $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $TriggerTime
    }

    Write-Host "[2/4] 创建动作..." -ForegroundColor Green
    $action = New-ScheduledTaskAction -Execute $Python -Argument "`"$ExecutorScript`" --session $Session" -WorkingDirectory $ScriptDir

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
        -Description $Description `
        -Force | Out-Null

    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Write-Host "[OK] $TaskName 注册成功 (session=$Session)" -ForegroundColor Green
        return $true
    } else {
        Write-Host "[ERROR] $TaskName registration failed" -ForegroundColor Red
        return $false
    }
}

# ============================================================
# 注册三个任务
# ============================================================
$amOK = Register-WeeklyTradeTask -TaskName $AMTaskName -TriggerTime $AMTime `
    -Description "v7.5 本周交易计划 AM: 加载当日计划 + 模拟盘执行上午批次 (09:30)" `
    -Session "morning"

$pmOK = Register-WeeklyTradeTask -TaskName $PMTaskName -TriggerTime $PMTime `
    -Description "v7.5 本周交易计划 PM: 模拟盘执行下午批次 + 生成报告 (14:00)" `
    -Session "afternoon"

$nightOK = Register-WeeklyTradeTask -TaskName $NightTaskName -TriggerTime $NightTime `
    -Description "v7.5 本周交易计划 Night: 期货夜盘对冲执行 (21:00, T+1规则)" `
    -Session "night"

# ============================================================
# 汇总
# ============================================================
Write-Host ""
Write-Host "============================================================" -ForegroundColor $(if ($amOK -and $pmOK -and $nightOK) { "Green" } else { "Yellow" })
if ($amOK -and $pmOK -and $nightOK) {
    Write-Host "  [OK] 所有任务注册成功!" -ForegroundColor Green
} else {
    Write-Host "  [WARN] 部分任务注册失败, 请检查上方日志" -ForegroundColor Yellow
}
Write-Host "============================================================" -ForegroundColor $(if ($amOK -and $pmOK -and $nightOK) { "Green" } else { "Yellow" })

Write-Host ""
Write-Host "管理命令:" -ForegroundColor Cyan
Write-Host "  查看所有:    Get-ScheduledTask -TaskName 'v75_Weekly*'"
Write-Host "  立即运行AM:  Start-ScheduledTask -TaskName '$AMTaskName'"
Write-Host "  立即运行PM:  Start-ScheduledTask -TaskName '$PMTaskName'"
Write-Host "  立即运行夜盘: Start-ScheduledTask -TaskName '$NightTaskName'"
Write-Host "  禁用AM:      Disable-ScheduledTask -TaskName '$AMTaskName'"
Write-Host "  禁用PM:      Disable-ScheduledTask -TaskName '$PMTaskName'"
Write-Host "  禁用夜盘:    Disable-ScheduledTask -TaskName '$NightTaskName'"
Write-Host "  卸载全部:    .\register_weekly_trade_task.ps1 -Uninstall"
Write-Host "  测试:        .\register_weekly_trade_task.ps1 -Test"
Write-Host ""
Write-Host "执行器命令:" -ForegroundColor Cyan
Write-Host "  查看本周概览: python weekly_trade_executor.py --week"
Write-Host "  干跑模式:     python weekly_trade_executor.py --dry-run"
Write-Host "  指定日期:     python weekly_trade_executor.py --date 2026-07-21"
Write-Host "  夜盘模式:     python weekly_trade_executor.py --session night --dry-run"

if (-not ($amOK -and $pmOK -and $nightOK)) { exit 1 }
