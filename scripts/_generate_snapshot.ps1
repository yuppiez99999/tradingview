# Pre-observation snapshot generator (ASCII only for PS 5.x compatibility)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$snapshot = @{}
$snapshot.snapshot_time = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$snapshot.description = "Pre-observation snapshot (2026-07-27 EOD)"

# T2.4 observation state
$t24Path = "C:\QuantSys\reports\shadow\admission_state.json"
if (Test-Path $t24Path) {
    $t24 = Get-Content $t24Path -Raw | ConvertFrom-Json
    $snapshot.t24 = @{}
    $snapshot.t24.observation_days = $t24.observation_days
    $snapshot.t24.observation_period_days = $t24.observation_period_days
    $snapshot.t24.fail_fast_triggered = $t24.fail_fast_triggered
    $snapshot.t24.stage_2_promoted = $t24.stage_2_promoted
    $snapshot.t24.stage_2_blocked_reason = $t24.stage_2_blocked_reason
}

# P3 observation state
$p3Path = "C:\QuantSys\reports\shadow_p3\admission_state.json"
if (Test-Path $p3Path) {
    $p3 = Get-Content $p3Path -Raw | ConvertFrom-Json
    $snapshot.p3 = @{}
    $snapshot.p3.observation_days = $p3.observation_days
    $snapshot.p3.observation_period_days = $p3.observation_period_days
    $snapshot.p3.fail_fast_triggered = $p3.fail_fast_triggered
    $snapshot.p3.stage_2_promoted = $p3.stage_2_promoted
    $snapshot.p3.stage_2_blocked_reason = $p3.stage_2_blocked_reason
}

# Shadow account state
$shadowPath = "C:\QuantSys\output\shadow_account\shadow_state.json"
if (Test-Path $shadowPath) {
    $shadow = Get-Content $shadowPath -Raw | ConvertFrom-Json
    $snapshot.shadow_account = @{}
    $snapshot.shadow_account.status = $shadow.status
    $snapshot.shadow_account.current_nav = $shadow.current_nav
    $snapshot.shadow_account.current_capital = $shadow.current_capital
    $snapshot.shadow_account.daily_nav_count = $shadow.daily_nav.Count
}

# daily_returns.jsonl
$jsonlPath = "C:\QuantSys\reports\shadow\daily_returns.jsonl"
if (Test-Path $jsonlPath) {
    $lines = Get-Content $jsonlPath
    $last = $lines[-1] | ConvertFrom-Json
    $snapshot.daily_returns = @{}
    $snapshot.daily_returns.record_count = $lines.Count
    $snapshot.daily_returns.last_record = $last
}

# P3 functional verification
$verifyPath = "C:\QuantSys\reports\shadow_p3\functional_verify.json"
if (Test-Path $verifyPath) {
    $verify = Get-Content $verifyPath -Raw | ConvertFrom-Json
    $snapshot.p3_functional_verify = @{}
    $snapshot.p3_functional_verify.overall_pass = $verify.overall_pass
    $snapshot.p3_functional_verify.total_tests = $verify.summary.total
    $snapshot.p3_functional_verify.passed = $verify.summary.passed
    $snapshot.p3_functional_verify.failed = $verify.summary.failed
}

# Scheduled tasks
$tasks = @("QuantPipelineFactor_06AM", "QuantWorkflow_07AM", "QuantMorning_0930", "QuantAfternoon_1400", "QuantEOD_1530")
$snapshot.tasks = @{}
foreach ($t in $tasks) {
    $r = schtasks /query /tn $t /v /fo LIST 2>&1
    $st = ($r | Select-String "Status") -replace ".*:\s*", ""
    $nr = ($r | Select-String "Next Run Time") -replace ".*:\s*", ""
    $snapshot.tasks[$t] = @{}
    $snapshot.tasks[$t].status = $st
    $snapshot.tasks[$t].next_run = $nr
}

# Save snapshot
$outPath = "C:\QuantSys\reports\shadow\pre_observation_snapshot.json"
$snapshot | ConvertTo-Json -Depth 10 | Out-File -FilePath $outPath -Encoding utf8
Write-Host "=== Pre-observation Snapshot ==="
Write-Host "Saved to: $outPath"
Write-Host ""
$snapshot | ConvertTo-Json -Depth 10
