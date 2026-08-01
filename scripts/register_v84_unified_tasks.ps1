# DEPRECATED: 已被 scripts\register_all_tasks_unified.ps1 替代，请勿执行
# 迁移日期: 2026-07-30
# 原因: 该脚本使用 py -3.8 启动器（SYSTEM 用户下不可用），已统一为 Python 3.11 绝对路径
# ============================================================
# v8.4 统一任务调度注册脚本 (v2: 修复中文路径乱码)
#
# 修复:
#   - 所有任务用 PowerShell 包装, 在内部 Set-Location 后用相对路径调用
#   - 避免中文路径在 cmd.exe /c 传递时变成乱码
#   - 统一 SYSTEM 身份 + Background 模式
#   - 强制 py -3.8 (vibe_trading_adapter 需要)
#
# 使用方法 (需管理员权限):
#   powershell -ExecutionPolicy Bypass -File scripts/register_v84_unified_tasks.ps1
#   powershell -ExecutionPolicy Bypass -File scripts/register_v84_unified_tasks.ps1 -Uninstall
#   powershell -ExecutionPolicy Bypass -File scripts/register_v84_unified_tasks.ps1 -DryRun
# ============================================================

param(
    [switch]$Uninstall,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$LogDir = Join-Path $RepoRoot "logs"

# 检查管理员权限
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin -and -not $DryRun) {
    Write-Error "需要管理员权限运行此脚本"
    exit 1
}

# v8.4 标准任务集 (用 PowerShell 包装避免中文路径乱码)
# 关键: 在 -Command 中先用 Set-Location 切到项目根目录, 然后用相对路径
$tasks = @(
    @{
        Name = "v84_PreMarket"
        Time = "07:00"
        Desc = "v8.4 Pre-Market: morning info + trade plan"
        # 调用 .bat (内部已用 py -3)
        Command = "Set-Location '$RepoRoot'; & '.\15_每日工作流\run_daily_morning.bat' *>&1 | Tee-Object -FilePath (Join-Path '$LogDir' ('premarket_' + (Get-Date -Format 'yyyyMMdd') + '.log')); exit `$LASTEXITCODE"
        Timeout = (New-TimeSpan -Hours 2)
    },
    @{
        Name = "v84_PostMarket"
        Time = "15:30"
        Desc = "v8.4 Post-Market: EOD workflow + hedge + gate check"
        Command = "Set-Location '$RepoRoot'; & '.\15_每日工作流\run_eod_workflow.bat' *>&1 | Tee-Object -FilePath (Join-Path '$LogDir' ('postmarket_' + (Get-Date -Format 'yyyyMMdd') + '.log')); exit `$LASTEXITCODE"
        Timeout = (New-TimeSpan -Hours 2)
    },
    @{
        Name = "v84_DailyPnlReport"
        Time = "16:00"
        Desc = "v8.4 Daily PnL Report + Attribution Panel"
        Command = "Set-Location '$RepoRoot'; & '.\run_daily_report.bat' *>&1 | Tee-Object -FilePath (Join-Path '$LogDir' ('pnl_report_' + (Get-Date -Format 'yyyyMMdd') + '.log')); exit `$LASTEXITCODE"
        Timeout = (New-TimeSpan -Minutes 30)
    },
    @{
        Name = "v84_UniverseScan"
        Time = "16:30"
        Desc = "v8.4 Full-market auto stock selection (py -3.8, vibe_trading_adapter)"
        # 强制 py -3.8 (vibe_trading_adapter 需要 Python 3.8)
        Command = "Set-Location '$RepoRoot'; & py -3.8 'research\run_universe_scan.py' --pool hs300_zz500 *>&1 | Tee-Object -FilePath (Join-Path '$LogDir' ('universe_scan_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.log')); exit `$LASTEXITCODE"
        Timeout = (New-TimeSpan -Hours 1)
    }
)

# 需要清理的旧任务名
$legacyTasks = @(
    "V84_DailyMorningWorkflow",
    "QuantUniverseDailyScan",
    "v84_PreMarket",
    "v84_PostMarket",
    "v84_DailyPnlReport",
    "v84_UniverseScan"
)

# ============================================================
# 删除旧任务
# ============================================================
function Remove-LegacyTasks {
    Write-Host "============================================================"
    Write-Host "  Clean up legacy tasks"
    Write-Host "============================================================"
    foreach ($name in $legacyTasks) {
        $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if ($task) {
            Write-Host "  [REMOVE] $name"
            if (-not $DryRun) {
                Unregister-ScheduledTask -TaskName $name -Confirm:$false
            }
        }
    }
}

# ============================================================
# 注册 v8.4 标准任务
# ============================================================
function Register-V84Tasks {
    Write-Host "============================================================"
    Write-Host "  Register v8.4 unified tasks"
    Write-Host "  Spec: SYSTEM account + Background mode + py -3.8"
    Write-Host "============================================================"

    foreach ($task in $tasks) {
        $name = $task.Name
        $time = $task.Time
        $desc = $task.Desc
        $command = $task.Command
        $timeout = $task.Timeout

        Write-Host ""
        Write-Host "  [$name] Time=$time"

        if ($DryRun) {
            Write-Host "    [DRY RUN] Skip"
            Write-Host "    Command: $command"
            continue
        }

        # 构造 Action: PowerShell 包装避免中文路径乱码
        # 注意: 用单引号包裹整个 -Command 内容, 内部用反引号转义 $
        $arg = "-ExecutionPolicy Bypass -NoProfile -Command `"$command`""
        $taskAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg

        # 触发器: 每日定时
        $trigger = New-ScheduledTaskTrigger -Daily -At $time

        # 设置: Background + 失败重试 + 网络可用
        $settings = New-ScheduledTaskSettingsSet `
            -StartWhenAvailable `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -RunOnlyIfNetworkAvailable `
            -MultipleInstances IgnoreNew `
            -ExecutionTimeLimit $timeout `
            -RestartCount 2 `
            -RestartInterval (New-TimeSpan -Minutes 5)

        # 身份: SYSTEM + ServiceAccount (Background)
        $principal = New-ScheduledTaskPrincipal `
            -UserId "NT AUTHORITY\SYSTEM" `
            -RunLevel Highest `
            -LogonType ServiceAccount

        Register-ScheduledTask `
            -TaskName $name `
            -Action $taskAction `
            -Trigger $trigger `
            -Principal $principal `
            -Settings $settings `
            -Description $desc `
            -Force | Out-Null

        Write-Host "    [OK] Registered (SYSTEM/Background)"
    }
}

# ============================================================
# 主流程
# ============================================================
Write-Host ""
Write-Host "============================================================"
Write-Host "  v8.4 Unified Task Scheduler Registration"
Write-Host "  Project Root: $RepoRoot"
Write-Host "  Log Dir:      $LogDir"
Write-Host "============================================================"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    Write-Host "  [CREATE] $LogDir"
}

if ($Uninstall) {
    Remove-LegacyTasks
    Write-Host ""
    Write-Host "  Uninstall complete."
    exit 0
}

Remove-LegacyTasks
Register-V84Tasks

Write-Host ""
Write-Host "============================================================"
Write-Host "  v8.4 Task Set Registered"
Write-Host "============================================================"
Write-Host ""
Write-Host "  Schedule:"
Write-Host "    07:00  v84_PreMarket       Morning info + trade plan"
Write-Host "    15:30  v84_PostMarket      EOD workflow + gate check"
Write-Host "    16:00  v84_DailyPnlReport  PnL report + attribution"
Write-Host "    16:30  v84_UniverseScan    Full-market scan (py -3.8)"
Write-Host ""
Write-Host "  Manual trigger:"
Write-Host "    schtasks /Run /TN 'v84_PreMarket'"
Write-Host "    schtasks /Run /TN 'v84_PostMarket'"
Write-Host "    schtasks /Run /TN 'v84_DailyPnlReport'"
Write-Host "    schtasks /Run /TN 'v84_UniverseScan'"
Write-Host ""
Write-Host "  Uninstall:"
Write-Host "    powershell -File scripts/register_v84_unified_tasks.ps1 -Uninstall"
Write-Host ""
