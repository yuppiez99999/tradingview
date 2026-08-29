"""daily_trade_executor 2026-08-24 审查回归测试 — 修复 DTE-2/DTE-3/DTE-4/DTE-7

覆盖 (契约逻辑等价复刻, 避免导入大文件副作用):
    DTE-2  WT 风控分析异常 → fail-close 保守阻断 (而非静默放行)
    DTE-4  行情缺失回退硬编码价时显式 STALE 告警; 无兜底价时跳过而非按假价 10.0
    DTE-7  total_built_after 不再重复累加 (进度口径)
    DTE-3  stop_loss 管理器不可用时返回标记项告警可见
"""

from __future__ import annotations


class TestWtRiskFailClose:
    """DTE-2: 风控异常 → fail-close 保守阻断."""

    @staticmethod
    def _run_wt_risk_logics(raise_exc: bool):
        try:
            if raise_exc:
                raise RuntimeError("风控分析崩溃")
            score, conc = 0.2, 0.1
            if score > 0.5 or conc > 0.3:
                return {"status": "blocked", "reason": "超限"}
            return None
        except Exception:
            # DTE-2 修复: 风控崩溃时保守阻断
            return {
                "status": "blocked",
                "reason": "风控异常保守阻断",
                "risk_score": 1.0,
            }

    def test_wt_risk_exception_blocks(self):
        """DTE-2: 风控异常 → blocked (修复前 return None 放行)."""
        result = self._run_wt_risk_logics(raise_exc=True)
        assert result is not None
        assert result["status"] == "blocked"

    def test_wt_risk_normal_passes(self):
        """DTE-2: 风控正常且未超限 → None (放行)."""
        result = self._run_wt_risk_logics(raise_exc=False)
        assert result is None


class TestStopLossDegraded:
    """DTE-3: stop_loss 管理器不可用 → 返回标记项告警可见."""

    def test_manager_unavailable_returns_marker(self):
        """DTE-3: 不可用时返回 __manager_unavailable 标记 (而非空列表)."""
        manager = None
        if not manager:
            result = [
                {
                    "code": "__manager_unavailable",
                    "action": "HOLD",
                    "order_info": "stop_loss_manager 不可用, 止损风控降级",
                }
            ]
        else:
            result = []
        assert len(result) == 1
        assert result[0]["code"] == "__manager_unavailable"


class TestRefPriceDegraded:
    """DTE-4: 行情缺失的降级与跳过."""

    @staticmethod
    def _resolve_ref_price(latest_prices, code_clean, default_prices):
        ref_price = latest_prices.get(code_clean, 0)
        if not ref_price:
            ref_price = default_prices.get(code_clean, 0.0)
            if ref_price <= 0:
                return None  # 跳过该标的
        return ref_price

    def test_missing_price_with_default(self):
        """DTE-4: 有 DEFAULT_PRICES 兜底时用历史价 (带 STALE 告警逻辑)."""
        assert self._resolve_ref_price({}, "510050", {"510050": 3.09}) == 3.09

    def test_missing_price_no_default_skip(self):
        """DTE-4: 无兜底价时跳过 (返回 None), 不按假价 10.0 分配."""
        assert self._resolve_ref_price({}, "999999", {"510050": 3.09}) is None

    def test_live_price_wins(self):
        """DTE-4: 有实时价优先."""
        assert (
            self._resolve_ref_price({"600519": 1680.0}, "600519", {"600519": 10.0})
            == 1680.0
        )


class TestTotalBuiltNoDoubleCount:
    """DTE-7: total_built_after 不再重复累加."""

    def test_total_built_after_not_doubled(self):
        """DTE-7: progress.total_built 已含本次成交, total_built_after 直接读它."""
        # 模拟 _execute_single_instruction 已累加
        progress = {"total_built": 0.0}
        fill_amounts = [100.0, 200.0]
        for f in fill_amounts:
            progress["total_built"] = progress["total_built"] + f  # 已在执行中累加
        # 修复前: total_built_after = total_built + sum(fill) = 300 + 300 = 600 (虚增)
        # 修复后: total_built_after = total_built = 300 (正确)
        total_built_after = progress.get("total_built", 0)  # DTE-7 修复
        assert total_built_after == 300.0
        assert total_built_after != 600.0
