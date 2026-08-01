@echo off
chcp 65001 >nul
echo ========================================
echo   盘中决策 - LLM Intraday Decision
echo ========================================
echo.

set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe
set WORKDIR=%~dp0
set LOG=%WORKDIR%logs\intraday_decision.log

echo [%date% %time%] 开始盘中决策... >> "%LOG%"
echo 时间: %date% %time%
echo 执行: %PYTHON% "%WORKDIR%v8.3_institutional\llm_intraday_decision_engine.py" --mode live
echo.

"%PYTHON%" "%WORKDIR%v8.3_institutional\llm_intraday_decision_engine.py" --mode live >> "%LOG%" 2>&1

echo.
echo 完成时间: %date% %time%
echo 日志: %LOG%
echo.
echo [%date% %time%] 盘中决策完成 >> "%LOG%"
