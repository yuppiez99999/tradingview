"""
QLib 改进版训练 — 扩展日历 + 全量数据 + 辅助训练池

改进点:
1. 扩展 QLib 日历到 2026 (用 Wind MCP 下载的实际交易日期)
2. 下载全部 15 只持仓标的 2015-2026 完整数据
3. 重写所有 bin 文件对齐扩展后的日历
4. 添加 csi50 辅助训练池提升横截面区分度
5. 优化模型参数 (num_leaves=128, boost_round=500)
"""
import datetime
import json
import os
import struct
import subprocess
import sys

# ============================================================
# 配置
# ============================================================
# P0-5 FIX: 改用环境变量 + 项目相对路径, 使脚本可在 CI / 团队成员 / 不同机器上运行
# 原代码硬编码了 7.1 版本项目路径, 不可移植、不可复现
from pathlib import Path as _Path

import numpy as np
import pandas as pd

_PROJECT_ROOT = _Path(__file__).resolve().parents[2]

QLIB_DATA_DIR = os.environ.get(
    "QLIB_DATA_DIR",
    str(_PROJECT_ROOT / "qlib_data" / "cn_data"),
)
WIND_MCP_DIR = os.environ.get(
    "WIND_MCP_DIR",
    str(_Path.home() / ".agents" / "skills" / "wind-mcp-skill"),
)
ENV_FILE = os.environ.get(
    "QUANT_ENV_FILE",
    str(_PROJECT_ROOT / ".env"),
)

# 全部 15 只持仓标的
PORTFOLIO_STOCKS = [
    ("002371", "SZ", "北方华创"),
    ("300308", "SZ", "中际旭创"),
    ("000425", "SZ", "徐工机械"),
    ("600276", "SH", "恒瑞医药"),
    ("600900", "SH", "长江电力"),
    ("600036", "SH", "招商银行"),
    ("601088", "SH", "中国神华"),
    ("300274", "SZ", "阳光电源"),
    ("603019", "SH", "中科曙光"),
    ("600089", "SH", "特变电工"),
    ("600019", "SH", "宝钢股份"),
    ("600219", "SH", "南山铝业"),
    ("688041", "SH", "海光信息"),
    ("688981", "SH", "中芯国际"),
    ("688017", "SH", "绿的谐波"),
]

# csi50 辅助训练池 (权重前 35 只, 排除已持仓)
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
    ("600000", "SH", "浦发银行"), ("601398", "SH", "工商银行"),
    ("601939", "SH", "建设银行"), ("601288", "SH", "农业银行"),
    ("600028", "SH", "中国石化"), ("601857", "SH", "中国石油"),
    ("000776", "SZ", "广发证券"), ("600999", "SH", "招商证券"),
    ("600016", "SH", "民生银行"), ("601628", "SH", "中国人寿"),
    ("601601", "SH", "中国太保"), ("600837", "SH", "海通证券"),
    ("000063", "SZ", "中兴通讯"), ("002304", "SZ", "洋河股份"),
    ("600276", "SH", "恒瑞医药"), ("002475", "SZ", "立讯精密"),
    ("600438", "SH", "通威股份"),
]

# 下载范围
BEGIN_DATE = "20150101"
END_DATE = "20260708"

# 训练时间分割
TRAIN_END = "2023-12-31"
VALID_END = "2024-06-30"
TEST_START = "2024-07-01"


def load_wind_api_key():
    with open(ENV_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("WIND_API_KEY="):
                key = line.split("=", 1)[1].strip()
                if key and "your_" not in key.lower():
                    return key
    return None


def wind_mcp_fetch_kline(code, exchange, begin_date, end_date, api_key):
    """从 Wind MCP 下载日K线"""
    wind_code = f"{code}.{exchange}"
    params = json.dumps({
        "windcode": wind_code,
        "begin_date": begin_date,
        "end_date": end_date,
        "period": "10",
        "aftime": "0",
    }, ensure_ascii=False)

    env = os.environ.copy()
    env["WIND_API_KEY"] = api_key

    try:
        result = subprocess.run(
            ["node", "scripts/cli.mjs", "call", "stock_data", "get_stock_kline", params],
            cwd=WIND_MCP_DIR, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60, env=env,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None

        stdout = result.stdout.strip()
        if "#< CLIXML" in stdout:
            stdout = stdout.split("\n")[0]

        outer = json.loads(stdout)
        if outer.get("isError"):
            return None
        text = (outer.get("content", [{}])[0] or {}).get("text", "")
        if not text:
            return None
        inner = json.loads(text)
        if inner.get("error"):
            return None
        data = inner.get("data")
        if not data:
            return None

        columns = [c["name"] for c in data.get("columns", [])]
        rows = data.get("rows", [])
        if not rows:
            return None

        col_map = {c: i for i, c in enumerate(columns)}
        records = []
        for row in rows:
            vals = {}
            valid = True
            for field, idx_key in [("open","OPEN"),("close","MATCH"),("high","HIGH"),("low","LOW"),("volume","VOLUME")]:
                raw = row[col_map[idx_key]]
                if isinstance(raw, str) and ("INVALID" in raw.upper() or raw == ""):
                    valid = False
                    break
                vals[field] = float(raw)
            if not valid:
                continue
            vals["date"] = row[col_map.get("TIME", 0)][:10] if row[col_map.get("TIME", 0)] else None
            records.append(vals)

        if not records:
            return None
        df = pd.DataFrame(records)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        df = df.dropna()
        return df

    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:

        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        print(f"    Wind MCP 异常: {e}")
        return None


def extend_calendar(all_dates_set, qlib_dir):
    """扩展 QLib 日历"""
    cal_path = os.path.join(qlib_dir, "calendars", "day.txt")
    with open(cal_path) as f:
        existing = [line.strip() for line in f if line.strip()]

    pd.to_datetime(existing)
    existing_set = set(existing)

    # 找出新日期
    new_dates = []
    for d in sorted(all_dates_set):
        d_str = pd.Timestamp(d).strftime("%Y-%m-%d")
        if d_str not in existing_set:
            new_dates.append(d_str)

    if new_dates:
        with open(cal_path, "a") as f:
            for d in new_dates:
                f.write(d + "\n")
        print(f"    日历扩展: +{len(new_dates)} 天 (总计 {len(existing) + len(new_dates)} 天)")
    else:
        print(f"    日历无需扩展 (已有 {len(existing)} 天)")

    # 重新读取完整日历
    with open(cal_path) as f:
        full_calendar = [line.strip() for line in f if line.strip()]
    return pd.to_datetime(full_calendar)


def write_bin_file(series, calendar_dates, start_idx, bin_path):
    """写入单个字段的 bin 文件"""
    aligned = series.reindex(calendar_dates)
    values = np.where(np.isnan(aligned.values), 0, aligned.values).astype(np.float32)

    with open(bin_path, "wb") as f:
        # 起始索引 (uint32)
        f.write(struct.pack("<I", start_idx))
        # 数据 (float32)
        values.tofile(f)


def write_stock_bins(df, qlib_symbol, calendar_dates, qlib_dir):
    """写入一只股票的所有 bin 文件"""
    feature_dir = os.path.join(qlib_dir, "features", qlib_symbol)
    os.makedirs(feature_dir, exist_ok=True)

    # 找到数据在日历中的起始位置
    cal_set = set(calendar_dates)
    df_dates_in_cal = [d for d in df.index if d in cal_set]
    if not df_dates_in_cal:
        print(f"    {qlib_symbol}: 无数据在日历范围内")
        return False

    first_date = df_dates_in_cal[0]
    start_idx = list(calendar_dates).index(first_date)

    # 写入各字段
    fields = ["open", "high", "low", "close", "volume"]
    for field in fields:
        write_bin_file(df[field], calendar_dates, start_idx,
                       os.path.join(feature_dir, f"{field}.day.bin"))

    # factor (复权因子, 固定 1.0)
    factor = pd.Series(1.0, index=df.index)
    write_bin_file(factor, calendar_dates, start_idx,
                   os.path.join(feature_dir, "factor.day.bin"))

    # change (涨跌幅)
    change = df["close"].pct_change().fillna(0) * 100
    write_bin_file(change, calendar_dates, start_idx,
                   os.path.join(feature_dir, "change.day.bin"))

    return True


def update_instruments(qlib_dir, stocks):
    """更新 instruments/all.txt"""
    all_txt_path = os.path.join(qlib_dir, "instruments", "all.txt")
    existing = set()
    lines = []
    with open(all_txt_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                existing.add(line.split("\t")[0])
                lines.append(line)

    for code, exchange, _name in stocks:
        qlib_code = f"{exchange}{code}"
        if qlib_code not in existing:
            lines.append(f"{qlib_code}\t2015-01-01\t2026-07-08")
            existing.add(qlib_code)

    with open(all_txt_path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")


def main():
    print(f"\n{'='*70}")
    print("QLib 改进版训练 — 扩展日历 + 全量数据 + 辅助训练池")
    print(f"{'='*70}\n")

    api_key = load_wind_api_key()
    if not api_key:
        print("[错误] 未找到 WIND_API_KEY")
        return

    # ============================================================
    # 步骤 1: 下载全部持仓标的 + 辅助训练池数据
    # ============================================================
    all_download_stocks = PORTFOLIO_STOCKS + CSI50_AUX
    # 去重 (恒瑞医药在两个列表中)
    seen = set()
    download_list = []
    for code, exch, name in all_download_stocks:
        key = f"{exch}{code}"
        if key not in seen:
            seen.add(key)
            download_list.append((code, exch, name))

    print(f"[步骤1] 下载 {len(download_list)} 只股票数据 (Wind MCP)\n")

    all_data = {}  # {qlib_symbol: DataFrame}
    all_dates = set()

    for code, exchange, name in download_list:
        qlib_symbol = f"{exchange.lower()}{code}"

        df = wind_mcp_fetch_kline(code, exchange, BEGIN_DATE, END_DATE, api_key)
        if df is not None and len(df) > 0:
            all_data[qlib_symbol] = df
            all_dates.update(df.index)
            print(f"  {exchange}{code} {name}: {len(df)} 条 ({df.index[0].date()} ~ {df.index[-1].date()})")
        else:
            print(f"  {exchange}{code} {name}: 下载失败")

    print(f"\n  总计: {len(all_data)} 只股票, {len(all_dates)} 个交易日\n")

    # ============================================================
    # 步骤 2: 扩展 QLib 日历
    # ============================================================
    print("[步骤2] 扩展 QLib 日历\n")
    calendar_dates = extend_calendar(all_dates, QLIB_DATA_DIR)
    cal_end = calendar_dates[-1].strftime("%Y-%m-%d")
    print(f"  日历范围: {calendar_dates[0].date()} ~ {cal_end}\n")

    # ============================================================
    # 步骤 3: 重写所有 bin 文件
    # ============================================================
    print("[步骤3] 重写 bin 文件\n")
    written = 0
    for qlib_symbol, df in all_data.items():
        success = write_stock_bins(df, qlib_symbol, calendar_dates, QLIB_DATA_DIR)
        if success:
            written += 1
    print(f"\n  成功写入 {written} 只股票的 bin 文件\n")

    # 更新 instruments
    update_instruments(QLIB_DATA_DIR, download_list)

    # ============================================================
    # 步骤 4: QLib 训练
    # ============================================================
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

    from qlib.contrib.data.handler import Alpha158
    from qlib.contrib.model.gbdt import LGBModel
    from qlib.utils import init_instance_by_config

    # 训练标的: 持仓 + 辅助池
    portfolio_symbols = [f"{exch}{code}" for code, exch, name in PORTFOLIO_STOCKS]
    aux_symbols = [f"{exch}{code}" for code, exch, name in CSI50_AUX
                   if f"{exch}{code}" not in portfolio_symbols]
    all_train_symbols = portfolio_symbols + aux_symbols

    # 只有在 all_data 中存在的才训练
    all_train_symbols = [s for s in all_train_symbols if s.lower() in all_data]
    portfolio_symbols = [s for s in portfolio_symbols if s.lower() in all_data]

    stock_names = {f"{exch}{code}": name for code, exch, name in PORTFOLIO_STOCKS + CSI50_AUX}

    DATA_START = "2015-01-01"  # noqa: N806
    DATA_END = cal_end  # noqa: N806

    print(f"\n{'='*70}")
    print(f"QLib 横截面模型训练 — {len(all_train_symbols)} 只股票")
    print(f"{'='*70}")
    print(f"训练池: {len(portfolio_symbols)} 持仓 + {len(all_train_symbols) - len(portfolio_symbols)} 辅助")
    print(f"数据范围: {DATA_START} ~ {DATA_END}")
    print(f"训练期:   {DATA_START} ~ {TRAIN_END}")
    print(f"验证期:   {TRAIN_END} ~ {VALID_END}")
    print(f"测试期:   {TEST_START} ~ {DATA_END}")
    print(f"{'='*70}\n")

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
        "label": ["Ref($close, -2) / Ref($close, -1) - 1"],
    }

    print("[1/4] Alpha158 特征工程...")
    handler = Alpha158(**data_handler_config)
    print("      完成 (158 特征)")

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

    print("[3/4] 训练 LightGBM (优化参数)...")
    model = LGBModel(
        loss="mse",
        num_leaves=128,
        learning_rate=0.02,
        num_boost_round=500,
        max_depth=8,
        feature_fraction=0.7,
        bagging_fraction=0.7,
        bagging_freq=5,
        early_stopping_rounds=50,
        lambda_l1=0.1,
        lambda_l2=0.1,
        verbose=-1,
    )
    model.fit(dataset)
    print("      训练完成!")

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
    print("模型训练结果 (改进版)")
    print(f"{'='*70}")
    print(f"训练股票数:        {n_stocks_train}")
    print(f"训练样本:          {len(train_data)}")
    print(f"测试样本:          {len(test_data)}")
    print(f"数据范围:          {DATA_START} ~ {DATA_END}")
    print(f"整体 IC:          {overall_ic:.4f}")
    print(f"日均 IC:          {mean_ic:.4f}")
    print(f"日均 Rank IC:     {mean_rank_ic:.4f}")
    print(f"IC IR:            {ic_ir:.4f}")
    print(f"IC > 0 占比:      {(ic_by_date > 0).mean():.2%}")

    # 持仓标的信号
    print(f"\n{'='*70}")
    print("持仓标的预测信号")
    print(f"{'='*70}")
    print(f"{'代码':<12} {'名称':<10} {'最新信号':>10} {'方向':>6} {'均值':>10} {'排名':>6}")
    print(f"{'─'*70}")

    # 计算排名
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
                    print(f"{symbol:<12} {name:<10} {latest:>10.6f} {direction:>6} {avg:>10.6f} {rank:>6}/{len(latest_preds)}")  # noqa: E501
                    results.append({
                        "code": symbol, "name": name,
                        "latest_signal": round(float(latest), 6),
                        "avg_signal": round(float(avg), 6),
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
    neutral_count = sum(1 for r in results if r["direction"] == "中性")
    print(f"信号分布: 看多 {long_count} / 看空 {short_count} / 中性 {neutral_count}")
    print(f"{'='*70}\n")

    # 保存报告
    report_dir = os.path.join(_cwd, "reports")
    os.makedirs(report_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(report_dir, f"qlib_improved_train_{ts}.json")
    report = {
        "timestamp": datetime.datetime.now().isoformat(),
        "qlib_version": qlib.__version__,
        "model": "LightGBM (Alpha158, Improved: extended calendar + aux pool)",
        "improvements": [
            "扩展 QLib 日历到 2026",
            "下载全部 15 只持仓标的 2015-2026 数据",
            "添加 34 只 csi50 辅助训练池",
            "优化模型参数 (num_leaves=128, boost_round=500, lr=0.02)",
        ],
        "training_pool_size": len(all_train_symbols),
        "portfolio_size": len(portfolio_symbols),
        "data_range": f"{DATA_START} ~ {DATA_END}",
        "train_samples": len(train_data),
        "test_samples": len(test_data),
        "n_stocks_train": int(n_stocks_train),
        "overall_ic": round(float(overall_ic), 4) if not np.isnan(overall_ic) else None,
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
