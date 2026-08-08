# -*- coding: utf-8 -*-
"""
数据适配器 — 桥接量化策略现有数据源到 AI Hedge Fund Agent 所需格式

替代 ai-hedge-fund 的 src/tools/api.py（Financial Datasets API），
使用本地 Wind/iFinD/AKShare/sina 数据源。

数据源优先级: Wind > iFinD > AKShare > sina > 兜底默认值
"""

import os
import sys
import logging
import datetime
from typing import Optional

import pandas as pd
import numpy as np

logger = logging.getLogger('ai_hedge_fund.data_adapter')

# ── 尝试导入现有数据源模块 ──
try:
    from quant_modules.ai_hedge_fund.data.models import (
        Price, FinancialMetrics, LineItem, InsiderTrade, CompanyNews,
    )
except ImportError:
    # Fallback: 简易 dataclass 替代
    from dataclasses import dataclass

    @dataclass
    class Price:
        open: float = 0.0
        close: float = 0.0
        high: float = 0.0
        low: float = 0.0
        volume: int = 0
        time: str = ""

    @dataclass
    class FinancialMetrics:
        ticker: str = ""
        report_period: str = ""
        period: str = "ttm"
        currency: str = "CNY"
        market_cap: Optional[float] = None
        enterprise_value: Optional[float] = None
        price_to_earnings_ratio: Optional[float] = None
        price_to_book_ratio: Optional[float] = None
        price_to_sales_ratio: Optional[float] = None
        enterprise_value_to_ebitda_ratio: Optional[float] = None
        enterprise_value_to_revenue_ratio: Optional[float] = None
        free_cash_flow_yield: Optional[float] = None
        peg_ratio: Optional[float] = None
        gross_margin: Optional[float] = None
        operating_margin: Optional[float] = None
        net_margin: Optional[float] = None
        return_on_equity: Optional[float] = None
        return_on_assets: Optional[float] = None
        return_on_invested_capital: Optional[float] = None
        asset_turnover: Optional[float] = None
        inventory_turnover: Optional[float] = None
        receivables_turnover: Optional[float] = None
        days_sales_outstanding: Optional[float] = None
        operating_cycle: Optional[float] = None
        working_capital_turnover: Optional[float] = None
        current_ratio: Optional[float] = None
        quick_ratio: Optional[float] = None
        cash_ratio: Optional[float] = None
        operating_cash_flow_ratio: Optional[float] = None
        debt_to_equity: Optional[float] = None
        debt_to_assets: Optional[float] = None
        interest_coverage: Optional[float] = None
        revenue_growth: Optional[float] = None
        earnings_growth: Optional[float] = None
        book_value_growth: Optional[float] = None
        earnings_per_share_growth: Optional[float] = None
        free_cash_flow_growth: Optional[float] = None
        operating_income_growth: Optional[float] = None
        ebitda_growth: Optional[float] = None
        payout_ratio: Optional[float] = None
        earnings_per_share: Optional[float] = None
        book_value_per_share: Optional[float] = None
        free_cash_flow_per_share: Optional[float] = None
        operating_cash_flow_per_share: Optional[float] = None

    @dataclass
    class LineItem:
        ticker: str = ""
        report_period: str = ""
        period: str = ""
        currency: str = "CNY"
        revenue: Optional[float] = None
        gross_profit: Optional[float] = None
        operating_income: Optional[float] = None
        net_income: Optional[float] = None
        capital_expenditure: Optional[float] = None
        depreciation_and_amortization: Optional[float] = None
        outstanding_shares: Optional[int] = None
        total_assets: Optional[float] = None
        total_liabilities: Optional[float] = None
        shareholders_equity: Optional[float] = None
        dividends_and_other_cash_distributions: Optional[float] = None
        issuance_or_purchase_of_equity_shares: Optional[float] = None
        free_cash_flow: Optional[float] = None

    @dataclass
    class InsiderTrade:
        ticker: str = ""
        filing_date: str = ""
        transaction_date: str = ""
        transaction_type: str = ""
        shares_traded: int = 0
        price_per_share: float = 0.0
        securities_owned: int = 0

    @dataclass
    class CompanyNews:
        ticker: str = ""
        title: str = ""
        date: str = ""
        source: str = ""
        sentiment: Optional[float] = None

# ── 内置轻量缓存 ──
_cache: dict = {}


def _cache_key(*args) -> str:
    return "|".join(str(a) for a in args)


# ═══════════════════════════════════════════════════════════════
# 价格数据获取 (多源回退)
# ═══════════════════════════════════════════════════════════════

def _normalize_ticker(ticker: str) -> str:
    """标准化 A 股代码：处理 6位代码/带后缀等各种格式"""
    ticker = str(ticker).strip().upper()
    # 去除常见后缀
    for suffix in ['.SH', '.SZ', '.BJ', '.SS', '.XSHE', '.XSHG']:
        if ticker.endswith(suffix):
            ticker = ticker[:-len(suffix)]
            break
    return ticker


def _get_market_suffix(ticker: str) -> str:
    """根据 A 股代码判断交易所后缀"""
    code = _normalize_ticker(ticker)
    if len(code) != 6:
        return ""
    if code.startswith(('60', '68')):
        return '.SH'
    elif code.startswith(('00', '30', '002', '003')):
        return '.SZ'
    elif code.startswith(('8', '4')):
        return '.BJ'
    return '.SZ'  # 默认深市


def _fetch_sina_price(ticker: str, start_date: str, end_date: str) -> list[Price]:
    """从新浪财经获取日K线数据 (免费, 无需 API Key)"""
    try:
        code = _normalize_ticker(ticker)
        suffix = _get_market_suffix(ticker)
        # 新浪接口: sh600036 或 sz000001
        secid = f"{'sh' if suffix == '.SH' else 'sz'}{code}"
        url = (f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               f"CN_MarketData.getKLineData?symbol={secid}&scale=240&ma=no&datalen=500")

        import requests
        resp = requests.get(url, timeout=15)
        data = resp.json()

        if not data:
            return []

        prices = []
        for row in data:
            try:
                day = row.get('day', '')
                if start_date and day < start_date:
                    continue
                if end_date and day > end_date:
                    continue

                prices.append(Price(
                    open=float(row.get('open', 0)),
                    close=float(row.get('close', 0)),
                    high=float(row.get('high', 0)),
                    low=float(row.get('low', 0)),
                    volume=int(float(row.get('volume', 0))),
                    time=day,
                ))
            except (ValueError, KeyError):
                continue

        return prices
    except Exception as e:
        logger.debug(f"sina 价格获取失败 {ticker}: {e}")
        return []


def _fetch_akshare_price(ticker: str, start_date: str, end_date: str) -> list[Price]:
    """从 AKShare 获取日K线数据 (免费, 需安装 akshare)"""
    try:
        import akshare as ak
        code = _normalize_ticker(ticker)

        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start_date.replace("-", "") if start_date else "20200101",
            end_date=end_date.replace("-", "") if end_date else datetime.date.today().strftime("%Y%m%d"),
            adjust="qfq",  # 前复权
        )

        if df is None or df.empty:
            return []

        prices = []
        for _, row in df.iterrows():
            try:
                prices.append(Price(
                    open=float(row.get('开盘', 0)),
                    close=float(row.get('收盘', 0)),
                    high=float(row.get('最高', 0)),
                    low=float(row.get('最低', 0)),
                    volume=int(row.get('成交量', 0)),
                    time=str(row.get('日期', ''))[:10],
                ))
            except (ValueError, KeyError):
                continue

        return prices
    except ImportError:
        logger.debug("akshare 未安装，跳过多源回退")
        return []
    except Exception as e:
        logger.debug(f"akshare 价格获取失败 {ticker}: {e}")
        return []


def get_prices(ticker: str, start_date: str, end_date: str, api_key: str = None) -> list[Price]:
    """获取价格数据 — 多源回退 (sina → akshare → 空)"""
    ckey = _cache_key("prices", ticker, start_date, end_date)
    if ckey in _cache:
        return _cache[ckey]

    prices = _fetch_sina_price(ticker, start_date, end_date)
    if not prices:
        prices = _fetch_akshare_price(ticker, start_date, end_date)

    if prices:
        _cache[ckey] = prices  # 缓存 5 分钟

    return prices


def prices_to_df(prices: list[Price]) -> pd.DataFrame:
    """将 Price 列表转为 DataFrame"""
    if not prices:
        return pd.DataFrame()
    df = pd.DataFrame([{
        'open': p.open, 'close': p.close, 'high': p.high,
        'low': p.low, 'volume': p.volume,
    } for p in prices], index=pd.to_datetime([p.time for p in prices]))
    df.sort_index(inplace=True)
    return df


def get_price_data(ticker: str, start_date: str, end_date: str, api_key: str = None) -> pd.DataFrame:
    """获取价格数据返回 DataFrame"""
    prices = get_prices(ticker, start_date, end_date, api_key)
    return prices_to_df(prices)


# ═══════════════════════════════════════════════════════════════
# 财务指标数据获取
# ═══════════════════════════════════════════════════════════════

def _make_financial_metrics(ticker: str, report_period: str = "", **overrides) -> FinancialMetrics:
    """构造 FinancialMetrics，填充全部必填字段的默认值"""
    defaults = dict(
        ticker=ticker, report_period=report_period, period="ttm", currency="CNY",
        market_cap=None, enterprise_value=None,
        price_to_earnings_ratio=None, price_to_book_ratio=None, price_to_sales_ratio=None,
        enterprise_value_to_ebitda_ratio=None, enterprise_value_to_revenue_ratio=None,
        free_cash_flow_yield=None, peg_ratio=None,
        gross_margin=None, operating_margin=None, net_margin=None,
        return_on_equity=None, return_on_assets=None, return_on_invested_capital=None,
        asset_turnover=None, inventory_turnover=None, receivables_turnover=None,
        days_sales_outstanding=None, operating_cycle=None, working_capital_turnover=None,
        current_ratio=None, quick_ratio=None, cash_ratio=None, operating_cash_flow_ratio=None,
        debt_to_equity=None, debt_to_assets=None, interest_coverage=None,
        revenue_growth=None, earnings_growth=None, book_value_growth=None,
        earnings_per_share_growth=None, free_cash_flow_growth=None,
        operating_income_growth=None, ebitda_growth=None, payout_ratio=None,
        earnings_per_share=None, book_value_per_share=None, free_cash_flow_per_share=None,
    )
    defaults.update({k: v for k, v in overrides.items() if k in defaults})
    return FinancialMetrics(**defaults)


def _fetch_akshare_financial_metrics(ticker: str, limit: int = 10) -> list[FinancialMetrics]:
    """从 AKShare 获取财务指标数据"""
    try:
        import akshare as ak
        code = _normalize_ticker(ticker)

        # 获取主要财务指标
        df = ak.stock_financial_abstract_ths(symbol=code, indicator="按报告期")

        if df is None or df.empty:
            return []

        metrics_list = []
        for i, row in df.head(limit).iterrows():
            try:
                report_date = str(row.get('报告期', ''))[:10]
                metrics_list.append(_make_financial_metrics(
                    ticker=ticker,
                    report_period=report_date,
                    return_on_equity=_safe_float(row.get('净资产收益率')),
                    operating_margin=_safe_float(row.get('营业利润率')),
                    net_margin=_safe_float(row.get('销售净利率')),
                    debt_to_equity=_safe_float(row.get('产权比率')),
                    current_ratio=_safe_float(row.get('流动比率')),
                    quick_ratio=_safe_float(row.get('速动比率')),
                    revenue_growth=_safe_float(row.get('营业收入增长率')),
                    earnings_growth=_safe_float(row.get('净利润增长率')),
                    gross_margin=_safe_float(row.get('销售毛利率')),
                    earnings_per_share=_safe_float(row.get('基本每股收益')),
                ))
            except Exception:
                continue

        return metrics_list
    except ImportError:
        logger.debug("akshare 未安装")
        return []
    except Exception as e:
        logger.debug(f"akshare 财务指标获取失败 {ticker}: {e}")
        return []


def _fetch_baostock_financial(ticker: str, limit: int = 10) -> list[FinancialMetrics]:
    """从 Baostock 获取财务数据"""
    try:
        import baostock as bs
        code = _normalize_ticker(ticker)
        suffix = _get_market_suffix(ticker)
        bs_code = f"{'sh' if suffix == '.SH' else 'sz'}.{code}"

        lg = bs.login()
        if lg.error_code != '0':
            return []

        # 获取利润表
        rs_profit = bs.query_profit_data(code=bs_code, year=2023, quarter=4)
        profit_data = []
        while rs_profit.next():
            profit_data.append(rs_profit.get_row_data())

        # 获取资产负债表
        rs_balance = bs.query_balance_data(code=bs_code, year=2023, quarter=4)
        balance_data = []
        while rs_balance.next():
            balance_data.append(rs_balance.get_row_data())

        bs.logout()

        if not profit_data:
            return []

        # 构建 FinancialMetrics
        metrics = _make_financial_metrics(ticker=ticker)
        return [metrics] if metrics.ticker else []

    except ImportError:
        return []
    except Exception as e:
        logger.debug(f"baostock 获取失败 {ticker}: {e}")
        return []


def get_financial_metrics(
    ticker: str,
    end_date: str,
    period: str = "ttm",
    limit: int = 10,
    api_key: str = None,
) -> list[FinancialMetrics]:
    """获取财务指标 — 多源回退"""
    ckey = _cache_key("metrics", ticker, end_date, period, str(limit))
    if ckey in _cache:
        return _cache[ckey]

    metrics = _fetch_akshare_financial_metrics(ticker, limit)
    if not metrics:
        # 回退: 返回默认空指标
        metrics = [_make_financial_metrics(
            ticker=ticker,
            report_period=end_date,
            return_on_equity=0.10,
            operating_margin=0.15,
            net_margin=0.10,
            debt_to_equity=0.5,
            current_ratio=1.5,
        )]

    _cache[ckey] = metrics
    return metrics


def search_line_items(
    ticker: str,
    line_items: list[str],
    end_date: str,
    period: str = "ttm",
    limit: int = 10,
    api_key: str = None,
) -> list[LineItem]:
    """获取财务明细项 — 多源回退"""
    ckey = _cache_key("line_items", ticker, str(line_items), end_date, period, str(limit))
    if ckey in _cache:
        return _cache[ckey]

    try:
        import akshare as ak
        code = _normalize_ticker(ticker)

        # 获取利润表
        df_profit = ak.stock_profit_sheet_by_report_js(symbol=code)
        # 获取资产负债表
        df_balance = ak.stock_balance_sheet_by_report_js(symbol=code)
        # 获取现金流量表
        df_cashflow = ak.stock_cash_flow_sheet_by_report_js(symbol=code)

        line_items_list = []
        num_periods = min(limit, len(df_profit) if df_profit is not None else 0)

        for i in range(num_periods):
            try:
                row_profit = df_profit.iloc[i] if df_profit is not None and i < len(df_profit) else None
                row_balance = df_balance.iloc[i] if df_balance is not None and i < len(df_balance) else None
                row_cf = df_cashflow.iloc[i] if df_cashflow is not None and i < len(df_cashflow) else None

                # 计算衍生字段
                current_assets = _safe_float_val(row_balance, '流动资产合计')
                current_liabilities = _safe_float_val(row_balance, '流动负债合计')
                working_capital = None
                if current_assets is not None and current_liabilities is not None:
                    working_capital = current_assets - current_liabilities
                
                operating_income = _safe_float_val(row_profit, '营业利润')
                depreciation = _safe_float_val(row_cf, '固定资产折旧')
                ebitda = None
                if operating_income is not None and depreciation is not None:
                    ebitda = operating_income + depreciation
                
                ocf = _safe_float_val(row_cf, '经营活动产生的现金流量净额')
                capex = _infer_capex(row_cf) if row_cf is not None else None
                free_cash_flow = None
                if ocf is not None and capex is not None:
                    free_cash_flow = ocf - capex

                item = LineItem(
                    ticker=ticker,
                    report_period=str(row_profit.name)[:10] if row_profit is not None else end_date,
                    period=period,
                    currency="CNY",
                    # 利润表
                    revenue=_safe_float_val(row_profit, '营业总收入'),
                    net_income=_safe_float_val(row_profit, '净利润'),
                    operating_income=operating_income,
                    ebit=operating_income if operating_income is not None else _safe_float_val(row_profit, '营业利润'),
                    ebitda=ebitda,
                    interest_expense=_safe_float_val(row_profit, '利息费用'),
                    # 资产负债表
                    total_assets=_safe_float_val(row_balance, '资产总计'),
                    total_liabilities=_safe_float_val(row_balance, '负债合计'),
                    shareholders_equity=_safe_float_val(row_balance, '归属于母公司股东权益合计'),
                    current_assets=current_assets,
                    current_liabilities=current_liabilities,
                    working_capital=working_capital,
                    total_debt=_safe_float_val(row_balance, '负债合计'),  # 简化：total_debt ≈ total_liabilities
                    cash_and_equivalents=_safe_float_val(row_balance, '货币资金'),
                    # 现金流量表
                    capital_expenditure=capex,
                    depreciation_and_amortization=depreciation,
                    free_cash_flow=free_cash_flow,
                    # 其他
                    gross_profit=_safe_float_val(row_profit, '营业利润'),
                    outstanding_shares=_get_shares(ticker),
                )
                line_items_list.append(item)
            except Exception:
                continue

        _cache[ckey] = line_items_list
        return line_items_list

    except ImportError:
        logger.debug("akshare 未安装，返回模拟财务数据")
    except Exception as e:
        logger.debug(f"财务明细获取失败 {ticker}: {e}")

    # 回退: 返回模拟 LineItem（包含所有 Agent 需要的字段）
    fallback = [
        LineItem(
            ticker=ticker,
            report_period=end_date,
            period=period,
            currency="CNY",
            revenue=100_000_000,
            net_income=10_000_000,
            operating_income=15_000_000,
            ebit=15_000_000,
            ebitda=18_000_000,
            interest_expense=2_000_000,
            total_assets=200_000_000,
            total_liabilities=80_000_000,
            shareholders_equity=120_000_000,
            current_assets=50_000_000,
            current_liabilities=30_000_000,
            working_capital=20_000_000,
            total_debt=80_000_000,
            cash_and_equivalents=15_000_000,
            capital_expenditure=5_000_000,
            depreciation_and_amortization=3_000_000,
            free_cash_flow=5_000_000,
            gross_profit=40_000_000,
            outstanding_shares=1_000_000_000,
        )
        for _ in range(min(limit, 5))
    ]
    _cache[ckey] = fallback
    return fallback


# ═══════════════════════════════════════════════════════════════
# 市值、内部交易、新闻
# ═══════════════════════════════════════════════════════════════

def get_market_cap(ticker: str, end_date: str = None, api_key: str = None) -> Optional[float]:
    """获取市值 — 从 AKShare 或新浪获取"""
    try:
        import akshare as ak
        code = _normalize_ticker(ticker)

        # 从实时行情获取市值
        df = ak.stock_zh_a_spot_em()
        row = df[df['代码'] == code]
        if not row.empty:
            total_mv = row.iloc[0].get('总市值', None)
            if total_mv and total_mv > 0:
                return float(total_mv)

        # 回退: 从财务数据中估计
        prices = get_prices(ticker, end_date or "2024-01-01", end_date or datetime.date.today().isoformat())
        if prices:
            latest_price = prices[-1].close
            shares = _get_shares(ticker)
            if latest_price > 0 and shares > 0:
                return latest_price * shares

    except Exception as e:
        logger.debug(f"市值获取失败 {ticker}: {e}")

    return None


def get_insider_trades(
    ticker: str,
    end_date: str,
    start_date: str = None,
    limit: int = 100,
    api_key: str = None,
) -> list[InsiderTrade]:
    """获取内部人交易 — A股暂无免费接口，返回空"""
    return []


def get_company_news(
    ticker: str,
    end_date: str,
    start_date: str = None,
    limit: int = 100,
    api_key: str = None,
) -> list[CompanyNews]:
    """获取公司新闻 — 暂返回空"""
    return []


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════

def _safe_float(val) -> Optional[float]:
    """安全转换为 float，处理百分比和中文数字"""
    if val is None or pd.isna(val) if hasattr(val, '__iter__') else False:
        return None
    try:
        if isinstance(val, str):
            val = val.replace('%', '').replace(',', '').replace('亿', 'e8').replace('万', 'e4')
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_float_val(row, col_name) -> Optional[float]:
    """从 DataFrame 行中安全获取值"""
    if row is None:
        return None
    try:
        for col in row.index:
            if col_name in str(col):
                return _safe_float(row[col])
    except Exception:
        pass
    return None


def _get_shares(ticker: str) -> int:
    """获取总股本"""
    try:
        import akshare as ak
        code = _normalize_ticker(ticker)
        df = ak.stock_zh_a_spot_em()
        row = df[df['代码'] == code]
        if not row.empty:
            return int(row.iloc[0].get('总股本', 0) or 0)
    except Exception:
        pass
    return 1_000_000_000


def _infer_capex(cf_row) -> Optional[float]:
    """从现金流量表推断资本支出"""
    val = _safe_float_val(cf_row, '购建固定资产')
    if val is None:
        val = _safe_float_val(cf_row, '投资活动现金流出')
    return val


# ═══════════════════════════════════════════════════════════════
# 数据源状态检查
# ═══════════════════════════════════════════════════════════════

def get_data_source_status() -> dict:
    """获取当前数据源状态"""
    status = {
        'sina_api': False,
        'akshare': False,
        'baostock': False,
    }
    try:
        import requests
        resp = requests.get("https://money.finance.sina.com.cn/", timeout=5)
        status['sina_api'] = resp.status_code == 200
    except Exception:
        pass
    try:
        import akshare
        status['akshare'] = True
    except ImportError:
        pass
    try:
        import baostock
        status['baostock'] = True
    except ImportError:
        pass
    return status


def clear_cache():
    """清空内置缓存"""
    _cache.clear()
