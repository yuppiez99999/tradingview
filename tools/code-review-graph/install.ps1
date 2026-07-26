# install.ps1 — code-review-graph 安装脚本（Windows）
#
# 用法:
#   .\tools\code-review-graph\install.ps1              # 默认安装 + 注册 Claude Code
#   .\tools\code-review-graph\install.ps1 -SkipRegister # 仅安装, 不注册 MCP
#   .\tools\code-review-graph\install.ps1 -BuildGraph   # 安装后立即构建图谱
#
# 环境:
#   HTTPS_PROXY  代理地址（默认 http://127.0.0.1:7897）

param(
    [switch]$SkipRegister,
    [switch]$BuildGraph,
    [string]$Proxy = "http://127.0.0.1:7897"
)

$ErrorActionPreference = "Stop"

# 强制 TLS 1.2+（GitHub/PyPI 兼容）
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls13

Write-Host "=== code-review-graph 安装脚本 ===" -ForegroundColor Cyan
Write-Host "代理: $Proxy"
Write-Host "跳过注册: $SkipRegister"
Write-Host "立即构建: $BuildGraph"
Write-Host ""

# 1. 检查 uv 是否已安装
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] uv 未安装，请先执行: irm https://astral.sh/uv/install.ps1 | iex" -ForegroundColor Red
    exit 1
}

# 2. 配置代理
$env:HTTPS_PROXY = $Proxy
$env:HTTP_PROXY = $Proxy

# 3. 通过 uv tool 隔离安装
Write-Host "[1/3] 通过 uv tool 安装 code-review-graph..." -ForegroundColor Yellow
if (Get-Command code-review-graph -ErrorAction SilentlyContinue) {
    Write-Host "  已安装, 跳过"
} else {
    uv tool install code-review-graph
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] uv tool install 失败" -ForegroundColor Red
        exit 1
    }
}

# 4. 验证版本
Write-Host ""
Write-Host "[2/3] 验证安装..." -ForegroundColor Yellow
$version = code-review-graph --version 2>&1
Write-Host "  版本: $version"

# 5. 注册到 Claude Code（在项目根目录执行）
if (-not $SkipRegister) {
    Write-Host ""
    Write-Host "[3/3] 注册到 Claude Code..." -ForegroundColor Yellow

    # 切换到项目根目录（脚本所在目录的父父目录）
    $projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
    Push-Location $projectRoot
    try {
        code-review-graph install --platform claude-code -y --no-hooks
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[WARN] 注册失败, 可手动执行: code-review-graph install --platform claude-code -y" -ForegroundColor Yellow
        }
    } finally {
        Pop-Location
    }
}

# 6. 可选: 立即构建图谱
if ($BuildGraph) {
    Write-Host ""
    Write-Host "[BONUS] 构建知识图谱（首次约 3-10 分钟）..." -ForegroundColor Yellow
    $projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
    Push-Location $projectRoot
    try {
        code-review-graph build
    } finally {
        Pop-Location
    }
}

Write-Host ""
Write-Host "=== 安装完成 ===" -ForegroundColor Green
Write-Host ""
Write-Host "下一步:"
Write-Host "  1. code-review-graph build              # 构建图谱（首次）"
Write-Host "  2. code-review-graph status             # 查看图谱统计"
Write-Host "  3. code-review-graph query SignalFusionEngine  # 测试查询"
Write-Host "  4. 重启 Claude Code / Trae-CN 让 MCP 配置生效"
