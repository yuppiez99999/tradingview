# -*- coding: utf-8 -*-
"""
QLib v3.5 优化版训练 — v3训练池 + v4参数优化

核心优化:
1. 保持v3成功的训练池规模（94只股票）
2. 降低 learning_rate 至 0.01（v3是0.05）
3. 增加 num_boost_round 至 2000（v3是1000）
4. early_stopping_rounds 增至 200（v3是100）
5. 5日收益标签继续使用
"""
import os
import sys
import json
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

CSI100_AUX = [
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
    ("600028", "SH", "中国石化"), ("000776", "SZ", "广发证券"),
    ("600999", "SH", "招商证券"), ("601628", "SH", "中国人寿"),
    ("601601", "SH", "中国太保"), ("600837", "SH", "海通证券"),
    ("002304", "SZ", "洋河股份"), ("600438", "SH", "通威股份"),
    ("002475", "SZ", "立讯精密"), ("300750", "SZ", "宁德时代"),
    ("601899", "SH", "紫金矿业"), ("600048", "SH", "保利发展"),
    ("000895", "SZ", "双汇发展"), ("601012", "SH", "隆基绿能"),
    ("600809", "SH", "山西汾酒"), ("600111", "SH", "北方稀土"),
    ("600346", "SH", "恒力石化"), ("601919", "SH", "中远海控"),
    ("000938", "SZ", "紫光国微"), ("002714", "SZ", "牧原股份"),
    ("002821", "SZ", "凯莱英"), ("300122", "SZ", "智飞生物"),
    ("300760", "SZ", "迈瑞医疗"), ("688008", "SH", "澜起科技"),
    ("688012", "SH", "中微公司"), ("688047", "SH", "赛微电子"),
    ("688050", "SH", "爱博医疗"), ("688126", "SH", "沪硅产业"),
    ("688169", "SH", "石头科技"), ("688237", "SH", "英科医疗"),
    ("688396", "SH", "华润微"), ("688599", "SH", "天合光能"),
    ("002594", "SZ", "比亚迪"), ("300059", "SZ", "东方财富"),
    ("300015", "SZ", "爱尔眼科"), ("600309", "SH", "万华化学"),
    ("600703", "SH", "三安光电"), ("600050", "SH", "中国联通"),
    ("601225", "SH", "陕西煤业"), ("300014", "SZ", "亿纬锂能"),
    ("300037", "SZ", "新宙邦"), ("300433", "SZ", "光环新网"),
    ("300724", "SZ", "捷佳伟创"), ("601818", "SH", "光大银行"),
    ("002008", "SZ", "大族激光"), ("600029", "SH", "南方航空"),
    ("600011", "SH", "华能国际"), ("000921", "SZ", "海信家电"),
    ("002001", "SZ", "新和成"), ("002129", "SZ", "中环股份"),
    ("300003", "SZ", "乐普医疗"), ("300142", "SZ", "沃森生物"),
    ("300347", "SZ", "泰格医药"), ("300498", "SZ", "温氏股份"),
    ("300595", "SZ", "欧普康视"), ("300601", "SZ", "康泰生物"),
    ("300769", "SZ", "德方纳米"), ("600547", "SH", "山东黄金"),
    ("600516", "SH", "方大炭素"), ("601006", "SH", "大秦铁路"),
    ("601898", "SH", "中煤能源"), ("601998", "SH", "中信银行"),
]

TRAIN_END = "2023-12-31"
VALID_END = "2024-06-30"
TEST_START = "2024-07-01"

FORWARD_DAYS = 5


def main():
    print(f"\n{'='*70}")
    print(f"QLib v3.5 优化版训练 — v3训练池 + v4参数")
    print(f"{'='*70}\n")

    USE_EXISTING_DATA = True

    if USE_EXISTING_DATA:
        print("[步骤1-3] 使用已有的 QLib 数据文件\n")
        features_dir = os.path.join(QLIB_DATA_DIR, "features")
        existing_symbols = [d for d in os.listdir(features_dir) if os.path.isdir(os.path.join(features_dir, d))]
        print(f"  已有的特征文件: {len(existing_symbols)} 只股票")
        
        cal_path = os.path.join(QLIB_DATA_DIR, "calendars", "day.txt")
        with open(cal_path, "r") as f:
            calendar_dates = pd.to_datetime([line.strip() for line in f if line.strip()])
        cal_end = calendar_dates[-1].strftime("%Y-%m-%d")
        print(f"  日历范围: {calendar_dates[0].date()} ~ {cal_end}\n")
        
        all_data = {s: None for s in existing_symbols}

    print("[步骤4] QLib 模型训练\n")

    _qlib_source = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qlib")
    _cwd = os.path.dirname(os.path.abspath(__file__))
    sys.path = [p for p in sys.path if p != _qlib_source and p != _cwd and os.path.abspath(p) != _qlib_source]

    os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"

    import qlib
    qlib.init(provider_uri=QLIB_DATA_DIR, region="cn")
    print(f"  [QLib] {qlib.__version__} 初始化完成")

    from qlib.config import C
    C.joblib_backend = "sequential"

    from qlib.contrib.model.gbdt import LGBModel
    from qlib.contrib.data.handler import Alpha158
    from qlib.utils import init_instance_by_config

    portfolio_symbols = [f"{exch}{code}" for code, exch, name in PORTFOLIO_STOCKS]
    aux_symbols = [f"{exch}{code}" for code, exch, name in CSI100_AUX
                   if f"{exch}{code}" not in portfolio_symbols]
    all_train_symbols = portfolio_symbols + aux_symbols

    all_train_symbols = [s for s in all_train_symbols if s.lower() in all_data]
    portfolio_symbols = [s for s in portfolio_symbols if s.lower() in all_data]

    stock_names = {f"{exch}{code}": name for code, exch, name in PORTFOLIO_STOCKS + CSI100_AUX}

    DATA_START = "2015-01-01"
    DATA_END = cal_end

    print(f"\n{'='*70}")
    print(f"QLib v3.5 横截面模型训练 — {len(all_train_symbols)} 只股票")
    print(f"{'='*70}")
    print(f"训练池: {len(portfolio_symbols)} 持仓 + {len(all_train_symbols) - len(portfolio_symbols)} 辅助")
    print(f"数据范围: {DATA_START} ~ {DATA_END}")
    print(f"训练期:   {DATA_START} ~ {TRAIN_END}")
    print(f"验证期:   {TRAIN_END} ~ {VALID_END}")
    print(f"测试期:   {TEST_START} ~ {DATA_END}")
    print(f"标签:     {FORWARD_DAYS}日前瞻收益")
    print(f"学习率:   0.01, 轮数: 2000, early_stopping: 200")
    print(f"{'='*70}\n")

    label_expr = f"Ref($close, -{FORWARD_DAYS}) / Ref($close, -1) - 1"

    data_handler_config = {
        "start_time": DATA_START,
        "end_time": DATA_END,
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

    train_dates = train_data.index.get_level_values(0).unique().sort_values()
    test_dates = test_data.index.get_level_values(0).unique().sort_values()
    print(f"      训练集: {len(train_data)} 行, {n_stocks_train} 股, {len(train_data.columns)-1} 特征")
    print(f"      测试集: {len(test_data)} 行")
    print(f"      训练时间: {train_dates[0].date()} ~ {train_dates[-1].date()}")
    print(f"      测试时间: {test_dates[0].date()} ~ {test_dates[-1].date()}")
    print(f"      时间分割验证: {'PASS' if train_dates[-1] < test_dates[0] else 'FAIL'}")

    print("[3/4] 训练 LightGBM (v3.5 优化参数)...")
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
        verbose=100,
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

    print(f"{'='*70}")
    print(f"模型训练结果 (v3.5 优化版)")
    print(f"{'='*70}")
    print(f"训练股票数:        {n_stocks_train}")
    print(f"训练样本:          {len(train_data)}")
    print(f"测试样本:          {len(test_data)}")
    print(f"数据范围:          {DATA_START} ~ {DATA_END}")
    print(f"标签:              {FORWARD_DAYS}日前瞻收益")
    print(f"整体 IC:          {overall_ic:.4f}")
    print(f"日均 IC:          {mean_ic:.4f}")
    print(f"日均 Rank IC:     {mean_rank_ic:.4f}")
    print(f"IC IR:            {ic_ir:.4f}")
    print(f"IC > 0 占比:      {(ic_by_date > 0).mean():.2%}")

    print(f"\n{'='*70}")
    print(f"持仓标的预测信号")
    print(f"{'='*70}")
    print(f"{'代码':<12} {'名称':<10} {'最新信号':>10} {'方向':>6} {'均值':>10} {'排名':>6}")
    print(f"{'─'*70}")

    latest_preds = pred_series.groupby(level=1).last()
    ranked = latest_preds.rank(ascending=False)

    results = []
    for symbol in portfolio_symbols:
        name = stock_names.get(symbol, symbol)
        for cv in [symbol, symbol.lower()]:
            try:
                stock_pred = pred_series.xs(cv, level=1)
                if len(stock_pred) > 0:
                    latest = stock_pred.iloc[-1]
                    avg = stock_pred.mean()
                    rank = int(ranked.get(cv, ranked.get(cv.lower(), 0)))
                    direction = "看多" if latest > 0 else ("看空" if latest < 0 else "中性")
                    print(f"{symbol:<12} {name:<10} {latest:>10.6f} {direction:>6} {avg:>10.6f} {rank:>6}/{len(latest_preds)}")
                    results.append({
                        "code": symbol, "name": name,
                        "latest_signal": round(float(latest), 6),
                        "qlib_avg_signal": round(float(avg), 6),
                        "rank": rank,
                        "total": len(latest_preds),
                        "direction": direction,
                    })
                    break
            except KeyError:
                continue

    print(f"{'─'*70}")
    long_count = sum(1 for r in results if r["direction"] == "看多")
    short_count = sum(1 for r in results if r["direction"] == "看空")
    print(f"信号分布: 看多 {long_count} / 看空 {short_count} / 中性 {len(results)-long_count-short_count}")
    print(f"{'='*70}\n")

    report_dir = os.path.join(_cwd, "reports")
    os.makedirs(report_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(report_dir, f"qlib_v3_5_train_{ts}.json")
    report = {
        "timestamp": datetime.datetime.now().isoformat(),
        "qlib_version": qlib.__version__,
        "model": f"LightGBM (Alpha158, v3.5: CSI100 + {FORWARD_DAYS}日收益 + lr=0.01)",
        "improvements": [
            f"训练池: {len(all_train_symbols)}只股票 (v3基础上保持)",
            f"标签保持 {FORWARD_DAYS}日前瞻收益",
            "降低 learning_rate 至 0.01 (v3是0.05)",
            "增加 num_boost_round 至 2000 (v3是1000)",
            "增加 early_stopping_rounds 至 200 (v3是100)",
        ],
        "training_pool_size": len(all_train_symbols),
        "portfolio_size": len(portfolio_symbols),
        "data_range": f"{DATA_START} ~ {DATA_END}",
        "train_samples": len(train_data),
        "test_samples": len(test_data),
        "n_stocks_train": int(n_stocks_train),
        "forward_days": FORWARD_DAYS,
        "overall_ic": round(float(overall_ic), 4) if not np.isnan(overall_ic) else None,
        "mean_daily_ic": round(float(mean_ic), 4),
        "mean_daily_rank_ic": round(float(mean_rank_ic), 4),
        "ic_ir": round(float(ic_ir), 4),
        "ic_positive_ratio": round(float((ic_by_date > 0).mean()), 4),
        "signal_distribution": {"long": long_count, "short": short_count, "neutral": len(results)-long_count-short_count},
        "stock_signals": results,
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    print(f"报告已保存: {report_path}")


if __name__ == "__main__":
    main()