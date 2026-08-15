"""A股涨跌停价 + 停牌标记计算器 (U2 回测涨跌停/停牌数据接入)

职责:
    1. 根据股票代码识别板块 (主板/创业板/科创板/北交所/ETF) 与 ST 状态
    2. 按板块规则计算涨跌停价 (前收盘价 × (1±比例), 四舍五入到分)
    3. 从 OHLCV 数据检测停牌 (volume==0 或 price<=0)
    4. 将 limit_up_prices / limit_down_prices / suspended 字段注入回测 day_data

A股涨跌停规则 (2020-08-24 创业板注册制后):
    - 沪深主板 (60xxxx / 00xxxx):        ±10%
    - ST/*ST (任意板块):                  ±5%
    - 创业板 (300xxx / 301xxx):           ±20%
    - 科创板 (688xxx / 689xxx):           ±20%
    - 北交所 (8xxxxx / 4xxxxx):           ±30%
    - ETF/基金 (51/58/15/16 开头):        ±10% (默认, 跨境 ETF 等特殊品种除外)
    - 可转债 (11/13 开头):                无涨跌幅限制 (本模块不处理, 返回 0/0)

新股上市首日特殊规则 (主板无限制, 创业板/科创板前 5 日无限制):
    本模块 MVP 不处理新股首日, 默认按常规板块规则计算;
    调用方应在 universe 构建时排除上市未满 5 日的新股, 或通过 st_codes/override 处理.

ST 状态说明:
    ST 状态随时间变化 (摘帽/戴帽), 属于 point-in-time 信息.
    本模块接受 st_codes 参数 (当前快照), 历史精确回测需调用方提供逐日 ST 状态.
    默认 is_st=False (非 ST), 对回测为保守近似 (±10% 而非 ±5%).

设计原则:
    - 纯函数 + 可测: 所有计算无副作用, 便于单元测试
    - 向后兼容: 不提供 limit 字段时回测引擎行为不变 (P2-2 已实现)
    - 防御性: prev_close<=0 / 代码无法识别时返回 (0.0, 0.0), 不抛异常

用法:
    from utils.price_limit_calculator import (
        calc_limit_prices,
        enrich_day_data_list,
        build_backtest_data_from_ohlcv,
    )
    # 1. 单个涨跌停价
    lu, ld = calc_limit_prices(prev_close=10.00, code="300750.SZ")
    # 2. 富化轻量 day_data 列表
    enriched = enrich_day_data_list(day_data_list)
    # 3. 从 OHLCV DataFrame 构建完整回测数据
    data = build_backtest_data_from_ohlcv(price_data_dict)
"""

from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


# ============================================================
# 板块类型枚举
# ============================================================
class BoardType(str, Enum):
    """A股板块分类 (决定涨跌停比例)"""

    MAIN_SH = "main_sh"  # 沪市主板 ±10%
    MAIN_SZ = "main_sz"  # 深市主板 ±10%
    GEM = "gem"  # 创业板 ±20% (300/301)
    STAR = "star"  # 科创板 ±20% (688/689)
    BSE = "bse"  # 北交所 ±30% (8开头/4开头)
    ETF = "etf"  # ETF/基金 ±10% (51/58/15/16)
    BOND = "bond"  # 可转债 无限制 (11/13)
    UNKNOWN = "unknown"  # 未知代码, 按主板 ±10% 兜底


# 板块涨跌停比例 (非 ST)
_LIMIT_PCT: dict[BoardType, float] = {
    BoardType.MAIN_SH: 0.10,
    BoardType.MAIN_SZ: 0.10,
    BoardType.GEM: 0.20,
    BoardType.STAR: 0.20,
    BoardType.BSE: 0.30,
    BoardType.ETF: 0.10,
    BoardType.BOND: 0.0,  # 可转债无涨跌幅限制
    BoardType.UNKNOWN: 0.10,  # 保守兜底按主板
}

# ST 涨跌停比例 (统一 ±5%, 无论哪个板块)
_ST_LIMIT_PCT = 0.05


# ============================================================
# 代码归一化 + 板块识别
# ============================================================
def normalize_code(code: str) -> str:
    """归一化股票代码为 6 位纯数字 (去除 .SH/.SZ/.BJ 后缀和 sh/sz/bj 前缀).

    Args:
        code: 原始代码 (如 "300750.SZ" / "sh600519" / "688981")

    Returns:
        6 位数字代码 (如 "300750"), 无法解析返回空字符串
    """
    if code is None:
        return ""
    s = str(code).strip()
    if not s or s.lower() == "none":
        return ""
    # 去前缀
    for prefix in ("sh", "sz", "bj", "SH", "SZ", "BJ"):
        if s.startswith(prefix):
            s = s[len(prefix) :]
            break
    # 去后缀
    for suffix in (".SH", ".SZ", ".BJ", ".sh", ".sz", ".bj"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    return s.strip()


def get_board_type(code: str) -> BoardType:
    """根据股票代码识别板块类型.

    代码前缀规则:
        - 688/689 → 科创板 (STAR)
        - 300/301 → 创业板 (GEM)
        - 60      → 沪市主板 (MAIN_SH)
        - 00      → 深市主板 (MAIN_SZ)
        - 8/4     → 北交所 (BSE)  [8开头4开头, 6位代码]
        - 51/58   → 沪市 ETF
        - 15/16   → 深市 ETF/LOF
        - 11/13   → 可转债 (BOND)
    """
    digits = normalize_code(code)
    if not digits or not digits.isdigit():
        return BoardType.UNKNOWN

    # 可转债 (11xxxx 沪 / 12xxxx 深 / 13xxxx 深可交债) — 优先判断
    if digits.startswith(("11", "12", "13")):
        return BoardType.BOND

    # ETF / 基金
    if digits.startswith(("51", "58")):
        return BoardType.ETF
    if digits.startswith(("15", "16")):
        return BoardType.ETF

    # 科创板 (688/689)
    if digits.startswith(("688", "689")):
        return BoardType.STAR

    # 创业板 (300/301)
    if digits.startswith(("300", "301")):
        return BoardType.GEM

    # 沪市主板 (60xxxx)
    if digits.startswith("60"):
        return BoardType.MAIN_SH

    # 深市主板 (00xxxx, 含 002 中小板已合并)
    if digits.startswith("00"):
        return BoardType.MAIN_SZ

    # 北交所 (8xxxxx / 4xxxxx, 6位代码)
    if digits.startswith(("8", "4")):
        return BoardType.BSE

    return BoardType.UNKNOWN


def get_limit_pct(code: str, is_st: bool = False) -> float:
    """获取涨跌停比例.

    Args:
        code: 股票代码
        is_st: 是否为 ST/*ST 股票 (ST 统一 ±5%)

    Returns:
        涨跌停比例 (如 0.10 表示 ±10%), 可转债返回 0.0 (无限制)
    """
    if is_st:
        return _ST_LIMIT_PCT
    return _LIMIT_PCT.get(get_board_type(code), 0.10)


# ============================================================
# 涨跌停价计算
# ============================================================
def _round_to_cents(value: float) -> float:
    """四舍五入到分 (0.01 元), 使用 Decimal ROUND_HALF_UP.

    A股交易所涨跌停价按"四舍五入"到 0.01 元;
    Python 内置 round() 采用银行家舍入 (round half to even), 此处用 ROUND_HALF_UP 对齐交易所规则.
    """
    if not (value == value) or value in (float("inf"), float("-inf")):  # NaN/inf 检查
        return 0.0
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def calc_limit_prices(
    prev_close: float,
    code: str,
    is_st: bool = False,
) -> tuple[float, float]:
    """计算涨停价 / 跌停价.

    公式:
        limit_up   = round(prev_close × (1 + pct), 2)
        limit_down = round(prev_close × (1 - pct), 2)

    Args:
        prev_close: 前收盘价 (T-1 日收盘价)
        code: 股票代码 (用于判断板块)
        is_st: 是否 ST

    Returns:
        (limit_up, limit_down) 涨停价 / 跌停价;
        prev_close<=0 返回 (0.0, 0.0); 可转债返回 (0.0, 0.0) 表示无限制.
    """
    if prev_close is None or prev_close <= 0:
        return 0.0, 0.0

    pct = get_limit_pct(code, is_st=is_st)
    if pct <= 0:
        # 可转债等无涨跌幅限制品种
        return 0.0, 0.0

    limit_up = _round_to_cents(prev_close * (1.0 + pct))
    limit_down = _round_to_cents(prev_close * (1.0 - pct))
    return limit_up, limit_down


def is_at_limit_up(price: float, limit_up: float, tol: float = 1e-3) -> bool:
    """判断当日是否涨停 (price >= limit_up, 容差 0.001 元)."""
    if limit_up <= 0:
        return False
    return price >= limit_up - tol


def is_at_limit_down(price: float, limit_down: float, tol: float = 1e-3) -> bool:
    """判断当日是否跌停 (price <= limit_down, 容差 0.001 元)."""
    if limit_down <= 0:
        return False
    return price <= limit_down + tol


# ============================================================
# 停牌检测
# ============================================================
def detect_suspended_from_row(
    close: float,
    volume: float = 0.0,
    open_price: float = 0.0,
) -> bool:
    """从单日 OHLCV 检测是否停牌.

    停牌判定 (满足任一):
        - 收盘价 <= 0 (无行情)
        - 成交量 <= 0 且 开盘价 <= 0 (全天无成交且无开盘)

    注意: 单纯 volume==0 但有开盘价 (一字涨停) 不应判为停牌,
          故需同时检查 open_price.
    """
    if close is None or close <= 0:
        return True
    # 一字板: volume 可能为 0 (罕见), 但 open/high/low/close 都 > 0, 不是停牌
    if (volume is None or volume <= 0) and (open_price is None or open_price <= 0):
        return True
    return False


# ============================================================
# day_data 富化 (轻量 List[Dict] 格式)
# ============================================================
def enrich_day_data_list(
    data: list[dict[str, Any]],
    st_codes: set[str] | None = None,
    price_field: str = "prices",
) -> list[dict[str, Any]]:
    """为轻量 day_data 列表注入 limit_up_prices / limit_down_prices / suspended 字段.

    输入格式 (每个元素):
        {"date": "2024-01-01", "prices": {code: close_price, ...}, ...}

    输出: 原列表 (原地修改并返回), 每个元素新增:
        - "limit_up_prices": {code: limit_up}
        - "limit_down_prices": {code: limit_down}
        - "suspended": {code: bool}

    前收盘价逻辑:
        - 第 0 日: 无前收盘价, limit 字段为空字典 (不约束)
        - 第 t 日: prev_close = data[t-1]["prices"][code]

    Args:
        data: day_data 列表 (按日期升序)
        st_codes: ST 股票代码集合 (代码格式不限, 内部归一化匹配)
        price_field: 价格字段名 (默认 "prices")

    Returns:
        富化后的 data (同一对象, 原地修改)
    """
    if not data:
        return data

    st_set = {normalize_code(c) for c in (st_codes or set())}

    prev_prices: dict[str, float] = {}

    for t, day_data in enumerate(data):
        prices: dict[str, float] = day_data.get(price_field, {}) or {}

        limit_up_prices: dict[str, float] = {}
        limit_down_prices: dict[str, float] = {}
        suspended: dict[str, bool] = {}

        for code, price in prices.items():
            try:
                price_f = float(price)
            except (TypeError, ValueError):
                continue

            # 停牌检测 (轻量格式无 volume, 用 price<=0 判定)
            suspended[code] = price_f <= 0

            if t == 0:
                # 首日无前收盘价, 跳过 limit 计算
                continue

            prev_close = prev_prices.get(code)
            if prev_close is None or prev_close <= 0:
                continue

            is_st = normalize_code(code) in st_set
            lu, ld = calc_limit_prices(prev_close, code, is_st=is_st)
            if lu > 0:
                limit_up_prices[code] = lu
            if ld > 0:
                limit_down_prices[code] = ld

        # 注入字段 (不覆盖已有值, 允许调用方预先提供)
        day_data.setdefault("limit_up_prices", limit_up_prices)
        day_data.setdefault("limit_down_prices", limit_down_prices)
        day_data.setdefault("suspended", suspended)

        # 更新 prev_prices 供下一日使用
        prev_prices = {c: float(p) for c, p in prices.items() if _is_positive_float(p)}

    return data


def _is_positive_float(v: Any) -> bool:
    try:
        return float(v) > 0
    except (TypeError, ValueError):
        return False


# ============================================================
# 从 OHLCV DataFrame 构建完整回测数据
# ============================================================
def build_backtest_data_from_ohlcv(
    price_data: dict[str, pd.DataFrame],
    st_codes: set[str] | None = None,
    etf_signals_by_date: dict[str, dict[str, dict[str, Any]]] | None = None,
    use_volume_for_suspension: bool = True,
    limit_pool_provider: Any = None,
) -> list[dict[str, Any]]:
    """从 OHLCV DataFrame 字典构建带涨跌停/停牌字段的回测 day_data 列表.

    比 enrich_day_data_list 更精确:
        - 使用真实 prev_close (前一日 close)
        - 停牌检测可用 volume + open + close 三字段
        - limit 价基于 close 而非单一 price
        - 可选接入涨停池数据源做交叉校验 (W6.6.3 P1-a)

    Args:
        price_data: {symbol: DataFrame(index=date, columns=[open,high,low,close,volume,...])}
            DataFrame 必须含 close 列; 若有 volume/open 列则用于停牌检测.
        st_codes: ST 股票代码集合
        etf_signals_by_date: 可选 ETF 信号 {date_str: {code: {signal, inflow}}}
        use_volume_for_suspension: 是否用 volume 检测停牌 (默认 True)
        limit_pool_provider: 可选 LimitPoolProvider 实例, 提供当日涨停池数据.
            若提供, 则在 day_data 中注入 limit_up_pool / limit_down_pool 字段
            (来自交易所涨停板数据, 与基于 prev_close 计算的 limit_up_prices 互为双保险).

    Returns:
        day_data 列表 (按日期升序), 每个元素:
            {"date", "prices", "limit_up_prices", "limit_down_prices",
             "suspended", "etf_signals"(可选), "limit_up_pool"(可选), "limit_down_pool"(可选)}
    """
    if not price_data:
        return []

    st_set = {normalize_code(c) for c in (st_codes or set())}

    # 收集所有交易日 (并集), 按升序
    all_dates: set[pd.Timestamp] = set()
    for df in price_data.values():
        if df is None or df.empty:
            continue
        all_dates.update(df.index)
    sorted_dates = sorted(all_dates)

    # 为每个标的建立 date -> row 映射, 并预计算 prev_close
    # symbol_meta[symbol] = {"lookup": {date: row}, "prev_close": float}
    symbol_meta: dict[str, dict[str, Any]] = {}
    for symbol, df in price_data.items():
        if df is None or df.empty or "close" not in df.columns:
            continue
        lookup: dict[pd.Timestamp, pd.Series] = {}
        for dt, row in df.iterrows():
            lookup[dt] = row
        symbol_meta[symbol] = {"lookup": lookup}

    data: list[dict[str, Any]] = []

    for t, dt in enumerate(sorted_dates):
        date_str = str(dt.date()) if hasattr(dt, "date") else str(dt)

        prices: dict[str, float] = {}
        limit_up_prices: dict[str, float] = {}
        limit_down_prices: dict[str, float] = {}
        suspended: dict[str, bool] = {}

        for symbol, meta in symbol_meta.items():
            row = meta["lookup"].get(dt)
            if row is None:
                # 该标的当日无数据 (可能停牌或未上市)
                # 用前一可用日 close 作为价格 (持仓估值冻结), 标记停牌
                prev_row = _find_prev_row(meta["lookup"], sorted_dates[:t], symbol)
                if prev_row is not None:
                    close = float(prev_row.get("close", 0))
                    prices[symbol] = close
                    suspended[symbol] = True
                continue

            try:
                close = float(row.get("close", 0))
            except (TypeError, ValueError):
                continue

            prices[symbol] = close

            # 停牌检测
            volume = float(row.get("volume", 0)) if "volume" in row else 0.0
            open_price = float(row.get("open", 0)) if "open" in row else close
            if use_volume_for_suspension:
                suspended[symbol] = detect_suspended_from_row(close, volume, open_price)
            else:
                suspended[symbol] = close <= 0

            # 涨跌停价 (基于前一交易日 close)
            if t == 0:
                continue
            prev_close = _get_prev_close(meta["lookup"], sorted_dates[:t])
            if prev_close is None or prev_close <= 0:
                continue

            is_st = normalize_code(symbol) in st_set
            lu, ld = calc_limit_prices(prev_close, symbol, is_st=is_st)
            if lu > 0:
                limit_up_prices[symbol] = lu
            if ld > 0:
                limit_down_prices[symbol] = ld

        day: dict[str, Any] = {
            "date": date_str,
            "prices": prices,
            "limit_up_prices": limit_up_prices,
            "limit_down_prices": limit_down_prices,
            "suspended": suspended,
        }

        # 可选: 注入 ETF 信号
        if etf_signals_by_date and date_str in etf_signals_by_date:
            day["etf_signals"] = etf_signals_by_date[date_str]

        # 可选: 注入涨停池数据 (W6.6.3 P1-a, 交易所涨停板双保险)
        if limit_pool_provider is not None:
            try:
                pool = limit_pool_provider.get_pool(date_str)
                day["limit_up_pool"] = pool.limit_up_codes
                day["limit_down_pool"] = pool.limit_down_codes
                day["broken_pool"] = pool.broken_codes
            except Exception as e:
                logger.debug("涨停池获取失败 %s: %s", date_str, e)

        data.append(day)

    logger.info(
        "[PriceLimitCalculator] 构建回测数据完成 | 标的=%d 交易日=%d | "
        "limit_up/down 字段已注入 (向后兼容: 引擎未启用时不约束)",
        len(symbol_meta),
        len(data),
    )
    return data


def _find_prev_row(
    lookup: dict[pd.Timestamp, pd.Series],
    prev_dates: list[pd.Timestamp],
    symbol: str,
) -> pd.Series | None:
    """从历史日期中查找该标的最新的可用 row (用于停牌日估值冻结)."""
    for dt in reversed(prev_dates):
        row = lookup.get(dt)
        if row is not None:
            return row
    return None


def _get_prev_close(
    lookup: dict[pd.Timestamp, pd.Series],
    prev_dates: list[pd.Timestamp],
) -> float | None:
    """获取前一交易日的 close (跳过停牌日)."""
    row = _find_prev_row(lookup, prev_dates, "")
    if row is None:
        return None
    try:
        close = float(row.get("close", 0))
        return close if close > 0 else None
    except (TypeError, ValueError):
        return None


# ============================================================
# 便捷: 批量获取 ST 股票代码 (从 akshare)
# ============================================================
def fetch_st_codes() -> set[str]:
    """从 akshare 获取当前 ST 股票代码集合.

    用于 enrch_day_data_list / build_backtest_data_from_ohlcv 的 st_codes 参数.
    注意: 这是当前快照, 历史回测存在 point-in-time 偏差 (摘帽/戴帽).

    Returns:
        ST 股票代码集合 (6 位数字), 失败返回空集合.
    """
    try:
        import akshare as ak  # type: ignore[import-not-found]

        df = ak.stock_zh_a_st_em()
        if df is None or df.empty:
            return set()
        # akshare ST 接口返回 "代码" 列
        code_col = "代码" if "代码" in df.columns else df.columns[0]
        return {str(c).strip() for c in df[code_col].tolist() if str(c).strip()}
    except (ImportError, RuntimeError, OSError, ConnectionError, ValueError, KeyError) as e:
        logger.warning("[PriceLimitCalculator] 获取 ST 代码失败 (akshare 不可用): %s", e)
        return set()


__all__ = [
    "BoardType",
    "normalize_code",
    "get_board_type",
    "get_limit_pct",
    "calc_limit_prices",
    "is_at_limit_up",
    "is_at_limit_down",
    "detect_suspended_from_row",
    "enrich_day_data_list",
    "build_backtest_data_from_ohlcv",
    "fetch_st_codes",
]
