"""统一入口共享引导层 — 登录器/项目路径/单例/可用性开关。

自 量化策略系统_统一入口_v8.6.py 字节级迁出 (2026-09-10, 审计 item 11 结构拆解)。
零行为变更: 迁出代码与原文逐字节一致, 仅 BASE_DIR 推导按新文件层级调整。

注意: 本模块在导入时会执行引导副作用(setup_utf8_console / load_dotenv /
setup_sys_path / setup_logging / 连接器注册), 与原先入口文件顶部行为一致。
"""

from __future__ import annotations

# 本模块的定位是"启动层命名空间": 入口文件与 helpers/deprecated_modes 通过
# `from cli.handlers.support import ...` 取用以下名字。声明 __all__ 既固化该契约,
# 也避免 F401 误报 re-export (pd / do_archive_report / 条件导入的引擎类等)。
__all__ = [
    "AI_COORDINATOR_AVAILABLE", "BASE_DIR", "ConfigError", "ConfigHub",
    "ConfigManager", "DataConnectorManager", "DataSourceError", "ETFFundFlowMonitor",
    "ETFFundFlowTracker", "EnhancedFeatureEngineer", "EnhancedMLTrainer",
    "EnhancedPredictor", "ExcelDrivenRebalancingEngineV4", "FIFTEEN_FIVE_AVAILABLE",
    "FifteenFivePlanAnalyzer", "GracefulFallback", "HARD_STOP_MAX_DRAWDOWN",
    "KONDRATIEV_AVAILABLE", "KondratievCycleAnalyzer", "LOG_DIR", "MLModelPredictor",
    "ML_ENHANCED_PREDICTOR_AVAILABLE", "ML_ENHANCED_TRAINER_AVAILABLE",
    "ML_PREDICTOR_AVAILABLE", "ModuleLoader", "ProgressIndicator",
    "SOCIAL_SECURITY_ETF_AVAILABLE", "SocialSecurityETFTracker", "StrategyRegistry",
    "TAIL_HEDGE_THRESHOLD", "_AI_HEDGE_IMPORTED", "_AI_HEDGE_MODULE", "auto_trading",
    "config_hub", "config_manager", "connector_manager", "daily_report", "data_provider",
    "do_archive_report", "event_tracker", "generate_stress_report", "get_ai_coordinator",
    "get_event_tracker", "get_logger", "graceful_fallback", "load_dotenv",
    "load_portfolio_config", "loader", "logger", "pd", "rebalance_engine",
    "register_all_connectors", "run_enhanced_training", "run_ml_signal_scan",
    "setup_logging", "setup_sys_path", "setup_utf8_console", "stop_loss",
    "strategy_registry",
]

import logging  # noqa: F401  (保留: 迁出代码通过 # noqa: E402 段后的 get_logger 定义 logger)
import os
import sys

# bootstrap: 本文件位于 cli/handlers/, 项目根为其上三级
_TMP_BASE_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _TMP_BASE_DIR not in sys.path:
    sys.path.insert(0, _TMP_BASE_DIR)

import pandas as pd

from utils.console_encoding import setup_utf8_console
from utils.env_loader import load_dotenv
from utils.stress_test import (
    HARD_STOP_MAX_DRAWDOWN,
    TAIL_HEDGE_THRESHOLD,
    generate_stress_report,
)

setup_utf8_console()
load_dotenv()  # 加载 .env 环境变量配置（含引号去除与已存在变量保护）

BASE_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)  # cli/handlers/support.py -> 项目根 (原入口文件为单层 dirname)
# Wave 3 第三阶段: 改用 utils.path_config.setup_sys_path() 统一管理
sys.path.insert(0, BASE_DIR)  # bootstrap: 确保 utils 包可导入 (向后兼容)
from utils.path_config import setup_sys_path  # noqa: E402

setup_sys_path()  # noqa: E402  # 统一注入 v8.3 根 / v8.3 src / utils

# ============================================================
# 日志配置 — 引入统一的日志管理器（借鉴 TradingAgents-CN 架构）
# ============================================================
from utils.logging_manager import get_logger, setup_logging
from utils.report_archiver import archive_report as do_archive_report

setup_logging()
logger = get_logger("quant")
LOG_DIR = os.path.join(BASE_DIR, "logs")

# ============================================================
# 事件追踪 — 引入统一的事件追踪器（借鉴 TradingAgents-CN 结构化事件模式）
# ============================================================
from utils.event_tracker import get_event_tracker

event_tracker = get_event_tracker()

# v5.2 去重 — ETF资金监控: engine.managers 未创建 (阶段4未完成), 优先集成版
# utils.etf_fund_tracker (Wind MCP → akshare → 模拟, 2026-09-09 从 etf-tracker 移植)。
# engine.managers 存在时仍以其为准; 缺失时以集成版类提供 ETFFundFlowMonitor 兼容符号。
try:
    from engine.managers import ETFFundFlowMonitor

    logger.info("✅ engine.managers ETFFundFlowMonitor 已加载")
except ImportError:
    try:
        from utils.etf_fund_tracker import ETFFundFlowTracker

        ETFFundFlowMonitor = ETFFundFlowTracker  # type: ignore[assignment]  # 集成版兼容别名
        logger.info("✅ utils.etf_fund_tracker (Wind MCP ETF资金监控, 集成版) 已加载")
    except ImportError:
        ETFFundFlowMonitor = None  # type: ignore[assignment,misc]
        logger.warning("⚠️ ETF资金流向监控模块未加载 (engine.managers 与 utils.etf_fund_tracker 均缺失)")

try:
    from engine.rebalance import ExcelDrivenRebalancingEngineV4
except ImportError:
    ExcelDrivenRebalancingEngineV4 = None  # type: ignore[assignment,misc]
    logger.warning("⚠️ engine.rebalance 未加载 (阶段4未完成), 再平衡模式将降级")
from quant_modules.core import (
    ConfigError,
    ConfigManager,
    DataSourceError,
    GracefulFallback,
    ModuleLoader,
    ProgressIndicator,
    StrategyRegistry,
    load_portfolio_config,
)
from quant_modules.data_layer import DataConnectorManager

# ============================================================
# 全局降级管理器
# ============================================================
graceful_fallback = GracefulFallback()

# 注册默认降级处理器
graceful_fallback.register_fallback(DataSourceError, lambda e: {})
graceful_fallback.register_fallback(ConfigError, lambda e: {})

# 全局配置管理器实例
config_manager = ConfigManager()

# ============================================================
# 模块导入 (优雅降级) - 加载核心模块
# ============================================================
loader = ModuleLoader()

# 数据提供层
data_provider = loader.load(
    "wind_data_provider",
    {
        "get_quotes_batch": "get_quotes_batch",
        "get_quote": "get_quote",
        "get_stats": "get_wind_stats",
        "reset_stats": "reset_stats",
    },
)

# 自动交易系统
auto_trading = loader.load(
    "auto_trading_system", {"AutoTradingSystem": "AutoTradingSystem"}
)

# 再平衡引擎
rebalance_engine = loader.load(
    "rebalancing_engine", {"RebalancingEngine": "RebalancingEngine"}
)

# 每日报告
daily_report = loader.load(
    "daily_report", {"generate_daily_report": "generate_daily_report"}
)

# 止损止盈监控
stop_loss = loader.load(
    "stop_loss_monitor",
    {
        "StopLossMonitor": "StopLossMonitor",
        "generate_risk_alert_report": "generate_risk_alert_report",
    },
)

# 策略注册表实例
strategy_registry = StrategyRegistry()
connector_manager = DataConnectorManager()

# v5.7 Phase 3: 统一配置中心 (替代分散的 load_portfolio_config)
config_hub = None
try:
    from utils.config_hub import ConfigHub

    config_hub = ConfigHub(config_dir=os.path.join(BASE_DIR, "config"))
    logger.info(
        f"[ConfigHub] 已加载 {len(config_hub.get_all_asset_codes())} 个标的的配置"
    )
except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
    logger.warning(f"[ConfigHub] 初始化失败: {e}，回退到传统配置加载")

# ============================================================
# 注册所有数据源连接器 (Wind MCP → 通达信 → Sina → 本地缓存)
# ============================================================
try:
    from quant_modules.connectors import register_all_connectors

    n_registered = register_all_connectors(connector_manager)
    if n_registered > 0:
        # 显式激活主连接器 (Wind MCP, 优先级200)
        primary = connector_manager.get_active_connector()
        logger.info(
            f"✅ 数据源连接器注册完成: {n_registered} 个可用, 主连接器: {primary.name if primary else 'None'}"
        )
    else:
        logger.warning("⚠️ 无可用数据源连接器，系统将以离线模式运行")
except ImportError as e:
    logger.warning(f"⚠️ 连接器注册模块加载失败: {e}")
except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
    logger.warning(f"⚠️ 连接器注册异常: {e}")

# ============================================================
# 康波周期 / 十五五规划 / 社保基金ETF 分析模块 (v5.1 新增)
# ============================================================
try:
    from utils.kondratiev_cycle import KondratievCycleAnalyzer

    KONDRATIEV_AVAILABLE = True
    logger.info("✅ 康波周期分析模块已加载")
except ImportError as e:
    KondratievCycleAnalyzer = None
    KONDRATIEV_AVAILABLE = False
    logger.warning(f"⚠️ 康波周期分析模块加载失败: {e}")

try:
    from utils.five_year_plan import FifteenFivePlanAnalyzer

    FIFTEEN_FIVE_AVAILABLE = True
    logger.info("✅ 十五五规划分析模块已加载")
except ImportError as e:
    FifteenFivePlanAnalyzer = None
    FIFTEEN_FIVE_AVAILABLE = False
    logger.warning(f"⚠️ 十五五规划分析模块加载失败: {e}")

try:
    from utils.social_security_etf import SocialSecurityETFTracker

    SOCIAL_SECURITY_ETF_AVAILABLE = True
    logger.info("✅ 社保基金ETF追踪模块已加载")
except ImportError as e:
    SocialSecurityETFTracker = None
    SOCIAL_SECURITY_ETF_AVAILABLE = False
    logger.warning(f"⚠️ 社保基金ETF追踪模块加载失败: {e}")

# ============================================================
# ML 模型预测模块 (v5.6 新增) - GradientBoosting + 特征选择
# ============================================================
try:
    from utils.ml_predictor import (
        EnhancedPredictor,
        MLModelPredictor,
        run_ml_signal_scan,
    )

    ML_PREDICTOR_AVAILABLE = True
    ML_ENHANCED_PREDICTOR_AVAILABLE = EnhancedPredictor is not None
    logger.info("✅ ML模型预测模块已加载")
except ImportError as e:
    MLModelPredictor = None
    run_ml_signal_scan = None
    EnhancedPredictor = None
    ML_PREDICTOR_AVAILABLE = False
    ML_ENHANCED_PREDICTOR_AVAILABLE = False
    logger.warning(f"⚠️ ML模型预测模块加载失败: {e}")

# v2.0 增强训练引擎
try:
    from utils.ml_enhanced_trainer import (
        EnhancedFeatureEngineer,
        EnhancedMLTrainer,
        run_enhanced_training,
    )

    ML_ENHANCED_TRAINER_AVAILABLE = True
    logger.info("✅ ML增强训练引擎 v2.0 已加载")
except ImportError as e:
    EnhancedMLTrainer = None
    EnhancedFeatureEngineer = None
    run_enhanced_training = None
    ML_ENHANCED_TRAINER_AVAILABLE = False
    logger.warning(f"⚠️ ML增强训练引擎未加载: {e}")

# ============================================================
# v5.7 Phase 2: AI Hedge Fund 懒加载缓存 & AI协调器
# ============================================================
_AI_HEDGE_IMPORTED = False
_AI_HEDGE_MODULE = {}

try:
    from utils.ai_coordinator import get_ai_coordinator

    AI_COORDINATOR_AVAILABLE = True
except ImportError:
    get_ai_coordinator = None
    AI_COORDINATOR_AVAILABLE = False

# ============================================================
# LSEG MCP 连接器集成 (优先级3) - 已禁用
# ============================================================
# 如需启用 LSEG，请取消下面的注释并设置 LSEG_API_KEY 环境变量
# try:
#     from lseg_integration import register_lseg_connector
#     register_lseg_connector(connector_manager)
#     logger.info("✅ LSEG MCP 连接器已注册 (优先级: 3)")
# except ImportError as e:
#     logger.warning(f"⚠️  LSEG 集成模块未找到: {e}")
#     logger.warning("   如需启用 LSEG 数据源，请确保 lseg_integration.py 存在")
# except Exception as e:
#     logger.warning(f"⚠️  LSEG 连接器注册失败: {e}")
