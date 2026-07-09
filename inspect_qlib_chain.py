import sys
sys.path.insert(0, r'E:\各种PY程序\28-终极量化交易系统7.1')
import pandas as pd
from utils.data_provider import get_historical_data
from utils.qlib_adapter import _normalize_klines, _build_price_features, _fallback_train_predict

code = '000002'
df = get_historical_data(code, period='6m')
print('RAW TYPE', type(df).__name__)
print('RAW SHAPE', getattr(df, 'shape', None))
print('RAW COLUMNS', list(df.columns))
print('HEAD\n', df.head(3).to_string())
print('TAIL\n', df.tail(3).to_string())
print('NORMALIZE', _normalize_klines(df) is not None)
features = _build_price_features(_normalize_klines(df))
print('FEATURES SHAPE', getattr(features, 'shape', None))
print('FEATURES HEAD\n', features.head(3).to_string())
print('FALLBACK', _fallback_train_predict(code, df))
