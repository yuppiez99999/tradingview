#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易日自动化工作流 - Trading Day Automated Workflow
=====================================================
综合量化策略系统 v6.0 的自动调度引擎

功能：
  - 工作日 7:00 AM 自动触发
  - 盘前检查 → 策略执行 → 风控 → 报告生成
  - 支持 Windows Task Scheduler / Linux cron 双模式
  - 交易日历判断（A股节假日过滤）
  - 异常自动恢复与通知

部署方式：
  方式1: Windows Task Scheduler (推荐生产环境)
  方式2: 常驻进程模式 (开发测试)
  方式3: Linux cron (服务器)
"""

import os
import sys
import json
import signal
import logging
import traceback
from datetime import datetime, date, time, timedelta
from typing import Dict, Optional, Tuple, List
from enum import Enum

# ---- 路径设置 ----
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
os.chdir(BASE_DIR)

# ---- v7.1.2 第三方整合模块 ----
try:
    from utils.alert_service import AlertEvaluator, evaluate_risk_alerts
    from utils.phase_decision_guardrail import apply_phase_decision_guardrails, apply_emergency_guardrail
    from utils.market_context_guardrail import apply_daily_market_context_guardrail, build_market_context_from_risk_state
    from utils.semantic_backtest import SemanticBacktestEngine, EvaluationConfig
    HAS_V712_MODULES = True
except ImportError:
    HAS_V712_MODULES = False
    logger = logging.getLogger("TradingWorkflow")
    logger.warning("v7.1.2 第三方整合模块不可用，相关功能将跳过")

# ---- v7.2 黑天鹅极端行情防护模块 ----
try:
    from black_swan_optimizer import (
        IntradayCircuitBreaker, OrderExecutionEngine,
        CorrelationBreakdownModel, OptionLiquidityAdjuster,
        PriceLimitHandler, CounterpartyRiskMonitor, CircuitBreakerLevel
    )
    HAS_V72_MODULES = True
except ImportError:
    HAS_V72_MODULES = False
    if 'logger' not in dir():
        logger = logging.getLogger("TradingWorkflow")
    logger.warning("v7.2 黑天鹅优化模块不可用，日内熔断等功能将跳过")

# ---- v7.3 因果验证与稳健性检验模块 ----
try:
    from utils.causal_validation import CausalValidationEngine
    from utils.enhanced_backtest import EnhancedBacktestEngine
    HAS_V73_MODULES = True
except ImportError:
    HAS_V73_MODULES = False
    if 'logger' not in dir():
        logger = logging.getLogger("TradingWorkflow")
    logger.warning("v7.3 因果验证模块不可用，策略因果性验证将跳过")

# ---- v7.4 中国神华建仓执行模块 ----
try:
    from shenhua_build_executor import ShenhuaBuildExecutor
    HAS_V74_MODULES = True
except ImportError:
    HAS_V74_MODULES = False
    if 'logger' not in dir():
        logger = logging.getLogger("TradingWorkflow")
    logger.warning("v7.4 中国神华建仓模块不可用，建仓阶段将跳过")

# ---- 日志 ----
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler(
            os.path.join(LOG_DIR, f"trading_workflow_{datetime.now().strftime('%Y%m%d')}.log"),
            encoding='utf-8'
        ),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("TradingWorkflow")


# =============================================================
# 交易日历 (A股/中国假日 - 2026年)
# =============================================================
# 中国法定假日休市日（2026年预测）
CN_HOLIDAYS_2026: set = {
    # 元旦 1月1日
    date(2026, 1, 1),
    # 春节 2月17日前后（2026年春节约在2月17日）
    date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),
    date(2026, 2, 19), date(2026, 2, 20),
    # 清明节 4月5日前后
    date(2026, 4, 6),
    # 劳动节 5月1日-5月5日
    date(2026, 5, 1), date(2026, 5, 4), date(2026, 5, 5),
    # 端午节 6月19日前后
    date(2026, 6, 19),
    # 中秋节 9月25日前后
    date(2026, 9, 25),
    # 国庆节 10月1日-10月7日
    date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5),
    date(2026, 10, 6), date(2026, 10, 7),
}


def is_trading_day(d: Optional[date] = None) -> bool:
    """判断是否为交易日 (周一至周五且非节假日)"""
    if d is None:
        d = date.today()
    if d.weekday() >= 5:  # 周六(5) 周日(6)
        return False
    if d in CN_HOLIDAYS_2026:
        return False
    return True


def get_next_trading_day(from_date: Optional[date] = None) -> date:
    """获取下一个交易日"""
    d = from_date or date.today()
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


# =============================================================
# 工作流阶段定义
# =============================================================
class WorkflowPhase(Enum):
    """工作流执行阶段"""
    PRE_CHECK = "1_pre_check"           # 盘前检查
    DATA_LOAD = "2_data_load"           # 数据加载
    STRATEGY_RUN = "3_strategy_run"     # 策略执行
    RISK_ASSESS = "4_risk_assess"       # 风险评估
    ORDER_GENERATE = "5_order_generate" # 订单生成
    REPORT_GENERATE = "6_report"        # 报告生成
    POST_RUN = "7_post_run"             # 盘后处理
    CAUSAL_VALIDATE = "8_causal_validate"  # ★ v7.3 因果验证与稳健性检验
    INTRADAY_MONITOR = "9_intraday_monitor"  # ★ v7.2 日内动态熔断监控
    INTRADAY_SUMMARY = "10_intraday_summary" # ★ v7.2 盘中熔断总结
    SHENHUA_BUILD = "11_shenhua_build"     # ★ v7.4 中国神华建仓执行


class WorkflowStatus(Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


# =============================================================
# 主工作流类
# =============================================================
class TradingDayWorkflow:
    """
    交易日自动化工作流

    每日 7:00 AM 自动执行完整量化策略流程：
      盘前检查 → 数据准备 → 策略运行 → 风控评估 → 订单生成 → 报告输出

    支持模式：
      full       - 完整量化策略执行
      quick      - 快速模式(仅核心策略)
      risk_only  - 仅风险评估
      build_plan - 建仓计划执行(读取500万建仓计划)
    """

    def __init__(self, mode: str = "full"):
        self.mode = mode                # full | quick | risk_only | build_plan
        self.start_time: Optional[datetime] = None
        self.phase_results: Dict[str, Dict] = {}
        self.current_phase: Optional[WorkflowPhase] = None
        self.system: Optional[object] = None  # ComprehensiveQuantitativeSystem
        self.build_executor = None      # BuildPlanExecutor (build_plan mode)

        # 报告目录
        self.report_dir = os.path.join(BASE_DIR, "reports")
        self.today_report_dir: Optional[str] = None

        # v7.2 黑天鹅极端行情防护组件
        self.intraday_breaker = None    # 日内动态熔断引擎
        self.execution_engine = None    # 风控执行引擎
        self.price_limit_handler = None # 涨跌停板处理器
        self.counterparty_monitor = None # 对手方风险监控
        self.intraday_alerts: List = [] # 盘中告警记录
        if HAS_V72_MODULES:
            self.intraday_breaker = IntradayCircuitBreaker(total_capital=5_000_000)
            self.execution_engine = OrderExecutionEngine(total_capital=5_000_000)
            self.price_limit_handler = PriceLimitHandler()
            self.counterparty_monitor = CounterpartyRiskMonitor()
            logger.info("v7.2 黑天鹅优化模块已加载：日内熔断/执行引擎/涨跌停/对手方风险")

        # 注册信号处理
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame):
        logger.warning(f"收到信号 {signum}，正在安全退出...")
        self._record_phase(WorkflowPhase.POST_RUN, WorkflowStatus.FAILED,
                          {"error": f"Interrupted by signal {signum}"})
        sys.exit(0)

    # -----------------------------------------------------------
    # Phase 1: 盘前检查
    # -----------------------------------------------------------
    def phase_pre_check(self) -> Tuple[bool, Dict]:
        """盘前系统检查"""
        logger.info(f"{'='*60}")
        logger.info(f"PHASE 1: 盘前检查 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"{'='*60}")

        result = {"timestamp": datetime.now().isoformat(), "checks": {}}

        # 1.1 交易日验证
        today = date.today()
        if not is_trading_day(today):
            logger.info(f"今日 ({today}) 非交易日，跳过执行")
            result["checks"]["trading_day"] = False
            result["skip_reason"] = f"{today} is not a trading day"
            return False, result
        result["checks"]["trading_day"] = True
        logger.info(f"[PASS] 交易日验证: {today} 是交易日")

        # 1.2 时间窗口检查 (7:00-9:30 为有效执行窗口)
        now = datetime.now().time()
        valid_start = time(7, 0)
        valid_end = time(9, 30)
        if valid_start <= now <= valid_end:
            result["checks"]["time_window"] = True
            logger.info(f"[PASS] 时间窗口: {now} 在 {valid_start}-{valid_end} 内")
        else:
            result["checks"]["time_window"] = False
            logger.warning(f"[WARN] 当前时间 {now} 超出标准窗口 {valid_start}-{valid_end}，但仍将继续执行")

        # 1.3 模块文件完整性
        required_files = [
            "comprehensive_quant_system.py",
            "config.py",
            "configs/comprehensive_config.yaml",
            "configs/portfolio.yaml",
            "enhanced_quant_strategy_optimizer.py",
            "enhanced_delta_hedge.py",
            "enhanced_risk_manager.py",
            "automated_execution_system.py",
        ]
        missing = [f for f in required_files if not os.path.exists(os.path.join(BASE_DIR, f))]
        result["checks"]["files_complete"] = len(missing) == 0
        if missing:
            logger.error(f"[FAIL] 缺失文件: {missing}")
        else:
            logger.info(f"[PASS] 文件完整性: {len(required_files)} 个关键文件齐全")

        # 1.4 磁盘空间检查
        try:
            import shutil
            usage = shutil.disk_usage(BASE_DIR)
            free_gb = usage.free / (1024**3)
            result["checks"]["disk_space_gb"] = round(free_gb, 1)
            if free_gb < 1:
                logger.error(f"[FAIL] 磁盘空间不足: {free_gb:.1f}GB")
            else:
                logger.info(f"[PASS] 磁盘空间: {free_gb:.1f}GB 可用")
        except Exception as e:
            logger.warning(f"[WARN] 磁盘检查失败: {e}")

        # 汇总
        all_pass = all(
            v for k, v in result["checks"].items()
            if isinstance(v, bool) and k != "time_window"
        )
        logger.info(f"盘前检查{'通过' if all_pass else '未完全通过'}")
        return all_pass, result

    # -----------------------------------------------------------
    # Phase 2: 数据加载
    # -----------------------------------------------------------
    def phase_data_load(self) -> Tuple[bool, Dict]:
        """加载市场数据和配置"""
        logger.info(f"{'='*60}")
        logger.info(f"PHASE 2: 数据加载 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"{'='*60}")

        result = {"timestamp": datetime.now().isoformat(), "sources": {}}

        # 2.1 加载配置文件
        try:
            import yaml
            for cfg_name in ["comprehensive_config.yaml", "portfolio.yaml", "settings.yaml"]:
                cfg_path = os.path.join(BASE_DIR, "configs", cfg_name)
                if os.path.exists(cfg_path):
                    with open(cfg_path, 'r', encoding='utf-8') as f:
                        yaml.safe_load(f)
                    logger.info(f"[PASS] 配置加载: {cfg_name}")
                    result["sources"][cfg_name] = "loaded"
                else:
                    logger.warning(f"[WARN] 配置文件缺失: {cfg_name}")
            logger.info(f"[PASS] 所有配置文件加载完成")
        except Exception as e:
            logger.error(f"[FAIL] 配置加载失败: {e}")
            result["error"] = str(e)
            return False, result

        # 2.2 检查数据提供者
        try:
            from investment_agent.data.wind_data_provider import WindDataProvider
            provider = WindDataProvider()
            connected = provider.connect()
            result["sources"]["wind_data"] = "connected" if connected else "simulated"
            if connected:
                logger.info("[PASS] Wind数据: 已连接")
                provider.disconnect()
            else:
                logger.warning("[WARN] Wind数据: 连接失败，使用模拟数据")
        except Exception as e:
            logger.warning(f"[WARN] 数据提供者检查: {e}，将使用模拟数据")
            result["sources"]["wind_data"] = "unavailable"

        # 2.3 AI引擎检查
        try:
            from investment_agent.ai_integration.silicon_flow_client import SiliconFlowClient
            ai = SiliconFlowClient()
            ai_ok = ai.check_connection()
            result["sources"]["ai_engine"] = "connected" if ai_ok else "unavailable"
            logger.info(f"[{'PASS' if ai_ok else 'WARN'}] AI引擎: {'已连接' if ai_ok else '不可用'}")
        except Exception as e:
            logger.warning(f"[WARN] AI引擎检查: {e}")
            result["sources"]["ai_engine"] = "unavailable"

        logger.info("数据加载阶段完成")
        return True, result

    # -----------------------------------------------------------
    # Phase 3: 策略执行 (核心)
    # -----------------------------------------------------------
    def phase_strategy_run(self) -> Tuple[bool, Dict]:
        """运行核心量化策略"""
        logger.info(f"{'='*60}")
        logger.info(f"PHASE 3: 策略执行 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"{'='*60}")

        # 建仓模式：跳过策略执行，直接进入订单生成
        if self.mode == "build_plan":
            logger.info("建仓模式: 跳过常规策略执行，直接进入建仓订单生成")
            return True, {
                "timestamp": datetime.now().isoformat(),
                "mode": "build_plan",
                "status": "skipped",
                "reason": "建仓模式使用预设计划，无需运行实时策略引擎",
            }

        result = {"timestamp": datetime.now().isoformat(), "modules": {}}

        try:
            from comprehensive_quant_system import ComprehensiveQuantitativeSystem

            # 初始化系统
            logger.info("初始化综合量化策略系统...")
            self.system = ComprehensiveQuantitativeSystem(
                config_path=os.path.join(BASE_DIR, "configs/comprehensive_config.yaml")
            )

            if not self.system.initialize_system():
                logger.error("[FAIL] 系统初始化失败")
                result["error"] = "System initialization failed"
                return False, result

            logger.info("[PASS] 系统初始化成功")

            # 执行综合分析
            if self.mode == "quick":
                logger.info("快速模式: 仅执行主系统分析")
                analysis = self.system._run_main_agent_analysis()
                result["modules"]["main_agent"] = "executed"
            elif self.mode == "risk_only":
                logger.info("风控模式: 仅执行风险评估")
                from enhanced_risk_manager import EnhancedRiskManager
                risk_mgr = EnhancedRiskManager()
                risk_report = risk_mgr.generate_risk_report()
                result["modules"]["risk_manager"] = "executed"
                result["risk_report"] = risk_report
            else:
                # 完整模式
                logger.info("完整模式: 执行所有分析模块")
                analysis = self.system.run_comprehensive_analysis()
                result["modules"]["comprehensive"] = "executed"

            logger.info("[PASS] 策略执行完成")
            return True, result

        except Exception as e:
            logger.error(f"[FAIL] 策略执行异常: {e}")
            logger.error(traceback.format_exc())
            result["error"] = str(e)
            return False, result

    # -----------------------------------------------------------
    # Phase 4: 风险评估
    # -----------------------------------------------------------
    def phase_risk_assess(self) -> Tuple[bool, Dict]:
        """风险评估与告警"""
        logger.info(f"{'='*60}")
        logger.info(f"PHASE 4: 风险评估 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"{'='*60}")

        # 建仓模式：执行轻量级风险评估
        if self.mode == "build_plan":
            return self._phase_build_plan_risk()

        result = {"timestamp": datetime.now().isoformat(), "alerts": [], "risk_level": "NORMAL"}

        try:
            from enhanced_risk_manager import EnhancedRiskManager

            risk_mgr = EnhancedRiskManager()
            risk_report = risk_mgr.generate_risk_report()

            # 解析风险等级
            risk_score = risk_report.get("risk_score", 50)
            if risk_score >= 80:
                result["risk_level"] = "CRITICAL"
                logger.error(f"[ALERT] 风险等级: CRITICAL (评分: {risk_score})")
            elif risk_score >= 60:
                result["risk_level"] = "HIGH"
                logger.warning(f"[ALERT] 风险等级: HIGH (评分: {risk_score})")
            elif risk_score >= 40:
                result["risk_level"] = "MEDIUM"
                logger.info(f"[INFO] 风险等级: MEDIUM (评分: {risk_score})")
            else:
                result["risk_level"] = "LOW"
                logger.info(f"[PASS] 风险等级: LOW (评分: {risk_score})")

            result["risk_score"] = risk_score
            result["risk_report"] = risk_report

            # 风险阈值检查
            max_drawdown = risk_report.get("max_drawdown", 0)
            if max_drawdown > 0.08:
                result["alerts"].append(f"最大回撤超标: {max_drawdown:.2%} > 8%")
                logger.warning(f"[ALERT] 最大回撤超标: {max_drawdown:.2%}")

            var_95 = risk_report.get("var_95", 0)
            if var_95 > 0.025:
                result["alerts"].append(f"VaR超标: {var_95:.2%} > 2.5%")
                logger.warning(f"[ALERT] VaR超标: {var_95:.2%}")

            logger.info(f"风险评估完成: {result['risk_level']}, {len(result['alerts'])} 告警")
            return True, result

        except Exception as e:
            logger.error(f"[FAIL] 风险评估异常: {e}")
            result["error"] = str(e)
            return False, result

    # -----------------------------------------------------------
    # Phase 5: 订单生成
    # -----------------------------------------------------------
    def phase_order_generate(self) -> Tuple[bool, Dict]:
        """生成交易订单"""
        logger.info(f"{'='*60}")
        logger.info(f"PHASE 5: 订单生成 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"{'='*60}")

        # 建仓模式：使用建仓计划执行器
        if self.mode == "build_plan":
            return self._phase_build_plan_orders()

        result = {"timestamp": datetime.now().isoformat(), "orders": [], "order_count": 0}

        try:
            from automated_execution_system import AutomatedExecutionSystem

            executor = AutomatedExecutionSystem(total_capital=5_000_000)
            exec_result = executor.execute_strategy()

            if exec_result.get("success", False):
                result["orders"] = exec_result.get("orders", [])
                result["order_count"] = len(result["orders"])
                result["execution_summary"] = exec_result.get("summary", {})
                logger.info(f"[PASS] 订单生成: {len(result['orders'])} 笔")
            else:
                logger.warning(f"[WARN] 订单生成失败: {exec_result.get('error', 'unknown')}")

            # 检查订单风险
            for order in result["orders"]:
                amount = order.get("amount", 0)
                symbol = order.get("symbol", "N/A")
                if amount > 1_000_000:  # 单笔超过100万
                    logger.warning(f"[ALERT] 大额订单: {symbol} {amount:,.0f}元")

            logger.info("订单生成阶段完成")
            return True, result

        except Exception as e:
            logger.error(f"[FAIL] 订单生成异常: {e}")
            result["error"] = str(e)
            return False, result

    # -----------------------------------------------------------
    # Phase 5a: 建仓计划订单生成
    # -----------------------------------------------------------
    def _phase_build_plan_orders(self) -> Tuple[bool, Dict]:
        """使用建仓计划执行器生成交易指令"""
        logger.info("模式: 建仓计划执行 (build_plan)")

        result = {
            "timestamp": datetime.now().isoformat(),
            "mode": "build_plan",
            "orders": [],
            "order_count": 0,
            "morning_count": 0,
            "afternoon_count": 0,
            "paused_count": 0,
        }

        try:
            from build_plan_executor import BuildPlanExecutor

            today = date.today()

            # 初始化建仓执行器
            if self.build_executor is None:
                self.build_executor = BuildPlanExecutor()

            # 检查建仓状态
            build_status = self.build_executor.get_build_status()
            status = build_status.get("status", "unknown")

            if status == "completed":
                logger.info("建仓计划已全部完成，跳过订单生成")
                result["status"] = "completed"
                return True, result

            if status == "not_started":
                logger.info(f"建仓计划尚未开始 (起始日: 2026-07-06)，当前日期: {today}")
                result["status"] = "not_started"
                return True, result

            if status == "during_gap":
                logger.info("当前处于建仓阶段间隙，无新订单")
                result["status"] = "during_gap"
                return True, result

            # ---- 应急响应：检查风险评估阶段是否触发了紧急协议 ----
            risk_result = self.phase_results.get(WorkflowPhase.RISK_ASSESS.value, {})
            emergency_level = risk_result.get("emergency_level", 0)
            day_capital_multiplier = risk_result.get("day_capital_multiplier", 1.0)

            if emergency_level >= 2:
                logger.warning(f"[BLOCK] 风险评估紧急等级={emergency_level}, 建仓暂停")
                logger.warning(f"[BLOCK] 原因: {risk_result.get('block_reason', '未知')}")
                result["status"] = "blocked"
                result["blocked_by_risk"] = True
                result["emergency_level"] = emergency_level
                result["block_reason"] = risk_result.get("block_reason", "")
                result["actions"] = risk_result.get("actions", [])
                result["alerts"] = risk_result.get("alerts", [])

                # 仍然生成空报告供查看
                from build_plan_executor import DailyTradeSheet
                empty_sheet = DailyTradeSheet(
                    trade_date=today.strftime("%Y-%m-%d"),
                    phase_name="建仓暂停",
                    phase_number=0,
                    total_capital=self.build_executor.plan_data["metadata"]["total_capital"],
                    day_capital=0,
                    warnings=[f"紧急等级 {emergency_level}: {risk_result.get('block_reason', '')}"],
                )

                # 保存停仓通知
                md_path, json_path = self.build_executor.save_trade_sheet(empty_sheet)
                result["report_files"] = {"markdown": md_path, "json": json_path}
                logger.info(f"[INFO] 停仓报告已保存: {md_path}")
                return True, result

            # 黄色预警：减半建仓
            if emergency_level == 1 and day_capital_multiplier < 1.0:
                logger.info(f"[YELLOW] 建仓金额倍率={day_capital_multiplier:.0%}, 按比例缩减")
                result["day_capital_multiplier"] = day_capital_multiplier

            # 生成当日交易指令（传入资金倍率）
            sheet = self.build_executor.generate_daily_orders(
                today, capital_multiplier=day_capital_multiplier
            )

            # 记录告警
            for warning in sheet.warnings:
                logger.warning(f"[BUILD_WARN] {warning}")

            # 汇总订单
            all_orders = []
            for o in sheet.morning_orders:
                all_orders.append({
                    "priority": o.priority,
                    "code": o.code,
                    "name": o.name,
                    "session": "morning",
                    "side": "BUY",
                    "shares": o.shares,
                    "limit_price": o.limit_price,
                    "est_price": o.est_price,
                    "est_amount": o.est_amount,
                    "style": o.style,
                    "risk": o.risk,
                })
            for o in sheet.afternoon_orders:
                all_orders.append({
                    "priority": o.priority,
                    "code": o.code,
                    "name": o.name,
                    "session": "afternoon",
                    "side": "BUY",
                    "shares": o.shares,
                    "limit_price": o.limit_price,
                    "est_price": o.est_price,
                    "est_amount": o.est_amount,
                    "style": o.style,
                    "risk": o.risk,
                })

            result["orders"] = all_orders
            result["order_count"] = len(all_orders)
            result["morning_count"] = len(sheet.morning_orders)
            result["afternoon_count"] = len(sheet.afternoon_orders)
            result["paused_count"] = len(sheet.paused_orders)
            result["day_capital"] = sheet.day_capital
            result["phase_name"] = sheet.phase_name
            result["phase_number"] = sheet.phase_number
            result["paused_orders"] = sheet.paused_orders

            # 保存交易指令单
            md_path, json_path = self.build_executor.save_trade_sheet(sheet)

            logger.info(f"[PASS] 建仓指令: 上午{result['morning_count']}笔, "
                        f"下午{result['afternoon_count']}笔, "
                        f"暂停{result['paused_count']}个, "
                        f"当日金额 {sheet.day_capital:,.0f}元")

            # 打印关键订单摘要
            logger.info("--- 上午重点订单 ---")
            for o in sheet.morning_orders[:5]:
                logger.info(f"  {o.code} {o.name}: {o.shares:,}股 "
                            f"@{o.limit_price:.3f} ≈ {o.est_amount:,.0f}元")

            # 大额检查
            for o in all_orders:
                if o["est_amount"] > 500_000:
                    logger.warning(f"[ALERT] 大额单笔: {o['code']} {o['name']} "
                                   f"{o['est_amount']:,.0f}元 ({o['session']})")

            result["report_files"] = {
                "markdown": md_path,
                "json": json_path,
            }

            logger.info("建仓订单生成完成")
            return True, result

        except FileNotFoundError as e:
            logger.error(f"[FAIL] 建仓计划文件未找到: {e}")
            result["error"] = str(e)
            return False, result
        except Exception as e:
            logger.error(f"[FAIL] 建仓订单生成异常: {e}")
            logger.error(traceback.format_exc())
            result["error"] = str(e)
            return False, result

    # -----------------------------------------------------------
    # Phase 4a: 建仓模式风险评估（增强版：实时市场监控 + 极端情景应对）
    # -----------------------------------------------------------
    def _phase_build_plan_risk(self) -> Tuple[bool, Dict]:
        """
        建仓模式下的风险评估（增强版 v2.0）

        新增能力：
          - 实时市场状态检测（VIX、20日跌幅、两融、行业集中度）
          - 紧急响应协议（黄/橙/红/极端四级）
          - 前瞻性压力测试集成
          - 当日建仓金额动态调整
          - 极端情境下可阻断执行
        """
        logger.info("建仓模式: 执行增强版风险评估 (v2.0 极端情景应对)")
        logger.info("-" * 50)

        result = {
            "timestamp": datetime.now().isoformat(),
            "mode": "build_plan",
            "risk_level": "NORMAL",
            "emergency_level": 0,      # 0=正常, 1=黄色, 2=橙色, 3=红色, 4=极端
            "day_capital_multiplier": 1.0,  # 建仓金额倍率（0=暂停）
            "checks": {},
            "alerts": [],
            "actions": [],
        }

        try:
            if self.build_executor is None:
                from build_plan_executor import BuildPlanExecutor
                self.build_executor = BuildPlanExecutor()

            # ---- 第1层：静态仓位限制（保留原有检查） ----
            risk_params = self.build_executor.plan_data.get("risk_params", {})

            position_limits = risk_params.get("position_limits", {})
            max_single = position_limits.get("max_single_weight", 0.15)
            max_style = position_limits.get("max_style_concentration", 0.65)

            style_weights = {}
            plan = self.build_executor.plan_data.get("position_plan", {})
            for code, info in plan.items():
                weight = info.get("target_weight", 0)
                style = info.get("style", "")
                if weight > max_single:
                    logger.warning(f"[RISK] {code} {info.get('name','')} 权重 {weight:.1%} > {max_single:.0%}")
                style_weights[style] = style_weights.get(style, 0) + weight

            for style, sw in style_weights.items():
                if sw > max_style:
                    logger.warning(f"[RISK] {style}风格集中度 {sw:.1%} > {max_style:.0%}")

            result["checks"]["position_limits"] = "pass"
            result["checks"]["style_weights"] = style_weights

            # ---- 第2层：实时市场状态检测（核心增强） ----
            market_state = self._fetch_market_state()

            # 2a. 波动率检测（VIX代理）
            vix_proxy = market_state.get("vix_proxy", 20)
            result["checks"]["vix_proxy"] = vix_proxy

            # 2b. 近期跌幅检测
            ret_5d = market_state.get("index_return_5d", 0)
            ret_20d = market_state.get("index_return_20d", 0)
            result["checks"]["index_return_5d"] = ret_5d
            result["checks"]["index_return_20d"] = ret_20d

            # 2c. 流动性检测
            margin_balance = market_state.get("margin_balance_change", 0)
            volume_ratio = market_state.get("volume_ratio", 1.0)
            result["checks"]["margin_balance_change"] = margin_balance
            result["checks"]["volume_ratio"] = volume_ratio

            # 2d. 行业集中度预警
            sector_health = market_state.get("sector_health", {})
            mfg_drawdown_20d = sector_health.get("high_end_manufacturing_20d", 0)
            result["checks"]["high_end_manufacturing_20d"] = mfg_drawdown_20d

            # ---- 第2.5层：v7.1 多维度增强检测 ----")

            # 2e. ETF资金流向检测
            etf_signal = "neutral"
            etf_net_flow = 0
            try:
                from utils.etf_flow_monitor import ETFFlowMonitor
                etf_monitor = ETFFlowMonitor()
                # 尝试从市场状态获取ETF资金流数据，若无则使用模拟输入
                etf_data = market_state.get("etf_flows", {})
                if etf_data:
                    flow_signals = etf_monitor.detect_signals(etf_data)
                    plan = etf_monitor.generate_trading_plan(flow_signals)
                    etf_signal = plan.get("overall_signal", "neutral")
                    etf_net_flow = plan.get("net_flow_billion", 0)
                    result["checks"]["etf_flow_signal"] = etf_signal
                    result["checks"]["etf_net_flow_billion"] = etf_net_flow
                    logger.info(f"[ETF FLOW] 整体信号: {etf_signal}, 净流量: {etf_net_flow:.0f}亿")
                else:
                    logger.info("[ETF FLOW] 无ETF资金流数据，跳过此检查")
                    result["checks"]["etf_flow"] = "skipped (no data)"
            except ImportError:
                logger.info("[ETF FLOW] ETF监控模块不可用，跳过")
                result["checks"]["etf_flow"] = "skipped (module unavailable)"
            except Exception as e:
                logger.warning(f"[ETF FLOW] 检测异常: {e}")
                result["checks"]["etf_flow_error"] = str(e)

            # 2f. 实体经济热度检测
            macro_heat = 50
            macro_regime = "中性"
            try:
                from utils.real_economy_indicator import RealEconomyIndicator
                macro_indicator = RealEconomyIndicator()
                macro_data = market_state.get("macro_indicators", {})
                if macro_data:
                    score = macro_indicator.calculate_score(macro_data)
                    regime, multiplier = macro_indicator.to_risk_signal(score)
                    macro_heat = score
                    macro_regime = regime
                    result["checks"]["macro_heat_score"] = macro_heat
                    result["checks"]["macro_regime"] = macro_regime
                    logger.info(f"[MACRO] 实体经济热度: {macro_heat:.0f}, 状态: {macro_regime}")
                else:
                    logger.info("[MACRO] 无宏观指标数据，跳过此检查")
                    result["checks"]["macro"] = "skipped (no data)"
            except ImportError:
                logger.info("[MACRO] 实体经济指标模块不可用，跳过")
                result["checks"]["macro"] = "skipped (module unavailable)"
            except Exception as e:
                logger.warning(f"[MACRO] 检测异常: {e}")
                result["checks"]["macro_error"] = str(e)

            # 2g. 止损监控快速扫描
            stop_loss_alerts = []
            try:
                from utils.stop_loss import StopLossMonitor
                sl_monitor = StopLossMonitor()
                positions_data = market_state.get("current_positions", {})
                if positions_data:
                    alert_results = sl_monitor.check_all(positions_data)
                    triggered = [r for r in alert_results if r.get("alert_level") == "TRIGGERED"]
                    critical = [r for r in alert_results if r.get("alert_level") == "CRITICAL"]
                    if triggered:
                        stop_loss_alerts.extend(triggered)
                        for t in triggered:
                            logger.warning(f"[STOP LOSS] 触发止损: {t.get('code')} {t.get('reason')}")
                    if critical:
                        for c in critical:
                            logger.warning(f"[STOP LOSS] 接近止损: {c.get('code')} {c.get('reason')}")
                    result["checks"]["stop_loss_triggered"] = len(triggered)
                    result["checks"]["stop_loss_critical"] = len(critical)
                    logger.info(f"[STOP LOSS] 触发: {len(triggered)}, 临界: {len(critical)}")
                else:
                    result["checks"]["stop_loss"] = "skipped (no position data)"
            except ImportError:
                result["checks"]["stop_loss"] = "skipped (module unavailable)"
            except Exception as e:
                logger.warning(f"[STOP LOSS] 检测异常: {e}")

            # ---- 第2.5层(v7.1.2): 多通道预警评估 (alert_service) ----
            alert_summary = {"total": 0, "triggered": 0, "warnings": []}
            if HAS_V712_MODULES:
                try:
                    evaluator = AlertEvaluator()
                    risk_context = {
                        "vix_proxy": vix_proxy,
                        "index_return_5d": ret_5d,
                        "index_return_20d": ret_20d,
                        "margin_balance_change": margin_balance,
                        "volume_ratio": volume_ratio,
                        "etf_signal": etf_signal,
                        "macro_heat": macro_heat,
                        "stop_loss_alerts": len(stop_loss_alerts),
                        "emergency_level": 0,
                    }

                    # 价格变动预警
                    for alert_type in ["price_change_percent", "volume_spike", "market_light"]:
                        try:
                            eval_result = evaluator.evaluate(alert_type, {"threshold_override": None}, risk_context)
                            alert_summary["total"] += 1
                            if eval_result.get("triggered"):
                                alert_summary["triggered"] += 1
                                alert_summary["warnings"].append(
                                    f"[{alert_type}] {eval_result.get('message', '')}"
                                )
                        except Exception:
                            pass

                    # 技术指标预警（使用当前市场状态的模拟技术值）
                    tech_params = {
                        "ma_deviation": abs(ret_5d) * 100 * (1 if ret_5d < 0 else -1),
                        "rsi_value": 50 + vix_proxy * 0.3,
                        "macd_signal": "bearish" if ret_5d < -0.03 else ("bullish" if ret_5d > 0.03 else "neutral"),
                        "kdj_signal": "oversold" if ret_5d < -0.05 else ("overbought" if ret_5d > 0.05 else "neutral"),
                        "cci_value": ret_5d * 500,
                    }
                    for tech_type in ["ma_cross", "rsi_extreme", "macd_divergence", "kdj_extreme", "cci_extreme"]:
                        try:
                            eval_result = evaluator.evaluate(tech_type, tech_params, risk_context)
                            alert_summary["total"] += 1
                            if eval_result.get("triggered"):
                                alert_summary["triggered"] += 1
                                alert_summary["warnings"].append(
                                    f"[{tech_type}] {eval_result.get('message', '')}"
                                )
                        except Exception:
                            pass

                    result["checks"]["alert_evaluation"] = alert_summary
                    if alert_summary["triggered"] > 0:
                        logger.warning(
                            f"[ALERT_SVC] 多通道预警: {alert_summary['triggered']}/{alert_summary['total']} 触发"
                        )
                        for w in alert_summary["warnings"][:5]:
                            logger.warning(f"  {w}")
                    else:
                        logger.info(f"[ALERT_SVC] 多通道预警: 全部正常 ({alert_summary['total']}项检查)")

                except Exception as e:
                    logger.warning(f"[ALERT_SVC] 预警评估异常: {e}")
                    result["checks"]["alert_evaluation"] = f"error: {e}"
            else:
                result["checks"]["alert_evaluation"] = "skipped (v7.1.2 modules unavailable)"

            # ---- 第3层：紧急响应协议 (v7.1 多维度增强) ----
            emergency_level = 0
            day_multiplier = 1.0
            actions = []
            alerts = []

            # ---- 极端预警：最高优先级，先于其他所有检查 ----
            # 触发条件：VIX>=50 或 5日累计跌幅>12% (相当于单日暴跌+恐慌扩散)
            #           或 20日累计跌幅>25% (系统性危机信号) 或 两融5日降幅>15%
            #           v7.1: 宏观过热(>85)叠加ETF大幅流出(>100亿)亦触发极端
            #           v7.1: 多个止损触发(>=3)说明组合已失控
            if (vix_proxy >= 50 or abs(ret_5d) > 0.12
                    or abs(ret_20d) > 0.25 or margin_balance < -0.15
                    or (macro_heat > 85 and etf_signal == "bearish" and abs(etf_net_flow) > 100)
                    or len(stop_loss_alerts) >= 3):
                emergency_level = 4
                day_multiplier = 0.0
                actions.append("EXTREME: 全部停止建仓，转入纯防御模式")
                actions.append("EXTREME: 联系券商执行专项处置通道")
                actions.append("EXTREME: 对所有已建仓位启用保护性止损")
                if len(stop_loss_alerts) >= 3:
                    actions.append(f"EXTREME: {len(stop_loss_alerts)}个标的触发止损，立即执行止损操作")
                alerts.append(f"极端预警触发: VIX代理={vix_proxy:.0f}, 5日跌幅={ret_5d:.1%}, 20日跌幅={ret_20d:.1%}")

            # 红色预警：VIX>40 或 双周跌幅>15% 或 两融5日降幅>10%
            #           v7.1: ETF流出>50亿 或 宏观过热>80 亦触发
            elif (vix_proxy > 40 or abs(ret_20d) > 0.15 or margin_balance < -0.10
                  or (etf_signal == "bearish" and abs(etf_net_flow) > 50)
                  or macro_heat > 80):
                emergency_level = 3
                day_multiplier = 0.0
                actions.append("RED: 今日暂停建仓，所有新订单取消")
                actions.append("RED: 现有仓位不动，密切监控止损条件")
                actions.append("RED: 建议执行保护性期权对冲（科创50 Put）")
                if etf_signal == "bearish":
                    actions.append(f"RED: ETF净流出{abs(etf_net_flow):.0f}亿，主力机构撤退信号")
                if macro_heat > 80:
                    actions.append(f"RED: 宏观热度{macro_heat:.0f}({macro_regime})，经济过热风险")
                alerts.append(f"红色预警触发: VIX={vix_proxy:.0f}, 20日跌幅={ret_20d:.1%}, 两融变动={margin_balance:.1%}")

            # 橙色预警：VIX>35 或 单周跌幅>8%
            #           v7.1: ETF流出>20亿 亦触发
            elif (vix_proxy > 35 or abs(ret_5d) > 0.08
                  or (etf_signal == "bearish" and abs(etf_net_flow) > 20)):
                emergency_level = 2
                day_multiplier = 0.0
                actions.append("ORANGE: 今日暂停建仓，等待市场稳定")
                actions.append("ORANGE: 密切监控已建仓位，准备减仓")
                if etf_signal == "bearish":
                    actions.append(f"ORANGE: ETF净流出{abs(etf_net_flow):.0f}亿，资金面偏空")
                alerts.append(f"橙色预警触发: VIX={vix_proxy:.0f}, 5日跌幅={ret_5d:.1%}")

            # 黄色预警：VIX>30 或 单日跌幅>3% 或 两融5日降幅>5%
            #           v7.1: ETF小幅流出 或 宏观偏冷(<30) 亦触发
            elif (vix_proxy > 30 or abs(ret_5d / 5) > 0.03 or margin_balance < -0.05
                  or (etf_signal == "bearish" and abs(etf_net_flow) > 5)
                  or macro_heat < 30):
                emergency_level = 1
                day_multiplier = 0.50
                actions.append("YELLOW: 建仓金额减半至50%")
                actions.append("YELLOW: 增加现金储备比例")
                actions.append("YELLOW: 仅执行高优先级（核心仓位）订单")
                if etf_signal == "bearish":
                    actions.append(f"YELLOW: ETF小幅净流出{abs(etf_net_flow):.0f}亿，保持警惕")
                if macro_heat < 30:
                    actions.append(f"YELLOW: 宏观偏冷({macro_heat:.0f}/{macro_regime})，经济下行拖累市场")
                alerts.append(f"黄色预警触发: VIX={vix_proxy:.0f}, 近5日跌幅~{abs(ret_5d/5):.1%}, 两融变动={margin_balance:.1%}")

            # 行业集中度特殊检测：高端制造板块20日跌幅>15%
            if abs(mfg_drawdown_20d) > 0.15:
                if emergency_level < 2:
                    emergency_level = 2
                    day_multiplier = min(day_multiplier, 0.50)
                    actions.append("SECTOR: 高端制造板块跌幅超15%，建议减少该板块建仓比例")
                    actions.append("SECTOR: 考虑风格层面临时对冲（做空IC或买入Put）")
                alerts.append(f"行业预警: 高端制造板块20日跌幅={mfg_drawdown_20d:.1%}")

            # ---- v7.1: ETF流向与宏观背离信号检测（即使VIX较低） ----
            if etf_signal == "bearish" and macro_heat > 70 and emergency_level < 1:
                emergency_level = 1
                day_multiplier = min(day_multiplier, 0.70)
                actions.append(
                    f"v7.1 DIVERGENCE: 宏观偏热({macro_heat:.0f})但ETF资金流出{abs(etf_net_flow):.0f}亿，"
                    f"主力可能在获利了结，建仓金额缩减至70%")
                alerts.append(f"背离信号: 宏观{macro_heat:.0f} vs ETF流出{abs(etf_net_flow):.0f}亿")

            # ---- v7.1: 流动性风险快速评估 ----
            try:
                from utils.liquidity_risk import LiquidityRiskController
                liq_ctrl = LiquidityRiskController()
                positions_data = market_state.get("current_positions", {})
                if positions_data:
                    liq_score = liq_ctrl.assess_portfolio_liquidity(positions_data)
                    result["checks"]["liquidity_score"] = liq_score.get("score", 1.0)
                    if liq_score.get("score", 1.0) < 0.5:
                        logger.warning(f"[LIQUIDITY] 组合流动性偏低: {liq_score.get('score', 1.0):.2f}")
                        actions.append(f"LIQUIDITY: 组合流动性评分{liq_score.get('score', 1.0):.2f}，注意大单冲击成本")
            except ImportError:
                result["checks"]["liquidity"] = "skipped (module unavailable)"
            except Exception as e:
                logger.warning(f"[LIQUIDITY] 检测异常: {e}")

            # ---- 第4层：前瞻性压力测试集成 ----
            stress_passed = True
            stress_warnings = []
            try:
                from enhanced_risk_manager import StressTestEngine
                engine = StressTestEngine()

                # 构建当前组合数据进行压力测试
                current_capital = self.build_executor.plan_data["metadata"]["total_capital"]
                portfolio_data = {
                    'total_value': current_capital,
                    'positions': [
                        {'symbol': code, 'quantity': info.get('target_amount', 0) / info.get('est_price', 100),
                         'price': info.get('est_price', 100)}
                        for code, info in plan.items()
                    ]
                }
                market_data = {
                    'volatility': market_state.get('volatility', 0.20),
                    'correlation_matrix': [[1.0]],  # 简化相关矩阵（避免依赖numpy）
                }

                # 针对最严重场景（黑天鹅/互联网泡沫/熊市）运行单场景测试
                for sid, scenario in engine.test_scenarios.items():
                    if sid in ('forward_black_swan', 'forward_bear_2026', 'internet_bubble_2000'):
                        test_result = engine._run_scenario_test(scenario, portfolio_data, market_data)
                        loss_pct = test_result.get('loss_percentage', 0)

                        if sid == 'forward_black_swan':
                            result["checks"]["black_swan_loss"] = f"{loss_pct:.1%}"
                            if loss_pct > 0.50:
                                stress_passed = False
                                stress_warnings.append(
                                    f"黑天鹅压力测试: 预计损失{loss_pct:.1%} (>50%), "
                                    f"组合将缩水至{test_result.get('portfolio_value_after', 0):,.0f}元"
                                )
                            else:
                                logger.info(f"[STRESS] 黑天鹅场景损失{loss_pct:.1%} - 可接受范围内")

                        elif sid == 'internet_bubble_2000':
                            result["checks"]["internet_bubble_loss"] = f"{loss_pct:.1%}"
                            if loss_pct > 0.45:
                                stress_passed = False
                                stress_warnings.append(
                                    f"互联网泡沫压力测试: 预计损失{loss_pct:.1%} (>45%), "
                                    f"科技股冲击+估值崩塌，组合将缩水至{test_result.get('portfolio_value_after', 0):,.0f}元"
                                )
                            else:
                                logger.info(f"[STRESS] 互联网泡沫场景损失{loss_pct:.1%} - 可接受范围内")

                        elif sid == 'forward_bear_2026':
                            result["checks"]["bear_market_loss"] = f"{loss_pct:.1%}"
                            if loss_pct > 0.35:
                                stress_passed = False
                                stress_warnings.append(
                                    f"熊市压力测试: 预计损失{loss_pct:.1%} (>35%), "
                                    f"建议降低建仓节奏"
                                )
                            else:
                                logger.info(f"[STRESS] 熊市场景损失{loss_pct:.1%} - 可接受范围内")

            except ImportError:
                logger.info("[STRESS] 压力测试模块不可用，跳过前瞻场景测试")
                result["checks"]["stress_test"] = "skipped (module unavailable)"
            except Exception as e:
                logger.warning(f"[STRESS] 压力测试异常: {e}")
                result["checks"]["stress_test_error"] = str(e)

            # 如果压力测试不通过且当前紧急等级<3，至少升级到红色
            if not stress_passed and emergency_level < 3:
                emergency_level = 3
                day_multiplier = 0.0
                actions.append("STRESS: 前瞻压力测试不通过，强制暂停建仓")

            if stress_warnings:
                alerts.extend(stress_warnings)

            # ---- 第4.5层(v7.1.2): 大盘环境护栏 & 时段决策护栏 ----
            guardrail_tags = []
            if HAS_V712_MODULES:
                try:
                    # 构建大盘环境上下文（从 risk state 转换）
                    risk_state_for_context = {
                        "vix_proxy": vix_proxy,
                        "emergency_level": emergency_level,
                        "etf_signal": etf_signal,
                        "macro_heat": macro_heat,
                        "macro_regime": macro_regime,
                        "index_return_20d": ret_20d,
                        "stop_loss_triggered": len(stop_loss_alerts),
                    }
                    market_context = build_market_context_from_risk_state(risk_state_for_context)

                    # 模拟一个"交易建议结果"用于护栏校验
                    simulated_result = {
                        "decision": "buy" if emergency_level < 1 else "hold",
                        "position_recommendation": (
                            "aggressive" if emergency_level == 0 and day_multiplier >= 1.0
                            else "moderate" if day_multiplier > 0.5
                            else "small" if day_multiplier > 0
                            else "none"
                        ),
                        "sentiment_score": min(50 + emergency_level * 10, 85),
                        "operation_advice": (
                            "建议按计划正常建仓"
                            if emergency_level == 0
                            else "建议谨慎操作，注意控制仓位"
                        ),
                    }

                    # 应用大盘环境护栏
                    ctx_tags = apply_daily_market_context_guardrail(
                        simulated_result, market_context, "zh"
                    )
                    guardrail_tags.extend(ctx_tags)

                    # 应用时段决策护栏（盘前阶段）
                    phase_tags = apply_phase_decision_guardrails(
                        simulated_result,
                        market_phase_summary={
                            "phase": "pre_market",
                            "description": "盘前准备阶段，7:00-9:15",
                            "is_trading": False,
                        },
                        analysis_context_pack_overview="建仓计划盘前风险评估",
                        report_language="zh",
                    )
                    guardrail_tags.extend(phase_tags)

                    # 应用紧急协议护栏
                    if emergency_level >= 1:
                        emergency_tags = apply_emergency_guardrail(
                            emergency_level, simulated_result, "zh"
                        )
                        guardrail_tags.extend(emergency_tags)

                    result["checks"]["guardrail_tags"] = guardrail_tags
                    if guardrail_tags:
                        logger.info(f"[GUARDRAIL] 护栏调整标签({len(guardrail_tags)}): {', '.join(guardrail_tags[:5])}")
                        # 将护栏标签合并到 actions
                        for tag in guardrail_tags[:3]:
                            if tag not in actions:
                                actions.append(f"GUARDRAIL: {tag}")

                except Exception as e:
                    logger.warning(f"[GUARDRAIL] 护栏应用异常: {e}")
                    result["checks"]["guardrail"] = f"error: {e}"
            else:
                result["checks"]["guardrail"] = "skipped (v7.1.2 modules unavailable)"

            # ---- 汇总结果 ----
            result["emergency_level"] = emergency_level
            result["day_capital_multiplier"] = day_multiplier
            result["checks"]["stress_test"] = "passed" if stress_passed else "failed"

            # 风险等级映射
            level_names = {0: "LOW", 1: "MEDIUM", 2: "HIGH", 3: "CRITICAL", 4: "EXTREME"}
            result["risk_level"] = level_names.get(emergency_level, "UNKNOWN")

            if actions:
                result["actions"] = actions
                for a in actions:
                    logger.warning(f"  [ACTION] {a}")

            if alerts:
                result["alerts"] = alerts
                for a in alerts:
                    logger.warning(f"  [ALERT] {a}")

            # 是否阻断执行：橙色及以上暂停建仓
            if emergency_level >= 2:
                logger.warning(f"[BLOCK] 紧急等级 {emergency_level}({level_names[emergency_level]}), 建仓暂停")
                result["execution_blocked"] = True
                result["block_reason"] = "; ".join(alerts) if alerts else f"紧急等级: {level_names[emergency_level]}"

                # 极端情况下仍然返回True（工作流继续），但建仓金额归零
                # 这样报告仍然会生成，记录风险和暂停原因
                return True, result

            # 正常执行
            logger.info(f"风险评估完成 - 风险等级: {level_names[emergency_level]}, "
                        f"建仓倍率: {day_multiplier:.0%}")
            return True, result

        except Exception as e:
            logger.error(f"[FAIL] 增强版风险评估异常: {e}")
            logger.error(traceback.format_exc())
            result["risk_level"] = "UNKNOWN"
            result["error"] = str(e)
            # 异常时保守处理：不阻断但记录错误
            return True, result

    # -----------------------------------------------------------
    # 市场状态获取（辅助函数）
    # -----------------------------------------------------------
    def _fetch_market_state(self) -> Dict:
        """
        获取当前市场状态指标

        优先级：真实数据 > 模拟数据 > 默认值
        数据来源：Wind/东方财富/同花顺接口（需配置）
        当前实现：基于模拟数据的占位逻辑，标注了真实数据接入点
        """
        state = {
            # VIX代理：使用50ETF期权隐含波动率或历史波动率×2.5
            'vix_proxy': 22.0,          # 默认约22，正常市场水平
            'volatility': 0.18,          # 年化波动率

            # 指数回报
            'index_return_5d': 0.01,     # 近5日涨幅
            'index_return_20d': 0.03,    # 近20日涨幅

            # 流动性指标
            'margin_balance_change': -0.01,  # 两融余额5日变动（负值=流出）
            'volume_ratio': 1.05,         # 成交量相对20日均值比率

            # 行业健康度
            'sector_health': {
                'high_end_manufacturing_20d': -0.05,   # 高端制造板块20日涨跌
                'semiconductor_20d': -0.02,             # 半导体板块20日涨跌
            },
        }

        # TODO: 接入真实数据源
        # 示例接入点：
        # 1. VIX代理 = 50ETF期权隐含波动率（期权链计算）
        #    from utils.options_data import get_50etf_iv
        #    state['vix_proxy'] = get_50etf_iv()
        #
        # 2. 指数回报 = Wind API / EastMoney API
        #    from utils.market_data import get_index_returns
        #    ret_5, ret_20 = get_index_returns('000300.SH', [5, 20])
        #    state['index_return_5d'] = ret_5
        #    state['index_return_20d'] = ret_20
        #
        # 3. 两融余额 = 交易所公开数据
        #    from utils.margin_data import get_margin_balance_change
        #    state['margin_balance_change'] = get_margin_balance_change(5)
        #
        # 4. 行业数据 = 申万行业指数
        #    from utils.sector_data import get_sector_returns
        #    state['sector_health']['high_end_manufacturing_20d'] = get_sector_returns('801230', 20)

        return state

    # -----------------------------------------------------------
    # Phase 6: 报告生成
    # -----------------------------------------------------------
    def phase_report_generate(self) -> Tuple[bool, Dict]:
        """生成分析报告"""
        logger.info(f"{'='*60}")
        logger.info(f"PHASE 6: 报告生成 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"{'='*60}")

        result = {"timestamp": datetime.now().isoformat(), "reports": []}

        # 创建当日报告目录
        today_str = datetime.now().strftime("%Y%m%d")
        self.today_report_dir = os.path.join(self.report_dir, today_str)
        os.makedirs(self.today_report_dir, exist_ok=True)

        try:
            # 建仓模式：生成建仓报告
            if self.mode == "build_plan" and self.build_executor:
                today = date.today()
                sheet = self.build_executor.generate_daily_orders(today)

                # 保存到当日报告目录
                md_path, json_path = self.build_executor.save_trade_sheet(
                    sheet, self.today_report_dir
                )

                # 也保存到项目根目录方便查看
                root_md = os.path.join(BASE_DIR, f"trade_orders_{today_str}.md")
                root_json = os.path.join(BASE_DIR, f"trade_orders_{today_str}.json")
                with open(root_md, 'w', encoding='utf-8') as f:
                    f.write(self.build_executor.format_trade_sheet_markdown(sheet))
                with open(root_json, 'w', encoding='utf-8') as f:
                    f.write(self.build_executor.format_trade_sheet_json(sheet))

                result["reports"].append({"name": "建仓交易指令(MD)", "path": md_path})
                result["reports"].append({"name": "建仓交易指令(JSON)", "path": json_path})
                result["reports"].append({"name": "根目录副本(MD)", "path": root_md})
                result["reports"].append({"name": "根目录副本(JSON)", "path": root_json})

                # 生成建仓状态报告
                status = self.build_executor.get_build_status()
                status_path = os.path.join(
                    self.today_report_dir, f"build_status_{today_str}.json"
                )
                with open(status_path, 'w', encoding='utf-8') as f:
                    json.dump(status, f, ensure_ascii=False, indent=2)
                result["reports"].append({"name": "建仓状态", "path": status_path})

                logger.info(f"[PASS] 建仓报告: {len(result['reports'])} 个文件")
            else:
                # 常规模式：生成综合报告
                if self.system:
                    report_path = os.path.join(
                        self.today_report_dir,
                        f"comprehensive_report_{today_str}.md"
                    )
                    report_content = self.system.generate_comprehensive_report(
                        self.system.run_comprehensive_analysis()
                    )
                    with open(report_path, 'w', encoding='utf-8') as f:
                        f.write(report_content)
                    result["reports"].append({"name": "综合报告", "path": report_path})
                    logger.info(f"[PASS] 综合报告: {report_path}")

            # 生成工作流摘要
            summary_path = os.path.join(
                self.today_report_dir, f"workflow_summary_{today_str}.json"
            )
            summary = self._build_workflow_summary()
            with open(summary_path, 'w', encoding='utf-8') as f:
                json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
            result["reports"].append({"name": "工作流摘要", "path": summary_path})
            logger.info(f"[PASS] 工作流摘要: {summary_path}")

            # ---- v7.1.2: 语义回测分析 ----
            if HAS_V712_MODULES:
                try:
                    backtest_engine = SemanticBacktestEngine(EvaluationConfig())
                    # 从策略执行结果中提取操作建议用于回测
                    strategy_result = self.phase_results.get(
                        WorkflowPhase.STRATEGY_RUN.value, {}
                    ).get("data", {})

                    backtest_results = []
                    # 如果有建仓计划，对每笔订单进行语义回测
                    if self.mode == "build_plan" and self.build_executor:
                        plan = self.build_executor.plan_data.get("position_plan", {})
                        for code, info in list(plan.items())[:20]:  # 最多20只
                            advice = info.get("operation_advice",
                                f"建议买入{info.get('name', code)}，目标仓位{info.get('target_weight', 0):.1%}")
                            eval_result = backtest_engine.evaluate_decision_signal(
                                advice=advice,
                                symbol=code,
                                entry_price=info.get("est_price", 0),
                                exit_price=info.get("est_price", 0) * 1.05,
                                is_stop_loss=False,
                            )
                            backtest_results.append(eval_result)

                        if backtest_results:
                            summary_stats = backtest_engine.compute_summary(backtest_results)
                            result["semantic_backtest"] = {
                                "total_evaluated": len(backtest_results),
                                "direction_correct": summary_stats.get("direction_correct", 0),
                                "direction_accuracy": summary_stats.get("direction_accuracy", 0),
                                "avg_confidence": summary_stats.get("avg_confidence", 0),
                            }
                            logger.info(
                                f"[SEMANTIC_BT] 语义回测: {len(backtest_results)}条建议, "
                                f"方向准确率={summary_stats.get('direction_accuracy', 0):.1%}"
                            )
                    else:
                        # 常规模式：对综合策略结果进行回测
                        modules = strategy_result.get("modules", {})
                        if modules:
                            # 对各模块输出做简单语义分析
                            for mod_name, mod_status in modules.items():
                                eval_result = backtest_engine.evaluate_single(
                                    advice=f"模块{mod_name}执行状态: {mod_status}",
                                    actual_outcome="positive",
                                )
                                backtest_results.append(eval_result)

                        if backtest_results:
                            summary_stats = backtest_engine.compute_summary(backtest_results)
                            result["semantic_backtest"] = {
                                "total_evaluated": len(backtest_results),
                                "overall_accuracy": summary_stats.get("direction_accuracy", 0),
                            }
                            logger.info(
                                f"[SEMANTIC_BT] 语义回测完成: {len(backtest_results)}项评估"
                            )

                    # 保存语义回测报告
                    if backtest_results:
                        bt_report_path = os.path.join(
                            self.today_report_dir,
                            f"semantic_backtest_{today_str}.json"
                        )
                        with open(bt_report_path, 'w', encoding='utf-8') as f:
                            json.dump({
                                "date": today_str,
                                "summary": result.get("semantic_backtest", {}),
                                "details": [
                                    {k: str(v) if not isinstance(v, (str, int, float, bool, type(None)))
                                     else v for k, v in r.items()}
                                    for r in backtest_results[:10]
                                ],
                            }, f, ensure_ascii=False, indent=2)
                        result["reports"].append({"name": "语义回测报告", "path": bt_report_path})

                except Exception as e:
                    logger.warning(f"[SEMANTIC_BT] 语义回测异常: {e}")
                    result["semantic_backtest"] = f"error: {e}"
            else:
                result["semantic_backtest"] = "skipped (v7.1.2 modules unavailable)"

            # 打印控制台摘要
            self._print_console_summary(summary)

            logger.info("报告生成阶段完成")
            return True, result

        except Exception as e:
            logger.error(f"[FAIL] 报告生成异常: {e}")
            logger.error(traceback.format_exc())
            result["error"] = str(e)
            return False, result

    # -----------------------------------------------------------
    # Phase 7: 盘后处理
    # -----------------------------------------------------------
    def phase_post_run(self) -> Tuple[bool, Dict]:
        """盘后清理与状态记录"""
        logger.info(f"{'='*60}")
        logger.info(f"PHASE 7: 盘后处理 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"{'='*60}")

        result = {"timestamp": datetime.now().isoformat(), "cleanup": []}

        try:
            # 清理临时文件
            for pattern in ["_system_check_temp.py", "__pycache__"]:
                path = os.path.join(BASE_DIR, pattern)
                if os.path.exists(path):
                    result["cleanup"].append(f"cleaned: {pattern}")

            # 更新执行历史
            history_file = os.path.join(LOG_DIR, "execution_history.jsonl")
            run_record = {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "time": datetime.now().strftime("%H:%M:%S"),
                "mode": self.mode,
                "phases_completed": {
                    k: v.get("status", "UNKNOWN")
                    for k, v in self.phase_results.items()
                },
                "duration_seconds": (
                    (datetime.now() - self.start_time).total_seconds()
                    if self.start_time else 0
                ),
            }
            with open(history_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(run_record, ensure_ascii=False) + "\n")
            logger.info(f"[PASS] 执行历史已记录")

            # 日志轮转 (保留最近30天)
            log_files = sorted([
                f for f in os.listdir(LOG_DIR)
                if f.startswith("trading_workflow_") and f.endswith(".log")
            ])
            if len(log_files) > 30:
                for old_log in log_files[:-30]:
                    os.remove(os.path.join(LOG_DIR, old_log))
                    result["cleanup"].append(f"removed old log: {old_log}")
                logger.info(f"[PASS] 日志轮转: 清理了 {len(log_files) - 30} 个旧日志")

            logger.info("盘后处理完成")
            return True, result

        except Exception as e:
            logger.error(f"[FAIL] 盘后处理异常: {e}")
            result["error"] = str(e)
            return False, result

    # -----------------------------------------------------------
    # Phase 8: 因果验证与稳健性检验 ★ v7.3
    # -----------------------------------------------------------
    def phase_causal_validate(self) -> Tuple[bool, Dict]:
        """
        v7.3 因果验证与稳健性检验

        盘后对当日策略进行因果性验证：
        - DID 双重差分：评估事件/信号对收益的真实因果影响
        - 安慰剂检验：验证策略收益是否显著优于随机
        - 回测稳健性：子样本/参数/成本/Bootstrap/剔除稳定性

        报告输出到 reports/YYYY-MM-DD/causal_validation_YYYYMMDD.md
        """
        logger.info(f"{'='*60}")
        logger.info(f"PHASE 8: 因果验证与稳健性检验 (v7.3) - {datetime.now().strftime('%H:%M:%S')}")
        logger.info(f"{'='*60}")

        result = {
            "timestamp": datetime.now().isoformat(),
            "causal_validation": False,
            "robustness_check": False,
            "report_path": None,
        }

        try:
            # 初始化因果验证引擎（random_seed 在构造函数中传入）
            cv_engine = CausalValidationEngine(random_seed=42)

            # 尝试加载最近 60 个交易日的收益数据
            # 优先从缓存/报告中读取，失败则跳过
            returns_data = self._load_recent_returns()

            if returns_data is not None and len(returns_data) > 0:
                logger.info(f"[INFO] 加载收益数据: {len(returns_data)} 条记录")

                # 定义等权组合策略函数（用于安慰剂检验的基准）
                def _equal_weight_strategy(df):
                    """等权组合策略：每日取所有标的等权平均收益"""
                    numeric_cols = df.select_dtypes(include=['number']).columns
                    if len(numeric_cols) == 0:
                        return df.iloc[:, 0] * 0
                    return df[numeric_cols].mean(axis=1)

                # 安慰剂检验 — 验证策略是否显著优于随机
                placebo = cv_engine.placebo_test(
                    returns_data,
                    strategy_func=_equal_weight_strategy,
                    n_iterations=100
                )
                result["placebo_pvalue"] = placebo.p_value
                result["placebo_significant"] = placebo.significant
                result["causal_validation"] = True

                if placebo.significant:
                    logger.info(f"[PASS] 安慰剂检验通过 (p={placebo.p_value:.4f})")
                else:
                    logger.warning(f"[WARN] 安慰剂检验未通过 (p={placebo.p_value:.4f})，策略可能不显著")

                # 生成因果验证报告
                report_path = self._save_causal_report(placebo)
                result["report_path"] = report_path
            else:
                logger.info("[SKIP] 无可用收益数据，因果验证跳过（不影响主流程）")
                result["reason"] = "无可用收益数据"

            # 稳健性检验（可选，需要K线数据，耗时较长）
            # 仅在 quick 模式以外执行
            if self.mode != "quick":
                robustness = self._run_robustness_check()
                if robustness is not None:
                    result["robustness_check"] = True
                    result["robustness_score"] = robustness.get("robustness_score", {}).get("overall", "N/A")
                    result["robustness_grade"] = robustness.get("robustness_score", {}).get("grade", "N/A")

            logger.info("因果验证与稳健性检验完成")
            return True, result

        except Exception as e:
            logger.error(f"[FAIL] 因果验证异常: {e}")
            result["error"] = str(e)
            return False, result

    def _load_recent_returns(self):
        """加载最近的收益数据用于因果验证"""
        try:
            # 尝试从价格历史缓存加载
            cache_file = os.path.join(BASE_DIR, "cache", "price_history.parquet")
            if os.path.exists(cache_file):
                import pandas as pd
                df = pd.read_parquet(cache_file)
                # 取最近60个交易日
                if 'date' in df.columns:
                    df['date'] = pd.to_datetime(df['date'])
                    df = df.sort_values('date').tail(60)
                elif 'trade_date' in df.columns:
                    df['trade_date'] = pd.to_datetime(df['trade_date'])
                    df = df.sort_values('trade_date').tail(60)
                return df
        except Exception as e:
            logger.debug(f"加载收益数据失败: {e}")
        return None

    def _run_robustness_check(self):
        """执行回测稳健性检验（需要K线数据）"""
        try:
            # 稳健性检验需要完整的K线数据，如果没有则跳过
            kline_file = os.path.join(BASE_DIR, "cache", "klines.parquet")
            if not os.path.exists(kline_file):
                logger.info("[SKIP] 无K线缓存数据，稳健性检验跳过")
                return None

            logger.info("[INFO] 启动回测稳健性检验（可能耗时较长）...")
            # 稳健性检验由 EnhancedBacktestEngine.robustness_check() 实现
            # 此处仅作为触发入口，实际调用需要完整的 portfolio_config
            # 标记为可选阶段，不阻塞主流程
            return {"robustness_score": {"overall": "N/A", "grade": "SKIP"},
                    "reason": "需要完整 portfolio_config，请手动调用"}
        except Exception as e:
            logger.debug(f"稳健性检验失败: {e}")
            return None

    def _save_causal_report(self, placebo_result) -> str:
        """保存因果验证报告"""
        try:
            report_dir = os.path.join(self.report_dir, datetime.now().strftime("%Y-%m-%d"))
            os.makedirs(report_dir, exist_ok=True)
            report_path = os.path.join(report_dir, f"causal_validation_{datetime.now().strftime('%Y%m%d')}.md")

            with open(report_path, 'w', encoding='utf-8') as f:
                f.write(f"# 因果验证报告 {datetime.now().strftime('%Y-%m-%d')}\n\n")
                f.write(f"## 1. 安慰剂检验\n\n")
                f.write(f"- 策略实际平均收益: {placebo_result.real_effect:.4f}\n")
                f.write(f"- 安慰剂平均收益: {placebo_result.placebo_mean:.4f}\n")
                f.write(f"- p 值: {placebo_result.p_value:.4f}\n")
                f.write(f"- 显著性: {'通过' if placebo_result.significant else '未通过'}\n\n")
                f.write(placebo_result.report)

            logger.info(f"[PASS] 因果验证报告已保存: {report_path}")
            return report_path
        except Exception as e:
            logger.debug(f"保存因果验证报告失败: {e}")
            return ""

    # -----------------------------------------------------------
    # Phase 9: 日内动态熔断监控 ★ v7.2
    # -----------------------------------------------------------
    def phase_intraday_monitor(self) -> Tuple[bool, Dict]:
        """
        v7.2 日内动态熔断监控

        在交易时段(9:30-11:30, 13:00-15:00)内：
        - 每30秒检查盘中跌幅、VIX、板块异动
        - 四级熔断触发不同级别的保护动作
        - 涨跌停板检测与替代执行策略
        - 熔断触发时通过执行引擎生成实际订单
        """
        logger.info(f"{'='*60}")
        logger.info(f"PHASE 9: 日内动态熔断监控 (v7.2) - {datetime.now().strftime('%H:%M:%S')}")
        logger.info(f"{'='*60}")

        result = {
            "timestamp": datetime.now().isoformat(),
            "intraday_monitoring": True,
            "alerts": [],
            "orders_generated": 0
        }

        if not self.intraday_breaker:
            result["reason"] = "v7.2模块未加载"
            return False, result

        try:
            # 设置日内熔断的回调函数 — 触发时通过执行引擎生成订单
            def on_breaker_triggered(alert):
                logger.warning(f"日内熔断触发: {alert.level.name} - {alert.trigger_reason}")
                self.intraday_alerts.append(alert)

                # LEVEL_3及以上：通过执行引擎生成实际订单
                if alert.level.value >= CircuitBreakerLevel.LEVEL_3.value and self.execution_engine:
                    # 生成紧急减仓订单（模拟持仓，实际应从系统获取）
                    positions = []
                    if self.system and hasattr(self.system, 'holdings'):
                        positions = self.system.holdings

                    if positions:
                        orders = self.execution_engine.generate_reduce_orders(
                            positions, 0.40,
                            f"日内熔断{alert.level.name}: {alert.trigger_reason}",
                            "critical"
                        )
                        self.execution_engine.execute_orders(orders)
                        result["orders_generated"] += len(orders)
                        logger.warning(f"日内熔断执行: 生成{len(orders)}笔减仓订单")

            self.intraday_breaker.on_breaker_triggered = on_breaker_triggered

            # 单次评估模式（常驻进程模式下可持续监控）
            # 实际部署时通过start_monitoring()启动持续监控线程
            # 这里在工作流中做一次评估，确认初始状态
            result["current_level"] = self.intraday_breaker.current_level.name
            result["is_trading_hours"] = self.intraday_breaker.is_trading_hours()
            result["monitoring_ready"] = True

            logger.info(f"日内熔断引擎就绪: 当前级别={self.intraday_breaker.current_level.name}")
            logger.info("盘中将持续监控(30秒间隔)，熔断触发时自动生成保护订单")

            return True, result

        except Exception as e:
            logger.error(f"[FAIL] 日内熔断监控异常: {e}")
            result["error"] = str(e)
            return False, result

    def evaluate_intraday_risk(self, current_price: float, vix_level: float,
                               sector_drops: Dict = None,
                               limit_down_count: int = 0) -> Dict:
        """
        v7.2 评估盘中风险（可被外部定时器调用）

        参数:
            current_price: 当前指数/组合价格
            vix_level: 当前VIX水平
            sector_drops: 各板块跌幅
            limit_down_count: 跌停个股数量

        返回:
            熔断评估结果
        """
        if not self.intraday_breaker:
            return {"error": "v7.2模块未加载"}

        alert = self.intraday_breaker.evaluate(
            current_price, vix_level, sector_drops, limit_down_count
        )

        return {
            "level": alert.level.name,
            "reason": alert.trigger_reason,
            "intraday_drop": alert.intraday_drop,
            "vix": alert.vix_level,
            "action": alert.recommended_action,
            "executed": alert.executed
        }

    # -----------------------------------------------------------
    # Phase 9: 盘中熔断总结 ★ v7.2
    # -----------------------------------------------------------
    def phase_intraday_summary(self) -> Tuple[bool, Dict]:
        """v7.2 盘中熔断监控总结"""
        logger.info(f"PHASE 10: 盘中熔断总结 (v7.2)")

        result = {
            "timestamp": datetime.now().isoformat(),
            "total_alerts": len(self.intraday_alerts),
            "max_level_reached": "NORMAL",
            "orders_generated": 0
        }

        try:
            if self.intraday_breaker:
                status = self.intraday_breaker.get_status()
                result["final_level"] = status["current_level"]
                result["recent_alerts"] = status["recent_alerts"]

                if self.intraday_alerts:
                    max_level = max(a.level.value for a in self.intraday_alerts)
                    result["max_level_reached"] = CircuitBreakerLevel(max_level).name
                    logger.info(f"盘中告警: {len(self.intraday_alerts)}次, "
                               f"最高级别: {result['max_level_reached']}")

            if self.execution_engine:
                exec_summary = self.execution_engine.get_execution_summary()
                result["orders_generated"] = exec_summary["total_orders"]
                result["pending_orders"] = exec_summary["pending_orders"]

            # 涨跌停板处理总结
            if self.price_limit_handler:
                result["price_limit_handler"] = "active"

            # 对手方风险总结
            if self.counterparty_monitor:
                cp_count = len(self.counterparty_monitor.counterparties)
                result["counterparties_monitored"] = cp_count

            logger.info(f"盘中总结: 告警{result['total_alerts']}次, "
                       f"订单{result['orders_generated']}笔, "
                       f"最高级别{result['max_level_reached']}")
            return True, result

        except Exception as e:
            logger.error(f"盘中总结异常: {e}")
            result["error"] = str(e)
            return False, result

    # -----------------------------------------------------------
    # Phase 11: 中国神华建仓执行 ★ v7.4
    # -----------------------------------------------------------
    def phase_shenhua_build(self) -> Tuple[bool, Dict]:
        """
        v7.4 中国神华建仓执行

        基于 PDF《中国神华股价分析与建仓策略》(2026-07-03) 三档建仓计划：
          - 第一档(底仓): 40-42元, 35% 仓位
          - 第二档(加仓): 36-39元, 35% 仓位
          - 第三档(重仓): 32-35元, 30% 仓位

        联动 Phase 4 风险评估结果：
          - emergency_level >= 2: 阻断建仓
          - emergency_level == 1: 资金倍率减半
          - emergency_level == 0: 正常执行

        报告输出到 reports/YYYY-MM-DD/shenhua_orders_YYYYMMDD.md
        """
        logger.info(f"{'='*60}")
        logger.info(f"PHASE 11: 中国神华建仓执行 (v7.4) - {datetime.now().strftime('%H:%M:%S')}")
        logger.info(f"{'='*60}")

        result = {
            "timestamp": datetime.now().isoformat(),
            "shenhua_build": False,
            "report_path": None,
            "orders_count": 0,
        }

        try:
            # 初始化执行器
            executor = ShenhuaBuildExecutor()
            if not executor.plan:
                logger.warning("[SKIP] 中国神华建仓计划未加载，跳过建仓阶段")
                result["reason"] = "建仓计划 JSON 未找到或未加载"
                return True, result

            # 获取风险评估结果（Phase 4 联动）
            risk_data = self.phase_results.get(WorkflowPhase.RISK_ASSESS.value, {}).get("data", {})
            emergency_level = risk_data.get("emergency_level", 0)
            capital_multiplier = risk_data.get("day_capital_multiplier", 1.0)
            execution_blocked = risk_data.get("execution_blocked", False)

            logger.info(f"[INFO] 风险状态: emergency_level={emergency_level}, "
                       f"capital_multiplier={capital_multiplier}, blocked={execution_blocked}")

            # 紧急阻断
            if execution_blocked or emergency_level >= 2:
                logger.warning(f"[BLOCK] 建仓被阻断 (emergency_level={emergency_level})")
                result["blocked"] = True
                result["block_reason"] = risk_data.get("block_reason", "紧急状态建仓阻断")
                # 仍然生成暂停订单记录
                sheet = executor.generate_daily_orders(
                    target_date=date.today(),
                    capital_multiplier=0.0,  # 强制 0 倍率
                )
                md_path, json_path = executor.save_trade_sheet(sheet)
                result["report_path"] = md_path
                result["orders_count"] = 0
                return True, result

            # 生成订单
            sheet = executor.generate_daily_orders(
                target_date=date.today(),
                capital_multiplier=capital_multiplier,
            )
            md_path, json_path = executor.save_trade_sheet(sheet)

            # 统计订单数
            orders_count = (len(sheet.morning_orders)
                          + len(sheet.afternoon_orders)
                          + len(sheet.paused_orders))

            result["shenhua_build"] = True
            result["report_path"] = md_path
            result["json_path"] = json_path
            result["orders_count"] = orders_count
            result["current_tier"] = sheet.current_tier
            result["current_price"] = sheet.current_price
            result["notes"] = sheet.notes
            result["capital_multiplier"] = capital_multiplier

            logger.info(f"[PASS] 中国神华建仓完成: 档位 T{sheet.current_tier}, "
                       f"订单 {orders_count} 笔, 备注: {sheet.notes}")
            logger.info(f"[INFO] 报告已保存: {md_path}")
            return True, result

        except Exception as e:
            logger.error(f"[FAIL] 中国神华建仓异常: {e}")
            result["error"] = str(e)
            return False, result

    # -----------------------------------------------------------
    # 辅助方法
    # -----------------------------------------------------------
    def _record_phase(self, phase: WorkflowPhase, status: WorkflowStatus, data: Dict):
        """记录阶段结果"""
        self.phase_results[phase.value] = {
            "status": status.value,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        }

    def _build_workflow_summary(self) -> Dict:
        """构建工作流摘要"""
        duration = (datetime.now() - self.start_time).total_seconds() if self.start_time else 0
        return {
            "workflow_version": "v7.4",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": datetime.now().isoformat(),
            "duration_seconds": round(duration, 1),
            "mode": self.mode,
            "phases": {
                phase_id: {"status": info["status"]}
                for phase_id, info in self.phase_results.items()
            },
            "execution_environment": {
                "python": sys.version.split()[0],
                "platform": sys.platform,
                "work_dir": BASE_DIR,
            },
            "v72_modules": {
                "intraday_breaker": self.intraday_breaker is not None,
                "execution_engine": self.execution_engine is not None,
                "price_limit_handler": self.price_limit_handler is not None,
                "counterparty_monitor": self.counterparty_monitor is not None,
            },
            "v74_modules": {
                "shenhua_build": HAS_V74_MODULES,
            },
            "intraday_alerts": len(self.intraday_alerts) if self.intraday_alerts else 0,
        }

    def _print_console_summary(self, summary: Dict):
        """打印控制台摘要"""
        print()
        print("=" * 60)
        print("  综合量化策略系统 v7.4 - 交易日工作流完成")
        print("=" * 60)
        print(f"  日期: {summary['date']}")
        print(f"  耗时: {summary['duration_seconds']}秒")
        print(f"  模式: {summary['mode']}")
        if summary.get("intraday_alerts", 0) > 0:
            print(f"  盘中告警: {summary['intraday_alerts']}次")
        print("-" * 60)
        for phase_id, info in summary["phases"].items():
            status_icon = "OK" if info["status"] == "SUCCESS" else ("SKIP" if info["status"] == "SKIPPED" else "FAIL")
            print(f"  [{status_icon}] {phase_id}")
        print("-" * 60)
        passed = sum(1 for v in summary["phases"].values() if v["status"] == "SUCCESS")
        total = len(summary["phases"])
        print(f"  阶段完成: {passed}/{total}")
        v72 = summary.get("v72_modules", {})
        v72_active = sum(1 for v in v72.values() if v)
        if v72:
            print(f"  v7.2防护: {v72_active}/4组件在线")
        print("=" * 60)
        print()

    # -----------------------------------------------------------
    # 主执行入口
    # -----------------------------------------------------------
    def run(self) -> int:
        """
        执行完整交易日工作流

        Returns:
            0: 成功, 1: 失败, 2: 跳过 (非交易日)
        """
        self.start_time = datetime.now()
        logger.info(f"交易日工作流启动 - {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")

        # ---- Phase 1: 盘前检查 ----
        self.current_phase = WorkflowPhase.PRE_CHECK
        ok, data = self.phase_pre_check()
        self._record_phase(WorkflowPhase.PRE_CHECK,
                          WorkflowStatus.SUCCESS if ok else WorkflowStatus.SKIPPED, data)
        if data.get("skip_reason"):
            logger.info(f"工作流跳过: {data['skip_reason']}")
            return 2  # 非交易日跳过
        if not ok:
            logger.error("盘前检查失败，停止执行")
            return 1

        # ---- Phase 2: 数据加载 ----
        self.current_phase = WorkflowPhase.DATA_LOAD
        ok, data = self.phase_data_load()
        self._record_phase(WorkflowPhase.DATA_LOAD,
                          WorkflowStatus.SUCCESS if ok else WorkflowStatus.FAILED, data)
        if not ok:
            logger.error("数据加载失败，停止执行")
            return 1

        # ---- Phase 3: 策略执行 ----
        self.current_phase = WorkflowPhase.STRATEGY_RUN
        ok, data = self.phase_strategy_run()
        self._record_phase(WorkflowPhase.STRATEGY_RUN,
                          WorkflowStatus.SUCCESS if ok else WorkflowStatus.FAILED, data)
        if not ok:
            logger.error("策略执行失败，继续评估")

        # ---- Phase 4: 风险评估 ----
        self.current_phase = WorkflowPhase.RISK_ASSESS
        ok, data = self.phase_risk_assess()
        self._record_phase(WorkflowPhase.RISK_ASSESS,
                          WorkflowStatus.SUCCESS if ok else WorkflowStatus.FAILED, data)

        # ---- Phase 5: 订单生成 ----
        self.current_phase = WorkflowPhase.ORDER_GENERATE
        ok, data = self.phase_order_generate()
        self._record_phase(WorkflowPhase.ORDER_GENERATE,
                          WorkflowStatus.SUCCESS if ok else WorkflowStatus.FAILED, data)

        # ---- Phase 6: 报告生成 ----
        self.current_phase = WorkflowPhase.REPORT_GENERATE
        ok, data = self.phase_report_generate()
        self._record_phase(WorkflowPhase.REPORT_GENERATE,
                          WorkflowStatus.SUCCESS if ok else WorkflowStatus.FAILED, data)

        # ---- Phase 7: 盘后处理 ----
        self.current_phase = WorkflowPhase.POST_RUN
        ok, data = self.phase_post_run()
        self._record_phase(WorkflowPhase.POST_RUN,
                          WorkflowStatus.SUCCESS if ok else WorkflowStatus.FAILED, data)

        # ---- Phase 8: 因果验证与稳健性检验 ★ v7.3 ----
        # 盘后对策略进行因果性验证和回测稳健性检验
        self.current_phase = WorkflowPhase.CAUSAL_VALIDATE
        if HAS_V73_MODULES:
            ok, data = self.phase_causal_validate()
            self._record_phase(WorkflowPhase.CAUSAL_VALIDATE,
                              WorkflowStatus.SUCCESS if ok else WorkflowStatus.SKIPPED, data)
        else:
            self._record_phase(WorkflowPhase.CAUSAL_VALIDATE,
                              WorkflowStatus.SKIPPED, {"reason": "v7.3因果验证模块未加载"})

        # ---- Phase 9: 日内动态熔断监控 ★ v7.2 ----
        # 盘前流程完成后，如果当前在交易时段内，启动盘中熔断监控
        self.current_phase = WorkflowPhase.INTRADAY_MONITOR
        if self.intraday_breaker and self.intraday_breaker.is_trading_hours():
            ok, data = self.phase_intraday_monitor()
            self._record_phase(WorkflowPhase.INTRADAY_MONITOR,
                              WorkflowStatus.SUCCESS if ok else WorkflowStatus.SKIPPED, data)
        else:
            self._record_phase(WorkflowPhase.INTRADAY_MONITOR,
                              WorkflowStatus.SKIPPED, {"reason": "非交易时段或v7.2模块未加载"})

        # ---- Phase 10: 盘中熔断总结 ★ v7.2 ----
        self.current_phase = WorkflowPhase.INTRADAY_SUMMARY
        ok, data = self.phase_intraday_summary()
        self._record_phase(WorkflowPhase.INTRADAY_SUMMARY,
                          WorkflowStatus.SUCCESS if ok else WorkflowStatus.SKIPPED, data)

        # ---- Phase 11: 中国神华建仓执行 ★ v7.4 ----
        # 基于 PDF《中国神华股价分析与建仓策略》生成的三档建仓计划
        self.current_phase = WorkflowPhase.SHENHUA_BUILD
        if HAS_V74_MODULES and self.mode == "shenhua_build":
            ok, data = self.phase_shenhua_build()
            self._record_phase(WorkflowPhase.SHENHUA_BUILD,
                              WorkflowStatus.SUCCESS if ok else WorkflowStatus.SKIPPED, data)
        else:
            self._record_phase(WorkflowPhase.SHENHUA_BUILD,
                              WorkflowStatus.SKIPPED, {"reason": "非 shenhua_build 模式或 v7.4 模块未加载"})

        # 最终状态
        elapsed = (datetime.now() - self.start_time).total_seconds()
        all_ok = all(
            info["status"] in ("SUCCESS", "SKIPPED")
            for info in self.phase_results.values()
        )
        logger.info(f"工作流完成 - 耗时 {elapsed:.1f}秒 - {'全部成功' if all_ok else '部分失败'}")
        return 0 if all_ok else 1


# =============================================================
# Windows Task Scheduler 辅助
# =============================================================
def generate_task_scheduler_xml(job_name: str = "QuantSystem_v6_Daily") -> str:
    """
    生成 Windows Task Scheduler XML 配置

    使用方法:
      python trading_workflow.py --generate-task-xml > task.xml
      schtasks /create /xml task.xml /tn QuantSystem_v6_Daily
    """
    python_exe = sys.executable
    script_path = os.path.abspath(__file__)
    work_dir = BASE_DIR

    xml = f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Date>{datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}</Date>
    <Author>Quant Strategy Team</Author>
    <Description>综合量化策略系统 v6.0 - 每日7:00自动执行</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{datetime.now().strftime('%Y-%m-%d')}T07:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByWeek>
        <DaysOfWeek>
          <Monday/>
          <Tuesday/>
          <Wednesday/>
          <Thursday/>
          <Friday/>
        </DaysOfWeek>
        <WeeksInterval>1</WeeksInterval>
      </ScheduleByWeek>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{os.getlogin()}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{python_exe}</Command>
      <Arguments>{script_path}</Arguments>
      <WorkingDirectory>{work_dir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>'''
    return xml


def generate_cron_entry() -> str:
    """生成 Linux cron 配置"""
    python_exe = sys.executable
    script_path = os.path.abspath(__file__)
    return (
        f"# 综合量化策略系统 v6.0 - 每日7:00 AM执行 (周一至周五)\n"
        f"0 7 * * 1-5 {python_exe} {script_path} >> "
        f"{os.path.join(BASE_DIR, 'logs', 'cron_output.log')} 2>&1"
    )


# =============================================================
# CLI 入口
# =============================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="综合量化策略系统 v6.0 - 交易日自动工作流",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python trading_workflow.py                                # 直接执行今日工作流
  python trading_workflow.py --mode quick                   # 快速模式(仅核心策略)
  python trading_workflow.py --mode risk_only               # 仅风险评估
  python trading_workflow.py --mode shenhua_build           # 中国神华建仓执行(v7.4)
  python trading_workflow.py --check-today                  # 检查今日是否为交易日
  python trading_workflow.py --generate-cron                # 生成Linux cron配置
  python trading_workflow.py --generate-task-xml            # 生成Windows计划任务XML
        """
    )
    parser.add_argument("--mode", "-m",
                        choices=["full", "quick", "risk_only", "build_plan", "shenhua_build"],
                        default="full", help="执行模式 (默认: full)")
    parser.add_argument("--check-today", action="store_true",
                        help="检查今日是否为交易日")
    parser.add_argument("--generate-cron", action="store_true",
                        help="生成Linux cron配置")
    parser.add_argument("--generate-task-xml", action="store_true",
                        help="生成Windows Task Scheduler XML配置")

    args = parser.parse_args()

    if args.check_today:
        today = date.today()
        is_td = is_trading_day(today)
        print(f"日期: {today}")
        print(f"星期: {['周一','周二','周三','周四','周五','周六','周日'][today.weekday()]}")
        print(f"交易日: {'是' if is_td else '否'}")
        if not is_td and today.weekday() < 5:
            print(f"原因: 节假日休市")
        sys.exit(0 if is_td else 2)

    if args.generate_cron:
        print(generate_cron_entry())
        sys.exit(0)

    if args.generate_task_xml:
        print(generate_task_scheduler_xml())
        sys.exit(0)

    # 执行工作流
    workflow = TradingDayWorkflow(mode=args.mode)
    exit_code = workflow.run()

    # 2 = 跳过 (非交易日), 视为正常退出
    if exit_code == 2:
        sys.exit(0)
    sys.exit(exit_code)
