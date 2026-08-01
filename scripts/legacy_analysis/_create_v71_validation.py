"""创建 V7.1 验证脚本 (基于 V7-Model 验证脚本)"""
src = r'e:\各种PY程序\28-终极量化交易系统8.4\_run_dsr_walkforward_v7_model.py'
dst = r'e:\各种PY程序\28-终极量化交易系统8.4\_run_dsr_walkforward_v71.py'

with open(src, encoding='utf-8') as f:
    content = f.read()

# 替换文件名模式: v7_model_regime_aware -> v71_signal_penalty
content = content.replace(
    'lgb_backtest_v7_model_regime_aware*.json',
    'lgb_backtest_v71_signal_penalty*.json'
)
# 替换标题
content = content.replace(
    'V7-Model DSR + Walk-Forward 稳定性验证 (Regime-Aware 特征工程版)',
    'V7.1 DSR + Walk-Forward 稳定性验证 (信号后处理: bull regime 高波动股惩罚)'
)
content = content.replace(
    'V7-Model DSR + Walk-Forward 验证 (Regime-Aware 特征工程)',
    'V7.1 DSR + Walk-Forward 验证 (信号后处理)'
)
# 替换输出文件名
content = content.replace(
    'dsr_walkforward_v7_model_',
    'dsr_walkforward_v71_'
)
# 替换对比表标题
content = content.replace(
    'V7-Model vs V6.2 vs V6 对比:',
    'V7.1 vs V7-Model vs V6.2 对比:'
)
# 替换验收检查标题
content = content.replace(
    'V7-Model 验收检查:',
    'V7.1 验收检查:'
)
content = content.replace(
    'V7-Model 全部验收通过! Regime-Aware 特征成功解决 bull regime 信号失效',
    'V7.1 全部验收通过! 信号后处理成功解决 bull regime 信号失效'
)
content = content.replace(
    'V7-Model 部分验收未通过',
    'V7.1 部分验收未通过'
)

# 修复 V7.1 对比表 (增加 V7-Model 列)
old_compare = '''print("V7.1 vs V7-Model vs V6.2 对比:")
print(f"{'指标':<18} {'V6[bug]':<12} {'V6.2':<12} {'V7-Model':<14} {'V7-V6.2':<10}")
print("-" * 70)
print(f"{'年化收益':<18} {'16.84%':<12} {'14.35%':<12} {f'{ann_ret*100:.2f}%':<14} {f'{(ann_ret-0.1435)*100:+.2f}%':<10}")
print(f"{'最大回撤':<18} {'7.57%':<12} {'7.90%':<12} {f'{max_dd*100:.2f}%':<14} {f'{(max_dd-0.0790)*100:+.2f}%':<10}")
print(f"{'Sharpe':<18} {'1.215':<12} {'1.092':<12} {f'{sharpe:.3f}':<14} {f'{sharpe-1.092:+.3f}':<10}")
print(f"{'峰度':<18} {'4.99':<12} {'6.86':<12} {f'{kurt:.2f}':<14} {f'{kurt-6.86:+.2f}':<10}")
print(f"{'偏度':<18} {'1.74':<12} {'-':<12} {f'{skew:.2f}':<14} {'-':<10}")
print(f"{'WF Sharpe CV':<18} {'0.46':<12} {'0.55':<12} {f'{sharpe_cv:.2f}':<14} {f'{sharpe_cv-0.55:+.2f}':<10}")
print(f"{'DSR n_trials':<18} {'3':<12} {'3':<12} {f'{max_pass}':<14} {f'{max_pass-3:+d}':<10}")
print(f"{'去极端月年化':<18} {'8.92%':<12} {'6.48%':<12} {f'{ann_ret_without*100:.2f}%':<14} {f'{(ann_ret_without-0.0648)*100:+.2f}%':<10}")
w1_sharpe = windows[1]["sharpe"]
w2_sharpe = windows[2]["sharpe"]
print(f"{'Window1 Sharpe':<18} {'-':<12} {'0.330':<12} {w1_sharpe:.3f} {w1_sharpe-0.330:+.3f}")
print(f"{'Window2 Sharpe':<18} {'-':<12} {'1.725':<12} {w2_sharpe:.3f} {w2_sharpe-1.725:+.3f}")'''

new_compare = '''print("V7.1 vs V7-Model vs V6.2 对比:")
print(f"{'指标':<18} {'V6.2':<10} {'V7-Model':<10} {'V7.1':<10} {'V71-V62':<10} {'V71-V7M':<10}")
print("-" * 78)
print(f"{'年化收益':<18} {'14.35%':<10} {'14.50%':<10} {f'{ann_ret*100:.2f}%':<10} {f'{(ann_ret-0.1435)*100:+.2f}%':<10} {f'{(ann_ret-0.1450)*100:+.2f}%':<10}")
print(f"{'最大回撤':<18} {'7.90%':<10} {'10.44%':<10} {f'{max_dd*100:.2f}%':<10} {f'{(max_dd-0.0790)*100:+.2f}%':<10} {f'{(max_dd-0.1044)*100:+.2f}%':<10}")
print(f"{'Sharpe':<18} {'1.092':<10} {'1.023':<10} {f'{sharpe:.3f}':<10} {f'{sharpe-1.092:+.3f}':<10} {f'{sharpe-1.023:+.3f}':<10}")
print(f"{'峰度':<18} {'6.86':<10} {'2.48':<10} {f'{kurt:.2f}':<10} {f'{kurt-6.86:+.2f}':<10} {f'{kurt-2.48:+.2f}':<10}")
print(f"{'偏度':<18} {'-':<10} {'1.02':<10} {f'{skew:.2f}':<10} {'-':<10} {f'{skew-1.02:+.2f}':<10}")
print(f"{'WF Sharpe CV':<18} {'0.55':<10} {'0.74':<10} {f'{sharpe_cv:.2f}':<10} {f'{sharpe_cv-0.55:+.2f}':<10} {f'{sharpe_cv-0.74:+.2f}':<10}")
print(f"{'DSR n_trials':<18} {'3':<10} {'5':<10} {f'{max_pass}':<10} {f'{max_pass-3:+d}':<10} {f'{max_pass-5:+d}':<10}")
print(f"{'去极端月年化':<18} {'6.48%':<10} {'7.17%':<10} {f'{ann_ret_without*100:.2f}%':<10} {f'{(ann_ret_without-0.0648)*100:+.2f}%':<10} {f'{(ann_ret_without-0.0717)*100:+.2f}%':<10}")
w1_sharpe = windows[1]["sharpe"]
w2_sharpe = windows[2]["sharpe"]
print(f"{'Window1 Sharpe':<18} {'0.330':<10} {'0.300':<10} {w1_sharpe:.3f}{'':<5} {w1_sharpe-0.330:+.3f}{'':<5} {w1_sharpe-0.300:+.3f}")
print(f"{'Window2 Sharpe':<18} {'1.725':<10} {'2.008':<10} {w2_sharpe:.3f}{'':<5} {w2_sharpe-1.725:+.3f}{'':<5} {w2_sharpe-2.008:+.3f}")'''

content = content.replace(old_compare, new_compare)

with open(dst, 'w', encoding='utf-8') as f:
    f.write(content)
print(f'V7.1 验证脚本已创建: {dst}')
