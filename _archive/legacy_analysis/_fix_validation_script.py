"""修复 _run_dsr_walkforward_v7_model.py 的 f-string 语法错误"""
fp = r'e:\各种PY程序\28-终极量化交易系统8.4\_run_dsr_walkforward_v7_model.py'
with open(fp, encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if 'Window1 Sharpe' in line and 'windows[1]' in line:
        new_lines.append('w1_sharpe = windows[1]["sharpe"]\n')
        new_lines.append('w2_sharpe = windows[2]["sharpe"]\n')
        new_lines.append('print(f"{\'Window1 Sharpe\':<18} {\'-\':<12} {\'0.330\':<12} {w1_sharpe:.3f} {w1_sharpe-0.330:+.3f}")\n')
    elif 'Window2 Sharpe' in line and 'windows[2]' in line:
        new_lines.append('print(f"{\'Window2 Sharpe\':<18} {\'-\':<12} {\'1.725\':<12} {w2_sharpe:.3f} {w2_sharpe-1.725:+.3f}")\n')
    else:
        new_lines.append(line)

with open(fp, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
print('FIXED')
