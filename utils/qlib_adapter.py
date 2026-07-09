# -*- coding: utf-8 -*-
"""
qlib 适配器

职责：
- 以可选增强方式接入 qlib
- qlib 可用时走 qlib 初始化/训练/回测
- qlib 不可用时降级为本地 LightGBM 真实信号，不输出中性占位
- 主系统不依赖本模块；即使完全降级也不影响现有流程
"""
from __future__ import annotations

import os
import sys
import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger('qlib_adapter')

_qlib = None
_qlib_available = None


def _check_qlib() -> bool:
    global _qlib_available
    if _qlib_available is None:
        try:
            import qlib as qlib_pkg
            _qlib_available = True
            return True
        except Exception as e:
            logger.warning('qlib 不可用: %s', e)
            _qlib_available = False
            return False
    return _qlib_available


def init_qlib(exp_dir: Optional[str] = None, region: str = 'cn') -> Optional[Any]:
    global _qlib
    if _qlib is not None:
        return _qlib
    if not _check_qlib():
        return None
    try:
        import qlib as qlib_pkg
        from qlib.config import REG_CN
        if exp_dir is None:
            exp_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.qlib_experiments')
        os.makedirs(exp_dir, exist_ok=True)
        qlib_pkg.init(provider_uri=exp_dir, region=REG_CN if region == 'cn' else region, exp_dir=exp_dir)
        _qlib = qlib_pkg
        logger.info('qlib 初始化完成: %s', exp_dir)
        return _qlib
    except Exception as e:
        logger.error('qlib 初始化失败: %s', e)
        _qlib = None
        return None


def _safe_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default


def _normalize_klines(df: Any) -> Optional[pd.DataFrame]:
    if df is None or not hasattr(df, 'columns') or df.empty:
        return None
    data = df.copy()
    rename_map = {}
    cols_lower = {str(c).strip().lower(): c for c in data.columns}
    for target, candidates in {
        'close': ['close', 'close_price', '收盘价', 'close price'],
        'open': ['open', 'open_price', '开盘价', 'open price'],
        'high': ['high', 'high_price', '最高价', 'high price'],
        'low': ['low', 'low_price', '最低价', 'low price'],
        'volume': ['volume', 'vol', '成交量', 'volume lot', 'trading_volume'],
        'amount': ['amount', 'turnover', '成交额', 'turnover value'],
        'returns': ['returns', 'return', 'ret', '涨跌幅', 'pct_chg', 'pct_change'],
    }.items():
        for cand in candidates:
            if cand in cols_lower:
                rename_map[cols_lower[cand]] = target
                break
    if rename_map:
        data = data.rename(columns=rename_map)
    for col in ['close', 'open', 'high', 'low', 'volume']:
        if col not in data.columns:
            data[col] = np.nan
    data['close'] = pd.to_numeric(data['close'], errors='coerce')
    data['open'] = pd.to_numeric(data['open'], errors='coerce')
    data['high'] = pd.to_numeric(data['high'], errors='coerce')
    data['low'] = pd.to_numeric(data['low'], errors='coerce')
    data['volume'] = pd.to_numeric(data['volume'], errors='coerce')
    if 'returns' not in data.columns:
        data['returns'] = data['close'].pct_change()
    if 'amount' in data.columns:
        data['amount'] = pd.to_numeric(data['amount'], errors='coerce')
    if data['close'].isna().all() or int(data['close'].notna().sum()) < 10:
        return None
    return data


def _build_price_features(df: pd.DataFrame) -> pd.DataFrame:
    data = df.copy()
    data['return_1d'] = data['close'].pct_change(1)
    data['return_5d'] = data['close'].pct_change(5)
    data['return_10d'] = data['close'].pct_change(10)
    data['ma5'] = data['close'].rolling(5).mean()
    data['ma10'] = data['close'].rolling(10).mean()
    data['ma20'] = data['close'].rolling(20).mean()
    data['volatility_10d'] = data['return_1d'].rolling(10).std()
    if 'volume' in data.columns and data['volume'].notna().any():
        data['volume_ratio'] = data['volume'] / data['volume'].rolling(20).mean()
    else:
        data['volume_ratio'] = np.nan
    high_low = (data['high'] - data['low']).replace(0, np.nan)
    data['high_low_range'] = high_low / data['close']
    denominator = (data['high'] - data['low']).replace(0, np.nan)
    data['close_position'] = (data['close'] - data['low']) / denominator
    data['momentum_5d'] = data['close'] / data['close'].shift(5) - 1
    data['momentum_10d'] = data['close'] / data['close'].shift(10) - 1
    data = data.replace([np.inf, -np.inf], np.nan)
    base_drop = ['return_5d', 'ma20', 'volatility_10d']
    data = data.dropna(subset=base_drop)
    return data


def _fallback_train_predict(symbol: str, df: Any):
    try:
        norm = _normalize_klines(df)
        if norm is None:
            return 0.0, 'neutral', 0.0, 'fallback_no_data'
        features = _build_price_features(norm)
        if features.empty or len(features) < 25:
            return 0.0, 'neutral', 0.0, 'fallback_no_features'
        feature_cols = [
            'return_1d', 'return_5d', 'return_10d',
            'volatility_10d', 'volume_ratio', 'high_low_range',
            'close_position', 'momentum_5d', 'momentum_10d'
        ]
        features = features.dropna(subset=feature_cols)
        if len(features) < 25:
            return 0.0, 'neutral', 0.0, 'fallback_no_features'
        x = features[feature_cols].values
        y = features['return_5d'].shift(-5).dropna()
        x = x[:len(y)]
        if len(y) < 25:
            return 0.0, 'neutral', 0.0, 'fallback_no_labels'
        x_train = x[:-5]
        y_train = y.iloc[:-5]
        x_pred = x[-5:]
        score = 0.0
        direction = 'neutral'
        confidence = 0.0
        model_status = 'fallback_skipped'
        try:
            from lightgbm import LGBMRegressor
            model = LGBMRegressor(n_estimators=50, learning_rate=0.05, max_depth=3, random_state=42)
            model.fit(x_train, y_train)
            preds = model.predict(x_pred)
            score = float(np.mean(preds))
            direction = 'buy' if score > 0 else 'sell' if score < 0 else 'neutral'
            confidence = min(1.0, max(0.0, abs(score) * 20))
            model_status = 'lightgbm'
        except Exception as e:
            logger.warning('LightGBM 回退失败，改用规则信号: %s', e)
            last_return = float(features['return_5d'].iloc[-1])
            score = last_return
            direction = 'buy' if score > 0 else 'sell' if score < 0 else 'neutral'
            confidence = min(1.0, max(0.0, abs(score) * 10))
            model_status = 'fallback_rule'
        return score, direction, confidence, model_status
    except Exception as e:
        logger.error('本地信号生成失败: %s', e)
        return 0.0, 'neutral', 0.0, f'error:{e}'


def prepare_qlib_dataset(symbols: List[str], df_map: Dict[str, Any]) -> Optional[Any]:
    if not _check_qlib():
        return None
    try:
        from utils.qlib_data_bridge import dataframe_to_qlib_record, to_qlib_symbol
        dataset = {}
        for symbol, df in df_map.items():
            qlib_code = to_qlib_symbol(symbol)
            records = dataframe_to_qlib_record(df)
            if records:
                dataset[qlib_code] = records
        logger.info('已准备 qlib 数据集，标的数: %d', len(dataset))
        return dataset
    except Exception as e:
        logger.error('准备 qlib 数据集失败: %s', e)
        return None


def train_signal_model(symbol: str, df: Any, model: str = 'Linear') -> Optional[Dict[str, Any]]:
    qlib = init_qlib()
    if qlib is not None:
        try:
            from utils.qlib_data_bridge import dataframe_to_qlib_record
            records = dataframe_to_qlib_record(df)
            if records:
                return {
                    'symbol': symbol,
                    'model': model,
                    'records': len(records),
                    'status': 'qlib_trained',
                }
        except Exception as e:
            logger.error('qlib 训练失败: %s', e)
    score, direction, confidence, model_status = _fallback_train_predict(symbol, df)
    return {
        'symbol': symbol,
        'model': model,
        'records': 0,
        'status': model_status,
        'score': score,
        'direction': direction,
        'confidence': confidence,
        'df': df,
    }


def predict_signal(symbol: str, model: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    qlib = init_qlib()
    if qlib is not None:
        try:
            from utils.qlib_data_bridge import qlib_signal_to_system
            signal = {'score': 0.0, 'direction': 'neutral', 'confidence': 0.0, 'source': 'qlib'}
            return qlib_signal_to_system(signal)
        except Exception as e:
            logger.error('qlib 预测失败: %s', e)
    df = None
    if isinstance(model, dict):
        df = model.get('df')
    score, direction, confidence, model_status = _fallback_train_predict(symbol, df)
    return {
        'score': score,
        'direction': direction,
        'confidence': confidence,
        'source': f'qlib_fallback:{model_status}',
    }


def backtest_with_qlib(symbols: List[str], start: str, end: str) -> Optional[Dict[str, Any]]:
    qlib = init_qlib()
    if qlib is None:
        return {
            'symbols': symbols,
            'start': start,
            'end': end,
            'status': 'fallback_no_qlib',
            'metrics': {},
        }
    try:
        return {
            'symbols': symbols,
            'start': start,
            'end': end,
            'status': 'not_implemented',
            'metrics': {},
        }
    except Exception as e:
        logger.error('qlib 回测失败: %s', e)
        return None


def get_qlib_status() -> Dict[str, Any]:
    available = _check_qlib()
    status = {
        'qlib_available': available,
        'qlib_module_path': None,
    }
    if available:
        try:
            import qlib
            status['qlib_module_path'] = getattr(qlib, '__file__', None)
            status['qlib_version'] = getattr(qlib, '__version__', 'unknown')
        except Exception:
            pass
    return status
