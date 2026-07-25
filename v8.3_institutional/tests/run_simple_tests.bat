@echo off
REM 简化版测试运行器批处理脚本
REM 不依赖外部包

setlocal enabledelayedexpansion

echo ========================================
echo 量化交易系统 - 简化版测试运行器
echo 生成时间: %date% %time%
echo ========================================
echo.

cd /d "%~dp0"

REM 检查Python环境
echo 检查Python环境...
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到Python环境
    pause
    exit /b 1
)

echo [信息] 发现Python环境
python --version
echo.

REM 运行测试
echo ========================================
echo 步骤1: 运行简化版测试
echo ========================================
echo.

python v8.3_institutional\tests\simple_test_runner.py v8.3_institutional\tests
set TEST_RESULT=%errorlevel%

echo.
echo ========================================
echo 步骤2: 运行覆盖率检查
echo ========================================
echo.

python v8.3_institutional\tests\simple_coverage_checker.py v8.3_institutional\src
set COVERAGE_RESULT=%errorlevel%

echo.
echo ========================================
echo 测试结果汇总
echo ========================================

if %TEST_RESULT% equ 0 (
    echo [✓] 测试通过
) else (
    echo [✗] 测试失败 (错误码: %TEST_RESULT%)
)

if %COVERAGE_RESULT% equ 0 (
    echo [✓] 覆盖率达标
) else (
    echo [✗] 覆盖率未达标 (错误码: %COVERAGE_RESULT%)
)

echo.
echo 详细结果请查看:
echo   - test_results.txt
echo   - coverage_report.txt
echo.

pause