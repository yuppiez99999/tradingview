"""AutoFactorResearch accepted 因子 → AlphaFactorLibrary 持久化闭环测试."""

from __future__ import annotations

from utils.alpha_factor.auto_research import (
    AutoFactorResearch,
    FactorCandidate,
    ReviewResult,
)
from utils.alpha_factor.library import (
    AlphaFactorLibrary,
    load_research_factors,
    save_research_factors,
)


def _specs() -> list[dict]:
    return [
        {
            "name": "EXPR_MOM_20D",
            "expression": "rank(close / delay(close, 20) - 1)",
            "category": "momentum",
            "hypothesis": "20日动量",
            "rationale": "经典截面动量",
            "source": "rule",
            "ic_mean": 0.041,
            "ic_ir": 0.62,
            "turnover": 0.21,
            "half_life_days": 8.0,
            "long_short": 0.03,
        }
    ]


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    store = tmp_path / "alpha_factor_research.json"
    monkeypatch.setattr("utils.alpha_factor.library._RESEARCH_FACTORS_PATH", str(store))
    n = save_research_factors(_specs())
    assert n == 1
    loaded = load_research_factors()
    assert len(loaded) == 1
    assert loaded[0]["name"] == "EXPR_MOM_20D"
    assert loaded[0]["cycles"] == 1


def test_merge_increments_cycles(tmp_path, monkeypatch):
    store = tmp_path / "alpha_factor_research.json"
    monkeypatch.setattr("utils.alpha_factor.library._RESEARCH_FACTORS_PATH", str(store))
    save_research_factors(_specs())
    save_research_factors(_specs())  # 同名合并
    loaded = load_research_factors()
    assert len(loaded) == 1
    assert loaded[0]["cycles"] == 2
    assert loaded[0]["ic_mean"] == 0.041


def test_get_accepted_specs():
    ar = AutoFactorResearch()
    cand = FactorCandidate(
        name="EXPR_MOM_20D",
        hypothesis="20日动量",
        expression="rank(close / delay(close, 20) - 1)",
        category="momentum",
    )
    ar.accepted_meta["EXPR_MOM_20D"] = cand
    ar._review_cache["EXPR_MOM_20D"] = ReviewResult(
        name="EXPR_MOM_20D", ic_mean=0.041, ic_ir=0.62, turnover=0.21
    )
    specs = ar.get_accepted_specs()
    assert len(specs) == 1
    assert specs[0]["expression"] == "rank(close / delay(close, 20) - 1)"
    assert specs[0]["ic_mean"] == 0.041
    assert specs[0]["category"] == "momentum"


def test_library_loads_persisted(tmp_path, monkeypatch):
    store = tmp_path / "alpha_factor_research.json"
    monkeypatch.setattr("utils.alpha_factor.library._RESEARCH_FACTORS_PATH", str(store))
    save_research_factors(_specs())
    lib = AlphaFactorLibrary(enable_expression=False)  # 研究因子仍应被自动纳入
    assert lib.research_factors
    assert lib.enable_expression is True
