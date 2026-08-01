# DEPRECATED: 已被 scripts\register_all_tasks_unified.ps1 替代，请勿执行
# 迁移日期: 2026-07-30
# 原因: 该脚本注册的任务与统一脚本冲突，且使用不统一的 Python 路径
# ============================================================
# 注册 v8.4 系统定时任务（统一入口）
# ============================================================
# 修复说明:
#   旧版调用 v7.5_institutional/run_all_modules.bat (文件不存在)
#   新版直接调用 v8.4 的入口脚本
# ============================================================

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$MorningBat = Join-Path $ScriptDir "15_每日工作流\run_daily_morning.bat"
$EodBat     = Join-Path $ScriptDir "15_每日工作流\run_eod_workflow.bat"
$ReportBat  = Join-Path $ScriptDir "run_daily_report.bat"

# 检查目标 bat 是否存在
foreach ($b in @($MorningBat, $EodBat, $ReportBat)) {
    if (-not (Test-Path $b)) {
        Write-Host "[WARN] 文件不存在: $b" -ForegroundColor Yellow
    }
}

$tasks = @(
    @{
        Name = "v84_PreMarket"
        Time = "07:00"
        Action = New-ScheduledTaskAction -Execute $MorningBat -WorkingDirectory $ScriptDir
        Desc = "v8.4 盘前批次: 晨间信息采集 + 建仓计划生成 (07:00)"
    },
    @{
        Name = "v84_PostMarket"
        Time = "15:30"
        Action = New-ScheduledTaskAction -Execute $EodBat -WorkingDirectory $ScriptDir
        Desc = "v8.4 盘后批次: 收盘工作流 + 持仓更新 (15:30)"
    },
    @{
        Name = "v84_DailyPnlReport"
        Time = "16:00"
        Action = New-ScheduledTaskAction -Execute $ReportBat -WorkingDirectory $ScriptDir
        Desc = "v8.4 每日盈亏报告 (16:00)"
    }
)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5)

foreach ($t in $tasks) {
    Write-Host "注册 $($t.Name) @ $($t.Time)..." -ForegroundColor Cyan
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $t.Time
    try {
        Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false -ErrorAction SilentlyContinue
    } catch {}
    try {
        Register-ScheduledTask -TaskName $t.Name -Trigger $trigger -Action $t.Action -Settings $settings -Description $t.Desc -Force | Out-Null
        Write-Host "  [OK] $($t.Name) 注册成功" -ForegroundColor Green
    } catch {
        Write-Host "  [FAIL] $($t.Name) 注册失败: $_" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "v8.4 任务注册完成!" -ForegroundColor Green
Write-Host ""
Write-Host "注意: 旧的 v75_* 任务（如有）已废弃，建议手动清理:" -ForegroundColor Yellow
Write-Host "  schtasks /delete /tn v75_PreMarket /f" -ForegroundColor Gray
Write-Host "  schtasks /delete /tn v75_PostMarket /f" -ForegroundColor Gray
Write-Host "  schtasks /delete /tn v75_DailyPnlReport /f" -ForegroundColor Gray
