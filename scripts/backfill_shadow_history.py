"""Shadow 账户历史真实数据回填脚本

用真实行情数据回填 daily_returns.jsonl 中的回测回填/修复值记录。
复用 ShadowRealDataFeeder.feed_history(), 从 TDX/AKShare 拉取真实价格,
按当日持仓权重计算组合日收益, 增量更新 (已存在日期则覆盖)。

用途:
    观察期内某些日期的数据来源是 "v9_phase10_real_backtest" (回测回填) 而非
    真实市场馈送, 需要用真实行情替换。本脚本一键回填指定日期范围。

注意:
    - 需要网络访问 (TDX/AKShare 数据源)
    - 会修改 reports/shadow/daily_returns.jsonl (已存在日期会被覆盖)
    - 持仓权重从 config/positions.json 加载 (weights_source="auto")

用法:
    python scripts/backfill_shadow_history.py
    python scripts/backfill_shadow_history.py --start-date 2026-07-27 --end-date 2026-07-31
    python scripts/backfill_shadow_history.py --dry-run  # 只预览不写盘
"""

from __future__ import annotations

import argparse
import io
import logging
import sys
from pathlib import Path

# 路径处理
_DIR = Path(__file__).resolve().parent
_PROJ = _DIR.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

# Windows 控制台 UTF-8 输出
for _name in ("stdout", "stderr"):
    _stream = getattr(sys, _name, None)
    if _stream is not None and getattr(_stream, "encoding", "").lower() != "utf-8":
        _buffer = getattr(_stream, "buffer", None)
        if _buffer is not None:
            try:
                setattr(
                    sys,
                    _name,
                    io.TextIOWrapper(_buffer, encoding="utf-8", errors="replace"),
                )
            except Exception:
                pass

logger = logging.getLogger(__name__)


def run_backfill(start_date: str, end_date: str, dry_run: bool = False) -> None:
    """执行历史真实数据回填.

    Args:
        start_date: 起始日期 YYYY-MM-DD
        end_date: 结束日期 YYYY-MM-DD
        dry_run: True 只预览不写盘

    Raises:
        ImportError: 依赖模块缺失
        RuntimeError: provider/feeder 初始化失败
    """
    from utils.alpha.shadow_real_data_feeder import ShadowRealDataFeeder
    from utils.data_provider import MarketDataProvider

    logger.info("初始化 MarketDataProvider (backtest_mode=False) ...")
    provider = MarketDataProvider(backtest_mode=False)

    logger.info("初始化 ShadowRealDataFeeder ...")
    feeder = ShadowRealDataFeeder(
        data_provider=provider,
        weights_source="auto",  # 自动从 positions.json 加载
        skip_weekend=True,  # 跳过周末
        verbose=True,
    )

    if dry_run:
        logger.info("【DRY-RUN 模式】只预览不写盘, 逐日计算 ...")
        from datetime import datetime, timedelta

        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        current = start_dt
        results = []
        while current <= end_dt:
            d = current.strftime("%Y-%m-%d")
            r = feeder.dry_run(d)
            results.append(r)
            current += timedelta(days=1)
    else:
        logger.info(
            "【正式回填】%s ~ %s, 将覆盖已存在的回测回填记录 ...", start_date, end_date
        )
        results = feeder.feed_history(start_date, end_date)

    # 打印逐日结果
    print("\n========== 回填结果 ==========")
    print(f"{'日期':<12} {'状态':<10} {'日收益':>10} {'覆盖率':>8} {'说明'}")
    print("-" * 70)
    success_count = 0
    skipped_count = 0
    failed_count = 0
    for r in results:
        if r.is_success:
            status = "✅ 成功"
            ret_str = f"{r.daily_return*100:+.4f}%"
            cov_str = f"{r.coverage*100:.1f}%"
            success_count += 1
        elif r.skipped:
            status = "⏭️ 跳过"
            ret_str = "—"
            cov_str = "—"
            skipped_count += 1
        else:
            status = "❌ 失败"
            ret_str = "—"
            cov_str = "—"
            failed_count += 1
        err = r.error or ""
        print(f"{r.date:<12} {status:<10} {ret_str:>10} {cov_str:>8} {err}")

    print("-" * 70)
    print(
        f"汇总: 成功 {success_count} / 跳过 {skipped_count} / 失败 {failed_count} / 总计 {len(results)}"
    )
    if dry_run:
        print("【DRY-RUN】未写入文件, 加 --dry-run 移除即正式回填")
    else:
        print("已写入 reports/shadow/daily_returns.jsonl")
    print("================================\n")


def main() -> None:
    """命令行入口."""
    parser = argparse.ArgumentParser(
        description="用真实行情数据回填 Shadow 账户 daily_returns.jsonl"
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default="2026-07-27",
        help="起始日期 YYYY-MM-DD (默认: 2026-07-27, 观察期起点)",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default="2026-07-31",
        help="结束日期 YYYY-MM-DD (默认: 2026-07-31, 回填5个交易日)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只预览不写盘 (推荐先跑一次确认数据)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="启用 DEBUG 级别日志",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        run_backfill(args.start_date, args.end_date, args.dry_run)
    except ImportError as e:
        logger.error("依赖模块缺失: %s", e)
        sys.exit(1)
    except (RuntimeError, ValueError, OSError, ConnectionError, TimeoutError) as e:
        logger.error("回填失败: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
