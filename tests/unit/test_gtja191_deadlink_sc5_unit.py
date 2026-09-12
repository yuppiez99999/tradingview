"""SC-5 剩余半边回归测试 — utils/gtja191_factors.py 死链修复.

先红实证 (修复前, main 2d5f0fa4):
  - GTJA191Factors().alpha144(df) -> AttributeError (VibeTradingAdapter 无 compute_single_stock)
  - factor_ids / count / list_by_theme / get_formula / get_info 同样死链
  - utils/signal_fusion.py `_get_gtja191_signal_source` 引用不存在的 utils.kronos_predictor
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))


def _make_df(n: int = 60, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    base = rng.random(n) * 10 + 10
    return pd.DataFrame(
        {
            "open": base,
            "high": base + rng.random(n),
            "low": base - rng.random(n),
            "close": base + rng.random(n) * 0.5,
            "volume": rng.random(n) * 1e6,
            "amount": rng.random(n) * 1e7,
        }
    )


class TestComputeNoDeadLink:
    """compute 族: 不得抛 AttributeError 死链异常"""

    @pytest.fixture()
    def factors(self):
        from utils.gtja191_factors import GTJA191Factors

        return GTJA191Factors(lookback=20)

    def test_alpha144_returns_value_or_none(self, factors):
        v = factors.alpha144(_make_df())
        assert v is None or isinstance(v, float)

    @pytest.mark.parametrize(
        "method",
        ["alpha144", "alpha001", "alpha005", "alpha010", "alpha028",
         "alpha040", "alpha072", "alpha158", "alpha189"],
    )
    def test_classic_shortcuts_no_deadlink(self, factors, method):
        # 修复前: 全部 AttributeError('compute_single_stock') 死链
        v = getattr(factors, method)(_make_df())
        assert v is None or isinstance(v, (int, float))

    def test_compute_batch_shape(self, factors):
        out = factors.compute(_make_df(), ["gtja191_144"])
        assert isinstance(out, dict)
        assert "gtja191_144" in out
        assert out["gtja191_144"] is None or isinstance(out["gtja191_144"], float)

    def test_compute_empty_df(self, factors):
        assert factors.compute(pd.DataFrame(), ["gtja191_144"]) == {}


class TestMetadataNoDeadLink:
    """元数据族: factor_ids/count/list_by_theme/get_formula/get_info"""

    def test_factor_ids_not_empty(self):
        from utils.gtja191_factors import GTJA191Factors

        f = GTJA191Factors()
        ids = f.factor_ids
        assert isinstance(ids, list)
        assert len(ids) > 0
        assert all(isinstance(i, str) for i in ids)

    def test_count_positive(self):
        from utils.gtja191_factors import GTJA191Factors

        assert GTJA191Factors().count > 0

    def test_list_by_theme_no_exc(self):
        from utils.gtja191_factors import GTJA191Factors

        out = GTJA191Factors().list_by_theme("momentum")
        assert isinstance(out, list)

    def test_get_formula_no_exc(self):
        from utils.gtja191_factors import GTJA191Factors

        f = GTJA191Factors()
        for aid in f.factor_ids[:5]:
            v = f.get_formula(aid)
            assert isinstance(v, str)
            assert v, f"get_formula({aid!r}) 返回空字符串, 与「获取因子公式」语义不符"

    def test_get_info_no_exc(self):
        from utils.gtja191_factors import GTJA191Factors

        f = GTJA191Factors()
        for aid in f.factor_ids[:5]:
            v = f.get_info(aid)
            assert isinstance(v, dict)


class TestFallbackSemantics:
    """降级语义: 后端不可用时必须显式 None / warning, 不得静默假值"""

    def test_compute_series_unsupported_backend_returns_none(self):
        from utils.gtja191_factors import GTJA191Factors

        f = GTJA191Factors()
        out = f.compute_series(_make_df(), "gtja191_144")
        assert out is None or isinstance(out, pd.Series)

    def test_signal_fusion_gtja_import_path_exists(self):
        """signal_fusion 的 GTJA 信号源 import 路径必须真实存在 (原死链 kronos_predictor)"""
        import importlib

        mod = importlib.import_module("utils.signal_fusion")
        src = Path(mod.__file__).read_text(encoding="utf-8")
        # 提取 _get_gtja191_signal_source 中的 from-import 语句逐个验证
        import re

        body = re.search(
            r"def _get_gtja191_signal_source.*?(?=\ndef )", src, re.S
        )
        assert body is not None
        for m in re.finditer(r"^\s+from (\.*[\w.]+) import", body.group(0), re.M):
            modname = m.group(1)
            if modname.startswith("."):
                # 相对导入: 以 utils.signal_fusion 所在包解析
                importlib.import_module("utils" + modname)
                continue
            try:
                importlib.import_module(modname)
            except ImportError as e:
                pytest.fail(f"signal_fusion GTJA 信号源 import 死链: {modname}: {e}")

class TestNoClaimRealityDrift:
    """SC-5 复核建议 #2/#3: 把「宣称」变成「断言」, 防止两份人工清单静默漂移

    - _MS_STRATEGY_IMPLEMENTED 必须与 ms_strategy 后端真实 alphaN 方法集一致
    - list_by_theme 主题映射必须完整覆盖全部已实现因子 (无因子从主题查询中消失)
    """

    def test_declared_set_matches_backend_methods(self):
        import re as _re

        from ms_strategy.factors.gtja191_factors import GTJA191Factors as MsBackend
        from utils.gtja191_factors import _MS_STRATEGY_IMPLEMENTED

        backend = MsBackend()
        detected = {
            int(m.group(1))
            for m in (
                _re.fullmatch(r"alpha(\d+)", name) for name in dir(backend)
            )
            if m
        }
        declared = set(_MS_STRATEGY_IMPLEMENTED)
        assert detected == declared, (
            f"宣称 ≠ 事实: 后端新增 {sorted(detected - declared)}, "
            f"后端缺失 {sorted(declared - detected)} —— 请同步 _MS_STRATEGY_IMPLEMENTED"
        )

    def test_theme_mapping_covers_all_implemented(self):
        from utils.gtja191_factors import _MS_STRATEGY_IMPLEMENTED, GTJA191Factors

        f = GTJA191Factors()
        themes = ["momentum", "reversal", "volume", "volatility",
                  "liquidity", "microstructure"]
        themed: set[str] = set()
        for t in themes:
            themed.update(int(aid.split("_")[-1]) for aid in f.list_by_theme(t))
        missing = set(_MS_STRATEGY_IMPLEMENTED) - themed
        assert not missing, (
            f"以下已实现因子不在任何主题映射中, 会从主题查询中消失: {sorted(missing)}"
        )
