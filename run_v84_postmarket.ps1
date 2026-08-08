# v8.6.14 Post-Market Launcher (UTF-8 BOM, SYSTEM safe)
# EOD workflow + HedgeCompletenessGate + ReturnExpectationGate
Set-Location "E:\各种PY程序\28-终极量化交易系统8.4"
# G8 修复: 限制 OpenBLAS/OMP/MKL 线程数, 避免 EOD 工作流在低内存环境 (可用内存 < 2GB) 下
# 线程栈内存分配失败 ("Memory allocation still failed after 10 retries") 导致 6/10 阶段崩溃。
# 08-07 首次发现并验证: 设置后 EOD 由 6/10 成功升至 8/10。
$env:OPENBLAS_NUM_THREADS = "1"
$env:OMP_NUM_THREADS = "1"
$env:MKL_NUM_THREADS = "1"
$logFile = Join-Path "E:\各种PY程序\28-终极量化交易系统8.4\logs" ("postmarket_" + (Get-Date -Format "yyyyMMdd") + ".log")
& py -3.8 "15_每日工作流\run_daily_eod_workflow.py" *>&1 | Tee-Object -FilePath $logFile
exit $LASTEXITCODE