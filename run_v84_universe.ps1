# v8.6.14 Full-Market Auto Stock Selection Launcher (UTF-8 BOM, SYSTEM safe)
# Python 3.8 required (vibe_trading_adapter)
Set-Location "E:\各种PY程序\28-终极量化交易系统8.4"
$logFile = Join-Path "E:\各种PY程序\28-终极量化交易系统8.4\logs" ("universe_scan_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")
& py -3.8 "research\run_universe_scan.py" --pool hs300_zz500 *>&1 | Tee-Object -FilePath $logFile
exit $LASTEXITCODE