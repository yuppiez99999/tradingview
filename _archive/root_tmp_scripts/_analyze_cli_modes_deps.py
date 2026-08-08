"""全面统计 cli/modes/*.py 的外部依赖"""
import re
import os
import glob
from collections import defaultdict

core_context_imports = set()
utils_cli_helpers_imports = set()
other_imports = defaultdict(set)

for f in sorted(glob.glob('cli/modes/*.py')):
    if f.endswith('__init__.py'):
        continue
    fname = os.path.basename(f)
    for line in open(f, encoding='utf-8'):
        line = line.strip()
        # from core.context import ...
        m = re.match(r'from core\.context import (.+)', line)
        if m:
            for name in re.findall(r'\b(\w+)\b', m.group(1)):
                if name not in ('import', 'from'):
                    core_context_imports.add(name)
            continue
        # from utils.cli_helpers import ...
        m = re.match(r'from utils\.cli_helpers import (.+)', line)
        if m:
            for name in re.findall(r'\b(\w+)\b', m.group(1)):
                if name not in ('import', 'from'):
                    utils_cli_helpers_imports.add(name)
            continue
        # 其他 from X import Y
        m = re.match(r'from (\S+) import (.+)', line)
        if m and not m.group(1).startswith('cli.modes'):
            mod = m.group(1)
            for name in re.findall(r'\b(\w+)\b', m.group(2)):
                if name not in ('import', 'from'):
                    other_imports[mod].add(name)
            continue
        # import X
        m = re.match(r'import (\S+)', line)
        if m and not m.group(1).startswith('cli'):
            other_imports[m.group(1)].add('(module)')

print('=== core.context 需提供 (%d 个) ===' % len(core_context_imports))
for n in sorted(core_context_imports):
    print('  ' + n)

print()
print('=== utils.cli_helpers 需提供 (%d 个) ===' % len(utils_cli_helpers_imports))
for n in sorted(utils_cli_helpers_imports):
    print('  ' + n)

print()
print('=== 其他外部依赖模块 (%d 个) ===' % len(other_imports))
for mod, names in sorted(other_imports.items()):
    print('  %s: %s' % (mod, ', '.join(sorted(names))))

# 检查这些模块是否存在
print()
print('=== 模块存在性检查 ===')
checks = [
    'core/context.py',
    'utils/cli_helpers.py',
    'cli/__init__.py',
    'utils/logging_manager.py',
    'utils/ai_coordinator.py',
    'utils/env_loader.py',
    'utils/report_archiver.py',
    'quant_modules/core.py',
    'engine/managers.py',
    'engine/rebalance.py',
    'quant_modules/data_layer.py',
]
for p in checks:
    print('  %-35s %s' % (p, 'EXISTS' if os.path.isfile(p) else 'MISSING'))
