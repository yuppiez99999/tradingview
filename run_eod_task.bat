@echo off
REM v8.6.1 EOD 四 Guard 风控链 + 盘后报告自动生成
REM 触发: 每周一至五 16:00 (A股收盘后)
chcp 65001 >nul 2>&1
cd /d "E:\各种PY程序\28-终极量化交易系统8.4"
"C:\Program Files\Python38\python.exe" run_daily_eod.py
