# ============================================================
# v7.5 全核心模块 - Windows 任务计划程序注册脚本
# ============================================================
# 功能:
#   1. 注册 "v75_PreMarket"  任务, 每个交易日 07:00 自动执行盘前批次
#      (Wind 校准 + v5 优化 + 每日交易工作流)
#   2. 注册 "v75_PostMarket" 任务, 每个交易日 15:30 自动执行盘后批次
#      (黑天鹅压力测试 + 汇总报告)
#   3. 周末自动跳过 (脚本内已判断)
#   4. 失败自动重试 3 次, 间隔 5 分钟
#
# 用法 (以管理员身份运行 PowerShell):
#   cd e:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional
#   .\register_all_tasks.ps1                # 注册两个任务
#   .\register_all_tasks.ps1 -Uninstall     # 卸载两个任务
#   .\register_all_tasks.ps1 -Test pre      # 立即测试盘前批次
#   .\register_all_tasks.ps1 -Test post     # 立即测试盘后批次
#   .\register_all_tasks.ps1 -Test all      # 立即测试全部
# ============================================================

param(
    [switch]$Uninstall,
    [string]$Test,
    [string]$PreTaskName = "v75_PreMarket",
    [string]$PostTaskName = "v75_PostMarket",
    [string]$PreTime = "07:00",
    [string]$PostTime = "15:30"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BatchFile = Join-Path $ScriptDir "run_all_modules.bat"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  v7.5 All Core Modules - Task Scheduler Registration" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "PreMarket  Task: $PreTaskName  @ $PreTime  (Mon-Fri)"
Write-Host "PostMarket Task: $PostTaskName @ $PostTime (Mon-Fri)"
Write-Host "Script:     $BatchFile"
Write-Host ""

# ============================================================
# 卸载模式
# ============================================================
if ($Uninstall) {
    Write-Host "[Uninstall] 删除所有 v7.5 任务..." -ForegroundColor Yellow
    foreach ($name in @($PreTaskName, $PostTaskName)) {
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
# 检查批处理文件
# ============================================================
if (-not (Test-Path $BatchFile)) {
    Write-Host "[ERROR] 找不到批处理文件: $BatchFile" -ForegroundColor Red
    exit 1
}

# ============================================================
# 测试模式
# ============================================================
if ($Test) {
    Write-Host "[Test] 立即执行测试: phase=$Test..." -ForegroundColor Yellow
    $phase = if ($Test -in @("pre","post","all")) { $Test } else { "all" }
    Start-Process -FilePath $BatchFile -ArgumentList $phase -Wait -NoNewWindow
    exit 0
}

# ============================================================
# 注册单个任务
# ============================================================
function Register-V75Task {
    param(
        [string]$TaskName,
        [string]$TriggerTime,
        [string]$Phase,
        [string]$Description
    )

    Write-Host ""
    Write-Host "------ 注册 $TaskName ------" -ForegroundColor Cyan

    # 检查是否已存在
    $existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existingTask) {
        Write-Host "[WARN] 任务已存在, 将先删除再重新创建" -ForegroundColor Yellow
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }

    # 触发器: 每周一至五
    Write-Host "[1/4] 创建触发器 (每周一至五 $TriggerTime)..." -ForegroundColor Green
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $TriggerTime

    # 动作: 启动批处理, 传入 phase 参数
    Write-Host "[2/4] 创建动作 (run_all_modules.bat $Phase)..." -ForegroundColor Green
    $action = New-ScheduledTaskAction -Execute $BatchFile -Argument $Phase -WorkingDirectory $ScriptDir

    # 设置: 重试 + 允许电池 + 不中断
    Write-Host "[3/4] 创建设置..." -ForegroundColor Green
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 5) `
        -ExecutionTimeLimit (New-TimeSpan -Hours 2)

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

    # 验证
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Write-Host "[OK] $TaskName 注册成功" -ForegroundColor Green
        Write-Host "     State:       $($task.State)"
        Write-Host "     NextRunTime: $($task.Triggers[0].StartBoundary)"
    } else {
        Write-Host "[ERROR] $TaskName registration failed" -ForegroundColor Red
        return $false
    }
    return $true
}

# ============================================================
# 注册两个任务
# ============================================================
$preOK = Register-V75Task -TaskName $PreTaskName -TriggerTime $PreTime -Phase "pre" `
    -Description "v7.5 盘前批次: Wind 校准 + v5 优化 + 每日交易工作流 (07:00)"

$postOK = Register-V75Task -TaskName $PostTaskName -TriggerTime $PostTime -Phase "post" `
    -Description "v7.5 盘后批次: 黑天鹅压力测试 + 汇总报告 (15:30)"

# ============================================================
# 汇总
# ============================================================
Write-Host ""
Write-Host "============================================================" -ForegroundColor $(if ($preOK -and $postOK) { "Green" } else { "Yellow" })
if ($preOK -and $postOK) {
    Write-Host "  [OK] 所有任务注册成功!" -ForegroundColor Green
} else {
    Write-Host "  [WARN] 部分任务注册失败, 请检查上方日志" -ForegroundColor Yellow
}
Write-Host "============================================================" -ForegroundColor $(if ($preOK -and $postOK) { "Green" } else { "Yellow" })

Write-Host ""
Write-Host "管理命令:" -ForegroundColor Cyan
Write-Host "  查看所有:    Get-ScheduledTask -TaskName 'v75_*'"
Write-Host "  立即运行盘前: Start-ScheduledTask -TaskName '$PreTaskName'"
Write-Host "  立即运行盘后: Start-ScheduledTask -TaskName '$PostTaskName'"
Write-Host "  禁用盘前:    Disable-ScheduledTask -TaskName '$PreTaskName'"
Write-Host "  禁用盘后:    Disable-ScheduledTask -TaskName '$PostTaskName'"
Write-Host "  卸载全部:    .\register_all_tasks.ps1 -Uninstall"
Write-Host "  测试盘前:    .\register_all_tasks.ps1 -Test pre"
Write-Host "  测试盘后:    .\register_all_tasks.ps1 -Test post"

if (-not ($preOK -and $postOK)) { exit 1 }
