"""验证用户示例代码"""
import sys

sys.path.insert(0, r'e:\各种PY程序\28-终极量化交易系统8.4')

import numpy as np
import pandas as pd

# 生成模拟数据
np.random.seed(42)
n = 300
dates = pd.date_range('2024-01-01', periods=n, freq='B')
close0 = 100.0
returns = np.random.randn(n) * 0.02 + 0.0005
close = close0 * np.exp(np.cumsum(returns))
high = close * (1 + np.abs(np.random.randn(n)) * 0.01)
low = close * (1 - np.abs(np.random.randn(n)) * 0.01)
open_p = close * (1 + np.random.randn(n) * 0.005)
volume = np.abs(np.random.randn(n)) * 1e7 + 1e6
amount = close * volume * 0.001

df = pd.DataFrame({
    'open': open_p, 'high': high, 'low': low,
    'close': close, 'volume': volume, 'amount': amount
}, index=dates)

print('测试数据:', len(df), '个交易日')
print()

# ========== 方式1: GTJA191 专用 ==========
print('=' * 60)
print('方式1: GTJA191Factors 测试')
print('=' * 60)

from utils.gtja191_factors import GTJA191Factors

calc = GTJA191Factors()

# 全量计算
values = calc.compute(df)
print(f'全量计算: {len(values)} 个因子成功')
print('前5个因子:')
for _i, (k, v) in enumerate(list(values.items())[:5]):
    print(f'  {k}: {v:.6f}')

# 单个因子快捷调用
print()
print('单个因子快捷调用:')
v144 = calc.alpha144(df)
v001 = calc.alpha001(df)
v028 = calc.alpha028(df)
v158 = calc.alpha158(df)
print(f'  alpha144(下跌日量价效率): {v144:.8f}')
print(f'  alpha001(量价秩相关):     {v001:.6f}')
print(f'  alpha028(KDJ类趋势):      {v028:.4f}')
print(f'  alpha158(长期趋势位置):   {v158:.6f}')

# 获取公式
print()
print('公式查询:')
formula = calc.get_formula('gtja191_001')
print(f'  gtja191_001: {formula[:70]}...')
formula144 = calc.get_formula('gtja191_144')
print(f'  gtja191_144: {formula144}')

# ========== 方式2: 全部因子库 ==========
print()
print('=' * 60)
print('方式2: Vibe-Trading 适配器测试')
print('=' * 60)

from utils.vibe_trading_adapter import get_vibe_adapter

adapter = get_vibe_adapter()

print(f'注册表健康状态: {adapter.health}')

# 测试各 zoo
for zoo in ['gtja191', 'qlib158', 'alpha101']:
    result = adapter.compute_single_stock(df, zoo=zoo)
    print(f'  {zoo:12s}: {len(result.values):3d} 个因子成功')

# 展示一些因子最新值
print()
print('QLib158 部分因子值:')
qlib_result = adapter.compute_single_stock(df, zoo='qlib158')
for _i, (k, v) in enumerate(list(qlib_result.values.items())[:8]):
    print(f'  {k:20s}: {v:.6f}')

# 查看时间序列
print()
print('gtja191_028 时间序列 (最后5天):')
series = adapter.compute_one_factor(df, 'gtja191_028')
if series is not None:
    print(series.tail())

print()
print('=' * 60)
print('✅ 所有代码运行正常！')
print('=' * 60)
