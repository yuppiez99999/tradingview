@echo off
setlocal enabledelayedexpansion
REM ============================================================
REM v7.5 Daily Workflow - launcher
REM Auto-run each trading day (called by Task Scheduler)
REM Passes all args through to daily_workflow.py
REM ============================================================

set PYTHON=C:\Program Files\Python38\python.exe
set WORKDIR=%~dp0
set LOGDIR=%WORKDIR%logs
set DAILY_SCRIPT=%WORKDIR%daily_workflow.py

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

REM Use PowerShell for reliable date/time formatting
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%d
for /f "tokens=1-2 delims=:." %%a in ('powershell -NoProfile -Command "Get-Date -Format HH-mm"') do set NOW=%%a-%%b
set LOGFILE=%LOGDIR%\daily_%TODAY%_%NOW%.log

echo ============================================================
echo   v7.5 Daily Workflow
echo   Date: %TODAY% %TIME%
echo   Script: %DAILY_SCRIPT%
echo   Log: %LOGFILE%
echo ============================================================

REM Skip weekends
for /f %%d in ('powershell -NoProfile -Command "(Get-Date).DayOfWeek.value__"') do set DOW=%%d
if %DOW% GTR 5 (
    echo [%TIME%] Weekend, skip
    exit /b 0
)

echo [%TIME%] Start daily workflow...
set PHASE_ARG=
if /i "%1"=="pre" goto :pre
if /i "%1"=="post" goto :post
if /i "%1"=="all" goto :all
echo [%TIME%] Unknown phase: %*, defaulting to --phase report
set PHASE_ARG=--phase report
goto :run

:pre
echo [%TIME%] Pre-market: generate trade plan + calibrate
echo [%TIME%] Step 1: generate_daily_trade_plan.py
"%PYTHON%" "%WORKDIR%generate_daily_trade_plan.py" >> "%LOGFILE%" 2>&1
echo [%TIME%] Step 2: daily_workflow.py --phase calibrate
set PHASE_ARG=--phase calibrate
goto :run

:post
echo [%TIME%] Post-market: daily_workflow.py --phase report
set PHASE_ARG=--phase report
goto :run

:all
echo [%TIME%] All phases: daily_workflow.py (no --phase)
set PHASE_ARG=
goto :run

:run
echo [%TIME%] Phase mapping: %1 -> !PHASE_ARG!
if defined PHASE_ARG (
    "%PYTHON%" "%DAILY_SCRIPT%" !PHASE_ARG! >> "%LOGFILE%" 2>&1
) else (
    "%PYTHON%" "%DAILY_SCRIPT%" >> "%LOGFILE%" 2>&1
)

set EXITCODE=%ERRORLEVEL%
echo [%TIME%] Done, exit code: %EXITCODE%

if %EXITCODE% NEQ 0 (
    echo [%TIME%] Workflow failed, check log: %LOGFILE%
)

exit /b %EXITCODE%
