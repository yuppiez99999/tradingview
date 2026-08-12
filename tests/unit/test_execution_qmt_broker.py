"""ms_strategy.src.execution.qmt_broker 单元测试 — xtquant 未安装时 QmtBrokerAPI 降级抛 RuntimeError"""
from __future__ import annotations

import pytest

from ms_strategy.src.execution import qmt_broker


def test_xtquant_unavailable_flag():
    """当前环境 xtquant 未安装 → XTQUANT_AVAILABLE == False"""
    assert qmt_broker.XTQUANT_AVAILABLE is False


def test_qmt_broker_construction_raises_when_no_xtquant():
    """xtquant 缺失时构造 QmtBrokerAPI 直接抛 RuntimeError (不裸实盘)"""
    with pytest.raises(RuntimeError, match="xtquant"):
        qmt_broker.QmtBrokerAPI(account_id="800123456", session_id=123456)


def test_qmt_broker_constants_present():
    """QMT 订单状态常量映射存在且含已成/已撤状态"""
    assert 55 in qmt_broker.QMT_ORDER_STATUS     # ALL_TRADED
    assert 54 in qmt_broker.QMT_ORDER_STATUS     # CANCELLED
    assert 48 in qmt_broker.QMT_ORDER_STATUS     # NOT_REPORTED


def test_qmt_broker_account_type_constants():
    """账户类型常量定义正确"""
    assert qmt_broker.QmtBrokerAPI.ACCOUNT_TYPE_STOCK == "STOCK"
    assert qmt_broker.QmtBrokerAPI.ACCOUNT_TYPE_FUTURE == "FUTURE"
    assert qmt_broker.QmtBrokerAPI.ACCOUNT_TYPE_CREDIT == "CREDIT"
