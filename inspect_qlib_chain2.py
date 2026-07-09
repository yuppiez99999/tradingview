import sys
sys.path.insert(0, r'E:\各种PY程序\28-终极量化交易系统7.1')
from utils.data_provider import get_historical_data
from utils.qlib_adapter import _normalize_klines

code = '000002'
df = get_historical_data(code, period='6m')
print('RAW TYPE', type(df).__name__)
print('RAW SHAPE', getattr(df, 'shape', None))
print('RAW COLUMNS', list(df.columns))
print('HAS ATTRS', hasattr(df, 'columns'), hasattr(df, 'empty'), hasattr(df, 'shape'))
norm = _normalize_klines(df)
print('NORMALIZE RESULT', type(norm).__name__ if norm is not None else None)
