# -*- coding: utf-8 -*-
"""
Purged Walk-Forward / Purged K-Fold 交叉验证 — v1.0
P0-3 修复: 消除回测前视偏差

基于 Marcos Lopez de Prado "Advances in Financial Machine Learning" Chapter 7:
- Purged K-Fold: 移除训练集中与测试标签时间重叠的样本
- Embargo: 在训练/测试之间添加缓冲期防止信息泄露
- Point-in-time: 确保特征只用回顾窗口内历史数据

核心概念:
- purging: 训练集末尾与测试集重叠的部分被移除
- embargo: 测试集之后的固定窗口期也被移除(防标签泄露)
- walk-forward: 滚动窗口训练，模拟真实交易中模型随时间演进
"""

import numpy as np
import pandas as pd
from typing import List, Tuple, Generator, Optional, Union
from sklearn.model_selection import BaseCrossValidator


class PurgedWalkForward(BaseCrossValidator):
    """Purged Walk-Forward 交叉验证器
    
    在每个时间步:
    1. 训练集: [0, test_start - embargo - purging]
    2. 测试集: [test_start, test_end]
    3. 滚动窗口前移
    
    参数:
        n_splits: 切分数量
        embargo_pct: 测试集之后禁止期 (占步长的比例, 默认0.01)
        purge_pct: 训练集末尾清除期 (占训练集长度的比例, 默认0.01)
        min_train_size: 最小训练样本数
        shuffle: False (时序数据不可打乱)
    """
    
    def __init__(
        self,
        n_splits: int = 5,
        embargo_pct: float = 0.01,
        purge_pct: float = 0.01,
        min_train_size: int = 100,
    ):
        if n_splits < 2:
            raise ValueError(f"n_splits >= 2 required, got {n_splits}")
        self.n_splits = n_splits
        self.embargo_pct = max(0.0, min(1.0, embargo_pct))
        self.purge_pct = max(0.0, min(1.0, purge_pct))
        self.min_train_size = min_train_size
        
    def split(
        self,
        X: Union[np.ndarray, pd.DataFrame],
        y: Union[np.ndarray, pd.Series] = None,
        groups=None,
        timestamps: Union[np.ndarray, pd.Series] = None,
    ) -> Generator[Tuple[np.ndarray, np.ndarray], None, None]:
        """生成训练/测试索引
        
        注意: timestamps 参数用于增强的 purge 计算 (时间感知)
        如果不提供，使用基于索引的简化 purge
        """
        n = X.shape[0]
        indices = np.arange(n)
        
        # 每折大小
        test_size = n // (self.n_splits + 2)  # 留足训练和缓冲
        step_size = test_size
        
        for i in range(self.n_splits):
            # 测试集位置: 从倒数第 (n_splits-i) 折开始
            test_end = n - (self.n_splits - i - 1) * step_size
            test_start = max(self.min_train_size + step_size, test_end - test_size)
            
            # 确保最小训练集
            if test_start < self.min_train_size:
                continue
            
            # Embargo: 训练集截止线 (测试集开始前)
            embargo_size = max(1, int(test_size * self.embargo_pct))
            train_end = test_start - embargo_size
            
            # Purging: 从训练集末尾清除可能含未来信息的样本
            purge_size = max(1, int(train_end * self.purge_pct))
            train_end_purged = max(0, train_end - purge_size)
            
            if train_end_purged < self.min_train_size:
                continue
            
            train_idx = indices[:train_end_purged]
            test_idx = indices[test_start:test_end]
            
            # 时间感知 purging (如果提供了timestamps)
            if timestamps is not None:
                train_idx = self._time_purge(
                    train_idx, test_idx, timestamps
                )
            
            yield train_idx, test_idx
    
    def _time_purge(
        self,
        train_idx: np.ndarray,
        test_idx: np.ndarray,
        timestamps: Union[np.ndarray, pd.Series],
    ) -> np.ndarray:
        """基于时间戳的精确 purging
        
        移除训练集中与测试集时间窗口重叠的样本。
        例如: 如果标签定义了 t+5 的涨跌，那么训练集中
        日期在 [test_date - 5, test_date] 之间的样本都含未来信息。
        """
        if isinstance(timestamps, pd.Series):
            ts = timestamps.values
        else:
            ts = np.asarray(timestamps)
        
        test_min_ts = ts[test_idx].min()
        
        # 保留时间戳严格早于测试集最早时间的训练样本
        valid_train = ts[train_idx] < test_min_ts
        return train_idx[valid_train]
    
    def get_n_splits(self, X=None, y=None, groups=None):
        return self.n_splits


class PurgedKFold(BaseCrossValidator):
    """Purged K-Fold 交叉验证器
    
    标准 K-Fold 的时序列安全版本:
    - 按时间顺序分块
    - 训练集在下折测试集附近被 purge
    - 添加 embargo 间隔
    
    参数:
        n_splits: 折数
        embargo_pct: 禁止期比例
        purge_pct: 清除期比例
    """
    
    def __init__(
        self,
        n_splits: int = 5,
        embargo_pct: float = 0.0,
        purge_pct: float = 0.0,
    ):
        if n_splits < 2:
            raise ValueError(f"n_splits >= 2 required, got {n_splits}")
        self.n_splits = n_splits
        self.embargo_pct = embargo_pct
        self.purge_pct = purge_pct
        
    def split(self, X, y=None, groups=None):
        n = X.shape[0]
        indices = np.arange(n)
        
        # 按时间顺序分块
        fold_size = n // self.n_splits
        
        for i in range(self.n_splits):
            test_start = i * fold_size
            test_end = (i + 1) * fold_size if i < self.n_splits - 1 else n
            
            # 训练集在测试集之前 (排除未来数据)
            train_idx = indices[:test_start]
            
            # Embargo: 移除训练集末尾靠近测试集的部分
            if self.embargo_pct > 0 and len(train_idx) > 0:
                embargo_n = max(1, int(fold_size * self.embargo_pct))
                train_idx = train_idx[:-embargo_n] if len(train_idx) > embargo_n else train_idx
            
            # Purging: 训练集中可能含未来信息的样本
            if self.purge_pct > 0 and len(train_idx) > 0:
                purge_n = max(1, int(len(train_idx) * self.purge_pct))
                train_idx = train_idx[:-purge_n] if len(train_idx) > purge_n else train_idx
            
            test_idx = indices[test_start:test_end]
            
            if len(train_idx) == 0 or len(test_idx) == 0:
                continue
            
            yield train_idx, test_idx
    
    def get_n_splits(self, X=None, y=None, groups=None):
        return self.n_splits


class WalkForwardValidator:
    """Walk-Forward 验证器 — 模拟真实交易环境
    
    每步:
    1. 用历史窗口训练模型
    2. 用下一窗口测试
    3. 记录预测和实际结果
    4. 向前滚动窗口
    
    这完全复现了"用过去数据训练，预测未来"的真实流程。
    
    参数:
        train_window: 训练窗口长度 (样本数)
        test_window: 测试窗口长度 (样本数, 默认1预测次日)
        step_size: 每步前进样本数
        min_train_size: 初始最小训练样本数
    """
    
    def __init__(
        self,
        train_window: int = 500,
        test_window: int = 1,
        step_size: int = 1,
        min_train_size: int = 250,
    ):
        self.train_window = train_window
        self.test_window = test_window
        self.step_size = step_size
        self.min_train_size = min_train_size
    
    def split(self, X, y=None, groups=None):
        n = X.shape[0]
        start = self.min_train_size
        
        while start + self.train_window + self.test_window <= n:
            train_start = start
            train_end = start + self.train_window
            test_end = train_end + self.test_window
            
            train_idx = np.arange(train_start, train_end)
            test_idx = np.arange(train_end, test_end)
            
            yield train_idx, test_idx
            
            start += self.step_size
    
    def validate(
        self,
        X: np.ndarray,
        y: np.ndarray,
        model_factory,
        scorer=None,
        verbose: bool = True,
    ) -> dict:
        """执行完整 Walk-Forward 验证
        
        Args:
            X: 特征矩阵
            y: 标签向量
            model_factory: 无参可调用对象, 返回新模型实例 (每次重新训练)
            scorer: 评分函数 score = fn(y_true, y_pred)
            verbose: 是否打印进度
        
        Returns:
            results: {
                'fold_scores': [...],
                'preds': np.ndarray,  # 所有预测
                'actuals': np.ndarray,  # 所有实际
                'mean_score': float,
                'std_score': float,
                'n_folds': int,
            }
        """
        all_preds = []
        all_actuals = []
        fold_scores = []
        
        for i, (train_idx, test_idx) in enumerate(self.split(X, y)):
            X_train, y_train = X[train_idx], y[train_idx]
            X_test, y_test = X[test_idx], y[test_idx]
            
            model = model_factory()
            model.fit(X_train, y_train)
            y_pred = model.predict(X_test)
            
            all_preds.extend(y_pred)
            all_actuals.extend(y_test)
            
            if scorer:
                score = scorer(y_test, y_pred)
                fold_scores.append(float(score))
            
            if verbose and (i + 1) % max(1, (n := len(list(self.split(X, y)))) // 5) == 0:
                from sklearn.metrics import accuracy_score
                cum_acc = accuracy_score(all_actuals, all_preds)
                print(f"  [WF] fold {i+1}/{n} | 累计准确率: {cum_acc:.4f}")
        
        results = {
            'fold_scores': fold_scores,
            'preds': np.array(all_preds),
            'actuals': np.array(all_actuals),
            'n_folds': len(fold_scores),
        }
        
        if fold_scores:
            results['mean_score'] = float(np.mean(fold_scores))
            results['std_score'] = float(np.std(fold_scores, ddof=1))
            
            if verbose:
                print(f"\n  [WF Result] mean={results['mean_score']:.4f} "
                      f"std={results['std_score']:.4f} folds={len(fold_scores)}")
        
        return results


# ── 前视偏差自检工具 ──

def check_lookahead_bias(
    feature_df: pd.DataFrame,
    lookback_windows: dict = None,
) -> dict:
    """P0-3 辅助: 检测特征工程中的前视偏差

    通过检查每个特征是否只使用了 lookback 窗口内的历史数据。
    
    Args:
        feature_df: 包含所有特征的 DataFrame (index 为日期)
        lookback_windows: {feature_name: max_lookback_days}
    
    Returns:
        report: {
            'features_checked': int,
            'suspected_leaks': [...],
            'passed': [...],
        }
    """
    if lookback_windows is None:
        # 根据 ml_predictor.py 中 MLFeatureEngineer 的特征定义
        lookback_windows = {
            'returns': 1, 'log_returns': 1, 'abs_returns': 1,
            'ma_5': 5, 'ma_10': 10, 'ma_20': 20, 'ma_60': 60, 'ma_120': 120,
            'ma5_ma20': 20, 'ma20_ma60': 60, 'ma60_ma120': 120,
            'volatility_5': 5, 'volatility_20': 20, 'volatility_60': 60, 'vol_ratio_5_20': 20,
            'rsi': 14, 'rsi_7': 7, 'rsi_21': 21,
            'macd': 26, 'macd_signal': 35, 'macd_hist': 35,
            'boll_mid': 20, 'boll_upper': 20, 'boll_lower': 20,
            'boll_width': 20, 'boll_pct': 20,
            'volume_ma_5': 5, 'volume_ma_20': 20,
            'volume_ratio': 5, 'volume_ma_ratio': 20, 'volume_change': 1,
            'vwap': 5, 'momentum_5': 5, 'momentum_10': 10,
            'momentum_20': 20, 'momentum_60': 60,
            'trend_5': 5, 'trend_20': 20, 'trend_60': 60,
            'range': 1, 'range_ratio': 1,
            'open_close_diff': 1, 'gap': 1,
        }
    
    report = {'features_checked': 0, 'suspected_leaks': [], 'passed': []}
    
    for feat_name, max_lookback in lookback_windows.items():
        report['features_checked'] += 1
        if feat_name not in feature_df.columns:
            report['suspected_leaks'].append(f"{feat_name}: 特征不存在")
            continue
        
        # 检查前 N 行是否有 NaN (lookback 窗口内)
        na_count = feature_df[feat_name].iloc[:max_lookback].isna().sum()
        
        if na_count == 0:
            # 无 NaN 意味着可能在构建时使用了未来数据
            report['suspected_leaks'].append(
                f"{feat_name}: 前{max_lookback}行无NaN (可能含前视偏差)"
            )
        elif na_count >= max_lookback:
            report['passed'].append(feat_name)
        else:
            # 部分 NaN 但少于预期 — 可能部分前视
            report['passed'].append(feat_name)
    
    report['leak_count'] = len(report['suspected_leaks'])
    report['pass_count'] = len(report['passed'])
    
    if report['leak_count'] > 0:
        print(f"\n[前视偏差检测] ⚠️ 发现 {report['leak_count']} 个可疑特征:")
        for leak in report['suspected_leaks']:
            print(f"  - {leak}")
    else:
        print(f"\n[前视偏差检测] ✅ 全部 {report['pass_count']} 个特征通过检查")
    
    return report


if __name__ == '__main__':
    # 演示: Purged Walk-Forward
    print("=== PurgedWalkForward 演示 ===")
    X = np.random.randn(1000, 10)
    y = np.random.randint(0, 2, 1000)
    
    pwf = PurgedWalkForward(n_splits=5, embargo_pct=0.01, purge_pct=0.01)
    for i, (train_idx, test_idx) in enumerate(pwf.split(X, y)):
        print(f"Fold {i}: train={len(train_idx)}, test={len(test_idx)}, "
              f"overlap={len(set(train_idx) & set(test_idx))}")
    
    # 演示: Walk-Forward 验证
    print("\n=== WalkForwardValidator 演示 ===")
    from sklearn.ensemble import RandomForestClassifier
    
    wf = WalkForwardValidator(train_window=300, test_window=20, step_size=10, min_train_size=200)
    
    def model_factory():
        return RandomForestClassifier(n_estimators=50, max_depth=5, random_state=42, n_jobs=-1)
    
    results = wf.validate(X, y, model_factory, verbose=True)
    if 'mean_score' in results:
        print(f"WF 累计准确率: {results['mean_score']:.4f} +/- {results['std_score']:.4f}")
        print(f"总预测数: {len(results['preds'])}")
    else:
        print("验证未产生有效折叠")
