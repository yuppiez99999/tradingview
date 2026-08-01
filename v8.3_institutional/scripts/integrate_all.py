#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v7.5 全量集成脚本 — 将v5.9/v5.10的50+增强模块迁移到v7.5_institutional/src/
策略: 先批量复制源文件 → 再批量修复import → 创建__init__和桥接模块
"""

import os
import re
import shutil

BASE_59 = r"e:\各种PY程序\11_量化策略"
BASE_75 = r"e:\各种PY程序\28-终极量化交易系统7.1\v7.5_institutional\src"
BASE_ROOT = r"e:\各种PY程序"
WORKFLOW = r"e:\各种PY程序\15_每日工作流"

# ── 批量复制清单: (源路径, 目标路径) ──
COPY_LIST = [
    # === 对冲体系 (→ src/hedging/) ===
    (f"{BASE_59}/utils/hedge_engine.py",              f"{BASE_75}/hedging/hedge_engine_v59.py"),
    (f"{BASE_59}/utils/hedge_rebalance_integrator.py", f"{BASE_75}/hedging/hedge_rebalance_v59.py"),
    (f"{BASE_59}/hedge_strategies/multi_layer_hedge_manager.py", f"{BASE_75}/hedging/multi_layer_hedge.py"),
    (f"{BASE_59}/hedge_strategies/smart_hedge_trigger.py",      f"{BASE_75}/hedging/smart_trigger.py"),
    (f"{BASE_59}/hedge_strategies/tail_risk_hedge.py",           f"{BASE_75}/hedging/tail_risk.py"),
    (f"{BASE_59}/hedge_strategies/volatility_hedge.py",          f"{BASE_75}/hedging/vol_hedge.py"),
    (f"{BASE_59}/hedge_strategies/enhanced_delta_hedge.py",      f"{BASE_75}/hedging/enhanced_delta.py"),

    # === 衍生品 (→ src/derivatives/) ===
    (f"{BASE_59}/quant_modules/greeks_calculator.py",  f"{BASE_75}/derivatives/greeks.py"),
    (f"{BASE_59}/quant_modules/futures_options_scanner.py", f"{BASE_75}/derivatives/futures_scan.py"),

    # === 风险控制 (→ src/risk/) ===
    (f"{BASE_59}/utils/risk_controls.py",      f"{BASE_75}/risk/risk_controls_v59.py"),
    (f"{BASE_59}/utils/stress_test.py",         f"{BASE_75}/risk/stress_test.py"),
    (f"{BASE_59}/utils/circuit_breaker.py",     f"{BASE_75}/risk/circuit_breaker.py"),
    (f"{BASE_59}/utils/concentration_risk.py",   f"{BASE_75}/risk/concentration.py"),
    (f"{BASE_59}/utils/correlation_monitor.py",  f"{BASE_75}/risk/correlation_monitor.py"),
    (f"{BASE_59}/utils/dynamic_risk_budget.py",  f"{BASE_75}/risk/dynamic_risk_budget.py"),
    (f"{BASE_59}/utils/psi_monitor.py",          f"{BASE_75}/risk/psi_monitor.py"),

    # === 信号融合 (→ src/signals/) ===
    (f"{BASE_59}/utils/signal_fusion.py",          f"{BASE_75}/signals/signal_fusion_v59.py"),
    (f"{BASE_59}/utils/enhanced_signal_fusion.py", f"{BASE_75}/signals/enhanced_fusion.py"),
    (f"{BASE_59}/utils/signal_independence.py",    f"{BASE_75}/signals/independence.py"),
    (f"{BASE_59}/utils/signal_audit.py",           f"{BASE_75}/signals/audit.py"),
    (f"{BASE_59}/utils/rule_engine.py",            f"{BASE_75}/signals/rule_engine.py"),

    # === ML体系 (→ src/ml/) ===
    (f"{BASE_59}/utils/ml_predictor.py",          f"{BASE_75}/ml/ml_predictor_v59.py"),
    (f"{BASE_59}/utils/ml_enhanced_trainer.py",   f"{BASE_75}/ml/enhanced_trainer.py"),
    (f"{BASE_59}/utils/ml_optuna_trainer.py",     f"{BASE_75}/ml/optuna_trainer.py"),
    (f"{BASE_59}/utils/mlflow_tracker.py",        f"{BASE_75}/ml/mlflow_tracker.py"),
    (f"{BASE_59}/utils/ml_significance.py",       f"{BASE_75}/ml/significance.py"),
    (f"{BASE_59}/utils/ml_labeling.py",           f"{BASE_75}/ml/labeling.py"),

    # === 验证体系 (→ src/validation/) ===
    (f"{BASE_59}/utils/walk_forward.py",              f"{BASE_75}/validation/walk_forward.py"),
    (f"{BASE_59}/utils/purged_cv.py",                 f"{BASE_75}/validation/purged_cv.py"),
    (f"{BASE_59}/utils/pit_checker.py",               f"{BASE_75}/validation/pit_checker.py"),
    (f"{BASE_59}/utils/deflated_sharpe.py",            f"{BASE_75}/validation/deflated_sharpe.py"),
    (f"{BASE_59}/utils/statistical_significance.py",   f"{BASE_75}/validation/stat_sig.py"),
    (f"{BASE_59}/utils/pre_deployment_validation.py",  f"{BASE_75}/validation/pre_deploy.py"),

    # === 宏观周期 (→ src/macro/) ===
    (f"{BASE_59}/utils/kondratiev_cycle.py",    f"{BASE_75}/macro/kondratiev.py"),
    (f"{BASE_59}/utils/five_year_plan.py",      f"{BASE_75}/macro/five_year_plan.py"),
    (f"{BASE_59}/utils/social_security_etf.py",  f"{BASE_75}/macro/social_security_etf.py"),

    # === AI路由 (→ src/ai/) ===
    (f"{BASE_59}/utils/multi_model_router.py",    f"{BASE_75}/ai/model_router.py"),
    (f"{BASE_59}/utils/glm5_decision_engine.py",  f"{BASE_75}/ai/glm5_engine.py"),
    (f"{BASE_59}/utils/ai_coordinator.py",        f"{BASE_75}/ai/coordinator.py"),
    (f"{WORKFLOW}/llm_client.py",                 f"{BASE_75}/ai/llm_client.py"),

    # === NLP/情感 (→ src/nlp/) ===
    (f"{BASE_59}/fin_sentiment_analyzer.py",   f"{BASE_75}/nlp/sentiment.py"),
    (f"{BASE_59}/event_driven_factor.py",      f"{BASE_75}/nlp/event_factor.py"),
    (f"{WORKFLOW}/sentiment_hub.py",           f"{BASE_75}/nlp/sentiment_hub.py"),

    # === 因子模型 (→ src/factors/) ===
    (f"{BASE_59}/five_factor_model.py",                f"{BASE_75}/factors/five_factor.py"),
    (f"{BASE_59}/quant_modules/dynamic_position.py",    f"{BASE_75}/factors/dynamic_position.py"),
    (f"{BASE_59}/quant_modules/decision_theories.py",   f"{BASE_75}/factors/decision_theories.py"),
    (f"{BASE_59}/utils/market_impact.py",               f"{BASE_75}/factors/market_impact.py"),

    # === 基础设施 (→ src/config/) ===
    (f"{BASE_59}/utils/config_hub.py",           f"{BASE_75}/config/config_hub.py"),
    (f"{BASE_59}/utils/config_validator.py",      f"{BASE_75}/config/config_validator.py"),
    (f"{BASE_59}/utils/performance_attribution.py", f"{BASE_75}/config/perf_attribution.py"),

    # === 回测增强 ===
    (f"{BASE_ROOT}/fast_backtest_v2.py", f"{BASE_75}/backtest/fast_backtest_v2.py"),
]

# ── Import修复规则: (target_file, 正则模式, 替换内容) ──
IMPORT_FIXES = [
    # 跨目录修复
    ("hedging/hedge_rebalance_v59.py",
     r"from utils\.hedge_engine import",
     r"from .hedge_engine_v59 import"),
    ("hedging/hedge_rebalance_v59.py",
     r"from utils\.kondratiev_cycle import",
     r"# [V75] from .kondratiev_cycle import  # 已迁移到 src/macro/kondratiev.py"),
    ("hedging/hedge_rebalance_v59.py",
     r"from quant_modules\.wind_mcp import",
     r"from ..bridges.wind_mcp import"),
    ("hedging/hedge_engine_v59.py",
     r"from quant_modules\.wind_mcp import",
     r"from ..bridges.wind_mcp import"),
    ("hedging/multi_layer_hedge.py",
     r"from enhanced_delta_hedge import",
     r"from .enhanced_delta import"),
    ("hedging/multi_layer_hedge.py",
     r"from volatility_hedge import",
     r"from .vol_hedge import"),
    ("hedging/multi_layer_hedge.py",
     r"from tail_risk_hedge import",
     r"from .tail_risk import"),
    ("risk/risk_controls_v59.py",
     r"from utils\.alert_notifier import",
     r"# [V75] from ..utils.alert_notifier import  # 需在v7.5创建alert_notifier"),
    ("risk/circuit_breaker.py",
     r"from utils\.alert_notifier import",
     r"# [V75] from ..utils.alert_notifier import  # 需在v7.5创建alert_notifier"),
    ("signals/signal_fusion_v59.py",
     r"from engine\.(\w+) import",
     r"from ..bridges.engine_\1 import  # [V75桥接]"),
    ("ml/ml_predictor_v59.py",
     r"from engine\.data import",
     r"# [V75] from ..bridges.engine_data import  # 数据引擎桥接"),
    ("ai/coordinator.py",
     r"from utils\.event_tracker import",
     r"from ..bridges.event_tracker import"),
    ("nlp/sentiment.py",
     r"from yizhao_data_loader import",
     r"# [V75] from ..bridges.yizhao_data import  # yizhao数据桥接"),
    ("nlp/event_factor.py",
     r"from yizhao_data_loader import",
     r"# [V75] from ..bridges.yizhao_data import  # yizhao数据桥接"),
    ("ai/llm_client.py",
     r"from Config import",
     r"# [V75] Config导入需适配 v7.5 config_hub\n# from Config import"),
]

# ── __init__.py 内容 ──
INIT_FILES = {
    "hedging/__enhanced__.py": '''# -*- coding: utf-8 -*-
"""v7.5 增强对冲子包 — 从v5.9迁移"""
from .hedge_engine_v59 import HedgeEngine, HedgeSignalStrength, HedgeType, HedgeRecommendation, PortfolioRisk
from .hedge_rebalance_v59 import HedgeRebalanceIntegrator
try: from .multi_layer_hedge import MultiLayerHedgeManager
except ImportError: MultiLayerHedgeManager = None
try: from .smart_trigger import SmartHedgeTrigger
except ImportError: SmartHedgeTrigger = None
try: from .tail_risk import TailRiskHedge
except ImportError: TailRiskHedge = None
try: from .vol_hedge import VolatilityHedge
except ImportError: VolatilityHedge = None
try: from .enhanced_delta import EnhancedDeltaHedge
except ImportError: EnhancedDeltaHedge = None
''',

    "risk/__enhanced__.py": '''# -*- coding: utf-8 -*-
"""v7.5 增强风控子包 — 从v5.9迁移"""
try: from .risk_controls_v59 import RiskControls, RiskControlLevel
except ImportError: RiskControls = None; RiskControlLevel = None
try: from .stress_test import StressTestEngine
except ImportError: StressTestEngine = None
try: from .circuit_breaker import CircuitBreaker
except ImportError: CircuitBreaker = None
try: from .concentration import ConcentrationRiskMonitor
except ImportError: ConcentrationRiskMonitor = None
try: from .correlation_monitor import CorrelationMonitor
except ImportError: CorrelationMonitor = None
try: from .dynamic_risk_budget import DynamicRiskBudget
except ImportError: DynamicRiskBudget = None
try: from .psi_monitor import PSIMonitor
except ImportError: PSIMonitor = None
''',

    "signals/__enhanced__.py": '''# -*- coding: utf-8 -*-
"""v7.5 增强信号子包 — 从v5.9迁移"""
try: from .signal_fusion_v59 import SignalFusionEngine
except ImportError: SignalFusionEngine = None
try: from .enhanced_fusion import EnhancedSignalFusion
except ImportError: EnhancedSignalFusion = None
try: from .independence import SignalIndependenceAnalyzer
except ImportError: SignalIndependenceAnalyzer = None
try: from .audit import SignalAuditor
except ImportError: SignalAuditor = None
try: from .rule_engine import RuleEngine
except ImportError: RuleEngine = None
''',

    "ml/__enhanced__.py": '''# -*- coding: utf-8 -*-
"""v7.5 增强ML子包 — 从v5.9迁移"""
try: from .ml_predictor_v59 import MLPredictor, MLSignal
except ImportError: MLPredictor = None; MLSignal = None
try: from .enhanced_trainer import EnhancedTrainer
except ImportError: EnhancedTrainer = None
try: from .optuna_trainer import OptunaTrainer
except ImportError: OptunaTrainer = None
try: from .mlflow_tracker import MLflowTracker
except ImportError: MLflowTracker = None
try: from .significance import MLSignificance
except ImportError: MLSignificance = None
try: from .labeling import LabelEngine
except ImportError: LabelEngine = None
''',

    "validation/__init__.py": '''# -*- coding: utf-8 -*-
"""v7.5 统计验证子包 — Walk-Forward / PIT / Deflated Sharpe"""
try: from .walk_forward import WalkForwardValidator
except ImportError: WalkForwardValidator = None
try: from .purged_cv import PurgedCrossValidator
except ImportError: PurgedCrossValidator = None
try: from .pit_checker import PITChecker
except ImportError: PITChecker = None
try: from .deflated_sharpe import DeflatedSharpeTest
except ImportError: DeflatedSharpeTest = None
try: from .stat_sig import StatisticalSignificance
except ImportError: StatisticalSignificance = None
try: from .pre_deploy import PreDeploymentValidator
except ImportError: PreDeploymentValidator = None
''',

    "macro/__enhanced__.py": '''# -*- coding: utf-8 -*-
"""v7.5 宏观周期子包 — 康波/十五五/社保ETF"""
try: from .kondratiev import KondratievCycleAnalyzer, KondratievPhase
except ImportError: KondratievCycleAnalyzer = None; KondratievPhase = None
try: from .five_year_plan import FifteenFivePlanAnalyzer
except ImportError: FifteenFivePlanAnalyzer = None
try: from .social_security_etf import SocialSecurityETFTracker, NationalTeamSignalDetector
except ImportError: SocialSecurityETFTracker = None; NationalTeamSignalDetector = None
''',

    "ai/__init__.py": '''# -*- coding: utf-8 -*-
"""v7.5 AI路由子包 — 多模型路由/GLM5决策/协调器/LLM客户端"""
try: from .model_router import ModelRouter
except ImportError: ModelRouter = None
try: from .glm5_engine import GLM5DecisionEngine
except ImportError: GLM5DecisionEngine = None
try: from .coordinator import AICoordinator
except ImportError: AICoordinator = None
try: from .llm_client import LLMClient
except ImportError: LLMClient = None
''',

    "nlp/__init__.py": '''# -*- coding: utf-8 -*-
"""v7.5 NLP/情感子包 — 金融情感/事件因子/情感聚合"""
try: from .sentiment import FinSentimentAnalyzer
except ImportError: FinSentimentAnalyzer = None
try: from .event_factor import EventDrivenFactor
except ImportError: EventDrivenFactor = None
try: from .sentiment_hub import SentimentHub
except ImportError: SentimentHub = None
''',

    "factors/__init__.py": '''# -*- coding: utf-8 -*-
"""v7.5 因子模型子包 — 五因子/动态仓位/决策理论/市场冲击"""
try: from .five_factor import FiveFactorModel
except ImportError: FiveFactorModel = None
try: from .dynamic_position import DynamicPositionSizer
except ImportError: DynamicPositionSizer = None
try: from .decision_theories import DecisionTheoryEngine
except ImportError: DecisionTheoryEngine = None
try: from .market_impact import MarketImpactModel
except ImportError: MarketImpactModel = None
''',

    "derivatives/__init__.py": '''# -*- coding: utf-8 -*-
"""v7.5 衍生品子包 — Greeks计算/期货期权扫描"""
try: from .greeks import GreeksCalculator
except ImportError: GreeksCalculator = None
try: from .futures_scan import FuturesOptionsScanner
except ImportError: FuturesOptionsScanner = None
''',

    "config/__init__.py": '''# -*- coding: utf-8 -*-
"""v7.5 配置管理子包 — ConfigHub/验证器/绩效归因"""
try: from .config_hub import ConfigHub
except ImportError: ConfigHub = None
try: from .config_validator import ConfigValidator
except ImportError: ConfigValidator = None
try: from .perf_attribution import PerformanceAttribution
except ImportError: PerformanceAttribution = None
''',
}

# ── 桥接模块 — 连接v5.9跨项目依赖到v7.5内部 ──
BRIDGE_FILES = {
    "bridges/__init__.py": '''# -*- coding: utf-8 -*-
"""v7.5 桥接层 — 连接v5.9跨项目依赖"""
''',

    "bridges/wind_mcp.py": '''# -*- coding: utf-8 -*-
"""Wind MCP 桥接 — 为 hedge_engine 提供数据接口"""
import os, json, logging
_log = logging.getLogger("bridges.wind_mcp")

def _wind_mcp_call(endpoint: str, params: dict = None) -> dict:
    """调用Wind MCP接口"""
    try:
        from ..data.wind_mcp import query_wind
        return query_wind(endpoint, **(params or {}))
    except ImportError:
        _log.warning("Wind MCP 不可用: %s", endpoint)
        return {"status": "unavailable", "endpoint": endpoint, "data": {}}

def get_realtime_prices_batch(symbols: list) -> dict:
    """批量获取实时价格"""
    try:
        result = _wind_mcp_call("realtime_prices", {"symbols": symbols})
        return result.get("data", {})
    except Exception:
        return {s: None for s in symbols}
''',

    "bridges/event_tracker.py": '''# -*- coding: utf-8 -*-
"""事件追踪桥接"""
class EventTracker:
    def __init__(self, *args, **kwargs): pass
    def log_event(self, *args, **kwargs): pass
    def get_recent_events(self, *args, **kwargs): return []
''',

    "bridges/engine_data.py": '''# -*- coding: utf-8 -*-
"""数据引擎桥接 — ml_predictor 的数据依赖"""
import logging
_log = logging.getLogger("bridges.engine_data")

def fetch_market_data(symbols: list, start_date: str = None, end_date: str = None) -> dict:
    """获取市场数据 - 桥接到v7.5 data层"""
    try:
        from ..data.market_data import fetch_data
        return fetch_data(symbols, start_date, end_date)
    except ImportError:
        _log.warning("data.market_data 不可用, 返回空数据")
        return {}
''',

    "bridges/yizhao_data.py": '''# -*- coding: utf-8 -*-
"""yizhao数据加载器桥接"""
import logging
_log = logging.getLogger("bridges.yizhao_data")

class YizhaoDataLoader:
    """占位 - 接入yizhao数据源后替换"""
    def __init__(self, *args, **kwargs):
        _log.info("YizhaoDataLoader 占位初始化")
    def load_articles(self, *args, **kwargs): return []
    def search_articles(self, *args, **kwargs): return []
''',
}


# ============================================================
def copy_files():
    """批量复制源文件到目标"""
    ok = skip = fail = 0
    for src, dst in COPY_LIST:
        if not os.path.exists(src):
            print(f"  [SKIP] 源不存在: {os.path.basename(src)}")
            skip += 1
            continue
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            ok += 1
        except Exception as e:
            print(f"  [FAIL] {os.path.basename(src)}: {e}")
            fail += 1
    print(f"\n复制完成: {ok} 成功 / {skip} 跳过 / {fail} 失败\n")
    return ok, skip, fail

def fix_imports():
    """批量修复 import 路径"""
    fixed = 0
    re.compile(r'^\s*(import|from)\s+')
    for target_pat, pattern, replacement in IMPORT_FIXES:
        rgx = re.compile(pattern)
        target_rgx = re.compile(target_pat.replace('.', r'\.'))
        for root, _dirs, files in os.walk(BASE_75):
            for f in files:
                if not f.endswith('.py'):
                    continue
                full = os.path.join(root, f)
                rel = os.path.relpath(full, BASE_75).replace('\\', '/')
                if target_rgx.search(rel) is None:
                    continue
                try:
                    with open(full, 'r', encoding='utf-8') as fh:
                        content = fh.read()
                    new_content, n = rgx.subn(replacement, content)
                    if n > 0:
                        with open(full, 'w', encoding='utf-8') as fh:
                            fh.write(new_content)
                        fixed += n
                        print(f"  [FIX] {rel}: {n}处替换 ({pattern[:40]}...)")
                except Exception as e:
                    print(f"  [ERR] {rel}: {e}")
    print(f"\nImport修复: {fixed} 处\n")
    return fixed

def create_init_files():
    """创建所有 __init__.py"""
    for rel, content in INIT_FILES.items():
        path = os.path.join(BASE_75, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"  [INIT] {rel}")

def create_bridge_files():
    """创建桥接模块"""
    for rel, content in BRIDGE_FILES.items():
        path = os.path.join(BASE_75, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"  [BRIDGE] {rel}")

def create_master_import():
    """创建 src/__v59_enhanced__.py 一键导入所有增强模块"""
    path = os.path.join(BASE_75, "__v59_enhanced__.py")
    content = '''# -*- coding: utf-8 -*-
"""
v7.5 一键导入所有v5.9增强模块
用法: from src.__v59_enhanced__ import (HedgeEngine, KondratievCycleAnalyzer, ...)
"""
# ── 对冲 ──
from .hedging.__enhanced__ import *
# ── 风控 ──
from .risk.__enhanced__ import *
# ── 信号 ──
from .signals.__enhanced__ import *
# ── ML ──
from .ml.__enhanced__ import *
# ── 验证 ──
from .validation import *
# ── 宏观 ──
from .macro.__enhanced__ import *
# ── AI ──
from .ai import *
# ── NLP ──
from .nlp import *
# ── 因子 ──
from .factors import *
# ── 衍生品 ──
from .derivatives import *
# ── 配置 ──
from .config import *
'''
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("  [MASTER] src/__v59_enhanced__.py")

def stats():
    """统计迁移结果"""
    total_files = 0
    total_lines = 0
    for root, _dirs, files in os.walk(BASE_75):
        for f in files:
            if not f.endswith('.py'):
                continue
            try:
                path = os.path.join(root, f)
                with open(path, 'r', encoding='utf-8') as fh:
                    lines = len(fh.readlines())
                total_files += 1
                total_lines += lines
            except Exception:
                pass
    print(f"\n{'='*60}")
    print(f"v7.5 src/ 总览: {total_files} 个 .py 文件, {total_lines:,} 行代码")
    print(f"{'='*60}")


# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("v7.5 全量集成脚本 v1.0")
    print("将 v5.9/v5.10 增强模块迁移到 v7.5_institutional/src/")
    print("=" * 60)

    print("\n[1/5] 批量复制源文件...")
    copy_files()

    print("[2/5] 修复 import 路径...")
    fix_imports()

    print("[3/5] 创建 __init__.py...")
    create_init_files()

    print("[4/5] 创建桥接模块...")
    create_bridge_files()

    print("[5/5] 创建一键导入入口...")
    create_master_import()

    stats()
    print("\n✅ 全量集成完成！")
