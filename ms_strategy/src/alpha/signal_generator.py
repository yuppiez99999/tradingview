"""
v7.5 SignalGenerator — 多因子 Alpha 信号生成
基于 QUANT_RESEARCH_MEMO_v7.5_INSTITUTIONAL §6 (src/alpha/)
采用 LASSO (L1) 特征选择 + 岭回归 (L2) 权重优化
"""
import logging
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.linear_model import LassoCV, RidgeCV
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger(__name__)


class SignalGenerator:
    """
    v7.5 Alpha 信号生成器

    流程：
    1. 从 FactorLibrary 获取因子矩阵
    2. LASSO (L1) 特征选择
    3. Ridge (L2) 权重估计
    4. 生成标准化信号 [-1, 1]
    """

    # P1-2: 资产类别 → 优先因子映射
    ASSET_FACTOR_PRIORITY: dict[str, list[str]] = {
        "STOCK": [
            "momentum", "volatility", "value", "quality", "size",
            "liquidity", "reversal", "growth", "leverage", "profitability",
        ],
        "ETF": [
            "momentum", "volatility", "liquidity", "trend", "flow",
        ],
        "FUTURE": [
            "momentum", "carry", "basis", "term_structure", "volatility",
            "open_interest", "liquidity",
        ],
        "OPTION": [
            "implied_volatility", "skew", "term_structure", "put_call_ratio",
            "gamma", "vega", "theta",
        ],
    }

    def __init__(self, factor_library,
                 lasso_alpha_range: tuple[float, ...] = (0.001, 0.01, 0.1, 1.0, 10.0),
                 ridge_alpha_range: tuple[float, ...] = (0.1, 1.0, 10.0, 100.0),
                 lookback: int = 252):
        """
        Args:
            factor_library: FactorLibrary 实例
            lasso_alpha_range: LASSO 正则化参数网格
            ridge_alpha_range: Ridge 正则化参数网格
            lookback: 训练窗口（交易日）
        """
        self.factor_lib = factor_library
        self.lasso_alpha_range = lasso_alpha_range
        self.ridge_alpha_range = ridge_alpha_range
        self.lookback = lookback
        self.scaler = StandardScaler()

        self.selected_factors: list[str] = []
        self.factor_weights: Optional[np.ndarray] = None
        self.latest_signals: dict[str, float] = {}

    # ---------- 特征选择 ----------
    def select_features(self, X: pd.DataFrame, y: pd.Series) -> list[str]:  # noqa: N803
        """
        LASSO L1 特征选择

        Args:
            X: 因子矩阵 [T x K]
            y: 前向收益（目标）

        Returns:
            被选中的因子名称列表
        """
        if X.empty or y.empty:
            logger.warning("因子矩阵或目标为空，无法进行特征选择")
            return list(X.columns)

        # 对齐
        common_idx = X.dropna().index.intersection(y.dropna().index)
        if len(common_idx) < self.lookback // 4:
            logger.warning(f"数据不足: {len(common_idx)} < {self.lookback // 4}")
            return list(X.columns)

        X_aligned = X.loc[common_idx].tail(self.lookback)  # noqa: N806
        y_aligned = y.loc[common_idx].tail(self.lookback)

        # 标准化
        X_scaled = self.scaler.fit_transform(X_aligned)  # noqa: N806

        # LASSO
        lasso = LassoCV(alphas=list(self.lasso_alpha_range), cv=5, max_iter=5000, random_state=42)
        lasso.fit(X_scaled, y_aligned)

        # 选出非零系数
        selected = X.columns[np.abs(lasso.coef_) > 1e-6].tolist()
        logger.info(f"LASSO 选出 {len(selected)}/{len(X.columns)} 个因子: \n{selected[:10]}...")
        return selected

    # ---------- 权重估计 ----------
    def estimate_weights(self, X: pd.DataFrame, y: pd.Series) -> np.ndarray:  # noqa: N803
        """
        Ridge L2 权重估计

        Args:
            X: 因子矩阵（仅选中因子）
            y: 前向收益

        Returns:
            权重向量
        """
        if X.empty or y.empty:
            return np.ones(X.shape[1]) / max(X.shape[1], 1)

        common_idx = X.dropna().index.intersection(y.dropna().index)
        if len(common_idx) < 20:
            return np.ones(X.shape[1]) / max(X.shape[1], 1)

        X_aligned = X.loc[common_idx].tail(self.lookback)  # noqa: N806
        y_aligned = y.loc[common_idx].tail(self.lookback)
        X_scaled = self.scaler.fit_transform(X_aligned)  # noqa: N806

        ridge = RidgeCV(alphas=list(self.ridge_alpha_range), cv=5)
        ridge.fit(X_scaled, y_aligned)

        logger.info(f"Ridge 完成权重估计，alpha={ridge.alpha_:.4f}")
        return ridge.coef_

    # ---------- 信号生成 ----------
    def generate(self, factor_matrix: Optional[pd.DataFrame] = None,
                 forward_returns: Optional[pd.Series] = None,
                 retrain: bool = True,
                 asset_class: str = "STOCK") -> pd.Series:
        """
        生成 Alpha 信号

        Args:
            factor_matrix: 因子矩阵，若为 None 则从 FactorLibrary 获取
            forward_returns: 前向收益用于训练
            retrain: 是否重新训练 LASSO + Ridge
            asset_class: P1-2: 资产类别 (影响因子优先级)

        Returns:
            signal Series: [T] 标准化信号 [-1, 1]
        """
        if factor_matrix is None:
            factor_matrix = self.factor_lib.get_factor_matrix()

        if factor_matrix is None or factor_matrix.empty:
            logger.error("因子矩阵为空，无法生成信号")
            return pd.Series(0.0, index=[0])

        # P1-2: 优先使用资产相关因子
        priority_factors = self.get_asset_factors(asset_class)
        available_factors = list(factor_matrix.columns)
        ordered_factors = [f for f in priority_factors if f in available_factors]
        ordered_factors += [f for f in available_factors if f not in ordered_factors]
        factor_matrix = factor_matrix[ordered_factors]

        # 特征选择 + 权重
        if retrain and forward_returns is not None:
            self.selected_factors = self.select_features(factor_matrix, forward_returns)
            if self.selected_factors:
                X_selected = factor_matrix[self.selected_factors]  # noqa: N806
                self.factor_weights = self.estimate_weights(X_selected, forward_returns)
            else:
                self.selected_factors = list(factor_matrix.columns)
                self.factor_weights = np.ones(len(self.selected_factors)) / len(self.selected_factors)
        else:
            if not self.selected_factors:
                self.selected_factors = list(factor_matrix.columns)
            if self.factor_weights is None:
                self.factor_weights = np.ones(len(self.selected_factors)) / len(self.selected_factors)

        # 计算信号
        X = factor_matrix[self.selected_factors].fillna(0)  # noqa: N806
        raw_signal = X.values @ self.factor_weights

        # 标准化至 [-1, 1]
        signal_max = np.abs(raw_signal).max()
        if signal_max > 0:
            normalized = raw_signal / signal_max
        else:
            normalized = raw_signal

        signal = pd.Series(np.clip(normalized, -1, 1), index=X.index)

        # 更新最新信号
        # G-20260812: signal 是一维组合信号 Series (index = X.index, 非因子列名),
        # 不能用因子名 col 去索引; 取综合信号最新值并按 selected_factors 映射
        latest_val = signal.iloc[-1] if len(signal) > 0 else 0.0
        self.latest_signals = dict.fromkeys(self.selected_factors, latest_val)

        logger.info(f"信号生成完成 [{asset_class}]: {len(self.selected_factors)} 个因子, "
                    f"信号范围 [{signal.min():.3f}, {signal.max():.3f}]")
        return signal

    # ---------- IC 分析 ----------
    def compute_ic(self, signal: pd.Series, forward_returns: pd.Series) -> dict:
        """计算信号 IC (Information Coefficient)"""
        common = signal.dropna().index.intersection(forward_returns.dropna().index)
        if len(common) < 10:
            return {'ic': 0.0, 'ir': 0.0}

        ic_series = forward_returns.loc[common].corr(signal.loc[common])
        ic_monthly = []
        for _, group in forward_returns.loc[common].groupby(pd.Grouper(freq='ME')):
            idx = group.index.intersection(common)
            if len(idx) > 5:
                ic_monthly.append(group.loc[idx].corr(signal.loc[idx]))

        ic_vals = [ic for ic in ic_monthly if not np.isnan(ic)]
        ir = np.mean(ic_vals) / max(np.std(ic_vals), 1e-8) if ic_vals else 0.0

        return {'ic': ic_series, 'ir': ir, 'ic_monthly': ic_monthly}

    # ---------- P1-2: 资产类别感知 ----------
    def get_asset_factors(self, asset_class: str = "STOCK") -> list[str]:
        """获取指定资产类别的优先因子列表

        Args:
            asset_class: "STOCK" / "ETF" / "FUTURE" / "OPTION"

        Returns:
            优先因子名称列表
        """
        return self.ASSET_FACTOR_PRIORITY.get(
            asset_class.upper(),
            self.ASSET_FACTOR_PRIORITY["STOCK"],
        )
