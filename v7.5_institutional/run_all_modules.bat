@echo off
chcp 65001 >nul
REM ============================================================
REM v7.5 全核心模块调度器 - Windows 启动脚本
REM ============================================================
REM 用法:
REM   run_all_modules.bat pre      (盘前 07:00)
REM   run_all_modules.bat post     (盘后 15:30)
REM   run_all_modules.bat all      (全部, 测试用)
REM   run_all_modules.bat pre dry-run
REM ============================================================

set PYTHON=C:\Program Files\Python38\python.exe
set WORKDIR=%~dp0
set LOGDIR=%WORKDIR%logs
set SCRIPT=%WORKDIR%run_all_modules.py

REM 创建日志目录
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

REM 获取当前日期时间
for /f "tokens=1-3 delims=/ " %%a in ('date /t') do set TODAY=%%c-%%a-%%b
for /f "tokens=1-2 delims=:." %%a in ('time /t') do set NOW=%%a-%%b
set LOGFILE=%LOGDIR%\all_modules_%TODAY%_%NOW%.log

REM 批次参数 (默认 all)
set PHASE=%1
if "%PHASE%"=="" set PHASE=all
set DRYRUN=%2

echo ============================================================
echo   v7.5 All Core Modules Runner
echo   Date: %TODAY% %TIME%
echo   Phase: %PHASE%
echo   Script: %SCRIPT%
echo   Log: %LOGFILE%
echo ============================================================

REM 判断是否为交易日 (周一至周五)
for /f %%d in ('powershell -Command "(Get-Date).DayOfWeek.value__"') do set DOW=%%d
if %DOW% GTR 5 (
    echo [%TIME%] 今天是周末, 跳过执行
    exit /b 0
)

REM 执行调度器
echo [%TIME%] 开始执行 phase=%PHASE% ...
if "%DRYRUN%"=="dry-run" (
    "%PYTHON%" "%SCRIPT%" --phase %PHASE% --dry-run >> "%LOGFILE%" 2>&1
) else (
    "%PYTHON%" "%SCRIPT%" --phase %PHASE% >> "%LOGFILE%" 2>&1
)

set EXITCODE=%ERRORLEVEL%
echo [%TIME%] 调度完成, 退出码: %EXITCODE%

exit /b %EXITCODE%
