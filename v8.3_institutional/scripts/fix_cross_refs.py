#!/usr/bin/env python3
"""批量修复v5.9模块中的跨文件导入引用"""
import os
import re

BASE = r'e:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional\src'

# 文件名旧→新映射
FILE_RENAME = {
    'signal_fusion': 'signal_fusion_v59',
    'hedge_engine': 'hedge_engine_v59',
    'hedge_rebalance_integrator': 'hedge_rebalance_v59',
    'risk_controls': 'risk_controls_v59',
    'ml_predictor': 'ml_predictor_v59',
    'kondratiev_cycle': 'kondratiev',
    'multi_layer_hedge_manager': 'multi_layer_hedge',
    'smart_hedge_trigger': 'smart_trigger',
    'tail_risk_hedge': 'tail_risk',
    'volatility_hedge': 'vol_hedge',
    'enhanced_delta_hedge': 'enhanced_delta',
    'multi_model_router': 'model_router',
    'glm5_decision_engine': 'glm5_engine',
    'fin_sentiment_analyzer': 'sentiment',
    'event_driven_factor': 'event_factor',
    'enhanced_signal_fusion': 'enhanced_fusion',
    'signal_independence': 'independence',
    'signal_audit': 'audit',
    'ml_enhanced_trainer': 'enhanced_trainer',
    'ml_optuna_trainer': 'optuna_trainer',
    'ml_significance': 'significance',
    'ml_labeling': 'labeling',
    'statistical_significance': 'stat_sig',
    'pre_deployment_validation': 'pre_deploy',
    'five_year_plan': 'five_year_plan',
    'social_security_etf': 'social_security_etf',
    'greeks_calculator': 'greeks',
    'futures_options_scanner': 'futures_scan',
    'concentration_risk': 'concentration',
}

count = 0
for root, _dirs, files in os.walk(BASE):
    for f in files:
        if not f.endswith('.py'):
            continue
        full = os.path.join(root, f)
        try:
            with open(full, 'r', encoding='utf-8') as fh:
                content = fh.read()
        except Exception: continue

        modified = False
        new_content = content

        for old_name, new_name in FILE_RENAME.items():
            if old_name == f.replace('.py', ''):
                continue  # skip self

            # Fix: from .old_name import  →  from .new_name import
            pat1 = rf"from \.{old_name} import"
            rep1 = rf"from .{new_name} import"
            if re.search(pat1, new_content):
                new_content = re.sub(pat1, rep1, new_content)
                modified = True

            # Fix: from old_name import (no dot - sibling import in same dir)
            pat2 = rf"from {old_name} import"
            rep2 = rf"from .{new_name} import"
            if re.search(pat2, new_content):
                new_content = re.sub(pat2, rep2, new_content)
                modified = True

        if modified:
            with open(full, 'w', encoding='utf-8') as fh:
                fh.write(new_content)
            count += 1
            rel = os.path.relpath(full, BASE)
            print(f'  [FIX] {rel}')

print(f'  {count} files fixed')
