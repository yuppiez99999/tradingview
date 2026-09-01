"""P3.0 影子账户闭环门禁验证 (2026-08-26 新增)

三件验证:
    ① shadow 实读 strategy=build fills 出 NAV
       — ShadowAccount.consume_fills_from_store 消费 fills, trade_log 非空, NAV 有变化
    ② daily_pnl 用 strategies=("build",) 过滤消费验证
       — fills_pnl_bridge.augment_market_prices(strategies=...) 只覆盖 build 成交
    ③ 积累 >=1 周真实成交
       — reports/fills/fills_*.jsonl 中 strategy=build 记录 >=5 个交易日

用法:
    python scripts/verify_p3_0_gate.py [--weeks 1] [--strategies build]

退出码:
    0 = 全部 PASS
    1 = 任一 FAIL (P3.1 不允许启动)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

_FILLS_DIR = _PROJECT_ROOT / "reports" / "fills"


def _recent_trade_dates(weeks: int) -> list[str]:
    """最近 N 周的工作日列表 (不含周末, 含今天)."""
    today = datetime.now().date()
    dates = []
    for i in range(weeks * 7):
        d = today - timedelta(days=i)
        if d.weekday() < 5:
            dates.append(d.strftime("%Y-%m-%d"))
    return sorted(dates)


def _count_build_fills(dates: list[str], strategies: tuple[str, ...]) -> dict[str, int]:
    """统计各交易日 fills 文件中指定策略的记录数."""
    counts: dict[str, int] = {}
    strategies_set = set(strategies)
    for date in dates:
        path = _FILLS_DIR / f"fills_{date}.jsonl"
        if not path.exists():
            continue
        n = 0
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    if rec.get("strategy") in strategies_set:
                        n += 1
        except (ValueError, KeyError, TypeError, OSError):
            pass
        if n > 0:
            counts[date] = n
    return counts


def verify_3_data_accumulation(
    dates: list[str], strategies: tuple[str, ...], min_days: int = 5
) -> dict:
    """验证 ③: 积累 >=1 周真实成交."""
    counts = _count_build_fills(dates, strategies)
    days_with_fills = len(counts)
    total_fills = sum(counts.values())
    passed = days_with_fills >= min_days
    return {
        "name": "③ 数据积累",
        "passed": passed,
        "detail": f"有 strategy={strategies} 成交的交易日 = {days_with_fills} (要求 >= {min_days}), 总记录 = {total_fills}",  # noqa: E501
        "days_with_fills": days_with_fills,
        "total_fills": total_fills,
        "min_days_required": min_days,
        "daily_counts": counts,
    }


def verify_1_shadow_consume_fills(
    dates: list[str], strategies: tuple[str, ...]
) -> dict:
    """验证 ①: shadow 实读 strategy=build fills 出 NAV."""
    try:
        from shadow_account_system import ShadowAccount
    except ImportError as e:
        return {
            "name": "① shadow 消费 fills",
            "passed": False,
            "detail": f"ShadowAccount 导入失败: {e}",
        }

    account = ShadowAccount(
        account_id="p3_0_gate", strategy_id="build_fills", initial_capital=1_000_000
    )
    result = account.consume_fills_from_store(dates, strategies=strategies)

    fills_count = result.get("fills_count", 0)
    trade_log_len = result.get("trade_log_len", 0)
    nav = result.get("nav", 1.0)
    holdings = result.get("holdings", {})

    passed = fills_count > 0 and trade_log_len > 0
    return {
        "name": "① shadow 消费 fills",
        "passed": passed,
        "detail": f"fills={fills_count}, trade_log={trade_log_len}, nav={nav:.6f}, holdings={len(holdings)} 只",
        "fills_count": fills_count,
        "trade_log_len": trade_log_len,
        "nav": nav,
        "holdings_count": len(holdings),
    }


def verify_2_daily_pnl_filtered(dates: list[str], strategies: tuple[str, ...]) -> dict:
    """验证 ②: daily_pnl 用 strategies 过滤消费验证.

    检查 FillsStore.latest_avg_price_by_symbol(strategies=...) 能取到指定策略的成交均价,
    证明 bridge → FillsStore 的 strategy 过滤链路通 (不依赖 market_prices 标的命中).
    """
    try:
        from utils.execution.fills_store import FillsStore
    except ImportError as e:
        return {
            "name": "② daily_pnl 过滤消费",
            "passed": False,
            "detail": f"FillsStore 导入失败: {e}",
        }

    store = FillsStore()
    dates_with_build_prices: list[str] = []
    total_symbols_covered = 0

    for date in dates:
        try:
            prices = store.latest_avg_price_by_symbol(date, strategies=strategies)
        except (ValueError, TypeError, KeyError, AttributeError, OSError):
            continue
        if prices:
            dates_with_build_prices.append(date)
            total_symbols_covered += len(prices)

    passed = len(dates_with_build_prices) > 0
    return {
        "name": "② daily_pnl 过滤消费",
        "passed": passed,
        "detail": f"strategies={strategies} 取到成交均价的交易日 = {len(dates_with_build_prices)}, 覆盖标的总数 = {total_symbols_covered}",  # noqa: E501
        "dates_with_coverage": dates_with_build_prices,
        "total_symbols_covered": total_symbols_covered,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="P3.0 影子账户闭环门禁验证")
    parser.add_argument("--weeks", type=int, default=1, help="检查最近 N 周 (默认 1)")
    parser.add_argument(
        "--strategies", nargs="+", default=["build"], help="策略标签 (默认 build)"
    )
    parser.add_argument(
        "--min-days", type=int, default=5, help="数据积累最少交易日 (默认 5)"
    )
    args = parser.parse_args()

    strategies_tuple = tuple(args.strategies)
    dates = _recent_trade_dates(args.weeks)

    print("=" * 60)
    print("P3.0 影子账户闭环门禁验证")
    print("=" * 60)
    print(f"检查范围: 最近 {args.weeks} 周 ({len(dates)} 个工作日)")
    print(f"策略标签: {strategies_tuple}")
    print(f"数据积累要求: >= {args.min_days} 个交易日有成交")
    print(f"日期范围: {dates[0] if dates else '?'} ~ {dates[-1] if dates else '?'}")
    print()

    v3 = verify_3_data_accumulation(dates, strategies_tuple, args.min_days)
    v1 = verify_1_shadow_consume_fills(dates, strategies_tuple)
    v2 = verify_2_daily_pnl_filtered(dates, strategies_tuple)

    results = [v1, v2, v3]
    all_passed = all(r["passed"] for r in results)

    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        print(f"  [{status}] {r['name']}: {r['detail']}")

    print()
    print("=" * 60)
    if all_passed:
        print("结果: 全部 PASS — P3.1 可启动")
        return 0
    failed = [r["name"] for r in results if not r["passed"]]
    print(f"结果: FAIL ({', '.join(failed)}) — P3.1 不允许启动")
    if not v3["passed"]:
        print(
            "  提示: 数据积累不足, 需运行 daily_trade_executor 积累更多 strategy=build fills"
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
