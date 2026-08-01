"""
全市场自动选股 CLI 入口

用法：
    # 烟雾测试（5只股票，验证流程）
    py -3.8 research/run_universe_scan.py --smoke-test --smoke-count 5

    # 全量扫描沪深300+中证500
    py -3.8 research/run_universe_scan.py --pool hs300_zz500

    # 仅沪深300
    py -3.8 research/run_universe_scan.py --pool hs300

    # 指定输出目录
    py -3.8 research/run_universe_scan.py --output reports/universe/custom/
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# 项目根加入 sys.path
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main():
    parser = argparse.ArgumentParser(
        description="对冲基金级全市场自动选股系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 烟雾测试
  py -3.8 research/run_universe_scan.py --smoke-test

  # 全量扫描
  py -3.8 research/run_universe_scan.py --pool hs300_zz500

  # 沪深300 + 自定义输出
  py -3.8 research/run_universe_scan.py --pool hs300 --output reports/scan_$(date +%Y%m%d)/
""",
    )
    parser.add_argument(
        "--pool",
        default="hs300_zz500",
        choices=["hs300", "zz500", "hs300_zz500", "tdx_all", "tdx_top800"],
        help="股票池类型 (默认: hs300_zz500, 推荐: tdx_top800 通达信稳定)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="输出目录 (默认: reports/universe/YYYY-MM-DD/)",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="烟雾测试模式 (仅处理少量股票)",
    )
    parser.add_argument(
        "--smoke-count",
        type=int,
        default=10,
        help="烟雾测试股票数 (默认: 10)",
    )
    parser.add_argument(
        "--trade-date",
        default=None,
        help="交易日期 YYYY-MM-DD (默认: 今天)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="详细日志",
    )

    args = parser.parse_args()

    # 日志配置
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # 第三方库降级日志
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("matplotlib").setLevel(logging.WARNING)

    # 延迟导入避免启动开销
    from utils.universe.scheduler import run_daily_scan

    result = run_daily_scan(
        trade_date=args.trade_date,
        output_dir=args.output,
        pool=args.pool,
        smoke_test=args.smoke_test,
        smoke_count=args.smoke_count,
    )

    # 退出码
    if result.success:
        print("\n" + "=" * 60)
        print(f"[OK] 选股完成 (耗时 {result.elapsed_seconds:.1f}秒)")
        print(f"  报告目录: {result.output_dir}")
        print(f"  候选股 CSV: {result.report_paths.get('candidates_csv', '')}")
        print(f"  汇总报告: {result.report_paths.get('report_md', '')}")
        print("=" * 60)
        sys.exit(0)
    else:
        print("\n" + "=" * 60)
        print(f"[FAIL] 选股失败: {result.error}")
        print("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()
