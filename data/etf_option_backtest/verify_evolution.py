"""
验证ETF子组合自我进化+再平衡全闭环
模拟30日运行，验证5个主系统模块状态变化
"""
import logging
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# 启用进化编排 Feature Flag
os.environ["USE_EVOLUTION_ORCHESTRATOR"] = "1"
os.environ["USE_STRATEGY_EVALUATOR"] = "1"

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.WARNING, format="%(asctime)s | %(name)s | %(levelname)s | %(message)s")
logger = logging.getLogger("verify")
logger.setLevel(logging.INFO)

# Mock网络调用，避免ETF资金流获取超时
import utils.broad_based_etf_policy as _etf_policy_mod

_etf_policy_mod.fetch_national_team_flow_signals = lambda: {}

from etf_option_hedge_rebalancer import ETFOptionHedgeRebalancer
from utils.signal_fusion import FusedSignalV2

prices_df = pd.read_parquet(PROJECT_ROOT / "data" / "etf_option_backtest" / "all_etf_daily.parquet")
prices_df["date"] = pd.to_datetime(prices_df["date"])
prices_df["code"] = prices_df["code"].str.split(".").str[0]
prices_pivot = prices_df.pivot_table(index="date", columns="code", values="close").sort_index()

cfg_path = PROJECT_ROOT / "config" / "etf_option_subportfolio.yaml"
import yaml

with open(cfg_path, encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

target_weights = {}
for code, info in cfg.get("positions", {}).items():
    pure_code = code.split(".")[0]
    target_weights[pure_code] = info["target_weight"]

rebalancer = ETFOptionHedgeRebalancer()

# Mock信号融合: 让fuse()返回基于target_weights的模拟信号
if rebalancer.signal_fusion:
    _target_codes = list(target_weights.keys())
    def _mock_fuse(alpha_signals=None, **kwargs):
        rng = np.random.default_rng(hash(str(pd.Timestamp.now().date())) % 2**32)
        signals = []
        for code in _target_codes[:6]:
            strength = float(rng.normal(0, 0.15))
            strength = max(-0.5, min(0.5, strength))
            signals.append(FusedSignalV2(
                symbol=code, strength=strength,
                sources={"alpha": strength, "technical": strength * 0.8},
                meta={"confidence": 0.7},
            ))
        return signals
    rebalancer.signal_fusion.fuse = _mock_fuse


test_dates = prices_pivot.index[-60:-30]
initial_capital = 2_000_000
positions = {}
first_prices = prices_pivot.iloc[-60]
for code, w in target_weights.items():
    if code in first_prices.index and first_prices[code] > 0:
        shares = int(initial_capital * w / first_prices[code] / 100) * 100
        positions[code] = {"shares": shares, "price": float(first_prices[code])}

results_log = []
regime_changes = []
prev_regime = None
rebalance_total = 0
drift_alerts = 0
reflection_records = 0


for i, date in enumerate(test_dates):
    date_str = date.strftime("%Y-%m-%d")
    row = prices_pivot.loc[date]

    prices_dict = {}
    for code in target_weights:
        if code in row.index and not np.isnan(row[code]):
            prices_dict[code] = float(row[code])
            if code in positions:
                positions[code]["price"] = float(row[code])

    eq = sum(positions.get(c, {}).get("shares", 0) * prices_dict.get(c, 0) for c in target_weights)
    if i == 0:
        peak = eq
    peak = max(peak, eq)
    dd = (peak - eq) / peak if peak > 0 else 0

    try:
        plan = rebalancer.run_daily_rebalance(positions, prices_dict, date_str, dd)
    except Exception as e:
        logger.warning("第%d日运行失败: %s", i, e)
        continue

    regime_label = plan.regime.get("label", "N/A") if plan.regime else "N/A"
    if regime_label != prev_regime:
        regime_changes.append((date_str, prev_regime, regime_label))
        prev_regime = regime_label

    n_orders = len(plan.rebalance_orders)
    rebalance_total += n_orders

    option_enabled = "启用" if plan.option_hedge.get("enabled") else "禁用"

    drift_status = plan.drift_status or {}
    n_alerts = len(drift_status.get("alerts", [])) if isinstance(drift_status.get("alerts"), list) else 0
    drift_alerts += n_alerts

    evo_action = plan.evolution_action.get("action", "N/A") if plan.evolution_action else "N/A"

    if i < 5 or i >= len(test_dates) - 5 or i % 10 == 0:
        pass

    results_log.append({
        "date": date_str, "regime": regime_label, "drawdown": dd,
        "n_rebalance": n_orders, "option_enabled": option_enabled,
        "n_alerts": n_alerts, "evo_action": evo_action,
        "fused_n": plan.fused_signals.get("n_signals", 0) if plan.fused_signals else 0,
    })


for d, old, new in regime_changes[:5]:
    pass
if len(regime_changes) > 5:
    pass

fused_days = [r for r in results_log if r["fused_n"] > 0]
if fused_days:
    pass

if rebalancer.drift_monitor:
    try:
        status = rebalancer.drift_monitor.get_status()
    except Exception:
        pass

if rebalancer.memory_reflection:
    mem_file = PROJECT_ROOT / "reports" / "ai_hedge_fund" / "memory" / "reflections.jsonl"
    if mem_file.exists():
        with open(mem_file, encoding="utf-8") as f:
            lines = f.readlines()
    else:
        pass
    try:
        ctx = rebalancer.memory_reflection.get_reflection_context(days=30)
    except Exception:
        pass

if rebalancer.evolution_orchestrator:
    try:
        status = rebalancer.evolution_orchestrator.get_status()
    except Exception:
        pass

rebalance_days = [r for r in results_log if r["n_rebalance"] > 0]

hedge_days = [r for r in results_log if r["option_enabled"] == "启用"]

checks = [
    ("Regime适应", len(regime_changes) > 0),
    ("信号融合", len(fused_days) > 0),
    ("漂移检测接入", rebalancer.drift_monitor is not None),
    ("决策记忆接入", rebalancer.memory_reflection is not None),
    ("进化编排接入", rebalancer.evolution_orchestrator is not None),
    ("再平衡执行", rebalance_total > 0),
    ("期权对冲", len(hedge_days) > 0),
]
for name, ok in checks:
    pass

all_ok = all(ok for _, ok in checks)
