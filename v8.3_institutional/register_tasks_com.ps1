# ============================================================
# 使用 COM 对象注册计划任务 (绕过 PowerShell cmdlet bug)
# ============================================================
$ErrorActionPreference = "Stop"

$service = New-Object -ComObject Schedule.Service
$service.Connect()
$rootFolder = $service.GetFolder("\")

# ============================================================
# 辅助函数: 删除已存在的任务
# ============================================================
function Remove-ExistingTask($name) {
    try {
        $rootFolder.GetTask($name)
        $rootFolder.DeleteTask($name, 0)
        Write-Host "  [OK] Deleted existing: $name" -ForegroundColor Yellow
    } catch {
        # 不存在，忽略
    }
}

# ============================================================
# 辅助函数: 注册任务
# ============================================================
function Register-Task($taskName, $batPath, $workDir, $triggerTime, $description) {
    Remove-ExistingTask $taskName

    $taskDef = $service.NewTask(0)
    $taskDef.RegistrationInfo.Description = $description
    $taskDef.RegistrationInfo.Author = $env:USERNAME
    $taskDef.Settings.Enabled = $true
    $taskDef.Settings.AllowDemandStart = $true
    $taskDef.Settings.StartWhenAvailable = $true
    $taskDef.Settings.ExecutionTimeLimit = "PT2H"

    # 触发器: 每周一至五
    $trigger = $taskDef.Triggers.Create(3)  # 3 = TASK_TRIGGER_WEEKLY
    $trigger.WeeksInterval = 1
    $trigger.DaysOfWeek = 62  # Mon=2+Tue=4+Wed=8+Thu=16+Fri=32
    $trigger.StartBoundary = "2026-01-01T${triggerTime}:00"
    $trigger.Enabled = $true

    # 动作: 启动 bat 文件
    $action = $taskDef.Actions.Create(0)  # 0 = TASK_ACTION_EXEC
    $action.Path = $batPath
    $action.WorkingDirectory = $workDir

    # 注册
    $rootFolder.RegisterTaskDefinition(
        $taskName,
        $taskDef,
        6,   # TASK_CREATE_OR_UPDATE
        $env:USERNAME,
        $null,
        3    # TASK_LOGON_INTERACTIVE_TOKEN
    ) | Out-Null

    Write-Host "  [OK] Registered: $taskName" -ForegroundColor Green
    Write-Host "       Trigger: Mon-Fri @ $triggerTime"
    Write-Host "       Action:  $batPath"
}

# ============================================================
# 注册三个任务
# ============================================================
$v75Dir = "E:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional"
$runDaily = Join-Path $v75Dir "run_daily.bat"
$runAllModules = Join-Path $v75Dir "run_all_modules.bat"
$runDailyReport = "E:\各种PY程序\28-终极量化交易系统7.1\run_daily_report.bat"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Registering v7.5 Scheduled Tasks (via COM)" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# 1. v75_PreMarket: 每日 07:00 盘前批次
Register-Task "v75_PreMarket" $runAllModules $v75Dir "07:00" "v7.5 盘前批次: Wind校准 + 每日交易工作流 (07:00)"

# 2. v75_PostMarket: 每日 15:30 盘后批次
Register-Task "v75_PostMarket" $runAllModules $v75Dir "15:30" "v7.5 盘后批次: 黑天鹅压力测试 + 盈亏报告 (15:30)"

# 3. v75_DailyWorkflow: 每日 07:05 直接跑 daily_workflow (独立备份)
Register-Task "v75_DailyWorkflow" $runDaily $v75Dir "07:05" "v7.5 每日交易工作流 (07:05)"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  All tasks registered!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Management commands:" -ForegroundColor Cyan
Write-Host "  Query:    schtasks.exe /Query /TN v75_PreMarket"
Write-Host "  Run now:  schtasks.exe /Run /TN v75_PreMarket"
Write-Host "  Disable:   schtasks.exe /Change /TN v75_PreMarket /DISABLE"
Write-Host "  Delete:    schtasks.exe /Delete /TN v75_PreMarket /F"
