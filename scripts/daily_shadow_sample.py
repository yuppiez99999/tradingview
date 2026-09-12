"""每日影子样本自动补全脚本 (2026-08-11 v8.6.14).

功能:
    1. 检查今天是否为交易日 (跳过周末)
    2. 拉取当日 26 个标的真实行情, 计算 daily_return
    3. 运行交叉验证 (复用 run_shadow_cross_validation 逻辑)
    4. 输出汇总报告, 记录到日志文件

使用方式:
    # 手动运行 (指定日期)
    python scripts/daily_shadow_sample.py --date 2026-08-11

    # 自动模式 (用今天日期, 供 Task Scheduler 调用)
    python scripts/daily_shadow_sample.py

设计原则:
    - 幂等: 同一天重复运行只更新不重复写入
    - 容错: 数据源失败时记录日志并退出, 不抛异常
    - 静默: 默认 INFO 级别日志, 写入 logs/daily_shadow_sample_YYYYMMDD.log
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

from utils.datetime_utils import now_bj

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)


def _setup_logging(date_str: str) -> logging.Logger:
    """配置日志: 同时输出到控制台和文件."""
    log_file = LOG_DIR / f"daily_shadow_sample_{date_str.replace('-', '')}.log"
    logger = logging.getLogger("daily_shadow_sample")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # 文件 handler
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # 控制台 handler
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger


def _is_trading_day(date: datetime) -> tuple[bool, str]:
    """判断是否为交易日 (仅跳过周末, 节假日需人工处理).

    Returns:
        (is_trading_day, reason)
    """
    weekday = date.weekday()  # 0=Monday, 6=Sunday
    if weekday == 5:  # Saturday
        return False, "weekend (weekday=5)"
    if weekday == 6:  # Sunday
        return False, "weekend (weekday=6)"
    return True, "trading day"


def _run_feed(date_str: str, logger: logging.Logger) -> dict:
    """拉取当日行情并计算 daily_return."""
    from utils.alpha.shadow_real_data_feeder import ShadowRealDataFeeder
    from utils.data_provider import MarketDataProvider

    logger.info("=== 步骤 1: 拉取行情 ===")
    provider = MarketDataProvider(backtest_mode=False)
    feeder = ShadowRealDataFeeder(data_provider=provider, verbose=False)

    result = feeder.feed_single_date(date_str)
    logger.info(
        "feed 结果: daily_return=%+.6f, success=%d/%d, coverage=%.2f, written=%s, skipped=%s",
        result.daily_return,
        result.success_count,
        result.total_count,
        result.coverage,
        result.written,
        result.skipped,
    )
    if result.skipped:
        logger.warning("跳过原因: %s", result.error)
    if result.warnings:
        logger.warning("警告: %s", result.warnings[:3])

    return {
        "date": date_str,
        "daily_return": result.daily_return,
        "success_count": result.success_count,
        "total_count": result.total_count,
        "coverage": result.coverage,
        "written": result.written,
        "skipped": result.skipped,
        "error": result.error,
    }


def _run_cross_validate(logger: logging.Logger) -> dict:
    """运行交叉验证, 用 subprocess 调用 run_shadow_cross_validation.py.

    用 subprocess 隔离 logging 配置, 避免子脚本的 basicConfig 覆盖本脚本的 handler.
    """
    logger.info("=== 步骤 2: 交叉验证 ===")

    import subprocess

    script_path = PROJECT_ROOT / "scripts" / "run_shadow_cross_validation.py"
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=300,  # 5 分钟超时
            encoding="utf-8",
            errors="replace",
        )
        # 解析输出, 提取关键信息
        output_lines = result.stdout.splitlines() if result.stdout else []
        for line in output_lines:
            if "交叉校验完成" in line or "ERROR" in line:
                logger.info("[子脚本] %s", line.strip())

        if result.returncode != 0:
            logger.error("交叉验证脚本退出码非零: %d", result.returncode)
            if result.stderr:
                logger.error("stderr: %s", result.stderr[-500:])
            return {
                "cross_validate_done": False,
                "error": f"exit_code={result.returncode}",
            }

        return {"cross_validate_done": True}
    except subprocess.TimeoutExpired:
        logger.error("交叉验证超时 (5 分钟)")
        return {"cross_validate_done": False, "error": "timeout"}
    except Exception as e:
        logger.error("交叉验证失败: %s", e)
        return {"cross_validate_done": False, "error": str(e)}


def _summarize(logger: logging.Logger) -> None:
    """输出当前 daily_returns.jsonl 汇总."""
    import json

    jsonl_path = PROJECT_ROOT / "reports" / "shadow" / "daily_returns.jsonl"
    if not jsonl_path.exists():
        logger.warning("daily_returns.jsonl 不存在")
        return

    records = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))

    logger.info("=== 步骤 3: 汇总 ===")
    logger.info("总样本数: %d", len(records))
    valid = sum(1 for r in records if r.get("cross_validated"))
    high = sum(1 for r in records if r.get("source_consistency") == "high")
    logger.info("cross_validated=true: %d/%d", valid, len(records))
    logger.info("source_consistency=high: %d/%d", high, len(records))

    # 距 20 条门槛
    remaining = max(0, 20 - len(records))
    logger.info("距 20 条门槛还差: %d 条", remaining)

    # 08-24 决策日预测 (用交易日数, 非日历天数)
    # 2026-08-11 v8.6.14: 决策日从 08-20 延期到 08-24 (周一)
    # 原因: 08-20 前预计只有 18 条样本 (差 2 条), 延期到 08-24 可凑齐 21 条
    today = now_bj().date()
    decision_day = datetime(2026, 8, 24).date()
    # 计算今天到决策日之间的交易日数 (排除周末)
    trading_days_left = 0
    cur = today + timedelta(days=1)  # 明天开始
    while cur < decision_day:  # 不含决策日当天
        if cur.weekday() < 5:  # 周一到周五
            trading_days_left += 1
        cur += timedelta(days=1)
    days_to_decision = (decision_day - today).days
    logger.info(
        "距 08-24 决策日: %d 天 (日历), %d 天 (交易日)",
        days_to_decision,
        trading_days_left,
    )

    if remaining > 0:
        # 预测能否在 08-24 前凑齐 (用交易日数)
        projected = len(records) + trading_days_left
        if projected >= 20:
            logger.info(
                "✓ 按当前进度, 08-24 前可凑齐 %d 条样本 (现有 %d + 未来 %d 交易日)",
                projected,
                len(records),
                trading_days_left,
            )
        else:
            logger.warning(
                "⚠ 按当前进度, 08-24 前预计只有 %d 条样本 (差 %d 条), 需考虑延期决策日",
                projected,
                20 - projected,
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="每日影子样本自动补全")
    parser.add_argument(
        "--date",
        default=None,
        help="指定日期 (YYYY-MM-DD), 默认用今天",
    )
    args = parser.parse_args()

    # 确定日期
    if args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d")
    else:
        target_date = now_bj()

    date_str = target_date.strftime("%Y-%m-%d")
    logger = _setup_logging(date_str)

    logger.info("========================================")
    logger.info("每日影子样本补全 - %s", date_str)
    logger.info("========================================")

    # 1. 检查交易日
    is_trading, reason = _is_trading_day(target_date)
    if not is_trading:
        logger.info("跳过非交易日: %s", reason)
        return 0

    logger.info("交易日确认: %s", reason)

    # 2. 拉取行情
    try:
        feed_result = _run_feed(date_str, logger)
    except Exception as e:
        logger.error("拉取行情失败: %s", e, exc_info=True)
        return 1

    if feed_result["skipped"]:
        logger.warning("当日数据跳过, 不运行交叉验证")
        return 0

    # 3. 交叉验证
    try:
        _run_cross_validate(logger)
    except Exception as e:
        logger.error("交叉验证异常: %s", e, exc_info=True)
        # 不 return, 继续汇总

    # 4. 汇总
    _summarize(logger)

    logger.info("========================================")
    logger.info("完成")
    logger.info("========================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
