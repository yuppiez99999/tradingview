"""检查缺失脚本的实际位置"""
import os

root = r'e:\各种PY程序\28-终极量化交易系统8.4'

# 1. 验证日志中提到的不存在脚本
print('[1] 日志中提到缺失的脚本:')
missing = [
    'v8.3_institutional/generate_daily_trade_plan.py',
    'v8.3_institutional/daily_workflow.py',
]
for s in missing:
    full = os.path.join(root, s)
    status = 'MISSING' if not os.path.exists(full) else 'EXISTS'
    print(f'  {status}  {s}')

# 2. 搜索实际位置
print()
print('[2] 搜索 generate_daily_trade_plan 实际位置:')
for dirpath, dirs, files in os.walk(root):
    skip = ['research', 'references', '.git', 'qlib_env', '__pycache__', 'node_modules']
    if any(s in dirpath for s in skip):
        continue
    for f in files:
        if 'generate_daily_trade_plan' in f.lower() and f.endswith('.py'):
            rel = os.path.relpath(os.path.join(dirpath, f), root)
            print(f'  FOUND  {rel}')

print()
print('[3] 搜索 daily_workflow 实际位置:')
for dirpath, dirs, files in os.walk(root):
    skip = ['research', 'references', '.git', 'qlib_env', '__pycache__', 'node_modules']
    if any(s in dirpath for s in skip):
        continue
    for f in files:
        if 'daily_workflow' in f.lower() and f.endswith('.py'):
            rel = os.path.relpath(os.path.join(dirpath, f), root)
            print(f'  FOUND  {rel}')

# 4. 检查 ai 模块
print()
print('[4] 检查 ai 模块位置:')
for dirpath, dirs, files in os.walk(root):
    skip = ['research', 'references', '.git', 'qlib_env', '__pycache__', 'node_modules']
    if any(s in dirpath for s in skip):
        continue
    if 'ai' in dirs:
        full_ai = os.path.join(dirpath, 'ai')
        rg = os.path.join(full_ai, 'recommendation_generator.py')
        rel = os.path.relpath(full_ai, root)
        rg_status = 'EXISTS' if os.path.exists(rg) else 'MISSING'
        print(f'  FOUND ai/ at {rel}  (recommendation_generator.py: {rg_status})')

# 5. 检查 generate_daily_trade_plan 是否被移动或重命名
print()
print('[5] 搜索所有 trade_plan 生成器 (含重命名):')
for dirpath, dirs, files in os.walk(root):
    skip = ['research', 'references', '.git', 'qlib_env', '__pycache__', 'node_modules']
    if any(s in dirpath for s in skip):
        continue
    for f in files:
        if f.endswith('.py') and ('trade_plan' in f.lower() or 'tradeplan' in f.lower()):
            if 'generate' in f.lower() or 'build' in f.lower() or 'create' in f.lower() or 'make' in f.lower():
                rel = os.path.relpath(os.path.join(dirpath, f), root)
                print(f'  FOUND  {rel}')
