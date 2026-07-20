"""
v7.5 SignalGenerator — 多因子 Alpha 信号生成
基于 QUANT_RESEARCH_MEMO_v7.5_INSTITUTIONAL §6 (src/alpha/)
采用 LASSO (L1) 特征选择 + 岭回归 (L2) 权重优化
"""
import numpy as np
import pandas as pd
import logging
from typing import Optional, Tuple, List, Dict
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

    防未来函数:
    - forward_returns 必须为 t+1 (已 shift(-1)) 的前向收益
    - 训练窗口仅使用 as_of_date 及之前的数据
    """

    def __init__(self, factor_library,
                 lasso_alpha_range: Tuple[float, ...] = (0.001, 0.01, 0.1, 1.0, 10.0),
                 ridge_alpha_range: Tuple[float, ...] = (0.1, 1.0, 10.0, 100.0),
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

        self.selected_factors: List[str] = []
        self.factor_weights: Optional[np.ndarray] = None
        self.latest_signals: Dict[str, float] = {}
        self._forward_returns_warned = False

    # ---------- 特征选择 ----------
    def select_features(self, X: pd.DataFrame, y: pd.Series,
                        as_of_date: Optional[pd.Timestamp] = None) -> List[str]:
        """
        LASSO L1 特征选择

        Args:
            X: 因子矩阵 [T x K]
            y: 前向收益（目标，必须已 shift(-1)）
            as_of_date: 截止日期，仅使用该日期及之前的数据（防未来函数）

        Returns:
            被选中的因子名称列表
        """
        if X.empty or y.empty:
            logger.warning("因子矩阵或目标为空，无法进行特征选择")
            return list(X.columns)

        # 防未来函数: 截断到 as_of_date
        if as_of_date is not None:
            X = X.loc[:as_of_date]
            y = y.loc[:as_of_date]

        # 对齐
        common_idx = X.dropna().index.intersection(y.dropna().index)
        if len(common_idx) < self.lookback // 4:
            logger.warning(f"数据不足: {len(common_idx)} < {self.lookback // 4}")
            return list(X.columns)

        X_aligned = X.loc[common_idx].tail(self.lookback)
        y_aligned = y.loc[common_idx].tail(self.lookback)

        # 标准化
        X_scaled = self.scaler.fit_transform(X_aligned)

        # LASSO
        lasso = LassoCV(alphas=list(self.lasso_alpha_range), cv=5, max_iter=5000, random_state=42)
        lasso.fit(X_scaled, y_aligned)

        # 选出非零系数
        selected = X.columns[np.abs(lasso.coef_) > 1e-6].tolist()
        logger.info(f"LASSO 选出 {len(selected)}/{len(X.columns)} 个因子: \n{selected[:10]}...")
        return selected

    # ---------- 权重估计 ----------
    def estimate_weights(self, X: pd.DataFrame, y: pd.Series,
                         as_of_date: Optional[pd.Timestamp] = None) -> np.ndarray:
        """
        Ridge L2 权重估计

        Args:
            X: 因子矩阵（仅选中因子）
            y: 前向收益（目标，必须已 shift(-1)）
            as_of_date: 截止日期，防未来函数

        Returns:
            权重向量
        """
        if X.empty or y.empty:
            return np.ones(X.shape[1]) / max(X.shape[1], 1)

        # 防未来函数: 截断到 as_of_date
        if as_of_date is not None:
            X = X.loc[:as_of_date]
            y = y.loc[:as_of_date]

        common_idx = X.dropna().index.intersection(y.dropna().index)
        if len(common_idx) < 20:
            return np.ones(X.shape[1]) / max(X.shape[1], 1)

        X_aligned = X.loc[common_idx].tail(self.lookback)
        y_aligned = y.loc[common_idx].tail(self.lookback)
        X_scaled = self.scaler.fit_transform(X_aligned)

        ridge = RidgeCV(alphas=list(self.ridge_alpha_range), cv=5)
        ridge.fit(X_scaled, y_aligned)

        logger.info(f"Ridge 完成权重估计，alpha={ridge.alpha_:.4f}")
        return ridge.coef_

    # ---------- 信号生成 ----------
    def _validate_forward_returns(self, y: pd.Series) -> bool:
        """
        验证 forward_returns 是否已正确偏移为 t+1。

        检查方法: 计算 y 与 shift(-1) 后的 y 的相关系数。
        如果 forward_returns 未做 shift(-1)，则 y 与 shift(-1)(y) 的
        自相关会非常高（因为包含同期信息），此时应发出警告。

        Returns:
            True 如果通过验证
        """
        if self._forward_returns_warned:
            return True

        if len(y) < 30:
            return True

        # 如果 forward_returns 是 shift(-1) 的，当前值应该与下一期值不相关
        y_clean = y.dropna()
        if len(y_clean) < 30:
            return True

        from scipy.stats import pearsonr
        idx = y_clean.index[:min(200, len(y_clean))]
        corr, pval = pearsonr(y_clean.loc[idx], y_clean.loc[idx].shift(-1).dropna())

        if corr > 0.8 and pval < 0.01:
            logger.warning(
                "⚠️ 数据泄露风险: forward_returns 自相关过高 (r=%.4f, p=%.6f)。"
                "forward_returns 可能未做 shift(-1) 偏移，训练可能使用了同期数据。"
                "请确保传入的是 t+1 前向收益。",
                corr, pval
            )
            self._forward_returns_warned = True
            return False

        self._forward_returns_warned = True
        return True

    def generate(self, factor_matrix: Optional[pd.DataFrame] = None,
                 forward_returns: Optional[pd.Series] = None,
                 retrain: bool = True,
                 as_of_date: Optional[pd.Timestamp] = None) -> pd.Series:
        """
        生成 Alpha 信号

        Args:
            factor_matrix: 因子矩阵，若为 None 则从 FactorLibrary 获取
            forward_returns: 前向收益用于训练（必须已 shift(-1) 为 t+1 收益）
            retrain: 是否重新训练 LASSO + Ridge
            as_of_date: 截止日期，仅使用该日期及之前的数据（防未来函数）

        Returns:
            signal Series: [T] 标准化信号 [-1, 1]
        """
        if factor_matrix is None:
            factor_matrix = self.factor_lib.get_factor_matrix()

        if factor_matrix is None or factor_matrix.empty:
            logger.error("因子矩阵为空，无法生成信号")
            return pd.Series(0.0, index=[0])

        # 特征选择 + 权重
        if retrain and forward_returns is not None:
            self._validate_forward_returns(forward_returns)
            self.selected_factors = self.select_features(
                factor_matrix, forward_returns, as_of_date=as_of_date
            )
            if self.selected_factors:
                X_selected = factor_matrix[self.selected_factors]
                self.factor_weights = self.estimate_weights(
                    X_selected, forward_returns, as_of_date=as_of_date
                )
            else:
                self.selected_factors = list(factor_matrix.columns)
                self.factor_weights = np.ones(len(self.selected_factors)) / len(self.selected_factors)
        else:
            if not self.selected_factors:
                self.selected_factors = list(factor_matrix.columns)
            if self.factor_weights is None:
                self.factor_weights = np.ones(len(self.selected_factors)) / len(self.selected_factors)

        # 计算信号
        X = factor_matrix[self.selected_factors].fillna(0)
        raw_signal = X.values @ self.factor_weights

        # 标准化至 [-1, 1]
        signal_max = np.abs(raw_signal).max()
        if signal_max > 0:
            normalized = raw_signal / signal_max
        else:
            normalized = raw_signal

        signal = pd.Series(np.clip(normalized, -1, 1), index=X.index)

        # 更新最新信号
        self.latest_signals = {col: signal[col].iloc[-1] if len(signal) > 0 else 0
                               for col in factor_matrix.columns}

        logger.info(f"信号生成完成: {len(self.selected_factors)} 个因子, "
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
        for _, group in forward_returns.loc[common].groupby(pd.Grouper(freq='M')):
            idx = group.index.intersection(common)
            if len(idx) > 5:
                ic_monthly.append(group.loc[idx].corr(signal.loc[idx]))

        ic_vals = [ic for ic in ic_monthly if not np.isnan(ic)]
        ir = np.mean(ic_vals) / max(np.std(ic_vals), 1e-8) if ic_vals else 0.0

        return {'ic': ic_series, 'ir': ir, 'ic_monthly': ic_monthly}
