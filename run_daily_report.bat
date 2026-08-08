@echo off
chcp 65001 >nul
REM ============================================================
REM 每日报告生成 - v8.6.14 入口
REM 修复: 原指向 v7.1 旧目录的死链
REM ============================================================
cd /d "E:\各种PY程序\28-终极量化交易系统8.4"

REM 优先用 py -3.8，备选 python
set PYTHON_EXE=py -3.8
py -3.8 -c "import sys" 2>nul
if errorlevel 1 (
    set PYTHON_EXE=python
)

REM 调用 v8.6.14 的报告生成器（如有多个备选，按优先级）
if exist "generate_daily_report.py" (
    %PYTHON_EXE% "generate_daily_report.py" %*
) else if exist "15_每日工作流\run_daily_eod_workflow.py" (
    %PYTHON_EXE% "15_每日工作流\run_daily_eod_workflow.py" --phase report %*
) else if exist "hn_daily_report.py" (
    %PYTHON_EXE% "hn_daily_report.py" %*
) else (
    echo [ERROR] 找不到可用的报告生成器
    echo 检查: generate_daily_report.py / 15_每日工作流\run_daily_eod_workflow.py / hn_daily_report.py
    exit /b 1
)

exit /b 0
