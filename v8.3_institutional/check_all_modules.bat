@echo off
chcp 65001 >nul
REM ============================================================
REM 全模块检查（7 phases dry-run）
REM ============================================================
set PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe
set SCRIPT=%~dp0daily_workflow.py
set DATE=%1
if "%DATE%"=="" set DATE=2026-07-06

echo ============================================================
echo   v8.4 全模块检查
echo   Date: %DATE%
echo ============================================================

set FAIL=0
call :run check
call :run market
call :run risk
call :run hedge
call :run signal
call :run execute
call :run report

echo ============================================================
if %FAIL%==0 (
    echo [OK] ALL PASS
) else (
    echo [FAIL] 有 %FAIL% 个模块未通过
)
echo ============================================================
exit /b %FAIL%

:run
echo.
echo [%DATE%] Phase %~1 ...
"%PYTHON%" "%SCRIPT%" --date %DATE% --phase %~1 --dry-run >nul 2>&1
if %ERRORLEVEL%==0 (
    echo [PASS] Phase %~1
) else (
    echo [FAIL] Phase %~1
    set /a FAIL+=1
)
goto :eof
