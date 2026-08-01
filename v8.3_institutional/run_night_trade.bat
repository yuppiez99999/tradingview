@echo off
chcp 65001 >nul
echo ========================================
echo   夜间交易 - Night Session
echo ========================================
echo.

set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe
set WORKDIR=%~dp0
set LOG=%WORKDIR%..\logs\night_trade.log

echo [%date% %time%] 开始夜间交易... >> "%LOG%"
echo 时间: %date% %time%
echo 执行: %PYTHON% "%WORKDIR%weekly_trade_executor.py" --session night
echo.

"%PYTHON%" "%WORKDIR%weekly_trade_executor.py" --session night >> "%LOG%" 2>&1

echo.
echo 完成时间: %date% %time%
echo 日志: %LOG%
echo.
echo [%date% %time%] 夜间交易完成 >> "%LOG%"
