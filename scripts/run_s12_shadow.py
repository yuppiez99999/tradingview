"""S12 纯防御风险平价 — Phase 3 影子账户每日运行器 (2026-09-02).

策略口径与 data/etf_option_backtest/run_etf_option_backtest.py 的
run_s12_defensive_rp 严格一致:
  - 池: 黄金 518880 / 国债 511260 / 红利低波 512890
  - 权重: 逆波动率 (60 交易日窗, 仅用 ≤t-1 数据, 无前视)
  - 再平衡: 每 21 交易日, turnover = 0.5·Σ|Δw|
  - 成本: (0.0003 + 0.001) × turnover, 当日收益中扣除

影子账户: 复用 shadow_account_system.ShadowAccount (200 万虚拟资金,
fail-fast 单日 3% / 3 日累计 5%)。状态落盘 output/shadow_account/。

数据源链: Wind MCP (前复权, 主) → akshare fund_etf_hist_em (qfq, 备)
→ akshare fund_etf_hist_sina (未复权, 末备, 记录降级)。

用法:
  python scripts/run_s12_shadow.py --init    # 初始化账户 (记录首日 NAV=1.0)
  python scripts/run_s12_shadow.py           # 每日 EOD 增量 (幂等, 自动补漏)
  python scripts/run_s12_shadow.py --status  # 账户状态摘要
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import pandas as pd

from utils.datetime_utils import now_bj

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from shadow_account_system import ShadowAccount  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [S12-Shadow] %(levelname)s %(message)s")
logger = logging.getLogger("s12_shadow")

CONFIG_PATH = _PROJECT_ROOT / "config" / "s12_shadow_config.json"
STATE_DIR = _PROJECT_ROOT / "output" / "shadow_account"
STATE_PATH = STATE_DIR / "s12_shadow_state.json"

WIND_SUFFIX = {"518880": "SH", "511260": "SH", "512890": "SH"}
SINA_PREFIX = {"518880": "sh", "511260": "sh", "512890": "sh"}
PRICE_LOOKBACK_DAYS = 130  # ≥ 61 收盘 (60 收益) + 再平衡间隔缓冲


# ============================================================
# 数据源
# ============================================================
def _fetch_wind(codes: list[str]) -> pd.DataFrame | None:
    """Wind MCP 前复权日线 → DataFrame[date × code] (close)."""
    try:
        sys.path.insert(0, str(_PROJECT_ROOT / "tools"))
        from wind_mcp_fetcher import wind_get_kline
    except (ImportError, OSError) as e:
        logger.warning("[data] Wind MCP 不可用: %s", e)
        return None
    cols: dict[str, pd.Series] = {}
    for code in codes:
        try:
            raw = wind_get_kline(
                f"{code}.{WIND_SUFFIX.get(code, 'SH')}",
                days=PRICE_LOOKBACK_DAYS, is_fund=True,
            )
        except (OSError, ValueError, TypeError, KeyError) as e:  # 数据源 fail-safe
            logger.warning("[data] Wind %s 异常: %s", code, e)
            return None
        if not raw:
            return None
        s = pd.Series(
            {str(r.get("TIME", ""))[:10]: float(r["MATCH"]) for r in raw if r.get("MATCH")},
            dtype=float,
        )
        if s.empty:
            return None
        cols[code] = s
    df = pd.DataFrame(cols)
    df.index = pd.to_datetime(df.index)
    return df.sort_index()


def _fetch_akshare_em(codes: list[str]) -> pd.DataFrame | None:
    """akshare 东财 qfq 日线 (东财常被代理拒连, 备选)."""
    os.environ["NO_PROXY"] = "push2his.eastmoney.com,push2.eastmoney.com,eastmoney.com"
    os.environ["no_proxy"] = os.environ["NO_PROXY"]
    try:
        import akshare as ak
    except (ImportError, OSError) as e:
        logger.warning("[data] akshare 不可用: %s", e)
        return None
    frames = []
    for code in codes:
        try:
            df = ak.fund_etf_hist_em(symbol=code, period="daily", adjust="qfq")
            if df is None or df.empty:
                return None
            s = df.set_index(pd.to_datetime(df["日期"]))["收盘"].astype(float).rename(code)
            frames.append(s)
        except (OSError, ValueError, TypeError, KeyError, IndexError, ImportError) as e:  # akshare em 拉取/解析失败
            logger.warning("[data] em %s 异常: %s", code, e)
            return None
    out = pd.concat(frames, axis=1)
    return out.tail(PRICE_LOOKBACK_DAYS).sort_index()


def _fetch_akshare_sina(codes: list[str]) -> pd.DataFrame | None:
    """akshare 新浪未复权日线 (末备 — 防御三资产近年无分红折算, 失真风险低)."""
    os.environ["NO_PROXY"] = "sinajs.cn,sina.com.cn"
    os.environ.setdefault("no_proxy", os.environ["NO_PROXY"])
    try:
        import akshare as ak
    except (ImportError, OSError) as e:
        logger.warning("[data] akshare 不可用: %s", e)
        return None
    frames = []
    for code in codes:
        try:
            df = ak.fund_etf_hist_sina(symbol=f"{SINA_PREFIX.get(code, 'sh')}{code}")
            if df is None or df.empty:
                return None
            s = df.set_index(pd.to_datetime(df["date"]))["close"].astype(float).rename(code)
            frames.append(s)
        except (OSError, ValueError, TypeError, KeyError, IndexError, ImportError) as e:  # akshare sina 拉取/解析失败
            logger.warning("[data] sina %s 异常: %s", code, e)
            return None
    out = pd.concat(frames, axis=1)
    return out.tail(PRICE_LOOKBACK_DAYS).sort_index()


def fetch_prices(codes: list[str]) -> tuple[pd.DataFrame, str]:
    """三源链拉收盘面板, 返回 (panel, source_name)."""
    for fn, name in (
        (_fetch_wind, "wind_mcp"),
        (_fetch_akshare_em, "akshare_em_qfq"),
        (_fetch_akshare_sina, "akshare_sina_unadjusted"),
    ):
        df = fn(codes)
        if df is not None and not df.empty and df.notna().any().all():
            logger.info("[data] %s: %d 行 × %d 列 (%s ~ %s)",
                        name, len(df), df.shape[1], df.index[0].date(), df.index[-1].date())
            return df, name
    raise ConnectionError("三个数据源均失败, 无法更新影子账户")


# ============================================================
# 策略核心 (与回测同口径)
# ============================================================
def inverse_vol_weights(
    panel: pd.DataFrame, codes: list[str], asof: pd.Timestamp, vol_window: int
) -> dict[str, float]:
    """asof 日用严格早于 asof 的收盘算逆波动率权重 (无前视)."""
    hist = panel[panel.index < asof]
    inv: dict[str, float] = {}
    for c in codes:
        px = hist[c].dropna()
        if len(px) < 2:
            inv[c] = 0.0
            continue
        rets = px.pct_change().iloc[-min(vol_window, len(px) - 1):].dropna()
        vol = float(rets.std()) if len(rets) >= 2 else 0.0
        inv[c] = 1.0 / vol if vol > 1e-9 else 0.0
    tot = sum(inv.values())
    if tot <= 0:
        return {c: 1.0 / len(codes) for c in codes}
    return {c: inv[c] / tot for c in codes}


# ============================================================
# 状态管理
# ============================================================
def load_state() -> dict | None:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return None


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def _rehydrate_account(state: dict) -> ShadowAccount:
    """从 state 重建 ShadowAccount (回放 daily_nav 不触发重复 fail-fast 误判 —
    record_daily_nav 只对新增日期检查, 历史 NAV 由 monitor 按序列重算)."""
    acc = ShadowAccount(
        account_id=state["account_id"],
        strategy_id=state["strategy_id"],
        initial_capital=float(state["initial_capital"]),
    )
    for entry in state["daily_nav"]:
        acc.daily_nav.append(entry)
        acc.current_nav = float(entry["nav"])
        acc.current_capital = float(entry["capital"])
    return acc


# ============================================================
# 每日增量更新
# ============================================================
def update(state: dict, config: dict) -> dict:
    """幂等增量: 补录 start_date 之后所有未记录交易日 (自动补漏)."""
    codes = list(config["universe"])
    vol_window = int(config["vol_window"])
    rebal_freq = int(config["rebal_freq"])
    unit_cost = float(config["transaction_cost"]) + float(config["slippage"])

    panel, source = fetch_prices(codes)
    start = pd.Timestamp(state["start_date"])
    all_dates = panel.index[panel.index >= start]
    if len(all_dates) == 0:
        logger.warning("[update] 面板中无 ≥ start_date 的交易日, 不更新")
        return state

    recorded = set(state["recorded_dates"])
    todo = [d for d in all_dates if d.strftime("%Y-%m-%d") not in recorded]
    if not todo:
        logger.info("[update] 无待记录交易日 (最新 %s)", all_dates[-1].date())
        state["last_run"] = now_bj().isoformat()
        state["price_source"] = source
        return state

    acc = _rehydrate_account(state)
    weights = dict(state["weights"])
    nav = float(state["nav"])

    for d in todo:
        # 第 k 个交易日 (start 日 = 第 1 日, 无收益; 之后逐日)
        k = state["trading_day_count"] + 1
        date_str = d.strftime("%Y-%m-%d")
        prev_rows = panel[panel.index < d]
        if k == 1:
            # 首日: 建仓 NAV=1.0 (等权, 与回测 day0 一致)
            weights = {c: 1.0 / len(codes) for c in codes}
            nav_today = nav
            state.setdefault("trade_log", []).append({
                "date": date_str, "action": "init",
                "weights": weights, "note": "等权建仓 (回测 day0 口径)",
            })
        elif prev_rows.empty:
            logger.warning("[update] %s 面板中无更早收盘, 跳过", date_str)
            continue
        else:
            # 再平衡: 回测口径 i % 21 == 0, 影子日 k = 回测日 i+1 → (k-1)%freq==0
            if (k - 1) % rebal_freq == 0:
                new_w = inverse_vol_weights(panel, codes, d, vol_window)
                turnover = 0.5 * sum(abs(new_w[c] - weights.get(c, 0.0)) for c in codes)
                cost = turnover * unit_cost
                state.setdefault("trade_log", []).append({
                    "date": date_str, "action": "rebalance",
                    "old_weights": {c: round(weights.get(c, 0.0), 6) for c in codes},
                    "new_weights": {c: round(new_w[c], 6) for c in codes},
                    "turnover": float(turnover), "cost": float(cost),
                })
                weights = new_w
            else:
                cost = 0.0
            # 当日收益: w·ret (用前一个可得收盘)
            prev_close = prev_rows.iloc[-1]
            today_close = panel.loc[d]
            day_ret = 0.0
            for c in codes:
                pc, tc = float(prev_close[c]), float(today_close[c])
                if pd.notna(pc) and pd.notna(tc) and pc > 0:
                    day_ret += weights.get(c, 0.0) * (tc / pc - 1.0)
            nav_today = nav * (1.0 + day_ret - cost)
            nav = nav_today

        state["trading_day_count"] = k
        state["recorded_dates"].append(date_str)
        state["weights"] = {c: round(weights.get(c, 0.0), 6) for c in codes}
        state["nav"] = float(nav)
        acc.record_daily_nav(date_str, float(nav))
        logger.info("[update] %s (第 %d 交易日): nav=%.6f w=%s",
                    date_str, k, nav, state["weights"])

    # 回写账户序列
    state["daily_nav"] = acc.daily_nav
    state["fail_fast_triggered"] = acc.status.value == "terminated"
    state["price_source"] = source
    state["last_run"] = now_bj().isoformat()
    perf = acc.get_performance()
    logger.info("[update] 完成: NAV=%.6f 回撤=%.2f%% 已跟踪 %d 交易日",
                perf["current_nav"], perf["max_drawdown"] * 100, perf["days_tracked"])
    return state


def init_account(config: dict) -> dict:
    """初始化账户状态 (首日 NAV=1.0)."""
    state = {
        "account_id": config["account_id"],
        "strategy_id": config["strategy_id"],
        "initial_capital": float(config["initial_capital"]),
        "start_date": now_bj().strftime("%Y-%m-%d"),
        "recorded_dates": [],
        "trading_day_count": 0,
        "weights": {c: 0.0 for c in config["universe"]},
        "nav": 1.0,
        "daily_nav": [],
        "trade_log": [],
        "fail_fast_triggered": False,
        "created_at": now_bj().isoformat(),
    }
    return update(state, config)


def print_status(state: dict) -> None:
    acc = _rehydrate_account(state)
    perf = acc.get_performance()
    rets = [e["daily_return"] for e in state["daily_nav"]]
    sharpe = ""
    if len(rets) >= 10:
        import numpy as np
        arr = np.array(rets)
        sd = arr.std()
        if sd > 1e-12:
            sharpe = f" Sharpe(日×√252)={arr.mean() / sd * (252 ** 0.5):.3f}"
    rebal = [t for t in state.get("trade_log", []) if t["action"] == "rebalance"]
    total_cost = sum(t.get("cost", 0.0) for t in rebal)
    print(f"账户: {state['account_id']} ({state['strategy_id']})")
    print(f"起始: {state['start_date']} | 已跟踪 {perf['days_tracked']} 交易日 | "
          f"最新: {state['recorded_dates'][-1] if state['recorded_dates'] else 'N/A'}")
    print(f"NAV: {perf['current_nav']:.6f} | 资金: ¥{perf['current_capital']:,.0f} "
          f"(初始 ¥{perf['initial_capital']:,.0f})")
    print(f"累计收益: {perf['total_return'] * 100:+.3f}% | 最大回撤: "
          f"{perf['max_drawdown'] * 100:.3f}%{sharpe}")
    print(f"当前权重: {state['weights']} | 数据源: {state.get('price_source', 'N/A')}")
    print(f"再平衡: {len(rebal)} 次 | 累计成本: {total_cost * 100:.4f}% | "
          f"fail-fast: {'触发!' if state.get('fail_fast_triggered') else '未触发'}")


def main() -> int:
    parser = argparse.ArgumentParser(description="S12 影子账户每日运行器 (Phase 3)")
    parser.add_argument("--init", action="store_true", help="初始化账户")
    parser.add_argument("--status", action="store_true", help="仅查看状态")
    args = parser.parse_args()

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    if args.init:
        if STATE_PATH.exists():
            logger.error("状态文件已存在 %s — 如需重建请先删除", STATE_PATH)
            return 1
        state = init_account(config)
        save_state(state)
        print_status(state)
        return 0

    state = load_state()
    if state is None:
        logger.error("账户未初始化 — 先运行 --init")
        return 1

    if args.status:
        print_status(state)
        return 0

    state = update(state, config)
    save_state(state)
    print_status(state)
    return 0


if __name__ == "__main__":
    # GBK 控制台无法编码 ¥ (U+00A5) — 输出统一 UTF-8 (同 compute_health_score.py 修法)
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
