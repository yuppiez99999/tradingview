import json
with open(r'e:\各种PY程序\28-终极量化交易系统7.1\config\positions.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
positions = data.get('positions', {})
for key in ['sh688981','sh600219','sh600019','sz510300','sh300308']:
    item = positions.get(key, {})
    print(f"{key}: price={item.get('est_price')}, source={item.get('price_source')}, update={item.get('last_update')}")
