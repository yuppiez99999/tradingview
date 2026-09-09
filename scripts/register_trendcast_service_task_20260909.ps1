# Register TrendCast Pro API (:8800) pre-market start / post-market stop tasks.
# Background (2026-09-09, F-3): 28 daily_runner 步骤2.5 依赖 16_ 服务在盘前已起;
# 服务未常驻时步骤 fail-open 跳过, 当日信号快照与日报卡片静默缺失 (无告警)。
#
# 设计要点:
#   * 启动任务 08:30 Mon-Fri, SYSTEM / LogonType=5 (Background) —— 与 v84 统一任务一致,
#     避免 InteractiveToken 被 Ctrl+C 杀掉 (0xC000013A, 见 2026-09-08 事故)。
#   * MultipleInstances=2 (IGNORE_NEW) —— 服务已在跑则不重复启动。
#   * 停止任务 20:00 Mon-Fri, 按 8800 端口监听进程 kill, 避免残留常驻进程。
#   * 需管理员权限运行 (注册 SYSTEM 任务)。
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$pythonExe   = "E:\Python38\python.exe"      # 16_ 依赖 (fastapi/uvicorn/lightgbm) 装在此解释器
$serviceDir  = "E:\各种PY程序\16_金融市场预测模型"
$startTask   = "TrendCast_Service_Start_8800"
$stopTask    = "TrendCast_Service_Stop_8800"
$startTime   = "08:30"
$stopTime    = "20:00"
$startBnd    = "2026-09-10T${startTime}:00"
$stopBnd     = "2026-09-10T${stopTime}:00"
$psExe       = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

if (-not (Test-Path $pythonExe))  { throw "python not found: $pythonExe" }
if (-not (Test-Path $serviceDir)) { throw "service dir not found: $serviceDir" }

$service = New-Object -ComObject Schedule.Service
$service.Connect()
$rootFolder = $service.GetFolder("\")

function New-WeekdayTrigger($td, $boundary) {
    $t = $td.Triggers.Create(3)          # TASK_TRIGGER_WEEKLY
    $t.WeeksInterval = 1
    $t.DaysOfWeek    = 0x3E              # Mon-Fri
    $t.StartBoundary = $boundary
    $t.Enabled       = $true
    return $t
}

# ---------- 启动任务 ----------
$td = $service.NewTask(0)
$td.RegistrationInfo.Description = "TrendCast Pro API (:8800) pre-market start, SYSTEM/Background (2026-09-09)"
$td.RegistrationInfo.Author      = "trendcast-integration-20260909"
$td.Settings.Enabled                    = $true
$td.Settings.AllowDemandStart           = $true
$td.Settings.StartWhenAvailable         = $true
$td.Settings.StopIfGoingOnBatteries     = $false
$td.Settings.DisallowStartIfOnBatteries = $false
$td.Settings.ExecutionTimeLimit         = "PT12H"
$td.Settings.MultipleInstances          = 2   # TASK_INSTANCES_IGNORE_NEW
New-WeekdayTrigger $td $startBnd | Out-Null

$action = $td.Actions.Create(0)
$action.Path = $pythonExe
$action.Arguments = "main.py serve"
$action.WorkingDirectory = $serviceDir

$rootFolder.RegisterTaskDefinition($startTask, $td, 6, "SYSTEM", $null, 5) | Out-Null
Write-Output "REGISTERED OK: $startTask ($startTime Mon-Fri, SYSTEM/Background)"

# ---------- 停止任务 ----------
$td2 = $service.NewTask(0)
$td2.RegistrationInfo.Description = "TrendCast Pro API (:8800) post-market stop by port (2026-09-09)"
$td2.RegistrationInfo.Author      = "trendcast-integration-20260909"
$td2.Settings.Enabled                    = $true
$td2.Settings.AllowDemandStart           = $true
$td2.Settings.StartWhenAvailable         = $true
$td2.Settings.StopIfGoingOnBatteries     = $false
$td2.Settings.DisallowStartIfOnBatteries = $false
$td2.Settings.ExecutionTimeLimit         = "PT5M"
New-WeekdayTrigger $td2 $stopBnd | Out-Null

$killCmd = "Get-NetTCPConnection -LocalPort 8800 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id `$_.OwningProcess -Force -ErrorAction SilentlyContinue }"
$action2 = $td2.Actions.Create(0)
$action2.Path = $psExe
$action2.Arguments = "-NoProfile -NonInteractive -Command `"$killCmd`""
$action2.WorkingDirectory = $serviceDir

$rootFolder.RegisterTaskDefinition($stopTask, $td2, 6, "SYSTEM", $null, 5) | Out-Null
Write-Output "REGISTERED OK: $stopTask ($stopTime Mon-Fri, SYSTEM/Background)"
