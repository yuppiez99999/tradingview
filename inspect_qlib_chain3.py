import sys
sys.path.insert(0, r'E:\各种PY程序\28-终极量化交易系统7.1')
from utils.data_provider import get_historical_data
from utils.qlib_adapter import train_signal_model, predict_signal

codes = ['000001', '000002', '600519', '510300', '588000']
for code in codes:
    try:
        df = get_historical_data(code, period='6m')
        trained = train_signal_model(code, df)
        pred = predict_signal(code, trained)
        print(code, 'TRAIN', trained)
        print(code, 'PRED', pred)
    except Exception as e:
        print(code, 'ERR', repr(e))
