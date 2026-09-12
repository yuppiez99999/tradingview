"""
LightGBM 因子挖掘 - 训练模型识别有效因子
从本地缓存数据计算 50+ 因子，训练 LightGBM 预测未来收益，
提取特征重要性排序，发现新的有效因子。
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from utils.datetime_utils import now_bj

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("lgbm_factor_mining")

OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

# ECC GAP-7: 训练管道可复现性 (G5 物理隔离: 2026-08-09 改指 utils 命名空间)
from utils.lgbm_reproducibility import (  # noqa: E402
    ManifestWriteError,
    TrainingConfig,
    artifact_name,
    construct_default_config,
    write_manifest,
)


def compute_all_factors(df_group: pd.DataFrame) -> pd.Series:
    """对单只股票计算所有技术因子 (50+)

    Args:
        df_group: 单只股票的日线数据 (按日期排序)

    Returns:
        最新时间点的因子值 Series
    """
    factors = {}
    close = df_group["close"]
    volume = df_group["volume"]
    high = df_group["high"]
    low = df_group["low"]
    df_group["open"]
    n = len(close)

    if n < 20:
        return pd.Series(factors)

    ret = close.pct_change()

    # === 动量类 ===
    for w in [5, 10, 20, 60, 120, 252]:
        if n > w:
            factors[f"MOM_{w}D"] = close.iloc[-1] / close.iloc[-w] - 1.0
    if n > 252:
        factors["MOM_12_1M"] = close.iloc[-21] / close.iloc[-252] - 1.0
    for w in [5, 20]:
        if n > w:
            factors[f"REVERSAL_{w}D"] = -(close.iloc[-1] / close.iloc[-w] - 1.0)
    for w in [20, 60]:
        if n > w:
            ret_w = ret.iloc[-w:]
            factors[f"UP_DOWN_RATIO_{w}D"] = (ret_w > 0).sum() / max(
                (ret_w < 0).sum(), 1
            )

    # === 波动率类 ===
    for w in [5, 20, 60, 120, 252]:
        if n > w:
            factors[f"VOL_{w}D"] = ret.iloc[-w:].std()
    for w in [20, 60]:
        if n > w:
            ret_w = ret.iloc[-w:]
            downside = ret_w[ret_w < 0]
            factors[f"DOWNSIDE_VOL_{w}D"] = downside.std() if len(downside) > 1 else 0.0
    for w in [60, 120]:
        if n > w:
            factors[f"SKEW_{w}D"] = ret.iloc[-w:].skew()
    for w in [60, 120]:
        if n > w:
            factors[f"KURT_{w}D"] = ret.iloc[-w:].kurtosis()

    # === 流动性类 ===
    for w in [5, 20, 60]:
        if n > w:
            factors[f"TURNOVER_{w}D"] = volume.iloc[-w:].mean()
    for w in [20, 60]:
        if n > w:
            ret_abs = ret.iloc[-w:].abs()
            vol_w = volume.iloc[-w:]
            amihud = (ret_abs / vol_w.replace(0, np.nan)).mean()
            factors[f"AMIHUD_{w}D"] = amihud if np.isfinite(amihud) else 0.0
    for w in [5, 20]:
        if n > w * 2:
            factors[f"VOLUME_CHG_{w}D"] = (
                volume.iloc[-w:].mean() / volume.iloc[-2 * w : -w].mean() - 1.0
            )
    for w in [20, 60]:
        if n > w:
            vol_w = volume.iloc[-w:]
            factors[f"VOLUME_Z_{w}D"] = (volume.iloc[-1] - vol_w.mean()) / max(
                vol_w.std(), 1e-12
            )

    # === 技术指标类 ===
    for w in [10, 20, 60, 120]:
        if n > w:
            ma = close.rolling(w).mean()
            factors[f"MA_DEV_{w}D"] = close.iloc[-1] / ma.iloc[-1] - 1.0
    if n > 26:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9, adjust=False).mean()
        factors["MACD"] = (dif.iloc[-1] - dea.iloc[-1]) / max(close.iloc[-1], 1e-12)
    for w in [6, 14, 28]:
        if n > w:
            delta = ret.iloc[-w:]
            gain = delta.where(delta > 0, 0).mean()
            loss = -delta.where(delta < 0, 0).mean()
            rs = gain / max(loss, 1e-12)
            factors[f"RSI_{w}D"] = 100 - (100 / (1 + rs))
    for w in [20, 60]:
        if n > w:
            ma_w = close.rolling(w).mean()
            std_w = close.rolling(w).std()
            factors[f"BB_WIDTH_{w}D"] = (std_w.iloc[-1] * 2) / max(ma_w.iloc[-1], 1e-12)
    for w in [14, 28]:
        if n > w:
            tr = pd.concat(
                [
                    high - low,
                    (high - close.shift(1)).abs(),
                    (low - close.shift(1)).abs(),
                ],
                axis=1,
            ).max(axis=1)
            factors[f"ATR_{w}D"] = tr.iloc[-w:].mean() / max(close.iloc[-1], 1e-12)

    # === 价量关系类 ===
    if n > 20:
        obv = (np.sign(ret.fillna(0)) * volume).cumsum()
        factors["OBV_CHG"] = obv.iloc[-1] / max(obv.iloc[-20], 1e-12) - 1.0
    for w in [20, 60]:
        if n > w:
            price_new_high = close.iloc[-1] >= close.iloc[-w:].max()
            vol_new_high = volume.iloc[-1] >= volume.iloc[-w:].max()
            factors[f"PRICE_VOL_DIVERG_{w}D"] = (
                1.0 if (price_new_high and not vol_new_high) else 0.0
            )

    # === 基本面代理因子 (从量价衍生) ===
    factors["SIZE_PROXY"] = close.iloc[-1]
    if n > 252:
        factors["LONG_TERM_RET"] = close.iloc[-1] / close.iloc[-252] - 1.0
    if n > 252:
        factors["EARNING_STABILITY"] = -ret.iloc[-252:].std()
    if n > 120:
        ret_120 = ret.iloc[-120:]
        factors["QUALITY_PROXY"] = ret_120.mean() / max(ret_120.std(), 1e-12)

    return pd.Series(factors)


def build_factor_panel(start_date: str = "2023-01-01", step: int = 10):
    """构建因子面板和标签数据

    Returns:
        DataFrame [date x stock, code + factors + y]
    """
    from research.factor_discovery import FactorDataFetcher

    loader = FactorDataFetcher()
    codes = loader.get_available_cached_symbols(min_days=120)
    logger.info(f"加载 {len(codes)} 只标的数据 (本地缓存)")

    all_data = loader.fetch_daily_data(codes, start_date=start_date)
    logger.info(f"成功加载 {len(all_data)} 只标的")

    # 确定所有可用日期
    all_dates = sorted(set().union(*[set(df.index) for df in all_data.values()]))
    calc_dates = all_dates[::step]
    logger.info(f"计算时点: {len(calc_dates)} 个")

    rows = []
    for date in calc_dates:
        for code, df in all_data.items():
            if date not in df.index:
                continue
            hist = df[df.index <= date]
            if len(hist) < 120:
                continue

            # 找当前日期在 df 中的位置
            pos = hist.index.get_loc(date)
            # 未来5日收益
            if pos + 6 < len(df):
                fut_ret = df["close"].iloc[pos + 5] / df["close"].iloc[pos] - 1.0
            else:
                continue

            try:
                fv = compute_all_factors(hist)
                if len(fv) > 20:
                    row = {"code": code, "date": date, "y": fut_ret}
                    row.update(fv.to_dict())
                    rows.append(row)
            except (
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                RuntimeError,
                OSError,
                TimeoutError,
                ConnectionError,
            ):
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                continue

    panel = pd.DataFrame(rows)
    logger.info(f"因子面板构建完成: {len(panel)} 行, {panel.shape[1]-3} 个因子")
    return panel


# NOTE (2026-08-09 G5): construct_default_config 已迁移至 utils.lgbm_reproducibility,
# 本模块通过上方 `from utils.lgbm_reproducibility import construct_default_config` 引用,
# 保持 HC-1 V9 基线参数 (num_leaves=31, learning_rate=0.05, seed=42) 完全不变.


def train_and_analyze(
    panel: pd.DataFrame,
    config: TrainingConfig | None = None,
) -> tuple[pd.DataFrame, list] | None:
    """训练 LightGBM 并分析特征重要性.

    ECC GAP-7 修改:
        - 新增可选 config 参数 (None 时用 construct_default_config 构造默认)
        - 训练完成后落盘 manifest.json (warn_only, 失败不阻断训练)
        - seed/超参从 config 读取 (便于复现测试注入不同 seed)

    Args:
        panel: 因子面板
        config: 训练配置 (None 时用默认, 保留原行为)

    Returns:
        (imp_df, fold_scores) 元组, 或 None (lightgbm 缺失时)
    """
    try:
        import lightgbm as lgb
        from sklearn.metrics import mean_squared_error, r2_score
        from sklearn.model_selection import TimeSeriesSplit
    except ImportError:
        logger.error("请先安装 lightgbm: pip install lightgbm")
        return None

    # ECC GAP-7: 构造或复用 TrainingConfig
    if config is None:
        config = construct_default_config(panel)
    else:
        # 调用方传入的 config, 确保填充 dataset_uri/code_sha/env/config_hash
        if not config.dataset_uri:
            config = config.with_dataset(panel)
        if not config.code_sha:
            config = config.with_code_sha([Path(__file__)])
        if not config.training_env:
            config = config.with_environment()
        if not config.config_hash:
            config = config.with_config_hash()

    # 清洗数据
    factor_cols = [c for c in panel.columns if c not in ("code", "date", "y")]
    panel_clean = panel.dropna(subset=factor_cols + ["y"], how="any")
    panel_clean = panel_clean.replace([np.inf, -np.inf], np.nan).dropna()

    # 去极值
    for col in factor_cols:
        q_low = panel_clean[col].quantile(0.01)
        q_high = panel_clean[col].quantile(0.99)
        panel_clean[col] = panel_clean[col].clip(q_low, q_high)

    # 按日期排序
    panel_clean = panel_clean.sort_values("date").reset_index(drop=True)

    X = panel_clean[factor_cols].values  # noqa: N806
    y = panel_clean["y"].values

    logger.info(f"训练数据: {len(X)} 样本, {len(factor_cols)} 因子")
    logger.info(f"ECC GAP-7: artifact_name={artifact_name(config)}")

    # 时间序列交叉验证
    tscv = TimeSeriesSplit(n_splits=config.n_splits)
    all_importance = np.zeros(len(factor_cols))
    fold_scores = []

    # 从 config 读取超参 (HC-1: 默认值与原硬编码一致)
    params = dict(config.lgb_params)
    num_boost_round = config.num_boost_round
    early_stopping_rounds = config.early_stopping_rounds

    for fold, (train_idx, test_idx) in enumerate(tscv.split(X)):
        X_train, X_test = X[train_idx], X[test_idx]  # noqa: N806
        y_train, y_test = y[train_idx], y[test_idx]

        train_data = lgb.Dataset(X_train, label=y_train)
        test_data = lgb.Dataset(X_test, label=y_test, reference=train_data)

        model = lgb.train(
            params,
            train_data,
            num_boost_round=num_boost_round,
            valid_sets=[test_data],
            callbacks=[
                lgb.early_stopping(early_stopping_rounds),
                lgb.log_evaluation(0),
            ],
        )

        # 预测
        y_pred = model.predict(X_test)
        mse = mean_squared_error(y_test, y_pred)
        r2 = r2_score(y_test, y_pred)
        fold_scores.append({"fold": fold, "mse": mse, "r2": r2})
        logger.info(f"Fold {fold}: MSE={mse:.6f}, R²={r2:.4f}")

        all_importance += model.feature_importance(importance_type="gain")

    # 平均重要性
    avg_importance = all_importance / tscv.get_n_splits()

    # 特征重要性排序
    imp_df = (
        pd.DataFrame(
            {
                "factor": factor_cols,
                "importance": avg_importance,
            }
        )
        .sort_values("importance", ascending=False)
        .reset_index(drop=True)
    )

    imp_df["importance_pct"] = imp_df["importance"] / imp_df["importance"].sum() * 100
    imp_df["cum_pct"] = imp_df["importance_pct"].cumsum()

    # ECC GAP-7: 落盘 manifest.json (warn_only, 失败不阻断)
    try:
        manifest_metrics: dict[str, Any] = {
            "feature_importance": {
                row["factor"]: float(row["importance"]) for _, row in imp_df.iterrows()
            },
            "fold_scores": fold_scores,
            "top_10_factors": imp_df.head(10)["factor"].tolist(),
            "avg_mse": (
                float(np.mean([s["mse"] for s in fold_scores])) if fold_scores else 0.0
            ),
            "avg_r2": (
                float(np.mean([s["r2"] for s in fold_scores])) if fold_scores else 0.0
            ),
        }
        artifact_dir = OUTPUT_DIR / artifact_name(config)
        write_manifest(artifact_dir, config, metrics=manifest_metrics)
    except ManifestWriteError as e:
        logger.warning(f"ECC GAP-7: manifest 落盘失败 (训练继续): {e}")

    return imp_df, fold_scores


def main():
    logger.info("=" * 60)
    logger.info("LightGBM 因子挖掘启动")
    logger.info("=" * 60)

    # 构建因子面板
    panel = build_factor_panel(start_date="2023-01-01", step=10)

    ts = now_bj().strftime("%Y%m%d_%H%M%S")

    # 保存面板
    panel_path = OUTPUT_DIR / f"factor_panel_{ts}.csv"
    panel.to_csv(panel_path, index=False)
    logger.info(f"因子面板已保存: {panel_path}")

    # 训练模型
    imp_df, scores = train_and_analyze(panel)

    if imp_df is not None:
        # 保存特征重要性
        imp_path = OUTPUT_DIR / f"lgbm_feature_importance_{ts}.csv"
        imp_df.to_csv(imp_path, index=False)
        logger.info(f"特征重要性已保存: {imp_path}")

        # 生成报告
        report_lines = []
        report_lines.append("# LightGBM 因子挖掘报告")
        report_lines.append("")
        report_lines.append(
            f"**生成时间**: {now_bj().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        report_lines.append(f"**样本数**: {len(panel)}")
        report_lines.append(
            f"**因子数**: {len([c for c in panel.columns if c not in ('code','date','y')])}"
        )
        report_lines.append("")

        report_lines.append("## 交叉验证结果")
        report_lines.append("")
        report_lines.append("| Fold | MSE | R² |")
        report_lines.append("|------|-----|-----|")
        for s in scores:
            report_lines.append(f"| {s['fold']} | {s['mse']:.6f} | {s['r2']:.4f} |")
        report_lines.append("")

        report_lines.append("## TOP 20 因子 (按特征重要性)")
        report_lines.append("")
        report_lines.append("| 排名 | 因子名 | 重要性 | 占比% | 累计% |")
        report_lines.append("|------|--------|--------|-------|-------|")
        for i, row in imp_df.head(20).iterrows():
            report_lines.append(
                f"| {i+1} | {row['factor']} | {row['importance']:.2f} | {row['importance_pct']:.2f} | {row['cum_pct']:.2f} |"  # noqa: E501
            )
        report_lines.append("")

        report_lines.append("## 新因子候选 (未在 alpha_factor_library 中定义)")
        report_lines.append("")
        existing_factors = set()
        # 从 alpha_factor_library 读取已有因子
        try:
            alpha_lib_path = (
                Path(__file__).resolve().parent.parent
                / "utils"
                / "alpha_factor_library.py"
            )
            import re

            content = alpha_lib_path.read_text(encoding="utf-8")
            for m in re.finditer(r'"([A-Z_0-9]+)"', content):
                existing_factors.add(m.group(1))
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            pass

        new_candidates = []
        for _i, row in imp_df.head(30).iterrows():
            if row["factor"] not in existing_factors:
                new_candidates.append(row)

        if new_candidates:
            report_lines.append("| 排名 | 因子名 | 重要性 | 占比% | 说明 |")
            report_lines.append("|------|--------|--------|-------|------|")
            for row in new_candidates:
                desc = "技术面衍生因子"
                if "SKEW" in row["factor"]:
                    desc = "收益偏度因子 - 捕捉收益分布不对称性"
                elif "KURT" in row["factor"]:
                    desc = "收益峰度因子 - 捕捉极端收益概率"
                elif "DOWNSIDE_VOL" in row["factor"]:
                    desc = "下行波动率 - 衡量下跌风险"
                elif "RSI" in row["factor"]:
                    desc = "RSI 强弱指标 - 超买超卖信号"
                elif "MA_DEV" in row["factor"]:
                    desc = "均线偏离度 - 趋势强弱指标"
                elif "MACD" in row["factor"]:
                    desc = "MACD 柱状值 - 趋势动能指标"
                elif "BB_WIDTH" in row["factor"]:
                    desc = "布林带宽度 - 波动率收敛/扩张"
                elif "ATR" in row["factor"]:
                    desc = "平均真实波幅 - 市场波动度量"
                elif "VOLUME_CHG" in row["factor"]:
                    desc = "成交量变化率 - 量能异动信号"
                elif "VOLUME_Z" in row["factor"]:
                    desc = "成交量 Z-Score - 异常放量信号"
                elif "OBV" in row["factor"]:
                    desc = "OBV 能量潮变化 - 资金流向指标"
                elif "PRICE_VOL_DIVERG" in row["factor"]:
                    desc = "价量背离 - 趋势反转预警"
                elif "UP_DOWN_RATIO" in row["factor"]:
                    desc = "涨跌日数比 - 趋势持续性"
                elif "PROXY" in row["factor"] or "STABILITY" in row["factor"]:
                    desc = "基本面代理因子 - 从量价衍生"
                report_lines.append(
                    f"| {imp_df[imp_df['factor']==row['factor']].index[0]+1} | {row['factor']} | {row['importance']:.2f} | {row['importance_pct']:.2f} | {desc} |"  # noqa: E501
                )
        else:
            report_lines.append("暂无新因子候选")
        report_lines.append("")

        report_path = OUTPUT_DIR / f"lgbm_factor_mining_report_{ts}.md"
        report_path.write_text("\n".join(report_lines), encoding="utf-8")
        logger.info(f"报告已保存: {report_path}")

        # 打印摘要
        logger.info("\n" + "=" * 60)
        logger.info("TOP 15 因子 (LightGBM 特征重要性)")
        logger.info("=" * 60)
        for i, row in imp_df.head(15).iterrows():
            logger.info(
                f"  {i+1:2d}. {row['factor']:25s}  {row['importance']:10.2f}  ({row['importance_pct']:5.2f}%)"
            )
        logger.info("=" * 60)


if __name__ == "__main__":
    main()
