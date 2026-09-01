# ============================================================
# 修复计划任务路径 (5 GBK-garbled + 3 path-broken)
# Uses COM Schedule.Service API to preserve triggers
# ============================================================
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$service = New-Object -ComObject Schedule.Service
$service.Connect()
$rootFolder = $service.GetFolder("\")

# P0-2 修复 (2026-09-01): 原硬编码 AppData Python311 路径不存在, 统一改用项目 .venv (3.14.4)
$py38 = "E:\各种PY程序\28-终极量化交易系统8.4\.venv\Scripts\python.exe"
$py314 = "E:\各种PY程序\28-终极量化交易系统8.4\.venv\Scripts\python.exe"
$v75Dir = "E:\各种PY程序\28-终极量化交易系统8.4\v8.3_institutional"
$baseDir = "E:\各种PY程序\28-终极量化交易系统8.4"
$flowDir = "E:\各种PY程序\15_每日工作流"
$sentDir = "E:\各种PY程序\02_舆情与竞品监控\舆情监控"
$stratDir = "E:\各种PY程序\11_量化策略"

$fixes = @(
    @{ Name="v75_PreMarket"; Path=Join-Path $v75Dir "run_all_modules.bat"; Arguments="pre"; WorkDir=$v75Dir }
    @{ Name="v75_PostMarket"; Path=Join-Path $v75Dir "run_all_modules.bat"; Arguments="post"; WorkDir=$v75Dir }
    @{ Name="v75_DailyWorkflow"; Path=Join-Path $v75Dir "run_daily.bat"; Arguments=""; WorkDir=$v75Dir }
    @{ Name="MorningReportRunner_7AM"; Path=$py38; Arguments="`"$flowDir\morning_report_runner.py`""; WorkDir=$flowDir }
    @{ Name="Commodity_Daily_Report_6_40AM"; Path=Join-Path $sentDir "每日自动运行.bat"; Arguments=""; WorkDir=$sentDir }
    @{ Name="盘前交易计划"; Path=Join-Path $baseDir "run_pre_market.bat"; Arguments=""; WorkDir=$baseDir }
    @{ Name="QuantTradingWorkflow"; Path=Join-Path $v75Dir "daily_workflow.py"; Arguments=""; WorkDir=$v75Dir }
    @{ Name="QuantV5_TradingHoursMaster"; Path=$py38; Arguments="`"$stratDir\量化策略系统 v5.10.py`" --live"; WorkDir=$stratDir }
)

function Repair-Task {
    param($cfg)
    $name = $cfg.Name
    Write-Host ""
    Write-Host "[$name]" -ForegroundColor Cyan
    Write-Host "  Target: $($cfg.Path)"
    Write-Host "  Args:   $($cfg.Arguments)"
    Write-Host "  WorkDir:$($cfg.WorkingDirectory)"

    if (-not (Test-Path $cfg.Path)) {
        Write-Host "  [SKIP] Target file not found" -ForegroundColor Red
        return $false
    }
    Write-Host "  [OK] Target file exists" -ForegroundColor Green

    try {
        $task = $rootFolder.GetTask($name)
        $def = $task.Definition
    } catch {
        Write-Host "  [ERROR] Task not found: $name" -ForegroundColor Red
        return $false
    }

    if ($def.Actions.Count -eq 0) {
        $action = $def.Actions.Create(0)
    } else {
        $action = $def.Actions.Item(1)
    }
    $oldPath = $action.Path
    $oldArgs = $action.Arguments
    Write-Host "  Old Path: $oldPath"
    Write-Host "  Old Args: $oldArgs"

    $action.Path = $cfg.Path
    $action.Arguments = $cfg.Arguments
    if ($cfg.WorkDir) { $action.WorkingDirectory = $cfg.WorkDir }

    $principal = $def.Principal
    $userId = $principal.UserId
    if (-not $userId) { $userId = $env:USERNAME }
    $logonType = $principal.LogonType
    if (-not $logonType) { $logonType = 3 }
    Write-Host "  User: $userId, LogonType: $logonType"

    try {
        $rootFolder.RegisterTaskDefinition($name, $def, 4, $userId, $null, $logonType) | Out-Null
        Write-Host "  [OK] Task updated successfully" -ForegroundColor Green
        return $true
    } catch {
        Write-Host "  [FAIL] $($_.Exception.Message)" -ForegroundColor Red
        return $false
    }
}

Write-Host "============================================================" -ForegroundColor Yellow
Write-Host "  Scheduled Task Repair (COM API)" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Yellow

$success = 0; $failed = 0
foreach ($cfg in $fixes) {
    if (Repair-Task $cfg) { $success++ } else { $failed++ }
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Yellow
Write-Host "  Repair Summary: Success=$success, Failed=$failed" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Yellow

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Verification - Listing repaired tasks" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

foreach ($cfg in $fixes) {
    Write-Host ""
    Write-Host "[$($cfg.Name)]" -ForegroundColor Cyan
    try {
        $task = $rootFolder.GetTask($cfg.Name)
        $action = $task.Definition.Actions.Item(1)
        Write-Host "  Path:    $($action.Path)" -ForegroundColor White
        Write-Host "  Args:    $($action.Arguments)" -ForegroundColor White
        Write-Host "  WorkDir: $($action.WorkingDirectory)" -ForegroundColor White
        $trigCount = $task.Definition.Triggers.Count
        Write-Host "  Triggers: $trigCount" -ForegroundColor Gray
        Write-Host "  LastRun: $($task.LastRunTime) (Exit=$($task.LastTaskResult))" -ForegroundColor Gray
    } catch {
        Write-Host "  [ERROR] Failed to read task" -ForegroundColor Red
    }
}
