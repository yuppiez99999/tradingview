"""option_contract_resolver 单测: OTM 解析 / 选价编码 / 失败降级 fail-open."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from utils.execution.option_contract_resolver import (  # noqa: E402
    _parse_otm_fraction,
    _parse_underlying_and_type,
    resolve_option_contract,
)

# 模拟 OptionDataFetcher.get_real_chain 返回 (字段对齐 utils.option_data_fetcher)
FAKE_CHAIN = [
    {"contract": "510300P2610M03800", "strike": 3.80, "expiry": "2026-10-28", "dte": 30, "option_type": "put", "premium": 0.10},
    {"contract": "510300P2612M03800", "strike": 3.80, "expiry": "2026-12-23", "dte": 102, "option_type": "put", "premium": 0.12},
    {"contract": "510300P2609M03800", "strike": 3.80, "expiry": "2026-09-23", "dte": 12, "option_type": "put", "premium": 0.09},
    {"contract": "510300P2612M03900", "strike": 3.90, "expiry": "2026-12-23", "dte": 102, "option_type": "put", "premium": 0.18},
    {"contract": "510300C2611M04200", "strike": 4.20, "expiry": "2026-11-26", "dte": 80, "option_type": "call", "premium": 0.15},
]


def _fetcher_returning(chain):
    """模拟 OptionDataFetcher.get_real_chain, 并复刻其 dte_range 过滤 (resolver 不负责 dte 过滤)."""
    f = MagicMock()

    def _get(underlying, option_type=None, dte_range=None, otm_range=None,
             spot_price=None, min_volume=0):
        result = list(chain)
        if option_type:
            result = [c for c in result if c.get("option_type") == option_type]
        if dte_range:
            lo, hi = dte_range
            result = [c for c in result if lo <= int(c.get("dte", 0)) <= hi]
        return result

    f.get_real_chain.side_effect = _get
    return f


@pytest.fixture(autouse=True)
def _clear_cache():
    from utils.execution.option_contract_resolver import _CACHE

    _CACHE.clear()
    yield


def test_parse_underlying_and_type():
    assert _parse_underlying_and_type({"instrument": "510300 Put"}) == ("510300", "put")
    assert _parse_underlying_and_type({"instrument": "510050 Call"}) == ("510050", "call")
    assert _parse_underlying_and_type({"instrument": "159915 Put"}) == ("159915", "put")
    assert _parse_underlying_and_type({"direction": "BUY_PUT", "underlying": "510300.SH"}) == ("510300", "put")
    assert _parse_underlying_and_type({"instrument": "FOO"}) == (None, None)


def test_parse_otm_fraction():
    assert _parse_otm_fraction({"strike_rule": "OTM 5%"}) == 0.05
    assert _parse_otm_fraction({"strike_rule": "OTM5%"}) == 0.05
    assert _parse_otm_fraction({"strike_rule": "OTM 8.5%"}) == 0.085
    assert _parse_otm_fraction({"strike_rule": ""}) is None
    assert _parse_otm_fraction({"strike_rule": "ATM"}) is None


def test_resolve_put_closest_strike_shortest_dte():
    # spot 4.00 -> Put 目标行权价 3.80; 同 strike 中 dte=30 最短 -> 2610 合约
    order = {"instrument": "510300 Put", "strike_rule": "OTM 5%"}
    f = _fetcher_returning(FAKE_CHAIN)
    code = resolve_option_contract(order, 4.00, fetcher=f)
    assert code == "510300P2610M03800"


def test_resolve_call_otm():
    order = {"instrument": "510300 Call", "strike_rule": "OTM 5%"}
    f = _fetcher_returning(FAKE_CHAIN)
    code = resolve_option_contract(order, 4.00, fetcher=f)
    assert code == "510300C2611M04200"


def test_resolve_dte_band_respected():
    # 放宽 dte_band 纳入 dte=12 合约 -> 同 strike 中最短 dte 胜出
    order = {"instrument": "510300 Put", "strike_rule": "OTM 5%"}
    f = _fetcher_returning(FAKE_CHAIN)
    code = resolve_option_contract(order, 4.00, dte_band=(5, 120), fetcher=f)
    assert code == "510300P2609M03800"


def test_resolve_no_chain_returns_none_fail_open():
    order = {"instrument": "510300 Put", "strike_rule": "OTM 5%"}
    f = _fetcher_returning([])
    assert resolve_option_contract(order, 4.00, fetcher=f) is None


def test_resolve_bad_spot_returns_none():
    order = {"instrument": "510300 Put", "strike_rule": "OTM 5%"}
    f = _fetcher_returning(FAKE_CHAIN)
    assert resolve_option_contract(order, 0, fetcher=f) is None
    assert resolve_option_contract(order, -1.0, fetcher=f) is None


def test_resolve_unparseable_order_returns_none():
    f = _fetcher_returning(FAKE_CHAIN)
    assert resolve_option_contract({"instrument": "???", "strike_rule": "OTM 5%"}, 4.0, fetcher=f) is None
    assert resolve_option_contract({"instrument": "510300 Put", "strike_rule": "ATM"}, 4.0, fetcher=f) is None
