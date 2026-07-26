@echo off
REM ============================================================
REM v7.x 旧版本定时任务清理启动器 (需管理员权限)
REM v8.6.8 P1-LIVE-07 (2026-07-26)
REM ============================================================
REM
REM 使用方法:
REM   1. 右键此 .bat 文件 -> 以管理员身份运行
REM   2. 或在管理员 PowerShell 中执行:
REM      powershell -ExecutionPolicy Bypass -File scripts\cleanup_v7x_legacy_tasks.ps1
REM
REM 此脚本会:
REM   1. 备份 13 个 v7.x 旧任务到 docs/legacy_tasks_backup_YYYYMMDD/
REM   2. 列出当前生产任务 (确认保留)
REM   3. 删除 v7.5/v7.6 旧版本任务
REM   4. 输出最终任务清单
REM ============================================================

REM 检查管理员权限
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] 需要管理员权限运行此脚本!
    echo.
    echo 请右键此 .bat 文件, 选择 "以管理员身份运行"
    echo 或在管理员 PowerShell 中执行:
    echo   powershell -ExecutionPolicy Bypass -File scripts\cleanup_v7x_legacy_tasks.ps1
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  v7.x 旧版本定时任务清理 (需管理员权限)
echo  v8.6.8 P1-LIVE-07 (2026-07-26)
echo ============================================================
echo.

REM 切换到脚本所在目录的上级 (项目根目录)
cd /d "%~dp0.."

REM 执行 PowerShell 脚本
powershell -ExecutionPolicy Bypass -File "%~dp0cleanup_v7x_legacy_tasks.ps1"

echo.
echo ============================================================
echo  清理完成. 详细日志见 docs/legacy_tasks_backup_YYYYMMDD\
echo ============================================================
pause
