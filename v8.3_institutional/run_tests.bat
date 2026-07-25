@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ============================================================
echo   v8.5 单元测试运行器
echo   覆盖模块: VegaMonitor / LiquidityMonitor / EVTTailRisk
echo            FactorDecayMonitor / ShadowAccount / PurgedKFoldCV
echo            DataPipeline / TimeSync / EnvironmentIsolation
echo ============================================================
echo.

set SCRIPT_DIR=%~dp0
set PYTHON=python

if not exist "%PYTHON%" (
    echo ERROR: Python 解释器不存在
    pause
    exit /b 1
)

echo [1/2] 运行 v8.5 模块单元测试...
"%PYTHON%" -m pytest "%SCRIPT_DIR%tests\test_v85_modules.py" -v --tb=short --color=full 2>&1
set TEST_RESULT=!errorlevel!

echo.
echo [2/2] 运行所有测试套件...
"%PYTHON%" -m unittest discover -s "%SCRIPT_DIR%tests" -p "test_*.py" -v 2>&1 | tee logs\test_suite_%date:~0,4%%date:~5,2%%date:~8,2%.log

echo.
if !TEST_RESULT! equ 0 (
    echo [SUCCESS] 所有测试通过!
) else (
    echo [FAILURE] 部分测试失败,请查看输出
)
echo.
pause
