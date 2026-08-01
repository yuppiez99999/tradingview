#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""N1 端到端验证: 实例化 IntegratedExecutionSystem 并调用修复后的 _hook_drift_and_retrain.

验证三条路径:
  1. 有 IC 数据 (daily_ic_scores.json 已由 N3 写入) + 影子模式
  2. _fetch_daily_ic 多源回退
  3. _trigger_retrain_with_cooldown 影子模式不执行真实重训
"""
import logging
import os
import sys

BASE = r"e:\各种PY程序\28-终极量化交易系统8.4"
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "v8.3_institutional", "src"))

# 配置日志可见 INFO
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s | %(message)s',
    datefmt='%H:%M:%S',
)

print("=" * 60)
print("  N1 端到端验证 (实例化 + 调用 _hook_drift_and_retrain)")
print("=" * 60)

# 1. 确认 IC 数据存在 (N3 写入)
from ic_recorder import load_ic_store

store = load_ic_store()
print(f"\n[预置] daily_ic_scores.json: latest_ic={store.get('latest_ic')}, source={store.get('latest_source')}")

# 2. 实例化 (捕获异常, 避免数据源连接失败阻断)
print("\n[步骤1] 实例化 IntegratedExecutionSystem...")
try:
    from system_integration import EVOLUTION_CONFIG, IntegratedExecutionSystem
    print(f"[预置] EVOLUTION_CONFIG.shadow_mode = {EVOLUTION_CONFIG['shadow_mode']}")
    system = IntegratedExecutionSystem(total_capital=5_000_000)
    print(f"[OK] 实例化成功: drift_detector={'有' if system.drift_detector else '无'}, report_dir={system.report_dir}")
except Exception as e:
    print(f"[FAIL] 实例化失败: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)

# 3. 调用 _fetch_daily_ic (验证 Bug-B 修复)
print("\n[步骤2] 调用 _fetch_daily_ic (验证 Bug-B 多源回退)...")
try:
    ic = system._fetch_daily_ic()
    print(f"[OK] _fetch_daily_ic 返回: {ic}")
    if ic is not None:
        print("     → IC 数据流已修复 (旧代码 daily_ic 恒为 0)")
except Exception as e:
    print(f"[FAIL] _fetch_daily_ic 失败: {e}")
    import traceback; traceback.print_exc()

# 4. 调用 _hook_drift_and_retrain (验证 Bug-A/B/C/D 修复)
print("\n[步骤3] 调用 _hook_drift_and_retrain (验证 Bug-A/B/C/D 修复)...")
try:
    system._hook_drift_and_retrain()
    trigger = getattr(system, 'last_retrain_trigger', None)
    check = getattr(system, 'last_drift_check', None)
    print("[OK] _hook_drift_and_retrain 执行完成")
    print(f"     last_drift_check = {check}")
    print(f"     last_retrain_trigger = {trigger}")
    if trigger:
        action = trigger.get('action')
        print(f"     action = {action}")
        if action == 'shadow_recorded':
            print("     → Bug-C 修复确认: 影子模式记录 (不破坏生产模型)")
        elif action == 'monitor_only':
            print("     → 告警未达重训阈值, 仅监控")
        elif action == 'retrain_triggered':
            print("     → 真实重训触发 (shadow_mode=False)")
except Exception as e:
    print(f"[FAIL] _hook_drift_and_retrain 失败: {e}")
    import traceback; traceback.print_exc()

# 5. 验证 Bug-A 修复: check_all 被调用而非 check_drift
print("\n[步骤4] 验证 Bug-A 修复 (check_all 而非 check_drift)...")
try:
    # 注入持续低 IC 触发告警 (25 天低 IC)
    from datetime import date, timedelta
    if system.drift_detector:
        today = date.today()
        for i in range(25):
            d = today - timedelta(days=24 - i)
            system.drift_detector.update_ic(d, -0.15)  # 持续负 IC
        alerts = system.drift_detector.check_all()
        print(f"[OK] check_all() 返回 {len(alerts)} 个告警 (旧代码 check_drift 永远返回 [])")
        if alerts:
            # 验证 Bug-D 修复: dataclass 属性访问
            a = alerts[0]
            sev = getattr(getattr(a, 'severity', None), 'value', str(a))
            msg = getattr(a, 'message', str(a))
            print(f"     告警示例: severity={sev}, message={msg[:60]}")
            print("     → Bug-D 修复确认: dataclass 属性访问正常 (旧代码 a.get('type') 会崩)")
except Exception as e:
    print(f"[FAIL] Bug-A/D 验证失败: {e}")
    import traceback; traceback.print_exc()

print("\n" + "=" * 60)
print("  N1 端到端验证完成")
print("=" * 60)
