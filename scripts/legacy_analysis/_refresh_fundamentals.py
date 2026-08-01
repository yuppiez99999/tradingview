"""重新下载 fundamentals - 触发旧文件 ROE=0 的回退搜索"""
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(r"e:\各种PY程序\28-终极量化交易系统8.4")
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s | %(message)s",
)

from cache.data_downloader import download_fundamentals_batch
from cache.symbol_universe import get_universe

universe = get_universe()
print(f"标的池: {len(universe)} 个")
print("重新下载 fundamentals (旧文件 ROE=0 会自动重下)...")
success, failed, failed_syms = download_fundamentals_batch(
    symbols=universe,
    skip_if_exists=True,  # download_fundamentals 内部检测 ROE=0 自动重下
    progress_every=10,
)
print(f"\n完成: 成功 {success} / 失败 {failed}")
if failed_syms:
    print(f"失败标的: {failed_syms[:20]}")
