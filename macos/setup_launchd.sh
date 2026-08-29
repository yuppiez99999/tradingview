#!/bin/bash
# ===========================================================================
# 安装量化系统 macOS 定时任务 (替代 Windows 任务计划程序 .ps1 / .bat)
#
# 用法:
#   bash macos/setup_launchd.sh                 # 自动推断项目根
#   bash macos/setup_launchd.sh /path/to/28-终极量化交易系统8.4
#
# 行为:
#   1. 将 macos/launchd/*.plist 中的 {{PROJECT_DIR}} 替换为真实绝对路径
#   2. 复制到 ~/Library/LaunchAgents/
#   3. launchctl load (Ventura 及以下) 或 bootstrap (Sonoma 及以上)
#
# 卸载:
#   launchctl unload ~/Library/LaunchAgents/com.yuppie.quant.*.plist
#   rm ~/Library/LaunchAgents/com.yuppie.quant.*.plist
# ===========================================================================
set -euo pipefail

# 推断项目根: 默认 = 本脚本上级目录
PROJ="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LAUNCHD_DIR="$PROJ/macos/launchd"
AGENT_DIR="$HOME/Library/LaunchAgents"

if [ ! -d "$LAUNCHD_DIR" ]; then
    echo "错误: 找不到 $LAUNCHD_DIR" >&2
    exit 1
fi

mkdir -p "$AGENT_DIR"

for plist in "$LAUNCHD_DIR"/*.plist; do
    [ -e "$plist" ] || continue
    name="$(basename "$plist")"
    # 替换占位符并写出到 LaunchAgents
    sed "s|{{PROJECT_DIR}}|$PROJ|g" "$plist" > "$AGENT_DIR/$name"
    echo "已写入: $AGENT_DIR/$name"

    # 先尝试卸载旧实例 (幂等)
    launchctl unload "$AGENT_DIR/$name" 2>/dev/null || true
    # 注册: 旧式 load 或 新式 bootstrap
    if ! launchctl load "$AGENT_DIR/$name" 2>/dev/null; then
        launchctl bootstrap "gui/$(id -u)" "$AGENT_DIR/$name" 2>/dev/null || true
    fi
done

echo
echo "安装完成。查看状态:"
echo "  launchctl list | grep yuppie"
echo
echo "日志在: $PROJ/logs/launchd.*.log"
echo "敏感环境变量 (WIND_API_KEY 等) 请写入 ~/.zshrc 或 plist 的 EnvironmentVariables。"
