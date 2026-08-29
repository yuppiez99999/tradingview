#!/bin/bash
# ===========================================================================
# 量化系统通用定时任务包装器 (macOS launchd 调用)
#
# 替代 Windows 下的 .bat / 任务计划程序, 统一注入运行所需的环境变量,
# 并用项目 venv 的 python (macOS 为 .venv/bin/python) 运行 Python 启动器。
#
# 用法 (由 plist 调用): run_quant_job.sh <启动器脚本相对项目根的路径> [额外参数...]
#   例: run_quant_job.sh 15_每日工作流/run_daily_eod_workflow.py
# ===========================================================================
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# macos/launchd/run_quant_job.sh -> 向上两级 = 项目根 (28-终极量化交易系统8.4)
PROJECT_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"

# ---- 原 Windows .bat 注入的必需环境变量 (见 15_每日工作流/run_eod_with_env.bat) ----
export NO_PROXY="push2his.eastmoney.com,push2.eastmoney.com,eastmoney.com,sinajs.cn,sina.com.cn"
export no_proxy="$NO_PROXY"
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export PYTHONIOENCODING=utf-8

# ---- 以下为敏感变量, 请自行在 ~/.zshrc 配置, 或由 plist 的 EnvironmentVariables 注入 ----
# export WIND_API_KEY="..."
# export WIND_MCP_SKILL_DIR="..."
# export QUANT11_ENV_FILE="..."
# export QUANT11_STOPLOSS_CONFIG="..."
# export TUSHARE_TOKEN="..."
# export QMT_ACCOUNT=""
# export TRADING_ENV="paper"   # production 需双签, 见 broker_factory

# ---- 选定解释器: 优先项目 venv, 退化到系统 python3 ----
PY="${PROJECT_DIR}/.venv/bin/python"
if [ ! -x "$PY" ]; then
    PY="$(command -v python3 || command -v python)"
fi

cd "$PROJECT_DIR" || exit 1
exec "$PY" "$@"
