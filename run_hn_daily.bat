@echo off
chcp 65001 >nul
cd /d "%~dp0"
"C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe" hn_daily_report.py %*
