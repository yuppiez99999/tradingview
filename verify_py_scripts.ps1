[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$qdir = "e:\各种PY程序\11_量化策略"
$scripts = @(
    "morning_workflow.py",
    "premarket_report.py",
    "auto_premarket_plan.py",
    "auto_intraday_decision.py",
    "daily_close_report.py"
)
foreach ($s in $scripts) {
    $p = Join-Path $qdir $s
    $exists = Test-Path -LiteralPath $p
    Write-Host ("{0,-32} exists={1}" -f $s, $exists)
}
