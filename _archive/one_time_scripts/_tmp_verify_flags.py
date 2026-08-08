"""临时验证: feature_flags.yaml 是否正确加载."""
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from utils.infra.feature_flags import FeatureFlags, is_enabled, list_flags

FeatureFlags.reset_instance()

flags = list_flags()
print(f"Registered flags: {len(flags)}")
for f in flags:
    print(f"  {f['name']}: default={f['default']}, current={f['current_value']}")

print()
print(f"USE_STRATEGY_EVALUATOR: {is_enabled('USE_STRATEGY_EVALUATOR')}")
print(f"USE_EVOLUTION_ORCHESTRATOR: {is_enabled('USE_EVOLUTION_ORCHESTRATOR')}")
print(f"USE_INTEGRATED_BOOTSTRAP: {is_enabled('USE_INTEGRATED_BOOTSTRAP')}")

# 验证 StrategyEvaluator 是否能检测到 flag 已注册 (但默认关闭)
from utils.alpha.strategy_evaluator import StrategyEvaluator
evaluator = StrategyEvaluator()
print()
print(f"StrategyEvaluator.enabled: {evaluator.enabled}")
print(f"  (flag={evaluator.feature_flag_name})")
