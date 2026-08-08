# -*- coding: utf-8 -*-
"""
极端压力测试模式 — v5.10 P0-9 重构
========================================
6历史情景 + 蒙特卡洛模拟 + 硬止损检查
"""
import os
import numpy as np
import yaml
import pandas as pd
from datetime import datetime

from core.context import BASE_DIR, logger, ProgressIndicator
from utils.stress_test import (
    generate_stress_report,
    HISTORICAL_SCENARIOS,
    TAIL_HEDGE_THRESHOLD,
    HARD_STOP_MAX_DRAWDOWN,
)


def run_stress_test_mode(args):
    """极端压力测试模式 v5.10 — 6历史情景+蒙特卡洛+硬止损检查"""
    print("\n🛡️ 极端压力测试 v5.10")
    print("=" * 70)
    print(f"尾部保护阈值: {TAIL_HEDGE_THRESHOLD:.0%}")
    print(f"硬止损阈值: {HARD_STOP_MAX_DRAWDOWN:.1%}")
    print()

    # 1. 加载组合配置
    portfolio_path = os.path.join(BASE_DIR, 'config', 'portfolio.yaml')
    if not os.path.exists(portfolio_path):
        print("❌ 未找到 portfolio.yaml，使用默认配置")
        default_positions = {
            '300750.SZ': {'shares': 1000, 'sector': '高端制造'},
            '688041.SH': {'shares': 800, 'sector': '高端制造'},
            '002371.SZ': {'shares': 600, 'sector': '高端制造'},
            '300308.SZ': {'shares': 500, 'sector': '高端制造'},
            '688981.SH': {'shares': 400, 'sector': '高端制造'},
            '000425.SZ': {'shares': 700, 'sector': '高端制造'},
            '601088.SH': {'shares': 1000, 'sector': '顺周期'},
            '600219.SH': {'shares': 600, 'sector': '顺周期'},
            '600019.SH': {'shares': 500, 'sector': '顺周期'},
            '518880.SH': {'shares': 2000, 'sector': '资源'},
            '000792.SZ': {'shares': 400, 'sector': '资源'},
            '600276.SH': {'shares': 600, 'sector': '防御'},
            '603259.SH': {'shares': 500, 'sector': '防御'},
            '002422.SZ': {'shares': 400, 'sector': '防御'},
        }
        positions = default_positions
    else:
        with open(portfolio_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        positions = config.get('positions', {})

    print(f"✅ 加载持仓: {len(positions)} 个标的")

    # 2. 获取当前价格和权重
    prices = {}
    total_value = 0.0
    for code in positions.keys():
        cache_file = os.path.join(BASE_DIR, 'data', 'cache', f'kline_{code}_daily.parquet')
        if os.path.exists(cache_file):
            try:
                df = pd.read_parquet(cache_file)
                if len(df) > 0 and 'close' in df.columns:
                    prices[code] = float(df['close'].iloc[-1])
                    total_value += positions[code]['shares'] * prices[code]
            except Exception:
                pass

    if total_value <= 0:
        print("❌ 无法获取有效价格数据")
        return

    weights = np.array([positions[code]['shares'] * prices.get(code, 1) / total_value
                        for code in positions.keys()])
    names = list(positions.keys())
    sectors = {code: positions[code].get('sector', '未分类') for code in names}

    print(f"✅ 组合总市值: ¥{total_value:.2f}")
    print()

    # 3. 加载历史收益率数据
    returns_data = []
    valid_codes = []
    for code in names:
        cache_file = os.path.join(BASE_DIR, 'data', 'cache', f'kline_{code}_daily.parquet')
        if os.path.exists(cache_file):
            try:
                df = pd.read_parquet(cache_file)
                if 'close' in df.columns and len(df) > 60:
                    rets = df['close'].pct_change().dropna().values
                    returns_data.append(rets[-252:] if len(rets) >= 252 else rets)
                    valid_codes.append(code)
            except Exception:
                pass

    if len(valid_codes) == 0:
        print("❌ 无法获取历史收益率数据")
        return

    # 对齐长度
    min_len = min(len(r) for r in returns_data)
    returns_aligned = np.array([r[-min_len:] for r in returns_data])
    weights_aligned = np.array([weights[names.index(code)] for code in valid_codes])
    names_aligned = valid_codes
    sectors_aligned = {code: sectors[code] for code in valid_codes}

    print(f"✅ 历史数据: {min_len} 交易日, {len(valid_codes)} 个标的")
    print()

    # 4. 生成压力测试报告
    report = generate_stress_report(
        returns=returns_aligned,
        weights=weights_aligned,
        names=names_aligned,
        sectors=sectors_aligned,
    )

    print(report)

    # 5. 保存报告
    output_dir = os.path.join(BASE_DIR, 'reports')
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_file = os.path.join(output_dir, f'stress_test_{timestamp}.md')

    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report)

    print(f"\n✅ 报告已保存: {report_file}")
    print("=" * 70)
