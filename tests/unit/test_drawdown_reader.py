# -*- coding: utf-8 -*-
"""
DrawdownReader 单元测试
========================

测试从 shadow_state.json 读取并计算当前组合回撤.

测试用例:
    - test_get_current_drawdown_normal: 正常读取, 返回正数回撤
    - test_get_current_drawdown_zero: 当前值等于峰值, 返回 0
    - test_get_current_drawdown_no_peak: 数据为空时返回 None
    - test_get_current_drawdown_file_missing: 文件不存在时返回 None
    - test_get_current_drawdown_invalid_json: JSON 格式错误时返回 None
    - test_get_peak_and_current: 返回 (peak, current) 元组
    - test_get_drawdown_details: 返回完整详情字典
    - test_custom_state_path: 自定义路径参数
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.alpha.drawdown_reader import DrawdownReader


# ============================================================
# 测试 fixtures
# ============================================================
@pytest.fixture
def normal_state_file(tmp_path) -> Path:
    """正常 shadow_state.json (有回撤)."""
    state_data = {
        "current_nav": 0.996477,
        "daily_nav": [
            {"date": "2026-07-27", "nav": 1.003251, "daily_return": 0.003251},
            {"date": "2026-07-28", "nav": 0.997283, "daily_return": -0.005949},
            {"date": "2026-07-29", "nav": 0.997897, "daily_return": 0.000616},
            {"date": "2026-07-30", "nav": 0.996218, "daily_return": -0.001683},
            {"date": "2026-07-31", "nav": 1.003485, "daily_return": 0.007295},  # peak
            {"date": "2026-08-03", "nav": 0.997079, "daily_return": -0.006384},
            {"date": "2026-08-04", "nav": 0.996477, "daily_return": -0.000603},  # current
        ],
    }
    state_path = tmp_path / "shadow_state.json"
    state_path.write_text(json.dumps(state_data, ensure_ascii=False), encoding="utf-8")
    return state_path


@pytest.fixture
def zero_drawdown_state_file(tmp_path) -> Path:
    """无回撤的 shadow_state.json (当前值 = 峰值)."""
    state_data = {
        "current_nav": 1.005,
        "daily_nav": [
            {"date": "2026-07-27", "nav": 1.0, "daily_return": 0.0},
            {"date": "2026-07-28", "nav": 1.002, "daily_return": 0.002},
            {"date": "2026-07-29", "nav": 1.005, "daily_return": 0.003},  # peak = current
        ],
    }
    state_path = tmp_path / "shadow_state.json"
    state_path.write_text(json.dumps(state_data, ensure_ascii=False), encoding="utf-8")
    return state_path


# ============================================================
# 测试用例: get_current_drawdown
# ============================================================
class TestGetCurrentDrawdown:
    """测试 get_current_drawdown 方法."""

    def test_get_current_drawdown_normal(self, normal_state_file):
        """正常读取, 返回正数回撤."""
        reader = DrawdownReader(state_path=normal_state_file)
        dd = reader.get_current_drawdown()

        assert dd is not None
        assert dd > 0
        # peak=1.003485, current=0.996477
        # drawdown = (1.003485 - 0.996477) / 1.003485 ≈ 0.006986
        assert 0.005 < dd < 0.01

    def test_get_current_drawdown_zero(self, zero_drawdown_state_file):
        """当前值等于峰值, 返回 0."""
        reader = DrawdownReader(state_path=zero_drawdown_state_file)
        dd = reader.get_current_drawdown()

        assert dd is not None
        assert dd == 0.0

    def test_get_current_drawdown_no_peak(self, tmp_path):
        """daily_nav 为空时返回 None."""
        state_data = {"current_nav": 1.0, "daily_nav": []}
        state_path = tmp_path / "shadow_state.json"
        state_path.write_text(json.dumps(state_data), encoding="utf-8")

        reader = DrawdownReader(state_path=state_path)
        dd = reader.get_current_drawdown()

        # 备选路径: 从 current_nav 读取, peak=current, drawdown=0
        assert dd is not None
        assert dd == 0.0

    def test_get_current_drawdown_file_missing(self, tmp_path):
        """文件不存在时返回 None."""
        reader = DrawdownReader(state_path=tmp_path / "nonexistent.json")
        dd = reader.get_current_drawdown()

        assert dd is None

    def test_get_current_drawdown_invalid_json(self, tmp_path):
        """JSON 格式错误时返回 None."""
        state_path = tmp_path / "shadow_state.json"
        state_path.write_text("invalid json content", encoding="utf-8")

        reader = DrawdownReader(state_path=state_path)
        dd = reader.get_current_drawdown()

        assert dd is None

    def test_get_current_drawdown_all_nav_none(self, tmp_path):
        """所有 nav 值为 None 时, 备选从 current_nav 读取."""
        state_data = {
            "current_nav": 0.95,
            "daily_nav": [
                {"date": "2026-07-27", "nav": None, "daily_return": 0.0},
                {"date": "2026-07-28", "nav": None, "daily_return": 0.0},
            ],
        }
        state_path = tmp_path / "shadow_state.json"
        state_path.write_text(json.dumps(state_data), encoding="utf-8")

        reader = DrawdownReader(state_path=state_path)
        dd = reader.get_current_drawdown()

        # 无历史 nav 时 peak=current, drawdown=0
        assert dd is not None
        assert dd == 0.0


# ============================================================
# 测试用例: get_peak_and_current
# ============================================================
class TestGetPeakAndCurrent:
    """测试 get_peak_and_current 方法."""

    def test_get_peak_and_current_normal(self, normal_state_file):
        """返回 (peak, current) 元组."""
        reader = DrawdownReader(state_path=normal_state_file)
        result = reader.get_peak_and_current()

        assert result is not None
        peak, current = result
        assert peak == pytest.approx(1.003485, rel=1e-5)
        assert current == pytest.approx(0.996477, rel=1e-5)
        assert peak > current

    def test_get_peak_and_current_file_missing(self, tmp_path):
        """文件不存在时返回 None."""
        reader = DrawdownReader(state_path=tmp_path / "nonexistent.json")
        result = reader.get_peak_and_current()

        assert result is None


# ============================================================
# 测试用例: get_drawdown_details
# ============================================================
class TestGetDrawdownDetails:
    """测试 get_drawdown_details 方法."""

    def test_get_drawdown_details_normal(self, normal_state_file):
        """返回完整详情字典."""
        reader = DrawdownReader(state_path=normal_state_file)
        details = reader.get_drawdown_details()

        assert details is not None
        assert "peak_nav" in details
        assert "current_nav" in details
        assert "drawdown_pct" in details
        assert "peak_date" in details
        assert "current_date" in details
        assert "total_days" in details

        assert details["peak_nav"] == pytest.approx(1.003485, rel=1e-5)
        assert details["current_nav"] == pytest.approx(0.996477, rel=1e-5)
        assert details["drawdown_pct"] > 0
        assert details["peak_date"] == "2026-07-31"
        assert details["current_date"] == "2026-08-04"
        assert details["total_days"] == 7

    def test_get_drawdown_details_file_missing(self, tmp_path):
        """文件不存在时返回 None."""
        reader = DrawdownReader(state_path=tmp_path / "nonexistent.json")
        details = reader.get_drawdown_details()

        assert details is None


# ============================================================
# 测试用例: 自定义路径
# ============================================================
class TestCustomPath:
    """测试自定义 state_path 参数."""

    def test_custom_state_path(self, normal_state_file):
        """使用自定义路径正常读取."""
        reader = DrawdownReader(state_path=normal_state_file)
        dd = reader.get_current_drawdown()
        assert dd is not None
        assert dd > 0
