@echo off
chcp 65001 >nul
REM ============================================================
REM v7.5 兼容入口（已迁移到 v8.4）
REM ============================================================
REM 此文件为兼容性占位符，自动转发到 v8.4 的入口
REM 原 v7.5_institutional 系统已被 v8.4 取代
REM ============================================================

cd /d "E:\各种PY程序\28-终极量化交易系统8.4"

set PHASE=%1
if "%PHASE%"=="" set PHASE=all

if /I "%PHASE%"=="pre" (
    echo [v7.5 兼容] 转发到 v8.4 盘前工作流...
    call "15_每日工作流\run_daily_morning.bat" %*
) else if /I "%PHASE%"=="post" (
    echo [v7.5 兼容] 转发到 v8.4 盘后工作流...
    call "15_每日工作流\run_eod_workflow.bat" %*
) else (
    echo [v7.5 兼容] 转发到 v8.4 完整工作流...
    call "15_每日工作流\run_daily_morning.bat" %*
    call "15_每日工作流\run_eod_workflow.bat" %*
)

exit /b %ERRORLEVEL%
