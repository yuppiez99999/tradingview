#!/usr/bin/env bash
# ============================================================
# ci_run.sh — 本地 CI 等效入口 (PR 触发作业本地版)
# ============================================================
# 模块整合 8.4 — T1.9-D
#
# 用途:
#   开发者在 push PR 前本地运行此脚本, 等效执行 GitHub Actions
#   上的 5 个 PR job, 避免占用 CI 资源.
#
# 等效 CI job:
#   1. lint-typecheck        (mypy + pylint, Phase 3-B 非阻断)
#   2. unit-tests            (tests/unit)
#   3. integration-tests     (tests/integration)
#   4. reexport-compat       (re-export 兼容性)
#   5. v9-quick-regression   (V9 Layer 1-4, <30s)
#
# 用法:
#   bash scripts/ci_run.sh                # 全量 (5 个 job)
#   bash scripts/ci_run.sh --quick        # 跳过 integration (开发者快速反馈)
#   bash scripts/ci_run.sh --skip-lint    # 跳过 mypy + pylint
#   bash scripts/ci_run.sh --skip-v9      # 跳过 V9 回归 (无基线文件时使用)
#   bash scripts/ci_run.sh --help         # 显示帮助
#
# 退出码:
#   0: 全部通过
#   1: 至少一个阻断 job 失败
#   2: 环境错误 (Python 缺失/依赖未安装)
# ============================================================

set -uo pipefail

# ============================================================
# 颜色与日志
# ============================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

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
QUICK_MODE=false
SKIP_LINT=false
SKIP_V9=false
SKIP_INTEGRATION=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --quick)
            QUICK_MODE=true
            SKIP_INTEGRATION=true
            shift
            ;;
        --skip-lint)
            SKIP_LINT=true
            shift
            ;;
        --skip-v9)
            SKIP_V9=true
            shift
            ;;
        --skip-integration)
            SKIP_INTEGRATION=true
            shift
            ;;
        --help|-h)
            cat <<EOF
用法: bash scripts/ci_run.sh [选项]

选项:
  --quick              快速模式 (跳过 integration tests)
  --skip-lint          跳过 mypy + pylint
  --skip-v9            跳过 V9 基线回归 (无基线文件时使用)
  --skip-integration   仅跳过 integration tests
  --help, -h           显示此帮助

退出码:
  0: 全部通过
  1: 至少一个阻断 job 失败
  2: 环境错误
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
info "终极量化交易系统 8.4 — 本地 CI 等效运行 (PR 模式)"
info "============================================================"
info "项目根目录: $PROJECT_ROOT"
info "运行模式: $([ "$QUICK_MODE" = "true" ] && echo "快速 (跳过 integration)" || echo "全量")"
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
# 失败 job 收集
# ============================================================
FAILED_JOBS=()
TOTAL_START=$(date +%s)

# ============================================================
# Job 1: Lint + Typecheck (Phase 3-B, 非阻断)
# ============================================================
if [ "$SKIP_LINT" = "false" ]; then
    info ""
    info "[Job 1/5] Lint + Typecheck (Phase 3-B, 非阻断)"
    info "------------------------------------------------------------"

    # MyPy
    if command -v mypy &> /dev/null || python -c "import mypy" &> /dev/null; then
        info "运行 mypy (utils/)..."
        if python -m mypy --config-file mypy.ini utils/ 2>&1 | tail -20; then
            ok "mypy 完成 (非阻断, Phase 3-B 渐进式严格)"
        else
            warn "mypy 报告错误 (非阻断, Phase 3-B 模式)"
        fi
    else
        warn "mypy 未安装, 跳过"
    fi

    # Pylint
    if command -v pylint &> /dev/null || python -c "import pylint" &> /dev/null; then
        info "运行 pylint (utils/infra, utils/risk, utils/execution, utils/data)..."
        if python -m pylint --rcfile=.pylintrc utils/infra/ utils/risk/ utils/execution/ utils/data/ 2>&1 | tail -20; then
            ok "pylint 完成 (非阻断, Phase 3-B 渐进式严格)"
        else
            warn "pylint 报告错误 (非阻断, Phase 3-B 模式)"
        fi
    else
        warn "pylint 未安装, 跳过"
    fi

    # Phase 3-B 验证脚本 (阻断)
    if [ -f "scripts/_verify_phase3b_static_analysis.py" ]; then
        info "运行 Phase 3-B 静态分析验证 (阻断)..."
        if python scripts/_verify_phase3b_static_analysis.py; then
            ok "Phase 3-B 静态分析验证通过"
        else
            fail "Phase 3-B 静态分析验证失败"
            FAILED_JOBS+=("lint-typecheck(phase3b)")
        fi
    else
        warn "Phase 3-B 验证脚本不存在, 跳过"
    fi
else
    info "[Job 1/5] Lint + Typecheck 已跳过 (--skip-lint)"
fi

# ============================================================
# Job 2: Unit Tests (阻断)
# ============================================================
info ""
info "[Job 2/5] Unit Tests (阻断)"
info "------------------------------------------------------------"
UNIT_START=$(date +%s)
if python -m pytest tests/unit -v --tb=short -m "not slow" 2>&1 | tail -40; then
    UNIT_END=$(date +%s)
    ok "Unit tests 通过 (${UNIT_END}-${UNIT_START}=$((UNIT_END-UNIT_START))s)"
else
    UNIT_END=$(date +%s)
    fail "Unit tests 失败 (${UNIT_END}-${UNIT_START}=$((UNIT_END-UNIT_START))s)"
    FAILED_JOBS+=("unit-tests")
fi

# ============================================================
# Job 3: Integration Tests (阻断)
# ============================================================
if [ "$SKIP_INTEGRATION" = "false" ]; then
    info ""
    info "[Job 3/5] Integration Tests (含 Chaos, 阻断)"
    info "------------------------------------------------------------"
    INT_START=$(date +%s)
    if python -m pytest tests/integration -v --tb=short -m "not slow" 2>&1 | tail -40; then
        INT_END=$(date +%s)
        ok "Integration tests 通过 (${INT_END}-${INT_START}=$((INT_END-INT_START))s)"
    else
        INT_END=$(date +%s)
        fail "Integration tests 失败 (${INT_END}-${INT_START}=$((INT_END-INT_START))s)"
        FAILED_JOBS+=("integration-tests")
    fi
else
    info "[Job 3/5] Integration Tests 已跳过 (--quick / --skip-integration)"
fi

# ============================================================
# Job 4: Re-export Compatibility (HC-1, 阻断)
# ============================================================
info ""
info "[Job 4/5] Re-export Compatibility (HC-1, 阻断)"
info "------------------------------------------------------------"
if [ -f "scripts/_verify_reexport_compat.py" ]; then
    if python scripts/_verify_reexport_compat.py 2>&1 | tail -20; then
        ok "Re-export 兼容性验证通过 (HC-1)"
    else
        fail "Re-export 兼容性验证失败 (HC-1 违反)"
        FAILED_JOBS+=("reexport-compat")
    fi
else
    warn "Re-export 验证脚本不存在, 跳过"
fi

# ============================================================
# Job 5: V9 Quick Regression (HC-1, 阻断)
# ============================================================
if [ "$SKIP_V9" = "false" ]; then
    info ""
    info "[Job 5/5] V9 Quick Regression (HC-1, Layer 1-4, 阻断)"
    info "------------------------------------------------------------"
    if [ -f "scripts/_run_v9_regression.py" ]; then
        V9_START=$(date +%s)
        if python scripts/_run_v9_regression.py 2>&1 | tail -30; then
            V9_END=$(date +%s)
            ok "V9 Quick Regression 通过 (${V9_END}-${V9_START}=$((V9_END-V9_START))s)"
        else
            V9_END=$(date +%s)
            fail "V9 Quick Regression 失败 (HC-1 违反, ${V9_END}-${V9_START}=$((V9_END-V9_START))s)"
            FAILED_JOBS+=("v9-quick-regression")
        fi
    else
        warn "V9 回归脚本不存在, 跳过"
    fi
else
    info "[Job 5/5] V9 Quick Regression 已跳过 (--skip-v9)"
fi

# ============================================================
# 总结
# ============================================================
TOTAL_END=$(date +%s)
TOTAL_DURATION=$((TOTAL_END-TOTAL_START))

echo ""
info "============================================================"
info "CI Summary (本地等效)"
info "============================================================"

if [ ${#FAILED_JOBS[@]} -eq 0 ]; then
    ok "全部通过 — 可以 push PR (总耗时 ${TOTAL_DURATION}s)"
    info "============================================================"
    exit 0
else
    fail "失败 — 以下 job 未通过:"
    for job in "${FAILED_JOBS[@]}"; do
        fail "  - $job"
    done
    fail "总耗时 ${TOTAL_DURATION}s"
    info "============================================================"
    exit 1
fi
