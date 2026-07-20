# -*- coding: utf-8 -*-
"""
标签窗口实验 — 测试3/5/7/10日不同前瞻收益标签的IC表现
优化版：减少训练池 + 彻底清理QLib状态
"""
import os
import sys
import json
import gc
import datetime
import numpy as np
import pandas as pd

QLIB_DATA_DIR = r"E:\各种PY程序\28-终极量化交易系统7.1\qlib_data\cn_data"

PORTFOLIO_STOCKS = [
    ("002371", "SZ", "北方华创"), ("300308", "SZ", "中际旭创"),
    ("000425", "SZ", "徐工机械"), ("600276", "SH", "恒瑞医药"),
    ("600900", "SH", "长江电力"), ("600036", "SH", "招商银行"),
    ("601088", "SH", "中国神华"), ("300274", "SZ", "阳光电源"),
    ("603019", "SH", "中科曙光"), ("600089", "SH", "特变电工"),
    ("600019", "SH", "宝钢股份"), ("600219", "SH", "南山铝业"),
    ("688041", "SH", "海光信息"), ("688981", "SH", "中芯国际"),
    ("688017", "SH", "绿的谐波"),
]

CSI50_AUX = [
    ("600519", "SH", "贵州茅台"), ("000858", "SZ", "五粮液"),
    ("601318", "SH", "中国平安"), ("000333", "SZ", "美的集团"),
    ("600030", "SH", "中信证券"), ("601166", "SH", "兴业银行"),
    ("000651", "SZ", "格力电器"), ("002415", "SZ", "海康威视"),
    ("600031", "SH", "三一重工"), ("601888", "SH", "中国中免"),
    ("600585", "SH", "海螺水泥"), ("000725", "SZ", "京东方A"),
    ("600009", "SH", "上海机场"), ("601668", "SH", "中国建筑"),
    ("600887", "SH", "伊利股份"), ("002555", "SZ", "三七互娱"),
    ("600690", "SH", "海尔智家"), ("000002", "SZ", "万科A"),
    ("601398", "SH", "工商银行"), ("601939", "SH", "建设银行"),
    ("600028", "SH", "中国石化"), ("002475", "SZ", "立讯精密"),
    ("300750", "SZ", "宁德时代"), ("601899", "SH", "紫金矿业"),
    ("600048", "SH", "保利发展"), ("601012", "SH", "隆基绿能"),
    ("600809", "SH", "山西汾酒"), ("600111", "SH", "北方稀土"),
    ("000938", "SZ", "紫光国微"), ("002714", "SZ", "牧原股份"),
]

TRAIN_END = "2023-12-31"
VALID_END = "2024-06-30"
TEST_START = "2024-07-01"
DATA_START = "2020-01-01"

FORWARD_DAYS_LIST = [3, 5, 7, 10]


def get_available_symbols():
    features_dir = os.path.join(QLIB_DATA_DIR, "features")
    if not os.path.exists(features_dir):
        return []
    return [d for d in os.listdir(features_dir) if os.path.isdir(os.path.join(features_dir, d))]


def get_cal_end():
    cal_path = os.path.join(QLIB_DATA_DIR, "calendars", "day.txt")
    with open(cal_path, "r") as f:
        calendar_dates = pd.to_datetime([line.strip() for line in f if line.strip()])
    return calendar_dates[-1].strftime("%Y-%m-%d")


def cleanup_qlib():
    global qlib, C, LGBModel, Alpha158, init_instance_by_config
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith('qlib'):
            del sys.modules[mod_name]
    qlib = None
    C = None
    LGBModel = None
    Alpha158 = None
    init_instance_by_config = None
    gc.collect()


def train_with_label(forward_days):
    global qlib, C, LGBModel, Alpha158, init_instance_by_config
    
    gc.collect()
    
    available_symbols = get_available_symbols()
    cal_end = get_cal_end()
    
    portfolio_symbols = [f"{exch}{code}" for code, exch, name in PORTFOLIO_STOCKS]
    aux_symbols = [f"{exch}{code}" for code, exch, name in CSI50_AUX
                   if f"{exch}{code}" not in portfolio_symbols]
    all_train_symbols = portfolio_symbols + aux_symbols
    
    all_train_symbols = [s for s in all_train_symbols if s.lower() in available_symbols]
    portfolio_symbols = [s for s in portfolio_symbols if s.lower() in available_symbols]
    
    os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"
    
    import qlib
    qlib.init(provider_uri=QLIB_DATA_DIR, region="cn")
    
    from qlib.config import C
    C.joblib_backend = "sequential"
    
    from qlib.contrib.model.gbdt import LGBModel
    from qlib.contrib.data.handler import Alpha158
    from qlib.utils import init_instance_by_config
    
    label_expr = f"Ref($close, -{forward_days}) / Ref($close, -1) - 1"
    
    data_handler_config = {
        "start_time": DATA_START,
        "end_time": cal_end,
        "fit_start_time": DATA_START,
        "fit_end_time": TRAIN_END,
        "instruments": all_train_symbols,
        "infer_processors": [
            {"class": "RobustZScoreNorm", "kwargs": {"fields_group": "feature", "clip_outlier": True}},
            {"class": "Fillna", "kwargs": {"fields_group": "feature"}},
        ],
        "learn_processors": [
            {"class": "DropnaLabel"},
            {"class": "CSRankNorm", "kwargs": {"fields_group": "label"}},
        ],
        "label": [label_expr],
    }
    
    handler = Alpha158(**data_handler_config)
    gc.collect()
    
    dataset_config = {
        "class": "DatasetH",
        "module_path": "qlib.data.dataset",
        "kwargs": {
            "handler": handler,
            "segments": {
                "train": (DATA_START, TRAIN_END),
                "valid": (TRAIN_END, VALID_END),
                "test": (TEST_START, cal_end),
            },
        },
    }
    
    dataset = init_instance_by_config(dataset_config)
    train_data = dataset.prepare("train", col_set=["feature", "label"])
    test_data = dataset.prepare("test", col_set=["feature", "label"])
    
    gc.collect()
    
    model = LGBModel(
        loss="mse",
        num_leaves=31,
        learning_rate=0.01,
        num_boost_round=2000,
        max_depth=6,
        feature_fraction=0.7,
        bagging_fraction=0.7,
        bagging_freq=5,
        early_stopping_rounds=200,
        lambda_l1=0.1,
        lambda_l2=0.1,
        min_data_in_leaf=20,
        verbose=0,
    )
    model.fit(dataset)
    
    gc.collect()
    
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
    
    label_aligned = label_aligned.replace([np.inf, -np.inf], np.nan)
    
    mask = pred_aligned.notna() & label_aligned.notna()
    pred_valid = pred_aligned[mask]
    label_valid = label_aligned[mask]
    
    overall_ic = pred_valid.corr(label_valid) if pred_valid.std() > 0 and label_valid.std() > 0 else np.nan
    ic_by_date = pred_valid.groupby(level=0).apply(
        lambda x: x.corr(label_valid.loc[x.index]) if len(x) > 1 else np.nan
    )
    mean_ic = ic_by_date.mean()
    rank_ic_by_date = pred_valid.groupby(level=0).apply(
        lambda x: x.rank().corr(label_valid.loc[x.index].rank()) if len(x) > 1 else np.nan
    )
    mean_rank_ic = rank_ic_by_date.mean()
    ic_ir = mean_ic / ic_by_date.std() if ic_by_date.std() > 0 else 0
    
    latest_preds = pred_series.groupby(level=1).last()
    long_count = sum(1 for s in latest_preds if s > 0)
    short_count = len(latest_preds) - long_count
    
    result = {
        "forward_days": forward_days,
        "overall_ic": round(float(overall_ic), 4) if not np.isnan(overall_ic) else None,
        "mean_daily_ic": round(float(mean_ic), 4),
        "mean_daily_rank_ic": round(float(mean_rank_ic), 4),
        "ic_ir": round(float(ic_ir), 4),
        "ic_positive_ratio": round(float((ic_by_date > 0).mean()), 4),
        "n_stocks": train_data.index.get_level_values(1).nunique(),
        "train_samples": len(train_data),
        "test_samples": len(test_data),
        "long_count": long_count,
        "short_count": short_count,
    }
    
    del model, dataset, handler, train_data, test_data, pred, pred_series, test_label, label_series
    del pred_aligned, label_aligned, pred_valid, label_valid
    cleanup_qlib()
    
    return result


def main():
    print(f"\n{'='*70}")
    print(f"标签窗口实验 — 测试3/5/7/10日前瞻收益")
    print(f"{'='*70}\n")
    
    results = []
    for forward_days in FORWARD_DAYS_LIST:
        print(f"[实验] 标签窗口: {forward_days}日...")
        try:
            result = train_with_label(forward_days)
            results.append(result)
            print(f"  IC IR: {result['ic_ir']:.4f}, 日均IC: {result['mean_daily_ic']:.4f}, 信号: 看多{result['long_count']}/看空{result['short_count']}")
        except Exception as e:
            print(f"  失败: {str(e)}")
            results.append({
                "forward_days": forward_days,
                "overall_ic": None,
                "mean_daily_ic": None,
                "mean_daily_rank_ic": None,
                "ic_ir": None,
                "ic_positive_ratio": None,
                "n_stocks": None,
                "train_samples": None,
                "test_samples": None,
                "long_count": None,
                "short_count": None,
                "error": str(e),
            })
        gc.collect()
    
    print(f"\n{'='*70}")
    print(f"实验结果汇总")
    print(f"{'='*70}")
    print(f"{'窗口':<6} {'整体IC':<10} {'日均IC':<10} {'RankIC':<10} {'IC IR':<10} {'IC>0':<10} {'多/空'}")
    print(f"{'─'*80}")
    
    best_ic_ir = -1
    best_window = None
    for r in results:
        ic_ir_str = f"{r['ic_ir']:.4f}" if r['ic_ir'] else "N/A"
        ic_str = f"{r['overall_ic']:.4f}" if r['overall_ic'] else "N/A"
        mean_ic_str = f"{r['mean_daily_ic']:.4f}" if r['mean_daily_ic'] else "N/A"
        rank_ic_str = f"{r['mean_daily_rank_ic']:.4f}" if r['mean_daily_rank_ic'] else "N/A"
        ic_pos_str = f"{r['ic_positive_ratio']:.1%}" if r['ic_positive_ratio'] else "N/A"
        long_short_str = f"{r['long_count']}/{r['short_count']}" if r['long_count'] else "N/A"
        
        if r['ic_ir'] and r['ic_ir'] > best_ic_ir:
            best_ic_ir = r['ic_ir']
            best_window = r['forward_days']
        
        print(f"{r['forward_days']:<6} {ic_str:<10} {mean_ic_str:<10} {rank_ic_str:<10} {ic_ir_str:<10} {ic_pos_str:<10} {long_short_str}")
    
    if best_window:
        print(f"\n★ 最优标签窗口: {best_window}日 (IC IR={best_ic_ir:.4f})")
    else:
        print("\n⚠️ 所有实验失败")
    
    _cwd = os.path.dirname(os.path.abspath(__file__))
    report_dir = os.path.join(_cwd, "reports")
    os.makedirs(report_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(report_dir, f"qlib_label_experiment_{ts}.json")
    report = {
        "timestamp": datetime.datetime.now().isoformat(),
        "description": "标签窗口实验 — 3/5/7/10日前瞻收益对比",
        "data_range": f"{DATA_START} ~ 最新",
        "train_end": TRAIN_END,
        "valid_end": VALID_END,
        "test_start": TEST_START,
        "best_window": best_window,
        "best_ic_ir": best_ic_ir,
        "results": results,
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n实验报告已保存: {report_path}")


if __name__ == "__main__":
    main()