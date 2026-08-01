@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ============================================================
echo   v8.5 机构级量化交易系统 - 统一启动菜单
echo   评级: A- (90分) | 内核: 10/11 完成 (90.9%)
echo ============================================================
echo.
echo [1] 每日完整工作流 (盘前检查 + 对冲联动 + 盘后报告)
echo [2] 盘前检查 (Pre-Market Check)
echo [3] 每日建仓 + 对冲联动 (Build + Hedge)
echo [4] 盘中决策 (Intraday Decision)
echo [5] 盘后报告 (Post-Market Report)
echo [6] 半日报告 (Half-Day Report)
echo [7] 实时监控调度器 (Live Scheduler)
echo [8] 停止监控 (Stop Scheduler)
echo [9] 系统健康检查 (Health Check)
echo [0] 退出 (Exit)
echo.
echo ============================================================
echo.

set /p choice="请选择操作 [0-9]: "

if "%choice%"=="1" goto daily_full
if "%choice%"=="2" goto pre_market
if "%choice%"=="3" goto build_hedge
if "%choice%"=="4" goto intraday
if "%choice%"=="5" goto post_market
if "%choice%"=="6" goto half_day
if "%choice%"=="7" goto live_scheduler
if "%choice%"=="8" goto stop_scheduler
if "%choice%"=="9" goto health_check
if "%choice%"=="0" goto end

echo 无效选择,请重试!
pause
exit /b 1

:daily_full
echo.
echo [1/3] 执行盘前检查...
call :run_script daily_workflow.py "盘前检查"
echo.
echo [2/3] 执行建仓 + 对冲联动...
call :run_script daily_build_and_hedge.py "每日建仓 + 对冲联动"
echo.
echo [3/3] 执行盘后报告...
call :run_script generate_daily_report.py "盘后报告"
echo.
echo 每日完整工作流执行完毕!
goto end

:pre_market
echo.
echo 执行盘前检查...
call :run_script daily_workflow.py "盘前检查"
goto end

:build_hedge
echo.
echo 执行每日建仓 + 对冲联动...
call :run_script daily_build_and_hedge.py "每日建仓 + 对冲联动"
goto end

:intraday
echo.
echo 执行盘中决策...
call :run_script intraday_decision.py "盘中决策"
goto end

:post_market
echo.
echo 执行盘后报告...
call :run_script generate_daily_report.py "盘后报告"
goto end

:half_day
echo.
echo 执行半日报告...
call :run_script hn_daily.py "半日报告"
goto end

:live_scheduler
echo.
echo 启动实时监控调度器...
call :run_script live_scheduler.py --start "实时监控调度器"
goto end

:stop_scheduler
echo.
echo 停止实时监控调度器...
call :run_script live_scheduler.py --stop "停止调度器"
goto end

:health_check
echo.
echo 执行系统健康检查...
call :run_script core_modules_check.py "系统健康检查"
goto end

:run_script
echo    脚本: %~1
echo    描述: %~2
REM B1.4: 副本删除后, generate_daily_report.py 改为调用根目录版
if exist "%~dp0%~1" (
    python "%~dp0%~1" 2>&1 | tee logs\%~n1_%date:~0,4%%date:~5,2%%date:~8,2%.log
) else if exist "%~dp0..\%~1" (
    python "%~dp0..\%~1" 2>&1 | tee logs\%~n1_%date:~0,4%%date:~5,2%%date:~8,2%.log
) else (
    echo [ERROR] 脚本不存在: %~1
    exit /b 1
)
if !errorlevel! equ 0 (
    echo [SUCCESS] %~2 执行成功!
) else (
    echo [ERROR] %~2 执行失败,请查看日志!
)
echo.
exit /b 0

:end
echo.
echo ============================================================
echo   v8.5 系统退出 | 感谢使用机构级量化交易平台
echo ============================================================
pause
exit /b 0
