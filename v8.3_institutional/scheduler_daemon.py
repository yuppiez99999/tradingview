# -*- coding: utf-8 -*-
"""
v7.5 每日交易工作流 - Python 调度守护进程
==================================================

作为 Windows 任务计划程序的补充, 提供更精细的调度控制:

    1. 每个交易日 07:00 自动执行 daily_workflow.py
    2. 节假日跳过 (可配置)
    3. 失败自动重试 (最多 3 次, 间隔 5 分钟)
    4. 心跳日志 + 进程监控

用法:
    python scheduler_daemon.py                    # 前台运行
    python scheduler_daemon.py --once             # 仅执行一次 (适合任务计划程序调用)
    python scheduler_daemon.py --install          # 安装为 Windows 服务 (需 pywin32)
    python scheduler_daemon.py --test             # 测试模式 (立即执行)

注意:
    - 推荐使用 Windows 任务计划程序 (register_task.ps1) 作为主调度
    - 本脚本作为补充, 提供常驻进程模式
    - 两者可共存: 任务计划程序触发本脚本的 --once 模式
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

# ============================================================
# 配置
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
PYTHON = r"C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
WORKFLOW_SCRIPT = BASE_DIR / "daily_workflow.py"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# 调度配置
TRIGGER_TIME = "07:00"          # 每日触发时间
MAX_RETRY = 3                    # 最大重试次数
RETRY_INTERVAL = 300             # 重试间隔 (秒) = 5 分钟
HEARTBEAT_INTERVAL = 60          # 心跳间隔 (秒)

# ER4 修复: 节假日列表改为从 utils.trade_calendar (akshare 动态获取) 委托
# 原 HOLIDAYS_2026 仅含 2026 假期, 2027 年后所有节假日会被误判为交易日
# 现统一走 akshare 动态日历, 自动覆盖任意年份, 失败时回退到周一至周五模式
HOLIDAYS_2026 = set()  # 保留变量名向后兼容, 实际不再使用
try:
    from utils.trade_calendar import is_trading_day as _dyn_is_trading_day
    _DYNAMIC_CALENDAR_AVAILABLE = True
    # 注意: logger 尚未初始化, 用 print 输出启动信息
    print("[ER4] 节假日判断已委托给 utils.trade_calendar (akshare 动态获取)")
except ImportError:
    _DYNAMIC_CALENDAR_AVAILABLE = False
    # ImportError 时回退: 保留 2026 硬编码列表作为兜底
    HOLIDAYS_2026 = {
        date(2026, 1, 1),
        date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),
        date(2026, 2, 19), date(2026, 2, 20), date(2026, 2, 23),
        date(2026, 4, 6), date(2026, 4, 7),
        date(2026, 5, 4), date(2026, 5, 5),
        date(2026, 6, 19), date(2026, 6, 22),
        date(2026, 9, 25),
        date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7),
        date(2026, 10, 8),
    }
    print("[ER4] utils.trade_calendar 不可用, 回退到 2026 硬编码假期列表 "
          "(2027+ 年节假日将无法识别)")

# ============================================================
# 日志
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(
            LOG_DIR / f"scheduler_{datetime.now():%Y%m%d}.log",
            encoding="utf-8",
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("v75.scheduler")


# ============================================================
# 调度器
# ============================================================
class DailyScheduler:
    """每日调度器"""

    def __init__(self):
        self.last_run_date: Optional[date] = None
        self.last_run_success: bool = False
        self.retry_count: int = 0

    # --------------------------------------------------------
    # 交易日判断
    # --------------------------------------------------------
    def is_trading_day(self, d: Optional[date] = None) -> bool:
        """判断是否为交易日 (周一至五且非节假日)

        ER4 修复: 优先委托给 utils.trade_calendar (akshare 动态获取),
        覆盖任意年份的节假日; 动态日历不可用时回退到 HOLIDAYS_2026 硬编码列表。
        """
        if d is None:
            d = date.today()

        # ER4 修复: 优先使用动态日历
        if _DYNAMIC_CALENDAR_AVAILABLE:
            try:
                return _dyn_is_trading_day(d.strftime('%Y-%m-%d'))
            except Exception as e:
                logger.warning(
                    f"[ER4] 动态日历查询失败 (date={d}), 回退到硬编码列表: {e}"
                )

        # 回退: 周末判断
        if d.weekday() >= 5:
            return False

        # 回退: 硬编码节假日列表 (仅 2026 年有效)
        if d in HOLIDAYS_2026:
            return False

        return True

    # --------------------------------------------------------
    # 执行工作流
    # --------------------------------------------------------
    def run_workflow(self, trade_date: Optional[str] = None) -> bool:
        """执行每日工作流"""
        if trade_date is None:
            trade_date = datetime.now().strftime("%Y-%m-%d")

        cmd = [PYTHON, str(WORKFLOW_SCRIPT), "--date", trade_date]
        logger.info(f"执行工作流: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                timeout=3600,  # 1 小时超时
                encoding="utf-8",
            )

            if result.returncode == 0:
                logger.info(f"工作流执行成功: {trade_date}")
                # 打印最后 10 行输出
                if result.stdout:
                    tail = result.stdout.strip().split("\n")[-10:]
                    for line in tail:
                        logger.info(f"  > {line}")
                return True
            else:
                logger.error(f"工作流执行失败 (exit={result.returncode}): {trade_date}")
                if result.stderr:
                    logger.error(f"stderr: {result.stderr[-500:]}")
                return False

        except subprocess.TimeoutExpired:
            logger.error(f"工作流执行超时 (1 小时): {trade_date}")
            return False
        except Exception as e:
            logger.error(f"工作流执行异常: {e}", exc_info=True)
            return False

    # --------------------------------------------------------
    # 带重试的执行
    # --------------------------------------------------------
    def run_with_retry(self, trade_date: Optional[str] = None) -> bool:
        """带重试的执行"""
        for attempt in range(1, MAX_RETRY + 1):
            logger.info(f"执行尝试 {attempt}/{MAX_RETRY}...")
            success = self.run_workflow(trade_date)
            if success:
                self.last_run_success = True
                self.retry_count = 0
                return True

            self.retry_count = attempt
            if attempt < MAX_RETRY:
                logger.warning(f"等待 {RETRY_INTERVAL} 秒后重试...")
                time.sleep(RETRY_INTERVAL)

        logger.error(f"全部 {MAX_RETRY} 次尝试均失败")
        self.last_run_success = False
        return False

    # --------------------------------------------------------
    # 检查是否到触发时间
    # --------------------------------------------------------
    def should_trigger(self) -> bool:
        """检查是否应该触发"""
        now = datetime.now()
        today = now.date()

        # 非交易日
        if not self.is_trading_day(today):
            return False

        # 今天已成功执行
        if self.last_run_date == today and self.last_run_success:
            return False

        # 检查时间 (07:00-07:30 窗口, 或失败重试)
        trigger_hour, trigger_min = map(int, TRIGGER_TIME.split(":"))
        trigger_time = now.replace(hour=trigger_hour, minute=trigger_min, second=0, microsecond=0)

        if now >= trigger_time:
            # 已过触发时间
            if self.last_run_date != today:
                # 今天还未执行
                return True
            elif not self.last_run_success and self.retry_count < MAX_RETRY:
                # 失败重试
                return True

        return False

    # --------------------------------------------------------
    # 心跳
    # --------------------------------------------------------
    def heartbeat(self) -> None:
        """心跳日志"""
        now = datetime.now()
        next_run = "今日已执行" if self.last_run_success else "待执行"
        logger.info(
            f"心跳 | now={now:%H:%M:%S} | "
            f"trading_day={self.is_trading_day()} | "
            f"last_run={self.last_run_date} | "
            f"success={self.last_run_success} | "
            f"retry={self.retry_count}/{MAX_RETRY} | "
            f"status={next_run}"
        )

    # --------------------------------------------------------
    # 常驻循环
    # --------------------------------------------------------
    def run_forever(self) -> None:
        """常驻调度循环"""
        logger.info("=" * 60)
        logger.info("v7.5 调度守护进程启动")
        logger.info(f"触发时间: 每个交易日 {TRIGGER_TIME}")
        logger.info(f"最大重试: {MAX_RETRY} 次, 间隔 {RETRY_INTERVAL} 秒")
        logger.info("=" * 60)

        last_heartbeat = 0

        while True:
            try:
                now = time.time()

                # 心跳
                if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                    self.heartbeat()
                    last_heartbeat = now

                # 触发检查
                if self.should_trigger():
                    today = datetime.now().strftime("%Y-%m-%d")
                    logger.info(f"触发工作流: {today}")
                    self.run_with_retry(today)
                    self.last_run_date = datetime.now().date()

                # 短暂休眠
                time.sleep(10)

            except KeyboardInterrupt:
                logger.info("收到中断信号, 退出")
                break
            except Exception as e:
                logger.error(f"调度循环异常: {e}", exc_info=True)
                time.sleep(60)

    # --------------------------------------------------------
    # 单次执行模式
    # --------------------------------------------------------
    def run_once(self) -> bool:
        """单次执行 (适合任务计划程序调用)"""
        today = datetime.now().strftime("%Y-%m-%d")

        if not self.is_trading_day():
            logger.info(f"{today} 非交易日, 跳过")
            return True

        logger.info(f"单次执行: {today}")
        success = self.run_with_retry(today)
        self.last_run_date = datetime.now().date()
        self.last_run_success = success
        return success


# ============================================================
# CLI 入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="v7.5 每日调度守护进程",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--once", action="store_true",
                        help="仅执行一次 (适合任务计划程序调用)")
    parser.add_argument("--test", action="store_true",
                        help="测试模式 (立即执行, 忽略交易日判断)")
    parser.add_argument("--install", action="store_true",
                        help="安装为 Windows 服务 (需 pywin32)")
    parser.add_argument("--check-calendar", action="store_true",
                        help="检查未来 30 天交易日历")

    args = parser.parse_args()

    scheduler = DailyScheduler()

    if args.check_calendar:
        # 检查未来 30 天交易日历
        today = date.today()
        logger.info("未来 30 天交易日历:")
        for i in range(30):
            d = today + timedelta(days=i)
            is_trading = scheduler.is_trading_day(d)
            marker = "[OK]" if is_trading else "[--]"
            reason = ""
            if d.weekday() >= 5:
                reason = "周末"
            elif d in HOLIDAYS_2026:
                reason = "节假日"
            logger.info(f"  {marker} {d} ({d.strftime('%a')}) {reason}")
        return

    if args.test:
        # 测试模式
        logger.info("测试模式: 立即执行工作流")
        success = scheduler.run_workflow()
        sys.exit(0 if success else 1)

    if args.install:
        # 安装为 Windows 服务
        logger.info("安装为 Windows 服务...")
        logger.info("提示: 推荐使用 register_task.ps1 注册任务计划程序")
        logger.info("      本功能需要 pywin32: pip install pywin32")
        # TODO: 实现 Windows 服务安装
        return

    if args.once:
        # 单次模式
        success = scheduler.run_once()
        sys.exit(0 if success else 1)

    # 默认: 常驻模式
    scheduler.run_forever()


if __name__ == "__main__":
    main()
