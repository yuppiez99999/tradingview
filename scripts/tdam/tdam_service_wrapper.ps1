<#
.SYNOPSIS
  TDAM MemoryCore 服务包装器 (Windows 本地部署)
.DESCRIPTION
  解决 "Trae CN 终端前台进程, 关终端即停" 问题。
  通过 Start-Process 后台启动 node, stdout/stderr 重定向到日志文件, PID 落盘。
  支持日志轮转 (保留 7 天)。
  与 register_tdam_task.ps1 配合, 由 Windows 任务计划程序托管。

  子命令:
    start  - 后台启动 TDAM 服务
    stop   - 停止 TDAM 服务
    status - 查看运行状态
    restart - 重启 (stop + start)
    logs   - 查看最近日志 (tail -50)

  鉴权配置:
    $env:TDAI_GATEWAY_API_KEY 为空时, TDAM 服务端禁用鉴权 (本地默认, 安全)。
    非空时, 客户端需带 Authorization: Bearer <key>。
    本包装器读取 E:\TDAM\MemoryCore\.env.local (若存在) 注入环境变量。
.PARAMETER Command
  子命令: start | stop | status | restart | logs
.EXAMPLE
  .\tdam_service_wrapper.ps1 start
  .\tdam_service_wrapper.ps1 status
  .\tdam_service_wrapper.ps1 stop
  .\tdam_service_wrapper.ps1 logs
#>
param(
    [Parameter(Position=0)]
    [ValidateSet("start","stop","status","restart","logs")]
    [string]$Command = "status"
)

$ErrorActionPreference = "Stop"

# ============================================================
# 路径常量
# ============================================================
$TDAM_DIR = "E:\TDAM\MemoryCore"
$CONFIG_FILE = "$TDAM_DIR\config.local.yaml"
$DATA_DIR = "E:\tdam-data\memory"
$LOG_DIR = "E:\tdam-data\logs"
$PID_FILE = "$DATA_DIR\gateway.pid"
$ENV_FILE = "$TDAM_DIR\.env.local"
$PORT = 8420
$NODE_EXE = "C:\Program Files\nodejs\node.exe"

# ============================================================
# 辅助函数
# ============================================================
function Write-Info  { param([string]$Msg) Write-Host "[INFO]  $Msg" -ForegroundColor Cyan }
function Write-OK    { param([string]$Msg) Write-Host "[OK]    $Msg" -ForegroundColor Green }
function Write-Warn  { param([string]$Msg) Write-Host "[WARN]  $Msg" -ForegroundColor Yellow }
function Write-Err   { param([string]$Msg) Write-Host "[ERROR] $Msg" -ForegroundColor Red }

function Load-Env {
    # 从 .env.local 读取环境变量 (若存在)
    if (Test-Path $ENV_FILE) {
        Get-Content $ENV_FILE | ForEach-Object {
            $line = $_.Trim()
            if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
                $parts = $line -split '=', 2
                $key = $parts[0].Trim()
                $val = $parts[1].Trim().Trim('"').Trim("'")
                Set-Item -Path "Env:$key" -Value $val
            }
        }
        Write-Info "已加载环境变量: $ENV_FILE"
    }
}

function Test-PortListening {
    param([int]$PortNum)
    $conn = Get-NetTCPConnection -LocalPort $PortNum -State Listen -ErrorAction SilentlyContinue
    return [bool]$conn
}

function Get-CurrentPid {
    if (Test-Path $PID_FILE) {
        try {
            $savedPid = [int](Get-Content $PID_FILE -Raw).Trim()
            $proc = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
            if ($proc -and $proc.ProcessName -eq "node") {
                return $savedPid
            }
        } catch { }
    }
    # 兜底: 通过端口查找
    $conn = Get-NetTCPConnection -LocalPort $PORT -State Listen -ErrorAction SilentlyContinue
    if ($conn) {
        return $conn[0].OwningProcess
    }
    return $null
}

function New-LogPath {
    # 日志文件名: gateway-YYYYMMDD.log
    $dateStr = (Get-Date).ToString("yyyyMMdd")
    return "$LOG_DIR\gateway-$dateStr.log"
}

function Invoke-LogRotation {
    # 保留最近 7 天日志
    if (-not (Test-Path $LOG_DIR)) { return }
    $cutoff = (Get-Date).AddDays(-7)
    Get-ChildItem $LOG_DIR -Filter "gateway-*.log" | Where-Object {
        $_.LastWriteTime -lt $cutoff
    } | ForEach-Object {
        Remove-Item $_.FullName -Force
        Write-Info "日志轮转: 删除 $($_.Name)"
    }
}

# ============================================================
# 子命令: start
# ============================================================
function Invoke-Start {
    Write-Host "=== TDAM MemoryCore 启动 (后台模式) ===" -ForegroundColor Cyan

    # 1. 检查 node_modules
    if (-not (Test-Path "$TDAM_DIR\node_modules")) {
        Write-Err "node_modules 不存在, 请先运行: cd $TDAM_DIR; npm install"
        exit 1
    }

    # 2. 检查是否已在运行
    $currentPid = Get-CurrentPid
    if ($currentPid) {
        Write-Warn "TDAM 已在运行: PID=$currentPid (端口 $PORT 监听中)"
        return
    }

    # 3. 创建目录
    foreach ($d in @($DATA_DIR, $LOG_DIR)) {
        if (-not (Test-Path $d)) {
            New-Item -ItemType Directory -Path $d -Force | Out-Null
            Write-Info "创建目录: $d"
        }
    }

    # 4. 日志轮转
    Invoke-LogRotation

    # 5. 加载环境变量
    Load-Env

    # 6. 设置环境变量 (默认值, .env.local 未设置时用)
    if (-not $env:TDAI_GATEWAY_CONFIG) { $env:TDAI_GATEWAY_CONFIG = $CONFIG_FILE }
    if (-not $env:TDAI_GATEWAY_HOST)   { $env:TDAI_GATEWAY_HOST = "127.0.0.1" }
    if (-not $env:TDAI_GATEWAY_PORT)   { $env:TDAI_GATEWAY_PORT = "$PORT" }
    if (-not $env:TDAI_DATA_DIR)       { $env:TDAI_DATA_DIR = $DATA_DIR }
    if (-not $env:NODE_ENV)            { $env:NODE_ENV = "production" }
    if (-not $env:NODE_OPTIONS)        { $env:NODE_OPTIONS = "--max-old-space-size=1536" }

    # 7. 启动 node (后台, stdout/stderr 重定向)
    $logPath = New-LogPath
    Write-Info "日志文件: $logPath"
    Write-Info "配置文件: $CONFIG_FILE"
    Write-Info "数据目录: $DATA_DIR"
    Write-Info "端口: $PORT (127.0.0.1)"
    Write-Info "LLM Key: $(if ($env:TDAI_LLM_API_KEY) { '已设置' } else { '未设置 (capture/extraction 会 401)' })"
    Write-Info "Gateway Key: $(if ($env:TDAI_GATEWAY_API_KEY) { '已设置 (启用鉴权)' } else { '未设置 (禁用鉴权, 本地默认)' })"

    # stdout 和 stderr 必须分开文件 (PowerShell Start-Process 限制)
    $errLogPath = $logPath -replace '\.log$', '.err.log'

    $startParams = @{
        FilePath               = $NODE_EXE
        ArgumentList           = @("--import", "tsx", "src/gateway/server.ts")
        WorkingDirectory       = $TDAM_DIR
        WindowStyle            = "Hidden"
        PassThru               = $true
        RedirectStandardOutput = $logPath
        RedirectStandardError  = $errLogPath
    }

    try {
        $proc = Start-Process @startParams
        $procId = $proc.Id
    } catch {
        Write-Err "启动失败: $_"
        exit 1
    }

    # 8. PID 落盘
    Set-Content -Path $PID_FILE -Value $procId -Encoding ASCII -NoNewline
    Write-OK "进程已启动: PID=$procId"

    # 9. 等待健康检查 (最多 15 秒)
    Write-Info "等待健康检查..."
    $ready = $false
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 1
        if (Test-PortListening -PortNum $PORT) {
            try {
                $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$PORT/health" -Method GET -TimeoutSec 2 -ErrorAction Stop
                if ($resp) { $ready = $true; break }
            } catch { }
        }
    }

    if ($ready) {
        Write-OK "TDAM 服务已就绪: http://127.0.0.1:$PORT/health"
    } else {
        Write-Warn "服务启动超时 (15s), 请检查日志: $logPath"
    }
}

# ============================================================
# 子命令: stop
# ============================================================
function Invoke-Stop {
    Write-Host "=== TDAM MemoryCore 停止 ===" -ForegroundColor Cyan

    $currentPid = Get-CurrentPid
    if (-not $currentPid) {
        Write-Info "未发现运行中的 TDAM 进程"
        return
    }

    $proc = Get-Process -Id $currentPid -ErrorAction SilentlyContinue
    if ($proc) {
        Write-Info "终止进程: PID=$currentPid ($($proc.ProcessName))"
        Stop-Process -Id $currentPid -Force
        Start-Sleep -Seconds 1
    }

    # 清理 PID 文件
    if (Test-Path $PID_FILE) {
        Remove-Item $PID_FILE -Force
    }

    # 验证端口已释放
    if (Test-PortListening -PortNum $PORT) {
        Write-Warn "端口 $PORT 仍被占用, 可能需要手动清理"
    } else {
        Write-OK "端口 $PORT 已释放"
    }
}

# ============================================================
# 子命令: status
# ============================================================
function Invoke-Status {
    Write-Host "=== TDAM MemoryCore 状态 ===" -ForegroundColor Cyan

    $currentPid = Get-CurrentPid
    if (-not $currentPid) {
        Write-Warn "状态: 未运行"
        return
    }

    $proc = Get-Process -Id $currentPid -ErrorAction SilentlyContinue
    if (-not $proc) {
        Write-Warn "状态: PID 文件存在但进程已死 (PID=$currentPid)"
        return
    }

    $memMB = [math]::Round($proc.WorkingSet64 / 1MB, 1)
    $startTime = $proc.StartTime
    $uptime = (Get-Date) - $startTime

    Write-OK "状态: 运行中"
    Write-Host "  PID:        $currentPid"
    Write-Host "  内存:       ${memMB} MB"
    Write-Host "  启动时间:   $($startTime.ToString('yyyy-MM-dd HH:mm:ss'))"
    Write-Host "  已运行:     $($uptime.ToString('d\.hh\:mm\:ss'))"
    Write-Host "  端口:       $PORT (127.0.0.1)"

    # 健康检查
    try {
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$PORT/health" -Method GET -TimeoutSec 2 -ErrorAction Stop
        Write-OK "健康检查: 通过"
    } catch {
        Write-Warn "健康检查: 失败 ($($_.Exception.Message))"
    }

    # 今日日志
    $logPath = New-LogPath
    if (Test-Path $logPath) {
        $logSize = [math]::Round((Get-Item $logPath).Length / 1KB, 1)
        Write-Host "  今日日志:   $logPath (${logSize} KB)"
    }
}

# ============================================================
# 子命令: logs
# ============================================================
function Invoke-Logs {
    $logPath = New-LogPath
    if (-not (Test-Path $logPath)) {
        Write-Warn "今日日志不存在: $logPath"
        return
    }
    Write-Host "=== 最近 50 行日志 ($logPath) ===" -ForegroundColor Cyan
    Get-Content $logPath -Tail 50
}

# ============================================================
# 主入口
# ============================================================
switch ($Command) {
    "start"   { Invoke-Start }
    "stop"    { Invoke-Stop }
    "status"  { Invoke-Status }
    "restart" { Invoke-Stop; Start-Sleep -Seconds 2; Invoke-Start }
    "logs"    { Invoke-Logs }
}
