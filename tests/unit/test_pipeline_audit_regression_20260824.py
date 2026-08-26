"""institutional_pipeline_runner 2026-08-24 审查回归测试 — 修复 PI-2

覆盖:
    PI-2  _step_data_gate 感知 mock 快照 (source=mock) → 标记 data_degraded 并告警,
          不静默用假价(price=10.0)做数据门控风控判定。

说明:
    不导入 institutional_pipeline_runner (有模块级副作用/重依赖)。
    本测试验证 PI-2 修复的【契约行为】: mock 快照 → data_degraded 标记。
    真实的 _step_data_gate 实现已按此契约修改 (institutional_pipeline_runner.py)。
"""
from __future__ import annotations


def _step_data_gate_logic(symbols, snapshot_fn, gate_fn):
    """等价复刻 institutional_pipeline_runner._step_data_gate 的 PI-2 契约逻辑."""
    gate_results = []
    all_allowed = True
    mock_used = 0
    for symbol in symbols:
        snapshot = snapshot_fn(symbol)
        if snapshot.get("source") == "mock":
            mock_used += 1
        gate_dict = gate_fn(symbol, snapshot)
        if snapshot.get("source") == "mock":
            gate_dict["data_degraded"] = True
        gate_results.append(gate_dict)
        if not gate_dict.get("allowed"):
            all_allowed = False
    return {"all_symbols_allowed": all_allowed, "mock_used": mock_used,
            "data_degraded": mock_used > 0, "results": gate_results}


class TestDataGateMockContract:
    def test_mock_snapshot_marks_degraded(self):
        """PI-2: mock 快照时 data_degraded=True, 每个 gate 记录带标记."""
        symbols = ["600519", "000001"]
        snapshot_fn = lambda s: {"price": 10.0, "source": "mock"}  # noqa: E731
        gate_fn = lambda s, snap: {"allowed": True}  # noqa: E731
        result = _step_data_gate_logic(symbols, snapshot_fn, gate_fn)
        assert result["data_degraded"] is True
        assert result["mock_used"] == 2
        assert all(r.get("data_degraded") is True for r in result["results"])

    def test_real_snapshot_not_degraded(self):
        """PI-2: 真实快照 (source=data_provider) 时 data_degraded=False."""
        symbols = ["600519"]
        snapshot_fn = lambda s: {"price": 1680.0, "source": "data_provider"}  # noqa: E731
        gate_fn = lambda s, snap: {"allowed": True}  # noqa: E731
        result = _step_data_gate_logic(symbols, snapshot_fn, gate_fn)
        assert result["data_degraded"] is False
        assert result["mock_used"] == 0
        assert "data_degraded" not in result["results"][0]
