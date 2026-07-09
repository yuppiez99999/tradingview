$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BatchFile = Join-Path $ScriptDir "v7.5_institutional\run_all_modules.bat"

$tasks = @(
    @{
        Name = "v75_PreMarket"
        Time = "07:00"
        Action = New-ScheduledTaskAction -Execute $BatchFile -Argument "pre" -WorkingDirectory (Split-Path $BatchFile)
        Desc = "盘前批次: Wind校准 + v5优化 + 每日交易工作流 (07:00)"
    },
    @{
        Name = "v75_PostMarket"
        Time = "15:30"
        Action = New-ScheduledTaskAction -Execute $BatchFile -Argument "post" -WorkingDirectory (Split-Path $BatchFile)
        Desc = "盘后批次: 黑天鹅压力测试 + 收盘盈亏报告 (15:30)"
    },
    @{
        Name = "v75_DailyPnlReport"
        Time = "15:30"
        Action = New-ScheduledTaskAction -Execute (Join-Path $ScriptDir "run_daily_report.bat")
        Desc = "收盘盈亏报告自动生成 (15:30)"
    }
)

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)

foreach ($t in $tasks) {
    Write-Host "注册 $($t.Name) @ $($t.Time)..." -ForegroundColor Cyan
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $t.Time
    try {
        Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false -ErrorAction SilentlyContinue
    } catch {}
    Register-ScheduledTask -TaskName $t.Name -Trigger $trigger -Action $t.Action -Settings $settings -Description $t.Desc -Force | Out-Null
    Write-Host "  [OK] $($t.Name) 注册成功" -ForegroundColor Green
}

Write-Host ""
Write-Host "所有任务注册完成!" -ForegroundColor Green
schtasks /query /tn "v75_*" /fo table