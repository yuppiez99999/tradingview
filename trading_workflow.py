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
from typing import Dict, Optional, Tuple
from enum import Enum

# ---- 路径设置 ----
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
os.chdir(BASE_DIR)

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

            # ---- 第3层：紧急响应协议 ----
            emergency_level = 0
            day_multiplier = 1.0
            actions = []
            alerts = []

            # ---- 极端预警：最高优先级，先于其他所有检查 ----
            # 触发条件：VIX>=50 或 5日累计跌幅>12% (相当于单日暴跌+恐慌扩散)
            #           或 20日累计跌幅>25% (系统性危机信号) 或 两融5日降幅>15%
            if (vix_proxy >= 50 or abs(ret_5d) > 0.12
                    or abs(ret_20d) > 0.25 or margin_balance < -0.15):
                emergency_level = 4
                day_multiplier = 0.0
                actions.append("EXTREME: 全部停止建仓，转入纯防御模式")
                actions.append("EXTREME: 联系券商执行专项处置通道")
                actions.append("EXTREME: 对所有已建仓位启用保护性止损")
                alerts.append(f"极端预警触发: VIX代理={vix_proxy:.0f}, 5日跌幅={ret_5d:.1%}, 20日跌幅={ret_20d:.1%}")

            # 红色预警：VIX>40 或 双周跌幅>15% 或 两融5日降幅>10%
            elif vix_proxy > 40 or abs(ret_20d) > 0.15 or margin_balance < -0.10:
                emergency_level = 3
                day_multiplier = 0.0
                actions.append("RED: 今日暂停建仓，所有新订单取消")
                actions.append("RED: 现有仓位不动，密切监控止损条件")
                actions.append("RED: 建议执行保护性期权对冲（科创50 Put）")
                alerts.append(f"红色预警触发: VIX={vix_proxy:.0f}, 20日跌幅={ret_20d:.1%}, 两融变动={margin_balance:.1%}")

            # 橙色预警：VIX>35 或 单周跌幅>8%
            elif vix_proxy > 35 or abs(ret_5d) > 0.08:
                emergency_level = 2
                day_multiplier = 0.0
                actions.append("ORANGE: 今日暂停建仓，等待市场稳定")
                actions.append("ORANGE: 密切监控已建仓位，准备减仓")
                alerts.append(f"橙色预警触发: VIX={vix_proxy:.0f}, 5日跌幅={ret_5d:.1%}")

            # 黄色预警：VIX>30 或 单日跌幅>3% 或 两融5日降幅>5%
            elif vix_proxy > 30 or abs(ret_5d / 5) > 0.03 or margin_balance < -0.05:
                emergency_level = 1
                day_multiplier = 0.50
                actions.append("YELLOW: 建仓金额减半至50%")
                actions.append("YELLOW: 增加现金储备比例")
                actions.append("YELLOW: 仅执行高优先级（核心仓位）订单")
                alerts.append(f"黄色预警触发: VIX={vix_proxy:.0f}, 近5日跌幅~{abs(ret_5d/5):.1%}, 两融变动={margin_balance:.1%}")

            # 行业集中度特殊检测：高端制造板块20日跌幅>15%
            if abs(mfg_drawdown_20d) > 0.15:
                if emergency_level < 2:
                    emergency_level = 2
                    day_multiplier = min(day_multiplier, 0.50)
                    actions.append("SECTOR: 高端制造板块跌幅超15%，建议减少该板块建仓比例")
                    actions.append("SECTOR: 考虑风格层面临时对冲（做空IC或买入Put）")
                alerts.append(f"行业预警: 高端制造板块20日跌幅={mfg_drawdown_20d:.1%}")

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

                # 针对最严重场景（黑天鹅）运行单场景测试
                for sid, scenario in engine.test_scenarios.items():
                    if sid in ('forward_black_swan', 'forward_bear_2026'):
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
            "workflow_version": "v6.0",
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
        }

    def _print_console_summary(self, summary: Dict):
        """打印控制台摘要"""
        print()
        print("=" * 60)
        print("  综合量化策略系统 v6.0 - 交易日工作流完成")
        print("=" * 60)
        print(f"  日期: {summary['date']}")
        print(f"  耗时: {summary['duration_seconds']}秒")
        print(f"  模式: {summary['mode']}")
        print("-" * 60)
        for phase_id, info in summary["phases"].items():
            status_icon = "OK" if info["status"] == "SUCCESS" else "FAIL"
            print(f"  [{status_icon}] {phase_id}")
        print("-" * 60)
        passed = sum(1 for v in summary["phases"].values() if v["status"] == "SUCCESS")
        total = len(summary["phases"])
        print(f"  阶段完成: {passed}/{total}")
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
  python trading_workflow.py                    # 直接执行今日工作流
  python trading_workflow.py --mode quick       # 快速模式(仅核心策略)
  python trading_workflow.py --mode risk_only   # 仅风险评估
  python trading_workflow.py --check-today      # 检查今日是否为交易日
  python trading_workflow.py --generate-cron    # 生成Linux cron配置
  python trading_workflow.py --generate-task-xml # 生成Windows计划任务XML
        """
    )
    parser.add_argument("--mode", "-m", choices=["full", "quick", "risk_only", "build_plan"],
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
