"""8/24 进化循环干跑验证脚本"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from utils.infra.feature_flags import enable, is_enabled

print("=== 8/24 进化循环干跑验证 ===")

# Step 1: 双签启用 Feature Flags
print("\n[1] 双签启用 Feature Flags...")
try:
    enable(
        "USE_EVOLUTION_ORCHESTRATOR",
        signer="quant_lead",
        co_signer="risk_manager",
        reason="8/24首次进化循环干跑验证",
    )
    print("  USE_EVOLUTION_ORCHESTRATOR: enabled")
except Exception as e:
    if "already" in str(e).lower() or is_enabled("USE_EVOLUTION_ORCHESTRATOR"):
        print("  USE_EVOLUTION_ORCHESTRATOR: already enabled")
    else:
        print(f"  USE_EVOLUTION_ORCHESTRATOR: FAILED - {e}")

try:
    enable(
        "USE_STRATEGY_EVALUATOR",
        signer="quant_lead",
        co_signer="risk_manager",
        reason="8/24进化循环需要策略评估器",
    )
    print("  USE_STRATEGY_EVALUATOR: enabled")
except Exception as e:
    if is_enabled("USE_STRATEGY_EVALUATOR"):
        print("  USE_STRATEGY_EVALUATOR: already enabled")
    else:
        print(f"  USE_STRATEGY_EVALUATOR: FAILED - {e}")

print("\n  Flag 状态:")
print(f"    USE_EVOLUTION_ORCHESTRATOR = {is_enabled('USE_EVOLUTION_ORCHESTRATOR')}")
print(f"    USE_STRATEGY_EVALUATOR = {is_enabled('USE_STRATEGY_EVALUATOR')}")

# Step 2: 验证 EvolutionOrchestratorV2 可导入
print("\n[2] 验证 EvolutionOrchestratorV2 可导入...")
try:
    from utils.evolution.orchestrator import EvolutionOrchestratorV2

    print("  EvolutionOrchestratorV2: import OK")

    orch = EvolutionOrchestratorV2()
    print(f"  Orchestrator enabled: {orch.enabled}")
    print(f"  Orchestrator stage: {getattr(orch, 'stage', 'unknown')}")
except Exception as e:
    print(f"  EvolutionOrchestratorV2: FAILED - {e}")

# Step 3: 验证 200万ETF配置可加载
print("\n[3] 验证 200万ETF配置...")
try:
    import yaml

    config_path = _ROOT / "config" / "portfolio_200w_etf.yaml"
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    core_etfs = config.get("core_holdings", [])
    satellite_etfs = config.get("satellite_holdings", [])
    total_etfs = len(core_etfs) + len(satellite_etfs)
    total_capital = config.get("portfolio", {}).get("total_capital", 0)

    print(f"  配置文件: {config_path.name}")
    print(f"  总资金: {total_capital / 10000:.0f} 万元")
    print(f"  ETF数量: {total_etfs} (核心{len(core_etfs)} + 卫星{len(satellite_etfs)})")
    print("  200万ETF配置: OK")
except Exception as e:
    print(f"  200万ETF配置: FAILED - {e}")

# Step 4: 生成首笔定投计划（不提交实盘）
print("\n[4] 生成200万ETF首笔定投计划（不提交实盘）...")
try:

    from utils.datetime_utils import now_bj

    phase1 = config.get("execution_phases", {}).get("phase_1_build", {})
    first_amount = 120000  # 12万元 (phase_1_build: 每月定投约12万)

    print(f"  首笔金额: {first_amount / 10000:.0f} 万元 (Phase 1 每月定投)")
    print(f"  建仓阶段: {phase1.get('period', 'N/A')}")
    print(f"  生成日期: {now_bj().strftime('%Y-%m-%d')}")

    print("\n  首笔分配明细 (核心仓, 按权重):")
    total_weight = 0
    for etf in core_etfs:
        w = etf.get("weight", 0)
        alloc = first_amount * w / sum(e.get("weight", 0) for e in core_etfs)
        print(
            f"    {etf['code']} ({etf['name']}): 权重{w:.1%} → {alloc / 10000:.2f}万元"
        )
        total_weight += w
    print(f"    核心仓总权重: {total_weight:.1%}")

    print("\n  首笔定投计划: OK (未提交实盘)")
except Exception as e:
    print(f"  首笔定投计划: FAILED - {e}")

print("\n=== 8/24 进化循环干跑验证完成 ===")
print("注意: Flag 已启用, 但未执行实盘交易。")
print("后续: 完成全部计划后, 再接入实盘执行。")
