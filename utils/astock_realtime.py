"""
A股实时行情接入层 (零 key, 不封 IP).

数据源优先级: 东财 push2 (主, 真实行情+资金流) -> 腾讯财经 qt.gtimg.cn (回退) -> 本地缓存.
与现有 Wind MCP 降级链解耦, 作为免费实时价兜底层 (插在新浪之前), 服务报告与下单.

来自 A股全栈数据 skill (V3.2.1) 验证过的接口:
- 东财 push2 ulist 批量行情: 现价/昨收/PE/PB/总市值/涨跌停
- 东财 push2 fflow 主力净流入: 真实资金流向 (元 -> 亿)
- 腾讯财经 qt.gtimg.cn: 实时价/PE/PB/涨跌/市值 (GBK)
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request

# W6.3.3 Step 1: 统一合约代码解析入口 (替代本地 _secid 前缀判定)
from utils.contracts.symbols import to_eastmoney_secid
from utils.logger import get_logger

logger = get_logger("astock_realtime")

CACHE_TTL = 60  # 秒
_cache: dict[str, tuple] = {}
_cache_lock = threading.Lock()
_eastmoney_blocked = False


def _secid(code: str) -> str:
    """6位代码 -> 东财 secid (市场.代码).

    W6.3.3 Step 1: 内部委托给 utils.contracts.symbols.to_eastmoney_secid()
    (统一入口)，保留函数签名与返回值格式以保持向后兼容。

    行为对齐 (与旧前缀判定 100% 一致):
        - 51/58/60/68/9/11 开头 -> 1.xxx
        - 15/16/00/30/12 开头 -> 0.xxx
        - 未知前缀 -> 1.xxx (与旧 fallback 一致)
    """
    # to_eastmoney_secid 内部对裸码走前缀推断，行为 100% 对齐
    return to_eastmoney_secid(code)


def _tx_prefix(code: str) -> str:
    """6位代码 -> 腾讯/新浪行情前缀 (sh / sz + 裸码).

    W6.3.3 Step 1: 继续委托 _secid -> 统一入口判断市场前缀。
    """
    sid = _secid(code)
    return ("sh" if sid.startswith("1.") else "sz") + str(code).strip()


# 独立无代理 opener, 绕过 Wind MCP 可能安装的全局 opener / 环境变量代理污染
_opener = urllib.request.build_opener(
    urllib.request.HTTPHandler(),
    urllib.request.HTTPSHandler(),
)


def _http_get(url: str, ref: str | None = None, timeout: int = 10) -> bytes:
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")
    if ref:
        req.add_header("Referer", ref)
    return _opener.open(req, timeout=timeout).read()  # type: ignore

def get_eastmoney_quotes(codes: list[str]) -> dict[str, dict]:
    """东财 push2 批量行情 (主源). 返回 {code: {price, pre_close, change_pct, pe, pb, mktcap_yi, ...}}."""
    global _eastmoney_blocked
    out: dict[str, dict] = {}
    if not codes or _eastmoney_blocked:
        return out
    secids = ",".join(_secid(c) for c in codes)
    url = (
        "https://push2.eastmoney.com/api/qt/ulist/get?pn=1&pz=2000"
        "&fields=f12,f13,f14,f43,f57,f60,f116,f162,f164,f167,f168,f170"
        f"&fltt=2&secids={secids}"
    )
    try:
        raw = _http_get(url, ref="https://quote.eastmoney.com/").decode("utf-8")
        d = json.loads(raw)
        diff = (d.get("data") or {}).get("diff") or []
        items = diff.values() if isinstance(diff, dict) else diff
        for it in items:
            code = str(it.get("f12", "")).strip()
            if not code:
                continue
            price = float(it.get("f43") or 0)
            pre = float(it.get("f60") or 0)
            mktcap = float(it.get("f116") or 0)
            pe = float(it.get("f162") or 0)
            pb = float(it.get("f167") or 0)
            out[code] = {
                "code": code,
                "name": it.get("f14", ""),
                "price": price,
                "pre_close": pre,
                "change_pct": round((price - pre) / pre * 100, 2) if pre else 0.0,
                "mktcap_yi": round(mktcap / 1e8, 2) if mktcap else 0.0,
                "pe": pe or None,
                "pe_static": float(it.get("f164") or 0) or None,
                "pb": pb or None,
                "limit_up": float(it.get("f168") or 0) or None,
                "limit_down": float(it.get("f170") or 0) or None,
                "source": "eastmoney",
            }
        if out:
            logger.info(f"东财实时价成功: {len(out)} 只")
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        logger.warning(f"东财实时价获取失败: {e}")
        _eastmoney_blocked = True
        logger.warning("东财实时价被封禁/不可用，本次运行内跳过后续尝试")
    return out


def get_tencent_quotes(codes: list[str]) -> dict[str, dict]:
    """腾讯财经 qt.gtimg.cn 行情 (回退源). 单位: 市值为亿元."""
    out: dict[str, dict] = {}
    if not codes:
        return out
    url = "https://qt.gtimg.cn/q=" + ",".join(_tx_prefix(c) for c in codes)
    try:
        raw = _http_get(url).decode("gbk")
        for line in raw.split(";"):
            line = line.strip()
            if "=" in line:
                k, _, v = line.partition("=")
                f = v.strip('"').split("~")
                if len(f) > 46:
                    code = k.replace("v_", "").replace("sh", "").replace("sz", "")[:6]
                    code = code[:6]
                    price = float(f[3] or 0)
                    pre = float(f[4] or 0)
                    pe = float(f[39] or 0)
                    pb = float(f[46] or 0)
                    out[code] = {
                        "code": code,
                        "name": f[1],
                        "price": price,
                        "pre_close": pre,
                        "change_pct": float(f[32] or 0) if f[32] else 0.0,
                        "mktcap_yi": float(f[45] or 0) or 0.0,
                        "pe": pe or None,
                        "pb": pb or None,
                        "source": "tencent",
                    }
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        logger.warning(f"腾讯实时价获取失败: {e}")
    return out


def get_realtime_quotes(codes: list[str], use_cache: bool = True) -> dict[str, dict]:
    """优先东财 -> 腾讯回退 -> 缓存. 返回 {code: 行情dict}."""
    codes = [str(c).strip() for c in codes if c]
    if not codes:
        return {}
    key = ",".join(sorted(codes))
    now = time.time()
    if use_cache:
        with _cache_lock:
            c = _cache.get(key)
            if c and now - c[0] < CACHE_TTL:
                return c[1]  # type: ignore[index]
    res = get_eastmoney_quotes(codes)
    missing_or_bad = [
        c for c in codes
        if c not in res or float(res[c].get("price") or 0) == 0
    ]
    if missing_or_bad:
        res.update(get_tencent_quotes(missing_or_bad))
    if use_cache:
        with _cache_lock:
            _cache[key] = (now, res)
    return res
