# DEPRECATED: 已被 scripts\register_all_tasks_unified.ps1 替代，请勿执行
# 迁移日期: 2026-07-30
# 原因: 标的池扫描已由 v84_UniverseScan（16:30 调用 research\run_universe_scan.py）承担
# ============================================================
# Full-Market Auto Stock Selection System - Windows Task Setup
#
# Daily after-close (16:00) auto run full-market stock scan.
#
# Usage (Run PowerShell as Administrator):
#   powershell -ExecutionPolicy Bypass -File scripts/setup_universe_scheduler.ps1
#   powershell -ExecutionPolicy Bypass -File scripts/setup_universe_scheduler.ps1 -Pool tdx_top800
#   powershell -ExecutionPolicy Bypass -File scripts/setup_universe_scheduler.ps1 -Uninstall
#   powershell -ExecutionPolicy Bypass -File scripts/setup_universe_scheduler.ps1 -DryRun
# ============================================================

param(
    [string]$Pool = "hs300_zz500",
    [switch]$SkipSmokeTest,
    [string]$Schedule = "16:00",
    [string]$PythonExe = "",
    [switch]$Uninstall,
    [switch]$DryRun,
    [string]$TaskName = "QuantUniverseDailyScan"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$EntryScript = Join-Path $RepoRoot "research\run_universe_scan.py"
$LogDir = Join-Path $RepoRoot "logs"
$OutputDir = Join-Path $RepoRoot "reports\universe"

# ============================================================
# 1. Auto-detect Python interpreter
# ============================================================
function Get-PythonPath {
    if ($PythonExe -and (Test-Path $PythonExe)) {
        return $PythonExe
    }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { return "py" }
    $p3 = Get-Command python3 -ErrorAction SilentlyContinue
    if ($p3) { return "python3" }
    $p = Get-Command python -ErrorAction SilentlyContinue
    if ($p) { return "python" }
    throw "[ERR] Python interpreter not found. Install Python 3.8+ or use -PythonExe parameter."
}

# ============================================================
# 2. Ensure required directories exist
# ============================================================
function Initialize-Directories {
    foreach ($dir in @($LogDir, $OutputDir)) {
        if (-not (Test-Path $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
            Write-Host "  [CREATE] $dir"
        }
    }
}

# ============================================================
# 3. Remove scheduled task
# ============================================================
function Uninstall-Task {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Write-Host "Uninstalling scheduled task: $TaskName"
        if (-not $DryRun) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        }
        Write-Host "  [REMOVED] $TaskName"
    }
    else {
        Write-Host "  [SKIP] Task '$TaskName' does not exist"
    }
}

# ============================================================
# 4. Smoke test - verify pipeline works before registering
# ============================================================
function Invoke-SmokeTest {
    param([string]$PythonCmd)
    Write-Host ""
    Write-Host "============================================================"
    Write-Host "  Smoke Test: 5-stock pipeline verification"
    Write-Host "============================================================"

    $pyExe = if ($PythonCmd -eq "py") { "py" } else { $PythonCmd }
    $smokeArgs = "research/run_universe_scan.py --smoke-test --smoke-count 5 --pool $Pool"
    Write-Host "  CMD: cd $RepoRoot ; $pyExe $smokeArgs"

    if ($DryRun) {
        Write-Host "  [DRY RUN] Skipping smoke test"
        return
    }

    Push-Location $RepoRoot
    try {
        $result = & $pyExe $smokeArgs.Split() 2>&1
        $exitCode = $LASTEXITCODE
        Write-Host $result
        if ($exitCode -ne 0) {
            Write-Warning "Smoke test FAILED (exit code: $exitCode). Continuing to register task anyway."
        }
        else {
            Write-Host "  [PASS] Smoke test succeeded"
        }
    }
    finally {
        Pop-Location
    }
}

# ============================================================
# 5. Register Windows scheduled task (daily)
# ============================================================
function Register-DailyTask {
    param([string]$PythonCmd)
    Write-Host ""
    Write-Host "============================================================"
    Write-Host "  Register Windows Scheduled Task"
    Write-Host "============================================================"

    $timeParts = $Schedule -split ":"
    $hour = [int]$timeParts[0]
    $minute = [int]$timeParts[1]

    # Build the task action: powershell wrapper that runs python script
    # 使用 py -3.8 强制使用 Python 3.8 (项目主版本, vibe_trading_adapter 需要)
    $actionScript = "-ExecutionPolicy Bypass -NoProfile -Command `"Set-Location '$RepoRoot'; " +
        "`$logPath = Join-Path '$LogDir' ('universe_scan_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.log'); " +
        "& py -3.8 research/run_universe_scan.py --pool $Pool *>&1 | Tee-Object -FilePath `$logPath; " +
        "exit `$LASTEXITCODE`""

    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument $actionScript

    $trigger = New-ScheduledTaskTrigger `
        -Daily `
        -At ("{0:00}:{1:00}" -f $hour, $minute)

    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -RunOnlyIfNetworkAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 60)

    $principal = New-ScheduledTaskPrincipal `
        -UserId "NT AUTHORITY\SYSTEM" `
        -RunLevel Limited

    Write-Host "  Task Name:    $TaskName"
    Write-Host "  Schedule:     Daily at $($hour.ToString('00')):$($minute.ToString('00'))"
    Write-Host "  Stock Pool:   $Pool"
    Write-Host "  Python:       $PythonCmd"
    Write-Host "  Entry Script: $EntryScript"
    Write-Host "  Log Dir:      $LogDir"
    Write-Host "  Report Dir:   $OutputDir"

    if ($DryRun) {
        Write-Host "  [DRY RUN] Task NOT registered"
        return
    }

    # Remove any existing task with same name first
    Uninstall-Task

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Description "Hedge-fund grade full-market auto stock selection system - daily scan" `
        -Force

    Write-Host ""
    Write-Host "  [REGISTERED] Scheduled task '$TaskName' created successfully"
    Write-Host "  Manual trigger:  schtasks /Run /TN '$TaskName'"
    Write-Host "  Check status:    schtasks /Query /TN '$TaskName' /V"
    Write-Host "  View task:       taskschd.msc"
}

# ============================================================
# MAIN
# ============================================================
function Main {
    Write-Host ""
    Write-Host "============================================================"
    Write-Host "  Full-Market Auto Stock Selection - Task Setup"
    Write-Host "============================================================"
    Write-Host ""

    # Check for admin privileges
    $isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin -and -not $DryRun) {
        Write-Warning "Administrator privileges are required to register a scheduled task."
        Write-Warning "Please run PowerShell as Administrator, or use -DryRun to preview."
        return
    }

    $python = Get-PythonPath
    Write-Host "Python Interpreter:  $python"
    Write-Host "Project Root:        $RepoRoot"
    Write-Host ""

    if ($Uninstall) {
        Uninstall-Task
        return
    }

    Initialize-Directories

    if (-not $SkipSmokeTest) {
        Invoke-SmokeTest -PythonCmd $python
    }

    Register-DailyTask -PythonCmd $python

    Write-Host ""
    Write-Host "============================================================"
    Write-Host "  Setup Complete"
    Write-Host "============================================================"
    Write-Host "  Daily at $Schedule : full-market auto stock scan"
    Write-Host "  Logs:    $LogDir\universe_scan_*.log"
    Write-Host "  Reports: $OutputDir\YYYY-MM-DD\"
    Write-Host ""
    Write-Host "  Manual trigger:  schtasks /Run /TN '$TaskName'"
    Write-Host "  Pause task:      schtasks /Change /TN '$TaskName' /DISABLE"
    Write-Host "  Resume task:     schtasks /Change /TN '$TaskName' /ENABLE"
    Write-Host "  Uninstall:       powershell -File scripts/setup_universe_scheduler.ps1 -Uninstall"
    Write-Host ""
}

Main
