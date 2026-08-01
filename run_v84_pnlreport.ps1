# v8.4 Daily PnL Report Launcher (UTF-8 BOM, SYSTEM safe)
Set-Location "E:\各种PY程序\28-终极量化交易系统8.4"
$logFile = Join-Path "E:\各种PY程序\28-终极量化交易系统8.4\logs" ("pnl_report_" + (Get-Date -Format "yyyyMMdd") + ".log")
& py -3.8 "generate_daily_report.py" *>&1 | Tee-Object -FilePath $logFile
exit $LASTEXITCODE