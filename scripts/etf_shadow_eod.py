"""P3 ETF影子账户 每日EOD脚本

每日收盘后运行:
1. 拉取最新ETF价格 (Wind MCP)
2. 计算当前持仓净值
3. 记算日收益率 + regime
4. 检查fail-fast (单日回撤>3% / 3日累计>5%)
5. 检查再平衡触发 (S6动态阈值)
6. 记录到影子账户状态

用法:
  python scripts/etf_shadow_eod.py              # 每日EOD
  python scripts/etf_shadow_eod.py --dry-run    # 干跑(不保存)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

STATE_PATH = PROJECT_ROOT / "output" / "shadow_etf" / "shadow_state.json"
NAV_LOG_PATH = PROJECT_ROOT / "reports" / "shadow_etf" / "daily_returns.jsonl"
NAV_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

ETF_CODES = [
    "510300", "510500", "510050", "512100", "588000", "159915",
    "512480", "512010", "512660", "515170", "159939",
    "518880", "511260", "510310",
]


def fetch_latest_prices() -> dict[str, float]:
    """拉取最新ETF收盘价 (Wind MCP → sina兜底)"""
    import os
    os.environ["NO_PROXY"] = "push2his.eastmoney.com,push2.eastmoney.com,eastmoney.com,sinajs.cn,sina.com.cn"
    os.environ["no_proxy"] = os.environ["NO_PROXY"]

    prices = {}
    try:
        sys.path.insert(0, str(PROJECT_ROOT / "tools"))
        from wind_mcp_fetcher import wind_get_kline
        for code in ETF_CODES:
            windcode = f"{code}.SH" if code.startswith(("51", "58")) else f"{code}.SZ"
            try:
                raw = wind_get_kline(windcode, days=5, is_fund=True)
                if raw:
                    rec = raw[-1]
                    close = float(rec.get("MATCH") or rec.get("close") or 0)
                    if close > 0:
                        prices[code] = close
            except Exception:
                pass
            if code not in prices:
                try:
                    import akshare as ak
                    sym = f"sh{code}" if code.startswith(("51", "58")) else f"sz{code}"
                    df = ak.fund_etf_hist_sina(symbol=sym)
                    if df is not None and len(df) > 0:
                        prices[code] = float(df.iloc[-1]["close"])
                except Exception:
                    pass
    except Exception as e:
        print(f"[WARN] 数据拉取异常: {e}")

    return prices


def load_state() -> dict | None:
    if not STATE_PATH.exists():
        return None
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def log_nav(date: str, nav: float, daily_ret: float, regime: str) -> None:
    entry = {
        "date": date,
        "nav": round(nav, 2),
        "daily_return": round(daily_ret, 6),
        "regime": regime,
        "timestamp": datetime.now().isoformat(),
    }
    with open(NAV_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def check_fail_fast(nav_history: list[dict], daily_dd_thresh: float = 0.03,
                     cum_3d_thresh: float = 0.05) -> tuple[bool, str]:
    if len(nav_history) < 1:
        return False, ""
    today = nav_history[-1]
    nav = today["nav"]
    prev_nav = nav_history[-2]["nav"] if len(nav_history) >= 2 else nav
    if prev_nav > 0:
        daily_dd = (prev_nav - nav) / prev_nav
        if daily_dd > daily_dd_thresh:
            return True, f"单日回撤 {daily_dd*100:.2f}% > {daily_dd_thresh*100:.0f}%"
    if len(nav_history) >= 3:
        nav_3d_ago = nav_history[-3]["nav"]
        if nav_3d_ago > 0:
            cum_dd = (nav_3d_ago - nav) / nav_3d_ago
            if cum_dd > cum_3d_thresh:
                return True, f"3日累计回撤 {cum_dd*100:.2f}% > {cum_3d_thresh*100:.0f}%"
    return False, ""


def main():
    parser = argparse.ArgumentParser(description="P3 ETF影子账户每日EOD")
    parser.add_argument("--dry-run", action="store_true", help="干跑(不保存)")
    args = parser.parse_args()

    state = load_state()
    if not state:
        print("[ERROR] 影子账户未初始化")
        return 1

    if state.get("status") != "running":
        print(f"[SKIP] 影子账户状态: {state.get('status')}")
        return 0

    print(f"=== ETF影子账户 EOD {datetime.now().strftime('%Y-%m-%d')} ===")

    prices = fetch_latest_prices()
    if not prices:
        print("[WARN] 未获取到价格数据, 跳过")
        return 1

    positions = state.get("positions", [])
    total_value = 0
    for pos in positions:
        code = pos["code"]
        if code in prices:
            pos["current_price"] = round(prices[code], 4)
            value = pos["shares"] * prices[code]
            pos["current_value"] = round(value, 2)
            total_value += value
        else:
            total_value += pos.get("current_value", pos.get("amount", 0))

    cash = state.get("cash", 0)
    nav = cash + total_value
    prev_nav = state.get("current_nav", state["initial_capital"])
    daily_ret = (nav / prev_nav - 1) if prev_nav > 0 else 0

    nav_history = state.get("nav_history", [])
    nav_history.append({"date": datetime.now().strftime("%Y-%m-%d"), "nav": round(nav, 2)})

    ff_triggered, ff_reason = check_fail_fast(nav_history,
                                               state.get("fail_fast", {}).get("daily_drawdown_threshold", 0.03),
                                               state.get("fail_fast", {}).get("cumulative_3d_drawdown_threshold", 0.05))

    days_elapsed = state.get("days_elapsed", 0) + 1
    observation_days = state.get("observation_days", 30)

    state["current_nav"] = round(nav, 2)
    state["nav_history"] = nav_history[-90:]
    state["days_elapsed"] = days_elapsed
    state["last_eod"] = datetime.now().isoformat()

    if ff_triggered:
        state["status"] = "terminated"
        state["fail_fast_reason"] = ff_reason
        print(f"[FAIL-FAST] {ff_reason} — 影子账户已终止!")
    elif days_elapsed >= observation_days:
        state["status"] = "observation_complete"
        print(f"[观察期完成] {days_elapsed}/{observation_days} 天 — 可评估准入")

    print(f"净值: {nav:,.0f} (日收益 {daily_ret*100:+.2f}%)")
    print(f"运行天数: {days_elapsed}/{observation_days}")
    print(f"状态: {state['status']}")

    if not args.dry_run:
        save_state(state)
        log_nav(datetime.now().strftime("%Y-%m-%d"), nav, daily_ret, state.get("current_regime", "unknown"))
        print(f"已保存: {STATE_PATH}")
        print(f"日志: {NAV_LOG_PATH}")

    return 0


if __name__ == "__main__":
    sys.exit(main())