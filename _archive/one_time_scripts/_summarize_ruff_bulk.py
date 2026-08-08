import collections, pathlib
from collections import Counter

lines = pathlib.Path('.ruff_current.txt').read_text(encoding='utf-8', errors='ignore').splitlines()
by_rule = Counter()
by_file = Counter()

for line in lines:
    if ':' not in line:
        continue
    parts = line.split(':', 3)
    if len(parts) < 4:
        continue
    path, lineno, rule = parts[0], parts[1], parts[2].strip()
    rule_code = ''.join(ch for ch in rule if ch.isalpha() or ch in '_#')
    by_rule[rule_code] += 1
    by_file[path] += 1

print('TOP_RULES')
for rule, count in by_rule.most_common(20):
    print(f'{count:5d} {rule}')

print('TOP_FILES')
for path, count in by_file.most_common(20):
    print(f'{count:5d} {path}')
