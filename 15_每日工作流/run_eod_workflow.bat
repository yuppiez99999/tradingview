@echo off
REM ============================================================
REM 每日收盘工作流批处理脚本 (EOD Workflow Launcher)
REM ============================================================
REM 功能:
REM   1. 生成收盘盈亏报告 (DeepSeek AI 决策建议)
REM   2. 生成次日交易计划
REM   3. 应用 DeepSeek 决策到次日交易计划
REM   4. 执行 EOD 四 Guard 风控链
REM   5. 归档所有报告到 每日报告归档/YYYY-MM-DD/
REM
REM 调度: Windows 计划任务每日 15:30 自动执行
REM 手动: 双击此批处理或在命令行运行
REM ============================================================

setlocal enabledelayedexpansion

REM 项目路径配置
set PROJECT_ROOT=E:\各种PY程序\28-终极量化交易系统8.4
set PYTHON_EXE=C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe
set EOD_SCRIPT=%PROJECT_ROOT%\15_每日工作流\run_daily_eod_workflow.py
set LOG_DIR=%PROJECT_ROOT%\logs

REM 创建日志目录
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM 使用 PowerShell 获取标准日期格式 (避免区域设置问题)
for /f "tokens=*" %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%i
for /f "tokens=*" %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set TIMESTAMP=%%i

set LOG_FILE=%LOG_DIR%\eod_workflow_%TODAY%.log

echo ============================================================
echo   每日收盘工作流启动 (EOD Workflow)
echo   日期: %TODAY%
echo   时间: %time%
echo   日志: %LOG_FILE%
echo ============================================================

REM 验证 Python 解释器
if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python 解释器不存在: %PYTHON_EXE%
    echo 请安装 Python 3.11+ 到指定路径, 或修改本脚本中的 PYTHON_EXE 变量
    pause
    exit /b 1
)

REM 验证 EOD 脚本
if not exist "%EOD_SCRIPT%" (
    echo [ERROR] EOD 工作流脚本不存在: %EOD_SCRIPT%
    pause
    exit /b 1
)

REM 切换到项目根目录
cd /d "%PROJECT_ROOT%"

REM 执行 EOD 工作流 (Python 脚本内部已写日志, 这里同时重定向到批处理日志)
echo.
echo [INFO] 开始执行 EOD 工作流...
echo [CMD] "%PYTHON_EXE%" "%EOD_SCRIPT%" %*
echo.

"%PYTHON_EXE%" "%EOD_SCRIPT%" %* >> "%LOG_FILE%" 2>&1
set EXIT_CODE=%errorlevel%

REM 同步输出日志最后 50 行到控制台
powershell -NoProfile -Command "Get-Content '%LOG_FILE%' -Tail 50" 2>nul

if %EXIT_CODE% geq 1 (
    echo.
    echo [WARN] EOD 工作流执行完成, 但存在部分失败 (exit code: %EXIT_CODE%)
    echo [INFO] 详细日志: %LOG_FILE%
) else (
    echo.
    echo [OK] EOD 工作流全部阶段执行成功
)

echo.
echo ============================================================
echo   EOD 工作流结束
echo   结束时间: %time%
echo   日志文件: %LOG_FILE%
echo ============================================================

REM 如果是计划任务调用 (SCHEDULED_TASK 环境变量), 不暂停; 双击运行时暂停
if defined SCHEDULED_TASK (
    exit /b %EXIT_CODE%
) else (
    pause
    exit /b %EXIT_CODE%
)
