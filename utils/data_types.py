"""
数据通用类型与转换工具

目标：
- 统一各数据源/模块的数值转换规则
- 提供股票代码/市场标签标准化工具
- 减少各模块重复实现的转换逻辑
"""

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional

# ============================================
# 安全类型转换
# ============================================


def safe_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    """
    安全转换为浮点数

    处理场景：
    - None / 空字符串 -> default
    - pandas / numpy NaN -> default
    - 数值字符串 -> float
    - 已是数值 -> float
    """
    try:
        if val is None:
            return default

        # L4: bool 是 int 子类, 会被 float(True)=1.0 误转。排除 bool, 返回 default。
        if isinstance(val, bool):
            return default

        if isinstance(val, str):
            val = val.strip()
            if val == "" or val == "-" or val == "--" or val.lower() in ("null", "none", "nan", "inf", "-inf"):
                return default

        try:
            if math.isnan(float(val)):
                return default
        except (ValueError, TypeError):
            pass

        return float(val)
    except (ValueError, TypeError):
        return default


def safe_int(val: Any, default: Optional[int] = None) -> Optional[int]:
    """
    安全转换为整数

    先转换为 float，再取整，处理 "123.0" 这类情况
    """
    f_val = safe_float(val, default=None)
    if f_val is not None:
        return int(f_val)
    return default


# ============================================
# 代码/市场标准化
# ============================================

_ETF_CODE_SUFFIXES = ("SH", "SZ", "BJ")
_CN_EXCHANGE_MAP = {
    "SH": "sh",
    "SZ": "sz",
    "BJ": "bj",
    "上交所": "sh",
    "深交所": "sz",
    "北交所": "bj",
}


def _is_etf_code(code: Optional[str]) -> bool:
    """
    粗略判断是否为 ETF 代码。
    该实现仅做兼容层；如需精确判断，建议接入本地持仓/行情元数据。
    """
    if not code:
        return False
    s = str(code).strip().upper()
    return s.startswith("5") or "ETF" in s


def normalize_stock_code(code: Optional[str]) -> str:
    """
    股票代码标准化

    尽量返回带市场前缀的格式，例如：
    - 600000 -> sh600000
    - 000001 -> sz000001
    - 300308 -> sz300308
    - 510300 -> sh510300
    """
    if not code:
        return ""

    s = str(code).strip()

    # 已经是带前缀格式
    if s.startswith(("sh", "sz", "bj", "SH", "SZ", "BJ")):
        return s

    # L2: 港股代码常为 5 位纯数字 (如 00700/00005), A股为 6 位。
    # 5 位纯数字 (非 9 开头 B 股) 视为港股, 返回原样, 避免误加 A股前缀。
    if s.isdigit() and len(s) == 5 and not s.startswith("9"):
        return s

    prefix = "sh"
    # L2: 7 开头为沪市新股 (730xxx 新股申购等), 显式归 sh
    if s.startswith(("6", "5", "7", "9")):
        prefix = "sh"
    elif s.startswith(("0", "3")):
        prefix = "sz"
    elif s.startswith(("4", "8")):
        prefix = "bj"
    s = f"{prefix}{s}"

    return s


# ============================================
# 市场标签/币种辅助
# ============================================


def get_market_tag(code: Optional[str]) -> str:
    """
    根据标准化代码推断市场标签
    返回 cn / hk / us / tw / jp / kr / unknown
    """
    if not code:
        return "unknown"

    s = str(code).strip().lower()
    if s.startswith(("hk", "hkex", "00700", "09988")):
        return "hk"
    # L2: 港股代码常为 5 位纯数字 (如 00700/00005/00941), 而 A股为 6 位。
    # 5 位纯数字 (无市场前缀) 视为港股, 避免误判为 cn。
    if s.isdigit() and len(s) == 5 and not s.startswith("9"):
        return "hk"
    if any(ch.isdigit() for ch in s) and not s.startswith(("us", "nas", "nyq")):
        return "cn"
    if s.startswith(("us", "nas", "nyq")):
        return "us"
    if s.startswith("jpy") or s.startswith("jp"):
        return "jp"
    if s.startswith("kr") or s.startswith("ks"):
        return "kr"
    if s.startswith("tw"):
        return "tw"
    return "unknown"


def get_currency_tag(code: Optional[str]) -> str:
    """
    根据代码推断报价币种
    """
    market = get_market_tag(code)
    return {"cn": "CNY", "hk": "HKD", "us": "USD", "tw": "TWD", "jp": "JPY", "kr": "KRW"}.get(market, "CNY")


# ============================================
# 行情结果辅助
# ============================================


@dataclass
class QuoteResult:
    """
    行情结果包装，便于上层在不依赖具体 Fetcher 类型的前提下处理结果
    """

    code: str
    price: Optional[float] = None
    raw: Any = None
    source: str = ""
    is_fallback: bool = False
    error: Optional[str] = None

    @property
    def is_ok(self) -> bool:
        return self.error is None and self.price is not None

    @property
    def price_safe(self) -> float:
        return self.price if self.price is not None else 0.0


@dataclass
class SourceHealth:
    """
    数据源健康状态快照，供监控/降级决策使用
    """

    code: str
    available: bool
    failure_count: int
    last_error: Optional[str] = None
    latency_ms: Optional[float] = None
    last_checked_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "available": self.available,
            "failure_count": self.failure_count,
            "last_error": self.last_error,
            "latency_ms": self.latency_ms,
            "last_checked_at": self.last_checked_at,
        }
