#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
系统健康检查脚本 - 验证模块加载与配置一致性
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

print("=" * 70)
print("系统健康检查")
print("=" * 70)

# 1. 验证 ConfigManager 资金配置
print("\n[1] ConfigManager 资金配置加载:")
try:
    from utils.config_manager import get_portfolio_config, list_available_configs, get_config_source
    cfg = get_portfolio_config()
    stock = cfg.get("stock_etf_capital")
    hedge = cfg.get("hedge_capital")
    total = (stock or 0) + (hedge or 0)
    source = get_config_source("portfolio")
    print(f"  stock_etf_capital = {stock:,}")
    print(f"  hedge_capital     = {hedge:,}")
    print(f"  total             = {total:,}")
    print(f"  source            = {source}")
    print(f"  配置: {'OK' if total == 5_000_000 else 'WARN: total != 5,000,000'}")
except Exception as e:
    print(f"  FAIL: {e}")

# 2. 验证 positions.json 资金配置
print("\n[2] positions.json 资金配置:")
try:
    with open(PROJECT_ROOT / "config" / "positions.json", encoding="utf-8") as f:
        data = json.load(f)
    meta = data.get("meta", {})
    stock = meta.get("stock_etf_capital")
    hedge = meta.get("hedge_capital")
    total = (stock or 0) + (hedge or 0)
    print(f"  stock_etf_capital = {stock:,}")
    print(f"  hedge_capital     = {hedge:,}")
    print(f"  total             = {total:,}")
except Exception as e:
    print(f"  FAIL: {e}")

# 3. 验证 ConfigManager 可用配置列表
print("\n[3] ConfigManager 可用配置:")
try:
    available = list_available_configs()
    for item in available[:10]:
        print(f"  - {item.get('name'):20s} | source: {item.get('source')}")
    print(f"  ... 共 {len(available)} 个配置")
except Exception as e:
    print(f"  FAIL: {e}")

# 4. 验证 9 个 v8.5 模块导入
print("\n[4] v8.5 9 个模块导入:")
v85_modules = [
    ("DataPipeline", "data.data_pipeline"),
    ("EnvironmentIsolation", "src.utils.environment_isolation"),
    ("GlobalTimeService", "src.utils.timesync"),
    ("VegaMonitor", "risk.vega_monitor"),
    ("LiquidityMonitor", "risk.liquidity_monitor"),
    ("ExtremeValueAnalyzer", "risk.evt_tail_risk"),
    ("PurgedKFold", "model_validation.purged_kfold_cv"),
    ("FactorDecayMonitor", "model_monitoring.factor_decay_monitor"),
    ("ShadowAccount", "validation.shadow_account_system"),
]
passed = 0
failed = 0
for cls_name, mod_path in v85_modules:
    try:
        __import__(mod_path)
        print(f"  OK  {cls_name:30s} <- {mod_path}")
        passed += 1
    except Exception as e:
        print(f"  FAIL {cls_name:30s} <- {mod_path}: {e}")
        failed += 1
print(f"  小结: {passed}/9 通过, {failed} 失败")

# 5. 验证核心风控模块
print("\n[5] 核心风控模块:")
risk_modules = [
    ("KillSwitch", "utils.kill_switch"),
    ("CircuitBreaker", "utils.circuit_breaker"),
    ("RiskGuardIntegrator", "utils.risk_guard_integrator"),
    ("HedgeExecutionEngine", "utils.hedge_execution_engine"),
    ("VolTargetController", "utils.vol_target_controller"),
    ("ProtectivePutEngine", "utils.protective_put_engine"),
    ("UnifiedRiskCockpit", "v8.3_institutional.src.risk.unified_risk_cockpit"),
    ("PortfolioOptimizer", "utils.portfolio_optimizer"),
    ("SignalFusionEngine", "utils.signal_fusion"),
    ("ConfigManager", "utils.config_manager"),
]
for cls_name, mod_path in risk_modules:
    try:
        __import__(mod_path)
        print(f"  OK  {cls_name:30s} <- {mod_path}")
    except Exception as e:
        print(f"  FAIL {cls_name:30s} <- {mod_path}: {e}")

# 6. 验证 KillSwitch 实例化与 broker_callback 注册能力
print("\n[6] KillSwitch 实例化测试:")
try:
    from utils.kill_switch import KillSwitch
    from utils.config_manager import clear_config_cache
    clear_config_cache()
    ks = KillSwitch()
    print(f"  KillSwitch 实例化: OK")
    print(f"  ks.config keys: {list(ks.config.keys())[:5]}")
    has_set_callback = hasattr(ks, "set_broker_callback")
    has_execute = hasattr(ks, "execute_kill_switch")
    has_estimate = hasattr(ks, "_estimate_margin_from_positions")
    print(f"  set_broker_callback: {'OK' if has_set_callback else 'MISSING'}")
    print(f"  execute_kill_switch: {'OK' if has_execute else 'MISSING'}")
    print(f"  _estimate_margin_from_positions: {'OK' if has_estimate else 'MISSING'}")
    # 实际测试保证金检查
    status = ks.check_margin_status()
    print(f"  check_margin_status: level={status.get('level')}, can_trade={status.get('can_trade')}")
except Exception as e:
    print(f"  FAIL: {e}")
    import traceback
    traceback.print_exc()

# 7. 验证 daily_workflow.py 可导入
print("\n[7] daily_workflow.py 导入测试:")
try:
    sys.path.insert(0, str(PROJECT_ROOT / "v8.3_institutional"))
    import daily_workflow
    v85_ready = getattr(daily_workflow, "V85_READY", None)
    failures = getattr(daily_workflow, "_V85_FAILURES", [])
    print(f"  daily_workflow 导入: OK")
    print(f"  V85_READY = {v85_ready}")
    if failures:
        print(f"  _V85_FAILURES = {failures}")
    else:
        print(f"  _V85_FAILURES = [] (9/9 全部就绪)")
    # 检查 phase 方法
    phases = ["phase_check", "phase_signal", "phase_execute", "phase_report",
              "phase_shadow_monitor", "phase_factor_kill_switch"]
    DailyWorkflow = getattr(daily_workflow, "DailyWorkflow", None)
    if DailyWorkflow:
        for ph in phases:
            has_it = hasattr(DailyWorkflow, ph)
            print(f"  {ph}: {'OK' if has_it else 'MISSING'}")
except Exception as e:
    print(f"  FAIL: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 70)
print("系统健康检查完成")
print("=" * 70)
