"""系统分析主入口文件所有 import 的模块存在性"""
import ast
import os
import sys

# 模拟 setup_sys_path 注入的路径
for p in ['.', 'v8.3_institutional', 'v8.3_institutional/src']:
    if os.path.isdir(p):
        sys.path.insert(0, p)

main_file = '量化策略系统_统一入口_v8.6.py'
tree = ast.parse(open(main_file, encoding='utf-8').read())

imports = []  # (module, names, lineno)
for node in ast.walk(tree):
    if isinstance(node, ast.ImportFrom):
        if node.module and not node.module.startswith('cli.modes'):
            imports.append((node.module, [n.name for n in node.names], node.lineno))
    elif isinstance(node, ast.Import):
        for n in node.names:
            imports.append((n.name, ['(module)'], node.lineno))

# 检查每个 import 模块的可导入性
missing = []
existing = []
for mod, names, lineno in imports:
    # 标准库/第三方不检查
    stdlib = {'argparse','glob','json','logging','os','sys','time','datetime','typing',
              'subprocess','pandas','numpy','yaml','requests','threading','traceback',
              'importlib','importlib.util','pathlib','__future__'}
    top = mod.split('.')[0]
    if top in stdlib or mod in stdlib:
        continue
    # 检查模块文件是否存在
    mod_path = mod.replace('.', '/') + '.py'
    mod_pkg = mod.replace('.', '/') + '/__init__.py'
    found = False
    for root in ['.', 'v8.3_institutional', 'v8.3_institutional/src']:
        if os.path.isfile(os.path.join(root, mod_path)) or os.path.isfile(os.path.join(root, mod_pkg)):
            found = True
            break
    if found:
        existing.append((mod, names, lineno))
    else:
        missing.append((mod, names, lineno))

print('=== 主文件 import 的缺失模块 (%d 个) ===' % len(missing))
for mod, names, lineno in sorted(missing, key=lambda x: x[2]):
    print('  L%-4d %-45s -> %s' % (lineno, mod, ', '.join(names)))

print()
print('=== 主文件 import 的已存在模块 (%d 个) ===' % len(existing))
for mod, names, lineno in sorted(existing, key=lambda x: x[2]):
    print('  L%-4d %-45s -> %s' % (lineno, mod, ', '.join(names[:3]) + ('...' if len(names)>3 else '')))

print()
print('=== 缺失模块需提供的唯一名字汇总 ===')
all_names = set()
for mod, names, _ in missing:
    for n in names:
        all_names.add(n)
print('共 %d 个名字:' % len(all_names))
for n in sorted(all_names):
    print('  ' + n)
