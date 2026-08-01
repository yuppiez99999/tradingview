# -*- coding: utf-8 -*-
"""临时诊断脚本: 定位 shadow daily_return 连续 0% 的根因."""
import json
import os
import sys

sys.path.insert(0, '.')
sys.path.insert(0, 'v8.3_institutional')
os.environ['NO_PROXY'] = 'push2his.eastmoney.com,push2.eastmoney.com,eastmoney.com,sinajs.cn,sina.com.cn'

print('=== 1. 检查 trade_plan target_weights (兜底来源) ===')
for d in ['20260727', '20260728', '20260729', '20260730']:
    tp = f'v8.3_institutional/trade_plans/trade_plan_{d}.json'
    if os.path.exists(tp):
        with open(tp, 'r', encoding='utf-8') as f:
            j = json.load(f)
        ep = j.get('execution_plan', {})
        mo = ep.get('morning_orders', [])
        ao = ep.get('afternoon_orders', [])
        dc = ep.get('day_capital', 0)
        syms = set([o.get('code', '') for o in mo + ao if o.get('code')])
        print(f'  {d}: orders={len(mo)+len(ao)}, day_capital={dc}, symbols={len(syms)}')
    else:
        print(f'  {d}: 文件不存在')

print()
print('=== 2. 测试 MarketDataProvider 数据源健康度 ===')
from utils.data_provider import MarketDataProvider

p = MarketDataProvider(backtest_mode=False)
h = p.source_health
for src in ['wind_mcp', 'ifind_mcp', 'tdx', 'akshare']:
    s = h.get(src, {})
    ok = s.get('ok')
    err = str(s.get('last_error', ''))[:80]
    print(f'  {src}: ok={ok}, err={err}')

print()
print('=== 3. 测试拉取 3 个标的 5d 历史数据 ===')
for sym in ['600276', '588000', '601088']:
    try:
        df = p.get_historical_data(sym, period='5d')
        if df is not None and not df.empty and len(df) >= 2:
            c2 = float(df['close'].iloc[-1])
            c1 = float(df['close'].iloc[-2])
            ret = c2 / c1 - 1
            print(f'  {sym}: rows={len(df)}, close {c1:.2f} -> {c2:.2f}, ret={ret*100:.4f}%')
        else:
            print(f'  {sym}: 获取失败 (返回空或不足2行)')
    except Exception as e:
        print(f'  {sym}: 异常 {type(e).__name__}: {e}')
