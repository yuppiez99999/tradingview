"""统一监控模式 - 一键启动所有模块 (v5.10 增强版: 8模块并行 + 自动对冲)"""

import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from core.context import (
    BASE_DIR,
    ML_PREDICTOR_AVAILABLE,
    SOCIAL_SECURITY_ETF_AVAILABLE,
    _get_portfolio_quotes,
    connector_manager,
    stop_loss,
)

LoggerFactory = Callable[[str], logging.Logger]


def _stock_monitor_func(get_logger: LoggerFactory) -> None:
    """股票实时行情快照（单次，非阻塞）"""
    module_logger = get_logger("股票实时监控")
    quotes = _get_portfolio_quotes()
    if not quotes:
        module_logger.warning("[股票监控] 数据源不可用，跳过本轮")
        return
    module_logger.info(f"[股票监控] 已获取 {len(quotes)} 只标的行情")
    for code, info in list(quotes.items())[:5]:
        module_logger.info(f"  {code}: {info['price']}")
    ds_label = getattr(connector_manager, "get_data_source_label", lambda: "Unknown")()
    module_logger.info(f"[股票监控] 数据源: {ds_label}")


def _futures_scan_func(get_logger: LoggerFactory) -> None:
    """期货期权扫描"""
    module_logger = get_logger("期货期权扫描")
    try:
        module_logger.info("[期货期权] 运行市场扫描...")
        from quant_modules.futures_options_scanner import run_full_scan

        result = run_full_scan(use_wind=True, use_deepseek=False)
        module_logger.info(
            f"[期货期权] 扫描完成 - 发现 {len(result.get('arbitrage_signals', []))} 个套利机会"
        )
    except ImportError as e:
        module_logger.error(f"[期货期权] 模块导入失败: {e}")
    except Exception as e:
        module_logger.error(f"[期货期权] 错误: {e}")


def _risk_check_func(get_logger: LoggerFactory) -> None:
    """止损止盈风险快照（单次，非阻塞）"""
    module_logger = get_logger("风险评估")
    StopLossMonitor = stop_loss.get("StopLossMonitor")
    quotes = _get_portfolio_quotes()
    if not StopLossMonitor or not quotes:
        module_logger.warning("[风险评估] 风控模块或行情不可用，跳过本轮")
        return
    try:
        alerts = StopLossMonitor().check_all(quotes)
        module_logger.info(
            f"[风险评估] 检查 {len(quotes)} 只标的，发现 {len(alerts)} 条告警"
        )
    except Exception as e:
        module_logger.error(f"[风险评估] 错误: {e}")


def _etf_flow_monitor_func(get_logger: LoggerFactory) -> None:
    """ETF资金流监控 - 每10分钟"""
    module_logger = get_logger("ETF资金流监控")
    try:
        module_logger.info("[ETF资金流] 运行监控...")
        quotes = _get_portfolio_quotes()
        if quotes:
            module_logger.info(
                f"[ETF资金流] 已获取 {len(quotes)} 只标的行情用于资金流分析"
            )
        from utils.social_security_etf import SocialSecurityETFTracker

        tracker = SocialSecurityETFTracker() if SOCIAL_SECURITY_ETF_AVAILABLE else None
        tracker_result = (
            tracker.track(tickers=list(quotes.keys())[:20]) if tracker else None
        )
        if tracker_result:
            module_logger.info(
                f"[ETF资金流] 风格信号: {tracker_result.get('regime', 'N/A')}"
            )
    except Exception as e:
        module_logger.debug(f"[ETF资金流] 跳过本轮: {e}")


def _ml_signal_monitor_func(get_logger: LoggerFactory) -> None:
    """ML信号扫描 - 每15分钟"""
    module_logger = get_logger("ML信号扫描")
    try:
        if not ML_PREDICTOR_AVAILABLE:
            return
        module_logger.info("[ML信号] 运行模型预测扫描...")
        from utils.ml_predictor import run_ml_signal_scan as ml_scan

        data_dir = os.path.join(BASE_DIR, "data", "cache")
        model_dir = os.path.join(BASE_DIR, "models")
        result = ml_scan(data_dir=data_dir, model_dir=model_dir, threshold=0.55)
        if "signals" in result:
            sigs = result["signals"]
            module_logger.info(
                f"[ML信号] 买入={len(sigs.get('buy', []))} "
                f"卖出={len(sigs.get('sell', []))} "
                f"持有={len(sigs.get('hold', []))}"
            )
    except Exception as e:
        module_logger.debug(f"[ML信号] 跳过本轮: {e}")


def _kommo_monitor_func(get_logger: LoggerFactory) -> None:
    """康波周期监控 - 每1小时"""
    module_logger = get_logger("康波周期监控")
    try:
        module_logger.info("[康波周期] 运行周期分析...")
        from utils.kondratiev_cycle import KondratievCycleAnalyzer

        analyzer = KondratievCycleAnalyzer()
        phase_info = analyzer.get_current_phase()
        cycle_info = {
            "phase": phase_info.get("phase", "N/A"),
            "suggestion": phase_info.get("suggestion", "N/A"),
        }
        if cycle_info:
            module_logger.info(
                f"[康波周期] 阶段: {cycle_info.get('phase', 'N/A')} | "
                f"建议配置: {cycle_info.get('suggestion', 'N/A')[:50]}"
            )
    except Exception as e:
        module_logger.debug(f"[康波周期] 跳过本轮: {e}")


def _connector_health_func(get_logger: LoggerFactory) -> None:
    """数据源健康探测 - 每2分钟"""
    module_logger = get_logger("数据源健康探测")
    try:
        health = getattr(connector_manager, "check_health", lambda: {})()
        active = health.get("active")
        ds_label = getattr(
            connector_manager, "get_data_source_label", lambda: "Unknown"
        )()
        fallbacks = getattr(connector_manager, "get_fallbacks_today", lambda: 0)()

        if fallbacks > 0:
            module_logger.warning(
                f"[数据源健康] 当前: {ds_label} | "
                f"今日降级 {fallbacks} 次 | "
                f"活跃连接器: {active}"
            )
        else:
            module_logger.info(f"[数据源健康] {ds_label}")
    except Exception as e:
        module_logger.debug(f"[数据源健康] 错误: {e}")


def _hedge_rebalance_func(get_logger: LoggerFactory, args: Any) -> None:
    """对冲+再平衡联动分析 - 每30分钟"""
    module_logger = get_logger("对冲再平衡联动")
    try:
        module_logger.info("[对冲再平衡] 运行联动分析...")
        from cli.modes.hedge_rebalance_joint import run_hedge_rebalance_joint

        run_hedge_rebalance_joint(args)
        module_logger.info("[对冲再平衡] 联动分析完成")
    except ImportError as e:
        module_logger.error(f"[对冲再平衡] 模块导入失败: {e}")
    except Exception as e:
        module_logger.error(f"[对冲再平衡] 错误: {e}", exc_info=True)


def run_unified_monitor(args: Any) -> None:
    """统一监控模式 - 一键启动所有模块 (v5.10 增强版: 8模块并行 + 自动对冲)"""
    # Windows 控制台 UTF-8 兼容
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    print("\n🎯 统一监控模式 (v5.7 增强版)")
    print("=" * 70)
    print("📋 启动所有交易模块...")
    print("-" * 70)

    # 配置日志（使用 TRADE_LOG_DIR 避免与全局 LOG_DIR 冲突）
    TRADE_LOG_DIR = Path(__file__).parent / "trade_logs"
    TRADE_LOG_DIR.mkdir(exist_ok=True)

    log_file = TRADE_LOG_DIR / f"unified_{datetime.now():%Y%m%d_%H%M%S}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )
    um_logger = logging.getLogger("unified_monitor")

    def get_module_logger(name: str) -> logging.Logger:
        module_logger = logging.getLogger(f"unified_monitor.{name}")
        if not module_logger.handlers:
            module_log_file = TRADE_LOG_DIR / f"unified_{name}.log"
            module_logger.addHandler(
                logging.FileHandler(module_log_file, encoding="utf-8")
            )
            module_logger.setLevel(logging.INFO)
            module_logger.propagate = False
        return module_logger

    def run_module_loop(
        name: str, func: Callable[[], None], interval: int = 300
    ) -> None:
        """模块循环执行"""
        module_logger = get_module_logger(name)
        module_logger.info(f"🚀 启动模块: {name}")
        um_logger.info(f"🚀 启动模块: {name}")
        while True:
            try:
                module_logger.info(f"▶️  执行: {name}")
                func()
                module_logger.info(f"✅ {name} 完成")
            except KeyboardInterrupt:
                module_logger.info(f"⏹️  用户中断: {name}")
                break
            except Exception as e:
                module_logger.error(f"❌ {name} 错误: {e}", exc_info=True)
            time.sleep(interval)

    # ── 启动线程 ──
    threads = []
    modules_config = [
        ("股票实时监控", lambda: _stock_monitor_func(get_module_logger), 300),
        ("期货期权扫描", lambda: _futures_scan_func(get_module_logger), 180),
        ("风险评估", lambda: _risk_check_func(get_module_logger), 300),
        ("ETF资金流监控", lambda: _etf_flow_monitor_func(get_module_logger), 600),
        ("ML信号扫描", lambda: _ml_signal_monitor_func(get_module_logger), 900),
        ("康波周期监控", lambda: _kommo_monitor_func(get_module_logger), 3600),
        ("数据源健康探测", lambda: _connector_health_func(get_module_logger), 120),
        (
            "对冲再平衡联动",
            lambda: _hedge_rebalance_func(get_module_logger, args),
            1800,
        ),
    ]

    print("\n" + "=" * 70)
    print("📋 已注册模块: (v5.10 增强版 - 8模块)")
    print("=" * 70)
    for name, _, interval in modules_config:
        interval_str = (
            f"每 {interval}秒" if interval < 3600 else f"每 {interval // 60}分钟"
        )
        print(f"  - {name}: {interval_str}")

    print("\n" + "=" * 70)
    print("🔥 开始并行启动所有模块...")
    print(
        f"📡 当前数据源: {getattr(connector_manager, 'get_data_source_label', lambda: 'Unknown')()}"
    )
    print("=" * 70)

    for name, func, interval in modules_config:
        thread = threading.Thread(
            target=run_module_loop, args=(name, func, interval), daemon=True, name=name
        )
        threads.append(thread)
        thread.start()
        time.sleep(0.5)  # 减少启动间隔

    print("\n" + "=" * 70)
    print("✅ 所有 8 个模块已启动！")
    print(f"📝 汇总日志: {log_file}")
    print(f"📁 模块日志目录: {TRADE_LOG_DIR}")
    print("💡 按 Ctrl+C 停止所有模块")
    print("=" * 70)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\n" + "=" * 70)
        print("⏹️  正在关闭所有模块...")
        print("=" * 70)
        um_logger.info("所有模块已停止")
