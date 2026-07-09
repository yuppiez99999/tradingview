@echo off
chcp 65001 >nul
echo 安装每日对冲自动更新定时任务...
echo.

:: 设置变量
set "PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe"
set "SCRIPT=E:\各种PY程序\28-终极量化交易系统7.1\daily_hedge_update.py"
set "TASK_NAME=DailyHedgeUpdate"
set "LOG_DIR=E:\各种PY程序\28-终极量化交易系统7.1\logs"
set "LOG_FILE=%LOG_DIR%\daily_hedge_update.log"

:: 创建日志目录
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

:: 删除旧任务（如果存在）
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

:: 创建新任务 - 每个交易日 9:15 运行
schtasks /create ^
    /tn "%TASK_NAME%" ^
    /tr "\"%PYTHON%\" \"%SCRIPT%\" >> \"%LOG_FILE%\" 2>&1" ^
    /sc weekly ^
    /d MON,TUE,WED,THU,FRI ^
    /st 09:15 ^
    /ru "%USERNAME%" ^
    /np ^
    /f

echo.
echo 任务创建完成！
echo 任务名称: %TASK_NAME%
echo 运行时间: 每个交易日 9:15
echo 脚本路径: %SCRIPT%
echo 日志路径: %LOG_FILE%
echo.
echo 查看任务: schtasks /query /tn "%TASK_NAME%"
echo 手动运行: schtasks /run /tn "%TASK_NAME%"
echo 删除任务: schtasks /delete /tn "%TASK_NAME%" /f
echo.
pause
