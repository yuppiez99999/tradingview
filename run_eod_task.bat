@echo off
REM v8.6.9 EOD 四 Guard 风控链 + 盘后报告自动生成
REM 触发: 每周一至五 16:00 (A股收盘后)
REM v8.6.9 FIX (2026-07-27): 改用 py -3.11 launcher 触发 Python 3.11 (生产环境)
REM   原始 bug: register_eod_task.ps1 硬编码 Python 3.8 路径,
REM            但 Python 3.8 缺少 lightgbm/qlib/pytdx 等依赖,
REM            导致任务计划返回 255 (Python 异常退出),
REM            虽然 7-Guard 主流程仍能完成 (try-except 兜底), 但部分功能降级.
chcp 65001 >nul 2>&1
cd /d "E:\各种PY程序\28-终极量化交易系统8.4"
py -3.11 run_daily_eod.py
