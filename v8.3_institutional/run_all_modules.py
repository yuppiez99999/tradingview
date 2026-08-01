# -*- coding: utf-8 -*-
"""
v7.5 全核心模块统一调度器
==========================

交易日自动运行所有核心模块，按盘前/盘后分两个批次:

盘前 07:00 (PreMarket):
    1. Wind 数据校准      (每周一, 其他日跳过)
    2. v5 组合优化        (每周一, 其他日跳过)
    3. 每日交易工作流     (每日, 7 阶段: check→market→risk→hedge→signal→execute→report)

盘后 15:30 (PostMarket):
    4. 黑天鹅压力测试     (每日, 2000 互联网泡沫 + 2008 次贷危机)
    5. 汇总报告归档       (每日, 整合所有模块输出)

用法:
    python run_all_modules.py --phase pre      # 盘前批次
    python run_all_modules.py --phase post     # 盘后批次
    python run_all_modules.py --phase all      # 全部 (测试用)
    python run_all_modules.py --phase pre --dry-run   # 干跑模式
"""
from __future__ import annotations

import os
import sys
import logging
import argparse
import subprocess
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional

# ============================================================
# 路径与全局配置
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
PYTHON = r"C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Wind MCP API Key (从环境变量读取, 不硬编码)
WIND_API_KEY = os.environ.get("WIND_API_KEY", "")
if WIND_API_KEY:
    os.environ["WIND_API_KEY"] = WIND_API_KEY
else:
    print("[WARN] WIND_API_KEY 环境变量未设置, Wind MCP 相关模块可能无法正常工作")

# 报告归档目录 (统一到根目录 e:\各种PY程序\每日报告归档)
ARCHIVE_ROOT = BASE_DIR.parent.parent / "每日报告归档"

# ER4 修复: 节假日列表改为从 utils.trade_calendar (akshare 动态获取) 委托
# 原 HOLIDAYS_2026 仅含 2026 假期, 2027 年后所有节假日会被误判为交易日
# 现统一走 akshare 动态日历, 自动覆盖任意年份, 失败时回退到 2026 硬编码列表
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
today_str = datetime.now().strftime("%Y%m%d")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(
            LOG_DIR / f"all_modules_{today_str}.log",
            encoding="utf-8",
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("v75.all_modules")


# ============================================================
# 模块定义
# ============================================================
class ModuleRunner:
    """单个核心模块的运行器"""

    def __init__(self,
                 name: str,
                 script: str,
                 description: str,
                 schedule: str = "daily",
                 timeout: int = 3600,
                 extra_args: Optional[List[str]] = None):
        """
        Args:
            name: 模块简称
            script: 脚本文件名 (相对 BASE_DIR)
            description: 中文描述
            schedule: "daily" / "weekly" (每周一运行)
            timeout: 超时秒数
            extra_args: 额外命令行参数列表
        """
        self.name = name
        self.script = BASE_DIR / script
        self.description = description
        self.schedule = schedule
        self.timeout = timeout
        self.extra_args = extra_args or []
        self.result: Optional[Dict] = None

    def should_run_today(self, d: Optional[date] = None) -> bool:
        """判断今天是否应该运行"""
        if d is None:
            d = date.today()
        if self.schedule == "daily":
            return True
        if self.schedule == "weekly":
            # 每周一运行
            return d.weekday() == 0
        return True

    def run(self, dry_run: bool = False) -> Dict:
        """执行模块"""
        status = "SKIP"
        exit_code = 0
        duration_sec = 0
        output_tail = ""

        if not self.script.exists():
            status = "NOT_FOUND"
            logger.error(f"[{self.name}] 脚本不存在: {self.script}")
        elif not self.should_run_today():
            status = "SKIP_SCHEDULE"
            logger.info(f"[{self.name}] 今日不需要运行 (schedule={self.schedule})")
        elif dry_run:
            status = "DRY_RUN"
            logger.info(f"[{self.name}] [DRY-RUN] {self.description}")
        else:
            logger.info(f"[{self.name}] 开始执行: {self.description}")
            start = datetime.now()
            try:
                cmd = [PYTHON, str(self.script), *list(self.extra_args)]
                result = subprocess.run(
                    cmd,
                    cwd=str(BASE_DIR),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout,
                    env=os.environ,
                )
                exit_code = result.returncode
                duration_sec = (datetime.now() - start).total_seconds()

                if exit_code == 0:
                    status = "OK"
                    logger.info(f"[{self.name}] 执行成功 ({duration_sec:.0f}s)")
                else:
                    status = "FAIL"
                    logger.error(f"[{self.name}] 执行失败 (exit={exit_code}, {duration_sec:.0f}s)")

                if result.stdout:
                    lines = result.stdout.strip().split("\n")
                    output_tail = "\n".join(lines[-5:])
                    if status == "OK":
                        for line in lines[-3:]:
                            logger.info(f"  > {line}")
                    else:
                        for line in lines[-5:]:
                            logger.error(f"  ! {line}")
                if result.stderr:
                    stderr_tail = result.stderr.strip().split("\n")[-3:]
                    for line in stderr_tail:
                        logger.error(f"  ! stderr: {line}")

            except subprocess.TimeoutExpired:
                status = "TIMEOUT"
                duration_sec = self.timeout
                logger.error(f"[{self.name}] 执行超时 ({self.timeout}s)")
            except Exception as e:
                status = "ERROR"
                logger.error(f"[{self.name}] 执行异常: {e}", exc_info=True)

        self.result = {
            "name": self.name,
            "description": self.description,
            "script": str(self.script.name),
            "status": status,
            "exit_code": exit_code,
            "duration_sec": round(duration_sec, 1),
            "output_tail": output_tail,
        }
        return self.result


# ============================================================
# 核心模块清单
# ============================================================
PREMARKET_MODULES = [
    ModuleRunner(
        name="wind_calibrate",
        script="calibrate_asset_params_wind.py",
        description="Wind MCP 数据校准 (19标的日K, 计算年化收益/波动/相关矩阵)",
        schedule="weekly",
        timeout=600,
    ),
    ModuleRunner(
        name="update_prices",
        script="../update_position_prices.py",
        description="更新持仓价格 (从最新收盘报告同步价格到 positions.json)",
        schedule="daily",
        timeout=120,
    ),
    ModuleRunner(
        name="daily_trade_executor_pre",
        script="../daily_trade_executor.py",
        description="盘前生成交易指令 (含预测信号调整分配, 输出 instructions.json+md)",
        schedule="daily",
        timeout=300,
        extra_args=["pre-market"],
    ),
    ModuleRunner(
        name="daily_workflow",
        script="daily_workflow.py",
        description="每日交易工作流 (盘前: check→calibrate→market→risk→hedge→signal→report)",
        schedule="daily",
        timeout=3600,
        extra_args=["--phase-end", "report"],
    ),
]

POSTMARKET_MODULES = [
    ModuleRunner(
        name="daily_trade_executor_post",
        script="../daily_trade_executor.py",
        description="盘后执行已确认交易指令 (更新 positions.json + build_progress.json)",
        schedule="daily",
        timeout=300,
        extra_args=["post-market"],
    ),
    ModuleRunner(
        name="daily_pnl_report",
        script="../generate_daily_report.py",
        description="收盘盈亏明细报告 (持仓盈亏+对冲明细+AI决策建议)",
        schedule="daily",
        timeout=600,
    ),
    ModuleRunner(
        name="stop_loss_monitor",
        script="../stop_loss_monitor.py",
        description="止损监控 (波动率调整止损规则检查, 触发减仓/清仓预警)",
        schedule="daily",
        timeout=120,
    ),
]


# ============================================================
# 调度器
# ============================================================
class AllModulesScheduler:
    """全核心模块调度器"""

    def __init__(self, dry_run: bool = False, sim_mode: bool = False):
        self.dry_run = dry_run
        self.sim_mode = sim_mode
        self.trade_date = datetime.now().strftime("%Y-%m-%d")
        self.start_time = datetime.now()
        self.results: List[Dict] = []

        # 创建当日归档目录 (YYYY-MM-DD)
        self.archive_dir = ARCHIVE_ROOT / datetime.now().strftime("%Y-%m-%d")
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    def is_trading_day(self, d: Optional[date] = None) -> bool:
        """判断是否为交易日

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

    def run_phase(self, phase: str) -> bool:
        """运行一个批次的所有模块

        Args:
            phase: "pre" (盘前) / "post" (盘后) / "all"
        """
        if not self.is_trading_day() and not self.dry_run:
            logger.info(f"{self.trade_date} 非交易日, 跳过执行")
            return True

        modules = []
        if phase in ("pre", "all"):
            modules.extend(PREMARKET_MODULES)
        if phase in ("post", "all"):
            modules.extend(POSTMARKET_MODULES)

        logger.info("=" * 70)
        logger.info(f"批次: {phase} | 交易日: {self.trade_date} | 模块数: {len(modules)}")
        logger.info("=" * 70)

        for module in modules:
            # 为 daily_workflow 注入模拟盘参数
            if self.sim_mode and module.name == "daily_workflow":
                module.extra_args = ["--sim"]
            elif not self.sim_mode and module.name == "daily_workflow":
                module.extra_args = []
            result = module.run(dry_run=self.dry_run)
            self.results.append(result)

        return all(r["status"] in ("OK", "SKIP", "SKIP_SCHEDULE", "DRY_RUN")
                   for r in self.results)

    def generate_summary_report(self) -> Path:
        """生成汇总报告"""
        duration = (datetime.now() - self.start_time).total_seconds()
        ok_count = sum(1 for r in self.results if r["status"] == "OK")
        fail_count = sum(1 for r in self.results if r["status"] in ("FAIL", "ERROR", "TIMEOUT"))
        skip_count = sum(1 for r in self.results if r["status"].startswith("SKIP"))

        report_path = self.archive_dir / f"all_modules_summary_{self.trade_date.replace('-','')}.md"

        with open(report_path, "w", encoding="utf-8") as f:
            f.write("# v7.5 全核心模块运行汇总报告\n\n")
            f.write(f"**交易日**: {self.trade_date}\n")
            f.write(f"**生成时间**: {datetime.now():%Y-%m-%d %H:%M:%S}\n")
            f.write(f"**总耗时**: {duration:.0f} 秒\n")
            f.write(f"**状态**: OK={ok_count} | FAIL={fail_count} | SKIP={skip_count}\n\n")

            f.write("## 模块执行详情\n\n")
            f.write("| # | 模块 | 描述 | 状态 | 耗时(s) | 退出码 |\n")
            f.write("|---|------|------|------|---------|--------|\n")
            for i, r in enumerate(self.results, 1):
                status_icon = {"OK": "✓", "FAIL": "✗", "SKIP": "−",
                              "SKIP_SCHEDULE": "−", "DRY_RUN": "○",
                              "TIMEOUT": "⏱", "ERROR": "✗"}.get(r["status"], "?")
                f.write(f"| {i} | {r['name']} | {r['description'][:30]} | "
                       f"{status_icon} {r['status']} | {r['duration_sec']} | {r['exit_code']} |\n")

            f.write("\n## 各模块输出末尾\n\n")
            for r in self.results:
                if r.get("output_tail"):
                    f.write(f"### {r['name']}\n```\n{r['output_tail']}\n```\n")

            f.write("\n## 归档文件清单\n\n")
            f.write(f"- 汇总报告: `{report_path}`\n")
            for r in self.results:
                if r["status"] == "OK":
                    f.write(f"- {r['name']}: 已执行\n")

        logger.info(f"汇总报告已保存: {report_path}")
        return report_path


# ============================================================
# CLI 入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="v7.5 全核心模块统一调度器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python run_all_modules.py --phase pre      # 盘前批次 (07:00)
    python run_all_modules.py --phase post     # 盘后批次 (15:30)
    python run_all_modules.py --phase all      # 全部 (测试用)
    python run_all_modules.py --phase pre --dry-run
        """,
    )
    parser.add_argument("--phase", choices=["pre", "post", "all"],
                        default="all", help="执行批次")
    parser.add_argument("--dry-run", action="store_true", help="干跑模式")
    parser.add_argument("--sim", action="store_true", help="模拟盘模式 (股票+期货，按交易日+夜盘执行)")
    args = parser.parse_args()

    scheduler = AllModulesScheduler(dry_run=args.dry_run, sim_mode=args.sim)
    logger.info(f"v7.5 全核心模块调度器启动 | phase={args.phase} | dry_run={args.dry_run} | sim={args.sim}")

    success = scheduler.run_phase(args.phase)
    report = scheduler.generate_summary_report()

    logger.info("=" * 70)
    logger.info(f"调度完成 | 成功={success} | 报告={report}")
    logger.info("=" * 70)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
