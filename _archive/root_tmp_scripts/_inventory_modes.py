"""盘点主入口文件的本地 run_ 定义 vs MODES 引用"""
import re

c = open('量化策略系统_统一入口_v8.6.py', encoding='utf-8').read()

# 所有本地 def run_ 定义
defs = re.findall(r'^def (run_\w+)', c, re.M)
print('=== 本地 def run_ 定义 (%d 个, 去重 %d) ===' % (len(defs), len(set(defs))))
for d in sorted(set(defs)):
    print('  ' + d)

# MODES 列表引用的 handler — 匹配 ('--flag', 'dest', 'help', handler) 形式
# 用更宽松的正则: 行尾的 run_xxx)
modes = re.findall(r"'[^']+',\s*\w+\),\s*(run_\w+)\)", c)
# 上面不准, 换一种: 找 MODES 元组里逗号后的 run_xxx)
modes = re.findall(r",\s+(run_\w+)\),?\s*$", c, re.M)
print()
print('=== MODES 引用的 handler (%d 个, 去重 %d) ===' % (len(modes), len(set(modes))))
for m in sorted(set(modes)):
    print('  ' + m)

local_set = set(defs)
import_deps = [m for m in sorted(set(modes)) if m not in local_set]
print()
print('=== 依赖 import 且无本地定义 (%d 个) — 删除 import 块会 NameError ===' % len(import_deps))
for m in import_deps:
    print('  ' + m)

# 同时检查: import 列表里导入但 MODES 未引用的 (纯死代码)
imported = [
    'run_ai_decision', 'run_commodity_fundamentals', 'run_comps_mode', 'run_daily_workflow',
    'run_dcf_mode', 'run_etf_flow_monitor', 'run_fifteen_five_analysis', 'run_futures_options_scan',
    'run_gemma_analyze_mode', 'run_hedge_detail_mode', 'run_hedge_mode', 'run_hedge_rebalance_joint',
    'run_kommo_monitor', 'run_kondratiev_analysis', 'run_kronos_predict_mode', 'run_macro_analysis',
    'run_ml_significance_mode', 'run_portfolio_optimization', 'run_risk_monitor',
    'run_social_security_analysis', 'run_unified_monitor',
]
modes_set = set(modes)
unused_imports = [i for i in imported if i not in modes_set]
print()
print('=== import 列表导入但 MODES 未引用 (%d 个) — 纯死代码 import ===' % len(unused_imports))
for m in unused_imports:
    print('  ' + m)
