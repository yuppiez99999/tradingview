# -*- coding: utf-8 -*-
"""
止损配置模式 — v5.10 P0-9 重构
========================================
查看/更新止损止盈规则，含ATR动态止损
"""
import os
import yaml

from core.context import BASE_DIR, logger


def run_stop_loss_config_mode(args):
    """止损配置模式 v5.10 — 查看/更新止损止盈规则"""
    config_path = os.path.join(BASE_DIR, 'config', 'stop_loss_rules_auto.yaml')
    print("\n🛡️ 止损止盈配置 v5.10")
    print("=" * 70)

    if not os.path.exists(config_path):
        print("❌ 配置文件不存在，运行以下命令生成:")
        print("  python scripts/generate_stop_loss_rules.py --regenerate")
        return

    with open(config_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)

    # 显示全局设置
    gs = data.get('global_settings', {})
    print(f"版本: {data.get('version', 'N/A')}")
    print(f"更新时间: {data.get('updated', 'N/A')}")
    print(f"\n全局设置:")
    print(f"  ATR动态止损: {'启用' if gs.get('atr_enabled') else '禁用'}")
    print(f"  ATR周期: {gs.get('atr_period', 14)}")
    print(f"  预警阈值: {gs.get('warning_threshold_pct', 5.0):.1f}%")
    print(f"  紧急阈值: {gs.get('critical_threshold_pct', 2.0):.1f}%")
    print(f"  启用追踪止损: {gs.get('enable_trailing_stop_pct', 10.0):.0f}%")

    # 显示标的规则
    assets = data.get('assets', [])
    print(f"\n标的配置 ({len(assets)} 只):")
    print("-" * 70)
    print(f"{'代码':<12} {'名称':<10} {'板块':<8} {'基准价':>8} {'ATR止损':>8} {'固定止损':>8} {'风险'}")
    print("-" * 70)

    risk_summary = {'high': 0, 'medium': 0, 'low': 0}
    for a in assets:
        code = a.get('code', '')
        name = a.get('name', '')[:8]
        sector = a.get('sector', '')[:6]
        base = a.get('base_price', 0)
        atr_sl = a.get('atr_stop_loss_price', 0)
        fixed_sl = a.get('stop_loss_price', 0)
        risk = a.get('risk_level', 'N/A')
        risk_summary[risk] = risk_summary.get(risk, 0) + 1
        print(f"{code:<12} {name:<10} {sector:<8} {base:>8.2f} {atr_sl:>8.2f} {fixed_sl:>8.2f} {risk}")

    print("-" * 70)
    print(f"\n风险分布: 高风险 {risk_summary.get('high', 0)} "
          f"| 中风险 {risk_summary.get('medium', 0)} "
          f"| 低风险 {risk_summary.get('low', 0)}")
    print(f"\n配置位置: {config_path}")
    print(f"\n更新命令: python scripts/generate_stop_loss_rules.py --regenerate")
    print("=" * 70)
