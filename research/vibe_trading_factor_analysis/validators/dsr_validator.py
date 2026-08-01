# -*- coding: utf-8 -*-
"""DSRValidator - Gate 3 防过拟合验证器

实现 Bailey & Lopez de Prado (2014) Deflated Sharpe Ratio:
  DSR = Phi_inv(1 - exp(-e * (SR_obs - E[max|SR|]) * sqrt(T)))
  E[max|SR|] = sqrt(2*ln(n_trials)) * sigma_SR

450+ 候选因子下多重检验偏差是头号风险，DSR 是唯一可信防线（Renaissance 信条）。
"""
from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass
from typing import Any, Dict

import numpy as np

logger = logging.getLogger("dsr_validator")


@dataclass
class DSRResult:
    """DSR 验证结果"""
    factor_name: str
    sr_observed: float = 0.0
    expected_max_sr: float = 0.0
    dsr_value: float = 0.0
    n_trials: int = 0
    sample_length: int = 0
    is_overfit: bool = True
    gate_3_pass: bool = False
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DSRValidator:
    """Deflated Sharpe Ratio 验证器（Gate 3）。阈值：MIN_TRIALS=5, DSR>0 即非过拟合"""
    MIN_TRIALS = 5

    def __init__(self, config=None):
        c = config or {}
        self.min_trials = int(c.get("min_trials", self.MIN_TRIALS))

    def validate(self, factor_returns, n_trials, factor_name="candidate"):
        """factor_returns=因子多空组合日收益率序列; n_trials=已测试因子总数"""
        r = DSRResult(factor_name=factor_name, n_trials=n_trials, sample_length=len(factor_returns))
        if len(factor_returns) < 20:
            r.reason = f"samples {len(factor_returns)} < 20"
            return r
        if n_trials < self.min_trials:
            r.reason = f"n_trials {n_trials} < {self.min_trials} (多重检验不可信)"
            return r
        rets = np.array(factor_returns, dtype=float)
        sr_per = self._sharpe_period(rets)
        sigma_sr = self._sr_std(rets, sr_per)
        T = len(rets)
        e_max = self._expected_max_sr(n_trials, sigma_sr)
        dsr = self._deflated_sr(sr_per, e_max, sigma_sr, T)
        r.sr_observed = float(sr_per * math.sqrt(252))  # 年化展示
        r.expected_max_sr = float(e_max * math.sqrt(252))
        r.dsr_value = float(dsr)
        r.is_overfit = dsr <= 0.0
        r.gate_3_pass = dsr > 0.0
        r.reason = "pass" if r.gate_3_pass else f"DSR={dsr:.3f} <= 0 (overfit)"
        return r

    def _sharpe_period(self, rets):
        if len(rets) < 2 or np.std(rets) < 1e-12: return 0.0
        return float(np.mean(rets) / np.std(rets, ddof=1))

    def _sr_std(self, rets, sr_per):
        n = len(rets)
        if n < 4: return 1.0 / math.sqrt(max(n, 1))
        skew = float(self._skew(rets))
        kurt = float(self._kurt(rets))
        var = max(1e-12, (1 - skew * sr_per + (kurt - 1) / 4.0 * sr_per ** 2) / n)
        return math.sqrt(var)

    def _expected_max_sr(self, n_trials, sigma_sr):
        if n_trials < 2 or sigma_sr < 1e-12: return 0.0
        z = math.sqrt(2.0 * math.log(n_trials))
        correction = (math.log(math.pi) + math.log(max(2.0 * math.log(n_trials), 1e-12))) / (2.0 * z)
        return sigma_sr * (z - correction)

    def _deflated_sr(self, sr_obs, e_max, sigma_sr, T):
        # 返回 z-score 形式（非概率）：>0 表示 SR_obs 超过多重检验期望，非过拟合
        if sigma_sr < 1e-12: return 0.0
        return (sr_obs - e_max) / sigma_sr

    def _norm_cdf(self, z):
        return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))

    def _skew(self, rets):
        if len(rets) < 3: return 0.0
        m = np.mean(rets)
        s = np.std(rets, ddof=1)
        if s < 1e-12: return 0.0
        return float(np.mean(((rets - m) / s) ** 3))

    def _kurt(self, rets):
        if len(rets) < 4: return 3.0
        m = np.mean(rets)
        s = np.std(rets, ddof=1)
        if s < 1e-12: return 3.0
        return float(np.mean(((rets - m) / s) ** 4))
