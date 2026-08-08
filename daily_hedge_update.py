"""
daily_hedge_update.py
功能：
1. 使用 Wind MCP 更新历史收益率数据
2. 运行对冲决策
3. 生成对冲报告
"""

import json
import logging
import os
from datetime import datetime

import pandas as pd

from utils.path_config import get_config_dir, get_report_dir, setup_sys_path

logger = logging.getLogger(__name__)

# 统一路径初始化: 替代所有硬编码 sys.path.insert / 绝对路径 (v8.5+)
setup_sys_path()

# 数据目录 (通过 path_config 统一派生)
DATA_DIR = get_config_dir()
REPORT_DIR = get_report_dir()

from hedging.hedge_coordinator import HedgeCoordinator
from wind_mcp_fetcher import wind_get_kline, wind_get_quote

# B2.4: 通用并发 IO 批量执行 (替代串行 for 循环拉取 Wind MCP 行情)
from utils.concurrency import run_io_batch


def _to_wind_code(symbol: str):
    s = str(symbol).strip()
    for prefix in ("sh", "sz", "bj", "SH", "SZ", "BJ"):
        if s.startswith(prefix):
            s = s[len(prefix) :]
            break
    for suffix in (".SH", ".SZ", ".BJ", ".sh", ".sz", ".bj"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    if s.startswith(("51", "58")):
        return f"{s}.SH", True
    if s.startswith(("15", "16")):
        return f"{s}.SZ", True
    if s.startswith(("00", "30")):
        return f"{s}.SZ", False
    if s.startswith("6"):
        return f"{s}.SH", False
    if s.startswith(("4", "8")):
        return f"{s}.BJ", False
    return f"{s}.SH", False


def _get_historical_kline(symbol: str, days: int = 252):
    wind_code, is_fund = _to_wind_code(symbol)
    items = wind_get_kline(wind_code, days=days, is_fund=is_fund)
    if not items:
        return None
    records = []
    for k in items:
        close = k.get("close") or k.get("CLOSE") or k.get("MATCH") or k.get("match")
        if close is None:
            continue
        try:
            close = float(close)
        except (TypeError, ValueError):
            continue
        if close <= 0:
            continue
        date = k.get("date") or k.get("DATE") or k.get("trade_date") or k.get("time") or k.get("TIME")
        records.append({"date": date, "close": close})
    if not records:
        return None
    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    df.sort_index(inplace=True)
    return df


def update_returns():
    """更新历史收益率数据 - Wind MCP 直连

    B2.4: 用 run_io_batch 并发拉取多 symbol 的 252 日 K 线 (替代串行 for 循环).
    Wind MCP 单次请求约 200-500ms, 串行 N 个标的需 N×500ms,
    并发 8 workers 后约 (N/8)×500ms.
    """
    print("=" * 60)
    print("1. 更新历史收益率数据")
    print("=" * 60)

    positions_path = DATA_DIR / "positions.json"
    with open(positions_path, encoding="utf-8") as f:
        positions_data = json.load(f)["positions"]

    symbols = [item.get("code") for item in positions_data.values() if item.get("code")]
    returns_data = {}
    success_count = 0
    fail_count = 0

    # B2.4: 并发拉取多 symbol K 线 (替代串行 for 循环)
    def _fetch_kline(symbol):
        """单 symbol 拉取 + 计算 returns; 失败返回 None。"""
        try:
            df = _get_historical_kline(symbol, days=252)
            if df is not None and not df.empty:
                df["return"] = df["close"].pct_change()
                return (symbol, df["return"].dropna())
        except Exception as e:
            logger.warning("拉取K线失败 %s: %s", symbol, e)
        return (symbol, None)

    pairs = run_io_batch(
        symbols,
        _fetch_kline,
        max_workers=8,
        timeout=60,
        desc="update_returns",
    )

    for symbol, ret_series in pairs:
        if symbol is None or ret_series is None:
            fail_count += 1
            continue
        returns_data[symbol] = ret_series
        success_count += 1

    print(f"更新完成: 成功 {success_count}, 失败 {fail_count}")

    if returns_data:
        returns_df = pd.DataFrame(returns_data)
        returns_path = DATA_DIR / "returns_history.json"
        returns_df.to_json(returns_path, orient="split", date_format="iso")

        market_returns = None  # H1修复: 显式初始化, 替代脆弱的 dir() 检测
        market_symbol = "510300"
        market_df = _get_historical_kline(market_symbol, days=252)
        if market_df is not None and not market_df.empty:
            market_returns = market_df["close"].pct_change().dropna()
            market_path = DATA_DIR / "market_returns.json"
            market_returns.to_json(market_path, orient="split", date_format="iso")

        return returns_df, market_returns
    else:
        return None, None


def run_hedge_decision():
    """运行对冲决策 - Wind MCP 直连

    B2.4: 用 run_io_batch 并发拉取多 position 的实时报价 (替代串行 for 循环).
    wind_get_quote 单次请求约 100-300ms, 串行 N 个标的需 N×300ms,
    并发 8 workers 后约 (N/8)×300ms.
    """
    print()
    print("=" * 60)
    print("2. 运行对冲决策")
    print("=" * 60)

    positions_path = DATA_DIR / "positions.json"
    with open(positions_path, encoding="utf-8") as f:
        positions_data = json.load(f)["positions"]

    # 先解析有效持仓项 (qty>0 且 code 非空)
    valid_items = []
    for _key, item in positions_data.items():
        code = item.get("code")
        qty = item.get("phase1_shares") or item.get("total_shares") or item.get("shares", 0)
        if not code or not qty:
            continue
        valid_items.append((code, float(qty), item))

    # B2.4: 并发拉取多 position 的实时报价
    def _fetch_quote(item_tuple):
        """单 position 拉取报价; 失败回退 est_price; 返回 (code, qty, price)。"""
        code, qty, item = item_tuple
        try:
            wind_code, is_fund = _to_wind_code(code)
            quote = wind_get_quote(wind_code, is_fund=is_fund)
            if quote and quote.get("price") is not None:
                try:
                    price = float(quote["price"])
                    if price > 0:
                        return (code, qty, price)
                except (TypeError, ValueError):
                    pass
        except Exception as e:
            logger.warning("获取报价失败 %s: %s", code, e)
        # 回退到 est_price
        fallback_price = float(item.get("est_price", 0.0) or 0.0)
        return (code, qty, fallback_price)

    triples = run_io_batch(
        valid_items,
        _fetch_quote,
        max_workers=8,
        timeout=30,
        desc="hedge_quotes",
    )

    positions = {}
    prices = {}
    for code, qty, price in triples:
        if code is None:
            continue
        positions[code] = float(qty)
        prices[code] = price

    # 加载历史数据
    returns_path = DATA_DIR / "returns_history.json"
    market_path = DATA_DIR / "market_returns.json"

    returns = pd.DataFrame()
    market_returns = pd.Series(dtype=float)

    if os.path.exists(returns_path) and os.path.exists(market_path):
        try:
            returns = pd.read_json(returns_path, orient="split")
            market_returns = pd.read_json(market_path, orient="split", typ="series")
            returns.columns = returns.columns.astype(str)
        except Exception as e:
            logger.warning("加载历史收益率数据失败: %s, 使用空数据集继续", e)
            returns = pd.DataFrame()
            market_returns = pd.Series(dtype=float)

    # 运行对冲引擎
    coordinator = HedgeCoordinator(enable_tail_risk=True)
    plan = coordinator.coordinate(
        positions=positions,
        prices=prices,
        returns=returns,
        market_returns=market_returns,
        vix=25.0,
        portfolio_value=5_000_000.0,
        hwm_drawdown=0.03,
        bs_loss=0.0,
    )

    print(f"动作: {plan.get('action')}")
    print(f"组合Beta: {plan.get('portfolio_beta'):.4f}")
    print(f"总对冲比例: {float(plan.get('total_hedge_pct', 0.0) or 0.0) * 100:.2f}%")
    print(f"总成本比例: {float(plan.get('total_cost_pct', 0.0) or 0.0) * 100:.4f}%")
    print(f"市场状态: {plan.get('regime')}")

    return plan


def generate_report(plan):
    """生成对冲报告"""
    print()
    print("=" * 60)
    print("3. 生成对冲报告")
    print("=" * 60)

    report_dir = str(REPORT_DIR)
    os.makedirs(report_dir, exist_ok=True)

    report = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "time": datetime.now().strftime("%H:%M:%S"),
        "action": plan.get("action"),
        "portfolio_beta": float(plan.get("portfolio_beta", 0.0) or 0.0),
        "total_hedge_pct": float(plan.get("total_hedge_pct", 0.0) or 0.0),
        "total_cost_pct": float(plan.get("total_cost_pct", 0.0) or 0.0),
        "regime": str(plan.get("regime")),
        "orders": plan.get("orders", []),
        "summary": plan.get("summary", {}),
    }

    report_path = os.path.join(report_dir, f"hedge_decision_{datetime.now().strftime('%Y%m%d')}.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"报告已保存: {report_path}")

    # 生成可读报告
    readme_path = os.path.join(report_dir, f"hedge_decision_{datetime.now().strftime('%Y%m%d')}.md")
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(f"# 对冲决策报告 - {report['date']}\n\n")
        f.write("## 决策结果\n\n")
        f.write(f"- **动作**: {report['action']}\n")
        f.write(f"- **组合Beta**: {report['portfolio_beta']:.4f}\n")
        f.write(f"- **总对冲比例**: {report['total_hedge_pct'] * 100:.2f}%\n")
        f.write(f"- **总成本比例**: {report['total_cost_pct'] * 100:.4f}%\n")
        f.write(f"- **市场状态**: {report['regime']}\n\n")

        if report["orders"]:
            f.write("## 对冲指令\n\n")
            for i, order in enumerate(report["orders"], 1):
                f.write(f"### {i}. {order.get('hedge_type', 'UNKNOWN')}\n\n")
                f.write(f"- 动作: {order.get('action')}\n")
                f.write(f"- 标的: {order.get('instrument', 'N/A')}\n")
                f.write(f"- 手数: {order.get('contracts', 'N/A')}\n")
                f.write(f"- 名义价值: {order.get('notional', 0):,.0f}\n")
                f.write(f"- 预估成本: {order.get('estimated_cost', order.get('budget', 0)):,.0f}\n\n")
        else:
            f.write("## 结论\n\n")
            f.write("当前无需开启额外对冲。\n")

    print(f"可读报告: {readme_path}")


if __name__ == "__main__":
    print("每日对冲自动更新 - Wind MCP")
    print("=" * 60)
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # 1. 更新收益率数据
    returns_df, market_returns = update_returns()

    # 2. 运行对冲决策
    plan = run_hedge_decision()

    # 3. 生成报告
    generate_report(plan)

    print()
    print("=" * 60)
    print("更新完成")
    print("=" * 60)
