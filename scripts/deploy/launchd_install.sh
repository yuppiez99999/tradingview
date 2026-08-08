#!/usr/bin/env bash
# ============================================================
# launchd_install.sh — macOS launchd 定时任务一键安装/管理
# ============================================================
# 用途: 在 Mac 上安装 quant-remote 的 launchd 定时任务,
#       按交易时段自动触发 Windows 云服务器实盘流程
#
# 定时任务清单 (A股交易时段, 已排除周末):
#   • premarket  — 07:05  盘前工作流
#   • intraday   — 09:35 / 10:30 / 11:20 / 13:05 / 14:00  盘中触发
#   • hedge      — 14:50  尾盘对冲+再平衡
#   • eod        — 15:35  盘后工作流
#   • check      — 08:50  盘前 P0 严格自检
#
# 用法:
#   ./launchd_install.sh install   [task]  安装 (默认全部, 可指定单个)
#   ./launchd_install.sh uninstall [task]  卸载
#   ./launchd_install.sh enable    task    启用某任务
#   ./launchd_install.sh disable   task    禁用某任务
#   ./launchd_install.sh list              列出已安装任务
#   ./launchd_install.sh status            查看运行状态
#   ./launchd_install.sh test      task    手动触发测试
#   ./launchd_install.sh help              帮助
#
# 前置条件:
#   • 已运行 mac_setup.sh (quant-remote 已部署到 ~/bin/)
#   • Tailscale 已组网, SSH 免密已配置
# ============================================================
set -euo pipefail

# ---------- 颜色 ----------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'
log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; }
info() { echo -e "${BLUE}[i]${NC} $1"; }

# ---------- 配置 ----------
LABEL_PREFIX="com.quantum"
LAUNCH_DIR="$HOME/Library/LaunchAgents"
QUANT_REMOTE="${QUANT_REMOTE:-$HOME/bin/quant-remote}"
WRAPPER="${WRAPPER:-$HOME/Projects/28-终极量化交易系统8.4/scripts/deploy/quant-trigger-launchd.sh}"
LOG_DIR="${QUANT_LOG_DIR:-$HOME/Projects/28-终极量化交易系统8.4/logs}"

mkdir -p "$LAUNCH_DIR" "$LOG_DIR"

# ---------- 任务定义 (名称 | 触发时间 JSON 数组) ----------
# 每个任务对应一个 plist, StartCalendarInterval 支持多个时间点
declare -A TASK_SCHEDULES
TASK_SCHEDULES["check"]='[{"hour":8,"minute":50}]'
TASK_SCHEDULES["premarket"]='[{"hour":7,"minute":5}]'
TASK_SCHEDULES["intraday"]='[{"hour":9,"minute":35},{"hour":10,"minute":30},{"hour":11,"minute":20},{"hour":13,"minute":5},{"hour":14,"minute":0}]'
TASK_SCHEDULES["hedge"]='[{"hour":14,"minute":50}]'
TASK_SCHEDULES["eod"]='[{"hour":15,"minute":35}]'

# ---------- 生成 plist ----------
generate_plist() {
    local task="$1"
    local schedule_json="${TASK_SCHEDULES[$task]}"
    local label="${LABEL_PREFIX}.${task}"
    local plist_path="$LAUNCH_DIR/${label}.plist"

    # 解析 JSON 数组生成 StartCalendarInterval XML
    local calendar_xml=""
    local n_items
    n_items=$(echo "$schedule_json" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo 1)

    if [ "$n_items" -gt 1 ]; then
        # 多时间点: 用 <array> 包裹
        calendar_xml="    <key>StartCalendarInterval</key>\n    <array>\n"
        for i in $(seq 0 $((n_items - 1))); do
            local h m
            h=$(echo "$schedule_json" | python3 -c "import json,sys; print(json.load(sys.stdin)[$i]['hour'])")
            m=$(echo "$schedule_json" | python3 -c "import json,sys; print(json.load(sys.stdin)[$i]['minute'])")
            calendar_xml+="        <dict>\n            <key>Hour</key><integer>$h</integer>\n            <key>Minute</key><integer>$m</integer>\n        </dict>\n"
        done
        calendar_xml+="    </array>"
    else
        # 单时间点
        local h m
        h=$(echo "$schedule_json" | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['hour'])")
        m=$(echo "$schedule_json" | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['minute'])")
        calendar_xml="    <key>StartCalendarInterval</key>\n    <dict>\n        <key>Hour</key><integer>$h</integer>\n        <key>Minute</key><integer>$m</integer>\n    </dict>"
    fi

    # 生成 plist 内容
    cat > "$plist_path" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${label}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${WRAPPER}</string>
        <string>${task}</string>
    </array>

$(echo -e "$calendar_xml")

    <key>StandardOutPath</key>
    <string>${LOG_DIR}/launchd_${task}.out</string>

    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/launchd_${task}.err</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>QUANT_RESEARCH_MODE</key>
        <string>1</string>
        <key>HOME</key>
        <string>${HOME}</string>
    </dict>

    <key>RunAtLoad</key>
    <false/>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key>
        <integer>1</integer>
    </dict>

    <key>KeepAlive</key>
    <false/>

    <key>ExitTimeOut</key>
    <integer>600</integer>
</dict>
</plist>
EOF

    echo "$plist_path"
}

# ---------- 命令实现 ----------

do_install() {
    local task="${1:-all}"
    info "安装 launchd 定时任务..."

    # 检查 wrapper
    if [ ! -f "$WRAPPER" ]; then
        err "wrapper 脚本不存在: $WRAPPER"
        err "请先运行 mac_setup.sh, 或手动复制 quant-trigger-launchd.sh 到该路径"
        exit 1
    fi
    chmod +x "$WRAPPER"

    # 检查 quant-remote
    if [ ! -x "$QUANT_REMOTE" ]; then
        warn "quant-remote 不存在: $QUANT_REMOTE"
        warn "launchd 任务会安装, 但触发时会失败, 请先部署 quant-remote"
    fi

    if [ "$task" = "all" ]; then
        for t in "${!TASK_SCHEDULES[@]}"; do
            install_single "$t"
        done
    else
        if [[ -z "${TASK_SCHEDULES[$task]:-}" ]]; then
            err "未知任务: $task (可选: ${!TASK_SCHEDULES[*]})"
            exit 1
        fi
        install_single "$task"
    fi

    log "安装完成!"
    echo ""
    info "已安装的定时任务:"
    do_list
    echo ""
    info "手动测试: $0 test premarket"
    info "查看状态: $0 status"
}

install_single() {
    local task="$1"
    local plist_path
    plist_path=$(generate_plist "$task")
    log "  • $task → $(basename "$plist_path")"

    # 卸载旧版本 (如果存在)
    launchctl unload "$plist_path" 2>/dev/null || true
    # 加载新版本
    launchctl load "$plist_path" 2>/dev/null || true
}

do_uninstall() {
    local task="${1:-all}"
    info "卸载 launchd 定时任务..."

    if [ "$task" = "all" ]; then
        for t in "${!TASK_SCHEDULES[@]}"; do
            uninstall_single "$t"
        done
    else
        uninstall_single "$task"
    fi
    log "卸载完成!"
}

uninstall_single() {
    local task="$1"
    local label="${LABEL_PREFIX}.${task}"
    local plist_path="$LAUNCH_DIR/${label}.plist"

    if [ -f "$plist_path" ]; then
        launchctl unload "$plist_path" 2>/dev/null || true
        rm -f "$plist_path"
        log "  • 已卸载 $task"
    else
        warn "  • $task 未安装"
    fi
}

do_enable() {
    local task="$1"
    local label="${LABEL_PREFIX}.${task}"
    local plist_path="$LAUNCH_DIR/${label}.plist"

    if [ -f "$plist_path" ]; then
        launchctl load "$plist_path" 2>/dev/null || true
        log "已启用: $task"
    else
        err "任务未安装: $task (先运行: $0 install $task)"
        exit 1
    fi
}

do_disable() {
    local task="$1"
    local label="${LABEL_PREFIX}.${task}"
    local plist_path="$LAUNCH_DIR/${label}.plist"

    if [ -f "$plist_path" ]; then
        launchctl unload "$plist_path" 2>/dev/null || true
        log "已禁用: $task"
    else
        warn "任务未安装: $task"
    fi
}

do_list() {
    info "已安装的 quant launchd 任务:"
    echo ""
    printf "  %-12s %-10s %-30s %s\n" "任务" "状态" "Label" "下次触发"
    echo "  ────────────────────────────────────────────────────────────────────"

    for task in "${!TASK_SCHEDULES[@]}"; do
        local label="${LABEL_PREFIX}.${task}"
        local plist_path="$LAUNCH_DIR/${label}.plist"
        local status="未安装"
        local next_run="-"

        if [ -f "$plist_path" ]; then
            # 检查是否已加载
            if launchctl list "$label" &>/dev/null; then
                status="✅ 已加载"
            else
                status="⚠️  已安装未加载"
            fi
            # 简易下次触发时间显示
            next_run="${TASK_SCHEDULES[$task]}"
        fi

        printf "  %-12s %-10s %-30s %s\n" "$task" "$status" "$label" "$next_run"
    done
    echo ""
}

do_status() {
    info "launchd 运行状态:"
    echo ""
    launchctl list | grep "com.quantum" || warn "无 com.quantum 任务运行"
    echo ""
    info "最近日志:"
    for task in "${!TASK_SCHEDULES[@]}"; do
        local log_file="$LOG_DIR/launchd_${task}.log"
        if [ -f "$log_file" ]; then
            echo ""
            echo "  [$task] 最后 3 行:"
            tail -3 "$log_file" | sed 's/^/    /'
        fi
    done
}

do_test() {
    local task="${1:-premarket}"
    info "手动测试任务: $task"
    warn "这将实际触发远程 Windows 服务器!"

    if [ ! -x "$WRAPPER" ]; then
        err "wrapper 不可执行: $WRAPPER"
        exit 1
    fi

    read -p "确认测试 $task? (y/N) " confirm
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        "$WRAPPER" "$task"
    else
        warn "已取消"
    fi
}

show_help() {
    cat << 'EOF'
quant-remote launchd 定时任务管理

用法: launchd_install.sh <command> [task]

命令:
  install   [task]  安装定时任务 (默认全部, task 可选: check/premarket/intraday/hedge/eod)
  uninstall [task]  卸载定时任务
  enable    task    启用已安装的任务
  disable   task    禁用任务 (保留 plist, 停止触发)
  list              列出所有任务及状态
  status            查看运行状态 + 最近日志
  test      task    手动触发测试 (会实际调用 Windows)
  help              显示此帮助

定时任务清单 (A股交易时段):
  check     08:50              盘前 P0 严格自检
  premarket 07:05              盘前工作流
  intraday  09:35/10:30/11:20/13:05/14:00  盘中触发
  hedge     14:50              尾盘对冲+再平衡
  eod       15:35              盘后工作流

文件位置:
  plist:    ~/Library/LaunchAgents/com.quantum.<task>.plist
  wrapper:  scripts/deploy/quant-trigger-launchd.sh
  日志:     logs/launchd_<task>.{log,err,out}

示例:
  ./launchd_install.sh install              # 安装全部 5 个任务
  ./launchd_install.sh install premarket    # 仅安装盘前任务
  ./launchd_install.sh list                 # 查看已安装任务
  ./launchd_install.sh disable hedge        # 临时禁用对冲任务
  ./launchd_install.sh test premarket       # 手动测试盘前触发
EOF
}

# ---------- 命令分发 ----------
case "${1:-help}" in
    install)   shift; do_install "${1:-all}" ;;
    uninstall) shift; do_uninstall "${1:-all}" ;;
    enable)    shift; do_enable "${1:?缺少 task 参数}" ;;
    disable)   shift; do_disable "${1:?缺少 task 参数}" ;;
    list)      do_list ;;
    status)    do_status ;;
    test)      shift; do_test "${1:-premarket}" ;;
    help|--help|-h) show_help ;;
    *) err "未知命令: $1"; echo ""; show_help; exit 1 ;;
esac
