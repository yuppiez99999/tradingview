#!/usr/bin/env bash
# ============================================================
# Mac 远程触发 + 状态查询脚本 — 终极量化交易系统 v8.4
# ============================================================
# 用途: 在 Mac 上通过 SSH 远程触发 Windows 云服务器的实盘任务,
#       并查询实盘状态 (通过 syncthing 同步过来的文件)
#
# 用法:
#   quant-remote <command>
#
# 命令:
#   premarket     触发盘前工作流 (run_daily_morning.py --phase all)
#   intraday      触发盘中决策 (run_intraday_decision)
#   eod           触发盘后工作流 (run_daily_eod_workflow.py)
#   hedge         触发对冲+再平衡 (tail_only 模式)
#   rebalance     触发再平衡执行
#   check         远程运行 P0 自检 (--strict)
#   status        查询实盘状态 (本地 + 远程)
#   logs [N]      查看最近 N 行日志 (默认 50)
#   rdp           打开 Microsoft Remote Desktop 连接
#   sync          手动触发 syncthing 同步
#   help          显示帮助
#
# 前置条件:
#   • Tailscale 已组网 (Mac + Windows 同账号)
#   • SSH 免密已配置 (ssh-copy-id)
#   • syncthing 已配置共享文件夹
# ============================================================
set -euo pipefail

# ---------- 配置 (按需修改) ----------
WIN_HOST="${QUANT_WIN_HOST:-quant-win}"       # Tailscale 主机名
WIN_USER="${QUANT_WIN_USER:-administrator}"
WIN_PROJECT="${QUANT_WIN_PROJECT:-C:\\QuantSys}"
LOCAL_PROJECT="${QUANT_LOCAL_PROJECT:-$HOME/Projects/28-终极量化交易系统8.4}"

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

# ---------- 帮助 ----------
show_help() {
    cat << 'EOF'
终极量化交易系统 v8.4 — Mac 远程触发脚本

用法: quant-remote <command>

命令:
  premarket     盘前工作流 (run_daily_morning.py --phase all)
  intraday      盘中决策 (run_intraday_decision)
  eod           盘后工作流 (run_daily_eod_workflow.py)
  hedge         对冲+再平衡 (tail_only 尾部保护)
  rebalance     再平衡执行
  check         远程 P0 自检 (--strict)
  status        查询实盘状态 (本地 syncthing + 远程 SSH)
  logs [N]      最近 N 行日志 (默认 50)
  rdp           打开 Microsoft Remote Desktop
  sync          手动触发 syncthing 同步
  help          显示此帮助

配置 (环境变量):
  QUANT_WIN_HOST        Windows Tailscale 主机名 (默认 quant-win)
  QUANT_WIN_USER        Windows SSH 用户 (默认 administrator)
  QUANT_WIN_PROJECT     Windows 项目路径 (默认 C:\QuantSys)
  QUANT_LOCAL_PROJECT   Mac 本地项目路径

示例:
  quant-remote premarket
  quant-remote status
  quant-remote logs 100
EOF
}

# ---------- 远程执行 ----------
remote_exec() {
    local cmd="$1"
    info "远程执行: $cmd"
    info "目标: $WIN_USER@$WIN_HOST"
    ssh -o ConnectTimeout=10 -o ServerAliveInterval=30 \
        "$WIN_USER@$WIN_HOST" \
        "cd $WIN_PROJECT && $cmd"
}

# ---------- 命令分发 ----------
case "${1:-help}" in

    premarket)
        log "触发盘前工作流..."
        remote_exec "python 15_每日工作流\\run_daily_morning.py --phase all"
        ;;

    intraday)
        log "触发盘中决策..."
        remote_exec "run_intraday_decision.bat"
        ;;

    eod)
        log "触发盘后工作流..."
        remote_exec "python 15_每日工作流\\run_daily_eod_workflow.py"
        ;;

    hedge)
        log "触发对冲+再平衡 (tail_only 尾部保护)..."
        warn "这是实盘操作, 需要 Windows 端 QMT 客户端已登录"
        read -p "确认执行? (y/N) " confirm
        if [[ "$confirm" =~ ^[Yy]$ ]]; then
            remote_exec "python run_daily_eod.py --hedge-rebalance --mode=tail_only"
        else
            warn "已取消"
            exit 1
        fi
        ;;

    rebalance)
        log "触发再平衡执行..."
        warn "这是实盘操作, 会修改持仓"
        read -p "确认执行? (y/N) " confirm
        if [[ "$confirm" =~ ^[Yy]$ ]]; then
            remote_exec "python rebalancing_execution_orders.py"
        else
            warn "已取消"
            exit 1
        fi
        ;;

    check)
        log "远程运行 P0 自检 (--strict)..."
        remote_exec "python scripts\\run_p0_startup_check.py --strict"
        ;;

    status)
        echo -e "${BLUE}========== 实盘状态总览 ==========${NC}"
        echo ""

        # 1. Windows 服务器状态
        info "1. Windows 云服务器状态 ($WIN_HOST)..."
        if ssh -o ConnectTimeout=5 "$WIN_USER@$WIN_HOST" "echo online" &>/dev/null; then
            log "服务器在线"
            echo ""
            info "Python 进程:"
            ssh "$WIN_USER@$WIN_HOST" "powershell -Command \"Get-Process python -ErrorAction SilentlyContinue | Select-Object Id,@{N='Started';E={\$_.StartTime.ToString('HH:mm:ss')}},@{N='CPU(s)';E={[math]::Round(\$_.CPU,1)}} | Format-Table -AutoSize\"" 2>/dev/null || warn "无 Python 进程运行"
        else
            err "服务器离线或 SSH 不可达"
        fi

        # 2. 本地持仓状态 (通过 syncthing 同步)
        echo ""
        info "2. 持仓状态 (本地 syncthing 同步)..."
        POS_FILE="$LOCAL_PROJECT/config/positions.json"
        if [ -f "$POS_FILE" ]; then
            if command -v jq &>/dev/null; then
                POS_COUNT=$(jq '.positions | length' "$POS_FILE" 2>/dev/null || echo "?")
                HEDGE_COUNT=$(jq '.hedge_positions | length' "$POS_FILE" 2>/dev/null || echo "0")
                LAST_META=$(jq -r '.meta.updated_at // .meta.last_update // "unknown"' "$POS_FILE" 2>/dev/null || echo "unknown")
                log "持仓: $POS_COUNT 只现货, $HEDGE_COUNT 个对冲头寸"
                info "最后更新: $LAST_META"
            else
                warn "jq 未安装, 无法解析持仓文件"
            fi
        else
            warn "持仓文件未同步: $POS_FILE"
        fi

        # 3. 今日报告
        echo ""
        info "3. 今日报告..."
        TODAY=$(date +%Y-%m-%d)
        REPORT_DIR="$LOCAL_PROJECT/每日报告归档/$TODAY"
        if [ -d "$REPORT_DIR" ]; then
            REPORT_COUNT=$(ls -1 "$REPORT_DIR" 2>/dev/null | wc -l | tr -d ' ')
            log "今日报告: $REPORT_COUNT 份 ($TODAY)"
            ls -lt "$REPORT_DIR" | head -5
        else
            warn "今日 ($TODAY) 暂无报告同步过来"
        fi

        # 4. P0 自检最新结果
        echo ""
        info "4. P0 自检最新结果..."
        CHECK_DIR="$LOCAL_PROJECT/reports/system_check"
        if [ -d "$CHECK_DIR" ]; then
            LATEST=$(ls -t "$CHECK_DIR"/*.json 2>/dev/null | head -1)
            if [ -n "$LATEST" ]; then
                if command -v jq &>/dev/null; then
                    EXIT_CODE=$(jq -r '.exit_code' "$LATEST" 2>/dev/null || echo "?")
                    PASSED=$(jq -r '.passed' "$LATEST" 2>/dev/null || echo "?")
                    FAILED=$(jq -r '.failed' "$LATEST" 2>/dev/null || echo "?")
                    if [ "$EXIT_CODE" = "0" ]; then
                        log "自检通过 (PASS:$PASSED FAIL:$FAILED)"
                    else
                        err "自检失败 (exit=$EXIT_CODE, PASS:$PASSED FAIL:$FAILED)"
                    fi
                    info "文件: $(basename "$LATEST")"
                fi
            else
                warn "无自检归档"
            fi
        else
            warn "自检归档目录不存在"
        fi

        echo ""
        log "状态查询完成"
        ;;

    logs)
        N="${2:-50}"
        log "查看最近 $N 行 Windows 端日志..."
        remote_exec "powershell -Command \"Get-Content 'logs\\quant.log' -Tail $N -ErrorAction SilentlyContinue; if (-not \$?) { Get-ChildItem logs -Filter '*.log' | Sort-Object LastWriteTime -Descending | Select-Object -First 1 | Get-Content -Tail $N }\""
        ;;

    rdp)
        log "打开 Microsoft Remote Desktop..."
        if [ -d "/Applications/Microsoft Remote Desktop.app" ]; then
            open -a "Microsoft Remote Desktop"
            info "在 RDP 应用中添加电脑, 地址填 Windows 的 Tailscale IP (100.x.x.x)"
        else
            warn "未安装 Microsoft Remote Desktop"
            info "从 App Store 安装: https://apps.apple.com/app/microsoft-remote-desktop/id1295203466"
        fi
        ;;

    sync)
        log "手动触发 syncthing 同步..."
        if command -v syncthing &>/dev/null; then
            # 通过 syncthing REST API 触发扫描
            curl -X POST "http://127.0.0.1:8384/rest/system/scan" \
                 -H "X-API-Key: $(grep -oP '(?<=<apikey>).*(?=</apikey>)' ~/Library/Application Support/Syncthing/config.xml 2>/dev/null)" \
                 2>/dev/null && log "已触发扫描" || warn "触发失败, 请手动打开 http://127.0.0.1:8384"
        else
            err "syncthing 未安装"
        fi
        ;;

    help|--help|-h)
        show_help
        ;;

    *)
        err "未知命令: $1"
        echo ""
        show_help
        exit 1
        ;;
esac
