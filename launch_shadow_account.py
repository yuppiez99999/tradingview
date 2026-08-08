"""
影子账户启动脚本 (Shadow Account Launcher)
==========================================

职责:
- 初始化 V9 策略的影子账户 (10% 资金 = 500,000)
- 加载回测基准 + fail-fast 配置
- 创建影子账户实例并保存状态
- 供 daily_workflow Phase 10 每日调用记录净值

使用:
    python launch_shadow_account.py              # 启动/初始化影子账户
    python launch_shadow_account.py --status     # 查看影子账户状态
    python launch_shadow_account.py --advance    # 手动推进灰度阶段 (需通过评估)

2026-07-25 顶级对冲基金审计: 影子账户 fail-fast 立即执行
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent
# Wave 3 第三阶段: 改用 utils.path_config.setup_sys_path() 统一管理
sys.path.insert(0, str(BASE_DIR))  # bootstrap: 确保 utils 包可导入
from utils.path_config import setup_sys_path  # noqa: E402
setup_sys_path()  # noqa: E402  # 统一注入 v8.3 根 / v8.3 src / utils
# 保留: validation 子目录 (setup_sys_path 未涵盖)
sys.path.insert(0, str(BASE_DIR / "v8.3_institutional" / "src" / "validation"))  # noqa: E402

# 日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(str(BASE_DIR / "shadow_account.log"), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("shadow_launcher")

# 配置文件路径
CONFIG_FILE = BASE_DIR / "config" / "shadow_account_config.json"
STATE_FILE = BASE_DIR / "output" / "shadow_account" / "shadow_state.json"
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """加载影子账户配置"""
    if not CONFIG_FILE.exists():
        logger.error("配置文件不存在: %s", CONFIG_FILE)
        sys.exit(1)
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_state() -> dict | None:
    """加载已有的影子账户状态"""
    if not STATE_FILE.exists():
        return None
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.warning("加载状态失败: %s", e)
        return None


def save_state(state: dict) -> None:
    """保存影子账户状态"""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, default=str)
    logger.info("影子账户状态已保存: %s", STATE_FILE)


def init_shadow_account() -> dict:
    """初始化影子账户 (Stage 1: 10% 资金 = 500,000)"""
    config = load_config()

    # 检查环境
    from utils.trading_env import get_trading_env_config

    env_config = get_trading_env_config()
    logger.info("=" * 70)
    logger.info("影子账户初始化")
    logger.info("=" * 70)
    logger.info("交易环境: %s (fail_closed=%s)", env_config.env, env_config.fail_closed)

    if not env_config.fail_closed:
        logger.warning(
            "⚠️ 当前环境 %s 未激活 fail-closed! 建议设置 TRADING_ENV=production 或 TRADING_ENV=shadow",
            env_config.env,
        )

    # 加载回测基准
    benchmark = config.get("backtest_benchmark", {})
    capital_config = config.get("capital_config", {})
    fail_fast_config = config.get("fail_fast_config", {})

    total_capital = float(capital_config.get("total_capital", 5_000_000))
    shadow_capital = float(capital_config.get("shadow_initial_capital", 500_000))

    logger.info("策略 ID: %s", config.get("strategy_id", ""))
    logger.info(
        "回测基准: 年化=%.2f%%, 回撤=%.2f%%, Sharpe=%.2f",
        benchmark.get("annual_return", 0) * 100,
        benchmark.get("max_drawdown", 0) * 100,
        benchmark.get("sharpe_annual", 0),
    )
    logger.info(
        "影子资金: ¥%.0f (总资金 %.0f 的 %.0f%%)",
        shadow_capital,
        total_capital,
        shadow_capital / total_capital * 100 if total_capital > 0 else 0,
    )
    logger.info(
        "Fail-fast: 单日>%.0f%%, 3日>%.0f%%",
        fail_fast_config.get("daily_drawdown_threshold", 0.03) * 100,
        fail_fast_config.get("cumulative_3d_drawdown_threshold", 0.05) * 100,
    )

    # 创建影子账户状态
    state = {
        "strategy_id": config.get("strategy_id", "V9_REGIME_SPECIFIC_LGB"),
        "account_id": f"shadow_v9_{datetime.now().strftime('%Y%m%d')}",
        "status": "RUNNING",
        "current_stage": 0,  # Stage 1: 10% 资金
        "stage_name": "stage_1",
        "capital_allocated": shadow_capital,
        "capital_pct": shadow_capital / total_capital if total_capital > 0 else 0,
        "initial_capital": shadow_capital,
        "current_capital": shadow_capital,
        "current_nav": 1.0,  # 净值从 1.0 开始
        "start_date": datetime.now().strftime("%Y-%m-%d"),
        "start_time": datetime.now().isoformat(),
        "daily_nav": [],
        "trade_log": [],
        "fail_fast_log": [],
        "fail_fast_config": fail_fast_config,
        "backtest_benchmark": benchmark,
        "acceptance_criteria": config.get("acceptance_criteria", {}),
        "gray_release_stages": config.get("gray_release_stages", []),
        "symbols": config.get("symbols", []),
        "risk_constraints": config.get("risk_constraints", {}),
        "last_updated": datetime.now().isoformat(),
    }

    save_state(state)

    logger.info("=" * 70)
    logger.info("✓ 影子账户已初始化 (Stage 1: 10%% 资金 = ¥%.0f)", shadow_capital)
    logger.info("  账户 ID: %s", state["account_id"])
    logger.info("  启动日期: %s", state["start_date"])
    logger.info("  状态文件: %s", STATE_FILE)
    logger.info("=" * 70)
    logger.info("下一步: daily_workflow Phase 10 将每日记录净值并检查 fail-fast")

    return state


def show_status() -> None:
    """显示影子账户状态"""
    state = load_state()
    if state is None:
        logger.warning("未找到影子账户状态, 请先运行: python launch_shadow_account.py")
        return

    logger.info("=" * 70)
    logger.info("影子账户状态")
    logger.info("=" * 70)
    logger.info("  账户 ID:        %s", state.get("account_id", ""))
    logger.info("  策略 ID:        %s", state.get("strategy_id", ""))
    logger.info("  状态:           %s", state.get("status", ""))
    logger.info("  当前阶段:       %s (%s)", state.get("stage_name", ""), state.get("current_stage", 0))
    logger.info("  初始资金:       ¥%.0f", state.get("initial_capital", 0))
    logger.info("  当前资金:       ¥%.0f", state.get("current_capital", 0))
    logger.info("  当前净值:       %.4f", state.get("current_nav", 1.0))
    logger.info("  启动日期:       %s", state.get("start_date", ""))
    logger.info("  运行天数:       %d", len(state.get("daily_nav", [])))

    # fail-fast 状态
    ff_log = state.get("fail_fast_log", [])
    if ff_log:
        logger.info("  ⚠️ Fail-fast 已触发 %d 次!", len(ff_log))
        for ff in ff_log:
            logger.info("    - %s: %s", ff.get("terminated_at", ""), ff.get("reason", ""))
    else:
        logger.info("  Fail-fast:      未触发 (正常)")

    # 最近 5 天净值
    daily_nav = state.get("daily_nav", [])
    if daily_nav:
        logger.info("  最近净值记录:")
        for nav in daily_nav[-5:]:
            logger.info("    %s: nav=%.4f", nav.get("date", ""), nav.get("nav", 0))

    # 绩效统计
    if len(daily_nav) >= 2:
        start_nav = daily_nav[0].get("nav", 1.0)
        end_nav = daily_nav[-1].get("nav", 1.0)
        total_return = (end_nav / start_nav - 1) if start_nav > 0 else 0
        logger.info("  累计收益:       %.2f%%", total_return * 100)

    logger.info("=" * 70)


def advance_stage() -> None:
    """手动推进灰度阶段"""
    state = load_state()
    if state is None:
        logger.error("未找到影子账户状态, 请先初始化")
        sys.exit(1)

    current_stage = state.get("current_stage", 0)
    stages = state.get("gray_release_stages", [])

    if current_stage >= len(stages) - 1:
        logger.info("已在最终阶段 (%s), 无法继续推进", stages[-1].get("name", ""))
        return

    # 检查运行天数
    daily_nav = state.get("daily_nav", [])
    min_days = state.get("acceptance_criteria", {}).get("min_running_days_before_advance", 14)
    if len(daily_nav) < min_days:
        logger.warning(
            "运行天数不足: %d/%d 天 (需至少 %d 天才能推进)",
            len(daily_nav),
            min_days,
            min_days,
        )
        return

    # 检查 fail-fast
    if state.get("status") == "TERMINATED":
        logger.error("影子账户已被 fail-fast 终止! 无法推进, 需回滚")
        return

    # 推进阶段
    next_stage = current_stage + 1
    next_stage_info = stages[next_stage]
    state["current_stage"] = next_stage
    state["stage_name"] = next_stage_info.get("name", "")
    state["capital_allocated"] = float(next_stage_info.get("capital_amount", 0))
    state["capital_pct"] = float(next_stage_info.get("capital_pct", 0))
    state["last_updated"] = datetime.now().isoformat()

    save_state(state)

    logger.info("=" * 70)
    logger.info("✓ 灰度阶段推进: %s → %s", stages[current_stage].get("name", ""), next_stage_info.get("name", ""))
    logger.info("  资金分配: ¥%.0f (%.0f%%)", state["capital_allocated"], state["capital_pct"] * 100)
    logger.info("=" * 70)


def main() -> None:
    """主入口"""
    parser = argparse.ArgumentParser(description="影子账户启动与管理")
    parser.add_argument("--status", action="store_true", help="查看影子账户状态")
    parser.add_argument("--advance", action="store_true", help="推进灰度阶段 (需通过评估)")
    args = parser.parse_args()

    if args.status:
        show_status()
    elif args.advance:
        advance_stage()
    else:
        # 默认: 初始化影子账户
        existing = load_state()
        if existing is not None:
            logger.info("影子账户已存在 (启动于 %s)", existing.get("start_date", ""))
            logger.info("如需重新初始化, 请先删除: %s", STATE_FILE)
            show_status()
        else:
            init_shadow_account()


if __name__ == "__main__":
    main()
