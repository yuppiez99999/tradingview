# ============================================================
# 诊断 5 个 GBK 乱码任务的详细信息
# ============================================================
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$service = New-Object -ComObject Schedule.Service
$service.Connect()
$rootFolder = $service.GetFolder("\")

# 5 个乱码任务名 (从 PowerShell 输出捕获,UTF-8 BOM 编码)
$garbledNames = @(
    "鏅ㄩ棿PY绋嬪簭鑷姩杩愯",
    "姣忔棩宸ヤ綔娴乢鏃╂棩鎶ュ憲",
    "閲忓寲绛栫暐_01鐩樺墠璁″垝",
    "閲忓寲绛栫暐_02鐩樹腑鐩戞帶",
    "閲忓寲绛栫暐_03鐩樺悗鎶ュ憲"
)

Write-Host "============================================================" -ForegroundColor Yellow
Write-Host "  Diagnosing 5 garbled tasks" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Yellow

foreach ($gname in $garbledNames) {
    Write-Host ""
    Write-Host "==================================================" -ForegroundColor Cyan
    Write-Host "TaskName: $gname" -ForegroundColor Cyan
    Write-Host "==================================================" -ForegroundColor Cyan
    try {
        $task = $rootFolder.GetTask($gname)
        $def = $task.Definition

        # State
        $stateMap = @{0="Unknown";1="Disabled";2="Queued";3="Ready";4="Running"}
        Write-Host "State:    $($stateMap[[int]$task.State])" -ForegroundColor Gray
        Write-Host "LastRun:  $($task.LastRunTime) (Exit=$($task.LastTaskResult))" -ForegroundColor Gray
        Write-Host "NextRun:  $($task.NextRunTime)" -ForegroundColor Gray

        # Actions
        Write-Host ""
        Write-Host "Actions ($($def.Actions.Count)):" -ForegroundColor Yellow
        for ($i = 1; $i -le $def.Actions.Count; $i++) {
            $a = $def.Actions.Item($i)
            Write-Host "  [$i] Path:    $($a.Path)" -ForegroundColor White
            Write-Host "      Args:    $($a.Arguments)" -ForegroundColor White
            Write-Host "      WorkDir: $($a.WorkingDirectory)" -ForegroundColor White
        }

        # Triggers
        Write-Host ""
        Write-Host "Triggers ($($def.Triggers.Count)):" -ForegroundColor Yellow
        for ($i = 1; $i -le $def.Triggers.Count; $i++) {
            $t = $def.Triggers.Item($i)
            $trigType = switch ($t.Type) {
                1 { "TASK_TRIGGER_TIME" }
                2 { "TASK_TRIGGER_DAILY" }
                3 { "TASK_TRIGGER_WEEKLY" }
                6 { "TASK_TRIGGER_CALENDAR" }
                default { "Type_$($t.Type)" }
            }
            Write-Host "  [$i] Type:        $trigType" -ForegroundColor White
            Write-Host "      StartBoundary: $($t.StartBoundary)" -ForegroundColor White
            Write-Host "      Enabled:       $($t.Enabled)" -ForegroundColor White
            if ($t.Type -eq 2) {
                Write-Host "      DaysInterval:  $($t.DaysInterval)" -ForegroundColor Gray
            }
            if ($t.Type -eq 3) {
                Write-Host "      WeeksInterval:$($t.WeeksInterval)" -ForegroundColor Gray
                Write-Host "      DaysOfWeek:   $($t.DaysOfWeek) (bitmask)" -ForegroundColor Gray
            }
        }

        # Principal
        $p = $def.Principal
        Write-Host ""
        Write-Host "Principal:" -ForegroundColor Yellow
        Write-Host "  UserId:    $($p.UserId)" -ForegroundColor White
        Write-Host "  LogonType: $($p.LogonType)" -ForegroundColor White

    } catch {
        Write-Host "[ERROR] Task not found: $gname" -ForegroundColor Red
        Write-Host "        Exception: $($_.Exception.Message)" -ForegroundColor Red
    }
}

# Also list all task names with their hex bytes for analysis
Write-Host ""
Write-Host "============================================================" -ForegroundColor Yellow
Write-Host "  All task names (raw, for cross-reference)" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Yellow
$allTasks = $rootFolder.GetTasks(0)
$idx = 0
foreach ($t in $allTasks) {
    $idx++
    $name = $t.Name
    # Get bytes for analysis
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($name)
    $hexStr = ($bytes | ForEach-Object { $_.ToString("X2") }) -join " "
    Write-Host "[$idx] $name" -ForegroundColor White
    Write-Host "     HEX: $hexStr" -ForegroundColor Gray
}
