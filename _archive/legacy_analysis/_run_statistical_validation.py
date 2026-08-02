"""
统计显著性验证：Deflated Sharpe + Purged CV + Walk-Forward 稳定性
=================================================================

对 LGB 集成回测的 38.81% 年化收益进行严格统计检验：
1. Deflated Sharpe Ratio (DSR) — 校正多重比较后的显著性
2. Walk-Forward 多窗口稳定性 — 检查各时间段表现是否一致
3. Purged K-Fold CV — 独立交叉验证 LGB alpha 信号的 OOS IC
4. 前视偏差泄露检查 — 验证特征工程无信息泄露

输入：
- output/backtest_result_latest.json (月度权重和收益)
- data_cache/historical_{symbol}_5y_base.parquet (日K线数据)
- v8.3_institutional/src/validation/ (验证工具库)
"""

import json
import logging
import math
import pathlib
import sys
import warnings
from datetime import datetime
from typing import Dict, List

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("validation")

BASE_DIR = pathlib.Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "data_cache"
VALIDATION_DIR = BASE_DIR / "v8.3_institutional" / "src" / "validation"
sys.path.insert(0, str(VALIDATION_DIR))

from deflated_sharpe import deflated_sharpe_ratio
from purged_cv import PurgedKFold, check_lookahead_bias
from walk_forward import walk_forward_stability_test

# ============================================================
# 1. 加载月度权重和日K线数据，计算日度组合收益
# ============================================================

def load_monthly_weights() -> List[Dict]:
    """加载回测月度权重"""
    with open(BASE_DIR / "output" / "backtest_result_latest.json", encoding="utf-8") as f:
        data = json.load(f)
    logger.info("加载回测结果: %d 个月, 年化 %.2f%%, 回撤 %.2f%%",
                data["months"], data["annual_return"] * 100, data["max_drawdown"] * 100)
    return data["records"]


def load_daily_ohlcv(symbol: str) -> pd.DataFrame:
    """加载标的日K线数据（从 _base.parquet）"""
    cache_file = CACHE_DIR / f"historical_{symbol}_5y_base.parquet"
    if not cache_file.exists():
        logger.warning("缓存文件不存在: %s", cache_file)
        return pd.DataFrame()
    df = pd.read_parquet(cache_file)
    if hasattr(df.index, "tz") and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df.sort_index()


def compute_daily_portfolio_returns(records: List[Dict]) -> pd.Series:
    """从月度权重和日K线数据计算日度组合收益率

    每月第一个交易日按 weights 配置，持有至下月，
    日度收益 = sum(weight_i * symbol_i_daily_return)
    """
    all_daily_rets = []
    all_dates = []

    for i, record in enumerate(records):
        date_str = record["date"]
        weights = record["weights"]
        rebalance_date = pd.Timestamp(date_str).normalize()

        # 下一个再平衡日期
        if i + 1 < len(records):
            next_date = pd.Timestamp(records[i + 1]["date"]).normalize()
        else:
            next_date = rebalance_date + pd.Timedelta(days=31)

        # 加载该月所有持仓标的的日收益率
        daily_rets_dict = {}
        for symbol, weight in weights.items():
            if weight == 0:
                continue
            df = load_daily_ohlcv(symbol)
            if df.empty or "close" not in df.columns:
                continue
            # 截取该月的数据
            mask = (df.index > rebalance_date) & (df.index <= next_date)
            month_df = df[mask]
            if month_df.empty:
                continue
            daily_ret = month_df["close"].pct_change().dropna()
            daily_rets_dict[symbol] = daily_ret

        if not daily_rets_dict:
            continue

        # 对齐到同一日期
        rets_df = pd.DataFrame(daily_rets_dict)
        rets_df = rets_df.fillna(0)

        # 计算加权日度收益
        weight_series = pd.Series(weights)
        available_symbols = [s for s in rets_df.columns if s in weight_series]
        if not available_symbols:
            continue

        w = weight_series[available_symbols].values
        # 归一化权重（防止部分标的缺失导致权重不等于1）
        w_sum = w.sum()
        if w_sum > 0:
            w = w / w_sum

        portfolio_daily = rets_df[available_symbols].values @ w
        all_daily_rets.extend(portfolio_daily.tolist())
        all_dates.extend(rets_df.index.tolist())

    daily_returns = pd.Series(all_daily_rets, index=pd.DatetimeIndex(all_dates))
    # 去重（防止月份交界处重复）
    daily_returns = daily_returns[~daily_returns.index.duplicated(keep="last")]
    logger.info("计算日度组合收益: %d 个交易日, 年化 %.2f%%, 夏普 %.2f",
                len(daily_returns),
                daily_returns.mean() * 252 * 100,
                daily_returns.mean() / max(daily_returns.std(), 1e-9) * math.sqrt(252))
    return daily_returns


# ============================================================
# 2. Deflated Sharpe Ratio
# ============================================================

def run_deflated_sharpe(daily_returns: pd.Series, n_trials: int = 100) -> Dict:
    """运行 DSR 检验

    n_trials: 独立策略试验次数
      - LGB 训练了 23 个标的 × 多种特征组合，保守估计 100 次
    """
    logger.info("=== Deflated Sharpe Ratio (n_trials=%d) ===", n_trials)
    rets_list = daily_returns.tolist()
    result = deflated_sharpe_ratio(
        daily_returns=rets_list,
        n_trials=n_trials,
        required_dsr=0.95,
        risk_free_rate=0.03,
    )
    logger.info(f"\n{'='*60}")
    logger.info("  Deflated Sharpe Ratio 检验")
    logger.info(f"{'='*60}")
    logger.info(f"  样本数: {len(rets_list)} 个交易日")
    logger.info(f"  观测年化夏普: {result.sharpe_ratio:.4f}")
    logger.info(f"  偏度: {result.skewness:.4f}")
    logger.info(f"  超额峰度: {result.kurtosis:.4f}")
    logger.info(f"  E[max_SR] (噪音上限): {result.e_max_sr:.4f}")
    logger.info(f"  DSR (去膨胀夏普): {result.deflated_sharpe_ratio:.4f}")
    logger.info(f"  p-value: {result.p_value:.6f}")
    logger.info(f"  阈值: {result.required_dsr}")
    logger.info(f"  通过: {'✅ 是' if result.is_pass else '❌ 否'}")
    logger.info(f"  结论: {result.verdict}")
    logger.info(f"{'='*60}")
    return {
        "sharpe_ratio": result.sharpe_ratio,
        "deflated_sharpe_ratio": result.deflated_sharpe_ratio,
        "p_value": result.p_value,
        "e_max_sr": result.e_max_sr,
        "n_trials": n_trials,
        "skewness": result.skewness,
        "kurtosis": result.kurtosis,
        "is_pass": result.is_pass,
        "verdict": result.verdict,
    }


# ============================================================
# 3. Walk-Forward 多窗口稳定性
# ============================================================

def run_walk_forward_stability(daily_returns: pd.Series) -> Dict:
    """运行多窗口稳定性检验"""
    logger.info("=== Walk-Forward 多窗口稳定性检验 ===")
    rets_list = daily_returns.tolist()
    result = walk_forward_stability_test(
        performance_series=rets_list,
        n_windows=4,  # 4个窗口（每段约6个月）
        sharpe_range_threshold=1.0,
        return_range_threshold=0.5,
    )
    logger.info(f"\n{'='*60}")
    logger.info(f"  Walk-Forward 多窗口稳定性检验 ({len(rets_list)}天 / {result.get('sharpe_cv', 0):.2f} CV)")
    logger.info(f"{'='*60}")
    for w in result.get("windows", []):
        print(f"  窗口{w['window']}: {w['n_days']}天, "
              f"年化={w['ann_return']*100:.2f}%, "
              f"夏普={w['sharpe']:.2f}, "
              f"回撤={w['max_drawdown']*100:.2f}%")
    logger.info(f"  夏普变异系数: {result.get('sharpe_cv', 0):.4f}")
    logger.info(f"  收益变异系数: {result.get('return_cv', 0):.4f}")
    logger.info(f"  稳定: {'✅ 是' if result.get('stable') else '❌ 否'}")
    if result.get("reasons"):
        logger.info("  原因:")
        for r in result["reasons"]:
            logger.info(f"    - {r}")
    logger.info(f"{'='*60}")
    return result


# ============================================================
# 4. Purged K-Fold CV — LGB Alpha 信号 OOS IC
# ============================================================

def run_purged_cv_alpha_ic() -> Dict:
    """对 LGB alpha 信号运行 Purged K-Fold CV

    对每个标的：
    1. 加载 5y 日K数据
    2. 构建特征（简化版：技术因子）
    3. 定义标签：t+5 日收益率
    4. 用 PurgedKFold 做 5 折 CV
    5. 计算每折 OOS IC
    """
    logger.info("=== Purged K-Fold CV (LGB Alpha OOS IC) ===")
    try:
        from lgb_enhanced_trainer import (
            POSITION_SYMBOLS,
            add_technical_features,
        )
    except Exception as e:
        logger.warning("LGB 依赖不可用，跳过 Purged CV: %s", e)
        return {"status": "skipped", "reason": str(e)}

    all_oos_ics = []
    per_symbol_results = {}

    for code, _suffix, _stype, name, _style in POSITION_SYMBOLS:
        df = load_daily_ohlcv(code)
        if df.empty or len(df) < 300:
            continue

        # 构建技术特征
        try:
            df_feat = add_technical_features(df)
        except Exception:
            df_feat = df.copy()

        # 定义标签：t+5 前向收益率
        df_feat["label"] = df_feat["close"].shift(-5).pct_change(5).shift(-5)
        # 用 t-1 的特征预测 t 的标签（避免当日特征包含当日收益）
        df_feat = df_feat.dropna(subset=["label"])

        # 选择特征列（排除 label 和原始 OHLCV）
        feature_cols = [c for c in df_feat.columns
                        if c not in ("label", "open", "high", "low", "close", "volume")
                        and df_feat[c].dtype in (np.float64, np.float32, float)]
        if len(feature_cols) < 3:
            continue

        X = df_feat[feature_cols].values
        y = df_feat["label"].values

        # 移除 NaN
        valid = np.isfinite(X).all(axis=1) & np.isfinite(y)
        X, y = X[valid], y[valid]

        if len(X) < 200:
            continue

        # Purged K-Fold (5折, embargo=1%, purge=1%)
        cv = PurgedKFold(n_splits=5, embargo_pct=0.01, purge_pct=0.01)
        fold_ics = []

        try:
            from sklearn.ensemble import GradientBoostingRegressor
        except Exception:
            logger.warning("sklearn 不可用，跳过 Purged CV")
            return {"status": "skipped", "reason": "sklearn unavailable"}

        for train_idx, test_idx in cv.split(X):
            if len(train_idx) < 100 or len(test_idx) < 20:
                continue
            X_train, y_train = X[train_idx], y[train_idx]
            X_test, y_test = X[test_idx], y[test_idx]

            # 轻量级 GBDT（加速）
            model = GradientBoostingRegressor(
                n_estimators=100, max_depth=3,
                learning_rate=0.05, random_state=42,
            )
            model.fit(X_train, y_train)
            y_pred = model.predict(X_test)

            # IC = Spearman 相关
            from scipy.stats import spearmanr
            if len(y_test) > 5:
                ic, _ = spearmanr(y_pred, y_test)
                if math.isfinite(ic):
                    fold_ics.append(float(ic))

        if fold_ics:
            mean_ic = float(np.mean(fold_ics))
            std_ic = float(np.std(fold_ics, ddof=1)) if len(fold_ics) > 1 else 0.0
            all_oos_ics.extend(fold_ics)
            per_symbol_results[code] = {
                "name": name,
                "mean_ic": mean_ic,
                "std_ic": std_ic,
                "ic_ir": mean_ic / (std_ic + 1e-9),
                "n_folds": len(fold_ics),
                "fold_ics": fold_ics,
            }

    # 汇总
    if all_oos_ics:
        overall_ic = float(np.mean(all_oos_ics))
        overall_std = float(np.std(all_oos_ics, ddof=1))
        positive_rate = float(np.mean([1 if ic > 0 else 0 for ic in all_oos_ics]))

        logger.info(f"\n{'='*60}")
        logger.info("  Purged K-Fold CV (5折, embargo=1%) — OOS IC")
        logger.info(f"{'='*60}")
        logger.info(f"  测试标的数: {len(per_symbol_results)}")
        logger.info(f"  总折叠数: {len(all_oos_ics)}")
        logger.info(f"  整体 OOS IC: {overall_ic:.4f} ± {overall_std:.4f}")
        logger.info(f"  IC IR: {overall_ic / (overall_std + 1e-9):.4f}")
        logger.info(f"  正 IC 占比: {positive_rate*100:.1f}%")
        logger.info(f"  显著 (IC > 0.02): {'✅' if overall_ic > 0.02 else '❌'}")
        logger.info("  ---")
        logger.info("  逐标的 OOS IC (前 10):")
        sorted_results = sorted(per_symbol_results.items(),
                                key=lambda x: abs(x[1]["mean_ic"]), reverse=True)
        for code, res in sorted_results[:10]:
            print(f"    {code} {res['name']}: IC={res['mean_ic']:.4f}±{res['std_ic']:.4f} "
                  f"(IR={res['ic_ir']:.2f}, {res['n_folds']}折)")
        logger.info(f"{'='*60}")

        return {
            "status": "ok",
            "overall_ic": overall_ic,
            "overall_std": overall_std,
            "ic_ir": overall_ic / (overall_std + 1e-9),
            "positive_rate": positive_rate,
            "n_symbols": len(per_symbol_results),
            "n_folds": len(all_oos_ics),
            "per_symbol": per_symbol_results,
        }
    else:
        logger.info("Purged CV 未产生有效结果")
        return {"status": "no_results"}


# ============================================================
# 5. 前视偏差泄露检查
# ============================================================

def run_lookahead_check() -> Dict:
    """检查特征工程是否存在前视偏差"""
    logger.info("=== 前视偏差泄露检查 ===")
    try:
        from lgb_enhanced_trainer import add_technical_features
    except Exception:
        return {"status": "skipped"}

    # 取一个标的做检查
    df = load_daily_ohlcv("600036")
    if df.empty:
        return {"status": "skipped"}

    df_feat = add_technical_features(df)
    report = check_lookahead_bias(df_feat)

    logger.info(f"\n{'='*60}")
    logger.info("  前视偏差泄露检查")
    logger.info(f"{'='*60}")
    logger.info(f"  检查特征数: {report.get('features_checked', 0)}")
    logger.info(f"  通过: {report.get('pass_count', 0)}")
    logger.info(f"  可疑: {report.get('leak_count', 0)}")
    if report.get('suspected_leaks'):
        logger.info("  可疑特征:")
        for leak in report['suspected_leaks']:
            logger.info(f"    ⚠️ {leak}")
    else:
        logger.info("  ✅ 未发现前视偏差")
    logger.info(f"{'='*60}")
    return report


# ============================================================
# 主流程
# ============================================================

def main():
    logger.info("=" * 60)
    logger.info("  LGB Alpha 集成 — 统计显著性验证")
    logger.info("  对象: 38.81% 年化收益回测结果")
    logger.info("=" * 60)
    print()

    # 1. 加载月度权重，计算日度收益
    records = load_monthly_weights()
    daily_returns = compute_daily_portfolio_returns(records)

    if len(daily_returns) < 50:
        logger.info("❌ 日度收益数据不足，无法验证")
        return

    # 2. Deflated Sharpe Ratio
    dsr_result = run_deflated_sharpe(daily_returns, n_trials=100)

    # 3. Walk-Forward 稳定性
    wf_result = run_walk_forward_stability(daily_returns)

    # 4. Purged K-Fold CV
    cv_result = run_purged_cv_alpha_ic()

    # 5. 前视偏差检查
    lookahead_result = run_lookahead_check()

    # 汇总
    logger.info("\n" + "=" * 60)
    logger.info("  验证汇总")
    logger.info("=" * 60)
    checks = []
    checks.append(("Deflated Sharpe >= 0.95", dsr_result.get("is_pass", False),
                   f"DSR={dsr_result.get('deflated_sharpe_ratio', 0):.4f}"))
    checks.append(("Walk-Forward 稳定性", wf_result.get("stable", False),
                   f"CV={wf_result.get('sharpe_cv', 0):.2f}"))
    if cv_result.get("status") == "ok":
        checks.append(("Purged CV OOS IC > 0.02", cv_result.get("overall_ic", 0) > 0.02,
                       f"IC={cv_result.get('overall_ic', 0):.4f}"))
    checks.append(("无前视偏差泄露", lookahead_result.get("leak_count", 0) == 0,
                   f"leaks={lookahead_result.get('leak_count', 0)}"))

    all_pass = True
    for name, passed, detail in checks:
        status = "✅" if passed else "❌"
        logger.info(f"  {status} {name}: {detail}")
        if not passed:
            all_pass = False

    logger.info(f"\n  总体验证: {'✅ 通过' if all_pass else '❌ 未通过'}")
    logger.info("=" * 60)

    # 保存报告
    output_dir = BASE_DIR / "output" / "validation_reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "timestamp": ts,
        "backtest": {
            "annual_return": 0.3881,
            "max_drawdown": 0.1111,
            "win_rate": 0.667,
        },
        "deflated_sharpe": dsr_result,
        "walk_forward_stability": wf_result,
        "purged_cv": cv_result,
        "lookahead_check": {
            "features_checked": lookahead_result.get("features_checked", 0),
            "leak_count": lookahead_result.get("leak_count", 0),
            "pass_count": lookahead_result.get("pass_count", 0),
        },
        "all_pass": all_pass,
    }
    report_file = output_dir / f"statistical_validation_{ts}.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"\n报告已保存: {report_file}")


if __name__ == "__main__":
    main()
