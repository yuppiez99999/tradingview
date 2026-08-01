@echo off
chcp 65001 >nul
REM ============================================
REM 每周对冲基金持仓报告 - 调度启动脚本
REM 功能: 每周五收盘后自动生成HTML+PDF报告并归档
REM ============================================
set ROOT=e:\各种PY程序\28-终极量化交易系统8.4
cd /d "%ROOT%"

set LOGDIR=%ROOT%\research\outputs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set LOG=%LOGDIR%\weekly_report_log.txt

echo. >> "%LOG%"
echo ================================================================ >> "%LOG%"
echo [%date% %time%] 开始生成每周持仓报告 >> "%LOG%"
echo ================================================================ >> "%LOG%"

REM 调用报告生成器(用当日日期)
py -3.14 research\generate_weekly_report.py >> "%LOG%" 2>&1

echo [%date% %time%] 报告生成流程结束 >> "%LOG%"
