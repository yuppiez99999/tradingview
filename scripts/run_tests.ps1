# -*- coding: utf-8 -*-
# ============================================================
# 分层测试运行器 (v8.6.12)
# ============================================================
# 用途: 本地推送前快速验证,与 CI 流水线分层一致
#
# 用法:
#   .\scripts\run_tests.ps1 fast        # 快测层 (契约+单元回归) <1s
#   .\scripts\run_tests.ps1 slow        # 慢测层 (integration 标记) ~10s
#   .\scripts\run_tests.ps1 contract   # 仅契约测试 <0.3s
#   .\scripts\run_tests.ps1 regression # 仅回归测试 (含 integration)
#   .\scripts\run_tests.ps1 all        # 全部 (契约+回归+integration)
#
# 退出码:
#   0 = 全部通过
#   1 = 有失败
#   2 = 脚本异常
# ============================================================

param(
    [Parameter(Position = 0)]
    [ValidateSet("fast", "slow", "contract", "regression", "all")]
    [string]$Layer = "fast"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Layer: $Layer" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

switch ($Layer) {
    "fast" {
        Write-Host "[fast] contract + unit regression (skip integration)" -ForegroundColor Yellow
        Write-Host "scope: PR gate quick check (<1s)" -ForegroundColor Gray
        Write-Host ""
        python -m pytest tests/test_data_contracts.py tests/test_regression_bugfixes.py -v --tb=short -m "not integration and not slow"
    }
    "slow" {
        Write-Host "[slow] integration-marked regression tests" -ForegroundColor Yellow
        Write-Host "scope: external dependency (TDX/Wind MCP) (~10s)" -ForegroundColor Gray
        Write-Host ""
        python -m pytest tests/test_regression_bugfixes.py -v --tb=short -m "integration and not slow"
    }
    "contract" {
        Write-Host "[contract] data contract tests only" -ForegroundColor Yellow
        Write-Host "scope: JSON schema drift check (<0.3s)" -ForegroundColor Gray
        Write-Host ""
        python -m pytest tests/test_data_contracts.py -v --tb=short -m "contract"
    }
    "regression" {
        Write-Host "[regression] all regression tests (incl integration)" -ForegroundColor Yellow
        Write-Host "scope: full regression (~10s)" -ForegroundColor Gray
        Write-Host ""
        python -m pytest tests/test_regression_bugfixes.py -v --tb=short -m "regression"
    }
    "all" {
        Write-Host "[all] contract + regression (incl integration)" -ForegroundColor Yellow
        Write-Host "scope: full validation (~10s)" -ForegroundColor Gray
        Write-Host ""
        python -m pytest tests/test_data_contracts.py tests/test_regression_bugfixes.py -v --tb=short
    }
}

$exitCode = $LASTEXITCODE
Write-Host ""
if ($exitCode -eq 0) {
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "  [PASS] Layer [$Layer] all tests passed" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
} else {
    Write-Host "============================================================" -ForegroundColor Red
    Write-Host "  [FAIL] Layer [$Layer] has failures (exit=$exitCode)" -ForegroundColor Red
    Write-Host "============================================================" -ForegroundColor Red
}

exit $exitCode
