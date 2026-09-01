# S10 影子账户每日净值计入 (wrapper: 先计算真实净值再计入)
# 调度: v84_ShadowS10Daily 17:46 周一至周五
$ErrorActionPreference = "Stop"
$PROJECT_ROOT = "E:\各种PY程序\28-终极量化交易系统8.4"
$PYTHON = "$PROJECT_ROOT\.venv\Scripts\python.exe"

Write-Host "=== S10 影子账户每日净值计入 $(Get-Date -Format 'yyyy-MM-dd HH:mm') ==="

# 1. 计算真实净值
Write-Host "[1/2] 计算 S10 真实净值..."
$navJson = & $PYTHON "$PROJECT_ROOT\scripts\compute_s10_nav.py" --output json
if ($LASTEXITCODE -ne 0) {
    Write-Host "compute_s10_nav.py 失败 (exit=$LASTEXITCODE), 回退到估算值"
    & $PYTHON "$PROJECT_ROOT\scripts\launch_etf_shadow.py" --daily --config shadow_etf_s10_candidate.json
    exit 0
}

$nav = ($navJson | ConvertFrom-Json).nav
$actualDate = ($navJson | ConvertFrom-Json).actual_date
Write-Host "真实净值: $nav (数据日期: $actualDate)"

# 2. 计入影子账户
Write-Host "[2/2] 计入影子账户..."
& $PYTHON "$PROJECT_ROOT\scripts\launch_etf_shadow.py" --daily --config shadow_etf_s10_candidate.json --nav $nav

Write-Host "=== 完成 ==="