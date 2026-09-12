import pandas as pd, numpy as np, glob, os

d = os.path.dirname(os.path.abspath(__file__)) + os.sep
data = {}
for f in glob.glob(d + '*.csv'):
    code = os.path.basename(f).replace('.csv', '')
    df = pd.read_csv(f)
    df['date'] = pd.to_datetime(df['date'])
    data[code] = df.set_index('date')['close']
px = pd.DataFrame(data).sort_index().ffill()
rets = px / px.iloc[0]
rets['sat'] = rets['510500']

full = {'510300': 90, '515080': 45, '510500': 15, 'sat': 15, '511260': 45, '518880': 30, '511880': 60}
w = pd.Series(full)
w = w / w.sum()
eq = ['510300', '515080', '510500', 'sat']

print('k     | 510300年末 | 组合年末 | 组合最大回撤 | 击穿15%?')
for k in [1.0, 1.3, 1.638, 1.9, 2.2, 2.5]:
    sim = rets.copy()
    for e in eq:
        sim[e] = 1 + (rets[e] - 1) * k
    pv = (sim[w.index] * w).sum(axis=1)
    dd = (pv - pv.cummax()) / pv.cummax()
    mdd = dd.min() * 100
    breach = 'YES' if mdd <= -15 else 'no'
    print('{:.3f} | {:+.1f}%     | {:+.2f}%   | {:.2f}%      | {}'.format(
        k, (sim['510300'].iloc[-1] - 1) * 100, (pv.iloc[-1] - 1) * 100, mdd, breach))

# 重点: k=1.638 -> 510300 -35%
k = 1.638
sim = rets.copy()
for e in eq:
    sim[e] = 1 + (rets[e] - 1) * k
pv = (sim[w.index] * w).sum(axis=1)
dd = (pv - pv.cummax()) / pv.cummax()
trough = dd.idxmin()
print()
print('=== k=1.638 (510300 -> -35%) 细节 ===')
print('组合最大回撤: {:.2f}%  谷底: {}'.format(dd.min() * 100, trough.date()))
print('谷底时 510300: {:.1f}%  510500: {:.1f}%  515080: {:.1f}%'.format(
    (sim['510300'].loc[trough] - 1) * 100,
    (sim['510500'].loc[trough] - 1) * 100,
    (sim['515080'].loc[trough] - 1) * 100))
print('谷底时组合价值(万): {:.1f}'.format(300 * pv.loc[trough]))
