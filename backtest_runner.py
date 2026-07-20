# -*- coding: utf-8 -*-
"""
最小样本外回测 (Walk-Forward)
================================

策略：
- 每月第一个交易日用 runner 生成目标权重
- 持有至下月，计算月度收益
- 输出年化收益、最大回撤、胜率
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

from institutional_pipeline_runner import InstitutionalPipelineRunner, PipelineContext
from utils.data_provider import MarketDataProvider

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("backtest")


# 回测模型验收约束（与 enhanced_backtest 保持一致）
MIN_ANNUAL_RETURN = 0.08      # 年化收益率下限：>= 8%
MAX_DRAWDOWN_LIMIT = 0.15     # 最大回撤上限：<= 15%


def _evaluate_acceptance(annual_return: float, max_drawdown: float) -> Dict:
    """
    回测模型验收：年化收益率 >= MIN_ANNUAL_RETURN 且 最大回撤 <= MAX_DRAWDOWN_LIMIT。
    两项同时成立才达标（passed=True）。
    """
    checks = [
        {
            "metric": "annual_return",
            "value": round(float(annual_return), 4),
            "required": f">= {MIN_ANNUAL_RETURN:.0%}",
            "ok": float(annual_return) >= MIN_ANNUAL_RETURN,
        },
        {
            "metric": "max_drawdown",
            "value": round(float(max_drawdown), 4),
            "required": f"<= {MAX_DRAWDOWN_LIMIT:.0%}",
            "ok": float(max_drawdown) <= MAX_DRAWDOWN_LIMIT,
        },
    ]
    passed = all(c["ok"] for c in checks)
    if passed:
        logger.info(f"回测验收达标：年化 {annual_return:.2%} >= {MIN_ANNUAL_RETURN:.0%}，"
                    f"回撤 {max_drawdown:.2%} <= {MAX_DRAWDOWN_LIMIT:.0%}")
    else:
        logger.warning(f"回测验收未达标：年化 {annual_return:.2%}（需>={MIN_ANNUAL_RETURN:.0%}），"
                       f"回撤 {max_drawdown:.2%}（需<={MAX_DRAWDOWN_LIMIT:.0%}）")
    return {
        "passed": passed,
        "min_annual_return": MIN_ANNUAL_RETURN,
        "max_drawdown_limit": MAX_DRAWDOWN_LIMIT,
        "checks": checks,
    }





def _monthly_dates(start: str, end: str) -> List[pd.Timestamp]:
    rng = pd.date_range(start=start, end=end, freq="BMS")  # 每月第一个交易日
    return [pd.Timestamp(d) for d in rng]


def _next_month_returns(symbol: str, date: pd.Timestamp, provider: MarketDataProvider) -> float:
    try:
        df = provider.get_historical_data(symbol, period="3y")
        if df is None or df.empty or len(df) < 22:
            return 0.0
        df = df.sort_index()
        # 强制索引和比较日期都为 tz-naive，避免混合时区比较报错
        try:
            if hasattr(df.index, "tz") and df.index.tz is not None:
                df.index = df.index.tz_localize(None)
        except Exception:
            df.index = pd.DatetimeIndex([pd.Timestamp(idx).tz_localize(None) if pd.Timestamp(idx).tzinfo else pd.Timestamp(idx) for idx in df.index])
        compare_date = pd.Timestamp(date).normalize()
        try:
            if hasattr(compare_date, "tz") and compare_date.tz is not None:
                compare_date = compare_date.tz_localize(None)
        except Exception:
            compare_date = pd.Timestamp(compare_date).tz_localize(None) if pd.Timestamp(compare_date).tzinfo else pd.Timestamp(compare_date)
        data_start = df.index[0]
        if compare_date < data_start:
            return 0.0
        future = df[df.index > compare_date]
        if len(future) < 22:
            return 0.0
        start_price = float(future.iloc[0]["close"])
        end_price = float(future.iloc[21]["close"])
        if start_price <= 0 or end_price <= 0:
            return 0.0
        return float(end_price / start_price - 1)
    except Exception as e:
        logger.warning("获取%s月度收益失败 %s: %s", symbol, date, e)
        return 0.0


def run_backtest(symbols: List[str], start: str = "2024-07-01", end: str = "2025-12-31") -> Dict:
    provider = MarketDataProvider()
    dates = _monthly_dates(start, end)
    records: List[Dict] = []

    for date in dates:
        ctx = PipelineContext(
            mode="backtest",
            symbols=symbols,
            report_date=date.strftime("%Y-%m-%d"),
        )
        runner = InstitutionalPipelineRunner(ctx)
        result = runner.run()
        # 优先使用优化器真实输出；若为空则安全回退到等权
        weights = result.get("steps", {}).get("portfolio_decision", {}).get("target_weights", {}) or {}
        if not weights:
            weights = {s: 1.0 / len(symbols) for s in symbols}
        status = result.get("status", "ok")

        rets = {}
        for symbol in symbols:
            rets[symbol] = _next_month_returns(symbol, date, provider)

        port_return = float(np.sum([weights.get(s, 0.0) * rets.get(s, 0.0) for s in symbols]))
        records.append({
            "date": date.strftime("%Y-%m-%d"),
            "status": status,
            "weights": weights,
            "returns": rets,
            "portfolio_return": port_return,
        })
        logger.info("回测 %s: return=%.2f%% weights=%s", date.strftime("%Y-%m-%d"), port_return * 100, weights)

    if not records:
        return {"error": "no_backtest_results"}

    returns = pd.Series([r["portfolio_return"] for r in records])
    equity = (1 + returns).cumprod()
    peak = equity.cummax()
    # 回撤取正值幅度（与 enhanced_backtest 一致），避免负值导致"回撤<=15%"判定恒为真
    dd_series = (peak - equity) / peak
    max_dd = float(dd_series.max()) if not dd_series.empty else 0.0
    annual_return = float((1 + returns.mean()) ** 12 - 1) if not returns.empty else 0.0
    win_rate = float((returns > 0).mean()) if not returns.empty else 0.0

    # 回测模型验收：年化收益率 >= 8% 且 最大回撤 <= 15%
    acceptance = _evaluate_acceptance(annual_return, max_dd)

    result = {
        "symbols": symbols,
        "period": f"{start}~{end}",
        "months": len(records),
        "annual_return": annual_return,
        "max_drawdown": max_dd,
        "win_rate": win_rate,
        "acceptance": acceptance,
        "records": records,
    }
    out_path = Path("output") / "backtest_result_latest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    result = run_backtest(["600519", "000858", "601318", "000001", "600036", "601398", "600276", "000063"], start="2024-01-01", end="2025-12-31")
    print(json.dumps(result, ensure_ascii=False, indent=2))
