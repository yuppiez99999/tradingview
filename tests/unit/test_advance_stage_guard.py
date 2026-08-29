"""advance_stage 推进守卫单元测试 (P0-1).

覆盖:
    G1 开关关闭: 直接放行 (灰度兼容)
    G2 trade_log 为空: 守卫拦截 (暂缓推进)
    G3 trade_log 非空 + 绩效达标: 放行
    G4 trade_log 非空 + 年化未达标: 拦截
    G5 无 returns 文件: 放行 (防御性)
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import launch_shadow_account as ls


class TestAdvanceGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.returns = self.root / "daily_returns.jsonl"
        # 备份开关
        self._orig_guard = ls.ENABLE_ADVANCE_TRADE_LOG_GUARD

    def tearDown(self):
        ls.ENABLE_ADVANCE_TRADE_LOG_GUARD = self._orig_guard
        self.tmp.cleanup()

    def _write_returns(self, rets):
        with open(self.returns, "w", encoding="utf-8") as f:
            for r in rets:
                f.write(json.dumps({"daily_return": r}) + "\n")

    def test_g1_guard_disabled(self):
        ls.ENABLE_ADVANCE_TRADE_LOG_GUARD = False
        state = {"trade_log": []}
        self.assertTrue(ls._enforce_advance_guards(state))

    def test_g2_empty_trade_log_blocks(self):
        ls.ENABLE_ADVANCE_TRADE_LOG_GUARD = True
        state = {"trade_log": []}
        self.assertFalse(ls._enforce_advance_guards(state))

    def test_g3_trade_log_nonempty_pass(self):
        ls.ENABLE_ADVANCE_TRADE_LOG_GUARD = True
        # 年化 ~ 用 252 天每日 0.05% -> 远超 8%
        self._write_returns([0.0005] * 252)
        state = {"trade_log": [{"symbol": "A", "side": "BUY"}]}
        self.assertTrue(ls._enforce_advance_guards(state, returns_file=self.returns))

    def test_g4_underperform_blocks(self):
        ls.ENABLE_ADVANCE_TRADE_LOG_GUARD = True
        # 年化远低于 8%
        self._write_returns([-0.0005] * 252)
        state = {"trade_log": [{"symbol": "A", "side": "BUY"}]}
        self.assertFalse(ls._enforce_advance_guards(state, returns_file=self.returns))

    def test_g5_no_returns_file_pass(self):
        ls.ENABLE_ADVANCE_TRADE_LOG_GUARD = True
        state = {"trade_log": [{"symbol": "A", "side": "BUY"}]}
        # 不存在的 returns 文件 -> 防御性放行
        self.assertTrue(
            ls._enforce_advance_guards(state, returns_file=self.root / "nope.jsonl")
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
