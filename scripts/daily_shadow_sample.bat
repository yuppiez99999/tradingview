@echo off
REM 每日影子样本自动补全 - Windows Task Scheduler 入口
REM 触发时间: 每周一到周五 15:30 (A 股收盘后 30 分钟)
REM
REM 功能:
REM   1. 拉取当日 26 个标的真实行情
REM   2. 计算日收益并写入 daily_returns.jsonl
REM   3. 运行交叉验证
REM   4. 输出汇总日志到 logs/daily_shadow_sample_YYYYMMDD.log

cd /d "e:\各种PY程序\28-终极量化交易系统8.4"

REM 使用项目虚拟环境
set "PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    REM 回退到系统 Python
    set "PYTHON=python"
)

REM 调用脚本 (自动用今天日期)
"%PYTHON%" scripts\daily_shadow_sample.py

REM 退出码
exit /b %ERRORLEVEL%
