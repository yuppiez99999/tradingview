@echo off
chcp 65001 >nul
set PYTHON=C:\Program Files\Python38\python.exe
set SCRIPT=E:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional\llm_intraday_decision_engine.py
set MODE=live

echo [%date% %time%] 启动LLM盘中决策引擎... >> "E:\各种PY程序\28-终极量化交易系统7.1\logs\intraday_decision.log"
"%PYTHON%" "%SCRIPT%" --mode %MODE% >> "E:\各种PY程序\28-终极量化交易系统7.1\logs\intraday_decision.log" 2>&1
