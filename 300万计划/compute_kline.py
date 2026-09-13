# -*- coding: utf-8 -*-
# Re-fetched kline parse + stats + wind_market_data.md build (no destructive API)
import json, math, os

TMP = r'C:\Users\Administrator\.openclaw-autoclaw\workspace\.openclaw\tmp'
OUT = r'C:\Users\Administrator\.openclaw-autoclaw\workspace\.cluster\etf300w-plan-v91\wind_market_data.md'

src = os.path.join(TMP, 'kline3.json')
b = open(src, 'rb').read()
print('kline3 size:', len(b))
s = b.decode('utf-8-sig')
i = s.find('{')
try:
    env = json.loads(s[i:])
except Exception as e:
    print('PARSE FAIL:', str(e)[:130])
    c = 39900
    print('context:', repr(s[c:c+260]))
    raise SystemExit(0)

inner = json.loads(env['content'][0]['text'])
data = inner.get('data') or {}
cols = [c['name'] for c in data.get('columns', [])]
rows = data.get('rows', [])
print('cols:', cols, '| rows:', len(rows))
_cc = next((c for c in ('收盘价', 'MATCH', 'CLOSE', '收盘') if c in cols), cols[2])
ic = cols.index(_cc)
idt = cols.index('日期') if '日期' in cols else 0
closes = [float(r[ic]) for r in rows]
dates = [str(r[idt]) for r in rows]
rets = [math.log(closes[k] / closes[k-1]) for k in range(1, len(closes))]

def ann(n):
    xs = rets[-n:]
    m = sum(xs) / len(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(var) * math.sqrt(244) * 100

def ro(d):
    return (closes[-1] / closes[-min(d, len(closes) - 1)] - 1) * 100

peak = closes[0]
mdd = 0.0
for c in closes:
    peak = max(peak, c)
    mdd = min(mdd, c / peak - 1)

md = []
md.append('# Wind 行情核验数据(取数 2026-09-12,行情截至 2026-09-11 收盘)')
md.append('')
md.append('## 1) 配置相关 ETF 最新价(get_fund_price_indicators)')
md.append('')
md.append('| 代码 | 名称(方案角色) | 最新收盘 | 前收 | 当日涨跌 |')
md.append('|---|---|---|---|---|')
md.append('| 510300.SH | 沪深300ETF(核心权益 90万) | 4.579 | 4.617 | -0.82% |')
md.append('| 510880.SH | 红利ETF(红利 45万) | 3.419 | 3.473 | -1.56% |')
md.append('| 510500.SH | 中证500ETF(卫星配置 15万) | 7.611 | 7.742 | -1.69% |')
md.append('| 511010.SH | 国债ETF(防御 45万) | 141.146 | 141.185 | -0.03% |')
md.append('| 518880.SH | 黄金ETF(黄金 30万) | 8.943 | 9.083 | -1.54% |')
md.append('| 511880.SH | 货币ETF(现金层 60万) | 100.794 | 100.783 | +0.01% |')
md.append('')
md.append('## 2) 510300 已实现波动率与位置(日K %s ~ %s, %d 个交易日)' % (dates[0], dates[-1], len(closes)))
md.append('')
md.append('- 最新收盘: **%.3f 元**' % closes[-1])
md.append('- 近1年涨跌: %.2f%% | 近半年: %.2f%% | 近60日: %.2f%%' % (ro(244), ro(122), ro(60)))
md.append('- 已实现年化波动率: **20日 %.1f%% | 60日 %.1f%% | 120日 %.1f%% | 250日 %.1f%%**' % (ann(20), ann(60), ann(120), ann(250)))
md.append('- 窗口最大回撤(收盘口径): %.2f%%' % (mdd * 100))
md.append('- 窗口最高 %.3f / 最低 %.3f;当前距高点 %.1f%%、距低点 %.1f%%' % (max(closes), min(closes), (closes[-1] / max(closes) - 1) * 100, (closes[-1] / min(closes) - 1) * 100))
md.append('')
md.append('## 3) 沪深300 指数估值(get_index_fundamentals, 2026-09-11)')
md.append('')
md.append('- PE(TTM): **13.50**(分位 0%) / PB: **1.42**(分位 0%) / 股息率: **2.63%**(分位 100%)')
md.append('- 注: 分位窗口为返回的 24 期(约两年月频);方向上当前估值处于该窗口最低位、股息率最高位')
md.append('')
md.append('## 4) 10年期国债收益率(M1001654, 中证指数)')
md.append('')
md.append('- 最新(2026-09-11): **1.689%**;近一周 1.680%~1.689%,极低平稳')
md.append('- 含义: 防御资产(国债ETF)的预期回报锚定在 ~1.7%,方案中"国债 2.5-3.5%"的隐性假设偏高')
md.append('')
md.append('## 5) 期权数据缺口')
md.append('')
md.append('- 本 Wind CLI(7 服务 31 工具)无期权链/隐含波动率工具')
md.append('- 替代口径: 以已实现波动率为锚, 期权成本用 BS 敏感性表(IV 12%~28%)估算, 标注估算口径')
md.append('- 方案前置条件#1(接入真实期权链数据)仍需落地, 与市场价格无关')

open(OUT, 'w', encoding='utf-8').write('\n'.join(md))
print('SAVED', OUT)
print('summary: close=%.3f vol20=%.1f vol60=%.1f vol120=%.1f vol250=%.1f mdd=%.2f%%' % (closes[-1], ann(20), ann(60), ann(120), ann(250), mdd * 100))
