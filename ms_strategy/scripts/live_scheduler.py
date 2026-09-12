"""
v7.5 实时监控并发调度器 (--live 模式)
====================================

参考项目记忆中 --live 模式要求:
    - 6个模块同时启动
    - 对冲再平衡联动: 每30分钟
    - ETF资金流监控: 每10分钟
    - ML信号扫描: 每15分钟
    - 实时行情监控: 每5分钟
    - 自动再平衡: 每60分钟
    - 收盘报告: 收盘后执行

架构:
    - threading.Timer 实现定时轮询
    - ThreadPoolExecutor 实现并发执行
    - 支持热重启和优雅退出
    - 模块状态监控与自动恢复

用法:
    python live_scheduler.py                    # 启动实时监控
    python live_scheduler.py --dry-run          # 干跑模式 (不执行实际交易)
    python live_scheduler.py --stop             # 停止运行中的调度器
    python live_scheduler.py --status           # 查看运行状态

依赖:
    - utils.etf_flow_monitor (ETF资金流)
    - utils.wt_hedge_strategy (对冲策略)
    - utils.wt_tick_engine (Tick回测引擎)
    - v7.5_institutional.src.hedging (对冲协调器)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import time as dt_time
from pathlib import Path
from typing import Any

from utils.datetime_utils import now_bj

# ============================================================
# 路径初始化
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
V75_DIR = BASE_DIR / "v7.5_institutional"
V75_SRC = V75_DIR / "src"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(V75_SRC))

# ============================================================
# 日志配置
# ============================================================
logger = logging.getLogger("live_scheduler")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(threadName)s] [%(levelname)s] %(message)s"
))
logger.addHandler(handler)
file_handler = logging.FileHandler(LOG_DIR / "live_scheduler.log", encoding="utf-8")
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(threadName)s] [%(levelname)s] %(message)s"
))
logger.addHandler(file_handler)

# ============================================================
# 全局状态
# ============================================================
RUNNING = True
MODULE_STATUS: dict[str, dict] = {}
# 跨平台: 直接用当前解释器 (Windows/macOS/Linux 通用), 不再写死 Windows 路径
PYTHON = sys.executable
LOCK_FILE = BASE_DIR / ".live_scheduler.lock"

# ============================================================
# 模块定义
# ============================================================
MODULE_DEFINITIONS = [
    {
        "name": "market_monitor",
        "description": "实时行情监控",
        "interval_seconds": 300,  # 5分钟
        "task_func": "run_market_monitor",
        "required": True,
    },
    {
        "name": "auto_rebalance",
        "description": "自动再平衡",
        "interval_seconds": 3600,  # 60分钟
        "task_func": "run_auto_rebalance",
        "required": False,
    },
    {
        "name": "hedge_rebalance",
        "description": "对冲再平衡联动",
        "interval_seconds": 1800,  # 30分钟
        "task_func": "run_hedge_rebalance",
        "required": True,
    },
    {
        "name": "etf_flow_monitor",
        "description": "ETF资金流监控",
        "interval_seconds": 600,  # 10分钟
        "task_func": "run_etf_flow_monitor",
        "required": True,
    },
    {
        "name": "ml_signal_scan",
        "description": "ML信号扫描",
        "interval_seconds": 900,  # 15分钟
        "task_func": "run_ml_signal_scan",
        "required": False,
    },
    {
        "name": "daily_report",
        "description": "收盘报告",
        "interval_seconds": None,  # 收盘后执行
        "task_func": "run_daily_report",
        "required": True,
        "trigger_time": dt_time(15, 15),  # 15:15 收盘后
    },
]

# ============================================================
# 任务函数
# ============================================================

def run_market_monitor(dry_run: bool = False) -> dict[str, Any]:
    """实时行情监控任务"""
    start = now_bj()
    result = {"status": "OK", "data": {}}
    try:
        from utils.data_provider import MarketDataProvider

        provider = MarketDataProvider()

        positions_path = BASE_DIR / "config" / "positions.json"
        if positions_path.exists():
            with open(positions_path, encoding="utf-8") as f:
                positions_data = json.load(f)
            codes = list(positions_data.get("positions", {}).keys())
        else:
            codes = ["510300.SH", "510050.SH", "588000.SH"]

        prices = {}
        for code in codes[:20]:
            try:
                market_data = provider.get_market_data(code)
                if market_data:
                    for key in ['price', 'index_price', 'close', 'last_price', 'current_price']:
                        if key in market_data and market_data[key]:
                            prices[code] = float(market_data[key])
                            break
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):  # noqa: E501
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                continue

        result["data"] = {
            "n_symbols": len(prices),
            "prices": prices,
            "timestamp": now_bj().isoformat(),
        }
        logger.info(f"[market_monitor] 获取 {len(prices)} 个标的行情")

    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:

        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        result["status"] = "FAIL"
        result["error"] = str(e)
        logger.error(f"[market_monitor] 执行失败: {e}")

    result["duration"] = (now_bj() - start).total_seconds()
    return result


def run_auto_rebalance(dry_run: bool = False) -> dict[str, Any]:
    """自动再平衡任务"""
    start = now_bj()
    result = {"status": "OK", "data": {}}
    try:
        from utils.risk_metrics import calculate_portfolio_weights

        positions_path = BASE_DIR / "config" / "positions.json"
        if positions_path.exists():
            with open(positions_path, encoding="utf-8") as f:
                positions_data = json.load(f)
            positions = positions_data.get("positions", {})

            actual_weights = calculate_portfolio_weights(positions)

            trade_plan_path = V75_DIR / "trade_plans" / "auto_trade_plan_500w_2026-2030.json"
            if trade_plan_path.exists():
                with open(trade_plan_path, encoding="utf-8") as f:
                    plan = json.load(f)
                target_weights = {}
                for pos in plan.get("stock_etf_account", {}).get("positions", []):
                    target_weights[pos["code"]] = pos["weight"]

                deviations = {}
                for code, actual in actual_weights.items():
                    target = target_weights.get(code, 0)
                    deviations[code] = abs(actual - target)

                result["data"] = {
                    "actual_weights": actual_weights,
                    "target_weights": target_weights,
                    "deviations": deviations,
                    "exceeds_threshold": {k: v for k, v in deviations.items() if v > 0.05},
                }
                logger.info(f"[auto_rebalance] 权重偏差检查完成, {len(result['data']['exceeds_threshold'])} 个标的偏差>5%")  # noqa: E501
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        result["status"] = "FAIL"
        result["error"] = str(e)
        logger.error(f"[auto_rebalance] 执行失败: {e}")

    result["duration"] = (now_bj() - start).total_seconds()
    return result


def run_hedge_rebalance(dry_run: bool = False) -> dict[str, Any]:
    """对冲再平衡联动任务"""
    start = now_bj()
    result = {"status": "OK", "data": {}}
    try:
        from hedging.beta_hedger import BetaHedger

        positions_path = BASE_DIR / "config" / "positions.json"
        if positions_path.exists():
            with open(positions_path, encoding="utf-8") as f:
                positions_data = json.load(f)
            positions = positions_data.get("positions", {})

            portfolio_value = sum(
                pos.get("phase1_amount", 0) + pos.get("phase2_amount", 0) + pos.get("phase3_amount", 0)
                for pos in positions.values()
            )

            if portfolio_value > 0:
                futures_config = {
                    "IF": {"multiplier": 300, "beta": 1.0, "price": 3800.0},
                    "IM": {"multiplier": 200, "beta": 1.1, "price": 5800.0},
                    "IC": {"multiplier": 200, "beta": 1.2, "price": 5500.0},
                }

                beta_hedger = BetaHedger(futures_config=futures_config, beta_trigger=0.7, beta_target=0.3)
                order = beta_hedger.compute_hedge(portfolio_beta=1.0, portfolio_value=portfolio_value)

                result["data"] = {
                    "portfolio_value": portfolio_value,
                    "hedge_order": order,
                }
                logger.info(f"[hedge_rebalance] 对冲计算完成: action={order.get('action')}, contracts={order.get('contracts', 0)}")  # noqa: E501
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        result["status"] = "FAIL"
        result["error"] = str(e)
        logger.error(f"[hedge_rebalance] 执行失败: {e}")

    result["duration"] = (now_bj() - start).total_seconds()
    return result


def run_etf_flow_monitor(dry_run: bool = False) -> dict[str, Any]:
    """ETF资金流监控任务"""
    start = now_bj()
    result = {"status": "OK", "data": {}}
    try:
        from utils.etf_flow_monitor import get_etf_flow_summary, refresh_etf_flow_signals

        summary = get_etf_flow_summary()

        if not dry_run:
            try:
                refresh_etf_flow_signals()
                summary = get_etf_flow_summary()
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:  # noqa: E501
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                logger.warning(f"[etf_flow_monitor] 刷新失败, 使用缓存数据: {e}")

        flow_data = summary.get("flow_data", {})
        result["data"] = {
            "n_etfs": len(flow_data),
            "total_inflow": summary.get("total_flow_yi", 0),
            "overall_trend": summary.get("overall_trend", "未知"),
            "signal_count": summary.get("signal_count", 0),
            "signals": summary.get("signals", []),
        }
        logger.info(f"[etf_flow_monitor] 监控完成: {len(flow_data)} 只ETF, 净流入={result['data']['total_inflow']:.2f}亿, 趋势={result['data']['overall_trend']}")  # noqa: E501
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        result["status"] = "FAIL"
        result["error"] = str(e)
        logger.error(f"[etf_flow_monitor] 执行失败: {e}")

    result["duration"] = (now_bj() - start).total_seconds()
    return result


def run_ml_signal_scan(dry_run: bool = False) -> dict[str, Any]:
    """ML信号扫描任务"""
    start = now_bj()
    result = {"status": "OK", "data": {}}
    try:
        from utils.tf_price_predictor import PricePredictor

        predictor = PricePredictor()

        codes = ["510300.SH", "510050.SH", "588000.SH", "688041.SH", "300308.SZ"]
        predictions = {}

        for code in codes:
            try:
                pred = predictor.predict(code, days=3)
                if pred:
                    predictions[code] = {
                        "predicted_return": pred.get("predicted_return", 0),
                        "confidence": pred.get("confidence", 0),
                    }
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):  # noqa: E501
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                continue

        result["data"] = {
            "n_predictions": len(predictions),
            "predictions": predictions,
        }
        logger.info(f"[ml_signal_scan] 预测完成: {len(predictions)} 个标的")
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        result["status"] = "FAIL"
        result["error"] = str(e)
        logger.error(f"[ml_signal_scan] 执行失败: {e}")

    result["duration"] = (now_bj() - start).total_seconds()
    return result


def run_daily_report(dry_run: bool = False) -> dict[str, Any]:
    """收盘报告任务"""
    start = now_bj()
    result = {"status": "OK", "data": {}}
    try:
        if dry_run:
            logger.info("[daily_report] [DRY-RUN] 收盘报告生成 (跳过实际执行)")
            result["data"] = {"dry_run": True}
            result["duration"] = 0
            return result

        # B1.4: 副本已删除, 改为调用根目录版 (含 DeepSeek AI 推荐)
        report_script = BASE_DIR.parent.parent / "generate_daily_report.py"
        if report_script.exists():
            cmd = [PYTHON, str(report_script)]
            proc = subprocess.run(
                cmd, cwd=str(report_script.parent),
                capture_output=True, text=True, encoding="utf-8",
                timeout=600,
            )
            if proc.returncode == 0:
                result["data"] = {"exit_code": 0}
                logger.info("[daily_report] 收盘报告生成成功")
            else:
                result["status"] = "FAIL"
                result["error"] = proc.stderr[:200]
                logger.error(f"[daily_report] 执行失败: {proc.stderr[:200]}")
        else:
            result["status"] = "FAIL"
            result["error"] = "generate_daily_report.py 不存在"
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        result["status"] = "FAIL"
        result["error"] = str(e)
        logger.error(f"[daily_report] 执行失败: {e}")

    result["duration"] = (now_bj() - start).total_seconds()
    return result


# ============================================================
# 调度器核心
# ============================================================
class LiveScheduler:
    """实时监控并发调度器"""

    def __init__(self, dry_run: bool = False, max_workers: int = 6):
        self.dry_run = dry_run
        self.max_workers = max_workers
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="live")
        self.timers: dict[str, threading.Timer] = {}
        self.module_last_run: dict[str, datetime] = {}
        self.module_results: dict[str, list[dict]] = {}

        for mod in MODULE_DEFINITIONS:
            self.module_results[mod["name"]] = []

    def _run_task(self, module_name: str, task_func: Callable) -> None:
        """运行单个任务"""
        try:
            result = task_func(dry_run=self.dry_run)
            self.module_last_run[module_name] = now_bj()
            self.module_results[module_name].append({
                "timestamp": now_bj().isoformat(),
                **result,
            })
            if len(self.module_results[module_name]) > 100:
                self.module_results[module_name] = self.module_results[module_name][-50:]

            MODULE_STATUS[module_name] = {
                "status": result["status"],
                "last_run": now_bj().isoformat(),
                "duration": result["duration"],
            }
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:  # noqa: E501
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            logger.error(f"[{module_name}] 任务异常: {e}")
            MODULE_STATUS[module_name] = {
                "status": "ERROR",
                "last_run": now_bj().isoformat(),
                "error": str(e),
            }

    def _schedule_module(self, module_def: dict) -> None:
        """调度单个模块"""
        name = module_def["name"]
        interval = module_def["interval_seconds"]
        task_func = globals()[module_def["task_func"]]

        def run_and_reschedule():
            if not RUNNING:
                return

            self._run_task(name, task_func)

            if RUNNING and interval:
                timer = threading.Timer(interval, run_and_reschedule)
                timer.daemon = True
                self.timers[name] = timer
                timer.start()

        run_and_reschedule()

    def _schedule_daily_report(self) -> None:
        """调度收盘报告(定时触发)"""
        def check_and_run():
            if not RUNNING:
                return

            now = now_bj()
            trigger_time = MODULE_DEFINITIONS[-1]["trigger_time"]

            if now.time() >= trigger_time and now.time() < trigger_time.replace(minute=trigger_time.minute + 5):
                today_str = now.strftime("%Y-%m-%d")
                key = f"daily_report_{today_str}"
                if key not in self.module_last_run:
                    self._run_task("daily_report", run_daily_report)
                    self.module_last_run[key] = now

            timer = threading.Timer(60, check_and_run)
            timer.daemon = True
            self.timers["daily_report_sched"] = timer
            timer.start()

        check_and_run()

    def start(self) -> None:
        """启动所有模块"""
        logger.info("=" * 70)
        logger.info("  v7.5 实时监控并发调度器启动")
        logger.info("  模式: %s", "DRY-RUN" if self.dry_run else "LIVE")
        logger.info("  模块数: %d", len(MODULE_DEFINITIONS))
        logger.info("=" * 70)

        for mod in MODULE_DEFINITIONS:
            if mod["interval_seconds"]:
                logger.info(f"  [启动] {mod['name']}: {mod['description']} (每{mod['interval_seconds']/60:.0f}分钟)")
                self._schedule_module(mod)
            else:
                logger.info(f"  [启动] {mod['name']}: {mod['description']} (定时 {mod['trigger_time']})")

        self._schedule_daily_report()

        logger.info("  所有模块已启动")
        logger.info("=" * 70)

    def stop(self) -> None:
        """停止所有模块"""
        logger.info("=" * 70)
        logger.info("  正在停止实时监控调度器...")
        logger.info("=" * 70)

        for name, timer in self.timers.items():
            timer.cancel()
            logger.info(f"  [停止] {name}")

        self.executor.shutdown(wait=True)
        logger.info("  调度器已停止")
        logger.info("=" * 70)

    def get_status(self) -> dict[str, Any]:
        """获取当前状态"""
        return {
            "running": RUNNING,
            "dry_run": self.dry_run,
            "modules": MODULE_STATUS,
            "last_runs": {k: v.isoformat() if isinstance(v, datetime) else v for k, v in self.module_last_run.items()},
            "timestamp": now_bj().isoformat(),
        }


# ============================================================
# 进程管理
# ============================================================
def _write_lock(pid: int) -> None:
    """写入锁文件"""
    with open(LOCK_FILE, "w", encoding="utf-8") as f:
        json.dump({"pid": pid, "start_time": now_bj().isoformat()}, f)


def _read_lock() -> dict | None:
    """读取锁文件"""
    if LOCK_FILE.exists():
        try:
            with open(LOCK_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
            # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
            return None
    return None


def _remove_lock() -> None:
    """移除锁文件"""
    if LOCK_FILE.exists():
        LOCK_FILE.unlink()


def _is_running() -> bool:
    """检查是否已有进程在运行"""
    lock = _read_lock()
    if not lock:
        return False
    pid = lock.get("pid")
    if not pid:
        return False
    try:
        subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True)
        return True
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError):
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        return False


# ============================================================
# 信号处理
# ============================================================
def signal_handler(signum, frame):
    """处理中断信号"""
    global RUNNING
    logger.info(f"收到信号 {signum}, 正在停止...")
    RUNNING = False


# ============================================================
# CLI 入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="v7.5 实时监控并发调度器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python live_scheduler.py                    # 启动实时监控 (6模块并发)
    python live_scheduler.py --dry-run          # 干跑模式
    python live_scheduler.py --stop             # 停止运行中的调度器
    python live_scheduler.py --status           # 查看运行状态
        """,
    )
    parser.add_argument("--dry-run", action="store_true", help="干跑模式")
    parser.add_argument("--stop", action="store_true", help="停止运行中的调度器")
    parser.add_argument("--status", action="store_true", help="查看运行状态")
    parser.add_argument("--max-workers", type=int, default=6, help="最大并发线程数")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if args.status:
        status = _read_lock()
        if status:
            logger.info(f"运行中 PID: {status['pid']}")
            logger.info(f"启动时间: {status['start_time']}")
        else:
            logger.info("调度器未运行")
        return 0

    if args.stop:
        lock = _read_lock()
        if lock and lock.get("pid"):
            try:
                subprocess.run(["taskkill", "/F", "/PID", str(lock["pid"])], capture_output=True)
                logger.info(f"已停止 PID {lock['pid']}")
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, TimeoutError, ConnectionError) as e:  # noqa: E501
                # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
                logger.info(f"停止失败: {e}")
        _remove_lock()
        return 0

    if _is_running():
        logger.info("调度器已在运行中")
        return 0

    _write_lock(os.getpid())

    scheduler = LiveScheduler(dry_run=args.dry_run, max_workers=args.max_workers)
    scheduler.start()

    try:
        while RUNNING:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    scheduler.stop()
    _remove_lock()


if __name__ == "__main__":
    sys.exit(main())
