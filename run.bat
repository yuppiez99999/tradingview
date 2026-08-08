@echo off
chcp 65001 >nul 2>&1
set PYTHONPATH=D:\pylibs_rose
"C:\Program Files\Python38\python.exe" "%~dp0rose_3d.py"
pause
