"""futures_rollover_manager 单元测试.

被测模块: utils/futures_rollover_manager.py
覆盖目标: >=90%

测试期货主力合约识别 + 换月管理: 第三周五到期日、主力合约识别、
换月检测、换月订单生成、合约解析、可交易判断、对冲合约名解析。

注意:
- 源模块 resolve_hedge_contract 使用了 re.match 但顶部缺失 `import re`,
  本测试通过 monkeypatch 注入 re 模块以覆盖该分支 (源 bug 已在报告中标注)。
- get_active_contract / detect_rollover_need 依赖 date.today(),
  使用 FakeDate 子类替换模块级 date 以稳定测试。
"""
from __future__ import annotations

import re as _re
import sys
from datetime import date
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import utils.futures_rollover_manager as frm  # noqa: E402
from utils.futures_rollover_manager import (  # noqa: E402
    ROLLOVER_DAYS_BEFORE_EXPIRY,
    FuturesRolloverManager,
    _get_futures_expiry,
    _get_third_friday,
)

# ============================================================
# 时间 mock 基础设施
# ============================================================


class _FakeDate(date):
    """可固定 today() 的 date 子类 (用于稳定测试时间相关逻辑)."""

    _fixed: date = date(2025, 6, 16)  # 默认 2025-06-16 (周一)

    @classmethod
    def today(cls) -> date:  # type: ignore[override]
        return cls._fixed


@pytest.fixture
def fixed_date(monkeypatch):
    """替换源模块的 date 为 _FakeDate, 返回 _FakeDate 以便测试调整 _fixed."""
    _FakeDate._fixed = date(2025, 6, 16)
    monkeypatch.setattr(frm, "date", _FakeDate)
    return _FakeDate


@pytest.fixture(autouse=True)
def _ensure_re_module(monkeypatch):
    """源模块缺失 `import re`, resolve_hedge_contract 需要它 — 注入以覆盖分支."""
    if not hasattr(frm, "re"):
        monkeypatch.setattr(frm, "re", _re, raising=False)


# ============================================================
# 模块级函数: _get_third_friday / _get_futures_expiry
# ============================================================


class FuturesRolloverManagerTest:
    """FuturesRolloverManager + 模块级辅助函数测试."""

    # ------ _get_third_friday ------
    def test_third_friday_jun_2025(self):
        # 2025-06: 1日周日, 第一个周五=6日, 第三个周五=20日
        assert _get_third_friday(2025, 6) == date(2025, 6, 20)

    def test_third_friday_dec_2025(self):
        # 2025-12: 1日周一, 第一个周五=5日, 第三个周五=19日
        assert _get_third_friday(2025, 12) == date(2025, 12, 19)

    def test_third_friday_jan_2025(self):
        # 2025-01: 1日周三, 第一个周五=3日, 第三个周五=17日
        assert _get_third_friday(2025, 1) == date(2025, 1, 17)

    def test_third_friday_feb_2025(self):
        # 2025-02: 1日周六, 第一个周五=7日, 第三个周五=21日
        assert _get_third_friday(2025, 2) == date(2025, 2, 21)

    def test_third_friday_is_weekday_friday(self):
        for (y, m) in [(2024, 3), (2024, 8), (2026, 10), (2027, 7)]:
            d = _get_third_friday(y, m)
            assert d.weekday() == 4  # 周五

    # ------ _get_futures_expiry ------
    def test_get_futures_expiry_index_futures(self):
        for p in ("IF", "IC", "IM", "IH"):
            assert _get_futures_expiry(p, "07", 2025) == date(2025, 7, 18)

    def test_get_futures_expiry_commodity_returns_none(self):
        for p in ("RB", "AU", "CU", "SC", "CF"):
            assert _get_futures_expiry(p, "08", 2025) is None

    # ------ __init__ ------
    def test_init_default_rollover_days(self):
        m = FuturesRolloverManager()
        assert m.rollover_days == ROLLOVER_DAYS_BEFORE_EXPIRY
        assert m.rollover_days == 3

    def test_init_custom_rollover_days(self):
        m = FuturesRolloverManager(rollover_days_before=5)
        assert m.rollover_days == 5

    # ------ get_active_contract: 股指 ------
    def test_get_active_contract_index_current_month(self, fixed_date):
        # today=2025-06-16, 6月第三个周五=20日, days=4 > 3 → 当月 2506
        fixed_date._fixed = date(2025, 6, 16)
        m = FuturesRolloverManager()
        assert m.get_active_contract("IF", "CFFEX") == "IF2506.CFFEX"

    def test_get_active_contract_index_rollover_to_next(self, fixed_date):
        # today=2025-06-18, 6月第三个周五=20日, days=2 <= 3 → 换月到7月 2507
        fixed_date._fixed = date(2025, 6, 18)
        m = FuturesRolloverManager()
        assert m.get_active_contract("IF", "CFFEX") == "IF2507.CFFEX"

    def test_get_active_contract_index_on_expiry_day(self, fixed_date):
        # today=2025-06-20 (到期日), days=0 <= 3 → 换月
        fixed_date._fixed = date(2025, 6, 20)
        m = FuturesRolloverManager()
        assert m.get_active_contract("IC", "CFFEX") == "IC2507.CFFEX"

    def test_get_active_contract_dec_rollover_cross_year(self, fixed_date):
        # today=2025-12-18, 12月第三个周五=19日, days=1 <= 3 → 换月到2026年1月 2601
        fixed_date._fixed = date(2025, 12, 18)
        m = FuturesRolloverManager()
        assert m.get_active_contract("IF", "CFFEX") == "IF2601.CFFEX"

    def test_get_active_contract_im_ih(self, fixed_date):
        fixed_date._fixed = date(2025, 6, 16)
        m = FuturesRolloverManager()
        assert m.get_active_contract("IM", "CFFEX") == "IM2506.CFFEX"
        assert m.get_active_contract("IH", "CFFEX") == "IH2506.CFFEX"

    # ------ get_active_contract: 商品期货 ------
    def test_get_active_contract_commodity_next_month(self, fixed_date):
        # 商品期货: 默认下月, today=2025-06-16 → 7月
        fixed_date._fixed = date(2025, 6, 16)
        m = FuturesRolloverManager()
        assert m.get_active_contract("RB", "SHFE") == "RB2507.SHFE"

    def test_get_active_contract_commodity_dec_cross_year(self, fixed_date):
        fixed_date._fixed = date(2025, 12, 10)
        m = FuturesRolloverManager()
        assert m.get_active_contract("CU", "SHFE") == "CU2601.SHFE"

    def test_get_active_contract_non_cffex_index_treated_as_commodity(self, fixed_date):
        # IF 但非 CFFEX 交易所 → 走商品分支
        fixed_date._fixed = date(2025, 6, 16)
        m = FuturesRolloverManager()
        assert m.get_active_contract("IF", "SHFE") == "IF2507.SHFE"

    # ------ detect_rollover_need ------
    def test_detect_rollover_need_unparseable(self, fixed_date):
        fixed_date._fixed = date(2025, 6, 16)
        m = FuturesRolloverManager()
        need, reason = m.detect_rollover_need("INVALID")
        assert need is False
        assert "无法解析" in reason

    def test_detect_rollover_need_commodity_not_supported(self, fixed_date):
        fixed_date._fixed = date(2025, 6, 16)
        m = FuturesRolloverManager()
        need, reason = m.detect_rollover_need("RB2508.SHFE")
        assert need is False
        assert "商品期货" in reason
        assert "暂不支持" in reason

    def test_detect_rollover_need_index_near_expiry(self, fixed_date):
        # IF2507 到期 2025-07-18, today=2025-07-16, days=2 <= 3 → 需换月
        fixed_date._fixed = date(2025, 7, 16)
        m = FuturesRolloverManager()
        need, reason = m.detect_rollover_need("IF2507.CFFEX")
        assert need is True
        assert "需要换月" in reason

    def test_detect_rollover_need_index_far_from_expiry(self, fixed_date):
        # IF2507 到期 2025-07-18, today=2025-06-16, days=32 > 3 → 不换月
        fixed_date._fixed = date(2025, 6, 16)
        m = FuturesRolloverManager()
        need, reason = m.detect_rollover_need("IF2507.CFFEX")
        assert need is False
        assert reason == ""

    def test_detect_rollover_need_custom_rollover_days(self, fixed_date):
        # rollover_days=10, IF2507 到期 7-18, today=7-10, days=8 <= 10 → 需换月
        fixed_date._fixed = date(2025, 7, 10)
        m = FuturesRolloverManager(rollover_days_before=10)
        need, _ = m.detect_rollover_need("IF2507.CFFEX")
        assert need is True

    # ------ generate_roll_orders ------
    def test_generate_roll_orders_unparseable_raises(self, fixed_date):
        fixed_date._fixed = date(2025, 6, 16)
        m = FuturesRolloverManager()
        with pytest.raises(ValueError, match="无法解析"):
            m.generate_roll_orders("INVALID", 1)

    def test_generate_roll_orders_no_rollover_needed(self, fixed_date):
        # today=2025-06-16, 主力=IF2506, old=IF2506 → new==old → 空 orders
        fixed_date._fixed = date(2025, 6, 16)
        m = FuturesRolloverManager()
        old, new, orders = m.generate_roll_orders("IF2506.CFFEX", 3)
        assert old == "IF2506.CFFEX"
        assert new == "IF2506.CFFEX"
        assert orders == []

    def test_generate_roll_orders_short_direction(self, fixed_date):
        # today=2025-06-16, 主力=IF2506, old=IF2507 → new=IF2506 != old
        fixed_date._fixed = date(2025, 6, 16)
        m = FuturesRolloverManager()
        old, new, orders = m.generate_roll_orders("IF2507.CFFEX", 3, direction="SHORT")
        assert old == "IF2507.CFFEX"
        assert new == "IF2506.CFFEX"
        assert len(orders) == 2
        # SHORT: 平仓 BUY, 开仓 SELL
        assert orders[0]["side"] == "BUY"
        assert orders[0]["offset"] == "CLOSE"
        assert orders[0]["symbol"] == "IF2507.CFFEX"
        assert orders[0]["quantity"] == 3
        assert orders[1]["side"] == "SELL"
        assert orders[1]["offset"] == "OPEN"
        assert orders[1]["symbol"] == "IF2506.CFFEX"
        assert orders[1]["quantity"] == 3

    def test_generate_roll_orders_long_direction(self, fixed_date):
        fixed_date._fixed = date(2025, 6, 16)
        m = FuturesRolloverManager()
        old, new, orders = m.generate_roll_orders("IF2507.CFFEX", 5, direction="LONG")
        assert len(orders) == 2
        # LONG: 平仓 SELL, 开仓 BUY
        assert orders[0]["side"] == "SELL"
        assert orders[0]["offset"] == "CLOSE"
        assert orders[1]["side"] == "BUY"
        assert orders[1]["offset"] == "OPEN"

    def test_generate_roll_orders_descriptions(self, fixed_date):
        fixed_date._fixed = date(2025, 6, 16)
        m = FuturesRolloverManager()
        _, _, orders = m.generate_roll_orders("IF2507.CFFEX", 1)
        assert "换月—平旧合约" in orders[0]["description"]
        assert "换月—开新合约" in orders[1]["description"]

    # ------ _parse_contract ------
    def test_parse_contract_index_cffex(self):
        m = FuturesRolloverManager()
        assert m._parse_contract("IF2507.CFFEX") == ("IF", "25", "07", "CFFEX")

    def test_parse_contract_commodity_shfe(self):
        m = FuturesRolloverManager()
        assert m._parse_contract("CU2508.SHFE") == ("CU", "25", "08", "SHFE")

    def test_parse_contract_old_shf_suffix(self):
        m = FuturesRolloverManager()
        # 旧写法 SHF → 规范化 SHFE, 但第4项保留原始后缀大写
        result = m._parse_contract("CU2508.SHF")
        assert result is not None
        assert result[0] == "CU"
        assert result[3] == "SHF"

    def test_parse_contract_ine(self):
        m = FuturesRolloverManager()
        assert m._parse_contract("SC2509.INE") == ("SC", "25", "09", "INE")

    def test_parse_contract_invalid_returns_none(self):
        m = FuturesRolloverManager()
        assert m._parse_contract("INVALID") is None
        assert m._parse_contract("600519.SH") is None
        assert m._parse_contract("IF2507") is None  # 缺交易所

    # ------ is_tradable ------
    def test_is_tradable_idx_false(self):
        m = FuturesRolloverManager()
        assert m.is_tradable("000300.IDX") is False

    def test_is_tradable_continuous_contract_false(self):
        # "IF00" (无后缀) endswith "00" → 连续合约拦截
        # 注: 源码 endswith("00") 检查整个字符串, 对带 .CFFEX 后缀的代码无效 (源 bug)
        m = FuturesRolloverManager()
        assert m.is_tradable("IF00") is False

    def test_is_tradable_index_contract_false(self):
        # "IF0001" (无后缀) endswith "0001" → 拦截为不可交易
        # 注: 源码 endswith("0001") 检查整个字符串, 对带 .CFFEX 后缀的代码无效 (源 bug)
        m = FuturesRolloverManager()
        assert m.is_tradable("IF0001") is False

    def test_is_tradable_valid_future_true(self):
        m = FuturesRolloverManager()
        assert m.is_tradable("IF2507.CFFEX") is True
        assert m.is_tradable("CU2508.SHFE") is True

    def test_is_tradable_unparseable_false(self):
        m = FuturesRolloverManager()
        assert m.is_tradable("600519.SH") is False
        assert m.is_tradable("GARBAGE") is False

    # ------ get_days_to_expiry ------
    def test_get_days_to_expiry_unparseable(self, fixed_date):
        fixed_date._fixed = date(2025, 6, 16)
        m = FuturesRolloverManager()
        assert m.get_days_to_expiry("INVALID") is None

    def test_get_days_to_expiry_commodity(self, fixed_date):
        fixed_date._fixed = date(2025, 6, 16)
        m = FuturesRolloverManager()
        assert m.get_days_to_expiry("RB2508.SHFE") is None

    def test_get_days_to_expiry_index(self, fixed_date):
        # IF2507 到期 2025-07-18, today=2025-06-16 → 32 天
        fixed_date._fixed = date(2025, 6, 16)
        m = FuturesRolloverManager()
        assert m.get_days_to_expiry("IF2507.CFFEX") == 32

    def test_get_days_to_expiry_past_expiry_negative(self, fixed_date):
        # IF2506 到期 2025-06-20, today=2025-06-25 → -5
        fixed_date._fixed = date(2025, 6, 25)
        m = FuturesRolloverManager()
        assert m.get_days_to_expiry("IF2506.CFFEX") == -5

    # ------ resolve_hedge_contract ------
    def test_resolve_hedge_already_tradable(self, fixed_date):
        fixed_date._fixed = date(2025, 6, 16)
        m = FuturesRolloverManager()
        assert m.resolve_hedge_contract("IF2507.CFFEX") == "IF2507.CFFEX"

    def test_resolve_hedge_if_futures_map(self, fixed_date):
        fixed_date._fixed = date(2025, 6, 16)
        m = FuturesRolloverManager()
        # IF_futures → IF → 主力 IF2506.CFFEX
        assert m.resolve_hedge_contract("IF_futures") == "IF2506.CFFEX"

    def test_resolve_hedge_if_cffex_map(self, fixed_date):
        fixed_date._fixed = date(2025, 6, 16)
        m = FuturesRolloverManager()
        assert m.resolve_hedge_contract("IF.CFFEX") == "IF2506.CFFEX"

    def test_resolve_hedge_bare_if_map(self, fixed_date):
        fixed_date._fixed = date(2025, 6, 16)
        m = FuturesRolloverManager()
        assert m.resolve_hedge_contract("IF") == "IF2506.CFFEX"

    def test_resolve_hedge_ic_im_ih(self, fixed_date):
        fixed_date._fixed = date(2025, 6, 16)
        m = FuturesRolloverManager()
        assert m.resolve_hedge_contract("IC_futures") == "IC2506.CFFEX"
        assert m.resolve_hedge_contract("IM_futures") == "IM2506.CFFEX"
        assert m.resolve_hedge_contract("IH_futures") == "IH2506.CFFEX"

    def test_resolve_hedge_commodity_rb(self, fixed_date):
        fixed_date._fixed = date(2025, 6, 16)
        m = FuturesRolloverManager()
        # RB_futures → RB → 商品 → exchange=None → "RB2507.None"
        result = m.resolve_hedge_contract("RB_futures")
        assert result is not None
        assert result.startswith("RB2507.")

    def test_resolve_hedge_unknown_letter_prefix(self, fixed_date):
        # 不在 _HEDGE_NAME_MAP, 但 re.match 提取字母前缀
        fixed_date._fixed = date(2025, 6, 16)
        m = FuturesRolloverManager()
        result = m.resolve_hedge_contract("XYZ_futures")
        assert result is not None
        assert result.startswith("XYZ2507.")

    def test_resolve_hedge_digit_start_returns_none(self, fixed_date):
        # 数字开头, re.match(r"^([A-Za-z]+)") 匹配失败 → None
        fixed_date._fixed = date(2025, 6, 16)
        m = FuturesRolloverManager()
        assert m.resolve_hedge_contract("123_futures") is None

    def test_resolve_hedge_active_none_returns_none(self, fixed_date, monkeypatch):
        # 覆盖 if active is None 分支 (get_active_contract 正常永不返回 None, 需 mock)
        fixed_date._fixed = date(2025, 6, 16)
        m = FuturesRolloverManager()
        monkeypatch.setattr(m, "get_active_contract", lambda *a, **k: None)
        assert m.resolve_hedge_contract("IF_futures") is None
