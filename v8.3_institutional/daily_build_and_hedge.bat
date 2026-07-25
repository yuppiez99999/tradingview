@echo off
chcp 65001 >nul
echo ============================================================
echo v8.3 Institutional - 每日建仓计划 + 期货期权对冲联动系统
echo ============================================================
echo.

setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "PYTHON_PATH=%SCRIPT_DIR%..\qlib_env\python.exe"
set "SCRIPT_PATH=%SCRIPT_DIR%daily_build_and_hedge.py"
set "LOG_DIR=%SCRIPT_DIR%logs"

if not exist "%PYTHON_PATH%" (
    echo [WARN] Python 解释器不存在: %PYTHON_PATH%
    echo [INFO] 使用系统Python
    set "PYTHON_PATH=python"
)

if not exist "%SCRIPT_PATH%" (
    echo [ERROR] 脚本文件不存在: %SCRIPT_PATH%
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

cd /d "%SCRIPT_DIR%"

echo [INFO] 正在执行每日建仓计划 + 对冲联动...
echo ------------------------------------------------------------

"%PYTHON_PATH%" "%SCRIPT_PATH%" --save 2>&1 | tee "%LOG_FILE%"

set "EXIT_CODE=!errorlevel!"

echo ------------------------------------------------------------
echo [INFO] 执行完成，退出码: %EXIT_CODE%

if %EXIT_CODE% equ 0 (
    echo [SUCCESS] 每日建仓计划 + 对冲联动执行成功！
) else (
    echo [ERROR] 执行失败，请查看日志: %LOG_FILE%
)

echo.
echo [INFO] 按任意键退出...
pause >nul

exit /b %EXIT_CODE%
