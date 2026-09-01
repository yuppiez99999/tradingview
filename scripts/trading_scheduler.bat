@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ========================================
echo   AutoHedge 交易日调度器
echo ========================================
echo.

REM P0-2 修复 (2026-09-01): 原硬编码 AppData Python311 路径不存在, 改用项目 .venv
set PYTHON=E:\各种PY程序\28-终极量化交易系统8.4\.venv\Scripts\python.exe
set WORKDIR=%~dp0
set LOG=%WORKDIR%logs\trading_scheduler.log
if not exist "%WORKDIR%logs" mkdir "%WORKDIR%logs"

:check_time
echo [%date% %time%] 检查任务... >> "%LOG%"

REM 获取当前时间（24小时制）
for /f "tokens=1-4 delims=/: " %%a in ("%time%") do (
    set HH=%%a
    set MM=%%b
)

REM 去掉小时前导零
set /a HH=%HH% + 0

echo 当前时间: %HH%:%MM%
echo.

REM 判断当前时间执行对应任务
if %HH% EQU 7 if %MM% EQU 0 (
    echo 执行盘前检查...
    call "%WORKDIR%run_pre_market.bat"
)

if %HH% EQU 9 if %MM% EQU 25 (
    echo 启动盘中监控...
    call "%WORKDIR%run_start_live.bat"
)

if %HH% EQU 15 if %MM% EQU 5 (
    echo 停止盘中监控...
    call "%WORKDIR%run_stop_live.bat"
)

echo.
echo 等待下一检查周期...
echo.

REM 等待 60 秒后再次检查
timeout /t 60 /nobreak >nul
goto check_time
