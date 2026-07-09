# -*- coding: utf-8 -*-
import json
from datetime import datetime

positions_path = r'e:\各种PY程序\28-终极量化交易系统7.1\config\positions.json'
with open(positions_path, 'r', encoding='utf-8') as f:
    data = json.load(f)

positions = data.get('positions', {})
now = datetime.now().isoformat()

fixes = {
    'sh688981': {'est_price': 144.12, 'price_source': 'manual_fix_20260706', 'last_update': now},
    'sh600219': {'est_price': 4.20, 'price_source': 'manual_fix_20260706', 'last_update': now},
    'sh600019': {'est_price': 5.65, 'price_source': 'manual_fix_20260706', 'last_update': now},
}

changed = []
for key, patch in fixes.items():
    item = positions.get(key)
    if not item:
        continue
    old_price = item.get('est_price')
    if old_price == patch['est_price']:
        continue
    item['est_price'] = patch['est_price']
    item['price_source'] = patch['price_source']
    item['last_update'] = patch['last_update']
    changed.append((key, item.get('name'), old_price, patch['est_price']))

if changed:
    with open(positions_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print('fixed:')
    for key, name, old, new in changed:
        print(f'{key} {name}: {old} -> {new}')
else:
    print('no change needed')
