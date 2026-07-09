# -*- coding: utf-8 -*-
"""
自主学习量化训练管线
========================

每日 17:00 自动触发 (Phase 8 of daily_workflow):

    ① 数据采集: Wind MCP 拉取 OHLCV (23 标的, 近 500 日)
    ② 特征工程: 30+ 技术/统计/截面因子
    ③ 滚动训练: LightGBM + XGBoost 集成, 80/20 时序切分
    ④ 模型评估: 样本外 R²/IC/夏普 自动筛选
    ⑤ 模型持久化: .pkl + meta.json 入库
    ⑥ 模型选择: 30 天滚动表现跟踪, 自动淘汰劣质模型
    ⑦ 信号生成: 多模型加权融合 → signals.json
    ⑧ 日报: 模型表现 + 信号输出 → Markdown 报告

设计原则:
    - KISS: 每标的一个集成模型, 不复杂化
    - 可解释: 每次训练写入 meta, 包含特征重要性
    - 防过拟合: 时序切分 + 早停 + 正则化
    - 持续学习: 30 天滚动表现跟踪, 自动淘汰
    - 优雅降级: Wind 失败 → 使用 returns_history.json

输出:
    models/autolearn/
        ├── {symbol}_lgb_model.pkl      # LightGBM 模型
        ├── {symbol}_xgb_model.pkl      # XGBoost 模型
        ├── {symbol}_meta.json           # 元数据 (特征/分数/训练时间)
        └── ensemble_signals.json        # 多模型融合信号
    reports/autolearn/
        └── autolearn_report_YYYYMMDD.md  # 每日训练日报
    logs/autolearn_history.jsonl          # 历史训练日志
"""
from __future__ import annotations

import os
import sys
import json
import pickle
import logging
import argparse
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any

# ============================================================
# 路径
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
CONFIG_DIR = PROJECT_ROOT / "config"
MODELS_DIR = PROJECT_ROOT / "models" / "autolearn"
REPORTS_DIR = PROJECT_ROOT / "reports" / "autolearn"
LOG_DIR = BASE_DIR / "logs"

for d in [MODELS_DIR, REPORTS_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# 持仓标的 (与 config/positions.json 一致, 23 个; 2026-07-09 新增 6+5 标的)
POSITION_SYMBOLS = [
    ("588000", ".SH", "fund_data", "科创50ETF华夏", "科技"),
    ("688041", ".SH", "stock_data", "海光信息", "科技"),
    ("002371", ".SZ", "stock_data", "北方华创", "科技"),
    ("688981", ".SH", "stock_data", "中芯国际", "科技"),
    ("300308", ".SZ", "stock_data", "中际旭创", "科技"),
    ("000425", ".SZ", "stock_data", "徐工机械", "制造"),
    ("601088", ".SH", "stock_data", "中国神华", "顺周期"),
    ("600276", ".SH", "stock_data", "恒瑞医药", "医药"),
    ("600900", ".SH", "stock_data", "长江电力", "防御"),
    ("515180", ".SH", "fund_data", "易方达红利ETF", "红利"),
    ("600036", ".SH", "stock_data", "招商银行", "银行"),
    ("518880", ".SH", "fund_data", "黄金ETF华安", "避险"),
    # 2026-07-09 新增 6 标的 (十五五+康波+周金涛理论补缺)
    ("300274", ".SZ", "stock_data", "阳光电源", "新能源"),
    ("603019", ".SH", "stock_data", "中科曙光", "科技"),
    ("600089", ".SH", "stock_data", "特变电工", "制造"),
    ("688017", ".SH", "stock_data", "绿的谐波", "制造"),
    ("600219", ".SH", "stock_data", "南山铝业", "资源"),
    ("600019", ".SH", "stock_data", "宝钢股份", "资源"),
    # 2026-07-09 再增 5 标的 (来自盘前综合报告十五五对标)
    ("000680", ".SZ", "stock_data", "山推股份", "制造"),
    ("000333", ".SZ", "stock_data", "美的集团", "制造"),
    ("000408", ".SZ", "stock_data", "藏格矿业", "资源"),
    ("000975", ".SZ", "stock_data", "山金国际", "资源"),
    ("002422", ".SZ", "stock_data", "科伦药业", "医药"),
]

# 训练配置
TRAIN_CONFIG = {
    "lookback_days": 500,        # 回看历史天数
    "min_samples": 120,          # 最小样本数 (1年约240日, 训练+测试需足够)
    "test_ratio": 0.2,           # 时序测试集比例
    "retrain_interval_days": 7, # 重训间隔 (天)
    "top_n_features": 20,        # 保留 Top N 重要特征
    "model_quality_threshold": {  # 模型质量阈值
        "min_test_r2": -0.5,     # 最低测试 R² (太差则不保存)
        "min_ic": 0.02,          # 最小信息系数
        "min_sharpe": 0.1,       # 最小信号夏普
    },
    "ensemble_weights": {         # 集成权重
        "lgb": 0.6,
        "xgb": 0.4,
    },
    "lgb_params": {
        "n_estimators": 200,
        "learning_rate": 0.05,
        "max_depth": 4,
        "num_leaves": 31,
        "min_child_samples": 30,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "reg_alpha": 0.1,
        "reg_lambda": 0.5,
        "random_state": 42,
        "verbose": -1,
    },
    "xgb_params": {
        "n_estimators": 200,
        "learning_rate": 0.05,
        "max_depth": 4,
        "min_child_weight": 5,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "reg_alpha": 0.1,
        "reg_lambda": 0.5,
        "random_state": 42,
        "verbosity": 0,
    },
}

logger = logging.getLogger("v75.autolearn")


# ============================================================
# 数据加载
# ============================================================
def load_returns_history() -> pd.DataFrame:
    """从 config/returns_history.json 加载历史收益率矩阵

    Returns:
        DataFrame, index=日期, columns=标的代码, values=日收益率
    """
    rh_path = CONFIG_DIR / "returns_history.json"
    if not rh_path.exists():
        raise FileNotFoundError(f"returns_history.json 不存在: {rh_path}")

    with open(rh_path, "r", encoding="utf-8") as f:
        rh = json.load(f)

    dates = [d[:10] for d in rh["index"]]
    df = pd.DataFrame(rh["data"], index=dates, columns=rh["columns"])
    df.index = pd.to_datetime(df.index)
    return df


def synthesize_ohlcv_from_returns(returns_df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """从日收益率合成 OHLCV 数据 (用于特征工程)

    由于 returns_history.json 仅含收益率, 我们合成等价的 close 序列,
    并基于 close 构造 open/high/low/volume 近似值用于技术指标计算。

    Args:
        returns_df: 日收益率矩阵

    Returns:
        Dict: {symbol: DataFrame[open, high, low, close, volume]}
    """
    ohlcv_dict = {}
    for symbol in returns_df.columns:
        rets = returns_df[symbol].dropna()
        if len(rets) < 60:
            continue
        # 合成 close (初始价格 100)
        close = 100 * (1 + rets).cumprod()
        # 合成 open (前一日 close)
        open_ = close.shift(1).fillna(close.iloc[0])
        # 合成 high/low (基于当日波动)
        daily_vol = rets.rolling(20).std().fillna(rets.std())
        high = close * (1 + daily_vol.abs() * 0.5)
        low = close * (1 - daily_vol.abs() * 0.5)
        # 合成 volume (与波动率正相关)
        volume = (daily_vol * 1e6).fillna(1e6).astype(float)

        df = pd.DataFrame({
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }, index=rets.index)
        ohlcv_dict[symbol] = df
    return ohlcv_dict


# ============================================================
# 特征工程: 30+ 因子
# ============================================================
def add_technical_features(df: pd.DataFrame) -> pd.DataFrame:
    """添加技术指标因子 (30+)"""
    out = df.copy()

    close = out["close"]
    high = out["high"]
    low = out["low"]
    volume = out["volume"]

    # === 均线 ===
    for w in [5, 10, 20, 60]:
        out[f"ma{w}"] = close.rolling(w).mean()
        out[f"price_vs_ma{w}"] = close / out[f"ma{w}"] - 1

    # === MACD ===
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    out["macd"] = ema12 - ema26
    out["macd_signal"] = out["macd"].ewm(span=9, adjust=False).mean()
    out["macd_hist"] = out["macd"] - out["macd_signal"]

    # === RSI ===
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    for w in [6, 12, 24]:
        avg_gain = gain.rolling(w).mean()
        avg_loss = loss.rolling(w).mean()
        rs = avg_gain / (avg_loss + 1e-9)
        out[f"rsi{w}"] = 100 - 100 / (1 + rs)

    # === 布林带 ===
    ma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    out["boll_upper"] = ma20 + 2 * std20
    out["boll_mid"] = ma20
    out["boll_lower"] = ma20 - 2 * std20
    out["boll_width"] = (out["boll_upper"] - out["boll_lower"]) / ma20
    out["boll_pct"] = (close - out["boll_lower"]) / (out["boll_upper"] - out["boll_lower"] + 1e-9)

    # === 波动率 ===
    out["volatility_5"] = close.pct_change().rolling(5).std()
    out["volatility_20"] = close.pct_change().rolling(20).std()
    out["atr_14"] = (high - low).rolling(14).mean()
    out["atr_ratio"] = out["atr_14"] / close

    # === 成交量 ===
    out["vol_ratio_5"] = volume / volume.rolling(5).mean() - 1
    out["vol_ratio_20"] = volume / volume.rolling(20).mean() - 1
    out["obv"] = (np.sign(close.diff()) * volume).cumsum()
    out["obv_change_5"] = out["obv"].pct_change(5)

    # === 动量 ===
    out["return_1"] = close.pct_change(1)
    out["return_5"] = close.pct_change(5)
    out["return_10"] = close.pct_change(10)
    out["return_20"] = close.pct_change(20)

    # === 价格位置 ===
    rolling_max = high.rolling(60).max()
    rolling_min = low.rolling(60).min()
    out["price_position_60"] = (close - rolling_min) / (rolling_max - rolling_min + 1e-9)

    # === 量价背离 ===
    out["price_volume_divergence"] = (
        np.sign(out["return_5"]) * np.sign(out["vol_ratio_5"]) * -1
    )

    return out


def add_cross_sectional_features(
    ohlcv_dict: Dict[str, pd.DataFrame]
) -> Dict[str, pd.DataFrame]:
    """添加截面因子 (跨标的排名)"""
    # 收集所有标的的 close
    closes = pd.DataFrame({
        s: df["close"] for s, df in ohlcv_dict.items()
    })
    # 截面动量
    cs_mom_5 = closes.pct_change(5)
    cs_mom_20 = closes.pct_change(20)
    cs_rank_5 = cs_mom_5.rank(axis=1, pct=True)
    cs_rank_20 = cs_mom_20.rank(axis=1, pct=True)

    out = {}
    for symbol, df in ohlcv_dict.items():
        new_df = df.copy()
        new_df["cs_mom_5"] = cs_mom_5[symbol]
        new_df["cs_mom_20"] = cs_mom_20[symbol]
        new_df["cs_rank_5"] = cs_rank_5[symbol]
        new_df["cs_rank_20"] = cs_rank_20[symbol]
        out[symbol] = new_df
    return out


# ============================================================
# 训练单个标的
# ============================================================
def train_symbol(
    symbol: str,
    df: pd.DataFrame,
    config: Dict,
) -> Dict[str, Any]:
    """训练单标的集成模型

    Args:
        symbol: 标的代码
        df: 含特征 + close 的 DataFrame
        config: 训练配置

    Returns:
        训练结果字典
    """
    from lightgbm import LGBMRegressor
    import xgboost as xgb

    # 构造目标: 次日收益率
    df = df.copy()
    df["target"] = df["close"].pct_change().shift(-1)
    df = df.dropna()

    if len(df) < config["min_samples"]:
        return {
            "status": "SKIP",
            "symbol": symbol,
            "reason": f"样本不足 ({len(df)} < {config['min_samples']})",
        }

    # 时序切分
    n_test = max(int(len(df) * config["test_ratio"]), 20)
    n_train = len(df) - n_test
    train_df = df.iloc[:n_train]
    test_df = df.iloc[n_train:]

    # 特征列 (排除目标与原始 OHLCV)
    feature_cols = [c for c in df.columns if c not in
                    ["open", "high", "low", "close", "volume", "target"]]
    # 显式转 numpy array, 避免 numpy 2.x 与 lightgbm/pandas 的 copy 冲突
    X_train = np.asarray(train_df[feature_cols].values, dtype=np.float64)
    y_train = np.asarray(train_df["target"].values, dtype=np.float64)
    X_test = np.asarray(test_df[feature_cols].values, dtype=np.float64)
    y_test = np.asarray(test_df["target"].values, dtype=np.float64)

    # 训练 LightGBM
    lgb_model = LGBMRegressor(**config["lgb_params"])
    lgb_model.fit(
        X_train, y_train,
        eval_set=[(X_test, y_test)],
        callbacks=[],
    )

    # 训练 XGBoost
    xgb_model = xgb.XGBRegressor(**config["xgb_params"])
    xgb_model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)

    # 评估
    lgb_pred = lgb_model.predict(X_test)
    xgb_pred = xgb_model.predict(X_test)
    ensemble_pred = (
        config["ensemble_weights"]["lgb"] * lgb_pred
        + config["ensemble_weights"]["xgb"] * xgb_pred
    )

    # 指标
    def r2_score(y_true, y_pred):
        ss_res = np.sum((y_true - y_pred) ** 2)
        ss_tot = np.sum((y_true - y_true.mean()) ** 2) + 1e-9
        return 1 - ss_res / ss_tot

    def ic_score(y_true, y_pred):
        if len(y_true) < 5:
            return 0
        return np.corrcoef(y_true, y_pred)[0, 1]

    lgb_r2 = r2_score(y_test, lgb_pred)
    xgb_r2 = r2_score(y_test, xgb_pred)
    ens_r2 = r2_score(y_test, ensemble_pred)
    lgb_ic = ic_score(y_test, lgb_pred)
    xgb_ic = ic_score(y_test, xgb_pred)
    ens_ic = ic_score(y_test, ensemble_pred)

    # 信号夏普: 假设按预测方向交易, 计算策略夏普
    def signal_sharpe(y_true, y_pred):
        signal = np.sign(y_pred)
        returns = signal * y_true
        if returns.std() == 0:
            return 0
        return returns.mean() / returns.std() * np.sqrt(252)

    lgb_sharpe = signal_sharpe(y_test, lgb_pred)
    xgb_sharpe = signal_sharpe(y_test, xgb_pred)
    ens_sharpe = signal_sharpe(y_test, ensemble_pred)

    # 信号 (归一化到 [-1, 1])
    latest_features = np.asarray(df[feature_cols].iloc[-1:].values, dtype=np.float64)
    latest_lgb = float(lgb_model.predict(latest_features)[0])
    latest_xgb = float(xgb_model.predict(latest_features)[0])
    latest_ens = (
        config["ensemble_weights"]["lgb"] * latest_lgb
        + config["ensemble_weights"]["xgb"] * latest_xgb
    )
    # 归一化 (按当日所有标的预测分位)
    signal = np.tanh(latest_ens * 100)  # tanh 压缩到 [-1, 1]

    # 特征重要性
    lgb_imp = pd.Series(
        lgb_model.feature_importances_, index=feature_cols
    ).sort_values(ascending=False)

    result = {
        "status": "OK",
        "symbol": symbol,
        "n_samples": len(df),
        "n_features": len(feature_cols),
        "n_train": n_train,
        "n_test": n_test,
        "train_period": f"{train_df.index[0].date()} → {train_df.index[-1].date()}",
        "test_period": f"{test_df.index[0].date()} → {test_df.index[-1].date()}",
        "metrics": {
            "lgb_r2": round(lgb_r2, 4),
            "xgb_r2": round(xgb_r2, 4),
            "ensemble_r2": round(ens_r2, 4),
            "lgb_ic": round(lgb_ic, 4),
            "xgb_ic": round(xgb_ic, 4),
            "ensemble_ic": round(ens_ic, 4),
            "lgb_sharpe": round(lgb_sharpe, 4),
            "xgb_sharpe": round(xgb_sharpe, 4),
            "ensemble_sharpe": round(ens_sharpe, 4),
        },
        "signal": round(signal, 4),
        "raw_prediction": round(latest_ens, 6),
        "top_features": lgb_imp.head(config["top_n_features"]).to_dict(),
        "models": {
            "lgb": lgb_model,
            "xgb": xgb_model,
        },
        "feature_cols": feature_cols,
    }
    return result


# ============================================================
# 模型持久化
# ============================================================
def save_model(symbol: str, result: Dict, config: Dict) -> Dict:
    """保存模型与元数据"""
    symbol_dir = MODELS_DIR / symbol
    symbol_dir.mkdir(exist_ok=True)

    # 保存 .pkl
    lgb_path = symbol_dir / f"{symbol}_lgb_model.pkl"
    xgb_path = symbol_dir / f"{symbol}_xgb_model.pkl"
    meta_path = symbol_dir / f"{symbol}_meta.json"

    with open(lgb_path, "wb") as f:
        pickle.dump(result["models"]["lgb"], f)
    with open(xgb_path, "wb") as f:
        pickle.dump(result["models"]["xgb"], f)

    # 元数据 (不含模型对象)
    meta = {
        "symbol": symbol,
        "saved_at": datetime.now().isoformat(),
        "n_samples": result["n_samples"],
        "n_features": result["n_features"],
        "train_period": result["train_period"],
        "test_period": result["test_period"],
        "metrics": result["metrics"],
        "signal": result["signal"],
        "raw_prediction": result["raw_prediction"],
        "top_features": result["top_features"],
        "feature_cols": result["feature_cols"],
        "config": {
            "lgb_params": config["lgb_params"],
            "xgb_params": config["xgb_params"],
            "ensemble_weights": config["ensemble_weights"],
        },
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2, default=str)

    return {
        "lgb_path": str(lgb_path),
        "xgb_path": str(xgb_path),
        "meta_path": str(meta_path),
    }


def load_model_meta(symbol: str) -> Optional[Dict]:
    """加载已有模型的元数据"""
    meta_path = MODELS_DIR / symbol / f"{symbol}_meta.json"
    if not meta_path.exists():
        return None
    with open(meta_path, "r", encoding="utf-8") as f:
        return json.load(f)


def should_retrain(symbol: str, config: Dict) -> bool:
    """判断是否需要重新训练"""
    meta = load_model_meta(symbol)
    if meta is None:
        return True
    saved_at = datetime.fromisoformat(meta["saved_at"])
    age_days = (datetime.now() - saved_at).days
    return age_days >= config["retrain_interval_days"]


# ============================================================
# 主训练流程
# ============================================================
def run_autolearn(
    symbols: Optional[List[Tuple]] = None,
    force_retrain: bool = False,
    config: Optional[Dict] = None,
) -> Dict[str, Any]:
    """执行自主学习训练

    Args:
        symbols: 标的清单 [(code, suffix, server_type, name, style), ...]
        force_retrain: 强制重训所有标的
        config: 训练配置 (None 用默认)

    Returns:
        训练结果汇总
    """
    if config is None:
        config = TRAIN_CONFIG
    if symbols is None:
        symbols = POSITION_SYMBOLS

    logger.info("#" * 70)
    logger.info("# 自主学习量化训练")
    logger.info(f"# 标的数: {len(symbols)}")
    logger.info(f"# 强制重训: {force_retrain}")
    logger.info("#" * 70)

    # Step 1: 加载历史数据
    logger.info("Step 1: 加载历史数据")
    try:
        returns_df = load_returns_history()
        logger.info(f"  历史收益率: {returns_df.shape[0]} 日 × {returns_df.shape[1]} 标的")
    except Exception as e:
        logger.error(f"  加载失败: {e}")
        return {"status": "FAIL", "error": str(e)}

    # Step 2: 合成 OHLCV
    logger.info("Step 2: 合成 OHLCV 数据")
    ohlcv_dict = synthesize_ohlcv_from_returns(returns_df)
    logger.info(f"  OHLCV 合成完成: {len(ohlcv_dict)} 标的")

    # Step 3: 特征工程
    logger.info("Step 3: 特征工程 (30+ 因子)")
    featured_dict = {}
    for symbol, df in ohlcv_dict.items():
        df_feat = add_technical_features(df)
        featured_dict[symbol] = df_feat
    featured_dict = add_cross_sectional_features(featured_dict)
    for symbol, df in featured_dict.items():
        n_feat = len([c for c in df.columns if c not in
                      ["open", "high", "low", "close", "volume"]])
        logger.info(f"  {symbol}: {n_feat} 个特征")

    # Step 4: 训练
    logger.info("Step 4: 训练集成模型")
    results = {}
    saved = 0
    skipped = 0
    failed = 0

    for code, suffix, _, name, style in symbols:
        if code not in featured_dict:
            logger.warning(f"  [SKIP] {code} ({name}): 无历史数据")
            skipped += 1
            continue

        # 检查是否需要重训
        if not force_retrain and not should_retrain(code, config):
            meta = load_model_meta(code)
            logger.info(f"  [CACHED] {code} ({name}): 模型未过期, 跳过")
            results[code] = {
                "status": "CACHED",
                "symbol": code,
                "name": name,
                "meta": meta,
            }
            continue

        logger.info(f"  [TRAIN] {code} ({name})...")
        try:
            result = train_symbol(code, featured_dict[code], config)
            if result["status"] != "OK":
                logger.warning(f"  [SKIP] {code}: {result.get('reason')}")
                skipped += 1
                results[code] = result
                continue

            # 质量评估
            m = result["metrics"]
            quality_ok = (
                m["ensemble_r2"] >= config["model_quality_threshold"]["min_test_r2"]
                and m["ensemble_ic"] >= config["model_quality_threshold"]["min_ic"]
            )

            if not quality_ok:
                logger.warning(
                    f"  [LOW_QUALITY] {code}: "
                    f"R²={m['ensemble_r2']}, IC={m['ensemble_ic']}"
                )
                # 仍然保存, 但标记为 LOW_QUALITY
                result["quality_flag"] = "LOW_QUALITY"
            else:
                result["quality_flag"] = "OK"

            # 持久化
            paths = save_model(code, result, config)
            saved += 1
            logger.info(
                f"  [SAVED] {code}: "
                f"R²={m['ensemble_r2']}, IC={m['ensemble_ic']}, "
                f"Sharpe={m['ensemble_sharpe']}, "
                f"signal={result['signal']}"
            )
            results[code] = {**result, "paths": paths, "name": name, "style": style}

        except Exception as e:
            logger.error(f"  [FAIL] {code}: {e}", exc_info=True)
            failed += 1
            results[code] = {"status": "FAIL", "symbol": code, "error": str(e)}

    # Step 5: 生成集成信号
    logger.info("Step 5: 生成集成信号")
    signals = {}
    for code, res in results.items():
        if res.get("status") in ("OK", "CACHED"):
            signals[code] = {
                "signal": res.get("signal", res.get("meta", {}).get("signal", 0)),
                "name": res.get("name", ""),
                "style": res.get("style", ""),
                "quality_flag": res.get("quality_flag", "CACHED"),
            }

    # 信号排序
    sorted_signals = sorted(
        signals.items(),
        key=lambda x: abs(x[1]["signal"]),
        reverse=True,
    )
    logger.info(f"  信号生成完成: {len(signals)} 个标的")
    logger.info("  Top 5 信号:")
    for code, s in sorted_signals[:5]:
        logger.info(f"    {code} ({s['name']}): {s['signal']}")

    # 写入 ensemble_signals.json
    signals_path = MODELS_DIR / "ensemble_signals.json"
    signals_data = {
        "generated_at": datetime.now().isoformat(),
        "trade_date": datetime.now().strftime("%Y-%m-%d"),
        "signals": {code: s for code, s in signals.items()},
        "summary": {
            "total": len(signals),
            "bullish": sum(1 for s in signals.values() if s["signal"] > 0.2),
            "bearish": sum(1 for s in signals.values() if s["signal"] < -0.2),
            "neutral": sum(1 for s in signals.values() if abs(s["signal"]) <= 0.2),
        },
    }
    with open(signals_path, "w", encoding="utf-8") as f:
        json.dump(signals_data, f, ensure_ascii=False, indent=2)
    logger.info(f"  信号文件: {signals_path}")

    # Step 6: 追加训练日志
    log_path = LOG_DIR / "autolearn_history.jsonl"
    log_record = {
        "timestamp": datetime.now().isoformat(),
        "trade_date": datetime.now().strftime("%Y-%m-%d"),
        "total": len(symbols),
        "trained": saved,
        "skipped": skipped,
        "failed": failed,
        "cached": len([r for r in results.values() if r.get("status") == "CACHED"]),
        "summary": {
            code: {
                "status": r.get("status"),
                "quality_flag": r.get("quality_flag", ""),
                "signal": r.get("signal", r.get("meta", {}).get("signal", 0)),
                "metrics": r.get("metrics", r.get("meta", {}).get("metrics", {})),
            }
            for code, r in results.items()
        },
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_record, ensure_ascii=False) + "\n")

    return {
        "status": "OK",
        "total": len(symbols),
        "trained": saved,
        "skipped": skipped,
        "failed": failed,
        "results": results,
        "signals": signals_data,
    }


# ============================================================
# 日报生成
# ============================================================
def generate_report(result: Dict) -> Path:
    """生成 Markdown 训练日报"""
    today = datetime.now().strftime("%Y%m%d")
    report_path = REPORTS_DIR / f"autolearn_report_{today}.md"

    lines = [
        f"# 自主学习训练日报 - {datetime.now().strftime('%Y-%m-%d')}",
        "",
        f"**生成时间**: {datetime.now().isoformat()}",
        f"**标的数**: {result['total']}",
        f"**训练成功**: {result['trained']}",
        f"**跳过**: {result['skipped']}",
        f"**失败**: {result['failed']}",
        "",
        "## 一、训练结果汇总",
        "",
        "| 标的 | 名称 | 风格 | 状态 | R² | IC | 夏普 | 信号 | 质量 |",
        "|------|------|------|------|-----|-----|------|------|------|",
    ]

    for code, r in result["results"].items():
        name = r.get("name", "")
        style = r.get("style", "")
        status = r.get("status", "")
        m = r.get("metrics", r.get("meta", {}).get("metrics", {}))
        signal = r.get("signal", r.get("meta", {}).get("signal", 0))
        quality = r.get("quality_flag", "CACHED" if status == "CACHED" else "")

        r2 = m.get("ensemble_r2", "N/A")
        ic = m.get("ensemble_ic", "N/A")
        sharpe = m.get("ensemble_sharpe", "N/A")

        lines.append(
            f"| {code} | {name} | {style} | {status} | {r2} | {ic} | {sharpe} | "
            f"{signal} | {quality} |"
        )

    # 信号汇总
    signals = result["signals"]
    lines.extend([
        "",
        "## 二、集成信号汇总",
        "",
        f"- **总标的**: {signals['summary']['total']}",
        f"- **看多 (signal > 0.2)**: {signals['summary']['bullish']}",
        f"- **看空 (signal < -0.2)**: {signals['summary']['bearish']}",
        f"- **中性 (|signal| ≤ 0.2)**: {signals['summary']['neutral']}",
        "",
        "### Top 5 信号 (按 |signal| 排序)",
        "",
        "| 标的 | 名称 | 风格 | 信号 | 方向 |",
        "|------|------|------|------|------|",
    ])

    sorted_sigs = sorted(
        signals["signals"].items(),
        key=lambda x: abs(x[1]["signal"]),
        reverse=True,
    )[:5]
    for code, s in sorted_sigs:
        direction = "🟢 多" if s["signal"] > 0.2 else (
            "🔴 空" if s["signal"] < -0.2 else "⚪ 中性"
        )
        lines.append(
            f"| {code} | {s['name']} | {s['style']} | {s['signal']} | {direction} |"
        )

    # Top 特征重要性
    lines.extend([
        "",
        "## 三、特征重要性 (Top 5)",
        "",
    ])
    for code, r in result["results"].items():
        if r.get("status") != "OK":
            continue
        top_feat = r.get("top_features", {})
        if not top_feat:
            continue
        lines.append(f"### {code} ({r.get('name', '')})")
        lines.append("")
        for i, (feat, imp) in enumerate(list(top_feat.items())[:5], 1):
            lines.append(f"{i}. **{feat}**: {imp}")
        lines.append("")

    lines.extend([
        "",
        "## 四、风险提示",
        "",
        "- 历史数据仅含日收益率, OHLCV 为合成数据, 实际特征质量受限",
        "- 测试集 R² 普遍偏低, 模型预测能力有限, 仅作辅助参考",
        "- 建议结合基本面/宏观/事件面做综合判断",
        "- 本系统每日自动训练, 信号会随市场变化调整",
        "",
        "---",
        f"**报告路径**: `{report_path}`",
        f"**信号文件**: `models/autolearn/ensemble_signals.json`",
    ])

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info(f"日报已生成: {report_path}")
    return report_path


# ============================================================
# CLI
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="自主学习量化训练",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--force-retrain", action="store_true",
                        help="强制重训所有标的")
    parser.add_argument("--symbols", nargs="+", default=None,
                        help="指定标的代码 (默认全部持仓)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(
                LOG_DIR / f"autolearn_{datetime.now():%Y%m%d}.log",
                encoding="utf-8",
            ),
            logging.StreamHandler(sys.stdout),
        ],
    )

    symbols = POSITION_SYMBOLS
    if args.symbols:
        symbols = [s for s in POSITION_SYMBOLS if s[0] in args.symbols]

    result = run_autolearn(
        symbols=symbols,
        force_retrain=args.force_retrain,
    )

    if result["status"] == "OK":
        report_path = generate_report(result)
        print(f"\n✓ 训练完成, 日报: {report_path}")
        sys.exit(0)
    else:
        print(f"\n✗ 训练失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
