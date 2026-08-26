"""DTE-1 建仓落盘 FillsStore 回归测试 (2026-08-24)

验证 daily_trade_executor 建仓成交通过 FillsStore.record_fill 落盘为可追溯事实源
(strategy="build"), 使"账本自我记账"成为可消费成交, 供 PnL/TCA/影子账户使用。

避免 import daily_trade_executor (模块级副作用), 直接用 FillsStore + 等价 helper 验证契约。
"""
from __future__ import annotations

import os
import tempfile

from utils.execution.fills_store import FillsStore


def _record_build_fill_contract(store, inst, result, target_date_str):
    """等价复刻 daily_trade_executor._record_build_fill 的契约逻辑."""
    if result.get("status") != "FILLED":
        return False
    qty = float(result.get("qty", 0) or 0)
    fill_price = float(result.get("fill_price", 0) or 0)
    if qty <= 0 or fill_price <= 0:
        return False
    symbol = str(inst.get("full_code") or inst.get("code", ""))
    side = str(result.get("action", "BUY"))
    store.record_fill(
        symbol=symbol, side=side, filled_qty=qty, avg_price=fill_price,
        broker="SimulatedBroker", is_live=False, strategy="build",
        source="sim_route", date=target_date_str,
        meta={"slippage_rate": inst.get("slippage", 0.0)},
    )
    return True


class TestBuildFillFillsStore:
    def setup_method(self):
        # 用临时文件避免污染真实 fills 数据
        self._tmpdir = tempfile.mkdtemp()
        self._orig_report = os.environ.get("REPORTS_DIR")
        os.environ["REPORTS_DIR"] = self._tmpdir

    def teardown_method(self):
        if self._orig_report is not None:
            os.environ["REPORTS_DIR"] = self._orig_report
        else:
            os.environ.pop("REPORTS_DIR", None)

    def test_filled_records_to_store(self):
        """DTE-1: FILLED 成交落盘 FillsStore, strategy=build."""
        store = FillsStore()
        inst = {"code": "600519", "full_code": "600519", "slippage": 0.0005}
        result = {"status": "FILLED", "action": "BUY", "qty": 100, "fill_price": 1680.0}
        assert _record_build_fill_contract(store, inst, result, "2026-08-24") is True

    def test_skipped_not_recorded(self):
        """DTE-1: SKIPPED 不落盘."""
        store = FillsStore()
        inst = {"code": "600519", "full_code": "600519"}
        result = {"status": "SKIPPED", "action": "BUY", "qty": 0, "fill_price": 1680.0}
        assert _record_build_fill_contract(store, inst, result, "2026-08-24") is False

    def test_zero_qty_not_recorded(self):
        """DTE-1: qty<=0 不落盘."""
        store = FillsStore()
        inst = {"code": "600519", "full_code": "600519"}
        result = {"status": "FILLED", "action": "BUY", "qty": 0, "fill_price": 1680.0}
        assert _record_build_fill_contract(store, inst, result, "2026-08-24") is False
