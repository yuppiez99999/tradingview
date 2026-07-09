@echo off
chcp 65001 >nul
cd /d "%~dp0"
"C:\Program Files\Python38\python.exe" hn_daily_report.py %*
