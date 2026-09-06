"""ETF期权对冲子组合 影子账户启动脚本 (P3.1)

复用 shadow_account_system.py 的 ShadowAccount + FailFastMonitor,
为 14 ETF 子组合配置 200万虚拟资金, S6 V9 Regime 策略.

用法:
  python scripts/launch_etf_shadow.py              # 初始化影子账户
  python scripts/launch_etf_shadow.py --status     # 查看状态
  python scripts/launch_etf_shadow.py --backtest   # 用回测数据验证影子账户
  python scripts/launch_etf_shadow.py --daily      # 记录今日净值 (EOD 调用)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_CONFIG = "shadow_etf_subportfolio.json"
STATE_DIR = PROJECT_ROOT / "output" / "shadow_etf"
STATE_DIR.mkdir(parents=True, exist_ok=True)


def load_config(config_file: str = DEFAULT_CONFIG) -> dict:
    path = PROJECT_ROOT / "config" / config_file
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def init_shadow(cfg: dict) -> dict:
    from shadow_account_system import create_shadow_account

    cap = cfg["capital"]["shadow_initial_capital"]
    ff = cfg["fail_fast"]
    sa = create_shadow_account(
        account_id=cfg["account_id"],
        strategy_id=cfg["strategy_id"],
        initial_capital=cap,
        daily_dd_threshold=ff["daily_drawdown_threshold"],
        cumulative_3d_threshold=ff["cumulative_3d_drawdown_threshold"],
    )

    state = {
        "account_id": sa.account_id,
        "strategy_id": sa.strategy_id,
        "initial_capital": cap,
        "current_nav": cap,
        "status": "running",
        "created_at": datetime.now().isoformat(),
        "n_days": 0,
        "daily_nav": [],
        "fail_fast": ff,
        "backtest_baseline": cfg.get("backtest_baseline", {}),
        "admission_criteria": cfg.get("admission_criteria", {}),
    }
    state_path = STATE_DIR / f"shadow_state_{cfg['account_id']}.json"
    state_path.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return state


def show_status(config_file: str = DEFAULT_CONFIG) -> None:
    cfg = load_config(config_file)
    state_path = STATE_DIR / f"shadow_state_{cfg['account_id']}.json"
    # 兼容旧版固定文件名 (account_id=shadow_etf_subportfolio 迁移)
    if not state_path.exists():
        legacy = STATE_DIR / "shadow_state.json"
        if legacy.exists():
            state_path = legacy
        else:
            print("影子账户未初始化, 请先运行: python scripts/launch_etf_shadow.py")
            return
    state = json.loads(state_path.read_text(encoding="utf-8"))
    print(f"账户ID: {state['account_id']}")
    print(f"策略: {state['strategy_id']}")
    print(f"初始资金: {state['initial_capital']:,.0f}")
    print(f"当前净值: {state.get('current_nav', 0):,.0f}")
    print(f"状态: {state.get('status', 'unknown')}")
    print(f"运行天数: {state.get('n_days', 0)}")
    print(f"创建时间: {state.get('created_at', 'N/A')}")

    bl = state.get("backtest_baseline", {})
    if bl:
        print(f"\n回测基准 ({bl.get('data_range', 'N/A')}):")
        print(f"  年化: {bl.get('annual_return', 0)*100:.2f}%")
        print(f"  回撤: {bl.get('max_drawdown', 0)*100:.2f}%")
        print(f"  Sharpe: {bl.get('sharpe', 0):.3f}")
        print(f"  DSR: {bl.get('dsr', 0):.4f}")
        print(f"  诚实验证: {bl.get('honest_validation', 'N/A')}")

    ac = state.get("admission_criteria", {})
    if ac:
        print("\n准入标准:")
        print(
            f"  年化≥{ac.get('min_annual_return', 0)*100:.0f}% / 回撤≤{ac.get('max_drawdown', 0)*100:.0f}% / Sharpe≥{ac.get('min_sharpe', 0):.2f}"  # noqa: E501
        )
        print(
            f"  DSR≥{ac.get('min_dsr', 0):.2f} / CPCV CV<{ac.get('max_cpcv_cv', 0):.2f}"
        )


# 回测策略映射 (--strategy 选项)
STRATEGY_FNS = {
    "s6": ("S6_V9_REGIME_ETF", "S6 V9Regime轮动+熔断"),
    "s8": ("S8_TREND_VOL", "S8 趋势+波动率目标(哑铃防御)"),
    "s9": ("S9_DEFENSIVE_DUMBBELL", "S9 防御倾斜哑铃(黄金28%+国债12%)"),
    "s10": ("S10_P2_POOL_UPGRADE", "S10 P2池升级(纳指标普+红利低波, 45%防御)"),
}

# 各策略回测数据文件 (S10 需 17 标的扩展池, 其余 14 标的长样本)
# 2026-09-05: 长样本数据由 D:\etf_data_2015_2026 并入工程 data/etf_2015_2026 (自包含)
_ETF_LONG_PARQUET = str(PROJECT_ROOT / "data" / "etf_2015_2026" / "all_etf_daily.parquet")
DATA_FILES = {
    "s6": _ETF_LONG_PARQUET,
    "s8": _ETF_LONG_PARQUET,
    "s9": _ETF_LONG_PARQUET,
    "s10": str(PROJECT_ROOT / "data" / "etf_option_backtest" / "p2_universe_2015_2026.parquet"),
}


def run_backtest_verify(cfg: dict, strategy: str = "s9") -> None:
    """用回测数据验证影子账户机制 (默认 S9, 推荐候选策略)"""
    import importlib.util

    ref = PROJECT_ROOT / "data" / "etf_option_backtest" / "run_etf_option_backtest.py"
    spec = importlib.util.spec_from_file_location("etf_ref", str(ref))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["etf_ref"] = mod
    spec.loader.exec_module(mod)

    data_file = DATA_FILES[strategy]
    prices = mod.load_etf_prices(data_file)
    tw = mod.extract_target_weights(mod.load_config())
    bench = mod.run_benchmark(prices)

    if strategy == "s6":
        eq, tc = mod.run_s6_v9_regime(prices, tw)
    elif strategy == "s8":
        eq, tc = mod.run_s8_trend_vol(prices, tw)
    elif strategy == "s10":
        eq, tc = mod.run_s10_p2(prices, tw)
    else:
        eq, tc = mod.run_s9_dumbbell(prices, tw)
    m = mod.compute_metrics(eq, bench)

    from shadow_account_system import create_shadow_account

    cap = cfg["capital"]["shadow_initial_capital"]
    sa = create_shadow_account(
        account_id=cfg["account_id"],
        strategy_id=cfg["strategy_id"],
        initial_capital=cap,
    )

    n_terminated = 0
    for i in range(1, len(eq)):
        date = str(prices.index[i].date())
        nav = eq[i]
        sa.record_daily_nav(date, nav)
        if sa.status.value == "terminated":
            n_terminated = i
            break

    perf = sa.get_performance()
    print(f"\n=== 影子账户回测验证 ({len(eq)-1} 日, 策略 {STRATEGY_FNS[strategy][1]}) ===")
    print(f"初始资金: {cap:,.0f}")
    print(f"最终净值: {perf.get('current_nav', 0):,.0f}")
    print(f"运行天数: {perf.get('n_days', 0)}")
    print(f"状态: {sa.status.value}")
    print(f"Fail-fast 触发: {'是' if n_terminated else '否'}")
    print("\n回测指标:")
    print(f"  年化: {m['annual_return']*100:.2f}%")
    print(f"  回撤: {m['max_drawdown']*100:.2f}%")
    print(f"  Sharpe: {m['sharpe']:.3f}")
    print("\n回测基准对比:")
    bl = cfg.get("backtest_baseline", {})
    print(
        f"  回测年化: {bl.get('annual_return', 0)*100:.2f}% vs 实测 {m['annual_return']*100:.2f}%"
    )
    print(
        f"  回撤: {bl.get('max_drawdown', 0)*100:.2f}% vs 实测 {m['max_drawdown']*100:.2f}%"
    )
    print(f"  Sharpe: {bl.get('sharpe', 0):.3f} vs 实测 {m['sharpe']:.3f}")


def record_daily(cfg: dict, nav: float | None, nav_date: str | None) -> None:
    """记录今日净值 (EOD 调用): --daily [--nav <净值>] [--date YYYY-MM-DD].

    nav: 当日策略净值 (相对初始资金 1.0 归一化). 不传时从回测基准年化估算
         (纸面跟踪起点, 后续应由 EOD 流程传入真实组合净值).
    """
    from shadow_account_system import AccountStatus, create_shadow_account

    account_id = cfg["account_id"]
    state_path = STATE_DIR / f"shadow_state_{account_id}.json"
    if not state_path.exists():
        print(f"影子账户 {account_id} 未初始化, 请先运行初始化.")
        return

    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("status") == "terminated":
        print("影子账户已终止 (fail-fast), 不再记录.")
        return

    date = nav_date or datetime.now().strftime("%Y-%m-%d")
    if nav is None:
        bl = state.get("backtest_baseline", {})
        annual = bl.get("annual_return", 0.0)
        days_run = state.get("n_days", 0)
        nav = round((1.0 + annual) ** (min(days_run + 1, 252) / 252.0), 6)
        print(f"(未传 --nav, 按基准年化 {annual*100:.1f}% 估算净值 {nav})")

    ff = state.get("fail_fast", {})
    sa = create_shadow_account(
        account_id=account_id,
        strategy_id=cfg["strategy_id"],
        initial_capital=cfg["capital"]["shadow_initial_capital"],
        daily_dd_threshold=ff.get("daily_drawdown_threshold", 0.03),
        cumulative_3d_threshold=ff.get("cumulative_3d_drawdown_threshold", 0.05),
    )
    # 恢复历史净值序列 (fail-fast 基于完整历史评估)
    for entry in state.get("daily_nav", []):
        sa.daily_nav.append(entry)
    sa.current_nav = state.get("current_nav", 1.0)
    sa.current_capital = state.get("current_nav", 1.0) * sa.initial_capital

    existing = [e for e in sa.daily_nav if e["date"] == date]
    if existing:
        print(f"[{date}] 已有记录 (nav={existing[-1]['nav']:.6f}), 更新为真实净值 {float(nav):.6f}")
        sa.daily_nav = [e for e in sa.daily_nav if e["date"] != date]
        if sa.daily_nav:
            sa.current_nav = sa.daily_nav[-1]["nav"]
            sa.current_capital = sa.current_nav * sa.initial_capital
        else:
            sa.current_nav = 1.0
            sa.current_capital = sa.initial_capital

    sa.record_daily_nav(date, float(nav))
    state["current_nav"] = sa.current_nav
    state["current_capital"] = sa.current_capital
    state["n_days"] = len(sa.daily_nav)
    state["daily_nav"] = sa.daily_nav
    if sa.status != AccountStatus.RUNNING:
        state["status"] = "terminated"
    state_path.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[{date}] 净值 {sa.current_nav:.6f} (初始资金 {sa.initial_capital:,.0f})")
    print(f"运行天数: {state['n_days']} | 状态: {state['status']}")
    if sa.status != AccountStatus.RUNNING:
        print("FAIL-FAST 已触发 — 账户终止.")


def main():
    parser = argparse.ArgumentParser(description="ETF子组合影子账户 (P3.1)")
    parser.add_argument("--status", action="store_true", help="查看状态")
    parser.add_argument("--backtest", action="store_true", help="用回测数据验证")
    parser.add_argument("--daily", action="store_true", help="记录今日净值 (EOD 调用)")
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help="影子账户配置文件名 (config/ 下, 默认 shadow_etf_subportfolio.json)",
    )
    parser.add_argument(
        "--strategy",
        default="s9",
        choices=["s6", "s8", "s9", "s10"],
        help="回测验证策略 (默认 s9 防御倾斜哑铃; s10 P2池升级)",
    )
    parser.add_argument("--nav", type=float, default=None, help="当日策略净值 (相对初始 1.0)")
    parser.add_argument("--date", default=None, help="记录日期 YYYY-MM-DD (默认今天)")
    args = parser.parse_args()

    cfg = load_config(args.config)

    if args.status:
        show_status(args.config)
    elif args.backtest:
        run_backtest_verify(cfg, args.strategy)
    elif args.daily:
        record_daily(cfg, args.nav, args.date)
    else:
        state = init_shadow(cfg)
        print("=== ETF子组合影子账户已初始化 ===")
        print(f"账户ID: {state['account_id']}")
        print(f"策略: {state['strategy_id']}")
        print(f"初始资金: {state['initial_capital']:,.0f} 元")
        print(f"观察期: {cfg['observation']['observation_days']} 天")
        print(f"状态文件: {STATE_DIR / ('shadow_state_' + cfg['account_id'] + '.json')}")
        print(
            "\n下一步: python scripts/launch_etf_shadow.py --backtest  # 用回测数据验证"
        )


if __name__ == "__main__":
    main()
