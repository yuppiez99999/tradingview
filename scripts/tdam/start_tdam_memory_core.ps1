<#
.SYNOPSIS
  TDAM MemoryCore 启动脚本 (兼容入口, 实际调用 tdam_service_wrapper.ps1)
.DESCRIPTION
  v2: 不再前台启动 node, 改为调用 tdam_service_wrapper.ps1 start。
  解决 "关终端即停" 问题: wrapper 用 Start-Process 后台启动 + 日志落盘。

  旧版直接 node --import tsx src/gateway/server.ts 前台运行,
  关闭 Trae CN 终端即停止服务, 且无日志落盘。已废弃。

  首次部署需先运行 init_tdam_admin.ps1 创建 admin user (生成 .admin-credentials.json)。
.PARAMETER LlmApiKey
  LLM API Key (可选, 不传则不启用 capture/extraction 类接口)。
  若需启用, 请写入 E:\TDAM\MemoryCore\.env.local 文件。
.EXAMPLE
  .\start_tdam_memory_core.ps1
#>
param(
    [string]$LlmApiKey = ""
)

$ErrorActionPreference = "Stop"
$WRAPPER = "$PSScriptRoot\tdam_service_wrapper.ps1"

if (-not (Test-Path $WRAPPER)) {
    Write-Host "[ERROR] 包装器脚本不存在: $WRAPPER" -ForegroundColor Red
    exit 1
}

# 可选: 把 LlmApiKey 写入 .env.local (若提供)
if ($LlmApiKey) {
    $envFile = "E:\TDAM\MemoryCore\.env.local"
    @"
TDAI_LLM_API_KEY=$LlmApiKey
TDAI_LLM_BASE_URL=https://api.deepseek.com/v1
TDAI_LLM_MODEL=deepseek-chat
"@ | Set-Content -Path $envFile -Encoding ASCII
    Write-Host "[OK] LLM API Key 已写入: $envFile" -ForegroundColor Green
}

# 调用 wrapper start
& powershell -NoProfile -ExecutionPolicy Bypass -File $WRAPPER start
