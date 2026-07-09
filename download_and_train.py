# -*- coding: utf-8 -*-
"""
Wind MCP 数据下载 + QLib 格式转换 + 模型训练

1. 用 Wind MCP 下载缺失股票的 OHLCV 数据
2. 转换为 QLib bin 格式
3. 更新 instruments 文件
4. 训练 QLib LightGBM 模型
"""
import os
import sys
import json
import struct
import subprocess
import datetime
import numpy as np
import pandas as pd

# ============================================================
# 配置
# ============================================================
QLIB_DATA_DIR = r"E:\各种PY程序\28-终极量化交易系统7.1\qlib_data\cn_data"
WIND_MCP_DIR = r"C:\Users\Administrator\.agents\skills\wind-mcp-skill"
ENV_FILE = r"E:\各种PY程序\11_量化策略\.env"

# 缺失股票: (代码, Wind代码, 名称, 上市日期)
MISSING_STOCKS = [
    ("688041", "688041.SH", "海光信息", "2022-08-12"),
    ("688981", "688981.SH", "中芯国际", "2020-07-16"),
    ("688017", "688017.SH", "绿的谐波", "2020-08-18"),
]

# 下载日期范围
BEGIN_DATE = "20200101"
END_DATE = "20260708"

# 持仓标的 (QLib 格式)
ALL_PORTFOLIO_STOCKS = [
    ("SZ002371", "北方华创"),
    ("SZ300308", "中际旭创"),
    ("SZ000425", "徐工机械"),
    ("SH600276", "恒瑞医药"),
    ("SH600900", "长江电力"),
    ("SH600036", "招商银行"),
    ("SH601088", "中国神华"),
    ("SZ300274", "阳光电源"),
    ("SH603019", "中科曙光"),
    ("SH600089", "特变电工"),
    ("SH600019", "宝钢股份"),
    ("SH600219", "南山铝业"),
    ("SH688041", "海光信息"),  # 待下载
    ("SH688981", "中芯国际"),  # 待下载
    ("SH688017", "绿的谐波"),  # 待下载
]

DATA_START = "2015-01-01"
DATA_END = "2020-09-25"  # 已有数据截止日期
TRAIN_END = "2018-12-31"
VALID_END = "2019-06-30"
TEST_START = "2019-07-01"


def load_wind_api_key():
    """从 .env 文件加载 WIND_API_KEY"""
    with open(ENV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("WIND_API_KEY="):
                key = line.split("=", 1)[1].strip()
                if key and "your_" not in key.lower():
                    return key
    return None


def wind_mcp_fetch_kline(code: str, wind_code: str, begin_date: str, end_date: str, api_key: str):
    """调用 Wind MCP CLI 获取日K线数据"""
    params = json.dumps({
        "windcode": wind_code,
        "begin_date": begin_date,
        "end_date": end_date,
        "period": "10",   # 日K
        "aftime": "0",    # 前复权
    }, ensure_ascii=False)

    env = os.environ.copy()
    env["WIND_API_KEY"] = api_key

    print(f"  [Wind] 下载 {code} ({wind_code}) {begin_date}~{end_date}...")
    try:
        result = subprocess.run(
            ["node", "scripts/cli.mjs", "call", "stock_data", "get_stock_kline", params],
            cwd=WIND_MCP_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            env=env,
        )
        if result.returncode != 0 or not result.stdout.strip():
            print(f"  [Wind] {code} 调用失败: returncode={result.returncode}")
            if result.stderr:
                print(f"  [Wind] stderr: {result.stderr[:200]}")
            return None

        stdout = result.stdout.strip()
        # 过滤 CLIXML 噪声
        if "#< CLIXML" in stdout:
            stdout = stdout.split("\n")[0]

        outer = json.loads(stdout)
        if outer.get("isError"):
            print(f"  [Wind] {code} 返回错误")
            return None

        text = (outer.get("content", [{}])[0] or {}).get("text", "")
        if not text:
            print(f"  [Wind] {code} 无数据")
            return None

        inner = json.loads(text)
        if inner.get("error"):
            print(f"  [Wind] {code} 错误: {inner['error']}")
            return None

        data = inner.get("data")
        if not data:
            print(f"  [Wind] {code} data 为空")
            return None

        columns = [c["name"] for c in data.get("columns", [])]
        rows = data.get("rows", [])
        if not rows:
            print(f"  [Wind] {code} rows 为空")
            return None

        col_map = {c: i for i, c in enumerate(columns)}
        records = []
        for row in rows:
            # 跳过含 INVALID 的行
            vals = {}
            valid = True
            for field, idx_key in [("open","OPEN"),("close","MATCH"),("high","HIGH"),("low","LOW"),("volume","VOLUME")]:
                raw = row[col_map[idx_key]]
                if isinstance(raw, str) and "INVALID" in raw.upper():
                    valid = False
                    break
                vals[field] = float(raw)
            if not valid:
                continue
            vals["date"] = row[col_map.get("TIME", 0)][:10] if row[col_map.get("TIME", 0)] else None
            records.append(vals)
        df = pd.DataFrame(records)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        df = df.dropna()
        print(f"  [Wind] {code} 下载成功: {len(df)} 条")
        return df

    except subprocess.TimeoutExpired:
        print(f"  [Wind] {code} 超时")
        return None
    except Exception as e:
        print(f"  [Wind] {code} 异常: {e}")
        return None


def write_qlib_bin(df: pd.DataFrame, symbol_lower: str, qlib_dir: str):
    """将 DataFrame 写入 QLib bin 格式"""
    # QLib bin 格式: 前4字节=起始日历索引(int32), 之后是 float32 数据数组
    feature_dir = os.path.join(qlib_dir, "features", symbol_lower)
    os.makedirs(feature_dir, exist_ok=True)

    # 读取日历
    calendar_path = os.path.join(qlib_dir, "calendars", "day.txt")
    with open(calendar_path, "r") as f:
        calendar = [line.strip() for line in f if line.strip()]

    cal_dates = pd.to_datetime(calendar)
    cal_series = pd.Series(range(len(cal_dates)), index=cal_dates)

    # 对齐数据到日历
    df_aligned = df.reindex(cal_dates)
    start_idx = None
    for i, d in enumerate(cal_dates):
        if d in df.index:
            start_idx = i
            break

    if start_idx is None:
        print(f"  [QLib] {symbol_lower} 日期不在日历范围内")
        return False

    # 写入各字段的 bin 文件
    fields = {
        "open": df["open"],
        "high": df["high"],
        "low": df["low"],
        "close": df["close"],
        "volume": df["volume"],
    }

    # 计算复权因子 (简单: 1.0)
    factor = pd.Series(1.0, index=df.index)

    # change (涨跌幅)
    change = df["close"].pct_change().fillna(0) * 100

    # 对齐到日历
    for field_name, series in fields.items():
        aligned = series.reindex(cal_dates).values
        aligned = np.where(np.isnan(aligned), 0, aligned).astype(np.float32)

        bin_path = os.path.join(feature_dir, f"{field_name}.day.bin")
        with open(bin_path, "wb") as f:
            # 写入起始索引 (int32)
            f.write(struct.pack("<I", start_idx))
            # 写入数据 (float32)
            aligned.tofile(f)
        print(f"  [QLib] 写入 {symbol_lower}/{field_name}.day.bin ({len(aligned)} 条)")

    # 写入 factor 和 change
    for field_name, series in [("factor", factor), ("change", change)]:
        aligned = series.reindex(cal_dates).values
        aligned = np.where(np.isnan(aligned), 0, aligned).astype(np.float32)
        bin_path = os.path.join(feature_dir, f"{field_name}.day.bin")
        with open(bin_path, "wb") as f:
            f.write(struct.pack("<I", start_idx))
            aligned.tofile(f)

    return True


def update_instruments(qlib_dir: str, new_stocks: list):
    """更新 instruments/all.txt 文件"""
    all_txt_path = os.path.join(qlib_dir, "instruments", "all.txt")
    with open(all_txt_path, "r", encoding="utf-8") as f:
        existing = set(line.strip().split("\t")[0] for line in f if line.strip())

    with open(all_txt_path, "a", encoding="utf-8") as f:
        for code, wind_code, name, ipo_date in new_stocks:
            qlib_code = f"SH{code}" if code.startswith("688") else f"SZ{code}"
            if qlib_code not in existing:
                end_date = "2020-09-25"  # QLib 数据截止日
                f.write(f"{qlib_code}\t{ipo_date}\t{end_date}\n")
                print(f"  [QLib] instruments/all.txt 添加 {qlib_code}")


def main():
    print(f"\n{'='*70}")
    print(f"Wind MCP 数据下载 + QLib 训练")
    print(f"{'='*70}\n")

    # ============================================================
    # 步骤 1: 下载缺失股票数据
    # ============================================================
    api_key = load_wind_api_key()
    if not api_key:
        print("[错误] 未找到 WIND_API_KEY")
        return

    print(f"[步骤1] Wind MCP 下载缺失数据 ({len(MISSING_STOCKS)} 只)\n")

    downloaded = []
    for code, wind_code, name, ipo_date in MISSING_STOCKS:
        df = wind_mcp_fetch_kline(code, wind_code, BEGIN_DATE, END_DATE, api_key)
        if df is not None and len(df) > 0:
            # 转换为 QLib bin 格式
            symbol_lower = code.lower()
            symbol_qlib = f"sh{code}" if code.startswith("688") else f"sz{code}"
            success = write_qlib_bin(df, symbol_qlib, QLIB_DATA_DIR)
            if success:
                downloaded.append((code, wind_code, name, ipo_date))
                print(f"  [完成] {code} {name}\n")
        else:
            print(f"  [跳过] {code} {name}\n")

    # 更新 instruments 文件
    if downloaded:
        update_instruments(QLIB_DATA_DIR, downloaded)
        print(f"\n共下载 {len(downloaded)} 只股票数据\n")

    # ============================================================
    # 步骤 2: QLib 训练 (所有持仓标的)
    # ============================================================
    print(f"[步骤2] QLib 模型训练\n")

    # 避免本地 qlib/ 源码遮蔽
    _qlib_source = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qlib")
    _cwd = os.path.dirname(os.path.abspath(__file__))
    sys.path = [p for p in sys.path if p != _qlib_source and p != _cwd and os.path.abspath(p) != _qlib_source]

    os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"

    import qlib
    qlib.init(provider_uri=QLIB_DATA_DIR, region="cn")
    print(f"[QLib] {qlib.__version__} 初始化完成")

    from qlib.config import C
    C.joblib_backend = "sequential"

    from qlib.contrib.model.gbdt import LGBModel
    from qlib.contrib.data.handler import Alpha158
    from qlib.utils import init_instance_by_config

    # 训练标的 (所有持仓)
    PORTFOLIO_STOCKS = [c for c, n in ALL_PORTFOLIO_STOCKS]
    STOCK_NAMES = {c: n for c, n in ALL_PORTFOLIO_STOCKS}

    print(f"\n{'='*70}")
    print(f"QLib 横截面模型训练 — 持仓标的 ({len(PORTFOLIO_STOCKS)}只)")
    print(f"{'='*70}")
    print(f"训练池: {', '.join(STOCK_NAMES.values())}")
    print(f"数据范围: {DATA_START} ~ {DATA_END}")
    print(f"训练期:   {DATA_START} ~ {TRAIN_END}")
    print(f"验证期:   {TRAIN_END} ~ {VALID_END}")
    print(f"测试期:   {TEST_START} ~ {DATA_END}")
    print(f"{'='*70}\n")

    # Alpha158
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
    print(f"      训练集: {len(train_data)} 行, {n_stocks_train} 股, {len(train_data.columns)-1} 特征")
    print(f"      测试集: {len(test_data)} 行")

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
    print(f"持仓标的预测信号")
    print(f"{'='*70}")
    print(f"{'代码':<12} {'名称':<10} {'最新信号':>10} {'方向':>6} {'均值':>10}")
    print(f"{'─'*70}")

    results = []
    for code in PORTFOLIO_STOCKS:
        name = STOCK_NAMES.get(code, code)
        for cv in [code, code.lower()]:
            try:
                stock_pred = pred_series.xs(cv, level=1)
                if len(stock_pred) > 0:
                    latest = stock_pred.iloc[-1]
                    avg = stock_pred.mean()
                    direction = "看多" if latest > 0 else ("看空" if latest < 0 else "中性")
                    print(f"{code:<12} {name:<10} {latest:>10.6f} {direction:>6} {avg:>10.6f}")
                    results.append({
                        "code": code, "name": name,
                        "latest_signal": round(float(latest), 6),
                        "avg_signal": round(float(avg), 6),
                        "direction": direction,
                    })
                    break
            except KeyError:
                continue

    print(f"{'─'*70}")
    long_count = sum(1 for r in results if r["direction"] == "看多")
    short_count = sum(1 for r in results if r["direction"] == "看空")
    neutral_count = sum(1 for r in results if r["direction"] == "中性")
    print(f"信号分布: 看多 {long_count} / 看空 {short_count} / 中性 {neutral_count}")
    print(f"{'='*70}\n")

    # 保存报告
    report_dir = os.path.join(_cwd, "reports")
    os.makedirs(report_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(report_dir, f"qlib_wind_train_{ts}.json")
    report = {
        "timestamp": datetime.datetime.now().isoformat(),
        "qlib_version": qlib.__version__,
        "model": "LightGBM (Alpha158, Portfolio + Wind Data)",
        "downloaded_stocks": [d[0] for d in downloaded],
        "training_pool": [f"{c} ({STOCK_NAMES[c]})" for c in PORTFOLIO_STOCKS],
        "data_range": f"{DATA_START} ~ {DATA_END}",
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
