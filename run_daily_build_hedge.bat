@echo off
chcp 65001 >nul
echo ============================================================
echo 每日建仓计划 + 期货期权对冲联动系统 v8.0
echo ============================================================
echo.

setlocal enabledelayedexpansion

set "PYTHON_PATH=C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
set "SCRIPT_PATH=%~dp0daily_build_and_hedge.py"
set "WORK_DIR=%~dp0"
set "LOG_DIR=%WORK_DIR%logs"

if not exist "%PYTHON_PATH%" (
    echo ERROR: Python 解释器不存在: %PYTHON_PATH%
    echo 请修改本脚本中的 PYTHON_PATH 变量
    pause
    exit /b 1
)

if not exist "%SCRIPT_PATH%" (
    echo ERROR: 脚本文件不存在: %SCRIPT_PATH%
    pause
    exit /b 1
)

if not exist "%LOG_DIR%" (
    mkdir "%LOG_DIR%"
)

set "LOG_FILE=%LOG_DIR%\daily_build_hedge_%date:~0,4%%date:~5,2%%date:~8,2%.log"

echo [INFO] 启动时间: %date% %time%
echo [INFO] Python路径: %PYTHON_PATH%
echo [INFO] 脚本路径: %SCRIPT_PATH%
echo [INFO] 日志文件: %LOG_FILE%
echo.

cd /d "%WORK_DIR%"

echo [INFO] 正在执行每日建仓计划 + 对冲联动...
echo ------------------------------------------------------------

"%PYTHON_PATH%" "%SCRIPT_PATH%" --save 2>&1 | tee "%LOG_FILE%"

set "EXIT_CODE=%errorlevel%"

echo ------------------------------------------------------------
echo [INFO] 执行完成，退出码: %EXIT_CODE%

if %EXIT_CODE% equ 0 (
    echo [SUCCESS] 每日建仓计划 + 对冲联动执行成功！
    echo [INFO] 报告已保存至: %WORK_DIR%每日报告归档\
) else (
    echo [ERROR] 执行失败，请查看日志: %LOG_FILE%
)

echo.
echo [INFO] 按任意键退出...
pause >nul

exit /b %EXIT_CODE%