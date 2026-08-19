@echo off
chcp 65001 >nul
REM ============================================
REM 每交易日早8点日报生成 - 调度启动脚本
REM 功能: 每交易日 08:00 生成收盘盈亏日报 + 晨间信息采集并归档
REM 交易日判断: 由脚本内部 is_trading_day() 处理, 非交易日自动跳过
REM ============================================
set ROOT=e:\各种PY程序\28-终极量化交易系统8.4
cd /d "%ROOT%"

set LOGDIR=%ROOT%\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set LOG=%LOGDIR%\daily_morning8_log.txt

echo. >> "%LOG%"
echo ================================================================ >> "%LOG%"
echo [%date% %time%] 早8点日报工作流启动 >> "%LOG%"
echo ================================================================ >> "%LOG%"

REM 清理可能干扰的环境变量
set PYTHONHOME=
set PYTHONPATH=
set PYTHONIOENCODING=utf-8

REM 交易日判断 (非交易日跳过)
"C:\Users\Administrator\py311\python.exe" -c "import sys; sys.path.insert(0, r'%ROOT%'); from utils.trade_calendar import is_trading_day; from datetime import datetime; d=datetime.now().strftime('%%Y-%%m-%%d'); sys.exit(0 if is_trading_day(d) else 1)" 2>> "%LOG%"
if errorlevel 1 (
    echo [%date% %time%] 非交易日, 跳过日报生成 >> "%LOG%"
    exit /b 0
)

REM 阶段一: 晨间信息采集 (7项报告)
echo [%date% %time%] 阶段一: 晨间信息采集 >> "%LOG%"
"C:\Users\Administrator\py311\python.exe" "%ROOT%\15_每日工作流\morning_info_runner.py" >> "%LOG%" 2>&1

REM 阶段二: 生成收盘盈亏日报 (基于上一交易日收盘数据)
echo [%date% %time%] 阶段二: 生成收盘盈亏日报 >> "%LOG%"
"C:\Users\Administrator\py311\python.exe" "%ROOT%\generate_daily_report.py" >> "%LOG%" 2>&1

echo [%date% %time%] 早8点日报工作流结束 >> "%LOG%"