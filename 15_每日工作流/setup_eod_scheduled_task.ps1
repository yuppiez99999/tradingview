# DEPRECATED: 已被 scripts\register_all_tasks_unified.ps1 替代，请勿执行
# 迁移日期: 2026-07-30
# 原因: EOD 工作流已由 v84_PostMarket（15:30 调用 run_daily_eod_workflow.py）承担
﻿# ============================================================
# Configure Windows Scheduled Task: Daily 15:30 EOD Workflow
# ============================================================
#
# Function:
#   - Create scheduled task "QuantEODWorkflow" to run EOD workflow daily at 15:30
#   - Uses COM object Schedule.Service (more stable than Register-ScheduledTask)
#   - Directly calls python.exe + .py (avoids .bat middle layer)
#
# Usage:
#   Run PowerShell as Administrator, then:
#   powershell -ExecutionPolicy Bypass -File setup_eod_scheduled_task.ps1
#
# Uninstall:
#   powershell -ExecutionPolicy Bypass -File setup_eod_scheduled_task.ps1 -Remove
# ============================================================

param(
    [switch]$Remove = $false,
    [string]$TaskName = "QuantEODWorkflow",
    [string]$StartTime = "15:30"
)

# Project path configuration
$ProjectRoot = "E:\各种PY程序\28-终极量化交易系统8.4"
$PythonExe = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
$EodScript = Join-Path $ProjectRoot "15_每日工作流\run_daily_eod_workflow.py"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  EOD Workflow Scheduled Task Configuration" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Task Name: $TaskName"
Write-Host "Run Time : Daily at $StartTime"
Write-Host "Python   : $PythonExe"
Write-Host "Script   : $EodScript"
Write-Host ""

# Uninstall mode
if ($Remove) {
    Write-Host "[INFO] Removing scheduled task: $TaskName" -ForegroundColor Yellow
    try {
        $service = New-Object -ComObject Schedule.Service
        $service.Connect()
        $rootFolder = $service.GetFolder("\")
        $rootFolder.DeleteTask($TaskName, 0)
        Write-Host "[OK] Scheduled task deleted: $TaskName" -ForegroundColor Green
    } catch {
        Write-Host "[WARN] Delete failed (task may not exist): $($_.Exception.Message)" -ForegroundColor Yellow
    }
    exit 0
}

# Verify files exist
if (-not (Test-Path $PythonExe)) {
    Write-Host "[ERROR] Python interpreter not found: $PythonExe" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $EodScript)) {
    Write-Host "[ERROR] EOD script not found: $EodScript" -ForegroundColor Red
    exit 1
}

# Use COM object Schedule.Service to create scheduled task
try {
    Write-Host "[INFO] Connecting to Task Scheduler service..." -ForegroundColor Yellow
    $service = New-Object -ComObject Schedule.Service
    $service.Connect()
    $rootFolder = $service.GetFolder("\")

    # If task already exists, delete it first
    try {
        $rootFolder.GetTask($TaskName) | Out-Null
        Write-Host "[INFO] Task already exists, deleting old task..." -ForegroundColor Yellow
        $rootFolder.DeleteTask($TaskName, 0)
    } catch {
        # Task does not exist, ignore
    }

    # Create task definition
    Write-Host "[INFO] Creating task definition..." -ForegroundColor Yellow
    $taskDef = $service.NewTask(0)
    $taskDef.RegistrationInfo.Description = "Daily EOD Workflow (15:30): closing report + DeepSeek decisions + risk guards + archive"
    $taskDef.RegistrationInfo.Author = "Quant Trading System v5.9"

    # Settings: allow on-demand start, run ASAP if missed
    $taskDef.Settings.Enabled = $true
    $taskDef.Settings.StartWhenAvailable = $true
    $taskDef.Settings.AllowDemandStart = $true
    $taskDef.Settings.ExecutionTimeLimit = "PT1H"  # 1 hour timeout
    $taskDef.Settings.DisallowStartIfOnBatteries = $false
    $taskDef.Settings.StopIfGoingOnBatteries = $false

    # Trigger: daily trigger
    Write-Host "[INFO] Configuring trigger: Daily at $StartTime" -ForegroundColor Yellow
    $triggers = $taskDef.Triggers
    $trigger = $triggers.Create(2)  # 2 = TASK_TRIGGER_DAILY
    $trigger.StartBoundary = "2026-01-01T${StartTime}:00"
    $trigger.DaysInterval = 1
    $trigger.Enabled = $true
    # Weekend skip: handled by Python script's is_trading_day() check

    # Action: start python.exe to run EOD script
    Write-Host "[INFO] Configuring action: launch Python to run EOD script..." -ForegroundColor Yellow
    $actions = $taskDef.Actions
    $action = $actions.Create(0)  # 0 = TASK_ACTION_EXEC
    $action.Path = $PythonExe
    $action.Arguments = "`"$EodScript`""
    $action.WorkingDirectory = $ProjectRoot

    # Principal: current user, interactive logon
    Write-Host "[INFO] Configuring principal: current user..." -ForegroundColor Yellow
    $principal = $taskDef.Principal
    $principal.UserId = $env:USERNAME
    $principal.LogonType = 3  # 3 = TASK_LOGON_INTERACTIVE_TOKEN_OR_PASSWORD
    $principal.RunLevel = 0   # 0 = TASK_RUNLEVEL_LUA (normal privileges)

    # Register task
    Write-Host "[INFO] Registering scheduled task..." -ForegroundColor Yellow
    $rootFolder.RegisterTaskDefinition(
        $TaskName,
        $taskDef,
        6,  # 6 = TASK_CREATE_OR_UPDATE
        $null,
        $null,
        3   # 3 = TASK_LOGON_INTERACTIVE_TOKEN_OR_PASSWORD
    ) | Out-Null

    Write-Host ""
    Write-Host "[OK] Scheduled task created successfully!" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "  Task Name : $TaskName" -ForegroundColor Green
    Write-Host "  Run Time  : Daily at $StartTime" -ForegroundColor Green
    Write-Host "  Command   : $PythonExe $EodScript" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Cyan
    Write-Host "  - View: open taskschd.msc (Task Scheduler) and search for '$TaskName'"
    Write-Host "  - Test now: right-click task -> Run"
    Write-Host "  - Uninstall: powershell -File setup_eod_scheduled_task.ps1 -Remove"
    Write-Host ""

    # Display task status
    try {
        $registeredTask = $rootFolder.GetTask($TaskName)
        $taskState = switch ($registeredTask.State) {
            0 { "Unknown" }
            1 { "Disabled" }
            2 { "Queued" }
            3 { "Ready" }
            4 { "Running" }
            default { "Unknown" }
        }
        Write-Host "[INFO] Task current state: $taskState" -ForegroundColor Cyan

        # Output next run time
        $nextRun = $registeredTask.NextRunTime
        if ($nextRun -gt [DateTime]::MinValue) {
            Write-Host "[INFO] Next run time: $($nextRun.ToString('yyyy-MM-dd HH:mm:ss'))" -ForegroundColor Cyan
        }
    } catch {
        Write-Host "[WARN] Cannot read task state: $($_.Exception.Message)" -ForegroundColor Yellow
    }

} catch {
    Write-Host "[ERROR] Scheduled task creation failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ""
    Write-Host "Troubleshooting:" -ForegroundColor Yellow
    Write-Host "  1. Make sure PowerShell is running as Administrator"
    Write-Host "  2. Confirm Task Scheduler service is running (services.msc -> Task Scheduler)"
    Write-Host "  3. Check paths:"
    Write-Host "     Python: $PythonExe"
    Write-Host "     Script: $EodScript"
    exit 1
}
