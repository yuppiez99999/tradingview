@echo off
chcp 65001 >nul
cd /d "%~dp0.."

echo ============================================================
echo   每日早晨工作流 (Daily Morning Workflow)
echo   日期: %date%
echo   时间: %time%
echo ============================================================
echo.

REM 清理可能干扰的环境变量
set PYTHONHOME=
set PYTHONPATH=

REM 使用 Python 运行工作流脚本 (优先py -3.14, 备选python)
set PYTHON_EXE=py -3
for /f "delims=" %%i in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set PYTHON_PATH=%%i
if not defined PYTHON_PATH (
    echo [WARN] py -3 不可用，尝试使用 python
    set PYTHON_EXE=python
)

%PYTHON_EXE% "15_每日工作流\run_daily_morning.py" --phase all %*
set EXITCODE=%ERRORLEVEL%

echo.
echo ============================================================
echo   工作流执行完毕 (退出码: %EXITCODE%)
echo ============================================================

exit /b %EXITCODE%
