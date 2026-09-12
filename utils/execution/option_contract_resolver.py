"""option_contract_resolver — 描述性期权订单 -> 具体合约代码解析器.

把 `hedge_order_executor` 的"描述性"订单 (instrument='510300 Put', strike_rule='OTM 5%')
解析为具体可交易合约交易代码 (如 '510300P2612M03800', 上交所 ETF 期权交易代码).

设计原则 (对齐项目铁律):
- 解析失败 / 无真实期权链 / 无匹配合约 -> 返回 ``None``, 由调用方 fail-open 处理
  (真实分支记录 SKIPPED 并告警; 模拟分支根本不调用本解析器).
- 不复刻期权链获取逻辑, 直接复用 :class:`utils.option_data_fetcher.OptionDataFetcher`
  的真实链 (AKShare ``option_finance_board`` 上交所 T 型行情).
- 本模块只做"选价 + 编码", 不触碰任何下单接口, 绝不引入裸实盘.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# instrument 形如 "510300 Put" / "510050 Call" / "159915 Put"
_INSTRUMENT_RE = re.compile(r"(?P<code>\d{6})\.?\w*\s*(?P<type>PUT|CALL)", re.IGNORECASE)
_OTM_RE = re.compile(r"OTM\s*(\d+(?:\.\d+)?)\s*%?", re.IGNORECASE)


def _parse_underlying_and_type(order: dict[str, Any]) -> tuple[str | None, str | None]:
    """从订单提取标的 6 位代码与期权类型 (put/call).

    优先 instrument 后缀 (510300 Put), 回退到 direction (BUY_PUT / SELL_CALL_COVERED)
    配合 underlying 字段. 返回 ``(underlying_code, 'put'|'call')``; 任一缺失返回 ``(None, None)``.
    """
    instrument = str(order.get("instrument", "") or "")
    m = _INSTRUMENT_RE.search(instrument)
    if m:
        code = m.group("code")
        otype = "call" if m.group("type").upper() == "CALL" else "put"
        return code, otype

    direction = str(order.get("direction", "") or "").upper()
    und = str(order.get("underlying", order.get("underlying_code", "")) or "")
    code_m = re.match(r"(\d{6})", und)
    if code_m:
        if "PUT" in direction:
            return code_m.group(1), "put"
        if "CALL" in direction:
            return code_m.group(1), "call"
    return None, None


def _parse_otm_fraction(order: dict[str, Any]) -> float | None:
    """从 strike_rule ('OTM 5%') 提取虚值比例; 缺省/解析失败返回 ``None``."""
    rule = str(order.get("strike_rule", order.get("otm", "")) or "")
    m = _OTM_RE.search(rule)
    if not m:
        return None
    try:
        return float(m.group(1)) / 100.0
    except (ValueError, TypeError):
        return None


# 进程内缓存: (underlying, otype, round(spot,2), otm, dte_band) -> contract code | None
_CACHE: dict[tuple, str | None] = {}


def resolve_option_contract(
    order: dict[str, Any],
    spot_price: float,
    *,
    dte_band: tuple[int, int] = (20, 90),
    fetcher: Any | None = None,
) -> str | None:
    """将描述性期权订单解析为具体合约交易代码 (如 '510300P2612M03800').

    Args:
        order: 含 ``instrument`` / ``strike_rule`` / ``direction`` 的订单 dict.
        spot_price: 标的当前价 (调用方提供, 复用 ``_load_underlying_price`` 结果).
        dte_band: 候选到期日区间 (自然日), 默认 20-90 天偏短端流动性.
        fetcher: 可注入 ``OptionDataFetcher`` 实例 (测试用); 缺省现场构造.

    Returns:
        具体合约交易代码字符串; 任一环节失败返回 ``None`` (fail-open).
    """
    if spot_price is None or spot_price <= 0:
        logger.warning("[resolver] spot_price 无效: %r — 无法解析合约", spot_price)
        return None

    underlying, otype = _parse_underlying_and_type(order)
    if underlying is None or otype is None:
        logger.warning(
            "[resolver] 无法从订单解析标的/类型: instrument=%s direction=%s",
            order.get("instrument"),
            order.get("direction"),
        )
        return None

    otm = _parse_otm_fraction(order)
    if otm is None:
        logger.warning("[resolver] 无法解析 strike_rule: %s", order.get("strike_rule"))
        return None

    cache_key = (underlying, otype, round(float(spot_price), 2), otm, dte_band)
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    try:
        if fetcher is None:
            from utils.option_data_fetcher import OptionDataFetcher

            fetcher = OptionDataFetcher()
        tol = max(otm * 0.25, 0.005)  # 允许 ±25% 或至少 ±0.5% 的虚值误差带
        otm_lo, otm_hi = otm - tol, otm + tol
        chain = fetcher.get_real_chain(
            underlying,
            option_type=otype,
            otm_range=(otm_lo, otm_hi),
            spot_price=float(spot_price),
            dte_range=dte_band,
        )
    except Exception as exc:  # noqa: BLE001 — 观测路径 fail-open, 不静默
        logger.warning("[resolver] 期权链获取异常, 解析降级 Skip: %s", exc)
        _CACHE[cache_key] = None
        return None

    if not chain:
        logger.warning(
            "[resolver] 标的 %s 无匹配 %s 合约 (OTM≈%.1f%%) — 返回 None",
            underlying, otype, otm * 100,
        )
        _CACHE[cache_key] = None
        return None

    # 目标行权价: Put 在下方, Call 在上方
    target = spot_price * (1.0 - otm) if otype == "put" else spot_price * (1.0 + otm)

    def _score(c: dict[str, Any]) -> tuple[float, int]:
        # 优先行权价最接近目标 (对冲 Delta 更准), 其次最短到期 (流动性/时间价值)
        strike = float(c.get("strike") or 0)
        dte = int(c.get("dte") or 0)
        return (abs(strike - target), dte)

    chosen = min(chain, key=_score)
    code = str(chosen.get("contract") or "")
    if not code:
        logger.warning("[resolver] 命中合约但缺 contract 字段, 跳过")
        _CACHE[cache_key] = None
        return None
    logger.info(
        "[resolver] %s %s OTM%.1f%% -> %s (strike=%.3f, dte=%d)",
        underlying, otype, otm * 100, code,
        float(chosen.get("strike") or 0), int(chosen.get("dte") or 0),
    )
    _CACHE[cache_key] = code
    return code
