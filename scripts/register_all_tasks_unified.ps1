# ============================================================
# 28终极量化交易系统 v8.6.14 - 统一计划任务注册脚本（唯一权威源）
# ============================================================
# 替代 7 套旧注册脚本的混乱，统一管理 9 个任务
#
# 设计要点：
#   - COM 对象 Schedule.Service（不用 PowerShell Register-ScheduledTask cmdlet）
#   - 所有任务直接调用 Python 3.11 绝对路径 + .py 文件（消除 .bat 中间层）
#   - 所有任务用 SYSTEM 身份（LogonType=5）+ 工作目录 = 项目根目录
#   - 触发器 TASK_TRIGGER_WEEKLY(3) + DaysOfWeek=0x3E（周一至周五）
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File "scripts\register_all_tasks_unified.ps1"           # 注册
#   powershell -ExecutionPolicy Bypass -File "scripts\register_all_tasks_unified.ps1" -Uninstall # 卸载
#   powershell -ExecutionPolicy Bypass -File "scripts\register_all_tasks_unified.ps1" -DryRun    # 预览
# ============================================================

param(
    [switch]$Uninstall,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# ============================================================
# 路径配置（统一使用 Python 3.11 绝对路径）
# ============================================================
$projectRoot = "e:\各种PY程序\28-终极量化交易系统8.4"
$pythonExe   = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
$logDir      = Join-Path $projectRoot "logs"

# 验证 Python 解释器存在
if (-not (Test-Path $pythonExe)) {
    Write-Host "[ERROR] Python 解释器不存在: $pythonExe" -ForegroundColor Red
    Write-Host "        请安装 Python 3.11+ 或修改此脚本中的 pythonExe 变量" -ForegroundColor Yellow
    exit 1
}

# 验证项目根目录存在
if (-not (Test-Path $projectRoot)) {
    Write-Host "[ERROR] 项目根目录不存在: $projectRoot" -ForegroundColor Red
    exit 1
}

# 确保日志目录存在
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

# ============================================================
# 9 个任务定义（替代 7 套旧脚本的混乱）
# ============================================================
# 每个任务：Name, StartTime, Script（相对项目根目录）, Args, Timeout, Description, RepetitionInterval, RepetitionDuration
$tasks = @(
    @{
        Name = "v84_PreMarket"
        StartTime = "07:00"
        Script = "15_每日工作流\run_daily_morning.py"
        Args = "--phase all"
        Timeout = "PT2H"
        Desc = "盘前工作流（晨间信息采集+康波+ETF+舆情）07:00"
        RepetitionInterval = $null
        RepetitionDuration = $null
    },
    @{
        Name = "v84_PreMarketInstructions"
        StartTime = "09:00"
        Script = "daily_trade_executor.py"
        Args = "pre-market"
        Timeout = "PT30M"
        Desc = "盘前生成交易指令 09:00"
        RepetitionInterval = $null
        RepetitionDuration = $null
    },
    @{
        Name = "v84_IntradayLLMDecision"
        StartTime = "09:30"
        Script = "v8.3_institutional\llm_intraday_decision_engine.py"
        Args = "--mode live"
        Timeout = "PT20M"
        Desc = "盘中LLM决策（每30分钟，持续6小时）09:30-15:30"
        RepetitionInterval = "PT30M"
        RepetitionDuration = "PT6H"
    },
    @{
        Name = "v84_PostMarket"
        StartTime = "15:30"
        Script = "15_每日工作流\run_daily_eod_workflow.py"
        Args = ""
        Timeout = "PT2H"
        Desc = "盘后工作流（含EOD四Guard风控链：保证金熔断→回撤→波动率→对冲→认沽保护）15:30"
        RepetitionInterval = $null
        RepetitionDuration = $null
    },
    @{
        Name = "v84_PostMarketExecute"
        StartTime = "15:35"
        Script = "daily_trade_executor.py"
        Args = "post-market"
        Timeout = "PT30M"
        Desc = "盘后执行已确认指令 15:35"
        RepetitionInterval = $null
        RepetitionDuration = $null
    },
    @{
        Name = "v84_DailyPnlReport"
        StartTime = "16:00"
        Script = "generate_daily_report.py"
        Args = ""
        Timeout = "PT30M"
        Desc = "收盘PnL报告自动生成 16:00"
        RepetitionInterval = $null
        RepetitionDuration = $null
    },
    @{
        Name = "v84_EvolutionEval"
        StartTime = "16:05"
        Script = "scripts\run_evolution_eval.py"
        Args = ""
        Timeout = "PT30M"
        Desc = "策略进化评估 16:05"
        RepetitionInterval = $null
        RepetitionDuration = $null
    },
    @{
        Name = "v84_ObservationBriefing"
        StartTime = "16:10"
        Script = "scripts\observation_daily_briefing.py"
        Args = ""
        Timeout = "PT15M"
        Desc = "观察简报 16:10"
        RepetitionInterval = $null
        RepetitionDuration = $null
    },
    @{
        # 2026-07-30 新增: 修复 shadow_admission_launcher.py daily 无自动触发机制的链路缺口
        # (daily_workflow.py shadow_monitor 只写 daily_returns.jsonl, 不调用 daily)
        # 与 v84_PostMarket(15:30) 错开 45 分钟, 确保 daily_returns.jsonl 已写完
        Name = "v84_ShadowAdmissionDaily"
        StartTime = "16:15"
        Script = "scripts\shadow_admission_launcher.py"
        Args = "daily"
        Timeout = "PT15M"
        Desc = "观察期每日 DSR 报告生成+fail-fast 风控检查 16:15 (shadow_admission_launcher daily)"
        RepetitionInterval = $null
        RepetitionDuration = $null
    },
    @{
        Name = "v84_UniverseScan"
        StartTime = "16:30"
        Script = "research\run_universe_scan.py"
        Args = "--pool hs300_zz500"
        Timeout = "PT1H"
        Desc = "标的池扫描 16:30"
        RepetitionInterval = $null
        RepetitionDuration = $null
    },
    @{
        # 2026-07-30 新增: Shadow Admission Watchdog 反馈机制
        # 检测 v84_ShadowAdmissionDaily (16:15) 是否成功生成 DSR, 未生成则补跑+告警
        # 与主任务错开 45 分钟, 确保 DSR 已写完或主任务已超时
        Name = "v84_ShadowAdmissionWatchdog"
        StartTime = "17:00"
        Script = "scripts\shadow_admission_watchdog.py"
        Args = ""
        Timeout = "PT5M"
        Desc = "Shadow 准入 DSR 自愈 watchdog (检测+补跑+告警) 17:00"
        RepetitionInterval = $null
        RepetitionDuration = $null
    }
)

# ============================================================
# 旧任务名清单（卸载时清理）
# ============================================================
$legacyTaskNames = @(
    "v75_DailyPnlReport",
    "v75_TradePreMarket",
    "v75_TradePostMarket",
    "v86_EOD_Report",
    "V84_DailyMorningWorkflow",
    "QuantEODWorkflow",
    "QuantUniverseDailyScan",
    "Quant_LLM_IntradayDecision",
    "v84_PreMarketDaily"
)

# ============================================================
# COM 连接
# ============================================================
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  28量化交易系统 v8.6.14 - 统一计划任务注册" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Project Root : $projectRoot"
Write-Host "  Python       : $pythonExe"
Write-Host "  Mode         : $(if ($Uninstall) { 'UNINSTALL' } elseif ($DryRun) { 'DRY-RUN' } else { 'REGISTER' })"
Write-Host ""

if ($DryRun) {
    Write-Host "[DRY-RUN] 将执行以下操作（不实际修改）：" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  1. 删除旧任务：" $legacyTaskNames -Separator ", "
    Write-Host ""
    Write-Host "  2. 注册新任务（共 $($tasks.Count) 个）：" -ForegroundColor Green
    foreach ($t in $tasks) {
        $scriptPath = Join-Path $projectRoot $t.Script
        $exists = Test-Path $scriptPath
        $existsStr = if ($exists) { "[OK]" } else { "[MISSING]" }
        Write-Host "    $($t.Name) ($($t.StartTime)) $existsStr $($t.Script) $($t.Args)"
    }
    Write-Host ""
    exit 0
}

# 连接 Task Scheduler (COM)
try {
    $service = New-Object -ComObject Schedule.Service
    $service.Connect()
    $rootFolder = $service.GetFolder("\")
} catch {
    Write-Host "[ERROR] 无法连接 Task Scheduler COM 对象: $_" -ForegroundColor Red
    exit 1
}

# ============================================================
# 辅助函数：删除已存在的任务
# ============================================================
function Remove-TaskIfExists {
    param([string]$name)
    try {
        $task = $rootFolder.GetTask($name)
        if ($task) {
            $rootFolder.DeleteTask($name, 0)
            Write-Host "  [DEL] 已删除旧任务: $name" -ForegroundColor Yellow
        }
    } catch {
        # 任务不存在，忽略
    }
}

# ============================================================
# 辅助函数：创建任务定义
# ============================================================
function New-TaskDef {
    param([string]$desc, [string]$timeout = "PT30M")

    $td = $service.NewTask(0)
    $td.RegistrationInfo.Description = $desc
    $td.RegistrationInfo.Author = "v8.6.14-unified"
    $td.Settings.Enabled = $true
    $td.Settings.AllowDemandStart = $true
    $td.Settings.StartWhenAvailable = $true       # 错过时间后补跑
    $td.Settings.StopIfGoingOnBatteries = $false
    $td.Settings.DisallowStartIfOnBatteries = $false
    $td.Settings.ExecutionTimeLimit = $timeout
    $td.Settings.MultipleInstances = 2  # TASK_INSTANCES_IGNORE_NEW
    return $td
}

# ============================================================
# 卸载模式：删除所有 v84_/v75_/v86_/Quant* 任务
# ============================================================
if ($Uninstall) {
    Write-Host "[UNINSTALL] 开始卸载所有 v84_/v75_/v86_/Quant* 任务..." -ForegroundColor Yellow
    Write-Host ""

    # 删除统一任务
    foreach ($t in $tasks) {
        Remove-TaskIfExists $t.Name
    }

    # 删除旧任务名
    foreach ($name in $legacyTaskNames) {
        Remove-TaskIfExists $name
    }

    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "  卸载完成！" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host ""
    exit 0
}

# ============================================================
# 注册模式：预检查所有脚本是否存在
# ============================================================
Write-Host "[CHECK] 预检查脚本文件存在性..." -ForegroundColor Cyan
$missingScripts = @()
foreach ($t in $tasks) {
    $scriptPath = Join-Path $projectRoot $t.Script
    if (-not (Test-Path $scriptPath)) {
        $missingScripts += "$($t.Name) -> $scriptPath"
    }
}

if ($missingScripts.Count -gt 0) {
    Write-Host ""
    Write-Host "[ERROR] 以下脚本文件不存在，无法注册：" -ForegroundColor Red
    foreach ($missing in $missingScripts) {
        Write-Host "  - $missing" -ForegroundColor Red
    }
    Write-Host ""
    Write-Host "请检查脚本路径或创建缺失的脚本后再运行此注册脚本。" -ForegroundColor Yellow
    exit 1
}

Write-Host "  所有脚本文件检查通过" -ForegroundColor Green
Write-Host ""

# ============================================================
# 注册模式：先删除旧任务（避免任务名冲突）
# ============================================================
Write-Host "[CLEANUP] 删除旧任务（避免名称冲突）..." -ForegroundColor Cyan
foreach ($name in $legacyTaskNames) {
    Remove-TaskIfExists $name
}
# 同时删除已存在的同名任务（重新注册时覆盖）
foreach ($t in $tasks) {
    Remove-TaskIfExists $t.Name
}
Write-Host ""

# ============================================================
# 注册模式：注册 9 个统一任务
# ============================================================
Write-Host "[REGISTER] 注册 $($tasks.Count) 个统一任务..." -ForegroundColor Cyan
Write-Host ""

$successCount = 0
$failCount = 0

foreach ($t in $tasks) {
    try {
        Write-Host "  [$($successCount + $failCount + 1)/$($tasks.Count)] $($t.Name)..." -NoNewline

        $scriptPath = Join-Path $projectRoot $t.Script
        $td = New-TaskDef $t.Desc $t.Timeout

        # 触发器：每周一至五
        $trigger = $td.Triggers.Create(3)  # 3 = TASK_TRIGGER_WEEKLY
        $trigger.WeeksInterval = 1
        $trigger.DaysOfWeek = 0x3E  # Mon=2 + Tue=4 + Wed=8 + Thu=16 + Fri=32
        $trigger.StartBoundary = "2026-07-31T$($t.StartTime):00"
        $trigger.Enabled = $true

        # 重复触发（仅 IntradayLLMDecision）
        if ($t.RepetitionInterval) {
            $trigger.Repetition.Interval = $t.RepetitionInterval
            $trigger.Repetition.Duration = $t.RepetitionDuration
        }

        # 动作：直接调用 python.exe + .py 文件
        $action = $td.Actions.Create(0)  # 0 = TASK_ACTION_EXEC
        $action.Path = $pythonExe
        if ($t.Args -and $t.Args.Trim() -ne "") {
            $action.Arguments = "`"$scriptPath`" $($t.Args)"
        } else {
            $action.Arguments = "`"$scriptPath`""
        }
        $action.WorkingDirectory = $projectRoot

        # 注册任务（SYSTEM 身份，LogonType=5 = TASK_LOGON_SERVICE_ACCOUNT）
        $rootFolder.RegisterTaskDefinition(
            $t.Name,
            $td,
            6,   # TASK_CREATE_OR_UPDATE
            "SYSTEM",
            $null,
            5    # TASK_LOGON_SERVICE_ACCOUNT
        ) | Out-Null

        Write-Host " OK" -ForegroundColor Green
        $successCount++
    } catch {
        Write-Host " FAIL: $_" -ForegroundColor Red
        $failCount++
    }
}

# ============================================================
# 注册结果汇总
# ============================================================
Write-Host ""
Write-Host "============================================================" -ForegroundColor $(if ($failCount -eq 0) { 'Green' } else { 'Yellow' })
Write-Host "  注册完成！" -ForegroundColor $(if ($failCount -eq 0) { 'Green' } else { 'Yellow' })
Write-Host "============================================================" -ForegroundColor $(if ($failCount -eq 0) { 'Green' } else { 'Yellow' })
Write-Host "  成功: $successCount / $($tasks.Count)" -ForegroundColor Green
if ($failCount -gt 0) {
    Write-Host "  失败: $failCount / $($tasks.Count)" -ForegroundColor Red
}
Write-Host ""
Write-Host "  任务清单：" -ForegroundColor Cyan
foreach ($t in $tasks) {
    Write-Host "    $($t.Name) ($($t.StartTime)) - $($t.Desc)"
}
Write-Host ""
Write-Host "  管理命令：" -ForegroundColor Yellow
Write-Host "    查询所有  : schtasks /Query /FO TABLE | findstr /R `"v84_`""
Write-Host "    立即运行  : schtasks /Run /TN <任务名>"
Write-Host "    禁用任务  : schtasks /Change /TN <任务名> /DISABLE"
Write-Host "    卸载全部  : powershell -File `"$PSCommandPath`" -Uninstall"
Write-Host "    图形界面  : taskschd.msc"
Write-Host ""

if ($failCount -gt 0) {
    exit 1
}
exit 0
