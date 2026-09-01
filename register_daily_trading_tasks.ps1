# DEPRECATED: 已被 scripts\register_all_tasks_unified.ps1 替代，请勿执行
# 迁移日期: 2026-07-30
# 原因: 该脚本注册的任务与统一脚本冲突，且使用不统一的 Python 路径
# 28终极量化交易系统 - 每日自动任务注册

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$projectRoot = "e:\各种PY程序\28-终极量化交易系统8.4"
$v83Dir = Join-Path $projectRoot "v8.3_institutional"
$pythonExe = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
$logDir = Join-Path $projectRoot "logs"

if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

$schedule = New-Object -ComObject Schedule.Service
$schedule.Connect()
$rootFolder = $schedule.GetFolder("\")

function Remove-TaskIfExists($name) {
    try {
        $t = $rootFolder.GetTask($name)
        if ($t) { $rootFolder.DeleteTask($name) }
    } catch {}
}

function New-TaskDef($desc, $limit = "PT30M") {
    $td = $schedule.NewTask(0)
    $td.RegistrationInfo.Description = $desc
    $td.RegistrationInfo.Author = "v84"
    $td.Settings.Enabled = $true
    $td.Settings.StartWhenAvailable = $true
    $td.Settings.StopIfGoingOnBatteries = $false
    $td.Settings.ExecutionTimeLimit = $limit
    $td.Settings.MultipleInstances = 2
    return $td
}

Write-Host ""
Write-Host "=== 28量化交易日自动任务注册 ===" -ForegroundColor Cyan
Write-Host ""

# 任务1: 盘前 08:30 工作流 + 交易计划
Write-Host "[1/4] Pre-market 08:30..." -ForegroundColor Cyan
$n1 = "v84_PreMarketDaily"
Remove-TaskIfExists $n1
$td1 = New-TaskDef "Pre-market daily workflow + plan (08:30)" "PT60M"
$t1 = $td1.Triggers.Create(3)
$t1.WeeksInterval = 1; $t1.DaysOfWeek = 0x3E
$t1.StartBoundary = "2026-07-30T08:30:00"; $t1.Enabled = $true
$a1a = $td1.Actions.Create(0)
$a1a.Path = $pythonExe; $a1a.Arguments = "`"$(Join-Path $v83Dir daily_workflow.py)`""
$a1a.WorkingDirectory = $v83Dir
$a1b = $td1.Actions.Create(0)
$a1b.Path = $pythonExe; $a1b.Arguments = "`"$(Join-Path $v83Dir generate_daily_trade_plan.py)`""
$a1b.WorkingDirectory = $v83Dir
$rootFolder.RegisterTaskDefinition($n1, $td1, 6, "SYSTEM", $null, 5)
Write-Host "  OK: v84_PreMarketDaily" -ForegroundColor Green

# 任务2: 盘中 每30分钟 LLM决策
Write-Host "[2/4] Intraday LLM every 30min..." -ForegroundColor Cyan
$n2 = "v84_IntradayLLMDecision"
Remove-TaskIfExists $n2
$td2 = New-TaskDef "Intraday LLM decision every 30min (09:30-15:00)" "PT20M"
$t2 = $td2.Triggers.Create(3)
$t2.WeeksInterval = 1; $t2.DaysOfWeek = 0x3E
$t2.StartBoundary = "2026-07-30T09:30:00"; $t2.Enabled = $true
$t2.Repetition.Interval = "PT30M"; $t2.Repetition.Duration = "PT6H"
$a2 = $td2.Actions.Create(0)
$a2.Path = $pythonExe; $a2.Arguments = "`"$(Join-Path $v83Dir llm_intraday_decision_engine.py)`" --mode live"
$a2.WorkingDirectory = $v83Dir
$rootFolder.RegisterTaskDefinition($n2, $td2, 6, "SYSTEM", $null, 5)
Write-Host "  OK: v84_IntradayLLMDecision" -ForegroundColor Green

# 任务3: 盘后 15:30 执行已确认指令
Write-Host "[3/4] Post-market 15:30..." -ForegroundColor Cyan
$n3 = "v84_PostMarketExecute"
Remove-TaskIfExists $n3
$td3 = New-TaskDef "Post-market execute confirmed orders (15:30)"
$t3 = $td3.Triggers.Create(3)
$t3.WeeksInterval = 1; $t3.DaysOfWeek = 0x3E
$t3.StartBoundary = "2026-07-30T15:30:00"; $t3.Enabled = $true
$a3 = $td3.Actions.Create(0)
$a3.Path = $pythonExe; $a3.Arguments = "`"$(Join-Path $projectRoot daily_trade_executor.py)`" post-market"
$a3.WorkingDirectory = $projectRoot
$rootFolder.RegisterTaskDefinition($n3, $td3, 6, "SYSTEM", $null, 5)
Write-Host "  OK: v84_PostMarketExecute" -ForegroundColor Green

# 任务4: 盘前 09:00 生成交易指令
Write-Host "[4/4] Pre-market instructions 09:00..." -ForegroundColor Cyan
$n4 = "v84_PreMarketInstructions"
Remove-TaskIfExists $n4
$td4 = New-TaskDef "Pre-market generate instructions (09:00)"
$t4 = $td4.Triggers.Create(3)
$t4.WeeksInterval = 1; $t4.DaysOfWeek = 0x3E
$t4.StartBoundary = "2026-07-30T09:00:00"; $t4.Enabled = $true
$a4 = $td4.Actions.Create(0)
$a4.Path = $pythonExe; $a4.Arguments = "`"$(Join-Path $projectRoot daily_trade_executor.py)`" pre-market"
$a4.WorkingDirectory = $projectRoot
$rootFolder.RegisterTaskDefinition($n4, $td4, 6, "SYSTEM", $null, 5)
Write-Host "  OK: v84_PreMarketInstructions" -ForegroundColor Green

Write-Host ""
Write-Host "=== 所有任务已注册 ===" -ForegroundColor Green
Write-Host "  08:30 Pre-market (workflow + plan)"
Write-Host "  09:00 Instructions generation"
Write-Host "  09:30 LLM intraday (every 30min)"
Write-Host "  15:30 Post-market execution"
Write-Host ""
Write-Host "管理: taskschd.msc"
Write-Host "卸载: .\unregister_daily_trading_tasks.ps1"
Write-Host ""
