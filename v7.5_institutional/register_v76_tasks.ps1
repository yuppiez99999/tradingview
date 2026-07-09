# ============================================================
# v7.6 Auto Trading - Windows Task Scheduler Registration
# ============================================================
# 1. Register "v76_PreMarket"  task, Mon-Fri 07:00
# 2. Register "v76_PostMarket" task, Mon-Fri 15:30
# 3. Weekend auto skip
# 4. Retry 3 times, interval 5 minutes
#
# Usage (PowerShell as Administrator):
#   cd e:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional
#   .\register_v76_tasks.ps1                # register
#   .\register_v76_tasks.ps1 -Uninstall     # uninstall
#   .\register_v76_tasks.ps1 -Test pre      # test pre-market
#   .\register_v76_tasks.ps1 -Test post     # test post-market
#   .\register_v76_tasks.ps1 -Test all      # test all
# ============================================================

param(
    [switch]$Uninstall,
    [string]$Test,
    [string]$PreTaskName = "v76_PreMarket",
    [string]$PostTaskName = "v76_PostMarket",
    [string]$PreTime = "07:00",
    [string]$PostTime = "15:30"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BatchFile = Join-Path $ScriptDir "run_daily.bat"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  v7.6 Auto Trading - Task Scheduler Registration" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "PreMarket  Task: $PreTaskName  @ $PreTime  (Mon-Fri)"
Write-Host "PostMarket Task: $PostTaskName @ $PostTime (Mon-Fri)"
Write-Host "Script:     $BatchFile"
Write-Host "Plan:       trade_plans/auto_trade_plan_500w_2026-2030.json"
Write-Host ""

# ============================================================
# Uninstall mode
# ============================================================
if ($Uninstall) {
    Write-Host "[Uninstall] Removing v7.6 tasks..." -ForegroundColor Yellow
    foreach ($name in @($PreTaskName, $PostTaskName)) {
        try {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction Stop
            Write-Host "  [OK] Removed: $name" -ForegroundColor Green
        } catch {
            Write-Host "  [SKIP] Not found: $name" -ForegroundColor Yellow
        }
    }
    exit 0
}

# ============================================================
# Check batch file
# ============================================================
if (-not (Test-Path $BatchFile)) {
    Write-Host "[ERROR] Batch file not found: $BatchFile" -ForegroundColor Red
    exit 1
}

# ============================================================
# Test mode
# ============================================================
if ($Test) {
    Write-Host "[Test] Running test: phase=$Test..." -ForegroundColor Yellow
    $phase = if ($Test -in @("pre","post","all")) { $Test } else { "all" }
    Start-Process -FilePath $BatchFile -ArgumentList $phase -Wait -NoNewWindow
    exit 0
}

# ============================================================
# Register single task
# ============================================================
function Register-V76Task {
    param(
        [string]$TaskName,
        [string]$TriggerTime,
        [string]$Phase,
        [string]$Description
    )

    Write-Host ""
    Write-Host "------ Registering $TaskName ------" -ForegroundColor Cyan

    # Check if exists
    $existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existingTask) {
        Write-Host "[WARN] Task exists, removing first" -ForegroundColor Yellow
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }

    # Trigger: Mon-Fri
    Write-Host "[1/4] Creating trigger (Mon-Fri $TriggerTime)..." -ForegroundColor Green
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $TriggerTime

    # Action: run batch file with phase arg
    Write-Host "[2/4] Creating action (run_daily.bat $Phase)..." -ForegroundColor Green
    $action = New-ScheduledTaskAction -Execute $BatchFile -Argument $Phase -WorkingDirectory $ScriptDir

    # Settings: retry + battery + no interrupt
    Write-Host "[3/4] Creating settings..." -ForegroundColor Green
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

    Write-Host "[4/4] Registering task..." -ForegroundColor Green
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Trigger $trigger `
        -Action $action `
        -Settings $settings `
        -Principal $principal `
        -Description $Description `
        -Force | Out-Null

    # Verify
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Write-Host "[OK] $TaskName registered" -ForegroundColor Green
        Write-Host "     State:       $($task.State)"
    } else {
        Write-Host "[ERROR] $TaskName registration failed" -ForegroundColor Red
        return $false
    }
    return $true
}

# ============================================================
# Register tasks
# ============================================================
$results = @()
$results += Register-V76Task -TaskName $PreTaskName -TriggerTime $PreTime -Phase pre `
    -Description "v7.6 PreMarket: Wind calibrate + position update + plan load"
$results += Register-V76Task -TaskName $PostTaskName -TriggerTime $PostTime -Phase post `
    -Description "v7.6 PostMarket: daily report + PnL + rebalance"

Write-Host ""
if ($results -contains $false) {
    Write-Host "[DONE] Some tasks failed, check logs above" -ForegroundColor Yellow
    exit 1
} else {
    Write-Host "[DONE] v7.6 auto trading tasks registered" -ForegroundColor Green
    exit 0
}
