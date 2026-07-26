@echo off
chcp 65001 >nul
REM ============================================================
REM 下周自动交易执行脚本 (2026-07-27 ~ 2026-07-31)
REM ============================================================
REM 用法:
REM   run_weekly_auto_20260727.bat morning   (09:30 早盘执行)
REM   run_weekly_auto_20260727.bat afternoon (14:00 午盘执行)
REM   run_weekly_auto_20260727.bat workflow  (07:00 每日工作流)
REM   run_weekly_auto_20260727.bat week      (查看周计划概览)
REM ============================================================

cd /d "e:\各种PY程序\28-终极量化交易系统8.4\v8.3_institutional"

set SESSION=%1
if "%SESSION%"=="" set SESSION=workflow

if /i "%SESSION%"=="morning" (
    echo === 执行早盘建仓 (09:30-10:30) ===
    py -3 weekly_trade_executor.py --session morning
    goto :end
)

if /i "%SESSION%"=="afternoon" (
    echo === 执行午盘建仓 (14:00-14:30) ===
    py -3 weekly_trade_executor.py --session afternoon
    goto :end
)

if /i "%SESSION%"=="week" (
    echo === 本周计划概览 ===
    py -3 weekly_trade_executor.py --week
    goto :end
)

REM 默认:执行每日工作流(07:00)
echo === 执行每日工作流 (%date% %time%) ===
echo === 加载周计划: weekly_plan_20260727_20260731.json ===
py -3 daily_workflow.py --date %date:~0,4%-%date:~5,2%-%date:~8,2%

:end
echo === 执行完成 %time% ===
