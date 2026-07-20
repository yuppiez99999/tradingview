from pywinauto import findwindows

all_windows = findwindows.find_elements()
futures = []
for w in all_windows:
    name = w.name
    class_name = w.class_name
    pid = w.process_id
    if '期货' in name or 'StockTrade' in class_name or 'STOCKTRADE' in class_name:
        futures.append({'name': name, 'class': class_name, 'pid': pid})

print(f"找到期货相关窗口: {len(futures)}")
for f in futures:
    print(f"标题: {f['name']}, 类名: {f['class']}, 进程ID: {f['pid']}")

print("\n=== 所有包含'期货'的窗口 ===")
for w in all_windows:
    if '期货' in w.name:
        print(f"标题: {w.name}, 类名: {w.class_name}, 进程ID: {w.process_id}")

print("\n=== 所有包含'StockTrade'或'STOCKTRADE'的窗口 ===")
for w in all_windows:
    if 'StockTrade' in w.class_name or 'STOCKTRADE' in w.class_name:
        print(f"标题: {w.name}, 类名: {w.class_name}, 进程ID: {w.process_id}")
