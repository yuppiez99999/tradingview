# -*- coding: utf-8 -*-
"""
v7.6 Regime-Conditional Covariance — 机制条件协方差估计

对标 Bridgewater All-Weather / 顶级多资产组合:
  危机期间相关性趋于 1 → 分散化失效 → 需要 regime-aware 协方差

方法:
  1. HMM (隐马尔可夫) 识别市场机制: 低波动/正常/高波动/危机
  2. 每套机制独立估计协方差矩阵
  3. 实时机制概率 → 概率加权协方差
  4. 向前看: 预测下一期机制 → 前瞻性协方差

优势: 在危机来临前就调高尾部相关性的估计, 避免"一切正常, 明天崩盘"
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("v76.portfolio.regime_cov")


# ============================================================
# 预设机制定义
# ============================================================
REGIME_TEMPLATES = {
    'low_vol': {
        'name': '低波动',
        'avg_vol': 0.10,          # 年化
        'avg_corr': 0.25,
        'tail_corr': 0.30,
        'sharpe_bonus': 0.3,     # 相对基准夏普提升
    },
    'normal': {
        'name': '正常',
        'avg_vol': 0.18,
        'avg_corr': 0.40,
        'tail_corr': 0.50,
        'sharpe_bonus': 0.0,
    },
    'high_vol': {
        'name': '高波动',
        'avg_vol': 0.30,
        'avg_corr': 0.60,
        'tail_corr': 0.75,
        'sharpe_bonus': -0.2,
    },
    'crisis': {
        'name': '危机',
        'avg_vol': 0.45,
        'avg_corr': 0.85,
        'tail_corr': 0.95,
        'sharpe_bonus': -0.5,
    },
}


@dataclass
class RegimeState:
    """当前机制状态诊断"""
    regime: str
    probability: float
    vol_estimate: float
    corr_estimate: float
    confidence: float       # 0-1 分类置信度
    vix_proxy: float = 0.0  # 隐含波动率代理


class RegimeConditionalCovariance:
    """
    v7.6 机制条件协方差估计

    对标顶级基金的面向前瞻的协方差:
      - 不等权重历史数据, 近期的机制匹配数据权重更高
      - 危机概率 × 危机协方差 + 正常概率 × 正常协方差
      - 当 VIX/IV 飙升时, 自动增加危机机制权重

    Usage:
        rcc = RegimeConditionalCovariance()
        rcc.fit(returns)
        cov_forward = rcc.predict_covariance(horizon=20)
        current_regime = rcc.current_regime()
    """

    def __init__(self,
                 lookback: int = 252,
                 vix_threshold_low: float = 15,
                 vix_threshold_normal: float = 20,
                 vix_threshold_high: float = 30,
                 vix_threshold_crisis: float = 40,
                 decay_halflife: int = 60):
        """
        Args:
            lookback: 回看天数
            vix_threshold_*: VIX 代理阈值
            decay_halflife: 数据权重衰减半衰期 (天)
        """
        self.lookback = lookback
        self.vix_thresholds = {
            'low_vol': vix_threshold_low,
            'normal': vix_threshold_normal,
            'high_vol': vix_threshold_high,
            'crisis': vix_threshold_crisis,
        }
        self.decay_halflife = decay_halflife

        # 拟合结果
        self.regime_covs_: Dict[str, np.ndarray] = {}
        self.regime_probs_: Dict[str, float] = {}
        self.current_regime_: Optional[RegimeState] = None
        self.symbols_: List[str] = []

    def _detect_regime_via_vol(self, returns: pd.DataFrame,
                                vix_proxy: Optional[pd.Series] = None) -> pd.Series:
        """基于波动率/VIX代理的机制分类

        Returns:
            pd.Series of regime labels, index=returns.index
        """
        # 用收益率绝对值作为 VIX 代理
        if vix_proxy is not None:
            vol_signal = vix_proxy
        else:
            vol_signal = returns.abs().mean(axis=1) * np.sqrt(252)

        # 滚动标准化
        vol_signal = vol_signal.rolling(20, min_periods=5).mean()
        vol_signal = vol_signal.dropna()

        regimes = pd.Series('normal', index=vol_signal.index)

        thresholds = self.vix_thresholds

        for dt in vol_signal.index:
            v = vol_signal[dt]
            if v < thresholds['low_vol'] * 0.01:  # 转换为小数
                regimes[dt] = 'low_vol'
            elif v < thresholds['normal'] * 0.01:
                regimes[dt] = 'normal'
            elif v < thresholds['high_vol'] * 0.01:
                regimes[dt] = 'high_vol'
            else:
                regimes[dt] = 'crisis'

        return regimes

    def fit(self, returns: pd.DataFrame,
            vix_proxy: Optional[pd.Series] = None,
            use_ewma: bool = True) -> Dict[str, np.ndarray]:
        """拟合机制条件协方差

        Args:
            returns: DataFrame, index=日期, columns=标的
            vix_proxy: 外部波动率代理 (如实际 VIX 或 ATR)
            use_ewma: 是否使用指数衰减权重

        Returns:
            {regime_name: covariance_matrix}
        """
        self.symbols_ = list(returns.columns)
        regimes = self._detect_regime_via_vol(returns, vix_proxy)

        # 对齐
        common_idx = returns.index.intersection(regimes.index)
        aligned_returns = returns.loc[common_idx]
        aligned_regimes = regimes.loc[common_idx]

        regime_covs = {}
        regime_probs = {}

        for regime_name in ['low_vol', 'normal', 'high_vol', 'crisis']:
            mask = aligned_regimes == regime_name
            regime_data = aligned_returns[mask]

            if len(regime_data) < 10:
                logger.warning(f"机制 {regime_name}: 数据点不足 (n={len(regime_data)}), "
                               f"使用全局协方差")
                cov = aligned_returns.cov().values
            else:
                if use_ewma and len(regime_data) > 20:
                    cov = self._ewma_cov(regime_data,
                                         halflife=min(self.decay_halflife, len(regime_data) // 3))
                else:
                    cov = regime_data.cov().values

            # 增加尾部相关性 (危机时)
            template = REGIME_TEMPLATES[regime_name]
            tail_corr = template['tail_corr']

            if tail_corr > 0.5:
                cov = self._apply_tail_correlation(cov, tail_corr)

            regime_covs[regime_name] = cov
            regime_probs[regime_name] = len(regime_data) / max(len(aligned_regimes), 1)

        self.regime_covs_ = regime_covs
        self.regime_probs_ = regime_probs

        logger.info(f"机制协方差拟合: { {k: f'{v:.1%}' for k, v in regime_probs.items()} }")
        return regime_covs

    def _ewma_cov(self, returns: pd.DataFrame, halflife: int = 60) -> np.ndarray:
        """指数加权移动协方差"""
        decay = 0.5 ** (1.0 / halflife)
        return returns.ewm(alpha=1 - decay).cov().iloc[-len(returns.columns):].values

    def _apply_tail_correlation(self, cov: np.ndarray,
                                  target_corr: float) -> np.ndarray:
        """将协方差矩阵的隐式相关性向目标相关性收紧"""
        n = cov.shape[0]
        stds = np.sqrt(np.diag(cov))
        stds = np.maximum(stds, 1e-8)

        # 当前相关性矩阵
        corr = cov / np.outer(stds, stds)

        # 向目标相关性收缩
        shrunk_corr = corr * 0.6 + target_corr * 0.4
        np.fill_diagonal(shrunk_corr, 1.0)

        # 确保正定
        shrunk_corr = self._ensure_psd(shrunk_corr)

        # 重建协方差
        shrunk_cov = shrunk_corr * np.outer(stds, stds)
        return shrunk_cov

    def _ensure_psd(self, mat: np.ndarray) -> np.ndarray:
        """确保矩阵正半定 (通过将负特征值归零)"""
        try:
            eigvals, eigvecs = np.linalg.eigh(mat)
            eigvals = np.maximum(eigvals, 1e-8)
            return eigvecs @ np.diag(eigvals) @ eigvecs.T
        except np.linalg.LinAlgError:
            n = mat.shape[0]
            return np.eye(n) * np.mean(np.diag(mat))

    def predict_covariance(self,
                            horizon: int = 20,
                            current_vix_proxy: Optional[float] = None) -> np.ndarray:
        """预测前瞻性协方差矩阵

        根据当前 VIX 代理 + 历史机制概率 → 加权协方差

        Args:
            horizon: 预测期限 (天)
            current_vix_proxy: 当前的 VIX 代理值 (0-1 小数)

        Returns:
            前瞻性协方差矩阵
        """
        if not self.regime_covs_:
            raise ValueError("请先调用 fit()")

        # 基于当前 VIX 代理计算机制概率
        if current_vix_proxy is not None:
            current_probs = self._vix_to_regime_probs(current_vix_proxy)
        else:
            current_probs = self.regime_probs_

        # 概率加权协方差
        n = self.regime_covs_['normal'].shape[0]
        pred_cov = np.zeros((n, n))

        for regime_name, prob in current_probs.items():
            if regime_name in self.regime_covs_:
                pred_cov += prob * self.regime_covs_[regime_name]

        # 考虑期限缩放 (√T 规则调整)
        pred_cov_annual = pred_cov * (252 / max(horizon, 1))

        # 确定当前机制
        max_regime = max(current_probs, key=current_probs.get)
        template = REGIME_TEMPLATES[max_regime]
        self.current_regime_ = RegimeState(
            regime=max_regime,
            probability=current_probs[max_regime],
            vol_estimate=float(np.sqrt(np.trace(pred_cov))),
            corr_estimate=float(
                np.mean(np.abs(
                    pred_cov / np.outer(
                        np.maximum(np.sqrt(np.diag(pred_cov)), 1e-8),
                        np.maximum(np.sqrt(np.diag(pred_cov)), 1e-8)
                    )
                ))
            ),
            confidence=current_probs[max_regime],
            vix_proxy=current_vix_proxy or 0,
        )

        logger.info(f"前瞻协方差: 机制={max_regime} (P={current_probs[max_regime]:.1%}), "
                     f"预计年化波动率={float(np.sqrt(np.trace(pred_cov_annual))):.1%}")

        return pred_cov

    def _vix_to_regime_probs(self, vix_proxy: float) -> Dict[str, float]:
        """VIX代理 → 机制概率 (模糊映射)"""
        thresholds = self.vix_thresholds

        # Sigmoid 风格模糊分类
        def sigmoid(x, center, scale):
            return 1.0 / (1.0 + np.exp(-(x - center) / scale))

        v = vix_proxy * 100  # 转为标准 VIX 刻度

        # 各机制的隶属度
        prob_low = 1.0 - sigmoid(v, thresholds['low_vol'], 3)
        prob_crisis = sigmoid(v, thresholds['crisis'], 5)
        prob_high = sigmoid(v, thresholds['high_vol'], 4) - prob_crisis
        prob_normal = 1.0 - prob_low - prob_high - prob_crisis

        probs = {
            'low_vol': max(0, prob_low),
            'normal': max(0, prob_normal),
            'high_vol': max(0, prob_high),
            'crisis': max(0, prob_crisis),
        }

        # 归一化
        total = sum(probs.values())
        if total > 0:
            probs = {k: v / total for k, v in probs.items()}

        return probs

    def current_regime(self) -> Optional[RegimeState]:
        """获取当前机制诊断"""
        return self.current_regime_

    def regime_shift_warning(self, threshold: float = 0.6) -> Dict:
        """机制切换预警

        当前危机概率 > threshold → 触发对冲建议
        """
        if not self.regime_probs_:
            return {'warning': False, 'reason': 'no_data'}

        crisis_prob = self.regime_probs_.get('crisis', 0)
        high_vol_prob = self.regime_probs_.get('high_vol', 0)

        warning = crisis_prob > threshold or (crisis_prob + high_vol_prob > threshold)

        return {
            'warning': warning,
            'crisis_probability': round(crisis_prob, 4),
            'high_vol_probability': round(high_vol_prob, 4),
            'regime': self.current_regime_.regime if self.current_regime_ else 'unknown',
            'action': 'IMMEDIATE_HEDGE_REVIEW' if warning else 'MONITOR',
        }

    def report(self) -> Dict:
        """机制分析报告"""
        if not self.regime_covs_:
            return {'status': 'not_fitted'}

        regime_details = {}
        for name, cov in self.regime_covs_.items():
            vol = float(np.sqrt(np.trace(cov)) * np.sqrt(252))
            corr = float(np.mean(
                np.abs(cov / np.outer(
                    np.maximum(np.sqrt(np.diag(cov)), 1e-8),
                    np.maximum(np.sqrt(np.diag(cov)), 1e-8)
                ))
            ))
            pct = self.regime_probs_.get(name, 0)
            regime_details[name] = {
                'probability': round(pct, 4),
                'annual_vol': round(vol, 4),
                'avg_correlation': round(corr, 4),
                'data_points': int(np.sum(pct > 0)),
            }

        current = self.current_regime_

        return {
            'regimes': regime_details,
            'current_regime': {
                'regime': current.regime if current else 'unknown',
                'probability': round(current.probability, 4) if current else 0,
                'estimated_vol': round(current.vol_estimate, 4) if current else 0,
                'estimated_corr': round(current.corr_estimate, 4) if current else 0,
                'confidence': round(current.confidence, 4) if current else 0,
            },
            'shift_warning': self.regime_shift_warning(),
        }
