@echo off
chcp 65001 >nul
echo ========================================
echo   停止盘中监控 - AutoHedge Live Stop
echo ========================================
echo.

set WORKDIR=%~dp0
set PIDFILE=%WORKDIR%live_monitor.pid

echo [%date% %time%] 停止盘中监控..." >> "%WORKDIR%logs\live_monitor.log"

if not exist "%PIDFILE%" (
    echo 未找到 PID 文件，盘中监控可能未运行
    pause
    exit /b 0
)

set /p PID=<"%PIDFILE%"
echo 正在停止进程 PID: %PID%

taskkill /F /PID %PID% 2>NUL
if %ERRORLEVEL% EQU 0 (
    echo 停止成功
) else (
    echo 进程 %PID% 未运行或已停止
)

del "%PIDFILE%" 2>NUL

echo.
echo 完成时间: %date% %time%
pause
