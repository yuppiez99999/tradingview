"""
QLib 标准回测脚本
用训练好的模型预测 + TopkDropoutStrategy 做回测
输出：年化收益、夏普比率、最大回撤、与基准对比
"""
import argparse
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
    p = argparse.ArgumentParser(description="QLib 回测")
    p.add_argument("--data-dir", default="./qlib_data/cn_data")
    p.add_argument("--market", default="csi300")
    p.add_argument("--start", default="2025-06-30", help="回测开始日期")
    p.add_argument("--end", default="2026-07-08", help="回测结束日期")
    p.add_argument("--model-path", default=None, help="模型 .pkl 路径")
    p.add_argument("--topk", type=int, default=10, help="持仓股票数")
    p.add_argument("--n-drop", type=int, default=2, help="每日换仓数")
    p.add_argument("--benchmark", default="SH000300", help="基准代码")
    return p.parse_args()


def main():
    args = parse_args()

    os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"

    import qlib
    from qlib.config import C
    C["exp_manager"]["kwargs"]["uri"] = "file:/tmp/mlruns"
    qlib.init(provider_uri=args.data_dir, region="cn")
    print(f"[QLib] {qlib.__version__} 初始化完成")

    from qlib.backtest import backtest as qlib_backtest
    from qlib.contrib.data.handler import Alpha158
    from qlib.contrib.strategy import TopkDropoutStrategy
    from qlib.utils import init_instance_by_config

    # 加载模型
    if args.model_path and os.path.exists(args.model_path):
        model = load_model_safe(args.model_path)
        print(f"已加载模型: {args.model_path}")
    else:
        print("未指定模型，请用 --model-path 指定")
        sys.exit(1)

    # 修复 instruments end_date（扩展到数据末日）
    for ins_file in ["all.txt", "csi300.txt", "csi500.txt", "csi100.txt"]:
        ins_path = os.path.join(args.data_dir, "instruments", ins_file)
        cal_path = os.path.join(args.data_dir, "calendars", "day.txt")
        if not os.path.exists(ins_path) or not os.path.exists(cal_path):
            continue
        with open(cal_path, encoding="utf-8") as f:
            last_date = f.read().strip().split("\n")[-1]
        with open(ins_path, encoding="utf-8") as f:
            lines = f.read().strip().split("\n")
        changed = False
        new_lines = []
        for line in lines:
            parts = line.split("\t")
            if len(parts) == 3 and parts[2] < last_date:
                parts[2] = last_date
                changed = True
            new_lines.append("\t".join(parts))
        if changed:
            with open(ins_path, "w", encoding="utf-8") as f:
                f.write("\n".join(new_lines) + "\n")
            print(f"[Instruments] 已扩展 {ins_file} end_date 到 {last_date}")

    # 准备数据集（用于预测）
    handler_config = {
        "start_time": args.start,
        "end_time": args.end,
        "fit_start_time": args.start,
        "fit_end_time": args.end,
        "instruments": args.market,
        "infer_processors": [
            {"class": "RobustZScoreNorm", "kwargs": {"fields_group": "feature", "clip_outlier": True}},
            {"class": "Fillna", "kwargs": {"fields_group": "feature"}},
        ],
        "learn_processors": [
            {"class": "DropnaLabel"},
            {"class": "CSRankNorm", "kwargs": {"fields_group": "label"}},
        ],
        "label": ["Ref($close, -2) / Ref($close, -1) - 1"],
    }
    handler = Alpha158(**handler_config)
    dataset_config = {
        "class": "DatasetH",
        "module_path": "qlib.data.dataset",
        "kwargs": {
            "handler": handler,
            "segments": {"test": (args.start, args.end)},
        },
    }
    dataset = init_instance_by_config(dataset_config)

    # 预测
    print("\n[1/3] 生成预测信号...")
    pred = model.predict(dataset, segment="test")
    if isinstance(pred, pd.DataFrame):
        pred = pred.iloc[:, 0]
    print(f"  预测: {len(pred)} 行, 日期范围 {pred.index.get_level_values(0).min()} ~ {pred.index.get_level_values(0).max()}")  # noqa: E501

    # 回测
    print("\n[2/3] 运行回测...")

    strategy = TopkDropoutStrategy(topk=args.topk, n_drop=args.n_drop, signal=pred)
    executor_config = {
        "class": "SimulatorExecutor",
        "module_path": "qlib.backtest.executor",
        "kwargs": {
            "time_per_step": "day",
            "generate_portfolio_metrics": True,
        },
    }
    report_normal, positions_normal = qlib_backtest(
        start_time=args.start,
        end_time=args.end,
        strategy=strategy,
        executor=executor_config,
        benchmark=args.benchmark,
        account=100000000,
        exchange_kwargs={
            "limit_threshold": 0.095,
            "deal_price": "close",
            "open_cost": 0.0005,
            "close_cost": 0.0015,
            "min_cost": 5,
        },
    )
    print("  回测完成!")

    # 分析结果
    print("\n[3/3] 分析结果...")

    # 计算关键指标
    report = report_normal
    report["return"] = report["return"].fillna(0)
    cum_return = (1 + report["return"]).cumprod() - 1
    total_return = cum_return.iloc[-1]
    n_days = len(report)
    annual_return = (1 + total_return) ** (252 / n_days) - 1
    daily_return = report["return"]
    sharpe = daily_return.mean() / daily_return.std() * np.sqrt(252) if daily_return.std() > 0 else 0
    cum_max = cum_return.cummax()
    drawdown = cum_return - cum_max
    max_drawdown = drawdown.min()

    # 基准收益
    bench_return = report.get("bench", pd.Series(dtype=float)).fillna(0)
    bench_cum = (1 + bench_return).cumprod() - 1
    bench_total = bench_cum.iloc[-1] if len(bench_cum) > 0 else 0
    bench_annual = (1 + bench_total) ** (252 / n_days) - 1 if bench_total > -1 else 0
    excess_return = total_return - bench_total
    bench_sharpe = bench_return.mean() / bench_return.std() * np.sqrt(252) if bench_return.std() > 0 else 0

    print(f"\n{'='*60}")
    print(f"回测结果 ({args.start} ~ {args.end})")
    print(f"{'='*60}")
    print(f"{'指标':<20} {'策略':>15} {'基准(' + args.benchmark + ')':>15} {'超额':>15}")
    print(f"{'-'*60}")
    print(f"{'总收益率':<20} {total_return:>14.2%} {bench_total:>14.2%} {excess_return:>14.2%}")
    print(f"{'年化收益率':<20} {annual_return:>14.2%} {bench_annual:>14.2%} {annual_return - bench_annual:>14.2%}")
    print(f"{'夏普比率':<20} {sharpe:>15.2f} {bench_sharpe:>15.2f} {sharpe - bench_sharpe:>15.2f}")
    print(f"{'最大回撤':<20} {max_drawdown:>14.2%}")
    print(f"{'交易天数':<20} {n_days:>15}")
    print(f"{'='*60}")

    # 保存结果
    ts = now_bj().strftime("%Y%m%d_%H%M%S")
    output_dir = Path("reports")
    output_dir.mkdir(exist_ok=True)
    report_file = output_dir / f"backtest_{ts}.csv"
    report.to_csv(report_file)
    print(f"\n回测明细已保存: {report_file}")

    summary = {
        "timestamp": now_bj().isoformat(),
        "period": f"{args.start} ~ {args.end}",
        "topk": args.topk,
        "n_drop": args.n_drop,
        "total_return": float(total_return),
        "annual_return": float(annual_return),
        "sharpe": float(sharpe),
        "max_drawdown": float(max_drawdown),
        "benchmark": args.benchmark,
        "bench_total_return": float(bench_total),
        "bench_annual_return": float(bench_annual),
        "bench_sharpe": float(bench_sharpe),
        "excess_return": float(excess_return),
    }
    import json
    summary_file = output_dir / f"backtest_summary_{ts}.json"
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"回测摘要已保存: {summary_file}")


if __name__ == "__main__":
    main()
