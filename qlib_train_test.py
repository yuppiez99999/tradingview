# -*- coding: utf-8 -*-
"""
QLib 横截面模型训练 — 仅持仓和交易计划标的

使用 12 只持仓标的作为训练池，训练 LightGBM 横截面模型。
对冲引擎为规则驱动，无需训练。
"""
import os
import sys
import json
import datetime
import numpy as np
import pandas as pd

# 避免本地 qlib/ 源码目录遮蔽已安装的 pyqlib 包
_qlib_source = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qlib")
_cwd = os.path.dirname(os.path.abspath(__file__))
sys.path = [p for p in sys.path if p != _qlib_source and p != _cwd and os.path.abspath(p) != _qlib_source]

os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"

import qlib
from qlib.contrib.model.gbdt import LGBModel
from qlib.contrib.data.handler import Alpha158
from qlib.utils import init_instance_by_config


def main():
    DATA_DIR = r"E:\各种PY程序\28-终极量化交易系统7.1\qlib_data\cn_data"
    qlib.init(provider_uri=DATA_DIR, region="cn")
    print(f"[QLib] {qlib.__version__} 初始化完成")

    # 关闭多进程，避免内存不足
    from qlib.config import C
    C.joblib_backend = "sequential"

    # 持仓标的（扩展到25只，500万计划）
    PORTFOLIO_STOCKS = [
        # ETF - 科技
        "SH588080",  # 科创50ETF易方达
        "SH512760",  # 半导体ETF国泰
        "SH588000",  # 科创50ETF华夏
        # ETF - 金融
        "SH512880",  # 证券ETF国泰
        "SH512800",  # 银行ETF华宝
        # ETF - 宽基
        "SH510050",  # 上证50ETF华夏
        "SH510300",  # 沪深300ETF华泰
        "SH510500",  # 中证500ETF南方
        "SH512100",  # 中证1000ETF
        # ETF - 新能源/医药/资源/成长
        "SH515030",  # 新能源车ETF华夏
        "SH512170",  # 医疗ETF华宝
        "SH518880",  # 黄金ETF华安
        "SZ159915",  # 创业板ETF易方达
        # 股票 - 科技
        "SH688041",  # 海光信息
        "SZ300308",  # 中际旭创
        "SZ002371",  # 北方华创
        "SH603019",  # 中科曙光
        "SZ300033",  # 同花顺
        "SZ300782",  # 卓胜微
        # 股票 - 制造/新能源/资源
        "SH688017",  # 绿的谐波
        "SZ300274",  # 阳光电源
        "SZ000408",  # 藏格矿业
        # 股票 - 顺周期/医药/防御
        "SH601088",  # 中国神华
        "SH600276",  # 恒瑞医药
        "SH600900",  # 长江电力
    ]
    STOCK_NAMES = {
        "SH588080": "科创50ETF易方达", "SH512760": "半导体ETF国泰",
        "SH588000": "科创50ETF华夏", "SH512880": "证券ETF国泰",
        "SH512800": "银行ETF华宝", "SH510050": "上证50ETF华夏",
        "SH510300": "沪深300ETF华泰", "SH510500": "中证500ETF南方",
        "SH512100": "中证1000ETF", "SH515030": "新能源车ETF华夏",
        "SH512170": "医疗ETF华宝", "SH518880": "黄金ETF华安",
        "SZ159915": "创业板ETF易方达", "SH688041": "海光信息",
        "SZ300308": "中际旭创", "SZ002371": "北方华创",
        "SH603019": "中科曙光", "SZ300033": "同花顺",
        "SZ300782": "卓胜微", "SH688017": "绿的谐波",
        "SZ300274": "阳光电源", "SZ000408": "藏格矿业",
        "SH601088": "中国神华", "SH600276": "恒瑞医药",
        "SH600900": "长江电力",
    }

    DATA_START = "2018-01-01"
    DATA_END = "2026-07-01"
    TRAIN_END = "2024-06-30"
    VALID_END = "2025-06-30"
    TEST_START = "2025-07-01"

    print(f"\n{'='*70}")
    print(f"QLib 横截面模型训练 — 持仓标的 (25只)")
    print(f"{'='*70}")
    print(f"训练池: {', '.join(STOCK_NAMES.values())}")
    print(f"数据范围: {DATA_START} ~ {DATA_END}")
    print(f"训练期:   {DATA_START} ~ {TRAIN_END}")
    print(f"验证期:   {TRAIN_END} ~ {VALID_END}")
    print(f"测试期:   {TEST_START} ~ {DATA_END}")
    print(f"对冲引擎: 规则驱动，无需训练")
    print(f"{'='*70}\n")

    # Alpha158 数据处理器 — 用持仓标的作为训练池
    data_handler_config = {
        "start_time": DATA_START,
        "end_time": DATA_END,
        "fit_start_time": DATA_START,
        "fit_end_time": TRAIN_END,
        "instruments": PORTFOLIO_STOCKS,
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
    handler = Alpha158(**data_handler_config)
    print(f"      完成 (158 特征)")

    dataset_config = {
        "class": "DatasetH",
        "module_path": "qlib.data.dataset",
        "kwargs": {
            "handler": handler,
            "segments": {
                "train": (DATA_START, TRAIN_END),
                "valid": (TRAIN_END, VALID_END),
                "test": (TEST_START, DATA_END),
            },
        },
    }

    print("[2/4] 构建数据集...")
    dataset = init_instance_by_config(dataset_config)
    train_data = dataset.prepare("train", col_set=["feature", "label"])
    test_data = dataset.prepare("test", col_set=["feature", "label"])
    n_stocks_train = train_data.index.get_level_values(1).nunique()
    n_stocks_test = test_data.index.get_level_values(1).nunique()
    print(f"      训练集: {len(train_data)} 行, {n_stocks_train} 股, {len(train_data.columns)-1} 特征")
    print(f"      测试集: {len(test_data)} 行, {n_stocks_test} 股")

    print("[3/4] 训练 LightGBM...")
    model = LGBModel(
        loss="mse",
        num_leaves=64,
        learning_rate=0.05,
        num_boost_round=200,
        max_depth=6,
        feature_fraction=0.8,
        bagging_fraction=0.8,
        bagging_freq=5,
        early_stopping_rounds=20,
        verbose=-1,
    )
    model.fit(dataset)
    print(f"      训练完成!")

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
    print(f"模型训练结果")
    print(f"{'='*70}")
    print(f"整体 IC:          {overall_ic:.4f}")
    print(f"日均 IC:          {mean_ic:.4f}")
    print(f"日均 Rank IC:     {mean_rank_ic:.4f}")
    print(f"IC IR:            {ic_ir:.4f}")
    print(f"IC > 0 占比:      {(ic_by_date > 0).mean():.2%}")

    # 各标的信号
    print(f"\n{'='*70}")
    print(f"持仓标的预测信号 (测试期末)")
    print(f"{'='*70}")
    print(f"{'代码':<12} {'名称':<10} {'最新信号':>10} {'方向':>6} {'均值':>10} {'波动':>10}")
    print(f"{'─'*70}")

    results = []
    for code in PORTFOLIO_STOCKS:
        name = STOCK_NAMES.get(code, code)
        code_variants = [code, code.lower()]
        for cv in code_variants:
            try:
                stock_pred = pred_series.xs(cv, level=1)
                if len(stock_pred) > 0:
                    latest = stock_pred.iloc[-1]
                    avg = stock_pred.mean()
                    std = stock_pred.std()
                    direction = "看多" if latest > 0 else ("看空" if latest < 0 else "中性")
                    print(f"{code:<12} {name:<10} {latest:>10.6f} {direction:>6} {avg:>10.6f} {std:>10.6f}")
                    results.append({
                        "code": code, "name": name,
                        "latest_signal": round(float(latest), 6),
                        "avg_signal": round(float(avg), 6),
                        "std_signal": round(float(std), 6),
                        "direction": direction,
                    })
                    break
            except KeyError:
                continue

    print(f"{'─'*70}")

    # 信号汇总
    long_count = sum(1 for r in results if r["direction"] == "看多")
    short_count = sum(1 for r in results if r["direction"] == "看空")
    neutral_count = sum(1 for r in results if r["direction"] == "中性")
    print(f"信号分布: 看多 {long_count} / 看空 {short_count} / 中性 {neutral_count}")
    print(f"{'='*70}\n")

    # 保存报告
    report_dir = os.path.join(_cwd, "reports")
    os.makedirs(report_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(report_dir, f"qlib_portfolio_train_{ts}.json")
    report = {
        "timestamp": datetime.datetime.now().isoformat(),
        "qlib_version": qlib.__version__,
        "model": "LightGBM (Alpha158, Portfolio Stocks)",
        "training_pool": [f"{c} ({STOCK_NAMES[c]})" for c in PORTFOLIO_STOCKS],
        "data_range": f"{DATA_START} ~ {DATA_END}",
        "train_samples": len(train_data),
        "test_samples": len(test_data),
        "n_stocks_train": int(n_stocks_train),
        "n_stocks_test": int(n_stocks_test),
        "overall_ic": round(float(overall_ic), 4),
        "mean_daily_ic": round(float(mean_ic), 4),
        "mean_daily_rank_ic": round(float(mean_rank_ic), 4),
        "ic_ir": round(float(ic_ir), 4),
        "ic_positive_ratio": round(float((ic_by_date > 0).mean()), 4),
        "signal_distribution": {"long": long_count, "short": short_count, "neutral": neutral_count},
        "stock_signals": results,
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)

    print(f"报告已保存: {report_path}")


if __name__ == "__main__":
    main()
