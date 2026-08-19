# ============================================================
# install_qmt_rpc_service.ps1 — 在 Win 实盘机注册 qmt_rpc_server 为 NSSM 服务
# 用法 (管理员 PowerShell):
#   .\scripts\cloud\install_qmt_rpc_service.ps1 -Install
#   .\scripts\cloud\install_qmt_rpc_service.ps1 -Uninstall
#   .\scripts\cloud\install_qmt_rpc_service.ps1 -Status
# 前置: pip install fastapi uvicorn; NSSM 已下载到 PATH
# ============================================================
param(
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Status,
    [string]$ServiceName = "QmtRpcGateway",
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$projectRoot = "e:\各种PY程序\28-终极量化交易系统8.4"
$pythonExe   = "$projectRoot\.venv\Scripts\python.exe"
$script      = "$projectRoot\utils\execution\qmt_rpc_server.py"
$nssm        = "nssm"

function Check-Nssm {
    $cmd = Get-Command $nssm -ErrorAction SilentlyContinue
    if (-not $cmd) {
        Write-Host "[ERROR] NSSM 未找到。请下载 https://nssm.cc/release/nssm-2.24.zip 并放入 PATH" -ForegroundColor Red
        exit 1
    }
}

if ($Status) {
    Get-Service $ServiceName -ErrorAction SilentlyContinue | Format-Table Name, Status, StartType
    exit 0
}

if ($Uninstall) {
    Write-Host "[UNINSTALL] 停止并删除服务 $ServiceName ..." -ForegroundColor Yellow
    try { & $nssm stop $ServiceName } catch {}
    & $nssm remove $ServiceName confirm
    Write-Host "完成。" -ForegroundColor Green
    exit 0
}

if ($Install) {
    Check-Nssm
    if (-not (Test-Path $pythonExe)) { Write-Host "[ERROR] $pythonExe 不存在" -ForegroundColor Red; exit 1 }
    if (-not (Test-Path $script))    { Write-Host "[ERROR] $script 不存在" -ForegroundColor Red; exit 1 }

    Write-Host "[INSTALL] 注册 $ServiceName (端口 $Port) ..." -ForegroundColor Cyan

    # 环境变量从 .env 读 (此处仅传端口, 其余 QMT_* / QMT_RPC_TOKEN 在系统环境变量或 .env 设置)
    & $nssm install $ServiceName $pythonExe "$script --port $Port"
    & $nssm set $ServiceName AppDirectory $projectRoot
    & $nssm set $ServiceName AppStdout "$projectRoot\logs\qmt_rpc.out.log"
    & $nssm set $ServiceName AppStderr "$projectRoot\logs\qmt_rpc.err.log"
    & $nssm set $ServiceName AppRotateFiles 1
    & $nssm set $ServiceName AppRotateBytes 10485760
    & $nssm set $ServiceName Start SERVICE_AUTO_START
    & $nssm set $ServiceName Description "QMT RPC Gateway for cloud bridge (28量化系统)"

    # 启动
    & $nssm start $ServiceName
    Start-Sleep -Seconds 2
    $svc = Get-Service $ServiceName
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor $(if ($svc.Status -eq 'Running') {'Green'} else {'Red'})
    Write-Host "  服务 $ServiceName : $($svc.Status)" -ForegroundColor $(if ($svc.Status -eq 'Running') {'Green'} else {'Red'})
    Write-Host "============================================================"
    Write-Host "  端口: $Port"
    Write-Host "  日志: $projectRoot\logs\qmt_rpc.{out,err}.log"
    Write-Host "  健康检查: curl http://localhost:$Port/health -H 'X-Token: \$env:QMT_RPC_TOKEN'"
    Write-Host ""
    exit 0
}

Write-Host "用法: .\install_qmt_rpc_service.ps1 -Install | -Uninstall | -Status" -ForegroundColor Yellow