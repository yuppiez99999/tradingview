"""N1 smoke test: _default_option_multiplier 按 instrument 前缀返回正确乘数.

验证: N1 修复 — hedge_order_executor.py ETF 期权 10000, 股指期权 100
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from hedge_order_executor import _default_option_multiplier  # noqa: E402


def test_etf_option_multiplier_default():
    assert _default_option_multiplier("510050 Put") == 10000
    assert _default_option_multiplier("159915 Call") == 10000
    assert _default_option_multiplier("") == 10000
    assert _default_option_multiplier("510300") == 10000


def test_index_option_multiplier():
    assert _default_option_multiplier("IO2401-C-3800") == 100
    assert _default_option_multiplier("MO2401-P-3200") == 100
    assert _default_option_multiplier("HO2401-C-2800") == 100
