import sys
sys.path.insert(0, r'E:\各种PY程序\28-终极量化交易系统8.4')
from utils.data_provider import get_historical_data
for code in ["000001", "000002", "600519", "510300", "588000", "512880"]:
    try:
        df = get_historical_data(code, period="6m")
        print('CODE', code, 'TYPE', type(df).__name__, 'SHAPE', getattr(df, 'shape', None), 'EMPTY', getattr(df, 'empty', None))
        if df is not None and hasattr(df, 'columns'):
            print('COLS', list(df.columns)[:20])
            print('HEAD\n', df.head(3).to_string())
            print('TAIL\n', df.tail(3).to_string())
        print('-' * 60)
    except Exception as e:
        print('CODE', code, 'ERR', repr(e))
