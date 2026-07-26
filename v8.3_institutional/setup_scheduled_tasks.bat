@echo off
chcp 65001 >nul
REM ============================================================
REM 注册下周自动交易任务计划 (2026-07-27 ~ 2026-07-31)
REM 需要管理员权限运行此脚本
REM v8.6.4 (2026-07-26): 新增 QuantPipelineFactor_06AM 离线因子信号生成任务
REM v8.6.5 (2026-07-26): P0-F 修复 — 添加 /rl HIGHEST /ru SYSTEM
REM   原始 bug: 任务以 Interactive only 模式注册, 用户登出/锁屏时不触发
REM   2026-07-26 审计发现 4 个任务 Last Run Time=1999/11/30 (从未运行)
REM   修复: 改为 SYSTEM 账户 + 最高权限, 无需用户登录即可触发
REM   注意: SYSTEM 账户下 py launcher 仍可用 (系统级注册)
REM ============================================================

REM P0-F 修复: 使用通用文件名, 避免每周手动更新 bat 文件名
REM 原始: set BAT_PATH=e:\各种PY程序\28-终极量化交易系统8.4\v8.3_institutional\run_weekly_auto_20260727.bat
REM 修复: 复制 run_weekly_auto_20260727.bat 到通用名 run_weekly_auto.bat
set BAT_PATH=e:\各种PY程序\28-终极量化交易系统8.4\v8.3_institutional\run_weekly_auto.bat
set PROJECT_ROOT=e:\各种PY程序\28-终极量化交易系统8.4
set SOURCE_BAT=e:\各种PY程序\28-终极量化交易系统8.4\v8.3_institutional\run_weekly_auto_20260727.bat

REM 复制到通用名 (如果源文件存在且目标不存在或源文件较新)
if exist "%SOURCE_BAT%" (
    copy /Y "%SOURCE_BAT%" "%BAT_PATH%" >nul
    echo [OK] 已复制到通用名: run_weekly_auto.bat
) else (
    echo [WARN] 源 bat 文件不存在: %SOURCE_BAT%
    echo [WARN] 请先运行 v8.3_institutional 目录下的周计划生成脚本
)

REM 删除旧任务(如果存在)
schtasks /delete /tn "QuantPipelineFactor_06AM" /f 2>nul
schtasks /delete /tn "QuantWorkflow_07AM" /f 2>nul
schtasks /delete /tn "QuantMorning_0930" /f 2>nul
schtasks /delete /tn "QuantAfternoon_1400" /f 2>nul

REM ============================================================
REM v8.6.5 P0-F 修复: 注册4个任务 (SYSTEM 账户 + 最高权限)
REM 关键参数:
REM   /rl HIGHEST  — 最高权限运行 (KillSwitch fail-closed 强平需要)
REM   /ru SYSTEM   — 系统账户运行 (用户登出/锁屏时仍可触发)
REM   /f           — 强制覆盖已存在的任务
REM ============================================================

REM 06:00 离线生成 Pipeline 因子组合信号 (v8.6.4 P0-A 深度修复)
schtasks /create /tn "QuantPipelineFactor_06AM" /tr "py -3 %PROJECT_ROOT%\scripts\run_pipeline_factor_offline.py" /sc weekly /d MON,TUE,WED,THU,FRI /st 06:00 /rl HIGHEST /ru SYSTEM /f

REM 07:00 每日工作流 (会读取 06:00 生成的因子信号)
schtasks /create /tn "QuantWorkflow_07AM" /tr "%BAT_PATH% workflow" /sc weekly /d MON,TUE,WED,THU,FRI /st 07:00 /rl HIGHEST /ru SYSTEM /f

REM 09:30 早盘建仓
schtasks /create /tn "QuantMorning_0930" /tr "%BAT_PATH% morning" /sc weekly /d MON,TUE,WED,THU,FRI /st 09:30 /rl HIGHEST /ru SYSTEM /f

REM 14:00 午盘建仓
schtasks /create /tn "QuantAfternoon_1400" /tr "%BAT_PATH% afternoon" /sc weekly /d MON,TUE,WED,THU,FRI /st 14:00 /rl HIGHEST /ru SYSTEM /f

echo.
echo === 任务计划注册完成 (4个任务, v8.6.5 P0-F 修复) ===
echo 关键改进:
echo   - /rl HIGHEST: 最高权限运行 (KillSwitch 强平可执行)
echo   - /ru SYSTEM:  系统账户运行 (用户登出/锁屏时仍可触发)
echo   - 通用 bat 文件名: run_weekly_auto.bat (避免每周手动重命名)
echo.
echo === 验证任务状态 ===
schtasks /query /tn "QuantPipelineFactor_06AM" /fo list | findstr /i "Task To Run\|Run As User\|Next Run\|Status"
schtasks /query /tn "QuantWorkflow_07AM" /fo list | findstr /i "Task To Run\|Run As User\|Next Run\|Status"
schtasks /query /tn "QuantMorning_0930" /fo list | findstr /i "Task To Run\|Run As User\|Next Run\|Status"
schtasks /query /tn "QuantAfternoon_1400" /fo list | findstr /i "Task To Run\|Run As User\|Next Run\|Status"
echo.
echo ============================================================
echo 注意: 如需验证 SYSTEM 账户能否访问 Python, 可手动运行:
echo   schtasks /run /tn "QuantPipelineFactor_06AM"
echo 然后检查 %PROJECT_ROOT%\logs\pipeline_factor_offline.log
echo ============================================================
pause
