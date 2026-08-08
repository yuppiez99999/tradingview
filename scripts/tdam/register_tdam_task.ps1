<#
.SYNOPSIS
  注册 TDAM MemoryCore 的 Windows 任务计划程序 (替代 Windows 服务方案)
.DESCRIPTION
  项目硬约束: "Windows 任务计划程序必须注册 Quant 任务" (见 project_memory)。
  本脚本注册 3 个任务, 实现:
    1. 开机自启 (TDAM_MemoryCore_AutoStart)
    2. 盘中停止 (TDAM_MemoryCore_StopIntraday) — 09:00 释放内存给交易系统
    3. 盘后启动 (TDAM_MemoryCore_StartPostMarket) — 15:30 恢复运行

  优势 (相比 nssm/Windows 服务):
    - 零依赖 (无需安装 nssm/WinSW)
    - 与项目现有 v84_PreMarket/v84_PostMarket 等任务架构一致
    - 支持失败重启 (Task Scheduler 原生能力)
    - SYSTEM 账户运行, 关终端不影响

  需要管理员权限运行本脚本。
.PARAMETER Action
  register   - 注册 3 个任务 (默认)
  unregister - 注销所有 TDAM 任务
  status     - 查看任务状态
.EXAMPLE
  # 以管理员身份运行
  .\register_tdam_task.ps1 -Action register
  .\register_tdam_task.ps1 -Action status
  .\register_tdam_task.ps1 -Action unregister
#>
param(
    [Parameter(Position=0)]
    [ValidateSet("register","unregister","status")]
    [string]$Action = "register"
)

$ErrorActionPreference = "Stop"

# ============================================================
# 路径常量
# ============================================================
$WRAPPER_SCRIPT = "E:\各种PY程序\28-终极量化交易系统8.4\scripts\tdam\tdam_service_wrapper.ps1"
$TASK_PREFIX = "TDAM_MemoryCore"

# ============================================================
# 辅助函数
# ============================================================
function Write-Info  { param([string]$Msg) Write-Host "[INFO]  $Msg" -ForegroundColor Cyan }
function Write-OK    { param([string]$Msg) Write-Host "[OK]    $Msg" -ForegroundColor Green }
function Write-Warn  { param([string]$Msg) Write-Host "[WARN]  $Msg" -ForegroundColor Yellow }
function Write-Err   { param([string]$Msg) Write-Host "[ERROR] $Msg" -ForegroundColor Red }

function Test-Admin {
    $current = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($current)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ============================================================
# 注册任务
# ============================================================
function Register-TDAMTask {
    param(
        [string]$TaskName,
        [string]$Subcommand,    # start / stop
        [string]$TriggerType,   # AtStartup / Daily
        [string]$StartTime = "",  # 仅 Daily 用, 格式 "HH:mm"
        [int]$DelaySeconds = 0    # 触发后延迟秒数
    )

    # 先删除已存在的同名任务
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Info "已删除旧任务: $TaskName"
    }

    # 构造 Action
    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$WRAPPER_SCRIPT`" $Subcommand"

    # 构造 Trigger
    if ($TriggerType -eq "AtStartup") {
        $trigger = New-ScheduledTaskTrigger -AtStartup
        if ($DelaySeconds -gt 0) {
            $trigger.Delay = "PT${DelaySeconds}S"
        }
    } elseif ($TriggerType -eq "Daily") {
        $trigger = New-ScheduledTaskTrigger -Daily -At $StartTime
        if ($DelaySeconds -gt 0) {
            $trigger.Delay = "PT${DelaySeconds}S"
        }
    }

    # 构造 Settings (失败重启: 1分钟后, 最多3次)
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
        -MultipleInstances IgnoreNew

    # Principal (SYSTEM 账户, 最高权限)
    $principal = New-ScheduledTaskPrincipal `
        -UserId "SYSTEM" `
        -LogonType ServiceAccount `
        -RunLevel Highest

    try {
        Register-ScheduledTask `
            -TaskName $TaskName `
            -Action $action `
            -Trigger $trigger `
            -Settings $settings `
            -Principal $principal `
            -Description "TDAM MemoryCore 服务托管 ($Subcommand) - 由 register_tdam_task.ps1 注册" `
            -Force | Out-Null
        Write-OK "任务已注册: $TaskName"
    } catch {
        Write-Err "注册失败 $TaskName : $_"
    }
}

# ============================================================
# Action: register
# ============================================================
function Invoke-Register {
    Write-Host "=== 注册 TDAM MemoryCore 任务计划 ===" -ForegroundColor Cyan

    if (-not (Test-Admin)) {
        Write-Err "需要管理员权限! 请用管理员身份打开 PowerShell 再运行本脚本。"
        Write-Host "  提示: 右键 PowerShell -> '以管理员身份运行'"
        exit 1
    }

    if (-not (Test-Path $WRAPPER_SCRIPT)) {
        Write-Err "包装器脚本不存在: $WRAPPER_SCRIPT"
        exit 1
    }

    Write-Info "包装器: $WRAPPER_SCRIPT"
    Write-Host ""

    # 1. 开机自启 (延迟 30 秒, 避免开机高峰抢资源)
    Write-Host "[1/3] 开机自启任务..." -ForegroundColor Yellow
    Register-TDAMTask `
        -TaskName "${TASK_PREFIX}_AutoStart" `
        -Subcommand "start" `
        -TriggerType "AtStartup" `
        -DelaySeconds 30

    # 2. 盘中停止 (09:00, 释放内存给交易系统; 9:25 开盘前完成停止)
    Write-Host "[2/3] 盘中停止任务..." -ForegroundColor Yellow
    Register-TDAMTask `
        -TaskName "${TASK_PREFIX}_StopIntraday" `
        -Subcommand "stop" `
        -TriggerType "Daily" `
        -StartTime "09:00"

    # 3. 盘后启动 (15:30, 收盘后恢复; 15:10 收盘 + 20 分钟缓冲)
    Write-Host "[3/3] 盘后启动任务..." -ForegroundColor Yellow
    Register-TDAMTask `
        -TaskName "${TASK_PREFIX}_StartPostMarket" `
        -Subcommand "start" `
        -TriggerType "Daily" `
        -StartTime "15:30"

    Write-Host ""
    Write-Host "=== 注册完成 ===" -ForegroundColor Green
    Write-Host "时段化隔离策略:"
    Write-Host "  09:00 - 15:30  TDAM 停止 (盘中内存让位交易系统)"
    Write-Host "  15:30 - 次日 09:00  TDAM 运行 (盘后 + 夜间记忆蒸馏)"
    Write-Host "  开机后 30 秒  TDAM 自启 (若在盘中时段, 09:00 仍会停止)"
    Write-Host ""
    Write-Host "手动操作:"
    Write-Host "  启动:  powershell -File `"$WRAPPER_SCRIPT`" start"
    Write-Host "  停止:  powershell -File `"$WRAPPER_SCRIPT`" stop"
    Write-Host "  状态:  powershell -File `"$WRAPPER_SCRIPT`" status"
    Write-Host "  日志:  powershell -File `"$WRAPPER_SCRIPT`" logs"
}

# ============================================================
# Action: unregister
# ============================================================
function Invoke-Unregister {
    Write-Host "=== 注销 TDAM MemoryCore 任务 ===" -ForegroundColor Cyan

    $tasks = @(
        "${TASK_PREFIX}_AutoStart",
        "${TASK_PREFIX}_StopIntraday",
        "${TASK_PREFIX}_StartPostMarket"
    )

    foreach ($taskName in $tasks) {
        if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
            Write-OK "已注销: $taskName"
        } else {
            Write-Info "不存在: $taskName"
        }
    }
}

# ============================================================
# Action: status
# ============================================================
function Invoke-Status {
    Write-Host "=== TDAM 任务计划状态 ===" -ForegroundColor Cyan
    Write-Host ""

    $tasks = @(
        "${TASK_PREFIX}_AutoStart",
        "${TASK_PREFIX}_StopIntraday",
        "${TASK_PREFIX}_StartPostMarket"
    )

    $format = "{0,-35} {1,-10} {2,-20} {3}"
    Write-Host ($format -f "任务名", "状态", "上次运行时间", "下次运行时间")
    Write-Host ("-" * 90)

    foreach ($taskName in $tasks) {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if (-not $task) {
            Write-Host ($format -f $taskName, "未注册", "-", "-")
            continue
        }

        $info = Get-ScheduledTaskInfo -TaskName $taskName
        $state = $task.State
        $lastRun = if ($info.LastRunTime) { $info.LastRunTime.ToString("MM-dd HH:mm") } else { "-" }
        $nextRun = if ($info.NextRunTime) { $info.NextRunTime.ToString("MM-dd HH:mm") } else { "-" }

        $color = if ($state -eq "Ready") { "Green" } elseif ($state -eq "Running") { "Yellow" } else { "Gray" }
        Write-Host ($format -f $taskName, $state, $lastRun, $nextRun) -ForegroundColor $color
    }
}

# ============================================================
# 主入口
# ============================================================
switch ($Action) {
    "register"   { Invoke-Register }
    "unregister" { Invoke-Unregister }
    "status"     { Invoke-Status }
}
