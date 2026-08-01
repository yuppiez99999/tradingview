@echo off
REM ============================================================
REM v8.6.10: 每日工作流定时任务 (SYSTEM 用户 + junction 路径)
REM v8.6.11: 新增 eod session (15:30 EOD + Shadow daily 报告)
REM 用法:
REM   run_workflow_task.bat workflow  (07:00 每日工作流)
REM   run_workflow_task.bat morning   (09:30 早盘执行)
REM   run_workflow_task.bat afternoon (14:00 午盘执行)
REM   run_workflow_task.bat eod       (15:30 EOD 风控 + Shadow daily 报告)
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
REM v8.6.11 FIX: wmic 在新 Windows 版本上已弃用, 改用 PowerShell 获取日期
for /f "delims=" %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TRADE_DATE=%%i
if "%TRADE_DATE%"=="" (
    REM 兜底: 使用 wmic (旧 Windows 版本)
    for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value 2^>nul') do set LDT=%%a
    set TRADE_DATE=%LDT:~0,4%-%LDT:~4,2%-%LDT:~6,2%
)

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

if /i "%SESSION%"=="eod" (
    echo === 执行 EOD 风控 + Shadow daily 报告 === >> "%LOG_FILE%"
    REM Step 1: 四 Guard 风控链 (run_daily_eod.py)
    cd /d "C:\QuantSys"
    "%PYTHON_HOME%\python.exe" run_daily_eod.py >> "%LOG_FILE%" 2>&1
    set EOD_EXIT=%ERRORLEVEL%
    echo EOD 四 Guard 退出代码: %EOD_EXIT% >> "%LOG_FILE%"
    REM Step 2: V9 Phase 10 影子账户监控 (写 daily_returns.jsonl)
    cd /d "C:\QuantSys\v8.3_institutional"
    "%PYTHON_HOME%\python.exe" daily_workflow.py --date %TRADE_DATE% --phase shadow_monitor >> "%LOG_FILE%" 2>&1
    set PHASE10_EXIT=%ERRORLEVEL%
    echo V9 Phase 10 退出代码: %PHASE10_EXIT% >> "%LOG_FILE%"
    REM Step 3: Shadow 每日 DSR 报告生成
    cd /d "C:\QuantSys"
    "%PYTHON_HOME%\python.exe" scripts\shadow_admission_launcher.py daily >> "%LOG_FILE%" 2>&1
    set SHADOW_EXIT=%ERRORLEVEL%
    echo Shadow daily 退出代码: %SHADOW_EXIT% >> "%LOG_FILE%"
    REM 任一关键步骤失败即标记 EOD 失败 (Phase 10 失败可容忍, 不阻断)
    if "%EOD_EXIT%"=="0" (
        set EXIT_CODE=0
    ) else (
        set EXIT_CODE=%EOD_EXIT%
    )
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
