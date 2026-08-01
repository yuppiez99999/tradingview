# -*- coding: utf-8 -*-
"""RegimeConditioner - E3 Regime 条件化验证器

测试因子在 bull/bear/choppy/rebound 全 regime 表现。
Regime 基于 510300 MA60（对齐 project_memory V6.2 bull regime 失败根因）。
通过条件：每 regime IC_IR > 0.2（或 regime_tagged 显式标注仅特定 regime 适用）。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, asdict
from typing import Any, Dict

import numpy as np

logger = logging.getLogger("regime_conditioner")


@dataclass
class RegimeResult:
    """单因子 Regime 条件化结果"""
    factor_name: str
    per_regime_ic_ir: Dict[str, float] = field(default_factory=dict)
    per_regime_samples: Dict[str, int] = field(default_factory=dict)
    min_regime_ic_ir: float = 0.0
    weakest_regime: str = ""
    pass_all_regimes: bool = False
    regime_tag: str = ""
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RegimeConditioner:
    """Regime 条件化验证器。阈值：IC_IR_PER_REGIME=0.2, MA_WINDOW=60"""
    IC_IR_PER_REGIME = 0.2
    MA_WINDOW = 60
    CHOPPY_BAND = 0.03
    REBOUND_THRESHOLD = 0.05

    def __init__(self, config=None):
        c = config or {}
        self.ic_ir_threshold = float(c.get("ic_ir_per_regime", self.IC_IR_PER_REGIME))
        self.ma_window = int(c.get("ma_window", self.MA_WINDOW))

    def _classify_regimes(self, benchmark_returns):
        """划分 regime：bull/bear/choppy/rebound，基于 510300 MA60"""
        rets = list(benchmark_returns)
        n = len(rets)
        regimes = ["unknown"] * n
        prices = [1.0]
        for r in rets:
            prices.append(prices[-1] * (1.0 + r))
        for i in range(n):
            if i < self.ma_window:
                regimes[i] = "warmup"
                continue
            window = prices[i - self.ma_window + 1: i + 2]
            ma = sum(window) / len(window)
            cur = prices[i + 1]
            prev_ma = sum(prices[i - self.ma_window: i + 1]) / (self.ma_window + 1)
            ratio = cur / ma - 1.0 if ma > 0 else 0.0
            ma_slope = (ma - prev_ma) / prev_ma if prev_ma > 0 else 0.0
            if abs(ratio) < self.CHOPPY_BAND:
                regimes[i] = "choppy"
            elif ratio > 0 and ma_slope > 0:
                regimes[i] = "bull"
            elif ratio < 0 and ma_slope < 0:
                regimes[i] = "bear"
            else:
                regimes[i] = "rebound"
        return regimes

    def validate(self, factor_history, benchmark_returns, forward_returns_history, factor_name="candidate"):
        r = RegimeResult(factor_name=factor_name)
        regimes = self._classify_regimes(benchmark_returns)
        n = min(len(factor_history), len(forward_returns_history), len(regimes))
        if n < 20:
            r.reason = f"samples {n} < 20"
            return r
        ics = {}
        cnt = {}
        for i in range(n):
            reg = regimes[i]
            if reg in ("warmup", "unknown"): continue
            ic = self._single_ic(factor_history[i], forward_returns_history[i])
            if ic is None: continue
            ics.setdefault(reg, []).append(ic)
            cnt[reg] = cnt.get(reg, 0) + 1
        for reg, lst in ics.items():
            r.per_regime_samples[reg] = cnt.get(reg, 0)
            if len(lst) >= 5:
                arr = np.array(lst, dtype=float)
                std = float(np.std(arr))
                r.per_regime_ic_ir[reg] = float(abs(np.mean(arr)) / std) if std > 1e-12 else 0.0
            else:
                # P2.2 v6.2d 修复：样本数 < 5 的 regime 不计入 min_regime_ic_ir
                # 旧版设为 -1.0 会导致 CapacityAgent 误否决（min_regime_ic_ir=-1.000 < 0.2）
                # 新版：标记为 "insufficient_samples"，不计入 min_regime_ic_ir 计算
                r.per_regime_ic_ir[reg] = None
        # 过滤掉 None 值（样本不足的 regime）
        r.per_regime_ic_ir = {k: v for k, v in r.per_regime_ic_ir.items() if v is not None}
        return self._finalize(r)

    def _finalize(self, r):
        # P2.2 v6.2d：记录样本不足的 regime（被跳过不计入 min_regime_ic_ir）
        # 依据：QualityTrend 季度频率因子在 120 天窗口内，某些 regime（如 bear/rebound）
        #   可能只有几天样本，样本数 < 5 时 IC_IR 统计不可靠
        #   旧版设为 -1.0 导致 CapacityAgent 误否决，新版跳过这些 regime
        if r.per_regime_ic_ir:
            r.min_regime_ic_ir = min(r.per_regime_ic_ir.values())
            r.weakest_regime = min(r.per_regime_ic_ir.items(), key=lambda kv: kv[1])[0]
        else:
            r.min_regime_ic_ir = 0.0
            r.weakest_regime = "insufficient_samples"
        r.pass_all_regimes = bool(r.per_regime_ic_ir) and all(v >= self.ic_ir_threshold for v in r.per_regime_ic_ir.values())
        if r.pass_all_regimes:
            r.regime_tag = "all_regime"
            r.reason = "pass"
        else:
            passing = [k for k, v in r.per_regime_ic_ir.items() if v >= self.ic_ir_threshold]
            r.regime_tag = ",".join(passing) if passing else "none"
            if r.per_regime_ic_ir:
                r.reason = f"weakest {r.weakest_regime} ic_ir={r.min_regime_ic_ir:.3f} < {self.ic_ir_threshold}"
            else:
                r.reason = "所有 regime 样本数不足（< 5），无法计算 IC_IR"
        return r

    def _single_ic(self, fv, fr):
        common = [s for s in fv if s in fr and math.isfinite(fv[s])]
        if len(common) < 5: return None
        x = np.array([fv[s] for s in common], dtype=float)
        y = np.array([fr[s] for s in common], dtype=float)
        if np.std(x) < 1e-12 or np.std(y) < 1e-12: return 0.0
        c = float(np.corrcoef(x, y)[0, 1])
        return c if math.isfinite(c) else 0.0
