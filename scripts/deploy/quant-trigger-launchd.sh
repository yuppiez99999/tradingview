#!/usr/bin/env bash
# ============================================================
# quant-trigger-launchd.sh — launchd 定时任务 wrapper
# ============================================================
# 用途: 被 macOS launchd 调用, 执行远程实盘触发任务
#       • 设置 PATH/环境变量
#       • 调用 quant-remote <task>
#       • 记录结构化日志
#       • 失败时发 macOS 通知
#
# 用法 (由 launchd 自动调用, 也可手动测试):
#   ./quant-trigger-launchd.sh <task>
#     task = premarket | intraday | eod | hedge | check
#
# 配套: launchd_install.sh 安装 plist 到 ~/Library/LaunchAgents
# ============================================================
set -uo pipefail

# ---------- 配置 ----------
TASK="${1:-unknown}"
QUANT_REMOTE="${QUANT_REMOTE:-$HOME/bin/quant-remote}"
LOG_DIR="${QUANT_LOG_DIR:-$HOME/Projects/28-终极量化交易系统8.4/logs}"
LOG_FILE="$LOG_DIR/launchd_${TASK}.log"
ERR_FILE="$LOG_DIR/launchd_${TASK}.err"

# 确保 PATH (launchd 环境最小, 需手动指定)
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export QUANT_RESEARCH_MODE="${QUANT_RESEARCH_MODE:-1}"
export NO_PROXY="${NO_PROXY:-push2his.eastmoney.com,push2.eastmoney.com,eastmoney.com,sinajs.cn,sina.com.cn}"

# ---------- 工具函数 ----------
timestamp() { date "+%Y-%m-%d %H:%M:%S"; }

log() {
    local msg="[$(timestamp)] [INFO] $1"
    echo "$msg" | tee -a "$LOG_FILE"
}

err() {
    local msg="[$(timestamp)] [ERROR] $1"
    echo "$msg" | tee -a "$ERR_FILE" >&2
    echo "$msg" >> "$LOG_FILE"
}

# macOS 通知 (失败时弹窗提醒)
notify() {
    local title="$1"
    local body="$2"
    if command -v osascript &>/dev/null; then
        osascript -e "display notification \"$body\" with title \"$title\" sound name \"Basso\"" 2>/dev/null || true
    fi
}

# 交易日判断 (跳过周末; 节假日需另配 config/trade_calendar_cache)
is_trading_day() {
    local dow
    dow=$(date "+%u")  # 1=周一 ... 7=周日
    if [ "$dow" -ge 6 ]; then
        return 1  # 周末
    fi
    # TODO: 节假日检查 (读取 config/trade_calendar_cache/)
    return 0
}

# ---------- 主流程 ----------
mkdir -p "$LOG_DIR"

log "========== launchd 触发开始 =========="
log "任务: $TASK"
log "QUANT_REMOTE: $QUANT_REMOTE"
log "日志: $LOG_FILE"

# 1. 检查 quant-remote 是否存在
if [ ! -x "$QUANT_REMOTE" ]; then
    err "quant-remote 不存在或不可执行: $QUANT_REMOTE"
    notify "量化触发失败" "quant-remote 未安装: $TASK"
    exit 2
fi

# 2. 交易日判断 (周末不触发, 避免无谓 SSH)
if ! is_trading_day; then
    log "今日非交易日 (周末), 跳过任务: $TASK"
    exit 0
fi

# 3. 执行任务
log "执行: $QUANT_REMOTE $TASK"
START_TIME=$(date +%s)

# 根据任务类型决定是否需要确认 (hedge/rebalance 是实盘操作, launchd 自动执行不确认)
case "$TASK" in
    premarket|intraday|eod|check)
        # 自动执行, 无需确认
        "$QUANT_REMOTE" "$TASK" >> "$LOG_FILE" 2>&1
        EXIT_CODE=$?
        ;;
    hedge)
        # 对冲操作: launchd 自动执行 (假设 Windows 端 QMT 已登录)
        # 通过环境变量 QUANT_AUTO_CONFIRM=1 跳过 quant-remote 的确认提示
        export QUANT_AUTO_CONFIRM=1
        echo "y" | "$QUANT_REMOTE" "$TASK" >> "$LOG_FILE" 2>&1
        EXIT_CODE=$?
        ;;
    *)
        err "未知任务: $TASK"
        notify "量化触发失败" "未知任务: $TASK"
        exit 3
        ;;
esac

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

# 4. 结果处理
if [ $EXIT_CODE -eq 0 ]; then
    log "任务成功: $TASK (耗时 ${ELAPSED}s)"
    # 成功通知 (可选, 避免打扰)
    if [ "${QUANT_NOTIFY_SUCCESS:-0}" = "1" ]; then
        notify "量化触发成功" "$TASK 完成 (${ELAPSED}s)"
    fi
else
    err "任务失败: $TASK (exit=$EXIT_CODE, 耗时 ${ELAPSED}s)"
    notify "量化触发失败" "$TASK 失败 (exit=$EXIT_CODE)"
    # 读取最后 5 行错误日志作为通知补充
    if [ -f "$ERR_FILE" ]; then
        tail -5 "$ERR_FILE" >> "$LOG_FILE"
    fi
fi

log "========== launchd 触发结束 =========="
exit $EXIT_CODE
