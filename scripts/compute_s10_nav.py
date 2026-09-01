#!/usr/bin/env python
"""S10 ETF 组合真实净值计算 — 获取 17 只 ETF 收盘价 → 运行 S10 策略 → 输出归一化净值.

用法:
    python scripts/compute_s10_nav.py [--date YYYY-MM-DD] [--output json|text]

输出:
    JSON: {"date": "2026-08-31", "nav": 1.0xxx, "source": "sina", ...}
    text: "1.0xxx"

设计:
    1. 加载 p2_universe_2015_2026.parquet 历史数据 (17 标的, ~2830 天)
    2. 若历史数据末尾 < 目标日期, 用 sina K线API 补充缺失交易日
    3. 运行 run_s10_p2(prices, tw) 得到完整净值序列
    4. 读取影子账户状态获取起始日期, 归一化: nav = eq[today] / eq[start]
    5. 输出供 launch_etf_shadow.py --daily --nav 使用

无前视偏差: 策略信号 (趋势过滤/波动率目标/熔断) 全部基于 t-1 及以前数据.
数据源: sina K线 API (不复权, 短期与 parquet qfq 一致; 长期需复权校验).
"""
import os

os.environ["NO_PROXY"] = "*"

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = PROJECT_ROOT / "data" / "etf_option_backtest" / "p2_universe_2015_2026.parquet"
BACKTEST_MODULE = PROJECT_ROOT / "data" / "etf_option_backtest" / "run_etf_option_backtest.py"
DEFAULT_STATE_FILE = PROJECT_ROOT / "output" / "shadow_etf" / "shadow_state_shadow_etf_s10_candidate.json"

SINA_KLINE_URL = (
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php"
    "/CN_MarketData.getKLineData"
)
SINA_HEADERS = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}

ETF_CODES = [
    "159915", "159939", "510050", "510300", "510310", "510500", "511260",
    "512010", "512100", "512480", "512660", "512890", "513100", "513500",
    "515170", "518880", "588000",
]

SH_CODES = {"510050", "510300", "510310", "510500", "511260", "512010", "512100",
            "512480", "512660", "512890", "513100", "513500", "515170", "518880", "588000"}


def load_backtest_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("etf_ref", str(BACKTEST_MODULE))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["etf_ref"] = mod
    spec.loader.exec_module(mod)
    return mod


def sina_symbol(code: str) -> str:
    return f"sh{code}" if code in SH_CODES else f"sz{code}"


def fetch_recent_closes(start_date: str, end_date: str) -> pd.DataFrame:
    """用 sina K线API 获取 17 只 ETF 日收盘价 (不复权).

    返回 DataFrame: index=date, columns=ETF代码(6位), values=close
    """
    start_dt = pd.to_datetime(start_date)
    end_dt = pd.to_datetime(end_date)
    calendar_days = (end_dt - start_dt).days + 1
    datalen = max(int(calendar_days * 0.8) + 5, 20)

    frames = []
    n_ok = 0
    n_fail = 0
    for code in ETF_CODES:
        sym = sina_symbol(code)
        try:
            r = requests.get(
                SINA_KLINE_URL,
                params={"symbol": sym, "scale": 240, "ma": "no", "datalen": datalen},
                headers=SINA_HEADERS,
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            if not data:
                n_fail += 1
                continue
            rows = []
            for item in data:
                d = pd.to_datetime(item["day"])
                if start_dt <= d <= end_dt:
                    rows.append({"date": d, code: float(item["close"])})
            if rows:
                df = pd.DataFrame(rows).set_index("date")
                frames.append(df)
                n_ok += 1
            else:
                n_fail += 1
        except (requests.RequestException, ValueError, KeyError) as e:
            print(f"  获取 {code} ({sym}) 失败: {e}", file=sys.stderr)
            n_fail += 1

    print(f"  sina 获取: {n_ok} 成功, {n_fail} 失败", file=sys.stderr)
    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames, axis=1)
    return result.sort_index()


def get_shadow_start_date(state_file: Path) -> str | None:
    """读取影子账户状态, 返回第一条 daily_nav 记录的日期 (起始日期)."""
    if not state_file.exists():
        return None
    state = json.loads(state_file.read_text(encoding="utf-8"))
    daily_nav = state.get("daily_nav", [])
    if not daily_nav:
        return None
    return daily_nav[0]["date"]


def main():
    parser = argparse.ArgumentParser(description="S10 ETF 组合真实净值计算")
    parser.add_argument("--date", default=None, help="目标日期 YYYY-MM-DD (默认今天)")
    parser.add_argument("--output", default="json", choices=["json", "text"], help="输出格式")
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE), help="影子账户状态文件路径")
    args = parser.parse_args()

    target_date = args.date or datetime.now().strftime("%Y-%m-%d")  # noqa: DTZ005
    target_dt = pd.to_datetime(target_date)
    state_file = Path(args.state_file)

    mod = load_backtest_module()

    prices = mod.load_etf_prices(str(DATA_FILE))
    last_date = prices.index[-1]
    print(
        f"历史数据: {prices.index[0].date()} ~ {last_date.date()}, "
        f"{len(prices)} 天, {len(prices.columns)} 标的",
        file=sys.stderr,
    )

    cfg = mod.load_config()
    tw = mod.extract_target_weights(cfg)

    if last_date < target_dt:
        start = (last_date + timedelta(days=1)).strftime("%Y%m%d")
        end = target_dt.strftime("%Y%m%d")
        print(f"补充数据: {start} ~ {end}", file=sys.stderr)

        new_data = fetch_recent_closes(start, end)
        if new_data.empty:
            print("ERROR: sina 获取失败, 无法计算真实净值", file=sys.stderr)
            sys.exit(1)

        common_cols = [c for c in new_data.columns if c in prices.columns]
        missing = [c for c in ETF_CODES if c not in new_data.columns]
        if missing:
            print(f"WARNING: 缺失标的: {missing}", file=sys.stderr)
        if not common_cols:
            print("ERROR: 无匹配标的", file=sys.stderr)
            sys.exit(1)

        new_data = new_data[common_cols]
        prices = pd.concat([prices, new_data]).sort_index()
        prices = prices[~prices.index.duplicated(keep="last")]
        print(
            f"补充后: {prices.index[0].date()} ~ {prices.index[-1].date()}, "
            f"{len(prices)} 天",
            file=sys.stderr,
        )

    eq, _ = mod.run_s10_p2(prices, tw)

    if prices.index[-1] >= target_dt:
        mask = prices.index >= target_dt
        idx = mask.argmax()
        actual_date = str(prices.index[idx].date())
    else:
        idx = len(prices) - 1
        actual_date = str(prices.index[-1].date())
        print(
            f"WARNING: 目标日期 {target_date} 无数据, 用最后可用日期 {actual_date}",
            file=sys.stderr,
        )

    start_date = get_shadow_start_date(state_file)
    if start_date:
        start_dt = pd.to_datetime(start_date)
        start_mask = prices.index >= start_dt
        if start_mask.any():
            start_idx = start_mask.argmax()
            target_nav = eq[idx] / eq[start_idx]
            print(
                f"归一化: 起始 {start_date} (eq={eq[start_idx]:.0f}) → "
                f"当日 {actual_date} (eq={eq[idx]:.0f}) → nav={target_nav:.6f}",
                file=sys.stderr,
            )
        else:
            print(
                f"ERROR: 影子账户起始日期 {start_date} 晚于最后数据 {actual_date}, "
                f"无法归一化 (可能当日未收盘)",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        target_nav = 1.0
        print("影子账户无历史记录, nav=1.0 (首日)", file=sys.stderr)

    result = {
        "date": target_date,
        "actual_date": actual_date,
        "nav": round(target_nav, 6),
        "source": "sina",
        "n_days_total": len(prices),
        "last_data_date": str(prices.index[-1].date()),
        "shadow_start_date": start_date,
    }

    if args.output == "json":
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"{target_nav:.6f}")


if __name__ == "__main__":
    main()
