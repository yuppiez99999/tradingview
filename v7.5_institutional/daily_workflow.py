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
import math
import logging
import argparse
import re
import requests
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any

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
try:
    from autolearn_trainer import run_autolearn as _run_autolearn
    from autolearn_trainer import generate_report as _generate_autolearn_report
    AUTOLEARN_READY = True
except ImportError as e:
    logger.warning(f"自主学习训练模块导入失败: {e}")
    AUTOLEARN_READY = False

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
    HEDGE_CAPITAL = 2_000_000          # 对冲资金 200 万 (40%)

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
                 sim_mode: bool = False):
        self.trade_date = trade_date or datetime.now().strftime("%Y-%m-%d")
        self.capital = capital
        self.dry_run = dry_run
        self.sim_mode = sim_mode
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
            self.external_report_loader = ExternalReportLoader()
            logger.info("ExternalReportLoader 已初始化")
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
                stock_broker = SimStockBroker(account=stock_account, price_provider=self.market_data_provider)
                futures_broker = SimFuturesBroker(account=futures_account, price_provider=self.market_data_provider)
                self.sim_engine = SimExecutionEngine(
                    stock_broker=stock_broker,
                    futures_broker=futures_broker,
                    calendar=calendar,
                    price_provider=self.market_data_provider,
                )
                self.position_sync = PositionSync(self.sim_engine.router)
                logger.info("模拟盘执行引擎已初始化 (股票+期货)")
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

        checks = {
            "v75_modules": V75_READY,
            "shenhua_plan": SHENHUA_READY,
            "ntp_sync": False,
            "risk_manager": False,
            "circuit_breaker": False,
        }

        self.ntp = NTPSync()

        if not V75_READY:
            logger.warning("v7.5 模块未就绪，进入降级模式继续执行")
            checks["v75_modules"] = False
            self.state["phases"]["check"] = {"status": "PASS", "checks": checks, "degraded": True}
            return True

        # NTP 同步
        try:
            ntp = NTPSync()
            offset = ntp.get_offset()
            checks["ntp_sync"] = abs(offset) < 0.05
            logger.info(f"NTP 同步: offset={offset:.3f}s {'OK' if checks['ntp_sync'] else 'DRIFT'}")
        except Exception as e:
            logger.warning(f"NTP 同步失败 (使用本地时间): {e}")
            checks["ntp_sync"] = True  # 降级允许

        # 风控初始化
        try:
            self.rm = RiskManager(total_capital=self.capital)
            self.cb = CircuitBreaker()
            checks["risk_manager"] = True
            checks["circuit_breaker"] = True
            logger.info(f"风险模式: {self.rm.mode}, 仓位系数: {self.rm.position_size_factor}")
        except Exception as e:
            logger.error(f"风控初始化失败: {e}")
            checks["risk_manager"] = False
            checks["circuit_breaker"] = False
            self.state["phases"]["check"] = {"status": "PASS", "checks": checks, "degraded": True}
            logger.warning("风控初始化失败，进入降级模式继续执行")
            return True

        self.ntp = ntp if checks["ntp_sync"] else NTPSync()
        self.state["phases"]["check"] = {"status": "PASS", "checks": checks}
        logger.info("Phase 1 完成: 全部自检通过")
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
                self.cb = CircuitBreaker()
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
        level = self.cb.check(
            portfolio_drop=market_data["portfolio_drop"],
            vix=market_data["vix"],
        )
        actions = self.cb.allowed_actions()

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

        if level >= CircuitLevel.LEVEL_3:
            logger.warning("市场熔断 LEVEL_3+, 暂停建仓")
            self.state["phases"]["market"]["build_allowed"] = False
        else:
            self.state["phases"]["market"]["build_allowed"] = True

        return level

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
                resp = session.get(url, headers=headers, timeout=10, verify=False)
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
            resp = session.get(url, headers=headers, timeout=10, verify=False)
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
            try:
                broker = MockBroker(price_dict={
                    str(k): v for k, v in self.config.MOCK_PRICES.items()
                })
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

        # === 融合 Qlib + iFinD + 外部报告 信号统一调整订单 ===
        if qlib_signals or ifind_insights:
            fused_adjustments = self._apply_fused_qlib_ifind_adjustments(
                morning_orders=adjusted_morning,
                afternoon_orders=adjusted_afternoon,
                qlib_signals=qlib_signals,
                ifind_insights=ifind_insights,
                external_factor=external_factor,
                regime_weights=regime_weights,
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
                logger.info(
                    "融合调整完成: 加仓=%d, 减仓=%d, 跳过=%d",
                    signal["qlib_boost_count"],
                    signal["qlib_cut_count"],
                    signal["qlib_skip_count"],
                )
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

    def _apply_fused_qlib_ifind_adjustments(self,
                                            *,
                                            morning_orders: List[Dict[str, Any]],
                                            afternoon_orders: List[Dict[str, Any]],
                                            qlib_signals: Dict[str, float],
                                            ifind_insights: Dict[str, Any],
                                            external_factor: float = 1.0,
                                            regime_weights: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
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

                # 应用 fused_factor
                if fused_factor <= 0.0:
                    logger.info("融合信号跳过订单 [%s] qlib_factor=%.2f ifind_factor=%.2f",
                                code, qlib_factor, ifind_factor)
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

        return {
            "morning_orders": new_morning,
            "afternoon_orders": new_afternoon,
            "skip_count": (len(morning_orders) - len(new_morning)) + (len(afternoon_orders) - len(new_afternoon)),
            "boost_count": _count(new_morning, 1.2) + _count(new_afternoon, 1.2),
            "cut_count": _count(new_morning, 0.5) + _count(new_afternoon, 0.5),
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

        # === 将 Qlib 信号注入 SignalFusion ===
        if signals and hasattr(self, 'signal_fusion'):
            try:
                from alpha.qlib_signal_adapter import pd as qlib_pd
                # 构造等权信号序列（用于融合）
                signal_series = qlib_pd.Series(list(signals.values()), index=list(signals.keys()))
                self.signal_fusion.inject_qlib_signal(signal_series)
                logger.info(f"Qlib 信号已注入 SignalFusion: {len(signals)} 个标的")
            except Exception as e:
                logger.warning(f"Qlib 信号注入 SignalFusion 失败: {e}")

        return signals

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
        logger.info("=" * 60)
        logger.info(f"Phase 6: 智能执行 ({'模拟盘' if self.sim_mode else 'MockBroker'})")
        logger.info("=" * 60)

        # === 信号校验 ===
        action = signal.get("action", "")
        if action not in ("BUILD_PLAN",):
            logger.info(f"信号动作 {action}, 无建仓订单, 跳过执行")
            self.state["phases"]["execute"] = {"status": "PASS", "fills": [], "action": action}
            return []

        morning_orders = signal.get("morning_orders", [])
        afternoon_orders = signal.get("afternoon_orders", [])
        if not morning_orders and not afternoon_orders:
            logger.info("无订单可执行")
            self.state["phases"]["execute"] = {"status": "PASS", "fills": []}
            return []

        # === 模拟盘模式 ===
        if self.sim_mode and self.sim_engine is not None:
            return self._execute_sim_mode(signal, morning_orders, afternoon_orders)

        # === DRY-RUN 模式 ===
        if self.dry_run:
            logger.info("DRY-RUN 模式, 仅生成指令不执行")
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
            self.state["phases"]["execute"] = {"status": "PASS", "fills": dry_orders}
            return dry_orders

        # === MockBroker 执行 ===
        try:
            broker = MockBroker(price_dict=dict(self.config.MOCK_PRICES))
            sor = SmartOrderRouter(broker, self.ntp)

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
            }
            return all_fills

        except Exception as e:
            logger.error(f"执行失败: {e}", exc_info=True)
            self.state["phases"]["execute"] = {"status": "FAIL", "error": str(e)}
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
        }
        return all_fills

    def _execute_sim_batch(self,
                           orders: List[Dict[str, Any]],
                           session: str) -> List[Dict[str, Any]]:
        """执行一批模拟盘订单（按股票/期货拆分）"""
        if not orders:
            return []

        stock_orders = []
        futures_orders = []
        for order in orders:
            symbol = str(order.get("code", order.get("symbol", "")))
            market = self.sim_engine.router._detect_market(symbol)
            if market == "stock":
                stock_orders.append(self._normalize_sim_order(order, session=session))
            else:
                futures_orders.append(self._normalize_sim_order(order, session=session))

        fills: List[Dict[str, Any]] = []
        if stock_orders:
            logger.info("[模拟盘] 股票订单 %d 笔 @ %s", len(stock_orders), session)
            fills.extend(self.sim_engine.execute_stock_orders(stock_orders, session=session))
        if futures_orders:
            logger.info("[模拟盘] 期货订单 %d 笔 @ %s", len(futures_orders), session)
            fills.extend(self.sim_engine.execute_futures_orders(futures_orders, session=session))
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

        # 报告路径
        report_dir = self.config.REPORT_DIR / self.trade_date.replace("-", "/") \
            if "\\" in str(self.config.REPORT_DIR) \
            else self.config.REPORT_DIR / self.trade_date
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
        json_path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info(f"状态 JSON: {json_path}")

        return report_path

    # --------------------------------------------------------
    # Phase 8: 自主学习量化训练 (新增)
    #   - 每日盘后自动训练 LightGBM + XGBoost 集成模型
    #   - 37 个特征 (技术指标 + 截面因子)
    #   - 模型持久化到 models/autolearn/{symbol}/
    #   - 生成 ensemble_signals.json 供次日交易使用
    # --------------------------------------------------------
    def phase_autolearn(self) -> bool:
        """自主学习量化训练"""
        logger.info("=" * 60)
        logger.info(f"Phase 8: 自主学习量化训练 @ {self.trade_date}")
        logger.info("=" * 60)

        if not AUTOLEARN_READY:
            logger.warning("autolearn_trainer 模块未就绪, 跳过训练")
            self.state["phases"]["autolearn"] = {
                "status": "SKIP",
                "reason": "autolearn_trainer 模块未导入",
            }
            return True

        try:
            # 执行训练 (7 天内不重训)
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
    # 主流程
    # --------------------------------------------------------
    def run(self, only_phase: Optional[str] = None) -> Dict[str, Any]:
        """执行完整工作流"""
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
            ("signal", self.phase_signal),
            ("execute", lambda: self.phase_execute(self.state.get("phases", {}).get("signal", {}))),
            ("report", self.phase_report),
            ("autolearn", self.phase_autolearn),
        ]

        for phase_name, phase_func in phases:
            if only_phase and phase_name != only_phase:
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
                        choices=["check", "calibrate", "market", "risk", "hedge", "signal", "execute", "report", "autolearn"],
                        help="仅执行指定阶段")

    args = parser.parse_args()

    workflow = DailyWorkflow(
        trade_date=args.date,
        capital=args.capital,
        dry_run=args.dry_run,
        sim_mode=args.sim,
    )
    state = workflow.run(only_phase=args.phase)

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
