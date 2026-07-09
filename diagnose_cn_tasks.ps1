# ============================================================
# 诊断+修复 5 个中文命名任务 (使用正确的 UTF-8 名称)
# ============================================================
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$service = New-Object -ComObject Schedule.Service
$service.Connect()
$rootFolder = $service.GetFolder("\")

# 使用正确的中文任务名 (UTF-8 BOM 文件,PowerShell 会正确解析)
$tasks = @(
    @{ Name="晨间PY程序自动运行"; Desc="晨间 PY 程序自动运行" },
    @{ Name="每日工作流_晨间报告"; Desc="每日工作流_晨间报告" },
    @{ Name="量化策略_01盘前计划"; Desc="量化策略_01盘前计划" },
    @{ Name="量化策略_02盘中监控"; Desc="量化策略_02盘中监控" },
    @{ Name="量化策略_03盘后报告"; Desc="量化策略_03盘后报告" }
)

Write-Host "============================================================" -ForegroundColor Yellow
Write-Host "  Diagnosing 5 Chinese-named tasks" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Yellow

foreach ($cfg in $tasks) {
    $name = $cfg.Name
    Write-Host ""
    Write-Host "==================================================" -ForegroundColor Cyan
    Write-Host "TaskName: $name" -ForegroundColor Cyan
    Write-Host "==================================================" -ForegroundColor Cyan
    try {
        $task = $rootFolder.GetTask($name)
        $def = $task.Definition

        # State
        $stateMap = @{0="Unknown";1="Disabled";2="Queued";3="Ready";4="Running"}
        Write-Host "State:    $($stateMap[[int]$task.State])" -ForegroundColor Gray
        Write-Host "Enabled:  $($task.Enabled)" -ForegroundColor Gray
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
                1 { "TIME_ONCE" }
                2 { "DAILY" }
                3 { "WEEKLY" }
                6 { "CALENDAR" }
                11 { "TIME_DAILY" }
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
                Write-Host "      DaysOfWeek:   $($t.DaysOfWeek) (bitmask 1=Sun,2=Mon..64=Sat)" -ForegroundColor Gray
            }
        }

        # Principal
        $p = $def.Principal
        Write-Host ""
        Write-Host "Principal:" -ForegroundColor Yellow
        Write-Host "  UserId:    $($p.UserId)" -ForegroundColor White
        Write-Host "  LogonType: $($p.LogonType) (3=Interactive,5=SrvAcct)" -ForegroundColor White

    } catch {
        Write-Host "[ERROR] Task not found: $name" -ForegroundColor Red
        Write-Host "        Exception: $($_.Exception.Message)" -ForegroundColor Red
    }
}
