# v8.4 Post-Market Launcher (UTF-8 BOM, SYSTEM safe)
# EOD workflow + HedgeCompletenessGate + ReturnExpectationGate
Set-Location "E:\各种PY程序\28-终极量化交易系统8.4"
$logFile = Join-Path "E:\各种PY程序\28-终极量化交易系统8.4\logs" ("postmarket_" + (Get-Date -Format "yyyyMMdd") + ".log")
& py -3.8 "15_每日工作流\run_daily_eod_workflow.py" *>&1 | Tee-Object -FilePath $logFile
exit $LASTEXITCODE