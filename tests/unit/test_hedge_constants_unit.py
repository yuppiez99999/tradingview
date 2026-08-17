"""test_hedge_constants_unit.py — 对冲共享常量单元测试"""
from __future__ import annotations

import pytest

from utils.hedge_constants import DEFENSE_ASSETS


class TestDefenseAssets:
    @pytest.mark.unit
    def test_has_three_assets(self):
        assert len(DEFENSE_ASSETS) == 3

    @pytest.mark.unit
    def test_changjiang_power(self):
        name, amount = DEFENSE_ASSETS["sh600900"]
        assert name == "长江电力"
        assert amount == 58_800

    @pytest.mark.unit
    def test_gold_etf(self):
        name, amount = DEFENSE_ASSETS["sz518880"]
        assert name == "黄金ETF华安"
        assert amount == 99_450

    @pytest.mark.unit
    def test_china_shenhua(self):
        name, amount = DEFENSE_ASSETS["sh601088"]
        assert name == "中国神华"
        assert amount == 38_500

    @pytest.mark.unit
    def test_all_amounts_positive(self):
        for code, (_, amount) in DEFENSE_ASSETS.items():
            assert amount > 0