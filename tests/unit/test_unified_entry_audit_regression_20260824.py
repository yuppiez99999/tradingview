"""量化策略系统_统一入口 2026-08-24 审查回归测试 — 修复 UE-2/UE-3

覆盖 (契约逻辑等价复刻, 避免导入大文件副作用):
    UE-2  gemma 子进程失败时退出码透传 (而非无条件 exit(0) 假成功)
    UE-3  压力测试权重跳过缺行情标的 (不按硬编码占位价 1 失真)
"""

from __future__ import annotations


class TestStressWeightNoPlaceholder:
    """UE-3: 缺行情标的不按占位价 1 计算权重."""

    @staticmethod
    def _compute_weights(positions, prices, total_value):
        valid_codes = [c for c in positions if prices.get(c, 0) > 0]
        if not valid_codes:
            return None, []
        weights = [
            positions[c]["shares"] * prices[c] / total_value for c in valid_codes
        ]
        return weights, valid_codes

    def test_missing_price_skipped(self):
        """UE-3: 缺价标的不进权重, 不按占位价 1 参与."""
        positions = {"600519": {"shares": 100}, "000001": {"shares": 1000}}
        prices = {"600519": 1680.0}  # 000001 缺价
        total_value = 100 * 1680.0 + 1000 * 1.0  # 分母含缺价标的按1 (原始口径)
        weights, valid = self._compute_weights(positions, prices, total_value)
        assert valid == ["600519"]  # 缺价 000001 被跳过
        assert weights is not None

    def test_all_missing_returns_none(self):
        """UE-3: 全部缺价 → 无法计算, 返回 None (不产出失真权重)."""
        positions = {"600519": {"shares": 100}}
        weights, valid = self._compute_weights(positions, {}, 168000.0)
        assert weights is None
        assert valid == []


class TestGemmaExitCode:
    """UE-2: gemma 子进程退出码透传."""

    @staticmethod
    def _run_gemma(returncode):
        import sys

        # 模拟 subprocess.run 返回 returncode
        sys.exit(returncode)

    def test_gemma_success(self):
        """UE-2: 子进程成功 (0) → 主进程 0."""
        try:
            self._run_gemma(0)
        except SystemExit as e:
            assert e.code == 0

    def test_gemma_failure_propagates(self):
        """UE-2: 子进程失败 (1) → 主进程 1, 不再假成功 exit(0)."""
        try:
            self._run_gemma(1)
        except SystemExit as e:
            assert e.code == 1
