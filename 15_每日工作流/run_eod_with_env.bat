@echo off
REM EOD 工作流 wrapper - 补齐 SYSTEM 用户缺失的 PYTHONPATH
REM 根因: urllib3 装在 Administrator user site, wind_mcp_fetcher 在 tools/
setlocal
set "PYTHONPATH=C:\Users\Administrator\AppData\Roaming\Python\Python311\site-packages;E:\各种PY程序\28-终极量化交易系统8.4\tools;E:\各种PY程序\28-终极量化交易系统8.4\src;%PYTHONPATH%"
cd /d "E:\各种PY程序\28-终极量化交易系统8.4"
"C:\Users\Administrator\py311\python.exe" "E:\各种PY程序\28-终极量化交易系统8.4\15_每日工作流\run_daily_eod_workflow.py" --skip-system-check %*
endlocal