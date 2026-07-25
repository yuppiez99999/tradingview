@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ============================================================
echo   v8.5 快速启动 - 核心工作流
echo   每日盘前 + 对冲联动 + 盘后报告
echo ============================================================
echo.

set SCRIPT_DIR=%~dp0
set LOG_DIR=%SCRIPT_DIR%logs

if not exist "%LOG_DIR%" (
    mkdir "%LOG_DIR%"
)

set DATE_STR=%date:~0,4%%date:~5,2%%date:~8,2%
set LOG_FILE=%LOG_DIR%\v8.5_full_%DATE_STR%.log

echo [%date% %time%] 开始执行 v8.5 完整工作流... >> "%LOG_FILE%"
echo.

echo [1/3] 盘前检查 (daily_workflow.py)...
python "%SCRIPT_DIR%daily_workflow.py" >> "%LOG_FILE%" 2>&1
if !errorlevel! equ 0 (
    echo [SUCCESS] 盘前检查完成
) else (
    echo [ERROR] 盘前检查失败
)
echo.

echo [2/3] 建仓 + 对冲联动 (daily_build_and_hedge.py)...
python "%SCRIPT_DIR%daily_build_and_hedge.py" >> "%LOG_FILE%" 2>&1
if !errorlevel! equ 0 (
    echo [SUCCESS] 建仓 + 对冲联动完成
) else (
    echo [ERROR] 建仓 + 对冲联动失败
)
echo.

echo [3/3] 盘后报告 (generate_daily_report.py)...
python "%SCRIPT_DIR%generate_daily_report.py" >> "%LOG_FILE%" 2>&1
if !errorlevel! equ 0 (
    echo [SUCCESS] 盘后报告完成
) else (
    echo [ERROR] 盘后报告失败
)
echo.

echo [%date% %time%] v8.5 完整工作流执行完毕 >> "%LOG_FILE%"
echo.
echo 日志文件: %LOG_FILE%
pause
