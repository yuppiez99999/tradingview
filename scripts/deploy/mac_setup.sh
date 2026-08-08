#!/usr/bin/env bash
# ============================================================
# Mac 端一键配置脚本 — 终极量化交易系统 v8.4 研究环境
# ============================================================
# 用途: 在 MacBook M5 Max 上配置研究环境
#   • 安装 Homebrew / Python 3.12 / Tailscale / syncthing
#   • 克隆项目 + 创建虚拟环境
#   • 安装研究依赖 (跳过 Windows 专属包)
#   • 配置 SSH 免密登录 Windows 云服务器
#   • 部署远程触发脚本
#
# 用法: chmod +x mac_setup.sh && ./mac_setup.sh
# ============================================================
set -euo pipefail

# ---------- 颜色输出 ----------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; }
info() { echo -e "${BLUE}[i]${NC} $1"; }

# ---------- 配置变量 (按需修改) ----------
PROJECT_DIR="${PROJECT_DIR:-$HOME/Projects/28-终极量化交易系统8.4}"
WINDOWS_HOST="${WINDOWS_HOST:-quant-win}"        # Tailscale 主机名
WINDOWS_USER="${WINDOWS_USER:-administrator}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"

echo -e "${BLUE}====================================================${NC}"
echo -e "${BLUE}  Mac 研究环境一键配置 — 终极量化交易系统 v8.4${NC}"
echo -e "${BLUE}====================================================${NC}"
echo ""
info "项目目录: $PROJECT_DIR"
info "Windows 云服务器: $WINDOWS_USER@$WINDOWS_HOST"
info "Python 版本: $PYTHON_VERSION"
echo ""

# ---------- 1. 安装 Homebrew ----------
if ! command -v brew &> /dev/null; then
    log "安装 Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
    eval "$(/opt/homebrew/bin/brew shellenv)"
else
    log "Homebrew 已安装"
fi

# ---------- 2. 安装 Python 3.12 ----------
if ! command -v python3.12 &> /dev/null; then
    log "安装 Python $PYTHON_VERSION..."
    brew install python@$PYTHON_VERSION
else
    log "Python $PYTHON_VERSION 已安装"
fi

# ---------- 3. 安装 Tailscale (内网穿透, 避免公网暴露) ----------
if ! command -v tailscale &> /dev/null; then
    log "安装 Tailscale..."
    brew install --cask tailscale
    warn "请手动启动 Tailscale 应用并登录: open /Applications/Tailscale.app"
else
    log "Tailscale 已安装"
fi

# ---------- 4. 安装 syncthing (双向同步) ----------
if ! command -v syncthing &> /dev/null; then
    log "安装 syncthing..."
    brew install syncthing
else
    log "syncthing 已安装"
fi

# ---------- 5. 配置 syncthing 开机自启 ----------
log "配置 syncthing 开机自启..."
brew services start syncthing 2>/dev/null || true
sleep 2
info "syncthing Web UI: http://127.0.0.1:8384 (稍后在此添加共享文件夹)"

# ---------- 6. 克隆项目 ----------
if [ ! -d "$PROJECT_DIR" ]; then
    warn "项目目录不存在, 请手动克隆:"
    echo "    git clone <你的仓库> \"$PROJECT_DIR\""
    echo "    或从 Windows 服务器通过 syncthing 同步过来"
else
    log "项目目录已存在: $PROJECT_DIR"
fi

# ---------- 7. 创建 Python 虚拟环境 ----------
cd "$PROJECT_DIR" 2>/dev/null || { err "项目目录不存在, 跳过虚拟环境创建"; exit 1; }

if [ ! -d ".venv-mac" ]; then
    log "创建 Python 虚拟环境 (.venv-mac)..."
    python3.12 -m venv .venv-mac
fi

# 激活虚拟环境
source .venv-mac/bin/activate
log "已激活虚拟环境: $(which python)"

# ---------- 8. 升级 pip ----------
log "升级 pip..."
pip install --upgrade pip wheel setuptools -q

# ---------- 9. 安装研究依赖 (跳过 Windows 专属包) ----------
log "安装研究依赖 (跳过 pywinauto/pyautogui/windpy)..."
pip install --quiet \
    numpy pandas scipy \
    scikit-learn lightgbm xgboost \
    optuna joblib statsmodels \
    akshare yfinance \
    matplotlib seaborn plotly dash \
    PyYAML python-dotenv schedule ntplib \
    PyPortfolioOpt quantstats sqlalchemy \
    aiohttp cryptography loguru numba tqdm \
    click jupyterlab

warn "已跳过 Windows 专属依赖: pywinauto, pyautogui, windpy, pytdx (可选)"
info "如需 pytdx (通达信 TCP 直连): pip install pytdx2"

# ---------- 10. 设置 QUANT_RESEARCH_MODE 环境变量 ----------
log "配置 QUANT_RESEARCH_MODE 环境变量..."

SHELL_RC="$HOME/.zshrc"
[ -f "$HOME/.bashrc" ] && [ ! -f "$HOME/.zshrc" ] && SHELL_RC="$HOME/.bashrc"

if ! grep -q "QUANT_RESEARCH_MODE" "$SHELL_RC" 2>/dev/null; then
    cat >> "$SHELL_RC" << 'EOF'

# === 终极量化交易系统 v8.4 — Mac 研究模式 ===
export QUANT_RESEARCH_MODE=1
export NO_PROXY="push2his.eastmoney.com,push2.eastmoney.com,eastmoney.com,sinajs.cn,sina.com.cn"
EOF
    log "已写入 $SHELL_RC"
else
    log "QUANT_RESEARCH_MODE 已在 $SHELL_RC 中配置"
fi

export QUANT_RESEARCH_MODE=1

# ---------- 11. 配置 SSH 免密登录 Windows ----------
info "配置 SSH 免密登录 Windows 云服务器..."

if [ ! -f "$HOME/.ssh/id_ed25519" ]; then
    log "生成 ed25519 SSH 密钥..."
    ssh-keygen -t ed25519 -C "mac-quant-research" -f "$HOME/.ssh/id_ed25519" -N ""
fi

echo ""
warn "请手动执行以下步骤完成 SSH 免密配置:"
echo "    ssh-copy-id -i ~/.ssh/id_ed25519.pub $WINDOWS_USER@$WINDOWS_HOST"
echo ""
warn "测试连接 (需先在 Windows 上开启 OpenSSH Server, 见 windows_setup.ps1):"
echo "    ssh $WINDOWS_USER@$WINDOWS_HOST 'echo connected'"
echo ""

# ---------- 12. 部署远程触发脚本 ----------
log "部署远程触发脚本到 ~/bin/..."
mkdir -p "$HOME/bin"

# 复制 quant-remote.sh 到 ~/bin/
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/quant-remote.sh" ]; then
    cp "$SCRIPT_DIR/quant-remote.sh" "$HOME/bin/quant-remote"
    chmod +x "$HOME/bin/quant-remote"
    log "已部署 ~/bin/quant-remote"
else
    warn "quant-remote.sh 不在 $SCRIPT_DIR, 请手动复制"
fi

# 添加 ~/bin 到 PATH
if ! grep -q 'export PATH="$HOME/bin:$PATH"' "$SHELL_RC" 2>/dev/null; then
    echo 'export PATH="$HOME/bin:$PATH"' >> "$SHELL_RC"
    log "已将 ~/bin 加入 PATH"
fi

# ---------- 13. 验证安装 ----------
echo ""
echo -e "${BLUE}========== 验证安装 ==========${NC}"

python -c "import numpy, pandas, sklearn, lightgbm; print(f'numpy={numpy.__version__}, pandas={pandas.__version__}, sklearn={sklearn.__version__}, lightgbm={lightgbm.__version__}')" && log "核心依赖 OK" || err "核心依赖缺失"

python -c "import akshare; print(f'akshare={akshare.__version__}')" && log "AKShare OK" || err "AKShare 缺失"

[ -f "$PROJECT_DIR/config/settings_mac.yaml" ] && log "settings_mac.yaml OK" || warn "settings_mac.yaml 不存在"

# 运行 P0 自检 (研究模式)
echo ""
info "运行 P0 自检 (研究模式)..."
cd "$PROJECT_DIR"
python -c "from utils.system_check import run_system_check; r = run_system_check(skip_datasource=True); print(f'退出码: {r.exit_code}')" || warn "自检有警告 (Mac 研究模式下 Wind/QMT 跳过属正常)"

# ---------- 完成 ----------
echo ""
echo -e "${GREEN}====================================================${NC}"
echo -e "${GREEN}  Mac 研究环境配置完成!${NC}"
echo -e "${GREEN}====================================================${NC}"
echo ""
echo "下一步:"
echo "  1. 启动 Tailscale 并登录同一账号: open /Applications/Tailscale.app"
echo "  2. 配置 syncthing 共享文件夹: http://127.0.0.1:8384"
echo "  3. 完成 SSH 免密: ssh-copy-id $WINDOWS_USER@$WINDOWS_HOST"
echo "  4. 触发 Windows 实盘: quant-remote premarket"
echo "  5. 查询实盘状态: quant-remote status"
echo ""
warn "重要: Mac 上禁止运行 --live / --hedge-rebalance / --ai-decision 等实盘模式"
