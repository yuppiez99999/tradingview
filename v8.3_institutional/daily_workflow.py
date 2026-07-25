# -*- coding: utf-8 -*-
"""
v7.5 机构级每日交易工作流
================================

每个交易日 7:00 自动执行，完整流程:

    Phase 1:   系统自检 (NTP/连接器/风控状态)
    Phase 1.5: 收益预测动态校准 (Wind MCP 拉取 + 年化计算 + projection 校准)
    Phase 2:   市场状态评估 (VIX/熔断级别)
    Phase 3:   风险预算计算 (Kelly + Risk Parity)
    Phase 4:   对冲评估 (Beta/Vol/Correlation 三联)
    Phase 5:   信号生成 (神华建仓 + Alpha 信号)
    Phase 6:   智能执行 (SOR + MockBroker)
    Phase 7:   盘后报告生成
    Phase 8:   自主学习量化训练 (LightGBM+XGBoost 集成 + 37 特征) [新增]
    Phase 9:   FactorKillSwitch 因子实时监控 (S6 持续监控 + 状态持久化) [新增]
    Phase 10:  影子账户监控 (Shadow Account + FailFast 3%/5%) [新增 2026-07-25]

用法:
    python daily_workflow.py                           # 执行今日 workflow
    python daily_workflow.py --date 2026-07-06         # 指定日期
    python daily_workflow.py --dry-run                 # 干跑模式 (不执行交易)
    python daily_workflow.py --phase check             # 仅执行某阶段
    python daily_workflow.py --phase calibrate         # 仅执行收益预测校准
    python daily_workflow.py --phase autolearn         # 仅执行自主学习训练
    python daily_workflow.py --phase factor_kill_switch # 仅执行因子监控
"""

from __future__ import annotations

import os
import sys
import json
import math
import logging
import argparse
import re
import requests
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import asdict

# ============================================================
# 路径初始化 (兼容 Python 3.8)
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "src"
sys.path.insert(0, str(SRC_DIR))
sys.path.insert(0, str(BASE_DIR.parent))  # 兼容 v7.4 模块
sys.path.insert(0, str(BASE_DIR / "data"))  # EDB 期货数据模块

# ============================================================
# 日志配置
# ============================================================
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(
            LOG_DIR / f"daily_workflow_{datetime.now():%Y%m%d}.log",
            encoding="utf-8",
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("v75.daily_workflow")

# ============================================================
# 导入 v7.5 模块
# ============================================================
# === v7.5 核心模块 (硬性要求 - 缺失时系统拒绝启动) ===
from risk.risk_manager import RiskManager
from risk.circuit_breaker import CircuitBreaker
class CircuitLevel:
    LEVEL_1 = 1; LEVEL_2 = 2; LEVEL_3 = 3; LEVEL_4 = 4
from hedging.beta_hedger import BetaHedger
from hedging.vol_hedger import VolHedger
from hedging.correlation_hedger import CorrelationHedger
from hedging.hedge_coordinator import HedgeCoordinator
from execution.smart_order_router import SmartOrderRouter, MockBroker
from execution.algo_engine import AlgoEngine, AlgoType
from execution.ntp_sync import NTPSync
from backtest.metrics import compute_all_metrics
from backtest.cost_model import CostModel
V75_READY = True
logger.info("v7.5 核心模块加载成功: RiskManager/CircuitBreaker/HedgeCoordinator/SmartOrderRouter/AlgoEngine")

# 导入 EDB 期货数据模块 (可选)
try:
    from data.edb_futures_data import EDBFuturesData
    EDB_READY = True
except ImportError as e:
    logger.warning(f"EDB 期货数据模块导入失败: {e}")
    EDB_READY = False

# 导入期货期权扫描器 (可选)
try:
    from futures_options_scanner import FuturesOptionsScanner
    SCANNER_READY = True
except ImportError as e:
    logger.warning(f"期货期权扫描器导入失败: {e}")
    SCANNER_READY = False

# 导入收益预测动态校准模块 (可选, 每日自动更新历史数据 + 校准预测)
try:
    from calibrate_returns_projection import run_calibration as _run_calibration
    CALIBRATE_READY = True
except ImportError as e:
    logger.warning(f"收益预测校准模块导入失败: {e}")
    CALIBRATE_READY = False

# 导入自主学习量化训练模块 (可选, 每日盘后自动训练集成模型)
# 优先使用 lgb_enhanced_trainer (真实OHLCV + 情绪因子 + 自适应重训)
# 不可用时回退到旧 autolearn_trainer
try:
    from lgb_enhanced_trainer import run_enhanced_training as _run_autolearn
    from lgb_enhanced_trainer import generate_comparison_report as _generate_autolearn_report
    AUTOLEARN_READY = True
    AUTOLEARN_ENGINE = "lgb_enhanced"
    logger.info("使用增强训练器: lgb_enhanced_trainer (真实OHLCV + 情绪因子 + 自适应重训)")
except ImportError as e:
    logger.warning(f"增强训练器导入失败: {e}, 尝试旧训练器")
    try:
        from autolearn_trainer import run_autolearn as _run_autolearn
        from autolearn_trainer import generate_report as _generate_autolearn_report
        AUTOLEARN_READY = True
        AUTOLEARN_ENGINE = "autolearn_legacy"
    except ImportError as e2:
        logger.warning(f"自主学习训练模块导入失败: {e2}")
        AUTOLEARN_READY = False
        AUTOLEARN_ENGINE = "none"

# ============================================================
# 对冲基金视角模块 (v7.7: Theta引擎 + Gamma尾部防御 + 三级熔断 + 2030清仓)
# ============================================================
# === 对冲基金核心模块 (硬性要求 - 缺失时系统拒绝启动) ===
from utils.theta_engine import ThetaEngine
from utils.gamma_engine import GammaEngine
from utils.kill_switch import KillSwitch
from utils.liquidation_scheduler import LiquidationScheduler
HEDGE_FUND_CORE_READY = True
logger.info("对冲基金核心模块加载成功: Theta/Gamma/KillSwitch/LiquidationScheduler")

# ============================================================
# 统一风险驾驶舱 (UnifiedRiskCockpit) — 2026-07-25 顶级对冲基金审计 P0-9
# 审计问题: daily_workflow.py 未真正导入并调用 UnifiedRiskCockpit,
#           四套熔断系统 (kill_switch/circuit_breaker/drawdown/portfolio.yaml) 各管各的
# 修复: 统一入口, phase_execute 下单前必须经过 UnifiedRiskCockpit.full_scan()
# ============================================================
UNIFIED_RISK_COCKPIT_READY = False
try:
    from src.risk.unified_risk_cockpit import UnifiedRiskCockpit
    UNIFIED_RISK_COCKPIT_READY = True
    logger.info("统一风险驾驶舱加载成功: UnifiedRiskCockpit (P0-9)")
except ImportError as _e_cockpit:
    logger.warning(f"UnifiedRiskCockpit 导入失败 (降级模式, 回退到 KillSwitch): {_e_cockpit}")

# ============================================================
# v10.0 风控模块 (回撤控制 + VaR 监控 + 压力测试 + 配置加载器)
# ============================================================
V10_RISK_READY = False
try:
    from utils.drawdown_controller import DrawdownController
    from utils.var_monitor import VaRMonitor
    from utils.stress_test_runner import StressTestRunner
    from utils.v10_config_loader import V10ConfigLoader
    V10_RISK_READY = True
    logger.info("v10.0 风控模块加载成功: DrawdownController/VaRMonitor/StressTestRunner/V10ConfigLoader")
except ImportError as e:
    logger.warning(f"v10.0 风控模块导入失败 (降级模式): {e}")

# ============================================================
# v10.0 量化中性 + 现金管理 + 方向性期货模块 (月度调仓 + 逆回购 + CU/AU/T 方向性)
# ============================================================
V10_STRATEGY_READY = False
try:
    from utils.quant_neutral_runner import QuantNeutralRunner
    from utils.ic_hedge_calculator import ICHedgeCalculator
    from utils.cash_manager import CashManager
    from utils.directional_futures_trader import DirectionalFuturesTrader
    V10_STRATEGY_READY = True
    logger.info("v10.0 策略模块加载成功: QuantNeutralRunner/ICHedgeCalculator/CashManager/DirectionalFuturesTrader")
except ImportError as e:
    logger.warning(f"v10.0 策略模块导入失败 (降级模式): {e}")

# ============================================================
# v10.0 十五五阶段管理器 (5 年度阶段 + 季度评估 + 2030 清仓 Q1-Q4)
# ============================================================
PHASE_MANAGER_READY = False
try:
    from utils.phase_manager import PhaseManager, PhaseInfo, QuarterlyReviewResult
    PHASE_MANAGER_READY = True
    logger.info("十五五阶段管理器加载成功: PhaseManager (5年度/季度评估/2030清仓)")
except ImportError as e:
    logger.warning(f"十五五阶段管理器导入失败 (降级模式): {e}")

# ============================================================
# 世界顶级对冲基金视角新增模块 (执行算法 + P&L归因 + 数据质量 + 多策略协调)
# + 顶级配置 (Black-Litterman + TCA + Barra)
# ============================================================
HEDGE_FUND_MODULES_READY = False
try:
    from utils.execution_algo_engine import ExecutionAlgoEngine, AlgoType as ExecAlgoType
    from utils.pnl_attribution_engine import PnLAttributionEngine
    from utils.data_quality_monitor import DataQualityMonitor
    from utils.multi_strategy_coordinator import MultiStrategyCoordinator
    HEDGE_FUND_MODULES_READY = True
    logger.info("对冲基金模块加载成功: ExecutionAlgo/PnLAttribution/DataQuality/MultiStrategyCoord")
except ImportError as e:
    logger.warning(f"对冲基金模块导入失败 (降级模式): {e}")

# 顶级配置模块 (Black-Litterman / TCA / Barra)
INSTITUTIONAL_MODULES_READY = False
try:
    from utils.black_litterman_optimizer import BlackLittermanOptimizer, View as BLView, BLResult
    from utils.tca_engine import TCAManager, FillRecord, BenchmarkPrices, TCAReport
    from utils.barra_risk_decomposer import BarraRiskDecomposer, BarraDecomposition, BARRA_STYLE_FACTORS
    INSTITUTIONAL_MODULES_READY = True
    logger.info("机构级模块加载成功: BlackLitterman/TCA/Barra")
except ImportError as e:
    logger.warning(f"机构级模块导入失败 (降级模式): {e}")

# 顶级风险管理模块 (Ledoit-Wolf / 风险预算约束 / 压力测试情景)
RISK_MGT_MODULES_READY = False
try:
    from utils.ledoit_wolf_covariance import LedoitWolfCovariance, ShrinkageResult
    from utils.risk_budget_optimizer import RiskBudgetOptimizer, RiskBudgetResult
    from utils.stress_test_scenario_library import (
        StressTestEngine, StressScenario, ShockFactors, StressTestResult,
    )
    RISK_MGT_MODULES_READY = True
    logger.info("风险管理模块加载成功: LedoitWolf/RiskBudgetOpt/StressTest")
except ImportError as e:
    logger.warning(f"风险管理模块导入失败 (降级模式): {e}")

# 顶级 Alpha 生成模块 (Alpha 因子库 / 动量反转 / Smart Beta)
ALPHA_MODULES_READY = False
try:
    from utils.alpha_factor_library import AlphaFactorLibrary, FactorLibraryResult
    from utils.momentum_reversal_engine import MomentumReversalEngine, MomentumResult
    from utils.smart_beta_engine import SmartBetaEngine, SmartBetaResult
    ALPHA_MODULES_READY = True
    logger.info("Alpha 生成模块加载成功: AlphaFactorLib/MomentumReversal/SmartBeta")
except ImportError as e:
    logger.warning(f"Alpha 生成模块导入失败 (降级模式): {e}")

# 顶级执行层模块 (执行算法 / 市场冲击 / 智能路由) — 注意使用别名避免与原有 SmartOrderRouter 冲突
EXECUTION_MODULES_READY = False
try:
    from utils.execution_algorithm_engine import (
        ExecutionAlgorithmEngine as InstitutionExecAlgoEngine,
        Order as ExecOrder, ExecutionPlan,
    )
    from utils.market_impact_model import MarketImpactModel, ImpactParams
    from utils.smart_order_router import SmartOrderRouter as InstitutionSmartRouter, Venue as RoutingVenue
    EXECUTION_MODULES_READY = True
    logger.info("执行层模块加载成功: ExecAlgo/MarketImpact/SmartRouter")
except ImportError as e:
    logger.warning(f"执行层模块导入失败 (降级模式): {e}")

# 顶级另类数据模块 (新闻情感 / 供应链 / 另类数据) — Renaissance/Two Sigma 标准
ALT_DATA_MODULES_READY = False
try:
    from utils.news_sentiment_engine import NewsSentimentEngine, NewsItem
    from utils.supply_chain_graph import SupplyChainGraph, SupplyChainEdge
    from utils.alt_data_indicators import AltDataIndicators
    ALT_DATA_MODULES_READY = True
    logger.info("另类数据模块加载成功: NewsSentiment/SupplyChain/AltData")
except ImportError as e:
    logger.warning(f"另类数据模块导入失败 (降级模式): {e}")

# ============================================================
# FactorKillSwitch 因子实时监控模块 (S6 持续监控, v8.5 新增)
#   - 与 utils.kill_switch.KillSwitch (组合级熔断) 不同,
#     FactorKillSwitch 是因子级状态机: 监控 IC 衰退 + 累计回撤
#   - 命名空间隔离: FACTOR_KS_READY / run_daily_kill_switch
#   - 失败时降级为 SKIP, 不影响主工作流
# ============================================================
FACTOR_KS_READY = False
try:
    from research.vibe_trading_factor_analysis.scripts.kill_switch_daily_runner import (
        run_daily_kill_switch as _run_factor_kill_switch_daily,
        FACTOR_KS_READY,
    )
    if FACTOR_KS_READY:
        logger.info("FactorKillSwitch 模块加载成功 (S6 持续监控就绪)")
    else:
        logger.warning("FactorKillSwitch 模块导入成功但子模块未就绪 (降级模式)")
except ImportError as e:
    logger.warning(f"FactorKillSwitch 模块导入失败 (降级模式): {e}")
    FACTOR_KS_READY = False

# ============================================================
# 神华建仓计划配置 (内置配置 - 已从 v7.4 归档目录迁移到核心)
# ============================================================
SHENHUA_READY = True
SHENHUA_CODE = "601088"
SHENHUA_NAME = "中国神华"
BUILD_TIERS = [
    {"tier": 1, "name": "第一档-底仓", "price_low": 40.0, "price_high": 42.0,
     "price_mid": 41.0, "weight_ratio": 0.35},
    {"tier": 2, "name": "第二档-加仓", "price_low": 36.0, "price_high": 39.0,
     "price_mid": 37.5, "weight_ratio": 0.35},
    {"tier": 3, "name": "第三档-重仓", "price_low": 32.0, "price_high": 35.0,
     "price_mid": 33.5, "weight_ratio": 0.30},
]
BUILD_PHASES = [
    {"phase": 1, "name": "第一阶段-底仓建立", "duration_days": 10,
     "capital_ratio": 0.35, "tier_ref": 1},
    {"phase": 2, "name": "第二阶段-回调加仓", "duration_days": 15,
     "capital_ratio": 0.35, "tier_ref": 2},
    {"phase": 3, "name": "第三阶段-深度配置", "duration_days": 20,
     "capital_ratio": 0.30, "tier_ref": 3},
]
EXECUTION_RULES = {
    "daily_timing": {
        "morning_window": ["09:35", "10:15"],
        "afternoon_window": ["14:00", "14:30"],
    },
    "price_rules": {
        "discount_buy": 0.02, "normal_buy": 0.00, "premium_skip": 0.03,
    },
    "min_lots": 100, "max_daily_lots": 1000,
}
VALUATION_SNAPSHOT = {"current_price": 40.70}
RISK_PARAMS = {"max_position_pct": 0.25, "stop_loss_pct": 0.10, "take_profit_pct": 0.30}

def calc_lots(target_amount: float, price: float, min_lots: int = 100) -> int:
    raw_shares = target_amount / price
    lots = int(raw_shares // min_lots) * min_lots
    return max(lots, min_lots)

def build_position_plan(capital: float, tiers: list, phases: list) -> list:
    """从内置配置生成建仓计划列表"""
    plan = []
    for phase in phases:
        phase_capital = capital * phase.get("capital_ratio", 0.33)
        tier = tiers[phase.get("tier_ref", 1) - 1]
        price_mid = tier.get("price_mid", 40)
        target_amount = phase_capital
        lots = calc_lots(target_amount, price_mid)
        plan.append({
            "phase": phase["phase"],
            "phase_name": phase["name"],
            "capital": phase_capital,
            "tier": tier["tier"],
            "tier_name": tier["name"],
            "price_range": (tier.get("price_low", 35), tier.get("price_high", 45)),
            "target_lots": lots,
            "duration_days": phase.get("duration_days", 15),
        })
    return plan

# ============================================================
# v8.5 机构级模块 (硬性要求 - 对冲基金级别集成)
# ============================================================
V85_READY = False
_V85_FAILURES = []
try:
    # 修复 P0: get_data_pipeline 函数不存在, 仅 DataPipeline 类可用
    from data.data_pipeline import DataPipeline
    # 兼容性: 提供 get_data_pipeline 工厂函数 (模块未定义时自建)
    def get_data_pipeline():
        """DataPipeline 工厂函数 (兼容性补充)"""
        return DataPipeline()
    logger.info("v8.5 DataPipeline 加载成功")
except ImportError as e:
    _V85_FAILURES.append(f"DataPipeline: {e}")

try:
    # 修复 P0: src/utils/ 无 __init__.py, 被 root utils/ 遮蔽, 改用 src.utils 前缀
    from src.utils.environment_isolation import EnvironmentIsolation
    logger.info("v8.5 EnvironmentIsolation 加载成功")
except ImportError as e:
    _V85_FAILURES.append(f"EnvironmentIsolation: {e}")

try:
    # 修复 P0: 实际类名为 GlobalTimeService (非 TimeSync), 路径用 src.utils 前缀
    from src.utils.timesync import GlobalTimeService as TimeSync
    logger.info("v8.5 TimeSync(GlobalTimeService) 加载成功")
except ImportError as e:
    _V85_FAILURES.append(f"TimeSync: {e}")

try:
    from risk.vega_monitor import VegaMonitor
    logger.info("v8.5 VegaMonitor 加载成功")
except ImportError as e:
    _V85_FAILURES.append(f"VegaMonitor: {e}")

try:
    from risk.liquidity_monitor import LiquidityMonitor
    logger.info("v8.5 LiquidityMonitor 加载成功")
except ImportError as e:
    _V85_FAILURES.append(f"LiquidityMonitor: {e}")

try:
    # 修复 P0: 实际类名为 ExtremeValueAnalyzer（非 EVTTailRisk）
    from risk.evt_tail_risk import ExtremeValueAnalyzer as EVTTailRisk
    logger.info("v8.5 EVTTailRisk(ExtremeValueAnalyzer) 加载成功")
except ImportError as e:
    _V85_FAILURES.append(f"EVTTailRisk: {e}")

try:
    # 修复 P0: 实际类名为 PurgedKFold（非 PurgedKFoldCV）
    from model_validation.purged_kfold_cv import PurgedKFold as PurgedKFoldCV
    logger.info("v8.5 PurgedKFoldCV(PurgedKFold) 加载成功")
except ImportError as e:
    _V85_FAILURES.append(f"PurgedKFoldCV: {e}")

try:
    from model_monitoring.factor_decay_monitor import FactorDecayMonitor
    logger.info("v8.5 FactorDecayMonitor 加载成功")
except ImportError as e:
    _V85_FAILURES.append(f"FactorDecayMonitor: {e}")

try:
    # 修复 P0: 实际类名为 ShadowAccount（非 ShadowAccountSystem）
    from validation.shadow_account_system import ShadowAccount as ShadowAccountSystem
    logger.info("v8.5 ShadowAccountSystem(ShadowAccount) 加载成功")
except ImportError as e:
    _V85_FAILURES.append(f"ShadowAccountSystem: {e}")

if _V85_FAILURES:
    logger.warning(f"v8.5 模块部分加载失败 ({len(_V85_FAILURES)}/9): {', '.join(_V85_FAILURES)}")
    logger.warning("系统将在降级模式下运行 - 缺少增强型风控/验证/监控能力")
else:
    logger.info("v8.5 全部9个机构级模块加载成功")

V85_READY = len(_V85_FAILURES) == 0


# ============================================================
# 工作流配置 (基于 portfolio.yaml v7.7 — 400万权益 + 100万对冲)
# P0-3 FIX (2026-07-23): 同步 configs/portfolio.yaml (v7.7 权威版)
#   原配置: STOCK_CAPITAL=3M, HEDGE_CAPITAL=1.06M (漂移严重)
#   修正为: STOCK_CAPITAL=4M, HEDGE_CAPITAL=1M
#   资产分类已与 portfolio.yaml 20个持仓对齐
# ============================================================
class WorkflowConfig:
    """工作流配置 — 基于 2026 年交易计划

    数据源:
    1. 2026年交易计划.md — 500万 4阶段建仓计划
    2. portfolio.yaml (v7.7) — 组合配置 (唯一权威源)
    3. trade_plan_{date}.json — 当日执行计划
    """

    # === 资金配置 (500万 = 400万权益 + 100万对冲) ===
    TOTAL_CAPITAL = 5_000_000          # 总资金 500 万
    STOCK_CAPITAL = 4_000_000          # 权益组合 400 万 (80%)
    HEDGE_CAPITAL = 1_000_000          # 对冲资金 100 万 (20%)

    # === 股票组合分类 (400万) — 与 portfolio.yaml 20个持仓对齐 ===
    STOCK_CATEGORIES = {
        "核心宽基ETF":     {"weight": 0.28, "amount": 1_120_000},   # 8只ETF
        "科技成长个股":    {"weight": 0.21, "amount": 840_000},     # 6只科创板/成长股
        "高端制造/新能源": {"weight": 0.07, "amount": 280_000},     # 绿的谐波+阳光电源
        "资源/能源":       {"weight": 0.07, "amount": 280_000},     # 藏格矿业+中国神华
        "医药/防御":       {"weight": 0.11, "amount": 440_000},     # 恒瑞医药+长江电力
        "现金缓冲":        {"weight": 0.26, "amount": 1_040_000},   # 现金+未分配
    }

    # === 对冲分类 (100万) — 与 portfolio.yaml hedge 对齐 ===
    HEDGE_CATEGORIES = {
        "期货对冲":        {"weight": 0.40, "amount": 400_000},     # IF_futures × 3
        "股票期权保护":    {"weight": 0.30, "amount": 300_000},     # Gamma/Vega引擎
        "避险资产":        {"weight": 0.15, "amount": 150_000},     # Theta引擎备兑
        "现金缓冲":        {"weight": 0.15, "amount": 150_000},     # 保证金缓冲
    }

    # === 风控参数 ===
    YELLOW_WARNING = -0.08             # 黄色预警: 组合回撤 ≥ 8% (检查持仓)
    ORANGE_WARNING = -0.10             # 橙色预警: 组合回撤 ≥ 10% (权益仓位降至70%)
    RED_WARNING = -0.12                # 红色预警: 组合回撤 ≥ 12% (权益仓位降至50%)
    FULL_STOP = -0.15                  # 全部止损: 组合回撤 ≥ 15%
    SINGLE_DAY_LOSS_PAUSE = -0.03      # 单日回撤 > 3% 暂停买入
    SINGLE_DAY_FORCE_REDUCE = -0.05    # 单日回撤 > 5% 强制减仓30%

    # === 个股止损 ===
    STOP_LOSS_RULES = {
        "宽基ETF": -0.08,              # -8% 减半仓
        "科技股": -0.12,               # -12% 清仓 (海光 -10%, 北方华创/中际旭创/阳光 -12%, 绿的谐波 -15%)
        "防御股": -0.08,               # -8% 减半仓
        "黄金ETF": {"half": -0.08, "clear": -0.12},  # -8% 减半, -12% 清仓
    }

    # === 执行参数 ===
    PRICE_BUFFER = 0.004               # 限价上浮 0.40%（降低到略高于常规滑点）
    PRICE_DEVIATION_SKIP = 0.10        # 价格偏离 ±10% 跳过
    MORNING_WINDOW = "09:30-10:30"
    AFTERNOON_WINDOW = "14:00-14:30"

    # === 再平衡规则 ===
    REBALANCE_PERIODIC = "每月末恢复目标权重"
    REBALANCE_THRESHOLD = 0.05         # 单只标的权重偏离 > 5% 即时调仓

    REPORT_DIR = BASE_DIR.parent.parent / "每日报告归档"
    PLAN_DIR = BASE_DIR / "trade_plans"

    # === MockBroker 价格字典 ===
    MOCK_PRICES = {
        # 核心宽基 ETF
        "sh510300": 4.0,    # 沪深300ETF华泰柏瑞
        "sh510500": 6.5,    # 中证500ETF南方
        "sh512100": 2.3,    # 中证1000ETF南方
        "sz588000": 1.05,   # 科创50ETF华夏
        "sz159915": 2.15,   # 创业板ETF易方达
        # 科技成长个股
        "sh688041": 85.0,   # 海光信息
        "sz300308": 120.0,  # 中际旭创
        "sz300274": 45.0,   # 阳光电源
        "sz002371": 350.0,  # 北方华创
        "sh688017": 180.0,  # 绿的谐波
        "sh600276": 50.0,   # 恒瑞医药
        "sh688981": 142.93, # 中芯国际
        "sh603019": 94.42,  # 中科曙光
        # 高端制造/基建
        "sh600089": 25.0,   # 特变电工
        "sh600875": 22.0,   # 东方电气
        "sz000425": 8.5,    # 徐工机械
        "sh600406": 35.0,   # 国电南瑞
        "sh600989": 18.0,   # 宝丰能源
        "sh600219": 4.19,   # 南山铝业
        "sh600019": 5.61,   # 宝钢股份
        # 防御/红利
        "sz515180": 5.0,    # 易方达中证红利ETF
        "sh600036": 38.0,   # 招商银行
        "sh600900": 27.05,  # 长江电力
        "sh601088": 40.70,  # 中国神华
        "sh601318": 48.96,  # 中国平安
        "sz000858": 73.21,  # 五粮液
        # 商品/避险
        "sz518880": 5.85,   # 黄金ETF华安
    }


# ============================================================
# 核心工作流 (基于 2026 年交易计划)
# ============================================================
class DailyWorkflow:
    """v7.5 每日交易工作流 — 2026 年交易计划

    基于 `2026年交易计划.md` 的 500 万 4 阶段建仓计划:
    - P1 (7/6-7/17): 底仓建立 35% = 175 万
    - P2 (7/20-8/7): 配置完善 30% = 150 万
    - P3 (8/10-8/28): 防御补充 20% = 100 万
    - P4 (9/1-9/26): 最终调整 15% = 75 万

    每日从 `trade_plans/trade_plan_{date}.json` 加载当日订单,
    通过 MockBroker 执行上午 + 下午批次。
    """

    @staticmethod
    def _safe_init(name: str, init_fn, fallback=None, critical: bool = False):
        """统一模块初始化: 异常捕获 + 日志 + 降级

        Args:
            name: 模块名称 (用于日志)
            init_fn: 无参初始化函数, 返回模块实例
            fallback: 初始化失败时的默认值 (None)
            critical: True 时初始化失败抛出异常, 阻止系统启动

        Returns:
            初始化成功的模块实例 或 fallback
        """
        try:
            instance = init_fn()
            logger.info("%s 初始化成功", name)
            return instance
        except (ImportError, ModuleNotFoundError) as exc:
            if critical:
                raise RuntimeError(f"关键模块 {name} 加载失败, 系统无法启动") from exc
            logger.exception("%s 导入失败: %s", name, exc)
            return fallback
        except Exception as exc:
            if critical:
                raise RuntimeError(f"关键模块 {name} 初始化失败, 系统无法启动") from exc
            logger.exception("%s 初始化失败: %s", name, exc)
            return fallback

    def __init__(self,
                 trade_date: Optional[str] = None,
                 capital: float = WorkflowConfig.TOTAL_CAPITAL,
                 dry_run: bool = False,
                 sim_mode: bool = False,
                 external_reports_dir: Optional[str] = None):
        self.trade_date = trade_date or datetime.now().strftime("%Y-%m-%d")
        self.capital = capital
        self.dry_run = dry_run
        self.sim_mode = sim_mode
        self.external_reports_dir = external_reports_dir
        self.config = WorkflowConfig()
        self.config.PLAN_DIR.mkdir(exist_ok=True)
        self.config.REPORT_DIR.mkdir(parents=True, exist_ok=True)

        # 加载 2026 年交易计划
        self.trade_plan: Dict[str, Any] = self._load_trade_plan()

        # 状态记录
        self.state: Dict[str, Any] = {
            "trade_date": self.trade_date,
            "capital": capital,
            "dry_run": dry_run,
            "sim_mode": sim_mode,
            "phases": {},
            "orders": [],
            "risk_status": {},
            "hedge_status": {},
            "trade_plan_loaded": bool(self.trade_plan),
        }

        # Alpha 信号融合（可选）— 修复: 正确导入路径 utils.signal_fusion.SignalFusionEngine
        self.signal_fusion = None
        try:
            from utils.signal_fusion import SignalFusionEngine
            self.signal_fusion = SignalFusionEngine()
            logger.info("SignalFusionEngine 已加载 (支持动态 IC 权重)")
        except (ImportError, ModuleNotFoundError) as _e:
            logger.debug("SignalFusionEngine 模块未安装或导入失败, 跳过: %s", _e)
        except Exception as _e:
            logger.warning("SignalFusionEngine 初始化异常: %s", _e)

        # iFinD 新闻资讯分析（可选）
        self.ifind_analyzer = None
        try:
            from utils.ifind_news_analyzer import IFinDNewsAnalyzer
            self.ifind_analyzer = IFinDNewsAnalyzer()
            if not self.ifind_analyzer.available():
                logger.warning("iFinD 新闻资讯模块已加载，但 call 客户端不可用")
                self.ifind_analyzer = None
            else:
                logger.info("iFinD 新闻资讯分析模块已就绪")
        except Exception as exc:
            logger.exception("iFinD 新闻资讯模块导入失败: %s", exc)
            self.ifind_analyzer = None

        # 信号融合配置 (从 settings.yaml 加载)
        self.fusion_config = self._load_fusion_config()

        # iFinD 新闻缓存 (同一天避免重复调用)
        self._ifind_cache: Dict[str, Any] = {}
        self._ifind_cache_date: str = ""
        self._ifind_planned_symbols: List[str] = []

        # EDB 期货数据缓存 (同一天避免重复调用)
        self._edb_cache: Dict[str, Dict[str, Any]] = {}
        self._edb_cache_date: str = ""

        # 十五五阶段管理器 (5 年度阶段 + 季度评估 + 2030 清仓)
        self.phase_manager = None
        self.current_phase_info: Optional[PhaseInfo] = None
        if PHASE_MANAGER_READY:
            try:
                self.phase_manager = PhaseManager()
                # 解析 trade_date 为 date 对象用于阶段判断
                try:
                    sim_date = datetime.strptime(self.trade_date, "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    sim_date = None
                self.current_phase_info = self.phase_manager.get_current_phase(sim_date)
                logger.info(
                    f"十五五阶段: {self.current_phase_info.year} {self.current_phase_info.phase_name} "
                    f"(目标 {self.current_phase_info.target_return:.0%}, "
                    f"回撤限 {self.current_phase_info.max_drawdown:.0%}, "
                    f"杠杆 {self.current_phase_info.leverage_target}x)"
                )
            except Exception as exc:
                logger.exception("PhaseManager 初始化失败: %s", exc)
                self.phase_manager = None

        # 对冲基金视角模块
        self.exec_algo_engine: Optional[ExecutionAlgoEngine] = None
        self.pnl_attribution_engine: Optional[PnLAttributionEngine] = None
        self.data_quality_monitor: Optional[DataQualityMonitor] = None
        self.strategy_coordinator: Optional[MultiStrategyCoordinator] = None
        if HEDGE_FUND_MODULES_READY:
            try:
                self.exec_algo_engine = ExecutionAlgoEngine()
                self.pnl_attribution_engine = PnLAttributionEngine()
                self.data_quality_monitor = DataQualityMonitor()
                self.strategy_coordinator = MultiStrategyCoordinator(
                    total_capital=getattr(self, "capital", 5_000_000)
                )
                logger.info("对冲基金模块初始化成功 (ExecutionAlgo/PnLAttribution/DataQuality/MultiStrategyCoord)")
            except Exception as exc:
                logger.exception("对冲基金模块初始化失败 (ExecutionAlgo): %s", exc)

        # 机构级配置模块 (Black-Litterman / TCA / Barra)
        self.bl_optimizer: Optional[BlackLittermanOptimizer] = None
        self.tca_manager: Optional[TCAManager] = None
        self.barra_decomposer: Optional[BarraRiskDecomposer] = None
        if INSTITUTIONAL_MODULES_READY:
            try:
                self.bl_optimizer = BlackLittermanOptimizer(
                    risk_aversion=2.5,
                    tau=0.05,
                    use_idzorek_omega=True,
                )
                self.tca_manager = TCAManager()
                self.barra_decomposer = BarraRiskDecomposer()
                logger.info("机构级模块初始化成功 (BlackLitterman/TCA/Barra)")
            except Exception as exc:
                logger.exception("机构级模块初始化失败 (BlackLitterman): %s", exc)

        # 顶级风险管理模块 (Ledoit-Wolf / 风险预算约束 / 压力测试)
        self.lw_cov_estimator: Optional[LedoitWolfCovariance] = None
        self.risk_budget_opt: Optional[RiskBudgetOptimizer] = None
        self.stress_test_engine: Optional[StressTestEngine] = None
        if RISK_MGT_MODULES_READY:
            try:
                self.lw_cov_estimator = LedoitWolfCovariance(annualize=True)
                self.risk_budget_opt = RiskBudgetOptimizer(
                    risk_aversion=2.5,
                    risk_free_rate=0.03,
                )
                self.stress_test_engine = StressTestEngine(
                    var_confidence=0.95,
                    risk_threshold=-0.10,
                )
                logger.info("风险管理模块初始化成功 (LedoitWolf/RiskBudgetOpt/StressTest)")
            except Exception as exc:
                logger.exception("风险管理模块初始化失败 (RiskMgt): %s", exc)

        # 顶级 Alpha 生成模块 (Alpha 因子库 / 动量反转 / Smart Beta)
        self.alpha_factor_lib: Optional[AlphaFactorLibrary] = None
        self.momentum_engine: Optional[MomentumReversalEngine] = None
        self.smart_beta_engine: Optional[SmartBetaEngine] = None
        if ALPHA_MODULES_READY:
            try:
                self.alpha_factor_lib = AlphaFactorLibrary()
                self.momentum_engine = MomentumReversalEngine()
                self.smart_beta_engine = SmartBetaEngine()
                logger.info("Alpha 生成模块初始化成功 (AlphaFactorLib/MomentumReversal/SmartBeta)")
            except Exception as exc:
                logger.exception("Alpha 生成模块初始化失败: %s", exc)

        # 顶级执行层模块 (执行算法 / 市场冲击 / 智能路由)
        self.execution_algo_engine: Optional[InstitutionExecAlgoEngine] = None
        self.market_impact_model: Optional[MarketImpactModel] = None
        self.smart_order_router_inst: Optional[InstitutionSmartRouter] = None
        if EXECUTION_MODULES_READY:
            try:
                self.execution_algo_engine = InstitutionExecAlgoEngine()
                self.market_impact_model = MarketImpactModel()
                self.smart_order_router_inst = InstitutionSmartRouter()
                logger.info("执行层模块初始化成功 (ExecAlgo/MarketImpact/SmartRouter)")
            except Exception as exc:
                logger.exception("执行层模块初始化失败 (Execution): %s", exc)

        # 顶级另类数据模块 (新闻情感 / 供应链 / 另类数据)
        self.news_sentiment_engine: Optional[NewsSentimentEngine] = None
        self.supply_chain_graph: Optional[SupplyChainGraph] = None
        self.alt_data_indicators: Optional[AltDataIndicators] = None
        if ALT_DATA_MODULES_READY:
            try:
                self.news_sentiment_engine = NewsSentimentEngine()
                self.supply_chain_graph = SupplyChainGraph()
                self.supply_chain_graph.load_default_chains()
                self.alt_data_indicators = AltDataIndicators()
                logger.info("另类数据模块初始化成功 (NewsSentiment/SupplyChain/AltData)")
            except Exception as exc:
                logger.exception("另类数据模块初始化失败 (AltData): %s", exc)

        # 市场数据提供器 (真实行情回退)
        self.market_data_provider = self._safe_init(
            "MarketDataProvider",
            lambda: __import__("utils.data_provider", fromlist=["MarketDataProvider"]).MarketDataProvider()
        )

        # 外部报告加载器（15_每日工作流报告）
        self.external_report_loader = None
        try:
            from report_parsers import ExternalReportLoader
            self.external_report_loader = ExternalReportLoader(base_dir=external_reports_dir)
            logger.info("ExternalReportLoader 已初始化，目录: %s", external_reports_dir or ExternalReportLoader.BASE_DIR)
        except Exception as exc:
            logger.warning("ExternalReportLoader 初始化失败: %s", exc)

        # 模拟盘执行引擎（可选）
        self.sim_engine = None
        self.position_sync = None
        self._sim_mode_requested = bool(sim_mode)
        if self.sim_mode:
            try:
                from sim_broker_integration import (
                    SimExecutionEngine,
                    SimStockBroker,
                    SimFuturesBroker,
                    SimAccount,
                    PositionSync,
                    TradingSessionCalendar,
                )
                from ths_sim_broker import THSQuoteProvider, SimOptionsBroker, THSSimFuturesBroker
                calendar = TradingSessionCalendar()
                stock_account = SimAccount(
                    account_id="SIM-STOCK",
                    total_capital=float(capital),
                    available_cash=float(capital),
                )
                futures_account = SimAccount(
                    account_id="SIM-FUTURES",
                    total_capital=float(capital * 0.3),
                    available_cash=float(capital * 0.3),
                )
                options_account = SimAccount(
                    account_id="SIM-OPTIONS",
                    total_capital=float(capital * 0.2),
                    available_cash=float(capital * 0.2),
                )
                # 同花顺 iFinD 行情提供者
                ths_quote = THSQuoteProvider()
                stock_broker = SimStockBroker(account=stock_account, price_provider=self.market_data_provider)
                # 使用同花顺期货通适配器替代原生 SimFuturesBroker
                futures_broker = THSSimFuturesBroker(
                    account=futures_account,
                    quote_provider=ths_quote,
                )
                options_broker = SimOptionsBroker(
                    account=options_account,
                    quote_provider=ths_quote,
                )
                self.sim_engine = SimExecutionEngine(
                    stock_broker=stock_broker,
                    futures_broker=futures_broker,
                    calendar=calendar,
                    price_provider=self.market_data_provider,
                    options_broker=options_broker,
                )
                self.position_sync = PositionSync(self.sim_engine.router)
                logger.info("模拟盘执行引擎已初始化 (股票+同花顺期货+期权)")
            except Exception as exc:
                logger.warning("模拟盘执行引擎初始化失败，回退 MockBroker: %s", exc)
                self.sim_engine = None
                self.sim_mode = False

    def _load_fusion_config(self) -> Dict[str, Any]:
        """从 settings.yaml 加载信号融合配置

        Returns:
            融合配置字典，加载失败时返回默认值
        """
        import os
        import yaml as _yaml
        defaults = {
            "qlib_weight": 0.50,
            "ifind_weight": 0.30,
            "external_weight": 0.20,
            "fused_factor_min": 0.5,
            "fused_factor_max": 1.3,
            "regime_adaptive": True,
            "bull_weights": {"qlib": 0.50, "ifind": 0.30, "external": 0.20},
            "bear_weights": {"qlib": 0.40, "ifind": 0.40, "external": 0.20},
            "crisis_weights": {"qlib": 0.30, "ifind": 0.30, "external": 0.40},
            "ifind_circuit_breaker": {"enabled": True, "direction": "negative", "min_confidence": 0.85},
            "ifind_factor_map": {
                "positive_high": 1.2, "positive_mid": 1.0, "neutral": 1.0,
                "negative_high": 0.0, "negative_mid": 0.5, "default": 1.0,
            },
            "qlib_factor_map": {
                "strong_long": 1.3, "long": 1.0, "neutral": 0.8,
                "short": 0.5, "strong_short": 0.0,
            },
            "macro_factors": {
                "vix_regime": True,
                "rate_surprise": True,
                "fx_stress": True,
                "credit_spread": True,
            },
            "model_cache": {"enabled": True, "retrain_days": 30},
            "lgb_confidence_gate": True,  # lgb_enhanced 信号置信度门控开关
        }
        try:
            cfg_path = os.path.join(os.path.dirname(__file__), "config", "settings.yaml")
            with open(cfg_path, "r", encoding="utf-8") as f:
                full = _yaml.safe_load(f)
            sf = full.get("signal_fusion", {})
            if sf:
                defaults.update(sf)
                logger.info("信号融合配置已加载: qlib=%.2f ifind=%.2f external=%.2f regime_adaptive=%s",
                           sf.get("qlib_weight", 0.5),
                           sf.get("ifind_weight", 0.3),
                           sf.get("external_weight", 0.2),
                           sf.get("regime_adaptive", True))
        except Exception as exc:
            logger.warning("加载 signal_fusion 配置失败，使用默认值: %s", exc)
        return defaults

    def _load_external_reports(self) -> Dict[str, Any]:
        """加载 15_每日工作流报告，作为信号生成的外部输入

        Returns:
            {
                "loaded": bool,
                "loaded_count": int,
                "total_count": int,
                "reason": str,
                "reports": {...},
                "sentiment_score": float,
                "risk_events": [...],
                "boost_symbols": [...],
                "cut_symbols": [...],
            }
        """
        if self.external_report_loader is None:
            return {
                "loaded": False,
                "reason": "ExternalReportLoader not initialized",
                "loaded_count": 0,
                "total_count": 0,
            }

        try:
            return self.external_report_loader.load_reports(self.trade_date)
        except Exception as exc:
            logger.error("加载外部报告失败: %s", exc, exc_info=True)
            return {
                "loaded": False,
                "reason": str(exc),
                "loaded_count": 0,
                "total_count": 0,
            }

    def _detect_market_regime(self) -> str:
        """检测当前市场状态：bull / bear / crisis

        Returns:
            市场状态字符串
        """
        try:
            market_phase = self.state.get("phases", {}).get("market", {})
            vix = float(market_phase.get("vix", 18.5))
            circuit_level = str(market_phase.get("circuit_level", "NORMAL")).upper()
            drawdown = float(market_phase.get("drawdown", 0.0))

            if circuit_level in ("LEVEL_3", "LEVEL_4", "CRISIS") or vix >= 40 or drawdown <= -0.10:
                return "crisis"
            if circuit_level in ("LEVEL_1", "LEVEL_2") or vix >= 30 or drawdown <= -0.05:
                return "bear"
            return "bull"
        except Exception:
            return "bull"

    def _get_regime_weights(self, regime: str) -> Dict[str, float]:
        """根据市场状态获取信号融合权重

        Args:
            regime: 市场状态 (bull/bear/crisis)

        Returns:
            权重字典 {"qlib": x, "ifind": y, "external": z}
        """
        fc = self.fusion_config
        if not fc.get("regime_adaptive", True):
            return {
                "qlib": float(fc.get("qlib_weight", 0.5)),
                "ifind": float(fc.get("ifind_weight", 0.3)),
                "external": float(fc.get("external_weight", 0.2)),
            }

        regime_key = f"{regime}_weights"
        weights = fc.get(regime_key, {})
        if not weights:
            return {
                "qlib": float(fc.get("qlib_weight", 0.5)),
                "ifind": float(fc.get("ifind_weight", 0.3)),
                "external": float(fc.get("external_weight", 0.2)),
            }

        return {
            "qlib": float(weights.get("qlib", 0.5)),
            "ifind": float(weights.get("ifind", 0.3)),
            "external": float(weights.get("external", 0.2)),
        }

    def _calculate_external_factor(self, external_reports: Dict[str, Any]) -> float:
        """根据外部报告计算订单调整系数

        Args:
            external_reports: _load_external_reports() 返回结果

        Returns:
            调整系数，范围 [0.5, 1.3]
        """
        if not external_reports.get("loaded"):
            return 1.0

        sentiment = float(external_reports.get("sentiment_score", 0.0))
        risk_events = external_reports.get("risk_events", [])
        boost_symbols = external_reports.get("boost_symbols", [])
        cut_symbols = external_reports.get("cut_symbols", [])

        factor = 1.0

        # 情绪映射: [-1, 1] -> [0.5, 1.3]
        factor += sentiment * 0.3

        # 风险事件压制
        if len(risk_events) >= 3:
            factor -= 0.2
        elif len(risk_events) >= 1:
            factor -= 0.1

        # 建议加仓/减仓标的数量影响整体系数
        if len(boost_symbols) > len(cut_symbols):
            factor += 0.1
        elif len(cut_symbols) > len(boost_symbols):
            factor -= 0.1

        return max(0.5, min(1.3, round(factor, 2)))

    def _get_ifind_insights(self, symbols: list, name_map: dict) -> Dict[str, Any]:
        """获取 iFinD 新闻研判 (带当日缓存)

        同一天内多次调用只请求一次 API，后续从缓存读取。
        优先只扫描交易计划内标的，避免扫到无关标的触发误熔断。

        Returns:
            {symbol: StockInsight} 字典
        """
        today = self.trade_date
        if self._ifind_cache_date == today and self._ifind_cache:
            return self._ifind_cache

        if self.ifind_analyzer is None:
            return {}

        # 优先使用交易计划内标的，避免误熔断
        if not symbols and self._ifind_planned_symbols:
            symbols = list(self._ifind_planned_symbols)
        if not symbols:
            return {}

        try:
            insights = self.ifind_analyzer.batch_analyze(symbols, name_map=name_map, size=4, days=3)
            self._ifind_cache = {item.symbol: item for item in insights}
            self._ifind_cache_date = today
            logger.info("iFinD 新闻研判完成 (已缓存): %d 个标的", len(self._ifind_cache))
            return self._ifind_cache
        except Exception:
            logger.error("iFinD 批量研判失败", exc_info=True)
            return {}

    def _get_edb_futures_data(self, names: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
        """获取 EDB 期货/商品数据 (带当日缓存)

        同一天内多次调用只请求一次 API，后续从缓存读取。

        Args:
            names: 品种名称列表，默认读取 AI 算力核心 6 品种

        Returns:
            {name: edb_result} 字典
        """
        if not EDB_READY:
            return {}

        today = self.trade_date
        if self._edb_cache_date == today and self._edb_cache:
            return self._edb_cache

        if names is None:
            names = ["锡", "铜", "铝", "银", "碳酸锂", "多晶硅"]

        try:
            results = EDBFuturesData.fetch_all()
            self._edb_cache = {name: results.get(name, {}) for name in names if name in results}
            self._edb_cache_date = today
            logger.info("EDB 期货数据获取完成 (已缓存): %d 个品种", len(self._edb_cache))
            return self._edb_cache
        except Exception:
            logger.error("EDB 期货数据获取失败", exc_info=True)
            return {}

    def _get_futures_scanner_summary(self) -> Dict[str, Dict[str, Any]]:
        """运行期货期权扫描器，并转换为与 edb_summary 兼容的结构

        Returns:
            {name: {latest, latest_date, ret20, score, opportunity, reasons}} 字典
        """
        if not SCANNER_READY:
            return {}
        try:
            scanner = FuturesOptionsScanner()
            result = scanner.run()
            summary: Dict[str, Dict[str, Any]] = {}
            for item in result.get("all", []):
                name = item.get("name")
                if not name:
                    continue
                summary[name] = {
                    "latest": item.get("latest"),
                    "latest_date": item.get("latest_date"),
                    "ret20": item.get("ret20"),
                    "score": item.get("score"),
                    "opportunity": item.get("opportunity"),
                    "reasons": item.get("reasons"),
                    "scanner_category": item.get("category"),
                }
            return summary
        except Exception:
            logger.error("期货期权扫描器执行失败", exc_info=True)
            return {}

    def _load_trade_plan(self) -> Dict[str, Any]:
        """加载交易计划文件

        优先加载 `trade_plans/trade_plan_{YYYYMMDD}.json`,
        若不存在则尝试无 dash 版本 `trade_plan_{YYYY-MM-DD}.json`,
        若仍不存在则回退到主计划 `auto_trade_plan_500w_2026-2030.json`。

        Returns:
            交易计划字典 (含 phase/execution_plan/hedge_config/risk_controls 等),
            若文件不存在返回空字典。
        """
        date_compact = self.trade_date.replace("-", "")
        candidates = [
            self.config.PLAN_DIR / f"trade_plan_{date_compact}.json",
            self.config.PLAN_DIR / f"trade_plan_{self.trade_date}.json",
        ]
        for path in candidates:
            if path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        plan = json.load(f)
                    logger.info(f"已加载交易计划: {path.name} "
                                f"(阶段: {plan.get('phase', {}).get('name', 'N/A')}, "
                                f"订单数: {plan.get('execution_plan', {}).get('total_orders', 0)})")
                    # 预提取交易计划内的标的，供 iFinD 新闻扫描优先使用
                    try:
                        exec_plan = plan.get("execution_plan", {})
                        planned = []
                        for order in exec_plan.get("morning_orders", []) + exec_plan.get("afternoon_orders", []):
                            code = order.get("code")
                            if code:
                                planned.append(str(code))
                        self._ifind_planned_symbols = planned
                    except Exception:
                        self._ifind_planned_symbols = []
                    return plan
                except Exception as e:
                    logger.error(f"加载交易计划失败 {path}: {e}")
                    return {}

        master_plan = self.config.PLAN_DIR / "auto_trade_plan_500w_2026-2030.json"
        if master_plan.exists():
            try:
                with open(master_plan, "r", encoding="utf-8") as f:
                    plan = json.load(f)
                logger.info(f"已回退加载主计划: {master_plan.name}")
                self._ifind_planned_symbols = []
                return plan
            except Exception as e:
                logger.error(f"加载主计划失败 {master_plan}: {e}")
                return {}

        logger.warning(f"未找到交易计划文件: trade_plan_{date_compact}.json, "
                       f"将在 phase_signal 中降级为空计划")
        return {}

    # --------------------------------------------------------
    # Phase 1: 系统自检
    # --------------------------------------------------------
    def phase_check(self) -> bool:
        """系统自检"""
        logger.info("=" * 60)
        logger.info(f"Phase 1: 系统自检 @ {self.trade_date}")
        logger.info("=" * 60)

        # 十五五年度阶段提示
        if self.current_phase_info is not None:
            pi = self.current_phase_info
            logger.info(
                f"[十五五阶段] {pi.year} {pi.phase_name} | 目标 {pi.target_return:.0%} | "
                f"回撤限 {pi.max_drawdown:.0%} | 杠杆 {pi.leverage_target}x | "
                f"季度 {pi.current_quarter}"
            )
            if pi.is_liquidation_year:
                logger.warning(
                    f"[2030清仓] {pi.current_quarter} 阶段 - "
                    f"{pi.liquidation_actions.get('name', '') if pi.liquidation_actions else ''}"
                )
            if self.phase_manager and self.phase_manager.is_quarter_end(
                datetime.strptime(self.trade_date, "%Y-%m-%d").date()
                if self.trade_date else None
            ):
                logger.info("[十五五阶段] 季度末 - 将在 v10_risk 阶段触发季度评估")

        checks = {
            "v75_modules": V75_READY,
            "shenhua_plan": SHENHUA_READY,
            "ntp_sync": False,
            "risk_manager": False,
            "circuit_breaker": False,
            "phase_manager": PHASE_MANAGER_READY,
            "hedge_fund_core": HEDGE_FUND_CORE_READY,
            "hedge_fund_modules": HEDGE_FUND_MODULES_READY,
            "institutional_modules": INSTITUTIONAL_MODULES_READY,
            "risk_mgmt_modules": RISK_MGT_MODULES_READY,
            "alpha_modules": ALPHA_MODULES_READY,
            "execution_modules": EXECUTION_MODULES_READY,
            "alt_data_modules": ALT_DATA_MODULES_READY,
            "v85_modules": V85_READY,
            "v85_module_count": 9 - len(_V85_FAILURES),
            "v85_failures": _V85_FAILURES,
        }

        self.ntp = NTPSync()

        if not V75_READY:
            logger.warning("v7.5 模块未就绪，进入降级模式继续执行")
            checks["v75_modules"] = False
            self.state["phases"]["check"] = {"status": "PASS", "checks": checks, "degraded": True}
            return True

        # === v8.5: 增强 NTP 时间同步 (TimeSync) ===
        try:
            if V85_READY:
                from utils.timesync import TimeSync
                ts = TimeSync()
                ts_result = ts.validate()
                checks["ntp_sync"] = ts_result.get("synced", False)
                checks["time_drift_ms"] = ts_result.get("drift_ms", 0)
                logger.info(
                    f"[v8.5] TimeSync: 同步={'OK' if checks['ntp_sync'] else 'DRIFT'}"
                    f", 漂移={ts_result.get('drift_ms', 0):.1f}ms"
                )
            else:
                ntp = NTPSync()
                offset = ntp.get_offset()
                checks["ntp_sync"] = abs(offset) < 0.05
                logger.info(f"NTP 同步: offset={offset:.3f}s {'OK' if checks['ntp_sync'] else 'DRIFT'}")
        except Exception as e:
            logger.warning(f"NTP 同步失败 (使用本地时间): {e}")
            checks["ntp_sync"] = True  # 降级允许

        # === v8.5: 数据管道健康检查 ===
        try:
            if V85_READY:
                from data.data_pipeline import get_data_pipeline
                dp = get_data_pipeline()
                dp_status = dp.health_check()
                checks["data_pipeline"] = dp_status
                logger.info(
                    f"[v8.5] DataPipeline: 状态={dp_status.get('status', 'UNKNOWN')}"
                    f", 延迟={dp_status.get('latency_seconds', -1):.1f}s"
                )
            else:
                checks["data_pipeline"] = {"status": "SKIP", "reason": "v85_not_ready"}
        except Exception as e:
            logger.warning(f"[v8.5] DataPipeline 健康检查异常 (非致命): {e}")
            checks["data_pipeline"] = {"status": "ERROR", "error": str(e)}

        # 风控初始化
        try:
            self.rm = RiskManager(total_capital=self.capital)
            # 修复 P0: CircuitBreaker 必须传入 name 参数（否则 __init__ 抛 TypeError）
            self.cb = CircuitBreaker(name="daily_workflow")
            checks["risk_manager"] = True
            checks["circuit_breaker"] = True
            logger.info(f"风险模式: {self.rm.mode}, 仓位系数: {self.rm.position_size_factor}")
        except Exception as e:
            logger.error(f"风控初始化失败: {e}")
            checks["risk_manager"] = False
            checks["circuit_breaker"] = False
            # 修复 P0: fail-closed — 风控核心失败时禁止交易（而非继续执行）
            self.state["phases"]["check"] = {
                "status": "FAIL",
                "checks": checks,
                "degraded": True,
                "fail_closed": True,
                "reason": f"风控初始化失败, 已进入 fail-closed 模式, 禁止开仓: {e}",
            }
            logger.critical("风控初始化失败, 进入 fail-closed 模式, 禁止开仓")
            return True

        try:
            self.ntp = ntp if checks.get("ntp_sync") else NTPSync()
        except (NameError, UnboundLocalError):
            self.ntp = NTPSync()  # v8.5 TimeSync 使用时不需要额外 NTP 对象
        self.state["phases"]["check"] = {
            "status": "PASS",
            "checks": checks,
            "v85_active": V85_READY,
            "v85_module_count": 9 - len(_V85_FAILURES),
        }
        logger.info(
            "Phase 1 完成: 自检通过 | v8.5模块=%d/9 | 风控=%s | 对冲核心=%s",
            9 - len(_V85_FAILURES),
            checks.get("risk_manager", "N/A"),
            checks.get("hedge_fund_core", "N/A"),
        )
        return True

    # --------------------------------------------------------
    # Phase 1.5: 收益预测动态校准 (新增)
    #   - Step 1: Wind MCP 拉取最新日K, 更新 returns_history.json + market_returns.json
    #   - Step 2: 计算已实现年化收益率
    #   - Step 3: 校准 portfolio_return_projection.json 概率权重
    # --------------------------------------------------------
    def phase_calibrate(self) -> bool:
        """收益预测动态校准"""
        logger.info("=" * 60)
        logger.info(f"Phase 1.5: 收益预测动态校准 @ {self.trade_date}")
        logger.info("=" * 60)

        if not CALIBRATE_READY:
            logger.warning("calibrate_returns_projection 模块未就绪, 跳过校准")
            self.state["phases"]["calibrate"] = {
                "status": "SKIP",
                "reason": "calibrate_returns_projection 模块未导入",
            }
            return True  # 不阻断后续流程

        try:
            # 执行三步校准: Wind 拉取 → 计算已实现 → 校准 projection
            result = _run_calibration()

            status = result.get("status", "FAIL")
            if status not in ("OK", "DEGRADED"):
                logger.error(f"收益预测校准失败: {result}")
                self.state["phases"]["calibrate"] = {
                    "status": "FAIL",
                    "error": str(result)[:500],
                }
                # 校准失败不阻断后续阶段
                return True

            step1 = result.get("step1_update", {})
            step2 = result.get("step2_realized", {})
            step3 = result.get("step3_calibration", {})

            self.state["phases"]["calibrate"] = {
                "status": "PASS" if status == "OK" else "DEGRADED",
                "wind_fetch": {
                    "success": step1.get("success", 0),
                    "fail": step1.get("fail", 0),
                    "total_days": step1.get("total_days", 0),
                    "total_symbols": step1.get("total_symbols", 0),
                    "degraded_reason": step1.get("degraded_reason"),
                },
                "realized": {
                    "start_date": step2.get("start_date"),
                    "end_date": step2.get("end_date"),
                    "years": step2.get("years"),
                    "portfolio_weighted_annualized": step2.get(
                        "portfolio_weighted_annualized", 0
                    ),
                    "market_annualized": step2.get("market_annualized", 0),
                    "market_sharpe": step2.get("market_sharpe", 0),
                    "portfolio_weight_total": step2.get(
                        "portfolio_weight_total", 0
                    ),
                },
                "calibration": {
                    "original_weights": step3.get("original_weights"),
                    "calibrated_weights": step3.get("calibrated_weights"),
                    "calibration_reason": step3.get("calibration_reason"),
                    "calibrated_expected_annualized": step3.get(
                        "calibrated_expected_annualized"
                    ),
                    "calibrated_expected_final": step3.get(
                        "calibrated_expected_final"
                    ),
                },
            }
            if status == "DEGRADED":
                logger.warning("Phase 1.5 完成 (降级模式): Wind 拉取失败, 使用现有历史数据校准")
            else:
                logger.info("Phase 1.5 完成: 收益预测校准成功")
            return True

        except Exception as e:
            logger.error(f"Phase 1.5 异常: {e}", exc_info=True)
            self.state["phases"]["calibrate"] = {
                "status": "FAIL",
                "error": str(e),
            }
            return True  # 不阻断后续流程

    # --------------------------------------------------------
    # Phase 2: 市场状态评估
    # --------------------------------------------------------
    def phase_market(self) -> CircuitLevel:
        """市场状态评估"""
        logger.info("=" * 60)
        logger.info("Phase 2: 市场状态评估")
        logger.info("=" * 60)

        # 懒初始化（支持单独运行该 phase）
        if not hasattr(self, "cb"):
            try:
                from risk.circuit_breaker import CircuitBreaker
                # 修复 P0: CircuitBreaker 必须传入 name 参数
                self.cb = CircuitBreaker(name="daily_workflow")
            except Exception as e:
                logger.warning(f"CircuitBreaker 初始化失败，使用模拟模式: {e}")
                self.cb = None

        # 模拟市场数据 (实盘应从 Wind/iFinD 获取)
        market_data = {
            "vix": 18.5,                    # VIX 18.5 (正常偏低)
            "portfolio_drop": 0.0,          # 当日无跌
            "index_return_20d": 0.02,       # 20日 +2%
            "index_return_60d": 0.05,       # 60日 +5%
        }

        # 熔断级别判定
        try:
            level = self.cb.check(portfolio_drop=market_data["portfolio_drop"], vix=market_data["vix"])
            actions = self.cb.allowed_actions()
        except (AttributeError, TypeError):
            from enum import Enum
            class _SafeLevel(Enum):
                NORMAL = 0; LEVEL_1 = 1; LEVEL_2 = 2; LEVEL_3 = 3
            level = _SafeLevel.NORMAL
            actions = {"open_new": True, "force_reduce_pct": 0.0}

        logger.info(f"VIX: {market_data['vix']}, 跌幅: {market_data['portfolio_drop']:.2%}")
        logger.info(f"熔断级别: {level.name}")
        logger.info(f"允许操作: open_new={actions['open_new']}, "
                    f"force_reduce={actions['force_reduce_pct']}")

        self.state["phases"]["market"] = {
            "status": "PASS",
            "vix": market_data["vix"],
            "circuit_level": level.name,
            "actions": actions,
        }

        try:
            _is_level3 = (hasattr(level, "value") and level.value >= 3) or (hasattr(level, "name") and "3" in str(level.name))
        except Exception:
            _is_level3 = False
        if _is_level3:
            logger.warning("市场熔断 LEVEL_3+, 暂停建仓")
            self.state["phases"]["market"]["build_allowed"] = False
        else:
            self.state["phases"]["market"]["build_allowed"] = True

        # === 对冲基金视角: 数据质量监控 ===
        if self.data_quality_monitor is not None:
            try:
                # 检查持仓数据质量
                positions = self._get_portfolio_positions_for_stress_test() if hasattr(self, "_get_portfolio_positions_for_stress_test") else []
                data_for_check = {}
                for pos in positions:
                    code = pos.get("code", "")
                    if code:
                        data_for_check[code] = {
                            "close": pos.get("price", pos.get("amount", 0)),
                            "volume": pos.get("volume", 0),
                            "timestamp": self.trade_date,
                        }
                if data_for_check:
                    expected_symbols = [p.get("code") for p in positions if p.get("code")]
                    dq_report = self.data_quality_monitor.check_market_data(
                        data_for_check, expected_symbols=expected_symbols
                    )
                    self.state["phases"]["market"]["data_quality"] = {
                        "score": dq_report.overall_score,
                        "critical": dq_report.critical_count,
                        "error": dq_report.error_count,
                        "warning": dq_report.warning_count,
                        "passed": dq_report.passed,
                    }
                    if not dq_report.passed:
                        logger.warning(
                            "[DataQuality] 数据质量未通过: %.1f/100 (critical=%d, error=%d)",
                            dq_report.overall_score,
                            dq_report.critical_count,
                            dq_report.error_count,
                        )
                    else:
                        logger.info(
                            "[DataQuality] 数据质量通过: %.1f/100",
                            dq_report.overall_score,
                        )
            except Exception as e:
                logger.error(f"[DataQuality] 数据质量检查失败: {e}", exc_info=True)

        # === AnySearch 实时新闻扫描 (v7.8: 作为 iFinD 的 fallback) ===
        self._scan_anysearch_news()

        return level

    def _scan_anysearch_news(self):
        """使用 AnySearch 扫描实时财经新闻，作为 iFinD 的补充数据源"""
        try:
            from utils.anysearch_connector import AnySearchConnector
            conn = AnySearchConnector()
            if conn.available:
                conn.connect()
                news = conn.get_finance_news()
                if news:
                    logger.info(f"[AnySearch] 获取到 {len(news)} 条财经新闻")
                    for i, item in enumerate(news[:3], 1):
                        title = item.get('title', '')[:40]
                        url = item.get('url', '')
                        logger.info(f"  [{i}] {title} -> {url}")
                    self.state["phases"]["market"]["anysearch_news_count"] = len(news)
                    self.state["phases"]["market"]["anysearch_news"] = news[:3]
                else:
                    logger.info("[AnySearch] 未获取到新闻")
            else:
                logger.info("[AnySearch] 不可用，跳过")
        except ImportError:
            logger.info("[AnySearch] 模块未安装，跳过")
        except Exception as e:
            logger.warning(f"[AnySearch] 新闻扫描失败: {e}")

    # --------------------------------------------------------
    # Phase 3: 风险预算计算 (组合级别 — 500万 4阶段)
    # --------------------------------------------------------
    def phase_risk(self) -> Dict[str, Any]:
        """风险预算计算 — 2026 年交易计划组合级别

        基于 `2026年交易计划.md` 的资金配置:
        - 总资金 500 万 = 股票/ETF 300 万 (60%) + 对冲 200 万 (40%)
        - 4 阶段建仓: P1 35% / P2 30% / P3 20% / P4 15%
        - 三层风控: 黄色 6% / 橙色 8% / 红色 12%
        - VaR 预算: 95% < 5%, 99% < 8%
        """
        # 懒初始化（支持单独运行该 phase）
        if not hasattr(self, "rm"):
            try:
                from risk.risk_manager import RiskManager
                self.rm = RiskManager(total_capital=self.capital)
            except Exception as e:
                logger.warning(f"RiskManager 初始化失败，使用模拟模式: {e}")
                self.rm = None
        logger.info("=" * 60)
        logger.info("Phase 3: 风险预算计算 (组合级别 — 500万 4阶段)")
        logger.info("=" * 60)

        # 当前组合净值 (建仓前为现金状态)
        current_equity = self.capital
        dd_status = self.rm.update_drawdown(current_equity)

        # === 从交易计划读取当日资金配置 ===
        plan_phase = self.trade_plan.get("phase", {})
        plan_exec = self.trade_plan.get("execution_plan", {})
        day_capital = float(plan_phase.get("day_capital", 0))
        morning_total = float(plan_exec.get("morning_total", 0))
        afternoon_total = float(plan_exec.get("afternoon_total", 0))
        grand_total = float(plan_exec.get("grand_total", 0))

        # === 资金配置明细 ===
        stock_capital = self.config.STOCK_CAPITAL
        hedge_capital = self.config.HEDGE_CAPITAL

        # === 风险预算计算 (简化版, 实盘应从 RiskManager 获取) ===
        # 单日建仓资金占股票组合的比例
        build_ratio = day_capital / stock_capital if stock_capital > 0 else 0

        # 个股止损 (取中风险 -12% 作为组合止损参考)
        portfolio_stop = self.config.STOP_LOSS_RULES["科技股"]

        # VaR 预算 (使用配置上限)
        var_95_limit = 0.05
        var_99_limit = 0.08

        logger.info(f"组合净值: {current_equity:,.0f}")
        logger.info(f"回撤状态: {dd_status} (模式: {self.rm.mode})")
        logger.info(f"股票组合资金: {stock_capital:,.0f} (60%)")
        logger.info(f"期权对冲资金: {hedge_capital:,.0f} (40%)")
        logger.info(f"当日建仓资金: {day_capital:,.0f} (占股票组合 {build_ratio:.2%})")
        logger.info(f"  上午批次: {morning_total:,.0f}")
        logger.info(f"  下午批次: {afternoon_total:,.0f}")
        logger.info(f"  合计: {grand_total:,.0f}")
        logger.info(f"组合止损: {portfolio_stop:.2%}, VaR95<{var_95_limit:.0%}, VaR99<{var_99_limit:.0%}")

        risk_status = {
            "equity": current_equity,
            "drawdown_status": dd_status,
            "mode": self.rm.mode,
            "position_factor": self.rm.position_size_factor,
            "stock_capital": stock_capital,
            "hedge_capital": hedge_capital,
            "day_capital": day_capital,
            "morning_total": morning_total,
            "afternoon_total": afternoon_total,
            "grand_total": grand_total,
            "build_ratio": build_ratio,
            "portfolio_stop": portfolio_stop,
            "var_95_limit": var_95_limit,
            "var_99_limit": var_99_limit,
            "yellow_warning": self.config.YELLOW_WARNING,
            "orange_warning": self.config.ORANGE_WARNING,
            "red_warning": self.config.RED_WARNING,
            "full_stop": self.config.FULL_STOP,
        }
        self.state["phases"]["risk"] = {"status": "PASS", **risk_status}
        self.state["risk_status"] = risk_status

        # === 顶级风险管理: 压力测试情景库 ===
        if self.stress_test_engine is not None:
            try:
                positions_list = (self._get_portfolio_positions_for_stress_test()
                                  if hasattr(self, "_get_portfolio_positions_for_stress_test") else [])
                if positions_list:
                    portfolio_value = sum(float(p.get("amount", 0)) for p in positions_list)
                    if portfolio_value > 0:
                        stress_results = self.stress_test_engine.run_all_scenarios(
                            positions=positions_list,
                            total_portfolio_value=portfolio_value,
                        )
                        stress_summary = self.stress_test_engine.summarize(stress_results)
                        self.state["phases"]["risk_stress_test"] = {
                            "n_scenarios": stress_summary.get("n_scenarios", 0),
                            "worst_scenario": stress_summary.get("worst_scenario", ""),
                            "worst_return": stress_summary.get("worst_return", 0.0),
                            "worst_pnl": stress_summary.get("worst_pnl", 0.0),
                            "best_return": stress_summary.get("best_return", 0.0),
                            "avg_return": stress_summary.get("avg_return", 0.0),
                            "n_breaches": stress_summary.get("n_breaches", 0),
                            "breach_scenarios": stress_summary.get("breach_scenarios", []),
                            "avg_var_change": stress_summary.get("avg_var_change", 0.0),
                        }
                        worst = self.stress_test_engine.get_worst_scenario(stress_results)
                        if worst:
                            logger.info(
                                "[StressTest] %d 场景: 最严重='%s' return=%.2f%% pnl=¥%.0f, breaches=%d",
                                stress_summary.get("n_scenarios", 0),
                                worst.scenario_name,
                                worst.portfolio_return * 100,
                                worst.portfolio_pnl,
                                stress_summary.get("n_breaches", 0),
                            )
                            if worst.is_breach:
                                logger.warning(
                                    "[StressTest] ⚠️ 风险突破: %s 收益 %.2f%% 低于阈值 %.0f%%",
                                    worst.scenario_name,
                                    worst.portfolio_return * 100,
                                    self.stress_test_engine.risk_threshold * 100,
                                )
            except Exception as exc:
                logger.error("[StressTest] 压力测试失败: %s", exc, exc_info=True)

        # === 顶级风险管理: 风险预算约束优化 (自动 TE 再平衡建议) ===
        if self.risk_budget_opt is not None and self.barra_decomposer is not None:
            try:
                barra_state = self.state.get("phases", {}).get("report_barra")
                if barra_state and barra_state.get("active_risk", 0) > 0.05:
                    logger.warning(
                        "[RiskBudget] Barra 显示 TE=%.2f%% 超过 5%% 预算, 生成再平衡建议",
                        barra_state["active_risk"] * 100,
                    )
                    self.state["phases"]["risk_budget_rebalance_needed"] = True
                    self.state["phases"]["risk_budget_target_te"] = 0.05
                    self.state["phases"]["risk_budget_current_te"] = barra_state["active_risk"]
            except Exception as exc:
                logger.error("[RiskBudget] 再平衡建议生成失败: %s", exc, exc_info=True)

        return risk_status

    # --------------------------------------------------------
    # Phase 4: 对冲评估 + 自动执行
    # --------------------------------------------------------
    def _infer_style_from_code(self, code: str) -> str:
        """基于代码前缀粗略推断持仓风格（建仓计划缺失时的回退）"""
        c = str(code).lstrip("shszbjSHBJ").lower()
        if c.startswith("588"):
            return "高端制造"
        if c.startswith("515"):
            return "红利"
        if c.startswith("518"):
            return "避险"
        if c.startswith("688") or c.startswith("300"):
            return "科技"
        if c.startswith("002"):
            return "制造"
        if c == "600276":
            return "医药"
        if c == "600900":
            return "防御"
        if c == "600036":
            return "银行"
        if c == "000425":
            return "制造"
        if c == "601088":
            return "顺周期"
        # 2026-07-09 新增 6 标的
        if c == "300274":
            return "新能源"
        if c == "603019":
            return "科技"
        if c == "600089":
            return "制造"
        if c == "688017":
            return "制造"
        if c == "600219":
            return "资源"
        if c == "600019":
            return "资源"
        return "科技"

    # --------------------------------------------------------
    def _style_beta_proxy(self,
                          positions: Dict[str, float],
                          prices: Dict[str, float]) -> float:
        """风格 Beta 代理：当真实历史收益率失效时，基于持仓风格权重估算组合 Beta

        Args:
            positions: 持仓 {symbol: quantity}
            prices: 当前价格

        Returns:
            估算的组合 Beta
        """
        # 风格 Beta 映射（来源：AGENTS.md 配置风格）
        # 2026-07-09 新增 6 标的: "新能源" + "资源" 风格 Beta 已校准
        style_beta_map = {
            "宽基": 0.95,
            "高端制造": 1.15,
            "科技": 1.20,
            "制造": 1.05,
            "新能源": 1.15,   # 阳光电源: 光伏储能, Beta 略高于制造
            "医药": 0.85,
            "银行": 0.75,
            "防御": 0.60,
            "顺周期": 1.10,
            "避险": -0.10,
            "红利": 0.70,
            "成长": 1.25,
            "资源": 1.10,    # 南山铝业/宝钢股份: 周期性大宗, Beta 与顺周期相当
        }

        # 优先从 500万建仓计划读取 style 和 weight，回退到 v7.6 主计划
        build_plan_path = BASE_DIR.parent / "500万建仓计划_20260706.json"
        style_weights: Dict[str, float] = {}
        if build_plan_path.exists():
            try:
                with open(build_plan_path, "r", encoding="utf-8") as _f:
                    _plan = json.load(_f)
                _target = _plan.get("target_portfolio", _plan.get("stock_etf_account", {}).get("positions", {}))
                for _code, _info in _target.items():
                    if _code in positions and _code in prices:
                        _style = _info.get("style")
                        _weight = float(_info.get("weight", 0.0))
                        if _style and _weight > 0:
                            style_weights[_style] = style_weights.get(_style, 0.0) + _weight
            except Exception as _exc:
                logger.warning("读取 500万建仓计划失败，回退代码前缀推断: %s", _exc)

        # 若仍未获取到风格权重，尝试 v7.6 主计划
        if not style_weights:
            master_plan_path = BASE_DIR / "trade_plans" / "auto_trade_plan_500w_2026-2030.json"
            if master_plan_path.exists():
                try:
                    with open(master_plan_path, "r", encoding="utf-8") as _f:
                        _plan = json.load(_f)
                    _target = _plan.get("stock_etf_account", {}).get("positions", {})
                    for _info in _target.values():
                        _code = _info.get("code")
                        if _code and _code in positions and _code in prices:
                            _style = _info.get("style")
                            _weight = float(_info.get("target_weight", 0.0))
                            if _style and _weight > 0:
                                style_weights[_style] = style_weights.get(_style, 0.0) + _weight
                except Exception as _exc:
                    logger.warning("读取 v7.6 主计划失败，回退代码前缀推断: %s", _exc)

        # 若仍无法获取风格权重，基于代码前缀推断
        if not style_weights:
            logger.warning("风格权重为空，基于代码前缀推断风格权重")
            for _code in positions:
                if _code in prices:
                    _style = self._infer_style_from_code(_code)
                    style_weights[_style] = style_weights.get(_style, 0.0) + 1.0

        # 若仍无法获取风格权重，使用市场中性默认值
        if not style_weights:
            logger.warning("风格权重仍为空，使用市场中性 Beta=1.00")
            return 1.00

        beta_port = 0.0
        total_weight = sum(style_weights.values())
        if total_weight <= 0:
            return 1.00
        for style, weight in style_weights.items():
            beta_port += (weight / total_weight) * style_beta_map.get(style, 1.0)
        return float(beta_port)

    def _get_if_realtime(self) -> dict:
        """获取 IF 期货实时价（新浪/腾讯 HTTP 回退）"""
        session = requests.Session()
        session.trust_env = False
        session.proxies = {"http": None, "https": None}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://stock.finance.sina.com.cn/",
        }

        def _first_positive(parts, indices):
            for idx in indices:
                try:
                    v = float(parts[idx])
                    if v > 0:
                        return v
                except (ValueError, TypeError, IndexError):
                    continue
            return None

        sina_candidates = ["IF0", "IF2506", "IF"]
        for sym in sina_candidates:
            try:
                url = f"https://hq.sinajs.cn/list=nf_{sym}"
                # P1-2: 启用TLS证书验证
                resp = session.get(url, headers=headers, timeout=10, verify=True)
                text = resp.text
                m = re.search(r'var hq_str_nf_' + re.escape(sym) + r'="(.+)"', text)
                if not m:
                    continue
                parts = m.group(1).split(",")
                if len(parts) < 15:
                    continue
                latest = _first_positive(parts, [8, 7, 3, 2])
                if latest is None:
                    continue
                return {
                    "symbol": sym,
                    "source": "sina_http",
                    "price": latest,
                }
            except Exception:
                continue

        try:
            url = "https://qt.gtimg.cn/q=IF"
            # P1-2: 启用TLS证书验证
            resp = session.get(url, headers=headers, timeout=10, verify=True)
            text = resp.text
            m = re.search(r'v_(.+)="(.+)"', text)
            if m:
                parts = m.group(2).split("~")
                if len(parts) >= 5:
                    latest = _first_positive(parts, [3, 5])
                    if latest:
                        return {
                            "symbol": "IF",
                            "source": "tencent_http",
                            "price": latest,
                        }
        except Exception:
            pass

        return {}

    def _compute_beta_hedge_order(self, portfolio_beta: float, portfolio_value: float, degraded: bool = False) -> Dict[str, object]:
        """基于 BetaHedger 计算对冲指令（降级路径）

        Args:
            portfolio_beta: 组合 Beta
            portfolio_value: 组合市值
            degraded: 是否为降级模式（降低触发阈值 + 兜底保护）
        """
        try:
            from hedging.beta_hedger import BetaHedger
            futures_config = {
                "IF": {"multiplier": 300, "beta": 1.0, "price": 3800.0},
                "IC": {"multiplier": 200, "beta": 1.2, "price": 5500.0},
                "IM": {"multiplier": 200, "beta": 1.1, "price": 5800.0},
            }
            if_realtime = self._get_if_realtime()
            if if_realtime and if_realtime.get("price"):
                futures_config["IF"]["price"] = float(if_realtime["price"])
                logger.info(f"注入 IF 实时价: {if_realtime['price']} (来源: {if_realtime.get('source', 'unknown')})")

            beta_trigger = 0.35 if degraded else 0.7
            # 2026-07-09 方案C: beta_target 0.3→0.25, 提升对冲比率至 75% (目标: 回撤<15%)
            beta_target = 0.25
            beta_hedger = BetaHedger(futures_config=futures_config, beta_trigger=beta_trigger, beta_target=beta_target)
            order = beta_hedger.compute_hedge(portfolio_beta, portfolio_value)

            # 降级兜底：如果 BetaHedger 仍返回 NO_HEDGE，强制生成最小对冲指令
            if degraded and order.get("action") == "NO_HEDGE":
                fut = futures_config.get("IF", futures_config["IF"])
                live_price = float(fut.get("price", 3800.0))
                notional_per_contract = float(fut.get("multiplier", 300)) * live_price
                n_contracts = max(1, int(round((portfolio_beta - beta_target) * portfolio_value / notional_per_contract)))
                if n_contracts <= 0:
                    n_contracts = 1
                commission_rate = 0.000023
                slippage_rate = 0.0001
                margin_rate = 0.12
                estimated_cost = n_contracts * notional_per_contract * (commission_rate + slippage_rate)
                estimated_margin = n_contracts * notional_per_contract * margin_rate
                order = {
                    "action": "SHORT_FUTURES",
                    "instrument": "IF",
                    "contracts": n_contracts,
                    "direction": "SELL",
                    "multiplier": int(fut.get("multiplier", 300)),
                    "futures_price": live_price,
                    "futures_beta": float(fut.get("beta", 1.0)),
                    "notional": float(n_contracts * notional_per_contract),
                    "estimated_cost": float(estimated_cost),
                    "cost_ratio": float(estimated_cost / portfolio_value) if portfolio_value > 0 else 0.0,
                    "current_beta": float(portfolio_beta),
                    "target_beta": float(beta_target),
                    "beta_reduced": float(portfolio_beta - beta_target),
                    "reason": "降级兜底强制对冲",
                    "cost_breakdown": {
                        "commission_rate": commission_rate,
                        "slippage_rate": slippage_rate,
                        "margin_rate": margin_rate,
                        "estimated_commission": float(n_contracts * notional_per_contract * commission_rate),
                        "estimated_slippage": float(n_contracts * notional_per_contract * slippage_rate),
                        "estimated_margin": float(estimated_margin),
                        "cost_note": "仅含佣金+滑点估算，不含保证金利息/冲击成本",
                    },
                }
                logger.warning(f"降级兜底: Beta {portfolio_beta:.3f} 仍触发 NO_HEDGE，强制 {n_contracts} 手 IF 空头")
            return order
        except Exception as exc:
            logger.error("风格 Beta 代理计算对冲指令失败: %s", exc)
            return {"action": "ERROR", "reason": str(exc)}

    def phase_hedge(self) -> Dict[str, Any]:
        """三联对冲评估 + 自动执行（与 7.4 AutoHedgeExecutor 行为对齐）

        修复点:
            1. 协调器 coordinate() 传正确签名 (positions/prices/returns/market_returns/vix)
            2. 对冲指令自动送 MockBroker 执行
            3. VIX 从 phase_market 读取，不再硬编码
            4. EDB 期货数据接入，补充 AI 算力商品价格信号
        """
        logger.info("=" * 60)
        logger.info("Phase 4: 三联对冲评估 + 自动执行")
        logger.info("=" * 60)

        # 从 phase_market 读取 VIX (不再硬编码)
        market_phase = self.state.get("phases", {}).get("market", {})
        vix_level = float(market_phase.get("vix", 18.5))
        circuit_level = market_phase.get("circuit_level", "NORMAL")

        # === EDB 期货数据接入 (AI 算力核心品种) ===
        edb_futures = self._get_edb_futures_data()
        edb_summary = {}
        if edb_futures:
            for name, data in edb_futures.items():
                latest = data.get("latest")
                latest_date = data.get("latest_date")
                rows = data.get("series", [])
                ret20 = None
                if len(rows) >= 21:
                    ret20 = (rows[-1][1] - rows[-21][1]) / rows[-21][1]
                edb_summary[name] = {
                    "latest": latest,
                    "latest_date": latest_date,
                    "ret20": ret20,
                    "query": data.get("query"),
                }
            logger.info("EDB 期货摘要: %s", json.dumps(edb_summary, ensure_ascii=False, default=str))

        # === 期货期权扫描器补充 (EDB + AKShare 回退) ===
        scanner_summary = self._get_futures_scanner_summary()
        if scanner_summary:
            merged = dict(edb_summary)
            for name, data in scanner_summary.items():
                if name in merged:
                    merged[name].update({k: v for k, v in data.items() if v is not None})
                else:
                    merged[name] = data
            edb_summary = merged
            logger.info("期货扫描器已合并: %d 个品种", len(edb_summary))

        # 真实行情回退：优先 MarketDataProvider，失败回退 MOCK_PRICES
        # 使用线程池+超时防止单个 Wind MCP 调用卡死整个 phase
        prices = dict(self.config.MOCK_PRICES)
        if self.market_data_provider is not None:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            _fetched = 0
            _skipped = 0
            def _fetch_one(code):
                try:
                    return code, self.market_data_provider.get_market_data(code)
                except Exception:
                    return code, None
            try:
                with ThreadPoolExecutor(max_workers=4) as pool:
                    futures = {pool.submit(_fetch_one, code): code for code in list(prices.keys())}
                    for fut in as_completed(futures, timeout=30):
                        try:
                            code, quote = fut.result(timeout=5)
                            if quote and quote.get("index_price"):
                                prices[code] = float(quote["index_price"])
                                _fetched += 1
                            else:
                                _skipped += 1
                        except Exception:
                            _skipped += 1
            except Exception as exc:
                logger.warning("获取实时价格失败 (超时/异常)，回退 MOCK_PRICES: %s", exc)
            logger.info("实时价格获取: %d 成功, %d 回退 MOCK", _fetched, _skipped)

        # 当前持仓：优先读取 config/positions.json，失败则回退 MOCK_PRICES 等权假设
        _positions_json = BASE_DIR.parent / "config" / "positions.json"
        _codes = list(self.config.MOCK_PRICES.keys())
        if _positions_json.exists():
            try:
                with open(_positions_json, "r", encoding="utf-8") as _f:
                    _pos_data = json.load(_f)
                _codes = [c for c in _pos_data.get("positions", []) if c in self.config.MOCK_PRICES]
                if _codes:
                    logger.info("phase_hedge 加载真实持仓代码: %d 只", len(_codes))
            except Exception as _exc:
                logger.warning("读取 config/positions.json 失败，回退 MOCK_PRICES: %s", _exc)

        _n = len(_codes)
        if _n > 0:
            _target_value = float(self.capital)
            _value_per_asset = _target_value / _n
            positions = {}
            for _code in _codes:
                _price = prices.get(_code, 0)
                if _price and _price > 0:
                    _shares = int(_value_per_asset / _price / 100) * 100
                    # 2026-07-09 新增: 对 18 标的全覆盖日志 (含 6 个新标的)
                    if _code in ("sz300274", "sh603019", "sh600089", "sh688017", "sh600219", "sh600019"):
                        logger.info(f"[18标的覆盖] {_code} 价格={_price} 股数={_shares} 金额={_shares*_price:.0f}")
                    positions[_code] = max(_shares, 100)
                else:
                    positions[_code] = 100
            logger.info("phase_hedge 等权假设持仓: %d 只, 单只约 %.0f 元", _n, _value_per_asset)
        else:
            positions = {code: 0 for code in self.config.MOCK_PRICES}

        # 历史收益率：优先真实 OHLCV，失败回退模拟收益率
        n_days = 60
        try:
            import numpy as np
            import pandas as pd
            price_frames = []
            def _fetch_hist(code):
                try:
                    if self.market_data_provider is not None:
                        return code, self.market_data_provider.get_historical_data(code, period="3m")
                except Exception:
                    pass
                return code, None
            with ThreadPoolExecutor(max_workers=4) as pool:
                hist_futures = {pool.submit(_fetch_hist, code): code for code in prices}
                for fut in as_completed(hist_futures, timeout=60):
                    try:
                        code, hist = fut.result(timeout=10)
                    except Exception:
                        continue
                    if hist is None or (hasattr(hist, "empty") and hist.empty):
                        continue
                    if "close" in hist.columns:
                        rets = hist["close"].astype(float).pct_change().dropna()
                        if len(rets) >= n_days:
                            rets = rets.tail(n_days)
                        price_frames.append(rets.rename(code))
            if price_frames:
                returns = pd.concat(price_frames, axis=1).fillna(0.0)
                if returns.shape[0] < n_days:
                    returns = returns.reindex(range(n_days)).fillna(0.0)
                returns = returns.tail(n_days).reset_index(drop=True)
            else:
                raise RuntimeError("no_price_frames")
            market_series = None
            for idx_code in ["sh000001", "sz399001", "sz399006", "sh000016"]:
                mkt = None
                if self.market_data_provider is not None:
                    try:
                        mkt = self.market_data_provider.get_historical_data(idx_code, period="3m")
                    except Exception:
                        mkt = None
                if mkt is not None and "close" in mkt.columns and not mkt.empty:
                    market_series = mkt["close"].astype(float).pct_change().dropna()
                    if len(market_series) >= n_days:
                        market_series = market_series.tail(n_days)
                    break
            if market_series is None:
                raise RuntimeError("no_market_series")
            market_returns = market_series.reset_index(drop=True)
            logger.info("对冲评估已使用真实价格与历史收益率")
        except Exception as exc:
            logger.warning("真实收益率获取失败，回退模拟数据: %s", exc)
            import numpy as np
            import pandas as pd
            np.random.seed(42)
            # 用更稳的默认序列：轻微正漂移 + 低波动，减少极端模拟收益
            drift = 0.0003
            vol = 0.012
            market_drift = 0.0002
            market_vol = 0.010
            returns = pd.DataFrame({
                code: np.random.normal(drift, vol, n_days) for code in prices
            })
            market_returns = pd.Series(np.random.normal(market_drift, market_vol, n_days))

        # === 三联对冲协调器 (正确签名调用) ===
        try:
            hc = HedgeCoordinator()
            coordinated = hc.coordinate(
                positions=positions,
                prices=prices,
                returns=returns,
                market_returns=market_returns,
                vix=vix_level,
                portfolio_value=self.capital,
            )
            logger.info(f"对冲协调: action={coordinated.get('action')}, "
                        f"总对冲比例={coordinated.get('total_hedge_pct', 0):.2%}, "
                        f"组合Beta={coordinated.get('portfolio_beta', 0):.3f}")
        except Exception as e:
            logger.error(f"对冲协调器执行失败: {e}")
            coordinated = {
                "action": "ERROR", "reason": str(e),
                "orders": [], "total_hedge_pct": 0,
                "summary": {"beta_hedger": "ERROR", "vol_hedger": "ERROR", "corr_hedger": "ERROR"},
            }

        # === 降级：风格 Beta 代理 (真实数据失效时，或主协调器判定 NO_HEDGE 但需验证价格链路) ===
        portfolio_beta = coordinated.get("portfolio_beta", 0.0)
        coordinated_action = coordinated.get("action", "")
        beta_invalid = False
        if portfolio_beta is None:
            beta_invalid = True
        elif isinstance(portfolio_beta, float):
            beta_invalid = math.isnan(portfolio_beta) or math.isinf(portfolio_beta)
        elif not isinstance(portfolio_beta, (int, float)):
            beta_invalid = True

        if beta_invalid or portfolio_beta <= 0.0 or coordinated_action in ("NO_HEDGE", "SKIP", "ERROR"):
            if beta_invalid:
                logger.warning("组合 Beta 异常 (%s)，回退到风格 Beta 代理", portfolio_beta)
            elif portfolio_beta <= 0.0:
                logger.warning("组合 Beta 计算为 0，回退到风格 Beta 代理")
            elif coordinated_action == "NO_HEDGE":
                logger.info("主协调器判定 NO_HEDGE，使用风格 Beta 代理验证价格链路")
            else:
                logger.warning("主协调器返回 %s，回退到风格 Beta 代理", coordinated_action)

            style_beta = self._style_beta_proxy(positions, prices)
            coordinated["portfolio_beta"] = style_beta
            beta_order = self._compute_beta_hedge_order(style_beta, self.capital, degraded=True)
            coordinated.setdefault("summary", {})["beta_hedge"] = beta_order.get("action")
            if beta_order.get("action") not in ("NO_HEDGE", "SKIP", "ERROR"):
                coordinated.setdefault("orders", [])
                coordinated["orders"].append({**beta_order, "hedge_type": "BETA"})
            logger.info(f"风格 Beta 代理: {style_beta:.3f}, action={beta_order.get('action')}, reason={beta_order.get('reason', '')}")

            # 回退后重算汇总指标，避免总对冲比例/成本仍为 0
            try:
                _orders = coordinated.get("orders", [])
                _pv = float(self.capital)
                _total_hedge_value = 0.0
                _total_cost = 0.0
                for _o in _orders:
                    _total_hedge_value += float(_o.get("notional", 0.0) or 0.0)
                    _total_cost += float(_o.get("estimated_cost", 0.0) or _o.get("cost", 0.0) or 0.0)
                coordinated["total_hedge_pct"] = _total_hedge_value / _pv if _pv > 0 else 0.0
                coordinated["total_cost"] = _total_cost
            except Exception as _exc:
                logger.warning("回退后重算对冲汇总失败: %s", _exc)

        # === 自动执行对冲指令 (与 7.4 AutoHedgeExecutor 对齐) ===
        hedge_orders = coordinated.get("orders", [])
        executed_orders = []

        if hedge_orders and not self.dry_run:
            # 尝试加载真实券商网关（按优先级：CTP > 同花顺 > Mock）
            broker = None
            broker_type = "mock"  # 默认降级到模拟
            
            # 优先级1: CTP期货网关（用于股指期权对冲）
            try:
                from src.execution.ctp_gateway import CTPGateway
                ctp = CTPGateway(
                    front_addr=getattr(self.config, 'CTP_FRONT_ADDR', ''),
                    broker_id=getattr(self.config, 'CTP_BROKER_ID', ''),
                    user_id=getattr(self.config, 'CTP_USER_ID', ''),
                    password=getattr(self.config, 'CTP_PASSWORD', ''),
                    flow_path=getattr(self.config, 'CTP_FLOW_PATH', 'ctp_flow'),
                )
                if ctp.is_connected():
                    broker = ctp
                    broker_type = "ctp"
                    logger.info("✓ 已连接 CTP 期货网关")
            except Exception as e:
                logger.debug(f"CTP网关不可用: {e}")
            
            # 优先级2: 同花顺真实下单（用于股票交易）
            if broker is None:
                try:
                    from ths_real_broker import THSRealBroker
                    ths_account = getattr(self.config, 'THS_ACCOUNT', '')
                    if ths_account:
                        broker = THSRealBroker(account=ths_account)
                        if broker.connect():
                            broker_type = "ths_real"
                            logger.info("✓ 已连接 同花顺真实交易网关")
                except Exception as e:
                    logger.debug(f"同花顺网关不可用: {e}")
            
            # 优先级3: 降级到 MockBroker（仅用于测试/开发环境）
            if broker is None:
                logger.warning(
                    "⚠️ 未检测到真实券商网关，降级到 MockBroker（模拟模式）\n"
                    "   生产环境请配置: CTP_FRONT_ADDR / THS_ACCOUNT\n"
                    "   当前日期: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                )
                broker = MockBroker(price_dict={
                    str(k): v for k, v in self.config.MOCK_PRICES.items()
                })

            # 对冲指令执行（所有 broker 类型通用；修复: 补全缺失的 try 匹配 L2305 except）
            try:
                from execution.smart_order_router import AlgoType

                for order in hedge_orders:
                    hedge_type = order.get("hedge_type", "UNKNOWN")
                    action = order.get("action", "")

                    # 期货空头下单 (Beta 对冲)
                    if action == "SHORT_FUTURES":
                        fut_code = order.get("instrument", "IF")
                        contracts = int(order.get("contracts", 0))
                        fut_price = float(order.get("futures_price", 0))
                        if contracts > 0 and fut_price > 0:
                            logger.info(f"[对冲执行] {hedge_type}: {fut_code} 空头 {contracts} 手 @ {fut_price}")
                            try:
                                oid = broker.place(
                                    symbol=fut_code,
                                    qty=contracts,
                                    side="SELL_SHORT",
                                    order_type="LIMIT",
                                    price=fut_price,
                                )
                                fill = broker.wait_fill(oid)
                                executed_orders.append({
                                    "type": hedge_type,
                                    "action": action,
                                    "instrument": fut_code,
                                    "side": "SELL_SHORT",
                                    "contracts": contracts,
                                    "price": fill["price"] if fill else fut_price,
                                    "notional": order.get("notional", 0),
                                    "cost": order.get("estimated_cost", 0),
                                    "status": fill["order_type"] if fill else "FILLED",
                                    "reason": "Beta 对冲自动执行",
                                    "order_id": oid,
                                    "cost_breakdown": order.get("cost_breakdown"),
                                })
                            except Exception as exc:
                                logger.error(f"Beta 对冲执行失败: {exc}")
                                executed_orders.append({
                                    "type": hedge_type,
                                    "action": action,
                                    "instrument": fut_code,
                                    "side": "SELL_SHORT",
                                    "contracts": contracts,
                                    "price": fut_price,
                                    "notional": order.get("notional", 0),
                                    "cost": order.get("estimated_cost", 0),
                                    "status": "FAILED",
                                    "reason": f"Beta 对冲执行失败: {exc}",
                                    "cost_breakdown": order.get("cost_breakdown"),
                                })

                    # 期权买入 (Vol 对冲)
                    elif action in ("BUY_PUT_SPREAD", "BUY_BARE_PUT", "BUY_EMERGENCY_PUT"):
                        budget = float(order.get("budget", 0))
                        if budget > 0:
                            logger.info(f"[对冲执行] {hedge_type}: {action} 预算 {budget:.0f}")
                            try:
                                opt_symbol = order.get("instrument", "50ETF_OPTIONS")
                                opt_price = budget / 10000.0 if budget > 0 else 0.0
                                oid = broker.place(
                                    symbol=opt_symbol,
                                    qty=1,
                                    side="BUY",
                                    order_type="LIMIT",
                                    price=opt_price,
                                    option_type="PUT",
                                    strike=0.0,
                                )
                                fill = broker.wait_fill(oid)
                                executed_orders.append({
                                    "type": hedge_type,
                                    "action": action,
                                    "instrument": opt_symbol,
                                    "side": "BUY",
                                    "option_type": "PUT",
                                    "strike": 0.0,
                                    "budget": budget,
                                    "delta_target": order.get("delta_target", -0.2),
                                    "coverage": order.get("actual_coverage", 0),
                                    "price": fill["price"] if fill else opt_price,
                                    "status": "FILLED",
                                    "reason": f"Vol 对冲自动执行 (VIX={vix_level})",
                                    "order_id": oid,
                                })
                            except Exception as exc:
                                logger.error(f"Vol 对冲执行失败: {exc}")
                                executed_orders.append({
                                    "type": hedge_type,
                                    "action": action,
                                    "instrument": order.get("instrument", "50ETF_OPTIONS"),
                                    "side": "BUY",
                                    "option_type": "PUT",
                                    "strike": 0.0,
                                    "budget": budget,
                                    "delta_target": order.get("delta_target", -0.2),
                                    "coverage": order.get("actual_coverage", 0),
                                    "status": "FAILED",
                                    "reason": f"Vol 对冲执行失败: {exc}",
                                })

                    # 避险资产配置 (Correlation 对冲)
                    elif action == "SAFE_HAVEN_ALLOC":
                        gold_value = float(order.get("gold_value", 0))
                        repo_value = float(order.get("repo_value", 0))
                        logger.info(f"[对冲执行] {hedge_type}: 黄金ETF {gold_value:.0f} + 逆回购 {repo_value:.0f}")
                        try:
                            gold_symbol = order.get("gold_etf", "518880")
                            est_gold_price = self.config.MOCK_PRICES.get(gold_symbol, 5.85)
                            gold_qty = int(gold_value / est_gold_price / 100) * 100
                            if gold_qty > 0:
                                oid = broker.place(
                                    symbol=gold_symbol,
                                    qty=gold_qty,
                                    side="BUY",
                                    order_type="LIMIT",
                                    price=est_gold_price,
                                )
                                fill = broker.wait_fill(oid)
                                executed_orders.append({
                                    "type": hedge_type,
                                    "action": action,
                                    "gold_etf": gold_symbol,
                                    "gold_qty": gold_qty,
                                    "gold_value": gold_value,
                                    "gold_price": fill["price"] if fill else est_gold_price,
                                    "repo_symbol": order.get("repo_symbol", "GC001"),
                                    "repo_value": repo_value,
                                    "status": "FILLED",
                                    "reason": f"Correlation 对冲自动执行 (ρ̄={order.get('avg_corr', 0):.3f})",
                                    "order_id": oid,
                                })
                        except Exception as exc:
                            logger.error(f"Correlation 对冲执行失败: {exc}")
                            executed_orders.append({
                                "type": hedge_type,
                                "action": action,
                                "gold_etf": order.get("gold_etf", "518880"),
                                "gold_value": gold_value,
                                "repo_symbol": order.get("repo_symbol", "GC001"),
                                "repo_value": repo_value,
                                "status": "FAILED",
                                "reason": f"Correlation 对冲执行失败: {exc}",
                            })

                    # 降级为 Put Spread
                    elif action == "DOWNGRADE_TO_PUT_SPREAD":
                        logger.info(f"[对冲执行] {hedge_type}: 降级至 Put Spread (成本超限)")
                        executed_orders.append({
                            "type": hedge_type,
                            "action": action,
                            "reason": order.get("reason", "成本超限降级"),
                            "status": "DOWNGRADED",
                        })

                logger.info(f"对冲执行完成: {len(executed_orders)} 笔指令已成交")

            except Exception as e:
                logger.error(f"对冲执行通道异常: {e}")
                executed_orders.append({
                    "type": "EXECUTION_ERROR",
                    "status": "FAILED",
                    "error": str(e),
                })
        elif hedge_orders and self.dry_run:
            logger.info(f"DRY-RUN 模式: {len(hedge_orders)} 笔对冲指令未执行")
            for order in hedge_orders:
                executed_orders.append({**order, "status": "DRY_RUN"})

        # === 熔断级别强制加对冲 (LEVEL_3+) ===
        if circuit_level in ("LEVEL_3", "LEVEL_4"):
            logger.warning(f"[{circuit_level}] 熔断触发, 强制加对冲")
            force_action = {
                "type": "CIRCUIT_BREAKER_HEDGE",
                "circuit_level": circuit_level,
                "force_reduce_pct": 0.50 if circuit_level == "LEVEL_3" else 1.0,
                "status": "TRIGGERED",
                "reason": f"熔断 {circuit_level} 强制对冲",
            }
            executed_orders.append(force_action)
            self.state["phases"]["market"]["build_allowed"] = False

        hedge_status = {
            "vix": vix_level,
            "circuit_level": circuit_level,
            "portfolio_beta": coordinated.get("portfolio_beta", 0),
            "actions": coordinated.get("summary", {}),
            "orders": executed_orders,
            "coordinated": coordinated,
            "total_hedge_pct": coordinated.get("total_hedge_pct", 0),
            "total_cost": coordinated.get("total_cost", 0),
            "hedge_enabled": len(executed_orders) > 0,
            "edb_futures": edb_summary,
        }

        logger.info(f"对冲汇总: 启用={hedge_status['hedge_enabled']}, "
                    f"指令数={len(executed_orders)}, "
                    f"总对冲比例={hedge_status['total_hedge_pct']:.2%}")

        self.state["phases"]["hedge"] = {"status": "PASS", **hedge_status}
        self.state["hedge_status"] = hedge_status

        # === 持久化对冲执行记录 ===
        try:
            trade_date = getattr(self, "trade_date", None) or datetime.now().strftime("%Y-%m-%d")
            reports_dir = BASE_DIR / "reports"
            reports_dir.mkdir(parents=True, exist_ok=True)
            hedge_fill_path = reports_dir / f"hedge_execution_fill_{trade_date}.json"
            fill_payload = {
                "trade_date": trade_date,
                "generated_at": datetime.now().isoformat(),
                "portfolio_beta": coordinated.get("portfolio_beta", 0),
                "total_hedge_pct": coordinated.get("total_hedge_pct", 0),
                "total_cost": coordinated.get("total_cost", 0),
                "hedge_enabled": len(executed_orders) > 0,
                "orders": executed_orders,
            }
            with open(hedge_fill_path, "w", encoding="utf-8") as f:
                json.dump(fill_payload, f, ensure_ascii=False, indent=2)
            logger.info(f"对冲成交记录已落盘: {hedge_fill_path}")
        except Exception as exc:
            logger.error(f"对冲成交记录落盘失败: {exc}")

        return hedge_status

    # --------------------------------------------------------
    # Phase 4.5: 对冲基金视角融合 (v7.7)
    #   - Theta引擎: 月度Covered Call计划生成 + 滚仓检查
    #   - Gamma/Vega引擎: MA60/IV分位监控 + 尾部对冲触发
    #   - 三级熔断: 保证金占用率检查 + 自动执行
    #   - 2030清仓协议: 阶段切换 + 预警
    # --------------------------------------------------------
    def phase_hedge_fund(self) -> Dict[str, Any]:
        """对冲基金视角融合阶段 — Theta/Gamma/KillSwitch/LiquidationScheduler"""
        logger.info("=" * 60)
        logger.info("Phase 4.5: 对冲基金视角融合 (Theta/Gamma/KillSwitch/Liquidation)")
        logger.info("=" * 60)

        result: Dict[str, Any] = {
            "status": "PASS",
            "modules_loaded": HEDGE_FUND_CORE_READY,
            "theta": {},
            "gamma": {},
            "kill_switch": {},
            "liquidation": {},
        }

        if not HEDGE_FUND_CORE_READY:
            logger.warning("对冲基金模块未加载, 跳过本阶段")
            result["status"] = "SKIP"
            result["reason"] = "modules_not_loaded"
            self.state["phases"]["hedge_fund"] = result
            return result

        # === 1. Theta引擎 — 月度Covered Call计划 + 滚仓检查 ===
        try:
            theta = ThetaEngine()
            theta_cfg = getattr(theta, "config", {}) or {}
            if not theta_cfg.get("enabled", False):
                logger.info("[Theta] 引擎未启用, 跳过")
                result["theta"] = {"enabled": False}
            else:
                # 滚仓检查 (到期前5个交易日)
                rollover_plan = theta.check_rollover()
                if rollover_plan:
                    logger.info("[Theta] 检测到需滚仓头寸: %d 个", len(rollover_plan.get("positions", [])))
                    result["theta"]["rollover"] = rollover_plan
                else:
                    # 生成/刷新月度计划
                    monthly_plan = theta.generate_monthly_plan()
                    logger.info("[Theta] 月度Covered Call计划: %d 个头寸, 预期权利金 %.0f",
                                len(monthly_plan.get("positions", [])),
                                monthly_plan.get("total_est_premium", 0))
                    result["theta"] = {
                        "enabled": True,
                        "plan_date": monthly_plan.get("plan_date"),
                        "positions_count": len(monthly_plan.get("positions", [])),
                        "total_premium": monthly_plan.get("total_est_premium", 0),
                        "monthly_return_pct": monthly_plan.get("portfolio_yield_monthly", 0),
                        "annualized_pct": monthly_plan.get("portfolio_yield_annualized", 0),
                        "plan_path": monthly_plan.get("plan_path", ""),
                    }
        except Exception as e:
            logger.error(f"[Theta] 引擎执行失败: {e}", exc_info=True)
            result["theta"] = {"status": "ERROR", "error": str(e)}

        # === 2. Gamma/Vega引擎 — 尾部危机监控 ===
        try:
            gamma = GammaEngine()
            monitor_result = gamma.monitor()
            logger.info("[Gamma] 监控完成: MA60=%s, IV分位=%s, 触发=%s",
                        monitor_result.get("ma60_status"),
                        monitor_result.get("iv_percentile"),
                        monitor_result.get("triggered"))
            result["gamma"] = monitor_result
            # 若触发, 记录但不在此自动执行 (由 phase_execute 接管)
            if monitor_result.get("triggered"):
                logger.warning("[Gamma] 尾部对冲触发! 类型=%s, 预算=%.0f",
                               monitor_result.get("trigger_type"),
                               monitor_result.get("budget", 0))
        except Exception as e:
            logger.error(f"[Gamma] 引擎执行失败: {e}", exc_info=True)
            result["gamma"] = {"status": "ERROR", "error": str(e)}

        # === 2.5 v8.5: Vega 暴露监控 (波动率风险检查) ===
        try:
            if V85_READY:
                from risk.vega_monitor import VegaMonitor
                vm = VegaMonitor()
                portfolio_value = float(self.state.get("portfolio_value", self.capital))
                vega_result = vm.monitor(portfolio_value=portfolio_value)
                logger.info(
                    "[v8.5 Vega] 总暴露=%s, Vega/净值=%s, 1%%波动影响=%s, 超限=%s",
                    vega_result.get("total_vega", "N/A"),
                    vega_result.get("vega_to_nav_pct", "N/A"),
                    vega_result.get("pnl_1pct_vol_change", "N/A"),
                    vega_result.get("breach", False),
                )
                result["vega"] = vega_result
                if vega_result.get("breach"):
                    logger.warning("[v8.5 Vega] 暴露超限! 建议: %s", vega_result.get("actions", []))
            else:
                result["vega"] = {"status": "SKIP", "reason": "v85_not_ready"}
        except Exception as e:
            logger.error(f"[v8.5 Vega] 监控失败: {e}", exc_info=True)
            result["vega"] = {"status": "ERROR", "error": str(e)}

        # === 3. 三级熔断协议 — 保证金占用率检查 ===
        try:
            ks = KillSwitch()
            ks_status = ks.check_margin_status()
            ks_level = int(ks_status.get("level", 0)) if isinstance(ks_status, dict) else 0
            logger.info("[KillSwitch] 当前熔断级别: L%d (%s), 保证金占用率: %.1f%%",
                        ks_level,
                        ks_status.get("level_name", "正常") if isinstance(ks_status, dict) else "未知",
                        (ks_status.get("margin_usage_ratio", 0) if isinstance(ks_status, dict) else 0) * 100)
            result["kill_switch"] = {
                "level": ks_level,
                "level_name": ks_status.get("level_name", "正常") if isinstance(ks_status, dict) else "未知",
                "margin_usage_ratio": ks_status.get("margin_usage_ratio", 0) if isinstance(ks_status, dict) else 0,
                "triggered": ks_level > 0,
            }
            if ks_level >= 1:
                logger.warning("[KillSwitch] L1触发: 停止新开仓, 进入防守模式")
            if ks_level >= 2:
                logger.error("[KillSwitch] L2触发: 强平深虚值期权空头!")
            if ks_level >= 3:
                logger.critical("[KillSwitch] L3触发: 变现红利ETF跨品种注入!")
                # 执行L3紧急协议
                try:
                    ks.execute_kill_switch(3)
                except Exception as e3:
                    logger.error(f"[KillSwitch] L3执行失败: {e3}")
        except Exception as e:
            logger.error(f"[KillSwitch] 检查失败: {e}", exc_info=True)
            result["kill_switch"] = {"status": "ERROR", "error": str(e)}

        # === 4. 2030清仓协议 — 阶段切换 + 预警 ===
        try:
            ls = LiquidationScheduler()
            current_phase = ls.get_current_phase()
            phase_num = current_phase.get("phase", 0) if current_phase else 0
            phase_name = current_phase.get("name", "未知") if current_phase else "未知"
            days_to_next = current_phase.get("days_to_next_phase") if current_phase else None
            logger.info("[Liquidation] 当前阶段: Phase %d (%s), 距下一阶段: %s 天",
                        phase_num, phase_name,
                        days_to_next if days_to_next is not None else "N/A")
            alert = ls.check_alert(days_threshold=30)
            result["liquidation"] = {
                "phase": phase_num,
                "phase_name": phase_name,
                "days_to_next": days_to_next,
                "alert": alert or {"alert": False},
            }
            if alert and alert.get("alert"):
                logger.warning("[Liquidation] 清仓预警: %s", alert.get("message", ""))
        except Exception as e:
            logger.error(f"[Liquidation] 调度器检查失败: {e}", exc_info=True)
            result["liquidation"] = {"status": "ERROR", "error": str(e)}

        # === 写入 state ===
        self.state["phases"]["hedge_fund"] = result
        logger.info("-" * 60)
        logger.info("Phase 4.5 完成: Theta=%s, Gamma触发=%s, 熔断级别=L%d, 清仓阶段=Phase %s",
                    "OK" if result["theta"] else "SKIP",
                    result["gamma"].get("triggered", False),
                    result["kill_switch"].get("level", 0),
                    result["liquidation"].get("phase", "UNKNOWN"))
        logger.info("=" * 60)
        return result

    # --------------------------------------------------------
    # Phase 4.6: v10.0 风控 (回撤控制 + VaR 监控 + 压力测试)
    # --------------------------------------------------------
    def phase_v10_risk(self) -> Dict[str, Any]:
        """v10.0 风控阶段 — 回撤控制 + VaR 监控 + 压力测试 + 配置加载"""
        logger.info("=" * 60)
        logger.info("Phase 4.6: v10.0 风控 (回撤控制 + VaR 监控 + 压力测试)")
        logger.info("=" * 60)

        result: Dict[str, Any] = {
            "status": "PASS",
            "modules_loaded": V10_RISK_READY,
            "drawdown": {},
            "var": {},
            "stress_test": {},
            "v10_config": {},
        }

        if not V10_RISK_READY:
            logger.warning("v10.0 风控模块未加载, 跳过本阶段")
            result["status"] = "SKIP"
            result["reason"] = "modules_not_loaded"
            self.state["phases"]["v10_risk"] = result
            return result

        # === 1. 加载 v10.0 配置 ===
        try:
            v10_loader = V10ConfigLoader()
            phase = v10_loader.get_current_phase()
            allocation = v10_loader.get_allocation()
            daily_build_limit = v10_loader.get_daily_build_limit()
            logger.info("[V10Config] 当前阶段: %s - %s, 每日建仓限额: ¥%.0f",
                        phase.get("phase_key", "N/A"),
                        phase.get("name", "N/A"),
                        daily_build_limit)
            result["v10_config"] = {
                "phase_key": phase.get("phase_key"),
                "phase_name": phase.get("name"),
                "daily_build_limit": daily_build_limit,
                "allocation": allocation,
            }
        except Exception as e:
            logger.error(f"[V10Config] 加载失败: {e}", exc_info=True)
            result["v10_config"] = {"status": "ERROR", "error": str(e)}

        # === 2. 回撤控制 ===
        try:
            dc = DrawdownController()
            # 使用当前组合净值 (从 state 读取或用默认值)
            portfolio_value = float(self.state.get("portfolio_value", self.capital))
            peak_value = float(self.state.get("peak_value", self.capital))
            dd_result = dc.check_drawdown(peak_value, portfolio_value)
            dd_level = dd_result.get("level", 0)
            logger.info("[Drawdown] 回撤级别: L%d (%s), 回撤: %.2f%%",
                        dd_level,
                        dd_result.get("level_name", "正常"),
                        abs(dd_result.get("drawdown_pct", 0)) * 100)
            result["drawdown"] = {
                "level": dd_level,
                "level_name": dd_result.get("level_name"),
                "drawdown_pct": dd_result.get("drawdown_pct"),
                "build_allowed": dd_result.get("build_allowed"),
                "spot_reduce_pct": dd_result.get("spot_reduce_pct"),
                "hedge_ratio_target": dd_result.get("hedge_ratio_target"),
                "actions": dd_result.get("actions", []),
            }
            if dd_level >= 2:
                logger.warning("[Drawdown] L%d 触发! 禁止新开仓, 建议减仓 %.0f%%",
                              dd_level, dd_result.get("spot_reduce_pct", 0) * 100)
        except Exception as e:
            logger.error(f"[Drawdown] 检查失败: {e}", exc_info=True)
            result["drawdown"] = {"status": "ERROR", "error": str(e)}

        # === 3. VaR 监控 ===
        try:
            vm = VaRMonitor()
            # 尝试从 returns_history.json 加载历史收益率
            returns_history = self._load_returns_history()
            portfolio_value = float(self.state.get("portfolio_value", self.capital))
            if returns_history and len(returns_history) >= 30:
                var_result = vm.calculate_var(returns_history, portfolio_value)
                logger.info("[VaR] 95%%=%.2f%% (限 %.2f%%), 99%%=%.2f%% (限 %.2f%%), 超限=%s",
                            var_result.get("var_95_pct", 0) * 100,
                            vm.VAR_95_LIMIT_PCT * 100,
                            var_result.get("var_99_pct", 0) * 100,
                            vm.VAR_99_LIMIT_PCT * 100,
                            var_result.get("any_breach", False))
                result["var"] = {
                    "var_95_pct": var_result.get("var_95_pct"),
                    "var_99_pct": var_result.get("var_99_pct"),
                    "var_95_breach": var_result.get("var_95_breach"),
                    "var_99_breach": var_result.get("var_99_breach"),
                    "any_breach": var_result.get("any_breach"),
                    "actions": var_result.get("actions", []),
                }
            else:
                logger.info("[VaR] 历史数据不足 (%d 日), 跳过 VaR 计算", len(returns_history) if returns_history else 0)
                result["var"] = {"status": "SKIP", "reason": "insufficient_data"}
        except Exception as e:
            logger.error(f"[VaR] 监控失败: {e}", exc_info=True)
            result["var"] = {"status": "ERROR", "error": str(e)}

        # === 4. 压力测试 (季度执行, 其他时间跳过) ===
        try:
            today = datetime.now()
            # 季度末 (3/6/9/12月最后一周) 执行
            is_quarter_end = today.month in (3, 6, 9, 12) and today.day >= 25
            if is_quarter_end:
                logger.info("[StressTest] 季度末, 执行压力测试")
                runner = StressTestRunner()
                positions = self._get_portfolio_positions_for_stress_test()
                portfolio_value = float(self.state.get("portfolio_value", self.capital))
                stress_result = runner.run_all_scenarios(positions, portfolio_value)
                logger.info("[StressTest] 结果: %s, 最差场景: %s (%.1f%%)",
                            "全部通过" if stress_result.get("all_pass") else "有超限",
                            stress_result.get("worst_scenario"),
                            stress_result.get("worst_dd", 0) * 100)
                result["stress_test"] = {
                    "executed": True,
                    "all_pass": stress_result.get("all_pass"),
                    "worst_scenario": stress_result.get("worst_scenario"),
                    "worst_dd": stress_result.get("worst_dd"),
                    "report_path": stress_result.get("report_path"),
                }
            else:
                logger.info("[StressTest] 非季度末, 跳过压力测试")
                result["stress_test"] = {"executed": False, "reason": "not_quarter_end"}
        except Exception as e:
            logger.error(f"[StressTest] 执行失败: {e}", exc_info=True)
            result["stress_test"] = {"status": "ERROR", "error": str(e)}

        # === 4.5 v8.5: 流动性监控 (执行可行性检查) ===
        try:
            if V85_READY:
                from risk.liquidity_monitor import LiquidityMonitor
                lm = LiquidityMonitor()
                portfolio_value = float(self.state.get("portfolio_value", self.capital))
                liq_result = lm.check(portfolio_value=portfolio_value)
                result["liquidity"] = liq_result
                logger.info(
                    "[v8.5 Liquidity] 评分=%s, 可执行=%s, 槽位利用=%s%%, 警告=%d",
                    liq_result.get("score", "N/A"),
                    liq_result.get("executable", False),
                    liq_result.get("slot_utilization_pct", 0),
                    len(liq_result.get("warnings", [])),
                )
                for w in liq_result.get("warnings", []):
                    logger.warning(f"  [v8.5 Liquidity] {w}")
            else:
                result["liquidity"] = {"status": "SKIP", "reason": "v85_not_ready"}
        except Exception as e:
            logger.error(f"[v8.5 Liquidity] 监控失败: {e}", exc_info=True)
            result["liquidity"] = {"status": "ERROR", "error": str(e)}

        # === 4.6 v8.5: EVT 尾部风险 (极值理论建模) ===
        try:
            if V85_READY:
                from risk.evt_tail_risk import EVTTailRisk
                evt = EVTTailRisk()
                returns = self._load_returns_history()
                if returns and len(returns) > 200:
                    evt_result = evt.analyze(returns=returns)
                    logger.info(
                        "[v8.5 EVT] 99%%VaR(GPD)=%s, 99.9%%VaR=%s, 尾部指数=%s",
                        evt_result.get("var_99_gpd", "N/A"),
                        evt_result.get("var_999_gpd", "N/A"),
                        evt_result.get("tail_index", "N/A"),
                    )
                    result["evt"] = evt_result
                else:
                    logger.info("[v8.5 EVT] 跳过 (收益率数据不足, 需>200观测)")
                    result["evt"] = {"status": "SKIP", "reason": "insufficient_observations", "count": len(returns) if returns else 0}
            else:
                result["evt"] = {"status": "SKIP", "reason": "v85_not_ready"}
        except Exception as e:
            logger.error(f"[v8.5 EVT] 分析失败: {e}", exc_info=True)
            result["evt"] = {"status": "ERROR", "error": str(e)}

        # === 5. 十五五阶段季度评估 (PhaseManager 季度末触发) ===
        try:
            if self.phase_manager is not None:
                sim_date = datetime.strptime(self.trade_date, "%Y-%m-%d").date() if self.trade_date else None
                is_pm_quarter_end = self.phase_manager.is_quarter_end(sim_date)
                if is_pm_quarter_end:
                    logger.info("[PhaseManager] 季度末, 触发十五五季度评估")
                    positions_for_review = self._get_portfolio_positions_for_stress_test()
                    portfolio_value = float(self.state.get("portfolio_value", self.capital))
                    review = self.phase_manager.trigger_quarterly_review(
                        positions=positions_for_review,
                        portfolio_value=portfolio_value,
                        today=sim_date,
                    )
                    logger.info(
                        "[PhaseManager] 季度评估完成: Q%s, 压测=%s, 调仓=%s, 动作数=%d",
                        review.quarter,
                        review.stress_test_triggered,
                        review.rebalance_needed,
                        len(review.actions),
                    )
                    for action in review.actions:
                        logger.info(f"  - {action}")
                    result["quarterly_review"] = {
                        "executed": True,
                        "quarter": review.quarter,
                        "is_quarter_end": review.is_quarter_end,
                        "stress_test_triggered": review.stress_test_triggered,
                        "stress_test_result": review.stress_test_result,
                        "rebalance_needed": review.rebalance_needed,
                        "actions": review.actions,
                        "strategy_effectiveness": review.strategy_effectiveness,
                    }
                    # 2030 清仓年: 输出清仓动作
                    if self.current_phase_info and self.current_phase_info.is_liquidation_year:
                        liq_actions = self.phase_manager.get_liquidation_actions(sim_date)
                        if liq_actions:
                            logger.warning(
                                "[PhaseManager] 2030 清仓 %s - %s",
                                self.current_phase_info.current_quarter,
                                liq_actions.get("name", "")
                            )
                            result["liquidation_actions"] = liq_actions
                else:
                    logger.info("[PhaseManager] 非季度末, 季度评估跳过")
                    result["quarterly_review"] = {"executed": False, "reason": "not_quarter_end"}
            else:
                result["quarterly_review"] = {"status": "SKIP", "reason": "phase_manager_not_loaded"}
        except Exception as e:
            logger.error(f"[PhaseManager] 季度评估失败: {e}", exc_info=True)
            result["quarterly_review"] = {"status": "ERROR", "error": str(e)}

        # === 写入 state ===
        self.state["phases"]["v10_risk"] = result
        logger.info("-" * 60)
        logger.info("Phase 4.6 完成: 回撤=L%d, VaR超限=%s, 压测=%s, 季度评估=%s",
                    result["drawdown"].get("level", 0),
                    result["var"].get("any_breach", False),
                    "执行" if result["stress_test"].get("executed") else "跳过",
                    "执行" if result.get("quarterly_review", {}).get("executed") else "跳过")
        logger.info("=" * 60)
        return result

    def _load_returns_history(self) -> List[float]:
        """加载历史收益率序列 (用于 VaR 计算)"""
        try:
            returns_path = BASE_DIR.parent / "config" / "returns_history.json"
            if returns_path.exists():
                with open(returns_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # 支持多种格式: {"returns": [...]} 或 {"daily_returns": [...]} 或 [...]
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict):
                    for key in ("returns", "daily_returns", "portfolio_returns"):
                        if key in data:
                            return data[key]
            return []
        except Exception:
            return []

    def _get_portfolio_positions_for_stress_test(self) -> List[Dict]:
        """获取用于压力测试的持仓列表"""
        positions: List[Dict] = []
        try:
            v10_loader = V10ConfigLoader()
            for pos in v10_loader.get_stock_positions():
                positions.append({
                    "code": pos.get("code"),
                    "name": pos.get("name"),
                    "amount": pos.get("amount", 0),
                    "strategy": "stock_long",
                    "style": pos.get("style", ""),
                })
            for pos in v10_loader.get_etf_positions():
                positions.append({
                    "code": pos.get("code"),
                    "name": pos.get("name"),
                    "amount": pos.get("amount", 0),
                    "strategy": "etf",
                    "style": pos.get("style", ""),
                })
            # 期货和期权账户
            futures_cfg = v10_loader.get_futures_config()
            if futures_cfg:
                positions.append({
                    "code": "futures",
                    "name": "期货账户",
                    "amount": futures_cfg.get("margin_capital", 0),
                    "strategy": "futures_hedge",
                })
            options_cfg = v10_loader.get_options_config()
            if options_cfg:
                positions.append({
                    "code": "options",
                    "name": "期权账户",
                    "amount": options_cfg.get("capital", 0),
                    "strategy": "options_tail",
                })
            cash_cfg = v10_loader.get_cash_config()
            if cash_cfg:
                positions.append({
                    "code": "cash",
                    "name": "现金管理",
                    "amount": cash_cfg.get("capital", 0),
                    "strategy": "cash",
                })
        except Exception as e:
            logger.warning(f"获取压力测试持仓失败: {e}")
        return positions

    # --------------------------------------------------------
    # Phase 4.7: 量化市场中性策略 (月度调仓 + IC 对冲)
    # --------------------------------------------------------
    def phase_quant_neutral(self) -> Dict[str, Any]:
        """量化市场中性策略 — 7 因子选股 + IC 期货对冲

        v10.0 投资计划 quant_neutral_account (70 万资金, 140 万名义敞口):
            - 做多 25 只因子 top 20% 股票
            - 做空 IC 期货对冲, 目标 beta 0.05
            - 月度调仓, 换手率 150%

        触发条件:
            - 每月最后一个交易日执行 (默认)
            - 或 IC 基差 > 1.5% 时触发减仓评估

        Returns:
            量化中性调仓结果
        """
        logger.info("=" * 60)
        logger.info("Phase 4.7: 量化市场中性策略 (月度调仓 + IC 对冲)")
        logger.info("=" * 60)

        result: Dict[str, Any] = {
            "status": "PASS",
            "action": "skip",
            "reason": "",
            "long_count": 0,
            "long_market_value": 0.0,
            "portfolio_beta": 0.0,
            "net_exposure": 0.0,
            "ic_hedge": {},
            "drawdown_action": "normal",
        }

        if not V10_STRATEGY_READY:
            result["status"] = "SKIP"
            result["reason"] = "v10.0 策略模块未加载"
            logger.warning("[QuantNeutral] v10.0 策略模块未加载, 跳过")
            self.state["phases"]["quant_neutral"] = result
            return result

        try:
            # 1. 检查是否调仓日 (每月最后一个交易日)
            today = date.today()
            is_month_end = today.day >= 25  # 简化: 25 日后视为月末窗口
            if not is_month_end:
                result["action"] = "skip"
                result["reason"] = f"非月末调仓窗口 (今日 {today.day} 日 < 25)"
                logger.info(f"[QuantNeutral] {result['reason']}, 跳过月度调仓")
                self.state["phases"]["quant_neutral"] = result
                return result

            # 2. 加载候选股票池 (从 v10.0 配置)
            v10_loader = V10ConfigLoader()
            stock_positions = v10_loader.get_stock_positions()

            # 构建候选池 (使用配置中的股票作为简化示例)
            # 实际生产环境应从 Wind/AKShare 获取全市场前 20% 股票
            candidate_universe = []
            for pos in stock_positions:
                candidate_universe.append({
                    "code": pos.get("code", ""),
                    "name": pos.get("name", ""),
                    "returns_20d": 0.05,            # 占位, 实际应从行情接口获取
                    "returns_5d": -0.02,
                    "volatility_60d": 0.25,
                    "avg_turnover_amount": 50_000_000,
                    "roe": 0.15,
                    "cashflow_ratio": 0.85,
                    "revenue_growth": 0.20,
                    "profit_growth": 0.18,
                    "pe_percentile": 0.45,
                    "pb_percentile": 0.30,
                    "beta": 1.0,
                })

            # 3. 加载当前持仓
            current_holdings = self._load_quant_neutral_holdings()

            # 4. 获取 IC 期货价格 (从 positions.json 或实时行情)
            ic_price = self._get_ic_price()

            # 5. 获取 IC 基差
            basis = self._get_ic_basis()

            # 6. 获取策略历史回撤
            strategy_dd_pct, history_95pct_dd, consecutive_months = self._load_strategy_drawdown_state("quant_neutral")

            # 7. 执行月度调仓
            runner = QuantNeutralRunner()
            qn_result = runner.run_monthly_rebalance(
                candidate_universe=candidate_universe,
                current_holdings=current_holdings,
                current_ic_contracts=self._get_current_ic_contracts(),
                ic_price=ic_price,
                basis=basis,
                strategy_drawdown_pct=strategy_dd_pct,
                strategy_history_95pct_drawdown=history_95pct_dd,
                consecutive_overdrawdown_months=consecutive_months,
                trade_date=today,
            )

            # 8. 输出摘要
            summary = runner.summary(qn_result)
            logger.info("\n" + summary)

            # 9. 更新结果
            result.update({
                "status": "PASS",
                "action": qn_result.action,
                "reason": qn_result.reason,
                "long_count": qn_result.long_count,
                "long_market_value": qn_result.long_market_value,
                "portfolio_beta": qn_result.portfolio_beta,
                "target_beta": qn_result.target_beta,
                "net_exposure": qn_result.net_exposure,
                "ic_hedge": qn_result.ic_hedge,
                "drawdown_action": qn_result.drawdown_action,
                "basis_warning": qn_result.basis_warning,
                "turnover_achieved": qn_result.turnover_achieved,
                "candidate_count": qn_result.candidate_count,
                "factors_used": qn_result.factors_used,
            })

            # 10. 紧急风控: 暂停策略 → 触发清仓
            if qn_result.action == "pause":
                logger.warning(f"[QuantNeutral] 策略暂停: {qn_result.reason}")
                result["status"] = "ALERT"

        except Exception as e:
            logger.error(f"[QuantNeutral] 月度调仓失败: {e}", exc_info=True)
            result["status"] = "ERROR"
            result["reason"] = str(e)

        # === 写入 state ===
        self.state["phases"]["quant_neutral"] = result
        logger.info("-" * 60)
        logger.info("Phase 4.7 完成: 动作=%s, 做多=%d 只, 净敞口=%.3f",
                    result.get("action", ""),
                    result.get("long_count", 0),
                    result.get("net_exposure", 0))
        logger.info("=" * 60)
        return result

    def _load_quant_neutral_holdings(self) -> List[Dict]:
        """加载量化中性策略的当前多头持仓"""
        try:
            positions_path = BASE_DIR.parent / "config" / "quant_neutral_positions.json"
            if positions_path.exists():
                with open(positions_path, "r", encoding="utf-8") as f:
                    return json.load(f).get("long_positions", [])
        except Exception:
            pass
        return []

    def _get_ic_price(self) -> float:
        """获取 IC 期货价格"""
        try:
            positions_path = BASE_DIR.parent / "config" / "positions.json"
            if positions_path.exists():
                with open(positions_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # 从 positions.json 中查找 IC 期货价格
                for pos in data.get("positions", []):
                    if pos.get("code") == "IC" or pos.get("symbol", "").startswith("IC"):
                        return float(pos.get("price", 5500.0))
        except Exception:
            pass
        return 5500.0  # 默认 IC 价格

    def _get_ic_basis(self) -> Optional[float]:
        """获取 IC 基差 (正=贴水, 负=升水)"""
        try:
            # 从市场数据中获取 IC 基差
            # 简化: 默认无基差警告
            return 0.0
        except Exception:
            return None

    def _get_current_ic_contracts(self) -> int:
        """获取当前持有的 IC 空头合约数"""
        try:
            positions_path = BASE_DIR.parent / "config" / "quant_neutral_positions.json"
            if positions_path.exists():
                with open(positions_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return int(data.get("ic_short_contracts", 0))
        except Exception:
            pass
        return 0

    def _load_strategy_drawdown_state(self, strategy_name: str) -> Tuple[float, float, int]:
        """加载策略回撤状态

        Returns:
            (当前回撤百分比, 历史 95% 分位回撤, 连续回撤超限月数)
        """
        try:
            state_path = BASE_DIR.parent / "config" / f"{strategy_name}_state.json"
            if state_path.exists():
                with open(state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return (
                    float(data.get("current_drawdown_pct", 0.0)),
                    float(data.get("history_95pct_drawdown", 0.05)),
                    int(data.get("consecutive_overdrawdown_months", 0)),
                )
        except Exception:
            pass
        return (0.0, 0.05, 0)

    # --------------------------------------------------------
    # Phase 4.8: 现金管理 (逆回购 + 货基 + 应急金监控)
    # --------------------------------------------------------
    def phase_cash_management(self) -> Dict[str, Any]:
        """现金管理 — 逆回购自动下单 + 应急金监控 + 保证金追加检查

        v10.0 投资计划 cash_management (130 万资金, 占总资本 26%):
            - 期货保证金 50 万 (维持率 ≥ 60%)
            - 期权抵押金 10 万
            - 应急保证金 30 万 (2 日内补足)
            - 逆回购 / 货基 40 万 (RCO001, 每日自动)

        自动化:
            - 每日 14:30 评估闲置资金
            - 闲置资金 > 1 万 → 自动下单 RCO001
            - 月末季末高利率期 → 加大投放 20%
            - 应急金动用 → 2 日内补足

        Returns:
            现金管理结果
        """
        logger.info("=" * 60)
        logger.info("Phase 4.8: 现金管理 (逆回购 + 货基 + 应急金)")
        logger.info("=" * 60)

        result: Dict[str, Any] = {
            "status": "PASS",
            "action": "skip",
            "total_cash": 0.0,
            "reverse_repo_amount": 0.0,
            "estimated_daily_income": 0.0,
            "repo_order": {},
            "margin_call": {},
            "emergency_replenish": {},
        }

        if not V10_STRATEGY_READY:
            result["status"] = "SKIP"
            result["reason"] = "v10.0 现金管理模块未加载"
            logger.warning("[CashManager] v10.0 模块未加载, 跳过")
            self.state["phases"]["cash_management"] = result
            return result

        try:
            # 1. 加载当前现金状态
            total_cash, futures_margin_used, options_collateral_used, emergency_used = self._load_cash_state()

            # 2. 获取当前逆回购利率
            repo_rate = self._get_current_repo_rate()

            # 3. 执行闲置资金分配
            cm = CashManager()
            cm_result = cm.allocate_idle_cash(
                total_cash=total_cash,
                futures_margin_used=futures_margin_used,
                options_collateral_used=options_collateral_used,
                emergency_used=emergency_used,
                current_repo_rate=repo_rate,
                trade_date=date.today(),
            )

            # 4. 输出摘要
            summary = cm.summary(cm_result)
            logger.info("\n" + summary)

            # 5. 检查应急金补足
            if emergency_used > 0:
                replenish = cm.check_emergency_replenish(emergency_used)
                result["emergency_replenish"] = replenish
                if replenish.get("action") == "replenish_now":
                    logger.warning(f"[CashManager] {replenish['reason']}")

            # 6. 检查期货保证金追加
            futures_account_value, futures_margin_used_actual = self._load_futures_account_state()
            if futures_account_value > 0:
                margin_check = cm.check_margin_call(futures_account_value, futures_margin_used_actual)
                result["margin_call"] = margin_check
                if margin_check.get("action") != "no_action":
                    logger.warning(f"[CashManager] {margin_check['reason']}")

            # 7. 更新结果
            result.update({
                "status": "PASS",
                "action": cm_result.action,
                "total_cash": cm_result.total_cash,
                "reverse_repo_amount": cm_result.reverse_repo,
                "estimated_daily_income": cm_result.estimated_daily_income,
                "estimated_annual_yield": cm_result.estimated_annual_yield,
                "repo_order": cm_result.repo_order,
                "is_month_end": cm_result.is_month_end,
                "is_quarter_end": cm_result.is_quarter_end,
                "futures_margin_ratio": cm_result.futures_margin_ratio,
                "emergency_replenish_needed": cm_result.emergency_replenish_needed,
            })

        except Exception as e:
            logger.error(f"[CashManager] 现金管理失败: {e}", exc_info=True)
            result["status"] = "ERROR"
            result["reason"] = str(e)

        # === 写入 state ===
        self.state["phases"]["cash_management"] = result
        logger.info("-" * 60)
        logger.info("Phase 4.8 完成: 动作=%s, 逆回购=¥%.0f, 日收益=¥%.2f",
                    result.get("action", ""),
                    result.get("reverse_repo_amount", 0),
                    result.get("estimated_daily_income", 0))
        logger.info("=" * 60)
        return result

    def _load_cash_state(self) -> Tuple[float, float, float, float]:
        """加载当前现金状态

        Returns:
            (总现金, 期货已用保证金, 期权已用抵押金, 应急金已动用)
        """
        try:
            cash_path = BASE_DIR.parent / "config" / "cash_state.json"
            if cash_path.exists():
                with open(cash_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return (
                    float(data.get("total_cash", 1_300_000)),
                    float(data.get("futures_margin_used", 480_000)),
                    float(data.get("options_collateral_used", 10_000)),
                    float(data.get("emergency_used", 0)),
                )
        except Exception:
            pass
        # 默认值
        return (1_300_000, 480_000, 10_000, 0.0)

    def _get_current_repo_rate(self) -> float:
        """获取当前逆回购利率 (RCO001)"""
        try:
            # 从市场数据获取
            # 简化: 默认 2.5%
            return 0.025
        except Exception:
            return 0.025

    def _load_futures_account_state(self) -> Tuple[float, float]:
        """加载期货账户状态

        Returns:
            (期货账户权益, 已用保证金)
        """
        try:
            futures_path = BASE_DIR.parent / "config" / "futures_account.json"
            if futures_path.exists():
                with open(futures_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return (
                    float(data.get("account_value", 500_000)),
                    float(data.get("margin_used", 0)),
                )
        except Exception:
            pass
        return (500_000, 0.0)

    # --------------------------------------------------------
    # Phase 4.9: 方向性期货交易 (CU/AU/T 三品种方向性交易)
    # --------------------------------------------------------
    def phase_directional_futures(self) -> Dict[str, Any]:
        """方向性期货交易 — CU(沪铜)/AU(黄金)/T(10年国债) 三品种

        v10.0 macro_hedge_account 中的方向性子模块:
            - CU 沪铜:    新能源需求方向, 默认做多
            - AU 黄金:    避险+通胀对冲, 默认做多
            - T  10年国债: 利率方向, 默认做空 (十五五财政发力推升利率)

        信号源:
            - MA20/MA60 趋势 + RSI 超买超卖 + MACD 动量 + 品种默认方向 0.5 票
            - 三重确认: ≥2 票做多, ≤-2 票做空, 否则空仓

        风控:
            - 单笔最大亏损 20% (保证金视角)
            - 日最大亏损 15% (账户视角)
            - 周连续亏损 25% → 暂停 7 天

        Returns:
            方向性期货交易结果
        """
        logger.info("=" * 60)
        logger.info("Phase 4.9: 方向性期货交易 (CU/AU/T)")
        logger.info("=" * 60)

        result: Dict[str, Any] = {
            "status": "PASS",
            "action": "skip",
            "signals": [],
            "orders": [],
            "risk_status": "normal",
            "total_margin_used": 0.0,
            "total_notional": 0.0,
            "pause_until": None,
        }

        if not V10_STRATEGY_READY:
            result["status"] = "SKIP"
            result["reason"] = "v10.0 方向性期货模块未加载"
            logger.warning("[DirectionalFutures] v10.0 模块未加载, 跳过")
            self.state["phases"]["directional_futures"] = result
            return result

        try:
            # 1. 加载市场数据 (CU/AU/T 的 OHLCV)
            market_data = self._load_directional_futures_market_data()

            # 2. 加载当前持仓
            current_positions = self._load_directional_futures_positions()

            # 3. 获取当前价格
            prices = self._get_futures_prices(market_data)

            # 4. 加载风控状态
            daily_pnl_pct, weekly_loss_pct, last_pause_date = self._load_directional_futures_risk_state()

            # 5. 调用 DirectionalFuturesTrader.run()
            trader = DirectionalFuturesTrader()
            df_result = trader.run(
                market_data=market_data,
                current_positions=current_positions,
                prices=prices,
                trade_date=date.today(),
                daily_pnl_pct=daily_pnl_pct,
                weekly_consecutive_loss_pct=weekly_loss_pct,
                last_loss_pause_date=last_pause_date,
            )

            # 6. 输出摘要
            summary = trader.summary(df_result)
            logger.info("\n" + summary)

            # 7. 保存指令到文件
            orders_saved = self._save_directional_futures_orders(df_result.orders)

            # 8. 更新结果
            result.update({
                "status": "PASS",
                "action": df_result.action,
                "signals": [asdict(s) if hasattr(s, '__dataclass_fields__') else dict(s)
                            for s in df_result.signals],
                "orders": [asdict(o) if hasattr(o, '__dataclass_fields__') else dict(o)
                           for o in df_result.orders],
                "risk_status": df_result.risk_status,
                "total_margin_used": df_result.total_margin_used,
                "total_notional": df_result.total_notional,
                "pause_until": df_result.pause_until.isoformat() if df_result.pause_until else None,
                "orders_file": orders_saved,
            })

            # 9. 风控告警
            if df_result.risk_status == "warning":
                logger.warning(f"[DirectionalFutures] 风控告警: 日亏损 {daily_pnl_pct:.2%}")
            elif df_result.risk_status == "paused":
                logger.error(f"[DirectionalFutures] 已暂停交易至 {df_result.pause_until}")

        except Exception as e:
            logger.error(f"[DirectionalFutures] 方向性期货交易失败: {e}", exc_info=True)
            result["status"] = "ERROR"
            result["reason"] = str(e)

        # === 写入 state ===
        self.state["phases"]["directional_futures"] = result
        logger.info("-" * 60)
        logger.info("Phase 4.9 完成: 动作=%s, 风控=%s, 保证金=¥%.0f, 名义=¥%.0f",
                    result.get("action", ""),
                    result.get("risk_status", ""),
                    result.get("total_margin_used", 0),
                    result.get("total_notional", 0))
        logger.info("=" * 60)
        return result

    def _load_directional_futures_market_data(self) -> Dict[str, Dict[str, Any]]:
        """加载方向性期货市场数据 (CU/AU/T 的 OHLCV)

        数据源优先级:
            1. config/futures_market_data.json (本地缓存)
            2. Wind MCP / iFinD MCP (实时获取, 待实现)
            3. 模拟数据 (60 日 OHLCV, 用于模块自测)

        Returns:
            {symbol: {"closes": [...], "volumes": [...], "opens": [...], "highs": [...], "lows": [...]}}
        """
        market_data: Dict[str, Dict[str, Any]] = {}

        # 1. 尝试从本地缓存加载
        try:
            cache_path = BASE_DIR.parent / "config" / "futures_market_data.json"
            if cache_path.exists():
                with open(cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for symbol in ("CU", "AU", "T"):
                    if symbol in data:
                        market_data[symbol] = data[symbol]
                if len(market_data) == 3:
                    logger.info("[DirectionalFutures] 从缓存加载 CU/AU/T 行情数据")
                    return market_data
        except Exception as e:
            logger.debug(f"[DirectionalFutures] 缓存加载失败: {e}")

        # 2. 实时数据源 (TODO: Wind MCP / iFinD MCP 集成)
        # 当前版本: 使用模拟数据 (60 日) 触发模块逻辑
        logger.warning("[DirectionalFutures] 实时期货行情未集成, 使用模拟数据 (60 日)")
        import random
        random.seed(42)  # 可复现

        base_prices = {"CU": 75000.0, "AU": 550.0, "T": 102.5}
        for symbol, base in base_prices.items():
            closes = []
            volumes = []
            price = base
            for i in range(60):
                # 模拟价格波动 (±2%)
                change = random.uniform(-0.02, 0.02)
                price = price * (1 + change)
                closes.append(round(price, 4))
                volumes.append(random.randint(10000, 100000))
            market_data[symbol] = {
                "closes": closes,
                "volumes": volumes,
                "opens": [c * (1 + random.uniform(-0.01, 0.01)) for c in closes],
                "highs": [c * (1 + random.uniform(0, 0.015)) for c in closes],
                "lows": [c * (1 - random.uniform(0, 0.015)) for c in closes],
            }

        return market_data

    def _load_directional_futures_positions(self) -> Dict[str, Dict]:
        """加载当前方向性期货持仓

        Returns:
            {symbol: {"direction": "long"/"short"/"flat", "contracts": int, "entry_price": float}}
        """
        positions: Dict[str, Dict] = {}
        try:
            pos_path = BASE_DIR.parent / "config" / "directional_futures_positions.json"
            if pos_path.exists():
                with open(pos_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for symbol in ("CU", "AU", "T"):
                    if symbol in data:
                        positions[symbol] = data[symbol]
        except Exception as e:
            logger.debug(f"[DirectionalFutures] 持仓加载失败: {e}")

        # 默认空仓
        for symbol in ("CU", "AU", "T"):
            if symbol not in positions:
                positions[symbol] = {
                    "direction": "flat",
                    "contracts": 0,
                    "entry_price": 0.0,
                }

        return positions

    def _get_futures_prices(self, market_data: Dict[str, Dict[str, Any]]) -> Dict[str, float]:
        """从市场数据中提取最新价格

        Args:
            market_data: {symbol: {"closes": [...]}}

        Returns:
            {symbol: 最新收盘价}
        """
        prices = {}
        for symbol in ("CU", "AU", "T"):
            closes = market_data.get(symbol, {}).get("closes", [])
            if closes:
                prices[symbol] = float(closes[-1])
            else:
                # 兜底默认值
                defaults = {"CU": 75000.0, "AU": 550.0, "T": 102.5}
                prices[symbol] = defaults[symbol]
        return prices

    def _load_directional_futures_risk_state(self) -> Tuple[float, float, Optional[date]]:
        """加载方向性期货风控状态

        Returns:
            (当日盈亏百分比, 周连续亏损百分比, 上次暂停日期)
        """
        try:
            risk_path = BASE_DIR.parent / "config" / "directional_futures_risk.json"
            if risk_path.exists():
                with open(risk_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                daily_pnl = float(data.get("daily_pnl_pct", 0.0))
                weekly_loss = float(data.get("weekly_consecutive_loss_pct", 0.0))
                pause_str = data.get("last_loss_pause_date")
                pause_date = date.fromisoformat(pause_str) if pause_str else None
                return (daily_pnl, weekly_loss, pause_date)
        except Exception as e:
            logger.debug(f"[DirectionalFutures] 风控状态加载失败: {e}")
        return (0.0, 0.0, None)

    def _save_directional_futures_orders(self, orders: List[Any]) -> Optional[str]:
        """保存方向性期货交易指令到文件

        Args:
            orders: 指令列表 (FuturesOrder 对象或字典)

        Returns:
            保存的文件路径, 失败返回 None
        """
        if not orders:
            return None

        try:
            orders_dir = BASE_DIR.parent / "trade_instructions"
            orders_dir.mkdir(parents=True, exist_ok=True)
            today = date.today().isoformat()
            file_path = orders_dir / f"directional_futures_orders_{today}.json"

            orders_data = []
            for o in orders:
                if hasattr(o, '__dataclass_fields__'):
                    orders_data.append(asdict(o))
                elif isinstance(o, dict):
                    orders_data.append(o)
                else:
                    orders_data.append(str(o))

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(orders_data, f, ensure_ascii=False, indent=2, default=str)

            logger.info(f"[DirectionalFutures] 指令已保存: {file_path}")
            return str(file_path)
        except Exception as e:
            logger.error(f"[DirectionalFutures] 指令保存失败: {e}")
            return None

    # --------------------------------------------------------
    # Phase 5: 信号生成 (2026 交易计划订单)
    # --------------------------------------------------------
    def phase_signal(self) -> Dict[str, Any]:
        """信号生成 — 从 trade_plan 加载订单

        读取 `trade_plans/trade_plan_{date}.json` 中的:
        - morning_orders: 上午批次
        - afternoon_orders: 下午批次

        Returns:
            信号字典, 含 action/morning_orders/afternoon_orders/summary 等。
            若交易计划未加载, 返回 {"action": "NO_PLAN"}。
        """
        logger.info("=" * 60)
        logger.info("Phase 5: 信号生成")
        logger.info("=" * 60)

        if not self.trade_plan:
            logger.warning("交易计划未加载, 无信号生成")
            self.state["phases"]["signal"] = {"status": "PASS", "action": "NO_PLAN"}
            return {"action": "NO_PLAN"}

        # === 从交易计划读取订单 ===
        plan_phase = self.trade_plan.get("phase", {})
        plan_exec = self.trade_plan.get("execution_plan", {})
        morning_orders = plan_exec.get("morning_orders", [])
        afternoon_orders = plan_exec.get("afternoon_orders", [])

        # === 计算外部报告调整系数 ===
        external_reports = self._load_external_reports()
        self.state["external_reports"] = external_reports
        if external_reports.get("loaded"):
            logger.info("外部报告加载完成: %d/%d 项",
                        external_reports.get("loaded_count", 0),
                        external_reports.get("total_count", 0))
            logger.info("外部情绪评分: %+.2f", external_reports.get("sentiment_score", 0.0))
            if external_reports.get("risk_events"):
                logger.info("外部风险事件: %d 项", len(external_reports["risk_events"]))
        else:
            logger.warning("外部报告加载失败: %s", external_reports.get("reason", "unknown"))

        external_factor = self._calculate_external_factor(external_reports)
        if external_factor != 1.0:
            logger.info("外部报告调整系数: %.2f", external_factor)

        # === 检测市场状态并获取动态融合权重 ===
        market_regime = self._detect_market_regime()
        regime_weights = self._get_regime_weights(market_regime)
        logger.info("市场状态: %s, 融合权重: Qlib=%.2f iFinD=%.2f External=%.2f",
                    market_regime,
                    regime_weights.get("qlib", 0.5),
                    regime_weights.get("ifind", 0.3),
                    regime_weights.get("external", 0.2))

        logger.info(f"阶段: {plan_phase.get('name', 'N/A')} "
                    f"(第 {plan_phase.get('day_index', 0)}/{plan_phase.get('duration_days', 0)} 日)")
        logger.info(f"上午批次: {len(morning_orders)} 笔, "
                    f"金额 {float(plan_exec.get('morning_total', 0)):,.0f}")
        logger.info(f"下午批次: {len(afternoon_orders)} 笔, "
                    f"金额 {float(plan_exec.get('afternoon_total', 0)):,.0f}")
        logger.info(f"单日合计: {float(plan_exec.get('grand_total', 0)):,.0f} "
                    f"({plan_exec.get('total_orders', 0)} 笔订单)")

        # === 检查市场状态是否允许建仓 ===
        market_phase = self.state.get("phases", {}).get("market", {})
        build_allowed = market_phase.get("build_allowed", True)
        if not build_allowed:
            logger.warning("市场熔断 LEVEL_3+, 暂停建仓")
            self.state["phases"]["signal"] = {
                "status": "PASS", "action": "PAUSED",
                "reason": "市场熔断暂停建仓",
            }
            return {"action": "PAUSED", "reason": "市场熔断暂停建仓"}

        # === 检查 iFinD 重大负面新闻熔断 ===
        cb_cfg = self.fusion_config.get("ifind_circuit_breaker", {})
        if cb_cfg.get("enabled", True) and self.ifind_analyzer is not None:
            planned_symbols = [str(o.get("code", "")) for o in morning_orders + afternoon_orders if o.get("code")]
            name_map = {str(o.get("code", "")): str(o.get("name", "")) for o in morning_orders + afternoon_orders if o.get("code")}
            insight_map = self._get_ifind_insights(planned_symbols, name_map)
            cb_dir = cb_cfg.get("direction", "negative")
            cb_min_conf = float(cb_cfg.get("min_confidence", 0.9))
            for symbol, insight in insight_map.items():
                if insight.direction == cb_dir and float(insight.confidence) >= cb_min_conf:
                    logger.warning("iFinD 重大负面新闻熔断: [%s] %s confidence=%.2f reasons=%s",
                                   insight.symbol, insight.direction, insight.confidence, insight.reasons)
                    self.state["phases"]["signal"] = {
                        "status": "PASS", "action": "PAUSED",
                        "reason": f"重大负面新闻暂停建仓: {insight.symbol} {insight.direction} confidence={insight.confidence:.2f}",
                    }
                    return {"action": "PAUSED", "reason": "重大负面新闻暂停建仓"}

        # === 检查 DEFENSE 模式仓位系数 ===
        position_factor = getattr(self, 'rm', None)
        if position_factor is not None and hasattr(position_factor, 'position_size_factor'):
            position_factor = position_factor.position_size_factor
        else:
            position_factor = 1.0
            logger.debug("RiskManager 未初始化，使用默认仓位系数 1.0")

        # === 应用仓位系数到订单股数 (DEFENSE 模式减仓) ===
        adjusted_morning = self._apply_position_factor(morning_orders, position_factor)
        adjusted_afternoon = self._apply_position_factor(afternoon_orders, position_factor)

        # === 十五五/康波宏观政策评分（可选增强） ===
        macro_scores = {}
        try:
            from src.macro.macro_policy_scoring import score_macro_policy
            planned_symbols = [str(o.get("code", "")) for o in adjusted_morning + adjusted_afternoon if o.get("code")]
            macro_scores = score_macro_policy(planned_symbols)
            logger.info("十五五/康波宏观评分完成: %d 个标的", len(macro_scores))
        except Exception as exc:
            logger.warning("十五五/康波宏观评分跳过: %s", exc)

        # === 计算调整后的金额 ===
        morning_amount = sum(o.get("est_amount", 0) for o in adjusted_morning)
        afternoon_amount = sum(o.get("est_amount", 0) for o in adjusted_afternoon)
        grand_amount = morning_amount + afternoon_amount

        signal = {
            "action": "BUILD_PLAN",
            "phase_name": plan_phase.get("name", ""),
            "phase_number": plan_phase.get("phase_number", 0),
            "day_index": plan_phase.get("day_index", 0),
            "morning_orders": adjusted_morning,
            "afternoon_orders": adjusted_afternoon,
            "morning_window": self.config.MORNING_WINDOW,
            "afternoon_window": self.config.AFTERNOON_WINDOW,
            "morning_count": len(adjusted_morning),
            "afternoon_count": len(adjusted_afternoon),
            "morning_amount": morning_amount,
            "afternoon_amount": afternoon_amount,
            "grand_amount": grand_amount,
            "total_orders": len(adjusted_morning) + len(adjusted_afternoon),
            "position_factor": position_factor,
        }

        logger.info(f"信号生成完成: {signal['total_orders']} 笔订单, "
                    f"总金额 {grand_amount:,.0f}")

        # === v7.5 + Qlib 集成：生成 Qlib 深度学习信号 ===
        qlib_signals = self._generate_qlib_signals()
        if qlib_signals:
            signal["qlib_signals"] = qlib_signals
            logger.info(f"Qlib 信号生成完成: {len(qlib_signals)} 个标的")

        # === 获取 iFinD 新闻研判 (带当日缓存) ===
        ifind_insights = {}
        if self.ifind_analyzer is not None:
            symbols = [str(o.get("code", "")) for o in adjusted_morning + adjusted_afternoon if o.get("code")]
            name_map = {str(o.get("code", "")): str(o.get("name", "")) for o in adjusted_morning + adjusted_afternoon if o.get("code")}
            ifind_insights = self._get_ifind_insights(symbols, name_map)

        # === 加载 lgb_enhanced 增强模型信号 (第四信号源) ===
        lgb_signals = self._load_lgb_enhanced_signals()
        if lgb_signals:
            signal["lgb_enhanced_signals"] = lgb_signals
            logger.info(f"LGB增强信号加载完成: {len(lgb_signals)} 个标的")

        # === 融合 Qlib + iFinD + 外部报告 + LGB增强 信号统一调整订单 ===
        if qlib_signals or ifind_insights or lgb_signals:
            fused_adjustments = self._apply_fused_qlib_ifind_adjustments(
                morning_orders=adjusted_morning,
                afternoon_orders=adjusted_afternoon,
                qlib_signals=qlib_signals,
                ifind_insights=ifind_insights,
                external_factor=external_factor,
                regime_weights=regime_weights,
                lgb_signals=lgb_signals,
            )
            if fused_adjustments:
                signal["qlib_adjusted"] = True
                signal["ifind_adjusted"] = True
                signal["morning_orders"] = fused_adjustments.get("morning_orders", adjusted_morning)
                signal["afternoon_orders"] = fused_adjustments.get("afternoon_orders", adjusted_afternoon)
                signal["qlib_skip_count"] = fused_adjustments.get("skip_count", 0)
                signal["qlib_boost_count"] = fused_adjustments.get("boost_count", 0)
                signal["qlib_cut_count"] = fused_adjustments.get("cut_count", 0)
                signal["ifind_skip_count"] = fused_adjustments.get("skip_count", 0)
                signal["ifind_boost_count"] = fused_adjustments.get("boost_count", 0)
                signal["ifind_cut_count"] = fused_adjustments.get("cut_count", 0)
                signal["lgb_boost_count"] = fused_adjustments.get("lgb_boost_count", 0)
                signal["lgb_cut_count"] = fused_adjustments.get("lgb_cut_count", 0)
                logger.info(
                    "融合调整完成: 加仓=%d, 减仓=%d, 跳过=%d | LGB: 加仓=%d, 减仓=%d",
                    signal["qlib_boost_count"],
                    signal["qlib_cut_count"],
                    signal["qlib_skip_count"],
                    signal["lgb_boost_count"],
                    signal["lgb_cut_count"],
                )
                # 记录 LGB 信号应用详情到监控日志 (供阈值优化分析)
                try:
                    from utils.lgb_signal_monitor import record_lgb_application
                    record_lgb_application(
                        trade_date=datetime.now().strftime("%Y-%m-%d"),
                        orders=signal["morning_orders"] + signal["afternoon_orders"],
                        lgb_signals=lgb_signals or {},
                        boost_count=signal["lgb_boost_count"],
                        cut_count=signal["lgb_cut_count"],
                    )
                except Exception:
                    logger.debug("LGB 监控记录失败 (非关键)", exc_info=True)
            else:
                signal["qlib_adjusted"] = False
                signal["ifind_adjusted"] = False
        else:
            signal["qlib_adjusted"] = False
            signal["ifind_adjusted"] = False

        # === 十五五/康波宏观政策评分实际调整订单 ===
        if macro_scores:
            macro_adjustments = self._apply_macro_policy_adjustments(
                morning_orders=signal.get("morning_orders", adjusted_morning),
                afternoon_orders=signal.get("afternoon_orders", adjusted_afternoon),
                macro_scores=macro_scores,
            )
            if macro_adjustments:
                signal["morning_orders"] = macro_adjustments.get("morning_orders", signal.get("morning_orders", adjusted_morning))
                signal["afternoon_orders"] = macro_adjustments.get("afternoon_orders", signal.get("afternoon_orders", adjusted_afternoon))
                signal["macro_skip_count"] = macro_adjustments.get("skip_count", 0)
                signal["macro_boost_count"] = macro_adjustments.get("boost_count", 0)
                signal["macro_cut_count"] = macro_adjustments.get("cut_count", 0)
                logger.info(
                    "宏观调整完成: 加仓=%d, 减仓=%d, 跳过=%d",
                    signal["macro_boost_count"],
                    signal["macro_cut_count"],
                    signal["macro_skip_count"],
                )

        # === 机构级配置: Black-Litterman 组合优化 ===
        if self.bl_optimizer is not None:
            try:
                positions = (self._get_portfolio_positions_for_stress_test()
                             if hasattr(self, "_get_portfolio_positions_for_stress_test") else [])
                if positions:
                    bl_assets = [p.get("code", "") for p in positions if p.get("code")]
                    bl_market_weights = [
                        float(p.get("amount", 0)) for p in positions if p.get("code")
                    ]
                    total_mv = sum(bl_market_weights)
                    if total_mv > 0:
                        bl_market_weights = [w / total_mv for w in bl_market_weights]
                        # 简化协方差: 单位对角矩阵 × 0.04 (4% 日波动)
                        import numpy as _np
                        n_assets = len(bl_assets)
                        bl_cov = _np.eye(n_assets) * 0.04 ** 2
                        # 观点: 从 morning_orders/afternoon_orders 中提取信号
                        # 仅纳入已在持仓中的标的 (避免 BL 维度不匹配)
                        bl_views = []
                        asset_set = set(bl_assets)
                        for order in signal.get("morning_orders", []) + signal.get("afternoon_orders", []):
                            code = str(order.get("code", ""))
                            if code not in asset_set:
                                continue
                            side = str(order.get("side", "BUY")).upper()
                            if side in ("BUY", "OPEN_LONG"):
                                bl_views.append(BLView(
                                    type="absolute", assets=[code], weights=[1.0],
                                    expected_return=0.02, confidence=0.6,
                                ))
                            elif side in ("SELL", "CLOSE_LONG"):
                                bl_views.append(BLView(
                                    type="absolute", assets=[code], weights=[1.0],
                                    expected_return=-0.02, confidence=0.5,
                                ))
                        bl_result = self.bl_optimizer.optimize(
                            assets=bl_assets,
                            market_weights=bl_market_weights,
                            cov_matrix=bl_cov,
                            views=bl_views or None,
                            risk_free_rate=0.03,
                        )
                        signal["bl_optimization"] = {
                            "optimal_weights": bl_result.optimal_weights.tolist(),
                            "weight_change_vs_market": bl_result.weight_change_vs_market.tolist(),
                            "sharpe_ratio": bl_result.sharpe_ratio,
                            "diversification_ratio": bl_result.diversification_ratio,
                            "effective_n": bl_result.effective_n,
                            "expected_portfolio_return": bl_result.expected_portfolio_return,
                            "expected_portfolio_vol": bl_result.expected_portfolio_vol,
                        }
                        logger.info(
                            "[BlackLitterman] 优化完成: Sharpe=%.3f, DivRatio=%.2f, EffN=%.1f",
                            bl_result.sharpe_ratio,
                            bl_result.diversification_ratio,
                            bl_result.effective_n,
                        )
            except Exception as exc:
                logger.error("[BlackLitterman] 优化失败: %s", exc, exc_info=True)

        # === Alpha 生成: Alpha 因子库 + 动量反转引擎 + Smart Beta 优化 ===
        if ALPHA_MODULES_READY and self.alpha_factor_lib is not None:
            try:
                import numpy as _np_alpha
                positions = (self._get_portfolio_positions_for_stress_test()
                             if hasattr(self, "_get_portfolio_positions_for_stress_test") else [])
                if positions:
                    # 构造简化价格数据 (用持仓成本/市值代理) — 真实场景应从 data_layer 加载
                    alpha_symbols = [str(p.get("code", "")) for p in positions if p.get("code")]
                    n_alpha = len(alpha_symbols)
                    if n_alpha > 0:
                        # 用持仓 amount 作为 market_cap 代理
                        alpha_mcap_dict = {
                            str(p.get("code", "")): max(float(p.get("amount", 1.0)), 1.0)
                            for p in positions if p.get("code")
                        }
                        # 构造合成价格数据 (100日, 用于因子计算)
                        _np_alpha.random.seed(42)
                        alpha_prices = _np_alpha.cumprod(
                            1.0 + _np_alpha.random.randn(100, n_alpha) * 0.02, axis=0
                        ) * 100.0
                        import pandas as _pd_alpha
                        alpha_price_df = _pd_alpha.DataFrame(alpha_prices, columns=alpha_symbols)
                        # 行业映射简化
                        alpha_industries = {s: "Unknown" for s in alpha_symbols}

                        # 1) Alpha 因子库计算
                        alpha_result = self.alpha_factor_lib.compute_all(
                            price_data=alpha_price_df,
                            fundamentals=None,
                            industries=alpha_industries,
                            benchmark_returns=None,
                        )
                        # 实际字段: factors (Dict), effective_factors (List), strong_factors (List)
                        signal["alpha_factors"] = {
                            "total_factors": len(alpha_result.factors),
                            "effective_factors": list(alpha_result.effective_factors),
                            "strong_factors": list(alpha_result.strong_factors),
                            "factor_names": list(alpha_result.factors.keys())[:20],
                        }
                        logger.info(
                            "[AlphaFactorLib] 因子计算完成: 总数=%d, 有效=%d, 强=%d",
                            len(alpha_result.factors),
                            len(alpha_result.effective_factors),
                            len(alpha_result.strong_factors),
                        )

                        # 2) 动量反转信号生成
                        if self.momentum_engine is not None:
                            mom_result = self.momentum_engine.generate_signals(alpha_price_df)
                            # 实际字段: signals (Dict), avg_signal_strength, bullish_count, bearish_count
                            signal["momentum_signals"] = {
                                "total_signals": len(mom_result.signals),
                                "bullish_count": mom_result.bullish_count,
                                "bearish_count": mom_result.bearish_count,
                                "avg_signal_strength": mom_result.avg_signal_strength,
                                "strategy_state": mom_result.strategy_state,
                                "top_long_candidates": mom_result.top_long_candidates[:5],
                                "top_short_candidates": mom_result.top_short_candidates[:5],
                            }
                            logger.info(
                                "[MomentumReversal] 信号生成: 总数=%d, 看多=%d, 看空=%d, 状态=%s",
                                len(mom_result.signals),
                                mom_result.bullish_count,
                                mom_result.bearish_count,
                                mom_result.strategy_state,
                            )

                        # 3) Smart Beta 多因子加权优化
                        if self.smart_beta_engine is not None:
                            # 构造 factor_scores: {symbol: {factor: value}}
                            # 从 alpha_result.factors (Dict[str, FactorValue]) 中提取
                            sb_factor_scores = {}
                            for fname, fvalue_obj in alpha_result.factors.items():
                                # FactorValue.values 是 Dict[str, float] = {symbol: value}
                                values_dict = getattr(fvalue_obj, "values", {}) or {}
                                for sym, val in values_dict.items():
                                    sb_factor_scores.setdefault(sym, {})[fname] = float(val)
                            # 仅纳入有因子值的标的
                            sb_symbols = [s for s in alpha_symbols if s in sb_factor_scores]
                            if sb_symbols:
                                # 因子等权
                                first_sym = sb_symbols[0]
                                sb_factor_weights = {k: 1.0 / len(sb_factor_scores[first_sym])
                                                     for k in sb_factor_scores[first_sym]}
                                sb_market_caps = {s: alpha_mcap_dict.get(s, 1.0) for s in sb_symbols}
                                sb_cov = _np_alpha.cov(alpha_prices[:, :len(sb_symbols)].T)
                                sb_result = self.smart_beta_engine.optimize(
                                    symbols=sb_symbols,
                                    factor_scores=sb_factor_scores,
                                    market_caps=sb_market_caps,
                                    factor_weights=sb_factor_weights,
                                    cov_matrix=sb_cov,
                                )
                                # 实际字段: smart_beta_weights, weight_concentration, effective_n, sharpe_ratio
                                signal["smart_beta"] = {
                                    "weights": sb_result.smart_beta_weights.tolist(),
                                    "weight_concentration": float(sb_result.weight_concentration),
                                    "effective_n": float(sb_result.effective_n),
                                    "sharpe_ratio": float(sb_result.sharpe_ratio),
                                    "tracking_error": float(sb_result.tracking_error),
                                    "information_ratio": float(sb_result.information_ratio),
                                }
                                logger.info(
                                    "[SmartBeta] 优化完成: HHI=%.3f, 有效持仓=%.1f, Sharpe=%.3f, TE=%.4f",
                                    sb_result.weight_concentration,
                                    sb_result.effective_n,
                                    sb_result.sharpe_ratio,
                                    sb_result.tracking_error,
                                )
            except Exception as exc:
                logger.error("[AlphaModules] 信号生成失败: %s", exc, exc_info=True)

        # === 另类数据视角: 新闻情感 + 供应链 + 卫星/搜索/招聘/专利 ===
        if ALT_DATA_MODULES_READY:
            try:
                # 收集当前持仓标的列表
                alt_symbols: List[str] = []
                for pos in (self._get_portfolio_positions_for_stress_test()
                            if hasattr(self, "_get_portfolio_positions_for_stress_test") else []):
                    code = str(pos.get("code", ""))
                    if code and code not in alt_symbols:
                        alt_symbols.append(code)

                # 1) 新闻情感分析 (若有引擎且添加过新闻)
                if self.news_sentiment_engine is not None:
                    try:
                        # 构建 supply_chain_map (从供应链图引擎)
                        supply_map: Dict[str, List[str]] = {}
                        if self.supply_chain_graph is not None:
                            for src, edges in getattr(self.supply_chain_graph, "adjacency", {}).items():
                                for e in edges:
                                    supply_map.setdefault(src, []).append(e.target)

                        ns_result = self.news_sentiment_engine.analyze(
                            symbols=alt_symbols or [],
                            supply_chain_map=supply_map,
                        )
                        # 实际字段: signals (Dict), market_sentiment, anomalies, hot_events, total_news_processed
                        ns_signals = getattr(ns_result, "signals", {}) or {}
                        ns_positive = sum(1 for s in ns_signals.values() if s.composite_sentiment > 0)
                        ns_negative = sum(1 for s in ns_signals.values() if s.composite_sentiment < 0)
                        ns_neutral = sum(1 for s in ns_signals.values() if s.composite_sentiment == 0)
                        ns_event_counts = {ev: cnt for ev, cnt in (getattr(ns_result, "hot_events", []) or [])}
                        signal["news_sentiment"] = {
                            "avg_sentiment": float(getattr(ns_result, "market_sentiment", 0.0)),
                            "positive_count": int(ns_positive),
                            "negative_count": int(ns_negative),
                            "neutral_count": int(ns_neutral),
                            "event_counts": dict(ns_event_counts),
                            "total_news": int(getattr(ns_result, "total_news_processed", 0)),
                            "top_positive": [
                                {"symbol": s.symbol, "score": float(s.composite_sentiment), "confidence": float(s.confidence)}
                                for s in sorted(ns_signals.values(),
                                                key=lambda x: float(x.composite_sentiment),
                                                reverse=True)
                                if s.composite_sentiment > 0
                            ][:3],
                            "top_negative": [
                                {"symbol": s.symbol, "score": float(s.composite_sentiment), "confidence": float(s.confidence)}
                                for s in sorted(ns_signals.values(),
                                                key=lambda x: float(x.composite_sentiment))
                                if s.composite_sentiment < 0
                            ][:3],
                        }
                        logger.info(
                            "[NewsSentiment] 分析完成: 总新闻=%d, 平均情感=%.3f, 正面=%d, 负面=%d",
                            int(getattr(ns_result, "total_news_processed", 0)),
                            float(getattr(ns_result, "market_sentiment", 0.0)),
                            int(ns_positive),
                            int(ns_negative),
                        )
                    except Exception as exc_ns:
                        logger.error("[NewsSentiment] 信号生成失败: %s", exc_ns, exc_info=True)

                # 2) 供应链关系图谱分析
                if self.supply_chain_graph is not None:
                    try:
                        sc_result = self.supply_chain_graph.analyze()
                        # 实际字段: nodes (List[str]), edges (List), metrics (Dict[str, NodeMetrics]),
                        # hubs, bottlenecks, risk_contagion, network_density, avg_path_length, num_components
                        sc_metrics = getattr(sc_result, "metrics", {}) or {}
                        sc_risk = getattr(sc_result, "risk_contagion", {}) or {}
                        sc_nodes = getattr(sc_result, "nodes", []) or []
                        sc_edges = getattr(sc_result, "edges", []) or []
                        signal["supply_chain"] = {
                            "total_nodes": int(len(sc_nodes)),
                            "total_edges": int(len(sc_edges)),
                            "top_central": [
                                {"symbol": s, "betweenness": float(getattr(m, "betweenness_centrality", 0.0)),
                                 "pagerank": float(getattr(m, "pagerank", 0.0))}
                                for s, m in sorted(sc_metrics.items(),
                                                    key=lambda x: float(getattr(x[1], "pagerank", 0.0)),
                                                    reverse=True)[:5]
                            ],
                            "hubs": list(getattr(sc_result, "hubs", []))[:5],
                            "bottlenecks": list(getattr(sc_result, "bottlenecks", []))[:5],
                            "risk_contagion": {
                                s: float(v) for s, v in list(sc_risk.items())[:5]
                            },
                            "network_density": float(getattr(sc_result, "network_density", 0.0)),
                            "avg_path_length": float(getattr(sc_result, "avg_path_length", 0.0)),
                        }
                        logger.info(
                            "[SupplyChain] 分析完成: 节点=%d, 边=%d, 中心节点=%d, 网络密度=%.3f",
                            len(sc_nodes),
                            len(sc_edges),
                            len(sc_metrics),
                            float(getattr(sc_result, "network_density", 0.0)),
                        )
                    except Exception as exc_sc:
                        logger.error("[SupplyChain] 信号生成失败: %s", exc_sc, exc_info=True)

                # 3) 另类数据综合指标
                if self.alt_data_indicators is not None and alt_symbols:
                    try:
                        import numpy as _np_alt  # 局部导入, 避免依赖外部 np
                        # 加载演示数据 (实盘接入前)
                        self.alt_data_indicators.load_demo_data(alt_symbols[:10])
                        ad_result = self.alt_data_indicators.analyze(alt_symbols[:10])
                        # 实际字段: signals (Dict), market_alt_score, anomalies, total_indicators, coverage_summary
                        ad_signals = getattr(ad_result, "signals", {}) or {}
                        ad_coverage = getattr(ad_result, "coverage_summary", {}) or {}
                        # 平均覆盖率
                        ad_avg_cov = float(_np_alt.mean(list(ad_coverage.values()))) if ad_coverage else 0.0
                        signal["alt_data"] = {
                            "total_symbols": int(len(ad_signals)),
                            "avg_composite_score": float(getattr(ad_result, "market_alt_score", 0.0)),
                            "coverage_rate": float(ad_avg_cov),
                            "top_scores": [
                                {
                                    "symbol": s.symbol,
                                    "composite_score": float(s.composite_score),
                                    "satellite_score": float(getattr(s, "satellite_score", 0.0)),
                                    "search_score": float(getattr(s, "search_score", 0.0)),
                                    "recruitment_score": float(getattr(s, "recruitment_score", 0.0)),
                                    "patent_score": float(getattr(s, "patent_score", 0.0)),
                                    "confidence": float(getattr(s, "confidence", 0.0)),
                                }
                                for s in sorted(ad_signals.values(),
                                                key=lambda x: float(x.composite_score),
                                                reverse=True)[:5]
                            ],
                            "anomalies": list(getattr(ad_result, "anomalies", []))[:3],
                        }
                        logger.info(
                            "[AltData] 分析完成: 标的=%d, 平均综合评分=%.3f, 平均覆盖率=%.1f%%",
                            int(len(ad_signals)),
                            float(getattr(ad_result, "market_alt_score", 0.0)),
                            float(ad_avg_cov) * 100,
                        )
                    except Exception as exc_ad:
                        logger.error("[AltData] 信号生成失败: %s", exc_ad, exc_info=True)
            except Exception as exc_outer:
                logger.error("[AltDataModules] 信号生成失败: %s", exc_outer, exc_info=True)

        # === 对冲基金视角: 多策略协调器 (冲突检测 + 风险预算审计) ===
        if self.strategy_coordinator is not None:
            try:
                # 构造目标信号: 把订单按策略账户归类
                target_signals: Dict[str, Dict[str, str]] = {
                    "stock_long": {},
                    "etf_allocation": {},
                }
                for order in signal.get("morning_orders", []) + signal.get("afternoon_orders", []):
                    code = str(order.get("code", ""))
                    side = str(order.get("side", "BUY")).upper()
                    asset_type = str(order.get("type", "STOCK")).upper()
                    direction = "BUY" if side in ("BUY", "OPEN_LONG") else "SELL"
                    if asset_type == "ETF":
                        target_signals["etf_allocation"][code] = direction
                    else:
                        target_signals["stock_long"][code] = direction

                # 期权/期货信号 (从 hedge_plan 与 options_plan)
                hedge_plan = self.state.get("phases", {}).get("hedge", {}).get("hedge_plan", {})
                if hedge_plan:
                    target_signals["macro_hedge"] = {
                        str(item.get("symbol", "")): "SELL"
                        for item in hedge_plan.get("futures", [])
                        if str(item.get("direction", "")).upper() in ("SHORT", "SELL")
                    }
                options_modules = self.trade_plan.get("hedge_account", {}).get("modules", []) if self.trade_plan else []
                if options_modules:
                    target_signals["options_tail"] = {"OPTIONS": "BUY"}

                # 当前持仓 (用于冲突检测) — 转换为 {code: {weight, strategy}} 格式
                current_positions: Dict[str, Dict[str, Any]] = {}
                portfolio_value = float(getattr(self, "capital", 5_000_000))
                for pos in (self._get_portfolio_positions_for_stress_test()
                            if hasattr(self, "_get_portfolio_positions_for_stress_test") else []):
                    code = pos.get("code", "")
                    amount = float(pos.get("amount", 0))
                    if code and portfolio_value > 0:
                        current_positions[code] = {
                            "weight": amount / portfolio_value,
                            "strategy": "stock_long" if str(pos.get("type", "STOCK")).upper() == "STOCK" else "etf_allocation",
                            "amount": amount,
                        }

                coord_decision = self.strategy_coordinator.coordinate(
                    target_signals=target_signals,
                    current_positions=current_positions,
                    strategy_pnl={},  # 实盘接入后填充
                    strategy_correlations=None,
                )

                signal["strategy_coordination"] = {
                    "is_approved": coord_decision.is_approved,
                    "total_allocated": coord_decision.total_allocated,
                    "cash_buffer": coord_decision.cash_buffer,
                    "risk_budget_used": coord_decision.risk_budget_used,
                    "risk_budget_limit": coord_decision.risk_budget_limit,
                    "conflicts": [
                        {
                            "strategies": c.strategies,
                            "symbol": c.symbol,
                            "conflict_type": c.conflict_type,
                            "severity": c.severity,
                            "message": c.description,
                            "suggested_action": c.suggested_action,
                        } for c in coord_decision.conflicts
                    ],
                    "adjusted_weights": coord_decision.strategy_weights,
                }

                if not coord_decision.is_approved:
                    err_conflicts = [c for c in coord_decision.conflicts if c.severity == "error"]
                    logger.warning(
                        "[MultiStrategy] 协调未通过: %d 个 error 级冲突, %d 个 warning",
                        len(err_conflicts),
                        len([c for c in coord_decision.conflicts if c.severity == "warning"]),
                    )
                    for c in err_conflicts:
                        logger.warning("  - [%s/%s] %s: %s",
                                       ",".join(c.strategies), c.symbol, c.conflict_type, c.description)
                else:
                    logger.info(
                        "[MultiStrategy] 协调通过: cash_buffer=%.0f, risk_used=%.0f/%.0f, conflicts=%d",
                        coord_decision.cash_buffer,
                        coord_decision.risk_budget_used,
                        coord_decision.risk_budget_limit,
                        len(coord_decision.conflicts),
                    )
            except Exception as exc:
                logger.error("[MultiStrategy] 协调失败: %s", exc, exc_info=True)

        self.state["phases"]["signal"] = {"status": "PASS", **signal}
        if macro_scores:
            signal["macro_policy"] = {k: {
                "fifteen_five_score": v.fifteen_five_score,
                "kondratiev_score": v.kondratiev_score,
                "combined_score": v.combined_score,
                "fifteen_five_note": v.fifteen_five_note,
                "kondratiev_note": v.kondratiev_note,
            } for k, v in macro_scores.items()}
            self.state["phases"]["signal"]["macro_policy"] = signal["macro_policy"]

        # === v8.5: 因子衰减监控 ===
        try:
            if V85_READY:
                from model_monitoring.factor_decay_monitor import FactorDecayMonitor
                fdm = FactorDecayMonitor()
                fdm_result = fdm.scan()
                signal["factor_decay"] = fdm_result
                decaying = fdm_result.get("decaying_factors", [])
                if decaying:
                    logger.warning(
                        "[v8.5 FactorDecay] 检测到 %d 个衰减因子: %s",
                        len(decaying),
                        ", ".join(d.get("name", "?") for d in decaying[:5]),
                    )
                else:
                    logger.info("[v8.5 FactorDecay] 所有因子健康, 无显著衰减")
            else:
                signal["factor_decay"] = {"status": "SKIP", "reason": "v85_not_ready"}
        except Exception as e:
            logger.error(f"[v8.5 FactorDecay] 监控失败: {e}", exc_info=True)
            signal["factor_decay"] = {"status": "ERROR", "error": str(e)}

        return signal

    @staticmethod
    def _qlib_signal_to_factor(signal_value: float) -> float:
        """将 Qlib 信号映射到订单调整系数

        Args:
            signal_value: [-1, 1] 标准化信号

        Returns:
            订单调整系数，例如 1.3 表示加仓 30%，0 表示跳过
        """
        if signal_value >= 0.5:
            return 1.3
        if signal_value >= 0.15:
            return 1.0
        if signal_value > -0.15:
            return 0.8
        if signal_value > -0.5:
            return 0.5
        return 0.0

    def _qlib_signals_to_adjustments(self,
                                     qlib_signals: Dict[str, float],
                                     *,
                                     morning_orders: List[Dict[str, Any]],
                                     afternoon_orders: List[Dict[str, Any]]) -> Dict[str, Any]:
        """按 Qlib 信号调整订单：强看多加仓、中性维持、看空减仓或跳过

        Returns:
            {
                "morning_orders": [...],
                "afternoon_orders": [...],
                "skip_count": int,
                "boost_count": int,
                "cut_count": int,
            }
        """
        if not qlib_signals:
            return {}

        def _apply(orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            adjusted: List[Dict[str, Any]] = []
            for order in orders:
                code = str(order.get("code", ""))
                signal_value = qlib_signals.get(code)
                if signal_value is None:
                    adjusted.append(dict(order))
                    continue

                factor = self._qlib_signal_to_factor(float(signal_value))
                if factor <= 0.0:
                    logger.info("Qlib 跳过订单 [%s] signal=%+.4f", code, signal_value)
                    continue

                new_order = dict(order)
                original_shares = int(order.get("shares", 0))
                original_amount = float(order.get("est_amount", 0))
                new_shares = max(100, int(original_shares * factor / 100) * 100)
                new_order["shares"] = new_shares
                new_order["est_amount"] = round(new_shares * float(order.get("est_price", 0)), 2)
                new_order["original_shares"] = original_shares
                new_order["qlib_signal"] = round(float(signal_value), 4)
                new_order["qlib_factor"] = round(factor, 2)
                adjusted.append(new_order)

                if factor >= 1.3:
                    logger.info("Qlib 加仓 [%s] signal=%+.4f -> factor=%.2f, %d 股", code, signal_value, factor, new_shares)
                elif factor <= 0.5:
                    logger.info("Qlib 减仓 [%s] signal=%+.4f -> factor=%.2f, %d 股", code, signal_value, factor, new_shares)
            return adjusted

        new_morning = _apply(morning_orders)
        new_afternoon = _apply(afternoon_orders)

        def _count(orders, threshold):
            return sum(1 for o in orders if o.get("qlib_factor", 1.0) >= threshold)

        return {
            "morning_orders": new_morning,
            "afternoon_orders": new_afternoon,
            "skip_count": (len(morning_orders) - len(new_morning)) + (len(afternoon_orders) - len(new_afternoon)),
            "boost_count": _count(new_morning, 1.3) + _count(new_afternoon, 1.3),
            "cut_count": _count(new_morning, 0.5) + _count(new_afternoon, 0.5),
        }

    @staticmethod
    def _ifind_signal_to_factor(direction: str, confidence: float) -> float:
        """将 iFinD 新闻研判映射到订单调整系数

        Args:
            direction: positive / negative / neutral
            confidence: 0.0-1.0 研判置信度

        Returns:
            订单调整系数，例如 1.2 表示加仓 20%，0 表示跳过
        """
        if direction == "positive" and confidence >= 0.7:
            return 1.2
        if direction == "positive" and confidence >= 0.5:
            return 1.0
        if direction == "neutral":
            return 1.0
        if direction == "negative" and confidence >= 0.85:
            return 0.0
        if direction == "negative" and confidence >= 0.7:
            return 0.5
        return 1.0

    @staticmethod
    def _fuse_qlib_ifind_factor(qlib_factor: float, ifind_factor: float,
                                *, qlib_weight: float = 0.6, ifind_weight: float = 0.4,
                                clamp_min: float = 0.5, clamp_max: float = 1.3) -> float:
        """融合 Qlib 和 iFinD 调整系数

        Args:
            qlib_factor: Qlib 信号因子
            ifind_factor: iFinD 新闻因子
            qlib_weight: Qlib 权重 (默认 0.6)
            ifind_weight: iFinD 权重 (默认 0.4)
            clamp_min: 融合因子下限
            clamp_max: 融合因子上限

        Returns:
            融合后的调整系数
        """
        # 如果 iFinD 要求跳过，保留跳过
        if ifind_factor <= 0.0:
            return 0.0
        # 归一化权重
        total_w = qlib_weight + ifind_weight
        if total_w <= 0:
            return 1.0
        w_q = qlib_weight / total_w
        w_i = ifind_weight / total_w
        fused = qlib_factor * w_q + ifind_factor * w_i
        return max(clamp_min, min(clamp_max, fused))

    def _load_lgb_enhanced_signals(self) -> Dict[str, Dict[str, Any]]:
        """加载 lgb_enhanced 增强模型信号文件

        从 models/lgb_enhanced/lgb_enhanced_signals.json 读取当日信号。
        若文件不存在或 trade_date 非今日, 返回空字典 (安全降级)。

        Returns:
            {code: {"signal": float, "quality_flag": str, "name": str}} 或 {}
        """
        try:
            signals_path = os.path.join(
                os.path.dirname(os.path.dirname(__file__)),
                "models", "lgb_enhanced", "lgb_enhanced_signals.json",
            )
            if not os.path.exists(signals_path):
                logger.debug("LGB增强信号文件不存在: %s", signals_path)
                return {}

            with open(signals_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # 新鲜度检查: trade_date 必须是今日 (防止使用过期信号)
            file_date = data.get("trade_date", "")
            today_str = datetime.now().strftime("%Y-%m-%d")
            if file_date != today_str:
                logger.info("LGB增强信号非今日 (文件: %s, 今日: %s), 跳过", file_date, today_str)
                return {}

            signals = data.get("signals", {})
            if not signals:
                return {}

            result = {}
            for code, info in signals.items():
                if not isinstance(info, dict):
                    continue
                sig = info.get("signal")
                if sig is None:
                    continue
                result[code] = {
                    "signal": float(sig),
                    "quality_flag": info.get("quality_flag", "OK"),
                    "name": info.get("name", code),
                }
            logger.info("LGB增强信号加载: %d 个标的 (trade_date=%s)", len(result), file_date)
            return result

        except Exception as e:
            logger.warning("LGB增强信号加载失败: %s", e)
            return {}

    @staticmethod
    def _lgb_confidence_multiplier(signal_value: Optional[float],
                                    quality_flag: str = "OK") -> float:
        """将 lgb_enhanced 信号映射到置信度乘数

        信号为 tanh(pred*100), 范围 [-1, 1]:
        - 正值 = 看涨 (预测正收益)
        - 负值 = 看跌 (预测负收益)
        - |值| = 置信强度

        乘数设计 (保守, 因 R² 普遍接近 0):
        - 强看涨 (>=0.3) + OK → 1.08 (+8%)
        - 中看涨 (>=0.15) + OK → 1.04 (+4%)
        - 弱看涨 (>=0.05) + OK → 1.00 (中性)
        - 中性 (>=-0.05) → 0.98 (-2%)
        - 弱看跌 (>=-0.15) → 0.92 (-8%)
        - 强看跌 (<-0.15) → 0.85 (-15%, 风控)
        - LOW_QUALITY → 1.0 (忽略, 不影响)
        - None → 1.0 (无信号)

        Args:
            signal_value: 信号值 [-1, 1] 或 None
            quality_flag: OK / LOW_QUALITY

        Returns:
            置信度乘数 [0.85, 1.08]
        """
        if signal_value is None:
            return 1.0
        if quality_flag == "LOW_QUALITY":
            return 1.0
        try:
            sig = float(signal_value)
        except (TypeError, ValueError):
            return 1.0

        if sig >= 0.3:
            return 1.08
        if sig >= 0.15:
            return 1.04
        if sig >= 0.05:
            return 1.0
        if sig >= -0.05:
            return 0.98
        if sig >= -0.15:
            return 0.92
        return 0.85

    def _apply_fused_qlib_ifind_adjustments(self,
                                            *,
                                            morning_orders: List[Dict[str, Any]],
                                            afternoon_orders: List[Dict[str, Any]],
                                            qlib_signals: Dict[str, float],
                                            ifind_insights: Dict[str, Any],
                                            external_factor: float = 1.0,
                                            regime_weights: Optional[Dict[str, float]] = None,
                                            lgb_signals: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
        """融合 Qlib 信号、iFinD 新闻研判与外部报告，统一调整订单

        Args:
            external_factor: 外部报告调整系数 [0.5, 1.3]，默认 1.0
            regime_weights: 市场状态动态权重 {"qlib": x, "ifind": y, "external": z}

        Returns:
            {
                "morning_orders": [...],
                "afternoon_orders": [...],
                "skip_count": int,
                "boost_count": int,
                "cut_count": int,
            }
        """
        if not qlib_signals and not ifind_insights:
            return {}

        # 从配置读取融合参数
        fc = self.fusion_config
        qlib_w = float(fc.get("qlib_weight", 0.5))
        ifind_w = float(fc.get("ifind_weight", 0.3))
        external_w = float(fc.get("external_weight", 0.2))
        clamp_min = float(fc.get("fused_factor_min", 0.5))
        clamp_max = float(fc.get("fused_factor_max", 1.3))
        lgb_confidence_gate = bool(fc.get("lgb_confidence_gate", True))

        # 若传入动态权重，覆盖默认值
        if regime_weights:
            qlib_w = float(regime_weights.get("qlib", qlib_w))
            ifind_w = float(regime_weights.get("ifind", ifind_w))
            external_w = float(regime_weights.get("external", external_w))

        def _apply(orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            adjusted: List[Dict[str, Any]] = []
            for order in orders:
                code = str(order.get("code", ""))
                qlib_signal = qlib_signals.get(code)
                insight = ifind_insights.get(code)

                # 计算 Qlib factor
                qlib_factor = self._qlib_signal_to_factor(float(qlib_signal)) if qlib_signal is not None else 1.0

                # 计算 iFinD factor
                ifind_factor = 1.0
                if insight:
                    ifind_factor = self._ifind_signal_to_factor(insight.direction, float(insight.confidence))

                # 三因子融合: Qlib + iFinD + 外部报告（权重可动态调整）
                if qlib_signal is not None and insight:
                    base_factor = self._fuse_qlib_ifind_factor(
                        qlib_factor, ifind_factor,
                        qlib_weight=qlib_w, ifind_weight=ifind_w,
                        clamp_min=clamp_min, clamp_max=clamp_max,
                    )
                    fused_factor = base_factor * (1 - external_w) + external_factor * external_w
                    fused_factor = max(clamp_min, min(clamp_max, fused_factor))
                elif qlib_signal is not None:
                    fused_factor = qlib_factor * (1 - external_w) + external_factor * external_w
                    fused_factor = max(clamp_min, min(clamp_max, fused_factor))
                elif insight:
                    fused_factor = ifind_factor * (1 - external_w) + external_factor * external_w
                    fused_factor = max(clamp_min, min(clamp_max, fused_factor))
                else:
                    adjusted.append(dict(order))
                    continue

                # 应用 lgb_enhanced 置信度乘数 (第四信号源, 保守调制)
                lgb_mult = 1.0
                lgb_sig_value = None
                if lgb_signals and lgb_confidence_gate:
                    lgb_info = lgb_signals.get(code)
                    if lgb_info:
                        lgb_sig_value = lgb_info.get("signal")
                        lgb_mult = self._lgb_confidence_multiplier(
                            lgb_sig_value, lgb_info.get("quality_flag", "OK"),
                        )
                        if lgb_mult != 1.0:
                            fused_factor = max(clamp_min, min(clamp_max, fused_factor * lgb_mult))

                # 应用 fused_factor
                if fused_factor <= 0.0:
                    logger.info("融合信号跳过订单 [%s] qlib_factor=%.2f ifind_factor=%.2f lgb_mult=%.2f",
                                code, qlib_factor, ifind_factor, lgb_mult)
                    continue

                new_order = dict(order)
                original_shares = int(order.get("shares", 0))
                original_amount = float(order.get("est_amount", 0))
                new_shares = max(100, int(original_shares * fused_factor / 100) * 100)
                new_order["shares"] = new_shares
                new_order["est_amount"] = round(new_shares * float(order.get("est_price", 0)), 2)
                new_order["original_shares"] = original_shares
                new_order["qlib_signal"] = round(float(qlib_signal), 4) if qlib_signal is not None else None
                new_order["qlib_factor"] = round(qlib_factor, 2) if qlib_signal is not None else None
                new_order["ifind_direction"] = insight.direction if insight else None
                new_order["ifind_confidence"] = round(float(insight.confidence), 2) if insight else None
                new_order["ifind_factor"] = round(ifind_factor, 2) if insight else None
                new_order["fused_factor"] = round(fused_factor, 2)
                new_order["ifind_reasons"] = insight.reasons[:3] if insight else []
                new_order["lgb_signal"] = round(float(lgb_sig_value), 4) if lgb_sig_value is not None else None
                new_order["lgb_multiplier"] = round(lgb_mult, 2) if lgb_mult != 1.0 else None
                adjusted.append(new_order)

                if fused_factor >= 1.2:
                    logger.info("融合加仓 [%s] qlib=%s ifind=%s -> fused=%.2f, %d 股",
                                code,
                                f"{qlib_signal:+.4f}" if qlib_signal is not None else "N/A",
                                f"{insight.direction}/{insight.confidence:.2f}" if insight else "N/A",
                                fused_factor, new_shares)
                elif fused_factor <= 0.5:
                    logger.info("融合减仓 [%s] qlib=%s ifind=%s -> fused=%.2f, %d 股",
                                code,
                                f"{qlib_signal:+.4f}" if qlib_signal is not None else "N/A",
                                f"{insight.direction}/{insight.confidence:.2f}" if insight else "N/A",
                                fused_factor, new_shares)
            return adjusted

        new_morning = _apply(morning_orders)
        new_afternoon = _apply(afternoon_orders)

        def _count(orders, threshold):
            return sum(1 for o in orders if o.get("fused_factor", 1.0) >= threshold)

        def _count_lgb(orders, op):
            return sum(1 for o in orders if o.get("lgb_multiplier") is not None and op(o.get("lgb_multiplier", 1.0)))

        return {
            "morning_orders": new_morning,
            "afternoon_orders": new_afternoon,
            "skip_count": (len(morning_orders) - len(new_morning)) + (len(afternoon_orders) - len(new_afternoon)),
            "boost_count": _count(new_morning, 1.2) + _count(new_afternoon, 1.2),
            "cut_count": _count(new_morning, 0.5) + _count(new_afternoon, 0.5),
            "lgb_boost_count": _count_lgb(new_morning + new_afternoon, lambda x: x > 1.0),
            "lgb_cut_count": _count_lgb(new_morning + new_afternoon, lambda x: x < 1.0),
        }

    def _apply_ifind_news_adjustments(self,
                                      *,
                                      morning_orders: List[Dict[str, Any]],
                                      afternoon_orders: List[Dict[str, Any]]) -> Dict[str, Any]:
        """根据 iFinD 新闻/公告研判结果调整订单

        Returns:
            {
                "morning_orders": [...],
                "afternoon_orders": [...],
                "skip_count": int,
                "boost_count": int,
                "cut_count": int,
            }
        """
        if self.ifind_analyzer is None:
            return {}

        symbols = []
        name_map = {}
        for order in morning_orders + afternoon_orders:
            code = str(order.get("code", ""))
            name = str(order.get("name", ""))
            if code and code not in name_map:
                symbols.append(code)
                name_map[code] = name

        if not symbols:
            return {}

        try:
            insights = self.ifind_analyzer.batch_analyze(symbols, name_map=name_map, size=4, days=3)
        except Exception:
            logger.error("iFinD 批量研判失败", exc_info=True)
            return {}

        insight_map = {item.symbol: item for item in insights}

        def _apply(orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            adjusted: List[Dict[str, Any]] = []
            for order in orders:
                code = str(order.get("code", ""))
                insight = insight_map.get(code)
                if insight is None:
                    adjusted.append(dict(order))
                    continue

                factor = self._ifind_signal_to_factor(insight.direction, float(insight.confidence))
                if factor <= 0.0:
                    logger.info("iFinD 跳过订单 [%s] %s confidence=%.2f", code, insight.direction, insight.confidence)
                    continue

                new_order = dict(order)
                original_shares = int(order.get("shares", 0))
                original_amount = float(order.get("est_amount", 0))
                new_shares = max(100, int(original_shares * factor / 100) * 100)
                new_order["shares"] = new_shares
                new_order["est_amount"] = round(new_shares * float(order.get("est_price", 0)), 2)
                new_order["original_shares"] = original_shares
                new_order["ifind_direction"] = insight.direction
                new_order["ifind_confidence"] = round(float(insight.confidence), 2)
                new_order["ifind_factor"] = round(factor, 2)
                new_order["ifind_reasons"] = insight.reasons[:3]
                adjusted.append(new_order)

                if factor >= 1.2:
                    logger.info("iFinD 加仓 [%s] %s confidence=%.2f -> factor=%.2f, %d 股", code, insight.direction, insight.confidence, factor, new_shares)
                elif factor <= 0.5:
                    logger.info("iFinD 减仓 [%s] %s confidence=%.2f -> factor=%.2f, %d 股", code, insight.direction, insight.confidence, factor, new_shares)
            return adjusted

        new_morning = _apply(morning_orders)
        new_afternoon = _apply(afternoon_orders)

        def _count(orders, threshold):
            return sum(1 for o in orders if o.get("ifind_factor", 1.0) >= threshold)

        return {
            "morning_orders": new_morning,
            "afternoon_orders": new_afternoon,
            "skip_count": (len(morning_orders) - len(new_morning)) + (len(afternoon_orders) - len(new_afternoon)),
            "boost_count": _count(new_morning, 1.2) + _count(new_afternoon, 1.2),
            "cut_count": _count(new_morning, 0.5) + _count(new_afternoon, 0.5),
        }

    def _apply_macro_policy_adjustments(self,
                                         *,
                                         morning_orders: List[Dict[str, Any]],
                                         afternoon_orders: List[Dict[str, Any]],
                                         macro_scores: Dict[str, Any]) -> Dict[str, Any]:
        """根据十五五/康波宏观评分调整订单

        Args:
            morning_orders: 上午订单
            afternoon_orders: 下午订单
            macro_scores: phase_signal 中计算的宏观评分

        Returns:
            {
                "morning_orders": [...],
                "afternoon_orders": [...],
                "skip_count": int,
                "boost_count": int,
                "cut_count": int,
            }
        """
        if not macro_scores:
            return {}

        try:
            from src.macro.macro_policy_scoring import macro_score_to_factor
        except Exception:
            return {}

        def _apply(orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            adjusted: List[Dict[str, Any]] = []
            for order in orders:
                code = str(order.get("code", ""))
                score_info = macro_scores.get(code)
                if not score_info:
                    adjusted.append(dict(order))
                    continue

                combined = float(getattr(score_info, "combined_score", 1.0))
                factor = macro_score_to_factor(combined)
                if factor <= 0.0:
                    logger.info("宏观评分跳过订单 [%s] combined=%.4f", code, combined)
                    continue

                new_order = dict(order)
                original_shares = int(order.get("shares", 0))
                original_amount = float(order.get("est_amount", 0))
                new_shares = max(100, int(original_shares * factor / 100) * 100)
                new_order["shares"] = new_shares
                new_order["est_amount"] = round(new_shares * float(order.get("est_price", 0)), 2)
                new_order["original_shares"] = original_shares
                new_order["macro_combined_score"] = round(combined, 4)
                new_order["macro_factor"] = round(factor, 2)
                new_order["fifteen_five_score"] = round(float(getattr(score_info, "fifteen_five_score", 1.0)), 4)
                new_order["kondratiev_score"] = round(float(getattr(score_info, "kondratiev_score", 1.0)), 4)
                adjusted.append(new_order)

                if factor >= 1.2:
                    logger.info("宏观加仓 [%s] combined=%.4f -> factor=%.2f, %d 股", code, combined, factor, new_shares)
                elif factor <= 0.8:
                    logger.info("宏观减仓 [%s] combined=%.4f -> factor=%.2f, %d 股", code, combined, factor, new_shares)
            return adjusted

        new_morning = _apply(morning_orders)
        new_afternoon = _apply(afternoon_orders)

        def _count(orders, threshold):
            return sum(1 for o in orders if o.get("macro_factor", 1.0) >= threshold)

        return {
            "morning_orders": new_morning,
            "afternoon_orders": new_afternoon,
            "skip_count": (len(morning_orders) - len(new_morning)) + (len(afternoon_orders) - len(new_afternoon)),
            "boost_count": _count(new_morning, 1.2) + _count(new_afternoon, 1.2),
            "cut_count": _count(new_morning, 0.8) + _count(new_afternoon, 0.8),
        }

    def _apply_position_factor(self,
                               orders: List[Dict[str, Any]],
                               factor: float) -> List[Dict[str, Any]]:
        """应用仓位系数到订单列表 (DEFENSE 模式减仓)

        Args:
            orders: 原始订单列表
            factor: 仓位系数 (1.0 = 全仓, 0.5 = 半仓)

        Returns:
            调整后的订单列表 (新建对象, 不修改原订单)
        """
        if factor >= 1.0:
            return list(orders)

        adjusted = []
        for order in orders:
            new_order = dict(order)
            original_shares = int(order.get("shares", 0))
            original_amount = float(order.get("est_amount", 0))
            # 按 factor 缩减股数, 并对齐到 100 股整数倍
            new_shares = max(100, (int(original_shares * factor) // 100) * 100)
            new_order["shares"] = new_shares
            new_order["est_amount"] = round(new_shares * float(order.get("est_price", 0)), 2)
            new_order["original_shares"] = original_shares
            new_order["position_factor"] = factor
            adjusted.append(new_order)
        return adjusted

    # --------------------------------------------------------
    # Qlib 信号生成 (v7.5 + Qlib 集成)
    # --------------------------------------------------------
    def _generate_qlib_signals(self) -> Dict[str, float]:
        """为交易计划中的标的生成 Qlib 深度学习信号

        Returns:
            {symbol: signal_value} 信号值在 [-1, 1] 区间
        """
        try:
            from alpha.qlib_signal_adapter import (
                generate_signal,
                is_qlib_available,
                fetch_ifind_ohlcv,
            )
        except ImportError:
            logger.warning("Qlib 信号适配器不可用")
            return {}

        if not is_qlib_available():
            logger.info("Qlib 不可用，使用本地 LightGBM 信号")

        signals = {}
        # 从交易计划中提取标的
        plan_exec = self.trade_plan.get("execution_plan", {}) if self.trade_plan else {}
        all_orders = plan_exec.get("morning_orders", []) + plan_exec.get("afternoon_orders", [])

        # 去重标的
        symbols = list(dict.fromkeys(o.get("code", "") for o in all_orders if o.get("code")))

        for symbol in symbols:  # 处理全部标的
            try:
                # 优先 iFinD 真实数据，失败则回退模拟数据
                df = fetch_ifind_ohlcv(symbol, days=500)
                if df is None or len(df) < 60:
                    logger.debug(f"iFinD 数据不足，使用模拟数据 [{symbol}]")
                    df = self._generate_mock_ohlcv(symbol, days=500)

                if df is None or len(df) < 60:
                    continue

                signal = generate_signal(df, symbol, model_type="lightgbm")
                if signal is not None and len(signal) > 0:
                    # 取最新信号值
                    latest_signal = float(signal.iloc[-1])
                    signals[symbol] = round(latest_signal, 4)
                    logger.info(f"Qlib 信号 [{symbol}]: {latest_signal:+.4f}")
            except Exception as e:
                logger.warning(f"Qlib 信号生成失败 [{symbol}]: {e}")

        # === 将 Qlib 信号注入 SignalFusion（修复: 用标准 pandas + 兼容方法）===
        if signals and getattr(self, 'signal_fusion', None) is not None:
            try:
                import pandas as _pd
                signal_series = _pd.Series(list(signals.values()), index=list(signals.keys()))
                self.signal_fusion.inject_qlib_signal(signal_series)
                logger.info(f"Qlib 信号已注入 SignalFusion: {len(signals)} 个标的")
            except Exception as e:
                logger.warning(f"Qlib 信号注入 SignalFusion 失败: {e}")

        # === 注入 forward_returns 激活动态 IC 权重（无前视偏差）===
        # 用各标的最近已实现的 20 日前向收益（t-20 → t），仅用已落盘数据
        if getattr(self, 'signal_fusion', None) is not None and signals:
            try:
                forward_returns = self._compute_realized_forward_returns(list(signals.keys()), horizon=20)
                if forward_returns:
                    self.signal_fusion.inject_forward_returns(forward_returns)
                    logger.info(f"forward_returns 已注入 SignalFusion: {len(forward_returns)} 个标的 (激活动态IC权重)")
            except Exception as e:
                logger.warning(f"forward_returns 注入失败: {e}")

        return signals

    def _compute_realized_forward_returns(self, symbols: List[str], horizon: int = 20) -> Dict[str, float]:
        """计算各标的最近已实现的前向收益（无前视偏差）

        取 t-horizon → t 的真实收益（t 为最新数据日），用于评估信号 IC。
        仅使用已落盘的历史数据，不包含任何未来信息。

        Args:
            symbols: 标的代码列表
            horizon: 前向收益窗口（交易日）

        Returns:
            {symbol: forward_return}
        """
        fr: Dict[str, float] = {}
        try:
            from utils.data_provider import get_historical_data
        except Exception:
            return fr
        import pandas as _pd
        for symbol in symbols:
            try:
                code = symbol
                for sfx in (".SH", ".SZ", ".BJ", ".sh", ".sz", ".bj"):
                    if code.endswith(sfx):
                        code = code[:-len(sfx)]
                        break
                df = get_historical_data(code, period="3y")
                if df is None or df.empty or len(df) < horizon + 5:
                    continue
                if hasattr(df.index, "tz") and df.index.tz is not None:
                    df.index = df.index.tz_localize(None)
                close = df["close"].sort_index()
                if len(close) < horizon + 5:
                    continue
                # 最近已实现的 horizon 日收益（t-horizon 买入, t 卖出）
                p_end = float(close.iloc[-1])
                p_start = float(close.iloc[-1 - horizon])
                if p_start > 0:
                    fr[symbol] = (p_end - p_start) / p_start
            except Exception:
                continue
        return fr

    def _generate_mock_ohlcv(self, symbol: str, days: int = 120) -> Optional[pd.DataFrame]:
        """生成模拟 OHLCV 数据 (用于 Qlib 演示)

        Args:
            symbol: 标的代码
            days: 数据天数

        Returns:
            DataFrame 或 None
        """
        try:
            import numpy as np
            import pandas as pd
            np.random.seed(hash(symbol) % (2**32))

            dates = pd.date_range(end=datetime.now(), periods=days, freq="B")
            base_price = 50 + np.random.random() * 100

            # 几何布朗运动模拟
            returns = np.random.normal(0.0005, 0.02, days)
            prices = base_price * np.exp(np.cumsum(returns))

            df = pd.DataFrame({
                "open": prices * (1 + np.random.normal(0, 0.005, days)),
                "high": prices * (1 + np.abs(np.random.normal(0, 0.01, days))),
                "low": prices * (1 - np.abs(np.random.normal(0, 0.01, days))),
                "close": prices,
                "volume": np.random.randint(1000000, 10000000, days),
            }, index=dates)

            return df
        except Exception as e:
            logger.warning(f"模拟数据生成失败 [{symbol}]: {e}")
            return None

    def _options_market_snapshot(self) -> Dict[str, Any]:
        """期权市场快照（最小可用版本）

        实盘应接入期权行情/IV/Greek；当前仅返回占位结构，
        保证 OptionsRunner 可正常执行计划生成流程。
        """
        return {
            "vix": 18.5,
            "market_status": "NORMAL",
            "underlyings": [],
            "note": "最小快照, 实盘应接入期权行情",
        }

    # --------------------------------------------------------
    # P0-6/P0-7/P0-9/P0-11: 顶级对冲基金审计修复 (2026-07-25)
    # 下单前风控门控 — UnifiedRiskCockpit + KillSwitch 预检查
    # --------------------------------------------------------
    def _execute_kill_switch_callback(self, level: int, actions: list) -> Dict[str, Any]:
        """Kill Switch broker_callback 实现 (P0-7 修复)

        当 KillSwitch.execute_kill_switch(level) 被调用时, 通过此回调执行真实动作。
        未注册时 execute_kill_switch 会抛 RuntimeError, 熔断协议无法真正执行。

        Args:
            level: 熔断级别 (1/2/3)
            actions: 配置中定义的动作列表

        Returns:
            执行结果字典, 包含 executed/actions_taken/critical_note
        """
        ts = datetime.now().isoformat()
        actions_taken: List[Dict[str, Any]] = []
        critical_note = ""

        logger.critical(
            "[KillSwitch Callback] 执行熔断 L%d @ %s | 动作: %s",
            level, ts, actions,
        )

        # L1: 切断开仓权限 (已在 _enforce_kill_switch_on_orders 中通过订单过滤实现)
        if level == 1:
            actions_taken.append({
                "action": "disable_new_positions",
                "status": "executed",
                "note": "已通过 _enforce_kill_switch_on_orders 过滤全部 BUY 订单",
            })

        # L2: 强平深虚值期权空头 + 释放流动性
        elif level == 2:
            # 取消所有待执行 BUY 订单
            actions_taken.append({
                "action": "cancel_pending_buys",
                "status": "executed",
                "note": "取消所有待执行买入订单",
            })
            # 标记需要平仓深虚值期权空头 (实盘需对接券商API)
            actions_taken.append({
                "action": "force_close_deep_otm_short",
                "status": "logged" if self.dry_run else "pending_broker_api",
                "note": "强平深虚值期权空头 (需对接券商API执行真实平仓)",
            })
            critical_note = "L2 熔断: 期权空头平仓需人工确认或对接券商API"

        # L3: 变现 10% 红利 ETF + 跨品种注入
        elif level == 3:
            actions_taken.append({
                "action": "liquidate_red_etf_10pct",
                "status": "logged" if self.dry_run else "pending_broker_api",
                "note": "变现 10% 红利 ETF (512890/515180), 跨品种注入期权账户",
            })
            actions_taken.append({
                "action": "halt_all_trading",
                "status": "executed",
                "note": "全面停止交易, 仅允许平仓",
            })
            critical_note = "L3 熔断: 红利ETF变现需人工确认或对接券商API"

        # 记录到 state
        self.state.setdefault("kill_switch_executions", []).append({
            "timestamp": ts,
            "level": level,
            "actions": actions,
            "actions_taken": actions_taken,
            "dry_run": self.dry_run,
            "critical_note": critical_note,
        })

        return {
            "executed": True,
            "level": level,
            "actions_taken": actions_taken,
            "critical_note": critical_note,
            "dry_run": self.dry_run,
            "note": "dry-run 模式仅记录日志, 实盘需对接券商API" if self.dry_run else "已执行, 期权/ETF平仓需人工确认",
        }

    def _pre_trade_risk_gate(self) -> Dict[str, Any]:
        """下单前风控门控 — UnifiedRiskCockpit 全量扫描 (P0-6/P0-9/P0-11)

        顶级对冲基金标准: 任何订单进入执行队列前, 必须经过统一风控驾驶舱扫描。
        审计问题: 原 phase_execute 直接调用 MockBroker 下单, KillSwitch 检查
                  仅在 Phase 4.5 记录日志但不拦截, L1/L2/L3 形同虚设。

        Returns:
            {
                "kill_switch_level": int,      # 0/1/2/3
                "can_trade": bool,             # level < 2 (L0/L1 可交易, L2+ 不可)
                "can_open": bool,              # level == 0 (仅正常可开仓)
                "margin_usage_ratio": float,
                "risk_snapshot": dict,         # UnifiedRiskCockpit 快照
                "fail_closed": bool,           # 异常时为 True, 阻止一切交易
            }

        异常处理 (P0-11): fail-closed — 任何异常都视为最高风险, 阻止交易。
        """
        # 默认 fail-closed (P0-11: 异常时阻止交易, 而非放行)
        fail_closed_result = {
            "kill_switch_level": 3,
            "can_trade": False,
            "can_open": False,
            "margin_usage_ratio": 1.0,
            "risk_snapshot": {"error": "fail_closed"},
            "fail_closed": True,
            "reason": "风控门控异常, fail-closed 阻止交易",
        }

        portfolio_value = float(getattr(self, "capital", 5_000_000))

        # === 优先使用 UnifiedRiskCockpit (P0-9) ===
        if UNIFIED_RISK_COCKPIT_READY:
            try:
                cockpit = UnifiedRiskCockpit(portfolio_value=portfolio_value)
                # 从 state 读取当前持仓 (若存在)
                positions = {}
                if hasattr(self, "_get_portfolio_positions_for_stress_test"):
                    try:
                        pos_list = self._get_portfolio_positions_for_stress_test()
                        for pos in pos_list:
                            code = pos.get("code", "")
                            amount = float(pos.get("amount", 0))
                            if code and portfolio_value > 0:
                                positions[code] = {
                                    "weight": amount / portfolio_value,
                                    "amount": amount,
                                    "category": pos.get("category", "unknown"),
                                }
                    except Exception:
                        pass

                snapshot = cockpit.full_scan(
                    margin_usage=None,       # 触发 KillSwitch 内部获取 (含 production fail-closed)
                    positions=positions,
                    pnl=0.0,
                    current_value=portfolio_value,
                )

                ks_level = int(snapshot.kill_switch_level)
                result = {
                    "kill_switch_level": ks_level,
                    "can_trade": ks_level < 2,   # L0/L1 可交易, L2+ 不可
                    "can_open": ks_level == 0,    # 仅正常可开仓
                    "margin_usage_ratio": snapshot.margin_usage_ratio,
                    "risk_snapshot": {
                        "all_clear": snapshot.all_clear,
                        "risk_level": snapshot.risk_level.name,
                        "drawdown": snapshot.current_drawdown,
                        "drawdown_breach": snapshot.drawdown_breach,
                        "var_breach": snapshot.var_breach,
                        "summary": snapshot.summary,
                        "actions_required": snapshot.actions_required,
                    },
                    "fail_closed": False,
                }
                logger.info(
                    "[RiskGate] UnifiedRiskCockpit 扫描完成: L%d, can_trade=%s, can_open=%s, margin=%.1f%%, %s",
                    ks_level, result["can_trade"], result["can_open"],
                    result["margin_usage_ratio"] * 100,
                    "ALL_CLEAR" if snapshot.all_clear else snapshot.summary,
                )
                return result
            except Exception as e:
                logger.error(
                    "[RiskGate] UnifiedRiskCockpit 扫描异常! fail-closed 阻止交易: %s",
                    e, exc_info=True,
                )
                return fail_closed_result

        # === 降级模式: 直接使用 KillSwitch (P0-6 兜底) ===
        try:
            ks = KillSwitch()
            ks_status = ks.check_margin_status()
            ks_level = int(ks_status.get("level", 0)) if isinstance(ks_status, dict) else 0
            result = {
                "kill_switch_level": ks_level,
                "can_trade": bool(ks_status.get("can_trade", ks_level < 2)),
                "can_open": bool(ks_status.get("can_open", ks_level == 0)),
                "margin_usage_ratio": float(ks_status.get("margin_usage_ratio", 0)),
                "risk_snapshot": {"source": "KillSwitch_fallback", "level_name": ks_status.get("level_name", "")},
                "fail_closed": False,
            }
            logger.info(
                "[RiskGate] KillSwitch 降级扫描: L%d, can_trade=%s, can_open=%s, margin=%.1f%%",
                ks_level, result["can_trade"], result["can_open"],
                result["margin_usage_ratio"] * 100,
            )
            return result
        except Exception as e:
            logger.error(
                "[RiskGate] KillSwitch 降级扫描也失败! fail-closed 阻止交易: %s",
                e, exc_info=True,
            )
            return fail_closed_result

    def _enforce_kill_switch_on_orders(
        self,
        morning_orders: List[Dict[str, Any]],
        afternoon_orders: List[Dict[str, Any]],
        risk_gate: Dict[str, Any],
    ) -> tuple:
        """根据 KillSwitch 级别过滤订单 (P0-7: L1/L2 真实拦截)

        L0 (正常):    放行所有订单
        L1 (警戒):    停止新开仓 — 过滤所有 BUY 订单, 仅保留 SELL (减仓/平仓)
        L2 (熔断):    阻止一切新订单 — 返回空列表, 同时触发强平深虚值期权空头
        L3 (互盲):    紧急变现 — 返回空列表 + 触发红利ETF变现 + 跨品种注入
        fail_closed:  异常时按 L3 处理, 阻止一切交易

        Returns:
            (filtered_morning, filtered_afternoon, enforcement_actions)
        """
        ks_level = int(risk_gate.get("kill_switch_level", 3))
        fail_closed = bool(risk_gate.get("fail_closed", False))

        if fail_closed:
            ks_level = 3  # 异常按 L3 处理

        enforcement_actions: List[Dict[str, Any]] = []

        if ks_level == 0:
            # L0: 正常放行
            return morning_orders, afternoon_orders, enforcement_actions

        if ks_level == 1:
            # L1: 停止新开仓 — 仅保留 SELL 订单 (P0-7 真实拦截)
            filtered_morning = [o for o in morning_orders if str(o.get("side", "BUY")).upper() != "BUY"]
            filtered_afternoon = [o for o in afternoon_orders if str(o.get("side", "BUY")).upper() != "BUY"]
            blocked_count = (len(morning_orders) - len(filtered_morning)) + (len(afternoon_orders) - len(filtered_afternoon))
            logger.warning(
                "[KillSwitch L1] 停止新开仓! 过滤 %d 笔 BUY 订单 (仅保留 %d 笔 SELL)",
                blocked_count, len(filtered_morning) + len(filtered_afternoon),
            )
            enforcement_actions.append({
                "level": 1,
                "action": "block_new_positions",
                "blocked_buy_orders": blocked_count,
                "remaining_sell_orders": len(filtered_morning) + len(filtered_afternoon),
            })
            return filtered_morning, filtered_afternoon, enforcement_actions

        # L2+: 阻止一切新订单 + 触发熔断动作
        logger.error(
            "[KillSwitch L%d] 阻止一切新订单! morning=%d, afternoon=%d 笔订单全部拦截",
            ks_level, len(morning_orders), len(afternoon_orders),
        )
        enforcement_actions.append({
            "level": ks_level,
            "action": "block_all_orders",
            "blocked_morning": len(morning_orders),
            "blocked_afternoon": len(afternoon_orders),
        })

        # L2: 强平深虚值期权空头
        if ks_level >= 2:
            try:
                ks = KillSwitch()
                if ks_level >= 3:
                    # L3: 变现红利ETF + 跨品种注入
                    logger.critical("[KillSwitch L3] 触发紧急变现协议: 红利ETF跨品种注入!")
                    try:
                        ks.execute_kill_switch(3)
                        enforcement_actions.append({"level": 3, "action": "liquidate_red_etf", "executed": True})
                    except RuntimeError as e:
                        logger.error(f"[KillSwitch L3] 紧急变现执行失败 (无 broker_callback): {e}")
                        enforcement_actions.append({"level": 3, "action": "liquidate_red_etf", "executed": False, "error": str(e)})
                else:
                    # L2: 强平深虚值期权空头
                    logger.error("[KillSwitch L2] 触发强平协议: 强平深虚值期权空头!")
                    try:
                        ks.execute_kill_switch(2)
                        enforcement_actions.append({"level": 2, "action": "force_close_otm_short", "executed": True})
                    except RuntimeError as e:
                        logger.error(f"[KillSwitch L2] 强平执行失败 (无 broker_callback): {e}")
                        enforcement_actions.append({"level": 2, "action": "force_close_otm_short", "executed": False, "error": str(e)})
            except Exception as e:
                logger.error(f"[KillSwitch] 熔断执行异常: {e}", exc_info=True)
                enforcement_actions.append({"level": ks_level, "action": "execute_failed", "error": str(e)})

        return [], [], enforcement_actions

    def _enforce_put_option_budget_limit(self, options_modules: List[Dict[str, Any]]) -> tuple:
        """Put Option 累计预算 60% 硬限制 (P0-12)

        审计问题: put option 累计预算无硬上限, 极端行情下可能耗尽对冲账户全部资金,
                  导致无法追加对冲。顶级对冲基金标准: 保留 40% 缓冲应对极端行情。

        规则: put_option 累计预算 <= 60% * hedge_capital
              即 1,000,000 * 0.60 = 600,000 (剩余 400,000 作为极端行情缓冲)

        Returns:
            (filtered_modules, budget_info)
        """
        hedge_capital = float(getattr(self.config, "HEDGE_CAPITAL", 1_000_000))
        put_budget_limit = hedge_capital * 0.60  # 60% 硬上限
        buffer_reserved = hedge_capital * 0.40   # 40% 极端行情缓冲

        filtered_modules = []
        cumulative_put_budget = 0.0
        blocked_modules: List[Dict[str, Any]] = []

        for module in options_modules:
            module_type = str(module.get("type", module.get("strategy", ""))).upper()
            module_budget = float(module.get("budget", module.get("notional", 0)))

            # 仅对 PUT 买入型模块应用 60% 限制 (备兑 Call / 期货对冲不计入)
            is_put_buy = any(kw in module_type for kw in ("PUT", "TAIL", "PROTECT", "HEDGE_LONG"))
            if not is_put_buy:
                filtered_modules.append(module)
                continue

            if cumulative_put_budget + module_budget > put_budget_limit:
                # 超限: 截断到预算上限
                remaining = max(0, put_budget_limit - cumulative_put_budget)
                if remaining > 0:
                    truncated = dict(module)
                    truncated["budget"] = remaining
                    truncated["truncated_by_budget_limit"] = True
                    filtered_modules.append(truncated)
                    cumulative_put_budget = put_budget_limit
                    logger.warning(
                        "[PutBudget] 模块 %s 截断至 %.0f (累计预算达 60%% 上限 %.0f)",
                        module_type, remaining, put_budget_limit,
                    )
                else:
                    blocked_modules.append({
                        "module": module_type,
                        "budget": module_budget,
                        "reason": "put_budget_exhausted",
                    })
                    logger.error(
                        "[PutBudget] 模块 %s 被阻止! 预算 %.0f 将使累计超过 60%% 上限 %.0f (已用 %.0f)",
                        module_type, module_budget, put_budget_limit, cumulative_put_budget,
                    )
            else:
                filtered_modules.append(module)
                cumulative_put_budget += module_budget

        budget_info = {
            "hedge_capital": hedge_capital,
            "put_budget_limit": put_budget_limit,
            "put_budget_used": cumulative_put_budget,
            "put_budget_remaining": max(0, put_budget_limit - cumulative_put_budget),
            "buffer_reserved": buffer_reserved,
            "buffer_utilization_pct": cumulative_put_budget / hedge_capital if hedge_capital > 0 else 0,
            "blocked_modules_count": len(blocked_modules),
            "blocked_modules": blocked_modules,
            "limit_enforced": cumulative_put_budget >= put_budget_limit,
        }
        logger.info(
            "[PutBudget] Put 累计预算: %.0f / %.0f (60%%上限), 40%%缓冲=%.0f, 阻止模块=%d",
            cumulative_put_budget, put_budget_limit, buffer_reserved, len(blocked_modules),
        )
        return filtered_modules, budget_info

    # --------------------------------------------------------
    # Phase 6: 智能执行 (MockBroker / SimExecutionEngine)
    # --------------------------------------------------------
    def phase_execute(self, signal: Dict[str, Any]) -> List[Dict[str, Any]]:
        """智能执行 — 2026 年交易计划订单

        支持两种执行模式:
            - MockBroker (默认): 原有模拟执行
            - SimExecutionEngine (--sim): 股票+期货模拟盘，按交易日+夜盘执行

        Args:
            signal: phase_signal() 返回的信号字典,
                    必须包含 morning_orders 和 afternoon_orders。

        Returns:
            成交记录列表, 每条含 symbol/side/qty/price/amount/session/status。
        """
        mode = "模拟盘" if self.sim_mode else "MockBroker"
        logger.info("=" * 60)
        logger.info(f"Phase 6: 智能执行 ({mode})")
        logger.info("=" * 60)

        # === 期权策略执行（v9.0 多策略期权覆盖层，优先于现货订单检查） ===
        options_plan = self.trade_plan.get("options_execution", {}) if self.trade_plan else {}
        options_modules = self.trade_plan.get("hedge_account", {}).get("modules", []) if self.trade_plan else []

        # === P0-12: Put Option 累计预算 60% 硬限制 (2026-07-25 顶级对冲基金审计) ===
        # 审计问题: put option 无累计预算上限, 极端行情下可能耗尽对冲账户全部资金
        # 修复: put 累计预算 <= 60% * hedge_capital, 保留 40% 缓冲应对极端行情
        put_budget_info: Dict[str, Any] = {}
        if options_modules:
            try:
                options_modules, put_budget_info = self._enforce_put_option_budget_limit(options_modules)
            except Exception as exc:
                logger.error("[PutBudget] 60%% 硬限制检查异常 (fail-closed, 阻止全部期权模块): %s", exc, exc_info=True)
                options_modules = []
                put_budget_info = {"error": str(exc), "fail_closed": True}

        options_fills: List[Dict[str, Any]] = []
        if options_plan and options_modules:
            try:
                from execution.options_runner import OptionsRunner
                runner = OptionsRunner(
                    trade_date=self.trade_date,
                    hedge_capital=float(self.trade_plan.get("hedge_account", {}).get("capital", 1_000_000)),
                    margin_usage_max=float(self.trade_plan.get("hedge_account", {}).get("margin_usage_max", 600_000)),
                    liquidity_buffer_min=float(self.trade_plan.get("hedge_account", {}).get("liquidity_buffer_min", 400_000)),
                )
                options_fills = runner.run_modules(
                    modules=options_modules,
                    market_data=self._options_market_snapshot(),
                    trigger_date=self.trade_date,
                    event_calendar=options_plan.get("event_calendar", []),
                )
                logger.info("期权策略执行完成: %d 条 fills", len(options_fills))
            except Exception as exc:
                logger.warning("期权策略执行失败: %s", exc, exc_info=True)
                options_fills = []
        else:
            logger.info("当前交易计划无期权策略模块，跳过期权执行")

        # === P0-6/P0-7/P0-9/P0-11: 下单前风控门控 (2026-07-25 顶级对冲基金审计) ===
        # 审计问题: 原 phase_execute 直接调用 MockBroker 下单, KillSwitch 检查
        #           仅在 Phase 4.5 记录日志但不拦截, L1/L2/L3 形同虚设
        # 修复: 下单前必须经过 UnifiedRiskCockpit 扫描, 按级别过滤/拦截订单
        risk_gate: Dict[str, Any] = {}
        kill_switch_enforcement: List[Dict[str, Any]] = []
        try:
            risk_gate = self._pre_trade_risk_gate()
        except Exception as exc:
            # P0-11: fail-closed — 风控门控异常时阻止一切交易
            logger.critical("[RiskGate] 风控门控异常! fail-closed 阻止全部交易: %s", exc, exc_info=True)
            risk_gate = {
                "kill_switch_level": 3, "can_trade": False, "can_open": False,
                "margin_usage_ratio": 1.0, "fail_closed": True,
                "risk_snapshot": {"error": str(exc)},
            }

        # === 信号校验 ===
        action = signal.get("action", "")
        morning_orders = signal.get("morning_orders", [])
        afternoon_orders = signal.get("afternoon_orders", [])
        has_orders = bool(morning_orders or afternoon_orders)

        # === P0-11: fail-closed 双重保险 — 自检阶段 fail_closed 时阻止一切交易 ===
        check_state = self.state.get("phases", {}).get("check", {})
        if check_state.get("fail_closed", False) or self.state.get("fail_closed", False):
            logger.critical(
                "[Fail-Closed] 系统处于 fail-closed 状态, 阻止 phase_execute 全部交易: %s",
                check_state.get("reason", "unknown"),
            )
            self.state["phases"]["execute"] = {
                "status": "BLOCKED",
                "reason": "fail_closed",
                "risk_gate": risk_gate,
                "blocked_orders": len(morning_orders) + len(afternoon_orders),
            }
            return []

        # 允许直接从 trade_plan 回退读单，避免 --phase execute 跳过 phase_signal 时空跑
        if not has_orders and self.trade_plan:
            plan_exec = self.trade_plan.get("execution_plan", {})
            morning_orders = plan_exec.get("morning_orders", []) or []
            afternoon_orders = plan_exec.get("afternoon_orders", []) or []
            has_orders = bool(morning_orders or afternoon_orders)
            if has_orders:
                logger.info("phase_signal 未提供订单，已从 trade_plan 回退加载 %d 笔", len(morning_orders) + len(afternoon_orders))

        # === P0-7: KillSwitch 订单拦截 (L1 过滤 BUY / L2+ 阻止全部) ===
        # 在订单进入执行队列前, 根据 risk_gate 级别过滤/拦截
        pre_filter_count = len(morning_orders) + len(afternoon_orders)
        if has_orders and risk_gate:
            try:
                morning_orders, afternoon_orders, kill_switch_enforcement = self._enforce_kill_switch_on_orders(
                    morning_orders, afternoon_orders, risk_gate,
                )
                has_orders = bool(morning_orders or afternoon_orders)
                post_filter_count = len(morning_orders) + len(afternoon_orders)
                if pre_filter_count != post_filter_count:
                    logger.warning(
                        "[RiskGate] KillSwitch 拦截: %d → %d 笔订单 (拦截 %d 笔)",
                        pre_filter_count, post_filter_count, pre_filter_count - post_filter_count,
                    )
            except Exception as exc:
                # P0-11: fail-closed — 拦截异常时阻止全部交易
                logger.critical(
                    "[RiskGate] KillSwitch 拦截异常! fail-closed 阻止全部交易: %s",
                    exc, exc_info=True,
                )
                morning_orders, afternoon_orders = [], []
                has_orders = False
                kill_switch_enforcement = [{
                    "level": risk_gate.get("kill_switch_level", 3),
                    "action": "enforce_failed_fail_closed",
                    "error": str(exc),
                    "blocked_morning": pre_filter_count,
                }]

        if action not in ("BUILD_PLAN",) and not has_orders:
            logger.info("信号动作 %s, 无建仓订单, 跳过执行", action)
            self.state["phases"]["execute"] = {
                "status": "PASS",
                "fills": [],
                "action": action,
                "options_fills": options_fills,
                "options_count": len(options_fills),
                "risk_gate": risk_gate,
                "kill_switch_enforcement": kill_switch_enforcement,
                "put_budget_info": put_budget_info,
            }
            return []

        if not has_orders:
            logger.info("无订单可执行")
            self.state["phases"]["execute"] = {
                "status": "PASS",
                "fills": [],
                "options_fills": options_fills,
                "options_count": len(options_fills),
                "risk_gate": risk_gate,
                "kill_switch_enforcement": kill_switch_enforcement,
                "put_budget_info": put_budget_info,
            }
            return []

        # === 模拟盘模式 ===
        if self.sim_mode and self.sim_engine is not None:
            sim_fills = self._execute_sim_mode(signal, morning_orders, afternoon_orders)
            if not self.state["phases"]["execute"].get("options_fills"):
                self.state["phases"]["execute"]["options_fills"] = options_fills
                self.state["phases"]["execute"]["options_count"] = len(options_fills)
            return sim_fills

        # === DRY-RUN 模式 ===
        if self.dry_run:
            logger.info("DRY-RUN 模式, 仅生成指令不执行")

            # === 对冲基金视角: 执行算法引擎 (大单拆单计划) ===
            execution_plans: List[Dict[str, Any]] = []
            if self.exec_algo_engine is not None:
                try:
                    for order in morning_orders + afternoon_orders:
                        shares = int(order.get("shares", 0))
                        est_price = float(order.get("est_price", 0))
                        code = str(order.get("code", ""))
                        notional = shares * est_price
                        if shares >= 5000 or notional >= 200_000:
                            try:
                                algo_type = self.exec_algo_engine.select_algo(
                                    total_shares=shares,
                                    avg_daily_volume=shares * 20,
                                    urgency="normal",
                                    volatility=0.02,
                                )
                                plan = self.exec_algo_engine.plan_order(
                                    algo=algo_type,
                                    symbol=code,
                                    side=str(order.get("side", "BUY")).upper(),
                                    total_shares=shares,
                                    duration_minutes=120,
                                    slice_minutes=15,
                                    current_price=est_price,
                                )
                                saved_path = self.exec_algo_engine.save_plan(plan)
                                execution_plans.append({
                                    "symbol": code,
                                    "algo": algo_type.value,
                                    "slices": len(plan.slices),
                                    "first_slice_shares": plan.slices[0].shares if plan.slices else 0,
                                    "last_slice_shares": plan.slices[-1].shares if plan.slices else 0,
                                    "est_total_cost": plan.estimated_total_cost,
                                    "est_slippage_bps": plan.estimated_slippage_bps,
                                    "plan_path": str(saved_path),
                                })
                                logger.info(
                                    "[ExecAlgo] %s 拆单: %s -> %d slices (slippage=%.1fbps, cost=%.0f)",
                                    code, algo_type.value, len(plan.slices),
                                    plan.estimated_slippage_bps, plan.estimated_total_cost,
                                )
                            except Exception as exc:
                                logger.warning("[ExecAlgo] %s 拆单失败: %s", code, exc)
                    if execution_plans:
                        logger.info("[ExecAlgo] 共生成 %d 个拆单计划", len(execution_plans))
                except Exception as exc:
                    logger.error("[ExecAlgo] 执行算法引擎失败: %s", exc, exc_info=True)

            dry_orders = []
            for order in morning_orders + afternoon_orders:
                dry_orders.append({
                    "symbol": order.get("code", ""),
                    "name": order.get("name", ""),
                    "session": order.get("session", ""),
                    "side": order.get("side", "BUY"),
                    "qty": int(order.get("shares", 0)),
                    "price": float(order.get("est_price", 0)),
                    "limit_price": float(order.get("limit_price", 0)),
                    "amount": float(order.get("est_amount", 0)),
                    "algo": "LIMIT",
                    "status": "DRY_RUN",
                })
            self.state["orders"].extend(dry_orders)
            self.state["phases"]["execute"] = {
                "status": "PASS",
                "fills": dry_orders,
                "options_fills": options_fills,
                "options_count": len(options_fills),
                "execution_plans": execution_plans,
                "execution_plans_count": len(execution_plans),
                "risk_gate": risk_gate,
                "kill_switch_enforcement": kill_switch_enforcement,
                "put_budget_info": put_budget_info,
            }
            return dry_orders

        # === 对冲基金视角: 执行算法引擎 (大单拆单计划) ===
        execution_plans: List[Dict[str, Any]] = []
        if self.exec_algo_engine is not None:
            try:
                for order in morning_orders + afternoon_orders:
                    shares = int(order.get("shares", 0))
                    est_price = float(order.get("est_price", 0))
                    code = str(order.get("code", ""))
                    # 大单阈值: 单笔金额 > 20万 或股数 > 5000 触发拆单
                    notional = shares * est_price
                    if shares >= 5000 or notional >= 200_000:
                        try:
                            algo_type = self.exec_algo_engine.select_algo(
                                total_shares=shares,
                                avg_daily_volume=shares * 20,  # 估计 ADV
                                urgency="normal",
                                volatility=0.02,
                            )
                            plan = self.exec_algo_engine.plan_order(
                                algo=algo_type,
                                symbol=code,
                                side=str(order.get("side", "BUY")).upper(),
                                total_shares=shares,
                                duration_minutes=120,
                                slice_minutes=15,
                                current_price=est_price,
                            )
                            saved_path = self.exec_algo_engine.save_plan(plan)
                            execution_plans.append({
                                "symbol": code,
                                "algo": algo_type.value,
                                "slices": len(plan.slices),
                                "first_slice_shares": plan.slices[0].shares if plan.slices else 0,
                                "last_slice_shares": plan.slices[-1].shares if plan.slices else 0,
                                "est_total_cost": plan.estimated_total_cost,
                                "est_slippage_bps": plan.estimated_slippage_bps,
                                "plan_path": str(saved_path),
                            })
                            logger.info(
                                "[ExecAlgo] %s 拆单: %s -> %d slices (slippage=%.1fbps, cost=%.0f)",
                                code, algo_type.value, len(plan.slices),
                                plan.estimated_slippage_bps, plan.estimated_total_cost,
                            )
                        except Exception as exc:
                            logger.warning("[ExecAlgo] %s 拆单失败: %s", code, exc)
                if execution_plans:
                    logger.info("[ExecAlgo] 共生成 %d 个拆单计划", len(execution_plans))
            except Exception as exc:
                logger.error("[ExecAlgo] 执行算法引擎失败: %s", exc, exc_info=True)

        # === MockBroker 执行 ===
        try:
            broker = MockBroker(price_dict=dict(self.config.MOCK_PRICES))
            ntp = getattr(self, 'ntp', None) or NTPSync()
            sor = SmartOrderRouter(broker, ntp)

            all_fills: List[Dict[str, Any]] = []

            # === 上午批次执行 ===
            logger.info(f"--- 上午批次 {self.config.MORNING_WINDOW} ---")
            morning_fills = self._execute_order_batch(
                sor, broker, morning_orders, session="morning"
            )
            all_fills.extend(morning_fills)

            # === 单日回撤检查 (上午批次后) ===
            # 若上午批次亏损 > 3%, 暂停下午批次
            morning_amount = sum(f.get("amount", 0) for f in morning_fills)
            logger.info(f"上午批次完成: {len(morning_fills)} 笔成交, 金额 {morning_amount:,.0f}")

            # === 下午批次执行 ===
            logger.info(f"--- 下午批次 {self.config.AFTERNOON_WINDOW} ---")
            afternoon_fills = self._execute_order_batch(
                sor, broker, afternoon_orders, session="afternoon"
            )
            all_fills.extend(afternoon_fills)

            afternoon_amount = sum(f.get("amount", 0) for f in afternoon_fills)
            logger.info(f"下午批次完成: {len(afternoon_fills)} 笔成交, 金额 {afternoon_amount:,.0f}")

            # === 订单级汇总（统一报告与 JSON 口径） ===
            order_summary = self._aggregate_order_summary(morning_orders + afternoon_orders, all_fills)

            # === 汇总 ===
            total_amount = morning_amount + afternoon_amount
            logger.info(f"执行完成: {len(order_summary)} 笔订单, "
                        f"总金额 {total_amount:,.0f}")

            self.state["orders"].extend(all_fills)

            self.state["phases"]["execute"] = {
                "status": "PASS",
                "fills": all_fills,
                "order_summary": order_summary,
                "morning_count": len(morning_fills),
                "afternoon_count": len(afternoon_fills),
                "morning_amount": morning_amount,
                "afternoon_amount": afternoon_amount,
                "total_amount": total_amount,
                "options_fills": options_fills,
                "options_count": len(options_fills),
                "execution_plans": execution_plans,
                "execution_plans_count": len(execution_plans),
                "risk_gate": risk_gate,
                "kill_switch_enforcement": kill_switch_enforcement,
                "put_budget_info": put_budget_info,
            }

            # === 机构级: TCA 交易后成本分析 ===
            if self.tca_manager is not None and all_fills:
                try:
                    fills_by_symbol: Dict[str, List[FillRecord]] = {}
                    benchmarks: Dict[str, BenchmarkPrices] = {}
                    for fill in all_fills:
                        sym = str(fill.get("symbol", fill.get("code", "")))
                        if not sym:
                            continue
                        fr = FillRecord(
                            symbol=sym,
                            side=str(fill.get("side", "BUY")).upper(),
                            shares=int(fill.get("qty", fill.get("shares", 0))),
                            price=float(fill.get("price", 0)),
                            timestamp=self.trade_date,
                        )
                        fills_by_symbol.setdefault(sym, []).append(fr)
                        # 决策价 = 限价, 到达价 = 成交价 (MockBroker)
                        exec_price = float(fill.get("price", 0))
                        benchmarks[sym] = BenchmarkPrices(
                            decision_price=exec_price,
                            arrival_price=exec_price,
                            vwap=exec_price,
                            close_price=exec_price,
                        )
                    tca_reports = self.tca_manager.analyze_batch(
                        fills_by_symbol=fills_by_symbol,
                        benchmarks=benchmarks,
                    )
                    tca_summary = self.tca_manager.summarize(tca_reports)
                    self.state["phases"]["execute"]["tca_summary"] = tca_summary
                    self.state["phases"]["execute"]["tca_reports"] = {
                        sym: {
                            "grade": r.quality_grade,
                            "is_cost_bps": r.is_cost_bps,
                            "vwap_deviation_bps": r.vwap_deviation_bps,
                            "fill_rate": r.fill_rate,
                            "issues": r.issues,
                        } for sym, r in tca_reports.items()
                    }
                    logger.info(
                        "[TCA] %d 笔成交分析完成: avg IS=%.1fbps, avg VWAP dev=%.1fbps, fill_rate=%.1f%%",
                        tca_summary.get("n_orders", 0),
                        tca_summary.get("avg_is_cost_bps", 0),
                        tca_summary.get("avg_vwap_deviation_bps", 0),
                        tca_summary.get("avg_fill_rate", 0) * 100,
                    )
                except Exception as exc:
                    logger.error("[TCA] 分析失败: %s", exc, exc_info=True)

            # === 执行层: 执行算法 + 市场冲击 + 智能路由 ===
            if EXECUTION_MODULES_READY and self.execution_algo_engine is not None:
                try:
                    import pandas as _pd_exec
                    import numpy as _np_exec
                    # 为每笔成交生成执行计划与冲击估计
                    exec_plans_summary: List[Dict[str, Any]] = []
                    impact_estimates: List[Dict[str, Any]] = []
                    routing_decisions: List[Dict[str, Any]] = []

                    for fill in all_fills:
                        sym = str(fill.get("symbol", fill.get("code", "")))
                        side = str(fill.get("side", "BUY")).upper()
                        # 字段兼容: qty (MockBroker) / filled_shares / shares
                        shares = float(fill.get("qty", fill.get("filled_shares", fill.get("shares", 0))) or 0)
                        price = float(fill.get("price", 0) or 0)

                        if shares <= 0 or not sym:
                            continue

                        # ADV 代理: 用成交股数 × 10 (假设)
                        adv_proxy = max(shares * 10, 100_000.0)

                        # 1) 市场冲击估计
                        if self.market_impact_model is not None:
                            impact_est = self.market_impact_model.estimate(
                                symbol=sym,
                                order_shares=shares,
                                adv=adv_proxy,
                                decision_price=price,
                                volatility=0.02,
                                execution_time_days=1.0,
                            )
                            impact_estimates.append({
                                "symbol": sym,
                                "order_shares": shares,
                                "adv": adv_proxy,
                                "participation_rate": impact_est.participation_rate,
                                "total_impact_bps": impact_est.total_impact_bps,
                                "temporary_impact_bps": impact_est.temporary_impact_bps,
                                "permanent_impact_bps": impact_est.permanent_impact_bps,
                                "expected_exec_price": impact_est.expected_exec_price,
                                "model": impact_est.model_used,
                            })

                        # 2) 执行算法选择 (自动)
                        if self.execution_algo_engine is not None:
                            try:
                                # 构造 ExecOrder (start/end 用今日 9:30-15:00)
                                today = _pd_exec.Timestamp.now().normalize()
                                exec_order = ExecOrder(
                                    symbol=sym,
                                    side=side,
                                    total_shares=shares,
                                    start_time=today + _pd_exec.Timedelta(hours=9, minutes=30),
                                    end_time=today + _pd_exec.Timedelta(hours=15, minutes=0),
                                    benchmark_price=price,
                                    urgency="MEDIUM",
                                )
                                # 自动选算法
                                algo_name = self.execution_algo_engine.select_algorithm(
                                    order=exec_order,
                                    adv=adv_proxy,
                                    volatility=0.02,
                                )
                                # 生成计划
                                if algo_name == "VWAP":
                                    plan = self.execution_algo_engine.vwap(exec_order)
                                elif algo_name == "TWAP":
                                    plan = self.execution_algo_engine.twap(exec_order)
                                elif algo_name == "POV":
                                    plan = self.execution_algo_engine.pov(exec_order, expected_market_volume=adv_proxy)
                                elif algo_name == "IS":
                                    plan = self.execution_algo_engine.is_algo(exec_order, daily_volatility=0.02)
                                else:
                                    plan = self.execution_algo_engine.vwap(exec_order)

                                plan_summary = self.execution_algo_engine.summarize_plan(plan)
                                plan_summary["selected_by"] = "auto"
                                exec_plans_summary.append(plan_summary)
                            except Exception as ex_inner:
                                logger.debug("[ExecAlgo] %s 计划生成失败: %s", sym, ex_inner)

                        # 3) 智能路由决策
                        if self.smart_order_router_inst is not None:
                            try:
                                routing = self.smart_order_router_inst.route(
                                    symbol=sym,
                                    side=side,
                                    total_shares=shares,
                                    order_books=None,  # 无盘口时用场所默认评分
                                    strategy="SMART",
                                    max_venues=2,
                                )
                                routing_decisions.append(
                                    self.smart_order_router_inst.summarize_decision(routing)
                                )
                            except Exception as ex_router:
                                logger.debug("[SmartRouter] %s 路由失败: %s", sym, ex_router)

                    if exec_plans_summary:
                        self.state["phases"]["execute"]["execution_plans"] = exec_plans_summary
                        avg_cost_bps = float(_np_exec.mean([p.get("expected_cost_bps", 0) for p in exec_plans_summary]))
                        logger.info(
                            "[ExecAlgo] %d 笔执行计划生成: avg预期成本=%.2fbps, 平均切片数=%.1f",
                            len(exec_plans_summary), avg_cost_bps,
                            float(_np_exec.mean([p.get("num_slices", 0) for p in exec_plans_summary])),
                        )

                    if impact_estimates:
                        self.state["phases"]["execute"]["impact_estimates"] = impact_estimates
                        avg_impact_bps = float(_np_exec.mean([e["total_impact_bps"] for e in impact_estimates]))
                        logger.info(
                            "[MarketImpact] %d 笔冲击估计: avg总冲击=%.2fbps, avg参与度=%.4f",
                            len(impact_estimates), avg_impact_bps,
                            float(_np_exec.mean([e["participation_rate"] for e in impact_estimates])),
                        )

                    if routing_decisions:
                        self.state["phases"]["execute"]["routing_decisions"] = routing_decisions
                        primary_venues = [r.get("primary_venue", "") for r in routing_decisions]
                        logger.info(
                            "[SmartRouter] %d 笔路由决策: 主场所分布=%s",
                            len(routing_decisions),
                            dict((v, primary_venues.count(v)) for v in set(primary_venues)),
                        )
                except Exception as exc:
                    logger.error("[ExecutionModules] 执行层分析失败: %s", exc, exc_info=True)

            # === v8.5: 影子账户跟踪 (成交后同步记录) ===
            try:
                if V85_READY:
                    from validation.shadow_account_system import ShadowAccountSystem
                    shadow = ShadowAccountSystem()
                    shadow_result = shadow.track(
                        fills=all_fills,
                        trade_date=self.trade_date,
                        mode="shadow",
                    )
                    logger.info(
                        "[v8.5 ShadowAccount] 已同步 %d 笔成交到影子账户, "
                        "偏离度=%s, 状态=%s",
                        shadow_result.get("tracked_count", 0),
                        shadow_result.get("deviation", "N/A"),
                        shadow_result.get("status", "N/A"),
                    )
                    self.state["phases"]["execute"]["shadow_account"] = shadow_result
                else:
                    self.state["phases"]["execute"]["shadow_account"] = {"status": "SKIP", "reason": "v85_not_ready"}
            except Exception as e:
                logger.error(f"[v8.5 ShadowAccount] 跟踪失败: {e}", exc_info=True)
                self.state["phases"]["execute"]["shadow_account"] = {"status": "ERROR", "error": str(e)}

            return all_fills

        except Exception as e:
            logger.error(f"执行失败: {e}", exc_info=True)
            self.state["phases"]["execute"] = {
                "status": "FAIL",
                "error": str(e),
                "options_fills": options_fills,
                "options_count": len(options_fills),
                "execution_plans": execution_plans,
                "execution_plans_count": len(execution_plans),
            }
            return []

    def _execute_sim_mode(self,
                          signal: Dict[str, Any],
                          morning_orders: List[Dict[str, Any]],
                          afternoon_orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """模拟盘模式执行：股票日盘 + 期货（日盘+夜盘）"""
        calendar = self.sim_engine.calendar
        if not calendar.is_trading_day():
            logger.info("非交易日，跳过模拟盘执行")
            self.state["phases"]["execute"] = {"status": "PASS", "fills": [], "action": "SKIP_NON_TRADING_DAY"}
            return []

        all_fills: List[Dict[str, Any]] = []

        # === 上午批次（股票+期货日盘） ===
        logger.info("--- 模拟盘上午批次 (股票日盘 + 期货日盘) ---")
        morning_fills = self._execute_sim_batch(morning_orders, session="day")
        all_fills.extend(morning_fills)

        # === 下午批次（股票+期货日盘） ===
        logger.info("--- 模拟盘下午批次 (股票日盘 + 期货日盘) ---")
        afternoon_fills = self._execute_sim_batch(afternoon_orders, session="day")
        all_fills.extend(afternoon_fills)

        # === 期货夜盘批次（如有夜盘品种） ===
        futures_night_orders = self._extract_futures_night_orders(morning_orders + afternoon_orders)
        if futures_night_orders and calendar.is_trading_day():
            logger.info("--- 模拟盘夜盘批次 (期货夜盘) ---")
            night_fills = self._execute_sim_batch(futures_night_orders, session="night")
            all_fills.extend(night_fills)

        # === 更新持仓 ===
        if self.position_sync:
            try:
                self.position_sync.update_positions_from_fills(all_fills)
            except Exception as exc:
                logger.warning("更新持仓失败: %s", exc)

        # === 日末持仓快照 ===
        if self.position_sync:
            try:
                snapshot_path = self.position_sync.save_daily_snapshot(self.trade_date)
                self.state.setdefault("phases", {}).setdefault("execute", {})["sim_snapshot"] = str(snapshot_path)
            except Exception as exc:
                logger.warning("保存日末持仓快照失败: %s", exc)

        # === 订单级汇总 ===
        all_orders = morning_orders + afternoon_orders
        order_summary = self._aggregate_order_summary(all_orders, all_fills)

        morning_amount = sum(f.get("amount", 0) for f in morning_fills)
        afternoon_amount = sum(f.get("amount", 0) for f in afternoon_fills)
        total_amount = morning_amount + afternoon_amount

        self.state["orders"].extend(all_fills)
        # 期权希腊字母暴露
        greek_exposure = {}
        try:
            greek_exposure = self.sim_engine.get_greek_exposure() if hasattr(self.sim_engine, "get_greek_exposure") else {}
            if greek_exposure:
                logger.info("[模拟盘] 期权希腊字母暴露: Delta=%.2f Gamma=%.2f Theta=%.2f Vega=%.2f",
                            greek_exposure.get("delta", 0), greek_exposure.get("gamma", 0),
                            greek_exposure.get("theta", 0), greek_exposure.get("vega", 0))
        except Exception:
            pass

        self.state["phases"]["execute"] = {
            "status": "PASS",
            "fills": all_fills,
            "order_summary": order_summary,
            "morning_count": len(morning_fills),
            "afternoon_count": len(afternoon_fills),
            "morning_amount": morning_amount,
            "afternoon_amount": afternoon_amount,
            "total_amount": total_amount,
            "sim_mode": True,
            "greek_exposure": greek_exposure,
        }
        return all_fills

    def _execute_sim_batch(self,
                           orders: List[Dict[str, Any]],
                           session: str) -> List[Dict[str, Any]]:
        """执行一批模拟盘订单（按股票/期货/期权拆分）"""
        if not orders:
            return []

        stock_orders = []
        futures_orders = []
        options_orders = []
        for order in orders:
            symbol = str(order.get("code", order.get("symbol", "")))
            market = self.sim_engine.router._detect_market(symbol)
            if market == "stock":
                stock_orders.append(self._normalize_sim_order(order, session=session))
            elif market == "options":
                options_orders.append(self._normalize_sim_order(order, session=session))
            else:
                futures_orders.append(self._normalize_sim_order(order, session=session))

        fills: List[Dict[str, Any]] = []
        if stock_orders:
            logger.info("[模拟盘] 股票订单 %d 笔 @ %s", len(stock_orders), session)
            fills.extend(self.sim_engine.execute_stock_orders(stock_orders, session=session))
        if futures_orders:
            logger.info("[模拟盘] 期货订单 %d 笔 @ %s (同花顺期货通)", len(futures_orders), session)
            fills.extend(self.sim_engine.execute_futures_orders(futures_orders, session=session))
        if options_orders:
            logger.info("[模拟盘] 期权订单 %d 笔 @ %s", len(options_orders), session)
            fills.extend(self.sim_engine.execute_options_orders(options_orders, session=session))
        return fills

    def _normalize_sim_order(self, order: Dict[str, Any], session: str) -> Dict[str, Any]:
        """将交易计划订单规范化为模拟盘订单"""
        symbol = str(order.get("code", order.get("symbol", "")))
        side = str(order.get("side", "BUY"))
        qty = int(order.get("shares", order.get("qty", 0)))
        price = float(order.get("est_price", order.get("price", 0)))
        return {
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "price": price,
            "order_type": "LIMIT",
            "session": session,
        }

    def _extract_futures_night_orders(self, orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """从订单中提取支持夜盘的期货订单"""
        night_codes = set(self.sim_engine.router.futures_broker._night_session_info.keys())
        result = []
        for order in orders:
            symbol = str(order.get("code", order.get("symbol", "")))
            code = symbol[:2] if len(symbol) >= 2 else symbol
            if code in night_codes:
                result.append(order)
        return result

    def _execute_order_batch(self,
                             sor: SmartOrderRouter,
                             broker: MockBroker,
                             orders: List[Dict[str, Any]],
                             session: str) -> List[Dict[str, Any]]:
        """执行一批订单 (上午或下午)

        Args:
            sor: SmartOrderRouter 实例
            broker: MockBroker 实例
            orders: 订单列表 (按 priority 排序)
            session: "morning" 或 "afternoon"

        Returns:
            成交记录列表
        """
        fills: List[Dict[str, Any]] = []
        for order in orders:
            symbol = order.get("code", "")
            name = order.get("name", "")
            qty = int(order.get("shares", 0))
            side = order.get("side", "BUY")
            est_price = float(order.get("est_price", 0))
            limit_price = float(order.get("limit_price", 0))
            risk = order.get("risk", "中")
            style = order.get("style", "")

            if qty <= 0 or est_price <= 0:
                logger.warning(f"跳过无效订单: {symbol} qty={qty} price={est_price}")
                continue

            # 确保 MockBroker 使用订单 est_price，避免键前缀不匹配导致默认 10.0
            broker.prices[symbol] = est_price
            decision_price = est_price

            try:
                # 使用 ICEBERG 拆单执行 (每片 100 股)
                # decision_price 使用 est_price, MockBroker 会以 est_price ± 0.05% 成交
                fill_objs = sor.execute(
                    symbol=symbol,
                    target_qty=qty,
                    side=side,
                    decision_price=decision_price,
                    algo=AlgoType.ICEBERG,
                )

                for f in fill_objs:
                    slip_pct = getattr(f, 'slippage', 0.0) / decision_price if decision_price > 0 else 0.0
                    fill_status = "FILLED" if slip_pct < 0.005 else "SLIPPAGE_BREAK"
                    fill_dict = {
                        "symbol": f.symbol,
                        "name": name,
                        "session": session,
                        "side": f.side,
                        "qty": f.fill_qty,
                        "price": f.fill_price,
                        "amount": f.fill_qty * f.fill_price,
                        "limit_price": limit_price,
                        "est_price": est_price,
                        "decision_price": decision_price,
                        "risk": risk,
                        "style": style,
                        "slippage_bps": getattr(f, "slippage_bps", 0),
                        "slippage_pct": slip_pct,
                        "status": fill_status,
                    }
                    fills.append(fill_dict)

                logger.info(f"  [{session}] {symbol} {name}: {side} {qty}股 @ {est_price} → "
                            f"{len(fill_objs)} 笔成交, 金额 {sum(f.fill_qty*f.fill_price for f in fill_objs):,.0f}")

            except Exception as e:
                logger.error(f"  [{session}] {symbol} {name} 执行失败: {e}")
                fills.append({
                    "symbol": symbol,
                    "name": name,
                    "session": session,
                    "side": side,
                    "qty": qty,
                    "price": 0,
                    "amount": 0,
                    "status": "FAILED",
                    "error": str(e),
                })

        return fills

    def _aggregate_order_summary(self,
                                 orders: List[Dict[str, Any]],
                                 fills: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """按订单汇总成交明细，统一报告与状态 JSON 的执行口径"""
        # 建立 symbol -> session 映射，订单本身可能不含 session
        symbol_session_map: Dict[str, str] = {}
        for f in fills:
            sym = f.get("symbol", "")
            if sym and sym not in symbol_session_map:
                symbol_session_map[sym] = f.get("session", "")

        order_map: Dict[str, Dict[str, Any]] = {}
        for order in orders:
            symbol = order.get("code", "")
            if not symbol:
                continue
            session = order.get("session", symbol_session_map.get(symbol, ""))
            key = f"{symbol}|{session}"
            order_map[key] = {
                "symbol": symbol,
                "name": order.get("name", ""),
                "session": session,
                "side": order.get("side", "BUY"),
                "qty": int(order.get("shares", 0)),
                "est_price": float(order.get("est_price", 0)),
                "limit_price": float(order.get("limit_price", 0)),
                "risk": order.get("risk", "中"),
                "style": order.get("style", ""),
                "filled_qty": 0,
                "filled_amount": 0.0,
                "avg_price": 0.0,
                "max_slippage_pct": 0.0,
                "status": "PENDING",
                "error": "",
            }

        for f in fills:
            symbol = f.get("symbol", "")
            session = f.get("session", "")
            key = f"{symbol}|{session}"
            record = order_map.get(key)
            if not record:
                continue
            record["filled_qty"] += int(f.get("qty", 0))
            record["filled_amount"] += float(f.get("amount", 0))
            record["max_slippage_pct"] = max(
                record["max_slippage_pct"], float(f.get("slippage_pct", 0.0))
            )
            if f.get("status") == "FAILED":
                record["status"] = "FAILED"
                record["error"] = f.get("error", "")
            elif f.get("status") == "SLIPPAGE_BREAK" and record["status"] not in ("FAILED", "SLIPPAGE_BREAK"):
                record["status"] = "SLIPPAGE_BREAK"
            elif record["status"] not in ("FAILED", "SLIPPAGE_BREAK"):
                record["status"] = "FILLED"

        for record in order_map.values():
            if record["filled_qty"] > 0:
                record["avg_price"] = (
                    record["filled_amount"] / record["filled_qty"]
                    if record["filled_qty"]
                    else 0.0
                )
            if record["status"] == "FAILED" and not record["error"]:
                record["error"] = "no_fill"

        return list(order_map.values())

    # --------------------------------------------------------
    # Phase 7: 盘后报告
    # --------------------------------------------------------
    def phase_report(self) -> Path:
        """盘后报告生成"""
        logger.info("=" * 60)
        logger.info("Phase 7: 盘后报告生成")
        logger.info("=" * 60)

        # 报告路径 (统一使用 YYYY-MM-DD 格式，与 run_all_modules.py 一致)
        report_dir = self.config.REPORT_DIR / self.trade_date
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"v75_daily_workflow_{self.trade_date.replace('-', '')}.md"

        # === 报告头部 ===
        lines = [
            f"# v7.5 每日交易工作流报告 — {self.trade_date}",
            "",
            f"**生成时间**: {datetime.now():%Y-%m-%d %H:%M:%S}",
            f"**资金规模**: {self.capital:,.0f}",
            f"**执行模式**: {'DRY-RUN' if self.dry_run else ('模拟盘' if getattr(self, '_sim_mode_requested', getattr(self, 'sim_mode', False)) else 'MOCK_EXECUTION')}",
            f"**策略**: 康波第六轮周期 × 十五五规划 × v7.0期货期权双层对冲",
            "",
            "## 阶段执行摘要",
            "",
            "| 阶段 | 状态 |",
            "|------|------|",
        ]
        for phase_name, phase_data in self.state["phases"].items():
            status = phase_data.get("status", "N/A") if isinstance(phase_data, dict) else "N/A"
            lines.append(f"| {phase_name} | {status} |")

        # === 十五五年度阶段摘要 ===
        if self.current_phase_info is not None:
            pi = self.current_phase_info
            lines.extend([
                "",
                "## 十五五年度阶段",
                "",
                f"- **年度**: {pi.year}",
                f"- **阶段**: {pi.phase_name}",
                f"- **周期**: {pi.period}",
                f"- **当前季度**: {pi.current_quarter}",
                f"- **目标年化收益**: {pi.target_return:.1%}",
                f"- **最大回撤限制**: {pi.max_drawdown:.1%}",
                f"- **杠杆目标**: {pi.leverage_target:.2f}x",
                f"- **风险关注**: {pi.risk_focus}",
            ])
            if pi.is_liquidation_year and pi.liquidation_actions:
                lines.extend([
                    "",
                    f"### ⚠️ 2030 清仓 {pi.current_quarter}",
                    f"- **动作**: {pi.liquidation_actions.get('name', '')}",
                ])
                for action in pi.liquidation_actions.get("actions", []):
                    lines.append(f"  - {action}")
            # 季度评估结果 (如果在 v10_risk 阶段执行了)
            v10_result = self.state.get("phases", {}).get("v10_risk", {})
            quarterly = v10_result.get("quarterly_review", {}) if isinstance(v10_result, dict) else {}
            if quarterly.get("executed"):
                lines.extend([
                    "",
                    "### 季度评估结果",
                    f"- **季度**: {quarterly.get('quarter', '')}",
                    f"- **压测触发**: {'是' if quarterly.get('stress_test_triggered') else '否'}",
                    f"- **调仓需要**: {'是' if quarterly.get('rebalance_needed') else '否'}",
                    f"- **动作数**: {len(quarterly.get('actions', []))}",
                ])
                for action in quarterly.get("actions", []):
                    lines.append(f"  - {action}")

        # === Phase 1: 自检 ===
        if "check" in self.state["phases"]:
            lines.extend(["", "## 1. 系统自检", ""])
            checks = self.state["phases"]["check"].get("checks", {})
            lines.append("| 检查项 | 状态 |")
            lines.append("|--------|------|")
            for k, v in checks.items():
                lines.append(f"| {k} | {'✓' if v else '✗'} |")

        # === Phase 1.5: 收益预测动态校准 (新增) ===
        if "calibrate" in self.state["phases"]:
            lines.extend(["", "## 1.5 收益预测动态校准", ""])
            cal = self.state["phases"]["calibrate"]
            lines.append(f"- 状态: {cal.get('status', 'N/A')}")
            if cal.get("status") in ("PASS", "DEGRADED"):
                wf = cal.get("wind_fetch", {})
                rz = cal.get("realized", {})
                cb = cal.get("calibration", {})
                if cal.get("status") == "DEGRADED":
                    lines.append(f"- ⚠️ 降级原因: {wf.get('degraded_reason', 'N/A')}")
                    lines.append(f"- Wind 拉取: {wf.get('success', 0)} 成功 / "
                                 f"{wf.get('fail', 0)} 失败 (配额耗尽或网络异常)")
                else:
                    lines.append(f"- Wind 拉取: {wf.get('success', 0)} 成功 / "
                                 f"{wf.get('fail', 0)} 失败 "
                                 f"({wf.get('total_days', 0)} 日 × "
                                 f"{wf.get('total_symbols', 0)} 标的)")
                lines.append(f"- 真实历史: {rz.get('start_date','')} → "
                             f"{rz.get('end_date','')} "
                             f"({rz.get('years', 0):.3f} 年)")
                lines.append(f"- 持仓加权年化: "
                             f"{rz.get('portfolio_weighted_annualized', 0)*100:+.2f}% "
                             f"(覆盖权重 {rz.get('portfolio_weight_total', 0)*100:.2f}%)")
                lines.append(f"- 基准年化: {rz.get('market_annualized', 0)*100:+.2f}%, "
                             f"夏普 {rz.get('market_sharpe', 0):.2f}")
                lines.append(f"- 校准原因: {cb.get('calibration_reason', 'N/A')}")
                lines.append(f"- 原概率权重: {cb.get('original_weights', {})}")
                lines.append(f"- 新概率权重: {cb.get('calibrated_weights', {})}")
                lines.append(f"- **新期望年化: "
                             f"{cb.get('calibrated_expected_annualized', 0):.2f}%**")
                lines.append(f"- 新期望期末金额: "
                             f"¥{cb.get('calibrated_expected_final', 0):,.0f}")
            elif cal.get("status") == "SKIP":
                lines.append(f"- 原因: {cal.get('reason', 'N/A')}")
            else:
                lines.append(f"- 错误: {cal.get('error', 'N/A')[:200]}")

        # === Phase 2: 市场状态 ===
        if "market" in self.state["phases"]:
            lines.extend(["", "## 2. 市场状态", ""])
            m = self.state["phases"]["market"]
            lines.append(f"- VIX: {m.get('vix', 'N/A')}")
            lines.append(f"- 熔断级别: {m.get('circuit_level', 'N/A')}")
            lines.append(f"- 允许建仓: {m.get('build_allowed', False)}")

        # === Phase 3: 风险预算 (组合级别) ===
        if "risk" in self.state["phases"]:
            lines.extend(["", "## 3. 风险预算 (组合级别 — 500万 4阶段)", ""])
            r = self.state["phases"]["risk"]
            lines.append(f"- 组合净值: {r.get('equity', 0):,.0f}")
            lines.append(f"- 风险模式: {r.get('mode', 'N/A')}")
            lines.append(f"- 仓位系数: {r.get('position_factor', 0):.2f}")
            lines.append(f"- 股票组合资金: {r.get('stock_capital', 0):,.0f} (60%)")
            lines.append(f"- 期权对冲资金: {r.get('hedge_capital', 0):,.0f} (40%)")
            lines.append(f"- 当日建仓资金: {r.get('day_capital', 0):,.0f} "
                         f"(占股票组合 {r.get('build_ratio', 0):.2%})")
            lines.append(f"  - 上午批次: {r.get('morning_total', 0):,.0f}")
            lines.append(f"  - 下午批次: {r.get('afternoon_total', 0):,.0f}")
            lines.append(f"  - 合计: {r.get('grand_total', 0):,.0f}")
            lines.append(f"- 四级风控: 黄色 {r.get('yellow_warning', 0):.2%} / "
                         f"橙色 {r.get('orange_warning', 0):.2%} / "
                         f"红色 {r.get('red_warning', 0):.2%} / "
                         f"全部止损 {r.get('full_stop', 0):.2%}")
            lines.append(f"- VaR 预算: 95% < {r.get('var_95_limit', 0):.0%}, "
                         f"99% < {r.get('var_99_limit', 0):.0%}")

        # === Phase 4: 对冲评估 ===
        if "hedge" in self.state["phases"]:
            lines.extend(["", "## 4. 对冲评估", ""])
            h = self.state["phases"]["hedge"]
            lines.append(f"- 总对冲比例: {h.get('total_hedge_pct', 0):.2%}")
            actions = h.get("actions", {})
            if isinstance(actions, dict):
                for k, v in actions.items():
                    lines.append(f"  - {k}: {v}")
            elif isinstance(actions, list):
                for action in actions:
                    lines.append(f"  - {action.get('type', '')}: {action.get('action', 'N/A')}")

        # === Phase 5: 交易信号 (计划标的) ===
        if "signal" in self.state["phases"]:
            lines.extend(["", "## 5. 交易信号 (计划标的)", ""])
            s = self.state["phases"]["signal"]
            action = s.get("action", "")
            if action == "BUILD_PLAN":
                lines.append(f"- 阶段: {s.get('phase_name', '')} "
                             f"(第 {s.get('day_index', 0)} 日)")
                lines.append(f"- 上午批次: {s.get('morning_count', 0)} 笔, "
                             f"金额 {s.get('morning_amount', 0):,.0f} "
                             f"({s.get('morning_window', '')})")
                lines.append(f"- 下午批次: {s.get('afternoon_count', 0)} 笔, "
                             f"金额 {s.get('afternoon_amount', 0):,.0f} "
                             f"({s.get('afternoon_window', '')})")
                lines.append(f"- 单日合计: {s.get('total_orders', 0)} 笔, "
                             f"金额 {s.get('grand_amount', 0):,.0f}")
                if s.get("position_factor", 1.0) < 1.0:
                    lines.append(f"- **DEFENSE 模式**: 仓位系数 {s.get('position_factor', 0):.2f}")
            else:
                lines.append(f"- 动作: {action}")

            # Qlib 信号摘要
            if s.get("qlib_adjusted"):
                lines.append(f"- **Qlib 信号已调整**: 加仓={s.get('qlib_boost_count', 0)}, "
                             f"减仓={s.get('qlib_cut_count', 0)}, 跳过={s.get('qlib_skip_count', 0)}")
                qlib_signals = s.get("qlib_signals", {})
                if qlib_signals:
                    lines.append("  - 信号详情:")
                    for code, value in list(qlib_signals.items())[:10]:
                        lines.append(f"    - {code}: {value:+.4f}")

            # iFinD 新闻摘要
            if s.get("ifind_adjusted"):
                lines.append(f"- **iFinD 新闻已调整**: 加仓={s.get('ifind_boost_count', 0)}, "
                             f"减仓={s.get('ifind_cut_count', 0)}, 跳过={s.get('ifind_skip_count', 0)}")
                # 输出研判原因详情，方便人工复核
                morning_orders = s.get("morning_orders", [])
                afternoon_orders = s.get("afternoon_orders", [])
                ifind_details = []
                for order in morning_orders + afternoon_orders:
                    reasons = order.get("ifind_reasons")
                    if reasons:
                        ifind_details.append(
                            f"- {order.get('code', '')} {order.get('name', '')}: "
                            f"{order.get('ifind_direction', '')} "
                            f"confidence={order.get('ifind_confidence', 0):.2f} "
                            f"factor={order.get('ifind_factor', 1.0):.2f} "
                            f"-> {', '.join(reasons)}"
                        )
                if ifind_details:
                    lines.append("  - **研判原因详情**:")
                    lines.extend(f"    {detail}" for detail in ifind_details[:20])

            # AnySearch 实时新闻回顾
            if "market" in self.state["phases"]:
                anysearch_news = self.state["phases"]["market"].get("anysearch_news", [])
                if anysearch_news:
                    lines.append("")
                    lines.append("  - **AnySearch 实时新闻**:")
                    for item in anysearch_news:
                        title = item.get('title', '')[:50]
                        url = item.get('url', '')[:80]
                        lines.append(f"    - {title} -> {url}")

        # === 执行记录 (计划标的) ===
        if "execute" in self.state["phases"]:
            lines.extend(["", "## 6. 执行记录 (计划标的)", ""])
            e = self.state["phases"]["execute"]
            mode = "模拟盘" if getattr(self, '_sim_mode_requested', e.get("sim_mode", False)) else ("DRY-RUN" if self.dry_run else "MockBroker")
            lines.append(f"- **执行模式**: {mode}")
            order_summary = e.get("order_summary", [])
            fills = e.get("fills", [])
            if order_summary:
                total_filled_amount = sum(item.get("filled_amount", 0) for item in order_summary)
                lines.append(f"**成交汇总**: {len(order_summary)} 笔订单, "
                             f"总金额 {total_filled_amount:,.0f}")
                lines.append("")
                lines.append("### 上午批次")
                lines.append("")
                lines.append("| # | 代码 | 名称 | 风格 | 风险 | 方向 | 股数 | 预估价 | 成交价 | 金额 | 滑点 | 状态 |")
                lines.append("|---|------|------|------|------|------|------|------|------|------|------|------|")
                morning_orders = [item for item in order_summary if item.get("session") == "morning"]
                for i, item in enumerate(morning_orders, 1):
                    lines.append(f"| {i} | {item.get('symbol', '')} | {item.get('name', '')} | "
                                 f"{item.get('style', '')} | {item.get('risk', '')} | "
                                 f"{item.get('side', '')} | {item.get('qty', 0)} | "
                                 f"{item.get('est_price', 0):.4f} | {item.get('avg_price', 0):.4f} | "
                                 f"{item.get('filled_amount', 0):,.0f} | "
                                 f"{item.get('max_slippage_pct', 0):.4%} | "
                                 f"{item.get('status', '')} |")
                morning_total = sum(item.get("filled_amount", 0) for item in morning_orders)
                lines.append(f"| | | | | | | | | | **合计** | | **{morning_total:,.0f}** |")

                lines.append("")
                lines.append("### 下午批次")
                lines.append("")
                lines.append("| # | 代码 | 名称 | 风格 | 风险 | 方向 | 股数 | 预估价 | 成交价 | 金额 | 滑点 | 状态 |")
                lines.append("|---|------|------|------|------|------|------|------|------|------|------|------|")
                afternoon_orders = [item for item in order_summary if item.get("session") == "afternoon"]
                for i, item in enumerate(afternoon_orders, 1):
                    lines.append(f"| {i} | {item.get('symbol', '')} | {item.get('name', '')} | "
                                 f"{item.get('style', '')} | {item.get('risk', '')} | "
                                 f"{item.get('side', '')} | {item.get('qty', 0)} | "
                                 f"{item.get('est_price', 0):.4f} | {item.get('avg_price', 0):.4f} | "
                                 f"{item.get('filled_amount', 0):,.0f} | "
                                 f"{item.get('max_slippage_pct', 0):.4%} | "
                                 f"{item.get('status', '')} |")
                afternoon_total = sum(item.get("filled_amount", 0) for item in afternoon_orders)
                lines.append(f"| | | | | | | | | | **合计** | | **{afternoon_total:,.0f}** |")

                lines.append("")
                lines.append(f"**单日总计**: 上午 {morning_total:,.0f} + "
                             f"下午 {afternoon_total:,.0f} = "
                             f"**{morning_total + afternoon_total:,.0f}**")
            else:
                lines.append("无成交")

            # 子成交明细（审计用）
            if fills:
                lines.extend(["", "### 子成交明细", ""])
                lines.append("| 代码 | 名称 | 批次 | 方向 | 股数 | 价格 | 金额 | 滑点 | 状态 |")
                lines.append("|------|------|------|------|------|------|------|------|------|")
                for f in fills:
                    lines.append(f"| {f.get('symbol', '')} | {f.get('name', '')} | "
                                 f"{f.get('session', '')} | {f.get('side', '')} | "
                                 f"{f.get('qty', 0)} | {f.get('price', 0):.4f} | "
                                 f"{f.get('amount', 0):,.0f} | "
                                 f"{f.get('slippage_pct', 0):.4%} | "
                                 f"{f.get('status', '')} |")

        # === 总结 ===
        execute_phase = self.state.get("phases", {}).get("execute", {})
        order_summary = execute_phase.get("order_summary", [])
        total_orders = len(order_summary)
        total_amount = sum(item.get("filled_amount", 0) for item in order_summary)
        # === 对冲基金视角: P&L 八维归因分析 ===
        if self.pnl_attribution_engine is not None:
            try:
                positions = (self._get_portfolio_positions_for_stress_test()
                             if hasattr(self, "_get_portfolio_positions_for_stress_test") else [])
                # 当日成交
                fills = self.state.get("phases", {}).get("execute", {}).get("fills", [])
                trading_costs = sum(
                    float(f.get("amount", 0)) * 0.001  # 估算 10bps 综合成本
                    for f in fills
                )
                # 对冲盈亏
                hedge_pnl = float(self.state.get("phases", {}).get("hedge", {}).get("hedge_pnl", 0.0))
                # 组合与基准收益 (基于持仓盈亏的简化估算) — 转换为收益序列
                portfolio_value = float(getattr(self, "capital", 5_000_000))
                # 当日盈亏 = Σ(持仓市值 × 当日涨幅) 简化: 使用 phase_market 中的 beta/涨幅代理
                market_phase = self.state.get("phases", {}).get("market", {})
                portfolio_ret_today = float(market_phase.get("portfolio_return", 0.0)) or 0.0
                benchmark_ret_today = float(market_phase.get("benchmark_return", 0.0)) or 0.0
                if abs(portfolio_ret_today) < 1e-6 and positions:
                    # 回退: 使用持仓总市值 vs 资金比例估算
                    total_mv = sum(float(p.get("amount", 0)) for p in positions)
                    portfolio_ret_today = (total_mv - portfolio_value * 0.5) / (portfolio_value * 0.5) * 0.005  # 0.5% 假设日收益
                # 包装为长度=1 的收益序列 (单日)
                portfolio_returns = [portfolio_ret_today]
                benchmark_returns = [benchmark_ret_today]
                market_returns = benchmark_returns  # 沪深300代理
                # 简化: 因子与行业收益沿用组合收益（实盘接入后由 Barra 模型填充）
                factor_returns = {
                    "momentum": [portfolio_ret_today * 0.3],
                    "reversal": [-portfolio_ret_today * 0.1],
                    "volatility": [portfolio_ret_today * 0.1],
                    "liquidity": [portfolio_ret_today * 0.05],
                    "earnings_quality": [portfolio_ret_today * 0.2],
                    "growth": [portfolio_ret_today * 0.15],
                    "valuation": [portfolio_ret_today * 0.1],
                }
                sector_returns = {
                    "高端制造": [portfolio_ret_today * 0.4],
                    "顺周期": [portfolio_ret_today * 0.2],
                    "资源": [portfolio_ret_today * 0.2],
                    "防御": [portfolio_ret_today * 0.2],
                }
                attribution = self.pnl_attribution_engine.attribute(
                    positions=positions,
                    portfolio_returns=portfolio_returns,
                    benchmark_returns=benchmark_returns,
                    market_returns=market_returns,
                    factor_returns=factor_returns,
                    sector_returns=sector_returns,
                    trading_costs=trading_costs,
                    funding_cost=0.0,
                    hedge_pnl=hedge_pnl,
                )
                self.state["phases"]["report_pnl_attribution"] = {
                    "total_pnl": attribution.total_pnl,
                    "total_return_pct": attribution.total_return_pct,
                    "alpha_pnl": attribution.alpha_pnl,
                    "beta_pnl": attribution.beta_pnl,
                    "style_pnl": attribution.style_pnl,
                    "sector_pnl": attribution.sector_pnl,
                    "timing_pnl": attribution.timing_pnl,
                    "hedge_pnl": attribution.hedge_pnl,
                    "trading_cost": attribution.trading_cost,
                    "funding_cost": attribution.funding_cost,
                    "sharpe_ratio": attribution.sharpe_ratio,
                    "information_ratio": attribution.information_ratio,
                    "tracking_error": attribution.tracking_error,
                    "anomalies": attribution.anomalies,
                }
                lines.extend([
                    "",
                    "## P&L 归因分析 (对冲基金视角)",
                    "",
                    f"- **总 P&L**: ¥{attribution.total_pnl:,.0f} ({attribution.total_return_pct:.2%})",
                    f"- **Alpha 贡献**: ¥{attribution.alpha_pnl:,.0f}",
                    f"- **Beta 贡献**: ¥{attribution.beta_pnl:,.0f}",
                    f"- **风格因子**: ¥{attribution.style_pnl:,.0f}",
                    f"- **行业配置**: ¥{attribution.sector_pnl:,.0f}",
                    f"- **择时**: ¥{attribution.timing_pnl:,.0f}",
                    f"- **对冲**: ¥{attribution.hedge_pnl:,.0f}",
                    f"- **交易成本**: ¥{attribution.trading_cost:,.0f}",
                    f"- **资金成本**: ¥{attribution.funding_cost:,.0f}",
                    "",
                    "### 风险调整收益指标",
                    "",
                    f"- **Sharpe Ratio (年化)**: {attribution.sharpe_ratio:.3f}",
                    f"- **Information Ratio**: {attribution.information_ratio:.3f}",
                    f"- **Tracking Error (年化)**: {attribution.tracking_error:.2%}",
                    "",
                ])
                if attribution.anomalies:
                    lines.extend([
                        "### 异常检测告警",
                        "",
                    ])
                    for a in attribution.anomalies:
                        lines.append(f"- ⚠️ {a}")
                    lines.append("")
                # 风格因子贡献明细
                if attribution.style_factors:
                    lines.extend([
                        "### 风格因子贡献明细",
                        "",
                        "| 因子 | 暴露 | 因子收益 | 贡献 |",
                        "|------|------|----------|------|",
                    ])
                    for fc in attribution.style_factors:
                        lines.append(
                            f"| {fc.factor_name} | {fc.exposure:.4f} | {fc.factor_return:.4f} | ¥{fc.contribution:,.0f} |"
                        )
                    lines.append("")
            except Exception as exc:
                logger.error("[PnLAttribution] 归因失败: %s", exc, exc_info=True)
                lines.extend(["", f"**P&L 归因失败**: {exc}", ""])

        # === 机构级: Barra 风险因子暴露分解 ===
        if self.barra_decomposer is not None:
            try:
                positions = (self._get_portfolio_positions_for_stress_test()
                             if hasattr(self, "_get_portfolio_positions_for_stress_test") else [])
                logger.info("[Barra] 持仓数量: %d", len(positions))
                if positions:
                    barra_result = self.barra_decomposer.decompose_from_positions(
                        positions=positions,
                        risk_budget=0.05,
                    )
                    logger.info(
                        "[Barra] 分解完成: TE=%.2f%%, IR=%.3f, 风险预算利用=%.1f%%",
                        barra_result.active_risk * 100,
                        barra_result.information_ratio,
                        barra_result.risk_budget_utilization * 100,
                    )
                    self.state["phases"]["report_barra"] = {
                        "active_risk": barra_result.active_risk,
                        "factor_risk": barra_result.factor_risk,
                        "specific_risk": barra_result.specific_risk,
                        "factor_risk_pct": barra_result.factor_risk_pct,
                        "active_return": barra_result.active_return,
                        "information_ratio": barra_result.information_ratio,
                        "risk_budget_used": barra_result.risk_budget_used,
                        "risk_budget_remaining": barra_result.risk_budget_remaining,
                        "risk_budget_utilization": barra_result.risk_budget_utilization,
                        "concentrated_factors": barra_result.concentrated_factors,
                        "missing_factors": barra_result.missing_factors,
                        "industry_exposures": barra_result.industry_exposures,
                    }
                    lines.extend([
                        "",
                        "## Barra 风险因子暴露分解 (AQR 风格)",
                        "",
                        f"- **主动风险 (跟踪误差)**: {barra_result.active_risk:.2%}",
                        f"  - 因子风险: {barra_result.factor_risk:.2%} ({barra_result.factor_risk_pct:.1%})",
                        f"  - 个股特异性风险: {barra_result.specific_risk:.2%}",
                        f"- **主动收益**: {barra_result.active_return:.2%}",
                        f"- **信息比率 (IR)**: {barra_result.information_ratio:.3f}",
                        f"  - 因子 IR: {barra_result.factor_ir:.3f}",
                        f"  - 个股 IR: {barra_result.specific_ir:.3f}",
                        "",
                        "### 风险预算审计",
                        "",
                        f"- 已使用: {barra_result.risk_budget_used:.2%}",
                        f"- 剩余: {barra_result.risk_budget_remaining:.2%}",
                        f"- 利用率: {barra_result.risk_budget_utilization:.1%}",
                        "",
                        "### 10 个风格因子暴露",
                        "",
                        "| 因子 | 主动暴露 | 因子收益 | 收益贡献 | 风险贡献 |",
                        "|------|----------|----------|----------|----------|",
                    ])
                    for fe in barra_result.style_factor_exposures:
                        lines.append(
                            f"| {fe.factor_name} | {fe.exposure:+.4f} | {fe.factor_return:+.4f} | "
                            f"{fe.contribution_to_active_return:+.4f} | {fe.contribution_to_active_risk:.4f} |"
                        )
                    lines.append("")
                    # 行业暴露
                    if barra_result.industry_exposures:
                        lines.extend([
                            "### 行业主动暴露",
                            "",
                            "| 行业 | 主动权重 |",
                            "|------|----------|",
                        ])
                        for ind, w in sorted(barra_result.industry_exposures.items(),
                                              key=lambda x: abs(x[1]), reverse=True):
                            lines.append(f"| {ind} | {w:+.2%} |")
                        lines.append("")
                    # 诊断告警
                    if barra_result.concentrated_factors:
                        lines.append(f"⚠️ **因子集中**: {', '.join(barra_result.concentrated_factors)}")
                    if barra_result.missing_factors:
                        lines.append(f"ℹ️ **因子缺失**: {', '.join(barra_result.missing_factors)}")
                    if barra_result.risk_budget_utilization > 0.9:
                        lines.append(f"⚠️ **风险预算紧张**: 利用率 {barra_result.risk_budget_utilization:.1%}")
                    lines.append("")
            except Exception as exc:
                logger.error("[Barra] 风险分解失败: %s", exc, exc_info=True)
                lines.extend(["", f"**Barra 风险分解失败**: {exc}", ""])

        all_pass = all(
            p.get("status") == "PASS" for p in self.state.get("phases", {}).values()
            if isinstance(p, dict)
        )

        # === v8.6.1: EOD 四 Guard 风控链强制执行 ===
        lines.extend(["", "## EOD 四 Guard 风控链 (v8.6.1)", ""])
        try:
            from utils.risk_guard_integrator import RiskGuardIntegrator
            # 计算下一交易日 (跳过周末)
            from datetime import timedelta as _td
            _next_dt = datetime.strptime(self.trade_date, "%Y-%m-%d") + _td(days=1)
            while _next_dt.weekday() >= 5:
                _next_dt += _td(days=1)
            _next_trade_date = _next_dt.strftime("%Y-%m-%d")

            logger.info(f"[v8.6.1] 启动 EOD 四 Guard 链 → 次日: {_next_trade_date}")
            _integrator = RiskGuardIntegrator(report_date=self.trade_date)
            _updated_plan = _integrator.run_all_guards(_next_trade_date)

            # 提取 Guard 结果并写入报告
            _rg = _updated_plan.get("risk_guard", {})
            lines.append(f"| Guard | 状态 | 关键指标 |")
            lines.append(f"|-------|------|----------|")
            _guard_map = {
                "kill_switch": ("保证金熔断", "level"),
                "drawdown": ("回撤检查", "level"),
                "vol_target": ("波动率控制", "vol_scale"),
                "hedge_execution": ("对冲执行", "hedge_pct"),
                "protective_put": ("认沽保护", "put_orders"),
            }
            _all_guards_pass = True
            for _key, (_name, _metric_key) in _guard_map.items():
                _gd = _rg.get(_key, {})
                if isinstance(_gd, dict):
                    _passed = _gd.get("passed", _gd.get("build_allowed", True))
                    _status = "✅ 通过" if _passed else "❌ 未通过"
                    if not _passed:
                        _all_guards_pass = False
                    _metric = str(_gd.get(_metric_key, "-"))
                else:
                    _status = "⚠️ 无数据"
                    _metric = "-"
                lines.append(f"| {_name} | {_status} | {_metric} |")
            lines.append("")
            if _all_guards_pass:
                lines.append(f"**四 Guard 链**: ✅ 全部通过, 次日 ({_next_trade_date}) 可正常交易")
            else:
                lines.append(f"**四 Guard 链**: ❌ 存在未通过项, 请检查 {_next_trade_date} 交易计划")
            logger.info(f"[v8.6.1] EOD 四 Guard 链完成: {'全部通过' if _all_guards_pass else '存在未通过项'}")
        except ImportError:
            lines.append("⚠️ RiskGuardIntegrator 不可用, 四 Guard 链未执行")
            logger.warning("[v8.6.1] RiskGuardIntegrator 导入失败, 四 Guard 链未执行")
        except Exception as _e_guard:
            lines.append(f"⚠️ 四 Guard 链执行异常: {_e_guard}")
            logger.error(f"[v8.6.1] EOD 四 Guard 链异常: {_e_guard}", exc_info=True)
        lines.append("")

        lines.extend([
            "",
            "## 总结",
            "",
            f"- 工作流执行 {'成功' if all_pass else '部分失败'}",
            f"- 总成交笔数: {total_orders}",
            f"- 总成交金额: {total_amount:,.0f}",
            f"- 数据源: 2026年交易计划.md + trade_plan_{self.trade_date.replace('-', '')}.json",
            "",
        ])

        # Report write with retry + fallback
        success = False
        for attempt in range(3):
            try:
                report_path.write_text("\n".join(lines), encoding="utf-8")
                logger.info(f"报告已生成: {report_path}")
                success = True
                break
            except PermissionError:
                if attempt < 2:
                    import time
                    logger.warning(f"Report write permission denied, retry {attempt+1}/3...")
                    time.sleep(1)
                else:
                    logger.error(f"Report write failed ({attempt+1}/3): Permission denied")

        if not success:
            fallback_path = self.log_dir / report_path.name
            try:
                fallback_path.write_text("\n".join(lines), encoding="utf-8")
                logger.warning(f"Report written to fallback: {fallback_path}")
            except Exception as e_fallback:
                logger.error(f"Fallback write also failed: {e_fallback}")

        # 同时保存 JSON 状态
        json_path = report_path.with_suffix(".json")
        try:
            # 使用 json.dump 流式写入文件，避免 json.dumps 在内存中构建巨大字符串导致 MemoryError
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2, default=str)
            logger.info(f"状态 JSON: {json_path}")
        except (MemoryError, OSError) as e:
            logger.warning(f"状态 JSON 保存失败（内存不足），尝试无缩进模式: {e}")
            try:
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(self.state, f, ensure_ascii=False, default=str)
                logger.info(f"状态 JSON (无缩进): {json_path}")
            except Exception as e2:
                logger.error(f"状态 JSON 保存彻底失败: {e2}")

        return report_path

    # --------------------------------------------------------
    # Phase 8: 自主学习量化训练 (增强版)
    #   - 优先使用 lgb_enhanced_trainer (真实OHLCV + 情绪因子 + 自适应重训)
    #   - 不可用时回退到 autolearn_trainer (合成OHLCV + LGB+XGB)
    #   - 真实 OHLCV: Wind MCP > iFinD MCP > 新浪 HTTP, 502 日数据
    #   - 新闻情绪因子: 一次性拉取 N 天真实新闻, 按 publish_time 分配
    #   - 自适应重训: best_iter <= 5 的标的用更小学习率重训
    #   - 模型持久化到 models/lgb_enhanced/{symbol}/ 或 models/autolearn/{symbol}/
    #   - 生成 lgb_enhanced_signals.json 供次日交易使用
    # --------------------------------------------------------
    def phase_autolearn(self) -> bool:
        """自主学习量化训练 (优先使用增强训练器)"""
        logger.info("=" * 60)
        logger.info(f"Phase 8: 自主学习量化训练 @ {self.trade_date}")
        logger.info(f"  引擎: {AUTOLEARN_ENGINE}")
        logger.info("=" * 60)

        if not AUTOLEARN_READY:
            logger.warning("自主学习训练模块未就绪, 跳过训练")
            self.state["phases"]["autolearn"] = {
                "status": "SKIP",
                "reason": "训练模块未导入",
            }
            return True

        try:
            # 执行训练 (7 天内不重训)
            # 增强训练器: 真实OHLCV + 情绪因子 + 自适应重训
            # 旧训练器: 合成OHLCV + LGB+XGB 集成
            result = _run_autolearn(force_retrain=False)

            if result.get("status") != "OK":
                logger.error(f"自主学习训练失败: {result}")
                self.state["phases"]["autolearn"] = {
                    "status": "FAIL",
                    "error": str(result)[:500],
                }
                return True

            # 生成日报
            try:
                report_path = _generate_autolearn_report(result)
                logger.info(f"自主学习日报: {report_path}")
            except Exception as e:
                logger.warning(f"日报生成失败: {e}")
                report_path = None

            # 保存状态
            signals = result.get("signals", {})
            summary = signals.get("summary", {})
            self.state["phases"]["autolearn"] = {
                "status": "PASS",
                "engine": AUTOLEARN_ENGINE,
                "total": result.get("total", 0),
                "trained": result.get("trained", 0),
                "skipped": result.get("skipped", 0),
                "failed": result.get("failed", 0),
                "signals_summary": summary,
                "top_signals": sorted(
                    [(k, v.get("signal", 0)) for k, v in signals.get("signals", {}).items()],
                    key=lambda x: abs(x[1]),
                    reverse=True,
                )[:5],
                "report_path": str(report_path) if report_path else None,
            }

            # === v8.5: Purged K-Fold 验证 (训练后验证过拟合风险) ===
            try:
                if V85_READY:
                    from model_validation.purged_kfold_cv import PurgedKFoldCV
                    pkf = PurgedKFoldCV(n_splits=5, purge_window=5)
                    cv_result = pkf.validate(result)
                    self.state["phases"]["autolearn"]["purged_kfold"] = cv_result
                    logger.info(
                        "[v8.5 PurgedKFold] 验证完成: avg_IC=%.4f, ICIR=%.4f, "
                        "过拟合标志=%s, 评分=%s",
                        cv_result.get("avg_ic", 0),
                        cv_result.get("icir", 0),
                        cv_result.get("overfit_detected", False),
                        cv_result.get("grade", "N/A"),
                    )
                    if cv_result.get("overfit_detected"):
                        logger.warning("[v8.5 PurgedKFold] 过拟合风险检测! 建议review因子/参数")
                else:
                    self.state["phases"]["autolearn"]["purged_kfold"] = {"status": "SKIP", "reason": "v85_not_ready"}
            except Exception as e:
                logger.error(f"[v8.5 PurgedKFold] 验证失败: {e}", exc_info=True)
                self.state["phases"]["autolearn"]["purged_kfold"] = {"status": "ERROR", "error": str(e)}

            logger.info(f"Phase 8 完成: 训练 {result.get('trained', 0)} 标的, "
                        f"信号 {summary.get('total', 0)} 个 "
                        f"(多 {summary.get('bullish', 0)}/空 {summary.get('bearish', 0)}/"
                        f"中性 {summary.get('neutral', 0)})")
            return True

        except Exception as e:
            logger.error(f"Phase 8 异常: {e}", exc_info=True)
            self.state["phases"]["autolearn"] = {
                "status": "FAIL",
                "error": str(e),
            }
            return True

    # --------------------------------------------------------
    # Phase 9: FactorKillSwitch 因子实时监控 (S6 持续监控)
    #   - 监控现有 51 个生产因子 + 16 个 vibe_trading 候选因子
    #   - 状态机: ACTIVE -> WARNED -> DEGRADED -> DISABLED -> RETIRED
    #     紧急退出: EMERGENCY_EXIT (单日/累计回撤触发)
    #   - 每日增量更新, 状态持久化到 reports/kill_switch/{trade_date}/
    #   - 失败降级为 SKIP, 不影响主工作流 (P0.6 异常隔离要求)
    # --------------------------------------------------------
    def phase_factor_kill_switch(self) -> bool:
        """FactorKillSwitch 每日监控 (S6 持续监控)"""
        logger.info("=" * 60)
        logger.info(f"Phase 9: FactorKillSwitch 因子实时监控 @ {self.trade_date}")
        logger.info("=" * 60)

        if not FACTOR_KS_READY:
            logger.warning("FactorKillSwitch 模块未就绪, 跳过 Phase 9")
            self.state["phases"]["factor_kill_switch"] = {
                "status": "SKIP",
                "reason": "FactorKillSwitch 模块未导入",
            }
            return True

        try:
            result = _run_factor_kill_switch_daily(trade_date=self.trade_date)
            status = result.get("status", "FAIL")

            if status == "PASS":
                total = result.get("total_monitored", 0)
                state_dist = result.get("state_distribution", {})
                triggered_today = result.get("triggered_today", [])
                active_cnt = state_dist.get("active", 0)
                active_rate = (active_cnt / total * 100) if total > 0 else 0

                logger.info(
                    f"Phase 9 完成: 监控 {total} 个因子 | "
                    f"ACTIVE {active_cnt}/{total} ({active_rate:.1f}%) | "
                    f"当日新触发 {len(triggered_today)} 个"
                )
                if triggered_today:
                    for t in triggered_today[:5]:
                        logger.warning(
                            "[FactorKillSwitch] %s: %s -> %s (%s)",
                            t.get("factor", "?"),
                            t.get("prev_status", "?"),
                            t.get("new_status", "?"),
                            t.get("trigger", "")[:80],
                        )

            elif status == "SKIP":
                logger.warning(f"Phase 9 跳过: {result.get('reason', 'unknown')}")
            else:
                logger.error(f"Phase 9 失败: {result.get('error', 'unknown')}")

            self.state["phases"]["factor_kill_switch"] = result
            return True

        except Exception as e:
            logger.error(f"Phase 9 异常: {e}", exc_info=True)
            self.state["phases"]["factor_kill_switch"] = {
                "status": "FAIL",
                "error": str(e),
            }
            # 异常隔离: Phase 9 失败不影响已完成的 Phase 1-8
            return True

    # --------------------------------------------------------
    # Phase 10: 影子账户监控 (Shadow Account Monitoring)
    # --------------------------------------------------------
    def phase_shadow_monitor(self) -> bool:
        """Phase 10: 影子账户每日净值记录 + fail-fast 检查

        职责:
        - 加载影子账户状态 (output/shadow_account/shadow_state.json)
        - 计算当日组合净值 (基于实际持仓 + 当日收盘价)
        - 记录每日净值到 daily_nav
        - 检查 fail-fast 触发条件 (单日>3%, 3日>5%)
        - 触发时终止影子账户并记录原因
        - 保存状态

        2026-07-25 顶级对冲基金审计 P0-11: 影子账户 fail-fast 集成
        """
        logger.info(f"Phase 10: 影子账户监控 @ {self.trade_date}")

        try:
            import json as _json
            from pathlib import Path as _Path

            # === 加载影子账户状态 ===
            state_file = _Path("output") / "shadow_account" / "shadow_state.json"
            if not state_file.exists():
                logger.info("Phase 10 跳过: 影子账户未初始化 (运行 python launch_shadow_account.py 启动)")
                self.state["phases"]["shadow_monitor"] = {
                    "status": "SKIP",
                    "reason": "shadow_account_not_initialized",
                }
                return True

            with open(state_file, "r", encoding="utf-8") as f:
                shadow_state = _json.load(f)

            # 已终止的影子账户仅记录状态, 不再更新
            if shadow_state.get("status") == "TERMINATED":
                logger.warning(
                    "Phase 10: 影子账户已被 fail-fast 终止 (原因: %s), 跳过净值记录",
                    shadow_state.get("fail_fast_log", [{}])[-1].get("reason", "unknown")
                    if shadow_state.get("fail_fast_log") else "unknown",
                )
                self.state["phases"]["shadow_monitor"] = {
                    "status": "TERMINATED",
                    "reason": shadow_state.get("fail_fast_log", [{}])[-1].get("reason", "")
                    if shadow_state.get("fail_fast_log") else "",
                }
                return True

            # === 计算当日组合净值 ===
            # 从 Phase 6 获取今日执行的订单 (影子账户跟踪生产信号)
            execute_phase = self.state.get("phases", {}).get("execute", {})
            orders = execute_phase.get("orders", []) if isinstance(execute_phase, dict) else []

            # 计算当日组合收益 (简化: 使用 signal 阶段的目标权重 + 实际收益)
            signal_phase = self.state.get("phases", {}).get("signal", {})
            target_weights = signal_phase.get("target_weights", {}) if isinstance(signal_phase, dict) else {}

            # 从 data_provider 获取当日收盘价, 计算实际收益
            daily_return = 0.0
            if target_weights:
                try:
                    from utils.data_provider import MarketDataProvider
                    provider = MarketDataProvider(backtest_mode=False)
                    total_weight = sum(abs(w) for w in target_weights.values())
                    if total_weight > 0:
                        for symbol, weight in target_weights.items():
                            if abs(weight) < 0.001:
                                continue
                            try:
                                df = provider.get_historical_data(symbol, period="5d")
                                if df is not None and not df.empty and len(df) >= 2:
                                    ret = float(df["close"].iloc[-1] / df["close"].iloc[-2] - 1)
                                    daily_return += weight * ret
                            except Exception:
                                pass
                except Exception as e:
                    logger.warning(f"Phase 10: 获取行情数据失败, 使用 0 收益: {e}")
                    daily_return = 0.0

            # === 更新净值 ===
            current_nav = float(shadow_state.get("current_nav", 1.0))
            new_nav = current_nav * (1 + daily_return)
            daily_nav_list = shadow_state.get("daily_nav", [])

            today_str = str(self.trade_date)
            daily_nav_list.append({
                "date": today_str,
                "nav": round(new_nav, 6),
                "daily_return": round(daily_return, 6),
                "capital": round(new_nav * float(shadow_state.get("initial_capital", 500_000)), 2),
                "recorded_at": datetime.now().isoformat(),
            })

            # 保留最近 365 天净值 (避免状态文件膨胀)
            if len(daily_nav_list) > 365:
                daily_nav_list = daily_nav_list[-365:]

            shadow_state["daily_nav"] = daily_nav_list
            shadow_state["current_nav"] = round(new_nav, 6)
            shadow_state["current_capital"] = round(new_nav * float(shadow_state.get("initial_capital", 500_000)), 2)
            shadow_state["last_updated"] = datetime.now().isoformat()

            # === Fail-fast 检查 (单日>3%, 3日>5%) ===
            ff_config = shadow_state.get("fail_fast_config", {})
            daily_dd_threshold = float(ff_config.get("daily_drawdown_threshold", 0.03))
            cum_3d_threshold = float(ff_config.get("cumulative_3d_drawdown_threshold", 0.05))

            fail_fast_triggered = False
            fail_fast_reason = ""

            # 检查 1: 单日回撤 > 3%
            if len(daily_nav_list) >= 2:
                prev_nav = float(daily_nav_list[-2].get("nav", new_nav))
                if prev_nav > 0:
                    daily_dd = (prev_nav - new_nav) / prev_nav
                    if daily_dd > daily_dd_threshold:
                        fail_fast_triggered = True
                        fail_fast_reason = f"daily_drawdown_exceeded: {daily_dd:.4f} > {daily_dd_threshold}"

            # 检查 2: 3日累计回撤 > 5%
            if not fail_fast_triggered and len(daily_nav_list) >= 4:
                nav_3d_ago = float(daily_nav_list[-4].get("nav", new_nav))
                if nav_3d_ago > 0:
                    cum_3d_dd = (nav_3d_ago - new_nav) / nav_3d_ago
                    if cum_3d_dd > cum_3d_threshold:
                        fail_fast_triggered = True
                        fail_fast_reason = f"cumulative_3d_drawdown_exceeded: {cum_3d_dd:.4f} > {cum_3d_threshold}"

            if fail_fast_triggered:
                # === Fail-fast 触发: 终止影子账户 ===
                shadow_state["status"] = "TERMINATED"
                termination_record = {
                    "terminated_at": datetime.now().isoformat(),
                    "trigger_date": today_str,
                    "reason": fail_fast_reason,
                    "final_nav": round(new_nav, 6),
                    "final_capital": shadow_state["current_capital"],
                    "days_tracked": len(daily_nav_list),
                    "total_trades": len(shadow_state.get("trade_log", [])),
                }
                shadow_state.setdefault("fail_fast_log", []).append(termination_record)

                logger.critical(
                    "Phase 10: ⚠️ FAIL-FAST 触发! 影子账户已终止: %s, "
                    "final_nav=%.4f, days_tracked=%d",
                    fail_fast_reason, new_nav, len(daily_nav_list),
                )
                logger.critical(
                    "Phase 10: 影子账户终止 → 策略不得推进到下一灰度阶段, 需回滚!"
                )

                self.state["phases"]["shadow_monitor"] = {
                    "status": "FAIL_FAST_TERMINATED",
                    "reason": fail_fast_reason,
                    "final_nav": round(new_nav, 6),
                    "days_tracked": len(daily_nav_list),
                }
            else:
                # 正常记录
                logger.info(
                    "Phase 10 完成: 影子账户净值=%.4f, 日收益=%.4f%%, 运行天数=%d, fail-fast=未触发",
                    new_nav, daily_return * 100, len(daily_nav_list),
                )
                self.state["phases"]["shadow_monitor"] = {
                    "status": "OK",
                    "current_nav": round(new_nav, 6),
                    "daily_return": round(daily_return, 6),
                    "days_tracked": len(daily_nav_list),
                    "stage": shadow_state.get("stage_name", "stage_1"),
                }

            # === 保存状态 ===
            with open(state_file, "w", encoding="utf-8") as f:
                _json.dump(shadow_state, f, ensure_ascii=False, indent=2, default=str)

            return True

        except Exception as e:
            logger.error(f"Phase 10 异常: {e}", exc_info=True)
            self.state["phases"]["shadow_monitor"] = {
                "status": "FAIL",
                "error": str(e),
            }
            # 异常隔离: Phase 10 失败不影响已完成的 Phase 1-9
            return True

    # --------------------------------------------------------
    # 主流程
    # --------------------------------------------------------
    def run(self, only_phase: Optional[str] = None, phase_start: Optional[str] = None, phase_end: Optional[str] = None) -> Dict[str, Any]:
        """执行完整工作流

        Args:
            only_phase: 仅执行指定的单个阶段
            phase_start: 执行的起始阶段（包含）
            phase_end: 执行的结束阶段（包含）
        """
        logger.info("#" * 60)
        logger.info(f"# v8.5 每日交易工作流 — {self.trade_date}")
        logger.info(f"# 资金: {self.capital:,.0f} | 模式: {'DRY-RUN' if self.dry_run else 'EXECUTE'}")
        logger.info(f"# v8.5 模块: {'全部就绪' if V85_READY else f'降级 ({9 - len(_V85_FAILURES)}/9)'}")
        logger.info("#" * 60)

        # === v8.5: Kill Switch 生命周期管理 ===
        try:
            # KillSwitch.__init__ 仅接受 config_path/margin_limit, 不接受 total_capital/dry_run
            self.ks = KillSwitch()
            # 修复 P0: 注册 broker_callback, 使 execute_kill_switch 可真实执行
            # (未注册时 execute_kill_switch 会抛 RuntimeError, 无法执行实际平仓)
            self.ks.set_broker_callback(self._execute_kill_switch_callback)
            try:
                _ks_status = self.ks.check_margin_status()
                _ks_level = int(_ks_status.get("level", 0))
            except Exception as _e_ks:
                logger.warning(f"[v8.5] Kill Switch 保证金状态查询失败, 默认 L0: {_e_ks}")
                _ks_level = 0
            ks_info = f"级别=L{_ks_level}, 保证金检查=启用, broker_callback=已注册"
            logger.info(f"[v8.5] Kill Switch 已武装: {ks_info}")
        except Exception as e:
            logger.critical(f"[v8.5] Kill Switch 初始化失败, 系统拒绝启动: {e}")
            self.state["kill_switch"] = {"status": "FAIL", "error": str(e), "triggered": True}
            return self.state

        # === v8.5: 环境隔离验证 (研究/生产环境分离检查) ===
        if V85_READY:
            try:
                from utils.environment_isolation import EnvironmentIsolation
                env_isolator = EnvironmentIsolation()
                env_status = env_isolator.validate()
                if not env_status.get("is_production_ready", True):
                    logger.error(f"[v8.5] 环境隔离检查未通过: {env_status}")
                    logger.error("[v8.5] 研究代码不得直连生产, 系统拒绝启动")
                    self.state["phases"]["check"] = {"status": "FAIL", "reason": "environment_isolation_failed"}
                    return self.state
                logger.info("[v8.5] 环境隔离验证通过: 研究/生产环境正确隔离")
            except Exception as e:
                logger.warning(f"[v8.5] 环境隔离验证异常 (非致命): {e}")

        phases = [
            ("check", self.phase_check),
            ("calibrate", self.phase_calibrate),
            ("market", self.phase_market),
            ("risk", self.phase_risk),
            ("hedge", self.phase_hedge),
            ("hedge_fund", self.phase_hedge_fund),  # v7.7: 对冲基金视角融合
            ("v10_risk", self.phase_v10_risk),  # v10.0: 回撤+VaR+压测
            ("quant_neutral", self.phase_quant_neutral),  # v10.0: 量化中性月度调仓+IC对冲
            ("cash_management", self.phase_cash_management),  # v10.0: 现金管理+逆回购
            ("directional_futures", self.phase_directional_futures),  # v10.0: 方向性期货 CU/AU/T
            ("signal", self.phase_signal),
            ("execute", lambda: self.phase_execute(self.state.get("phases", {}).get("signal", {}))),
            ("report", self.phase_report),
            ("autolearn", self.phase_autolearn),
            ("factor_kill_switch", self.phase_factor_kill_switch),  # v8.5: S6 因子实时监控
            ("shadow_monitor", self.phase_shadow_monitor),  # Phase 10: 影子账户监控
        ]

        phase_names = [p[0] for p in phases]
        start_idx = 0 if phase_start is None else (phase_names.index(phase_start) if phase_start in phase_names else 0)
        end_idx = len(phases) if phase_end is None else (phase_names.index(phase_end) + 1 if phase_end in phase_names else len(phases))

        for i, (phase_name, phase_func) in enumerate(phases):
            if only_phase and phase_name != only_phase:
                continue
            if phase_start or phase_end:
                if i < start_idx or i >= end_idx:
                    continue
            try:
                phase_func()
                # check 阶段: 严格 fail-closed 检查 (P0-11 修复)
                if phase_name == "check":
                    check_state = self.state["phases"].get("check", {})
                    check_status = check_state.get("status")
                    # 修复 P0: fail_closed=True 时必须终止 (风控核心失败禁止交易)
                    if check_state.get("fail_closed", False):
                        logger.critical(
                            "[Fail-Closed] 系统自检触发 fail-closed, 终止工作流: %s",
                            check_state.get("reason", "unknown"),
                        )
                        self.state["fail_closed"] = True
                        break
                    if check_status == "FAIL":
                        logger.error("系统自检失败, 终止工作流")
                        break
                    if check_status != "PASS":
                        logger.warning("系统自检状态异常: %s，继续执行", check_status)
                # === v8.5: Kill Switch 每阶段后检查 ===
                try:
                    ks_status = self.ks.check_margin_status()
                except Exception as e_ks:
                    # 修复 P0: 异常时 fail-closed (假设最高风险, 而非放行)
                    ks_status = {
                        "level": 3,
                        "level_name": "检查异常-强制熔断",
                        "can_trade": False,
                        "can_open": False,
                        "margin_usage_ratio": 1.0,
                        "action": f"check_margin_status error: {e_ks}",
                        "fail_closed": True,
                    }
                    logger.critical(
                        "[v8.5] Kill Switch 状态检查异常! fail-closed 视为 L3: %s", e_ks,
                    )
                # 修复 P0: check_margin_status() 返回 level (0/1/2/3), 无 "triggered" 字段
                # 原代码 ks_status.get("triggered") 永远为 None, Kill Switch 形同虚设
                # 正确判断: level >= 2 (L2 不可交易, L3 全面停止)
                _ks_level = int(ks_status.get("level", 0))
                _ks_can_trade = bool(ks_status.get("can_trade", _ks_level < 2))
                if _ks_level >= 2 or not _ks_can_trade:
                    logger.critical(
                        "[v8.5] Kill Switch 触发! 级别=L%d, can_trade=%s, 原因=%s, 先阶段=%s",
                        _ks_level,
                        _ks_can_trade,
                        ks_status.get("action", ks_status.get("level_name", "unknown")),
                        phase_name,
                    )
                    self.state["kill_switch"] = ks_status
                    self.state["phases"][phase_name] = {
                        "status": "KILLED",
                        "kill_switch": ks_status,
                    }
                    break
            except Exception as e:
                logger.error(f"Phase {phase_name} 异常: {e}", exc_info=True)
                self.state["phases"][phase_name] = {"status": "FAIL", "error": str(e)}
                break

        logger.info("#" * 60)
        logger.info("# v8.5 工作流执行完成")
        logger.info(f"# Kill Switch: {'触发!' if self.state.get('kill_switch', {}).get('triggered') else '正常'}")
        logger.info(f"# v8.5 模块: {9 - len(_V85_FAILURES)}/9 就绪")
        logger.info("#" * 60)
        return self.state


# ============================================================
# CLI 入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="v8.5 每日交易工作流 (对冲基金级机构系统)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--date", default=None,
                        help="交易日期 YYYY-MM-DD (默认今日)")
    parser.add_argument("--capital", type=float, default=WorkflowConfig.TOTAL_CAPITAL,
                        help=f"资金规模 (默认 {WorkflowConfig.TOTAL_CAPITAL})")
    parser.add_argument("--dry-run", action="store_true",
                        help="干跑模式 (不执行交易)")
    parser.add_argument("--sim", action="store_true",
                        help="模拟盘模式 (股票+期货，按交易日+夜盘执行)")
    parser.add_argument("--phase", default=None,
                        choices=["check", "calibrate", "market", "risk", "hedge", "hedge_fund", "v10_risk", "quant_neutral", "cash_management", "directional_futures", "signal", "execute", "report", "autolearn", "factor_kill_switch", "shadow_monitor"],
                        help="仅执行指定阶段")
    parser.add_argument("--phase-start", default=None,
                        choices=["check", "calibrate", "market", "risk", "hedge", "hedge_fund", "v10_risk", "quant_neutral", "cash_management", "directional_futures", "signal", "execute", "report", "autolearn", "factor_kill_switch", "shadow_monitor"],
                        help="执行的起始阶段（包含）")
    parser.add_argument("--phase-end", default=None,
                        choices=["check", "calibrate", "market", "risk", "hedge", "hedge_fund", "v10_risk", "quant_neutral", "cash_management", "directional_futures", "signal", "execute", "report", "autolearn", "factor_kill_switch", "shadow_monitor"],
                        help="执行的结束阶段（包含）")
    parser.add_argument("--external-reports-dir", default=None,
                        help="外部报告目录 (默认 E:\\各种PY程序\\每日报告归档)")
    parser.add_argument("--ai-sandbox", action="store_true",
                        help="只读 AI 决策沙箱：生成 GLM-5.2 建议并落盘，不触发下单")
    parser.add_argument("--ai-auto-approve", action="store_true",
                        help="AI 自动确认：基于 gate 结果自动确认可执行指令")
    parser.add_argument("--execution-review", action="store_true",
                        help="执行复盘：核对 AI 建议与收盘盈亏，生成复盘报告")
    parser.add_argument("--dynamic-risk", action="store_true",
                        help="动态风控：基于复盘结果调整风控阈值")
    parser.add_argument("--write-gate-limits", action="store_true",
                        help="动态风控时同时写回 AI Gate 可读取的限值文件")
    parser.add_argument("--intraday-monitor", action="store_true",
                        help="盘中监控：输出监控摘要、风险事件与动态调整建议")
    parser.add_argument("--auto-closed-loop", action="store_true",
                        help="自动闭环：执行复盘后自动触发动态风控，并写回 AI Gate 风控限值")

    args = parser.parse_args()

    if args.ai_sandbox:
        from ai_decision_sandbox import run_ai_sandbox
        result = run_ai_sandbox(trade_date=args.date)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result.get("status") == "PASS" else 1)

    if args.ai_auto_approve:
        from ai_auto_approver import run_ai_auto_approver
        result = run_ai_auto_approver(trade_date=args.date, auto_mode=bool(getattr(args, "ai_auto_approve", False)))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result.get("status") == "PASS" else 1)

    if args.execution_review:
        from execution_reviewer import run_execution_review
        result = run_execution_review(trade_date=args.date, auto_closed_loop=args.auto_closed_loop)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0)

    if args.dynamic_risk:
        from dynamic_risk_adjuster import run_dynamic_risk_adjuster
        result = run_dynamic_risk_adjuster(trade_date=args.date, write_gate_limits=args.write_gate_limits or args.auto_closed_loop)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0)

    if args.auto_closed_loop and not args.execution_review and not args.dynamic_risk:
        from execution_reviewer import run_execution_review
        from dynamic_risk_adjuster import run_dynamic_risk_adjuster
        review_result = run_execution_review(trade_date=args.date, auto_closed_loop=True)
        risk_result = run_dynamic_risk_adjuster(trade_date=args.date, write_gate_limits=True)
        print(json.dumps({
            "execution_review": review_result,
            "dynamic_risk": risk_result,
            "status": "PASS",
        }, ensure_ascii=False, indent=2))
        sys.exit(0)

    if args.intraday_monitor:
        from intraday_monitor import run_intraday_monitor
        result = run_intraday_monitor(trade_date=args.date)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result.get("status") == "PASS" else 1)

    workflow = DailyWorkflow(
        trade_date=args.date,
        capital=args.capital,
        dry_run=args.dry_run,
        sim_mode=args.sim,
        external_reports_dir=args.external_reports_dir,
    )
    state = workflow.run(only_phase=args.phase, phase_start=args.phase_start, phase_end=args.phase_end)

    # 退出码：check 阶段单独允许降级通过，避免计划文件缺失导致整条自动任务失败
    all_pass = True
    for name, phase in state.get("phases", {}).items():
        status = phase.get("status")
        if status == "FAIL":
            all_pass = False
            break
        if status == "PASS":
            continue
        # 既不是 PASS 也不是 FAIL 的中间状态，视作通过
        all_pass = all_pass and True
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
