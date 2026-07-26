#!/usr/bin/env bash
# ============================================================
# ci_nightly.sh — 本地 nightly 等效入口 (完整回测, >30 分钟)
# ============================================================
# 模块整合 8.4 — T1.9-D
#
# 用途:
#   等效执行 GitHub Actions nightly job, 运行 V9 完整回测
#   (Layer 5) + 全量测试套件 (含 nightly marked 测试).
#
# 等效 CI job:
#   nightly-regression:
#     - V9 完整回测 (Layer 5, >30 分钟)
#     - 全量 pytest (含 nightly marked)
#
# 用法:
#   bash scripts/ci_nightly.sh                # 完整 nightly
#   bash scripts/ci_nightly.sh --skip-v9      # 跳过 V9 完整回测
#   bash scripts/ci_nightly.sh --skip-tests   # 跳过全量 pytest
#   bash scripts/ci_nightly.sh --help         # 显示帮助
#
# 退出码:
#   0: 全部通过
#   1: 至少一项失败
#   2: 环境错误
#
# 注意:
#   - 此脚本耗时 >30 分钟, 仅在 nightly 或手动触发时运行
#   - 建议在 git push 前使用 ci_run.sh (PR 模式) 而非此脚本
# ============================================================

set -uo pipefail

# ============================================================
# 颜色与日志
# ============================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; }

# ============================================================
# 项目根目录
# ============================================================
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"
cd "$PROJECT_ROOT"

# ============================================================
# 参数解析
# ============================================================
SKIP_V9=false
SKIP_TESTS=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-v9)
            SKIP_V9=true
            shift
            ;;
        --skip-tests)
            SKIP_TESTS=true
            shift
            ;;
        --help|-h)
            cat <<EOF
用法: bash scripts/ci_nightly.sh [选项]

选项:
  --skip-v9       跳过 V9 完整回测 (Layer 5)
  --skip-tests    跳过全量 pytest (含 nightly marked)
  --help, -h      显示此帮助

退出码:
  0: 全部通过
  1: 至少一项失败
  2: 环境错误

注意:
  - 此脚本耗时 >30 分钟
  - 推荐在 push 前使用 ci_run.sh (PR 模式)
EOF
            exit 0
            ;;
        *)
            warn "未知参数: $1 (忽略)"
            shift
            ;;
    esac
done

# ============================================================
# 环境检查
# ============================================================
info "============================================================"
info "终极量化交易系统 8.4 — 本地 nightly 等效运行 (完整回测)"
info "============================================================"
info "项目根目录: $PROJECT_ROOT"
info "运行时间: $(date '+%Y-%m-%d %H:%M:%S')"
info "============================================================"
echo ""

# Python 检查
if ! command -v python &> /dev/null; then
    fail "Python 未安装或不在 PATH"
    exit 2
fi

PYTHON_VERSION=$(python --version 2>&1)
info "Python: $PYTHON_VERSION"

# pytest 检查
if ! python -c "import pytest" &> /dev/null; then
    warn "pytest 未安装, 尝试安装..."
    python -m pip install pytest pytest-cov pytest-xdist pyyaml pandas numpy || {
        fail "pytest 安装失败"
        exit 2
    }
fi

# ============================================================
# 失败项收集
# ============================================================
FAILED_ITEMS=()
TOTAL_START=$(date +%s)

# ============================================================
# Step 1: V9 完整回测 (Layer 5, >30 分钟)
# ============================================================
if [ "$SKIP_V9" = "false" ]; then
    info ""
    info "[Step 1/2] V9 完整回测 (Layer 5, 含 nightly marked 测试, >30 分钟)"
    info "------------------------------------------------------------"
    if [ -f "scripts/_run_v9_regression.py" ]; then
        V9_START=$(date +%s)
        info "启动 V9 完整回测 (--full 标志, 含 Layer 5)..."
        info "预计耗时 >30 分钟, 请耐心等待"

        if python scripts/_run_v9_regression.py --full 2>&1 | tail -50; then
            V9_END=$(date +%s)
            V9_DURATION=$((V9_END-V9_START))
            ok "V9 完整回测通过 (耗时 ${V9_DURATION}s = $((V9_DURATION/60))m$((V9_DURATION%60))s)"
        else
            V9_END=$(date +%s)
            V9_DURATION=$((V9_END-V9_START))
            fail "V9 完整回测失败 (耗时 ${V9_DURATION}s = $((V9_DURATION/60))m$((V9_DURATION%60))s)"
            FAILED_ITEMS+=("v9-full-regression")
        fi
    else
        warn "V9 回归脚本不存在: scripts/_run_v9_regression.py"
        warn "跳过 V9 完整回测"
    fi
else
    info "[Step 1/2] V9 完整回测已跳过 (--skip-v9)"
fi

# ============================================================
# Step 2: 全量 pytest (含 nightly marked 测试)
# ============================================================
if [ "$SKIP_TESTS" = "false" ]; then
    info ""
    info "[Step 2/2] 全量 pytest (含 nightly marked 测试)"
    info "------------------------------------------------------------"
    TESTS_START=$(date +%s)
    info "运行全量测试 (tests/, 包含 unit + integration + e2e + regression)..."

    if python -m pytest tests -v --tb=short 2>&1 | tail -80; then
        TESTS_END=$(date +%s)
        TESTS_DURATION=$((TESTS_END-TESTS_START))
        ok "全量 pytest 通过 (耗时 ${TESTS_DURATION}s = $((TESTS_DURATION/60))m$((TESTS_DURATION%60))s)"
    else
        TESTS_END=$(date +%s)
        TESTS_DURATION=$((TESTS_END-TESTS_START))
        fail "全量 pytest 失败 (耗时 ${TESTS_DURATION}s = $((TESTS_DURATION/60))m$((TESTS_DURATION%60))s)"
        FAILED_ITEMS+=("full-pytest")
    fi
else
    info "[Step 2/2] 全量 pytest 已跳过 (--skip-tests)"
fi

# ============================================================
# 总结
# ============================================================
TOTAL_END=$(date +%s)
TOTAL_DURATION=$((TOTAL_END-TOTAL_START))
TOTAL_MIN=$((TOTAL_DURATION/60))
TOTAL_SEC=$((TOTAL_DURATION%60))

echo ""
info "============================================================"
info "Nightly Summary (本地等效)"
info "============================================================"

if [ ${#FAILED_ITEMS[@]} -eq 0 ]; then
    ok "全部通过 — nightly 回归成功"
    info "总耗时: ${TOTAL_DURATION}s = ${TOTAL_MIN}m${TOTAL_SEC}s"
    info "============================================================"
    exit 0
else
    fail "失败 — 以下项未通过:"
    for item in "${FAILED_ITEMS[@]}"; do
        fail "  - $item"
    done
    fail "总耗时: ${TOTAL_DURATION}s = ${TOTAL_MIN}m${TOTAL_SEC}s"
    info "============================================================"
    exit 1
fi
