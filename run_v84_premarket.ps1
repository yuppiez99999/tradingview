# v8.6.14 Pre-Market Launcher (UTF-8 BOM, SYSTEM safe)
# Directly calls Python entry, bypasses .bat encoding issues
Set-Location "E:\各种PY程序\28-终极量化交易系统8.4"
$logFile = Join-Path "E:\各种PY程序\28-终极量化交易系统8.4\logs" ("premarket_" + (Get-Date -Format "yyyyMMdd") + ".log")
& py -3.8 "15_每日工作流\run_daily_morning.py" --phase all *>&1 | Tee-Object -FilePath $logFile
exit $LASTEXITCODE