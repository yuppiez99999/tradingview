"""W6.3.3 Step 0: 统一合约代码解析 + NewType 类型分层 (QS-Trader 风格)。

设计目标:
    - 消除全系统 8 种代码格式分裂 (裸码6位 / Wind后缀 / 东财secid / sh+前缀 / ...)
    - 提供 1 个公开入口 parse_symbol() 替代 3+ 处本地实现
    - NewType 运行时退化为 str, 不影响生产行为; 静态层面 mypy 可区分
    - strict 模式抛 SymbolParseError 不降级, 与 W6.3.2 前视偏差门禁范式一致

使用方式:
    from utils.contracts.symbols import parse_symbol, WindCode, EastMoneySecId

    info = parse_symbol("600519.SH")
    # → SymbolInfo(wind_code="600519.SH", code6="600519", exchange="SSE", ...)

    info = parse_symbol("IF2507.CFFEX")
    # → SymbolInfo(wind_code="IF2507.CFFEX", code6="IF2507", exchange="CFFEX",
    #             asset_type="INDEX_FUTURE", product="IF", ...)

    secid = to_eastmoney_secid("510300.SH")  # → "1.510300"

向后兼容:
    - strict=False (默认): 未知格式仅 RuntimeWarning, 返回 best-effort 结果
    - strict=True: 非法格式抛 SymbolParseError
    - NewType 在运行时 = str, 传入旧函数零修改

设计原则 (AGENTS.md):
    - 不可变性 (§5.1): SymbolInfo 为 dataclass(frozen=True)
    - 单一职责: 本模块只做解析, 不做行情/交易
    - 多小文件 (§5.3): 本模块 < 400 行
"""

from __future__ import annotations

import re
import warnings as _warnings
from dataclasses import dataclass, field
from typing import Literal, NewType

# ============================================================
# NewType 定义 (运行时 = str, mypy 静态可区分)
# ============================================================

AShareCode6 = NewType("AShareCode6", str)  # "600519" (6位裸码)
WindCode = NewType("WindCode", str)  # "600519.SH" / "IF2507.CFFEX"
EastMoneySecId = NewType("EastMoneySecId", str)  # "1.600519"
FuturesContractCode = NewType("FuturesContractCode", str)  # "IF2507.CFFEX"
ExchangeCode = NewType("ExchangeCode", str)  # "SSE" / "CFFEX" / "SHFE"
ProductCode = NewType("ProductCode", str)  # "IF" / "CU" / "600519"


# ============================================================
# 异常
# ============================================================


class SymbolParseError(ValueError):
    """合约代码解析失败 (strict 模式下抛出, 不降级)。

    与 W6.3.2 NonMonotonicTimestampError 同属"防前视偏差硬门禁"范式:
    非法代码可能导致 secid 错分市场 → 请求错数据 → 组合权益失真。

    Attributes:
        raw: 原始输入字符串
        reason: 失败原因
    """

    def __init__(self, raw: str, reason: str) -> None:
        self.raw = raw
        self.reason = reason
        super().__init__(f"SymbolParseError: 无法解析合约代码 {raw!r} — {reason}")


# ============================================================
# 交易所常量 + 映射表
# ============================================================

EXCHANGES = frozenset(
    {
        "SSE",
        "SZSE",
        "BSE",  # 股票
        "CFFEX",
        "SHFE",
        "INE",
        "DCE",
        "CZCE",
        "GFEX",  # 期货
    }
)

# Wind 后缀 → 规范化交易所 (兼容旧写法 SHF→SHFE, ZCE→CZCE)
_SUFFIX_TO_EXCHANGE: dict[str, str] = {
    "SH": "SSE",
    "SHSE": "SSE",
    "SZ": "SZSE",
    "BJ": "BSE",
    "CFFEX": "CFFEX",
    "SHFE": "SHFE",
    "SHF": "SHFE",
    "INE": "INE",
    "DCE": "DCE",
    "CZCE": "CZCE",
    "ZCE": "CZCE",
    "GFEX": "GFEX",
}

# 规范化交易所 → Wind 后缀 (输出用, 统一为规范形式)
_EXCHANGE_TO_SUFFIX: dict[str, str] = {
    "SSE": "SH",
    "SZSE": "SZ",
    "BSE": "BJ",
    "CFFEX": "CFFEX",
    "SHFE": "SHFE",
    "INE": "INE",
    "DCE": "DCE",
    "CZCE": "CZCE",
    "GFEX": "GFEX",
}

# 东财 secid 市场前缀
_EM_SH = "1"  # 沪市
_EM_SZ = "0"  # 深市
_EM_BJ = "1"  # 北交所 (W6.3.3 Step1: 与旧 astock_realtime fallback 保持 1.xxx 一致,
# 新前缀判定 83/43/87/88 ∈ BSE 后, 若用 0.xxx 会与旧 1.xxx 差异 → 保守对齐)
_EM_UNKNOWN = "1"  # 未知前缀 (与旧 astock_realtime fallback 一致, 默认 1.xxx)

# 股票/ETF/可转债 前缀规则表
#   (前缀元组, 交易所, 资产类型, 东财市场前缀)
#   注意: 3 位前缀 (113/110/123/127/128) 被 2 位前缀 (11/12) 覆盖, 资产类型一致, 无冲突
_PREFIX_RULES: list[tuple[tuple[str, ...], str, str, str]] = [
    (("60",), "SSE", "STOCK", _EM_SH),
    (("68",), "SSE", "STOCK", _EM_SH),  # 科创板
    (("9",), "SSE", "B_STOCK", _EM_SH),  # 沪 B 股
    (("11",), "SSE", "CONVERTIBLE_BOND", _EM_SH),  # 沪可转债 (含 113/110)
    (("51", "58"), "SSE", "ETF", _EM_SH),
    (("00",), "SZSE", "STOCK", _EM_SZ),
    (("30",), "SZSE", "STOCK", _EM_SZ),  # 创业板
    (("12",), "SZSE", "CONVERTIBLE_BOND", _EM_SZ),  # 深可转债 (含 123/127/128)
    (("15", "16"), "SZSE", "ETF", _EM_SZ),
    (("83", "43", "87", "88"), "BSE", "BSE_STOCK", _EM_BJ),
]

# 期货合约正则 (修复 W6.3.3 难点 §2.1: SHFE/INE/CZCE 完整, 兼容 SHF/ZCE 旧写法)
FUTURES_CODE_PATTERN = re.compile(
    r"^([A-Za-z]+)(\d{2})(\d{2})\.(CFFEX|SHFE|SHF|INE|DCE|CZCE|ZCE|GFEX)$"
)

# 东财 secid 输入格式 (如 "1.600519")
_EM_SECID_PATTERN = re.compile(r"^(\d)\.(\d{6})$")


# ============================================================
# SymbolInfo 数据类 (不可变)
# ============================================================


@dataclass(frozen=True)
class SymbolInfo:
    """parse_symbol() 的解析结果 (不可变)。

    Attributes:
        raw: 原始输入字符串
        wind_code: 规范化 Wind 码 "600519.SH" / "IF2507.CFFEX"
        code6: 6 位裸码 (股票) 或合约号 (期货) "600519" / "IF2507"
        exchange: 规范化交易所 "SSE" / "CFFEX"
        asset_type: STOCK / ETF / CONVERTIBLE_BOND / B_STOCK / BSE_STOCK /
                    INDEX_FUTURE / COMMODITY_FUTURE / UNKNOWN
        product: 品种代码 "600519" / "IF" / "CU"
        eastmoney_secid: 东财 secid "1.600519" (期货暂为 best-effort)
        futures_year: 期货年份 (25=2025), 非期货为 None
        futures_month: 期货月份, 非期货为 None
        warnings: 解析过程中的警告列表 (strict=False 时填充)
    """

    raw: str
    wind_code: str
    code6: str
    exchange: str
    asset_type: str
    product: str
    eastmoney_secid: str
    futures_year: int | None = None
    futures_month: int | None = None
    warnings: list = field(default_factory=list)


# ============================================================
# 内部辅助
# ============================================================


def _normalize_exchange(suffix: str) -> str:
    """Wind 后缀 → 规范化交易所代码。"""
    exc = _SUFFIX_TO_EXCHANGE.get(suffix.upper())
    if exc is None:
        raise SymbolParseError(suffix, f"未知交易所后缀 {suffix!r}")
    return exc


def _exchange_to_suffix(exchange: str) -> str:
    """规范化交易所 → Wind 后缀。"""
    suffix = _EXCHANGE_TO_SUFFIX.get(exchange.upper())
    if suffix is None:
        raise SymbolParseError(exchange, f"未知交易所代码 {exchange!r}")
    return suffix


def _classify_by_prefix(code6: str) -> tuple[str, str, str]:
    """6 位裸码 → (交易所, 资产类型, 东财市场前缀)。

    Raises:
        SymbolParseError: 前缀无法识别
    """
    for prefixes, exc, atype, em_mkt in _PREFIX_RULES:
        if code6.startswith(prefixes):
            return exc, atype, em_mkt
    raise SymbolParseError(code6, f"无法识别的代码前缀 {code6[:2]!r}")


def _parse_futures_match(raw: str, m: re.Match) -> SymbolInfo:
    """解析期货合约 (正则已匹配)。"""
    product, yy, mm, suffix = (
        m.group(1),
        m.group(2),
        m.group(3),
        m.group(4),
    )
    exchange = _normalize_exchange(suffix)
    code6 = f"{product}{yy}{mm}"
    wind_code = f"{code6}.{_exchange_to_suffix(exchange)}"
    is_index = product.upper() in ("IF", "IC", "IH", "IM")
    atype = "INDEX_FUTURE" if is_index else "COMMODITY_FUTURE"
    # 东财期货 secid 格式较复杂 (如 8.IF2507), Step 2 补充; 暂 best-effort
    secid = f"0.{code6}"
    return SymbolInfo(
        raw=raw,
        wind_code=wind_code,
        code6=code6,
        exchange=exchange,
        asset_type=atype,
        product=product,
        eastmoney_secid=secid,
        futures_year=2000 + int(yy),
        futures_month=int(mm),
    )


# ============================================================
# 公开 API: parse_symbol
# ============================================================


def parse_symbol(
    s: str,
    *,
    hint_asset: Literal["auto", "stock", "etf", "future", "option", "bond"] = "auto",
    strict: bool = False,
) -> SymbolInfo:
    """统一合约代码解析入口 — 替代全系统 3+ 处本地实现。

    支持的输入格式:
        - Wind 码: "600519.SH" / "IF2507.CFFEX" / "CU2508.SHFE"
        - 裸码: "600519" / "510300" (自动推断交易所)
        - 东财 secid: "1.600519" / "0.300750"
        - 期货旧写法: "CU2508.SHF" / "CF2509.ZCE" (自动规范化为 SHFE/CZCE)

    Args:
        s: 输入代码字符串
        hint_asset: 资产类型提示 (当前仅 "future" 会强制走期货路径, 其余 auto)
        strict: True 时非法格式抛 SymbolParseError; False (默认) 仅 warning

    Returns:
        SymbolInfo 不可变解析结果
    """
    raw = s.strip()
    warns: list[str] = []

    # 1. 东财 secid 输入 ("1.600519")
    em_m = _EM_SECID_PATTERN.match(raw)
    if em_m:
        market, code6 = em_m.group(1), em_m.group(2)
        try:
            exchange, atype, _ = _classify_by_prefix(code6)
        except SymbolParseError:
            exchange = "SSE" if market == "1" else "SZSE"
            atype = "UNKNOWN"
            warns.append(f"secid 市场前缀 {market} 与裸码 {code6} 前缀不匹配, 降级")
        wind_code = f"{code6}.{_exchange_to_suffix(exchange)}"
        return SymbolInfo(
            raw=raw,
            wind_code=wind_code,
            code6=code6,
            exchange=exchange,
            asset_type=atype,
            product=code6,
            eastmoney_secid=raw,
            warnings=warns,
        )

    # 2. 期货合约 (正则匹配)
    fut_m = FUTURES_CODE_PATTERN.match(raw)
    if fut_m:
        return _parse_futures_match(raw, fut_m)
    if hint_asset == "future" and not fut_m:
        if strict:
            raise SymbolParseError(raw, "hint_asset='future' 但代码不匹配期货正则")
        warns.append(f"hint_asset='future' 但代码 {raw!r} 不匹配期货正则, 降级 auto")

    # 3. Wind 码 (含 ".")
    if "." in raw:
        parts = raw.split(".", 1)
        code6, suffix = parts[0], parts[1].upper()
        try:
            exchange = _normalize_exchange(suffix)
        except SymbolParseError:
            if strict:
                raise
            warns.append(f"未知交易所后缀 {suffix!r}, 降级为 UNKNOWN")
            exchange = "UNKNOWN"
        try:
            exc2, atype, em_mkt = _classify_by_prefix(code6)
            if exchange == "UNKNOWN":
                exchange = exc2
        except SymbolParseError:
            if strict:
                raise
            atype = "UNKNOWN"
            em_mkt = "0"
            warns.append(f"无法识别裸码前缀 {code6[:2]!r}")
        if exchange != "UNKNOWN":
            wind_code = f"{code6}.{_exchange_to_suffix(exchange)}"
            secid = f"{em_mkt}.{code6}"
        else:
            wind_code = raw
            # W6.3.3 Step1: 与旧 astock_realtime fallback 对齐 (未知前缀默认 1.xxx)
            secid = f"{_EM_UNKNOWN}.{code6}"
        return SymbolInfo(
            raw=raw,
            wind_code=wind_code,
            code6=code6,
            exchange=exchange,
            asset_type=atype,
            product=code6,
            eastmoney_secid=secid,
            warnings=warns,
        )

    # 4. 裸码 (无后缀, 自动推断)
    code6 = raw
    try:
        exchange, atype, em_mkt = _classify_by_prefix(code6)
    except SymbolParseError:
        if strict:
            raise
        _warnings.warn(
            f"parse_symbol: 无法识别裸码前缀 {code6[:2]!r} "
            f"(code={code6!r}), 降级为 UNKNOWN",
            RuntimeWarning,
            stacklevel=2,
        )
        return SymbolInfo(
            raw=raw,
            wind_code=raw,
            code6=code6,
            exchange="UNKNOWN",
            asset_type="UNKNOWN",
            product=code6,
            # W6.3.3 Step1: 与旧 astock_realtime fallback 对齐 (未知前缀默认 1.xxx)
            eastmoney_secid=f"{_EM_UNKNOWN}.{code6}",
            warnings=[f"无法识别裸码前缀 {code6[:2]!r}"],
        )
    wind_code = f"{code6}.{_exchange_to_suffix(exchange)}"
    secid = f"{em_mkt}.{code6}"
    return SymbolInfo(
        raw=raw,
        wind_code=wind_code,
        code6=code6,
        exchange=exchange,
        asset_type=atype,
        product=code6,
        eastmoney_secid=secid,
        warnings=warns,
    )


# ============================================================
# 便捷函数 (替代 3 处本地实现)
# ============================================================


def to_eastmoney_secid(s: str) -> str:
    """便捷函数: 任意代码格式 → 东财 secid。

    替代 utils.astock_realtime._secid() 和 utils.etf_flow_monitor 行内拼接。
    """
    return parse_symbol(s).eastmoney_secid


def to_wind_code(s: str) -> str:
    """便捷函数: 任意代码格式 → 规范化 Wind 码。

    用于统一 backtest 引擎中 order.code == event.code 的格式。
    """
    return parse_symbol(s).wind_code


def normalize_exchange(suffix: str) -> str:
    """Wind 后缀 → 规范化交易所代码 (公开接口)。

    "SH" → "SSE", "SHF" → "SHFE", "ZCE" → "CZCE", ...
    """
    return _normalize_exchange(suffix)


__all__ = [
    # NewType
    "AShareCode6",
    "WindCode",
    "EastMoneySecId",
    "FuturesContractCode",
    "ExchangeCode",
    "ProductCode",
    # 异常
    "SymbolParseError",
    # 数据类
    "SymbolInfo",
    # 函数
    "parse_symbol",
    "to_eastmoney_secid",
    "to_wind_code",
    "normalize_exchange",
    # 常量
    "EXCHANGES",
    "FUTURES_CODE_PATTERN",
]
