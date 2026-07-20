# -*- coding: utf-8 -*-
"""
v5训练脚本 — 5日标签 + XGBoost直接训练
"""
import os
import sys
import json
import gc
import datetime
import numpy as np
import pandas as pd
import xgboost as xgb

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
FORWARD_DAYS = 5


def main():
    gc.collect()
    
    available_symbols = set()
    features_dir = os.path.join(QLIB_DATA_DIR, "features")
    if os.path.exists(features_dir):
        available_symbols = {d for d in os.listdir(features_dir) if os.path.isdir(os.path.join(features_dir, d))}
    
    cal_path = os.path.join(QLIB_DATA_DIR, "calendars", "day.txt")
    with open(cal_path, "r") as f:
        calendar_dates = pd.to_datetime([line.strip() for line in f if line.strip()])
    cal_end = calendar_dates[-1].strftime("%Y-%m-%d")
    
    portfolio_symbols = [f"{exch}{code}" for code, exch, name in PORTFOLIO_STOCKS]
    aux_symbols = [f"{exch}{code}" for code, exch, name in CSI50_AUX
                   if f"{exch}{code}" not in portfolio_symbols]
    all_train_symbols = portfolio_symbols + aux_symbols
    
    all_train_symbols = [s for s in all_train_symbols if s.lower() in available_symbols]
    portfolio_symbols = [s for s in portfolio_symbols if s.lower() in available_symbols]
    
    print(f"可用训练股票数: {len(all_train_symbols)}")
    print(f"持仓股票数: {len(portfolio_symbols)}")
    
    os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"
    
    import qlib
    qlib.init(provider_uri=QLIB_DATA_DIR, region="cn")
    
    from qlib.config import C
    C.joblib_backend = "sequential"
    
    from qlib.contrib.model.gbdt import LGBModel
    from qlib.contrib.data.handler import Alpha158
    from qlib.utils import init_instance_by_config
    
    label_expr = f"Ref($close, -{FORWARD_DAYS}) / Ref($close, -1) - 1"
    
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
    
    print(f"训练样本: {len(train_data)}, 测试样本: {len(test_data)}")
    print(f"特征数: {len(train_data['feature'].columns)}")
    
    gc.collect()
    
    X_train = train_data["feature"].values
    y_train = train_data["label"].values.flatten()
    X_valid = dataset.prepare("valid", col_set=["feature"])["feature"].values
    y_valid = dataset.prepare("valid", col_set=["label"])["label"].values.flatten()
    X_test = test_data["feature"].values
    y_test = test_data["label"].values.flatten()
    test_index = test_data["label"].index
    
    mask_train = np.isfinite(y_train)
    X_train = X_train[mask_train]
    y_train = y_train[mask_train]
    
    mask_valid = np.isfinite(y_valid)
    X_valid = X_valid[mask_valid]
    y_valid = y_valid[mask_valid]
    
    mask_test = np.isfinite(y_test)
    X_test = X_test[mask_test]
    y_test = y_test[mask_test]
    test_index = test_index[mask_test]
    
    dtrain = xgb.DMatrix(X_train, label=y_train)
    dvalid = xgb.DMatrix(X_valid, label=y_valid)
    dtest = xgb.DMatrix(X_test, label=y_test)
    
    params = {
        "objective": "reg:squarederror",
        "max_depth": 6,
        "learning_rate": 0.01,
        "subsample": 0.7,
        "colsample_bytree": 0.7,
        "gamma": 0.1,
        "reg_alpha": 0.1,
        "reg_lambda": 0.1,
        "min_child_weight": 20,
        "verbosity": 0,
        "random_state": 42,
    }
    
    print("训练XGBoost...")
    model = xgb.train(
        params,
        dtrain,
        num_boost_round=2000,
        evals=[(dvalid, "valid")],
        early_stopping_rounds=200,
        verbose_eval=50,
    )
    
    gc.collect()
    
    pred = model.predict(dtest)
    pred_series = pd.Series(pred, index=test_index)
    
    test_label = test_data["label"]
    label_series = test_label.iloc[:, 0] if isinstance(test_label, pd.DataFrame) else test_label
    label_series = label_series.loc[test_index]
    
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
    
    portfolio_preds = {}
    for code, exch, name in PORTFOLIO_STOCKS:
        symbol = f"{exch}{code}"
        if symbol in latest_preds.index:
            portfolio_preds[code] = {
                "name": name,
                "symbol": symbol,
                "signal": float(latest_preds[symbol]),
                "direction": "看多" if latest_preds[symbol] > 0 else "看空",
            }
        else:
            portfolio_preds[code] = {
                "name": name,
                "symbol": symbol,
                "signal": None,
                "direction": "无信号",
            }
    
    long_count = sum(1 for _, v in portfolio_preds.items() if v["signal"] and v["signal"] > 0)
    short_count = sum(1 for _, v in portfolio_preds.items() if v["signal"] and v["signal"] <= 0)
    
    report = {
        "version": "v5",
        "model": "XGBoost",
        "label_days": FORWARD_DAYS,
        "timestamp": datetime.datetime.now().isoformat(),
        "data_range": f"{DATA_START} ~ {cal_end}",
        "train_end": TRAIN_END,
        "valid_end": VALID_END,
        "test_start": TEST_START,
        "n_stocks": train_data.index.get_level_values(1).nunique(),
        "train_samples": len(train_data),
        "test_samples": len(test_data),
        "n_features": len(train_data["feature"].columns),
        "ic": round(float(overall_ic), 4) if not np.isnan(overall_ic) else None,
        "mean_daily_ic": round(float(mean_ic), 4),
        "mean_daily_rank_ic": round(float(mean_rank_ic), 4),
        "ic_ir": round(float(ic_ir), 4),
        "ic_positive_ratio": round(float((ic_by_date > 0).mean()), 4),
        "long_count": long_count,
        "short_count": short_count,
        "portfolio_signals": portfolio_preds,
    }
    
    print(f"\n{'='*70}")
    print(f"v5 XGBoost训练报告")
    print(f"{'='*70}")
    print(f"模型: XGBoost, 标签窗口: {FORWARD_DAYS}日")
    print(f"训练股票: {report['n_stocks']}, 特征数: {report['n_features']}")
    print(f"\nIC指标:")
    print(f"  整体IC: {report['ic']:.4f}")
    print(f"  日均IC: {report['mean_daily_ic']:.4f}")
    print(f"  日均RankIC: {report['mean_daily_rank_ic']:.4f}")
    print(f"  IC IR: {report['ic_ir']:.4f}")
    print(f"  IC>0占比: {report['ic_positive_ratio']:.1%}")
    print(f"\n信号分布:")
    print(f"  看多: {report['long_count']}只")
    print(f"  看空: {report['short_count']}只")
    print(f"\n持仓股票信号:")
    print(f"{'代码':<10} {'名称':<12} {'信号':<10} {'方向'}")
    print(f"{'─'*45}")
    for code, info in portfolio_preds.items():
        signal_str = f"{info['signal']:.4f}" if info['signal'] is not None else "None"
        print(f"{code:<10} {info['name']:<12} {signal_str:<10} {info['direction']}")
    
    _cwd = os.path.dirname(os.path.abspath(__file__))
    report_dir = os.path.join(_cwd, "reports")
    os.makedirs(report_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(report_dir, f"qlib_v5_train_{ts}.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n报告已保存: {report_path}")
    
    return report


if __name__ == "__main__":
    main()