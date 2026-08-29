# -*- coding: utf-8 -*-
"""P2.2 改进补丁脚本：为现有 100 个 *_history.json 缓存补齐 revenue/yoy_pni 字段

需求背景：
    VT_QUALTREND_GROWTH_ACCEL 改进需要营收 YoY 加速 + 扣非净利 YoY 加速。
    原 download_fundamentals_history 不含 revenue 和 yoy_pni 字段，
    需要重新拉取所有历史季度数据补齐这些字段。

    依赖 download_fundamentals_history 内置的 schema_version 检测：
    - schema_version < 2 的缓存自动重下（仅 1 次，避免无限循环）
    - schema_version >= 2 的跳过

执行：
    python -m cache.patch_history_growth_data
    python cache/patch_history_growth_data.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# 项目根目录加入 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from cache.data_downloader import download_fundamentals_history_batch
from cache.symbol_universe import get_universe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s | %(message)s",
)
logger = logging.getLogger("patch_history_growth_data")


def main() -> int:
    """补齐 100 个历史季度缓存文件的 revenue/yoy_pni 字段

    通过 download_fundamentals_history 内置的 schema_version 检测自动识别旧缓存，
    仅重下 schema_version < 2 的文件（增量更新，避免重复下载）。
    """
    universe = get_universe()
    logger.info("=" * 70)
    logger.info("P2.2 改进：补齐历史季度缓存 revenue/yoy_pni 字段")
    logger.info("=" * 70)
    logger.info(f"标的池: {len(universe)} 个")

    # 调用 batch（内部会自动跳过 schema_version >= 2 的缓存，仅重下旧缓存）
    # skip_if_exists=True + 内部 schema_version 检测 = 增量更新
    success, failed_count, failed_symbols = download_fundamentals_history_batch(
        symbols=universe,
        n_quarters=8,
        skip_if_exists=True,  # 依赖 schema_version 检测自动跳过 v2
        progress_every=10,
        force_refresh=False,
    )

    print()
    logger.info("=" * 70)
    logger.info("补齐完成")
    logger.info("=" * 70)
    logger.info(f"  成功: {success} / {len(universe)}")
    logger.info(f"  失败: {failed_count}")
    if failed_symbols:
        logger.info("  失败标的（前 10 个）:")
        for s in failed_symbols[:10]:
            logger.info(f"    {s}")

    # 验证：检查一个样本文件是否含新字段
    if success > 0:
        sample_path = _PROJECT_ROOT / "cache" / "fundamentals" / f"{universe[0]}_history.json"
        if sample_path.exists():
            import json
            with open(sample_path, "r", encoding="utf-8") as f:
                sample = json.load(f)
            schema_v = sample.get("schema_version", 1)
            quarters = sample.get("quarters", [])
            latest_q = quarters[0] if quarters else {}
            print()
            logger.info(f"样本验证（{universe[0]}）:")
            logger.info(f"  schema_version: {schema_v}")
            logger.info(f"  n_valid: {len(quarters)}")
            print(f"  最新季度字段: revenue={latest_q.get('revenue', 'N/A')}, "
                  f"yoy_pni={latest_q.get('yoy_pni', 'N/A')}, "
                  f"yoy_ni={latest_q.get('yoy_ni', 'N/A')}")

    return 0 if success > 0 else 1


if __name__ == "__main__":
    sys.exit(main())