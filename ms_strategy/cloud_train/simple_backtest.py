"""
简化回测脚本
用模型预测信号 + qlib 股票价格，手动计算回测指标
不依赖 qlib.backtest 模块，避免分钟数据依赖
"""
import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from utils.datetime_utils import now_bj


def load_model_safe(path, expected_sha256=None):
    """安全加载 pickle 模型: 委托全项目唯一反序列化收口 (CWE-502).

    - 提供 expected_sha256 或存在同路径 .sha256 侧车文件时, 严格校验, 失败拒绝加载.
    - 无侧车时按 `QUANT_REQUIRE_PICKLE_INTEGRITY` 策略: 置 1 即拒绝 (fail-closed),
      未置则仅记录哈希作审计线索 (行为与接入前一致)。

    2026-09-12 (Issue #30 二次复扫): 原实现在本目录 **三份文件里各写一遍**
    (校验逻辑重复 = 一处修另两处漏), 现统一委托 `utils.safe_pickle.load_pickle`。
    """
    from utils.safe_pickle import PickleIntegrityError, load_pickle

    try:
        return load_pickle(path, expected_sha256=expected_sha256)
    except PickleIntegrityError as exc:
        # 保持既有契约: 原实现在校验失败时抛 RuntimeError
        raise RuntimeError(str(exc)) from exc


def parse_args():
    p = argparse.ArgumentParser(description="简化回测")
    p.add_argument("--data-dir", default="./qlib_data/cn_data")
    p.add_argument("--market", default="csi300")
    p.add_argument("--start", default="2025-06-30")
    p.add_argument("--end", default="2026-07-08")
    p.add_argument("--model-path", default=None)
    p.add_argument("--topk", type=int, default=10)
    p.add_argument("--n-drop", type=int, default=2)
    p.add_argument("--benchmark", default="SH000300")
    return p.parse_args()


def main():
    args = parse_args()
    os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"

    import qlib
    from qlib.config import C
    C["exp_manager"]["kwargs"]["uri"] = "file:/tmp/mlruns"
    qlib.init(provider_uri=args.data_dir, region="cn")
    print(f"[QLib] {qlib.__version__} 初始化完成")

    from qlib.contrib.data.handler import Alpha158
    from qlib.utils import init_instance_by_config

    # 加载模型
    if args.model_path and os.path.exists(args.model_path):
        model = load_model_safe(args.model_path)
        print(f"已加载模型: {args.model_path}")
    else:
        print("未指定模型")
        sys.exit(1)

    # 准备数据并预测
    handler_config = {
        "start_time": args.start, "end_time": args.end,
        "fit_start_time": args.start, "fit_end_time": args.end,
        "instruments": args.market,
        "infer_processors": [
            {"class": "RobustZScoreNorm", "kwargs": {"fields_group": "feature", "clip_outlier": True}},
            {"class": "Fillna", "kwargs": {"fields_group": "feature"}},
        ],
        "learn_processors": [{"class": "DropnaLabel"}, {"class": "CSRankNorm", "kwargs": {"fields_group": "label"}}],
        "label": ["Ref($close, -2) / Ref($close, -1) - 1"],
    }
    handler = Alpha158(**handler_config)
    dataset = init_instance_by_config({
        "class": "DatasetH", "module_path": "qlib.data.dataset",
        "kwargs": {"handler": handler, "segments": {"test": (args.start, args.end)}},
    })

    print("\n[1/4] 生成预测信号...")
    pred = model.predict(dataset, segment="test")
    if isinstance(pred, pd.DataFrame):
        pred = pred.iloc[:, 0]
    print(f"  预测: {len(pred)} 行")

    # 获取所有股票的收盘价
    print("\n[2/4] 获取股票价格...")
    from qlib.data import D
    instruments = D.instruments(market=args.market)
    fields = ["$close", "$open"]
    start_date = pred.index.get_level_values(0).min()
    end_date = pred.index.get_level_values(0).max()
    prices = D.features(instruments, fields, start_time=start_date, end_time=end_date)
    prices.columns = ["close", "open"]
    # qlib 返回 (instrument, datetime)，改为 (datetime, instrument) 与 pred 对齐
    prices = prices.swaplevel(0, 1).sort_index()
    # 统一股票代码为大写
    new_idx = pd.MultiIndex.from_tuples(
        [(dt, str(stock).upper()) for dt, stock in prices.index],
        names=["datetime", "instrument"],
    )
    prices.index = new_idx
    print(f"  价格数据: {len(prices)} 行")

    # 获取基准价格
    bench_prices = D.features([args.benchmark], ["$close"], start_time=start_date, end_time=end_date)
    bench_prices.columns = ["bench_close"]
    bench_prices = bench_prices.swaplevel(0, 1).sort_index()
    new_bench_idx = pd.MultiIndex.from_tuples(
        [(dt, str(stock).upper()) for dt, stock in bench_prices.index],
        names=["datetime", "instrument"],
    )
    bench_prices.index = new_bench_idx
    print(f"  基准数据: {len(bench_prices)} 行")

    # 回测：每日选 Topk 股票等权持仓
    print("\n[3/4] 运行回测...")
    dates = sorted(pred.index.get_level_values(0).unique())
    daily_returns = []
    bench_returns = []
    portfolio_values = [1.0]
    bench_values = [1.0]

    for i in range(1, len(dates)):
        prev_date = dates[i - 1]
        curr_date = dates[i]

        # 用前一天的预测信号选股
        pred_prev = pred.xs(prev_date, level=0) if prev_date in pred.index.get_level_values(0) else pd.Series(dtype=float)  # noqa: E501
        if len(pred_prev) == 0:
            # 尝试找最近的有预测的日期
            available_dates = pred.index.get_level_values(0).unique()
            available_before = [d for d in available_dates if d <= prev_date]
            if available_before:
                pred_prev = pred.xs(available_before[-1], level=0)
            else:
                daily_returns.append(0.0)
                portfolio_values.append(portfolio_values[-1])
                bench_returns.append(0.0)
                bench_values.append(bench_values[-1])
                continue

        # 选 Topk 股票
        top_stocks = pred_prev.nlargest(args.topk).index.tolist()

        # 计算这些股票从 prev_date 到 curr_date 的收益
        stock_returns = []
        for stock in top_stocks:
            try:
                prev_close = prices.loc[(prev_date, stock), "close"]
                curr_close = prices.loc[(curr_date, stock), "close"]
                if prev_close > 0 and curr_close > 0 and not np.isnan(prev_close) and not np.isnan(curr_close):
                    stock_returns.append(curr_close / prev_close - 1)
            except KeyError:
                continue

        if stock_returns:
            daily_ret = np.mean(stock_returns)
        else:
            daily_ret = 0.0

        daily_returns.append(daily_ret)
        portfolio_values.append(portfolio_values[-1] * (1 + daily_ret))

        # 基准收益
        try:
            bench_prev = bench_prices.loc[(prev_date, args.benchmark), "bench_close"]
            bench_curr = bench_prices.loc[(curr_date, args.benchmark), "bench_close"]
            bench_ret = bench_curr / bench_prev - 1 if bench_prev > 0 else 0.0
        except KeyError:
            bench_ret = 0.0
        bench_returns.append(bench_ret)
        bench_values.append(bench_values[-1] * (1 + bench_ret))

    # 计算指标
    print("\n[4/4] 计算指标...")
    daily_returns = np.array(daily_returns)
    bench_returns = np.array(bench_returns)
    n_days = len(daily_returns)

    total_return = portfolio_values[-1] - 1
    bench_total = bench_values[-1] - 1
    annual_return = (1 + total_return) ** (252 / n_days) - 1 if total_return > -1 else 0
    bench_annual = (1 + bench_total) ** (252 / n_days) - 1 if bench_total > -1 else 0

    sharpe = daily_returns.mean() / daily_returns.std() * np.sqrt(252) if daily_returns.std() > 0 else 0
    bench_sharpe = bench_returns.mean() / bench_returns.std() * np.sqrt(252) if bench_returns.std() > 0 else 0

    cum_returns = np.array(portfolio_values[1:]) - 1
    cum_max = np.maximum.accumulate(cum_returns)
    drawdown = cum_returns - cum_max
    max_drawdown = drawdown.min()

    daily_returns - bench_returns
    excess_total = total_return - bench_total
    excess_annual = annual_return - bench_annual

    # 输出结果
    print(f"\n{'='*65}")
    print(f"回测结果 ({args.start} ~ {args.end}, Top{args.topk} 等权)")
    print(f"{'='*65}")
    print(f"{'指标':<20} {'策略':>15} {'基准(' + args.benchmark + ')':>15} {'超额':>15}")
    print(f"{'-'*65}")
    print(f"{'总收益率':<20} {total_return:>14.2%} {bench_total:>14.2%} {excess_total:>14.2%}")
    print(f"{'年化收益率':<20} {annual_return:>14.2%} {bench_annual:>14.2%} {excess_annual:>14.2%}")
    print(f"{'夏普比率':<20} {sharpe:>15.2f} {bench_sharpe:>15.2f} {sharpe - bench_sharpe:>15.2f}")
    print(f"{'最大回撤':<20} {max_drawdown:>14.2%}")
    print(f"{'交易天数':<20} {n_days:>15}")
    print(f"{'='*65}")

    # 保存
    ts = now_bj().strftime("%Y%m%d_%H%M%S")
    output_dir = Path("reports")
    output_dir.mkdir(exist_ok=True)

    summary = {
        "timestamp": now_bj().isoformat(),
        "period": f"{args.start} ~ {args.end}",
        "topk": args.topk,
        "total_return": float(total_return),
        "annual_return": float(annual_return),
        "sharpe": float(sharpe),
        "max_drawdown": float(max_drawdown),
        "benchmark": args.benchmark,
        "bench_total_return": float(bench_total),
        "bench_annual_return": float(bench_annual),
        "bench_sharpe": float(bench_sharpe),
        "excess_total": float(excess_total),
        "excess_annual": float(excess_annual),
        "n_days": n_days,
    }
    summary_file = output_dir / f"backtest_summary_{ts}.json"
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\n摘要已保存: {summary_file}")

    # 保存每日收益
    df = pd.DataFrame({
        "date": dates[1:],
        "strategy_return": daily_returns,
        "bench_return": bench_returns,
        "strategy_value": portfolio_values[1:],
        "bench_value": bench_values[1:],
    })
    df_file = output_dir / f"backtest_daily_{ts}.csv"
    df.to_csv(df_file, index=False)
    print(f"每日收益已保存: {df_file}")


if __name__ == "__main__":
    main()
