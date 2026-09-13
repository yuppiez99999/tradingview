# -*- coding: utf-8 -*-
# Mainline BS sensitivity cross-check for the collar plan (no external deps beyond stdlib)
import math, io
from statistics import NormalDist

N = lambda x: NormalDist().cdf(x)
S = 4.579          # 510300 close 2026-09-11
T = 0.25           # 3-month
r = 0.017          # 10Y CGB proxy
q = 0.015          # dividend yield assumption

def strike_for_put_delta(sig, td):
    lo, hi = S * 0.6, S * 1.2
    for _ in range(400):
        mid = (lo + hi) / 2
        d1 = (math.log(S / mid) + (r - q + 0.5 * sig * sig) * T) / (sig * math.sqrt(T))
        d_ = -math.exp(-q * T) * (N(d1) - 1)
        if d_ < td: lo = mid
        else: hi = mid
    return (lo + hi) / 2

def strike_for_call_delta(sig, td):
    lo, hi = S * 0.8, S * 1.5
    for _ in range(400):
        mid = (lo + hi) / 2
        d1 = (math.log(S / mid) + (r - q + 0.5 * sig * sig) * T) / (sig * math.sqrt(T))
        d_ = math.exp(-q * T) * N(d1)
        if d_ > td: lo = mid
        else: hi = mid
    return (lo + hi) / 2

def bs(K, sig, kind):
    d1 = (math.log(S / K) + (r - q + 0.5 * sig * sig) * T) / (sig * math.sqrt(T))
    d2 = d1 - sig * math.sqrt(T)
    if kind == 'p':
        return K * math.exp(-r * T) * N(-d2) - S * math.exp(-q * T) * N(-d1)
    return S * math.exp(-q * T) * N(d1) - K * math.exp(-r * T) * N(d2)

md = []
md.append('# 主线 BS 成本敏感性核验(交叉核验材料)')
md.append('')
md.append('参数: S=4.579(510300, 09-11 收盘), T=0.25(3个月), r=1.7%(10年国债代理), q=1.5%(股息率假设)')
md.append('结构: 买3M虚值Put(Delta≈-0.175) + 卖3M虚值Call(Delta≈+0.175); 单季净成本×4 = 年化(未计滑点)')
md.append('')
md.append('| IV | Put行权价 | Put成本%名义 | Call行权价 | Call收入%名义 | 净成本/季 | 净成本/年(×4) | 50%保护(45万)年成本 | 100%保护(90万)年成本 | 占300万比例(50%保护) |')
md.append('|---|---|---|---|---|---|---|---|---|---|')
rows_out = []
for sig in (0.12, 0.16, 0.20, 0.24, 0.28):
    Kp = strike_for_put_delta(sig, 0.175); pp = bs(Kp, sig, 'p')
    Kc = strike_for_call_delta(sig, 0.175); cp = bs(Kc, sig, 'c')
    net_q = pp - cp
    net_y = net_q * 4
    cost50 = 45.0 * net_y            # 万元
    cost100 = 90.0 * net_y
    pct300 = cost50 / 300.0 * 100
    row = '| %d%% | %.3f | %.2f%% | %.3f | %.2f%% | %.2f%% | %.2f%% | %.2f万 | %.2f万 | %.2f%% |' % (
        sig * 100, Kp, pp / S * 100, Kc, cp / S * 100, net_q / S * 100, net_y * 100, cost50, cost100, pct300)
    md.append(row)
    rows_out.append(row)
md.append('')
md.append('参考: 年度预算 0.8%-1.2% × 300万 = 2.4万~3.6万元/年。')
md.append('注: call 侧未加波动率偏斜(真实市场 put 偏斜通常使 put 更贵、call 更便宜,净成本略高于本表);滑点与手续费另计。')
md.append('')
md.append('## Put-Only 对照(不卖Call)')
md.append('')
md.append('| IV | Put成本/季 | Put成本/年(×4) | 50%保护(45万)年成本 | 占300万比例 |')
md.append('|---|---|---|---|---|')
for sig in (0.12, 0.16, 0.20, 0.24, 0.28):
    Kp = strike_for_put_delta(sig, 0.175); pp = bs(Kp, sig, 'p')
    cost50 = 45.0 * pp / S * 4
    md.append('| %d%% | %.2f%% | %.2f%% | %.2f万 | %.2f%% |' % (sig * 100, pp / S * 100, pp / S * 100 * 4, cost50, cost50 / 300.0 * 100))

out = '\n'.join(md)
print(out)
open(r'C:\Users\Administrator\.openclaw-autoclaw\workspace\.cluster\etf300w-plan-v91\mainline_bs.md', 'w', encoding='utf-8').write(out + '\n')
print('SAVED mainline_bs.md')
