@echo off
chcp 65001 >nul
REM ============================================
REM 每周五收盘后周报生成 - 调度启动脚本
REM 功能: 每周五 16:30 自动生成本周周报并归档
REM ============================================
set ROOT=e:\各种PY程序\28-终极量化交易系统8.4
cd /d "%ROOT%"

set LOGDIR=%ROOT%\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set LOG=%LOGDIR%\weekly_report_log.txt

echo. >> "%LOG%"
echo ================================================================ >> "%LOG%"
echo [%date% %time%] 开始生成每周工作报告 >> "%LOG%"
echo ================================================================ >> "%LOG%"

REM 交易日判断 (非交易日跳过)
"C:\Users\Administrator\py311\python.exe" -c "import sys; sys.path.insert(0, r'%ROOT%'); from utils.trade_calendar import is_trading_day; from datetime import datetime; d=datetime.now().strftime('%%Y-%%m-%%d'); sys.exit(0 if is_trading_day(d) else 1)" 2>> "%LOG%"
if errorlevel 1 (
    echo [%date% %time%] 非交易日, 跳过周报生成 >> "%LOG%"
    exit /b 0
)

REM 生成本周周报 (HTML/PDF 对冲基金持仓报告, 归档到 research/每日报告归档/)
"C:\Users\Administrator\py311\python.exe" "%ROOT%\research\generate_weekly_report.py" --no-pdf >> "%LOG%" 2>&1

echo [%date% %time%] 周报生成流程结束 >> "%LOG%"
