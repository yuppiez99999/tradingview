@echo off
chcp 65001 >nul
echo ========================================
echo   盘前检查 - AutoHedge PreMarket
echo ========================================
echo.

set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe
set WORKDIR=%~dp0
set LOG=%WORKDIR%logs\pre_market.log

echo [%date% %time%] 开始盘前检查... >> "%LOG%"
echo 时间: %date% %time%
echo 执行: %PYTHON% "%WORKDIR%v7.5_institutional\daily_workflow.py"
echo.

"%PYTHON%" "%WORKDIR%v7.5_institutional\daily_workflow.py" >> "%LOG%" 2>&1

echo.
echo 完成时间: %date% %time%
echo 日志: %LOG%
echo.
echo [%date% %time%] 盘前检查完成 >> "%LOG%"
