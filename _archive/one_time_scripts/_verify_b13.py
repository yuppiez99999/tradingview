"""B1.3 临时验证脚本 - 验证所有调用点的 MAX_DRAWDOWN 值正确读取

修复 (2026-08-01): v8.3_institutional 模块名含数字开头,
  importlib.import_module 无法加载, 改用 importlib.util.spec_from_file_location.
"""
import importlib.util
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# 1. utils/quant_neutral_runner (中性策略专属 0.08)
from utils.quant_neutral_runner import DEFAULT_MAX_DRAWDOWN  # noqa: E402

print(f"[1] quant_neutral DEFAULT_MAX_DRAWDOWN: {DEFAULT_MAX_DRAWDOWN} (期望 0.08)")
assert DEFAULT_MAX_DRAWDOWN == 0.08, f"中性策略应为 0.08, 实际 {DEFAULT_MAX_DRAWDOWN}"

# 2. v8.3_institutional/llm_intraday_decision_engine (组合整体 0.15)
# 修复: v8.3_institutional 模块名含数字开头, 用 spec_from_file_location 加载
_llm_path = _PROJECT_ROOT / "v8.3_institutional" / "llm_intraday_decision_engine.py"
_spec = importlib.util.spec_from_file_location("_llm_intraday_b13", str(_llm_path))
_llm_intraday = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_llm_intraday)
MAX_DRAWDOWN_LIMIT = _llm_intraday.MAX_DRAWDOWN_LIMIT
print(f"[2] llm_intraday MAX_DRAWDOWN_LIMIT: {MAX_DRAWDOWN_LIMIT} (期望 0.15)")
assert MAX_DRAWDOWN_LIMIT == 0.15, f"组合整体应为 0.15, 实际 {MAX_DRAWDOWN_LIMIT}"

# 3. research/annual_return_forecast (组合整体 0.15)
from research.annual_return_forecast import MAX_DRAWDOWN_LIMIT as forecast_dd  # noqa: E402

print(f"[3] annual_return_forecast MAX_DRAWDOWN_LIMIT: {forecast_dd} (期望 0.15)")
assert forecast_dd == 0.15, f"应为 0.15, 实际 {forecast_dd}"

# 4. research/backtest_runner (组合整体 0.15)
from research.backtest_runner import MAX_DRAWDOWN_LIMIT as backtest_dd  # noqa: E402

print(f"[4] backtest_runner MAX_DRAWDOWN_LIMIT: {backtest_dd} (期望 0.15)")
assert backtest_dd == 0.15, f"应为 0.15, 实际 {backtest_dd}"

# 5. alpha_hedge_engine (组合整体 0.15)
from alpha_hedge_engine import _DEFAULT_MAX_DRAWDOWN_LIMIT  # noqa: E402

print(f"[5] alpha_hedge _DEFAULT_MAX_DRAWDOWN_LIMIT: {_DEFAULT_MAX_DRAWDOWN_LIMIT} (期望 0.15)")
assert _DEFAULT_MAX_DRAWDOWN_LIMIT == 0.15, f"应为 0.15, 实际 {_DEFAULT_MAX_DRAWDOWN_LIMIT}"

# 6. research/backtest_current_portfolio (组合整体 0.15)
from research.backtest_current_portfolio import PortfolioBacktester  # noqa: E402

print(f"[6] PortfolioBacktester TARGET_MAX_DRAWDOWN: {PortfolioBacktester.TARGET_MAX_DRAWDOWN} (期望 0.15)")
assert PortfolioBacktester.TARGET_MAX_DRAWDOWN == 0.15, f"应为 0.15, 实际 {PortfolioBacktester.TARGET_MAX_DRAWDOWN}"

# 7. research/fast_backtest_aggregator (组合整体 0.15)
import research.fast_backtest_aggregator as fba  # noqa: E402

print(f"[7] fast_backtest _MAX_DRAWDOWN_LIMIT: {fba._MAX_DRAWDOWN_LIMIT} (期望 0.15)")
assert fba._MAX_DRAWDOWN_LIMIT == 0.15, f"应为 0.15, 实际 {fba._MAX_DRAWDOWN_LIMIT}"

# 8. 验证 alpha_hedge_engine RiskControl 默认行为
from alpha_hedge_engine import RiskControl  # noqa: E402

rc = RiskControl()  # 不传 max_drawdown_limit
print(f"[8] RiskControl() 默认 max_drawdown_limit: {rc.max_drawdown_limit} (期望 0.15)")
assert rc.max_drawdown_limit == 0.15, f"默认应为 0.15, 实际 {rc.max_drawdown_limit}"

# 9. 验证显式传参仍然有效 (向后兼容)
rc2 = RiskControl(max_drawdown_limit=0.20)
print(f"[9] RiskControl(max_drawdown_limit=0.20) 显式传参: {rc2.max_drawdown_limit} (期望 0.20)")
assert rc2.max_drawdown_limit == 0.20, f"显式传参应生效, 实际 {rc2.max_drawdown_limit}"

# 10. 验证 config/risk_params.yaml 为唯一事实源
from utils.risk_params import get_max_drawdown_limit, get_quant_neutral_max_drawdown  # noqa: E402

print(f"[10] config 读取: max_drawdown_limit={get_max_drawdown_limit()}, "
      f"quant_neutral={get_quant_neutral_max_drawdown()}")
assert get_max_drawdown_limit() == 0.15
assert get_quant_neutral_max_drawdown() == 0.08

print("\n✅ B1.3 所有调用点验证通过")
print("   - 组合整体回撤上限: 0.15 (7 处生产代码 + 1 处 yaml 配置)")
print("   - 中性策略专属阈值: 0.08 (1 处, 策略文档保留)")
print("   - RiskControl 默认值从 config 读取, 显式传参向后兼容")
print("   - 唯一事实源: v8.3_institutional/config/risk_params.yaml")

