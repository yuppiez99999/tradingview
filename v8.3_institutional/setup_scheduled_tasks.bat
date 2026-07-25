@echo off
chcp 65001 >nul
REM ============================================================
REM 注册下周自动交易任务计划 (2026-07-27 ~ 2026-07-31)
REM 需要管理员权限运行此脚本
REM ============================================================

set BAT_PATH=e:\各种PY程序\28-终极量化交易系统8.4\v8.3_institutional\run_weekly_auto_20260727.bat

REM 删除旧任务(如果存在)
schtasks /delete /tn "QuantWorkflow_07AM" /f 2>nul
schtasks /delete /tn "QuantMorning_0930" /f 2>nul
schtasks /delete /tn "QuantAfternoon_1400" /f 2>nul

REM 注册3个任务: 每个交易日 07:00/09:30/14:00
schtasks /create /tn "QuantWorkflow_07AM" /tr "%BAT_PATH% workflow" /sc weekly /d MON,TUE,WED,THU,FRI /st 07:00 /f
schtasks /create /tn "QuantMorning_0930" /tr "%BAT_PATH% morning" /sc weekly /d MON,TUE,WED,THU,FRI /st 09:30 /f
schtasks /create /tn "QuantAfternoon_1400" /tr "%BAT_PATH% afternoon" /sc weekly /d MON,TUE,WED,THU,FRI /st 14:00 /f

echo === 任务计划注册完成 ===
schtasks /query /tn "QuantWorkflow_07AM"
schtasks /query /tn "QuantMorning_0930"
schtasks /query /tn "QuantAfternoon_1400"
pause
