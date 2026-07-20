# -*- coding: utf-8 -*-
import urllib.request, json

def get(url, ref=None, timeout=15):
    req = urllib.request.Request(url)
    req.add_header('User-Agent', 'Mozilla/5.0')
    if ref:
        req.add_header('Referer', ref)
    return urllib.request.urlopen(req, timeout=timeout).read()

out = []
# 腾讯行情
try:
    codes = ['sh600519','sh510300','sz300750','sh601318','sh512100','sz159915','sz002415','sh600036']
    raw = get('https://qt.gtimg.cn/q=' + ','.join(codes)).decode('gbk')
    ok = 0
    for line in raw.split(';'):
        line = line.strip()
        if '=' in line:
            k, _, v = line.partition('=')
            f = v.strip('"').split('~')
            if len(f) > 46:
                ok += 1
                out.append(f"[腾讯] {f[0]} {f[1]} 现价={f[3]} 昨收={f[4]} 涨跌%={f[32]} PE={f[39]} PB={f[46]} 市值(亿)={f[45]}")
    out.append(f"--- 腾讯成功解析 {ok} 只")
except Exception as e:
    out.append(f"[腾讯] ERR {repr(e)}")

# 东财实时价
try:
    secids = {'600519':'1.600519','510300':'1.510300','300750':'0.300750','601318':'1.601318','512100':'1.512100','159915':'0.159915','002415':'0.002415','600036':'1.600036'}
    for code, secid in secids.items():
        url = f'https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields=f43,f57,f58,f60,f116,f117,f162,f164,f167,f168,f170&fltt=2'
        d = json.loads(get(url, ref='https://quote.eastmoney.com/').decode('utf-8'))
        sd = d.get('data') or {}
        out.append(f"[东财] {code} {sd.get('f58')} 现价={sd.get('f43')} 昨收={sd.get('f60')} 市值(亿)={round((sd.get('f116') or 0)/1e8,1)} PE={sd.get('f162')} PB={sd.get('f167')} 涨停={sd.get('f168')} 跌停={sd.get('f170')}")
except Exception as e:
    out.append(f"[东财] ERR {repr(e)}")

with open('_test_realtime.txt', 'w', encoding='utf-8') as fp:
    fp.write('\n'.join(out))
print('DONE')
