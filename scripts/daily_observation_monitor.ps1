# ============================================================
# 每日观察期监控脚本 (每日 16:00 运行)
# 用法: powershell -ExecutionPolicy Bypass -File scripts\daily_observation_monitor.ps1
# ============================================================

$ErrorActionPreference = "Continue"
$projectRoot = "e:\各种PY程序\28-终极量化交易系统8.4"
$junctionRoot = "C:\QuantSys"

# 切换到 UTF-8 编码 (避免中文乱码)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "============================================================"
Write-Host "每日观察期监控 - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host "============================================================"
Write-Host ""

# ============================================================
# 1. 检查 EOD 任务执行状态
# ============================================================
Write-Host "[1/6] 检查 EOD 任务执行状态..."
try {
    $eodTask = schtasks /query /tn "QuantEOD_1530" /v /fo LIST 2>&1
    $lastResult = ($eodTask | Select-String "Last Result").ToString().Split(":")[1].Trim()
    $lastRun = ($eodTask | Select-String "Last Run Time").ToString().Split(":", 2)[1].Trim()
    $nextRun = ($eodTask | Select-String "Next Run Time").ToString().Split(":", 2)[1].Trim()

    if ($lastResult -eq "0") {
        Write-Host "  [OK] EOD 任务成功 (Last Result=0)"
        Write-Host "       上次运行: $lastRun"
        Write-Host "       下次运行: $nextRun"
    } else {
        Write-Host "  [FAIL] EOD 任务失败 (Last Result=$lastResult)"
        Write-Host "         上次运行: $lastRun"
        Write-Host "         应急: 手动运行 C:\QuantSys\run_workflow_task.bat eod"
    }
} catch {
    Write-Host "  [WARN] 无法查询 EOD 任务状态: $_"
}
Write-Host ""

# ============================================================
# 2. 检查 EOD 日志
# ============================================================
Write-Host "[2/6] 检查 EOD 日志..."
$eodLog = "$junctionRoot\logs\workflow_eod_latest.log"
if (Test-Path $eodLog) {
    $logContent = Get-Content $eodLog -Tail 30
    $logDate = ($logContent | Select-String "交易日:").ToString().Split(":")[1].Trim()
    $phase10Ok = $logContent | Select-String "Phase 10 完成"
    $jsonlOk = $logContent | Select-String "jsonl"

    if ($phase10Ok -and $jsonlOk) {
        Write-Host "  [OK] EOD 日志正常 (交易日=$logDate)"
        Write-Host "       Phase 10 完成: 是"
        Write-Host "       JSONL 写入: 是"
    } else {
        Write-Host "  [WARN] EOD 日志可能异常 (交易日=$logDate)"
        Write-Host "         Phase 10 完成: $([bool]$phase10Ok)"
        Write-Host "         JSONL 写入: $([bool]$jsonlOk)"
    }
} else {
    Write-Host "  [WARN] EOD 日志文件不存在: $eodLog"
}
Write-Host ""

# ============================================================
# 3. 检查 daily_returns.jsonl 数据累积
# ============================================================
Write-Host "[3/6] 检查 daily_returns.jsonl 数据累积..."
$jsonlPath = "$projectRoot\reports\shadow\daily_returns.jsonl"
if (Test-Path $jsonlPath) {
    $lines = Get-Content $jsonlPath
    $lineCount = $lines.Count
    $lastLine = $lines[-1] | ConvertFrom-Json
    $lastDate = $lastLine.date
    $lastReturn = $lastLine.daily_return

    Write-Host "  [OK] daily_returns.jsonl 存在"
    Write-Host "       总记录数: $lineCount"
    Write-Host "       最新日期: $lastDate"
    Write-Host "       最新收益: $lastReturn"

    if ($lastReturn -eq 0) {
        Write-Host "  [WARN] 最新 daily_return = 0, 可能 target_weights 为空"
    }
} else {
    Write-Host "  [WARN] daily_returns.jsonl 不存在"
}
Write-Host ""

# ============================================================
# 4. 检查 shadow_state.json 状态健康
# ============================================================
Write-Host "[4/6] 检查 shadow_state.json 状态..."
$statePath = "$projectRoot\output\shadow_account\shadow_state.json"
if (Test-Path $statePath) {
    $state = Get-Content $statePath -Raw | ConvertFrom-Json
    $status = $state.status
    $currentNav = $state.current_nav
    $dailyNavCount = $state.daily_nav.Count

    Write-Host "  [OK] shadow_state.json 存在"
    Write-Host "       状态: $status"
    Write-Host "       当前净值: $currentNav"
    Write-Host "       daily_nav 记录数: $dailyNavCount"

    if ($status -ne "RUNNING") {
        Write-Host "  [FAIL] 影子账户状态异常: $status"
        if ($state.fail_fast_log) {
            Write-Host "         Fail-Fast 日志: $($state.fail_fast_log | ConvertTo-Json -Depth 5)"
        }
    }
} else {
    Write-Host "  [WARN] shadow_state.json 不存在"
}
Write-Host ""

# ============================================================
# 5. 检查 DSR 报告生成
# ============================================================
Write-Host "[5/6] 检查 DSR 报告生成..."
$dsrDir = "$projectRoot\reports\shadow"
$today = Get-Date -Format "yyyy-MM-dd"
$todayDsr = Get-ChildItem $dsrDir -Filter "${today}_dsr.json" -ErrorAction SilentlyContinue

if ($todayDsr) {
    $dsrContent = Get-Content $todayDsr.FullName -Raw | ConvertFrom-Json
    $isRealData = $dsrContent.is_real_data
    $daysTracked = $dsrContent.days_tracked
    $failFast = $dsrContent.fail_fast_triggered

    Write-Host "  [OK] 当日 DSR 报告存在: $($todayDsr.Name)"
    Write-Host "       is_real_data: $isRealData"
    Write-Host "       days_tracked: $daysTracked"
    Write-Host "       fail_fast_triggered: $failFast"
} else {
    Write-Host "  [WARN] 当日 DSR 报告不存在: ${today}_dsr.json"
    Write-Host "         手动生成: python scripts\shadow_admission_launcher.py daily"
}
Write-Host ""

# ============================================================
# 6. 检查 P3 观察期状态
# ============================================================
Write-Host "[6/6] 检查 P3 观察期状态..."
$p3StatePath = "$projectRoot\reports\shadow_p3\admission_state.json"
if (Test-Path $p3StatePath) {
    $p3State = Get-Content $p3StatePath -Raw | ConvertFrom-Json
    $p3Days = $p3State.observation_days
    $p3Target = $p3State.observation_period_days
    $p3FailFast = $p3State.fail_fast_triggered

    Write-Host "  [OK] P3 观察期状态存在"
    Write-Host "       观察期: $p3Days / $p3Target 天"
    Write-Host "       fail_fast_triggered: $p3FailFast"

    if ($p3FailFast) {
        Write-Host "  [FAIL] P3 Fail-Fast 已触发!"
    }
} else {
    Write-Host "  [WARN] P3 观察期状态不存在"
}
Write-Host ""

# ============================================================
# 总结
# ============================================================
Write-Host "============================================================"
Write-Host "监控总结 - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host "============================================================"

# 计算观察期进度
$t24StatePath = "$projectRoot\reports\shadow\admission_state.json"
if (Test-Path $t24StatePath) {
    $t24State = Get-Content $t24StatePath -Raw | ConvertFrom-Json
    $t24Days = $t24State.observation_days
    $t24Target = $t24State.observation_period_days
    $t24Progress = [math]::Round(($t24Days / $t24Target) * 100, 1)
    Write-Host "T2.4 观察期: $t24Days / $t24Target 天 ($t24Progress%)"
}
if (Test-Path $p3StatePath) {
    $p3Days = $p3State.observation_days
    $p3Target = $p3State.observation_period_days
    $p3Progress = [math]::Round(($p3Days / $p3Target) * 100, 1)
    Write-Host "P3 观察期:   $p3Days / $p3Target 天 ($p3Progress%)"
}
Write-Host ""
Write-Host "预计 Stage 2 评估日期: 2026-08-10"
Write-Host "============================================================"
