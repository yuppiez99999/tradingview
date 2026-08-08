import akshare as ak

# 搜索 akshare 中与港口库存、动力煤、碳市场相关的函数
all_funcs = [f for f in dir(ak) if not f.startswith('_')]

keywords = ['coal', 'carbon', 'inventory', 'port', '动力煤', '碳', '库存', '港口']
for func in all_funcs:
    if any(kw in func.lower() for kw in keywords):
        print(func)
