@echo off
chcp 65001 >nul
echo ========================================
echo   部署 Windows 任务计划
echo ========================================
echo.

echo 正在创建 3 个交易日自动任务...
echo.

REM 盘前检查 - 每天 7:00
schtasks /create /tn "AutoHedge_PreMarket_0700" /tr "\"C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe\" --mode pre-market --broker mock" /sc weekly /d MON,TUE,WED,THU,FRI /st 07:00 /ru "%USERNAME%" /rl HIGHEST /f
echo.

REM 启动盘中监控 - 每天 9:25
schtasks /create /tn "AutoHedge_Live_Start_0925" /tr "powershell.exe -ExecutionPolicy Bypass -File \"%~dp0start_live_monitor.ps1\"" /sc weekly /d MON,TUE,WED,THU,FRI /st 09:25 /ru "%USERNAME%" /rl HIGHEST /f
echo.

REM 停止盘中监控 - 每天 15:05
schtasks /create /tn "AutoHedge_Live_Stop_1505" /tr "powershell.exe -ExecutionPolicy Bypass -File \"%~dp0stop_live_monitor.ps1\"" /sc weekly /d MON,TUE,WED,THU,FRI /st 15:05 /ru "%USERNAME%" /rl HIGHEST /f
echo.

echo ========================================
echo   部署完成！
echo ========================================
echo.
echo 任务列表：
echo   1. AutoHedge_PreMarket_0700 - 盘前检查（7:00）
echo   2. AutoHedge_Live_Start_0925 - 启动盘中监控（9:25）
echo   3. AutoHedge_Live_Stop_1505 - 停止盘中监控（15:05）
echo.
echo 查看任务: schtasks /query /tn "AutoHedge_*"
echo 手动运行: 双击对应 bat 文件
echo 删除任务: schtasks /delete /tn "任务名" /f
echo.
pause
