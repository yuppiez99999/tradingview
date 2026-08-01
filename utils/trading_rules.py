"""
QMT 交易制度检查模块 (T+0/T+1/T+2 交收制度)

统一管理 A股股票/ETF/期货/期权 的交易制度差异:
- A股股票: T+1 (当日买入, 次交易日方可卖出)
- 债券ETF: T+0 (当日可买卖)
- 跨境ETF: T+0
- 黄金ETF: T+0
- 货币ETF: T+0
- 期货: T+0 (当日开仓可当日平仓)
- 期权: T+0
- 科创板: T+1 (但涨跌幅20%)
- 北交所: T+1 (涨跌幅30%)

QMT passorder 不会自动拦截 T+1 违规, 需要策略层自行判断。
"""

from __future__ import annotations

from datetime import date

# ============================================================
# T+0 标的数据库
# ============================================================

# T+0 ETF 前缀 (上海) — 必须三位精确匹配, 510xxx 是普通 T+1 ETF
T0_ETF_SH_PREFIXES = {
    "511": True,  # 债券ETF (511010等)
    "513": True,  # 跨境ETF (513100等)
    "518": True,  # 商品ETF (518880黄金ETF等)
}

# T+0 ETF 前缀 (深圳)
T0_ETF_SZ_PREFIXES = {
    "159": True,  # 跨境ETF (159920等)
    "16": True,  # LOF (可T+0)
}

# 已知 T+0 标的完整列表 (精确匹配, 优先级高于前缀)
T0_EXACT_CODES = {
    # 债券ETF
    "511010": True,  # 国债ETF
    "511020": True,  # 活跃国债ETF
    "511260": True,  # 十年国债ETF
    "511380": True,  # 可转债ETF
    # 货币ETF
    "511880": True,  # 银华日利
    "511660": True,  # 建信添益
    "511990": True,  # 华宝添益
    # 跨境ETF
    "513100": True,  # 纳指ETF
    "513050": True,  # 中概互联
    "513500": True,  # 标普500ETF
    "159920": True,  # 恒生ETF
    "159941": True,  # 纳指ETF(深圳)
    # 黄金ETF
    "518880": True,  # 黄金ETF华安
    "159934": True,  # 黄金ETF(深圳)
    # 商品ETF
    "518800": True,  # 黄金ETF基金
    # LOF
    "160505": True,  # 博时主题
    "160706": True,  # 嘉实沪深300
}


def is_t0_eligible(code: str, product_class: str = "STOCK") -> bool:
    """判断标的是否支持 T+0 交易

    Args:
        code: 标的代码, 如 "510300.SH" 或 "510300"
        product_class: 品种类型 ("STOCK"/"ETF"/"FUTURE"/"OPTION")

    Returns:
        True = 可当日买卖, False = T+1
    """
    # 期货/期权 永远是 T+0
    if product_class in ("FUTURE", "OPTION"):
        return True

    # 提取纯数字代码
    code_clean = str(code).split(".")[0].zfill(6)

    # 精确匹配
    if code_clean in T0_EXACT_CODES:
        return True

    # 前缀匹配
    if code_clean.startswith("5"):
        for prefix in T0_ETF_SH_PREFIXES:
            if code_clean.startswith(prefix):
                return True
    elif code_clean.startswith(("1", "0")):
        for prefix in T0_ETF_SZ_PREFIXES:
            if code_clean.startswith(prefix):
                return True

    return False  # 默认 T+1


def can_sell_today(code: str, buy_date: date, product_class: str = "STOCK") -> tuple[bool, str]:
    """判断今日是否可以卖出

    Args:
        code: 标的代码
        buy_date: 买入日期
        product_class: 品种类型

    Returns:
        (can_sell, reason) — (True, "") 或 (False, "T+1限制: 今日买入, 明日方可卖出")
    """
    if is_t0_eligible(code, product_class):
        return True, ""

    today = date.today()
    if buy_date < today:
        return True, ""
    elif buy_date == today:
        return False, f"T+1限制: {code} 今日买入, 最早 {_next_trade_day(today)} 方可卖出"
    else:
        return False, f"日期异常: buy_date={buy_date} > today={today}"


def _next_trade_day(d: date) -> date:
    """下一个交易日 (简化: 跳过周六日)"""
    from datetime import timedelta

    next_day = d + timedelta(days=1)
    while next_day.weekday() >= 5:  # 周六=5, 周日=6
        next_day += timedelta(days=1)
    return next_day


def get_trading_rule(code: str, product_class: str = "STOCK") -> dict:
    """获取标的完整交易规则

    Returns:
        {
            'settlement': 'T+0' | 'T+1',
            'can_short': bool,       # 是否可融券做空
            'price_limit_pct': float, # 涨跌幅限制
            'margin_required': bool,  # 是否需要保证金
            'min_unit': int,          # 最小交易单位
        }
    """
    code_clean = str(code).split(".")[0].zfill(6)
    is_t0 = is_t0_eligible(code, product_class)

    rules = {
        "settlement": "T+0" if is_t0 else "T+1",
        "can_short": product_class == "FUTURE",
        # v8.6.13 MEDIUM FIX (2026-08-01 量化审计): 按产品类型设置最小交易单位
        # 原代码硬编码 min_unit=1, 若被用于下单手数计算会导致股数不是 100 的整数倍被交易所拒绝.
        # A股/ETF: 100 股(份)起买, 1 手 = 100 股
        # 期货: 1 手起 (合约乘数由合约规格决定, 如 IF=300元/点)
        # 期权: 1 张起 (1 张 = 10000 份, 但申报单位为 1 张)
        "min_unit": 1 if product_class in ("FUTURE", "OPTION") else 100,
    }

    if product_class == "FUTURE":
        rules["price_limit_pct"] = 0.10  # type: ignore[assignment]  # 股指期货 ±10%
        rules["margin_required"] = True
    elif product_class == "OPTION":
        rules["price_limit_pct"] = 0.0  # type: ignore[assignment]  # 期权无涨跌停
        rules["margin_required"] = True
    elif code_clean.startswith(("68", "8")):
        # 科创板/北交所
        rules["price_limit_pct"] = 0.20 if code_clean.startswith("68") else 0.30  # type: ignore
        rules["margin_required"] = False
    elif code_clean.startswith("3"):
        rules["price_limit_pct"] = 0.20  # type: ignore[assignment]  # 创业板
        rules["margin_required"] = False
    else:
        rules["price_limit_pct"] = 0.10  # type: ignore[assignment]  # 主板
        rules["margin_required"] = False

    return rules


__all__ = [
    "T0_EXACT_CODES",
    "can_sell_today",
    "get_trading_rule",
    "is_t0_eligible",
]
