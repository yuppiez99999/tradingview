@echo off
chcp 65001 >nul
echo ========================================
echo   v7.5 实时监控并发调度器 - 停止
echo ========================================
echo.

set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe
set WORKDIR=%~dp0
set LOG=%WORKDIR%logs\live_scheduler.log

echo [%date% %time%] 停止实时监控调度器..." >> "%LOG%"

"%PYTHON%" "%WORKDIR%live_scheduler.py" --stop

echo.
echo 完成时间: %date% %time%
echo.
pause
