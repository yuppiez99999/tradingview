# ============================================================
# Shadow EOD 计划任务加固注册脚本 (0xC000013A 修复)
# ============================================================
# 背景 (2026-09-07 诊断):
#   Shadow30Day_EOD / S12_Shadow_EOD / GNN_S6_Paper_EOD 以
#   InteractiveToken + 裸 python.exe 注册, 定时触发(16:30/16:35/16:50)
#   返回 0xC000013A (STATUS_CONTROL_C_EXIT) —— 无交互会话场景下
#   控制台进程收到 CTRL_CLOSE 被杀; 人工 schtasks /Run 补跑成功 (Result 0).
#
# 加固方案 (对齐 register_all_tasks_unified.ps1 的 v84 先例):
#   - 身份: Administrator(InteractiveToken) -> SYSTEM (LogonType=5)
#   - 动作: 裸 python.exe -> powershell.exe -WindowStyle Hidden 调 wrapper
#     scripts\run_cron_python_wrapper.ps1 (字节级 stdout/stderr 落盘
#     logs\task_logs\<Tag>_<ts>.log, 传递 python exit code)
#   - 保留: 原周历触发器(Mon-Fri) / StartBoundary / MultipleInstances=IgnoreNew
#   - 移除: RestartOnFailure (0xC000013A 会触发 3x5min 无谓重试; SYSTEM
#     模式不再需要); 电池项对齐 v84 设为 false; ExecutionTimeLimit 显式 2h
#
# 用法:
#   powershell -ExecutionPolicy Bypass -File "scripts\register_shadow_eod_tasks_fix.ps1"           # 注册/覆盖
#   powershell -ExecutionPolicy Bypass -File "scripts\register_shadow_eod_tasks_fix.ps1" -DryRun    # 预览
# ============================================================

param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$projectRoot = "E:\各种PY程序\28-终极量化交易系统8.4"
$logDir      = Join-Path $projectRoot "logs"
$taskLogDir  = Join-Path $logDir "task_logs"
$powerShellExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$wrapperPs1  = Join-Path $projectRoot "scripts\run_cron_python_wrapper.ps1"

if (-not (Test-Path $taskLogDir)) {
    New-Item -ItemType Directory -Path $taskLogDir -Force | Out-Null
}

# 三项任务: Name / StartBoundary / 相对项目根的脚本 / wrapper 参数 / 描述
$tasks = @(
    @{
        Name      = "Shadow30Day_EOD"
        Start     = "2026-09-04T16:35:00"
        ScriptRel = "scripts\launch_shadow_30day.py"
        PyArgs    = ""
        Desc      = "W7.2.8/W7.2.9 shadow 30 天验证每日运行器 (MVSK P5-2 + qlib_lgb_v2), 窗口 09-13~10-12, 交易日 16:35 [SYSTEM+wrapper 加固 2026-09-07]"
    },
    @{
        Name      = "S12_Shadow_EOD"
        Start     = "2026-09-02T16:30:00"
        ScriptRel = "scripts\run_s12_shadow.py"
        PyArgs    = ""
        Desc      = "S12 纯防御风险平价影子账户每日 EOD 16:30 [SYSTEM+wrapper 加固 2026-09-07]"
    },
    @{
        Name      = "GNN_S6_Paper_EOD"
        Start     = "2026-09-04T16:50:00"
        ScriptRel = "scripts\s6_paper_trading_runner.py"
        PyArgs    = "--run"
        Desc      = "Wave 5 S6 GNN CHAIN_MOM_60D 纸交易跟踪, 交易日 16:50 [SYSTEM+wrapper 加固 2026-09-07]"
    }
)

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Shadow EOD 计划任务加固 (0xC000013A -> SYSTEM + wrapper)" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  PowerShell : $powerShellExe"
Write-Host "  Wrapper    : $wrapperPs1"
Write-Host "  Log dir    : $taskLogDir"
Write-Host "  Mode       : $(if ($DryRun) { 'DRY-RUN' } else { 'REGISTER' })"
Write-Host ""

# 预检查 wrapper 与目标脚本存在
$missing = @()
foreach ($t in $tasks) {
    $sp = Join-Path $projectRoot $t.ScriptRel
    if (-not (Test-Path $sp)) { $missing += "$($t.Name) -> $sp" }
}
if (-not (Test-Path $wrapperPs1)) { $missing += "wrapper -> $wrapperPs1" }
if ($missing.Count -gt 0) {
    Write-Host "[ERROR] 以下路径缺失, 中止:" -ForegroundColor Red
    foreach ($m in $missing) { Write-Host "  - $m" -ForegroundColor Red }
    exit 1
}

if ($DryRun) {
    foreach ($t in $tasks) {
        Write-Host "  [OK] $($t.Name)  Start=$($t.Start)  PyArgs='$($t.PyArgs)'"
    }
    exit 0
}

# 连接 Task Scheduler
try {
    $service = New-Object -ComObject Schedule.Service
    $service.Connect()
    $rootFolder = $service.GetFolder("\")
} catch {
    Write-Host "[ERROR] 无法连接 Task Scheduler: $_" -ForegroundColor Red
    exit 1
}

$successCount = 0
foreach ($t in $tasks) {
    try {
        Write-Host "  [$($t.Name)] ..." -NoNewline

        $td = $service.NewTask(0)
        $td.RegistrationInfo.Description = $t.Desc
        $td.RegistrationInfo.Author = "shadow-eod-harden-20260907"
        $td.Settings.Enabled = $true
        $td.Settings.AllowDemandStart = $true
        $td.Settings.StartWhenAvailable = $true       # 错过补跑
        $td.Settings.StopIfGoingOnBatteries = $false
        $td.Settings.DisallowStartIfOnBatteries = $false
        $td.Settings.ExecutionTimeLimit = "PT2H"      # 显式限时
        $td.Settings.MultipleInstances = 2            # TASK_INSTANCES_IGNORE_NEW
        # 不设置 RestartCount/RestartInterval => 默认 RestartOnFailure 关闭
        # (0xC000013A 会触发 3x5min 无谓重试, SYSTEM 模式不需要)

        # 触发器: 每周一至五 (0x3E), 保留原 StartBoundary
        $trigger = $td.Triggers.Create(3)  # 3 = TASK_TRIGGER_WEEKLY
        $trigger.WeeksInterval = 1
        $trigger.DaysOfWeek = 0x3E  # Mon..Fri
        $trigger.StartBoundary = $t.Start
        $trigger.Enabled = $true

        # 动作: powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File wrapper.ps1 ...
        $action = $td.Actions.Create(0)  # TASK_ACTION_EXEC
        $action.Path = $powerShellExe
        $psArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$wrapperPs1`" -Script `"$($t.ScriptRel)`" -Tag `"$($t.Name)`""
        if ($t.PyArgs -ne "") {
            $psArgs += " -PyArgs `"$($t.PyArgs)`""
        }
        $action.Arguments = $psArgs
        $action.WorkingDirectory = $projectRoot

        # 注册: SYSTEM + LogonType=5
        $rootFolder.RegisterTaskDefinition(
            $t.Name,
            $td,
            6,          # TASK_CREATE_OR_UPDATE
            "SYSTEM",
            $null,
            5           # TASK_LOGON_SERVICE_ACCOUNT
        ) | Out-Null

        Write-Host " OK" -ForegroundColor Green
        $successCount++
    } catch {
        Write-Host " FAIL: $_" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "  完成: $successCount / $($tasks.Count)" -ForegroundColor $(if ($successCount -eq $tasks.Count) { 'Green' } else { 'Yellow' })
Write-Host ""
Write-Host "  验证命令:" -ForegroundColor Yellow
Write-Host "    schtasks /Query /TN Shadow30Day_EOD /V /FO LIST"
Write-Host "    schtasks /Run /TN Shadow30Day_EOD   (手动触发冒烟)"
Write-Host "    日志: logs\task_logs\*.log"
Write-Host ""

if ($successCount -ne $tasks.Count) { exit 1 }
exit 0
