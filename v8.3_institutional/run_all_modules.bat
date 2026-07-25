@echo off
REM ============================================================
REM v7.5 All Core Modules Runner
REM ============================================================
REM Usage:
REM   run_all_modules.bat pre      (pre-market 07:00)
REM   run_all_modules.bat post     (post-market 15:30)
REM   run_all_modules.bat all      (all, for testing)
REM   run_all_modules.bat pre dry-run
REM ============================================================

set PYTHON=C:\Program Files\Python38\python.exe
set WORKDIR=%~dp0
set LOGDIR=%WORKDIR%logs
set SCRIPT=%WORKDIR%run_all_modules.py

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

set PHASE=%1
if "%PHASE%"=="" set PHASE=all
set DRYRUN=%2

for /f %%d in ('powershell -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%d
for /f "tokens=1-2 delims=:." %%a in ('powershell -Command "Get-Date -Format HH-mm"') do set NOW=%%a-%%b
set LOGFILE=%LOGDIR%\all_modules_%TODAY%_%NOW%.log

echo ============================================================
echo   v7.5 All Core Modules Runner
echo   Date: %TODAY% %TIME%
echo   Phase: %PHASE%
echo   Script: %SCRIPT%
echo   Log: %LOGFILE%
echo ============================================================

for /f %%d in ('powershell -Command "(Get-Date).DayOfWeek.value__"') do set DOW=%%d
if %DOW% GTR 5 (
    echo [%TIME%] Weekend, skip
    exit /b 0
)

echo [%TIME%] Start phase=%PHASE% ...
if "%DRYRUN%"=="dry-run" (
    "%PYTHON%" "%SCRIPT%" --phase %PHASE% --dry-run >> "%LOGFILE%" 2>&1
) else (
    "%PYTHON%" "%SCRIPT%" --phase %PHASE% >> "%LOGFILE%" 2>&1
)

set EXITCODE=%ERRORLEVEL%
echo [%TIME%] Done, exit code: %EXITCODE%

exit /b %EXITCODE%
