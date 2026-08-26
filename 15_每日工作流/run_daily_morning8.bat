@echo off
chcp 65001 >nul
REM ============================================
REM 每日早8点日报+盘前交易计划 - 调度启动脚本
REM 功能: 每日 08:00 执行完整早间工作流
REM   阶段0: 晨间信息采集 (7项报告, 非交易日也运行)
REM   阶段1: 盘前市场校准与风险评估 (仅交易日)
REM   阶段2: 生成每日交易计划 (仅交易日)
REM   阶段3: 应用大模型决策到交易计划 (仅交易日)
REM   阶段4: 生成每日综合报告 (仅交易日)
REM   归档: 所有报告归档到 每日报告归档/YYYY-MM-DD/
REM 交易日判断: 由 run_daily_morning.py 内部 is_trading_day() 处理
REM ============================================
set ROOT=e:\各种PY程序\28-终极量化交易系统8.4
cd /d "%ROOT%"

set LOGDIR=%ROOT%\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set LOG=%LOGDIR%\daily_morning8_log.txt

echo. >> "%LOG%"
echo ================================================================ >> "%LOG%"
echo [%date% %time%] 早8点日报+盘前交易计划工作流启动 >> "%LOG%"
echo ================================================================ >> "%LOG%"

REM 清理可能干扰的环境变量
set PYTHONHOME=
set PYTHONPATH=
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

REM Python 解释器: 优先 QUANT_PYTHON 环境变量, 备选 py -3, 再备选 python
if defined QUANT_PYTHON (
    set PYTHON_EXE=%QUANT_PYTHON%
    goto :run
)
for /f "delims=" %%i in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set PYTHON_EXE=%%i
if not defined PYTHON_EXE (
    echo [%date% %time%] [WARN] py -3 不可用, 尝试 python >> "%LOG%"
    set PYTHON_EXE=python
)

:run
echo [%date% %time%] 使用 Python: %PYTHON_EXE% >> "%LOG%"
echo [%date% %time%] 执行: run_daily_morning.py --phase all >> "%LOG%"
echo [%date% %time%] 流程: info(7项报告) ^> calibrate ^> plan ^> LLM决策 ^> report ^> 归档 >> "%LOG%"
echo.

REM 调用统一早间工作流 (--phase all = info→calibrate→plan→LLM→report→archive)
REM 非交易日: 自动只跑 info 阶段 (信息采集), 跳过决策类阶段
"%PYTHON_EXE%" "%ROOT%\15_每日工作流\run_daily_morning.py" --phase all >> "%LOG%" 2>&1
set EXITCODE=%ERRORLEVEL%

echo. >> "%LOG%"
echo [%date% %time%] 早8点工作流结束 (退出码: %EXITCODE%) >> "%LOG%"
echo ================================================================ >> "%LOG%"

exit /b %EXITCODE%
