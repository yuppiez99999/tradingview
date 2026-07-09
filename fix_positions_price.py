# -*- coding: utf-8 -*-
"""恢复 positions.json 中被默认值污染的价格"""
import json

path = r'e:\各种PY程序\28-终极量化交易系统7.1\config\positions.json'

with open(path, 'r', encoding='utf-8') as f:
    data = json.load(f)

# 正确预估价映射（code -> est_price）
correct_prices = {
    '510300': 4.0,
    '510500': 6.5,
    '512100': 2.3,
    '588000': 1.05,
    '159915': 2.15,
    '515180': 5.0,
    '688041': 85.0,
    '300308': 120.0,
    '002371': 350.0,
    '688981': 142.93,
    '603019': 94.42,
    '688017': 180.0,
    '600089': 25.0,
    '600875': 22.0,
    '000425': 8.5,
    '600406': 35.0,
    '600219': 4.19,
    '600019': 5.61,
    '600276': 50.0,
    '600989': 18.0,
    '300274': 45.0,
    '600036': 38.0,
    '600900': 28.0,
    '601088': 38.5,
    '518880': 5.85,
}

restored = 0
for key, item in data.get('positions', {}).items():
    code = item.get('code')
    if code in correct_prices:
        if item.get('est_price') == 3000.0:
            item['est_price'] = correct_prices[code]
            restored += 1

with open(path, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f'已恢复 {restored} 个标的价格')
