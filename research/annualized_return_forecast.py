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
import json
import logging
import os
import sys
from datetime import datetime
from typing import Dict

import numpy as np
import pandas as pd

# 统一成本模型（全系统唯一成本来源，禁止本地硬编码）
from utils.cost_model import get_cost_model

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger('forecast')

# 持仓标的 (从 config/positions.json 同步 - 22标的, 500万计划)
# 说明: 已剔除数据过期严重的同花顺、卓胜微、藏格矿业，权重已重新分配
PORTFOLIO = {
    # ETF - 科技
    "588080": {"name": "科创50ETF易方达", "weight": 0.05, "sector": "科技"},
    "512760": {"name": "半导体ETF国泰", "weight": 0.03, "sector": "科技"},
    "588000": {"name": "科创50ETF华夏", "weight": 0.03, "sector": "科技"},
    # ETF - 金融
    "512880": {"name": "证券ETF国泰", "weight": 0.05, "sector": "金融"},
    "512800": {"name": "银行ETF华宝", "weight": 0.06, "sector": "金融"},
    # ETF - 宽基
    "510050": {"name": "上证50ETF华夏", "weight": 0.06, "sector": "核心宽基"},
    "510300": {"name": "沪深300ETF华泰", "weight": 0.03, "sector": "核心宽基"},
    "510500": {"name": "中证500ETF南方", "weight": 0.03, "sector": "核心宽基"},
    "512100": {"name": "中证1000ETF", "weight": 0.02, "sector": "核心宽基"},
    # ETF - 新能源/医药/资源/成长
    "515030": {"name": "新能源车ETF华夏", "weight": 0.05, "sector": "新能源"},
    "512170": {"name": "医疗ETF华宝", "weight": 0.07, "sector": "医药"},
    "518880": {"name": "黄金ETF华安", "weight": 0.05, "sector": "黄金"},
    "159915": {"name": "创业板ETF易方达", "weight": 0.02, "sector": "成长"},
    # 股票 - 科技
    "688041": {"name": "海光信息", "weight": 0.05, "sector": "科技"},
    "300308": {"name": "中际旭创", "weight": 0.05, "sector": "科技"},
    "002371": {"name": "北方华创", "weight": 0.05, "sector": "科技"},
    "603019": {"name": "中科曙光", "weight": 0.04, "sector": "科技"},
    # 股票 - 制造/新能源
    "688017": {"name": "绿的谐波", "weight": 0.05, "sector": "制造"},
    "300274": {"name": "阳光电源", "weight": 0.05, "sector": "新能源"},
    # 股票 - 顺周期/医药/防御
    "601088": {"name": "中国神华", "weight": 0.05, "sector": "顺周期"},
    "600276": {"name": "恒瑞医药", "weight": 0.06, "sector": "医药"},
    "600900": {"name": "长江电力", "weight": 0.05, "sector": "防御"},
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


def load_wind_mcp_data(code: str, days: int = 1200) -> pd.Series:
    """从 Wind MCP 加载价格数据作为第一优先数据源"""
    try:
        import wind_mcp_fetcher as wm

        is_fund = code.startswith("5") or code.startswith("1599")
        windcode = f"{code}.SH" if code.startswith("6") or code.startswith("5") or code.startswith("11") or code.startswith("13") else f"{code}.SZ"
        if code.startswith("4") or code.startswith("8"):
            windcode = f"{code}.SH"
        elif code.startswith("0") or code.startswith("3") or code.startswith("1599"):
            windcode = f"{code}.SZ"
        recs = wm.wind_get_kline(windcode, days=days, is_fund=is_fund)
        if not recs:
            return pd.Series()
        rows = []
        for r in recs:
            dt = r.get("_DATE") or r.get("TIME")
            close = r.get("MATCH") or r.get("CLOSE")
            if not dt or close is None:
                continue
            rows.append({"日期": str(dt)[:10], "收盘": float(close)})
        if not rows:
            return pd.Series()
        df = pd.DataFrame(rows)
        df["日期"] = pd.to_datetime(df["日期"])
        df = df.sort_values("日期").drop_duplicates("日期")
        s = df.set_index("日期")["收盘"].sort_index()
        return s[s > 0].dropna()
    except Exception as exc:
        logger.debug(f"  Wind MCP 加载失败 {code}: {exc}")
        return pd.Series()


def load_akshare_data(code: str, start_date: str = "2018-01-01", end_date: str = None) -> pd.Series:
    """从 akshare 加载价格数据作为 QLib 缺失时的备选"""
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")

    try:
        import akshare as ak

        if code.startswith("5") or code.startswith("1599"):
            etf_hist_df = pd.DataFrame()
            try:
                etf_hist_df = ak.fund_etf_hist_sina(symbol=code)
            except Exception as exc_sina:
                logger.debug(f"  akshare 新浪 ETF 失败 {code}: {exc_sina}")
            if etf_hist_df.empty:
                try:
                    etf_hist_df = ak.fund_etf_hist_em(
                        symbol=code, period="daily", start_date=start_date, end_date=end_date, adjust="hfq"
                    )
                except Exception as exc_em:
                    logger.debug(f"  akshare 东方财富 ETF 失败 {code}: {exc_em}")
            if etf_hist_df.empty:
                return pd.Series()

            etf_hist_df = etf_hist_df.copy()
            etf_hist_df["日期"] = pd.to_datetime(etf_hist_df["日期"])
            etf_hist_df = etf_hist_df.set_index("日期")
            s = etf_hist_df["收盘"].sort_index()
        else:
            stock_zh_a_hist_df = ak.stock_zh_a_hist(
                symbol=code, period="daily", start_date=start_date, end_date=end_date, adjust="hfq"
            )
            if stock_zh_a_hist_df.empty:
                return pd.Series()
            stock_zh_a_hist_df["日期"] = pd.to_datetime(stock_zh_a_hist_df["日期"])
            stock_zh_a_hist_df = stock_zh_a_hist_df.set_index("日期")
            s = stock_zh_a_hist_df["收盘"].sort_index()

        s = s[s > 0].dropna()
        return s
    except Exception as exc:
        logger.debug(f"  akshare 加载失败 {code}: {exc}")
        return pd.Series()


def load_ifind_data(code: str, days: int = 800) -> pd.Series:
    """从 iFinD MCP 加载价格数据作为 QLib/akshare 失败时的回退"""
    try:
        from utils.ifind_client import IFindClient
        client = IFindClient()
        if code.startswith("5") or code.startswith("1599"):
            raw = client.get_etf_historical(code, days=days)
            if not raw:
                return pd.Series()
            df = pd.DataFrame(raw)
            df["日期"] = pd.to_datetime(df["日期"])
            s = df.set_index("日期")["收盘"].sort_index()
        else:
            raw = client.get_historical_klines(code, days=days)
            if not raw:
                return pd.Series()
            df = pd.DataFrame(raw)
            df["日期"] = pd.to_datetime(df["日期"])
            s = df.set_index("日期")["收盘"].sort_index()
        return s[s > 0].dropna()
    except Exception as exc:
        logger.debug(f"  iFinD 加载失败 {code}: {exc}")
        return pd.Series()


def load_local_etf_fallback(code: str) -> pd.Series:
    """从本地 ETF 兜底目录加载价格数据"""
    base_dir = os.path.join(os.path.dirname(__file__), "data", "etf_fallback")
    candidates = [
        os.path.join(base_dir, f"{code}.json"),
        os.path.join(base_dir, f"{code}.csv"),
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            if path.endswith(".json"):
                with open(path, "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
                records = payload.get("prices") or payload.get("data") or []
                if not records:
                    return pd.Series()
                df = pd.DataFrame(records)
                df["日期"] = pd.to_datetime(df["日期"])
                s = df.set_index("日期")["收盘"].sort_index()
                return s[s > 0].dropna()
            else:
                df = pd.read_csv(path)
                df["日期"] = pd.to_datetime(df["日期"])
                s = df.set_index("日期")["收盘"].sort_index()
                return s[s > 0].dropna()
        except Exception as exc:
            logger.debug(f"  本地ETF兜底失败 {code}: {exc}")
    return pd.Series()


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
    report_dir = os.path.join(os.path.dirname(__file__), "reports")
    if os.path.exists(report_dir):
        for f in sorted(os.listdir(report_dir), reverse=True):
            if f.startswith("qlib_improved_train_") and f.endswith(".json"):
                path = os.path.join(report_dir, f)
                with open(path, "r", encoding="utf-8") as fh:
                    report = json.load(fh)
                signals = report.get("stock_signals", [])
                if signals:
                    result = {}
                    for s in signals:
                        code = s.get("code", "")
                        if code:
                            clean_code = code.lstrip("SH").lstrip("SZ")
                            result[clean_code] = s.get("latest_signal", 0.0)
                    logger.info(f"加载 ML 信号: {f} ({len(result)} 标的)")
                    return result
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

    # 收集各标的波动率与权重用于协方差矩阵
    asset_vols = []
    asset_weights = []

    for code, info in PORTFOLIO.items():
        name = info["name"]
        weight = info["weight"]
        sector = info["sector"]

        # 加载价格：优先 Wind MCP，回退 QLib，再回退 akshare，再回退 iFinD MCP，最后回退本地 ETF 兜底
        prices = load_wind_mcp_data(code)

        if len(prices) < 60:
            prices = load_qlib_bin(code, "close")

        if len(prices) < 60:
            prices = load_akshare_data(code)

        if len(prices) < 60:
            prices = load_ifind_data(code)

        if len(prices) < 60 and (code.startswith("5") or code.startswith("1599")):
            prices = load_local_etf_fallback(code)

        if len(prices) < 60:
            logger.warning(f"  {name} ({code}) 数据不足: {len(prices)} 天, 跳过")
            continue

        # 检查数据是否过期（超过1年）
        last_date = prices.index[-1]
        days_since_last = (datetime.now() - last_date).days
        if days_since_last > 365:
            logger.warning(f"  {name} ({code}) 数据过期: {last_date.date()}, 已 {days_since_last} 天, 跳过")
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
            "资源": 1.1, "黄金": 0.7, "金融": 0.9, "核心宽基": 1.0,
            "新能源": 1.08, "医药": 0.95, "顺周期": 0.9, "成长": 1.02
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
        asset_vols.append(stats["annual_vol"])
        asset_weights.append(weight)
        total_weight += weight

    # 组合波动率: 常相关性模型 (ρ=0.55)
    # portfolio_var = (1-ρ) * Σ(w_i² * σ_i²) + ρ * (Σ w_i * σ_i)²
    # 相比 ρ=0 简化版, 此模型可捕捉ETF间和股票间的高相关性
    DEFAULT_RHO = 0.55
    if len(asset_vols) > 1:
        asset_vols_arr = np.array(asset_vols)
        asset_weights_arr = np.array(asset_weights)
        w_sigma_sq = np.sum((asset_weights_arr * asset_vols_arr) ** 2)
        w_sigma_sum = np.sum(asset_weights_arr * asset_vols_arr)
        portfolio_var = (1 - DEFAULT_RHO) * w_sigma_sq + DEFAULT_RHO * (w_sigma_sum ** 2)
        portfolio_vol = np.sqrt(max(portfolio_var, 0))
    elif len(asset_vols) == 1:
        portfolio_vol = asset_vols[0] * asset_weights[0]
    else:
        portfolio_vol = 0
    blended_portfolio = (0.50 * portfolio_hist_return +
                        0.30 * portfolio_ml_return +
                        0.20 * portfolio_factor_return)

    # 成本拖累：统一成本模型（此前错误地取 0.45%，严重低估；现已并入
    # 佣金/印花税/冲击/期权覆盖/期货对冲，年化约 2.4%，全系统唯一来源）
    cost_model = get_cost_model()
    cost_breakdown = cost_model.breakdown()
    cost_drag = cost_breakdown["total"]   # 年化总成本
    hedge_cost = 0.0                        # 对冲成本已并入 cost_model.annual_total_cost

    # 最终预期（净收益必须扣除统一年化成本后再报）
    net_return = cost_model.net_return(blended_portfolio)
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
            "cost_breakdown": {k: round(v, 4) for k, v in cost_breakdown.items()},
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
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "v8.3_institutional"))
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
        logger.info("\n--- 成本分析 ---")
        logger.info(f"总成本:       {c['total_cost']:,.0f} 元 ({c['cost_as_pct']:.2%})")
        logger.info(f"  佣金:       {c['commission']:,.0f} 元")
        logger.info(f"  印花税:     {c['stamp_duty']:,.0f} 元")
        logger.info(f"  市场冲击:   {c['market_impact']:,.0f} 元")
        logger.info(f"  交易笔数:   {c['n_trades']}")
        logger.info(f"  平均成本:   {c['avg_cost_per_trade']:,.0f} 元/笔")
        logger.info(f"  换手率:     {c['turnover']:.1f}x")
        logger.info("\n--- 有无成本对比 ---")
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
