"""ShadowFillsIntegrator 单元测试 (P0-1).

覆盖:
    T1 真实撮合桥接: trade_log / nav_by_fills / data_source_real 写入双状态
    T2 无成交: data_source_real=False, 不写 trade_log
    T3 幂等: 重复运行不重复追加
    T4 nav_by_fills 净值序列正确累乘
    T5 异常 fail-open: 数据源抛异常时 success=False 不崩溃
    T6 SELL/BUY 方向影响日收益符号
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from utils.alpha.shadow_fills_integrator import (
    ShadowFillsIntegrator,
    _compute_nav_by_fills,
    _fill_to_trade_log_entry,
)


def _make_fill(symbol, side, qty, price, d="2026-08-27", **extra):
    f = {
        "ts": f"{d}T08:30:00",
        "date": d,
        "symbol": symbol,
        "side": side,
        "filled_qty": qty,
        "avg_price": price,
        "broker": "SimulatedBroker",
        "is_live": False,
        "strategy": "build",
        "source": "sim_route",
        "meta": {"commission": 1.0, "transfer_fee": 0.1, "stamp_duty": 0.0},
    }
    f.update(extra)
    return f


class _Harness:
    """注入内存态 FillsStore + 临时状态文件路径."""

    def __init__(self, fills=None):
        self._fills = fills or []
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.shadow = self.root / "shadow_state.json"
        self.admission = self.root / "admission_state.json"
        self.launch = self.root / "launch_state.json"

    def integrator(self, fills=None):
        src = self._fills if fills is None else fills
        return ShadowFillsIntegrator(
            project_root=self.root,
            shadow_state_path=self.shadow,
            admission_state_path=self.admission,
            launch_state_path=self.launch,
            fills_source=lambda td: src,
        )

    def read_shadow(self):
        if self.shadow.exists():
            return json.loads(self.shadow.read_text(encoding="utf-8"))
        return {}

    def read_admission(self):
        if self.admission.exists():
            return json.loads(self.admission.read_text(encoding="utf-8"))
        return {}

    def read_launch(self):
        if self.launch.exists():
            return json.loads(self.launch.read_text(encoding="utf-8"))
        return {}

    def close(self):
        self.tmp.cleanup()


class TestShadowFillsIntegrator(unittest.TestCase):
    def setUp(self):
        self.h = _Harness()

    def tearDown(self):
        self.h.close()

    # T1 真实撮合桥接
    def test_t1_real_fills_bridge(self):
        fills = [_make_fill("600519.SH", "BUY", 100, 1800.0)]
        res = self.h.integrator(fills).integrate()
        self.assertTrue(res.success)
        self.assertEqual(res.fills_consumed, 1)
        self.assertTrue(res.data_source_real)
        self.assertEqual(res.trade_log_len, 1)
        self.assertTrue(res.shadow_state_written)
        self.assertTrue(res.admission_state_written)

        sh = self.h.read_shadow()
        self.assertEqual(len(sh["trade_log"]), 1)
        self.assertEqual(sh["trade_log"][0]["symbol"], "600519.SH")
        self.assertEqual(sh["trade_log"][0]["commission"], 1.0)
        self.assertTrue(sh["data_source_real"])
        self.assertGreaterEqual(len(sh["nav_by_fills"]), 1)

        ad = self.h.read_admission()
        self.assertEqual(len(ad["trade_log"]), 1)
        self.assertTrue(ad["data_source_real"])

        # 三态同步: launch 状态 (advance_stage 守卫读取来源) 也写入
        ln = self.h.read_launch()
        self.assertEqual(len(ln["trade_log"]), 1)
        self.assertTrue(ln["data_source_real"])

    # T2 无成交
    def test_t2_no_fills(self):
        res = self.h.integrator([]).integrate()
        self.assertTrue(res.success)
        self.assertFalse(res.data_source_real)
        self.assertEqual(res.trade_log_len, 0)
        sh = self.h.read_shadow()
        self.assertFalse(sh.get("data_source_real", True))
        self.assertEqual(sh.get("trade_log", []), [])

    # T3 幂等
    def test_t3_idempotent(self):
        fills = [_make_fill("600519.SH", "BUY", 100, 1800.0)]
        it = self.h.integrator(fills)
        r1 = it.integrate()
        r2 = it.integrate()
        self.assertEqual(r1.new_fills, 1)
        self.assertEqual(r2.new_fills, 0)  # 已桥接日期跳过
        sh = self.h.read_shadow()
        self.assertEqual(len(sh["trade_log"]), 1)  # 不重复

    # T4 nav_by_fills 累乘
    def test_t4_nav_series(self):
        fills = [
            _make_fill("A", "BUY", 100, 10.0, d="2026-08-25"),
            _make_fill("A", "SELL", 100, 11.0, d="2026-08-26"),
        ]
        series = _compute_nav_by_fills(fills)
        self.assertEqual(len(series), 2)
        self.assertEqual(series[0]["date"], "2026-08-25")
        self.assertEqual(series[1]["date"], "2026-08-26")
        # 最终 nav 应大于 1.0 (SELL 高于 BUY, 净收益为正)
        self.assertGreater(series[-1]["nav"], 1.0)
        # nav 始终 > 0 (不会归零)
        for pt in series:
            self.assertGreater(pt["nav"], 0.0)
        # 单调累乘关系成立
        self.assertAlmostEqual(
            series[-1]["nav"],
            series[0]["nav"] * (1.0 + series[1]["daily_return"]),
        )

    # T5 异常 fail-open
    def test_t5_exception_fail_open(self):
        def boom(td):
            raise RuntimeError("FillsStore down")

        it = ShadowFillsIntegrator(
            project_root=self.h.root,
            shadow_state_path=self.h.shadow,
            admission_state_path=self.h.admission,
            fills_source=boom,
        )
        res = it.integrate()
        self.assertFalse(res.success)
        self.assertIsNotNone(res.error)
        self.assertIn("FillsStore down", res.error)

    # T6 方向符号
    def test_t6_side_sign(self):
        buy = _compute_nav_by_fills([_make_fill("A", "BUY", 100, 10.0, d="2026-08-25")])
        sell = _compute_nav_by_fills(
            [_make_fill("A", "SELL", 100, 10.0, d="2026-08-25")]
        )
        # BUY 净流出 -> 日收益为负代理; SELL 净流入 -> 正
        self.assertLess(buy[0]["daily_return"], 0.0)
        self.assertGreater(sell[0]["daily_return"], 0.0)

    # 规范化字段
    def test_fill_to_trade_log_entry(self):
        f = _make_fill("600519.SH", "BUY", 100, 1800.0)
        e = _fill_to_trade_log_entry(f)
        for k in ("ts", "symbol", "side", "filled_qty", "avg_price"):
            self.assertIn(k, e)
        self.assertEqual(e["commission"], 1.0)
        self.assertEqual(e["date"], "2026-08-27")


if __name__ == "__main__":
    unittest.main(verbosity=2)
