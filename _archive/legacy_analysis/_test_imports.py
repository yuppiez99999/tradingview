"""验证导入和板块约束"""
import sys

sys.path.insert(0, '.')
sys.path.insert(0, 'research')

from research.backtest_runner import (
    _BACKTEST_SECTOR_MAP,
    _DRAWDOWN_BREAKER_FACTOR,
    _DRAWDOWN_BREAKER_THRESHOLD,
    _DRAWDOWN_SEVERE_FACTOR,
    _DRAWDOWN_SEVERE_THRESHOLD,
)
from utils.risk_constraints import enforce_hard_constraints

# Test sector map covers all 23 symbols
test_symbols = [
    '588000','688041','002371','688981','300308','000425','601088','600276',
    '600900','515180','600036','518880','300274','603019','600089','688017',
    '600219','600019','000680','000333','000408','000975','002422'
]
missing = [s for s in test_symbols if s not in _BACKTEST_SECTOR_MAP]
print("missing:", missing if missing else "NONE (all 23 covered)")

from collections import Counter  # noqa: E402

sectors = Counter([_BACKTEST_SECTOR_MAP[s] for s in test_symbols])
print("sector distribution:")
for sec, count in sorted(sectors.items(), key=lambda x: -x[1]):
    print(f"  {sec}: {count}")

# Test enforce_hard_constraints on a problematic case
test_weights = {'688041': 0.15, '300308': 0.15, '688981': 0.15, '588000': 0.10, '603019': 0.10}
clamped, violations = enforce_hard_constraints(
    test_weights, max_weight=0.15, sector_map=_BACKTEST_SECTOR_MAP, max_sector=0.25
)
tech_before = sum(w for s, w in test_weights.items() if _BACKTEST_SECTOR_MAP.get(s) == "科技")
tech_after = sum(w for s, w in clamped.items() if _BACKTEST_SECTOR_MAP.get(s) == "科技")
print("\nsector constraint test:")
print(f"  input: {test_weights}")
print(f"  tech total before: {tech_before:.2%}")
print(f"  violations: {violations if violations else 'NONE'}")
print(f"  clamped: {clamped}")
print(f"  tech total after: {tech_after:.2%}")

print("\ndrawdown breaker params:")
print(f"  warning: dd>={_DRAWDOWN_BREAKER_THRESHOLD:.0%} -> factor={_DRAWDOWN_BREAKER_FACTOR}")
print(f"  severe:  dd>={_DRAWDOWN_SEVERE_THRESHOLD:.0%} -> factor={_DRAWDOWN_SEVERE_FACTOR}")
