@echo off
chcp 65001 >nul
echo v8.3 Institutional - 每日对冲自动更新定时任务安装器
echo ============================================================
echo.

set "SCRIPT_DIR=%~dp0"
set "PYTHON_PATH=%SCRIPT_DIR%..\qlib_env\python.exe"
set "SCRIPT_PATH=%SCRIPT_DIR%daily_hedge_update.py"
set "TASK_NAME=V83DailyHedgeUpdate"
set "LOG_DIR=%SCRIPT_DIR%logs"
set "LOG_FILE=%LOG_DIR%\daily_hedge_update.log"

if not exist "%PYTHON_PATH%" (
    echo [WARN] Python 解释器不存在，使用系统Python
    set "PYTHON_PATH=python"
)

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo [INFO] 脚本路径: %SCRIPT_PATH%
echo [INFO] 日志路径: %LOG_FILE%
echo.

:: 删除旧任务（如果存在）
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

:: 创建新任务 - 每个交易日 9:15 运行
schtasks /create ^
    /tn "%TASK_NAME%" ^
    /tr "\"%PYTHON_PATH%\" \"%SCRIPT_PATH%\" >> \"%LOG_FILE%\" 2>&1" ^
    /sc weekly ^
    /d MON,TUE,WED,THU,FRI ^
    /st 09:15 ^
    /ru "%USERNAME%" ^
    /np ^
    /f

echo.
echo [SUCCESS] 任务创建完成！
echo   任务名称: %TASK_NAME%
echo   运行时间: 每个交易日 9:15
echo   脚本路径: %SCRIPT_PATH%
echo   日志路径: %LOG_FILE%
echo.
echo [INFO] 查看任务: schtasks /query /tn "%TASK_NAME%"
echo [INFO] 手动运行: schtasks /run /tn "%TASK_NAME%"
echo [INFO] 删除任务: schtasks /delete /tn "%TASK_NAME%" /f
echo.
pause
