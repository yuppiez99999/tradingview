# 注册每日早8点日报+盘前交易计划定时任务
# 功能: 每日 08:00 执行完整早间工作流
#   info(7项报告) → calibrate(盘前校准) → plan(交易计划) → LLM决策 → report(综合报告) → 归档
# 非交易日: 自动只跑 info 阶段 (信息采集), 跳过决策类阶段
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$BatchFile = Join-Path $ScriptDir "run_daily_morning8.bat"
$LogDir = Join-Path $ProjectRoot "logs"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

$TaskName = "V84_DailyMorning8Report"
$TaskDesc = "v8.6.14 每日早8点日报+盘前交易计划 (info→calibrate→plan→LLM→report)"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  每日早8点日报+盘前交易计划 定时任务注册" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "项目根目录: $ProjectRoot"
Write-Host "批处理文件: $BatchFile"
Write-Host "任务名称:   $TaskName"
Write-Host "执行时间:   每日 08:00"
Write-Host "流程:       info(7项) → calibrate → plan → LLM → report → 归档"
Write-Host "非交易日:   仅运行 info (信息采集), 跳过决策类阶段"
Write-Host ""

if (-not (Test-Path $BatchFile)) {
    Write-Host "[ERROR] 批处理文件不存在: $BatchFile" -ForegroundColor Red
    exit 1
}

# 触发器: 每日 08:00
$trigger = New-ScheduledTaskTrigger -Daily -At 08:00
$logFile = Join-Path $LogDir "morning8_workflow.log"
$argStr = "/c `"$BatchFile`" >> `"$logFile`" 2>&1"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $argStr -WorkingDirectory $ProjectRoot

# 设置: 电池供电也运行 / 错过自动补跑 / 失败重试3次(间隔5分钟) / 超时2小时 / 不重复启动
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew

# 删除旧任务 (如果存在)
try {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "删除旧任务..." -ForegroundColor Yellow
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    }
} catch {}

# 注册新任务
Write-Host "注册定时任务..." -ForegroundColor Cyan

try {
    # 以当前用户身份运行 (无需登录时也执行)
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Trigger $trigger `
        -Action $action `
        -Settings $settings `
        -Principal $principal `
        -Description $TaskDesc `
        -Force -ErrorAction Stop | Out-Null

    Write-Host ""
    Write-Host "[OK] 定时任务注册成功!" -ForegroundColor Green
    $task = Get-ScheduledTask -TaskName $TaskName
    $info = $task | Get-ScheduledTaskInfo
    Write-Host "状态:     $($task.State)" -ForegroundColor Green
    Write-Host "下次运行: $($info.NextRunTime)" -ForegroundColor Green
    Write-Host "日志文件: $logFile" -ForegroundColor Green
} catch {
    Write-Host "[失败] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

# 显示当前所有量化相关定时任务
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "当前量化相关定时任务:" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Get-ScheduledTask | Where-Object {
    $_.TaskName -like "*v8*" -or
    $_.TaskName -like "*morning*" -or
    $_.TaskName -like "*Daily*" -or
    $_.TaskName -like "*EOD*" -or
    $_.TaskName -like "*Retrain*"
} | ForEach-Object {
    $info = $_ | Get-ScheduledTaskInfo
    $time = if ($info.NextRunTime) { $info.NextRunTime.ToString("yyyy-MM-dd HH:mm") } else { "N/A" }
    Write-Host "  $($_.TaskName.PadRight(35)) | $($_.State.PadRight(8)) | 下次: $time"
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  完成! 每日 08:00 将自动执行:" -ForegroundColor Green
Write-Host "  1. 晨间信息采集 (7项报告)" -ForegroundColor Green
Write-Host "  2. 盘前市场校准 + 风险评估" -ForegroundColor Green
Write-Host "  3. 生成交易计划" -ForegroundColor Green
Write-Host "  4. LLM 决策注入" -ForegroundColor Green
Write-Host "  5. 综合报告 + 归档" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
