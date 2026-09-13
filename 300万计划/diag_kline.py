# -*- coding: utf-8 -*-
# Diagnose kline capture files (read-only, no destructive API)
import json, os

TMP = r'C:\Users\Administrator\.openclaw-autoclaw\workspace\.openclaw\tmp'
for name in ('kline_raw.txt', 'kline_510300.json'):
    p = os.path.join(TMP, name)
    if not os.path.exists(p):
        print(name, '-> MISSING')
        continue
    b = open(p, 'rb').read()
    print('== %s: %d bytes' % (name, len(b)))
    print('   head hex:', b[:24].hex())
    for enc in ('utf-8-sig', 'utf-16', 'utf-8', 'gbk'):
        try:
            s = b.decode(enc)
        except Exception as e:
            print('   [%s] decode fail: %s' % (enc, str(e)[:60]))
            continue
        i = s.find('"content"')
        if i >= 0:
            i = s.rfind('{', 0, i)
        if i < 0:
            i = s.find('{')
        try:
            env = json.loads(s[i:])
            print('   [%s] JSON OK (from %d); has content: %s' % (enc, i, isinstance(env, dict) and 'content' in env))
            break
        except Exception as e:
            print('   [%s] json fail from %d: %s' % (enc, i, str(e)[:90]))
print('done')
