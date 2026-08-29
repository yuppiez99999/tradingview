"""W6.3.3 Step 4: wt_structs.py __post_init__ code/exchange 一致性校验单元测试。

覆盖 6 个数据类 × 4 类场景 (正常 / 裸码跳过 / 空 exchange 跳过 / strict 抛错 / 非 strict warning):
    - TickData / BarData / OrderData / TradeData / PositionData / ContractData
"""

from __future__ import annotations

import warnings as _warnings

import pytest

from utils.wt_structs import (
    BarData,
    CodeExchangeMismatchError,
    CodeExchangeMismatchWarning,
    ContractData,
    OrderData,
    PositionData,
    TickData,
    TradeData,
    is_strict_symbol_validation,
    strict_symbol_validation,
)

# ============================================================
# 共享构造参数 (仅填充必填字段, 其余默认)
# ============================================================

_TICK_KWARGS = dict(
    code="600519.SH",
    exchange="SSE",
    price=1700.0,
    open=1690.0,
    high=1705.0,
    low=1688.0,
    pre_close=1685.0,
    volume=1_000_000,
    amount=1_700_000_000,
)
_BAR_KWARGS = dict(
    code="600519.SH",
    exchange="SSE",
    period="1d",
    open=1690.0,
    high=1705.0,
    low=1688.0,
    close=1700.0,
    volume=1_000_000,
    amount=1_700_000_000,
)
_ORDER_KWARGS = dict(
    order_id="o1",
    code="600519.SH",
    exchange="SSE",
    direction="BUY",
)
_TRADE_KWARGS = dict(
    trade_id="t1",
    order_id="o1",
    code="600519.SH",
    exchange="SSE",
    direction="BUY",
    offset="OPEN",
    price=1700.0,
    volume=100,
    amount=1_700_000,
)
_POSITION_KWARGS = dict(
    code="600519.SH",
    exchange="SSE",
)
_CONTRACT_KWARGS = dict(
    code="600519.SH",
    exchange="SSE",
    name="贵州茅台",
)

# 每个数据类的 (构造函数, 必填 kwargs)
_ALL_DATACLASSES = [
    ("TickData", TickData, _TICK_KWARGS),
    ("BarData", BarData, _BAR_KWARGS),
    ("OrderData", OrderData, _ORDER_KWARGS),
    ("TradeData", TradeData, _TRADE_KWARGS),
    ("PositionData", PositionData, _POSITION_KWARGS),
    ("ContractData", ContractData, _CONTRACT_KWARGS),
]


# ============================================================
# 1. 正常构造 (code/exchange 一致) — 不 warning 不报错
# ============================================================


class TestNormalConstruct:
    @pytest.mark.parametrize("cls_name,cls,kwargs", _ALL_DATACLASSES)
    def test_consistent(self, cls_name, cls, kwargs) -> None:
        """code/exchange 一致 → 无 warning 无异常。"""
        with _warnings.catch_warnings(record=True) as record:
            _warnings.simplefilter("always")
            obj = cls(**kwargs)
        relevant = [
            w for w in record if issubclass(w.category, CodeExchangeMismatchWarning)
        ]
        assert (
            len(relevant) == 0
        ), f"{cls_name} 一致构造应无 warning, 实际: {[str(w.message) for w in relevant]}"
        assert obj.code == kwargs["code"]
        assert obj.exchange == kwargs["exchange"]

    @pytest.mark.parametrize("cls_name,cls,kwargs", _ALL_DATACLASSES)
    def test_consistent_strict(self, cls_name, cls, kwargs) -> None:
        """strict 模式下一致构造 → 不抛异常。"""
        with strict_symbol_validation(True):
            obj = cls(**kwargs)
        assert obj.code == kwargs["code"]


# ============================================================
# 2. 裸码构造 (code 无 ".") — 跳过校验 (向后兼容)
# ============================================================


class TestBareCodeSkip:
    @pytest.mark.parametrize("cls_name,cls,kwargs", _ALL_DATACLASSES)
    def test_bare_code_skips(self, cls_name, cls, kwargs) -> None:
        bare_kwargs = {**kwargs, "code": "600519"}  # 裸码 6 位
        with _warnings.catch_warnings(record=True) as record:
            _warnings.simplefilter("always")
            cls(**bare_kwargs)
        relevant = [
            w for w in record if issubclass(w.category, CodeExchangeMismatchWarning)
        ]
        assert len(relevant) == 0, "裸码 (code 无 '.') 应跳过校验"


# ============================================================
# 3. exchange 为空 / "UNKNOWN" — 跳过校验 (adapters.py 退化场景)
# ============================================================


class TestUnknownExchangeSkip:
    @pytest.mark.parametrize("cls_name,cls,kwargs", _ALL_DATACLASSES)
    def test_empty_exchange_skips(self, cls_name, cls, kwargs) -> None:
        bad_kwargs = {**kwargs, "exchange": ""}
        with _warnings.catch_warnings(record=True) as record:
            _warnings.simplefilter("always")
            cls(**bad_kwargs)
        relevant = [
            w for w in record if issubclass(w.category, CodeExchangeMismatchWarning)
        ]
        assert len(relevant) == 0, "exchange 为空应跳过校验"

    @pytest.mark.parametrize("cls_name,cls,kwargs", _ALL_DATACLASSES)
    def test_unknown_exchange_skips(self, cls_name, cls, kwargs) -> None:
        bad_kwargs = {**kwargs, "exchange": "UNKNOWN"}
        with _warnings.catch_warnings(record=True) as record:
            _warnings.simplefilter("always")
            cls(**bad_kwargs)
        relevant = [
            w for w in record if issubclass(w.category, CodeExchangeMismatchWarning)
        ]
        assert len(relevant) == 0, "exchange=UNKNOWN 应跳过校验"


# ============================================================
# 4. code 后缀 & exchange 不匹配
# ============================================================


class TestMismatch:
    def _bad_kwargs(self, kwargs: dict) -> dict:
        """code=600519.SH 但 exchange=SZSE (错误)。"""
        return {**kwargs, "code": "600519.SH", "exchange": "SZSE"}

    def _bad_kwargs_cn_convention(self, kwargs: dict) -> dict:
        """code=300750.SZ 但 exchange=SSE (中文约定 SSE=沪, SZSE=深) 反向错。"""
        return {**kwargs, "code": "300750.SZ", "exchange": "SSE"}

    # --- 非 strict: RuntimeWarning CodeExchangeMismatchWarning ---

    @pytest.mark.parametrize("cls_name,cls,kwargs", _ALL_DATACLASSES)
    def test_non_strict_warns_sh_sz(self, cls_name, cls, kwargs) -> None:
        bad = self._bad_kwargs(kwargs)
        with pytest.warns(CodeExchangeMismatchWarning, match=r"600519\.SH.*SSE.*SZSE"):
            cls(**bad)

    @pytest.mark.parametrize("cls_name,cls,kwargs", _ALL_DATACLASSES)
    def test_non_strict_warns_sz_sh(self, cls_name, cls, kwargs) -> None:
        bad = self._bad_kwargs_cn_convention(kwargs)
        with pytest.warns(CodeExchangeMismatchWarning, match=r"300750\.SZ.*SZSE.*SSE"):
            cls(**bad)

    # --- strict: 抛 CodeExchangeMismatchError ---

    @pytest.mark.parametrize("cls_name,cls,kwargs", _ALL_DATACLASSES)
    def test_strict_raises_sh_sz(self, cls_name, cls, kwargs) -> None:
        bad = self._bad_kwargs(kwargs)
        with strict_symbol_validation(True):
            with pytest.raises(CodeExchangeMismatchError) as excinfo:
                cls(**bad)
            e = excinfo.value
            assert e.code == "600519.SH"
            assert e.exchange == "SZSE"
            assert e.expected_exchange == "SSE"
            assert cls_name in e.cls_name
            assert "撮合引擎" in str(e)

    @pytest.mark.parametrize("cls_name,cls,kwargs", _ALL_DATACLASSES)
    def test_strict_raises_sz_sh(self, cls_name, cls, kwargs) -> None:
        bad = self._bad_kwargs_cn_convention(kwargs)
        with strict_symbol_validation(True):
            with pytest.raises(CodeExchangeMismatchError) as excinfo:
                cls(**bad)
            assert excinfo.value.expected_exchange == "SZSE"


# ============================================================
# 5. 大小写不敏感 (exchange= lowercase / 小写后缀)
# ============================================================


class TestCaseInsensitive:
    @pytest.mark.parametrize("cls_name,cls,kwargs", _ALL_DATACLASSES)
    def test_exchange_lowercase_accepted(self, cls_name, cls, kwargs) -> None:
        """exchange 字段小写 (如 "sse") — 规范化比较后视为一致 → 无 warning。"""
        low_kwargs = {**kwargs, "exchange": "sse"}
        with _warnings.catch_warnings(record=True) as record:
            _warnings.simplefilter("always")
            cls(**low_kwargs)
        relevant = [
            w for w in record if issubclass(w.category, CodeExchangeMismatchWarning)
        ]
        assert len(relevant) == 0, "exchange 小写规范化后应视为一致"

    @pytest.mark.parametrize("cls_name,cls,kwargs", _ALL_DATACLASSES)
    def test_code_suffix_lowercase_accepted(self, cls_name, cls, kwargs) -> None:
        """code 后缀小写 (如 "600519.sh") — 规范化比较后视为一致 → 无 warning。"""
        low_kwargs = {**kwargs, "code": "600519.sh"}
        with _warnings.catch_warnings(record=True) as record:
            _warnings.simplefilter("always")
            cls(**low_kwargs)
        relevant = [
            w for w in record if issubclass(w.category, CodeExchangeMismatchWarning)
        ]
        assert len(relevant) == 0, "code 后缀小写规范化后应视为一致"


# ============================================================
# 6. strict_symbol_validation 上下文管理器
# ============================================================


class TestStrictContextManager:
    def test_default_not_strict(self) -> None:
        assert is_strict_symbol_validation() is False

    def test_enable_then_restore(self) -> None:
        original = is_strict_symbol_validation()
        with strict_symbol_validation(True):
            assert is_strict_symbol_validation() is True
        assert is_strict_symbol_validation() == original

    def test_nested_disable(self) -> None:
        with strict_symbol_validation(True):
            with strict_symbol_validation(False):
                assert is_strict_symbol_validation() is False
            assert is_strict_symbol_validation() is True
        assert is_strict_symbol_validation() is False

    def test_restore_on_exception(self) -> None:
        original = is_strict_symbol_validation()
        with pytest.raises(ValueError), strict_symbol_validation(True):
            raise ValueError("boom")
        assert is_strict_symbol_validation() == original


# ============================================================
# 7. 旧写法 SHF / ZCE → 规范化 SHFE / CZCE 测试
# ============================================================


class TestOldSuffixNormalized:
    @pytest.mark.parametrize(
        "cls_name,cls,kwargs",
        [
            (
                "PositionData",
                PositionData,
                {"code": "CU2508.SHF", "exchange": "SHFE"},  # SHF 后缀 → 预期 SHFE
            ),
            (
                "ContractData",
                ContractData,
                {"code": "CF2509.ZCE", "exchange": "CZCE", "name": "棉花期货"},
            ),
        ],
    )
    def test_old_suffix_accepted(self, cls_name, cls, kwargs) -> None:
        """旧后缀 SHF/ZCE — normalize 后与 SHFE/CZCE 一致 → 无 warning。"""
        with _warnings.catch_warnings(record=True) as record:
            _warnings.simplefilter("always")
            cls(**kwargs)
        relevant = [
            w for w in record if issubclass(w.category, CodeExchangeMismatchWarning)
        ]
        assert (
            len(relevant) == 0
        ), f"{cls_name} 旧后缀 {kwargs['code']!r} 规范化后应视为一致"
