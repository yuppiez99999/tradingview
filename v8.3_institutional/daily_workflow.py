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

用法:
    python daily_workflow.py                           # 执行今日 workflow
    python daily_workflow.py --date 2026-07-06         # 指定日期
    python daily_workflow.py --dry-run                 # 干跑模式 (不执行交易)
    python daily_workflow.py --phase check             # 仅执行某阶段
    python daily_workflow.py --phase calibrate         # 仅执行收益预测校准
    python daily_workflow.py --phase autolearn         # 仅执行自主学习训练
"""

from __future__ import annotations

import os
import sys
import json
import logging
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

# ============================================================
# 路径初始化 (兼容 Python 3.8)
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "src"
sys.path.insert(0, str(SRC_DIR))
sys.path.insert(0, str(BASE_DIR.parent))  # 兼容 v7.4 模块
sys.path.insert(0, str(BASE_DIR / "data"))  # EDB 期货数据模块
sys.path.insert(0, str(BASE_DIR))  # workflow 子包 (拆分后 phase 模块导入)

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
try:
    from risk.risk_manager import RiskManager
    from risk.circuit_breaker import CircuitBreaker, CircuitLevel
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
except ImportError as e:
    logger.warning(f"v7.5 模块导入失败, 降级模式: {e}")
    V75_READY = False

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
try:
    from utils.theta_engine import ThetaEngine
    from utils.gamma_engine import GammaEngine
    from utils.kill_switch import KillSwitch
    from utils.liquidation_scheduler import LiquidationScheduler
    HEDGE_FUND_MODULES_READY = True
    logger.info("对冲基金模块加载成功: Theta/Gamma/KillSwitch/LiquidationScheduler")
except ImportError as e:
    logger.warning(f"对冲基金模块导入失败 (降级模式): {e}")
    HEDGE_FUND_MODULES_READY = False

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

# 导入 v7.4 神华建仓计划 (可选)
try:
    # 优先尝试 v7.4 模块
    sys.path.insert(0, str(BASE_DIR.parent / "_archive_dead_code"))
    from generate_shenhua_build_plan import (
        SHENHUA_CODE, SHENHUA_NAME, BUILD_TIERS, BUILD_PHASES,
        RISK_PARAMS, EXECUTION_RULES, VALUATION_SNAPSHOT,
        calc_lots, build_position_plan,
    )
    SHENHUA_READY = True
except ImportError:
    SHENHUA_READY = False
    # 降级: 内置神华配置
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

    def calc_lots(target_amount: float, price: float, min_lots: int = 100) -> int:
        raw_shares = target_amount / price
        lots = int(raw_shares // min_lots) * min_lots
        return max(lots, min_lots)


# ============================================================
# 工作流配置 (基于 4 份权威文档 — 300万股票 + 200万期权对冲)
# ============================================================
class WorkflowConfig:
    """工作流配置 — 基于 2026 年交易计划

    数据源:
    1. 2026年交易计划.md — 500万 4阶段建仓计划
    2. portfolio.yaml — 组合配置
    3. trade_plan_{date}.json — 当日执行计划
    """

    # === 资金配置 (500万 = 300万股票 + 200万对冲) ===
    TOTAL_CAPITAL = 5_000_000          # 总资金 500 万
    STOCK_CAPITAL = 3_000_000          # 股票组合 300 万 (60%)
    HEDGE_CAPITAL = 1_060_000          # 对冲资金 106 万 (21.2%)

    # === 股票组合分类 (300万) ===
    STOCK_CATEGORIES = {
        "核心宽基ETF": {"weight": 0.28, "amount": 840_000},
        "科技成长个股": {"weight": 0.20, "amount": 600_000},
        "高端制造/基建": {"weight": 0.20, "amount": 600_000},
        "防御/红利": {"weight": 0.15, "amount": 450_000},
        "商品/避险": {"weight": 0.05, "amount": 150_000},
        "现金缓冲": {"weight": 0.08, "amount": 240_000},
    }

    # === 对冲分类 (200万) ===
    HEDGE_CATEGORIES = {
        "期货对冲": {"weight": 0.30, "amount": 600_000},
        "股票期权保护": {"weight": 0.25, "amount": 500_000},
        "避险资产": {"weight": 0.15, "amount": 300_000},
        "现金缓冲": {"weight": 0.30, "amount": 600_000},
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

        # Alpha 信号融合（可选）
        self.signal_fusion = None
        try:
            from alpha.signal_fusion import SignalFusion
            self.signal_fusion = SignalFusion()
        except Exception:
            pass

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
            logger.warning("iFinD 新闻资讯模块导入失败: %s", exc)
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
                except Exception:
                    sim_date = None
                self.current_phase_info = self.phase_manager.get_current_phase(sim_date)
                logger.info(
                    f"十五五阶段: {self.current_phase_info.year} {self.current_phase_info.phase_name} "
                    f"(目标 {self.current_phase_info.target_return:.0%}, "
                    f"回撤限 {self.current_phase_info.max_drawdown:.0%}, "
                    f"杠杆 {self.current_phase_info.leverage_target}x)"
                )
            except Exception as exc:
                logger.warning("PhaseManager 初始化失败: %s", exc)
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
                logger.warning("对冲基金模块初始化失败: %s", exc)

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
                logger.warning("机构级模块初始化失败: %s", exc)

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
                logger.warning("风险管理模块初始化失败: %s", exc)

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
                logger.warning("Alpha 生成模块初始化失败: %s", exc)

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
                logger.warning("执行层模块初始化失败: %s", exc)

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
                logger.warning("另类数据模块初始化失败: %s", exc)

        # 市场数据提供器 (真实行情回退)
        self.market_data_provider = None
        try:
            from utils.data_provider import MarketDataProvider
            self.market_data_provider = MarketDataProvider()
            logger.info("MarketDataProvider 已初始化")
        except Exception as exc:
            logger.warning("MarketDataProvider 初始化失败: %s", exc)

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

    def _get_edb_futures_data(self, names=None) -> Dict[str, Dict[str, Any]]:
        """获取 EDB 期货/商品数据 (委托至 workflow.phases.hedge)"""
        from workflow.phases.hedge import _get_edb_futures_data as _impl
        return _impl(self._build_context(), names)

    def _get_futures_scanner_summary(self) -> Dict[str, Dict[str, Any]]:
        """期货期权扫描器汇总 (委托至 workflow.phases.hedge)"""
        from workflow.phases.hedge import _get_futures_scanner_summary as _impl
        return _impl()

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
    def _build_context(self):
        """构造 WorkflowContext, 供拆分出的 phase 子模块使用"""
        from workflow.context import WorkflowContext
        return WorkflowContext(self)

    def phase_check(self) -> bool:
        """系统自检 (委托至 workflow.phases.check)"""
        from workflow.phases.check import phase_check as _phase_check
        ctx = self._build_context()
        return _phase_check(ctx)

    # --------------------------------------------------------
    # Phase 1.5: 收益预测动态校准 (新增)
    #   - Step 1: Wind MCP 拉取最新日K, 更新 returns_history.json + market_returns.json
    #   - Step 2: 计算已实现年化收益率
    #   - Step 3: 校准 portfolio_return_projection.json 概率权重
    # --------------------------------------------------------
    def phase_calibrate(self) -> bool:
        """收益预测动态校准 (委托至 workflow.phases.calibrate)"""
        from workflow.phases.calibrate import phase_calibrate as _phase_calibrate
        ctx = self._build_context()
        return _phase_calibrate(ctx)

    # --------------------------------------------------------
    # Phase 2: 市场状态评估
    # --------------------------------------------------------
    def phase_market(self) -> CircuitLevel:
        """市场状态评估 (委托至 workflow.phases.market)"""
        from workflow.phases.market import phase_market as _phase_market
        ctx = self._build_context()
        return _phase_market(ctx)

    # --------------------------------------------------------
    # Phase 3: 风险预算计算 (组合级别 — 500万 4阶段)
    # --------------------------------------------------------
    def phase_risk(self) -> Dict[str, Any]:
        """风险预算计算 (委托至 workflow.phases.risk)"""
        from workflow.phases.risk import phase_risk as _phase_risk
        ctx = self._build_context()
        return _phase_risk(ctx)

    # --------------------------------------------------------
    # Phase 4: 对冲评估 + 自动执行
    # --------------------------------------------------------
    def _infer_style_from_code(self, code: str) -> str:
        """基于代码前缀推断持仓风格 (委托至 workflow.phases.risk)"""
        from workflow.phases.risk import _infer_style_from_code as _impl
        return _impl(code)

    def _style_beta_proxy(self,
                          positions: Dict[str, float],
                          prices: Dict[str, float]) -> float:
        """风格 Beta 代理 (委托至 workflow.phases.risk)"""
        from workflow.phases.risk import _style_beta_proxy as _impl
        return _impl(positions, prices)

    def _get_if_realtime(self) -> dict:
        """获取 IF 期货实时价 (委托至 workflow.phases.risk)"""
        from workflow.phases.risk import _get_if_realtime as _impl
        return _impl()

    def _compute_beta_hedge_order(self, portfolio_beta: float, portfolio_value: float, degraded: bool = False) -> Dict[str, object]:
        """基于 BetaHedger 计算对冲指令 (委托至 workflow.phases.hedge)"""
        from workflow.phases.hedge import _compute_beta_hedge_order as _impl
        return _impl(portfolio_beta, portfolio_value, degraded)


    def phase_hedge(self) -> Dict[str, Any]:
        """三联对冲评估 + 自动执行 (委托至 workflow.phases.hedge)"""
        from workflow.phases.hedge import phase_hedge as _phase_hedge
        ctx = self._build_context()
        return _phase_hedge(ctx)

    def _execute_sim_hedge_orders(self, orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """模拟盘对冲订单路由 (委托至 workflow.phases.hedge)

        新增方法 (第 3 轮拆分): 符合 test_phase_hedge_sim_branch.py 规约,
        将对冲订单按 action 路由到 sim_engine 的 futures/options/stock broker。
        """
        from workflow.phases.hedge import _execute_sim_hedge_orders as _impl
        mock_prices = getattr(self.config, "MOCK_PRICES", {}) if self.config is not None else {}
        return _impl(getattr(self, "sim_engine", None), mock_prices, orders)


    # --------------------------------------------------------
    # Phase 4.5: 对冲基金视角融合 (v7.7)
    #   - Theta引擎: 月度Covered Call计划生成 + 滚仓检查
    #   - Gamma/Vega引擎: MA60/IV分位监控 + 尾部对冲触发
    #   - 三级熔断: 保证金占用率检查 + 自动执行
    #   - 2030清仓协议: 阶段切换 + 预警
    # --------------------------------------------------------
    def phase_hedge_fund(self) -> Dict[str, Any]:
        """对冲基金视角融合 (委托至 workflow.phases.hedge_fund)"""
        from workflow.phases.hedge_fund import phase_hedge_fund as _phase_hedge_fund
        ctx = self._build_context()
        return _phase_hedge_fund(ctx)


    # --------------------------------------------------------
    # Phase 4.6: v10.0 风控 (回撤控制 + VaR 监控 + 压力测试)
    # --------------------------------------------------------
    def phase_v10_risk(self) -> Dict[str, Any]:
        """v10.0 风控 (委托至 workflow.phases.v10_risk)"""
        from workflow.phases.v10_risk import phase_v10_risk as _phase_v10_risk
        ctx = self._build_context()
        return _phase_v10_risk(ctx)

    def _get_portfolio_positions_for_stress_test(self) -> List[Dict]:
        """获取压力测试持仓 (委托至 workflow.phases.v10_risk)"""
        from workflow.phases.v10_risk import _get_portfolio_positions_for_stress_test as _impl
        return _impl()

    # --------------------------------------------------------
    # Phase 4.7: 量化市场中性策略 (月度调仓 + IC 对冲)
    # --------------------------------------------------------
    def phase_quant_neutral(self) -> Dict[str, Any]:
        """量化市场中性策略 (委托至 workflow.phases.quant_neutral)"""
        from workflow.phases.quant_neutral import phase_quant_neutral as _phase_quant_neutral
        ctx = self._build_context()
        return _phase_quant_neutral(ctx)

    def _load_quant_neutral_holdings(self) -> List[Dict]:
        """加载量化中性多头持仓 (委托至 workflow.phases.quant_neutral)"""
        from workflow.phases.quant_neutral import _load_quant_neutral_holdings as _impl
        return _impl()

    def _get_ic_price(self) -> float:
        """获取 IC 期货价格 (委托至 workflow.phases.quant_neutral)"""
        from workflow.phases.quant_neutral import _get_ic_price as _impl
        return _impl()

    def _get_ic_basis(self) -> Optional[float]:
        """获取 IC 基差 (委托至 workflow.phases.quant_neutral)"""
        from workflow.phases.quant_neutral import _get_ic_basis as _impl
        return _impl()

    def _get_current_ic_contracts(self) -> int:
        """获取 IC 空头合约数 (委托至 workflow.phases.quant_neutral)"""
        from workflow.phases.quant_neutral import _get_current_ic_contracts as _impl
        return _impl()

    def _load_strategy_drawdown_state(self, strategy_name: str) -> Tuple[float, float, int]:
        """加载策略回撤状态 (委托至 workflow.phases.quant_neutral)"""
        from workflow.phases.quant_neutral import _load_strategy_drawdown_state as _impl
        return _impl(strategy_name)


    # --------------------------------------------------------
    # Phase 4.8: 现金管理 (逆回购 + 货基 + 应急金监控)
    # --------------------------------------------------------
    def phase_cash_management(self) -> Dict[str, Any]:
        """现金管理 (委托至 workflow.phases.cash_management)"""
        from workflow.phases.cash_management import phase_cash_management as _phase_cash_management
        ctx = self._build_context()
        return _phase_cash_management(ctx)

    # --------------------------------------------------------
    # Phase 4.9: 方向性期货交易 (CU/AU/T 三品种方向性交易)
    # --------------------------------------------------------
    def phase_directional_futures(self) -> Dict[str, Any]:
        """方向性期货交易 (委托至 workflow.phases.directional_futures)"""
        from workflow.phases.directional_futures import phase_directional_futures as _phase_directional_futures
        ctx = self._build_context()
        return _phase_directional_futures(ctx)

    # --------------------------------------------------------
    # Phase 5: 信号生成 (2026 交易计划订单) -- 拆分至 workflow/phases/signal*.py
    # --------------------------------------------------------
    def phase_signal(self) -> Dict[str, Any]:
        """信号生成 -- 从 trade_plan 加载订单 (委托至 workflow.phases.signal)"""
        from workflow.phases.signal import phase_signal as _phase_signal
        ctx = self._build_context()
        return _phase_signal(ctx)

    @staticmethod
    def _qlib_signal_to_factor(signal_value: float) -> float:
        """Qlib 信号 -> 订单调整系数 (委托至 workflow.phases.signal_qlib)"""
        from workflow.phases.signal_qlib import qlib_signal_to_factor
        return qlib_signal_to_factor(signal_value)

    def _qlib_signals_to_adjustments(self, qlib_signals, *, morning_orders, afternoon_orders):
        """按 Qlib 信号调整订单 (委托至 workflow.phases.signal_qlib)"""
        from workflow.phases.signal_qlib import qlib_signals_to_adjustments
        return qlib_signals_to_adjustments(qlib_signals, morning_orders=morning_orders, afternoon_orders=afternoon_orders)

    @staticmethod
    def _ifind_signal_to_factor(direction: str, confidence: float) -> float:
        """iFinD 研判 -> 订单调整系数 (委托至 workflow.phases.signal_ifind)"""
        from workflow.phases.signal_ifind import ifind_signal_to_factor
        return ifind_signal_to_factor(direction, confidence)

    @staticmethod
    def _fuse_qlib_ifind_factor(qlib_factor, ifind_factor, *, qlib_weight=0.6, ifind_weight=0.4, clamp_min=0.5, clamp_max=1.3):
        """融合 Qlib + iFinD 因子 (委托至 workflow.phases.signal)"""
        from workflow.phases.signal import fuse_qlib_ifind_factor
        return fuse_qlib_ifind_factor(qlib_factor, ifind_factor, qlib_weight=qlib_weight, ifind_weight=ifind_weight, clamp_min=clamp_min, clamp_max=clamp_max)

    def _load_lgb_enhanced_signals(self) -> Dict[str, Dict[str, Any]]:
        """加载 LGB 增强信号 (委托至 workflow.phases.signal_lgb)"""
        from workflow.phases.signal_lgb import load_lgb_enhanced_signals
        return load_lgb_enhanced_signals()

    @staticmethod
    def _lgb_confidence_multiplier(signal_value, quality_flag="OK"):
        """LGB 信号 -> 置信度乘数 (委托至 workflow.phases.signal_lgb)"""
        from workflow.phases.signal_lgb import lgb_confidence_multiplier
        return lgb_confidence_multiplier(signal_value, quality_flag)

    def _apply_fused_qlib_ifind_adjustments(self, *, morning_orders, afternoon_orders, qlib_signals, ifind_insights, external_factor=1.0, regime_weights=None, lgb_signals=None):
        """四源融合调整订单 (委托至 workflow.phases.signal)"""
        from workflow.phases.signal import apply_fused_qlib_ifind_adjustments
        ctx = self._build_context()
        return apply_fused_qlib_ifind_adjustments(ctx, morning_orders=morning_orders, afternoon_orders=afternoon_orders, qlib_signals=qlib_signals, ifind_insights=ifind_insights, external_factor=external_factor, regime_weights=regime_weights, lgb_signals=lgb_signals)

    def _apply_ifind_news_adjustments(self, *, morning_orders, afternoon_orders):
        """iFinD 新闻调整订单 (委托至 workflow.phases.signal_ifind)"""
        from workflow.phases.signal_ifind import apply_ifind_news_adjustments
        ctx = self._build_context()
        return apply_ifind_news_adjustments(ctx, morning_orders=morning_orders, afternoon_orders=afternoon_orders)

    def _apply_macro_policy_adjustments(self, *, morning_orders, afternoon_orders, macro_scores):
        """宏观政策评分调整 (委托至 workflow.phases.signal_ifind)"""
        from workflow.phases.signal_ifind import apply_macro_policy_adjustments
        return apply_macro_policy_adjustments(morning_orders=morning_orders, afternoon_orders=afternoon_orders, macro_scores=macro_scores)

    def _apply_position_factor(self, orders, factor):
        """DEFENSE 模式仓位系数调整 (委托至 workflow.phases.signal)"""
        from workflow.phases.signal import apply_position_factor
        return apply_position_factor(orders, factor)

    def _generate_qlib_signals(self) -> Dict[str, float]:
        """生成 Qlib 深度学习信号 (委托至 workflow.phases.signal_qlib)"""
        from workflow.phases.signal_qlib import generate_qlib_signals
        ctx = self._build_context()
        return generate_qlib_signals(ctx)

    def _generate_mock_ohlcv(self, symbol: str, days: int = 120):
        """生成模拟 OHLCV 数据 (委托至 workflow.phases.signal_qlib)"""
        from workflow.phases.signal_qlib import generate_mock_ohlcv
        return generate_mock_ohlcv(symbol, days)

    def _options_market_snapshot(self) -> Dict[str, Any]:
        """期权市场快照 (委托至 workflow.phases.signal_ifind)"""
        from workflow.phases.signal_ifind import options_market_snapshot
        return options_market_snapshot()


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

        # === 信号校验 ===
        action = signal.get("action", "")
        morning_orders = signal.get("morning_orders", [])
        afternoon_orders = signal.get("afternoon_orders", [])
        has_orders = bool(morning_orders or afternoon_orders)

        # 允许直接从 trade_plan 回退读单，避免 --phase execute 跳过 phase_signal 时空跑
        if not has_orders and self.trade_plan:
            plan_exec = self.trade_plan.get("execution_plan", {})
            morning_orders = plan_exec.get("morning_orders", []) or []
            afternoon_orders = plan_exec.get("afternoon_orders", []) or []
            has_orders = bool(morning_orders or afternoon_orders)
            if has_orders:
                logger.info("phase_signal 未提供订单，已从 trade_plan 回退加载 %d 笔", len(morning_orders) + len(afternoon_orders))

        if action not in ("BUILD_PLAN",) and not has_orders:
            logger.info("信号动作 %s, 无建仓订单, 跳过执行", action)
            self.state["phases"]["execute"] = {
                "status": "PASS",
                "fills": [],
                "action": action,
                "options_fills": options_fills,
                "options_count": len(options_fills),
            }
            return []

        if not has_orders:
            logger.info("无订单可执行")
            self.state["phases"]["execute"] = {
                "status": "PASS",
                "fills": [],
                "options_fills": options_fills,
                "options_count": len(options_fills),
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
            _split_total = 0
            _split_fail = 0
            if self.exec_algo_engine is not None:
                try:
                    for order in morning_orders + afternoon_orders:
                        shares = int(order.get("shares", 0))
                        est_price = float(order.get("est_price", 0))
                        code = str(order.get("code", ""))
                        notional = shares * est_price
                        if shares >= 5000 or notional >= 200_000:
                            try:
                                _split_total += 1
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
                                    "first_slice_shares": plan.slices[0].target_shares if plan.slices else 0,
                                    "last_slice_shares": plan.slices[-1].target_shares if plan.slices else 0,
                                    "est_total_cost": plan.expected_cost,
                                    "est_slippage_bps": plan.expected_slippage_bps,
                                    "plan_path": str(saved_path),
                                })
                                logger.info(
                                    "[ExecAlgo] %s 拆单: %s -> %d slices (slippage=%.1fbps, cost=%.0f)",
                                    code, algo_type.value, len(plan.slices),
                                    plan.expected_slippage_bps, plan.expected_cost,
                                )
                            except Exception as exc:
                                _split_fail += 1
                                logger.error("[ExecAlgo] %s 拆单失败: %s", code, exc, exc_info=True)
                        else:
                            # G12 修复 (2026-08-06): 小单也估算冲击成本, 裸市价仅限极小单
                            _small_notional = notional
                            _est_slippage_bps = max(2.0, _small_notional / 1_000_000 * 5.0)  # 简化冲击估算
                            if _est_slippage_bps > 10.0:
                                # 冲击成本 > 10bp 的小单也走 TWAP 拆分
                                try:
                                    _split_total += 1
                                    _plan = self.exec_algo_engine.plan_order(
                                        algo="TWAP",
                                        symbol=code,
                                        side=str(order.get("side", "BUY")).upper(),
                                        total_shares=shares,
                                        duration_minutes=30,
                                        slice_minutes=5,
                                        current_price=est_price,
                                    )
                                    _saved = self.exec_algo_engine.save_plan(_plan)
                                    execution_plans.append({
                                        "symbol": code,
                                        "algo": "TWAP",
                                        "slices": len(_plan.slices),
                                        "first_slice_shares": _plan.slices[0].target_shares if _plan.slices else 0,
                                        "last_slice_shares": _plan.slices[-1].target_shares if _plan.slices else 0,
                                        "est_total_cost": _plan.expected_cost,
                                        "est_slippage_bps": _plan.expected_slippage_bps,
                                        "plan_path": str(_saved),
                                        "small_order_twap": True,
                                    })
                                    logger.info(
                                        "[ExecAlgo] %s 小单TWAP: %d slices (slippage=%.1fbps)",
                                        code, len(_plan.slices), _plan.expected_slippage_bps,
                                    )
                                except Exception as exc:
                                    _split_fail += 1
                                    logger.error("[ExecAlgo] %s 小单TWAP失败: %s", code, exc, exc_info=True)
                            else:
                                execution_plans.append({
                                    "symbol": code,
                                    "algo": "MARKET",
                                    "slices": 1,
                                    "first_slice_shares": shares,
                                    "last_slice_shares": shares,
                                    "est_total_cost": _small_notional,
                                    "est_slippage_bps": _est_slippage_bps,
                                    "plan_path": None,
                                    "small_order_market": True,
                                })
                                logger.debug(
                                    "[ExecAlgo] %s 小单市价 (notional=%.0f, slippage=%.1fbps)",
                                    code, _small_notional, _est_slippage_bps,
                                )
                    if _split_total > 0:
                        _fail_rate = _split_fail / _split_total
                        logger.info(
                            "[ExecAlgo] 拆单统计: 成功 %d/%d, 失败 %d (%.1f%%)",
                            _split_total - _split_fail, _split_total, _split_fail, _fail_rate * 100,
                        )
                        if _fail_rate > 0.5:
                            logger.error(
                                "[ExecAlgo] 拆单失败率 %.1f%% > 50%% 阈值, 执行质量降级",
                                _fail_rate * 100,
                            )
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
                "split_total": _split_total,
                "split_fail": _split_fail,
                "split_failure_rate": (_split_fail / _split_total) if _split_total > 0 else 0.0,
            }
            return dry_orders

        # === 对冲基金视角: 执行算法引擎 (大单拆单计划) ===
        execution_plans: List[Dict[str, Any]] = []
        _split_total = 0
        _split_fail = 0
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
                            _split_total += 1
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
                                "first_slice_shares": plan.slices[0].target_shares if plan.slices else 0,
                                "last_slice_shares": plan.slices[-1].target_shares if plan.slices else 0,
                                "est_total_cost": plan.expected_cost,
                                "est_slippage_bps": plan.expected_slippage_bps,
                                "plan_path": str(saved_path),
                            })
                            logger.info(
                                "[ExecAlgo] %s 拆单: %s -> %d slices (slippage=%.1fbps, cost=%.0f)",
                                code, algo_type.value, len(plan.slices),
                                plan.expected_slippage_bps, plan.expected_cost,
                            )
                        except Exception as exc:
                            _split_fail += 1
                            logger.error("[ExecAlgo] %s 拆单失败: %s", code, exc, exc_info=True)
                    else:
                        # G12 修复 (2026-08-06): 小单也估算冲击成本, 裸市价仅限极小单
                        _small_notional = notional
                        _est_slippage_bps = max(2.0, _small_notional / 1_000_000 * 5.0)
                        if _est_slippage_bps > 10.0:
                            try:
                                _split_total += 1
                                _plan = self.exec_algo_engine.plan_order(
                                    algo="TWAP",
                                    symbol=code,
                                    side=str(order.get("side", "BUY")).upper(),
                                    total_shares=shares,
                                    duration_minutes=30,
                                    slice_minutes=5,
                                    current_price=est_price,
                                )
                                _saved = self.exec_algo_engine.save_plan(_plan)
                                execution_plans.append({
                                    "symbol": code,
                                    "algo": "TWAP",
                                    "slices": len(_plan.slices),
                                    "first_slice_shares": _plan.slices[0].target_shares if _plan.slices else 0,
                                    "last_slice_shares": _plan.slices[-1].target_shares if _plan.slices else 0,
                                    "est_total_cost": _plan.expected_cost,
                                    "est_slippage_bps": _plan.expected_slippage_bps,
                                    "plan_path": str(_saved),
                                    "small_order_twap": True,
                                })
                                logger.info(
                                    "[ExecAlgo] %s 小单TWAP: %d slices (slippage=%.1fbps)",
                                    code, len(_plan.slices), _plan.expected_slippage_bps,
                                )
                            except Exception as exc:
                                _split_fail += 1
                                logger.error("[ExecAlgo] %s 小单TWAP失败: %s", code, exc, exc_info=True)
                        else:
                            execution_plans.append({
                                "symbol": code,
                                "algo": "MARKET",
                                "slices": 1,
                                "first_slice_shares": shares,
                                "last_slice_shares": shares,
                                "est_total_cost": _small_notional,
                                "est_slippage_bps": _est_slippage_bps,
                                "plan_path": None,
                                "small_order_market": True,
                            })
                            logger.debug(
                                "[ExecAlgo] %s 小单市价 (notional=%.0f, slippage=%.1fbps)",
                                code, _small_notional, _est_slippage_bps,
                            )
                if _split_total > 0:
                    _fail_rate = _split_fail / _split_total
                    logger.info(
                        "[ExecAlgo] 拆单统计: 成功 %d/%d, 失败 %d (%.1f%%)",
                        _split_total - _split_fail, _split_total, _split_fail, _fail_rate * 100,
                    )
                    if _fail_rate > 0.5:
                        logger.error(
                            "[ExecAlgo] 拆单失败率 %.1f%% > 50%% 阈值, 执行质量降级",
                            _fail_rate * 100,
                        )
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
                "split_total": _split_total,
                "split_fail": _split_fail,
                "split_failure_rate": (_split_fail / _split_total) if _split_total > 0 else 0.0,
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
                "split_total": _split_total,
                "split_fail": _split_fail,
                "split_failure_rate": (_split_fail / _split_total) if _split_total > 0 else 0.0,
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

        report_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"报告已生成: {report_path}")

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
        """自主学习量化训练 (委托至 workflow.phases.autolearn)"""
        from workflow.phases.autolearn import phase_autolearn as _phase_autolearn
        ctx = self._build_context()
        return _phase_autolearn(ctx)

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
        logger.info(f"# v7.5 每日交易工作流 — {self.trade_date}")
        logger.info(f"# 资金: {self.capital:,.0f} | 模式: {'DRY-RUN' if self.dry_run else 'EXECUTE'}")
        logger.info("#" * 60)

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
                # check 阶段不再阻断后续流程；允许降级继续执行
                if phase_name == "check":
                    check_status = self.state["phases"].get("check", {}).get("status")
                    if check_status == "FAIL":
                        logger.error("系统自检失败, 终止工作流")
                        break
                    if check_status != "PASS":
                        logger.warning("系统自检状态异常: %s，继续执行", check_status)
            except Exception as e:
                logger.error(f"Phase {phase_name} 异常: {e}", exc_info=True)
                self.state["phases"][phase_name] = {"status": "FAIL", "error": str(e)}
                break

        logger.info("#" * 60)
        logger.info("# 工作流执行完成")
        logger.info("#" * 60)
        return self.state


# ============================================================
# CLI 入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="v7.5 每日交易工作流",
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
                        choices=["check", "calibrate", "market", "risk", "hedge", "hedge_fund", "v10_risk", "quant_neutral", "cash_management", "directional_futures", "signal", "execute", "report", "autolearn"],
                        help="仅执行指定阶段")
    parser.add_argument("--phase-start", default=None,
                        choices=["check", "calibrate", "market", "risk", "hedge", "hedge_fund", "v10_risk", "quant_neutral", "cash_management", "directional_futures", "signal", "execute", "report", "autolearn"],
                        help="执行的起始阶段（包含）")
    parser.add_argument("--phase-end", default=None,
                        choices=["check", "calibrate", "market", "risk", "hedge", "hedge_fund", "v10_risk", "quant_neutral", "cash_management", "directional_futures", "signal", "execute", "report", "autolearn"],
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
