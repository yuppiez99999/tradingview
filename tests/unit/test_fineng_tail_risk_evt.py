"""utils.fineng.tail_risk_evt 单元测试 — EVT GPD 拟合 / VaR / ES"""
from __future__ import annotations

import math
import random

import pytest

from utils.fineng.tail_risk_evt import (
    EVTResult,
    evt_var_es,
    fit_evt,
)


def _synthetic_heavy_tailed(n: int = 2000, seed: int = 31) -> list:
    """合成厚尾收益序列: 90% 正态 + 10% 极端负值"""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        if rng.random() < 0.1:
            out.append(rng.gauss(-0.05, 0.03))   # 极端负收益
        else:
            out.append(rng.gauss(0.0, 0.01))
    return out


def test_fit_evt_converged():
    """fit_evt: 返回 EVTResult, 厚尾序列收敛且参数合理"""
    r = _synthetic_heavy_tailed()
    res = fit_evt(r)
    assert isinstance(res, EVTResult)
    assert res.converged is True
    # 形状参数有限 (厚尾 xi 与 0 偏离)
    assert math.isfinite(res.xi)
    assert math.isfinite(res.sigma)
    assert res.sigma > 0.0
    assert math.isfinite(res.threshold_u)
    assert res.n_excess > 0
    assert res.n_excess < res.n_total


def test_fit_evt_min_history_rejects():
    """样本不足 min_history → converged=False"""
    short = _synthetic_heavy_tailed(n=50)
    res = fit_evt(short, min_history=120)
    assert res.converged is False


def test_evt_var_es_dict():
    """evt_var_es: 返回 dict 含 var / es (两者有限且为尾部负值)"""
    r = _synthetic_heavy_tailed()
    res99 = evt_var_es(r, confidence=0.99)
    res95 = evt_var_es(r, confidence=0.95)
    assert "var" in res99 and "es" in res99
    assert math.isfinite(res99["var"]) and math.isfinite(res99["es"])
    # VaR / ES 为尾部损失 (负值)
    assert res99["var"] < 0.0
    assert res99["es"] < 0.0
    # 99% 置信度下 VaR 应比 95% 更极端 (更负)
    assert res99["var"] <= res95["var"] + 1e-9


def test_evt_var_es_consistent_with_fit():
    """evt_var_es 与 fit_evt 的 var_99/es_99 一致"""
    r = _synthetic_heavy_tailed()
    res = fit_evt(r)
    d = evt_var_es(r, confidence=0.99)
    assert d["var"] == pytest.approx(res.var_99, abs=1e-6)
    assert d["es"] == pytest.approx(res.es_99, abs=1e-6)


def test_fit_evt_normal_data():
    """纯正态数据: 样本充足时仍收敛, xi 接近 0 区间"""
    rng = random.Random(5)
    normal = [rng.gauss(0.0, 0.01) for _ in range(1500)]
    res = fit_evt(normal)
    assert res.converged is True
    assert abs(res.xi) == pytest.approx(0.0, abs=0.5)
