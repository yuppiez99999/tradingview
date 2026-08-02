"""修复 checks 列表中被错误替换的行"""
fp = r'e:\各种PY程序\28-终极量化交易系统8.4\_run_dsr_walkforward_v7_model.py'
with open(fp, encoding='utf-8') as f:
    lines = f.readlines()

# 找到 checks = [ 行
checks_start = None
for i, line in enumerate(lines):
    if line.strip().startswith('checks = ['):
        checks_start = i
        break

if checks_start is None:
    print('ERROR: checks = [ not found')
    exit(1)

# 找到 checks 列表结束的 ]
checks_end = None
for i in range(checks_start + 1, len(lines)):
    if lines[i].strip() == ']':
        checks_end = i
        break

print(f'checks list: lines {checks_start+1} ~ {checks_end+1}')
print('Current content:')
for i in range(checks_start, checks_end + 1):
    print(f'  {i+1}: {lines[i].rstrip()}')

# 重建 checks 列表
new_checks = [
    'checks = [\n',
    '    ("WF Sharpe CV < 0.5", sharpe_cv < 0.5, f"{sharpe_cv:.4f}"),\n',
    '    ("年化收益 >= 8%", ann_ret >= 0.08, f"{ann_ret*100:.2f}%"),\n',
    '    ("去极端月年化 >= 8%", ann_ret_without >= 0.08, f"{ann_ret_without*100:.2f}%"),\n',
    '    ("最大回撤 <= 15%", max_dd <= 0.15, f"{max_dd*100:.2f}%"),\n',
    '    ("DSR n_trials >= 5", max_pass >= 5, f"{max_pass}"),\n',
    '    ("Window1 Sharpe >= 0.5", windows[1]["sharpe"] >= 0.5, f"{windows[1][\'sharpe\']:.3f}"),\n',
    ']\n',
]

# 替换
new_lines = lines[:checks_start] + new_checks + lines[checks_end + 1:]
with open(fp, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
print('FIXED')
