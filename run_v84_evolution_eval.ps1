# v8.6.14 Evolution Eval Launcher (UTF-8 BOM, SYSTEM safe)
# 自我进化编排器评估入口 (观察期只读模式, HC-1/HC-4 合规)
# 时序: 15:30 PostMarket -> 16:00 PnLReport -> 16:05 EvolutionEval
Set-Location "E:\各种PY程序\28-终极量化交易系统8.4"
$logFile = Join-Path "E:\各种PY程序\28-终极量化交易系统8.4\logs" ("evolution_eval_" + (Get-Date -Format "yyyyMMdd") + ".log")
& py -3.8 "scripts\run_evolution_eval.py" *>&1 | Tee-Object -FilePath $logFile
exit $LASTEXITCODE
