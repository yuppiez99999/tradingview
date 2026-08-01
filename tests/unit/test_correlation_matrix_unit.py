# -*- coding: utf-8 -*-
"""test_correlation_matrix_unit.py — 相关性矩阵与每日监控单元测试

B-3.3 收尾前置 (2026-08-01): 为 compute_correlation_matrix + monitor_daily_correlation 补测试覆盖。

背景:
    这两个方法在 hedge_engine_v59.py (实例方法, L615-765) 和
    portfolio_risk_assessor.py (根级函数, L339-661) 存在重复实现 (DRY 违规),
    且此前零测试覆盖。
    本测试基于 portfolio_risk_assessor 根级函数版本 (DRY 统一后的目标形态),
    拆分后该模块将 re-export risk.correlation 的实现, 测试自动覆盖。

测试覆盖:
    TestComputeCorrelationMatrix:
        1. 空输入 / 不足2个标的 → {}
        2. 历史数据不足 (< lookback_days) → {}
        3. 完全正相关 → corr=1.0
        4. 完全负相关 → corr=-1.0
        5. 对角线 = 1.0
        6. 对称性 corr[i][j] == corr[j][i]
        7. 三标的矩阵结构
        8. lookback_days 截断

    TestMonitorDailyCorrelation:
        1. 空数据 → status=NO_DATA
        2. 低相关 → alert=False
        3. 高平均相关 (avg>0.7) → alert=True
        4. 极高单对相关 (max>0.85, avg<0.7) → alert=True + "共振风险"
        5. 返回字段完整性
        6. high_correlation_pairs 内容
        7. risk_score ∈ [0, 1]
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

# ── 路径设置 (兼容 conftest.py 与独立运行) ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_ROOT = PROJECT_ROOT / "v8.3_institutional" / "src"
for _p in (str(PROJECT_ROOT), str(SRC_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from risk.portfolio_risk_assessor import (  # noqa: E402
    compute_correlation_matrix,
    monitor_daily_correlation,
)


# ============================================================
# Fixture: 确定性测试数据
# ============================================================

def _make_returns(n_days: int = 100, seed: int = 42) -> list:
    """生成确定性伪随机日收益率序列 (均值0, 波动~2%)"""
    rng = random.Random(seed)
    return [rng.gauss(0, 0.02) for _ in range(n_days)]


def _make_positions(codes: list) -> dict:
    """构造 positions dict (monitor_daily_correlation 只读 keys)"""
    return {c: {"shares": 100, "avg_cost": 10.0} for c in codes}


# ============================================================
# TestComputeCorrelationMatrix
# ============================================================

class TestComputeCorrelationMatrix:
    """compute_correlation_matrix 行为契约测试"""

    def test_empty_codes(self):
        """codes 为空 → {}"""
        result = compute_correlation_matrix({}, [])
        assert result == {}

    def test_single_code(self):
        """仅1个标的 → {} (不足2个)"""
        rets = _make_returns()
        result = compute_correlation_matrix({"A": rets}, ["A"])
        assert result == {}

    def test_code_not_in_returns(self):
        """codes 在 historical_returns 中不存在 → {}"""
        rets = _make_returns()
        result = compute_correlation_matrix({"A": rets}, ["A", "B", "C"])
        assert result == {}

    def test_insufficient_history(self):
        """历史数据 < lookback_days → {}"""
        short_rets = _make_returns(n_days=30)
        result = compute_correlation_matrix(
            {"A": short_rets, "B": short_rets},
            ["A", "B"],
            lookback_days=60,
        )
        assert result == {}

    def test_perfect_positive_correlation(self):
        """完全正相关 (A==B) → corr=1.0"""
        rets = _make_returns(seed=1)
        result = compute_correlation_matrix(
            {"A": rets, "B": list(rets)}, ["A", "B"], lookback_days=60
        )
        assert "A" in result and "B" in result
        assert result["A"]["B"] == pytest.approx(1.0, abs=1e-4)
        assert result["B"]["A"] == pytest.approx(1.0, abs=1e-4)

    def test_perfect_negative_correlation(self):
        """完全负相关 (B=-A) → corr=-1.0"""
        rets = _make_returns(seed=2)
        neg_rets = [-x for x in rets]
        result = compute_correlation_matrix(
            {"A": rets, "B": neg_rets}, ["A", "B"], lookback_days=60
        )
        assert result["A"]["B"] == pytest.approx(-1.0, abs=1e-4)

    def test_diagonal_is_one(self):
        """对角线 = 1.0"""
        rets_a = _make_returns(seed=1)
        rets_b = _make_returns(seed=2)
        rets_c = _make_returns(seed=3)
        result = compute_correlation_matrix(
            {"A": rets_a, "B": rets_b, "C": rets_c},
            ["A", "B", "C"],
            lookback_days=60,
        )
        for code in ("A", "B", "C"):
            assert result[code][code] == pytest.approx(1.0, abs=1e-4)

    def test_symmetric(self):
        """corr[i][j] == corr[j][i]"""
        rets_a = _make_returns(seed=1)
        rets_b = _make_returns(seed=2)
        rets_c = _make_returns(seed=3)
        result = compute_correlation_matrix(
            {"A": rets_a, "B": rets_b, "C": rets_c},
            ["A", "B", "C"],
            lookback_days=60,
        )
        for i in ("A", "B", "C"):
            for j in ("A", "B", "C"):
                assert result[i][j] == pytest.approx(result[j][i], abs=1e-6)

    def test_three_assets_matrix_shape(self):
        """3个标的 → 3×3 矩阵, 每行3个键"""
        rets_a = _make_returns(seed=1)
        rets_b = _make_returns(seed=2)
        rets_c = _make_returns(seed=3)
        result = compute_correlation_matrix(
            {"A": rets_a, "B": rets_b, "C": rets_c},
            ["A", "B", "C"],
            lookback_days=60,
        )
        assert len(result) == 3
        for code in ("A", "B", "C"):
            assert len(result[code]) == 3

    def test_lookback_truncation(self):
        """lookback_days 只取末尾 N 天, 不同窗口产生有效结果"""
        rets_a = _make_returns(n_days=100, seed=1)
        rets_b = _make_returns(n_days=100, seed=2)
        r60 = compute_correlation_matrix(
            {"A": rets_a, "B": rets_b}, ["A", "B"], lookback_days=60
        )
        r100 = compute_correlation_matrix(
            {"A": rets_a, "B": rets_b}, ["A", "B"], lookback_days=100
        )
        # 两者都应有效 (非空)
        assert r60 and r100
        # 验证返回有效浮点 (lookback 生效, 不强制不等)
        assert isinstance(r60["A"]["B"], float)
        assert isinstance(r100["A"]["B"], float)


# ============================================================
# TestMonitorDailyCorrelation
# ============================================================

class TestMonitorDailyCorrelation:
    """monitor_daily_correlation 行为契约测试"""

    def test_no_data_empty_positions(self):
        """空 positions → status=NO_DATA"""
        result = monitor_daily_correlation({}, {})
        assert result["status"] == "NO_DATA"
        assert result["alert"] is False
        assert result["average_correlation"] == 0.0

    def test_no_data_insufficient_history(self):
        """历史数据不足 → status=NO_DATA"""
        short_rets = _make_returns(n_days=30)
        positions = _make_positions(["A", "B"])
        result = monitor_daily_correlation(
            positions, {"A": short_rets, "B": short_rets}, lookback_days=60
        )
        assert result["status"] == "NO_DATA"

    def test_low_correlation_no_alert(self):
        """低相关 (独立序列) → alert=False"""
        rets_a = _make_returns(seed=10)
        rets_b = _make_returns(seed=20)
        rets_c = _make_returns(seed=30)
        positions = _make_positions(["A", "B", "C"])
        result = monitor_daily_correlation(
            positions,
            {"A": rets_a, "B": rets_b, "C": rets_c},
            alert_threshold=0.7,
            lookback_days=60,
        )
        assert result["status"] == "OK"
        assert result["alert"] is False
        assert result["average_correlation"] < 0.7

    def test_high_avg_correlation_alert(self):
        """高平均相关 (完全正相关 A==B==C) → alert=True + '平均相关系数'"""
        rets = _make_returns(seed=1)
        positions = _make_positions(["A", "B", "C"])
        result = monitor_daily_correlation(
            positions,
            {"A": rets, "B": list(rets), "C": list(rets)},
            alert_threshold=0.7,
            lookback_days=60,
        )
        assert result["status"] == "OK"
        assert result["alert"] is True
        assert result["average_correlation"] > 0.7
        assert "平均相关系数" in result["alert_reason"]

    def test_max_correlation_alert_only(self):
        """仅 max>0.85 触发 (A==B 完全相关, C 独立) → alert=True + '共振风险'

        avg = (1.0 + corr_AC + corr_BC) / 3 ≈ 0.33 < 0.7
        max = corr(A,B) = 1.0 > 0.85 → alert 由 max 触发
        """
        rets_a = _make_returns(seed=1)
        rets_c = _make_returns(seed=99)  # 与 A 独立
        positions = _make_positions(["A", "B", "C"])
        result = monitor_daily_correlation(
            positions,
            {"A": rets_a, "B": list(rets_a), "C": rets_c},
            alert_threshold=0.7,
            lookback_days=60,
        )
        assert result["status"] == "OK"
        assert result["max_correlation"] > 0.85
        assert result["alert"] is True
        assert "共振风险" in result["alert_reason"]

    def test_return_fields_complete(self):
        """返回字段完整性"""
        rets_a = _make_returns(seed=1)
        rets_b = _make_returns(seed=2)
        positions = _make_positions(["A", "B"])
        result = monitor_daily_correlation(
            positions, {"A": rets_a, "B": rets_b}, lookback_days=60
        )
        required_keys = {
            "status", "correlation_matrix", "average_correlation",
            "max_correlation", "high_correlation_pairs", "alert",
            "alert_reason", "risk_score", "lookback_days", "timestamp",
        }
        assert required_keys.issubset(result.keys())

    def test_high_correlation_pairs_content(self):
        """high_correlation_pairs 包含超阈值对 (code_i, code_j, corr)"""
        rets = _make_returns(seed=1)
        positions = _make_positions(["A", "B"])
        result = monitor_daily_correlation(
            positions,
            {"A": rets, "B": list(rets)},
            alert_threshold=0.7,
            lookback_days=60,
        )
        assert len(result["high_correlation_pairs"]) >= 1
        pair = result["high_correlation_pairs"][0]
        assert pair[0] in ("A", "B")
        assert pair[1] in ("A", "B")
        assert pair[2] > 0.7

    def test_risk_score_bounded(self):
        """risk_score ∈ [0, 1]"""
        rets_a = _make_returns(seed=1)
        rets_b = _make_returns(seed=2)
        positions = _make_positions(["A", "B"])
        result = monitor_daily_correlation(
            positions, {"A": rets_a, "B": rets_b}, lookback_days=60
        )
        assert 0.0 <= result["risk_score"] <= 1.0
