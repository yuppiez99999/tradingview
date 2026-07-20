# -*- coding: utf-8 -*-
from backtest_runner import run_backtest
import json

result = run_backtest(["600519", "000858", "601318"], "2024-01-01", "2024-01-31")
print(json.dumps(result, ensure_ascii=False, indent=2))
