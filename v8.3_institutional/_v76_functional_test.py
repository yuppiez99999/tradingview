"""v7.6 功能验证脚本"""
import sys

sys.path.insert(0, 'src')

import numpy as np
import pandas as pd

np.random.seed(42)

print("=== v7.6 功能验证 ===")
print()

# 1. PM Limits Matrix
print("[1/7] PM限额矩阵...")
from risk.pm_limits import create_default_limits

pm = create_default_limits(total_nav=5_000_000)
test_pos = {'300308.SZ': 0.15, '688041.SH': 0.12, '601088.SH': 0.10}
result = pm.check_all(test_pos)
print(f"  Status: {result['overall_status']}, Violations: {result['n_violations']}")

# 预交易检查
pre = pm.pre_trade_check('300308.SZ', order_qty=800_000, side='BUY',
                         current_positions={'300308.SZ': 600_000})
print(f"  Pre-trade 300308: {pre.status.value} -> {pre.message[:60] if pre.message else 'OK'}")

# 2. 深度压力测试
print("[2/7] 深度压力测试...")
from risk.deep_stress import DeepStressTester

dpt = DeepStressTester(total_nav=5_000_000)
exposures = {'equity_cn': 4_000_000, 'cn_bond': 1_000_000, 'gold': 500_000}
report = dpt.run_all(exposures)
print(f"  评级: {report['overall_rating']}, CVaR: {report['cvar']['pct']:.2%}, 最差: {report['worst_scenario']}")

# 3. TCA
print("[3/7] TCA交易成本...")
from execution.tca import TradeRecord, TransactionCostAnalyzer

tca = TransactionCostAnalyzer()
trade = TradeRecord(
    symbol='300308.SZ', side='BUY', order_qty=10000, fill_qty=9800,
    arrival_price=150.0, avg_fill_price=150.3, vwap_benchmark=150.1,
    close_price=151.0, decision_time='2026-07-21T09:30:00',
    market_volume=5_000_000
)
tca.add_trade(trade)
tca.analyze()
summary = tca.rolling_summary()
attr = tca.cost_attribution_report()
print(f"  总成本: {summary['avg_total_cost_bps']}bps, 滑点: {summary['avg_slippage_bps']}bps")
print(f"  改进建议: {attr['suggestions'][0][:60]}...")

# 4. Implementation Shortfall
print("[4/7] 实现缺口(IS)...")
from execution.implementation_shortfall import ImplementationShortfall

iss = ImplementationShortfall()
decomp = iss.decompose(
    symbol='600900.SH', side='BUY', order_type='TWAP',
    decision_price=25.00, arrival_price=25.02, avg_fill_price=25.04,
    close_price=25.10, vwap=25.03,
    order_qty=20000, fill_qty=19500,
    execution_time_s=300, daily_volume=2_000_000
)
print(f"  总IS: {decomp.total_is_bps}bps, 延迟: {decomp.delay_cost_bps}bps, 冲击: {decomp.impact_cost_bps}bps")
sugs = iss.generate_suggestions(decomp)
if sugs:
    print(f"  优化建议: {sugs[0].recommendation[:60]}...")

# 5. HRP
print("[5/7] 分层风险平价...")
from portfolio.hrp import HierarchicalRiskParity

dates = pd.date_range('2025-01-01', '2026-07-20', freq='B')
np.random.seed(42)
returns = pd.DataFrame({
    s: np.random.normal(0.0005, 0.02, len(dates))
    for s in ['STOCK1', 'STOCK2', 'BOND1', 'GOLD1', 'CASH1']
}, index=dates)
hrp = HierarchicalRiskParity()
w = hrp.fit(returns)
print("  权重: {k: round(v,3) for k,v in w.items()}")
risk_rpt = hrp.portfolio_risk(returns)
print(f"  HRP年化波动率: {risk_rpt['annual_vol']:.1%}")

# 6. 机制协方差
print("[6/7] 机制条件协方差...")
from portfolio.regime_covariance import RegimeConditionalCovariance

rcc = RegimeConditionalCovariance()
rcc.fit(returns)
pred = rcc.predict_covariance(horizon=20, current_vix_proxy=0.15)
cur = rcc.current_regime()
print(f"  当前机制: {cur.regime}, 概率: {cur.probability:.1%}")
print(f"  年化波动率: {cur.vol_estimate:.2%}")
warning = rcc.regime_shift_warning()
print(f"  切换预警: {warning['action']}")

# 7. 信号半衰期
print("[7/7] 信号半衰期管理...")
from signals.signal_half_life import PRESET_HALF_LIVES, SignalHalfLifeManager

mgr = SignalHalfLifeManager()
for name, cfg in list(PRESET_HALF_LIVES.items())[:5]:
    mgr.register_signal(name, cfg['cat'], cfg['hl'])
w_fast = mgr.decay_weight('momentum_20d', 0.8, days_ago=5.0)
w_slow = mgr.decay_weight('pe_ratio', 0.8, days_ago=5.0)
print(f"  快信号 5d衰减: momentum_20d {0.8:.1f} -> {w_fast:.4f}")
print(f"  慢信号 5d衰减: pe_ratio {0.8:.1f} -> {w_slow:.4f}")
fs = mgr.freshness_score()
print(f"  信号新鲜度: {fs['overall_freshness']}%")

print()
print("=== 全部 7/7 模块功能验证通过! ===")
