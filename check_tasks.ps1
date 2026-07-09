# ============================================================
# 检查所有计划任务状态
# ============================================================
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$service = New-Object -ComObject Schedule.Service
$service.Connect()
$rootFolder = $service.GetFolder("\")

# 之前修复的 8 个任务
$repairedTasks = @(
    "v75_PreMarket",
    "v75_PostMarket",
    "v75_DailyWorkflow",
    "MorningReportRunner_7AM",
    "Commodity_Daily_Report_6_40AM",
    "盘前交易计划",
    "QuantTradingWorkflow",
    "QuantV5_TradingHoursMaster"
)

Write-Host "============================================================" -ForegroundColor Yellow
Write-Host "  Repaired Tasks Status (8)" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Yellow

foreach ($name in $repairedTasks) {
    Write-Host ""
    Write-Host "[$name]" -ForegroundColor Cyan
    try {
        $task = $rootFolder.GetTask($name)
        $action = $task.Definition.Actions.Item(1)
        $stateMap = @{0="Unknown";1="Disabled";2="Queued";3="Ready";4="Running"}
        $state = $stateMap[[int]$task.State]

        Write-Host "  Path:    $($action.Path)" -ForegroundColor White
        Write-Host "  Args:    $($action.Arguments)" -ForegroundColor White
        Write-Host "  WorkDir: $($action.WorkingDirectory)" -ForegroundColor White
        Write-Host "  State:   $state" -ForegroundColor Gray
        Write-Host "  LastRun: $($task.LastRunTime)" -ForegroundColor Gray
        $exitCode = $task.LastTaskResult
        $exitHex = "0x{0:X}" -f $exitCode
        $exitDesc = switch ($exitCode) {
            0           { "SUCCESS" }
            1           { "SCRIPT_ERROR" }
            2           { "FILE_NOT_FOUND" }
            267009      { "HAS_NOT_RUN" }
            267011      { "HAS_NOT_RUN_2" }
            -2147024629 { "PATH_NOT_FOUND_0x80070003" }
            default     { "OTHER" }
        }
        Write-Host "  LastExit:$exitCode ($exitHex) = $exitDesc" -ForegroundColor Gray
        Write-Host "  NextRun: $($task.NextRunTime)" -ForegroundColor Gray

        # Verify file existence
        if (Test-Path $action.Path) {
            Write-Host "  File:    EXISTS" -ForegroundColor Green
        } else {
            Write-Host "  File:    NOT_FOUND" -ForegroundColor Red
        }
    } catch {
        Write-Host "  [ERROR] Task not found" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Yellow
Write-Host "  All Tasks Summary" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Yellow

# List all tasks
$tasks = $rootFolder.GetTasks(0)
$totalCount = 0
$enabledCount = 0
$disabledCount = 0
$hasNotRunCount = 0
$successCount = 0
$failCount = 0

$results = @()
foreach ($task in $tasks) {
    $totalCount++
    if ($task.State -eq 1) { $disabledCount++ } else { $enabledCount++ }

    $exitCode = $task.LastTaskResult
    if ($exitCode -eq 267009 -or $exitCode -eq 267011) {
        $hasNotRunCount++
    } elseif ($exitCode -eq 0) {
        $successCount++
    } else {
        $failCount++
    }

    $action = $null
    try { $action = $task.Definition.Actions.Item(1) } catch {}

    $results += [PSCustomObject]@{
        Name = $task.Name
        State = $task.State
        LastExit = $exitCode
        Path = if ($action) { $action.Path } else { "" }
    }
}

Write-Host "Total:     $totalCount" -ForegroundColor White
Write-Host "Enabled:    $enabledCount" -ForegroundColor Green
Write-Host "Disabled:   $disabledCount" -ForegroundColor Yellow
Write-Host "Never Ran:  $hasNotRunCount" -ForegroundColor Gray
Write-Host "Success:    $successCount" -ForegroundColor Green
Write-Host "Failed:     $failCount" -ForegroundColor Red
Write-Host ""
Write-Host "All Tasks:" -ForegroundColor Cyan
$results | Format-Table Name, State, LastExit, Path -AutoSize
