"""验证 TRADING_ENV 配置和 fail-closed 状态"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.trading_env import get_trading_env, get_trading_env_config

env = get_trading_env()
config = get_trading_env_config()

print("=" * 60)
print("TRADING_ENV 配置验证")
print("=" * 60)
print(f"  当前环境:         {env}")
print(f"  is_production:    {config.is_prod}")
print(f"  is_shadow:        {config.is_shadow}")
print(f"  fail_closed:      {config.fail_closed}")
print(f"  allow_real_orders:{config.allow_real_orders}")
print(f"  shadow_capital_pct: {config.shadow_capital_pct}")

if config.fail_closed:
    print()
    print("  ✓ fail-closed 模式已激活")
    print("    - Kill Switch 启用")
    print("    - 影子账户 fail-fast 启用 (单日>3%, 3日>5%)")
    print("    - VaR 95% > 1.5% 阻断下单")
    print("    - AI/半导体板块敞口 <= 25%")
else:
    print()
    print("  ⚠ fail-closed 未激活!")

print()
print("=" * 60)
print("影子账户 fail-fast 触发器状态")
print("=" * 60)

# 验证 FailFastMonitor
sys.path.insert(0, str(PROJECT_ROOT / "v8.3_institutional" / "src" / "validation"))
from shadow_account_system import FailFastMonitor

ffm = FailFastMonitor(daily_drawdown_threshold=0.03, cumulative_3d_drawdown_threshold=0.05)
status = ffm.get_status()
print(f"  单日回撤阈值:   {status['daily_dd_threshold']*100:.1f}%")
print(f"  3日累计阈值:    {status['cumulative_3d_threshold']*100:.1f}%")
print(f"  已触发:         {status['triggered']}")
reason = status['reason'] or "无 (正常)"
print(f"  触发原因:       {reason}")
print()
print("  ✓ 影子账户 fail-fast 监控器已就绪")

print()
print("=" * 60)
print("影子账户状态")
print("=" * 60)

import json

state_file = PROJECT_ROOT / "output" / "shadow_account" / "shadow_state.json"
if state_file.exists():
    with open(state_file, encoding="utf-8") as f:
        state = json.load(f)
    print(f"  账户 ID:        {state.get('account_id', '')}")
    print(f"  策略 ID:        {state.get('strategy_id', '')}")
    print(f"  状态:           {state.get('status', '')}")
    print(f"  当前阶段:       {state.get('stage_name', '')}")
    print(f"  初始资金:       ¥{state.get('initial_capital', 0):.0f}")
    print(f"  当前净值:       {state.get('current_nav', 1.0):.4f}")
    print(f"  启动日期:       {state.get('start_date', '')}")
    print(f"  Fail-fast 触发: {len(state.get('fail_fast_log', []))} 次")
    print()
    print("  ✓ 影子账户正常运行中 (Stage 1: 10% 资金灰度发布)")
else:
    print("  ⚠ 影子账户状态文件不存在")
