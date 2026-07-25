# P0 Issues Research Plan

## Status
- **Completed**: 6 direct code bugs fixed (Bug 1-6)
- **In Progress**: 3 complex architectural modules

## Completed Fixes (Bug 1-6)
1. Bug 1-3: Simple variable/type fixes
2. Bug 4: KillSwitch API compatibility - added `margin_usage` parameter and `can_trade`/`can_open` fields
3. Bug 5: Mock value replacement - changed to raise NotImplementedError
4. Bug 6: Alpha hedge engine integration with KillSwitch

## Remaining Tasks

### Task 1: Data Pipeline Fail-Fast Mechanism
**Goal**: Implement fail-fast data validation in the data loading pipeline
**Files**: `utils/hedge_rebalance_backtest.py` (BacktestDataLoader class)
**Key Issues**:
- No early validation of data quality before backtest execution
- Missing data should trigger immediate failure rather than silent fallback
- Need data completeness checks (coverage >80%, missing rate <10%)

### Task 2: Unified Risk Control Circuit Breaker + VaR Backtesting
**Goal**: Integrate VaR backtesting with the risk control system
**Files**: 
- `utils/var_backtest.py` (already exists)
- `alpha_hedge_engine.py` (RiskControl class)
- `utils/kill_switch.py` (already fixed)
**Key Issues**:
- VaR backtesting results not used in real-time risk decisions
- Need to connect traffic light signals to kill switch triggers
- Daily VaR monitoring should update risk model covariance matrix

### Task 3: Alpha Pipeline Fixes
**Goal**: Fix alpha factor evaluation and lifecycle management
**Files**:
- `utils/alpha_factor_library.py` (factor computation)
- `utils/alpha_evaluator.py` (IC/ICIR calculation, decay detection)
**Key Issues**:
- Factor IC calculation uses Spearman but needs Pearson for normality
- Decay detection window too short (60 days vs recommended 120 days)
- Missing factor orthogonality check before adding to portfolio
- Factor registry in hedge_rebalance_backtest not synchronized with alpha_evaluator

## Acceptance Criteria
1. Data pipeline fails fast when coverage <80% or missing rate >10%
2. VaR traffic light RED triggers automatic kill switch L2
3. All factors pass orthogonality check (incremental IC >0.01) before being added
4. Factor decay window extended to 120 days with BIC penalty for complexity
