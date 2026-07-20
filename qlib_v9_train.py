# -*- coding: utf-8 -*-
"""
QLib v9训练脚本 — v5配置+适度正则化+CSI50训练池
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
    ("600651", "SZ", "格力电器"), ("002415", "SZ", "海康威视"),
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
    print(f"训练池规模: {len(all_train_symbols)} 只股票")
    
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
    
    _cwd = os.path.dirname(os.path.abspath(__file__))
    features_path = os.path.join(_cwd, "config", f"top_{TOP_N}_features.json")
    
    if os.path.exists(features_path):
        with open(features_path, "r", encoding="utf-8") as f:
            top_features_config = json.load(f)
        top_features = top_features_config["top_features"]
        print(f"已加载Top {TOP_N}特征配置")
    else:
        print(f"警告: 未找到特征配置，使用全部特征")
        top_features = feature_names
    
    top_feature_indices = [i for i, name in enumerate(feature_names) if name in top_features]
    print(f"\n特征筛选: {len(feature_names)} → {len(top_feature_indices)}")
    
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
    valid_data = dataset.prepare("valid", col_set=["feature", "label"])
    test_data = dataset.prepare("test", col_set=["feature", "label"])
    
    gc.collect()
    
    X_train = train_data["feature"].values[:, top_feature_indices]
    y_train = train_data["label"].values.flatten()
    
    X_valid = valid_data["feature"].values[:, top_feature_indices]
    y_valid = valid_data["label"].values.flatten()
    
    X_test = test_data["feature"].values[:, top_feature_indices]
    y_test = test_data["label"].values.flatten()
    
    mask_train = np.isfinite(y_train)
    X_train = X_train[mask_train]
    y_train = y_train[mask_train]
    
    mask_valid = np.isfinite(y_valid)
    X_valid = X_valid[mask_valid]
    y_valid = y_valid[mask_valid]
    
    dtrain = xgb.DMatrix(X_train, label=y_train)
    dvalid = xgb.DMatrix(X_valid, label=y_valid)
    
    params = {
        "objective": "reg:squarederror",
        "max_depth": 6,
        "learning_rate": 0.01,
        "subsample": 0.7,
        "colsample_bytree": 0.7,
        "gamma": 0.15,
        "reg_alpha": 0.15,
        "reg_lambda": 0.3,
        "min_child_weight": 25,
        "verbosity": 0,
        "random_state": 42,
        "importance_type": "gain",
    }
    
    print("训练XGBoost模型(v9)...")
    print(f"正则化参数: gamma={params['gamma']}, reg_alpha={params['reg_alpha']}, reg_lambda={params['reg_lambda']}")
    print(f"树参数: max_depth={params['max_depth']}, min_child_weight={params['min_child_weight']}")
    
    model = xgb.train(
        params,
        dtrain,
        num_boost_round=2000,
        evals=[(dvalid, "valid")],
        early_stopping_rounds=200,
        verbose_eval=200,
    )
    
    gc.collect()
    
    y_pred_train = model.predict(xgb.DMatrix(X_train))
    y_pred_valid = model.predict(xgb.DMatrix(X_valid))
    y_pred_test = model.predict(xgb.DMatrix(X_test))
    
    mask_test = np.isfinite(y_test)
    y_test_clean = y_test[mask_test]
    y_pred_test_clean = y_pred_test[mask_test]
    
    ic_train = np.corrcoef(y_train, y_pred_train)[0, 1]
    ic_valid = np.corrcoef(y_valid, y_pred_valid)[0, 1]
    ic_test = np.corrcoef(y_test_clean, y_pred_test_clean)[0, 1] if len(y_test_clean) > 1 else np.nan
    
    rank_ic_train = np.corrcoef(pd.Series(y_train).rank(), pd.Series(y_pred_train).rank())[0, 1]
    rank_ic_valid = np.corrcoef(pd.Series(y_valid).rank(), pd.Series(y_pred_valid).rank())[0, 1]
    rank_ic_test = np.corrcoef(pd.Series(y_test_clean).rank(), pd.Series(y_pred_test_clean).rank())[0, 1] if len(y_test_clean) > 1 else np.nan
    
    print(f"\n{'='*70}")
    print(f"XGBoost v9 (Top{TOP_N}特征+适度正则化) 训练完成")
    print(f"{'='*70}")
    print(f"训练集 IC: {ic_train:.4f}")
    print(f"验证集 IC: {ic_valid:.4f}")
    print(f"测试集 IC: {ic_test:.4f}")
    print(f"训练集 Rank IC: {rank_ic_train:.4f}")
    print(f"验证集 Rank IC: {rank_ic_valid:.4f}")
    print(f"测试集 Rank IC: {rank_ic_test:.4f}")
    
    test_data_df = pd.DataFrame({
        "date": test_data["feature"].index.get_level_values(0).astype(str),
        "instrument": test_data["feature"].index.get_level_values(1),
        "signal": y_pred_test,
        "label": y_test,
    })
    
    daily_ics = []
    for date, group in test_data_df.groupby("date"):
        valid_group = group[np.isfinite(group["label"]) & np.isfinite(group["signal"])]
        if len(valid_group) >= 5:
            daily_ic = np.corrcoef(valid_group["signal"], valid_group["label"])[0, 1]
            daily_ics.append({"date": date, "ic": daily_ic})
    
    daily_ics_df = pd.DataFrame(daily_ics)
    avg_ic = daily_ics_df["ic"].mean() if len(daily_ics_df) > 0 else np.nan
    ic_std = daily_ics_df["ic"].std() if len(daily_ics_df) > 0 else np.nan
    ic_ir = avg_ic / ic_std if ic_std != 0 else np.nan
    ic_positive_ratio = (daily_ics_df["ic"] > 0).mean() if len(daily_ics_df) > 0 else np.nan
    
    print(f"\n日均 IC: {avg_ic:.4f}")
    print(f"IC 标准差: {ic_std:.4f}")
    print(f"IC IR: {ic_ir:.4f}")
    print(f"IC > 0 占比: {ic_positive_ratio:.4%}")
    
    latest_signals = {}
    latest_date = test_data_df["date"].max()
    latest_data = test_data_df[test_data_df["date"] == latest_date]
    
    latest_signals = dict(zip(latest_data["instrument"], latest_data["signal"].astype(float)))
    
    report = {
        "version": "v9",
        "model": "XGBoost_Top50_Moderate",
        "timestamp": datetime.datetime.now().isoformat(),
        "train_end": TRAIN_END,
        "valid_end": VALID_END,
        "test_start": TEST_START,
        "forward_days": FORWARD_DAYS,
        "num_features": len(top_feature_indices),
        "total_features": len(feature_names),
        "top_n": TOP_N,
        "n_stocks": len(all_train_symbols),
        "params": {
            "max_depth": params["max_depth"],
            "learning_rate": params["learning_rate"],
            "subsample": params["subsample"],
            "colsample_bytree": params["colsample_bytree"],
            "gamma": params["gamma"],
            "reg_alpha": params["reg_alpha"],
            "reg_lambda": params["reg_lambda"],
            "min_child_weight": params["min_child_weight"],
        },
        "metrics": {
            "ic_train": float(ic_train),
            "ic_valid": float(ic_valid),
            "ic_test": float(ic_test),
            "rank_ic_train": float(rank_ic_train),
            "rank_ic_valid": float(rank_ic_valid),
            "rank_ic_test": float(rank_ic_test),
            "avg_ic": float(avg_ic),
            "ic_std": float(ic_std),
            "ic_ir": float(ic_ir),
            "ic_positive_ratio": float(ic_positive_ratio),
            "test_samples": len(y_test_clean),
        },
        "signals": latest_signals,
        "top_features": top_features,
        "top_feature_indices": top_feature_indices,
    }
    
    reports_dir = os.path.join(_cwd, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    report_path = os.path.join(reports_dir, f"qlib_v9_train_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    print(f"\n报告已保存: {report_path}")
    
    return report


if __name__ == "__main__":
    main()