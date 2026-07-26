@echo off
REM ============================================================
REM v8.6.10: 每日工作流定时任务 (SYSTEM 用户 + junction 路径)
REM 用法:
REM   run_workflow_task.bat workflow  (07:00 每日工作流)
REM   run_workflow_task.bat morning   (09:30 早盘执行)
REM   run_workflow_task.bat afternoon (14:00 午盘执行)
REM ============================================================

chcp 65001 >nul 2>&1
cd /d "C:\QuantSys"

REM PATH 防御性设置
set PYTHON_HOME=C:\Users\Administrator\AppData\Local\Programs\Python\Python314
set PATH=%PYTHON_HOME%;%PYTHON_HOME%\Scripts;%PATH%
set PYTHONPATH=C:\QuantSys;C:\QuantSys\utils;C:\QuantSys\v8.3_institutional
set PYTHONIOENCODING=utf-8

REM 日志目录
if not exist "C:\QuantSys\logs" mkdir "C:\QuantSys\logs" >nul 2>&1

REM 解析会话参数
set SESSION=%1
if "%SESSION%"=="" set SESSION=workflow

REM 获取当前日期 (YYYY-MM-DD 格式)
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value 2^>nul') do set LDT=%%a
set TRADE_DATE=%LDT:~0,4%-%LDT:~4,2%-%LDT:~6,2%

set LOG_FILE=C:\QuantSys\logs\workflow_%SESSION%_latest.log

echo ============================================================ > "%LOG_FILE%"
echo 每日工作流任务 >> "%LOG_FILE%"
echo 会话: %SESSION% >> "%LOG_FILE%"
echo 交易日: %TRADE_DATE% >> "%LOG_FILE%"
echo 开始时间: %date% %time% >> "%LOG_FILE%"
echo ============================================================ >> "%LOG_FILE%"

set EXIT_CODE=0

if /i "%SESSION%"=="morning" (
    echo === 执行早盘建仓 === >> "%LOG_FILE%"
    cd /d "C:\QuantSys\v8.3_institutional"
    "%PYTHON_HOME%\python.exe" weekly_trade_executor.py --session morning >> "%LOG_FILE%" 2>&1
    set EXIT_CODE=%ERRORLEVEL%
    goto task_end
)

if /i "%SESSION%"=="afternoon" (
    echo === 执行午盘建仓 === >> "%LOG_FILE%"
    cd /d "C:\QuantSys\v8.3_institutional"
    "%PYTHON_HOME%\python.exe" weekly_trade_executor.py --session afternoon >> "%LOG_FILE%" 2>&1
    set EXIT_CODE=%ERRORLEVEL%
    goto task_end
)

REM 默认: 执行每日工作流
echo === 执行每日工作流 === >> "%LOG_FILE%"
cd /d "C:\QuantSys\v8.3_institutional"
"%PYTHON_HOME%\python.exe" daily_workflow.py --date %TRADE_DATE% >> "%LOG_FILE%" 2>&1
set EXIT_CODE=%ERRORLEVEL%

:task_end
echo. >> "%LOG_FILE%"
echo 结束时间: %date% %time% >> "%LOG_FILE%"
echo 退出代码: %EXIT_CODE% >> "%LOG_FILE%"

exit /b %EXIT_CODE%
