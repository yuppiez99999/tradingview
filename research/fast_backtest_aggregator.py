"""
快速回测聚合：基于已有 pipeline_backtest.json 计算收益
"""

import json
import sys
from pathlib import Path

import pandas as pd

from utils.data_provider import MarketDataProvider

# B1.3: 从 config/risk_params.yaml 统一读取回撤上限
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.risk_params import (
    get_max_drawdown_limit as _get_max_drawdown_limit,
)  # noqa: E402

_MAX_DRAWDOWN_LIMIT = _get_max_drawdown_limit()

BASE = Path("E:/各种PY程序/28-终极量化交易系统8.4/output/institutional_pipeline")
SYMBOLS = ["600519", "000858", "601318"]


def _load_pipeline_results() -> list:
    records = []
    for folder in sorted(BASE.iterdir()):
        if not folder.is_dir():
            continue
        path = folder / "pipeline_backtest.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            continue
        date_str = data.get("report_date") or folder.name
        try:
            date = pd.Timestamp(date_str)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            continue
        weights = (
            data.get("steps", {})
            .get("portfolio_decision", {})
            .get("target_weights", {})
        )
        if not weights:
            continue
        records.append({"date": date, "weights": weights})
    return records


def _monthly_returns(
    symbol: str, start: pd.Timestamp, end: pd.Timestamp, provider: MarketDataProvider
) -> float:
    # 阶段 1: free-stockdb 本地优先 (研究/回测专用), 自动回退
    df = None
    try:
        from utils.free_stockdb_adapter import get_historical_data_fs

        df = get_historical_data_fs(symbol, period="5y", use_fallback=False)
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ):
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        df = None
    if df is None or df.empty:
        df = provider.get_historical_data(symbol, period="5y")
    if df is None or df.empty or len(df) < 22:
        return 0.0
    df = df.sort_index()
    try:
        if hasattr(df.index, "tz") and df.index.tz is not None:
            df.index = df.index.tz_localize(None)
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ):
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        pass
    compare_start = pd.Timestamp(start).normalize()
    compare_end = pd.Timestamp(end).normalize()
    future = df[(df.index > compare_start) & (df.index <= compare_end)]
    if len(future) < 2:
        return 0.0
    start_price = float(future.iloc[0]["close"])
    end_price = float(future.iloc[-1]["close"])
    if start_price <= 0 or end_price <= 0:
        return 0.0
    return float(end_price / start_price - 1)


def run_fast_backtest(start: str = "2024-01-01", end: str = "2024-12-31") -> dict:
    records = _load_pipeline_results()
    if not records:
        return {"error": "no_pipeline_results"}

    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    records = [r for r in records if start_ts <= r["date"] <= end_ts]
    if not records:
        return {"error": "no_pipeline_results_in_range"}

    provider = MarketDataProvider()
    results = []
    for i, rec in enumerate(records):
        date = rec["date"]
        weights = rec["weights"]
        next_date = (
            records[i + 1]["date"]
            if i + 1 < len(records)
            else date + pd.DateOffset(months=1)
        )
        rets = {}
        for symbol in SYMBOLS:
            rets[symbol] = _monthly_returns(symbol, date, next_date, provider)
        port_return = float(
            sum(weights.get(s, 0.0) * rets.get(s, 0.0) for s in SYMBOLS)
        )
        results.append(
            {
                "date": date.strftime("%Y-%m-%d"),
                "next_date": next_date.strftime("%Y-%m-%d"),
                "weights": weights,
                "returns": rets,
                "portfolio_return": port_return,
            }
        )
        print(
            f"回测 {date.strftime('%Y-%m-%d')} ~ {next_date.strftime('%Y-%m-%d')}: return={port_return*100:.2f}% weights={weights}"
        )

    if not results:
        return {"error": "no_backtest_results"}

    returns = pd.Series([r["portfolio_return"] for r in results])
    equity = (1 + returns).cumprod()
    peak = equity.cummax()
    # 回撤取正值幅度（与 enhanced_backtest 一致），避免负值导致"回撤<=15%"判定恒为真
    dd_series = (peak - equity) / peak
    max_dd = float(dd_series.max()) if not dd_series.empty else 0.0
    annual_return = float((1 + returns.mean()) ** 12 - 1) if not returns.empty else 0.0
    win_rate = float((returns > 0).mean()) if not returns.empty else 0.0

    # 回测模型验收：年化收益率 >= 8% 且 最大回撤 <= _MAX_DRAWDOWN_LIMIT (B1.3: 配置化)
    checks = [
        {
            "metric": "annual_return",
            "value": round(annual_return, 4),
            "required": ">= 8%",
            "ok": annual_return >= 0.08,
        },
        {
            "metric": "max_drawdown",
            "value": round(max_dd, 4),
            "required": f"<= {_MAX_DRAWDOWN_LIMIT:.0%}",
            "ok": max_dd <= _MAX_DRAWDOWN_LIMIT,
        },
    ]
    passed = all(c["ok"] for c in checks)
    acceptance = {
        "passed": passed,
        "min_annual_return": 0.08,
        "max_drawdown_limit": _MAX_DRAWDOWN_LIMIT,
        "checks": checks,
    }

    result = {
        "symbols": SYMBOLS,
        "period": f"{start}~{end}",
        "months": len(results),
        "annual_return": annual_return,
        "max_drawdown": max_dd,
        "win_rate": win_rate,
        "acceptance": acceptance,
        "records": results,
    }
    out_path = Path("output") / "fast_backtest_result.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


if __name__ == "__main__":
    res = run_fast_backtest()
    print(json.dumps(res, ensure_ascii=False, indent=2))
