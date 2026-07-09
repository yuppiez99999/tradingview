# -*- coding: utf-8 -*-
"""
魔搭社区 / AutoDL / 超算中心 云端训练脚本

使用方法:
  1. 上传 qlib_data/cn_data 目录到云端
  2. pip install pyqlib lightgbm
  3. python cloud_train.py

支持参数:
  python cloud_train.py --market csi300 --start 2015-01-01 --end 2020-09-25
  python cloud_train.py --market all --start 2018-01-01 --end 2020-09-25
  python cloud_train.py --model lightgbm --num_leaves 128 --boost_round 500
"""
import os
import sys
import json
import argparse
import datetime
import numpy as np
import pandas as pd


def parse_args():
    p = argparse.ArgumentParser(description="QLib 云端训练")
    p.add_argument("--data-dir", default="./qlib_data/cn_data", help="QLib 数据目录")
    p.add_argument("--market", default="csi300", choices=["csi300", "csi500", "all"])
    p.add_argument("--start", default="2015-01-01")
    p.add_argument("--end", default="2020-09-25")
    p.add_argument("--train-end", default="2018-12-31")
    p.add_argument("--valid-end", default="2019-06-30")
    p.add_argument("--model", default="lightgbm", choices=["lightgbm"])
    p.add_argument("--num-leaves", type=int, default=64)
    p.add_argument("--boost-round", type=int, default=200)
    p.add_argument("--learning-rate", type=float, default=0.05)
    p.add_argument("--max-depth", type=int, default=6)
    p.add_argument("--output", default="./reports")
    return p.parse_args()


def main():
    args = parse_args()

    os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"

    import qlib
    qlib.init(provider_uri=args.data_dir, region="cn")
    print(f"[QLib] {qlib.__version__} 初始化完成")

    from qlib.contrib.model.gbdt import LGBModel
    from qlib.contrib.data.handler import Alpha158
    from qlib.utils import init_instance_by_config

    TEST_START = args.valid_end

    print(f"\n{'='*70}")
    print(f"QLib 横截面模型训练 ({args.market})")
    print(f"{'='*70}")
    print(f"数据: {args.start} ~ {args.end}")
    print(f"训练: {args.start} ~ {args.train_end}")
    print(f"验证: {args.train_end} ~ {args.valid_end}")
    print(f"测试: {TEST_START} ~ {args.end}")
    print(f"模型: LightGBM (leaves={args.num_leaves}, rounds={args.boost_round})")
    print(f"{'='*70}\n")

    # Alpha158
    handler_config = {
        "start_time": args.start,
        "end_time": args.end,
        "fit_start_time": args.start,
        "fit_end_time": args.train_end,
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

    print("[1/4] Alpha158 特征工程...")
    handler = Alpha158(**handler_config)

    dataset_config = {
        "class": "DatasetH",
        "module_path": "qlib.data.dataset",
        "kwargs": {
            "handler": handler,
            "segments": {
                "train": (args.start, args.train_end),
                "valid": (args.train_end, args.valid_end),
                "test": (TEST_START, args.end),
            },
        },
    }

    print("[2/4] 构建数据集...")
    dataset = init_instance_by_config(dataset_config)
    train_data = dataset.prepare("train", col_set=["feature", "label"])
    test_data = dataset.prepare("test", col_set=["feature", "label"])
    n_stocks = train_data.index.get_level_values(1).nunique()
    print(f"      训练: {len(train_data)} 行, {n_stocks} 股, {len(train_data.columns)-1} 特征")
    print(f"      测试: {len(test_data)} 行")

    print("[3/4] 训练 LightGBM...")
    model = LGBModel(
        loss="mse",
        num_leaves=args.num_leaves,
        learning_rate=args.learning_rate,
        num_boost_round=args.boost_round,
        max_depth=args.max_depth,
        feature_fraction=0.8,
        bagging_fraction=0.8,
        bagging_freq=5,
        early_stopping_rounds=20,
        verbose=-1,
    )
    model.fit(dataset)
    print(f"      完成!")

    print("[4/4] 评估...\n")
    pred = model.predict(dataset, segment="test")
    if isinstance(pred, pd.DataFrame):
        pred_series = pred.iloc[:, 0]
    else:
        pred_series = pd.Series(pred)

    test_label = test_data["label"]
    label_series = test_label.iloc[:, 0] if isinstance(test_label, pd.DataFrame) else test_label
    common_idx = pred_series.index.intersection(label_series.index)
    pred_aligned = pred_series.loc[common_idx]
    label_aligned = label_series.loc[common_idx]

    overall_ic = pred_aligned.corr(label_aligned)
    ic_by_date = pred_aligned.groupby(level=0).apply(
        lambda x: x.corr(label_aligned.loc[x.index]) if len(x) > 1 else np.nan
    )
    mean_ic = ic_by_date.mean()
    rank_ic_by_date = pred_aligned.groupby(level=0).apply(
        lambda x: x.rank().corr(label_aligned.loc[x.index].rank()) if len(x) > 1 else np.nan
    )
    mean_rank_ic = rank_ic_by_date.mean()
    ic_ir = mean_ic / ic_by_date.std() if ic_by_date.std() > 0 else 0

    print(f"{'='*70}")
    print(f"训练结果")
    print(f"{'='*70}")
    print(f"整体 IC:          {overall_ic:.4f}")
    print(f"日均 IC:          {mean_ic:.4f}")
    print(f"日均 Rank IC:     {mean_rank_ic:.4f}")
    print(f"IC IR:            {ic_ir:.4f}")
    print(f"IC > 0 占比:      {(ic_by_date > 0).mean():.2%}")

    # 保存 Top 10 预测信号
    latest_preds = pred_series.groupby(level=1).last().sort_values(ascending=False)
    print(f"\nTop 10 看多标的:")
    for code, signal in latest_preds.head(10).items():
        print(f"  {code}: {signal:.6f}")
    print(f"\nTop 10 看空标的:")
    for code, signal in latest_preds.tail(10).items():
        print(f"  {code}: {signal:.6f}")

    # 保存报告
    os.makedirs(args.output, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(args.output, f"qlib_train_{ts}.json")
    report = {
        "timestamp": datetime.datetime.now().isoformat(),
        "qlib_version": qlib.__version__,
        "config": {
            "market": args.market,
            "data_range": f"{args.start} ~ {args.end}",
            "num_leaves": args.num_leaves,
            "boost_round": args.boost_round,
            "learning_rate": args.learning_rate,
        },
        "train_samples": len(train_data),
        "test_samples": len(test_data),
        "n_stocks": int(n_stocks),
        "overall_ic": round(float(overall_ic), 4),
        "mean_daily_ic": round(float(mean_ic), 4),
        "mean_daily_rank_ic": round(float(mean_rank_ic), 4),
        "ic_ir": round(float(ic_ir), 4),
        "ic_positive_ratio": round(float((ic_by_date > 0).mean()), 4),
        "top_long": {k: round(float(v), 6) for k, v in latest_preds.head(10).items()},
        "top_short": {k: round(float(v), 6) for k, v in latest_preds.tail(10).items()},
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n报告已保存: {report_path}")


if __name__ == "__main__":
    main()
