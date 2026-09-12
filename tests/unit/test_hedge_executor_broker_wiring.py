"""hedge_order_executor 券商接线门控测试: 默认 sim / 实盘就绪才走真实分支 / 装配失败 fail-closed.

验证 _select_option_broker 的四重门控:
- 非实盘就绪 (broker_factory 不可用 / is_live_intent=False / 空配置) -> OptionsSimBroker (行为不变)
- 实盘就绪但真实 broker 装配不出 -> 抛 LiveBrokerUnavailableError (绝不降级模拟)
- 实盘就绪且装配成功 -> _LiveOptionBrokerAdapter, 且 place() 经合约解析器解析合约
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import hedge_order_executor  # noqa: E402

_POS = {"positions": {"510300": {"est_price": 4.0}}}


def test_broker_factory_unavailable_returns_sim(monkeypatch):
    """broker_factory 不可用 -> 降级 OptionsSimBroker, 不阻断链路."""
    monkeypatch.setattr(hedge_order_executor, "_GET_BROKER_AVAILABLE", False)
    monkeypatch.setattr(hedge_order_executor, "is_live_intent", None)
    broker = hedge_order_executor._select_option_broker({}, "2026-09-12")
    assert isinstance(broker, hedge_order_executor.OptionsSimBroker)


def test_not_live_intent_returns_sim(monkeypatch):
    """is_live_intent=False (默认 enabled=false / dry_run / TRADING_ENV≠production) -> sim."""
    monkeypatch.setattr(hedge_order_executor, "_GET_BROKER_AVAILABLE", True)
    monkeypatch.setattr(hedge_order_executor, "is_live_intent", lambda: False)
    broker = hedge_order_executor._select_option_broker({}, "2026-09-12")
    assert isinstance(broker, hedge_order_executor.OptionsSimBroker)


def test_live_intent_unavailable_raises_fail_closed(monkeypatch):
    """实盘就绪但 get_broker 抛 LiveBrokerUnavailableError -> fail-closed 透传, 不降级 sim."""
    monkeypatch.setattr(hedge_order_executor, "_GET_BROKER_AVAILABLE", True)
    monkeypatch.setattr(hedge_order_executor, "is_live_intent", lambda: True)
    monkeypatch.setattr(
        hedge_order_executor,
        "get_broker",
        MagicMock(side_effect=hedge_order_executor.LiveBrokerUnavailableError("装配失败")),
    )
    with pytest.raises(hedge_order_executor.LiveBrokerUnavailableError):
        hedge_order_executor._select_option_broker({}, "2026-09-12")


def test_live_intent_non_live_broker_raises(monkeypatch):
    """实盘就绪却拿到非真实 broker (is_live_broker=False) -> 拒绝, 绝不伪实盘."""
    monkeypatch.setattr(hedge_order_executor, "_GET_BROKER_AVAILABLE", True)
    monkeypatch.setattr(hedge_order_executor, "is_live_intent", lambda: True)
    monkeypatch.setattr(
        hedge_order_executor,
        "get_broker",
        MagicMock(return_value=hedge_order_executor.OptionsSimBroker()),
    )
    monkeypatch.setattr(hedge_order_executor, "is_live_broker", lambda b: False)
    with pytest.raises(RuntimeError):
        hedge_order_executor._select_option_broker({}, "2026-09-12")


def test_live_intent_returns_adapter_and_resolves(monkeypatch):
    """实盘就绪且装配成功 -> 适配器, place() 调用合约解析器得到具体合约代码."""
    monkeypatch.setattr(hedge_order_executor, "_GET_BROKER_AVAILABLE", True)
    monkeypatch.setattr(hedge_order_executor, "is_live_intent", lambda: True)
    fake_real = MagicMock()
    monkeypatch.setattr(hedge_order_executor, "get_broker", MagicMock(return_value=fake_real))
    monkeypatch.setattr(hedge_order_executor, "is_live_broker", lambda b: True)

    captured = {}
    monkeypatch.setattr(
        "hedge_order_executor.resolve_option_contract",
        lambda order, spot, **kw: captured.setdefault("code", "510300P2612M03800"),
    )

    broker = hedge_order_executor._select_option_broker(_POS, "2026-09-12")
    assert isinstance(broker, hedge_order_executor._LiveOptionBrokerAdapter)

    oid = broker.place(
        {
            "order_id": "O1",
            "instrument": "510300 Put",
            "direction": "BUY_PUT",
            "contracts": 1,
        }
    )
    assert captured["code"] == "510300P2612M03800"
    assert oid.startswith("HEDGE-LIVE-")
