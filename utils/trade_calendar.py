"""
A股交易日历工具
================
基于 akshare 自动同步 A 股交易日历，判断交易日 / 下一交易日。

缓存策略:
    - 每年首次调用时拉取该年度交易日列表
    - 缓存到本地 JSON 文件 (按年), 避免重复联网
    - 失败时回退到 "周一至周五即交易日" 模式

用法:
    from utils.trade_calendar import is_trading_day, next_trading_day
    if is_trading_day('2026-07-10'):
        ...
    next = next_trading_day('2026-07-10')  # 返回 '2026-07-13'
"""

from __future__ import annotations

import json
import logging
from datetime import date as _date_cls
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# ============================================================
# 路径与缓存
# ============================================================
CACHE_DIR = Path(__file__).resolve().parent.parent / "config" / "trade_calendar_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(year: int) -> Path:
    """指定年度的缓存文件路径"""
    return CACHE_DIR / f"trade_dates_{year}.json"


# ============================================================
# 日历合理性校验 (P0 修复 2026-08-11)
# ------------------------------------------------------------
# 背景: config/trade_calendar_cache/trade_dates_2026.json 曾被写入
#       261 天的"全年工作日"假日历(= 365 - 104 个周末), 含元旦/春节/
#       劳动节/国庆等全部法定节假日。由于缓存优先且永不校验、永不过期,
#       错误被永久固化, 导致 is_trading_day('2026-10-01') 返回 True。
#       下游 daily_trade_executor.py:628 以该函数作为交易执行的唯一
#       日期守卫 —— 节假日会被误判为交易日并继续生成交易指令。
#
# 防御策略 (三层):
#   L1 缓存合理性校验: 读/写缓存均校验, 不合格即丢弃并重新拉取
#   L2 固定节假日兜底: 即使降级到"仅判周末"模式, 也排除公历固定节假日
#   L3 降级状态可观测: get_calendar_status() 暴露当前是精确/降级模式
# ============================================================

# A 股年交易日数量的合理区间。历史区间 242~245 (2015-2026),
# 取 [235, 250] 留出闰年/临时休市的安全边际。
# 假日历特征: 仅排除周末 => 260~262 天, 必然落在区间外被拦截。
_MIN_TRADING_DAYS_PER_YEAR = 235
_MAX_TRADING_DAYS_PER_YEAR = 250

# 公历日期固定的法定节假日 (月, 日)。
# 春节/清明/端午/中秋依农历浮动, 无法硬编码, 只能依赖真实日历数据;
# 此表仅用于 L1 校验与 L2 兜底, 覆盖"必然非交易日"的确定性子集。
_FIXED_HOLIDAYS: frozenset[tuple[int, int]] = frozenset(
    {
        (1, 1),  # 元旦
        (5, 1),  # 劳动节
        (10, 1),  # 国庆节
        (10, 2),
        (10, 3),
    }
)


def _validate_year_dates(year: int, dates: set[str]) -> tuple[bool, str]:
    """校验年度交易日集合是否合理。

    拦截三类污染数据:
      1. 数量异常 (如 261 天的"全年工作日"假日历)
      2. 含周末 (真实交易日历不含周末)
      3. 含公历固定法定节假日 (元旦/劳动节/国庆)

    Args:
        year: 年份
        dates: 交易日集合, 元素形如 'YYYY-MM-DD'

    Returns:
        (是否合格, 不合格原因)。合格时原因为空串。
    """
    if not dates:
        return False, "空集合"

    count = len(dates)
    if not (_MIN_TRADING_DAYS_PER_YEAR <= count <= _MAX_TRADING_DAYS_PER_YEAR):
        return False, (
            f"交易日数量 {count} 超出合理区间 "
            f"[{_MIN_TRADING_DAYS_PER_YEAR}, {_MAX_TRADING_DAYS_PER_YEAR}] "
            f"(疑似仅排除周末的假日历)"
        )

    weekend_hits: list[str] = []
    holiday_hits: list[str] = []
    for iso in dates:
        try:
            d = _date_cls.fromisoformat(iso)
        except (ValueError, TypeError):
            return False, f"日期格式非法: {iso!r}"
        if d.year != year:
            return False, f"日期 {iso} 不属于 {year} 年"
        if d.weekday() >= 5:
            weekend_hits.append(iso)
        if (d.month, d.day) in _FIXED_HOLIDAYS:
            holiday_hits.append(iso)

    if weekend_hits:
        return False, f"含周末 {len(weekend_hits)} 天, 例: {sorted(weekend_hits)[:3]}"
    if holiday_hits:
        return (
            False,
            f"含法定节假日 {len(holiday_hits)} 天, 例: {sorted(holiday_hits)[:3]}",
        )

    return True, ""


def _is_fixed_holiday(d: _date_cls) -> bool:
    """是否为公历固定法定节假日 (降级模式下的兜底判断)."""
    return (d.month, d.day) in _FIXED_HOLIDAYS


def _fetch_trade_dates_via_akshare(year: int) -> set[str] | None:
    """通过 akshare 拉取交易日历

    使用 akshare.tool_trade_date_hist_sina() 获取所有 A 股交易日,
    然后筛选出指定年度的日期集合。
    """
    try:
        import akshare as ak

        df = ak.tool_trade_date_hist_sina()
        if df is None or len(df) == 0:
            return None
        # 列名兼容: trade_date / date
        col = "trade_date" if "trade_date" in df.columns else df.columns[0]
        dates = df[col].astype(str).str[:10].tolist()
        year_dates = {d for d in dates if d.startswith(str(year))}
        return year_dates if year_dates else None
    except (
        ImportError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ) as e:  # P2 模块 fail-safe, 待后续精确化
        logger.error(f"[trade_calendar] akshare 拉取失败 (year={year}): {e}")
        return None


def _load_year_dates(year: int, allow_fetch: bool = True) -> set[str]:
    """加载指定年度的交易日集合 (优先缓存, 其次 akshare, 最后回退)

    P0 修复 (2026-08-11): 缓存读写均经过 `_validate_year_dates` 校验。
    校验不通过的缓存会被隔离 (改名为 .invalid) 并触发重新拉取, 避免
    污染数据被永久固化。
    """
    cache_file = _cache_path(year)
    if cache_file.exists():
        cached: set[str] | None = None
        try:
            with open(cache_file, encoding="utf-8") as f:
                cached = set(json.load(f))
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ):  # P2 模块 fail-safe, 待后续精确化
            cached = None

        if cached is not None:
            ok, reason = _validate_year_dates(year, cached)
            if ok:
                return cached
            # 缓存污染: 隔离而非静默忽略, 保留现场供排查
            logger.error(
                f"[trade_calendar] {year} 年缓存校验失败 ({reason}), 已隔离并尝试重新拉取"
            )
            try:
                cache_file.replace(cache_file.with_suffix(".json.invalid"))
            except OSError as e:
                logger.warning(f"[trade_calendar] 隔离污染缓存失败: {e}")

    if not allow_fetch:
        return set()

    # 尝试 akshare
    dates = _fetch_trade_dates_via_akshare(year)
    if dates:
        ok, reason = _validate_year_dates(year, dates)
        if not ok:
            # 上游数据同样不可信 (如未来年份交易所尚未公布), 不落盘、不使用
            logger.error(
                f"[trade_calendar] {year} 年 akshare 数据校验失败 ({reason}), 拒绝缓存"
            )
            return set()
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(sorted(dates), f, ensure_ascii=False, indent=2)
            logger.info(f"[trade_calendar] 缓存 {year} 年交易日: {len(dates)} 天")
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            OSError,
            TimeoutError,
            ConnectionError,
        ):  # P2 模块 fail-safe, 待后续精确化
            pass
        return dates

    # 回退: 周一到周五即交易日 (无法联网时的兜底)
    logger.warning(
        f"[trade_calendar] 警告: 无法获取 {year} 年交易日历, 回退到周一至周五模式"
    )
    return set()


def is_trading_day(date: str | _date_cls | datetime | None = None) -> bool:
    """判断指定日期是否为 A 股交易日

    Args:
        date: 日期字符串 'YYYY-MM-DD' 或 'YYYYMMDD',
              或 datetime.date / datetime.datetime 对象,
              None 表示今天

    Returns:
        True 表示是交易日
    """
    # 兼容 date / datetime 对象入参 (B1.2: 统一 is_trading_day 调用入口)
    if date is None:
        iso_date = datetime.now().strftime("%Y-%m-%d")
    elif isinstance(date, (datetime, _date_cls)):
        iso_date = date.strftime("%Y-%m-%d")
    else:
        clean = str(date).replace("-", "").replace("/", "")
        iso_date = f"{clean[:4]}-{clean[4:6]}-{clean[6:8]}"

    year = int(iso_date[:4])
    year_dates = _load_year_dates(year)

    if year_dates:
        return iso_date in year_dates

    # 回退模式: 判断周末 + 公历固定法定节假日兜底
    # (L2 防御: 降级时也不得把元旦/劳动节/国庆当作交易日;
    #  春节等农历节日无法兜底, 依赖 L1 保证真实日历可用)
    d = datetime.strptime(iso_date, "%Y-%m-%d")
    if d.weekday() >= 5:  # 0=周一 ... 4=周五
        return False
    return not _is_fixed_holiday(d.date())


def get_calendar_status(year: int | None = None) -> dict:
    """返回指定年度日历的可用状态 (L3 降级可观测).

    调用方可据此判断当前 `is_trading_day` 处于精确模式还是降级模式,
    从而决定是否允许自动交易执行。

    Args:
        year: 年份, None 表示当前年

    Returns:
        {
            "year": int,
            "mode": "exact" | "degraded",
            "trading_days": int,      # 精确模式下的交易日数, 降级为 0
            "cache_file": str,
            "safe_for_trading": bool, # 降级模式下建议禁止自动下单
        }
    """
    y = year if year is not None else datetime.now().year
    dates = _load_year_dates(y, allow_fetch=False)
    exact = bool(dates)
    return {
        "year": y,
        "mode": "exact" if exact else "degraded",
        "trading_days": len(dates),
        "cache_file": str(_cache_path(y)),
        "safe_for_trading": exact,
    }


def next_trading_day(date: str | None = None, max_lookahead: int = 30) -> str:
    """获取下一交易日 (跳过周末和节假日)

    Args:
        date: 起始日期, None 表示今天
        max_lookahead: 最多向后查看天数 (防止无限循环)

    Returns:
        下一交易日 'YYYY-MM-DD'
    """
    if date is None:
        d = datetime.now()
    else:
        clean = date.replace("-", "").replace("/", "")
        d = datetime.strptime(clean, "%Y%m%d")

    # 缓存当前年和下一年的交易日 (避免每次循环都加载)
    years_needed = {d.year, d.year + 1}
    year_dates_map = {y: _load_year_dates(y) for y in years_needed}

    for _ in range(max_lookahead):
        d = d + timedelta(days=1)
        iso = d.strftime("%Y-%m-%d")
        year_dates = year_dates_map.get(d.year) or _load_year_dates(d.year)
        year_dates_map[d.year] = year_dates
        if year_dates:
            if iso in year_dates:
                return iso
        else:
            # 回退模式: 跳过周末 + 公历固定法定节假日 (与 is_trading_day 口径一致)
            if d.weekday() < 5 and not _is_fixed_holiday(d.date()):
                return iso
    # 兜底: 返回下周一
    while d.weekday() >= 5 or _is_fixed_holiday(d.date()):
        d = d + timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def current_trading_day(date: str | None = None) -> str:
    """获取当前交易日 (如果今天是交易日就返回今天, 否则返回上一交易日)

    Args:
        date: 起始日期, None 表示今天

    Returns:
        当前交易日 'YYYY-MM-DD'
    """
    if date is None:
        d = datetime.now()
    else:
        clean = date.replace("-", "").replace("/", "")
        d = datetime.strptime(clean, "%Y%m%d")

    years_needed = {d.year, d.year - 1}
    year_dates_map = {y: _load_year_dates(y) for y in years_needed}

    for _ in range(30):
        iso = d.strftime("%Y-%m-%d")
        year_dates = year_dates_map.get(d.year) or _load_year_dates(d.year)
        year_dates_map[d.year] = year_dates
        if year_dates:
            if iso in year_dates:
                return iso
        else:
            # 回退模式: 与 is_trading_day 口径一致 (周末 + 固定节假日)
            if d.weekday() < 5 and not _is_fixed_holiday(d.date()):
                return iso
        d = d - timedelta(days=1)
    return date or datetime.now().strftime("%Y-%m-%d")


if __name__ == "__main__":
    # 自测
    today = datetime.now().strftime("%Y-%m-%d")
    logger.info(f"今天: {today}")
    logger.info(f"  是交易日: {is_trading_day(today)}")
    logger.info(f"  下一交易日: {next_trading_day(today)}")
    logger.info(f"  当前交易日: {current_trading_day(today)}")
