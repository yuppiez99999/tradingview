# -*- coding: utf-8 -*-
"""
Optuna 超参数优化训练器 v1.0

替代 auto_train_optimized.py 中的 GridSearchCV，
使用 Optuna 的 TPE (Tree-structured Parzen Estimator) 贝叶斯优化
实现更高效的超参数搜索。

核心改进：
- TPE Sampler + Median Pruner 自动剪枝低效试验
- 参数空间扩大 10-50 倍（不再仅搜索 2 个 depth 值）
- 支持 Walk-Forward 交叉验证
- 支持 Triple Barrier 标签
- 自动选择最佳模型 + 保存
"""

import os
import sys
import json
import glob
import warnings
import numpy as np
import pandas as pd
import joblib
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Any, Callable

warnings.filterwarnings('ignore')

try:
    import optuna
    from optuna.samplers import TPESampler
    from optuna.pruners import MedianPruner
    OPTUNA_AVAILABLE = True
except ImportError:
    OPTUNA_AVAILABLE = False

_has_sklearn = False
try:
    from sklearn.model_selection import TimeSeriesSplit, cross_val_score
    from sklearn.metrics import accuracy_score, f1_score, roc_auc_score, precision_score, recall_score
    from sklearn.ensemble import GradientBoostingClassifier, ExtraTreesClassifier
    from sklearn.linear_model import LogisticRegression
    _has_sklearn = True
except ImportError as e:
    print(f"[WARN] sklearn 导入失败: {e}")
    TimeSeriesSplit = None
    cross_val_score = None
    accuracy_score = None
    f1_score = None
    roc_auc_score = None
    precision_score = None
    recall_score = None
    GradientBoostingClassifier = None
    ExtraTreesClassifier = None
    LogisticRegression = None

_has_xgb = False
try:
    import xgboost as xgb
    _has_xgb = True
except ImportError:
    pass

try:
    import lightgbm as lgb
    _has_lgb = True
except ImportError:
    pass


# ── Walk-Forward 交叉验证工具 ──

def walk_forward_split(X: np.ndarray, y: np.ndarray,
                        n_splits: int = 5,
                        train_min_size: int = None) -> List[Tuple[np.ndarray, np.ndarray]]:
    """
    时序 Walk-Forward 划分。

    每次训练集不断扩展，测试集为下一段数据。
    避免随机划分导致的前瞻偏差（look-ahead bias）。

    Returns:
        [(train_idx, test_idx), ...] 列表
    """
    n = len(X)
    if train_min_size is None:
        train_min_size = n // (n_splits + 1)

    split_size = (n - train_min_size) // n_splits
    folds = []

    for i in range(n_splits):
        train_end = train_min_size + i * split_size
        test_end = min(train_end + split_size, n)
        train_idx = np.arange(0, train_end)
        test_idx = np.arange(train_end, test_end)
        folds.append((train_idx, test_idx))

    return folds


# ── Optuna 训练器 ──

class OptunaModelTrainer:
    """使用 Optuna 进行贝叶斯超参数优化的模型训练器"""

    def __init__(self, model_dir: str = 'models',
                 n_trials: int = 100,
                 cv_splits: int = 5,
                 early_stopping_rounds: int = 50):
        """
        Args:
            model_dir: 模型保存目录
            n_trials: Optuna 试验次数（越大搜索越充分，默认100）
            cv_splits: Walk-Forward 交叉验证折数
            early_stopping_rounds: 早停轮数（仅 XGBoost/LightGBM）
        """
        if not OPTUNA_AVAILABLE:
            raise ImportError(
                "Optuna 未安装。请运行: pip install optuna"
            )

        self.model_dir = model_dir
        self.n_trials = n_trials
        self.cv_splits = cv_splits
        self.early_stopping_rounds = early_stopping_rounds
        self.study_results: Dict[str, Any] = {}

    # ── XGBoost 优化 ──

    def optimize_xgboost(self, X: np.ndarray, y: np.ndarray,
                         feature_names: List[str] = None) -> Dict[str, Any]:
        """使用 Optuna 优化 XGBoost 超参数"""
        if not _has_xgb:
            raise ImportError("XGBoost 未安装: pip install xgboost")

        print("\n  [Optuna] Optimizing XGBoost ...")

        def objective(trial):
            params = {
                'n_estimators': trial.suggest_int('n_estimators', 100, 800),
                'max_depth': trial.suggest_int('max_depth', 2, 12),
                'learning_rate': trial.suggest_float('learning_rate', 0.005, 0.3, log=True),
                'subsample': trial.suggest_float('subsample', 0.5, 1.0),
                'colsample_bytree': trial.suggest_float('colsample_bytree', 0.5, 1.0),
                'colsample_bylevel': trial.suggest_float('colsample_bylevel', 0.5, 1.0),
                'min_child_weight': trial.suggest_int('min_child_weight', 1, 20),
                'gamma': trial.suggest_float('gamma', 0, 5),
                'reg_alpha': trial.suggest_float('reg_alpha', 0, 5),
                'reg_lambda': trial.suggest_float('reg_lambda', 0.1, 10, log=True),
                'scale_pos_weight': trial.suggest_float('scale_pos_weight', 0.5, 3.0),
                'random_state': 42,
                'n_jobs': -1,
                'verbosity': 0,
            }

            # Walk-Forward CV
            scores = []
            folds = walk_forward_split(X, y, n_splits=self.cv_splits)
            for train_idx, test_idx in folds:
                X_train, X_test = X[train_idx], X[test_idx]
                y_train, y_test = y[train_idx], y[test_idx]
                model = xgb.XGBClassifier(**params)
                model.fit(X_train, y_train, verbose=False)
                y_pred = model.predict(X_test)
                scores.append(f1_score(y_test, y_pred, average='binary', zero_division=0))

            return np.mean(scores)

        sampler = TPESampler(seed=42)
        pruner = MedianPruner(n_startup_trials=10, n_warmup_steps=5)
        study = optuna.create_study(
            direction='maximize', sampler=sampler, pruner=pruner
        )
        study.optimize(objective, n_trials=self.n_trials, show_progress_bar=False)

        best_params = study.best_params
        best_params['random_state'] = 42
        best_params['n_jobs'] = -1

        # 用最佳参数训练最终模型
        final_model = xgb.XGBClassifier(**best_params)
        final_model.fit(X, y, verbose=False)

        # 评估
        y_pred = final_model.predict(X)
        y_proba = final_model.predict_proba(X)[:, 1]

        result = {
            'best_params': study.best_params,
            'best_f1_cv': study.best_value,
            'train_accuracy': accuracy_score(y, y_pred),
            'train_auc': roc_auc_score(y, y_proba) if len(set(y)) > 1 else 0.5,
            'n_trials_completed': len(study.trials),
            'n_pruned': sum(1 for t in study.trials if t.state == optuna.trial.TrialState.PRUNED),
        }

        # 保存模型
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        model_path = os.path.join(self.model_dir, f'XGBoost_optuna_{ts}.pkl')
        joblib.dump(final_model, model_path)
        result['model_path'] = model_path

        self.study_results['XGBoost_optuna'] = result
        return result

    # ── LightGBM 优化 ──

    def optimize_lightgbm(self, X: np.ndarray, y: np.ndarray,
                          feature_names: List[str] = None) -> Dict[str, Any]:
        """使用 Optuna 优化 LightGBM 超参数"""
        if not _has_lgb:
            raise ImportError("LightGBM 未安装: pip install lightgbm")

        print("\n  [Optuna] Optimizing LightGBM ...")

        def objective(trial):
            params = {
                'n_estimators': trial.suggest_int('n_estimators', 100, 1000),
                'max_depth': trial.suggest_int('max_depth', 2, 15),
                'num_leaves': trial.suggest_int('num_leaves', 8, 256),
                'learning_rate': trial.suggest_float('learning_rate', 0.005, 0.3, log=True),
                'subsample': trial.suggest_float('subsample', 0.5, 1.0),
                'colsample_bytree': trial.suggest_float('colsample_bytree', 0.5, 1.0),
                'min_child_samples': trial.suggest_int('min_child_samples', 5, 100),
                'min_child_weight': trial.suggest_float('min_child_weight', 1e-5, 1.0, log=True),
                'reg_alpha': trial.suggest_float('reg_alpha', 0, 5),
                'reg_lambda': trial.suggest_float('reg_lambda', 0.1, 10, log=True),
                'subsample_freq': trial.suggest_int('subsample_freq', 0, 10),
                'random_state': 42,
                'n_jobs': -1,
                'verbose': -1,
                'force_col_wise': True,
            }

            scores = []
            folds = walk_forward_split(X, y, n_splits=self.cv_splits)
            for train_idx, test_idx in folds:
                X_train, X_test = X[train_idx], X[test_idx]
                y_train, y_test = y[train_idx], y[test_idx]
                model = lgb.LGBMClassifier(**params)
                model.fit(X_train, y_train)
                y_pred = model.predict(X_test)
                scores.append(f1_score(y_test, y_pred, average='binary', zero_division=0))

            return np.mean(scores)

        sampler = TPESampler(seed=42)
        pruner = MedianPruner(n_startup_trials=10, n_warmup_steps=5)
        study = optuna.create_study(
            direction='maximize', sampler=sampler, pruner=pruner
        )
        study.optimize(objective, n_trials=self.n_trials, show_progress_bar=False)

        best_params = study.best_params
        best_params['random_state'] = 42
        best_params['n_jobs'] = -1
        best_params['verbose'] = -1
        best_params['force_col_wise'] = True

        final_model = lgb.LGBMClassifier(**best_params)
        final_model.fit(X, y)

        y_pred = final_model.predict(X)
        y_proba = final_model.predict_proba(X)[:, 1]

        result = {
            'best_params': study.best_params,
            'best_f1_cv': study.best_value,
            'train_accuracy': accuracy_score(y, y_pred),
            'train_auc': roc_auc_score(y, y_proba) if len(set(y)) > 1 else 0.5,
            'n_trials_completed': len(study.trials),
            'n_pruned': sum(1 for t in study.trials if t.state == optuna.trial.TrialState.PRUNED),
        }

        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        model_path = os.path.join(self.model_dir, f'LightGBM_optuna_{ts}.pkl')
        joblib.dump(final_model, model_path)
        result['model_path'] = model_path

        self.study_results['LightGBM_optuna'] = result
        return result

    # ── GradientBoosting 优化 ──

    def optimize_gradient_boosting(self, X: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
        """使用 Optuna 优化 GradientBoosting 超参数"""
        print("\n  [Optuna] Optimizing GradientBoosting ...")

        def objective(trial):
            params = {
                'n_estimators': trial.suggest_int('n_estimators', 100, 600),
                'max_depth': trial.suggest_int('max_depth', 2, 10),
                'learning_rate': trial.suggest_float('learning_rate', 0.005, 0.3, log=True),
                'subsample': trial.suggest_float('subsample', 0.5, 1.0),
                'min_samples_split': trial.suggest_int('min_samples_split', 2, 20),
                'min_samples_leaf': trial.suggest_int('min_samples_leaf', 1, 20),
                'max_features': trial.suggest_float('max_features', 0.3, 1.0),
                'random_state': 42,
            }

            scores = []
            folds = walk_forward_split(X, y, n_splits=self.cv_splits)
            for train_idx, test_idx in folds:
                X_train, X_test = X[train_idx], X[test_idx]
                y_train, y_test = y[train_idx], y[test_idx]
                model = GradientBoostingClassifier(**params)
                model.fit(X_train, y_train)
                y_pred = model.predict(X_test)
                scores.append(f1_score(y_test, y_pred, average='binary', zero_division=0))

            return np.mean(scores)

        sampler = TPESampler(seed=42)
        pruner = MedianPruner(n_startup_trials=10, n_warmup_steps=5)
        study = optuna.create_study(
            direction='maximize', sampler=sampler, pruner=pruner
        )
        study.optimize(objective, n_trials=min(self.n_trials, 60), show_progress_bar=False)

        best_params = study.best_params
        best_params['random_state'] = 42

        final_model = GradientBoostingClassifier(**best_params)
        final_model.fit(X, y)

        y_pred = final_model.predict(X)
        y_proba = final_model.predict_proba(X)[:, 1]

        result = {
            'best_params': study.best_params,
            'best_f1_cv': study.best_value,
            'train_accuracy': accuracy_score(y, y_pred),
            'train_auc': roc_auc_score(y, y_proba) if len(set(y)) > 1 else 0.5,
            'n_trials_completed': len(study.trials),
            'n_pruned': sum(1 for t in study.trials if t.state == optuna.trial.TrialState.PRUNED),
        }

        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        model_path = os.path.join(self.model_dir, f'GradientBoosting_optuna_{ts}.pkl')
        joblib.dump(final_model, model_path)
        result['model_path'] = model_path

        self.study_results['GradientBoosting_optuna'] = result
        return result

    # ── 全模型优化 ──

    def optimize_all(self, X: np.ndarray, y: np.ndarray,
                     feature_names: List[str] = None) -> Dict[str, Any]:
        """
        批量优化所有支持的模型。

        Returns:
            {
                'results': {模型名: 训练结果},
                'best_model': '最佳模型名',
                'best_f1': 最佳F1分数,
                'label_config': 标签配置信息,
            }
        """
        all_results = {}

        # GradientBoosting（最快，先跑）
        try:
            all_results['GradientBoosting_optuna'] = self.optimize_gradient_boosting(X, y)
        except Exception as e:
            print(f"  ⚠️ GradientBoosting 优化失败: {e}")

        # XGBoost
        if _has_xgb:
            try:
                all_results['XGBoost_optuna'] = self.optimize_xgboost(X, y, feature_names)
            except Exception as e:
                print(f"  ⚠️ XGBoost 优化失败: {e}")

        # LightGBM
        if _has_lgb:
            try:
                all_results['LightGBM_optuna'] = self.optimize_lightgbm(X, y, feature_names)
            except Exception as e:
                print(f"  ⚠️ LightGBM 优化失败: {e}")

        # 基准 ExtraTrees（网格搜索）
        try:
            print("\n  [Benchmark] Using ExtraTrees (GridSearch)...")
            from sklearn.model_selection import GridSearchCV
            et = ExtraTreesClassifier(random_state=42)
            et_params = {
                'n_estimators': [100, 200, 300],
                'max_depth': [5, 10, 15],
                'min_samples_split': [2, 5, 10],
            }
            gs = GridSearchCV(et, et_params, cv=3, scoring='f1', n_jobs=-1)
            gs.fit(X, y)
            best_et = gs.best_estimator_
            y_pred_et = best_et.predict(X)
            y_proba_et = best_et.predict_proba(X)[:, 1]
            all_results['ExtraTrees_grid'] = {
                'best_params': gs.best_params_,
                'best_f1_cv': gs.best_score_,
                'train_accuracy': accuracy_score(y, y_pred_et),
                'train_auc': roc_auc_score(y, y_proba_et) if len(set(y)) > 1 else 0.5,
            }
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            model_path = os.path.join(self.model_dir, f'ExtraTrees_grid_{ts}.pkl')
            joblib.dump(best_et, model_path)
            all_results['ExtraTrees_grid']['model_path'] = model_path
        except Exception as e:
            print(f"  ⚠️ ExtraTrees 训练失败: {e}")

        # 选出最佳模型(基于best_f1_cv,避免选择过拟合的模型)
        best_model_name = None
        best_f1 = -1
        for name, res in all_results.items():
            f1_val = res.get('best_f1_cv', 0)  # 使用CV F1而非train F1
            if f1_val > best_f1:
                best_f1 = f1_val
                best_model_name = name

        # 保存元数据
        meta = {
            'timestamp': datetime.now().strftime('%Y%m%d_%H%M%S'),
            'best_model': best_model_name,
            'best_f1': best_f1,
            'results': all_results,
            'feature_count': X.shape[1],
            'sample_count': X.shape[0],
            'num_classes': len(set(y)),
            'n_trials': self.n_trials,
            'cv_method': 'Walk-Forward',
        }
        if feature_names:
            meta['features'] = feature_names

        meta_path = os.path.join(
            self.model_dir,
            f'training_metadata_optuna_{meta["timestamp"]}.json'
        )
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2, default=str)

        self.study_results['_meta'] = meta
        self.study_results['_meta_path'] = meta_path

        print(f"\n  ✅ 最佳模型: {best_model_name} (F1={best_f1:.4f})")
        print(f"  [Metadata] {os.path.basename(meta_path)}")

        return meta


# ── 便捷入口 ──

def run_optuna_training(data_paths: List[str] = None,
                         model_dir: str = 'models',
                         n_trials: int = 100,
                         use_triple_barrier: bool = False,
                         tb_upper: float = 0.05,
                         tb_lower: float = 0.03,
                         tb_time: int = 10) -> Dict[str, Any]:
    """
    一键运行 Optuna 优化训练。

    Args:
        data_paths: K线数据文件路径列表（.parquet），None 则自动扫描 data/cache/
        model_dir: 模型保存目录
        n_trials: Optuna 试验次数（越大越精细，建议 100-200）
        use_triple_barrier: 是否使用 Triple Barrier 标签
        tb_upper/lower/time: Triple Barrier 参数

    Returns:
        训练元数据
    """
    if not OPTUNA_AVAILABLE:
        print("❌ Optuna 未安装。请运行: pip install optuna")
        return {'error': 'Optuna not installed'}

    # 准备数据目录
    os.makedirs(model_dir, exist_ok=True)

    # 加载数据
    if data_paths is None:
        data_dir = 'data/cache'
        if not os.path.exists(data_dir):
            data_dir = os.path.join(os.path.dirname(__file__), '..', 'data', 'cache')
        data_paths = sorted(glob.glob(os.path.join(data_dir, 'kline_*.parquet')))

    if not data_paths:
        print("❌ 未找到K线数据文件")
        return {'error': 'No data files found'}

    print(f"Loading {len(data_paths)} data files...")
    dfs = []
    for fp in data_paths:
        try:
            df = pd.read_parquet(fp)
            dfs.append(df)
        except Exception as e:
            print(f"  ⚠️ 跳过 {os.path.basename(fp)}: {e}")

    if not dfs:
        return {'error': 'No valid data loaded'}

    combined = pd.concat(dfs, ignore_index=True)
    print(f"Combined data shape: {combined.shape}")

    # 特征工程
    from .ml_predictor_v59 import MLFeatureEngineer
    engineer = MLFeatureEngineer()
    feat_df = engineer.build_features(combined)
    feat_df = feat_df.dropna()

    # 标签
    if use_triple_barrier:
        print(f"Using Triple Barrier labeling (upper {tb_upper:.1%}/lower {tb_lower:.1%}/{tb_time})")
        from .labeling import TripleBarrierLabeler, BarrierConfig
        labeler = TripleBarrierLabeler(BarrierConfig(
            upper_barrier=tb_upper,
            lower_barrier=tb_lower,
            time_barrier=tb_time,
            volatility_scaled=True,
        ))
        labeled = labeler.generate_labels_dataframe(combined, tb_upper, tb_lower, tb_time)
        y = labeled['label_binary'].values
    else:
        # 简单次日涨跌标签
        print("Using simple next-day direction label")
        close = combined['close']
        y = (close.pct_change().shift(-1) > 0).astype(int).values

    # 对齐 X 和 y
    min_len = min(len(feat_df), len(y))
    feat_df = feat_df.iloc[:min_len]
    y = y[:min_len]

    # 去除 NaN
    mask = ~np.isnan(y)
    X = feat_df.values[mask]
    y = y[mask]

    print(f"Training samples: {X.shape[0]}, Features: {X.shape[1]}")
    print(f"   正样本: {y.sum()}, 负样本: {(1-y).sum()}, 比例: {y.mean():.2%}")

    # Optuna 训练
    trainer = OptunaModelTrainer(
        model_dir=model_dir,
        n_trials=n_trials,
    )
    result = trainer.optimize_all(X, y, feature_names=list(feat_df.columns))

    if use_triple_barrier:
        result['label_config'] = {
            'type': 'triple_barrier',
            'upper': tb_upper,
            'lower': tb_lower,
            'time': tb_time,
        }

    return result


if __name__ == '__main__':
    if OPTUNA_AVAILABLE:
        # 简易自测：生成模拟数据
        np.random.seed(42)
        n = 1000
        X_sim = np.random.randn(n, 10)
        y_sim = (X_sim[:, 0] + X_sim[:, 1] * 0.5 + np.random.randn(n) * 0.5 > 0).astype(int)

        trainer = OptunaModelTrainer(n_trials=20)
        result = trainer.optimize_all(X_sim, y_sim)
        print("\n=== Optuna 优化结果 ===")
        for name, res in trainer.study_results.items():
            if not name.startswith('_'):
                print(f"  {name}: F1={res.get('train_f1', 0):.4f}, "
                      f"AUC={res.get('train_auc', 0):.4f}")
    else:
        print("请安装 Optuna: pip install optuna")
