@echo off
chcp 65001 >nul
echo ========================================
echo   启动盘中监控 - AutoHedge Live
echo ========================================
echo.

set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe
set WORKDIR=%~dp0
set LOG=%WORKDIR%logs\live_monitor.log
set PIDFILE=%WORKDIR%live_monitor.pid

echo [%date% %time%] 启动盘中监控..." >> "%LOG%"
echo 时间: %date% %time%
echo 执行: %PYTHON% --mode live --broker mock
echo.

REM 检查是否已在运行
if exist "%PIDFILE%" (
    set /p OLDPID=<"%PIDFILE%"
    tasklist /FI "PID eq %OLDPID%" 2>NUL | find "%OLDPID%" >NUL
    if %ERRORLEVEL% == 0 (
        echo 盘中监控已在运行，PID: %OLDPID%
        pause
        exit /b 0
    )
)

REM 启动 live 模式
start "AutoHedge Live Monitor" /B "%PYTHON%" --mode live --broker mock >> "%LOG%" 2>&1

REM 获取新进程PID
timeout /t 3 /nobreak >nul
for /f "tokens=2" %%a in ('tasklist /FI "WINDOWTITLE eq AutoHedge Live Monitor" /FO CSV ^| find "python"') do (
    echo %%a > "%PIDFILE%"
)

echo.
echo 盘中监控已启动
echo PID 文件: %PIDFILE%
echo 日志文件: %LOG%
echo.
echo 停止监控: run_stop_live.bat
echo.
pause
