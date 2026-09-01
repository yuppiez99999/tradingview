# DEPRECATED: 已被 scripts\register_all_tasks_unified.ps1 替代，请勿执行
# 迁移日期: 2026-07-30
# 原因: 盘前/盘后交易执行已由 v84_PreMarketInstructions(09:00) 和 v84_PostMarketExecute(15:35) 承担
# 注册每日自动交易执行任务
# 盘前 09:00 生成交易指令
# 盘后 15:30 执行已确认指令

$ErrorActionPreference = "Stop"

$projectRoot = "e:\各种PY程序\28-终极量化交易系统8.4"
# P0-2 修复 (2026-09-01): 原硬编码 AppData Python311 路径不存在
$pythonExe = "E:\各种PY程序\28-终极量化交易系统8.4\.venv\Scripts\python.exe"
$executorScript = Join-Path $projectRoot "daily_trade_executor.py"

# 使用 COM 对象创建计划任务 (比 PowerShell cmdlet 更稳定)
$schedule = New-Object -ComObject Schedule.Service
$schedule.Connect()
$rootFolder = $schedule.GetFolder("\")

# ============================================================
# 任务1: 盘前生成交易指令 (每个交易日 09:00)
# ============================================================
$taskName1 = "v75_TradePreMarket"

# 先删除已存在的任务
$taskExists1 = $false
try {
    $t1 = $rootFolder.GetTask($taskName1)
    if ($t1) {
        $rootFolder.DeleteTask($taskName1)
        Write-Host "已删除旧任务: $taskName1" -ForegroundColor Yellow
    }
} catch {
    Write-Host "任务 $taskName1 不存在, 将创建新任务"
}

$taskDef1 = $schedule.NewTask(0)
$taskDef1.RegistrationInfo.Description = "盘前生成交易指令 (每日09:00)"
$taskDef1.RegistrationInfo.Author = "trade_executor"
$taskDef1.Settings.Enabled = $true
$taskDef1.Settings.StartWhenAvailable = $true
$taskDef1.Settings.StopIfGoingOnBatteries = $false
$taskDef1.Settings.ExecutionTimeLimit = "PT15M"

# 触发器: 每个交易日 09:00 (周一至周五)
$trigger1 = $taskDef1.Triggers.Create(3)  # 3 = TASK_TRIGGER_WEEKLY
$trigger1.WeeksInterval = 1
$trigger1.DaysOfWeek = 0x3E  # 周一至周五 (bit mask: Mon=2, Tue=4, Wed=8, Thu=16, Fri=32)
$trigger1.StartBoundary = "2026-07-10T09:00:00"
$trigger1.Enabled = $true

# 动作: 运行 pre-market 指令生成
$action1 = $taskDef1.Actions.Create(0)
$action1.Path = $pythonExe
$action1.Arguments = "`"$executorScript`" pre-market"
$action1.WorkingDirectory = $projectRoot

# 注册任务
$rootFolder.RegisterTaskDefinition($taskName1, $taskDef1, 6, "SYSTEM", $null, 5)
Write-Host "✓ 任务1已注册: $taskName1 (每日09:00盘前生成指令)" -ForegroundColor Green

# ============================================================
# 任务2: 盘后执行已确认指令 (每个交易日 15:30)
# ============================================================
$taskName2 = "v75_TradePostMarket"

try {
    $t2 = $rootFolder.GetTask($taskName2)
    if ($t2) {
        $rootFolder.DeleteTask($taskName2)
        Write-Host "已删除旧任务: $taskName2" -ForegroundColor Yellow
    }
} catch {
    Write-Host "任务 $taskName2 不存在, 将创建新任务"
}

$taskDef2 = $schedule.NewTask(0)
$taskDef2.RegistrationInfo.Description = "盘后执行已确认交易指令 (每日15:30)"
$taskDef2.RegistrationInfo.Author = "trade_executor"
$taskDef2.Settings.Enabled = $true
$taskDef2.Settings.StartWhenAvailable = $true
$taskDef2.Settings.StopIfGoingOnBatteries = $false
$taskDef2.Settings.ExecutionTimeLimit = "PT15M"

# 触发器: 每个交易日 15:30 (周一至周五)
$trigger2 = $taskDef2.Triggers.Create(3)
$trigger2.WeeksInterval = 1
$trigger2.DaysOfWeek = 0x3E  # 周一至周五
$trigger2.StartBoundary = "2026-07-10T15:30:00"  # 必须包含秒
$trigger2.Enabled = $true

# 动作: 运行 post-market 执行
$action2 = $taskDef2.Actions.Create(0)
$action2.Path = $pythonExe
$action2.Arguments = "`"$executorScript`" post-market"
$action2.WorkingDirectory = $projectRoot

$rootFolder.RegisterTaskDefinition($taskName2, $taskDef2, 6, "SYSTEM", $null, 5)
Write-Host "✓ 任务2已注册: $taskName2 (每日15:30盘后执行已确认指令)" -ForegroundColor Green

# ============================================================
# 验证
# ============================================================
Write-Host ""
Write-Host "=== 已注册任务 ===" -ForegroundColor Cyan

$task1 = $rootFolder.GetTask($taskName1)
Write-Host "任务1: $($task1.Name)"
Write-Host "  状态: $($task1.State)"
Write-Host "  描述: $($task1.Definition.RegistrationInfo.Description)"

$task2 = $rootFolder.GetTask($taskName2)
Write-Host "任务2: $($task2.Name)"
Write-Host "  状态: $($task2.State)"
Write-Host "  描述: $($task2.Definition.RegistrationInfo.Description)"

Write-Host ""
Write-Host "=== 工作流程 ===" -ForegroundColor Cyan
Write-Host "1. 09:00 自动生成交易指令 → trade_instructions/YYYY-MM-DD_instructions.json + .md"
Write-Host "2. 09:00-15:30 人工确认 → 修改 JSON 中的 confirm: false → true"
Write-Host "3. 15:30 自动执行已确认指令 → 更新 build_progress.json + 生成执行报告"
Write-Host ""
Write-Host "=== 手动运行 ===" -ForegroundColor Cyan
Write-Host "盘前: python daily_trade_executor.py pre-market"
Write-Host "盘后: python daily_trade_executor.py post-market"
Write-Host "进度: python daily_trade_executor.py progress"
Write-Host "计划: python daily_trade_executor.py schedule"
