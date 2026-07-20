# -*- coding: utf-8 -*-
"""
特征重要性分析 — 基于XGBoost筛选Top50特征
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
TOP_N = 50


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
    
    os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"
    
    import qlib
    qlib.init(provider_uri=QLIB_DATA_DIR, region="cn")
    
    from qlib.config import C
    C.joblib_backend = "sequential"
    
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
    feature_config = handler.get_feature_config()
    if isinstance(feature_config, dict):
        features_raw = feature_config.get("feature", [])
    else:
        features_raw = feature_config[0] if feature_config else []
    feature_names = []
    for f in features_raw:
        if isinstance(f, str):
            feature_names.append(f)
        elif isinstance(f, (list, tuple)):
            feature_names.append(str(f[0]) if f else "")
        else:
            feature_names.append(str(f))
    
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
    
    gc.collect()
    
    X_train = train_data["feature"].values
    y_train = train_data["label"].values.flatten()
    
    mask_train = np.isfinite(y_train)
    X_train = X_train[mask_train]
    y_train = y_train[mask_train]
    
    dtrain = xgb.DMatrix(X_train, label=y_train)
    
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
        "importance_type": "gain",
    }
    
    print("训练XGBoost获取特征重要性...")
    model = xgb.train(
        params,
        dtrain,
        num_boost_round=1000,
        verbose_eval=100,
    )
    
    importances = model.get_score(importance_type="gain")
    importance_list = [(k, v) for k, v in importances.items()]
    importance_list.sort(key=lambda x: x[1], reverse=True)
    
    total_gain = sum(v for _, v in importance_list)
    cumulative_gain = 0
    top_features = []
    top_indices = []
    
    print(f"\n{'='*70}")
    print(f"特征重要性分析报告")
    print(f"{'='*70}")
    print(f"总特征数: {len(feature_names)}")
    print(f"Top {TOP_N}特征累计增益占比分析:\n")
    
    print(f"{'排名':<6} {'特征名':<45} {'增益':>12} {'占比':>8} {'累计':>8}")
    print(f"{'─'*80}")
    
    for i, (feature_idx_str, gain) in enumerate(importance_list[:TOP_N], 1):
        feature_idx = int(feature_idx_str[1:])
        feature_name = feature_names[feature_idx]
        cumulative_gain += gain
        print(f"{i:<6} {feature_name:<45} {gain:>12.4f} {gain/total_gain*100:>7.1f}% {cumulative_gain/total_gain*100:>7.1f}%")
        top_features.append(feature_name)
        top_indices.append(feature_idx)
    
    print(f"\nTop {TOP_N}特征累计增益占比: {cumulative_gain/total_gain*100:.1f}%")
    
    _cwd = os.path.dirname(os.path.abspath(__file__))
    features_dir = os.path.join(_cwd, "config")
    os.makedirs(features_dir, exist_ok=True)
    
    features_config = {
        "timestamp": datetime.datetime.now().isoformat(),
        "total_features": len(feature_names),
        "top_n": TOP_N,
        "total_gain": total_gain,
        "cumulative_gain": cumulative_gain,
        "cumulative_ratio": cumulative_gain / total_gain,
        "top_features": top_features,
        "top_indices": top_indices,
        "all_importance": [{feature_names[int(k[1:])]: v} for k, v in importance_list],
    }
    
    features_path = os.path.join(features_dir, f"top_{TOP_N}_features.json")
    with open(features_path, "w", encoding="utf-8") as f:
        json.dump(features_config, f, ensure_ascii=False, indent=2)
    
    print(f"\n特征配置已保存: {features_path}")
    
    return features_config


if __name__ == "__main__":
    main()