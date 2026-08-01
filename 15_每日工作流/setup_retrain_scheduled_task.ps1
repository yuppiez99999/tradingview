# ============================================================
# Configure Windows Scheduled Task: Monthly ML Model Retrain
# ============================================================
#
# Function:
#   - Create scheduled task "QuantMLRetrain" to run ML model retrain monthly
#   - Runs on 1st of each month at 06:00 AM (before market open)
#   - Uses COM object Schedule.Service (more stable than Register-ScheduledTask)
#   - Directly calls python.exe + run_auto_retrain.py (avoids .bat middle layer)
#
# Usage:
#   Run PowerShell as Administrator, then:
#   powershell -ExecutionPolicy Bypass -File setup_retrain_scheduled_task.ps1
#
# Uninstall:
#   powershell -ExecutionPolicy Bypass -File setup_retrain_scheduled_task.ps1 -Remove
# ============================================================

param(
    [switch]$Remove = $false,
    [string]$TaskName = "QuantMLRetrain",
    [string]$StartTime = "06:00"
)

# Project path configuration
$ProjectRoot = "E:\各种PY程序\28-终极量化交易系统8.4"
$PythonExe = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
$RetrainScript = Join-Path $ProjectRoot "15_每日工作流\run_auto_retrain.py"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  ML Model Auto-Retrain Scheduled Task Configuration" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Task Name : $TaskName"
Write-Host "Run Time  : Monthly 1st at $StartTime"
Write-Host "Python    : $PythonExe"
Write-Host "Script    : $RetrainScript"
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
if (-not (Test-Path $RetrainScript)) {
    Write-Host "[ERROR] Retrain script not found: $RetrainScript" -ForegroundColor Red
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
    $taskDef.RegistrationInfo.Description = "Monthly ML Model Auto-Retrain (1st 06:00): scan all LGB models, retrain degraded ones (IC<0 or age>30 days)"
    $taskDef.RegistrationInfo.Author = "Quant Trading System v5.9"

    # Settings
    $taskDef.Settings.Enabled = $true
    $taskDef.Settings.StartWhenAvailable = $true
    $taskDef.Settings.AllowDemandStart = $true
    $taskDef.Settings.ExecutionTimeLimit = "PT4H"  # 4 hour timeout (full retrain may take 2+ hours)
    $taskDef.Settings.DisallowStartIfOnBatteries = $false
    $taskDef.Settings.StopIfGoingOnBatteries = $false

    # Trigger: monthly trigger (1st of each month)
    Write-Host "[INFO] Configuring trigger: Monthly 1st at $StartTime" -ForegroundColor Yellow
    $triggers = $taskDef.Triggers
    $trigger = $triggers.Create(4)  # 4 = TASK_TRIGGER_MONTHLY
    $trigger.StartBoundary = "2026-02-01T${StartTime}:00"
    $trigger.MonthsOfYear = 0xFFF  # All 12 months (bitmask: Jan=1, Feb=2, ..., Dec=2048)
    $trigger.DaysOfMonth = 0x1     # 1st day of month (bitmask: day 1 = 0x1)
    $trigger.Enabled = $true

    # Action: start python.exe to run retrain script
    Write-Host "[INFO] Configuring action: launch Python to run retrain script..." -ForegroundColor Yellow
    $actions = $taskDef.Actions
    $action = $actions.Create(0)  # 0 = TASK_ACTION_EXEC
    $action.Path = $PythonExe
    $action.Arguments = "`"$RetrainScript`""
    $action.WorkingDirectory = $ProjectRoot

    # Principal
    Write-Host "[INFO] Configuring principal: current user..." -ForegroundColor Yellow
    $principal = $taskDef.Principal
    $principal.UserId = $env:USERNAME
    $principal.LogonType = 3  # 3 = TASK_LOGON_INTERACTIVE_TOKEN_OR_PASSWORD
    $principal.RunLevel = 0   # 0 = TASK_RUNLEVEL_LUA

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
    Write-Host "  Run Time  : Monthly 1st at $StartTime" -ForegroundColor Green
    Write-Host "  Command   : $PythonExe $RetrainScript" -ForegroundColor Green
    Write-Host "  Timeout   : 4 hours" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Retrain Strategy:" -ForegroundColor Cyan
    Write-Host "  - Incremental retrain (default): only retrain models with IC<0, Sharpe<0, or age>30 days"
    Write-Host "  - Full retrain: add --force flag to retrain all 23 models (takes ~2-3 hours)"
    Write-Host "  - Manual run: right-click task -> Run, or execute the script directly"
    Write-Host "  - Uninstall: powershell -File setup_retrain_scheduled_task.ps1 -Remove"
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
    Write-Host "  2. Confirm Task Scheduler service is running"
    Write-Host "  3. Check paths:"
    Write-Host "     Python: $PythonExe"
    Write-Host "     Script: $RetrainScript"
    exit 1
}
