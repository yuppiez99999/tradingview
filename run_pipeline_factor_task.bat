@echo off
REM v8.6.9 P1 FIX: 因子流水线定时任务 (SYSTEM 用户 + 英文 junction 路径)
REM v8.6.9 FIX: 使用 junction C:\QuantSys -> E:\各种PY程序\28-终极量化交易系统8.4
REM              避免 SYSTEM 用户运行 cmd.exe 时中文路径编码问题
chcp 65001 >nul 2>&1
cd /d "C:\QuantSys"
"C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe" scripts\run_pipeline_factor_offline.py
exit /b %ERRORLEVEL%
