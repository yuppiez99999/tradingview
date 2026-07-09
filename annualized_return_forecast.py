# -*- coding: utf-8 -*-
"""
年化收益预测器 + 成本感知回测 v1.0

三维度预测:
1. ML 信号预测: 基于 QLib 模型信号 → 预期超额收益
2. 历史统计: 滚动 252 日均值 → 年化基准
3. 因子分解: 动量 + 价值 + 质量 → 预期收益分解

回测: 成本感知回测 (佣金 + 印花税 + 市场冲击)

使用:
    python annualized_return_forecast.py
"""
import os
import sys
import json
import math
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import logging

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger('forecast')

# 持仓标的 (从 500万建仓计划)
PORTFOLIO = {
    # ETF
    "510300": {"name": "沪深300ETF", "weight": 0.08, "sector": "核心宽基"},
    "510500": {"name": "中证500ETF", "weight": 0.05, "sector": "核心宽基"},
    "588000": {"name": "科创50ETF", "weight": 0.05, "sector": "核心宽基"},
    "518880": {"name": "黄金ETF", "weight": 0.11, "sector": "黄金"},
    # 科技/AI
    "688041": {"name": "海光信息", "weight": 0.04, "sector": "科技"},
    "300308": {"name": "中际旭创", "weight": 0.05, "sector": "科技"},
    "002371": {"name": "北方华创", "weight": 0.04, "sector": "科技"},
    "688981": {"name": "中芯国际", "weight": 0.04, "sector": "科技"},
    "300274": {"name": "阳光电源", "weight": 0.04, "sector": "科技"},
    "603019": {"name": "中科曙光", "weight": 0.04, "sector": "科技"},
    "600276": {"name": "恒瑞医药", "weight": 0.04, "sector": "科技"},
    # 高端制造
    "000425": {"name": "徐工机械", "weight": 0.04, "sector": "制造"},
    "600089": {"name": "特变电工", "weight": 0.04, "sector": "制造"},
    "688017": {"name": "绿的谐波", "weight": 0.03, "sector": "制造"},
    # 防御/红利
    "600900": {"name": "长江电力", "weight": 0.06, "sector": "防御"},
    "601088": {"name": "中国神华", "weight": 0.05, "sector": "防御"},
    "600019": {"name": "宝钢股份", "weight": 0.05, "sector": "资源"},
    "600219": {"name": "南山铝业", "weight": 0.05, "sector": "资源"},
    "600036": {"name": "招商银行", "weight": 0.04, "sector": "金融"},
    "515180": {"name": "红利ETF", "weight": 0.06, "sector": "防御"},
}

# 无风险利率 (中国 10 年期国债)
RF_RATE = 0.025

# QLib bin 数据目录
QLIB_DATA_DIR = os.path.join(os.path.dirname(__file__), "qlib_data", "cn_data")


def code_to_qlib(code: str) -> str:
    """股票代码转 QLib 格式: 600019 → sh600019, 000425 → sz000425"""
    if code.startswith("6"):
        return f"sh{code}"
    elif code.startswith(("0", "3")):
        return f"sz{code}"
    elif code.startswith("5"):  # ETF
        return f"sh{code}"
    elif code.startswith("1"):  # 深市 ETF
        return f"sz{code}"
    return f"sh{code}"


def load_qlib_bin(code: str, field: str = "close") -> pd.Series:
    """从 QLib bin 文件加载价格数据"""
    qlib_code = code_to_qlib(code)
    path = os.path.join(QLIB_DATA_DIR, "features", qlib_code, f"{field}.day.bin")
    if not os.path.exists(path):
        return pd.Series()

    # QLib bin 格式: header (36 bytes) + float32 values
    raw = np.fromfile(path, dtype="<f4")
    # 跳过前 9 个 float (header)
    values = raw[9:]
    # 过滤无效值
    values = np.where(values > 0, values, np.nan)

    # 加载日历
    cal_path = os.path.join(QLIB_DATA_DIR, "calendars", "day.txt")
    if os.path.exists(cal_path):
        with open(cal_path, "r") as f:
            dates = [line.strip() for line in f if line.strip()]
    else:
        dates = pd.bdate_range("2015-01-01", periods=len(values)).strftime("%Y-%m-%d").tolist()

    if len(values) > len(dates):
        values = values[:len(dates)]
    elif len(values) < len(dates):
        dates = dates[:len(values)]

    s = pd.Series(values, index=pd.to_datetime(dates), name=code)
    # 去除尾部 NaN
    s = s.dropna()
    return s


def compute_historical_stats(prices: pd.Series, window: int = 252) -> dict:
    """计算历史统计指标"""
    if len(prices) < 20:
        return {}

    # 基础价格校验
    if prices.iloc[0] <= 0 or (hasattr(prices, 'iloc') and prices.iloc[-1] <= 0):
        logger.warning("  [SKIP] 价格序列含非正值，无法计算统计指标")
        return {}

    returns = prices.pct_change().dropna()
    if len(returns) < 10:
        logger.warning("  [SKIP] 有效收益样本不足 (%d < 10)", len(returns))
        return {}

    # 年化收益 (252 日均值)
    annual_return = returns.mean() * 252
    if not np.isfinite(annual_return):
        logger.warning("  [SKIP] 年化收益计算失败 (NaN/Inf)")
        return {}

    # 年化波动率
    annual_vol = returns.std() * np.sqrt(252)
    # 夏普比率
    sharpe = (annual_return - RF_RATE) / annual_vol if annual_vol > 1e-6 else 0.0

    # 最大回撤
    try:
        cumret = (1 + returns).cumprod()
        if not np.isfinite(cumret).all():
            logger.warning("  [SKIP] 累计收益序列含非法值")
            return {}
        rolling_max = cumret.expanding().max()
        max_dd = ((cumret - rolling_max) / rolling_max).min()
        if not np.isfinite(max_dd):
            logger.warning("  [SKIP] 最大回撤计算失败 (NaN/Inf)")
            return {}
    except Exception as exc:
        logger.warning("  [SKIP] 最大回撤计算失败: %s", exc)
        return {}

    # Sortino
    downside = returns[returns < 0]
    downside_std = downside.std() if len(downside) > 0 else np.nan
    sortino = (annual_return - RF_RATE) / (downside_std * np.sqrt(252)) if np.isfinite(downside_std) and downside_std > 1e-6 else 0.0

    # Calmar
    calmar = annual_return / abs(max_dd) if max_dd < 0 and abs(max_dd) > 1e-6 else 0.0

    # 偏度和峰度
    try:
        skew = returns.skew()
        kurtosis = returns.kurtosis()
    except Exception:
        skew = 0.0
        kurtosis = 0.0

    return {
        "annual_return": round(float(annual_return), 4),
        "annual_vol": round(float(annual_vol), 4),
        "sharpe": round(float(sharpe), 3),
        "sortino": round(float(sortino), 3),
        "calmar": round(float(calmar), 3),
        "max_drawdown": round(float(max_dd), 4),
        "skewness": round(float(skew), 3),
        "kurtosis": round(float(kurtosis), 3),
        "n_days": len(returns),
        "data_start": returns.index[0].strftime("%Y-%m-%d"),
        "data_end": returns.index[-1].strftime("%Y-%m-%d"),
    }


def load_ml_signals() -> Dict[str, float]:
    """加载最新 ML 模型预测信号"""
    # 尝试加载改进版训练结果
    report_dir = os.path.join(os.path.dirname(__file__), "reports")
    if os.path.exists(report_dir):
        for f in sorted(os.listdir(report_dir), reverse=True):
            if f.startswith("qlib_improved_train_") and f.endswith(".json"):
                path = os.path.join(report_dir, f)
                with open(path, "r", encoding="utf-8") as fh:
                    report = json.load(fh)
                signals = report.get("signals", {})
                if signals:
                    logger.info(f"加载 ML 信号: {f} ({len(signals)} 标的)")
                    return {s["symbol"]: s["raw_signal"] for s in signals if "raw_signal" in s}
    return {}


def forecast_annualized_return() -> dict:
    """预测组合年化收益率

    三维度预测:
    1. 历史统计: 各标的滚动 252 日年化收益 → 组合加权
    2. ML 信号: QLib 模型信号 → 信号强度 × 历史波动率 → 预期超额收益
    3. 因子分解: 板块动量 + 价值均值 + 质量调整

    最终: 50% 历史 + 30% ML + 20% 因子
    """
    logger.info("=" * 70)
    logger.info("年化收益预测器 v1.0")
    logger.info("=" * 70)

    # 加载 ML 信号
    ml_signals = load_ml_signals()

    # 加载各标的数据
    results = []
    portfolio_hist_return = 0.0
    portfolio_ml_return = 0.0
    portfolio_factor_return = 0.0
    portfolio_vol = 0.0
    total_weight = 0.0

    for code, info in PORTFOLIO.items():
        name = info["name"]
        weight = info["weight"]
        sector = info["sector"]

        # 加载价格
        prices = load_qlib_bin(code, "close")
        if len(prices) < 60:
            logger.warning(f"  {name} ({code}) 数据不足: {len(prices)} 天, 跳过")
            continue

        # 历史统计
        stats = compute_historical_stats(prices)
        if not stats:
            logger.warning(f"  {name} ({code}) 统计指标计算失败, 跳过")
            continue

        # ML 信号
        ml_signal = ml_signals.get(code, 0.0)

        # 信号 → 预期超额收益
        # 信号 [-1, 1] × 年化波动率 × 0.3 (信号衰减系数)
        try:
            ml_expected_excess = ml_signal * stats["annual_vol"] * 0.3
        except Exception as exc:
            logger.warning(f"  {name} ({code}) ML 预期收益计算失败: {exc}, 跳过")
            continue
        ml_expected_return = RF_RATE + ml_expected_excess

        # 因子分解 (简化版)
        # 板块因子: 科技 1.05x, 制造 1.0x, 防御 0.8x, 资源 1.1x, 黄金 0.7x
        sector_factor = {
            "科技": 1.05, "制造": 1.0, "防御": 0.85,
            "资源": 1.1, "黄金": 0.7, "金融": 0.9, "核心宽基": 1.0
        }.get(sector, 1.0)

        # 动量因子: 近 60 日收益率年化
        recent_60 = prices.iloc[-60:]
        if recent_60.iloc[0] <= 0:
            logger.warning(f"  {name} ({code}) 近60日起始价格为0或负，跳过动量计算")
            momentum_return = 0.0
        else:
            momentum_return = (recent_60.iloc[-1] / recent_60.iloc[0] - 1) * (252 / 60)
            # 异常值保护
            if not np.isfinite(momentum_return) or abs(momentum_return) > 10.0:
                logger.warning(f"  {name} ({code}) 动量收益异常: {momentum_return*100:.2f}%，已归零")
                momentum_return = 0.0

        # 因子预期: 板块因子 × 历史均值 × 0.5 + 动量 × 0.5
        factor_expected = (stats["annual_return"] * sector_factor * 0.5 +
                          momentum_return * 0.5)

        # 综合预期: 50% 历史 + 30% ML + 20% 因子
        blended_return = (0.50 * stats["annual_return"] +
                         0.30 * ml_expected_return +
                         0.20 * factor_expected)

        results.append({
            "code": code,
            "name": name,
            "weight": weight,
            "sector": sector,
            "hist_return": stats["annual_return"],
            "hist_vol": stats["annual_vol"],
            "hist_sharpe": stats["sharpe"],
            "ml_signal": round(ml_signal, 4),
            "ml_expected": round(ml_expected_return, 4),
            "factor_expected": round(factor_expected, 4),
            "blended_forecast": round(blended_return, 4),
            "data_range": f"{stats['data_start']} ~ {stats['data_end']}",
            "n_days": stats["n_days"],
        })

        # 组合加权
        portfolio_hist_return += stats["annual_return"] * weight
        portfolio_ml_return += ml_expected_return * weight
        portfolio_factor_return += factor_expected * weight
        portfolio_vol += (stats["annual_vol"] * weight) ** 2  # 简化: 忽略相关性
        total_weight += weight

    # 组合统计
    portfolio_vol = np.sqrt(portfolio_vol) if portfolio_vol > 0 else 0
    blended_portfolio = (0.50 * portfolio_hist_return +
                        0.30 * portfolio_ml_return +
                        0.20 * portfolio_factor_return)

    # 成本拖累 (估算)
    # 年换手率 3x, 平均成本 15bps
    cost_drag = 3.0 * 0.0015  # ~0.45%

    # 对冲成本
    hedge_cost = 0.003  # ~0.3% (期货保证金成本)

    # 最终预期
    net_return = blended_portfolio - cost_drag - hedge_cost
    net_sharpe = (net_return - RF_RATE) / portfolio_vol if portfolio_vol > 0 else 0

    report = {
        "timestamp": datetime.now().isoformat(),
        "portfolio": {
            "total_weight": round(total_weight, 4),
            "n_assets": len(results),
            "rf_rate": RF_RATE,
        },
        "historical_forecast": {
            "portfolio_annual_return": round(portfolio_hist_return, 4),
            "portfolio_volatility": round(portfolio_vol, 4),
            "portfolio_sharpe": round((portfolio_hist_return - RF_RATE) / portfolio_vol, 3) if portfolio_vol > 0 else 0,
        },
        "ml_forecast": {
            "portfolio_annual_return": round(portfolio_ml_return, 4),
            "signals_loaded": len(ml_signals),
        },
        "factor_forecast": {
            "portfolio_annual_return": round(portfolio_factor_return, 4),
        },
        "blended_forecast": {
            "gross_return": round(blended_portfolio, 4),
            "cost_drag": round(cost_drag, 4),
            "hedge_cost": round(hedge_cost, 4),
            "net_return": round(net_return, 4),
            "net_sharpe": round(net_sharpe, 3),
            "rf_rate": RF_RATE,
            "excess_return": round(net_return - RF_RATE, 4),
        },
        "assets": results,
    }

    return report


def run_cost_aware_backtest() -> dict:
    """运行成本感知回测"""
    logger.info("\n" + "=" * 70)
    logger.info("成本感知回测 v1.0")
    logger.info("=" * 70)

    # 加载价格数据
    prices_dict = {}
    for code in PORTFOLIO:
        p = load_qlib_bin(code, "close")
        if len(p) > 60:
            prices_dict[code] = p

    if len(prices_dict) < 5:
        logger.error(f"价格数据不足: {len(prices_dict)} 标的")
        return {"error": "insufficient_data", "n_assets": len(prices_dict)}

    prices_df = pd.DataFrame(prices_dict)
    # 只用 2018 年以后的数据 (避免早期缺失)
    prices_df = prices_df[prices_df.index >= "2018-01-01"]
    prices_df = prices_df.dropna(how="all").fillna(method="ffill").fillna(0)

    # 目标权重
    target_w = pd.Series({c: PORTFOLIO[c]["weight"] for c in prices_df.columns})
    # 归一化
    target_w = target_w / target_w.sum()

    # 日成交量 (从 QLib 加载)
    vol_dict = {}
    for code in prices_df.columns:
        v = load_qlib_bin(code, "volume")
        if len(v) > 60:
            vol_dict[code] = v
    vol_df = pd.DataFrame(vol_dict).reindex(prices_df.index).fillna(1e6)

    # 运行成本感知回测
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "v7.5_institutional"))
    from src.backtest.cost_aware_backtest import CostAwareBacktest

    bt = CostAwareBacktest(initial_capital=5_000_000)

    # 创建目标权重 DataFrame (每月再平衡)
    rebal_dates = prices_df.resample("BM").last().index
    target_weights = pd.DataFrame(
        [target_w] * len(rebal_dates),
        index=rebal_dates,
        columns=prices_df.columns,
    )

    result = bt.run_strategy(
        prices=prices_df,
        target_weights=target_weights,
        rebalance_threshold=0.03,
        daily_volumes=vol_df,
    )

    # 对比无成本
    comparison = bt.compare_with_no_cost(result)

    return {
        "backtest_period": {
            "start": prices_df.index[0].strftime("%Y-%m-%d"),
            "end": prices_df.index[-1].strftime("%Y-%m-%d"),
            "n_days": len(prices_df),
        },
        "returns": {
            "total_return": round(result.total_return, 4),
            "annual_return_gross": round(result.annual_return, 4),
            "max_drawdown": round(result.max_drawdown, 4),
            "volatility": round(result.volatility, 4),
            "sharpe_ratio": round(result.sharpe_ratio, 3),
            "sortino_ratio": round(result.sortino_ratio, 3),
            "calmar_ratio": round(result.calmar_ratio, 3),
        },
        "costs": {
            "total_cost": round(result.total_cost, 2),
            "commission": round(result.total_commission, 2),
            "stamp_duty": round(result.total_stamp_duty, 2),
            "market_impact": round(result.total_market_impact, 2),
            "cost_as_pct": round(result.cost_as_return_pct, 4),
            "n_trades": result.n_trades,
            "avg_cost_per_trade": round(result.avg_cost_per_trade, 2),
            "turnover": round(result.turnover, 2),
        },
        "comparison": {
            "return_with_cost": round(comparison["total_return_with_cost"], 4),
            "return_without_cost": round(comparison["total_return_without_cost"], 4),
            "cost_drag": round(comparison["cost_drag"], 4),
            "cost_breakdown": comparison["cost_breakdown"],
        },
    }


def main():
    # 1. 年化收益预测
    forecast = forecast_annualized_return()

    # 打印预测结果
    logger.info("\n" + "=" * 70)
    logger.info("各标的年化收益预测")
    logger.info("=" * 70)
    logger.info(f"{'代码':<8} {'名称':<10} {'权重':>6} {'历史σ':>8} {'历史收益':>8} {'ML信号':>8} {'综合预测':>8}")
    logger.info("─" * 70)

    for a in forecast["assets"]:
        logger.info(
            f"{a['code']:<8} {a['name']:<10} {a['weight']:>5.1%} "
            f"{a['hist_vol']:>7.1%} {a['hist_return']:>+7.1%} "
            f"{a['ml_signal']:>+7.3f} {a['blended_forecast']:>+7.1%}"
        )

    logger.info("─" * 70)
    bf = forecast["blended_forecast"]
    logger.info(f"{'组合':<8} {'':<10} {forecast['portfolio']['total_weight']:>5.1%} "
                f"{'':>8} {'':>8} {'':>8} {bf['gross_return']:>+7.1%}")
    logger.info(f"\n毛收益:   {bf['gross_return']:.2%}")
    logger.info(f"成本拖累: -{bf['cost_drag']:.2%}")
    logger.info(f"对冲成本: -{bf['hedge_cost']:.2%}")
    logger.info(f"净收益:   {bf['net_return']:.2%}")
    logger.info(f"超额收益: {bf['excess_return']:.2%} (vs 国债 {bf['rf_rate']:.2%})")
    logger.info(f"净夏普:   {bf['net_sharpe']:.3f}")

    # 2. 成本感知回测
    bt_result = run_cost_aware_backtest()

    if "error" not in bt_result:
        logger.info("\n" + "=" * 70)
        logger.info("成本感知回测结果")
        logger.info("=" * 70)
        r = bt_result["returns"]
        c = bt_result["costs"]
        comp = bt_result["comparison"]
        logger.info(f"回测区间: {bt_result['backtest_period']['start']} ~ {bt_result['backtest_period']['end']}")
        logger.info(f"总收益:   {r['total_return']:.2%}")
        logger.info(f"年化收益: {r['annual_return_gross']:.2%}")
        logger.info(f"最大回撤: {r['max_drawdown']:.2%}")
        logger.info(f"波动率:   {r['volatility']:.2%}")
        logger.info(f"夏普比率: {r['sharpe_ratio']:.3f}")
        logger.info(f"Sortino:  {r['sortino_ratio']:.3f}")
        logger.info(f"Calmar:   {r['calmar_ratio']:.3f}")
        logger.info(f"\n--- 成本分析 ---")
        logger.info(f"总成本:       {c['total_cost']:,.0f} 元 ({c['cost_as_pct']:.2%})")
        logger.info(f"  佣金:       {c['commission']:,.0f} 元")
        logger.info(f"  印花税:     {c['stamp_duty']:,.0f} 元")
        logger.info(f"  市场冲击:   {c['market_impact']:,.0f} 元")
        logger.info(f"  交易笔数:   {c['n_trades']}")
        logger.info(f"  平均成本:   {c['avg_cost_per_trade']:,.0f} 元/笔")
        logger.info(f"  换手率:     {c['turnover']:.1f}x")
        logger.info(f"\n--- 有无成本对比 ---")
        logger.info(f"含成本收益:   {comp['return_with_cost']:.2%}")
        logger.info(f"无成本收益:   {comp['return_without_cost']:.2%}")
        logger.info(f"成本拖累:     -{comp['cost_drag']:.2%}")

    # 3. 保存报告
    report = {
        "timestamp": datetime.now().isoformat(),
        "forecast": forecast,
        "backtest": bt_result,
    }

    report_path = os.path.join(
        os.path.dirname(__file__), "reports",
        f"annualized_return_forecast_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)

    logger.info(f"\n报告已保存: {report_path}")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
