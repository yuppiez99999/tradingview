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

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from workflow.context import WorkflowContext

# ============================================================
# 路径初始化 (兼容 Python 3.8)
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "src"
sys.path.insert(0, str(SRC_DIR))
sys.path.insert(0, str(BASE_DIR.parent))  # 兼容 v7.4 模块
sys.path.insert(0, str(BASE_DIR / "data"))  # EDB 期货数据模块
sys.path.insert(0, str(BASE_DIR))  # workflow 子包 (拆分后 phase 模块导入)
# v8.6: 对冲模块 hedging 位于 ms_strategy/src/hedging, 需将其加入 sys.path
_MS_STRATEGY_SRC = BASE_DIR.parent / "ms_strategy" / "src"
if _MS_STRATEGY_SRC.exists():
    sys.path.insert(0, str(_MS_STRATEGY_SRC))

# 审计 item 8 (2026-09-11): 业务时间走 now_bj() (naive 北京时间), 消除本机时区依赖
# (须在上方 sys.path 引导之后导入, 否则 repo root 尚不可解析)
from utils.datetime_utils import now_bj  # noqa: E402

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
            LOG_DIR / f"daily_workflow_{now_bj():%Y%m%d}.log",
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
    from backtest.cost_model import CostModel
    from backtest.metrics import compute_all_metrics
    from execution.algo_engine import AlgoEngine, AlgoType
    from execution.ntp_sync import NTPSync
    from execution.smart_order_router import MockBroker, SmartOrderRouter
    from hedging.beta_hedger import BetaHedger
    from hedging.correlation_hedger import CorrelationHedger
    from hedging.hedge_coordinator import HedgeCoordinator
    from hedging.vol_hedger import VolHedger
    from risk.circuit_breaker import CircuitBreaker, CircuitLevel
    from risk.risk_manager import RiskManager

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
    from lgb_enhanced_trainer import (
        generate_comparison_report as _generate_autolearn_report,
    )
    from lgb_enhanced_trainer import run_enhanced_training as _run_autolearn

    AUTOLEARN_READY = True
    AUTOLEARN_ENGINE = "lgb_enhanced"
    logger.info(
        "使用增强训练器: lgb_enhanced_trainer (真实OHLCV + 情绪因子 + 自适应重训)"
    )
except ImportError as e:
    logger.warning(f"增强训练器导入失败: {e}, 尝试旧训练器")
    try:
        from autolearn_trainer import generate_report as _generate_autolearn_report
        from autolearn_trainer import run_autolearn as _run_autolearn

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
    from utils.gamma_engine import GammaEngine
    from utils.kill_switch import KillSwitch
    from utils.liquidation_scheduler import LiquidationScheduler
    from utils.theta_engine import ThetaEngine

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
    from utils.stress_test_runner import StressTestRunner
    from utils.v10_config_loader import V10ConfigLoader
    from utils.var_monitor import VaRMonitor

    V10_RISK_READY = True
    logger.info(
        "v10.0 风控模块加载成功: DrawdownController/VaRMonitor/StressTestRunner/V10ConfigLoader"
    )
except ImportError as e:
    logger.warning(f"v10.0 风控模块导入失败 (降级模式): {e}")

# ============================================================
# v10.0 量化中性 + 现金管理 + 方向性期货模块 (月度调仓 + 逆回购 + CU/AU/T 方向性)
# ============================================================
V10_STRATEGY_READY = False
try:
    from utils.cash_manager import CashManager
    from utils.directional_futures_trader import DirectionalFuturesTrader
    from utils.ic_hedge_calculator import ICHedgeCalculator
    from utils.quant_neutral_runner import QuantNeutralRunner

    V10_STRATEGY_READY = True
    logger.info(
        "v10.0 策略模块加载成功: QuantNeutralRunner/ICHedgeCalculator/CashManager/DirectionalFuturesTrader"
    )
except ImportError as e:
    logger.warning(f"v10.0 策略模块导入失败 (降级模式): {e}")

# ============================================================
# v10.0 十五五阶段管理器 (5 年度阶段 + 季度评估 + 2030 清仓 Q1-Q4)
# ============================================================
PHASE_MANAGER_READY = False
try:
    from utils.phase_manager import PhaseInfo, PhaseManager, QuarterlyReviewResult

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
    from utils.data_quality_monitor import DataQualityMonitor
    from utils.execution_algo_engine import AlgoType as ExecAlgoType
    from utils.execution_algo_engine import ExecutionAlgoEngine
    from utils.multi_strategy_coordinator import MultiStrategyCoordinator
    from utils.pnl_attribution_engine import PnLAttributionEngine

    HEDGE_FUND_MODULES_READY = True
    logger.info(
        "对冲基金模块加载成功: ExecutionAlgo/PnLAttribution/DataQuality/MultiStrategyCoord"
    )
except ImportError as e:
    logger.warning(f"对冲基金模块导入失败 (降级模式): {e}")

# 顶级配置模块 (Black-Litterman / TCA / Barra)
INSTITUTIONAL_MODULES_READY = False
try:
    from utils.barra_risk_decomposer import (
        BARRA_STYLE_FACTORS,
        BarraDecomposition,
        BarraRiskDecomposer,
    )
    from utils.black_litterman_optimizer import BlackLittermanOptimizer, BLResult
    from utils.black_litterman_optimizer import View as BLView
    from utils.tca_engine import BenchmarkPrices, FillRecord, TCAManager, TCAReport

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
        ShockFactors,
        StressScenario,
        StressTestEngine,
        StressTestResult,
    )

    RISK_MGT_MODULES_READY = True
    logger.info("风险管理模块加载成功: LedoitWolf/RiskBudgetOpt/StressTest")
except ImportError as e:
    logger.warning(f"风险管理模块导入失败 (降级模式): {e}")

# 顶级 Alpha 生成模块 (Alpha 因子库 / 动量反转 / Smart Beta)
ALPHA_MODULES_READY = False
try:
    from utils.alpha_factor_library import AlphaFactorLibrary, FactorLibraryResult
    from utils.momentum_reversal_engine import MomentumResult, MomentumReversalEngine
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
    )
    from utils.execution_algorithm_engine import (
        ExecutionPlan,
    )
    from utils.execution_algorithm_engine import (
        Order as ExecOrder,
    )
    from utils.market_impact_model import ImpactParams, MarketImpactModel
    from utils.smart_order_router import SmartOrderRouter as InstitutionSmartRouter
    from utils.smart_order_router import Venue as RoutingVenue

    EXECUTION_MODULES_READY = True
    logger.info("执行层模块加载成功: ExecAlgo/MarketImpact/SmartRouter")
except ImportError as e:
    logger.warning(f"执行层模块导入失败 (降级模式): {e}")

# 顶级另类数据模块 (新闻情感 / 供应链 / 另类数据) — Renaissance/Two Sigma 标准
ALT_DATA_MODULES_READY = False
try:
    from utils.alt_data_indicators import AltDataIndicators
    from utils.news_sentiment_engine import NewsItem, NewsSentimentEngine
    from utils.supply_chain_graph import SupplyChainEdge, SupplyChainGraph

    ALT_DATA_MODULES_READY = True
    logger.info("另类数据模块加载成功: NewsSentiment/SupplyChain/AltData")
except ImportError as e:
    logger.warning(f"另类数据模块导入失败 (降级模式): {e}")

# 导入 v7.4 神华建仓计划 (可选)
try:
    # 优先尝试 v7.4 模块
    sys.path.insert(0, str(BASE_DIR.parent / "_archive_dead_code"))
    from generate_shenhua_build_plan import (
        BUILD_PHASES,
        BUILD_TIERS,
        EXECUTION_RULES,
        RISK_PARAMS,
        SHENHUA_CODE,
        SHENHUA_NAME,
        VALUATION_SNAPSHOT,
        build_position_plan,
        calc_lots,
    )

    SHENHUA_READY = True
except ImportError:
    SHENHUA_READY = False
    # 降级: 内置神华配置
    SHENHUA_CODE = "601088"
    SHENHUA_NAME = "中国神华"
    BUILD_TIERS = [
        {
            "tier": 1,
            "name": "第一档-底仓",
            "price_low": 40.0,
            "price_high": 42.0,
            "price_mid": 41.0,
            "weight_ratio": 0.35,
        },
        {
            "tier": 2,
            "name": "第二档-加仓",
            "price_low": 36.0,
            "price_high": 39.0,
            "price_mid": 37.5,
            "weight_ratio": 0.35,
        },
        {
            "tier": 3,
            "name": "第三档-重仓",
            "price_low": 32.0,
            "price_high": 35.0,
            "price_mid": 33.5,
            "weight_ratio": 0.30,
        },
    ]
    BUILD_PHASES = [
        {
            "phase": 1,
            "name": "第一阶段-底仓建立",
            "duration_days": 10,
            "capital_ratio": 0.35,
            "tier_ref": 1,
        },
        {
            "phase": 2,
            "name": "第二阶段-回调加仓",
            "duration_days": 15,
            "capital_ratio": 0.35,
            "tier_ref": 2,
        },
        {
            "phase": 3,
            "name": "第三阶段-深度配置",
            "duration_days": 20,
            "capital_ratio": 0.30,
            "tier_ref": 3,
        },
    ]
    EXECUTION_RULES = {
        "daily_timing": {
            "morning_window": ["09:35", "10:15"],
            "afternoon_window": ["14:00", "14:30"],
        },
        "price_rules": {
            "discount_buy": 0.02,
            "normal_buy": 0.00,
            "premium_skip": 0.03,
        },
        "min_lots": 100,
        "max_daily_lots": 1000,
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
    TOTAL_CAPITAL = 5_000_000  # 总资金 500 万
    STOCK_CAPITAL = 3_000_000  # 股票组合 300 万 (60%)
    HEDGE_CAPITAL = 1_060_000  # 对冲资金 106 万 (21.2%)

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
    YELLOW_WARNING = -0.08  # 黄色预警: 组合回撤 ≥ 8% (检查持仓)
    ORANGE_WARNING = -0.10  # 橙色预警: 组合回撤 ≥ 10% (权益仓位降至70%)
    RED_WARNING = -0.12  # 红色预警: 组合回撤 ≥ 12% (权益仓位降至50%)
    FULL_STOP = -0.15  # 全部止损: 组合回撤 ≥ 15%
    SINGLE_DAY_LOSS_PAUSE = -0.03  # 单日回撤 > 3% 暂停买入
    SINGLE_DAY_FORCE_REDUCE = -0.05  # 单日回撤 > 5% 强制减仓30%

    # === 个股止损 ===
    STOP_LOSS_RULES = {
        "宽基ETF": -0.08,  # -8% 减半仓
        "科技股": -0.12,  # -12% 清仓 (海光 -10%, 北方华创/中际旭创/阳光 -12%, 绿的谐波 -15%)
        "防御股": -0.08,  # -8% 减半仓
        "黄金ETF": {"half": -0.08, "clear": -0.12},  # -8% 减半, -12% 清仓
    }

    # === 执行参数 ===
    PRICE_BUFFER = 0.004  # 限价上浮 0.40%（降低到略高于常规滑点）
    PRICE_DEVIATION_SKIP = 0.10  # 价格偏离 ±10% 跳过
    MORNING_WINDOW = "09:30-10:30"
    AFTERNOON_WINDOW = "14:00-14:30"

    # === 再平衡规则 ===
    REBALANCE_PERIODIC = "每月末恢复目标权重"
    REBALANCE_THRESHOLD = 0.05  # 单只标的权重偏离 > 5% 即时调仓

    REPORT_DIR = BASE_DIR.parent.parent / "每日报告归档"
    PLAN_DIR = BASE_DIR / "trade_plans"

    # === MockBroker 价格字典 ===
    MOCK_PRICES = {
        # 核心宽基 ETF
        "sh510300": 4.0,  # 沪深300ETF华泰柏瑞
        "sh510500": 6.5,  # 中证500ETF南方
        "sh512100": 2.3,  # 中证1000ETF南方
        "sz588000": 1.05,  # 科创50ETF华夏
        "sz159915": 2.15,  # 创业板ETF易方达
        # 科技成长个股
        "sh688041": 85.0,  # 海光信息
        "sz300308": 120.0,  # 中际旭创
        "sz300274": 45.0,  # 阳光电源
        "sz002371": 350.0,  # 北方华创
        "sh688017": 180.0,  # 绿的谐波
        "sh600276": 50.0,  # 恒瑞医药
        "sh688981": 142.93,  # 中芯国际
        "sh603019": 94.42,  # 中科曙光
        # 高端制造/基建
        "sh600089": 25.0,  # 特变电工
        "sh600875": 22.0,  # 东方电气
        "sz000425": 8.5,  # 徐工机械
        "sh600406": 35.0,  # 国电南瑞
        "sh600989": 18.0,  # 宝丰能源
        "sh600219": 4.19,  # 南山铝业
        "sh600019": 5.61,  # 宝钢股份
        # 防御/红利
        "sz515180": 5.0,  # 易方达中证红利ETF
        "sh600036": 38.0,  # 招商银行
        "sh600900": 27.05,  # 长江电力
        "sh601088": 40.70,  # 中国神华
        "sh601318": 48.96,  # 中国平安
        "sz000858": 73.21,  # 五粮液
        # 商品/避险
        "sz518880": 5.85,  # 黄金ETF华安
    }


# ============================================================
# 核心工作流 (基于 2026 年交易计划)
# ============================================================
# 2026-09-11 拆解: 装载/环境感知 与 模拟执行 两大**实现簇**迁至 workflow_mixins/
# (mixin 共享 self; 编排层 __init__ / phase_* 委托 shim / _build_context / run / main 留在本文件)
from workflow_mixins.context_loader import ContextLoaderMixin  # noqa: E402
from workflow_mixins.sim_execution import SimExecutionMixin  # noqa: E402


class DailyWorkflow(ContextLoaderMixin, SimExecutionMixin):
    """v7.5 每日交易工作流 — 2026 年交易计划

    基于 `2026年交易计划.md` 的 500 万 4 阶段建仓计划:
    - P1 (7/6-7/17): 底仓建立 35% = 175 万
    - P2 (7/20-8/7): 配置完善 30% = 150 万
    - P3 (8/10-8/28): 防御补充 20% = 100 万
    - P4 (9/1-9/26): 最终调整 15% = 75 万

    每日从 `trade_plans/trade_plan_{date}.json` 加载当日订单,
    通过 MockBroker 执行上午 + 下午批次。
    """

    def __init__(
        self,
        trade_date: str | None = None,
        capital: float = WorkflowConfig.TOTAL_CAPITAL,
        dry_run: bool = False,
        sim_mode: bool = False,
        external_reports_dir: str | None = None,
    ) -> None:
        self.trade_date = trade_date or now_bj().strftime("%Y-%m-%d")
        self.capital = capital
        # P1-1: 与统一三态开关取或 — 全局 QUANT_DRY_RUN/QUANT_SANDBOX=1 时,
        # 编程调用方 (测试/计划任务) 也强制干跑/模拟盘, 防止漏传参数触发实盘路径
        try:
            from utils.runtime_mode import is_dry_run, is_sandbox

            self.dry_run = dry_run or is_dry_run()
            self.sim_mode = sim_mode or is_sandbox()
        except ImportError:
            self.dry_run = dry_run
            self.sim_mode = sim_mode
        self.external_reports_dir = external_reports_dir
        self.config = WorkflowConfig()
        self.config.PLAN_DIR.mkdir(exist_ok=True)
        self.config.REPORT_DIR.mkdir(parents=True, exist_ok=True)

        # 加载 2026 年交易计划
        self.trade_plan: dict[str, Any] = self._load_trade_plan()

        # 状态记录
        self.state: dict[str, Any] = {
            "trade_date": self.trade_date,
            "capital": capital,
            "dry_run": self.dry_run,
            "sim_mode": self.sim_mode,
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
        except Exception as exc:  # fail-safe
            logger.warning("iFinD 新闻资讯模块导入失败: %s", exc)
            self.ifind_analyzer = None

        # 信号融合配置 (从 settings.yaml 加载)
        self.fusion_config = self._load_fusion_config()

        # iFinD 新闻缓存 (同一天避免重复调用)
        self._ifind_cache: dict[str, Any] = {}
        self._ifind_cache_date: str = ""
        self._ifind_planned_symbols: list[str] = []

        # EDB 期货数据缓存 (同一天避免重复调用)
        self._edb_cache: dict[str, dict[str, Any]] = {}
        self._edb_cache_date: str = ""

        # 十五五阶段管理器 (5 年度阶段 + 季度评估 + 2030 清仓)
        self.phase_manager = None
        self.current_phase_info: PhaseInfo | None = None
        if PHASE_MANAGER_READY:
            try:
                self.phase_manager = PhaseManager()
                # 解析 trade_date 为 date 对象用于阶段判断
                try:
                    sim_date = datetime.strptime(self.trade_date, "%Y-%m-%d").date()
                except Exception:  # fail-safe
                    sim_date = None
                self.current_phase_info = self.phase_manager.get_current_phase(sim_date)
                logger.info(
                    f"十五五阶段: {self.current_phase_info.year} {self.current_phase_info.phase_name} "
                    f"(目标 {self.current_phase_info.target_return:.0%}, "
                    f"回撤限 {self.current_phase_info.max_drawdown:.0%}, "
                    f"杠杆 {self.current_phase_info.leverage_target}x)"
                )
            except Exception as exc:  # fail-safe
                logger.warning("PhaseManager 初始化失败: %s", exc)
                self.phase_manager = None

        # 对冲基金视角模块
        self.exec_algo_engine: ExecutionAlgoEngine | None = None
        self.pnl_attribution_engine: PnLAttributionEngine | None = None
        self.data_quality_monitor: DataQualityMonitor | None = None
        self.strategy_coordinator: MultiStrategyCoordinator | None = None
        if HEDGE_FUND_MODULES_READY:
            try:
                self.exec_algo_engine = ExecutionAlgoEngine()
                self.pnl_attribution_engine = PnLAttributionEngine()
                self.data_quality_monitor = DataQualityMonitor()
                self.strategy_coordinator = MultiStrategyCoordinator(
                    total_capital=getattr(self, "capital", 5_000_000)
                )
                logger.info(
                    "对冲基金模块初始化成功 (ExecutionAlgo/PnLAttribution/DataQuality/MultiStrategyCoord)"
                )
            except Exception as exc:  # fail-safe
                logger.warning("对冲基金模块初始化失败: %s", exc)

        # 机构级配置模块 (Black-Litterman / TCA / Barra)
        self.bl_optimizer: BlackLittermanOptimizer | None = None
        self.tca_manager: TCAManager | None = None
        self.barra_decomposer: BarraRiskDecomposer | None = None
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
            except Exception as exc:  # fail-safe
                logger.warning("机构级模块初始化失败: %s", exc)

        # 顶级风险管理模块 (Ledoit-Wolf / 风险预算约束 / 压力测试)
        self.lw_cov_estimator: LedoitWolfCovariance | None = None
        self.risk_budget_opt: RiskBudgetOptimizer | None = None
        self.stress_test_engine: StressTestEngine | None = None
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
                logger.info(
                    "风险管理模块初始化成功 (LedoitWolf/RiskBudgetOpt/StressTest)"
                )
            except Exception as exc:  # fail-safe
                logger.warning("风险管理模块初始化失败: %s", exc)

        # 顶级 Alpha 生成模块 (Alpha 因子库 / 动量反转 / Smart Beta)
        self.alpha_factor_lib: AlphaFactorLibrary | None = None
        self.momentum_engine: MomentumReversalEngine | None = None
        self.smart_beta_engine: SmartBetaEngine | None = None
        if ALPHA_MODULES_READY:
            try:
                self.alpha_factor_lib = AlphaFactorLibrary()
                self.momentum_engine = MomentumReversalEngine()
                self.smart_beta_engine = SmartBetaEngine()
                logger.info(
                    "Alpha 生成模块初始化成功 (AlphaFactorLib/MomentumReversal/SmartBeta)"
                )
            except Exception as exc:  # fail-safe
                logger.warning("Alpha 生成模块初始化失败: %s", exc)

        # 顶级执行层模块 (执行算法 / 市场冲击 / 智能路由)
        self.execution_algo_engine: InstitutionExecAlgoEngine | None = None
        self.market_impact_model: MarketImpactModel | None = None
        self.smart_order_router_inst: InstitutionSmartRouter | None = None
        if EXECUTION_MODULES_READY:
            try:
                self.execution_algo_engine = InstitutionExecAlgoEngine()
                self.market_impact_model = MarketImpactModel()
                self.smart_order_router_inst = InstitutionSmartRouter()
                logger.info("执行层模块初始化成功 (ExecAlgo/MarketImpact/SmartRouter)")
            except Exception as exc:  # fail-safe
                logger.warning("执行层模块初始化失败: %s", exc)

        # 顶级另类数据模块 (新闻情感 / 供应链 / 另类数据)
        self.news_sentiment_engine: NewsSentimentEngine | None = None
        self.supply_chain_graph: SupplyChainGraph | None = None
        self.alt_data_indicators: AltDataIndicators | None = None
        if ALT_DATA_MODULES_READY:
            try:
                self.news_sentiment_engine = NewsSentimentEngine()
                self.supply_chain_graph = SupplyChainGraph()
                self.supply_chain_graph.load_default_chains()
                self.alt_data_indicators = AltDataIndicators()
                logger.info(
                    "另类数据模块初始化成功 (NewsSentiment/SupplyChain/AltData)"
                )
            except Exception as exc:  # fail-safe
                logger.warning("另类数据模块初始化失败: %s", exc)

        # 市场数据提供器 (真实行情回退)
        self.market_data_provider = None
        try:
            from utils.data_provider import MarketDataProvider

            self.market_data_provider = MarketDataProvider()
            logger.info("MarketDataProvider 已初始化")
        except Exception as exc:  # fail-safe
            logger.warning("MarketDataProvider 初始化失败: %s", exc)

        # 外部报告加载器（15_每日工作流报告）
        self.external_report_loader = None
        try:
            from report_parsers import ExternalReportLoader

            self.external_report_loader = ExternalReportLoader(
                base_dir=external_reports_dir
            )
            logger.info(
                "ExternalReportLoader 已初始化，目录: %s",
                external_reports_dir or ExternalReportLoader.BASE_DIR,
            )
        except Exception as exc:  # fail-safe
            logger.warning("ExternalReportLoader 初始化失败: %s", exc)

        # 模拟盘执行引擎（可选）
        self.sim_engine = None
        self.position_sync = None
        self._sim_mode_requested = bool(sim_mode)
        if self.sim_mode:
            try:
                from sim_broker_integration import (
                    PositionSync,
                    SimAccount,
                    SimExecutionEngine,
                    SimStockBroker,
                    TradingSessionCalendar,
                )
                from ths_sim_broker import (
                    SimOptionsBroker,
                    THSQuoteProvider,
                    THSSimFuturesBroker,
                )

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
                stock_broker = SimStockBroker(
                    account=stock_account, price_provider=self.market_data_provider
                )
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
            except Exception as exc:  # fail-safe
                logger.warning("模拟盘执行引擎初始化失败，回退 MockBroker: %s", exc)
                self.sim_engine = None
                self.sim_mode = False


    # --------------------------------------------------------
    # Phase 1: 系统自检
    # --------------------------------------------------------
    def _build_context(self) -> WorkflowContext:
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
    def phase_risk(self) -> dict[str, Any]:
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

    def _style_beta_proxy(
        self, positions: dict[str, float], prices: dict[str, float]
    ) -> float:
        """风格 Beta 代理 (委托至 workflow.phases.risk)"""
        from workflow.phases.risk import _style_beta_proxy as _impl

        return _impl(positions, prices)

    def _get_if_realtime(self) -> dict:
        """获取 IF 期货实时价 (委托至 workflow.phases.risk)"""
        from workflow.phases.risk import _get_if_realtime as _impl

        return _impl()

    def _compute_beta_hedge_order(
        self, portfolio_beta: float, portfolio_value: float, degraded: bool = False
    ) -> dict[str, object]:
        """基于 BetaHedger 计算对冲指令 (委托至 workflow.phases.hedge)"""
        from workflow.phases.hedge import _compute_beta_hedge_order as _impl

        return _impl(portfolio_beta, portfolio_value, degraded)

    def phase_hedge(self) -> dict[str, Any]:
        """三联对冲评估 + 自动执行 (委托至 workflow.phases.hedge)"""
        from workflow.phases.hedge import phase_hedge as _phase_hedge

        ctx = self._build_context()
        return _phase_hedge(ctx)

    def _execute_sim_hedge_orders(
        self, orders: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """模拟盘对冲订单路由 (委托至 workflow.phases.hedge)

        新增方法 (第 3 轮拆分): 符合 test_phase_hedge_sim_branch.py 规约,
        将对冲订单按 action 路由到 sim_engine 的 futures/options/stock broker。
        """
        from workflow.phases.hedge import _execute_sim_hedge_orders as _impl

        mock_prices = (
            getattr(self.config, "MOCK_PRICES", {}) if self.config is not None else {}
        )
        return _impl(getattr(self, "sim_engine", None), mock_prices, orders)

    # --------------------------------------------------------
    # Phase 4.5: 对冲基金视角融合 (v7.7)
    #   - Theta引擎: 月度Covered Call计划生成 + 滚仓检查
    #   - Gamma/Vega引擎: MA60/IV分位监控 + 尾部对冲触发
    #   - 三级熔断: 保证金占用率检查 + 自动执行
    #   - 2030清仓协议: 阶段切换 + 预警
    # --------------------------------------------------------
    def phase_hedge_fund(self) -> dict[str, Any]:
        """对冲基金视角融合 (委托至 workflow.phases.hedge_fund)"""
        from workflow.phases.hedge_fund import phase_hedge_fund as _phase_hedge_fund

        ctx = self._build_context()
        return _phase_hedge_fund(ctx)

    # --------------------------------------------------------
    # Phase 4.6: v10.0 风控 (回撤控制 + VaR 监控 + 压力测试)
    # --------------------------------------------------------
    def phase_v10_risk(self) -> dict[str, Any]:
        """v10.0 风控 (委托至 workflow.phases.v10_risk)"""
        from workflow.phases.v10_risk import phase_v10_risk as _phase_v10_risk

        ctx = self._build_context()
        return _phase_v10_risk(ctx)

    def _get_portfolio_positions_for_stress_test(self) -> list[dict]:
        """获取压力测试持仓 (委托至 workflow.phases.v10_risk)"""
        from workflow.phases.v10_risk import (
            _get_portfolio_positions_for_stress_test as _impl,
        )

        return _impl()

    # --------------------------------------------------------
    # Phase 4.7: 量化市场中性策略 (月度调仓 + IC 对冲)
    # --------------------------------------------------------
    def phase_quant_neutral(self) -> dict[str, Any]:
        """量化市场中性策略 (委托至 workflow.phases.quant_neutral)"""
        from workflow.phases.quant_neutral import (
            phase_quant_neutral as _phase_quant_neutral,
        )

        ctx = self._build_context()
        return _phase_quant_neutral(ctx)

    def _load_quant_neutral_holdings(self) -> list[dict]:
        """加载量化中性多头持仓 (委托至 workflow.phases.quant_neutral)"""
        from workflow.phases.quant_neutral import _load_quant_neutral_holdings as _impl

        return _impl()

    def _get_ic_price(self) -> float:
        """获取 IC 期货价格 (委托至 workflow.phases.quant_neutral)"""
        from workflow.phases.quant_neutral import _get_ic_price as _impl

        return _impl()

    def _get_ic_basis(self) -> float | None:
        """获取 IC 基差 (委托至 workflow.phases.quant_neutral)"""
        from workflow.phases.quant_neutral import _get_ic_basis as _impl

        return _impl()

    def _get_current_ic_contracts(self) -> int:
        """获取 IC 空头合约数 (委托至 workflow.phases.quant_neutral)"""
        from workflow.phases.quant_neutral import _get_current_ic_contracts as _impl

        return _impl()

    def _load_strategy_drawdown_state(
        self, strategy_name: str
    ) -> tuple[float, float, int]:
        """加载策略回撤状态 (委托至 workflow.phases.quant_neutral)"""
        from workflow.phases.quant_neutral import _load_strategy_drawdown_state as _impl

        return _impl(strategy_name)

    # --------------------------------------------------------
    # Phase 4.8: 现金管理 (逆回购 + 货基 + 应急金监控)
    # --------------------------------------------------------
    def phase_cash_management(self) -> dict[str, Any]:
        """现金管理 (委托至 workflow.phases.cash_management)"""
        from workflow.phases.cash_management import (
            phase_cash_management as _phase_cash_management,
        )

        ctx = self._build_context()
        return _phase_cash_management(ctx)

    # --------------------------------------------------------
    # Phase 4.9: 方向性期货交易 (CU/AU/T 三品种方向性交易)
    # --------------------------------------------------------
    def phase_directional_futures(self) -> dict[str, Any]:
        """方向性期货交易 (委托至 workflow.phases.directional_futures)"""
        from workflow.phases.directional_futures import (
            phase_directional_futures as _phase_directional_futures,
        )

        ctx = self._build_context()
        return _phase_directional_futures(ctx)

    # --------------------------------------------------------
    # Phase 5: 信号生成 (2026 交易计划订单) -- 拆分至 workflow/phases/signal*.py
    # --------------------------------------------------------
    def phase_signal(self) -> dict[str, Any]:
        """信号生成 -- 从 trade_plan 加载订单 (委托至 workflow.phases.signal)"""
        from workflow.phases.signal import phase_signal as _phase_signal

        ctx = self._build_context()
        return _phase_signal(ctx)

    @staticmethod
    def _qlib_signal_to_factor(signal_value: float) -> float:
        """Qlib 信号 -> 订单调整系数 (委托至 workflow.phases.signal_qlib)"""
        from workflow.phases.signal_qlib import qlib_signal_to_factor

        return qlib_signal_to_factor(signal_value)

    def _qlib_signals_to_adjustments(
        self,
        qlib_signals: dict[str, Any],
        *,
        morning_orders: dict[str, Any],
        afternoon_orders: dict[str, Any],
    ) -> dict[str, Any]:
        """按 Qlib 信号调整订单 (委托至 workflow.phases.signal_qlib)"""
        from workflow.phases.signal_qlib import qlib_signals_to_adjustments

        return qlib_signals_to_adjustments(
            qlib_signals,
            morning_orders=morning_orders,
            afternoon_orders=afternoon_orders,
        )

    @staticmethod
    def _ifind_signal_to_factor(direction: str, confidence: float) -> float:
        """iFinD 研判 -> 订单调整系数 (委托至 workflow.phases.signal_ifind)"""
        from workflow.phases.signal_ifind import ifind_signal_to_factor

        return ifind_signal_to_factor(direction, confidence)

    @staticmethod
    def _fuse_qlib_ifind_factor(
        qlib_factor: float,
        ifind_factor: float,
        *,
        qlib_weight: float = 0.6,
        ifind_weight: float = 0.4,
        clamp_min: float = 0.5,
        clamp_max: float = 1.3,
    ) -> float:
        """融合 Qlib + iFinD 因子 (委托至 workflow.phases.signal)"""
        from workflow.phases.signal import fuse_qlib_ifind_factor

        return fuse_qlib_ifind_factor(
            qlib_factor,
            ifind_factor,
            qlib_weight=qlib_weight,
            ifind_weight=ifind_weight,
            clamp_min=clamp_min,
            clamp_max=clamp_max,
        )

    def _load_lgb_enhanced_signals(self) -> dict[str, dict[str, Any]]:
        """加载 LGB 增强信号 (委托至 workflow.phases.signal_lgb)"""
        from workflow.phases.signal_lgb import load_lgb_enhanced_signals

        return load_lgb_enhanced_signals()

    @staticmethod
    def _lgb_confidence_multiplier(
        signal_value: float, quality_flag: str = "OK"
    ) -> float:
        """LGB 信号 -> 置信度乘数 (委托至 workflow.phases.signal_lgb)"""
        from workflow.phases.signal_lgb import lgb_confidence_multiplier

        return lgb_confidence_multiplier(signal_value, quality_flag)

    def _apply_fused_qlib_ifind_adjustments(
        self,
        *,
        morning_orders: dict[str, Any],
        afternoon_orders: dict[str, Any],
        qlib_signals: dict[str, Any],
        ifind_insights: dict[str, Any],
        external_factor: float = 1.0,
        regime_weights: dict[str, Any] | None = None,
        lgb_signals: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """四源融合调整订单 (委托至 workflow.phases.signal)"""
        from workflow.phases.signal import apply_fused_qlib_ifind_adjustments

        ctx = self._build_context()
        return apply_fused_qlib_ifind_adjustments(
            ctx,
            morning_orders=morning_orders,
            afternoon_orders=afternoon_orders,
            qlib_signals=qlib_signals,
            ifind_insights=ifind_insights,
            external_factor=external_factor,
            regime_weights=regime_weights,
            lgb_signals=lgb_signals,
        )

    def _apply_ifind_news_adjustments(
        self, *, morning_orders: dict[str, Any], afternoon_orders: dict[str, Any]
    ) -> dict[str, Any]:
        """iFinD 新闻调整订单 (委托至 workflow.phases.signal_ifind)"""
        from workflow.phases.signal_ifind import apply_ifind_news_adjustments

        ctx = self._build_context()
        return apply_ifind_news_adjustments(
            ctx, morning_orders=morning_orders, afternoon_orders=afternoon_orders
        )

    def _apply_macro_policy_adjustments(
        self,
        *,
        morning_orders: dict[str, Any],
        afternoon_orders: dict[str, Any],
        macro_scores: dict[str, Any],
    ) -> dict[str, Any]:
        """宏观政策评分调整 (委托至 workflow.phases.signal_ifind)"""
        from workflow.phases.signal_ifind import apply_macro_policy_adjustments

        return apply_macro_policy_adjustments(
            morning_orders=morning_orders,
            afternoon_orders=afternoon_orders,
            macro_scores=macro_scores,
        )

    def _apply_position_factor(
        self, orders: dict[str, Any], factor: float
    ) -> dict[str, Any]:
        """DEFENSE 模式仓位系数调整 (委托至 workflow.phases.signal)"""
        from workflow.phases.signal import apply_position_factor

        return apply_position_factor(orders, factor)

    def _generate_qlib_signals(self) -> dict[str, float]:
        """生成 Qlib 深度学习信号 (委托至 workflow.phases.signal_qlib)"""
        from workflow.phases.signal_qlib import generate_qlib_signals

        ctx = self._build_context()
        return generate_qlib_signals(ctx)

    def _generate_mock_ohlcv(self, symbol: str, days: int = 120) -> dict[str, Any]:
        """生成模拟 OHLCV 数据 (委托至 workflow.phases.signal_qlib)"""
        from workflow.phases.signal_qlib import generate_mock_ohlcv

        return generate_mock_ohlcv(symbol, days)

    def _options_market_snapshot(self) -> dict[str, Any]:
        """期权市场快照 (委托至 workflow.phases.signal_ifind)"""
        from workflow.phases.signal_ifind import options_market_snapshot

        return options_market_snapshot()

    # --------------------------------------------------------
    # Phase 6: 智能执行 (MockBroker / SimExecutionEngine)
    # --------------------------------------------------------
    def phase_execute(self, signal: dict[str, Any]) -> list[dict[str, Any]]:
        """智能执行 (委托到 workflow/phases/execute.py)。"""
        from workflow.context import WorkflowContext
        from workflow.phases.execute import phase_execute as _phase_execute

        ctx = WorkflowContext(self)
        return _phase_execute(ctx, signal)


    # --------------------------------------------------------
    # Phase 7: 盘后报告
    # --------------------------------------------------------
    def phase_report(self) -> Path:
        """盘后报告生成 (委托到 workflow/phases/report.py)。"""
        from workflow.context import WorkflowContext
        from workflow.phases.report import phase_report as _phase_report

        ctx = WorkflowContext(self)
        return _phase_report(ctx)

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
    def run(
        self,
        only_phase: str | None = None,
        phase_start: str | None = None,
        phase_end: str | None = None,
    ) -> dict[str, Any]:
        """执行完整工作流

        Args:
            only_phase: 仅执行指定的单个阶段
            phase_start: 执行的起始阶段（包含）
            phase_end: 执行的结束阶段（包含）
        """
        logger.info("#" * 60)
        logger.info(f"# v7.5 每日交易工作流 — {self.trade_date}")
        logger.info(
            f"# 资金: {self.capital:,.0f} | 模式: {'DRY-RUN' if self.dry_run else 'EXECUTE'}"
        )
        logger.info("#" * 60)

        phases = [
            ("check", self.phase_check),
            ("calibrate", self.phase_calibrate),
            ("market", self.phase_market),
            ("risk", self.phase_risk),
            ("hedge", self.phase_hedge),
            ("hedge_fund", self.phase_hedge_fund),  # v7.7: 对冲基金视角融合
            ("v10_risk", self.phase_v10_risk),  # v10.0: 回撤+VaR+压测
            (
                "quant_neutral",
                self.phase_quant_neutral,
            ),  # v10.0: 量化中性月度调仓+IC对冲
            ("cash_management", self.phase_cash_management),  # v10.0: 现金管理+逆回购
            (
                "directional_futures",
                self.phase_directional_futures,
            ),  # v10.0: 方向性期货 CU/AU/T
            ("signal", self.phase_signal),
            (
                "execute",
                lambda: self.phase_execute(
                    self.state.get("phases", {}).get("signal", {})
                ),
            ),
            ("report", self.phase_report),
            ("autolearn", self.phase_autolearn),
        ]

        phase_names = [p[0] for p in phases]
        start_idx = (
            0
            if phase_start is None
            else (phase_names.index(phase_start) if phase_start in phase_names else 0)
        )
        end_idx = (
            len(phases)
            if phase_end is None
            else (
                phase_names.index(phase_end) + 1
                if phase_end in phase_names
                else len(phases)
            )
        )

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
            except Exception as e:  # fail-safe
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
def main() -> None:
    from utils.runtime_mode import env_flag, set_mode

    parser = argparse.ArgumentParser(
        description="v7.5 每日交易工作流",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--date", default=None, help="交易日期 YYYY-MM-DD (默认今日)")
    parser.add_argument(
        "--capital",
        type=float,
        default=WorkflowConfig.TOTAL_CAPITAL,
        help=f"资金规模 (默认 {WorkflowConfig.TOTAL_CAPITAL})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=env_flag("QUANT_DRY_RUN"),
        help="干跑模式 (不执行交易; 可用 QUANT_DRY_RUN=1 预设)",
    )
    parser.add_argument(
        "--sim",
        action="store_true",
        default=env_flag("QUANT_SANDBOX"),
        help="模拟盘模式 (股票+期货，按交易日+夜盘执行; 可用 QUANT_SANDBOX=1 预设)",
    )
    parser.add_argument(
        "--phase",
        default=None,
        choices=[
            "check",
            "calibrate",
            "market",
            "risk",
            "hedge",
            "hedge_fund",
            "v10_risk",
            "quant_neutral",
            "cash_management",
            "directional_futures",
            "signal",
            "execute",
            "report",
            "autolearn",
        ],
        help="仅执行指定阶段",
    )
    parser.add_argument(
        "--phase-start",
        default=None,
        choices=[
            "check",
            "calibrate",
            "market",
            "risk",
            "hedge",
            "hedge_fund",
            "v10_risk",
            "quant_neutral",
            "cash_management",
            "directional_futures",
            "signal",
            "execute",
            "report",
            "autolearn",
        ],
        help="执行的起始阶段（包含）",
    )
    parser.add_argument(
        "--phase-end",
        default=None,
        choices=[
            "check",
            "calibrate",
            "market",
            "risk",
            "hedge",
            "hedge_fund",
            "v10_risk",
            "quant_neutral",
            "cash_management",
            "directional_futures",
            "signal",
            "execute",
            "report",
            "autolearn",
        ],
        help="执行的结束阶段（包含）",
    )
    parser.add_argument(
        "--external-reports-dir",
        default=None,
        help="外部报告目录 (默认 E:\\各种PY程序\\每日报告归档)",
    )
    parser.add_argument(
        "--ai-sandbox",
        action="store_true",
        help="只读 AI 决策沙箱：生成 GLM-5.2 建议并落盘，不触发下单",
    )
    parser.add_argument(
        "--ai-auto-approve",
        action="store_true",
        help="AI 自动确认：基于 gate 结果自动确认可执行指令",
    )
    parser.add_argument(
        "--execution-review",
        action="store_true",
        help="执行复盘：核对 AI 建议与收盘盈亏，生成复盘报告",
    )
    parser.add_argument(
        "--dynamic-risk", action="store_true", help="动态风控：基于复盘结果调整风控阈值"
    )
    parser.add_argument(
        "--write-gate-limits",
        action="store_true",
        help="动态风控时同时写回 AI Gate 可读取的限值文件",
    )
    parser.add_argument(
        "--intraday-monitor",
        action="store_true",
        help="盘中监控：输出监控摘要、风险事件与动态调整建议",
    )
    parser.add_argument(
        "--auto-closed-loop",
        action="store_true",
        help="自动闭环：执行复盘后自动触发动态风控，并写回 AI Gate 风控限值",
    )

    args = parser.parse_args()

    # P1-1: CLI/env 解析结果广播到统一三态开关 (深层模块经 is_dry_run/is_sandbox 感知)
    set_mode(dry_run=args.dry_run, sandbox=args.sim)

    if args.ai_sandbox:
        from ai_decision_sandbox import run_ai_sandbox

        result = run_ai_sandbox(trade_date=args.date)
        sys.exit(0 if result.get("status") == "PASS" else 1)

    if args.ai_auto_approve:
        from ai_auto_approver import run_ai_auto_approver

        result = run_ai_auto_approver(
            trade_date=args.date,
            auto_mode=bool(getattr(args, "ai_auto_approve", False)),
        )
        sys.exit(0 if result.get("status") == "PASS" else 1)

    if args.execution_review:
        from execution_reviewer import run_execution_review

        result = run_execution_review(
            trade_date=args.date, auto_closed_loop=args.auto_closed_loop
        )
        sys.exit(0)

    if args.dynamic_risk:
        from dynamic_risk_adjuster import run_dynamic_risk_adjuster

        result = run_dynamic_risk_adjuster(
            trade_date=args.date,
            write_gate_limits=args.write_gate_limits or args.auto_closed_loop,
        )
        sys.exit(0)

    if args.auto_closed_loop and not args.execution_review and not args.dynamic_risk:
        from dynamic_risk_adjuster import run_dynamic_risk_adjuster
        from execution_reviewer import run_execution_review

        run_execution_review(trade_date=args.date, auto_closed_loop=True)
        run_dynamic_risk_adjuster(trade_date=args.date, write_gate_limits=True)
        sys.exit(0)

    if args.intraday_monitor:
        from intraday_monitor import run_intraday_monitor

        result = run_intraday_monitor(trade_date=args.date)
        sys.exit(0 if result.get("status") == "PASS" else 1)

    workflow = DailyWorkflow(
        trade_date=args.date,
        capital=args.capital,
        dry_run=args.dry_run,
        sim_mode=args.sim,
        external_reports_dir=args.external_reports_dir,
    )
    state = workflow.run(
        only_phase=args.phase, phase_start=args.phase_start, phase_end=args.phase_end
    )

    # v8.6: 对冲阶段完成后自动串联 RiskGuardIntegrator (盈亏→风控→改写 trade_plan→对冲增减)
    # 触发条件: 完整 workflow 或 --phase hedge, 且非 dry_run
    _hedge_done = (
        "hedge" in state.get("phases", {})
        and state["phases"]["hedge"].get("status") == "PASS"
    )
    if _hedge_done and not args.dry_run:
        try:
            from datetime import timedelta as _td

            from utils.risk_guard_integrator import RiskGuardIntegrator

            _today = datetime.strptime(args.date, "%Y-%m-%d")
            _next = _today + _td(days=1)
            while _next.weekday() >= 5:
                _next += _td(days=1)
            _next_date = _next.strftime("%Y-%m-%d")
            logger.info(
                f"[AutoClosedLoop] 对冲完成, 自动串联 8-Guard 链: {args.date} → {_next_date}"
            )
            _integrator = RiskGuardIntegrator(report_date=args.date)
            _integrator.run_all_guards(next_trade_date=_next_date)
            logger.info("[AutoClosedLoop] 8-Guard 链执行完成, trade_plan 已自动改写")
        except Exception as _e:  # fail-safe
            logger.warning(f"[AutoClosedLoop] 8-Guard 链失败 (fail-open): {_e}")

    # 退出码：check 阶段单独允许降级通过，避免计划文件缺失导致整条自动任务失败
    all_pass = True
    for _name, phase in state.get("phases", {}).items():
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
