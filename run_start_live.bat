@echo off
chcp 65001 >nul
echo ========================================
echo   v7.5 实时监控并发调度器 - 启动
echo ========================================
echo.

set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe
set WORKDIR=%~dp0
set LOG=%WORKDIR%logs\live_scheduler.log
set PIDFILE=%WORKDIR%.live_scheduler.lock

echo [%date% %time%] 启动实时监控调度器..." >> "%LOG%"
echo 时间: %date% %time%
echo 执行: %PYTHON% live_scheduler.py
echo.

REM 检查是否已在运行
"%PYTHON%" "%WORKDIR%live_scheduler.py" --status
if %ERRORLEVEL% == 0 (
    echo 实时监控调度器已在运行
    pause
    exit /b 0
)

REM 启动并发调度器
start "v7.5 Live Scheduler" /B "%PYTHON%" "%WORKDIR%live_scheduler.py" >> "%LOG%" 2>&1

timeout /t 5 /nobreak >nul

"%PYTHON%" "%WORKDIR%live_scheduler.py" --status

echo.
echo 实时监控调度器已启动 (6模块并发)
echo PID 文件: %PIDFILE%
echo 日志文件: %LOG%
echo.
echo 停止监控: run_stop_live.bat
echo 查看状态: python live_scheduler.py --status
echo.
pause
