#!/usr/bin/env python
"""ETF 回测价格基 与 Wind MCP 逐日一致性校验 (数据源交叉比对)。

背景
----
`scripts/run_200w_etf_backtest.py` 的价格基来自本地 parquet:
  主数据 `data/etf_option_backtest/all_etf_daily.parquet`
  + 按代码命名的单标的日线 / p2_candidates* 候选腿 (只补主数据缺失的 6 位代码)。
本地 parquet 的**原始出处未在仓库留痕**, 一旦某条腿被未复权数据污染 (份额折算 ETF 上
sina 未复权曾把 512100 的 +19.8% 放大成 +221.5%), 回测结论会静默失真。Wind 是口径
事实源里的 P0/P1 权威源, 因此这里做逐日交叉比对:

    对配置里每个持仓代码, 取本地价格序列与 Wind `fund_data.get_fund_kline` 前复权
    日线, 在共同交易日上计算 |Δclose| / close, 要求 **零容差**(默认 1e-6)。

判定 (fail-closed)
------------------
  * 任一代码无本地数据            -> FAIL
  * 任一代码 Wind 取数失败/为空    -> FAIL (缺数据不得判通过)
  * 任一共同交易日的相对偏差超阈值 -> FAIL
  全部通过才 exit 0, 并落盘 JSON 证据 (含逐票明细与复现命令)。

用法
----
  .venv/Scripts/python.exe scripts/verify_etf_price_source.py
  .venv/Scripts/python.exe scripts/verify_etf_price_source.py \\
      --config config/portfolio_200w_etf_v91.yaml --days 1400 --tolerance 1e-6
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import pandas as pd

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from tools.wind_mcp_fetcher import wind_get_kline  # noqa: E402
from utils.datetime_utils import now_bj  # noqa: E402

_DATA_DIR = os.path.join(_PROJECT_ROOT, "data", "etf_option_backtest")
_MAIN_PARQUET = os.path.join(_DATA_DIR, "all_etf_daily.parquet")
_HOLDING_LAYERS = (
    "core_holdings",
    "satellite_holdings",
    "ballast_holdings",
    "cash_holdings",
)
#: 与 run_200w_etf_backtest._load_etf_prices 的候选腿检索顺序保持一致
_EXTRA_SUBDIRS = ("", "p2_candidates_long", "p2_candidates")


def _display_path(path: str) -> str:
    """项目内路径用相对形式 (可跨机器复现), 项目外 (如临时配置) 退化为绝对路径。"""
    try:
        rel = os.path.relpath(path, _PROJECT_ROOT)
    except ValueError:
        return path.replace("\\", "/")
    return rel.replace("\\", "/")


def _windcode_of(code: str) -> str:
    """6 位代码 -> Wind 代码 (5/6 开头为沪市, 其余为深市)。"""
    if "." in code:
        return code
    six = code.split(".")[0]
    return f"{six}.SH" if six[0] in ("5", "6") else f"{six}.SZ"


def _config_codes(cfg: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for layer in _HOLDING_LAYERS:
        for item in cfg.get(layer) or []:
            if isinstance(item, dict) and item.get("code"):
                code = str(item["code"]).split(".")[0]
                if code not in codes:
                    codes.append(code)
    return codes


def _load_main_pivot() -> pd.DataFrame:
    if not os.path.isfile(_MAIN_PARQUET):
        raise FileNotFoundError(f"主数据缺失: {_MAIN_PARQUET}")
    df = pd.read_parquet(_MAIN_PARQUET)
    df["date"] = pd.to_datetime(df["date"])
    df["code"] = df["code"].astype(str).str.split(".").str[0]
    return df.pivot_table(index="date", columns="code", values="close").sort_index()


def _local_series(code: str, pivot: pd.DataFrame) -> tuple[pd.Series | None, str]:
    """返回 (价格序列 index=date, 数据来源标签); 找不到返回 (None, reason)。"""
    if code in pivot.columns:
        s = pivot[code].dropna()
        if not s.empty:
            return s, "all_etf_daily.parquet"
    for sub in _EXTRA_SUBDIRS:
        path = os.path.join(_DATA_DIR, sub, f"{code}.parquet") if sub else os.path.join(_DATA_DIR, f"{code}.parquet")
        if not os.path.isfile(path):
            continue
        extra = pd.read_parquet(path)
        extra["date"] = pd.to_datetime(extra["date"])
        s = extra.groupby("date")["close"].last().dropna()
        if not s.empty:
            label = os.path.relpath(path, _PROJECT_ROOT).replace("\\", "/")
            return s, label
    return None, "no_local_data"


def _wind_series(windcode: str, days: int) -> pd.Series | None:
    recs = wind_get_kline(windcode, days=days, is_fund=True)
    if not recs:
        return None
    df = pd.DataFrame(recs)
    if "TIME" not in df.columns or "MATCH" not in df.columns:
        return None
    idx = pd.to_datetime(df["TIME"], utc=True).dt.tz_convert("Asia/Shanghai").dt.normalize().dt.tz_localize(None)
    vals = pd.to_numeric(df["MATCH"], errors="coerce")
    out = pd.Series(vals.to_numpy(), index=idx).dropna()
    return out.groupby(level=0).last().sort_index() if not out.empty else None


def check(config_path: str, days: int, tolerance: float) -> tuple[bool, dict[str, Any]]:
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f) if config_path.endswith(".json") else _load_yaml(f)
    codes = _config_codes(cfg)
    pivot = _load_main_pivot()

    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    for code in codes:
        windcode = _windcode_of(code)
        local, source = _local_series(code, pivot)
        row: dict[str, Any] = {"code": code, "windcode": windcode, "local_source": source}
        if local is None:
            row["status"] = "FAIL"
            row["reason"] = "本地无该代码价格数据"
            failures.append(f"{code}: 本地无数据")
            rows.append(row)
            continue
        wind = _wind_series(windcode, days)
        if wind is None:
            row["status"] = "FAIL"
            row["reason"] = "Wind 取数失败或返回为空"
            failures.append(f"{code}: Wind 取数为空")
            rows.append(row)
            continue
        joined = pd.concat([local.rename("local"), wind.rename("wind")], axis=1, join="inner").dropna()
        if joined.empty:
            row["status"] = "FAIL"
            row["reason"] = "本地与 Wind 无共同交易日"
            failures.append(f"{code}: 无共同交易日")
            rows.append(row)
            continue
        diff = (joined["wind"] - joined["local"]).abs()
        pct = diff / joined["local"].abs().replace(0, pd.NA)
        worst = pct.idxmax()
        row.update(
            {
                "n_local": int(local.size),
                "n_wind": int(wind.size),
                "n_common": int(joined.shape[0]),
                "window": [joined.index.min().strftime("%Y-%m-%d"), joined.index.max().strftime("%Y-%m-%d")],
                "mean_abs_pct_diff": float(pct.mean()),
                "max_abs_pct_diff": float(pct.max()),
                "worst_date": worst.strftime("%Y-%m-%d"),
                "local_at_worst": float(joined.loc[worst, "local"]),
                "wind_at_worst": float(joined.loc[worst, "wind"]),
            }
        )
        if float(pct.max()) > tolerance:
            row["status"] = "FAIL"
            failures.append(f"{code}: 最大相对偏差 {float(pct.max()):.8f} > {tolerance}")
        else:
            row["status"] = "PASS"
        rows.append(row)

    report = {
        "check": "etf_price_source_vs_wind",
        "generated_at": now_bj().isoformat(),
        "config": _display_path(config_path),
        "tolerance_pct": tolerance,
        "wind_days_requested": days,
        "total_codes": len(codes),
        "passed": sum(1 for r in rows if r.get("status") == "PASS"),
        "failed": sum(1 for r in rows if r.get("status") == "FAIL"),
        "failures": failures,
        "rows": rows,
        "reproduce": (
            ".venv/Scripts/python.exe scripts/verify_etf_price_source.py "
            f"--config {_display_path(config_path)} --days {days}"
        ),
    }
    return (not failures), report


def _load_yaml(handle: Any) -> dict[str, Any]:
    import yaml

    return yaml.safe_load(handle.read())


def main() -> int:
    parser = argparse.ArgumentParser(description="ETF 回测价格基 vs Wind MCP 逐日一致性校验")
    parser.add_argument(
        "--config",
        default=os.path.join(_PROJECT_ROOT, "config", "portfolio_200w_etf_v91.yaml"),
        help="组合配置 (读取 core/satellite/ballast/cash 四层的 code)",
    )
    parser.add_argument("--days", type=int, default=1400, help="Wind K 线回看自然日数")
    parser.add_argument("--tolerance", type=float, default=1e-6, help="允许的最大相对偏差 (默认零容差)")
    parser.add_argument(
        "--out",
        default=os.path.join(_PROJECT_ROOT, "reports", "etf_price_source_check.json"),
        help="证据落盘路径",
    )
    args = parser.parse_args()

    ok, report = check(args.config, args.days, args.tolerance)
    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    for row in report["rows"]:
        line = f"{row['code']}: {row['status']}"
        if row.get("n_common"):
            line += (
                f" n_common={row['n_common']} window={row['window'][0]}..{row['window'][1]}"
                f" mean_pct={row['mean_abs_pct_diff']:.8f} max_pct={row['max_abs_pct_diff']:.8f}"
                f" source={row['local_source']}"
            )
        else:
            line += f" reason={row.get('reason')}"
        print(line)
    print(f"RESULT: {'PASS' if ok else 'FAIL'} ({report['passed']}/{report['total_codes']}) -> {args.out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
