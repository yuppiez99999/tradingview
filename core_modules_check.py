#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
系统核心模块完整性检查报告 - 最终版
======================================
Author: Agnes-2.0 Flash Team
Date: 2026-07-23
"""

import sys
import os

sys.stdout.reconfigure(encoding='utf-8')

def test_module(code, cwd=None):
    """测试模块导入"""
    try:
        if cwd:
            os.chdir(cwd)
        exec(code)
        return True, None
    except Exception as e:
        return False, str(e)[:80]

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    v83_dir = os.path.join(base_dir, 'v8.3_institutional')
    
    print("=" * 80)
    print("系统核心模块完整性检查报告")
    print("=" * 80)
    print()
    
    total_passed = 0
    total_failed = 0
    
    # 1. 数据源层
    print("【1/7】数据源层 (Data Source Layer)")
    data_sources = [
        ("通达信(TDX)", "from utils.tdx_data_source import TDXDataSource; TDXDataSource()", base_dir),
        ("东方财富", "from tools.eastmoney_data_fetcher import EastMoneyDataFetcher; EastMoneyDataFetcher()", base_dir),
        ("Wind MCP", "from tools.wind_mcp_fetcher import wind_get_quote", base_dir),
    ]
    for name, code, cwd in data_sources:
        ok, err = test_module(code, cwd)
        if ok:
            print(f"  ✓ {name}")
            total_passed += 1
        else:
            print(f"  ✗ {name}: {err}")
            total_failed += 1
    print()
    
    # 2. 核心引擎层
    print("【2/7】核心引擎层 (Core Engine Layer)")
    engines = [
        ("Alpha对冲引擎", "from alpha_hedge_engine import AlphaHedgeEngine; AlphaHedgeEngine(account_id='test')", base_dir),
        ("v8.3 HedgeEngine", "sys.path.insert(0, 'src'); from hedging.hedge_engine_v59 import get_hedge_engine; get_hedge_engine()", v83_dir),
        ("v8.3 SignalFusionEngine", "sys.path.insert(0, 'src'); from signals.signal_fusion_v59 import get_fusion_engine; get_fusion_engine()", v83_dir),
    ]
    for name, code, cwd in engines:
        ok, err = test_module(code, cwd)
        if ok:
            print(f"  ✓ {name}")
            total_passed += 1
        else:
            print(f"  ✗ {name}: {err}")
            total_failed += 1
    print()
    
    # 3. 风险管理层
    print("【3/7】风险管理层 (Risk Management Layer)")
    risk_modules = [
        ("TransactionCostModel", "from utils.transaction_cost_model import TransactionCostModel; TransactionCostModel()", base_dir),
        ("RiskAttribution", "from utils.risk_attribution import compute_attribution; compute_attribution()", base_dir),
        ("v8.3 RiskManager", "sys.path.insert(0, 'src'); from risk.risk_manager import RiskManager; RiskManager()", v83_dir),
    ]
    for name, code, cwd in risk_modules:
        ok, err = test_module(code, cwd)
        if ok:
            print(f"  ✓ {name}")
            total_passed += 1
        else:
            print(f"  ✗ {name}: {err}")
            total_failed += 1
    print()
    
    # 4. 衍生品分析
    print("【4/7】衍生品分析 (Derivatives Analysis)")
    derivative_modules = [
        ("Greeks计算", "from derivatives.greeks import compute_greeks", v83_dir),
        ("v8.3 OptionPricing", "sys.path.insert(0, 'src'); from derivatives.greeks import PortfolioGreeks", v83_dir),
    ]
    for name, code, cwd in derivative_modules:
        ok, err = test_module(code, cwd)
        if ok:
            print(f"  ✓ {name}")
            total_passed += 1
        else:
            print(f"  ✗ {name}: {err}")
            total_failed += 1
    print()
    
    # 5. 执行优化层
    print("【5/7】执行优化层 (Execution Optimization)")
    execution_modules = [
        ("ExecutionSelector", "from utils.execution_selector import choose_execution_algorithm; choose_execution_algorithm(1500000, 1500, 1e8)", base_dir),
        ("GreekHedgeManager", "from utils.greek_hedge_manager import GreekHedgeManager; GreekHedgeManager(target_delta=0.0, target_gamma=0.0)", base_dir),
    ]
    for name, code, cwd in execution_modules:
        ok, err = test_module(code, cwd)
        if ok:
            print(f"  ✓ {name}")
            total_passed += 1
        else:
            print(f"  ✗ {name}: {err}")
            total_failed += 1
    print()
    
    # 6. 监控与可视化
    print("【6/7】监控与可视化 (Monitoring & Visualization)")
    monitor_modules = [
        ("GreekExposureDashboard", "from utils.greek_exposure_dashboard import compute_dashboard; compute_dashboard()", base_dir),
    ]
    for name, code, cwd in monitor_modules:
        ok, err = test_module(code, cwd)
        if ok:
            print(f"  ✓ {name}")
            total_passed += 1
        else:
            print(f"  ✗ {name}: {err}")
            total_failed += 1
    print()
    
    # 7. 依赖库
    print("【7/7】核心依赖库 (Core Dependencies)")
    deps = [
        "numpy", "pandas", "scipy", "sklearn",
        "xgboost", "lightgbm", "yfinance", "akshare",
        "matplotlib", "seaborn", "sqlalchemy", "loguru",
        "click", "tqdm", "yaml", "schedule"
    ]
    missing = []
    for dep in deps:
        try:
            __import__(dep)
            print(f"  ✓ {dep}")
            total_passed += 1
        except ImportError:
            print(f"  ✗ {dep} (缺失)")
            missing.append(dep)
            total_failed += 1
    print()
    
    # 总结
    print("=" * 80)
    print("检查结果汇总")
    print("=" * 80)
    print(f"  ✓ 通过: {total_passed}")
    print(f"  ✗ 失败: {total_failed}")
    print(f"  成功率: {total_passed / (total_passed + total_failed) * 100:.1f}%")
    print()
    
    if missing:
        print(f"⚠ 建议安装缺失的依赖库: {', '.join(missing)}")
        print(f"   运行命令: pip install {' '.join(missing)}")
    print("=" * 80)

if __name__ == "__main__":
    main()
