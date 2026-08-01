#!/usr/bin/env pwsh
# service_manager.ps1
# Windows 服务化管理脚本
param(
    [ValidateSet("install", "uninstall", "start", "stop", "restart", "status")]
    [string]$Action = "status"
)

$ErrorActionPreference = "Stop"
$ServiceName = "AutoHedgeExecutor"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
$Wrapper = Join-Path $ProjectDir "_archive_dead_code\service_wrapper.py"  # 已归档，路径更新于 2026-07-09。如已迁移至 v8.3_institutional，请改用该目录下的对应脚本。

function Write-Info($msg) { Write-Host "[INFO] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Err($msg) { Write-Host "[ERROR] $msg" -ForegroundColor Red }

switch ($Action) {
    "install" {
        Write-Info "安装 Windows 服务: $ServiceName"
        & $Python $Wrapper install
        Write-Info "启动服务..."
        Start-Service -Name $ServiceName -ErrorAction SilentlyContinue
        Write-Info "服务已安装并启动"
    }
    "uninstall" {
        Write-Warn "卸载服务: $ServiceName"
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        & $Python $Wrapper uninstall
        Write-Info "服务已卸载"
    }
    "start" {
        Write-Info "启动服务: $ServiceName"
        Start-Service -Name $ServiceName -ErrorAction SilentlyContinue
        Write-Info "服务启动命令已发送"
    }
    "stop" {
        Write-Warn "停止服务: $ServiceName"
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        Write-Info "服务已停止"
    }
    "restart" {
        Write-Warn "重启服务: $ServiceName"
        Restart-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        Write-Info "服务已重启"
    }
    "status" {
        $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
        if ($svc) {
            Write-Info "服务状态: $($svc.Status)"
        } else {
            Write-Warn "服务未安装"
        }
    }
}
