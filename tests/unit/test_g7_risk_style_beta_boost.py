"""style_beta 单测 (G17 风控因子)。"""
from __future__ import annotations

import pytest

from utils.risk.style_beta import DEFAULT_STYLE_BETA, STYLE_BETA_PROXY, get_style_beta


# ============================================================
# 1. STYLE_BETA_PROXY 字典
# ============================================================


class TestStyleBetaProxy:
    def test_known_style_科技(self):
        assert get_style_beta("科技") == 1.20

    def test_known_style_防御(self):
        assert get_style_beta("防御") == 0.60

    def test_unknown_style_default(self):
        assert get_style_beta("未知风格") == DEFAULT_STYLE_BETA

    def test_negative_beta_避险(self):
        assert get_style_beta("避险") == -0.10

    def test_proxy_keys_non_empty(self):
        assert len(STYLE_BETA_PROXY) > 0

    def test_proxy_values_finite(self):
        for beta in STYLE_BETA_PROXY.values():
            assert isinstance(beta, (int, float))


# ============================================================
# 2. get_style_beta
# ============================================================


class TestGetStyleBeta:
    def test_case_sensitive(self):
        assert get_style_beta("科技") == 1.20

    def test_whitespace_returns_default(self):
        assert get_style_beta("") == DEFAULT_STYLE_BETA

    def test_宽基(self):
        assert get_style_beta("宽基") == 0.95
