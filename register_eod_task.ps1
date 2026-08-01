# DEPRECATED: 已被 scripts\register_all_tasks_unified.ps1 替代，请勿执行
# 迁移日期: 2026-07-30
# 原因: EOD 四 Guard 风控链已合并到 v84_PostMarket（15:30 调用 run_daily_eod_workflow.py）
# ============================================================
# 注册盘后报告自动运行任务 (16:00 触发)
# ============================================================
# 任务: v86_EOD_Report
# 触发: 每周一至五 16:00 (A股收盘后)
# 动作: 调用 run_daily_eod.py 执行 EOD 四 Guard 风控链 + 生成报告
#       (保证金熔断→回撤检查→波动率控制→对冲执行→认沽保护)
#
# 使用 COM 对象 Schedule.Service (绕过 PowerShell cmdlet bug)
# 直接调用 python.exe + .py 文件 (避免 .bat 中间层)
# ============================================================

$ErrorActionPreference = "Stop"

# ============================================================
# 路径配置 (v8.6.1: 从 v7.1 更新到 v8.4)
# ============================================================
$projectDir = "E:\各种PY程序\28-终极量化交易系统8.4"
$pythonExe  = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
$scriptPath = Join-Path $projectDir "run_daily_eod.py"

# 检查 python.exe 是否存在
if (-not (Test-Path $pythonExe)) {
    Write-Host "[ERROR] Python not found: $pythonExe" -ForegroundColor Red
    Write-Host "        Please install Python 3.11+ or update this script." -ForegroundColor Yellow
    exit 1
}

# 检查 run_daily_eod.py 是否存在
if (-not (Test-Path $scriptPath)) {
    Write-Host "[ERROR] Script not found: $scriptPath" -ForegroundColor Red
    exit 1
}

# ============================================================
# 连接 Task Scheduler (COM)
# ============================================================
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Registering v86_EOD_Report Task (via COM)" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Project Dir : $projectDir"
Write-Host "  Python      : $pythonExe"
Write-Host "  Script      : $scriptPath"
Write-Host ""

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
        # 任务不存在, 忽略
    }
}

# ============================================================
# 任务名称和描述
# ============================================================
$taskName = "v86_EOD_Report"
$description = "v8.6.1 EOD 四 Guard 风控链 + 盘后报告自动生成 (16:00) — 保证金熔断/回撤检查/波动率控制/对冲执行/认沽保护 (基于 config/positions.json)"

# ============================================================
# 删除已存在的任务
# ============================================================
Remove-ExistingTask $taskName

# ============================================================
# 创建任务定义
# ============================================================
$taskDef = $service.NewTask(0)
$taskDef.RegistrationInfo.Description = $description
$taskDef.RegistrationInfo.Author = $env:USERNAME

# 任务设置
$taskDef.Settings.Enabled = $true
$taskDef.Settings.AllowDemandStart = $true
$taskDef.Settings.StartWhenAvailable = $true       # 错过时间后补跑
$taskDef.Settings.ExecutionTimeLimit = "PT2H"        # 最长运行 2 小时
$taskDef.Settings.DisallowStartIfOnBatteries = $false
$taskDef.Settings.StopIfGoingOnBatteries = $false

# ============================================================
# 触发器: 每周一至五 16:00
# ============================================================
$trigger = $taskDef.Triggers.Create(3)  # 3 = TASK_TRIGGER_WEEKLY
$trigger.WeeksInterval = 1
$trigger.DaysOfWeek = 62  # Mon=2 + Tue=4 + Wed=8 + Thu=16 + Fri=32
$trigger.StartBoundary = "2026-01-01T16:00:00"
$trigger.Enabled = $true

# ============================================================
# 动作: 直接调用 python.exe 执行 run_daily_eod.py
# ============================================================
$action = $taskDef.Actions.Create(0)  # 0 = TASK_ACTION_EXEC
$action.Path = $pythonExe
$action.Arguments = "`"$scriptPath`""
$action.WorkingDirectory = $projectDir

# ============================================================
# 注册任务
# ============================================================
$rootFolder.RegisterTaskDefinition(
    $taskName,
    $taskDef,
    6,   # TASK_CREATE_OR_UPDATE
    $env:USERNAME,
    $null,
    3    # TASK_LOGON_INTERACTIVE_TOKEN
) | Out-Null

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Task Registered Successfully!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Task Name : $taskName" -ForegroundColor Cyan
Write-Host "  Trigger   : Mon-Fri @ 16:00 (A股收盘后)" -ForegroundColor Cyan
Write-Host "  Action    : $pythonExe `"$scriptPath`"" -ForegroundColor Cyan
Write-Host "  WorkDir   : $projectDir" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Report Output:" -ForegroundColor Cyan
Write-Host "    EOD Guard Report: 每日报告归档\{YYYY-MM-DD}\eod_guard_report_{YYYY-MM-DD}.md"
Write-Host "    Trade Plan      : v8.3_institutional\trade_plans\trade_plan_{YYYYMMDD}.json (含 risk_guard 结果)"
Write-Host "    Log             : logs\run_daily_eod_{YYYYMMDD}.log"
Write-Host ""
Write-Host "  Management Commands:" -ForegroundColor Yellow
Write-Host "    Query   : schtasks.exe /Query /TN v86_EOD_Report"
Write-Host "    Run now : schtasks.exe /Run   /TN v86_EOD_Report"
Write-Host "    Disable : schtasks.exe /Change /TN v86_EOD_Report /DISABLE"
Write-Host "    Delete  : schtasks.exe /Delete /TN v86_EOD_Report /F"
Write-Host ""
