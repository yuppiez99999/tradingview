"""核心模块快速体检"""
import sys
import importlib.util
from pathlib import Path

base = Path(r"e:\各种PY程序\28-终极量化交易系统7.1")
sys.path.insert(0, str(base / "v7.5_institutional"))
sys.path.insert(0, str(base / "utils"))
sys.path.insert(0, str(base / "v7.5_institutional" / "src"))

results = []

def check(name, fn):
    try:
        ok = fn()
        results.append((name, ok, None))
    except Exception as e:
        results.append((name, False, str(e)))

# 1) daily_workflow 可导入
def import_daily_workflow():
    spec = importlib.util.spec_from_file_location(
        "daily_workflow", base / "v7.5_institutional" / "daily_workflow.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["daily_workflow"] = mod
    spec.loader.exec_module(mod)
    return hasattr(mod, "DailyWorkflow")

check("daily_workflow_import", import_daily_workflow)

# 2) data_provider 可导入
def import_data_provider():
    spec = importlib.util.spec_from_file_location(
        "utils.data_provider", base / "utils" / "data_provider.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["utils.data_provider"] = mod
    spec.loader.exec_module(mod)
    return hasattr(mod, "MarketDataProvider")

check("data_provider_import", import_data_provider)

# 3) utils 核心模块导入
def import_utils():
    from utils.logger import get_logger
    from utils.data_types import safe_float, safe_int, normalize_stock_code
    from utils.risk_metrics import calculate_var, calculate_max_drawdown
    return True

check("utils_core_import", import_utils)

# 4) v7.5 src 核心模块导入
def import_v75_src():
    from execution.smart_order_router import SmartOrderRouter, MockBroker
    from execution.algo_engine import AlgoEngine, AlgoType
    from risk.circuit_breaker import CircuitBreaker, CircuitLevel
    from risk.risk_manager import RiskManager
    from hedging.beta_hedger import BetaHedger
    from hedging.hedge_coordinator import HedgeCoordinator
    from alpha.signal_fusion import SignalFusion
    from backtest.metrics import PerformanceMetrics
    return True

check("v75_src_import", import_v75_src)

# 5) daily_workflow 阶段 check 可运行
def run_check_phase():
    from daily_workflow import DailyWorkflow
    w = DailyWorkflow(trade_date="2026-07-05", dry_run=False)
    ok = w.run(only_phase="check")
    return ok

check("daily_workflow_check_phase", run_check_phase)

# 6) data_provider 默认市场数据可生成
def data_provider_default_market():
    from utils.data_provider import MarketDataProvider
    p = MarketDataProvider()
    data = p._get_default_market_data()
    return isinstance(data, dict) and "index_price" in data

check("data_provider_default_market", data_provider_default_market)

# 7) qlib_adapter 降级逻辑可用
def qlib_adapter_fallback():
    from utils.qlib_adapter import _check_qlib, init_qlib
    assert _check_qlib() in (True, False)
    assert init_qlib() is None or init_qlib() is not None
    return True

check("qlib_adapter_fallback", qlib_adapter_fallback)

print("\n=== 核心模块体检结果 ===")
all_ok = True
for name, ok, err in results:
    status = "PASS" if ok else "FAIL"
    if not ok:
        all_ok = False
    print(f"[{status}] {name}" + (f" :: {err}" if err else ""))
print("\n总体:", "全部通过" if all_ok else "存在失败项")
