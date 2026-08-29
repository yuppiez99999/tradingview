"""utils/risk/style_beta.py 覆盖率补测 (W7.4.5 覆盖率冲刺)

验证目标:
    1. STYLE_BETA_PROXY 字典完整性 (13 风格)
    2. get_style_beta 已知风格返回正确 beta
    3. get_style_beta 未知风格返回 DEFAULT_STYLE_BETA (1.0)
    4. DEFAULT_STYLE_BETA 常量值正确
    5. __all__ 导出完整性
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from utils.risk.style_beta import (  # noqa: E402
    DEFAULT_STYLE_BETA,
    STYLE_BETA_PROXY,
    get_style_beta,
)


# ============================================================
# STYLE_BETA_PROXY 字典完整性测试
# ============================================================
class TestStyleBetaProxyDict:
    """STYLE_BETA_PROXY 风格 Beta 代理字典测试."""

    def test_dict_has_13_styles(self):
        """字典包含 13 个风格标签."""
        assert len(STYLE_BETA_PROXY) == 13

    def test_dict_contains_key_styles(self):
        """字典包含关键风格标签."""
        expected_keys = {
            "宽基",
            "高端制造",
            "科技",
            "制造",
            "新能源",
            "医药",
            "化工",
            "银行",
            "防御",
            "顺周期",
            "避险",
            "红利",
            "成长",
        }
        assert expected_keys.issubset(STYLE_BETA_PROXY.keys())

    def test_all_values_are_float(self):
        """所有 beta 值都是 float 类型."""
        for style, beta in STYLE_BETA_PROXY.items():
            assert isinstance(beta, float), f"{style} 的 beta 不是 float: {type(beta)}"

    def test_specific_beta_values(self):
        """关键风格的 beta 值正确."""
        assert STYLE_BETA_PROXY["科技"] == 1.20
        assert STYLE_BETA_PROXY["防御"] == 0.60
        assert STYLE_BETA_PROXY["避险"] == -0.10
        assert STYLE_BETA_PROXY["成长"] == 1.25
        assert STYLE_BETA_PROXY["银行"] == 0.75


# ============================================================
# get_style_beta 函数测试
# ============================================================
class TestGetStyleBeta:
    """get_style_beta 查询函数测试."""

    def test_known_style_returns_correct_beta(self):
        """已知风格返回正确的 beta 值."""
        assert get_style_beta("科技") == 1.20
        assert get_style_beta("防御") == 0.60
        assert get_style_beta("宽基") == 0.95

    def test_unknown_style_returns_default(self):
        """未知风格返回 DEFAULT_STYLE_BETA (1.0)."""
        assert get_style_beta("未知风格") == DEFAULT_STYLE_BETA
        assert get_style_beta("不存在的风格") == 1.0

    def test_empty_string_returns_default(self):
        """空字符串返回默认值."""
        assert get_style_beta("") == DEFAULT_STYLE_BETA

    def test_all_known_styles_match_dict(self):
        """所有字典中的风格通过函数查询结果一致."""
        for style, expected_beta in STYLE_BETA_PROXY.items():
            assert get_style_beta(style) == expected_beta, f"{style} 不一致"

    def test_negative_beta_style(self):
        """避险风格返回负 beta (-0.10)."""
        assert get_style_beta("避险") == -0.10


# ============================================================
# DEFAULT_STYLE_BETA 常量测试
# ============================================================
class TestDefaultStyleBeta:
    """DEFAULT_STYLE_BETA 常量测试."""

    def test_default_value_is_1(self):
        """默认 beta 值为 1.0."""
        assert DEFAULT_STYLE_BETA == 1.0

    def test_default_is_float(self):
        """默认 beta 是 float 类型."""
        assert isinstance(DEFAULT_STYLE_BETA, float)


# ============================================================
# __all__ 导出完整性测试
# ============================================================
class TestAllExport:
    """__all__ 导出列表测试."""

    def test_all_contains_three_names(self):
        """__all__ 包含 3 个导出名称."""
        from utils.risk import style_beta

        assert len(style_beta.__all__) == 3

    def test_all_contains_expected_names(self):
        """__all__ 包含预期的导出名称."""
        from utils.risk import style_beta

        expected = {"STYLE_BETA_PROXY", "DEFAULT_STYLE_BETA", "get_style_beta"}
        assert set(style_beta.__all__) == expected
