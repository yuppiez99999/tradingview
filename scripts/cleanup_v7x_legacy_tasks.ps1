<#
.SYNOPSIS
    清理 v7.x 旧版本 Windows 定时任务残留
.DESCRIPTION
    清理 v7.5 / v7.6 版本遗留的定时任务, 这些任务已被 v8.6 版本的
    QuantPipelineFactor_06AM / QuantWorkflow_07AM / QuantMorning_0930 /
    QuantAfternoon_1400 / v86_EOD_Report 取代.

    v8.6.8 P1-LIVE-07 修复 (2026-07-26):
        原始问题: 系统中存在 13 个 v7.x 旧版本定时任务, 与 v8.6 任务并行运行,
        可能导致重复执行、资源冲突、信号冲突.

    清理原则:
        1. 仅清理明确属于 v7.5/v7.6 的任务 (v75_*/v76_*/v7.5_*)
        2. 保留所有 v8.6+ 任务 (Quant*/v86_*)
        3. 清理前自动备份到 docs/legacy_tasks_backup_YYYYMMDD/
        4. 需要 Administrator 权限运行

.NOTES
    需要管理员权限: 以 Administrator 身份运行 PowerShell
    使用方法: 右键 -> 以管理员身份运行 PowerShell, 然后执行:
        powershell -ExecutionPolicy Bypass -File scripts\cleanup_v7x_legacy_tasks.ps1
#>

#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"

# 当前生产任务白名单 (v8.6+)
$PRODUCTION_TASKS = @(
    "QuantPipelineFactor_06AM",
    "QuantWorkflow_07AM",
    "QuantMorning_0930",
    "QuantAfternoon_1400",
    "v86_EOD_Report"
)

# 待清理的 v7.x 旧版本任务
$LEGACY_TASKS = @(
    "v7.5_Live_Scheduler",
    "v7.5_Live_Scheduler_Stop",
    "v75_AutoLearn",
    "v75_DailyWorkflow",
    "v75_PostMarket",
    "v75_PreMarket",
    "v75_TradePostMarket",
    "v75_TradePreMarket",
    "v75_WeeklyTrade",
    "v75_WeeklyTrade_Night",
    "v75_WeeklyTrade_PM",
    "v76_PostMarket",
    "v76_PreMarket"
)

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptRoot
$backupDir = Join-Path $projectRoot "docs\legacy_tasks_backup_$(Get-Date -Format 'yyyyMMdd')"

Write-Host "=" * 70 -ForegroundColor Cyan
Write-Host "v7.x 旧版本定时任务清理脚本" -ForegroundColor Cyan
Write-Host "v8.6.8 P1-LIVE-07 (2026-07-26)" -ForegroundColor Cyan
Write-Host "=" * 70 -ForegroundColor Cyan
Write-Host ""

# 1. 备份
Write-Host "[1/3] 备份旧任务信息..." -ForegroundColor Yellow
if (-not (Test-Path $backupDir)) {
    New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
}

foreach ($task in $LEGACY_TASKS) {
    $info = schtasks /query /tn $task /fo LIST /v 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) {
        $info | Out-File -FilePath "$backupDir\$task.txt" -Encoding UTF8
        Write-Host "  ✓ 备份: $task" -ForegroundColor Green
    } else {
        Write-Host "  - 跳过 (不存在): $task" -ForegroundColor Gray
    }
}
Write-Host ""

# 2. 列出当前生产任务 (确认不会被删除)
Write-Host "[2/3] 当前生产任务 (将保留):" -ForegroundColor Yellow
foreach ($task in $PRODUCTION_TASKS) {
    $info = schtasks /query /tn $task /fo LIST 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) {
        $nextRun = (Select-String -InputObject $info -Pattern "Next Run Time: (.+)" |
                   ForEach-Object { $_.Matches.Groups[1].Value.Trim() }) -join ", "
        Write-Host "  ✓ $task (Next: $nextRun)" -ForegroundColor Green
    } else {
        Write-Host "  ✗ $task (未注册!)" -ForegroundColor Red
    }
}
Write-Host ""

# 3. 删除旧任务
Write-Host "[3/3] 删除 v7.x 旧版本任务..." -ForegroundColor Yellow
$deleted = 0
$failed = 0
$skipped = 0

foreach ($task in $LEGACY_TASKS) {
    # 安全检查: 确认不在生产任务白名单中
    if ($PRODUCTION_TASKS -contains $task) {
        Write-Host "  ✗ 跳过 (在生产白名单): $task" -ForegroundColor Red
        $skipped++
        continue
    }

    # 检查任务是否存在
    $null = schtasks /query /tn $task 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  - 已不存在: $task" -ForegroundColor Gray
        $skipped++
        continue
    }

    # 删除任务
    $result = schtasks /delete /tn $task /f 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  ✓ 已删除: $task" -ForegroundColor Green
        $deleted++
    } else {
        Write-Host "  ✗ 删除失败: $task - $result" -ForegroundColor Red
        $failed++
    }
}

Write-Host ""
Write-Host "=" * 70 -ForegroundColor Cyan
Write-Host "清理完成" -ForegroundColor Cyan
Write-Host "  已删除: $deleted" -ForegroundColor Green
Write-Host "  已跳过: $skipped" -ForegroundColor Gray
Write-Host "  失败:   $failed" -ForegroundColor $(if ($failed -gt 0) { 'Red' } else { 'Green' })
Write-Host "  备份目录: $backupDir" -ForegroundColor Gray
Write-Host "=" * 70 -ForegroundColor Cyan

# 4. 验证最终状态
Write-Host ""
Write-Host "最终任务列表 (Quant 相关):" -ForegroundColor Yellow
$allTasks = schtasks /query /fo LIST 2>&1 | Out-String
$quantTasks = Select-String -InputObject $allTasks -Pattern "TaskName:.*\\(Quant|v\d+_)" |
              ForEach-Object { $_.Line.Trim() -replace "TaskName:\s*", "" }
foreach ($t in $quantTasks) {
    Write-Host "  $t" -ForegroundColor White
}

Write-Host ""
Write-Host "日志已保存到: $backupDir\cleanup_log_$(Get-Date -Format 'yyyyMMdd_HHmmss').txt" -ForegroundColor Gray
$endTime = Get-Date -Format "yyyyMMdd_HHmmss"
$report = @"
v7.x 旧版本定时任务清理报告
时间: $(Get-Date)
操作员: $env:USERNAME
机器: $env:COMPUTERNAME

已删除任务数: $deleted
已跳过任务数: $skipped
失败任务数: $failed

保留的生产任务:
$($PRODUCTION_TASKS -join "`n")

删除的旧任务:
$($LEGACY_TASKS -join "`n")
"@
$report | Out-File -FilePath "$backupDir\cleanup_log_$endTime.txt" -Encoding UTF8
