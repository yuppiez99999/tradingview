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

CONFIG_PATH = PROJECT_ROOT / "config" / "shadow_etf_subportfolio.json"
STATE_DIR = PROJECT_ROOT / "output" / "shadow_etf"
STATE_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
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
        "fail_fast": ff,
        "backtest_baseline": cfg.get("backtest_baseline", {}),
        "admission_criteria": cfg.get("admission_criteria", {}),
    }
    state_path = STATE_DIR / "shadow_state.json"
    state_path.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return state


def show_status() -> None:
    state_path = STATE_DIR / "shadow_state.json"
    if not state_path.exists():
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
            f"  年化≥{ac.get('min_annual_return', 0)*100:.0f}% / 回撤≤{ac.get('max_drawdown', 0)*100:.0f}% / Sharpe≥{ac.get('min_sharpe', 0):.2f}"
        )
        print(
            f"  DSR≥{ac.get('min_dsr', 0):.2f} / CPCV CV<{ac.get('max_cpcv_cv', 0):.2f}"
        )


def run_backtest_verify(cfg: dict) -> None:
    """用 S6 回测数据验证影子账户机制"""
    import importlib.util

    ref = PROJECT_ROOT / "data" / "etf_option_backtest" / "run_etf_option_backtest.py"
    spec = importlib.util.spec_from_file_location("etf_ref", str(ref))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["etf_ref"] = mod
    spec.loader.exec_module(mod)

    data_file = r"D:\etf_data_2015_2026\all_etf_daily.parquet"
    prices = mod.load_etf_prices(data_file)
    tw = mod.extract_target_weights(mod.load_config())
    bench = mod.run_benchmark(prices)

    eq, tc = mod.run_s6_v9_regime(prices, tw)
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
    print(f"\n=== 影子账户回测验证 ({len(eq)-1} 日) ===")
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


def main():
    parser = argparse.ArgumentParser(description="ETF子组合影子账户 (P3.1)")
    parser.add_argument("--status", action="store_true", help="查看状态")
    parser.add_argument("--backtest", action="store_true", help="用回测数据验证")
    parser.add_argument("--daily", action="store_true", help="记录今日净值")
    args = parser.parse_args()

    cfg = load_config()

    if args.status:
        show_status()
    elif args.backtest:
        run_backtest_verify(cfg)
    else:
        state = init_shadow(cfg)
        print("=== ETF子组合影子账户已初始化 ===")
        print(f"账户ID: {state['account_id']}")
        print(f"策略: {state['strategy_id']}")
        print(f"初始资金: {state['initial_capital']:,.0f} 元")
        print(f"观察期: {cfg['observation']['observation_days']} 天")
        print(f"状态文件: {STATE_DIR / 'shadow_state.json'}")
        print(
            "\n下一步: python scripts/launch_etf_shadow.py --backtest  # 用回测数据验证"
        )


if __name__ == "__main__":
    main()
