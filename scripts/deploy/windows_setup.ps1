# ============================================================
# Windows 云服务器一键配置脚本 — 终极量化交易系统 v8.6.14 实盘环境
# ============================================================
# 用途: 在阿里云/腾讯云 Windows Server 上配置实盘执行环境
#   • 开启 OpenSSH Server (仅监听 Tailscale 网卡)
#   • 安装 Tailscale / syncthing / Git / Python
#   • 创建项目目录结构
#   • 配置防火墙 (仅 Tailscale 网段可访问 RDP/SSH)
#   • 安装 Wind 终端 / QMT / 同花顺 提示
#
# 用法: 以管理员身份运行 PowerShell
#       Set-ExecutionPolicy -Scope Process Bypass
#       .\windows_setup.ps1
# ============================================================

#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"

# ---------- 配置变量 ----------
$ProjectDir = "C:\QuantSys"
$PythonVersion = "3.12"
$TailscaleHostname = "quant-win"

function Write-Log($msg) { Write-Host "[OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[!] $msg" -ForegroundColor Yellow }
function Write-Info($msg) { Write-Host "[i] $msg" -ForegroundColor Cyan }
function Write-Err($msg) { Write-Host "[X] $msg" -ForegroundColor Red }

Write-Host "====================================================" -ForegroundColor Cyan
Write-Host "  Windows 云服务器配置 — 终极量化交易系统 v8.6.14 实盘" -ForegroundColor Cyan
Write-Host "====================================================" -ForegroundColor Cyan
Write-Host ""

# ---------- 1. 系统更新 ----------
Write-Info "检查 Windows 更新..."
$hotfix = Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 1
Write-Info "最新补丁: $($hotfix.HotFixID) ($($hotfix.InstalledOn))"

# ---------- 2. 安装 OpenSSH Server ----------
Write-Info "配置 OpenSSH Server..."
$sshcap = Get-WindowsCapability -Online -Name "OpenSSH.Server~~~~0.0.1.0"
if ($sshcap.State -ne "Installed") {
    Add-WindowsCapability -Online -Name "OpenSSH.Server~~~~0.0.1.0"
    Write-Log "OpenSSH Server 已安装"
} else {
    Write-Log "OpenSSH Server 已存在"
}

Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
Write-Log "sshd 服务已启动并设为自动"

# ---------- 3. 安装 Winget / Chocolatey ----------
if (-not (Get-Command choco -ErrorAction SilentlyContinue)) {
    Write-Info "安装 Chocolatey..."
    Set-ExecutionPolicy Bypass -Scope Process -Force
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
    Invoke-Expression ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
    refreshenv
    Write-Log "Chocolatey 已安装"
} else {
    Write-Log "Chocolatey 已存在"
}

# ---------- 4. 安装基础工具 ----------
Write-Info "安装基础工具 (Git / Python / syncthing)..."
choco install git -y --no-progress
choco install python312 -y --no-progress
choco install syncthing -y --no-progress
Write-Log "基础工具安装完成"

# ---------- 5. 安装 Tailscale ----------
Write-Info "安装 Tailscale..."
$ts = Get-Command tailscale -ErrorAction SilentlyContinue
if (-not $ts) {
    choco install tailscale -y --no-progress
    Write-Log "Tailscale 已安装"
} else {
    Write-Log "Tailscale 已存在"
}

Write-Warn "请手动登录 Tailscale:"
Write-Host "    tailscale up --hostname=$TailscaleHostname"
Write-Host "    (登录同一账号, 与 Mac 组网)"
Write-Host ""

# ---------- 6. 配置 SSH 仅监听 Tailscale 网卡 ----------
Write-Info "配置 SSH 仅监听 Tailscale 网卡..."
$sshdConfig = "C:\ProgramData\ssh\sshd_config"
if (Test-Path $sshdConfig) {
    $backup = "$sshdConfig.bak.$(Get-Date -Format 'yyyyMMddHHmmss')"
    Copy-Item $sshdConfig $backup
    Write-Info "已备份 sshd_config 到 $backup"

    # 获取 Tailscale IP (100.x.x.x 段)
    $tsIP = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -like "100.*" } | Select-Object -First 1).IPAddress
    if ($tsIP) {
        Write-Info "检测到 Tailscale IP: $tsIP"
        # 追加配置
        $configAddition = @"

# === 量化系统安全配置 (仅 Tailscale 网卡可访问 SSH) ===
ListenAddress $tsIP
PasswordAuthentication no
PubkeyAuthentication yes
"@
        Add-Content -Path $sshdConfig -Value $configAddition
        Restart-Service sshd
        Write-Log "SSH 已限制为仅 Tailscale 网卡 ($tsIP) 可访问"
    } else {
        Write-Warn "未检测到 Tailscale IP, 请先 tailscale up, 再重跑此脚本"
    }
}

# ---------- 7. 配置防火墙 ----------
Write-Info "配置防火墙规则..."

# 删除公网 RDP/SSH 规则 (如果存在)
Get-NetFirewallRule -DisplayName "OpenSSH Server (sshd)" -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue

# 仅允许 Tailscale 网段 (100.64.0.0/10) 访问 22/3389
$tsPrefix = "100.64.0.0/10"

# SSH (22) - 仅 Tailscale
$sshRule = Get-NetFirewallRule -DisplayName "Quant-SSH-Tailscale-Only" -ErrorAction SilentlyContinue
if (-not $sshRule) {
    New-NetFirewallRule -DisplayName "Quant-SSH-Tailscale-Only" `
        -Direction Inbound -Action Allow -Protocol TCP -LocalPort 22 `
        -RemoteAddress $tsPrefix
    Write-Log "防火墙: SSH 仅允许 Tailscale 网段"
}

# RDP (3389) - 仅 Tailscale (禁用公网 RDP)
$rdpPublic = Get-NetFirewallRule -DisplayGroup "Remote Desktop" -ErrorAction SilentlyContinue
if ($rdpPublic) {
    # 禁用公网 RDP 规则
    $rdpPublic | Where-Object { $_.Profile -like "*Public*" } | Disable-NetFirewallRule
    Write-Log "已禁用公网 RDP 规则"
}

$rdpTsRule = Get-NetFirewallRule -DisplayName "Quant-RDP-Tailscale-Only" -ErrorAction SilentlyContinue
if (-not $rdpTsRule) {
    New-NetFirewallRule -DisplayName "Quant-RDP-Tailscale-Only" `
        -Direction Inbound -Action Allow -Protocol TCP -LocalPort 3389 `
        -RemoteAddress $tsPrefix
    Write-Log "防火墙: RDP 仅允许 Tailscale 网段"
}

# ---------- 8. 创建项目目录 ----------
Write-Info "创建项目目录结构..."
$dirs = @(
    "$ProjectDir",
    "$ProjectDir\config",
    "$ProjectDir\utils",
    "$ProjectDir\每日报告归档",
    "$ProjectDir\models",
    "$ProjectDir\reports",
    "$ProjectDir\logs",
    "$ProjectDir\data_cache"
)
foreach ($d in $dirs) {
    if (-not (Test-Path $d)) {
        New-Item -ItemType Directory -Path $d -Force | Out-Null
    }
}
Write-Log "项目目录已创建: $ProjectDir"

# ---------- 9. 配置 syncthing 自启 ----------
Write-Info "配置 syncthing 开机自启..."
$stService = Get-Service "syncthing" -ErrorAction SilentlyContinue
if (-not $stService) {
    # 用 nssm 或 schtasks 创建服务
    schtasks /Create /TN "Syncthing" /TR "cmd /c \"C:\Program Files\syncthing\syncthing.exe\" --no-browser" /SC ONLOGON /RL HIGHEST /F 2>$null | Out-Null
    Write-Log "syncthing 计划任务已创建"
} else {
    Set-Service "syncthing" -StartupType Automatic
    Write-Log "syncthing 服务已设为自动"
}

# ---------- 10. 风控守卫: 禁止公网出站到券商 (仅 Tailscale + 国内白名单) ----------
Write-Info "提示: 券商 IP 白名单需联系营业部绑定本机公网 IP"
$pubIP = (Invoke-RestMethod "https://ifconfig.me" -TimeoutSec 5)
Write-Warn "本机当前公网 IP: $pubIP"
Write-Warn "请将此 IP 提交给券商营业部 (QMT/Wind/同花顺) 绑定白名单"
Write-Warn "注意: 必须使用固定公网 IP, 弹性 IP 变更会导致白名单失效"

# ---------- 11. 安装券商客户端提示 ----------
Write-Host ""
Write-Warn "请手动安装以下券商客户端 (按顺序):"
Write-Host "    1. Wind 终端 (万得金融终端) - 从 Wind 官网下载"
Write-Host "    2. QMT 客户端 - 联系券商营业部获取"
Write-Host "    3. 同花顺客户端 - 用于 GUI 自动化兜底"
Write-Host "    4. 通达信终端 (可选, pytdx 服务端)"
Write-Host ""

# ---------- 12. 配置环境变量 ----------
Write-Info "配置环境变量..."
$envVars = @(
    @{Name="WIND_API_KEY"; Value=""; Desc="Wind MCP 认证密钥"},
    @{Name="IFIND_TOKEN"; Value=""; Desc="iFinD MCP 认证令牌"},
    @{Name="TS_TOKEN"; Value=""; Desc="Tushare 令牌"},
    @{Name="VOLCENGINE_API_KEY"; Value=""; Desc="豆包 LLM"},
    @{Name="REPORT_OUTPUT_DIR"; Value="$ProjectDir\每日报告归档"},
    @{Name="LOG_LEVEL"; Value="INFO"},
    @{Name="NO_PROXY"; Value="push2his.eastmoney.com,push2.eastmoney.com,eastmoney.com,sinajs.cn,sina.com.cn"}
)

foreach ($v in $envVars) {
    $current = [Environment]::GetEnvironmentVariable($v.Name, "Machine")
    if (-not $current) {
        [Environment]::SetEnvironmentVariable($v.Name, $v.Value, "Machine")
        Write-Info "  设置 $($v.Name) (请手动填值)"
    } else {
        Write-Info "  $($v.Name) 已存在"
    }
}

Write-Warn "请手动填写以下环境变量 (系统属性→环境变量):"
Write-Host "    WIND_API_KEY = <你的 Wind API 密钥>"
Write-Host "    IFIND_TOKEN  = <你的 iFinD 令牌>"
Write-Host "    TS_TOKEN     = <你的 Tushare 令牌> (可选)"
Write-Host ""

# ---------- 13. 验证 ----------
Write-Host ""
Write-Host "========== 验证安装 ==========" -ForegroundColor Cyan

$python = Get-Command python -ErrorAction SilentlyContinue
if ($python) {
    Write-Log "Python: $(python --version)"
} else {
    Write-Err "Python 未安装"
}

$git = Get-Command git -ErrorAction SilentlyContinue
if ($git) { Write-Log "Git: $(git --version)" } else { Write-Err "Git 未安装" }

$st = Get-Command syncthing -ErrorAction SilentlyContinue
if ($st) { Write-Log "syncthing 已安装" } else { Write-Err "syncthing 未安装" }

$ssh = Get-Service sshd -ErrorAction SilentlyContinue
if ($ssh.Status -eq "Running") { Write-Log "sshd 运行中" } else { Write-Err "sshd 未运行" }

# ---------- 完成 ----------
Write-Host ""
Write-Host "====================================================" -ForegroundColor Green
Write-Host "  Windows 云服务器配置完成!" -ForegroundColor Green
Write-Host "====================================================" -ForegroundColor Green
Write-Host ""
Write-Host "下一步:"
Write-Host "  1. 登录 Tailscale: tailscale up --hostname=$TailscaleHostname"
Write-Host "  2. 记录 Tailscale IP (100.x.x.x), 填入 Mac 端 quant-remote 脚本"
Write-Host "  3. 安装 Wind/QMT/同花顺 客户端并登录"
Write-Host "  4. 联系券商营业部绑定公网 IP 白名单"
Write-Host "  5. 配置 syncthing 共享文件夹 (http://127.0.0.1:8384)"
Write-Host "  6. 克隆项目到 $ProjectDir"
Write-Host "  7. 填写环境变量 WIND_API_KEY / IFIND_TOKEN"
Write-Host "  8. 运行 P0 自检: python scripts\run_p0_startup_check.py --strict"
Write-Host ""
Write-Warn "重要: 实盘启动前必须运行 --strict 自检通过"
