@echo off
chcp 65001 >nul
REM ============================================================
REM 注册下周自动交易任务计划 (2026-07-27 ~ 2026-07-31)
REM 需要管理员权限运行此脚本
REM
REM 版本历史:
REM   v8.6.4 (2026-07-26): 新增 QuantPipelineFactor_06AM 离线因子信号生成任务
REM   v8.6.5 (2026-07-26): P0-F 修复 — 添加 /rl HIGHEST /ru SYSTEM
REM     原始 bug: 任务以 Interactive only 模式注册, 用户登出/锁屏时不触发
REM     2026-07-26 审计发现 4 个任务 Last Run Time=1999/11/30 (从未运行)
REM     修复: 改为 SYSTEM 账户 + 最高权限, 无需用户登录即可触发
REM   v8.6.10 (2026-07-26): P1 修复 — 彻底断根 SYSTEM 用户 exit code=1 问题
REM     问题: SYSTEM 用户 PATH 环境变量缺失, py launcher 找不到 Python
REM     修复: 所有任务改用 junction 路径 C:\QuantSys + 显式 Python 3.14 路径
REM     新增: PATH 防御性设置 + 日志记录 + 错误处理
REM ============================================================

REM v8.6.10: 使用 junction 路径 (避免中文路径编码问题)
set "JUNCTION_ROOT=C:\QuantSys"
set "PIPELINE_BAT=%JUNCTION_ROOT%\run_pipeline_factor_task.bat"
set "WORKFLOW_BAT=%JUNCTION_ROOT%\run_workflow_task.bat"

REM 检查 junction 是否存在
if not exist "%JUNCTION_ROOT%" (
    echo [ERROR] junction 路径不存在: %JUNCTION_ROOT%
    echo [ERROR] 请先创建 junction: mklink /J C:\QuantSys "e:\各种PY程序\28-终极量化交易系统8.4"
    pause
    exit /b 1
)

REM 检查批处理文件是否存在
if not exist "%PIPELINE_BAT%" (
    echo [ERROR] 批处理文件不存在: %PIPELINE_BAT%
    echo [ERROR] 请先复制 run_pipeline_factor_task.bat 到 C:\QuantSys\
    pause
    exit /b 1
)

if not exist "%WORKFLOW_BAT%" (
    echo [ERROR] 批处理文件不存在: %WORKFLOW_BAT%
    echo [ERROR] 请先复制 run_workflow_task.bat 到 C:\QuantSys\
    pause
    exit /b 1
)

echo [OK] junction 路径检查通过: %JUNCTION_ROOT%
echo [OK] 批处理文件检查通过:
echo   - %PIPELINE_BAT%
echo   - %WORKFLOW_BAT%
echo.

REM 删除旧任务(如果存在)
schtasks /delete /tn "QuantPipelineFactor_06AM" /f 2>nul
schtasks /delete /tn "QuantWorkflow_07AM" /f 2>nul
schtasks /delete /tn "QuantMorning_0930" /f 2>nul
schtasks /delete /tn "QuantAfternoon_1400" /f 2>nul

REM ============================================================
REM v8.6.10 P1 修复: 注册 4 个任务 (SYSTEM 账户 + 最高权限 + junction 路径)
REM 关键参数:
REM   /rl HIGHEST  — 最高权限运行 (KillSwitch fail-closed 强平需要)
REM   /ru SYSTEM   — 系统账户运行 (用户登出/锁屏时仍可触发)
REM   /f           — 强制覆盖已存在的任务
REM
REM v8.6.10 改进:
REM   - 所有任务使用 junction 路径 C:\QuantSys (避免中文路径编码问题)
REM   - 所有任务使用显式 Python 3.14 路径 (避免 PATH 环境变量缺失)
REM   - 所有任务写入日志文件 (C:\QuantSys\logs\)
REM ============================================================

REM 06:00 离线生成 Pipeline 因子组合信号
schtasks /create /tn "QuantPipelineFactor_06AM" /tr "%PIPELINE_BAT%" /sc weekly /d MON,TUE,WED,THU,FRI /st 06:00 /rl HIGHEST /ru SYSTEM /f

REM 07:00 每日工作流 (会读取 06:00 生成的因子信号)
schtasks /create /tn "QuantWorkflow_07AM" /tr "%WORKFLOW_BAT% workflow" /sc weekly /d MON,TUE,WED,THU,FRI /st 07:00 /rl HIGHEST /ru SYSTEM /f

REM 09:30 早盘建仓
schtasks /create /tn "QuantMorning_0930" /tr "%WORKFLOW_BAT% morning" /sc weekly /d MON,TUE,WED,THU,FRI /st 09:30 /rl HIGHEST /ru SYSTEM /f

REM 14:00 午盘建仓
schtasks /create /tn "QuantAfternoon_1400" /tr "%WORKFLOW_BAT% afternoon" /sc weekly /d MON,TUE,WED,THU,FRI /st 14:00 /rl HIGHEST /ru SYSTEM /f

echo.
echo === 任务计划注册完成 (4个任务, v8.6.10 P1 修复) ===
echo 关键改进 (v8.6.10):
echo   - junction 路径: C:\QuantSys (避免中文路径编码问题)
echo   - 显式 Python 3.14 路径 (避免 PATH 环境变量缺失)
echo   - PATH 防御性设置 + PYTHONPATH 环境变量
echo   - 日志记录: C:\QuantSys\logs\
echo   - /rl HIGHEST: 最高权限运行 (KillSwitch 强平可执行)
echo   - /ru SYSTEM:  系统账户运行 (用户登出/锁屏时仍可触发)
echo.
echo === 验证任务状态 ===
echo.
echo [QuantPipelineFactor_06AM]
schtasks /query /tn "QuantPipelineFactor_06AM" /fo list | findstr /i "Task To Run\|Run As User\|Next Run\|Status"
echo.
echo [QuantWorkflow_07AM]
schtasks /query /tn "QuantWorkflow_07AM" /fo list | findstr /i "Task To Run\|Run As User\|Next Run\|Status"
echo.
echo [QuantMorning_0930]
schtasks /query /tn "QuantMorning_0930" /fo list | findstr /i "Task To Run\|Run As User\|Next Run\|Status"
echo.
echo [QuantAfternoon_1400]
schtasks /query /tn "QuantAfternoon_1400" /fo list | findstr /i "Task To Run\|Run As User\|Next Run\|Status"
echo.
echo ============================================================
echo 验证方法:
echo   1. 手动触发: schtasks /run /tn "QuantPipelineFactor_06AM"
echo   2. 检查日志: type C:\QuantSys\logs\pipeline_factor_offline.log
echo   3. 检查结果: schtasks /query /tn "QuantPipelineFactor_06AM" /v /fo LIST ^| findstr "Last Result"
echo   4. 检查信号: dir C:\QuantSys\models\pipeline_factor_signals\*.json
echo ============================================================
pause
