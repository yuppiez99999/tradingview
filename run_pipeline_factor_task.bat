@echo off
REM ============================================================
REM v8.6.10: 因子流水线定时任务 (SYSTEM 用户 + junction 路径)
REM 使用 junction C:\QuantSys 避免中文路径编码问题
REM 使用显式 Python 3.14 路径避免 PATH 缺失
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

REM 执行脚本, 输出写入日志文件
echo ============================================================ > "C:\QuantSys\logs\pipeline_factor_offline.log"
echo Pipeline 因子信号离线生成 >> "C:\QuantSys\logs\pipeline_factor_offline.log"
echo 开始时间: %date% %time% >> "C:\QuantSys\logs\pipeline_factor_offline.log"
echo ============================================================ >> "C:\QuantSys\logs\pipeline_factor_offline.log"

"%PYTHON_HOME%\python.exe" scripts\run_pipeline_factor_offline.py >> "C:\QuantSys\logs\pipeline_factor_offline.log" 2>&1
set EXIT_CODE=%ERRORLEVEL%

echo. >> "C:\QuantSys\logs\pipeline_factor_offline.log"
echo 结束时间: %date% %time% >> "C:\QuantSys\logs\pipeline_factor_offline.log"
echo 退出代码: %EXIT_CODE% >> "C:\QuantSys\logs\pipeline_factor_offline.log"

exit /b %EXIT_CODE%
