<#
.SYNOPSIS
  TDAM Memory Core 停止脚本 (Windows 本地部署)
.DESCRIPTION
  停止正在运行的 TDAM MemoryCore Node.js 进程。
  Phase 1' 盘中停止: 09:00 前执行此脚本停止 TDAM, 释放内存给交易系统。
.EXAMPLE
  .\stop_tdam_memory_core.ps1
#>

$ErrorActionPreference = "SilentlyContinue"

Write-Host "=== TDAM Memory Core 停止 ===" -ForegroundColor Cyan

# 查找并终止占用 8420 端口的进程
$connections = Get-NetTCPConnection -LocalPort 8420 -State Listen -ErrorAction SilentlyContinue
if ($connections) {
    $pid = $connections[0].OwningProcess
    $process = Get-Process -Id $pid -ErrorAction SilentlyContinue
    if ($process) {
        Write-Host "找到 TDAM 进程: PID=$pid ($($process.ProcessName))" -ForegroundColor Yellow
        Stop-Process -Id $pid -Force
        Write-Host "[OK] 已终止 PID=$pid" -ForegroundColor Green
    }
} else {
    # 也尝试通过进程名查找
    $nodeProcesses = Get-Process -Name "node" -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -match "gateway/server.ts" -or $_.CommandLine -match "tdam"
    }
    if ($nodeProcesses) {
        $nodeProcesses | ForEach-Object {
            Write-Host "终止 TDAM Node 进程: PID=$($_.Id)" -ForegroundColor Yellow
            Stop-Process -Id $_.Id -Force
        }
        Write-Host "[OK] 已终止 $($nodeProcesses.Count) 个进程" -ForegroundColor Green
    } else {
        Write-Host "[INFO] 未找到运行中的 TDAM 进程" -ForegroundColor Gray
    }
}

# 验证端口已释放
Start-Sleep -Seconds 1
$check = Get-NetTCPConnection -LocalPort 8420 -State Listen -ErrorAction SilentlyContinue
if (-not $check) {
    Write-Host "[OK] 端口 8420 已释放" -ForegroundColor Green
} else {
    Write-Host "[WARN] 端口 8420 仍被占用" -ForegroundColor Red
}
