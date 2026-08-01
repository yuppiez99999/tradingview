"""P2.2 历史季度财务数据下载脚本

下载 105 个标的的过去 8 个季度财务数据，用于 P2.2 质量变化类因子计算。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from cache.data_downloader import download_fundamentals_history_batch
from cache.symbol_universe import get_universe


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s | %(message)s",
    )
    print("=" * 70)
    print("P2.2 历史季度财务数据下载")
    print("=" * 70)

    universe = get_universe()
    print(f"标的池: {len(universe)} 个")

    success, failed_count, failed_syms = download_fundamentals_history_batch(
        symbols=universe,
        n_quarters=8,
        skip_if_exists=True,
        progress_every=10,
    )

    print()
    print("=" * 70)
    print("下载完成")
    print("=" * 70)
    print(f"  成功: {success} / {len(universe)}")
    print(f"  失败: {failed_count}")

    if failed_syms:
        print("  失败标的（前 10 个）:")
        for s in failed_syms[:10]:
            print(f"    {s}")

    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
